// =====================================================================
// analysis-status — 분석 잡 폴링 (API 계약 v1 §7: GET /analysis-status/{job_id})
// 클라가 3초 간격 폴링. 본인 잡만 조회 가능.
// =====================================================================
import { createClient } from "jsr:@supabase/supabase-js@2";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type, x-idempotency-key, x-request-id",
  "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
};

function json(status: number, body: Record<string, unknown>): Response {
  return new Response(JSON.stringify(body), { status, headers: { ...CORS, "Content-Type": "application/json" } });
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  const requestId = req.headers.get("x-request-id") ?? crypto.randomUUID();
  const url = new URL(req.url);
  // /analysis-status/{job_id} 또는 ?job_id= 둘 다 지원
  const parts = url.pathname.split("/").filter(Boolean);
  const jobId = url.searchParams.get("job_id") ?? (parts.length > 1 ? parts[parts.length - 1] : null);
  if (!jobId || !/^[0-9a-f-]{36}$/i.test(jobId)) {
    return json(400, { ok: false, error: { code: "VALIDATION_ERROR", message: "job_id required", retryable: false }, request_id: requestId });
  }

  const admin = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!);
  const auth = req.headers.get("authorization") ?? "";
  const { data: userData, error: authErr } = await admin.auth.getUser(auth.replace(/^Bearer\s+/i, ""));
  if (authErr || !userData?.user) {
    return json(401, { ok: false, error: { code: "UNAUTHORIZED", message: "invalid or missing JWT", retryable: false }, request_id: requestId });
  }

  const { data: job } = await admin.from("analysis_job")
    .select("id,status,result,error_code,error_message,photo_sha256,engine_version,created_at,updated_at")
    .eq("id", jobId).eq("user_id", userData.user.id).maybeSingle();
  if (!job) {
    return json(404, { ok: false, error: { code: "VALIDATION_ERROR", message: "job not found", retryable: false }, request_id: requestId });
  }
  return json(200, {
    ok: job.status !== "failed",
    data: {
      job_id: job.id, status: job.status, result: job.result,
      photo_sha256: job.photo_sha256,
      error_code: job.error_code, error_message: job.error_message,
      poll_hint_ms: job.status === "processing" ? 3000 : null,
    },
    schema_version: "analyze.v1",
    engine_version: job.engine_version ?? undefined,
    request_id: requestId,
  });
});
