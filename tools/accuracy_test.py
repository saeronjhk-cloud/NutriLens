#!/usr/bin/env python3
"""
NutriLens 정확도 테스트 도구 v3 (P0-2, 2026-05-05)
──────────────────────────────────────────────────
food_analyzer의 전체 파이프라인(analyze_food_image + match_with_db)을 거쳐
실제 사용자에게 표시되는 결과의 정확도를 측정.

두 가지 모드:

  1. DB 매칭 테스트 (--db)
     → 흔한 음식 100개로 DB에서 영양정보를 찾을 수 있는지 확인
     → API 비용 없음, 즉시 실행

  2. 실제 사진 테스트 (--photo)
     → .tmp/test_images/ 폴더의 사진을 GPT-4o + 전체 파이프라인으로 분석
     → 파일명이 정답 (예: "01_김치.jpg" → 정답: "김치")
     → 측정 항목:
        - 음식명 인식 정확도 (정확/부분/실패)
        - 영양소 정확도 (칼로리 ±20% 이내)
        - 매칭 소스 분포 (GOLD_REF / GOLD_DB / DB_MATCHED / AI_ESTIMATED)

실행법:
  python tools/accuracy_test.py --db       # DB 매칭 (무료)
  python tools/accuracy_test.py --photo    # 사진 인식 (~$0.005/장)
  python tools/accuracy_test.py --all      # 둘 다
"""

import os
import sys
import json
import time
from pathlib import Path
from datetime import datetime

# ── 경로 설정 ──
PROJECT_DIR = Path(__file__).parent.parent
TOOLS_DIR = Path(__file__).parent
TEST_DIR = PROJECT_DIR / ".tmp" / "test_images"
RESULT_DIR = PROJECT_DIR / ".tmp"
sys.path.insert(0, str(TOOLS_DIR))


# ── .env 로드 ──
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


# ══════════════════════════════════════════════════════════
#  1. DB 매칭 테스트 (Gold Table 우선)
# ══════════════════════════════════════════════════════════

# 한국인이 자주 먹는 100개 음식 (한식 50 + 다이어트 20 + 외식 15 + 간식 15)
COMMON_FOODS = [
    # 한식 (50개)
    "김치", "비빔밥", "불고기", "김밥", "떡볶이",
    "된장찌개", "삼겹살", "잡채", "순두부찌개", "갈비탕",
    "냉면", "김치찌개", "제육볶음", "파전", "감자탕",
    "칼국수", "삼계탕", "족발", "라면", "닭갈비",
    "볶음밥", "만두", "육회", "떡국", "순대",
    "치킨", "해물탕", "오징어볶음", "떡", "김치볶음밥",
    "갈비찜", "보쌈", "콩나물국", "미역국", "어묵",
    "김치전", "잔치국수", "비빔냉면", "소불고기", "돼지갈비",
    "멸치볶음", "두부조림", "감자조림", "계란찜", "깍두기",
    "시금치나물", "콩나물무침", "오이소박이", "무생채", "열무김치",
    # 다이어트/피트니스 (20개)
    "닭가슴살", "샐러드", "고구마", "계란", "현미밥",
    "오트밀", "연어", "아보카도", "두부", "바나나",
    "그릭요거트", "브로콜리", "퀴노아", "단호박", "토마토",
    "블루베리", "아몬드", "땅콩버터", "치아시드", "단백질보충제",
    # 프랜차이즈/외식 (15개)
    "햄버거", "피자", "파스타", "초밥", "돈까스",
    "짜장면", "짬뽕", "탕수육", "양장피", "마라탕",
    "쌀국수", "카레", "우동", "스테이크", "리조또",
    # 간식/음료 (15개)
    "아메리카노", "카페라떼", "녹차", "콜라", "사이다",
    "떡케이크", "호떡", "붕어빵", "아이스크림", "초콜릿",
    "과일주스", "스무디", "쿠키", "마카롱", "크로와상",
]


def run_db_test():
    """DB 매칭 정확도 — Gold Table(_search_gold) 사용.

    food_analyzer의 _search_gold가 진짜 사용되는 매칭 함수이므로
    여기서도 그것을 그대로 사용해야 의미 있는 측정.
    """
    from food_analyzer import _search_gold, load_food_db

    # SQLite DB 연결 (gold 테이블만 쓸 거지만 _search_gold가 SQLite도 fallback으로 봄)
    load_food_db()

    print()
    print("=" * 60)
    print("  NutriLens DB 매칭 테스트 (Gold Table)")
    print("=" * 60)
    print(f"  테스트 음식: {len(COMMON_FOODS)}종")
    print()

    results = []
    found = 0
    not_found = 0
    by_source = {"core": 0, "gold": 0, "mfds": 0, "none": 0}

    for food_name in COMMON_FOODS:
        gold_key, gold_data = _search_gold(food_name)
        if gold_data:
            source = gold_data.get('source', 'gold')
            by_source[source] = by_source.get(source, 0) + 1
            found += 1
            results.append({
                "food": food_name, "status": "FOUND",
                "matched": gold_key, "source": source,
                "kcal_per_100g": gold_data.get('cal'),
            })
            tag = "★" if source == "core" else "○"
            print(f"  {tag} {food_name} → {gold_key} ({source}, {gold_data.get('cal')}kcal/100g)")
        else:
            by_source["none"] += 1
            not_found += 1
            results.append({"food": food_name, "status": "MISS"})
            print(f"  ✗ {food_name} → 매칭 없음")

    total = len(COMMON_FOODS)
    coverage_pct = found / total * 100

    print()
    print("=" * 60)
    print(f"  DB 매칭 결과")
    print("=" * 60)
    print(f"  총 테스트: {total}종")
    print(f"  매칭 성공: {found}종 ({coverage_pct:.1f}%)")
    print(f"    ★ CORE_FOODS (수동 검증): {by_source.get('core', 0)}종")
    print(f"    ○ Gold Table:           {by_source.get('gold', 0)}종")
    print(f"  매칭 실패: {not_found}종")
    print("=" * 60)

    missed = [r["food"] for r in results if r["status"] == "MISS"]
    if missed:
        print(f"\n  매칭 안 된 음식 (CORE_FOODS 추가 후보):")
        for m in missed:
            print(f"    - {m}")

    # 리포트 저장
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    report = {
        "test_type": "db_matching",
        "date": datetime.now().isoformat(),
        "test_count": total,
        "matched": found,
        "not_found": not_found,
        "coverage_pct": round(coverage_pct, 1),
        "by_source": by_source,
        "missed_foods": missed,
        "details": results,
    }

    json_path = RESULT_DIR / "db_test_results.json"
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n  결과 저장: {json_path}")

    return coverage_pct


# ══════════════════════════════════════════════════════════
#  2. 사진 인식 + 영양소 정확도 (전체 파이프라인)
# ══════════════════════════════════════════════════════════

def normalize(name):
    """공백·언더스코어 제거 + 소문자"""
    return name.strip().lower().replace(" ", "").replace("_", "")


def match_strictness(expected, ai_name):
    """매칭 엄격도 분류.

    - EXACT: 정확히 같음
    - CONTAINS: 한쪽이 다른 쪽에 포함 + 짧은 쪽이 긴 쪽의 60% 이상
              (예: "비빔밥"(3) in "돌솥비빔밥"(5) = 60% -> CONTAINS)
    - LOOSE: 한쪽이 다른 쪽에 포함되지만 길이 차가 큼 OR 앞 2글자만 같음
            (예: "김치"(2) in "김치찌개"(4) = 50% -> LOOSE, 별개 음식)
    - NONE: 매칭 안 됨
    """
    e = normalize(expected)
    a = normalize(ai_name)
    if not e or not a:
        return "NONE"
    if e == a:
        return "EXACT"
    if e in a or a in e:
        shorter, longer = (e, a) if len(e) <= len(a) else (a, e)
        if len(longer) > 0 and len(shorter) / len(longer) >= 0.6:
            return "CONTAINS"
        return "LOOSE"
    if len(e) >= 2 and len(a) >= 2 and e[:2] == a[:2]:
        return "LOOSE"
    return "NONE"


def find_best_match(expected, ai_foods):
    """AI가 인식한 음식 목록에서 정답과 가장 잘 맞는 것 찾기."""
    best = None
    best_strictness = "NONE"
    strictness_rank = {"EXACT": 3, "CONTAINS": 2, "LOOSE": 1, "NONE": 0}

    for f in ai_foods:
        ai_name = f.get("name_ko", "") or f.get("name", "")
        s = match_strictness(expected, ai_name)
        if strictness_rank[s] > strictness_rank[best_strictness]:
            best = f
            best_strictness = s

    return best, best_strictness


def expected_kcal(food_name):
    """CORE_FOODS에서 정답 칼로리(1인분 기준) 추출. 없으면 None."""
    from food_analyzer import CORE_FOODS, _search_core_foods
    key, data = _search_core_foods(food_name)
    if not data:
        # 직접 lookup
        if food_name in CORE_FOODS:
            data = CORE_FOODS[food_name]
        else:
            return None
    serving = data.get('serving', 100)
    cal_per_100g = data.get('cal', 0)
    return round(cal_per_100g * serving / 100)


def kcal_accuracy(expected_kcal_val, actual_kcal):
    """칼로리 정확도 — ±20% 이내면 OK"""
    if expected_kcal_val is None or expected_kcal_val == 0:
        return "UNKNOWN"
    if actual_kcal == 0:
        return "BAD"
    diff_pct = abs(actual_kcal - expected_kcal_val) / expected_kcal_val * 100
    if diff_pct <= 20:
        return "GOOD"
    if diff_pct <= 50:
        return "OK"
    return "BAD"


def run_photo_test():
    """전체 파이프라인으로 사진 정확도 측정."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("  OPENAI_API_KEY가 설정되지 않았습니다.")
        return None

    if not TEST_DIR.exists():
        TEST_DIR.mkdir(parents=True, exist_ok=True)

    images = sorted(
        [f for f in TEST_DIR.iterdir() if f.suffix.lower() in ('.jpg', '.jpeg', '.png')]
    )

    if not images:
        print()
        print("=" * 60)
        print("  사진 테스트 — 이미지 없음")
        print("=" * 60)
        print(f"\n  {TEST_DIR} 폴더에 음식 사진을 넣으세요.")
        print("  파일명 = 정답 음식명 (예: 01_김치.jpg, 비빔밥.jpg)")
        return None

    # food_analyzer 임포트 (전체 파이프라인 사용)
    from food_analyzer import analyze_food_image, match_with_db, load_food_db

    print()
    print("=" * 60)
    print("  NutriLens 사진 인식 테스트 (전체 파이프라인)")
    print("=" * 60)
    print(f"  테스트 이미지: {len(images)}장")
    print(f"  모델: GPT-4o Vision")
    print(f"  파이프라인: GPT 인식 → match_with_db (Gold/DB/AI)")
    cost_est = len(images) * 0.005
    print(f"  예상 비용: ~${cost_est:.2f}")
    print()

    foods_db = load_food_db()

    results = []
    correct_exact = 0
    correct_loose = 0
    wrong = 0
    errors = 0
    by_source = {"GOLD_REF": 0, "GOLD_DB": 0, "DB_MATCHED": 0, "AI_ESTIMATED": 0, "?": 0}
    kcal_dist = {"GOOD": 0, "OK": 0, "BAD": 0, "UNKNOWN": 0}

    for i, img_path in enumerate(images, 1):
        # 파일명에서 정답 추출 (예: "01_김치" → "김치")
        expected_name = img_path.stem
        if "_" in expected_name and expected_name.split("_")[0].isdigit():
            expected_name = expected_name.split("_", 1)[1]

        print(f"  [{i:02d}/{len(images)}] {expected_name}...", end=" ", flush=True)

        # 전체 파이프라인 호출
        analysis = analyze_food_image(str(img_path), api_key=api_key)

        if "error" in analysis:
            print(f"에러: {analysis['error'][:60]}")
            results.append({
                "expected": expected_name, "result": "ERROR",
                "error": analysis['error'][:200],
            })
            errors += 1
            time.sleep(1)
            continue

        # match_with_db로 후처리 (실제 사용자 화면과 동일)
        if foods_db:
            analysis = match_with_db(analysis, foods_db)

        ai_foods = analysis.get("foods", [])
        best, strictness = find_best_match(expected_name, ai_foods)

        # 정확도 카운트
        if strictness == "EXACT":
            correct_exact += 1
            tag = "✓ EXACT"
        elif strictness == "CONTAINS":
            correct_loose += 1
            tag = "△ CONTAINS"
        elif strictness == "LOOSE":
            wrong += 1  # LOOSE는 의심스러우니 오답으로 분류
            tag = "? LOOSE"
        else:
            wrong += 1
            tag = "✗ MISS"

        # 매칭 소스
        source = best.get("source", "?") if best else "?"
        by_source[source] = by_source.get(source, 0) + 1

        # 칼로리 정확도
        exp_kcal = expected_kcal(expected_name)
        actual_kcal = best.get("calories_kcal", 0) if best else 0
        kcal_status = kcal_accuracy(exp_kcal, actual_kcal)
        kcal_dist[kcal_status] = kcal_dist.get(kcal_status, 0) + 1

        ai_name_str = (best.get("name_ko", "?") if best else "인식 없음")
        print(f"{tag} (AI:{ai_name_str}, src:{source}, {actual_kcal}kcal vs {exp_kcal})")

        results.append({
            "expected": expected_name,
            "ai_name": ai_name_str,
            "match": strictness,
            "source": source,
            "kcal_actual": actual_kcal,
            "kcal_expected": exp_kcal,
            "kcal_status": kcal_status,
            "confidence": best.get("confidence", 0) if best else 0,
        })
        time.sleep(1)

    total = len(results)
    name_accuracy = (correct_exact + correct_loose) / total * 100 if total > 0 else 0
    strict_accuracy = correct_exact / total * 100 if total > 0 else 0
    kcal_accuracy_pct = (kcal_dist.get("GOOD", 0)) / total * 100 if total > 0 else 0

    print()
    print("=" * 60)
    print(f"  사진 인식 결과")
    print("=" * 60)
    print(f"  총 테스트:        {total}장")
    print(f"  ✓ EXACT 일치:     {correct_exact}장 ({correct_exact/total*100:.1f}%)")
    print(f"  △ CONTAINS 일치:  {correct_loose}장")
    print(f"  ✗ 오답:           {wrong}장")
    print(f"  ! 에러:           {errors}장")
    print()
    print(f"  음식명 정확도 (엄격): {strict_accuracy:.1f}%")
    print(f"  음식명 정확도 (관대): {name_accuracy:.1f}%")
    print()
    print(f"  매칭 소스 분포:")
    for src, count in by_source.items():
        if count > 0:
            print(f"    {src}: {count}장")
    print()
    print(f"  칼로리 정확도 (±20% = GOOD):")
    for status, count in kcal_dist.items():
        if count > 0:
            print(f"    {status}: {count}장")
    print(f"  칼로리 정확도 (GOOD 비율): {kcal_accuracy_pct:.1f}%")
    print("=" * 60)

    # 리포트 저장
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    report = {
        "test_type": "photo_recognition_full_pipeline",
        "date": datetime.now().isoformat(),
        "model": "gpt-4o",
        "total": total,
        "correct_exact": correct_exact,
        "correct_loose": correct_loose,
        "wrong": wrong,
        "errors": errors,
        "name_accuracy_strict_pct": round(strict_accuracy, 1),
        "name_accuracy_loose_pct": round(name_accuracy, 1),
        "kcal_accuracy_pct": round(kcal_accuracy_pct, 1),
        "by_source": by_source,
        "kcal_distribution": kcal_dist,
        "details": results,
    }

    json_path = RESULT_DIR / "photo_test_results.json"
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n  결과 저장: {json_path}")

    # 텍스트 리포트
    report_path = RESULT_DIR / "accuracy_report.txt"
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write("NutriLens 정확도 테스트 리포트 (전체 파이프라인)\n")
        f.write(f"테스트 일시: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        f.write(f"모델: GPT-4o Vision + match_with_db\n")
        f.write("=" * 60 + "\n\n")
        f.write(f"총 테스트: {total}장\n")
        f.write(f"음식명 정확도 (엄격): {strict_accuracy:.1f}%\n")
        f.write(f"음식명 정확도 (관대): {name_accuracy:.1f}%\n")
        f.write(f"칼로리 정확도 (±20%): {kcal_accuracy_pct:.1f}%\n\n")
        f.write("매칭 소스:\n")
        for src, count in by_source.items():
            if count > 0:
                f.write(f"  {src}: {count}장\n")
        f.write("\n" + "-" * 60 + "\n")
        for r in results:
            mark = {"EXACT": "✓", "CONTAINS": "△", "LOOSE": "?", "NONE": "✗"}.get(r.get("match", "NONE"), "?")
            f.write(f"{mark} {r['expected']} → {r.get('ai_name', '?')} "
                    f"(src:{r.get('source', '?')}, "
                    f"{r.get('kcal_actual', 0)}kcal vs 정답 {r.get('kcal_expected', '?')}, "
                    f"{r.get('kcal_status', '?')})\n")
    print(f"  텍스트 리포트: {report_path}")

    return strict_accuracy


# ══════════════════════════════════════════════════════════
#  메인
# ══════════════════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(description="NutriLens 정확도 테스트 v3 (P0-2)")
    parser.add_argument("--db", action="store_true", help="DB 매칭 테스트 (무료, 즉시)")
    parser.add_argument("--photo", action="store_true", help="사진 인식 테스트 (~$0.005/장)")
    parser.add_argument("--all", action="store_true", help="둘 다")
    args = parser.parse_args()

    if not (args.db or args.photo or args.all):
        print()
        print("NutriLens 정확도 테스트 v3 (P0-2, 2026-05-05)")
        print()
        print("사용법:")
        print("  python tools/accuracy_test.py --db      # DB 매칭 (무료)")
        print("  python tools/accuracy_test.py --photo   # 사진 인식 (~$0.005/장)")
        print("  python tools/accuracy_test.py --all     # 둘 다")
        print()
        print(f"사진 위치: {TEST_DIR}")
        print("파일명 = 정답 (예: 01_김치.jpg → 정답 '김치')")
        sys.exit(0)

    if args.db or args.all:
        cov = run_db_test()
        if cov is not None:
            mark = "✓" if cov >= 90 else "△" if cov >= 70 else "✗"
            print(f"\n  {mark} DB 매칭 커버리지: {cov:.1f}%")

    if args.photo or args.all:
        acc = run_photo_test()
        if acc is not None:
            mark = "✓" if acc >= 80 else "△" if acc >= 60 else "✗"
            print(f"\n  {mark} 음식명 정확도(엄격): {acc:.1f}%")


if __name__ == "__main__":
    main()
