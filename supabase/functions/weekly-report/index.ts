// =====================================================================
// weekly-report — 주간 리포트 어댑터 (API 계약 v1 §5-2)
// 흐름: JWT 인증 → can_process(meal_log, weekly_report) 게이트
//       → 캐시(weekly_report) 조회(force=1이면 재생성)
//       → meal_log 주간 조회 → canonical 슬리밍(§5-2 A6, jsonb 통째 전송 금지)
//       → targets(user_goal, 없으면 기본값) → 엔진 /v1/report/weekly 호출
//       → 구조 게이트(guardrail_passed 아니면 안전 폴백) → weekly_report upsert → 반환
// PII 미전송(§6): 엔진에는 uid 없이 날짜·집계 배열만.
// 기본 주간: 최근 완결 주(지난주 월~일, Asia/Seoul). ?week_start=YYYY-MM-DD 지정 가능.
// =====================================================================
import { createClient } from "jsr:@supabase/supabase-js@2";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type, x-request-id",
  "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
};

const ENGINE_URL = Deno.env.get("ENGINE_URL") ?? "https://web-production-0cbc5.up.railway.app";
const ENGINE_API_KEY = Deno.env.get("ENGINE_API_KEY") ?? "";
const ENGINE_TIMEOUT_MS = 10_000; // 계약 §7: report는 10s 동기
const SCHEMA_VERSION = "report.v1";
const KST_OFFSET_MS = 9 * 3600 * 1000;
const FALLBACK_SAFE = "이번 주는 일반적인 식생활 균형을 참고해 주세요."; // 03 §7 사전 승인 문구
const DEFAULT_TARGETS = { calories_kcal: 1800, protein_g: 60, sodium_max_mg: 2000, sugar_max_g: 50, fiber_min_g: 25 };

function json(status: number, body: Record<string, unknown>): Response {
  return new Response(JSON.stringify(body), { status, headers: { ...CORS, "Content-Type": "application/json" } });
}
function err(status: number, code: string, message: string, requestId: string, retryable = false): Response {
  return json(status, { ok: false, error: { code, message, retryable }, request_id: requestId });
}

// KST 기준 날짜 유틸
function kstDateStr(d: Date): string {
  return new Date(d.getTime() + KST_OFFSET_MS).toISOString().slice(0, 10);
}
function lastCompletedWeekStart(): string {
  // 오늘(KST) 기준 지난주 월요일
  const nowKst = new Date(Date.now() + KST_OFFSET_MS);
  const dow = (nowKst.getUTCDay() + 6) % 7; // 월=0
  const thisMonday = new Date(nowKst.getTime() - dow * 86400_000);
  return new Date(thisMonday.getTime() - 7 * 86400_000).toISOString().slice(0, 10);
}
function kstDayToUtcIso(dateStr: string, endOfDay = false): string {
  const base = new Date(`${dateStr}T00:00:00+09:00`);
  return new Date(base.getTime() + (endOfDay ? 86400_000 : 0)).toISOString();
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  const requestId = req.headers.get("x-request-id") ?? crypto.randomUUID();

  const admin = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!);

  // 1) 인증
  const authHeader = req.headers.get("authorization") ?? "";
  const { data: userData, error: authErr } = await admin.auth.getUser(authHeader.replace(/^Bearer\s+/i, ""));
  if (authErr || !userData?.user) return err(401, "UNAUTHORIZED", "invalid or missing JWT", requestId);
  const uid = userData.user.id;

  // 2) 동의 게이트 (§5-2: meal_log + weekly_report 둘 다)
  for (const domain of ["meal_log", "weekly_report"]) {
    const { data: allowed, error: cpErr } = await admin.rpc("can_process", { p_user: uid, p_domain: domain });
    if (cpErr) return err(500, "INTERNAL", `can_process failed: ${cpErr.message}`, requestId, true);
    if (!allowed) return err(403, "VALIDATION_ERROR", `consent required (${domain})`, requestId);
  }

  // 3) 주간 결정
  const url = new URL(req.url);
  const weekStart = url.searchParams.get("week_start") ?? lastCompletedWeekStart();
  if (!/^\d{4}-\d{2}-\d{2}$/.test(weekStart)) {
    return err(400, "VALIDATION_ERROR", "week_start must be YYYY-MM-DD", requestId);
  }
  const startDate = new Date(`${weekStart}T00:00:00+09:00`);
  const weekEndStr = kstDateStr(new Date(startDate.getTime() + 6 * 86400_000));
  const force = url.searchParams.get("force") === "1";

  // 4) 캐시 조회
  const { data: cached } = await admin.from("weekly_report")
    .select("id,payload,generated_at,first_viewed_at")
    .eq("user_id", uid).eq("period_start", weekStart).maybeSingle();
  if (cached && !force) {
    return json(200, {
      ok: true,
      data: { report_id: cached.id, period: { start: weekStart, end: weekEndStr }, report: cached.payload, cached: true, first_viewed_at: cached.first_viewed_at },
      schema_version: SCHEMA_VERSION, request_id: requestId,
    });
  }

  // 5) meal_log 주간 조회 → canonical 슬리밍 (A6)
  const utcFrom = kstDayToUtcIso(weekStart);
  const utcTo = kstDayToUtcIso(weekEndStr, true);
  const { data: rows, error: mlErr } = await admin.from("meal_log")
    .select("eaten_at,meal_slot,foods,summary")
    .eq("user_id", uid).gte("eaten_at", utcFrom).lt("eaten_at", utcTo)
    .order("eaten_at", { ascending: true });
  if (mlErr) return err(500, "INTERNAL", `meal_log query failed: ${mlErr.message}`, requestId, true);

  const FOOD_FIELDS = ["name_ko", "name_en", "amount", "calories_kcal", "protein_g", "carbs_g", "fat_g", "sodium_mg", "sugar_g", "fiber_g", "db_matched", "match_confidence"] as const;
  const SUM_FIELDS = ["total_calories_kcal", "total_protein_g", "total_carbs_g", "total_fat_g", "total_sodium_mg", "total_sugar_g", "total_fiber_g"] as const;
  const meals = (rows ?? []).map((r) => {
    const eaten = new Date(r.eaten_at as string);
    const kst = new Date(eaten.getTime() + KST_OFFSET_MS);
    const foods = ((r.foods as Record<string, unknown>[]) ?? []).map((f) => {
      const slim: Record<string, unknown> = {};
      for (const k of FOOD_FIELDS) if (f[k] !== undefined) slim[k] = f[k];
      return slim;
    });
    const summary: Record<string, unknown> = {};
    for (const k of SUM_FIELDS) summary[k] = (r.summary as Record<string, unknown>)?.[k] ?? 0;
    return {
      date: kst.toISOString().slice(0, 10),
      time: kst.toISOString().slice(11, 16),
      meal_slot: r.meal_slot,
      foods, summary,
    };
  });

  // 6) targets = user_goal ?? 기본값
  const { data: goal } = await admin.from("user_goal")
    .select("calories,protein_g,sodium_max_mg,sugar_max_g,fiber_min_g")
    .eq("user_id", uid).maybeSingle();
  const targets = {
    calories_kcal: goal?.calories ?? DEFAULT_TARGETS.calories_kcal,
    protein_g: goal?.protein_g ?? DEFAULT_TARGETS.protein_g,
    sodium_max_mg: goal?.sodium_max_mg ?? DEFAULT_TARGETS.sodium_max_mg,
    sugar_max_g: goal?.sugar_max_g ?? DEFAULT_TARGETS.sugar_max_g,
    fiber_min_g: goal?.fiber_min_g ?? DEFAULT_TARGETS.fiber_min_g,
  };

  // 7) 엔진 호출 (PII 없음: 날짜·집계만)
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), ENGINE_TIMEOUT_MS);
  let engineBody: Record<string, unknown>;
  try {
    const r = await fetch(`${ENGINE_URL}/v1/report/weekly`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${ENGINE_API_KEY}`,
        "X-Idempotency-Key": requestId,
        "X-Request-Id": requestId,
      },
      body: JSON.stringify({ period: { start: weekStart, end: weekEndStr, tz: "Asia/Seoul" }, meals, targets }),
      signal: ctl.signal,
    });
    engineBody = await r.json().catch(() => ({}));
    if (!r.ok || !(engineBody as { ok?: boolean }).ok) {
      const code = r.status === 401 ? "ENGINE_KEY_MISSING" : "INTERNAL";
      return err(502, code, `engine report failed (http ${r.status})`, requestId, true);
    }
  } catch (e) {
    return err(504, "UPSTREAM_TIMEOUT", (e as Error).name === "AbortError" ? "engine timeout" : (e as Error).message, requestId, true);
  } finally {
    clearTimeout(timer);
  }

  // 8) 구조 게이트: next_action 가드레일 통과 표식 없으면 안전 폴백(빈칸 금지, 03 §7)
  const payload = (engineBody as { data: Record<string, unknown> }).data;
  const na = (payload?.next_action ?? {}) as Record<string, unknown>;
  if (na.guardrail_passed !== true || typeof na.message !== "string" || !na.message) {
    payload.next_action = {
      source: "fallback_safe", message: FALLBACK_SAFE, evidence_level: "nutrition_db",
      guardrail_passed: false, blocked_reason: (na.blocked_reason as string) ?? "edge_structural_gate",
    };
  }

  // 9) 저장 (unique(user_id, period_start) upsert)
  const { data: saved, error: upErr } = await admin.from("weekly_report")
    .upsert({ user_id: uid, period_start: weekStart, period_end: weekEndStr, payload, generated_at: new Date().toISOString() }, { onConflict: "user_id,period_start" })
    .select("id,first_viewed_at").single();
  if (upErr || !saved) return err(500, "INTERNAL", `report save failed: ${upErr?.message}`, requestId, true);

  return json(200, {
    ok: true,
    data: { report_id: saved.id, period: { start: weekStart, end: weekEndStr }, report: payload, cached: false, first_viewed_at: saved.first_viewed_at },
    schema_version: SCHEMA_VERSION,
    engine_version: (engineBody as { engine_version?: string }).engine_version,
    request_id: requestId,
  });
});
