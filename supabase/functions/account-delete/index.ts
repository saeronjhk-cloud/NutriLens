// =====================================================================
// account-delete — 본인 계정 삭제(개인정보보호법 삭제권) · L3(Cursor) 트랙
// 근거: 08_...UI폴리시_v1(§5.3/§6 Step E), 02_스키마_v1 §8(삭제권 범위)
// 원칙: 관리자 아님(본인만). service_role. 파괴적 → CORS 운영 도메인만.
// 설계 사실(스키마 검증):
//   - 모든 유저 테이블 FK = auth.users(id) ON DELETE CASCADE
//     → auth.admin.deleteUser(uid)가 DB 행 자동 정리(안전망)
//   - profiles PK = id(=user_id) / user_goal PK = user_id / 그 외 = user_id 컬럼
//   - Storage(meal-photos)는 cascade 대상 아님 → 반드시 명시 삭제(§8-(2))
// 삭제 순서: Storage → 도메인 테이블 명시삭제(감사·멱등) → 0행 검증 → deleteUser(cascade 백스톱)
// 멱등: 재-DELETE/재-remove 모두 성공. deleteUser 이후 재호출은 토큰 무효로 401(정상 종료).
// 감사: user_id 원본 미기록 — SHA-256 해시 + 타임스탬프만 로그(PII 없음).
// =====================================================================
import { createClient } from "jsr:@supabase/supabase-js@2";

// 파괴적 액션: 운영 도메인만 허용(‘*’ 금지). 프리뷰 테스트 시 임시 추가.
const ALLOWED_ORIGINS = new Set<string>([
  "https://nutrition-diary-shell.lovable.app",
]);
function corsFor(origin: string | null) {
  const allow = origin && ALLOWED_ORIGINS.has(origin) ? origin : "https://nutrition-diary-shell.lovable.app";
  return {
    "Access-Control-Allow-Origin": allow,
    "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type, x-request-id",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Vary": "Origin",
  };
}

// 유저 소유 도메인 테이블 (삭제 대상 전량). [테이블, 유저키]
const USER_TABLES: Array<[string, string]> = [
  ["app_event", "user_id"],
  ["affiliate_click", "user_id"],
  ["coaching_memory", "user_id"],
  ["weekly_report", "user_id"],
  ["meal_log", "user_id"],
  ["product_scan", "user_id"],
  ["survey_health", "user_id"],
  ["user_goal", "user_id"],   // PK가 user_id
  ["user_consent", "user_id"],
  ["profiles", "id"],         // PK id = user_id
];
const STORAGE_BUCKET = "meal-photos";

function json(status: number, cors: Record<string, string>, body: Record<string, unknown>): Response {
  return new Response(JSON.stringify(body), { status, headers: { ...cors, "Content-Type": "application/json" } });
}

async function sha256Hex(s: string): Promise<string> {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

// 버킷 내 `${uid}/` 프리픽스 전체 삭제(페이지네이션, 멱등)
async function purgeStorage(admin: ReturnType<typeof createClient>, uid: string): Promise<{ removed: number }> {
  let removed = 0;
  const prefix = uid; // list()는 폴더 경로 기준
  // list는 최대 100개씩 → 반복
  // eslint-disable-next-line no-constant-condition
  while (true) {
    const { data: items, error } = await admin.storage.from(STORAGE_BUCKET).list(prefix, { limit: 100 });
    if (error) throw new Error(`storage list failed: ${error.message}`);
    if (!items || items.length === 0) break;
    const paths = items.filter((i) => i.name).map((i) => `${prefix}/${i.name}`);
    if (paths.length === 0) break;
    const { error: rmErr } = await admin.storage.from(STORAGE_BUCKET).remove(paths);
    if (rmErr) throw new Error(`storage remove failed: ${rmErr.message}`);
    removed += paths.length;
    if (items.length < 100) break; // 마지막 페이지
  }
  return { removed };
}

Deno.serve(async (req) => {
  const origin = req.headers.get("origin");
  const cors = corsFor(origin);
  if (req.method === "OPTIONS") return new Response("ok", { headers: cors });
  const requestId = req.headers.get("x-request-id") ?? crypto.randomUUID();
  if (req.method !== "POST") {
    return json(405, cors, { ok: false, error: { code: "METHOD_NOT_ALLOWED", message: "POST only" }, request_id: requestId });
  }

  const admin = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!);

  // 1) 인증: 본인만. 미인증 가입 도용 차단 위해 email_confirmed_at 필수.
  const token = (req.headers.get("authorization") ?? "").replace(/^Bearer\s+/i, "");
  const { data: userData, error: authErr } = await admin.auth.getUser(token);
  if (authErr || !userData?.user) {
    return json(401, cors, { ok: false, error: { code: "UNAUTHORIZED", message: "invalid or missing JWT" }, request_id: requestId });
  }
  const user = userData.user;
  if (!user.email_confirmed_at) {
    return json(403, cors, { ok: false, error: { code: "EMAIL_NOT_CONFIRMED", message: "email confirmation required" }, request_id: requestId });
  }
  const uid = user.id;

  // 2) 오작동 방지 확인 토큰(선택이지만 권장): body { confirm: "DELETE" }
  let confirm = "";
  try { confirm = (await req.json())?.confirm ?? ""; } catch { /* body 없음 허용 */ }
  if (confirm !== "DELETE") {
    return json(400, cors, { ok: false, error: { code: "CONFIRM_REQUIRED", message: 'body must include {"confirm":"DELETE"}' }, request_id: requestId });
  }

  const uidHash = await sha256Hex(uid);

  try {
    // 3) Storage 삭제(§8-(2), cascade 대상 아님) — 멱등
    const { removed } = await purgeStorage(admin, uid);

    // 4) 도메인 테이블 명시 삭제(감사·멱등). cascade가 백스톱이지만 명시로 계약 보장.
    for (const [table, key] of USER_TABLES) {
      const { error } = await admin.from(table).delete().eq(key, uid);
      if (error) throw new Error(`delete ${table} failed: ${error.message}`);
    }

    // 5) 0행 검증(부분삭제 방지). 하나라도 남으면 중단(멱등 재시도 가능).
    const remaining: Record<string, number> = {};
    for (const [table, key] of USER_TABLES) {
      const { count, error } = await admin.from(table).select("*", { count: "exact", head: true }).eq(key, uid);
      if (error) throw new Error(`verify ${table} failed: ${error.message}`);
      if ((count ?? 0) > 0) remaining[table] = count ?? 0;
    }
    const { data: leftover, error: lsErr } = await admin.storage.from(STORAGE_BUCKET).list(uid, { limit: 1 });
    if (lsErr) throw new Error(`verify storage failed: ${lsErr.message}`);
    if (leftover && leftover.length > 0) remaining["storage:meal-photos"] = leftover.length;
    if (Object.keys(remaining).length > 0) {
      console.error(JSON.stringify({ event: "account_delete_incomplete", uid_hash: uidHash, remaining, request_id: requestId }));
      return json(409, cors, { ok: false, error: { code: "DELETE_INCOMPLETE", message: "verification found remaining data; retry", remaining }, request_id: requestId });
    }

    // 6) auth 사용자 삭제(cascade 백스톱). 이 이후 토큰 무효 → 재호출은 401(정상).
    const { error: delErr } = await admin.auth.admin.deleteUser(uid);
    if (delErr) throw new Error(`auth deleteUser failed: ${delErr.message}`);

    // 7) 감사 로그(PII 없음): 해시 + 타임스탬프만.
    console.log(JSON.stringify({ event: "account_deleted", uid_hash: uidHash, storage_removed: removed, at: new Date().toISOString(), request_id: requestId }));

    return json(200, cors, { ok: true, data: { deleted: true, storage_removed: removed }, request_id: requestId });
  } catch (e) {
    console.error(JSON.stringify({ event: "account_delete_error", uid_hash: uidHash, message: (e as Error).message, request_id: requestId }));
    // 부분 실패: 멱등이므로 사용자가 재요청 가능.
    return json(500, cors, { ok: false, error: { code: "INTERNAL", message: "deletion failed; safe to retry", retryable: true }, request_id: requestId });
  }
});
