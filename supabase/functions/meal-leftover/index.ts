// =====================================================================
// meal-leftover — 정찬·잔반 Path A Edge (계약 34 §B)
// 흐름: JWT → can_process → 멱등 → canonical 조회 → 엔진 /v1/analyze → 저장
// 신뢰경계·소유권·세션상태·멱등은 전부 Edge. 엔진은 순수 산술만.
// =====================================================================
import { createClient } from "jsr:@supabase/supabase-js@2";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers":
    "authorization, x-client-info, apikey, content-type, x-idempotency-key, x-request-id",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

const ENGINE_URL = Deno.env.get("ENGINE_URL") ?? "https://web-production-0cbc5.up.railway.app";
const ENGINE_API_KEY = Deno.env.get("ENGINE_API_KEY") ?? "";
const ENGINE_TIMEOUT_MS = 15_000;
const SCHEMA_VERSION = "analyze.v1";
const ENGINE_VERSION = Deno.env.get("ENGINE_VERSION") ?? "nl-4.0";

type Envelope = {
  ok: boolean;
  data?: unknown;
  error?: { code: string; message: string; retryable: boolean };
  schema_version?: string;
  engine_version?: string;
  request_id: string;
};

type EngineFood = Record<string, unknown> & { food_item_id: string; _meal_log_id?: string };

function json(status: number, body: Envelope): Response {
  return new Response(JSON.stringify(body), { status, headers: { ...CORS, "Content-Type": "application/json" } });
}
function err(status: number, code: string, message: string, requestId: string, retryable = false): Response {
  return json(status, { ok: false, error: { code, message, retryable }, request_id: requestId });
}

async function sha256hex(text: string): Promise<string> {
  const data = new TextEncoder().encode(text);
  const digest = await crypto.subtle.digest("SHA-256", data);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** 멱등용 — 신뢰하지 않는 필드 제외, 키 정렬 JSON */
function requestHashPayload(body: Record<string, unknown>): Record<string, unknown> {
  const allowed = [
    "pre_meal_log_id", "pre_meal_session_id", "leftover_method",
    "eaten_ratio", "session_eaten_ratio", "per_food", "mode",
  ];
  const out: Record<string, unknown> = {};
  for (const k of allowed.sort()) {
    if (body[k] !== undefined) out[k] = body[k];
  }
  return out;
}

function ensureFoodItemId(f: Record<string, unknown>, index: number): string {
  const id = f.food_item_id;
  if (typeof id === "string" && id.trim()) return id.trim();
  return `food_${String(index + 1).padStart(2, "0")}`;
}

function foodToEngineItem(f: Record<string, unknown>, foodItemId: string, mealLogId?: string): EngineFood {
  const item: EngineFood = {
    food_item_id: foodItemId,
    name_ko: f.name_ko ?? f.name ?? "",
    calories_kcal: Number(f.calories_kcal ?? f.calories ?? 0),
    protein_g: Number(f.protein_g ?? f.protein ?? 0),
    carbs_g: Number(f.carbs_g ?? f.carbs ?? 0),
    fat_g: Number(f.fat_g ?? f.fat ?? 0),
    sodium_mg: Number(f.sodium_mg ?? f.sodium ?? 0),
  };
  const serving = f.estimated_serving_g ?? f.serving_g;
  if (serving != null) item.estimated_serving_g = Number(serving);
  if (mealLogId) item._meal_log_id = mealLogId;
  return item;
}

function buildOriginalFoodsSnapshot(foods: EngineFood[]): Record<string, unknown> {
  const snap: Record<string, unknown> = {};
  for (const f of foods) {
    snap[f.food_item_id] = {
      name_ko: f.name_ko,
      calories_kcal: f.calories_kcal,
      protein_g: f.protein_g,
      carbs_g: f.carbs_g,
      fat_g: f.fat_g,
      sodium_mg: f.sodium_mg,
      ...(f.estimated_serving_g != null ? { estimated_serving_g: f.estimated_serving_g } : {}),
    };
  }
  return snap;
}

function engineSummaryToMealLog(summary: Record<string, unknown>): Record<string, unknown> {
  return {
    total_calories_kcal: summary.calories_kcal ?? 0,
    total_protein_g: summary.protein_g ?? 0,
    total_carbs_g: summary.carbs_g ?? 0,
    total_fat_g: summary.fat_g ?? 0,
    total_sodium_mg: summary.sodium_mg ?? 0,
  };
}

function mealLogSummaryToPre(summary: Record<string, unknown> | null): Record<string, number> {
  if (!summary) return {};
  return {
    calories_kcal: Number(summary.total_calories_kcal ?? summary.calories_kcal ?? 0),
    protein_g: Number(summary.total_protein_g ?? summary.protein_g ?? 0),
    carbs_g: Number(summary.total_carbs_g ?? summary.carbs_g ?? 0),
    fat_g: Number(summary.total_fat_g ?? summary.fat_g ?? 0),
    sodium_mg: Number(summary.total_sodium_mg ?? summary.sodium_mg ?? 0),
  };
}

function foodsFromMealLog(
  log: { id: string; foods: unknown },
  startIndex: number,
): { foods: EngineFood[]; nextIndex: number } {
  const raw = (log.foods as Record<string, unknown>[]) ?? [];
  const foods: EngineFood[] = [];
  let idx = startIndex;
  for (const f of raw) {
    const fid = ensureFoodItemId(f, idx);
    foods.push(foodToEngineItem(f, fid, log.id));
    idx += 1;
  }
  return { foods, nextIndex: idx };
}

function validateRatioSpec(body: Record<string, unknown>): string | null {
  const hasGlobal = body.eaten_ratio !== undefined;
  const hasSession = body.session_eaten_ratio !== undefined;
  const hasPerFood = body.per_food !== undefined;
  if (body.pre_meal_session_id && hasPerFood) return "session_perfood_forbidden";
  if (hasPerFood && (hasGlobal || hasSession)) return "mutually_exclusive";
  if (hasSession && hasGlobal) return "mutually_exclusive";
  if (!hasGlobal && !hasSession && !hasPerFood) return "ratio required";
  return null;
}

function representativeRatio(body: Record<string, unknown>): number {
  if (typeof body.eaten_ratio === "number") return body.eaten_ratio;
  if (typeof body.session_eaten_ratio === "number") return body.session_eaten_ratio;
  const pf = body.per_food as { eaten_ratio: number }[] | undefined;
  if (pf?.length) {
    return pf.reduce((s, x) => s + Number(x.eaten_ratio), 0) / pf.length;
  }
  return 1;
}

function aggregateLeftoverNote(foods: Record<string, unknown>[]): string | null {
  const notes = foods.map((f) => f.leftover_note).filter(Boolean) as string[];
  return notes.length ? notes.join("; ") : null;
}

async function callEngineLeftover(
  engineBody: Record<string, unknown>,
  requestId: string,
  idemKey: string,
): Promise<{ data: Record<string, unknown>; engine_version?: string }> {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), ENGINE_TIMEOUT_MS);
  try {
    const r = await fetch(`${ENGINE_URL}/v1/analyze`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${ENGINE_API_KEY}`,
        "X-Request-Id": requestId,
        "X-Idempotency-Key": idemKey,
      },
      body: JSON.stringify({ mode: "leftover", ...engineBody }),
      signal: ctl.signal,
    });
    const body = await r.json().catch(() => ({})) as Envelope & { data?: Record<string, unknown> };
    if (!r.ok || !body.ok) {
      const code = (body.error?.code as string) ?? (r.status === 400 ? "VALIDATION_ERROR" : "INTERNAL");
      throw new EngineError(r.status, code, body.error?.message ?? `engine http ${r.status}`);
    }
    return { data: body.data ?? {}, engine_version: body.engine_version ?? ENGINE_VERSION };
  } catch (e) {
    if (e instanceof EngineError) throw e;
    if ((e as Error).name === "AbortError") throw new EngineError(504, "UPSTREAM_TIMEOUT", "engine timeout");
    throw new EngineError(502, "INTERNAL", (e as Error).message);
  } finally {
    clearTimeout(timer);
  }
}

class EngineError extends Error {
  constructor(public status: number, public code: string, msg: string) {
    super(msg);
  }
}

function successResponse(
  data: Record<string, unknown>,
  requestId: string,
  engineVersion?: string,
): Response {
  return json(200, {
    ok: true,
    data,
    schema_version: SCHEMA_VERSION,
    engine_version: engineVersion ?? ENGINE_VERSION,
    request_id: requestId,
  });
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  const requestId = req.headers.get("x-request-id") ?? crypto.randomUUID();
  if (req.method !== "POST") return err(405, "VALIDATION_ERROR", "POST only", requestId);

  const admin = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!);

  // 1) JWT
  const authHeader = req.headers.get("authorization") ?? "";
  const { data: userData, error: authErr } = await admin.auth.getUser(authHeader.replace(/^Bearer\s+/i, ""));
  if (authErr || !userData?.user) return err(401, "UNAUTHORIZED", "invalid or missing JWT", requestId);
  const uid = userData.user.id;

  // 2) 동의 (slider — intl_transfer 불필요)
  const { data: allowed, error: cpErr } = await admin.rpc("can_process", { p_user: uid, p_domain: "meal_log" });
  if (cpErr) return err(500, "INTERNAL", `can_process failed: ${cpErr.message}`, requestId, true);
  if (!allowed) return err(403, "VALIDATION_ERROR", "consent required (meal_log)", requestId);

  // 3) Idempotency-Key + request_hash
  const idemKey = req.headers.get("x-idempotency-key");
  if (!idemKey?.trim()) return err(400, "VALIDATION_ERROR", "X-Idempotency-Key required", requestId);

  let body: Record<string, unknown>;
  try {
    body = await req.json();
  } catch {
    return err(400, "VALIDATION_ERROR", "json body required", requestId);
  }

  const requestHash = await sha256hex(JSON.stringify(requestHashPayload(body)));

  // 4) 신뢰경계 — 클라 pre_result/pre_summary 거부
  if ("pre_result" in body || "pre_summary" in body) {
    return err(400, "VALIDATION_ERROR", "client_pre_result_not_trusted", requestId);
  }

  const leftoverMethod = String(body.leftover_method ?? "slider");
  if (leftoverMethod !== "slider") {
    return err(400, "VALIDATION_ERROR", "Path A supports leftover_method=slider only", requestId);
  }

  const preMealLogId = body.pre_meal_log_id as string | undefined;
  const preMealSessionId = body.pre_meal_session_id as string | undefined;
  if (!preMealLogId && !preMealSessionId) {
    return err(400, "VALIDATION_ERROR", "pre_meal_log_id or pre_meal_session_id required", requestId);
  }
  if (preMealLogId && preMealSessionId) {
    return err(400, "VALIDATION_ERROR", "pre_meal_log_id and pre_meal_session_id are mutually exclusive", requestId);
  }

  // 5) 멱등 조회
  const { data: existingAdj } = await admin.from("meal_log_adjustment")
    .select("id,request_hash,adjusted_summary,original_summary_snapshot,method,scope")
    .eq("user_id", uid)
    .eq("idempotency_key", idemKey)
    .maybeSingle();

  if (existingAdj) {
    if (existingAdj.request_hash !== requestHash) {
      return err(409, "IDEMPOTENCY_KEY_REUSE_MISMATCH", "idempotency key reused with different body", requestId);
    }
    return successResponse({
      adjusted_summary: existingAdj.adjusted_summary,
      pre_summary: mealLogSummaryToPre(existingAdj.original_summary_snapshot as Record<string, unknown>),
      foods: [],
      leftover_method: existingAdj.method ?? leftoverMethod,
      idempotent_replay: true,
    }, requestId);
  }

  // 6) 입력 배타/검증
  const ratioErr = validateRatioSpec(body);
  if (ratioErr) return err(400, "VALIDATION_ERROR", ratioErr, requestId);

  const enginePayload: Record<string, unknown> = { pre_result: { foods: [] as EngineFood[] } };
  if (body.eaten_ratio !== undefined) enginePayload.eaten_ratio = body.eaten_ratio;
  if (body.session_eaten_ratio !== undefined) enginePayload.session_eaten_ratio = body.session_eaten_ratio;
  if (body.per_food !== undefined) enginePayload.per_food = body.per_food;

  let scope: "meal" | "session" = "meal";
  let targetMealLogId: string | null = preMealLogId ?? null;
  let targetSessionId: string | null = preMealSessionId ?? null;
  let sessionLogs: Record<string, unknown>[] = [];
  let canonicalFoods: EngineFood[] = [];
  let originalSummaryCanonical: Record<string, unknown> | null = null;
  let previousAdjusted: Record<string, unknown> | null = null;

  // 7) canonical 조회 + 소유권
  if (preMealLogId) {
    const { data: mealLog, error: mlErr } = await admin.from("meal_log")
      .select("id,user_id,foods,summary,original_summary,adjusted_summary,source")
      .eq("id", preMealLogId)
      .eq("user_id", uid)
      .maybeSingle();
    if (mlErr) return err(500, "INTERNAL", mlErr.message, requestId, true);
    if (!mealLog) return err(403, "VALIDATION_ERROR", "forbidden_owner_mismatch", requestId);

    const built = foodsFromMealLog(mealLog as { id: string; foods: unknown }, 0);
    canonicalFoods = built.foods;
    originalSummaryCanonical = (mealLog.original_summary as Record<string, unknown>) ?? (mealLog.summary as Record<string, unknown>);
    previousAdjusted = mealLog.adjusted_summary as Record<string, unknown> | null;
    sessionLogs = [mealLog];
  } else if (preMealSessionId) {
    scope = "session";
    const { data: session, error: sErr } = await admin.from("meal_session")
      .select("id,user_id,status")
      .eq("id", preMealSessionId)
      .eq("user_id", uid)
      .maybeSingle();
    if (sErr) return err(500, "INTERNAL", sErr.message, requestId, true);
    if (!session) return err(403, "VALIDATION_ERROR", "forbidden_owner_mismatch", requestId);
    if (session.status !== "closed") {
      return err(409, "SESSION_STILL_OPEN", "session must be closed before leftover adjustment", requestId);
    }

    const { data: logs, error: lgErr } = await admin.from("meal_log")
      .select("id,user_id,foods,summary,original_summary,adjusted_summary,source,meal_session_id")
      .eq("meal_session_id", preMealSessionId)
      .eq("user_id", uid)
      .order("eaten_at", { ascending: true });
    if (lgErr) return err(500, "INTERNAL", lgErr.message, requestId, true);
    sessionLogs = logs ?? [];
    if (!sessionLogs.length) return err(400, "VALIDATION_ERROR", "session has no meal_log items", requestId);

    let idx = 0;
    for (const log of sessionLogs) {
      const built = foodsFromMealLog(log as { id: string; foods: unknown }, idx);
      canonicalFoods.push(...built.foods);
      idx = built.nextIndex;
    }
    enginePayload.pre_meal_session_id = preMealSessionId;

    const agg = { calories: 0, protein: 0, carbs: 0, fat: 0, sodium: 0 };
    for (const log of sessionLogs) {
      const s = (log.original_summary ?? log.summary) as Record<string, unknown> | null;
      if (!s) continue;
      agg.calories += Number(s.total_calories_kcal ?? 0);
      agg.protein += Number(s.total_protein_g ?? 0);
      agg.carbs += Number(s.total_carbs_g ?? 0);
      agg.fat += Number(s.total_fat_g ?? 0);
      agg.sodium += Number(s.total_sodium_mg ?? 0);
    }
    originalSummaryCanonical = {
      total_calories_kcal: agg.calories,
      total_protein_g: agg.protein,
      total_carbs_g: agg.carbs,
      total_fat_g: agg.fat,
      total_sodium_mg: agg.sodium,
    };
  }

  (enginePayload.pre_result as { foods: EngineFood[] }).foods = canonicalFoods.map(({ _meal_log_id: _, ...rest }) => rest);

  // 8) original_summary materialize (첫 보정 시 summary → original_summary)
  const originalFoodsSnapshot = buildOriginalFoodsSnapshot(canonicalFoods);
  if (!originalSummaryCanonical && sessionLogs.length === 1) {
    originalSummaryCanonical = sessionLogs[0].summary as Record<string, unknown>;
  }

  // 9) 엔진 호출
  let engineResult: Record<string, unknown>;
  let engineVersion = ENGINE_VERSION;
  try {
    const eng = await callEngineLeftover(enginePayload, requestId, idemKey);
    engineResult = eng.data;
    engineVersion = eng.engine_version ?? ENGINE_VERSION;
  } catch (e) {
    const ee = e as EngineError;
    return err(ee.status, ee.code, ee.message, requestId, ee.code === "UPSTREAM_TIMEOUT");
  }

  const engineFoods = (engineResult.foods as Record<string, unknown>[]) ?? [];
  const adjustedEngineSummary = engineResult.summary as Record<string, unknown>;
  const preEngineSummary = engineResult.pre_summary as Record<string, unknown>;
  const adjustedCanonical = engineSummaryToMealLog(adjustedEngineSummary);
  const originalSnapshot = originalSummaryCanonical ?? engineSummaryToMealLog(preEngineSummary);
  // per_food: eaten_ratio 컬럼은 대표평균; 정확값은 adjusted_summary·original_foods_snapshot 참조
  const eatenRatio = representativeRatio(body);
  const leftoverNote = aggregateLeftoverNote(engineFoods);

  const mealLogUpdates = scope === "meal"
    ? [{
        id: targetMealLogId,
        leftover_method: leftoverMethod,
        eaten_ratio: eatenRatio,
        original_summary: (sessionLogs[0]?.original_summary as unknown) ?? originalSnapshot,
        adjusted_summary: adjustedCanonical,
        leftover_note: leftoverNote,
        leftover_engine_version: engineVersion,
      }]
    : sessionLogs.map((log) => {
        const logId = log.id as string;
        const logFoodIds = canonicalFoods.filter((f) => f._meal_log_id === logId).map((f) => f.food_item_id);
        const logEngineFoods = engineFoods.filter((f) => logFoodIds.includes(String(f.food_item_id)));
        const subSummary = logEngineFoods.length
          ? engineSummaryToMealLog({
            calories_kcal: logEngineFoods.reduce((s, f) => s + Number(f.calories_kcal ?? 0), 0),
            protein_g: logEngineFoods.reduce((s, f) => s + Number(f.protein_g ?? 0), 0),
            carbs_g: logEngineFoods.reduce((s, f) => s + Number(f.carbs_g ?? 0), 0),
            fat_g: logEngineFoods.reduce((s, f) => s + Number(f.fat_g ?? 0), 0),
            sodium_mg: logEngineFoods.reduce((s, f) => s + Number(f.sodium_mg ?? 0), 0),
          })
          : adjustedCanonical;
        return {
          id: logId,
          leftover_method: leftoverMethod,
          eaten_ratio: eatenRatio,
          original_summary: (log.original_summary as unknown) ?? (log.summary as unknown),
          adjusted_summary: subSummary,
          leftover_note: leftoverNote,
          leftover_engine_version: engineVersion,
        };
      });

  // 10) 저장 — 단일 트랜잭션 RPC (meal_log + adjustment 원자성)
  const { data: rpcRes, error: rpcErr } = await admin.rpc("apply_leftover_adjustment", {
    p: {
      user_id: uid,
      idempotency_key: idemKey,
      request_hash: requestHash,
      meal_log_id: scope === "meal" ? targetMealLogId : null,
      meal_session_id: scope === "session" ? targetSessionId : null,
      method: "slider",
      scope,
      eaten_ratio: eatenRatio,
      original_summary_snapshot: originalSnapshot,
      previous_adjusted_summary: scope === "meal" ? previousAdjusted : null,
      adjusted_summary: adjustedCanonical,
      original_foods_snapshot: originalFoodsSnapshot,
      session_food_snapshot: scope === "session"
        ? sessionLogs.map((log) => ({ meal_log_id: log.id, foods: log.foods, summary: log.summary }))
        : null,
      confidence: null,
      user_confirmed: true,
      endpoint: "analyze.leftover",
      engine_version: engineVersion,
      meal_log_updates: mealLogUpdates,
    },
  });
  if (rpcErr) {
    if (rpcErr.message?.includes("IDEMPOTENCY_KEY_REUSE_MISMATCH")) {
      return err(409, "IDEMPOTENCY_KEY_REUSE_MISMATCH", "idempotency key reused with different body", requestId);
    }
    return err(500, "INTERNAL", `apply_leftover_adjustment failed: ${rpcErr.message}`, requestId, true);
  }

  const rpc = (rpcRes ?? {}) as { replay?: boolean; adjusted_summary?: Record<string, unknown> };

  // 11) 응답
  return successResponse({
    adjusted_summary: rpc.adjusted_summary ?? adjustedCanonical,
    pre_summary: mealLogSummaryToPre(originalSnapshot),
    foods: rpc.replay ? [] : engineFoods,
    leftover_method: leftoverMethod,
    ...(rpc.replay ? { idempotent_replay: true } : {}),
  }, requestId, engineVersion);
});
