"""백반·정식 구성 합산 v1 (2026-10-13) — 제이 결정 «구성 합산»
배경: CORE 백반·정식 5종이 찌개 한 그릇 무게(400g)라 ≈30% 과소(김치찌개백반 400kcal < 김치찌개 200 + 흰밥 315).
규칙: core_serving_overrides_v1.json «set_meal» = {세트: {구성 음식: g}} → CORE 세트값 = Σ(구성 100g당 × g) / Σg, 1인분 = Σg.
      resolve(이름만)는 set_components(구성별 엔진 확정값, 양 칩용 portion_base_g = g)를 함께 준다 → 웹이 나눠 담음.
기본 반찬 2종(배추김치 40 · 콩나물무침 60), 국 제외. 기대값은 수정 «전»에 고정. 실행: python tools/tests/test_set_meal_v1.py
"""
import os, sys, io, contextlib
T = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, T)
with contextlib.redirect_stdout(io.StringIO()):
    import food_analyzer as fa
    import food_lookup as fl
fails, n = [], 0
def check(ok, msg):
    global n
    n += 1
    if not ok: fails.append(msg)

EXP = {  # 세트: (구성 [(이름, g)], 기대 kcal)
    '김치찌개백반': ([('흰밥', 210), ('김치찌개', 400), ('배추김치', 40), ('콩나물무침', 60)], 542.8),
    '된장찌개백반': ([('흰밥', 210), ('된장찌개', 400), ('배추김치', 40), ('콩나물무침', 60)], 494.8),
    '불고기백반':   ([('흰밥', 210), ('불고기', 200), ('배추김치', 40), ('콩나물무침', 60)], 656.8),
    '제육정식':     ([('흰밥', 210), ('제육볶음', 150), ('배추김치', 40), ('콩나물무침', 60)], 627.8),
    '생선구이정식': ([('흰밥', 210), ('고등어구이', 100), ('배추김치', 40), ('콩나물무침', 60)], 532.8),
    # v1.1 (2026-10-14 제이 «제육쌈밥 기준 합산»): 쌈밥 = 흰밥 + 제육볶음 + 상추쌈 + 쌈장 15g + 기본 반찬 2종
    #   315 + 285 + 7.5 + 21.3(쌈장 142/100g, 식약처 음식 DB) + 6.8 + 21 = 656.6
    '쌈밥':         ([('흰밥', 210), ('제육볶음', 150), ('상추쌈', 50), ('쌈장', 15), ('배추김치', 40), ('콩나물무침', 60)], 656.6),
}
SM = getattr(fa, 'SET_MEALS', {})
for name, (comps, kcal) in EXP.items():
    # S1 표가 실렸고 1인분 = Σg
    check(SM.get(name) == dict(comps), f'S1 {name} 표: {SM.get(name)}')
    check(fa.CORE_FOODS.get(name, {}).get('serving') == sum(g for _, g in comps), f'S1 {name} 1인분 {fa.CORE_FOODS.get(name, {}).get("serving")}')
    # S2 이름만(사진·검색 공통 match_with_db) = 구성 합산
    f = {'name_ko': name, 'estimated_serving_g': None}
    with contextlib.redirect_stdout(io.StringIO()):
        fa.match_with_db({'foods': [f]}, None)
    check(f.get('calories_kcal') is not None and abs(f['calories_kcal'] - kcal) <= 2, f'S2 {name} 이름만 {f.get("calories_kcal")} ≠ {kcal}')
    # S3 resolve(이름만) → set_components 4개 · 각 g · 기준 g · 합 = 세트
    with contextlib.redirect_stdout(io.StringIO()):
        r = fl.resolve_food(name)
    sc = (r or {}).get('set_components') or []
    check([c['name_ko'] for c in sc] == [c for c, _ in comps], f'S3 {name} 구성 이름 {[c.get("name_ko") for c in sc]}')
    check([c['estimated_serving_g'] for c in sc] == [g for _, g in comps], f'S3 {name} 구성 g {[c.get("estimated_serving_g") for c in sc]}')
    check([c.get('portion_base_g') for c in sc] == [g for _, g in comps], f'S3 {name} 구성 기준 g')
    check(abs(sum(c['calories_kcal'] for c in sc) - r['calories_kcal']) <= 1.5, f'S3 {name} 구성 합 {sum(c["calories_kcal"] for c in sc)} ≠ {r["calories_kcal"]}')
    for c in sc:
        check(not c.get('set_components'), f'S3 {name} 구성 안 중첩')
# S8 (v1.1) 쌈장 CORE 신규(overrides «add», 식약처 음식 DB 값) · 1인분 15g
_sj = fa.CORE_FOODS.get('쌈장', {})
check(_sj.get('serving') == 15 and _sj.get('cal') == 142.0 and _sj.get('sodium') == 2335.0, f'S8 쌈장 {_sj}')
with contextlib.redirect_stdout(io.StringIO()):
    _r = fl.resolve_food('쌈밥')
check(_r is not None and abs(_r['sodium_mg'] - 1781.95) <= 2, f'S8 쌈밥 나트륨 {_r and _r.get("sodium_mg")}')
check(fa.CORE_FOODS.get('비빔쌈밥', {}).get('serving') == 300 and '비빔쌈밥' not in fa.SET_MEALS, 'S8 비빔쌈밥 바뀜')
# S4 사진 무게가 있거나 양 선택이면 나누지 않음(한 줄 유지)
with contextlib.redirect_stdout(io.StringIO()):
    r1 = fl.resolve_food('김치찌개백반', 355)
    r2 = fl.resolve_food('김치찌개백반', 710, '많게')
check(r1 is not None and not r1.get('set_components'), 'S4 무게 있음 → 나누지 않음')
check(r2 is not None and not r2.get('set_components'), 'S4 양 선택 → 나누지 않음')
# S5 세트 아닌 음식 불변
with contextlib.redirect_stdout(io.StringIO()):
    k = fl.resolve_food('김치찌개')
check(k['calories_kcal'] == 200.0 and not k.get('set_components'), f'S5 김치찌개 {k}')
# S6 검색 목록에도 합산값
with contextlib.redirect_stdout(io.StringIO()):
    items = {x['name_ko']: x for x in fl.search_foods('김치찌개백반')}
check(items.get('김치찌개백반', {}).get('serving_g') == 710, f'S6 검색 {items.get("김치찌개백반")}')
# S7 순수 빌더: 구성 하나라도 CORE 에 없으면 그 세트는 건너뜀
core = {'a': {'cal': 100, 'prot': 1, 'carbs': 1, 'fat': 1, 'fiber': 0, 'sodium': 10, 'sugar': 0, 'serving': 100},
        'b': {'cal': 50, 'prot': 2, 'carbs': 2, 'fat': 2, 'fiber': 1, 'sodium': 20, 'sugar': 1, 'serving': 200}}
sets, built = fa._build_set_meals({'ab': {'a': 100, 'b': 100}, 'ax': {'a': 100, 'x': 50}}, core)
check(list(sets) == ['ab'], f'S7 건너뜀 {list(sets)}')
check(built['ab']['serving'] == 200 and built['ab']['cal'] == 75.0 and built['ab']['sodium'] == 15.0, f'S7 값 {built.get("ab")}')
for x in fails: print('FAIL', x)
print(f'{n - len(fails)}/{n}')
sys.exit(1 if fails else 0)
