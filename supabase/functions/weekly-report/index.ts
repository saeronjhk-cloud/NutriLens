// =====================================================================
// weekly-report — 주간 리포트 어댑터 (API 계약 v1 §5-2) · live 각색본
// 각색점(구 프로젝트 → 현 live lrnuqhpgyuizfggxgxpl):
//   · can_process(meal_log, weekly_report) 동의 게이트 제거
//     → 현 live는 동의=앱레이어(IP73: has_consent/can_process 미도입). meal-analysis-jobs 각색과 동일 원칙.
//   · 그 외 불변: 엔진 URL 기본값이 이미 현 엔진(web-production-0cbc5), ENGINE_API_KEY Edge Secret 사용,
//     user_goal 없으면 기본 타깃으로 폴백(테이블 없어도 무해).
// 2026-10-05 누적 v1 (영양공식 IP/integration/weekly_accumulation_design_v1.md D1·D2·D4 · 평가 G1~G6):
//   · 실섭취 우선 — 엔진 summary = adjusted_summary ?? summary (앱 다른 화면과 같은 규칙)
//   · 캐시 재사용 조건 = calc_version 일치 + 그 주 식사 updated_at ≤ generated_at + 끼니 수 일치
//   · foods 슬림에 barcode·missing_nutrients, 끼니에 adjusted·eaten_ratio·original_summary 전달
//   ⚠ 저장소 supabase/functions/weekly-report/index.ts 는 이 파일과 «바이트 동일»해야 한다(규칙 61).
// 흐름: JWT 인증 → 캐시(weekly_report) 조회(force=1이면 재생성)
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
const CALC_VERSION = "weekly.v3"; // 엔진 report_weekly.CALC_VERSION 과 같아야 캐시를 재사용 (v3 = 끼니 빠뜨린 날 보완, 2026-10-05)
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

  // 2) [각색] 동의 게이트 제거 — 현 live는 동의=앱레이어(can_process 미도입).
  //    (구 프로젝트: for domain of ["meal_log","weekly_report"] → can_process 검사)

  // 3) 주간 결정
  const url = new URL(req.url);
  const weekStart = url.searchParams.get("week_start") ?? lastCompletedWeekStart();
  if (!/^\d{4}-\d{2}-\d{2}$/.test(weekStart)) {
    return err(400, "VALIDATION_ERROR", "week_start must be YYYY-MM-DD", requestId);
  }
  const startDate = new Date(`${weekStart}T00:00:00+09:00`);
  const weekEndStr = kstDateStr(new Date(startDate.getTime() + 6 * 86400_000));
  const force = url.searchParams.get("force") === "1";

  // 4) 캐시 조회 — 재사용은 ①calc_version 일치 ②그 주 식사가 생성 뒤 바뀌지 않음 ③끼니 수 일치(삭제 감지)
  const utcFrom = kstDayToUtcIso(weekStart);
  const utcTo = kstDayToUtcIso(weekEndStr, true);
  const { data: cached } = await admin.from("weekly_report")
    .select("id,payload,generated_at,first_viewed_at")
    .eq("user_id", uid).eq("period_start", weekStart).maybeSingle();
  let cacheFresh = false;
  if (cached && !force && (cached.payload as Record<string, unknown>)?.calc_version === CALC_VERSION) {
    const { data: stamps } = await admin.from("meal_log")
      .select("updated_at")
      .eq("user_id", uid).gte("eaten_at", utcFrom).lt("eaten_at", utcTo);
    const genAt = new Date(cached.generated_at as string).getTime();
    const changed = (stamps ?? []).some((x) => new Date(x.updated_at as string).getTime() > genAt);
    const cachedMeals = ((cached.payload as Record<string, unknown>)?.coverage as { meals?: number })?.meals;
    cacheFresh = !!stamps && !changed && cachedMeals === stamps.length;
  }
  if (cached && cacheFresh) {
    return json(200, {
      ok: true,
      data: { report_id: cached.id, period: { start: weekStart, end: weekEndStr }, report: cached.payload, cached: true, first_viewed_at: cached.first_viewed_at },
      schema_version: SCHEMA_VERSION, request_id: requestId,
    });
  }

  // 5) meal_log 주간 조회 → canonical 슬리밍 (A6) · 실섭취 우선(D1)
  const { data: rows, error: mlErr } = await admin.from("meal_log")
    .select("eaten_at,meal_slot,foods,summary,original_summary,adjusted_summary,eaten_ratio")
    .eq("user_id", uid).gte("eaten_at", utcFrom).lt("eaten_at", utcTo)
    .order("eaten_at", { ascending: true });
  if (mlErr) return err(500, "INTERNAL", `meal_log query failed: ${mlErr.message}`, requestId, true);

  const FOOD_FIELDS = ["name_ko", "name_en", "amount", "calories_kcal", "protein_g", "carbs_g", "fat_g", "sodium_mg", "sugar_g", "fiber_g", "db_matched", "match_confidence", "barcode", "missing_nutrients"] as const;
  const SUM_FIELDS = ["total_calories_kcal", "total_protein_g", "total_carbs_g", "total_fat_g", "total_sodium_mg", "total_sugar_g", "total_fiber_g"] as const;
  const meals = (rows ?? []).map((r) => {
    const eaten = new Date(r.eaten_at as string);
    const kst = new Date(eaten.getTime() + KST_OFFSET_MS);
    const foods = ((r.foods as Record<string, unknown>[]) ?? []).map((f) => {
      const slim: Record<string, unknown> = {};
      for (const k of FOOD_FIELDS) if (f[k] !== undefined) slim[k] = f[k];
      return slim;
    });
    const actual = (r.adjusted_summary ?? r.summary) as Record<string, unknown> | null; // 실섭취 우선
    const original = (r.original_summary ?? r.summary) as Record<string, unknown> | null;
    const summary: Record<string, unknown> = {};
    const original_summary: Record<string, unknown> = {};
    for (const k of SUM_FIELDS) {
      summary[k] = actual?.[k] ?? 0;
      original_summary[k] = original?.[k] ?? 0;
    }
    return {
      date: kst.toISOString().slice(0, 10),
      time: kst.toISOString().slice(11, 16),
      meal_slot: r.meal_slot,
      foods, summary, original_summary,
      adjusted: r.adjusted_summary != null,
      eaten_ratio: r.eaten_ratio ?? null,
    };
  });

  // 6) targets = user_goal ?? 기본값 (user_goal 테이블 없거나 행 없으면 기본값으로 폴백)
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
