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


# ══════════════════════════════════════════════════════════════════════════
# 기준선 v1 평가셋 (32장) — 세션46 신설
# ──────────────────────────────────────────────────────────────────────────
# ★ 왜 명시 목록이 필요한가 (2026-08-19 실측으로 발견):
#   IP/165 §5 의 G4 게이트는 「32장 EXACT 59.4% ±6pt 유지」다. 그런데 세션43(2026-08-04)에
#   `.tmp/test_images/` 에 59장이 추가되어 지금은 **91장**이다(IP/170 §자산표).
#   폴더를 전수 스캔하면 91장이 돌아가고, 그 EXACT% 는 59.4% 와 비교할 수 있는 수가 아니다.
#   (분모도 구성도 다르다. 비용도 ~$0.16 → ~$0.46 으로 3배)
#   → 게이트 판정은 반드시 이 32장으로만 한다. 목록은 기준선 정본
#     `IP/nutrilens_baseline_v1_2026-07-23.md` 및 2026-07-24 재측정 결과와 일치한다.
BASELINE_V1_32 = [
    '01_김치', '02_비빔밥', '101_콘치즈', '102_조기구이', '103_쭈꾸미볶음',
    '104_등갈비강정', '105_갈비탕', '106_황태구이', '107_양배추샐러드', '108_고등어구이',
    '109_김치전골', '110_돌솥비빔밥', '111_라면', '32_샐러드', '38_아보카도',
    '40_바나나', '44_브로콜리', '50_햄버거', '51_피자', '59_타코',
    '63_핫도그', '64_감자튀김', '68_사과', '69_오렌지', '70_블루베리',
    '71_키위', '72_딸기', '78_아이스크림', '79_와플', '92_카페라떼',
    '97_초콜릿', '98_쿠키',
]


def run_photo_test(photo_set="baseline32", preprocess="raw"):
    """전체 파이프라인으로 사진 정확도 측정.

    photo_set:
      'baseline32' — IP/165 G4 게이트용. 기준선 v1 과 직접 비교 가능한 32장. (기본값)
      'all'        — 폴더 전수(91장). 탐색용. G4 판정에 쓰지 말 것.

    preprocess:
      'raw'        — (기본) 원본·detail:high·무크롭. 59.4% 기준선이 이 조건이다.
      'production' — GPT-4o 에게 프로덕션과 같은 768px·detail:low·center-crop 을 준다
                     (IP/174 §4-3). 엔진 입력은 두 모드 모두 원본이다.
                     ⚠ 결과를 59.4% 와 같은 표에 올리지 말 것. 별도 기준선이다(규칙34).
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("  OPENAI_API_KEY가 설정되지 않았습니다.")
        return None, True

    if not TEST_DIR.exists():
        TEST_DIR.mkdir(parents=True, exist_ok=True)

    images = sorted(
        [f for f in TEST_DIR.iterdir() if f.suffix.lower() in ('.jpg', '.jpeg', '.png')]
    )

    if photo_set == "baseline32":
        want = set(BASELINE_V1_32)
        picked = [f for f in images if f.stem in want]
        missing = sorted(want - {f.stem for f in picked})
        if missing:
            # 게이트 셋이 깨졌으면 조용히 적은 장수로 돌리지 않는다.
            # 분모가 달라진 EXACT% 를 59.4% 와 비교하는 것이 이 게이트의 유일한 실패 방식이다.
            print()
            print("=" * 60)
            print(f"  ★ 기준선 v1 32장 중 {len(missing)}장이 없습니다 — 중단합니다.")
            print("=" * 60)
            for m in missing:
                print(f"    없음: {m}.jpg")
            print("\n  G4 판정은 32장 전체가 있어야 성립합니다(IP/165 §5).")
            print("  탐색만 하려면: python tools/accuracy_test.py --photo --set all")
            return None, True
        images = picked

    if not images:
        print()
        print("=" * 60)
        print("  사진 테스트 — 이미지 없음")
        print("=" * 60)
        print(f"\n  {TEST_DIR} 폴더에 음식 사진을 넣으세요.")
        print("  파일명 = 정답 음식명 (예: 01_김치.jpg, 비빔밥.jpg)")
        return None, True

    # food_analyzer 임포트 (전체 파이프라인 사용)
    from food_analyzer import analyze_food_image, match_with_db, load_food_db

    print()
    print("=" * 60)
    print("  NutriLens 사진 인식 테스트 (전체 파이프라인)")
    print("=" * 60)
    print(f"  평가셋: {photo_set}"
          + ("  (IP/165 G4 게이트 · 기준선 v1 과 비교 가능)" if photo_set == "baseline32"
             else "  ⚠ 탐색용 — 59.4% 기준선과 비교하지 말 것"))
    print(f"  테스트 이미지: {len(images)}장")
    print(f"  모델: GPT-4o Vision")
    if preprocess == "production":
        print(f"  전처리: production — GPT-4o 에 768px·detail:low·center-crop(IP/174 §4-3)")
        print(f"          엔진 입력은 원본 그대로 (프로덕션과 동일)")
        print(f"  ⚠ 이 결과를 59.4% 와 같은 표에 올리지 마십시오. 별도 기준선입니다.")
    else:
        print(f"  전처리: raw — 원본·detail:high·무크롭 (59.4% 기준선 조건)")
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

    # ── food30 엔진 텔레메트리 (세션46 신설, IP/166 v2) ──────────────────────
    # ★ 이게 없으면 G4 를 돌려도 「엔진이 몇 건을 바꿨는가」를 알 수 없다.
    #   EXACT% 만 보면 «교체 0건이라 그대로»와 «교체는 됐는데 우연히 같음»이
    #   구분되지 않는다 — IP/172 §3-1 의 진단 순서가 성립하지 않는다.
    f30 = {
        "photos_with_engine_field": 0,   # apply_food30_override 가 실제로 돈 사진 수
        "detected_rice": 0, "detected_soup": 0,
        "changed": 0,                    # 이름이 실제로 바뀐 건수
        "already_correct": 0,            # 엔진과 GPT 가 일치 (changed=False)
        "disagreement": 0,               # 엔진은 검출, GPT 응답에 해당 계열 없음
        "no_db_key": 0, "preempted": 0,
        "to_class": {},                  # 교체 도착지 분포 — 「혼동 흡수처」 감시 (IP/172 결정1)
        "events": [],                    # 사진별 원본 기록
    }

    for i, img_path in enumerate(images, 1):
        # 파일명에서 정답 추출 (예: "01_김치" → "김치")
        expected_name = img_path.stem
        if "_" in expected_name and expected_name.split("_")[0].isdigit():
            expected_name = expected_name.split("_", 1)[1]

        print(f"  [{i:02d}/{len(images)}] {expected_name}...", end=" ", flush=True)

        # 전체 파이프라인 호출
        # allow_raw=True: 오프라인 CLI 평가 전용(가드 설계 참조). 평가 이미지는
        # 사용자 데이터가 아닌 자체 테스트셋이므로 최소화 미적용 전송 허용.
        # 사진별 소요시간을 남깁니다 — 2026-08-21 타임아웃 사고 때 「어느 사진이 얼마나
        # 걸리는가」를 알 수 없어 원인 규명이 늦어졌습니다.
        _t_start = time.time()
        analysis = analyze_food_image(str(img_path), api_key=api_key, allow_raw=True,
                                      preprocess=preprocess)
        _elapsed = time.time() - _t_start

        if "error" in analysis:
            print(f"[{_elapsed:5.1f}s] 에러: {analysis['error'][:110]}")
            results.append({
                "expected": expected_name, "result": "ERROR",
                "error": analysis['error'][:300],
                "elapsed_s": round(_elapsed, 1),
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

        # ── food30 엔진 기록 ──────────────────────────────────────────────
        # analysis 는 match_with_db 가 제자리 수정하므로 top-level 키가 보존된다.
        # 엔진이 비활성(모델 없음·FOOD30_ENGINE=0)이면 이 키 자체가 없다 → 그것도 신호다.
        eng = analysis.get("food30_engine")
        eng_note = ""
        if isinstance(eng, dict):
            # 타입 가드 — 이 페이로드는 apply_food30_override 가 만들지만,
            # 유료 실행이 집계 한 줄 때문에 통째로 죽고 아무것도 저장되지 않는
            # 사태를 막습니다(2026-08-19 독립감사 경-1: 크래시 3종 · 오집계 1종 실측).
            f30["photos_with_engine_field"] += 1
            det = eng.get("detected")
            det = det if isinstance(det, dict) else {}
            for _slot in ("rice", "soup"):
                if isinstance(det.get(_slot), dict):
                    f30[f"detected_{_slot}"] += 1
            applied = eng.get("applied")
            applied = applied if isinstance(applied, list) else []
            for a in applied:
                if not isinstance(a, dict):
                    continue
                if a.get("changed"):
                    f30["changed"] += 1
                    _to = a.get("to")
                    if not isinstance(_to, str):
                        _to = "(이름없음)"        # dict 면 unhashable, None 이면 JSON 에 "null"
                    f30["to_class"][_to] = f30["to_class"].get(_to, 0) + 1
                else:
                    f30["already_correct"] += 1
            for _k in ("disagreement", "no_db_key", "preempted"):
                _v = eng.get(_k)
                # 문자열은 len() 이 돌지만 글자 수를 세게 됩니다 — 조용한 오집계.
                f30[_k] += len(_v) if isinstance(_v, list) else 0
            _chg = [f"{a.get('from')}->{a.get('to')}"
                    for a in applied if isinstance(a, dict) and a.get("changed")]
            if _chg:
                eng_note = "  [food30] " + ", ".join(_chg)
            f30["events"].append({
                "photo": img_path.stem,
                "detected": det,
                "applied": applied,
                "disagreement": eng.get("disagreement") or [],
                "preempted": eng.get("preempted") or [],
            })

        print(f"[{_elapsed:5.1f}s] {tag} (AI:{ai_name_str}, src:{source}, "
              f"{actual_kcal}kcal vs {exp_kcal}){eng_note}")

        results.append({
            "food30_engine": eng if isinstance(eng, dict) else None,
            "expected": expected_name,
            "ai_name": ai_name_str,
            "match": strictness,
            "source": source,
            "kcal_actual": actual_kcal,
            "kcal_expected": exp_kcal,
            "kcal_status": kcal_status,
            "elapsed_s": round(_elapsed, 1),
            "confidence": best.get("confidence", 0) if best else 0,
            # 세션36: MISS 원인 분석용 — 엔진이 실제로 감지한 음식명 전체를 기록.
            # "인식 없음" = 감지 0이 아니라 기대명과 매칭 실패(오인식 포함)일 수 있음.
            "ai_foods_detected": [f.get("name_ko", "?") for f in ai_foods],
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
    print(f"  ✓ EXACT 일치:     {correct_exact}장 ({strict_accuracy:.1f}%)")
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

    # ── food30 엔진 요약 (세션46 신설) ──────────────────────────────────────
    print()
    print("=" * 60)
    print("  food30 엔진 (IP/166 v2 · tau=0.70)")
    print("=" * 60)
    if f30["photos_with_engine_field"] == 0:
        print("  ★ 엔진이 한 번도 돌지 않았습니다 (food30_engine 필드 0건).")
        print("    → 모델 미로드 / FOOD30_ENGINE=0 / 배선 누락 중 하나입니다.")
        print("    → 이 상태의 EXACT% 는 '엔진 적용 전 기준선'이지 G4 결과가 아닙니다.")
    else:
        print(f"  엔진이 돈 사진:      {f30['photos_with_engine_field']}/{total}장")
        print(f"  검출 (밥류):         {f30['detected_rice']}건")
        print(f"  검출 (탕류):         {f30['detected_soup']}건")
        print(f"  ★ 이름 교체:         {f30['changed']}건")
        print(f"  이미 정답 (무변경):   {f30['already_correct']}건")
        print(f"  불일치 (추가 안 함):  {f30['disagreement']}건")
        print(f"  DB 키 없음:          {f30['no_db_key']}건")
        print(f"  선점으로 미교체:      {f30['preempted']}건")
        if f30["to_class"]:
            print()
            print("  교체 도착지 분포 (혼동 흡수처 감시 — IP/172 결정1):")
            for k, v in sorted(f30["to_class"].items(), key=lambda x: -x[1]):
                print(f"    {k}: {v}건")
        if f30["changed"] == 0:
            print()
            print("  ※ 교체 0건입니다. 배선은 살아 있으나 아무것도 바꾸지 않았습니다.")
            print("    disagreement 가 크면 허용목록 커버리지를 의심하십시오(IP/172 미결).")
    print("=" * 60)

    # ── G4 게이트 판정 (세션46 신설) ────────────────────────────────────────
    # IP/165 §5: 「통합 후 32장 EXACT 59.4% ±6pt 유지」. ±6pt 는 알려진 런투런 노이즈
    # (07-23 59.4 vs 07-24 53.1, 같은 32장 · IP/nutrilens_miss5_진단_2026-07-24).
    gate_failed = False

    # ══════════════════════════════════════════════════════════════════════
    # ★ 0단계 — 측정이 성립했는가. 2026-08-24 세션48 신설.
    # ══════════════════════════════════════════════════════════════════════
    # 실제로 이 사고가 났습니다: 32장 전부 API 호출 실패(프록시 차단)인데
    # `errors=32`, `EXACT 0.0%` 로 결과 파일이 **정상 저장되고 exit 0** 이었습니다.
    # 그리고 그 0.0% 가 「프로덕션 조건 새 기준선」이라는 이름을 달았습니다.
    #
    # errors 는 이미 세고 있었지만 **어디에서도 판정에 쓰이지 않았습니다.**
    # 세는 것과 판정에 쓰는 것은 다른 일입니다.
    #
    # 임계: 절반. 32장 중 1~2장의 일시적 타임아웃은 재시도로 흡수되는 알려진
    # 현상이고(IP/173, 2026-08-21), 그걸 실패로 만들면 게이트가 너무 잘 깨집니다.
    # 절반이 죽었다면 그건 네트워크·키·페이로드 문제이고 측정이 아닙니다.
    measurement_broken = (total > 0 and errors >= total / 2)
    if measurement_broken:
        print()
        print("=" * 60)
        print(f"  ★ 측정 실패 — {total}장 중 {errors}장이 에러입니다.")
        print("=" * 60)
        print("  이 실행의 숫자는 모델 성능이 아니라 **호출 실패**를 반영합니다.")
        print("  기준선으로 기록하지 마십시오. IP 문서에 append 하지 마십시오.")
        print()
        print("  먼저 볼 것:")
        print("   1) OPENAI_API_KEY 가 유효한가 (.env 또는 환경변수)")
        print("   2) 네트워크에서 api.openai.com 에 닿는가")
        print("   3) 위 개별 에러 메시지의 예외 클래스명 (ProxyError / ConnectTimeout /")
        print("      ReadTimeout 은 원인이 다릅니다 — 2026-08-21 사고 참조)")
        print("=" * 60)

    if photo_set == "baseline32" and preprocess != "raw":
        # 세션48: 전처리를 바꾸면 기준선(19/32)과 비교할 수 없다. 게이트를 아예 돌리지
        # 않고, 대신 왜 안 도는지 화면에 남긴다 — 조용히 건너뛰면 다음 사람이
        # 「PASS 였겠지」로 읽는다(규칙34 · IP/174 §4-3).
        print()
        print("=" * 60)
        print(f"  G4 게이트 판정 — 건너뜀 (preprocess={preprocess})")
        print("=" * 60)
        print("  G4 기준선 19/32 는 raw(원본·detail:high·무크롭) 조건의 값입니다.")
        print("  전처리가 다른 실행을 그 기준으로 판정하면 게이트가 무의미해집니다.")
        print("  이 실행은 별도 기준선으로 기록하십시오(IP/165 §7 에 append).")
        print("=" * 60)
    elif photo_set == "baseline32":
        # ── ★ 왜 %가 아니라 '장수'로 판정하는가 (2026-08-19 독립감사 치명-1) ──
        # IP/165 §5 는 「59.4% ±6pt」라고 적혀 있고, 그 ±6pt 의 근거는
        # 「07-23 19/32=59.4% vs 07-24 17/32=53.1%, 같은 32장」입니다
        # (IP/nutrilens_miss5_진단_2026-07-24).
        #
        # 그런데 59.4 - 6.0 = 53.4 이고, 근거가 된 17/32 는 53.125% 입니다.
        # **±6pt 를 그대로 %로 적용하면 그 근거 자체가 FAIL 합니다.** 0.275pt 차이로.
        # 분모가 32 라 가능한 값이 3.125pt 간격이기 때문입니다 — 53.4 는
        # 애초에 도달할 수 없는 수이고, 실질 컷은 18/32(56.25%)가 됩니다.
        # 즉 문서가 「6pt 허용」이라 말하는 동안 코드는 「1장 허용」이 됩니다.
        #
        # → 알려진 노이즈(19→17, 2장)를 그대로 허용합니다. 이게 설계 의도입니다.
        #   장수로 적으면 분모 격자와 어긋날 일이 없습니다.
        G4_BASE_EXACT, G4_TOTAL = 19, 32
        G4_NOISE_PHOTOS = 2          # 07-23 19장 → 07-24 17장 (실측된 런투런 폭)
        G4_MIN_EXACT = G4_BASE_EXACT - G4_NOISE_PHOTOS      # = 17
        print()
        print("=" * 60)
        print("  G4 게이트 판정 (IP/165 §5)")
        print("=" * 60)
        print(f"  기준선 v1 (2026-07-23): EXACT {G4_BASE_EXACT}/{G4_TOTAL}  (59.4%)")
        print(f"  재측정   (2026-07-24): EXACT 17/{G4_TOTAL}  (53.1%)  ← 노이즈 하단")
        print(f"  이번 실행:             EXACT {correct_exact}/{total}  "
              f"({strict_accuracy:.1f}%)")
        print(f"  허용 하한:             {G4_MIN_EXACT}/{G4_TOTAL}  "
              f"(기준선 -{G4_NOISE_PHOTOS}장 = 실측 런투런 폭)")
        print()
        if total != G4_TOTAL:
            gate_failed = True
            print(f"  ▶ 판정 불가 — 32장이 아니라 {total}장입니다.")
        elif correct_exact >= G4_MIN_EXACT:
            print(f"  ▶ PASS — 하락 폭이 알려진 런투런 노이즈 안입니다.")
            print(f"    다음: IP/165 §7 기록표에 append (덮어쓰기 금지).")
            print(f"    반드시 함께 적을 것: 평가셋 이름 · 교체 건수 · disagreement 건수")
        else:
            gate_failed = True
            print(f"  ▶ FAIL — 기준선 대비 {G4_BASE_EXACT - correct_exact}장 하락 "
                  f"(허용 {G4_NOISE_PHOTOS}장).")
            print(f"    1) Railway 환경변수 FOOD30_ENGINE=0 (재배포 불필요)")
            print(f"    2) 위 '이름 교체' 건수를 먼저 보십시오 —")
            print(f"       0건이면 배선 문제, 0건 초과면 판별 규칙 문제 (IP/172 §3-1)")
        print("=" * 60)

    # 리포트 저장
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    report = {
        "test_type": "photo_recognition_full_pipeline",
        "date": datetime.now().isoformat(),
        "model": "gpt-4o",
        # 세션46: 어느 평가셋인지 반드시 남긴다. 32장/91장 결과가 같은 파일명으로
        # 덮어써지면 나중에 어느 쪽 숫자였는지 알 수 없다.
        "photo_set": photo_set,
        # 세션48: 전처리 조건도 반드시 남긴다. IP/174 §4 로 「엔진과 GPT-4o 가
        # 서로 다른 입력을 받고 있었다」가 드러났고, 그걸 모르면 두 실행의 숫자를
        # 같은 표에 올리게 된다(규칙34).
        "preprocess": preprocess,
        "gate_comparable": (photo_set == "baseline32" and preprocess == "raw"
                            and not measurement_broken),
        # ★ 세션48: 이 파일을 나중에 읽는 사람이 「쓸 수 있는 측정인가」를
        #   숫자를 해석하기 전에 알 수 있어야 합니다. errors 를 세어 두기만 하면
        #   다음 세션이 0.0% 를 성능으로 읽습니다(실제로 그럴 뻔했습니다).
        "usable": not measurement_broken,
        "measurement_broken_reason": (f"{errors}/{total} photos errored"
                                      if measurement_broken else None),
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
        "food30_engine_summary": f30,
        "details": results,
    }

    # 세션46: 평가셋별로 파일을 나눈다. 예전에는 32장·91장 결과가 같은 파일을
    # 덮어써서 「이 숫자가 어느 셋이었나」를 사후에 알 수 없었다.
    #   세션48: 전처리 모드도 파일명에 넣는다. production 결과가 59.4% 파일을
    #   덮으면 회귀 기준선이 사라진다 — IP/174 §4-3 이 명시적으로 금지한 사고다.
    _sfx = "" if photo_set == "baseline32" else f"_{photo_set}"
    if preprocess != "raw":
        _sfx += f"_{preprocess}"
    json_path = RESULT_DIR / f"photo_test_results{_sfx}.json"

    # ★ 덮어쓰기 전에 이전 실행을 보관한다 (세션46 신설).
    #   2026-08-19 에 실제로 사고가 났습니다 — 스모크 테스트 1회가 2026-07-24
    #   재측정 기록을 통째로 덮어썼고, 그 파일이 유일본이었습니다(.tmp 는 gitignore).
    #
    #   ⚠ 2026-08-19 독립감사 중-1/2/3 반영 — 초판에 구멍이 셋 있었습니다:
    #     · date 에 '/' 가 들어가거나 JSON 이 깨져 있으면 보관에 실패했고,
    #       그때 **새 결과도 저장하지 않고 return** 했습니다. 깨진 파일은 그대로 남으니
    #       그 뒤 모든 실행이 같은 지점에서 실패 = 영구 락. 막으려던 사고와 결과가 같습니다.
    #     · 같은 날 3회차부터 2회차가 무경고 소실 (`if not exists` 가 조용히 skip).
    #     · 텍스트 리포트는 아예 보관 대상이 아니었습니다.
    #   → 보관은 **절대 새 결과 저장을 막지 않습니다.** 실패하면 경고만 남깁니다.
    def _archive(path):
        """덮어쓰기 전 보관. 실패해도 새 결과 저장을 막지 않는다."""
        if not path.exists():
            return
        stamp = None
        if path.suffix == '.json':
            try:
                _d = json.load(open(path, encoding='utf-8')).get('date')
                if isinstance(_d, str) and len(_d) >= 10:
                    stamp = _d[:10].replace('/', '-').replace(':', '-')
            except Exception:
                stamp = None                      # 깨진 파일도 버리지 않는다
        if not stamp:
            # date 를 못 읽으면 파일 수정시각으로. '알 수 없음'이 곧 '버림'이 되지 않게.
            stamp = datetime.fromtimestamp(path.stat().st_mtime).strftime('%Y-%m-%d')
        arch = path.with_name(f"{path.stem}_{stamp}{path.suffix}")
        if arch.exists():
            # 같은 날 여러 번 돌 때 앞선 보관본을 덮지 않는다(감사 중-2).
            arch = path.with_name(
                f"{path.stem}_{stamp}_{datetime.now().strftime('%H%M%S')}{path.suffix}")
        try:
            arch.write_bytes(path.read_bytes())
            print(f"\n  이전 기록 보관: {arch.name}")
        except Exception as e:
            print(f"\n  [경고] 이전 기록 보관 실패 (새 결과는 정상 저장합니다): {e}")

    _archive(json_path)
    _archive(RESULT_DIR / f"accuracy_report{_sfx}.txt")

    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n  결과 저장: {json_path}")

    # 텍스트 리포트
    report_path = RESULT_DIR / f"accuracy_report{_sfx}.txt"
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write("NutriLens 정확도 테스트 리포트 (전체 파이프라인)\n")
        f.write(f"테스트 일시: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        f.write(f"모델: GPT-4o Vision + match_with_db\n")
        f.write(f"평가셋: {photo_set}"
                + ("  (IP/165 G4 게이트)\n" if photo_set == "baseline32"
                   else "  (탐색용 — 기준선과 비교 불가)\n"))
        f.write(f"전처리: {preprocess}"
                + ("  (59.4% 기준선 조건)\n" if preprocess == "raw"
                   else "  (프로덕션 조건 — 별도 기준선, 59.4%와 비교 불가)\n"))
        if measurement_broken:
            f.write("\n" + "!" * 60 + "\n")
            f.write(f"★ 측정 실패 — {total}장 중 {errors}장 에러.\n")
            f.write("  이 파일의 숫자는 모델 성능이 아니라 호출 실패를 반영합니다.\n")
            f.write("  기준선으로 인용하지 마십시오.\n")
            f.write("!" * 60 + "\n")
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

    # (정확도, 게이트 실패 여부) — 호출부가 종료코드를 정할 수 있게 한다.
    # 초판은 accuracy 만 돌려줘서 G4 FAIL 이어도 exit 0 이었고,
    # bat 의 "STOPPED. Do not deploy." 가 파이썬 예외에만 걸렸습니다(감사 중-4).
    #
    # ★ 세션48: 측정이 깨졌으면 정확도를 돌려주지 않는다.
    #   숫자를 돌려주면 호출부가 그걸 출력하고, 화면에 「0.0% (새 기준선)」이 찍힙니다.
    #   None 을 돌려주면 main 의 `acc is None` 경로가 exit 1 로 잡습니다.
    #   결과 파일은 이미 저장됐지만 `usable: false` 가 박혀 있습니다 —
    #   저장을 막지는 않습니다(세션46: 보관 실패가 저장을 막지 않게 한 것과 같은 이유).
    if measurement_broken:
        return None, True
    return strict_accuracy, gate_failed


# ══════════════════════════════════════════════════════════
#  메인
# ══════════════════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(description="NutriLens 정확도 테스트 v3 (P0-2)")
    parser.add_argument("--db", action="store_true", help="DB 매칭 테스트 (무료, 즉시)")
    parser.add_argument("--photo", action="store_true", help="사진 인식 테스트 (~$0.005/장)")
    parser.add_argument("--all", action="store_true", help="둘 다")
    parser.add_argument("--set", dest="photo_set", default="baseline32",
                        choices=["baseline32", "all"],
                        help="사진 평가셋. baseline32=IP/165 G4 게이트(기본), "
                             "all=폴더 전수(탐색용, 기준선과 비교 불가)")
    parser.add_argument("--preprocess", dest="preprocess", default="raw",
                        choices=["raw", "production"],
                        help="GPT-4o 로 보내는 이미지의 전처리. raw=원본·detail:high(기본, "
                             "59.4% 기준선 조건), production=768px·detail:low·center-crop "
                             "(IP/174 §4-3). 엔진 입력은 두 모드 모두 원본. "
                             "production 결과는 별도 파일에 저장되고 G4 판정 대상이 아님")
    args = parser.parse_args()

    if not (args.db or args.photo or args.all):
        print()
        print("NutriLens 정확도 테스트 v3 (P0-2, 2026-05-05)")
        print()
        print("사용법:")
        print("  python tools/accuracy_test.py --db      # DB 매칭 (무료)")
        print("  python tools/accuracy_test.py --photo   # 사진 인식, 기준선 32장 (~$0.16)")
        print("  python tools/accuracy_test.py --photo --set all   # 전수 91장 (~$0.46, 탐색용)")
        print("  python tools/accuracy_test.py --all     # 둘 다")
        print()
        print("  # 프로덕션 조건 재측정 (IP/174 §4-3, 세션48 신설)")
        print("  python tools/accuracy_test.py --photo --preprocess production")
        print("    → GPT-4o 에도 768px·detail:low·center-crop 을 준다(프로덕션과 동일).")
        print("    → 결과는 photo_test_results_production.json 에 별도 저장.")
        print("    → G4 게이트 판정 대상이 아니다. 59.4% 와 비교하지 말 것.")
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
        acc, gate_failed = run_photo_test(photo_set=args.photo_set,
                                          preprocess=args.preprocess)
        # 세션48: 전처리를 바꾼 실행은 G4 판정 대상이 아니다. 기준선이 다르다(규칙34).
        _gate_applicable = (args.photo_set == "baseline32" and args.preprocess == "raw")
        if acc is not None:
            # ★ 절대 기준(80/60)이 아니라 게이트 판정으로 표시한다.
            #   초판은 G4 PASS 인 19/32(59.4%)에도 마지막 줄에 '✗' 를 찍어
            #   G4 블록의 PASS 와 정면으로 모순됐습니다(감사 경-5).
            if _gate_applicable:
                mark = "✗ G4 FAIL" if gate_failed else "✓ G4 PASS"
                print(f"\n  {mark} — 음식명 정확도(엄격): {acc:.1f}%")
            elif args.preprocess == "production":
                print(f"\n  · 음식명 정확도(엄격): {acc:.1f}%  "
                      f"(프로덕션 전처리 — 게이트 판정 아님 · 새 기준선)")
                print(f"    ★ 볼 것은 EXACT% 가 아니라 위의 food30 엔진 교체 건수입니다.")
                print(f"      IP/174 §4-3: 「프로덕션 조건에서 GPT-4o 가 놓치는 만큼")
                print(f"      엔진이 메우는가」가 이 트랙의 질문입니다.")
            else:
                print(f"\n  · 음식명 정확도(엄격): {acc:.1f}%  "
                      f"(탐색용 셋 — 게이트 판정 아님)")
        # ══════════════════════════════════════════════════════════════════
        # 종료코드 — .bat 의 errorlevel 검사가 이걸 봅니다.
        # ══════════════════════════════════════════════════════════════════
        # ★ 2026-08-24 독립감사 치명-1 수정.
        #   초판은 `gate_failed and _gate_applicable` 이었습니다. 그런데
        #   run_photo_test 는 **측정을 시작조차 못한 경우에도** (None, True) 를
        #   돌려줍니다 — API 키 없음 / 기준선 32장 결손 / 이미지 0장.
        #   production 실행은 _gate_applicable=False 이므로 그 세 경우가 전부
        #   **exit 0** 이 됐고, .bat 은 "DONE. Saved: ..._production.json" 을
        #   출력했습니다. 그 파일은 만들어진 적이 없고, 이전 실행의 낡은 파일이
        #   그 자리에 남아 있으면 다음 세션이 그걸 이번 측정으로 인용합니다.
        #   → IP/174 §2 사고와 정확히 같은 형태입니다.
        #
        #   두 실패를 분리합니다:
        #     acc is None      = 측정 자체가 안 됨 → 전처리·평가셋과 무관하게 실패
        #     gate_failed      = 측정은 됐고 G4 기준 미달 → 게이트 적용 시에만 실패
        if acc is None:
            print("\n  ✗ 쓸 수 있는 측정이 나오지 않았습니다 — 위 메시지를 보십시오.")
            print("    두 경우가 있습니다:")
            print("     · 시작조차 못함(키 없음 / 32장 결손 / 이미지 0장)")
            print("       → 결과 파일이 생성되지 않았습니다. 같은 이름의 파일이 있다면")
            print("         그건 이전 실행의 것입니다 — 이번 측정으로 인용하지 마십시오.")
            print("     · 절반 이상 에러")
            print("       → 파일은 저장됐지만 `\"usable\": false` 가 박혀 있습니다.")
            print("         숫자를 인용하기 전에 그 필드를 확인하십시오.")
            sys.exit(1)
        if gate_failed and _gate_applicable:
            sys.exit(1)


if __name__ == "__main__":
    main()
