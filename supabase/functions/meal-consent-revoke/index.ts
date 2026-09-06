// meal-consent-revoke — 식사 사진 분석 동의 '철회 + 즉시 삭제' (변호사 2차 검토 P0)
// 흐름(원자적):
//   ① JWT 인증 → uid
//   ② meal_consent.revoked_at 세팅(트리거가 meal_consent_audit에 revoke 이벤트 적재 = 동의증빙 유지)
//   ③ delete_meal_data(uid) RPC — 식사 스택(meal_log·session·adjustment·analysis_job·weekly_report) 삭제
//   ④ Storage(meal-photos) `${uid}/` 원본 사진 스윕(account-delete purgeBucket 패턴: 반환 error 검사·페이지네이션·잔여0 검증)
//   유지: meal_consent(revoked)·meal_consent_audit(증빙 3년) — 삭제하지 않음. 계정·설문·검진 유지.
// fail-closed: storage 미완이면 409 + retryable(revoked_at·DB삭제는 이미 반영 = 재시도 안전/멱등).
// ⚠️ 배포: 환경변수 MEAL_PHOTO_BUCKET(기본 'meal-photos'). 버킷 경로 관례 `${uid}/` 확인.
import { createClient } from "jsr:@supabase/supabase-js@2";

const CORS: Record<string, string> = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "Authorization, Content-Type, apikey, x-client-info",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};
const JSON_HEADERS = { ...CORS, "Content-Type": "application/json; charset=utf-8" };

const SUPABASE_URL = Deno.env.get("SUPABASE_URL");
const SUPABASE_ANON_KEY = Deno.env.get("SUPABASE_ANON_KEY");
const SUPABASE_SERVICE_ROLE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
if (!SUPABASE_URL || !SUPABASE_ANON_KEY || !SUPABASE_SERVICE_ROLE_KEY) {
  throw new Error("[meal-consent-revoke] env 누락: SUPABASE_URL / SUPABASE_ANON_KEY / SUPABASE_SERVICE_ROLE_KEY");
}
const MEAL_PHOTO_BUCKET = Deno.env.get("MEAL_PHOTO_BUCKET") ?? "meal-photos";

const authClient = createClient(SUPABASE_URL, SUPABASE_ANON_KEY);
const admin = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY);

function json(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), { status, headers: JSON_HEADERS });
}

// 버킷 내 `${uid}/` 전량 삭제 — 반환 error 검사 + 페이지네이션 + 잔여 검증(account-delete와 동일 규칙).
async function purgeBucket(bucket: string, uid: string): Promise<{ removed: number; error?: string }> {
  let removed = 0;
  while (true) {
    const { data: items, error } = await admin.storage.from(bucket).list(uid, { limit: 100 });
    if (error) return { removed, error: `list ${bucket}: ${error.message}` };
    if (!items || items.length === 0) break;
    const paths = items.filter((i: { name?: string }) => i.name).map((i: { name: string }) => `${uid}/${i.name}`);
    if (paths.length === 0) break;
    const { error: rmErr } = await admin.storage.from(bucket).remove(paths);
    if (rmErr) return { removed, error: `remove ${bucket}: ${rmErr.message}` };
    removed += paths.length;
    if (items.length < 100) break;
  }
  const { data: left, error: vErr } = await admin.storage.from(bucket).list(uid, { limit: 1 });
  if (vErr) return { removed, error: `verify ${bucket}: ${vErr.message}` };
  if (left && left.length > 0) return { removed, error: `remaining ${bucket}: ${left.length}` };
  return { removed };
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });
  if (req.method !== "POST") return json({ error: "POST only", code: "METHOD_NOT_ALLOWED" }, 405);

  const authHeader = req.headers.get("Authorization");
  if (!authHeader?.startsWith("Bearer ")) return json({ error: "Missing or malformed Authorization header", code: "UNAUTHORIZED" }, 401);
  const jwt = authHeader.slice("Bearer ".length).trim();
  const { data: { user }, error: authErr } = await authClient.auth.getUser(jwt);
  if (authErr || !user) return json({ error: "Invalid or expired token", code: "UNAUTHORIZED" }, 401);
  const uid = user.id;

  try {
    // ② 철회 기록(트리거가 감사로그 적재). upsert로 meal_consent 행이 없어도 안전.
    const { error: rvErr } = await admin.from("meal_consent")
      .upsert({ user_id: uid, revoked_at: new Date().toISOString() }, { onConflict: "user_id" });
    if (rvErr) {
      console.error(`[meal-consent-revoke] revoke set 실패 uid=${uid}: ${rvErr.message}`);
      return json({ ok: false, error: { code: "REVOKE_FAILED", message: rvErr.message, retryable: true } }, 500);
    }

    // ③ 식사 스택 DB 삭제(감사로그·동의기록 제외)
    const { data: deleted, error: rpcErr } = await admin.rpc("delete_meal_data", { p_uid: uid });
    if (rpcErr) {
      console.error(`[meal-consent-revoke] delete_meal_data 실패 uid=${uid}: ${rpcErr.message}`);
      return json({ ok: false, error: { code: "DATA_DELETE_FAILED", message: rpcErr.message, retryable: true } }, 500);
    }

    // ④ Storage 스윕 — fail-closed
    const { removed, error: stErr } = await purgeBucket(MEAL_PHOTO_BUCKET, uid);
    if (stErr) {
      console.error(JSON.stringify({ event: "meal_revoke_storage_incomplete", uid, error: stErr }));
      return json({ ok: false, error: { code: "DELETE_INCOMPLETE", message: "storage sweep incomplete; retry", retryable: true }, dataDeleted: deleted, storageRemoved: removed }, 409);
    }

    console.log(`[meal-consent-revoke] 완료 uid=${uid} storage_removed=${removed}`);
    return json({ ok: true, dataDeleted: deleted, storageRemoved: removed }, 200);
  } catch (e) {
    console.error(`[meal-consent-revoke] 예외: ${(e as Error).message}`);
    return json({ ok: false, error: { code: "INTERNAL", message: (e as Error).message, retryable: true } }, 500);
  }
});
