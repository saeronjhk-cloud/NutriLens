// beta-feedback-notify / format.ts — 순수 로직(Deno·Node 공용, 외부 의존 0)
// 테스트: web/src/__tests__/beta_feedback_notify.test.ts (N01~N08)
//
// ★ 개인정보 원칙: 메일에 «사용자가 쓴 글(message·food_name)»·user_id 를 싣지 않는다.
//   발송 대행(Resend)은 현행 처리방침 수탁자 목록에 없다 → 알림은 «메타데이터만».
//   내용은 관리자가 SQL Editor 에서 id 로 조회한다(메일 본문에 쿼리 동봉).

export const KIND_LABELS: Record<string, string> = {
  correction: "음식명 정정",
  bug: "오류·버그",
  opinion: "의견·제안",
};

export interface FeedbackMeta {
  id: string;
  kind: string;
  hasJob: boolean;
  page: string;
  createdAt: string; // ISO
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** DB 트리거가 보낸 record(jsonb) → 메타데이터. 형식이 틀리면 null. */
export function parseFeedbackRecord(raw: unknown): FeedbackMeta | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  if (typeof r.id !== "string" || !UUID.test(r.id)) return null;
  if (typeof r.kind !== "string" || !(r.kind in KIND_LABELS)) return null;
  const page = typeof r.page === "string" ? r.page.replace(/[^A-Za-z0-9/_\-#?=&.]/g, "").slice(0, 80) : "";
  const createdAt = typeof r.created_at === "string" && !Number.isNaN(Date.parse(r.created_at))
    ? r.created_at
    : new Date().toISOString();
  return { id: r.id, kind: r.kind, hasJob: typeof r.job_id === "string" && r.job_id.length > 0, page, createdAt };
}

function kst(iso: string): string {
  const d = new Date(Date.parse(iso) + 9 * 3600 * 1000);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())} KST`;
}

export function buildFeedbackEmail(m: FeedbackMeta): { subject: string; text: string; html: string } {
  const label = KIND_LABELS[m.kind];
  const when = kst(m.createdAt);
  const sql = `select * from public.beta_feedback where id = '${m.id}';`;
  const lines = [
    `뉴트리렌즈(식사 기록) 베타 패널 제보가 1건 들어왔습니다.`,
    ``,
    `종류: ${label}`,
    `시각: ${when}`,
    `화면: ${m.page || "-"}`,
    `사진 연결: ${m.hasJob ? "있음" : "없음"}`,
    `제보 id: ${m.id}`,
    ``,
    `내용 확인 (Supabase SQL Editor):`,
    sql,
    ``,
    `※ 개인정보 보호를 위해 메일에는 제보 내용·사용자 정보를 싣지 않습니다.`,
  ];
  const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const html = `<div style="font-family:sans-serif;line-height:1.6">${lines
    .map((l) => (l === sql ? `<pre style="background:#f4f4f4;padding:8px">${esc(l)}</pre>` : esc(l) || "<br>"))
    .join("<br>")}</div>`;
  return { subject: `[영양공식 베타] 새 제보 — ${label} · ${when}`, text: lines.join("\n"), html };
}

/** 트리거 ↔ 함수 공유 비밀 비교(길이 노출 외 상수시간). 빈 값은 항상 거부. */
export function isAuthorized(given: string | null | undefined, expected: string | null | undefined): boolean {
  if (!given || !expected || given.length !== expected.length) return false;
  let diff = 0;
  for (let i = 0; i < given.length; i++) diff |= given.charCodeAt(i) ^ expected.charCodeAt(i);
  return diff === 0;
}
