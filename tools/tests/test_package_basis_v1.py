"""포장 기준(건면·분말) CORE 항목 — GPT «조리된 그릇 무게» × «건조 100g당 영양» 곱셈 결함 수정 (2026-10-12)
결함: 라면 CORE = 건면 1봉 120g · 375kcal/100g. GPT 는 끓인 그릇 무게(≈500g)를 준다 → 1,448kcal · 나트륨 5,790mg.
      10-11 양 보정 v2(C3b)가 «CORE 덮어쓰기»를 없애면서 volume 항목(라면류)에서 드러남(그 전엔 덮어쓰기가 120g 으로 되돌려 가렸음).
      piece 항목(짜파게티·오트밀·미숫가루·프로틴 분말)은 원래부터 GPT g 100% → 3~4배 과대.
수정 v1: 이 항목들은 GPT g 와 무관하게 «1회분(포장 1개 = CORE serving)» 으로 계산 — 배포 전(덮어쓰기 시절) 라면 결과와 같다.
기대값은 수정 «전»에 고정. 실행: python tools/tests/test_package_basis_v1.py
"""
import os, sys, io, contextlib
sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')))  # normpath: food_analyzer 가 Path(__file__).parent.parent 로 gold_foods.json 을 찾음
with contextlib.redirect_stdout(io.StringIO()):
    import food_analyzer as fa

# (이름, GPT g, 기대 kcal, 기대 나트륨) — 기대 = CORE serving × 100g당 값 (1회분)
CASES = [
    ('라면', 500, 450.0, 1800.0), ('라면', 120, 450.0, 1800.0), ('라면', None, 450.0, 1800.0),
    ('신라면', 550, 450.0, 1800.0), ('진라면', 500, 450.0, 1800.0),
    ('컵라면', 300, 195.0, 845.0), ('편의점컵라면', 300, 286.0, None),
    ('불닭볶음면', 300, 529.2, None), ('짜파게티', 350, 518.0, None),
    ('오트밀', 250, 151.6, None), ('미숫가루', 300, 111.0, None),
    ('게이너쉐이크', 400, 400.0, None), ('소이프로틴', 300, 101.4, None), ('식물성프로틴', 300, 106.5, None),
    ('즉석 라면', 500, 450.0, 1800.0),   # 운영 기록 이름(10-02) → db 라면
]
# 대조군 — 수정 전과 같은 값이어야 함(포장 기준 아님)
CONTROLS = [('라면땅', 90, 405.0), ('짜장면', 600, 529.0), ('김치찌개', 400, 200.0), ('사과', 200, 114.0), ('갈비탕', 350, 280.0)]

fails = []
def run(name, g):
    f = {'name_ko': name, 'estimated_serving_g': g}
    with contextlib.redirect_stdout(io.StringIO()):
        fa.match_with_db({'foods': [f]}, None)
    return f
for name, g, kcal, na in CASES:
    f = run(name, g)
    if abs((f.get('calories_kcal') or 0) - kcal) > 1 or (na is not None and abs((f.get('sodium_mg') or 0) - na) > 1):
        fails.append(f'{name} GPT {g}g: 기대 {kcal}kcal/{na}mg · 실제 {f.get("calories_kcal")}kcal/{f.get("sodium_mg")}mg ({f.get("db_name")})')
for name, g, kcal in CONTROLS:
    f = run(name, g)
    if abs((f.get('calories_kcal') or 0) - kcal) > 1:
        fails.append(f'[대조] {name} GPT {g}g: 기대 {kcal} · 실제 {f.get("calories_kcal")}')
for x in fails: print('FAIL', x)
n = len(CASES) + len(CONTROLS)
print(f'{n - len(fails)}/{n}')
sys.exit(1 if fails else 0)
