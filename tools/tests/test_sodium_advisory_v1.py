"""CORE 나트륨 자문 v1 반영 (2026-10-14) — 제이 결론(자문 1라운드, IP/integration/sodium_advisory_v1*)
Q1 고기구이 = «고기만 유지»(곁들이 소금·기름장·쌈장은 별도 음식) · 스테이크만 조리 간 120 · 닭똥집구이 68 유지
Q2 짠 반찬: 오이지(무침 기준) 1,130 · 고추장아찌 2,577 · 부추김치 767 · 오이소박이 532 · 고사리나물 515 · 어묵볶음 600 유지
교체는 overrides json «nutrient» 로만(나트륨만, 열량·단백질 불변). 기대값은 수정 «전»에 고정.
실행: python tools/tests/test_sodium_advisory_v1.py
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
CHANGE = {  # 음식: (나트륨/100g, 1인분 g, 열량/100g, 단백질/100g)
    '스테이크':   (120, 200, 271, 26.0),
    '오이지':     (1130, 30, 12, 0.5),
    '고추장아찌': (2577, 30, 75, 1.8),
    '부추김치':   (767, 50, 22, 2.0),
    '오이소박이': (532, 50, 18, 1.0),
    '고사리나물': (515, 50, 35, 2.5),
}
KEEP = {'삼겹살': 60, '삼겹살구이': 65, '목살': 55, '항정살': 55, '항정살구이': 60, '소고기': 60, '닭똥집구이': 68, '어묵볶음': 600}
for k, (na, sv, cal, prot) in CHANGE.items():
    d = C.get(k, {})
    check(d.get('sodium') == na, f'N1 {k} 나트륨 {d.get("sodium")} ≠ {na}')
    check(d.get('serving') == sv and d.get('cal') == cal and d.get('prot') == prot, f'N1 {k} 다른 값 바뀜 {d}')
    with contextlib.redirect_stdout(io.StringIO()):
        r = fl.resolve_food(k, sv)
    check(r is not None and abs(r['sodium_mg'] - na * sv / 100) <= 1, f'N2 {k} resolve {sv}g {r and r.get("sodium_mg")}')
for k, na in KEEP.items():
    check(C.get(k, {}).get('sodium') == na, f'N3 {k} 유지 아님 {C.get(k, {}).get("sodium")}')
# N4 앞서 고친 생선구이 4종 그대로
for k, na in (('고등어구이', 376), ('꽁치구이', 266), ('삼치구이', 390), ('가자미구이', 372)):
    check(C[k]['sodium'] == na, f'N4 {k} {C[k]["sodium"]}')
print(f'test_sodium_advisory_v1: {n - len(fails)}/{n} 통과')
for m in fails: print('  ✗', m)
sys.exit(1 if fails else 0)
