// =====================================================================
// meal-analysis-jobs — 엔진 비동기 어댑터 (API 계약 v1 §5-1, §6, §7)
// ★ 라이브(lrnuqhpgyuizfggxgxpl) 이력:
//   2026-07-09 초기: 동의를 앱 레이어(ConsentGate)에서만 관리(서버 게이트 없음).
//   2026-07-12(IP111/115 반영, R5 정정): 서버측 동의 게이트 **복원·강제됨**.
//   → 아래 (2) 단계에서 meal_consent_active(uid) RPC로 DB가 권위 검증:
//     민감정보(건강)+국외이전 동의 both && 만14세 확인 && 미철회. 실패 시 403 차단.
//   앱 ConsentGate는 UX 선차단, 서버 게이트가 최종 강제(localStorage 아닌 DB 권위).
// 흐름: JWT 인증 → 방어(8MB/일일횟수/sha256중복) → EXIF 제거 → job 생성
//       → 엔진 호출(SYNC_WAIT_MS 내 완료 시 200, 초과 시 202+job_id) → legacy→canonical 매핑
// 저장 분리: meal_log 저장은 클라이언트(RLS)가 결과+photo_sha256으로 수행.
// =====================================================================
import { createClient } from "jsr:@supabase/supabase-js@2";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type, x-idempotency-key, x-request-id",
  "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
};

const ENGINE_URL = Deno.env.get("ENGINE_URL") ?? "https://web-production-0cbc5.up.railway.app";
const ENGINE_API_KEY = Deno.env.get("ENGINE_API_KEY") ?? ""; // 엔진측 검증은 추후 활성화
const SYNC_WAIT_MS = 12_000;      // 이 시간 내 완료 시 동기처럼 반환 (계약 §7: 10~15s)
const ENGINE_TIMEOUT_MS = 60_000; // 백그라운드 포함 엔진 최대 대기
const MAX_IMAGE_BYTES = 8 * 1024 * 1024; // X1: ≤8MB
const DAILY_LIMIT = 30;           // X1: 유저/일 분석 횟수 제한

const SCHEMA_VERSION = "analyze.v1";

type Envelope = { ok: boolean; data?: unknown; error?: { code: string; message: string; retryable: boolean }; schema_version?: string; engine_version?: string; request_id: string };

function json(status: number, body: Envelope): Response {
  return new Response(JSON.stringify(body), { status, headers: { ...CORS, "Content-Type": "application/json" } });
}
function err(status: number, code: string, message: string, requestId: string, retryable = false): Response {
  return json(status, { ok: false, error: { code, message, retryable }, request_id: requestId });
}

// ---- EXIF/메타 제거 (계약 §6): JPEG의 APP1~APP15·COM 세그먼트 제거 ----
function stripJpegMetadata(buf: Uint8Array): Uint8Array {
  if (buf.length < 4 || buf[0] !== 0xff || buf[1] !== 0xd8) return buf; // JPEG 아니면 원본
  const out: number[] = [0xff, 0xd8];
  let i = 2;
  while (i + 4 <= buf.length) {
    if (buf[i] !== 0xff) break; // 마커 구조 붕괴 시 잔여를 그대로 복사
    const marker = buf[i + 1];
    if (marker === 0xda) { // SOS: 이후는 이미지 데이터 — 전부 복사
      for (let k = i; k < buf.length; k++) out.push(buf[k]);
      return new Uint8Array(out);
    }
    const segLen = (buf[i + 2] << 8) | buf[i + 3];
    const isMeta = (marker >= 0xe1 && marker <= 0xef) || marker === 0xfe; // APP1~15, COM
    if (!isMeta) for (let k = i; k < i + 2 + segLen; k++) out.push(buf[k]);
    i += 2 + segLen;
  }
  return new Uint8Array(out);
}

async function sha256hex(buf: Uint8Array): Promise<string> {
  const d = await crypto.subtle.digest("SHA-256", buf.slice().buffer as ArrayBuffer);
  return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

// ---- legacy → canonical 매핑 (계약 §4) ----
//
// ⚠⚠ 세션52 (2026-09-05) — **이 함수는 «화이트리스트»다. 여기 없는 필드는 조용히 사라진다.**
//
//   엔진(test_server.py `/analyze`)이 무엇을 더 내려보내든, 여기 안 적으면
//   `analysis_job.result` 에 저장되지 않고 앱에도 도달하지 않는다. 에러도 로그도 없다.
//
//   실측 사고: 세션52 가 엔진에 `alternates`(구별불가쌍 정정 후보)를 붙이고 배포까지
//   했는데 앱에 칩이 안 떴다. 원인은 엔진이 아니라 «여기»였다. 배포 상태·환경변수·
//   모델 파일을 한참 뒤진 뒤에야 찾았다.
//
//   같은 이유로 **`food30_engine` 텔레메트리가 프로덕션에서 한 번도 저장된 적이 없다.**
//   세션50 이 넣은 `widened` 플래그도 실서비스에서는 관측 불가였다 —
//   평가 스크립트(accuracy_test.py)는 food_analyzer 를 «로컬에서 직접» 부르므로 보였을 뿐이다.
//
// ⇒ **엔진 응답에 필드를 추가하면 반드시 여기도 고치고 `supabase functions deploy` 하라.**
//    엔진만 배포하면 아무 일도 안 일어난다.
function toCanonical(raw: Record<string, unknown>, engineVersion: string) {
  const foods = (raw.foods as Record<string, unknown>[] | undefined) ?? [];
  const s = (raw.summary as Record<string, number> | undefined) ?? {};
  const pick = (obj: Record<string, unknown>, canonical: string, legacy: string) =>
    (obj[canonical] ?? obj[legacy] ?? 0) as number;
  const canonicalFoods = foods.map((f) => ({
    name_ko: f.name_ko ?? f.name ?? "",
    name_en: f.name_en ?? "",
    amount: f.amount ?? "",
    calories_kcal: pick(f, "calories_kcal", "calories"),
    protein_g: pick(f, "protein_g", "protein"),
    carbs_g: pick(f, "carbs_g", "carbs"),
    fat_g: pick(f, "fat_g", "fat"),
    sodium_mg: pick(f, "sodium_mg", "sodium"),
    sugar_g: pick(f, "sugar_g", "sugar"),
    fiber_g: pick(f, "fiber_g", "fiber"),
    db_matched: Boolean(f.db_matched),
    db_name: f.db_name ?? null,
    match_confidence: f.match_confidence ?? "none",
    quality_flags: (f.quality_flags as string[] | undefined) ??
      (f.match_confidence === "low" ? ["low_confidence"] : []),
    // 세션52 — 구별 불가 쌍(설렁탕↔곰탕 · 꽃게탕↔해물탕)의 정정 후보.
    // 서버가 형제 이름의 «영양까지» 계산해 보낸다. 앱에는 음식 DB 가 없어서
    // 이름만 주면 사용자가 고쳐도 칼로리를 바꿀 방법이 없기 때문이다.
    // 없는 음식에는 붙지 않으므로 대부분의 항목에서 undefined 다(그때는 키가 빠진다).
    ...(f.alternates ? { alternates: f.alternates } : {}),
    ...(f.alternates_reason ? { alternates_reason: f.alternates_reason } : {}),
  }));
  const sum = (k: keyof typeof canonicalFoods[number]) =>
    Math.round(canonicalFoods.reduce((a, f) => a + (Number(f[k]) || 0), 0) * 10) / 10;
  const summary = {
    total_calories_kcal: (s.total_calories_kcal ?? s.total_calories) ?? sum("calories_kcal"),
    total_protein_g: (s.total_protein_g ?? s.total_protein) ?? sum("protein_g"),
    total_carbs_g: (s.total_carbs_g ?? s.total_carbs) ?? sum("carbs_g"),
    total_fat_g: (s.total_fat_g ?? s.total_fat) ?? sum("fat_g"),
    total_sodium_mg: (s.total_sodium_mg ?? s.total_sodium) ?? sum("sodium_mg"),
    total_sugar_g: (s.total_sugar_g ?? s.total_sugar) ?? sum("sugar_g"),
    total_fiber_g: (s.total_fiber_g ?? s.total_fiber) ?? sum("fiber_g"),
  };
  return {
    foods: canonicalFoods,
    summary,
    reference: raw.reference ?? { detected: false, type: null, confidence: 0 },
    quality_flags: (raw.quality_flags as string[] | undefined) ?? [],
    engine_version: engineVersion,
    schema_version: SCHEMA_VERSION,
    // 세션52 — food30 엔진 텔레메트리를 «프로덕션에서도» 남긴다.
    // 이 줄이 없어서 지금까지 실서비스의 엔진 동작을 한 번도 관측하지 못했다:
    //   detected(무엇을 검출했나) · applied(교체했나, widened 인가) ·
    //   disagreement(검출했는데 못 고쳤나) · preempted · no_db_key
    // 엔진이 꺼져 있거나 모델이 없으면 이 키 자체가 없다 — 그것도 신호다.
    ...(raw.food30_engine ? { food30_engine: raw.food30_engine } : {}),
  };
}

// ---- 엔진 호출 ----
async function callEngine(image: Uint8Array, jobId: string, mode: string): Promise<Record<string, unknown>> {
  const fd = new FormData();
  fd.append("uid", jobId); // PII 미전송(§6): Supabase uid 대신 job_id
  fd.append("mode", mode);
  fd.append("image", new Blob([image.slice().buffer as ArrayBuffer], { type: "image/jpeg" }), `${crypto.randomUUID()}.jpg`); // 파일명 난수화
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), ENGINE_TIMEOUT_MS);
  try {
    const r = await fetch(`${ENGINE_URL}/analyze`, {
      method: "POST",
      headers: { "Authorization": `Bearer ${ENGINE_API_KEY}`, "X-Idempotency-Key": jobId, "X-Request-Id": jobId },
      body: fd,
      signal: ctl.signal,
    });
    const body = await r.json().catch(() => ({}));
    if (!r.ok || body.error) {
      throw new EngineError(r.status === 429 ? "RATE_LIMITED" : "ANALYSIS_FAILED", String(body.error ?? `engine http ${r.status}`));
    }
    return body as Record<string, unknown>;
  } catch (e) {
    if (e instanceof EngineError) throw e;
    if ((e as Error).name === "AbortError") throw new EngineError("UPSTREAM_TIMEOUT", "engine timeout");
    throw new EngineError("ANALYSIS_FAILED", (e as Error).message);
  } finally {
    clearTimeout(timer);
  }
}
class EngineError extends Error {
  constructor(public code: string, msg: string) { super(msg); }
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  const requestId = req.headers.get("x-request-id") ?? crypto.randomUUID();
  if (req.method !== "POST") return err(405, "VALIDATION_ERROR", "POST only", requestId);

  const admin = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!);

  // 1) 인증 (사용자 JWT)
  const authHeader = req.headers.get("authorization") ?? "";
  const { data: userData, error: authErr } = await admin.auth.getUser(authHeader.replace(/^Bearer\s+/i, ""));
  if (authErr || !userData?.user) return err(401, "UNAUTHORIZED", "invalid or missing JWT", requestId);
  const uid = userData.user.id;

  // 2) 동의 게이트 — 서버 재검증(P0-③ G2 / P0-④ / IP111·IP112). 아니면 차단.
  //    meal_consent_active(uid): 건강민감정보+국외이전 동의 둘 다 && 만14세이상 확인 && (미철회 or 철회<최신동의).
  //    앱 localStorage가 아니라 DB가 권위. 만14세 미만은 age_confirmed_14plus=false → false → 차단.
  const { data: consentOk, error: consentErr } = await admin.rpc("meal_consent_active", { p_uid: uid });
  if (consentErr) return err(500, "INTERNAL", `consent check failed: ${consentErr.message}`, requestId, true);
  if (consentOk !== true) {
    return err(403, "CONSENT_REQUIRED", "meal photo consent (sensitive info + intl transfer) required", requestId);
  }

  // 3) 입력 파싱
  const idem = req.headers.get("x-idempotency-key");
  if (!idem) return err(400, "VALIDATION_ERROR", "X-Idempotency-Key required", requestId);
  let form: FormData;
  try { form = await req.formData(); } catch { return err(400, "VALIDATION_ERROR", "multipart/form-data required", requestId); }
  const file = form.get("image");
  if (!(file instanceof File)) return err(400, "NO_IMAGE", "image file required", requestId);
  if (file.size > MAX_IMAGE_BYTES) return err(413, "BAD_IMAGE", "image exceeds 8MB", requestId);
  const mode = String(form.get("mode") ?? "default");

  // 4) 멱등: 동일 (user, idempotency_key) 잡이 있으면 그대로 반환
  const { data: existing } = await admin.from("analysis_job")
    .select("id,status,result,error_code,error_message,photo_sha256,engine_version")
    .eq("user_id", uid).eq("idempotency_key", idem).maybeSingle();
  if (existing) {
    const st = existing.status === "processing" ? 202 : 200;
    return json(st, { ok: existing.status !== "failed", data: { job_id: existing.id, status: existing.status, result: existing.result, photo_sha256: existing.photo_sha256, error_code: existing.error_code, error_message: existing.error_message }, schema_version: SCHEMA_VERSION, engine_version: existing.engine_version ?? undefined, request_id: requestId });
  }

  // 5) 방어: 일일 횟수 + 동일 이미지 중복
  const raw = new Uint8Array(await file.arrayBuffer());
  const clean = stripJpegMetadata(raw);          // EXIF/GPS 제거 (§6)
  const sha = await sha256hex(clean);
  const since = new Date(Date.now() - 24 * 3600 * 1000).toISOString();
  const { count } = await admin.from("analysis_job").select("id", { count: "exact", head: true })
    .eq("user_id", uid).gte("created_at", since);
  if ((count ?? 0) >= DAILY_LIMIT) return err(429, "RATE_LIMITED", "daily analysis limit reached", requestId);
  const { data: dupMeal } = await admin.from("meal_log").select("id", { head: false })
    .eq("user_id", uid).eq("photo_sha256", sha).limit(1);
  if (dupMeal && dupMeal.length > 0) return err(409, "VALIDATION_ERROR", "duplicate image (already logged)", requestId);

  // 6) 잡 생성
  const { data: job, error: insErr } = await admin.from("analysis_job")
    .insert({ user_id: uid, idempotency_key: idem, photo_sha256: sha, request_id: requestId })
    .select("id").single();
  if (insErr || !job) return err(500, "INTERNAL", `job insert failed: ${insErr?.message}`, requestId, true);
  const jobId = job.id as string;

  // 7) 엔진 호출 — SYNC_WAIT_MS 내 완료 시 동기 반환, 아니면 202 + 백그라운드
  const work = (async () => {
    try {
      const rawResult = await callEngine(clean, jobId, mode);
      const engineVersion = String(rawResult.engine_version ?? rawResult.version ?? "unknown");
      const canonical = toCanonical(rawResult, engineVersion);
      await admin.from("analysis_job").update({ status: "done", result: canonical, engine_version: engineVersion }).eq("id", jobId);
      return canonical;
    } catch (e) {
      const ee = e instanceof EngineError ? e : new EngineError("INTERNAL", (e as Error).message);
      await admin.from("analysis_job").update({ status: "failed", error_code: ee.code, error_message: ee.message.slice(0, 500) }).eq("id", jobId);
      throw ee;
    }
  })();

  const timeoutToken = Symbol("timeout");
  const raced = await Promise.race([work, new Promise((r) => setTimeout(() => r(timeoutToken), SYNC_WAIT_MS))]).catch((e) => e);

  if (raced === timeoutToken) {
    // @ts-ignore EdgeRuntime는 Supabase Edge 전역
    if (typeof EdgeRuntime !== "undefined") EdgeRuntime.waitUntil(work.catch(() => {}));
    return json(202, { ok: true, data: { job_id: jobId, status: "processing", photo_sha256: sha, poll_hint_ms: 3000 }, schema_version: SCHEMA_VERSION, request_id: requestId });
  }
  if (raced instanceof EngineError) {
    return err(raced.code === "UPSTREAM_TIMEOUT" ? 504 : 502, raced.code, raced.message, requestId, raced.code === "UPSTREAM_TIMEOUT");
  }
  return json(200, { ok: true, data: { job_id: jobId, status: "done", result: raced, photo_sha256: sha }, schema_version: SCHEMA_VERSION, engine_version: (raced as { engine_version?: string }).engine_version, request_id: requestId });
});
