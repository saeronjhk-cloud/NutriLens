"""닭똥집 소금구이 v1 (2026-10-15) — 제이 결정: 나트륨 380/100g(식당 실제값) · 별칭 전부 연결
배경: 자문 v1(10-14) «닭똥집구이 68 유지, 향후 닭똥집소금구이 별도 항목 권고» · 운영 엔진이 닭똥집·닭똥집볶음·닭똥집 소금구이를 못 찾음(10-15 확인)
신규는 overrides json «add», 별칭은 «alias»(규칙 123). 기대값은 수정 «전»에 고정.
실행: python tools/tests/test_gizzard_v1.py
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
C = fa.CORE_FOODS
SALT = {'cal': 94, 'prot': 17.7, 'carbs': 0, 'fat': 2.6, 'fiber': 0, 'sodium': 380, 'sugar': 0, 'serving': 80}
BOKKEUM = {'cal': 72.0, 'prot': 8.2, 'carbs': 3.8, 'fat': 2.3, 'fiber': 0.8, 'sodium': 159.0, 'sugar': 1.4, 'serving': 100}
# G1 신규 값(열량·단백질은 CORE 닭똥집구이와 같고 나트륨만 소금 간)
for name, exp in (('닭똥집소금구이', SALT), ('닭똥집볶음', BOKKEUM)):
    d = C.get(name, {})
    for k, v in exp.items():
        check(d.get(k) == v, f'G1 {name} {k} {d.get(k)} ≠ {v}')
# G2 별칭 → 같은 값
for a, std in (('닭똥집', '닭똥집소금구이'), ('소금닭똥집', '닭똥집소금구이'), ('똥집소금구이', '닭똥집소금구이'),
               ('닭모래집소금구이', '닭똥집소금구이'), ('닭근위소금구이', '닭똥집소금구이')):
    check(C.get(a) is not None and C.get(a) == C.get(std), f'G2 별칭 {a} ≠ {std}: {C.get(a)}')
# G3 resolve(사진 없이 기록 경로) — 1인분 기본
for nm, sv, na, kcal in (('닭똥집소금구이', 80, 304.0, 75.2), ('닭똥집', 80, 304.0, 75.2),
                         ('소금닭똥집', 80, 304.0, 75.2), ('닭똥집볶음', 100, 159.0, 72.0)):
    with contextlib.redirect_stdout(io.StringIO()):
        r = fl.resolve_food(nm, None)
    check(r is not None, f'G3 {nm} 미매칭')
    if r:
        check(r.get('estimated_serving_g') == sv, f'G3 {nm} g {r.get("estimated_serving_g")} ≠ {sv}')
        check(abs(r['sodium_mg'] - na) <= 1, f'G3 {nm} Na {r["sodium_mg"]} ≠ {na}')
        check(abs(r['calories_kcal'] - kcal) <= 1, f'G3 {nm} kcal {r["calories_kcal"]} ≠ {kcal}')
# G4 띄어쓰기 «닭똥집 소금구이» 도 소금구이 값
with contextlib.redirect_stdout(io.StringIO()):
    r = fl.resolve_food('닭똥집 소금구이', None)
check(r is not None and abs(r['sodium_mg'] - 304.0) <= 1, f'G4 띄어쓰기 {r and (r.get("name_ko"), r.get("sodium_mg"))}')
# G5 기존 유지: 닭똥집구이 68(무염) · 쌈장 · 자문 v1 값
check(C['닭똥집구이']['sodium'] == 68 and C['닭똥집구이']['serving'] == 80, f'G5 닭똥집구이 {C["닭똥집구이"]}')
check(C['쌈장']['sodium'] == 2335.0, 'G5 쌈장')
check(C['스테이크']['sodium'] == 120 and C['고등어구이']['sodium'] == 376, 'G5 자문·생선')
print(f'test_gizzard_v1: {n - len(fails)}/{n} 통과')
for m in fails: print('  ✗', m)
sys.exit(1 if fails else 0)
