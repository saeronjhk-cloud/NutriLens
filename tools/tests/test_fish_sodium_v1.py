"""생선구이 나트륨 보정 v1 (2026-10-14) — 제이 결정 «식약처 비율 환산»
배경: CORE 수기(hand) 생선구이 4종(고등어 68·꽁치 62·삼치 60·가자미 78mg/100g)이 소금 없는 생선 값.
      같은 CORE 의 식약처 생선구이(갈치 297 ~ 조기 1,364)보다 4~20배 낮아 생선구이정식 나트륨이 1,520→522mg 로 내려갔다(R76).
규칙: core_serving_overrides_v1.json «nutrient» = {음식: {sodium: 값, src: 근거}} — 나트륨만 교체(열량·단백질 유지).
      값 = 식약처 음식 DB(gold_foods.json source=mfds) «단백질 1g당 나트륨» × CORE 단백질(뼈 무게 차이 보정).
      고등어 235/12.7×20.3=376 · 꽁치 279/21.6×20.6=266 · 삼치 167/9.2×21.5=390 · 가자미 291/14.7×18.8=372.
기대값은 수정 «전»에 고정. 실행: python tools/tests/test_fish_sodium_v1.py
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

EXP = {  # 음식: (나트륨/100g, 1인분 g, 열량/100g 불변, 단백질/100g 불변)
    '고등어구이': (376, 100, 190, 20.3),
    '꽁치구이':   (266, 100, 171, 20.6),
    '삼치구이':   (390, 100, 155, 21.5),
    '가자미구이': (372, 120, 91, 18.8),
}
C = fa.CORE_FOODS
for k, (na, sv, cal, prot) in EXP.items():
    d = C.get(k, {})
    # F1 CORE 나트륨 교체 · 다른 값 불변
    check(d.get('sodium') == na, f'F1 {k} 나트륨 {d.get("sodium")} ≠ {na}')
    check(d.get('serving') == sv and d.get('cal') == cal and d.get('prot') == prot, f'F1 {k} 다른 값 바뀜 {d}')
    # F2 이름만(사진·검색 공통 match_with_db) 1인분 나트륨
    f = {'name_ko': k, 'estimated_serving_g': sv}
    with contextlib.redirect_stdout(io.StringIO()):
        fa.match_with_db({'foods': [f]}, None)
    check(f.get('sodium_mg') is not None and abs(f['sodium_mg'] - na * sv / 100) <= 1, f'F2 {k} 사진 {sv}g 나트륨 {f.get("sodium_mg")}')
    # F3 검색 resolve
    with contextlib.redirect_stdout(io.StringIO()):
        r = fl.resolve_food(k, sv)
    check(r is not None and abs(r['sodium_mg'] - na * sv / 100) <= 1, f'F3 {k} resolve {r and r.get("sodium_mg")}')
# F4 생선구이정식 = 구성 합산에 새 고등어 나트륨 반영 (흰밥·김치·콩나물무침 + 고등어 376)
with contextlib.redirect_stdout(io.StringIO()):
    r = fl.resolve_food('생선구이정식')
sc = {c['name_ko']: c for c in (r or {}).get('set_components') or []}
check(abs(sc.get('고등어구이', {}).get('sodium_mg', 0) - 376) <= 1, f'F4 정식 안 고등어 {sc.get("고등어구이", {}).get("sodium_mg")}')
_others = sum(C[c]['sodium'] * g / 100 for c, g in (('흰밥', 210), ('배추김치', 40), ('콩나물무침', 60)))
check(r is not None and abs(r['sodium_mg'] - (_others + 376)) <= 2, f'F4 정식 합 {r and r.get("sodium_mg")} ≠ {_others + 376:.1f}')
# F5 다른 생선 불변(식약처 구이 · 생물 고등어)
for k, na in (('갈치구이', 297.24), ('조기구이', 1364.48), ('고등어석쇠구이', 126.26), ('고등어', None)):
    if na is not None:
        check(abs(C[k]['sodium'] - na) < 0.01, f'F5 {k} 바뀜 {C[k]["sodium"]}')
check(C.get('고등어', {}).get('sodium') != 376, 'F5 생물 고등어까지 바뀜')

print(f'test_fish_sodium_v1: {n - len(fails)}/{n} 통과')
for m in fails: print('  ✗', m)
sys.exit(1 if fails else 0)
