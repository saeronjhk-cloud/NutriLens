"""2순위(가공식품 SQLite DB) 수정 v1 (2026-10-17) — 제이 결정 «2순위 수정 v1»
발견: 운영 사진 분석(10-15) «치즈 소스» 80g → 2,357.3kcal. 원인 3가지
  D1 SQLite 값은 100g당인데 (GPT g ÷ 1회 제공량 g) 배율을 곱함 → ×(100/1회 제공량) 오차 (치즈 소스 15g → 6.7배)
  D2 sodium_mg 열에 탄수화물 비슷한 값(신라면 65.8 · 코카콜라 10.7), sugar·fiber 는 0 → 나트륨·당·섬유는 쓰면 안 됨
  D3 부분·역방향 매칭이 규칙 131~133(중심어·묶음 이름)을 거치지 않음(«베이컨과 계란 프라이»→«프라이»)
수정: 정확한 이름만 · 100g 기준 · 열량·단백·탄수·지방만 DB 값 · 나트륨·당·섬유는 AI 값 유지 · 묶음 이름 차단
  (10-17 멕시코 이름 v1: «치즈 소스»·«살사 소스»는 CORE(USDA FNDDS)가 맡게 됨 → 2순위 예시를 SQLite 에만 있는 «갈릭치즈마요소스»·«피자용 소스»로 교체, 검사 내용은 같음)
기대값은 수정 «전»에 고정. 실행: python tools/tests/test_sqlite_tier2_v1.py
"""
import os, sys, io, contextlib, copy
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
AI = dict(calories_kcal=300.0, protein_g=10.0, carbs_g=20.0, fat_g=15.0, fiber_g=1.5, sodium_mg=600.0, sugar_g=4.0)
def run(name, g):
    f = dict(AI, name_ko=name)
    if g is not None: f['estimated_serving_g'] = g
    with contextlib.redirect_stdout(io.StringIO()):
        return fa.match_with_db({'foods': [f]}, None)['foods'][0]
def near(a, b, tol=0.15): return a is not None and abs(a - b) <= tol

# 전제: 아래 이름은 gold 에 없음(2순위로 감) — 바뀌면 이 평가를 다시 짠다
for nm in ['갈릭치즈마요소스', '피자용 소스', '허니버터칩', '코카콜라', '햇반참기름', '구운 야채', '베이컨과 계란 프라이']:
    gk, gd = fa._search_gold(nm)
    check(gd is None, f'P0 {nm} gold 매칭 {gk} — 전제 깨짐')

# DB 100g당 값(10-17 확인 · 영양값 바로잡기 v1 뒤 탄수는 식약처 원값): (kcal, 단백, 탄수, 지방, 1회 제공량 g)
DB = {'갈릭치즈마요소스': (475, 1.0, 12.0, 47.0, 15), '피자용 소스': (56, 2.3, 10.8, 0.4, 15),
      '허니버터칩': (542, 5.0, 58.34, 31.67, 30), '코카콜라': (43, 0.0, 10.67, 0.0, 200), '햇반참기름': (900, 0, 0, 100, 10)}
DB_OLD_NA = {'갈릭치즈마요소스': 12.0, '피자용 소스': 10.8, '허니버터칩': 58.3, '코카콜라': 10.7, '햇반참기름': 0.0}
CASES = [('갈릭치즈마요소스', 80), ('피자용 소스', 50), ('허니버터칩', 60), ('코카콜라', 250), ('햇반참기름', 5), ('갈릭치즈마요소스', None)]
for nm, g in CASES:
    kcal, p, c, fat, sv = DB[nm]
    eg = g if g else sv
    r = run(nm, g)
    tag = f'{nm} {g}g'
    check(r.get('db_matched') is True and r.get('db_name') == nm and r.get('source') == 'DB_MATCHED', f'A1 {tag} 매칭 {r.get("db_matched"), r.get("db_name"), r.get("source")}')
    check(near(r.get('calories_kcal'), round(kcal * eg / 100, 1)), f'A2 {tag} kcal {r.get("calories_kcal")} ≠ {round(kcal*eg/100,1)} (100g 기준)')
    check(near(r.get('protein_g'), round(p * eg / 100, 1)), f'A3 {tag} 단백 {r.get("protein_g")}')
    check(near(r.get('carbs_g'), round(c * eg / 100, 1)), f'A4 {tag} 탄수 {r.get("carbs_g")}')
    check(near(r.get('fat_g'), min(round(fat * eg / 100, 1), eg)), f'A5 {tag} 지방 {r.get("fat_g")}')
    # 10-17 영양값 바로잡기 v1 이후: 나트륨·당은 DB 값(test_sqlite_nutrients_v1 이 값 검증) · 섬유는 DB 없음 → AI 값
    check(nm == '햇반참기름' or r.get('sodium_mg') != round(DB_OLD_NA[nm] * eg / 15, 1), f'A6 {tag} 나트륨 {r.get("sodium_mg")} — 예전 오값(탄수 열) 아님')
    check(r.get('fiber_g') == AI['fiber_g'], f'A7 {tag} 섬유 {r.get("fiber_g")} — DB 없음 → AI 값')

# 운영 사례 그대로: 치즈 소스 80g 은 2,357kcal 이 아님(v1 353.6 → 10-17 멕시코 이름 v1 CORE 130.4)
r = run('치즈 소스', 80)
check(r.get('calories_kcal', 0) < 400, f'B1 치즈 소스 80g {r.get("calories_kcal")}kcal (운영 2,357.3)')

# 부분·역방향 매칭은 쓰지 않는다 → AI 값 그대로
for nm in ['구운 야채', '베이컨과 계란 프라이', '치즈 소스 듬뿍', '허니버터']:
    r = run(nm, 100)
    check(r.get('db_matched') is False and r.get('source') == 'AI_ESTIMATED' and r.get('calories_kcal') == AI['calories_kcal'],
          f'C1 {nm} 부분 매칭 차단 실패 {r.get("db_name"), r.get("calories_kcal")}')

# 묶음 이름은 정확히 같아도 확정 안 함(규칙 132)
fa_multi = fa._is_multi_food_name(fa._normalize_food_name('나초와 치즈 소스'))
check(fa_multi, 'D0 «나초와 치즈 소스» 묶음 판정')

# 회귀: gold high 는 그대로(1순위)
r = run('나초', 100)
check(r.get('source') == 'GOLD_REF' and near(r.get('calories_kcal'), 350.0), f'E1 나초 {r.get("source"), r.get("calories_kcal")}')
r = run('신라면', 120)
check(r.get('source') in ('GOLD_REF', 'GOLD_DB') and r.get('db_name') == '신라면', f'E2 신라면 {r.get("source"), r.get("db_name")}')
# gold low + SQLite 정확 일치: 10-17 v2 부터 SQLite 값(test_sqlite_goldlow_v2) — v1 땐 AI 값이었음
r = run('새우깡블랙', 30)
check(r.get('source') == 'DB_MATCHED' and abs(r.get('calories_kcal', 0) - 148.2) <= 0.6, f'E3 새우깡블랙 {r.get("source"), r.get("calories_kcal")}')

print(f'test_sqlite_tier2_v1: {n - len(fails)}/{n} 통과')
for m in fails: print('  ✗', m)
sys.exit(1 if fails else 0)
