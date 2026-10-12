"""포장 기준(건면·분말) v2 — GPT 완성 무게 → 봉지(1회분) 수 환산 (2026-10-13)
v1(0e1cc7a): GPT g 무시, 무조건 1회분. → 2봉 먹어도 1봉.
v2: 봉지 수 = GPT 완성 무게 ÷ 1회분 완성 무게(자문 Q1 확정: 라면 600 · 컵라면 350 · 불닭·짜파게티 280 · 오트밀 240 ·
    미숫가루 230 · 게이너 500 · 프로틴 280), 0.5 단위(정확히 .25/.75 는 아래로), 최소 1 · 최대 3. GPT 무게 없으면 1.
    최소 1 인 이유: GPT 가 건조 무게(라면 120g)를 주는 경우를 반 봉지로 깎지 않기 위해.
기대값은 수정 «전»에 고정. 실행: python tools/tests/test_package_basis_v2.py  (v1 테스트를 대체)
"""
import os, sys, io, contextlib
sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')))
with contextlib.redirect_stdout(io.StringIO()):
    import food_analyzer as fa

# (이름, GPT g, 기대 봉지 수) — kcal 기대 = 1회분 kcal × 봉지 수
CASES = [
    ('라면', 500, 1), ('라면', 120, 1), ('라면', None, 1), ('라면', 750, 1), ('라면', 900, 1.5), ('라면', 1200, 2), ('라면', 5000, 3),
    ('신라면', 550, 1), ('진라면', 1150, 2), ('컵라면', 300, 1), ('컵라면', 700, 2), ('편의점컵라면', 300, 1),
    ('불닭볶음면', 300, 1), ('불닭볶음면', 560, 2), ('짜파게티', 350, 1), ('짜파게티', 420, 1.5),
    ('오트밀', 250, 1), ('오트밀', 480, 2), ('미숫가루', 300, 1.5), ('게이너쉐이크', 400, 1),
    ('소이프로틴', 300, 1), ('식물성프로틴', 300, 1), ('즉석 라면', 500, 1),
]
CONTROLS = [('라면땅', 90, 405.0), ('짜장면', 600, None), ('김치찌개', 400, 200.0), ('사과', 200, 114.0), ('갈비탕', 350, 280.0)]
fails = []
def run(name, g):
    f = {'name_ko': name, 'estimated_serving_g': g}
    with contextlib.redirect_stdout(io.StringIO()):
        fa.match_with_db({'foods': [f]}, None)
    return f
for name, g, packs in CASES:
    f = run(name, g)
    d = fa.CORE_FOODS[f['db_name']]
    exp = round(d['cal'] * d['serving'] / 100 * packs, 1)
    if abs((f.get('calories_kcal') or 0) - exp) > 1:
        fails.append(f'{name} GPT {g}g: 기대 {packs}봉 {exp}kcal · 실제 {f.get("calories_kcal")}kcal')
    if f.get('serving_basis') != 'package':
        fails.append(f'{name}: serving_basis 표시 없음')
for name, g, kcal in CONTROLS:
    f = run(name, g)
    if kcal is not None and abs((f.get('calories_kcal') or 0) - kcal) > 1:
        fails.append(f'[대조] {name} GPT {g}g: 기대 {kcal} · 실제 {f.get("calories_kcal")}')
    if f.get('serving_basis') == 'package':
        fails.append(f'[대조] {name}: 포장 기준으로 잘못 처리')
for x in fails: print('FAIL', x)
n = len(CASES) + len(CONTROLS)
print(f'{n - len(fails)}/{n}')
sys.exit(1 if fails else 0)
