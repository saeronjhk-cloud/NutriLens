// =====================================================================
// food-lookup — 식사 결과 «음식 편집» v1 (음식 검색 · 영양 확정) Edge
// 흐름: JWT(로그인 사용자만) → 요청 검증(lookup_core) → 엔진 /v1/food/{search|resolve} 프록시
// 개인정보 없음(음식 이름·그램만) → 동의 게이트 불필요. 영양 계산은 엔진 단일 소스.
// 설계 IP/integration/meal_food_edit_design_v1.md §3-3
// =====================================================================
import { createClient } from "jsr:@supabase/supabase-js@2";
import { engineCall, parseLookupRequest } from "./lookup_core.ts";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type, x-request-id",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};
const ENGINE_URL = Deno.env.get("ENGINE_URL") ?? "https://web-production-0cbc5.up.railway.app";
const ENGINE_API_KEY = Deno.env.get("ENGINE_API_KEY") ?? "";
const ENGINE_TIMEOUT_MS = 8_000;

function json(status: number, body: Record<string, unknown>): Response {
  return new Response(JSON.stringify(body), { status, headers: { ...CORS, "Content-Type": "application/json" } });
}
function err(status: number, code: string, message: string, requestId: string, retryable = false): Response {
  return json(status, { ok: false, error: { code, message, retryable }, request_id: requestId });
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  const requestId = req.headers.get("x-request-id") ?? crypto.randomUUID();
  if (req.method !== "POST") return err(405, "VALIDATION_ERROR", "POST only", requestId);

  // 1) JWT — 로그인 사용자만 (남용 방지)
  const admin = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!);
  const token = (req.headers.get("authorization") ?? "").replace(/^Bearer\s+/i, "");
  const { data: userData, error: authErr } = await admin.auth.getUser(token);
  if (authErr || !userData?.user) return err(401, "UNAUTHORIZED", "invalid or missing JWT", requestId);

  // 2) 검증
  const body = await req.json().catch(() => null);
  const parsed = parseLookupRequest(body);
  if (!parsed.ok) return err(400, "VALIDATION_ERROR", parsed.message, requestId);

  // 3) 엔진
  const call = engineCall(parsed.req);
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), ENGINE_TIMEOUT_MS);
  try {
    const r = await fetch(`${ENGINE_URL}${call.path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${ENGINE_API_KEY}`, "X-Request-Id": requestId },
      body: JSON.stringify(call.body),
      signal: ctl.signal,
    });
    const eb = await r.json().catch(() => ({})) as { ok?: boolean; data?: unknown; error?: { code?: string; message?: string } };
    if (!r.ok || !eb.ok) {
      console.error(`[food-lookup] engine ${r.status} ${eb.error?.code ?? ""} ${eb.error?.message ?? ""}`);
      return err(502, eb.error?.code ?? "INTERNAL", "engine error", requestId, true);
    }
    return json(200, { ok: true, data: eb.data ?? {}, request_id: requestId });
  } catch (e) {
    if ((e as Error).name === "AbortError") return err(504, "UPSTREAM_TIMEOUT", "engine timeout", requestId, true);
    console.error(`[food-lookup] fetch failed: ${(e as Error).message}`);
    return err(502, "INTERNAL", "engine unreachable", requestId, true);
  } finally {
    clearTimeout(timer);
  }
});
