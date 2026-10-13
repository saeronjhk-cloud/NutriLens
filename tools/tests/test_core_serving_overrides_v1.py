"""CORE 1인분 덮어쓰기 표 v1 (2026-10-13) — 자문 1라운드(Gemini·ChatGPT) + 제이 확정 «추천대로»
원본 IP/통합앱_P1/eval/core_serving_overrides_v1.json · 코드 사본 tools/core_serving_overrides_v1.json
판정 기준(패킷 §3, 반영 전 고정): ① 동의어 불일치 0 ② 양 순서 평가 하락 0 ③ 배포 전 검증 ④ 전수 비교 = 표의 키만 변경
기대값은 수정 «전»에 고정. 실행: python tools/tests/test_core_serving_overrides_v1.py
"""
import os, sys, io, json, contextlib
T = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, T)
with contextlib.redirect_stdout(io.StringIO()):
    import food_analyzer as fa
O = json.load(open(os.path.join(T, 'core_serving_overrides_v1.json'), encoding='utf-8'))
fails = []
# ① 표의 값이 그대로 들어갔는가 + 동의어는 표준 키와 영양값·1인분이 같은가
for k, g in O['serving'].items():
    if fa.CORE_FOODS.get(k, {}).get('serving') != g:
        fails.append(f'1인분 {k}: 기대 {g} · 실제 {fa.CORE_FOODS.get(k, {}).get("serving")}')
for a, s in O['alias'].items():
    da, ds = fa.CORE_FOODS.get(a, {}), fa.CORE_FOODS.get(s, {})
    for f in ('cal', 'prot', 'carbs', 'fat', 'sodium', 'serving'):
        if da.get(f) != ds.get(f):
            fails.append(f'동의어 {a}≠{s} ({f}: {da.get(f)} vs {ds.get(f)})'); break
# 대표 사례 — 무게 없이 이름만(음식 고치기·검색 경로 = 1인분 100%)
def kcal(name):
    f = {'name_ko': name, 'estimated_serving_g': None}
    with contextlib.redirect_stdout(io.StringIO()):
        fa.match_with_db({'foods': [f]}, None)
    return f.get('calories_kcal')
CASES = [('아구찜', 287), ('붕어찜', 304), ('돼지국밥', 575), ('자장면', 441), ('짜장면', 441), ('닭도리탕', 360),
         ('참치죽', 412), ('짬뽕밥', 387), ('순대볶음', 435),
         ('공기밥', 315), ('공깃밥', 315), ('백미밥', 315), ('계란볶음밥', 584), ('파스타', 480), ('도미찜', 316), ('두부김치', 160)]   # 마지막 4 = 유지(K1·K2·K9·합의). 도미찜: CORE 1인분이 정확히 100g 이면 _estimate_realistic_serving 이 이름 규칙으로 추정(250g) — 현행 그대로
for n, e in CASES:
    k = kcal(n)
    if k is None or abs(k - e) > 2:
        fails.append(f'{n} 이름만: 기대 ≈{e}kcal · 실제 {k}')
for x in fails: print('FAIL', x)
n = len(O['serving']) + len(O['alias']) + len(CASES)
print(f'{n - len(fails)}/{n}')
sys.exit(1 if fails else 0)
