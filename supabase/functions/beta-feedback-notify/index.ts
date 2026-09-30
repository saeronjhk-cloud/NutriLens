// beta-feedback-notify — 뉴트리렌즈 베타 패널 제보(beta_feedback INSERT) → 관리자 알림 메일 (2026-09-30)
// 흐름: DB 트리거(web/supabase/153_beta_feedback_notify_v1.sql, pg_net 비동기) → 이 함수 → Resend API
//   · 제보 저장은 알림 성공/실패와 무관(pg_net 비동기) — 메일 장애가 제보를 막지 않는다.
//   · 인증: JWT 아님(verify_jwt=false) → 헤더 x-notify-secret 과 env NOTIFY_SECRET 비교.
//   · 메일 본문 = 메타데이터만(format.ts 원칙). 내용은 SQL Editor 에서 id 로 조회.
// env(Supabase secrets): RESEND_API_KEY · NOTIFY_SECRET · NOTIFY_TO(수신) · NOTIFY_FROM(선택, 기본 onboarding@resend.dev)
//   ⚠ 발신 도메인 미인증 상태의 onboarding@resend.dev 는 «Resend 가입 계정 메일»로만 보낼 수 있다 → NOTIFY_TO = 가입 메일.
import { buildFeedbackEmail, isAuthorized, parseFeedbackRecord } from "./format.ts";

const RESEND_API_KEY = Deno.env.get("RESEND_API_KEY");
const NOTIFY_SECRET = Deno.env.get("NOTIFY_SECRET");
const NOTIFY_TO = Deno.env.get("NOTIFY_TO");
const NOTIFY_FROM = Deno.env.get("NOTIFY_FROM") ?? "영양공식 알림 <onboarding@resend.dev>";

const json = (body: unknown, status: number) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json; charset=utf-8" } });

Deno.serve(async (req) => {
  if (req.method !== "POST") return json({ error: "method" }, 405);
  if (!isAuthorized(req.headers.get("x-notify-secret"), NOTIFY_SECRET)) return json({ error: "unauthorized" }, 401);
  if (!RESEND_API_KEY || !NOTIFY_TO) {
    console.error("[beta-feedback-notify] env 누락: RESEND_API_KEY / NOTIFY_TO");
    return json({ error: "not_configured" }, 500);
  }

  let payload: unknown;
  try { payload = await req.json(); } catch { return json({ error: "bad_json" }, 400); }
  const meta = parseFeedbackRecord((payload as { record?: unknown } | null)?.record);
  if (!meta) return json({ error: "bad_record" }, 400);

  const mail = buildFeedbackEmail(meta);
  const res = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: { Authorization: `Bearer ${RESEND_API_KEY}`, "Content-Type": "application/json" },
    body: JSON.stringify({ from: NOTIFY_FROM, to: [NOTIFY_TO], subject: mail.subject, text: mail.text, html: mail.html }),
  });
  if (!res.ok) {
    const detail = (await res.text()).slice(0, 300);
    console.error("[beta-feedback-notify] resend 실패", res.status, detail);
    return json({ error: "send_failed", status: res.status }, 502);
  }
  return json({ ok: true, id: meta.id }, 200);
});
