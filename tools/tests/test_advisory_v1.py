"""외부 자문 1라운드 v1 반영 (2026-10-18) — 판단 필요 28종 중 «Claude·자문 2곳 합의» 19종
자문: IP/integration/value_advisory_v1.md → reply_1.md · reply_2.md (제이 10:41 전달). 합의 = 두 자문이 같은 선택 + Claude 추천에 포함.
바뀜 15(식약처 D1~D3 100g 또는 USDA 같은 음식) · 유지 4(누룽지·도미찜·우엉볶음·부추잡채). 1인분은 그대로.
충돌 9 = 제이 결정(10:49 «추천대로»): 옥수수밥 B · 무나물 A · 우동 B 바뀜 / 코다리조림·꼬리곰탕·뼈다귀해장국(B=현재값)·감자튀김·오징어탕수·햄구이 유지 → ITEMS2.
판정: _search_gold 100g당 kcal ±1 · Na ±2 · 기록 경로 1인분 그대로. 기대값은 수정 «전»에 고정. 실행: python tools/tests/test_advisory_v1.py
"""
import os, sys, io, contextlib
T = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, T)
with contextlib.redirect_stdout(io.StringIO()):
    import food_analyzer as fa
    import food_lookup as fl
    fa.load_food_db()
fails, n = [], 0
def check(ok, msg):
    global n
    n += 1
    if not ok: fails.append(msg)
ITEMS = [('굴비구이', 177.0, 1364.0, 150), ('대추차', 75.0, 30.0, 200), ('채소튀김', 239, 12.0, 100), ('달걀국', 13.0, 246.0, 450), ('깻잎김치', 62.0, 924.0, 100), ('두부조림', 143.0, 161.0, 100), ('누룽지', 87.0, 1.0, 100), ('도미찜', 126.3, 882.0, 250), ('무말랭이무침', 121.0, 450.0, 50), ('도라지생채', 77.0, 283.0, 120), ('고등어튀김', 284, 409, 150), ('잔치국수', 44.0, 216.0, 500), ('우엉볶음', 160.0, 457.0, 100), ('가자미튀김', 217, 405, 100), ('부추잡채', 108.0, 290.0, 100), ('애호박찜', 36.0, 574.0, 250), ('팟타이 소스', 87.0, 1744.0, 100), ('새우튀김', 242, 344, 100), ('돼지갈비구이', 294.0, 389.0, 300)]
for nm, kcal, na, sv in ITEMS:
    with contextlib.redirect_stdout(io.StringIO()):
        k, d = fa._search_gold(nm); r = fl.resolve_food(nm, None)
    check(d is not None and abs(d['cal'] - kcal) <= 1 and abs((d.get('sodium') or 0) - na) <= 2, f"V {nm} → {k} {d and (d['cal'], d.get('sodium'))} (기대 {kcal}·{na})")
    check(r is not None and r.get('estimated_serving_g') == sv, f"S {nm} 1인분 {r and r.get('estimated_serving_g')} (그대로 {sv})")
ITEMS2 = [('옥수수밥', 113.0, 4.0, 400), ('코다리조림', 146.7, 433.2, 100), ('꼬리곰탕', 107.2, 109.2, 700), ('뼈다귀해장국', 71.0, 309.0, 600), ('감자튀김', 312, 210, 115), ('오징어탕수', 247.0, 332.0, 100), ('무나물', 21.0, 352.0, 50), ('우동', 60.0, 341.0, 500), ('햄구이', 339.41, 886.32, 170)]
for nm, kcal, na, sv in ITEMS2:
    with contextlib.redirect_stdout(io.StringIO()):
        k, d = fa._search_gold(nm); r = fl.resolve_food(nm, None)
    check(d is not None and abs(d['cal'] - kcal) <= 1 and abs((d.get('sodium') or 0) - na) <= 2, f"J {nm} → {k} {d and (d['cal'], d.get('sodium'))} (기대 {kcal}·{na})")
    check(r is not None and r.get('estimated_serving_g') == sv, f"JS {nm} 1인분 {r and r.get('estimated_serving_g')} (그대로 {sv})")
print(f'test_advisory_v1: {n - len(fails)}/{n} 통과')
for m in fails: print('  ✗', m)
sys.exit(1 if fails else 0)
