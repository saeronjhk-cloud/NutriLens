// beta-feedback-notify 평가 N01~N08 — 실행: node --test supabase/functions/beta-feedback-notify/format.test.ts
// (Node 22 type-stripping · 외부 의존 0 · Deno 배포 번들에는 포함되지 않음: index.ts 가 import 하지 않음)
// 원칙: 메일에 «사용자가 쓴 글(message·food_name)»·user_id 를 싣지 않는다 (format.ts 머리주석).
import { test } from "node:test";
import assert from "node:assert/strict";
import { buildFeedbackEmail, isAuthorized, parseFeedbackRecord, KIND_LABELS } from "./format.ts";

// 영양공식 web/src/lib/betaPanel.ts FEEDBACK_KINDS · 151_beta_feedback_v1.sql check 제약과 «글자까지» 같아야 함
const APP_KINDS = ["correction", "bug", "opinion"];

const rec = {
  id: "0f1e2d3c-aaaa-bbbb-cccc-1234567890ab",
  user_id: "4b2c297c-1111-2222-3333-444455556666",
  job_id: "9a8b7c6d-0000-1111-2222-333344445555",
  kind: "correction",
  message: "이건 곰탕이 아니라 설렁탕이에요 010-1234-5678",
  food_name: "설렁탕",
  page: "/meal",
  created_at: "2026-09-30T05:05:00Z",
};
const mail = (r: unknown) => buildFeedbackEmail(parseFeedbackRecord(r)!);

test("N01 종류 라벨이 앱 FEEDBACK_KINDS 와 1:1", () => {
  assert.deepEqual(Object.keys(KIND_LABELS).sort(), [...APP_KINDS].sort());
});
test("N02 제목 = [영양공식 베타] 라벨 · KST 시각", () => {
  assert.equal(mail(rec).subject, "[영양공식 베타] 새 제보 — 음식명 정정 · 2026-09-30 14:05 KST");
});
test("N03 본문에 사용자 글·음식명·user_id 가 절대 없음", () => {
  const m = mail(rec);
  for (const body of [m.text, m.html, m.subject]) {
    assert.ok(!body.includes("설렁탕"));
    assert.ok(!body.includes("010-1234"));
    assert.ok(!body.includes("4b2c297c"));
  }
});
test("N04 제보 id·사진 연결·조회 SQL 포함", () => {
  const t = mail(rec).text;
  assert.ok(t.includes("0f1e2d3c-aaaa-bbbb-cccc-1234567890ab"));
  assert.ok(t.includes("사진 연결: 있음"));
  assert.ok(t.includes("select * from public.beta_feedback where id = '0f1e2d3c-aaaa-bbbb-cccc-1234567890ab';"));
});
test("N05 job_id 없으면 «사진 연결: 없음»", () => {
  assert.ok(mail({ ...rec, job_id: null }).text.includes("사진 연결: 없음"));
});
test("N06 잘못된 레코드 → null", () => {
  assert.equal(parseFeedbackRecord({ ...rec, kind: "spam" }), null);
  assert.equal(parseFeedbackRecord({ ...rec, id: undefined }), null);
  assert.equal(parseFeedbackRecord(null), null);
  assert.equal(parseFeedbackRecord("x"), null);
});
test("N07 page 주입 차단", () => {
  assert.ok(!mail({ ...rec, page: "/meal<script>" }).html.includes("<script>"));
});
test("N08 비밀값 비교", () => {
  assert.equal(isAuthorized("s3cret-abc", "s3cret-abc"), true);
  assert.equal(isAuthorized("s3cret-abd", "s3cret-abc"), false);
  assert.equal(isAuthorized(null, "s3cret-abc"), false);
  assert.equal(isAuthorized("", ""), false);
  assert.equal(isAuthorized("x", undefined), false);
});
