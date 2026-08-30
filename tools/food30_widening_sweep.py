# -*- coding: utf-8 -*-
"""세션50 · $0 시뮬레이션 — apply_food30_override 의 «카테고리 소속 검사» 를 넓히면
   aihub300 에서 몇 장을 얻고 몇 장을 잃는가.

전제(세션50에서 실측 확정):
  · accuracy_test.py 의 ai_name=='인식 없음' 은 GPT 침묵이 아니다.
    best,_ = find_best_match(GT, ai_foods) 가 None 일 때 붙는 라벨이며
    match=='NONE' 과 143:143 완전 일치하는 «동어반복»이다.
    GPT-4o 는 300장 전부에 음식명을 냈다(빈 ai_foods_detected 0장).
  · 진짜 손실 지점: 엔진이 GT 를 정확히 검출(29장, 다수 conf>=0.9)했는데
    _f30_is_soup/_f30_is_rice 가 GPT 이름을 「해당 계열 아님」으로 판정해
    교체 후보(matched)가 비고 → disagreement 로 기록만 하고 버린다.

이 스크립트는 API 를 호출하지 않는다. photo_test_results_aihub300.json 만 재해석한다.
apply_food30_override 의 분기(1차 exact / 2차 첫 후보 교체 / preempted / no_db_key)를
그대로 재현하되, 카테고리 검사만 L0~L3 로 갈아끼운다.
"""
import json
import re
import sys
from pathlib import Path

# 기본값은 «이 파일 위치» 기준으로 잡는다 — 하드코딩된 D:\ 경로는 다른 머신에서 죽는다.
_DEFAULT = (Path(__file__).resolve().parent.parent
            / '.tmp' / 'photo_test_results_aihub300.json')
RESULT = Path(sys.argv[1]) if len(sys.argv) > 1 else _DEFAULT

FOOD30 = [
    '갈비탕', '감자탕', '곰탕', '매운탕', '꼬리곰탕', '꽃게탕', '낙지탕', '내장탕',
    '닭곰탕', '닭볶음탕', '지리탕', '도가니탕', '삼계탕', '설렁탕', '알탕', '연포탕',
    '오리탕', '추어탕', '해물탕', '닭개장', '육개장', '뼈해장국',
    '쌀밥', '현미밥', '보리밥', '콩밥', '흑미밥', '감자밥', '돌솥밥', '기타잡곡밥',
]
DB_KEY = {n: n for n in FOOD30}
DB_KEY['기타잡곡밥'] = '잡곡밥'

RICE_ITEMS = {
    '쌀밥', '현미밥', '보리밥', '콩밥', '흑미밥', '감자밥', '돌솥밥', '잡곡밥',
    '흰밥', '백미밥', '쌀 밥', '검은쌀밥', '흑미쌀밥', '현미잡곡밥', '보리쌀밥',
    '돌솥쌀밥', '서리태콩밥', '완두콩밥', '강낭콩밥', '검은콩밥',
}
SOUP_ITEMS = {
    '갈비탕', '감자탕', '곰탕', '매운탕', '꼬리곰탕', '꽃게탕', '낙지탕', '내장탕',
    '닭곰탕', '닭볶음탕', '지리탕', '도가니탕', '삼계탕', '설렁탕', '알탕', '연포탕',
    '오리탕', '추어탕', '해물탕', '닭개장', '육개장', '뼈해장국',
    '닭도리탕', '소꼬리곰탕', '꼬리탕', '왕갈비탕', '설농탕', '육계장',
    '뼈다귀해장국', '뼈해장국탕', '소내장탕', '미꾸라지탕',
}

TRAILING = re.compile(
    r'\s*(?:\d+(?:\.\d+)?\s*(?:g|kg|ml|l|인분|공기|그릇|개|접시|볼)'
    r'|한|두|세|네)?\s*(?:공기|그릇|인분|접시|볼|대|중|소)?\s*$')


def norm(name):
    if not isinstance(name, str):
        return ''
    n = re.sub(r'\([^)]*\)', ' ', name)
    n = re.sub(r'\s+', ' ', n).strip()
    prev = None
    while prev != n:
        prev = n
        n = TRAILING.sub('', n).strip()
    return n.replace(' ', '')


# ── 카테고리 검사 레벨 ────────────────────────────────────────────────
# L0 = 현재 프로덕션. L1~L3 로 넓힌다.
SOUP_SUFFIX_NARROW = ('탕', '해장국', '곰탕')          # 대구탕·문어탕·순대국 계열 중 '탕'
SOUP_SUFFIX_WIDE = ('탕', '국', '찌개', '전골', '해장국')
RICE_SUFFIX = ('밥',)

# 국물이 아닌데 '국/탕'으로 끝나는 함정(측정용 · 확장 시 제외)
SOUP_TRAP = {'콩국수', '떡국', '만두국', '국수', '칼국수', '수제비'}


def make_tests(level):
    def is_soup(nm):
        n = norm(nm)
        if n in SOUP_ITEMS:
            return True
        if level == 0:
            return False
        if n in SOUP_TRAP:
            return False
        if level == 1:
            return n.endswith(SOUP_SUFFIX_NARROW)
        return n.endswith(SOUP_SUFFIX_WIDE)

    def is_rice(nm):
        n = norm(nm)
        if n in RICE_ITEMS:
            return True
        if level < 3:
            return False
        return n.endswith(RICE_SUFFIX)

    return {'rice': is_rice, 'soup': is_soup}


def override(foods, hits, tests, tau=0.70):
    """apply_food30_override 재현. foods = [이름(str)] 리스트를 제자리 수정하고 로그 반환."""
    log = {'applied': [], 'disagreement': [], 'no_db_key': [], 'preempted': []}
    touched = set()                       # name_source == food30* 대용
    for slot in ('rice', 'soup'):
        hit = hits.get(slot)
        if not isinstance(hit, dict) or 'class' not in hit:
            continue
        if (hit.get('confidence') or 0) < tau:
            continue
        db_name = DB_KEY.get(hit['class'])
        if db_name is None:
            log['no_db_key'].append(hit['class'])
            continue
        test = tests[slot]
        matched = [(i, norm(f)) for i, f in enumerate(foods) if test(f)]
        exact = next((m for m in matched if m[1] == db_name), None)
        if exact is not None:
            log['applied'].append({'slot': slot, 'from': exact[1], 'to': db_name,
                                   'changed': False})
            continue
        done = preempted_here = False
        for i, nm in matched:
            if i in touched:
                log['preempted'].append({'slot': slot, 'item': nm})
                preempted_here = True
                continue
            foods[i] = db_name
            touched.add(i)
            log['applied'].append({'slot': slot, 'from': nm, 'to': db_name,
                                   'changed': True})
            done = True
            break
        if not done and not preempted_here:
            log['disagreement'].append({'slot': slot, 'class': hit['class']})
    return log


def grade(gt, foods):
    """find_best_match 근사. EXACT > CONTAINS > NONE."""
    ns = [norm(f) for f in foods]
    g = norm(gt)
    if g in ns:
        return 'EXACT'
    for n in ns:
        if n and (n in g or g in n):
            return 'CONTAINS'
    return 'NONE'


def main():
    data = json.loads(RESULT.read_text(encoding='utf-8'))
    det = data['details']

    # ── 사실 확인 1: '인식 없음' 은 침묵이 아니다 ──────────────────────
    silent = [r for r in det if r['ai_name'] == '인식 없음']
    none_m = [r for r in det if r['match'] == 'NONE']
    empty = [r for r in det if not r['ai_foods_detected']]
    print('=' * 66)
    print('사실 확인 — 「GPT 침묵 48%」의 정체')
    print('=' * 66)
    print(f"  ai_name=='인식 없음'      : {len(silent)}장")
    print(f"  match=='NONE'             : {len(none_m)}장")
    print(f"  두 집합 동일              : {set(map(id, silent)) == set(map(id, none_m))}")
    print(f"  GPT 가 아무것도 못 낸 장  : {len(empty)}장   ← 진짜 침묵")
    print(f"  GT 가 GPT 응답에 있던 장  : "
          f"{sum(1 for r in silent if r['expected'] in r['ai_foods_detected'])}/{len(silent)}")
    print()

    # ── 사실 확인 2: 엔진이 정답을 알고도 버려진 장 ──────────────────
    rescue = [r for r in none_m
              if r['food30_engine'] and any(
                  v.get('class') == r['expected']
                  for v in (r['food30_engine'].get('detected') or {}).values())]
    print(f"  ★ 엔진이 GT 를 검출했는데 최종 NONE : {len(rescue)}장")
    print(f"     그중 applied 가 비어 있음(교체 시도조차 안 함) : "
          f"{sum(1 for r in rescue if not r['food30_engine']['applied'])}장")
    print()

    # ── 시뮬레이션 ────────────────────────────────────────────────────
    base = {r['match'] for r in det}
    print('=' * 66)
    print('카테고리 검사 확장 시뮬레이션 (API 호출 없음 · $0)')
    print('=' * 66)
    print(f"{'레벨':<34}{'EXACT':>7}{'Δ':>6}{'획득':>6}{'상실':>6}{'획득:상실':>11}")

    l0_grade = None
    for level, label in [(0, 'L0 현재(food30 30종만)'),
                         (1, "L1 +'탕/해장국' 접미사"),
                         (2, "L2 +'국/찌개/전골'"),
                         (3, "L3 +밥 접미사까지")]:
        tests = make_tests(level)
        grades, changes = [], []
        for r in det:
            foods = list(r['ai_foods_detected'])
            hits = {k: v for k, v in (r['food30_engine'] or {}).get('detected', {}).items()}
            log = override(foods, hits, tests)
            g = grade(r['expected'], foods)
            grades.append(g)
            changes.append((r, foods, log, g))
        exact = sum(1 for g in grades if g == 'EXACT')
        if level == 0:
            l0_grade = grades
            gain = loss = 0
        else:
            gain = sum(1 for a, b in zip(l0_grade, grades)
                       if a != 'EXACT' and b == 'EXACT')
            loss = sum(1 for a, b in zip(l0_grade, grades)
                       if a == 'EXACT' and b != 'EXACT')
        ratio = f'{gain/loss:.2f} : 1' if loss else (f'{gain} : 0' if gain else '—')
        d = exact - sum(1 for g in l0_grade if g == 'EXACT')
        print(f'{label:<34}{exact:>7}{d:>+6}{gain:>6}{loss:>6}{ratio:>11}')

        if level == 3:
            print()
            print('  ── L3 가 L2 에 더한 장(밥류) ──')
            for a, (r, foods, log, g) in zip(l0_grade, changes):
                if a != 'EXACT' and g == 'EXACT' and any(
                        x.get('slot') == 'rice' and x.get('changed') for x in log['applied']):
                    print(f"     GT={r['expected']:7s} GPT={r['ai_foods_detected']!s:26s}"
                          f" → {foods}")
            print()

        if level == 2:
            print()
            print('  ── L2 상세: 새로 맞힌 장 ──')
            for a, (r, foods, log, g) in zip(l0_grade, changes):
                if a != 'EXACT' and g == 'EXACT':
                    print(f"     GT={r['expected']:7s} GPT={r['ai_foods_detected']!s:26s}"
                          f" → {foods}")
            print('  ── L2 상세: 새로 틀린 장 ──')
            n_loss = 0
            for a, (r, foods, log, g) in zip(l0_grade, changes):
                if a == 'EXACT' and g != 'EXACT':
                    n_loss += 1
                    print(f"     GT={r['expected']:7s} GPT={r['ai_foods_detected']!s:26s}"
                          f" → {foods}")
            if n_loss == 0:
                print('     (없음)')
            print()

    print()
    print('※ L0 재현값이 실측 EXACT 137 과 다르면 grade() 근사의 한계다 — 아래 검산 참조.')
    print(f"   실측 EXACT = {sum(1 for r in det if r['match']=='EXACT')}, "
          f"L0 재현 EXACT = {sum(1 for g in l0_grade if g=='EXACT')}")


if __name__ == '__main__':
    main()
