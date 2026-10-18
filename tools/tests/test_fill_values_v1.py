"""값 없는 음식 채우기 v1 (2026-10-15) — 제이 결정 «추천대로 채움» + 수육 «값 유지, 1인분 200g»
출처 우선: 식약처 음식 DB(gold mfds) → USDA FoodData Central(SR Legacy 2018-04 · FNDDS 2024-10-31, fdcId 는 overrides src) → 국내 제품 라벨(gold db_gold)
기대값(100g당 kcal·나트륨, 1인분 g)은 수정 «전»에 고정. 실행: python tools/tests/test_fill_values_v1.py
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
def res(nm, g=None):
    with contextlib.redirect_stdout(io.StringIO()):
        return fl.resolve_food(nm, g)
EXP = {  # 이름: (kcal/100g, Na/100g, 1인분 g)
    '베이컨': (468, 1680, 20), '생크림': (346, 20, 20), '쿠키': (492, 311, 30), '올리브': (116, 735, 15),
    '피칸': (691, 0, 15), '부리또': (257, 571, 250), '볶음면': (190, 367, 300), '새우깡': (517, 678, 30),   # 10-18 흔한 이름 v1: 국내 라벨로 교체(전 426·571)
    '과카몰리': (155, 344, 30), '굴라쉬': (107, 307, 255), '칠리 소스': (180, 1100, 15), '할라피뇨 피클': (24, 1670, 20),
    '오크라': (22, 6, 80), '나시 레막': (267, 182, 150), '슈페츨레': (138, 165, 160), '웨이퍼 롤': (528, 85, 20),
    '크리스피 포크': (404, 448, 150), '감자 퓌레': (114, 210, 150), '감자 퓨레': (114, 210, 150),
    '호박 퓨레': (52, 131, 100), '호박 퓌레': (52, 131, 100), '튀긴 마늘': (491, 30, 10), '크랜베리 소스': (159, 5, 30),
    '감자 경단': (135, 279, 188), '가지 구이': (35, 207, 100), '복숭아 통조림': (39, 0, 150),
}
# 10-18 흔한 이름 v1: 굴라쉬·나시 레막·슈페츨레·감자 경단 1인분 = FNDDS QNS(규칙 156) · 새우깡 국내 라벨
C = fa.CORE_FOODS
for nm, (kcal, na, sv) in EXP.items():
    d = C.get(nm)
    check(d is not None and abs(d['cal'] - kcal) < 0.6 and abs(d.get('sodium', 0) - na) < 0.6 and d['serving'] == sv,
          f'F1 {nm} CORE {d and (d["cal"], d.get("sodium"), d["serving"])} ≠ {(kcal, na, sv)}')
    r = res(nm, sv)   # 1인분 g 를 주면 그 g 그대로(C3c 범위 안)
    check(r is not None and r.get('db_name') == nm and abs(r['calories_kcal'] - kcal * sv / 100) <= 1
          and abs(r['sodium_mg'] - na * sv / 100) <= 1, f'F2 {nm} resolve {r and (r.get("db_name"), r.get("calories_kcal"), r.get("sodium_mg"))}')
    check(res(nm) is not None, f'F3 {nm} 사진 없이 기록(양 없음) 미매칭')
# 수육: 값 유지(406.1/100g) · 1인분 300→200
d = C['돼지고기수육']
check(d['serving'] == 200 and abs(d['cal'] - 406.1) < 0.1, f'S1 돼지고기수육 {d["serving"]}g {d["cal"]}')
check(C.get('수육') == d, 'S2 수육 별칭 = 돼지고기수육')
r = res('수육')
check(r is not None and r['estimated_serving_g'] == 200 and abs(r['calories_kcal'] - 812.2) <= 1, f'S3 수육 resolve {r and (r["estimated_serving_g"], r["calories_kcal"])}')
print(f'test_fill_values_v1: {n - len(fails)}/{n} 통과')
for m in fails: print('  ✗', m)
sys.exit(1 if fails else 0)
