// meal-analysis-jobs toCanonical — 화이트리스트 회귀 방지 (2026-10-13)
// 실행: node --test supabase/functions/meal-analysis-jobs/canonical.test.ts  (Node 22 type-stripping · 외부 의존 0)
// 사고 2회: 세션52 alternates · 10-13 분량 조절 portion_base_g — 엔진이 보낸 필드를 여기서 조용히 버림.
import { test } from "node:test";
import assert from "node:assert/strict";
import { toCanonical, FOOD_PASSTHROUGH } from "./canonical.ts";

const engineFood = {
  name_ko: "돼지국밥", name_en: "pork soup", amount: "1그릇",
  calories_kcal: 260, protein_g: 20, carbs_g: 0, fat_g: 10, sodium_mg: 1500, sugar_g: 0, fiber_g: 0,
  db_matched: true, db_name: "돼지국밥", match_confidence: "high", quality_flags: [],
  alternates: [{ name_ko: "순대국" }], alternates_reason: "photo_confusable",
  estimated_serving_g: 700, portion_base_g: 640, rice_separated: true, package_count: 1.5,
};

test("C01 엔진 음식 필드 통과 — 분량 조절 v1 필드 포함", () => {
  const out = toCanonical({ foods: [engineFood], summary: {} }, "nl-4.0");
  const f = out.foods[0] as Record<string, unknown>;
  for (const k of ["estimated_serving_g", "portion_base_g", "rice_separated", "package_count", "alternates", "alternates_reason"]) {
    assert.deepEqual(f[k], (engineFood as Record<string, unknown>)[k], k);
  }
});

test("C02 없는 필드는 키 자체를 만들지 않음(대부분 음식)", () => {
  const out = toCanonical({ foods: [{ name_ko: "밥", calories_kcal: 315 }], summary: {} }, "nl-4.0");
  const f = out.foods[0] as Record<string, unknown>;
  for (const k of FOOD_PASSTHROUGH) assert.equal(k in f, false, k);
});

test("C03 기존 계약 유지 — legacy 이름·합계·food30_engine", () => {
  const out = toCanonical({ foods: [{ name: "김치", calories: 10, sodium: 300 }], summary: { total_calories: 10 }, food30_engine: { x: 1 } }, "v");
  assert.equal(out.foods[0].name_ko, "김치");
  assert.equal(out.foods[0].calories_kcal, 10);
  assert.equal(out.foods[0].sodium_mg, 300);
  assert.equal(out.summary.total_calories_kcal, 10);
  assert.deepEqual((out as Record<string, unknown>).food30_engine, { x: 1 });
  assert.equal(out.schema_version, "analyze.v1");
});

test("C04 잘못된 형식은 버림(숫자 아님·false)", () => {
  const out = toCanonical({ foods: [{ name_ko: "x", portion_base_g: "640", rice_separated: false, package_count: null }], summary: {} }, "v");
  const f = out.foods[0] as Record<string, unknown>;
  assert.equal("portion_base_g" in f, false);
  assert.equal("rice_separated" in f, false);
  assert.equal("package_count" in f, false);
});
