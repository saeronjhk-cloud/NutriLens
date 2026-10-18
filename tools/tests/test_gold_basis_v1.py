"""gold mfds 100mL 행 바로잡기 v1 (2026-10-18, 제이 «진행») — 조사 .tmp/dq_v1/
gold_foods.json 의 source=mfds 8,313행 중 3,857행이 식약처 음식 «기준 100mL»(D4~D7·D2) 행에서 온 값(규칙 169 위반).
그중 엔진에서 닿고(CORE 가 먼저 안 이김) 같은 이름(단어 순서 뒤집은 이름 포함) D1~D3 100g 행이 있고, 4·4·9 검산·행 간 차이(30%) 통과,
기존 결정(자문·값 대조·overrides)과 겹치지 않는 240종을 100g 행 중앙값으로 교체 — tools/gold_basis_fix_v1.json (gold_foods.json 원본은 그대로).
A 표본 교체 20 · B 전량 교체 1 · C 유지(기존 결정·음료 100mL·D1~D3 없음·CORE 먼저) · D 원본 파일 불변.
판정: _search_gold 100g당 kcal ±1 · Na ±2. 기대값은 수정 «전»에 고정. 실행: python tools/tests/test_gold_basis_v1.py
"""
import os, sys, io, json, hashlib, contextlib
T = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, T)
with contextlib.redirect_stdout(io.StringIO()):
    import food_analyzer as fa
    fa.load_food_db()
fails, n = [], 0
def check(ok, msg):
    global n
    n += 1
    if not ok: fails.append(msg)
def g(nm):
    with contextlib.redirect_stdout(io.StringIO()):
        return fa._search_gold(nm)
A = [('된장국배추', 20.0, 223.0, 17.0), ('김치볶음 참치', 122.0, 480.0, 72.0), ('라면 치즈', 99.0, 352.0, 95.0), ('소고기 장조림', 69.0, 543.0, 209.0), ('계란 덮밥', 143.0, 223.0, 152.0), ('고춧가루콩나물무침', 93.0, 203.0, 19.0), ('채소 감자볶음', 108.0, 208.0, 67.0), ('볶음 애호박', 59.0, 447.0, 64.0), ('국밥콩나물', 52.0, 172.0, 60.0), ('된장취나물', 44.0, 235.0, 63.0), ('새싹 샐러드', 74.0, 156.0, 117.0), ('고추장 장어구이', 259.0, 322.0, 134.0), ('미역국홍합', 26.0, 314.0, 9.0), ('닭고기육개장', 20.0, 167.0, 28.0), ('견과류 잔멸치볶음', 388.0, 1753.0, 154.0), ('국밥굴', 105.0, 252.0, 63.0), ('매운탕해물', 35.0, 368.0, 33.0), ('매운탕광어', 70.0, 300.0, 62.0), ('고춧가루 콩나물무침', 93.0, 203.0, 19.0), ('덮밥닭고기', 125.0, 153.0, 97.0)]
for nm, kcal, na, old in A:
    k, d = g(nm)
    check(d is not None and k == nm and abs(d['cal'] - kcal) <= 1 and abs((d.get('sodium') or 0) - (na or 0)) <= 2, f"A 교체 {nm} → {k} {d and (d['cal'], d.get('sodium'))} (기대 {kcal}·{na}, 전 {old})")
# B 전량: 교체 파일 v1 240 키가 모두 gold 에 반영
fx = json.load(open(os.path.join(T, 'gold_basis_fix_v1.json'), encoding='utf-8')) if os.path.exists(os.path.join(T, 'gold_basis_fix_v1.json')) else {'items': {}}
gold = fa._load_gold_db()
v1 = {k: v for k, v in fx.get('items', {}).items() if '규칙 169' in v.get('src', '')}   # v1.1 부터 자문 반영분이 같은 파일에 추가됨
bad = [k for k, v in fx.get('items', {}).items() if abs(gold[k]['cal'] - v['cal']) > 0.01]
check(len(v1) == 240 and not bad, f"B 전량 {len(v1)}/240 · 불일치 {bad[:5]}")
# C 유지
C = [('누룽지', 87.0), ('우엉볶음', 160.0), ('오징어탕수', 247.0), ('부추잡채', 108.0), ('게조림', 93.0),
     ('어묵탕', 21.0), ('감자구이', 123.0), ('김부각', 415.0), ('마카로니 샐러드', 138.0)]
for nm, kcal in C:
    k, d = g(nm)
    check(d is not None and abs(d['cal'] - kcal) <= 1, f"C 유지 {nm} → {k} {d and d['cal']} (기대 {kcal})")
# D 원본 불변
h = hashlib.sha256(open(os.path.join(T, '..', 'gold_foods.json'), 'rb').read()).hexdigest()[:12]
check(h == 'd6c1fd634e5f', f"D gold_foods.json 바뀜 {h}")
print(f'test_gold_basis_v1: {n - len(fails)}/{n} 통과')
for m in fails: print('  ✗', m)
sys.exit(1 if fails else 0)
