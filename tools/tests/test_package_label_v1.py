"""포장제품 라벨 1회분 영양 v1 (2026-10-13) — core_serving_overrides_v1.json 의 label 섹션
지금까지 CORE 값: 신라면·진라면·라면 450kcal(라벨 500) · 짜파게티 518(610) · 컵라면 195(300, «1컵 값»이 100g당 자리에 들어간 오류)
라벨 = 제조사 표시값(코스트코 상품 페이지 영양정보, URL 은 json 에). 1봉 = 라벨 그대로 나와야 한다.
기대값은 수정 «전»에 고정. 실행: python tools/tests/test_package_label_v1.py
"""
import os, sys, io, json, contextlib
T = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, T)
with contextlib.redirect_stdout(io.StringIO()):
    import food_analyzer as fa
L = json.load(open(os.path.join(T, 'core_serving_overrides_v1.json'), encoding='utf-8'))['label']
fails = []
def run(name, g):
    f = {'name_ko': name, 'estimated_serving_g': g}
    with contextlib.redirect_stdout(io.StringIO()):
        fa.match_with_db({'foods': [f]}, None)
    return f
MAP = {'kcal': 'calories_kcal', 'carbs': 'carbs_g', 'fat': 'fat_g', 'prot': 'protein_g', 'sodium': 'sodium_mg', 'sugar': 'sugar_g'}
for k, lab in L.items():
    for g, packs in ((None, 1), (lab['serving'] * 4.0, None)):     # 무게 없음 = 1봉 · 완성 무게 = 봉지 수 환산(포장 v2)
        f = run(k, g)
        n = f.get('package_count') or 1
        if packs is not None and n != packs:
            fails.append(f'{k} GPT {g}: 봉지 {n}')
        for lk, fk in MAP.items():
            exp = lab[lk] * n
            tol = max(1.0, exp * 0.01)
            if abs((f.get(fk) or 0) - exp) > tol:
                fails.append(f'{k} GPT {g} {lk}: 기대 {exp} · 실제 {f.get(fk)}'); break
# 대조: 불닭볶음면(라벨 530kcal 와 이미 일치, 표에 없음) · 다른 음식 불변
for name, g, kcal in (('불닭볶음면', None, 529.2), ('김치찌개', 400, 200.0), ('라면땅', 90, 405.0)):
    f = run(name, g)
    if abs((f.get('calories_kcal') or 0) - kcal) > 1:
        fails.append(f'[대조] {name}: 기대 {kcal} · 실제 {f.get("calories_kcal")}')
for x in fails: print('FAIL', x)
n = len(L) * 2 + 3
print(f'{n - len(fails)}/{n}')
sys.exit(1 if fails else 0)
