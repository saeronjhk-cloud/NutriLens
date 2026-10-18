"""gold 값 근거 정리 — 외부 자문 1라운드 v1 반영 (2026-10-18, 규칙 175) — IP/integration/gold_basis_advisory_v1*.md
자문 2곳 모두 21종 Claude 추천과 같은 선택(20종 B · 게조림 A 유지) + 방침 P-3 → 전부 합의, 충돌 0.
A 반영 19(엔진에서 gold 로 닿는 이름) · B 게조림 유지 · C CORE 가 먼저 이겨 그대로 12 · D gold_foods.json 원본 불변.
판정: _search_gold 100g당 kcal ±1 · Na ±2. 기대값은 수정 «전»에 고정. 실행: python tools/tests/test_gold_basis_advisory_v1.py
"""
import os, sys, io, hashlib, contextlib
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
A = [('된장국 시금치', 15.0, 237.5, 14.0, 2), ('된장국시금치', 15.0, 237.5, 15.0, 2), ('된장국 시래기', 18.5, 267.0, 13.0, 3), ('된장국시래기', 18.5, 267.0, 13.0, 3), ('무국 소고기', 23.0, 216.5, 39.0, 4), ('무국소고기', 23.0, 216.5, 23.0, 4), ('김치찌개 햄', 47.0, 486.5, 44.0, 5), ('소고기 수육', 166.0, 31.0, 145.0, 9), ('소고기수육', 166.0, 31.0, 145.0, 9), ('수육 소고기', 166.0, 31.0, 145.0, 9), ('갈치구이 기름', 173.5, 346.0, 124.0, 10), ('기름 갈치구이', 173.5, 346.0, 124.0, 10), ('기름갈치구이', 173.5, 346.0, 124.0, 10), ('달걀부침(달걀후라이)', 208.0, 161.0, 184.0, 11), ('소등심 스테이크', 194.0, 134.0, 229.0, 17), ('스테이크소등심', 194.0, 134.0, 229.0, 17), ('소안심스테이크', 183.0, 103.0, 197.0, 18), ('된장국감자', 32.5, 344.5, 22.0, 19), ('매운탕대구', 41.0, 290.5, 54.0, 20)]
for nm, kcal, na, old, no in A:
    k, d = g(nm)
    check(d is not None and k == nm and abs(d['cal'] - kcal) <= 1 and abs((d.get('sodium') or 0) - na) <= 2, f"A #{no} {nm} → {k} {d and (d['cal'], d.get('sodium'))} (기대 {kcal}·{na}, 전 {old})")
k, d = g('게조림')
check(d is not None and abs(d['cal'] - 93.0) <= 1 and abs(d['sodium'] - 656.0) <= 2, f"B 게조림 유지 → {d and (d['cal'], d['sodium'])}")
CORE = [('곰탕', 60), ('시금치된장국', 30.4), ('시래기된장국', 25), ('햄김치찌개', 63.5), ('오리백숙', 111.67), ('추어탕', 55), ('가오리찜', 102.0), ('건새우볶음', 200), ('미역튀각', 442.0), ('깻잎나물', 76.0), ('달래무침', 61.67), ('미역초무침', 49.9)]
for nm, kcal in CORE:
    k, d = g(nm)
    check(d is not None and d.get('source') == 'core' and abs(d['cal'] - kcal) <= 0.01, f"C CORE 그대로 {nm} → {d and (d.get('source'), d['cal'])} (기대 core {kcal})")
h = hashlib.sha256(open(os.path.join(T, '..', 'gold_foods.json'), 'rb').read()).hexdigest()[:12]
check(h == 'd6c1fd634e5f', f"D gold_foods.json 바뀜 {h}")
print(f'test_gold_basis_advisory_v1: {n - len(fails)}/{n} 통과')
for m in fails: print('  ✗', m)
sys.exit(1 if fails else 0)
