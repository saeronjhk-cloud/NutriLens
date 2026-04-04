#!/usr/bin/env python3
"""
식약처 식품영양성분 DB 가져오기
────────────────────────────────
식약처 공식 API에서 영양성분 데이터를 가져와 골드 테이블에 추가합니다.
100g당 기준으로 통일된, 정부 인증 데이터입니다.

실행법:
  python tools/fetch_mfds_data.py

필요한 것:
  .env 파일에 MFDS_API_KEY 설정 (식품안전나라 API 키)

주의: 이 스크립트는 유료 API가 아닙니다 (무료 공공데이터).
"""

import os
import sys
import json
import time
import urllib.request
import urllib.error
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent
GOLD_JSON_PATH = PROJECT_DIR / 'gold_foods.json'

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


def fetch_mfds_page(api_key, start, end):
    """식품안전나라 API에서 한 페이지 가져오기"""
    # I2790: 식품영양성분DB (~2023) — 100g당 기준
    url = f"http://openapi.foodsafetykorea.go.kr/api/{api_key}/I2790/json/{start}/{end}"

    try:
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'NutriLens/1.0')
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode('utf-8'))

        result = data.get('I2790', {})
        total = int(result.get('total_count', '0'))
        rows = result.get('row', [])
        return total, rows, None

    except urllib.error.HTTPError as e:
        return 0, [], f"HTTP {e.code}: {e.read().decode('utf-8', errors='replace')[:200]}"
    except Exception as e:
        return 0, [], str(e)


def parse_mfds_item(row):
    """식약처 API 응답 1건 → 골드 테이블 형식 변환"""
    name = (row.get('DESC_KOR') or '').strip()
    if not name:
        return None, None

    def safe_float(val, default=0):
        try:
            v = str(val).strip()
            if not v or v == '-':
                return default
            return float(v)
        except (ValueError, TypeError):
            return default

    # 식약처 데이터는 100g당 기준
    # NUTR_CONT1: 에너지(kcal), NUTR_CONT2: 탄수화물(g), NUTR_CONT3: 단백질(g)
    # NUTR_CONT4: 지방(g), NUTR_CONT5: 당류(g), NUTR_CONT6: 나트륨(mg)
    # NUTR_CONT7: 콜레스테롤(mg), NUTR_CONT8: 포화지방산(g), NUTR_CONT9: 트랜스지방산(g)

    cal = safe_float(row.get('NUTR_CONT1'))
    carbs = safe_float(row.get('NUTR_CONT2'))
    prot = safe_float(row.get('NUTR_CONT3'))
    fat = safe_float(row.get('NUTR_CONT4'))
    sugar = safe_float(row.get('NUTR_CONT5'))
    sodium = safe_float(row.get('NUTR_CONT6'))

    # 칼로리 없으면 스킵
    if cal <= 0:
        return None, None

    # 서빙 사이즈 추정 (식약처 데이터에 SERVING_SIZE 필드가 있는 경우)
    serving_wt = safe_float(row.get('SERVING_WT'))
    if serving_wt <= 0:
        serving_wt = safe_float(row.get('SERVING_SIZE'))
    if serving_wt <= 0:
        serving_wt = 100  # 기본값

    # 카테고리 추정
    group = (row.get('GROUP_NAME') or '').strip()
    category = 'korean'
    if any(w in group for w in ['음료', '커피', '차', '주류']):
        category = 'snack_drink'
    elif any(w in group for w in ['과자', '빵', '케이크', '사탕', '초콜릿', '아이스크림']):
        category = 'snack_drink'
    elif any(w in group for w in ['과일']):
        category = 'fruit'
    elif any(w in group for w in ['햄버거', '피자', '파스타', '스테이크']):
        category = 'foreign_popular'

    entry = {
        "cal": cal,
        "prot": prot,
        "carbs": carbs,
        "fat": fat,
        "fiber": 0,  # 식약처 I2790에는 식이섬유 없음
        "sodium": sodium,
        "sugar": sugar,
        "serving": int(serving_wt),
        "category": category,
        "source": "mfds",
    }

    return name, entry


def main():
    api_key = os.environ.get('MFDS_API_KEY')
    if not api_key:
        print("에러: .env 파일에 MFDS_API_KEY가 없습니다.")
        print("식품안전나라(https://www.foodsafetykorea.go.kr/apiMain.do)에서 발급받으세요.")
        sys.exit(1)

    print()
    print("=" * 60)
    print("  식약처 식품영양성분 DB 가져오기")
    print("=" * 60)

    # 기존 골드 테이블 로드
    if GOLD_JSON_PATH.exists():
        with open(GOLD_JSON_PATH, 'r', encoding='utf-8') as f:
            gold = json.load(f)
        print(f"  기존 골드 테이블: {len(gold)}건")
    else:
        gold = {}
        print("  기존 골드 테이블: 없음 (새로 생성)")

    # 기존 core/db_gold 항목 수
    core_count = sum(1 for v in gold.values() if v.get('source') == 'core')
    db_count = sum(1 for v in gold.values() if v.get('source') == 'db_gold')
    mfds_count = sum(1 for v in gold.values() if v.get('source') == 'mfds')
    print(f"    CORE: {core_count}, DB골드: {db_count}, 식약처: {mfds_count}")

    # API 첫 호출 — 총 건수 확인
    print("\n  API 연결 중...", end=" ", flush=True)
    total, first_rows, err = fetch_mfds_page(api_key, 1, 1)
    if err:
        print(f"실패: {err}")
        sys.exit(1)
    print(f"OK (총 {total:,}건)")

    # 전체 가져오기 (1000건씩)
    page_size = 1000
    added = 0
    skipped = 0

    for start in range(1, total + 1, page_size):
        end = min(start + page_size - 1, total)
        print(f"  [{start:,}~{end:,}] 가져오는 중...", end=" ", flush=True)

        _, rows, err = fetch_mfds_page(api_key, start, end)
        if err:
            print(f"에러: {err}")
            time.sleep(2)
            continue

        page_added = 0
        for row in rows:
            name, entry = parse_mfds_item(row)
            if name is None:
                continue

            # CORE 항목은 절대 덮어쓰지 않음 (수동 검증 데이터 보호)
            if name in gold and gold[name].get('source') == 'core':
                skipped += 1
                continue

            # 이미 있는 db_gold보다 식약처 데이터 우선 (정부 인증)
            if name not in gold or gold[name].get('source') != 'core':
                gold[name] = entry
                page_added += 1
                added += 1

        print(f"+{page_added}건")
        time.sleep(0.3)  # API 부하 방지

    # 저장
    with open(GOLD_JSON_PATH, 'w', encoding='utf-8') as f:
        json.dump(gold, f, ensure_ascii=False)

    print()
    print("=" * 60)
    print(f"  완료!")
    print(f"  식약처에서 추가: {added:,}건")
    print(f"  CORE 보호 (스킵): {skipped}건")
    print(f"  골드 테이블 최종: {len(gold):,}건")

    # 소스별 통계
    sources = {}
    for v in gold.values():
        s = v.get('source', 'unknown')
        sources[s] = sources.get(s, 0) + 1
    print(f"\n  소스별:")
    for s, cnt in sorted(sources.items(), key=lambda x: -x[1]):
        print(f"    {s:12s}: {cnt:>6,}건")
    print("=" * 60)


if __name__ == "__main__":
    main()
