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

# 세션49(2026-08-28): AI Hub Validation holdout 평가셋.
#   `build_aihub_val_evalset.py` 가 <음식명>/ 폴더 구조로 만든 1,800장(30종×60).
#   ⛔ 이 폴더를 `.tmp/test_images` 로 병합하지 마십시오 — 그 폴더는 G4 게이트 32장이
#      사는 곳이고, 분모가 바뀌면 59.4% 와 비교할 수 없게 됩니다(IP/175 §1-4, 제이 판단④).
#      여기서는 **원래 위치에서 직접 읽습니다.**
#   ⚠ in-domain 셋입니다. 학습과 같은 촬영 조건이므로 실사용 성능의 **상한**이지
#      실사용 성능이 아닙니다(IP/175 §7-D-10 · 규칙47).
ROOT_DIR = PROJECT_DIR.parent.parent                # D:\서박사의 영양공식
AIHUB_VAL_DIR = ROOT_DIR / "Images" / "aihub_val"
AIHUB_PER_CLASS = 10                                # 30종 × 10 = 300장 (~$1.5)

sys.path.insert(0, str(TOOLS_DIR))


# ── .env 로드 ──
def load_env():
    env_paths = [PROJECT_DIR / '.env', Path.cwd() / '.env']
    for p in env_paths:
        if p.exists():
            with open(p, encoding='utf-8') as f:
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


def run_photo_test(photo_set="baseline32", preprocess="raw", run_tag="", dry_run=False):
    """전체 파이프라인으로 사진 정확도 측정.

    photo_set:
      'baseline32' — IP/165 G4 게이트용. 기준선 v1 과 직접 비교 가능한 32장. (기본값)
      'all'        — 폴더 전수(91장). 탐색용. G4 판정에 쓰지 말 것.
      'aihub300'   — AI Hub Validation holdout 에서 30종 × 10장 = 300장. (세션49)
                     GT 는 **폴더명**이다 — 파일명(`04_043_04013002_...`)에서는
                     유도할 수 없다. 정렬 후 앞 N장을 뽑으므로 매번 같은 300장이다.
                     ★ 이 셋의 목적: baseline32 에는 food30 30클래스 사진이
                       105_갈비탕 **1장뿐**이고 그마저 엔진이 검출에 실패해서,
                       「GPT 가 놓치는 만큼 엔진이 메우는가」(IP/174 §4-3)를
                       구조적으로 물을 수 없었다(IP/175 세션49 실측).

    run_tag:
      결과 파일명에 붙는 접미사. 반복 측정(run-to-run 분산)에서 **기준선 파일을
      덮어쓰지 않기 위한 것**이다. 비우면 기존 동작 그대로.
      예: run_tag='run2' → photo_test_results_run2.json
      ⚠ 이름이 `tag` 가 아닌 이유: 이 함수의 사진 루프가 `tag = "✓ EXACT"` 로
        같은 이름을 재할당한다. 파라미터를 `tag` 로 두면 마지막 사진의 판정
        문자열이 파일명이 되어 `photo_test_results_✗ MISS.json` 이 생긴다.
        2026-08-28 감사에서 실측으로 잡혔다(세션48 `out` 섀도잉과 같은 사고).

    preprocess:
      'raw'        — (기본) 원본·detail:high·무크롭. 59.4% 기준선이 이 조건이다.
      'production' — GPT-4o 에게 프로덕션과 같은 768px·detail:low·center-crop 을 준다
                     (IP/174 §4-3). 엔진 입력은 두 모드 모두 원본이다.
                     ⚠ 결과를 59.4% 와 같은 표에 올리지 말 것. 별도 기준선이다(규칙34).
    """
    # run_tag 는 파일명이 된다. 경로 구분자나 공백이 들어오면 저장이 깨지거나
    # 폴더를 탈출한다 — 조용히 이상한 곳에 쓰기 전에 여기서 막는다.
    if run_tag:
        import re as _re
        if not _re.fullmatch(r"[A-Za-z0-9_-]{1,32}", run_tag):
            print()
            print("  ★ --tag 는 영문/숫자/밑줄/하이픈 1~32자만 됩니다 — 중단합니다.")
            print(f"    받은 값: {run_tag!r}")
            return None, True
        # 파일명은 `photo_test_results[_평가셋][_전처리][_태그].json` 이다.
        # 태그가 평가셋/전처리 이름과 같으면 서로 다른 조건이 **같은 파일**을 쓴다.
        #   예: --set baseline32 --tag production  →  photo_test_results_production.json
        #       = --preprocess production 의 파일. 조건이 통째로 뒤바뀐다.
        _RESERVED = {"raw", "production", "baseline32", "all", "aihub300"}
        if run_tag in _RESERVED:
            print()
            print(f"  ★ --tag 로 {run_tag!r} 는 쓸 수 없습니다 — 중단합니다.")
            print("    평가셋·전처리 이름과 같으면 다른 조건의 결과 파일과")
            print("    이름이 겹칩니다(2026-08-28 감사 중-5).")
            print(f"    예약어: {', '.join(sorted(_RESERVED))}")
            print("    run2 · run3 · v2 처럼 회차를 뜻하는 이름을 쓰십시오.")
            return None, True

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key and not dry_run:
        print("  OPENAI_API_KEY가 설정되지 않았습니다.")
        return None, True

    if not TEST_DIR.exists():
        TEST_DIR.mkdir(parents=True, exist_ok=True)

    # gt_map: 파일 경로 → 정답 음식명.
    #   비어 있으면 기존대로 **파일명 stem** 에서 유도한다(01_김치 → 김치).
    #   aihub300 은 파일명이 `04_043_04013002_160293077496393_1.jpg` 라서
    #   stem 파싱이 통째로 틀린다 — 그래서 폴더명을 여기에 실어 보낸다.
    gt_map = {}

    _EXTS = ('.jpg', '.jpeg', '.png')

    if photo_set == "aihub300":
        if not AIHUB_VAL_DIR.exists():
            print()
            print("=" * 60)
            print("  aihub300 — 평가셋 폴더가 없습니다. 중단합니다.")
            print("=" * 60)
            print(f"\n  없음: {AIHUB_VAL_DIR}")
            print("  먼저 run-aihub-val-evalset.bat 으로 holdout 을 추출하십시오.")
            return None, True

        images = []
        classes = sorted(d for d in AIHUB_VAL_DIR.iterdir() if d.is_dir())
        short = []
        for d in classes:
            fs = sorted(f for f in d.iterdir() if f.suffix.lower() in _EXTS)
            picked = fs[:AIHUB_PER_CLASS]
            if len(picked) < AIHUB_PER_CLASS:
                short.append(f"{d.name} {len(picked)}/{AIHUB_PER_CLASS}")
            for f in picked:
                gt_map[f] = d.name
            images.extend(picked)

        # ★ 무엇을 몇 장 셌는지 추론 전에 출력한다(규칙38·42).
        print()
        print("=" * 60)
        print("  aihub300 인벤토리 — AI Hub Validation holdout")
        print("=" * 60)
        print(f"  클래스 {len(classes)}종 · 클래스당 최대 {AIHUB_PER_CLASS}장 "
              f"· 합계 {len(images)}장")
        print(f"  출처: {AIHUB_VAL_DIR}")
        print("  GT = 폴더명 (파일명에서는 유도 불가)")
        print("  샘플링 = 파일명 정렬 후 앞 N장 → 매 실행 같은 사진 (재현 가능)")
        # ★ 경고를 «출력만» 하면 안 된다(규칙44). 셋이 깨진 채로 $1.5 가 나간다.
        #   2026-08-28 감사 실측: 3종 14장짜리 셋에서도 exit 0 이었고, .bat 은
        #   그 뒤에 "30 classes, 300 total" 이라는 하드코딩 문구를 출력했다.
        _broken = []
        if short:
            print(f"  [경고] 목표 미달 클래스 {len(short)}종: {', '.join(short)}")
            _broken.append(f"클래스당 {AIHUB_PER_CLASS}장을 못 채운 클래스 {len(short)}종")
        if len(classes) != 30:
            print(f"  [경고] 클래스가 30종이 아니라 {len(classes)}종입니다 — "
                  "셋 구성이 바뀌었는지 확인하십시오.")
            _broken.append(f"클래스가 30종이 아니라 {len(classes)}종")
        print("=" * 60)
        if _broken:
            print()
            print("  ★ 평가셋이 예상과 다릅니다 — 중단합니다.")
            for b in _broken:
                print(f"    · {b}")
            print()
            print("  이대로 돌리면 분모가 달라진 정확도가 나오고, 나중에 30종×10장")
            print("  결과와 나란히 놓이게 됩니다(규칙34). 먼저 확인하십시오:")
            print("   1) run-aihub-val-evalset.bat 이 완주했는가")
            print(f"   2) {AIHUB_VAL_DIR} 아래 폴더가 30개인가")
            print(f"   3) 각 폴더에 사진이 {AIHUB_PER_CLASS}장 이상인가")
            return None, True

    else:
        # baseline32 / all — 기존 경로. `.tmp/test_images` 평면 폴더를 스캔한다.
        images = sorted(
            [f for f in TEST_DIR.iterdir() if f.suffix.lower() in _EXTS]
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
        if photo_set == "aihub300":
            print(f"\n  {AIHUB_VAL_DIR} 아래에 <음식명>/ 폴더와 사진이 없습니다.")
            print("  run-aihub-val-evalset.bat 으로 holdout 을 먼저 추출하십시오.")
        else:
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
    if photo_set == "aihub300":
        print("          AI Hub Validation holdout · 학습 누수 0 검증됨(IP/175 §7-D-7)")
        print("          ⚠ in-domain 입니다 — 실사용 성능의 **상한**이지 실사용 성능이")
        print("             아닙니다(규칙47). 「앱에서 이만큼 나온다」로 인용 금지.")
    if run_tag:
        print(f"  태그: {run_tag}  (결과가 별도 파일로 저장됩니다 — 기준선 파일 무손상)")
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

    # ★ 세션49: 돈을 쓰기 전에 «무엇을 잴 것인지»를 확인할 수 있어야 한다.
    #   aihub300 은 300장 ~$1.5 다. 클래스가 빠졌거나 GT 매핑이 틀린 채로
    #   전부 돌고 나서 알아차리면 그 돈은 돌아오지 않는다.
    if dry_run:
        print("=" * 60)
        print("  --dry-run — API 를 호출하지 않고 여기서 멈춥니다 (비용 $0)")
        print("=" * 60)
        _by_gt = {}
        for _p in images:
            _g = gt_map.get(_p)
            if _g is None:
                _g = _p.stem
                if "_" in _g and _g.split("_")[0].isdigit():
                    _g = _g.split("_", 1)[1]
            _by_gt[_g] = _by_gt.get(_g, 0) + 1
        print(f"  정답(GT) 종류 {len(_by_gt)}종 · 사진 {len(images)}장")
        print(f"  GT 결정 방식: {'폴더명 (gt_map)' if gt_map else '파일명 stem 파싱'}")
        print()
        for _g in sorted(_by_gt):
            print(f"    {_g:<14s} {_by_gt[_g]}장")
        print()
        print("  ★ 위 정답 이름이 실제 음식과 맞는지 눈으로 확인하십시오.")
        print("    맞으면 --dry-run 을 빼고 다시 실행하면 됩니다.")
        return None, False

    foods_db = load_food_db()

    results = []
    correct_exact = 0
    correct_loose = 0
    correct_group = 0      # 세션52: 구별 불가 쌍 안에서의 오답. EXACT 와 겹치지 않는다.
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
        # 정답(GT) 결정. gt_map 에 있으면 그게 우선 — aihub300 은 **폴더명**이 GT다.
        #   파일명이 `04_043_04013002_160293077496393_1` 이라 아래 stem 파싱을 태우면
        #   `043_04013002_160293077496393_1` 이라는 엉뚱한 정답이 나온다(세션49 실측).
        expected_name = gt_map.get(img_path)
        if expected_name is None:
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

        # ── 세션52: GROUP — «구별 불가 쌍» 안에서 틀린 것 ────────────────────
        # ⚠ 위 카운터를 «전혀» 건드리지 않는다. 별도 칼럼으로만 센다.
        #   EXACT 에 얹으면 세션32~51 의 모든 과거 수치와 비교가 끊기고,
        #   「병합했더니 정확도가 올랐다」는 거짓 초록이 된다(제이 결정 2026-09-03).
        group_hit = False
        if strictness != "EXACT":
            try:
                from food_analyzer import food30_same_group
                group_hit = any(
                    food30_same_group(expected_name,
                                      f.get("name_ko") or f.get("name") or "")
                    for f in ai_foods if isinstance(f, dict))
            except Exception:
                group_hit = False
        if group_hit:
            correct_group += 1
            tag += " (계열)"

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
    # 세션52: 별도 칼럼. 위 EXACT/CONTAINS 수치에 포함되지 «않는다».
    print(f"  ◇ GROUP(계열):    {correct_group}장  — 설렁탕↔곰탕 · 꽃게탕↔해물탕 (구별 불가 쌍). EXACT 미포함")
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
    engine_never_ran = (f30["photos_with_engine_field"] == 0)
    if engine_never_ran:
        print("  ★ 엔진이 한 번도 돌지 않았습니다 (food30_engine 필드 0건).")
        print("    → 모델 미로드 / FOOD30_ENGINE=0 / 배선 누락 중 하나입니다.")
        print("    → 이 상태의 EXACT% 는 '엔진 적용 전 기준선'이지 G4 결과가 아닙니다.")
        if photo_set == "aihub300":
            # ★ aihub300 은 «엔진이 GPT 를 메우는가»를 재려고 300장 $1.5 를 쓰는 셋이다.
            #   엔진이 안 돌았으면 그 질문에 대한 답이 아니라 «GPT 단독 성능»이다.
            #   화면에만 적어 두면 다음 세션이 changed:0 을 「엔진은 아무것도
            #   바꾸지 않는다」로 읽는다 — 정반대 결론이다(2026-08-28 감사 치명-3).
            print()
            print("  ⛔ 이 실행(aihub300)의 목적은 엔진 기여 측정입니다.")
            print("     엔진이 돌지 않았으므로 이 결과로 그 질문에 답할 수 없습니다.")
            print("     결과 파일은 저장하되 종료코드 1 로 알립니다.")
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
        # 세션49: 반복 측정(run-to-run 분산)에서 어느 회차인지. 비우면 "".
        "tag": run_tag,
        # 세션49: aihub300 은 클래스당 장수가 결과의 성격을 정한다. 나중에
        # 다른 N 으로 잰 값과 나란히 놓지 않도록 파일에 박아 둔다(규칙34).
        "aihub_per_class": AIHUB_PER_CLASS if photo_set == "aihub300" else None,
        "gate_comparable": (photo_set == "baseline32" and preprocess == "raw"
                            and not measurement_broken),
        # ★ 세션48: 이 파일을 나중에 읽는 사람이 「쓸 수 있는 측정인가」를
        #   숫자를 해석하기 전에 알 수 있어야 합니다. errors 를 세어 두기만 하면
        #   다음 세션이 0.0% 를 성능으로 읽습니다(실제로 그럴 뻔했습니다).
        "usable": not measurement_broken,
        # 세션49: 엔진이 실제로 돌았는가. changed:0 에는 두 가지 뜻이 있다 —
        #   「돌았는데 바꿀 게 없었다」와 「아예 안 돌았다」. 파일만 보고 구별되어야 한다.
        "engine_ran": not engine_never_ran,
        "measurement_broken_reason": (f"{errors}/{total} photos errored"
                                      if measurement_broken else None),
        "total": total,
        "correct_exact": correct_exact,
        "correct_loose": correct_loose,
        "correct_group": correct_group,   # 세션52 · EXACT 와 겹치지 않음
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
    #   세션49: tag 도 넣는다. 같은 조건을 여러 번 재는 분산 측정에서, 태그가 없으면
    #   2회차가 1회차를 덮고 **기준선 파일(photo_test_results.json)까지 밀어낸다.**
    #   보관 로직이 데이터는 지켜 주지만, 「59.4% = 이 파일」이라는 정체성은 못 지킨다.
    _sfx = "" if photo_set == "baseline32" else f"_{photo_set}"
    if preprocess != "raw":
        _sfx += f"_{preprocess}"
    if run_tag:
        _sfx += f"_{run_tag}"
    json_path = RESULT_DIR / f"photo_test_results{_sfx}.json"

    # 태그 없이 기준선 파일을 덮어쓰는 경우, 조용히 넘어가지 않는다.
    if not _sfx:
        print()
        print("  ⚠ 이 실행은 기준선 파일 photo_test_results.json 을 덮어씁니다.")
        print("    (이전 기록은 아래에 자동 보관됩니다)")
        print("    분산 측정처럼 기준선을 남겨야 하면 --tag 를 쓰십시오.")

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
    # 세션49: aihub300 에서 엔진이 안 돌았으면 «측정은 됐지만 재려던 것을 못 쟀다».
    #   acc 는 돌려준다(GPT 단독 성능으로는 유효하다). 다만 실패로 알린다.
    if engine_never_ran and photo_set == "aihub300":
        return strict_accuracy, True
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
                        choices=["baseline32", "all", "aihub300"],
                        # ⚠ help 안의 %는 %%로 (아래 --preprocess 주석 참조)
                        help="사진 평가셋. baseline32=IP/165 G4 게이트(기본), "
                             "all=폴더 전수(탐색용, 기준선과 비교 불가), "
                             "aihub300=AI Hub holdout 30종x10장(~$1.5, in-domain 상한)")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true",
                        help="API 를 호출하지 않고 «무엇을 잴 것인지»만 출력 "
                             "(비용 $0). 사진 수와 정답 매핑을 돈 쓰기 전에 확인")
    parser.add_argument("--tag", dest="tag", default="",
                        help="결과 파일명 접미사. 반복 측정에서 기준선 파일을 "
                             "덮지 않기 위한 것. 예: --tag run2 → "
                             "photo_test_results_run2.json")
    parser.add_argument("--preprocess", dest="preprocess", default="raw",
                        choices=["raw", "production"],
                        # ★ 2026-08-26: help 안의 %는 반드시 %%로 이스케이프한다.
                        #   argparse 는 help 를 `help_string % params` 로 포맷한다.
                        #   「59.4% 기준선」의 `% 기` 를 포맷 지정자로 읽어
                        #   ValueError: unsupported format character '기' 로 죽었다.
                        #   Python 3.14 부터 add_argument 시점에 검사하므로
                        #   --help 를 안 쳐도 **파서 생성만으로 즉시** 터진다.
                        #   제이 PC 에서 실측(2026-08-26): STEP 2 진입 직후 크래시.
                        help="GPT-4o 로 보내는 이미지의 전처리. raw=원본·detail:high(기본, "
                             "59.4%% 기준선 조건), production=768px·detail:low·center-crop "
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
        print("  # 실행 간 분산 측정 (세션49 신설) — 기준선 파일을 덮지 않는다")
        print("  python tools/accuracy_test.py --photo --tag run2")
        print("    → photo_test_results_run2.json 으로 저장.")
        print("    → 여러 회차를 모아 비교: python tools/compare_runs.py .tmp/photo_test_results*.json")
        print()
        print("  # AI Hub holdout 300장 (세션49 신설, ~$1.5)")
        print("  python tools/accuracy_test.py --photo --set aihub300")
        print("    → 30종 x 10장. GT 는 폴더명. 엔진이 실제로 일하는 셋이다.")
        print("    → ⚠ in-domain — 실사용 성능의 상한이지 실사용 성능이 아니다(규칙47).")
        print()
        print(f"사진 위치: {TEST_DIR}")
        print(f"           {AIHUB_VAL_DIR}  (--set aihub300)")
        print("파일명 = 정답 (예: 01_김치.jpg → 정답 '김치')")
        sys.exit(0)

    if args.db or args.all:
        cov = run_db_test()
        if cov is not None:
            mark = "✓" if cov >= 90 else "△" if cov >= 70 else "✗"
            print(f"\n  {mark} DB 매칭 커버리지: {cov:.1f}%")

    if args.photo or args.all:
        acc, gate_failed = run_photo_test(photo_set=args.photo_set,
                                          preprocess=args.preprocess,
                                          run_tag=args.tag,
                                          dry_run=args.dry_run)
        # 세션49: --dry-run 은 «측정 실패»가 아니라 «측정하지 않기로 한 것»이다.
        #   acc is None 이라는 점은 같지만 종료코드가 달라야 한다 — 아래 exit 1 은
        #   .bat 이 「측정이 깨졌다」로 읽는 신호이고, dry-run 에 그걸 붙이면
        #   정상 확인 절차가 실패로 보인다.
        if args.dry_run:
            # ⚠ 초판은 무조건 exit 0 이었습니다. 그러면 --tag 오타 · 평가셋 폴더 없음 ·
            #   32장 결손 같은 **중단 사유가 전부 exit 0** 이 되어, 확인하려고 만든
            #   절차가 실패를 숨깁니다(규칙44 — 세는 것과 판정에 쓰는 것은 다르다).
            #   실측으로 잡았습니다: `--tag '../evil hack'` 이 거부됐는데 EXIT=0.
            if gate_failed:
                print("\n  ✗ --dry-run 확인이 실패했습니다 — 위 메시지를 보십시오.")
                print("    이 상태로 본 실행을 걸면 같은 지점에서 멈춥니다.")
                sys.exit(1)
            print("\n  (--dry-run 이므로 여기서 정상 종료합니다. 비용 $0)")
            sys.exit(0)
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
        # 세션49: G4 게이트가 아닌 실행에도 «실패»가 있다. aihub300 에서 엔진이
        #   안 돌면 재려던 것을 못 잰 것이므로 실패로 알린다(감사 치명-3).
        #   _gate_applicable 만 보면 그 신호가 통째로 사라진다 — 세션48 치명-1 과
        #   같은 구조의 구멍이다.
        if gate_failed and (_gate_applicable or args.photo_set == "aihub300"):
            sys.exit(1)


if __name__ == "__main__":
    main()
