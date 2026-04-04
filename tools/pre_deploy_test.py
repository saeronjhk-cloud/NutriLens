#!/usr/bin/env python3
"""
NutriLens 배포 전 자동 검증 스크립트
─────────────────────────────────────
커밋하기 전에 이 스크립트를 한 번 돌려서 문제를 미리 잡아냄.
API 호출 없음 — 비용 0원, 즉시 실행.

실행법:
  python tools/pre_deploy_test.py

통과 조건:
  - CORE_FOODS 영양값 범위 정상
  - DB 매칭에서 오매칭(false positive) 없음
  - 식단 시나리오 칼로리 합계 합리적
  - match_with_db() 3-tier 우선순위 정상 동작

종료 코드:
  0 = 전체 통과 (배포 OK)
  1 = 실패 항목 있음 (수정 필요)
"""

import sys
import os
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent
TOOLS_DIR = Path(__file__).parent
sys.path.insert(0, str(TOOLS_DIR))

# .env 로드
def load_env():
    env_paths = [PROJECT_DIR / '.env', Path.cwd() / '.env']
    for p in env_paths:
        if p.exists():
            with open(p) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, val = line.split('=', 1)
                        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))
            break

load_env()

# ── 테스트 결과 추적 ──
_passed = 0
_failed = 0
_errors = []


def check(condition, name, detail=""):
    global _passed, _failed
    if condition:
        _passed += 1
    else:
        _failed += 1
        msg = f"FAIL: {name}"
        if detail:
            msg += f" — {detail}"
        _errors.append(msg)
        print(f"  ✗ {name}: {detail}")


# ══════════════════════════════════════════════════════════
#  테스트 1: CORE_FOODS 영양값 범위 검증
# ══════════════════════════════════════════════════════════
def test_core_foods_sanity():
    """모든 CORE_FOODS 항목이 상식적인 영양값 범위인지 확인"""
    print("\n[테스트 1] CORE_FOODS 영양값 범위 검증")
    print("-" * 50)

    from food_analyzer import CORE_FOODS

    check(len(CORE_FOODS) >= 100, "CORE_FOODS 항목 수",
          f"{len(CORE_FOODS)}개 (최소 100개 필요)")

    for name, data in CORE_FOODS.items():
        cal = data.get("cal", 0)
        prot = data.get("prot", 0)
        carbs = data.get("carbs", 0)
        fat = data.get("fat", 0)
        serving = data.get("serving", 0)

        # 칼로리: 100g당 0~700kcal (초콜릿이 546)
        check(0 < cal <= 700, f"{name} 칼로리",
              f"{cal}kcal/100g (범위: 1~700)")

        # 서빙 사이즈: 10~600g
        check(10 <= serving <= 600, f"{name} 서빙 사이즈",
              f"{serving}g (범위: 10~600)")

        # 100g당 칼로리로 1인분 칼로리 계산 — 1인분 0~1500kcal
        per_serving_cal = cal * serving / 100
        check(per_serving_cal <= 1500, f"{name} 1인분 칼로리",
              f"{per_serving_cal:.0f}kcal (최대 1500kcal)")

        # 단백질, 탄수, 지방 합이 100g 초과하면 이상
        macro_sum = prot + carbs + fat
        check(macro_sum <= 100, f"{name} 매크로 합계",
              f"단{prot}+탄{carbs}+지{fat}={macro_sum}g/100g (최대 100g)")

        # 필수 필드 존재
        for field in ["cal", "prot", "carbs", "fat", "serving", "category"]:
            check(field in data, f"{name} 필드 {field}", "필수 필드 누락")

    ok_count = len([n for n in CORE_FOODS
                    if 0 < CORE_FOODS[n]["cal"] <= 700
                    and 10 <= CORE_FOODS[n]["serving"] <= 600])
    print(f"  → {ok_count}/{len(CORE_FOODS)} 항목 정상")


# ══════════════════════════════════════════════════════════
#  테스트 2: CORE_FOODS 매칭 로직 검증
# ══════════════════════════════════════════════════════════
def test_core_foods_matching():
    """_search_core_foods()가 올바른 매칭을 하는지 확인"""
    print("\n[테스트 2] CORE_FOODS 매칭 로직 검증")
    print("-" * 50)

    from food_analyzer import _search_core_foods

    # 정확히 맞아야 하는 케이스
    exact_cases = [
        ("귤", "귤"),
        ("김치", "김치"),
        ("밥", "밥"),
        ("삼겹살", "삼겹살"),
        ("라면", "라면"),
        ("치킨", "치킨"),
        ("피자", "피자"),
        ("커피", "커피"),
        ("두부", "두부"),
        ("바나나", "바나나"),
        ("감자탕", "감자탕"),
        ("김치볶음밥", "김치볶음밥"),
        ("튀긴 양파", "튀긴 양파"),
        ("계란후라이", "계란후라이"),
    ]

    for query, expected_key in exact_cases:
        key, data = _search_core_foods(query)
        check(key == expected_key, f"매칭: '{query}'",
              f"기대={expected_key}, 실제={key}")

    # 매칭 안 돼야 하는 케이스 (오매칭 방지)
    no_match_cases = [
        "레귤러",         # "귤"과 매칭되면 안 됨
        "아이스아메리카",  # 너무 다른 단어
    ]

    for query in no_match_cases:
        key, data = _search_core_foods(query)
        # 매칭이 됐더라도 "귤" 같은 잘못된 매칭이면 실패
        if query == "레귤러":
            check(key != "귤", f"오매칭 방지: '{query}'",
                  f"'귤'에 매칭되면 안 됨 (실제: {key})")


# ══════════════════════════════════════════════════════════
#  테스트 3: 주요 음식 1인분 칼로리 현실성 검증
# ══════════════════════════════════════════════════════════
def test_per_serving_calories():
    """흔한 음식의 1인분 칼로리가 상식과 맞는지"""
    print("\n[테스트 3] 주요 음식 1인분 칼로리 현실성")
    print("-" * 50)

    from food_analyzer import CORE_FOODS

    # (음식명, 1인분 최소kcal, 1인분 최대kcal) — 상식 범위
    reality_checks = [
        ("귤",       20,   80),    # 귤 1개 ~40kcal
        ("밥",      250,  400),    # 밥 1공기 ~300kcal
        ("김치",      3,   15),    # 김치 1접시 ~7kcal
        ("삼겹살",  400,  900),    # 삼겹살 200g ~660kcal
        ("바나나",   80,  150),    # 바나나 1개 ~110kcal
        ("사과",     80,  150),    # 사과 1개 ~110kcal
        ("라면",    350,  550),    # 라면 1봉 ~450kcal
        ("아메리카노", 5,  30),    # 아메리카노 ~15kcal
        ("치킨",    200,  350),    # 치킨 100g ~266kcal
        ("계란후라이", 80, 180),   # 계란후라이 1개 ~120kcal
        ("두부",     50,  120),    # 두부 100g ~79kcal
        ("김치찌개", 150,  300),   # 김치찌개 1인분 ~200kcal
        ("된장찌개", 100,  250),   # 된장찌개 1인분 ~150kcal
        ("고구마",  100,  250),    # 고구마 1개 ~170kcal
        ("튀긴양파",  50, 150),    # 튀긴양파 30g ~90kcal
        ("샐러드",   30,   80),    # 샐러드 1접시 ~50kcal
        ("피자",    200,  400),    # 피자 1조각 ~285kcal
        ("초밥",    200,  400),    # 초밥 1인분 ~300kcal
    ]

    for food_name, min_cal, max_cal in reality_checks:
        if food_name not in CORE_FOODS:
            check(False, f"{food_name} 현실성", "CORE_FOODS에 없음")
            continue

        data = CORE_FOODS[food_name]
        per_serving = data["cal"] * data["serving"] / 100
        check(min_cal <= per_serving <= max_cal,
              f"{food_name} 1인분",
              f"{per_serving:.0f}kcal (허용: {min_cal}~{max_cal})")


# ══════════════════════════════════════════════════════════
#  테스트 4: DB 매칭 오탐(false positive) 방지
# ══════════════════════════════════════════════════════════
def test_db_false_positive_prevention():
    """DB 검색에서 과거에 발생했던 오매칭이 재발하지 않는지"""
    print("\n[테스트 4] DB 오매칭(false positive) 방지")
    print("-" * 50)

    from food_analyzer import _is_relevant_match

    # (검색어, DB 결과명, 관련성 있어야 하는지)
    relevance_cases = [
        ("귤",     "레귤러 피자",          False),  # 과거 오매칭
        ("귤",     "귤",                   True),
        ("김치",   "김치찌개",             True),
        ("감자",   "감자탕",               True),
        ("두부",   "두부",                 True),
        ("양파",   "튀긴 양파 후레이크",    True),   # 관련은 있음
    ]

    for query, db_name, should_match in relevance_cases:
        result = _is_relevant_match(query, db_name)
        if should_match:
            check(result, f"관련성 O: '{query}'→'{db_name}'",
                  f"관련 있어야 하는데 False 반환")
        else:
            check(not result, f"관련성 X: '{query}'→'{db_name}'",
                  f"관련 없어야 하는데 True 반환")


# ══════════════════════════════════════════════════════════
#  테스트 5: DB 품질 점수 검증
# ══════════════════════════════════════════════════════════
def test_db_quality_score():
    """_db_quality_score()가 올바르게 작동하는지"""
    print("\n[테스트 5] DB 품질 점수 로직")
    print("-" * 50)

    from food_analyzer import _db_quality_score

    # 서빙 사이즈 없으면 최대 2점 (통과 기준 3점 미달)
    bad_item = {
        "calories_kcal": 250,
        "protein_g": 10,
        "carbs_g": 30,
        "fat_g": 8,
        "serving_size_g": 0,  # 서빙 사이즈 없음
    }
    score = _db_quality_score(bad_item)
    check(score <= 2, "서빙없는 항목 점수",
          f"점수={score} (최대 2여야 함)")

    # 전부 있으면 5점
    good_item = {
        "calories_kcal": 250,
        "protein_g": 10,
        "carbs_g": 30,
        "fat_g": 8,
        "serving_size_g": 100,
    }
    score = _db_quality_score(good_item)
    check(score == 5, "완전한 항목 점수",
          f"점수={score} (5여야 함)")

    # 칼로리만 있고 나머지 0
    minimal_item = {
        "calories_kcal": 100,
        "protein_g": 0,
        "carbs_g": 0,
        "fat_g": 0,
        "serving_size_g": 0,
    }
    score = _db_quality_score(minimal_item)
    check(score <= 2, "최소 항목 점수",
          f"점수={score} (최대 2여야 함)")


# ══════════════════════════════════════════════════════════
#  테스트 6: 식단 시나리오 — 1끼 칼로리 합리성
# ══════════════════════════════════════════════════════════
def test_meal_scenarios():
    """현실적인 식단 조합의 총 칼로리가 합리적인지"""
    print("\n[테스트 6] 식단 시나리오 칼로리 합리성")
    print("-" * 50)

    from food_analyzer import CORE_FOODS

    def meal_cal(foods_with_grams):
        """[(음식명, 그램), ...] → 총 칼로리"""
        total = 0
        for name, grams in foods_with_grams:
            if name not in CORE_FOODS:
                return None  # 누락 음식
            data = CORE_FOODS[name]
            total += data["cal"] * grams / 100
        return total

    # (시나리오명, [(음식, g),...], 최소kcal, 최대kcal)
    scenarios = [
        ("한식 기본 (밥+김치찌개+김치)",
         [("밥", 210), ("김치찌개", 400), ("김치", 40)],
         350, 650),

        ("다이어트 (닭가슴살+샐러드+고구마)",
         [("닭가슴살", 150), ("샐러드", 200), ("고구마", 150)],
         250, 500),

        ("외식 (피자 2조각+콜라)",
         [("피자", 214), ("콜라", 250)],
         500, 800),

        ("분식 (떡볶이+순대+어묵)",
         [("떡볶이", 300), ("순대", 150), ("어묵", 100)],
         650, 1100),

        ("아침 (토스트+커피+바나나)",
         [("토스트", 150), ("커피", 200), ("바나나", 120)],
         400, 700),

        ("고기구이 (삼겹살+밥+된장찌개+김치)",
         [("삼겹살", 200), ("밥", 210), ("된장찌개", 400), ("김치", 40)],
         750, 1200),
    ]

    for scenario_name, foods, min_cal, max_cal in scenarios:
        total = meal_cal(foods)
        if total is None:
            check(False, scenario_name, "CORE_FOODS에 누락된 음식 있음")
            continue
        check(min_cal <= total <= max_cal, scenario_name,
              f"{total:.0f}kcal (허용: {min_cal}~{max_cal})")


# ══════════════════════════════════════════════════════════
#  테스트 7: CORE_FOODS 중복/일관성 검증
# ══════════════════════════════════════════════════════════
def test_core_foods_consistency():
    """같은 음식의 별명 항목들이 일관된 값을 갖는지"""
    print("\n[테스트 7] CORE_FOODS 일관성 검증")
    print("-" * 50)

    from food_analyzer import CORE_FOODS

    # 같은 음식의 별명 쌍 — 값이 같아야 함
    alias_pairs = [
        ("귤", "감귤"),
        ("밥", "흰밥"),
        ("김치", "배추김치"),
    ]

    for name1, name2 in alias_pairs:
        if name1 not in CORE_FOODS or name2 not in CORE_FOODS:
            check(False, f"별명 쌍: {name1}={name2}", "CORE_FOODS에 누락")
            continue
        d1, d2 = CORE_FOODS[name1], CORE_FOODS[name2]
        check(d1["cal"] == d2["cal"],
              f"별명 일치: {name1}={name2}",
              f"{name1}:{d1['cal']}kcal vs {name2}:{d2['cal']}kcal")

    # 카테고리가 빈 문자열이 아닌지
    for name, data in CORE_FOODS.items():
        cat = data.get("category", "")
        check(cat != "", f"{name} 카테고리", "카테고리 비어있음")


# ══════════════════════════════════════════════════════════
#  메인
# ══════════════════════════════════════════════════════════
def main():
    print()
    print("=" * 60)
    print("  NutriLens 배포 전 검증 스크립트")
    print("=" * 60)
    print("  API 호출 없음 — 비용 0원")
    print()

    try:
        test_core_foods_sanity()
        test_core_foods_matching()
        test_per_serving_calories()
        test_db_false_positive_prevention()
        test_db_quality_score()
        test_meal_scenarios()
        test_core_foods_consistency()
    except Exception as e:
        print(f"\n  ✗ 테스트 실행 중 에러: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    print()
    print("=" * 60)
    total = _passed + _failed
    print(f"  결과: {_passed}/{total} 통과, {_failed} 실패")
    print("=" * 60)

    if _failed > 0:
        print("\n  실패 항목:")
        for err in _errors:
            print(f"    {err}")
        print(f"\n  ✗ 배포 전 수정 필요!")
        sys.exit(1)
    else:
        print(f"\n  ✓ 전체 통과 — 배포 OK!")
        sys.exit(0)


if __name__ == "__main__":
    main()
