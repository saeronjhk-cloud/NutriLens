// meal-analysis-jobs — legacy → canonical 매핑 (순수 로직, 외부 의존 0) · 2026-10-13 index.ts 에서 분리
// 테스트: node --test supabase/functions/meal-analysis-jobs/canonical.test.ts
export const SCHEMA_VERSION = "analyze.v1";

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
// 분량 조절 v1(2026-10-13) — 엔진이 음식마다 보내는 «선택 필드». 형식이 맞을 때만 키를 만든다.
//   estimated_serving_g: 사진 추정량(이름 바꾸기 때 양 유지 — 음식 편집 v1 설계, 지금까지 여기서 버려지고 있었음)
//   portion_base_g: «적게·보통·많게»의 기준 g · rice_separated: 국밥 «국만» 값 · package_count: 라면 봉지 수
export const FOOD_PASSTHROUGH = ["estimated_serving_g", "portion_base_g", "rice_separated", "package_count"] as const;
function passthrough(f: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const k of ["estimated_serving_g", "portion_base_g", "package_count"]) {
    const v = f[k];
    if (typeof v === "number" && Number.isFinite(v) && v > 0) out[k] = v;
  }
  if (f.rice_separated === true) out.rice_separated = true;
  return out;
}

export function toCanonical(raw: Record<string, unknown>, engineVersion: string) {
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
    ...passthrough(f),
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
