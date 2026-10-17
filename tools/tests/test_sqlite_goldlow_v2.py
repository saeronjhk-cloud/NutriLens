"""2순위 v2 (2026-10-17) — 제이 «진행»: gold 매칭이 «확신 낮음»이어도 가공식품 SQLite 에 «정확히 같은 이름»이 있으면 그 값
+ 같은 이름인데 식약처 값이 제각각인 이름(tools/mfds_ambiguous_names_v1.json, 11,021개)은 SQLite 값을 쓰지 않는다(2순위 전체)
기대값 = 식약처 API 100g당(10-17) — 수정 «전»에 고정. 실행: python tools/tests/test_sqlite_goldlow_v2.py
"""
import os, sys, io, contextlib, json
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
def near(a, b, tol=0.6): return a is not None and abs(a - b) <= tol
AI = dict(calories_kcal=300.0, protein_g=10.0, carbs_g=20.0, fat_g=15.0, fiber_g=1.5, sodium_mg=600.0, sugar_g=4.0)
def run(name, g):
    f = dict(AI, name_ko=name, estimated_serving_g=g)
    with contextlib.redirect_stdout(io.StringIO()):
        return fa.match_with_db({'foods': [f]}, None)['foods'][0]
def conf(nm):
    k, d = fa._search_gold(nm)
    return (k, fa._match_confidence(fa._normalize_food_name(nm), k) if d else 'none')

# 1) gold low + SQLite 정확·일관 → SQLite 값  (100g당 kcal, Na)
LOW_OK = {'신라면블랙': (429, 1328, 120), '새우깡블랙': (494, 713, 30), '바나나맛우유': (90, 34, 240),
          '고추장새우깡': (493, 960, 30), '신라면건면': (361, 1845, 120), '꼬막덮밥': None, '오징어채': None}
for nm, v in LOW_OK.items():
    k, c = conf(nm)
    if v is None: continue
    check(c == 'low', f'P1 {nm} gold {k} {c} — 전제(low) 깨짐')
    kc, na, g = v
    r = run(nm, g)
    check(r.get('source') == 'DB_MATCHED' and r.get('db_name') == nm, f'G1 {nm} {r.get("source"), r.get("db_name")}')
    check(near(r.get('calories_kcal'), round(kc * g / 100, 1)), f'G2 {nm} kcal {r.get("calories_kcal")} ≠ {round(kc*g/100,1)}')
    check(near(r.get('sodium_mg'), round(na * g / 100, 1), 1), f'G3 {nm} Na {r.get("sodium_mg")} ≠ {round(na*g/100,1)}')
    check(r.get('match_confidence') != 'low', f'G4 {nm} match_confidence low 남음')

# 2) 같은 이름 값이 제각각(모호) → AI 값 (gold low 든 gold 없음이든)
AMB = json.load(open(os.path.join(T, 'mfds_ambiguous_names_v1.json'), encoding='utf-8'))['names']
for nm in ['양념장', '크림', '물짜장', '갈릭토스트', '오징어채']:
    check(nm in AMB, f'P2 {nm} 모호 목록에 없음')
    r = run(nm, 100)
    check(r.get('source') == 'AI_ESTIMATED' and r.get('calories_kcal') == AI['calories_kcal'], f'A1 {nm} 모호인데 {r.get("source"), r.get("db_name"), r.get("calories_kcal")}')

# 3) 묶음 이름은 정확히 있어도 안 씀
r = run('햄&스위스 샌드위치', 150)
check(r.get('source') == 'AI_ESTIMATED', f'M1 햄&스위스 샌드위치 {r.get("source")}')

# 4) 회귀: gold high 그대로 · 2순위 v1 사례 그대로
r = run('신라면', 120)
check(r.get('source') in ('GOLD_REF', 'GOLD_DB') and r.get('db_name') == '신라면', f'R1 신라면 {r.get("source")}')
r = run('갈릭치즈마요소스', 80)   # 10-17 멕시코 이름 v1: «치즈 소스»는 CORE 로 → 2순위 예시 교체
check(r.get('source') == 'DB_MATCHED' and near(r.get('calories_kcal'), 380.0), f'R2 갈릭치즈마요소스 {r.get("source"), r.get("calories_kcal")}')
r = run('나초', 100)
check(r.get('source') == 'GOLD_REF', f'R3 나초 {r.get("source")}')

# 5) 운영 실사용 이름(.tmp/real_names + 10-08~16 사진): v2 로 바뀌는 이름 0 (양념장·크림은 모호라 그대로)
p = os.path.join(os.path.dirname(T), '.tmp', 'real_names', 'names.txt')
if os.path.exists(p):
    changed = []
    for nm in [x for x in open(p, encoding='utf-8').read().split('\n') if x]:
        k, c = conf(nm)
        if c != 'low': continue
        if run(nm, 100).get('source') == 'DB_MATCHED': changed.append(nm)
    check(changed == [], f'C1 실사용 이름 중 바뀐 것 {changed}')

# 6) 1회분 기준 행(FC 프랜차이즈·FP·KR·SD — 값이 «1인분당»)은 2순위에 쓰지 않는다(100g 기준 오계산 방지, gold 가 맡음)
#    10-17 발견: «양꼬치 (10꼬치)»(FP, 550kcal/250g) 를 100g당으로 읽으면 250g → 1,375kcal
for nm in ['양꼬치 (10꼬치)', '터키 브레스트 (15cm)']:
    r = run(nm, 250)
    check(r.get('source') != 'DB_MATCHED', f'S1 {nm} 1회분 기준 행을 100g 기준으로 씀 {r.get("db_name"), r.get("calories_kcal")}')
# 가공식품 CSV 행(PROC_, 기준량 100g)은 써도 된다
r = run('하인즈 버거소스(230g)', 20)
check(r.get('source') == 'DB_MATCHED' and near(r.get('calories_kcal'), 64.4), f'S2 하인즈 버거소스 {r.get("source"), r.get("calories_kcal")}')

print(f'test_sqlite_goldlow_v2: {n - len(fails)}/{n} 통과')
for m in fails: print('  ✗', m)
sys.exit(1 if fails else 0)
