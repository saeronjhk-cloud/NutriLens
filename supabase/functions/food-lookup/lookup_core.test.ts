// food-lookup 평가 F01~F06 — 실행: node --test supabase/functions/food-lookup/lookup_core.test.ts
// (Node 22 type-stripping · 외부 의존 0 · index.ts 만 배포 번들에 들어감)
import { test } from "node:test";
import assert from "node:assert/strict";
import { parseLookupRequest, engineCall } from "./lookup_core.ts";

test("F01 search: trim · 기본 limit 10", () => {
  const r = parseLookupRequest({ action: "search", q: " 국밥 " });
  assert.deepEqual(r, { ok: true, req: { action: "search", q: "국밥", limit: 10 } });
  const r2 = parseLookupRequest({ action: "search", q: "밥", limit: 5 });
  assert.equal(r2.ok && r2.req.action === "search" && r2.req.limit, 5);
});

test("F02 search 거부: 빈 · 공백 · 31자 · 숫자 · limit 범위", () => {
  for (const q of ["", "   ", "가".repeat(31), 3]) assert.equal(parseLookupRequest({ action: "search", q }).ok, false);
  for (const limit of [0, 21, 2.5, "5"]) assert.equal(parseLookupRequest({ action: "search", q: "밥", limit }).ok, false);
});

test("F03 resolve: serving null/생략 허용", () => {
  assert.deepEqual(parseLookupRequest({ action: "resolve", name: "밥", serving_g: null }),
    { ok: true, req: { action: "resolve", name: "밥", serving_g: null } });
  assert.deepEqual(parseLookupRequest({ action: "resolve", name: " 밥 " }),
    { ok: true, req: { action: "resolve", name: "밥", serving_g: null } });
  assert.deepEqual(parseLookupRequest({ action: "resolve", name: "밥", serving_g: 210 }),
    { ok: true, req: { action: "resolve", name: "밥", serving_g: 210 } });
});

test("F04 resolve 거부: serving 0 · 2001 · 문자열 · NaN · 이름 없음/41자", () => {
  for (const serving_g of [0, 2001, "300", NaN, -5]) {
    assert.equal(parseLookupRequest({ action: "resolve", name: "밥", serving_g }).ok, false);
  }
  for (const name of ["", " ", "가".repeat(41), null]) assert.equal(parseLookupRequest({ action: "resolve", name }).ok, false);
});

test("F05 action 없음/다름 · 배열/null 본문 거부", () => {
  for (const b of [{ action: "delete", q: "밥" }, { q: "밥" }, null, [], "x"]) assert.equal(parseLookupRequest(b).ok, false);
});

test("F06 엔진 경로 매핑", () => {
  assert.deepEqual(engineCall({ action: "search", q: "국밥", limit: 10 }), { path: "/v1/food/search", body: { q: "국밥", limit: 10 } });
  assert.deepEqual(engineCall({ action: "resolve", name: "밥", serving_g: null }), { path: "/v1/food/resolve", body: { name: "밥", serving_g: null } });
});
