"""가공식품 SQLite 영양값 바로잡기 v1 (2026-10-17) — 제이 결정 «진행»(나트륨 값 바로잡기)
원인(확정): 예전 가져오기(tools/mfds_importer.py, data.go.kr FoodNtrCpntDbInfo02)가 AMT_NUM 순서를 잘못 앎.
  실제 AMT_NUM: 1 kcal · 2 수분 · 3 단백 · 4 지방 · 5 회분 · 6 탄수 · 7 당류 · 8 식이섬유 · 13 나트륨
  예전 매핑: 2→탄수(실제 수분) · 5→당류(실제 회분) · 6→나트륨(실제 탄수) → sodium_mg 열 = 탄수화물(원본 CSV 대조 42,386/42,433)
  carbs_g 는 뒤에 4·4·9 역산으로 메워짐. 당·섬유 0.
기대값 = 식약처 API(2026-10-17 전량 341,645행 받음, .tmp/na_fix_v1/pages) — 수정 «전»에 고정
실행: python tools/tests/test_sqlite_nutrients_v1.py
"""
import os, sys, io, contextlib, sqlite3
T = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, T)
with contextlib.redirect_stdout(io.StringIO()):
    import food_analyzer as fa
    fa.load_food_db()
DB = os.path.join(os.path.dirname(T), 'nutrilens_db.sqlite')
fails, n = [], 0
def check(ok, msg):
    global n
    n += 1
    if not ok: fails.append(msg)
def near(a, b, tol=0.6): return a is not None and b is not None and abs(a - b) <= tol

# 100g(100mL)당 정답: (kcal, 단백, 지방, 탄수, 당류, 식이섬유 or None, 나트륨)
TRUTH = {
    '신라면': (417, 8.33, 13.33, 65.83, 3.33, None, 1492), '코카콜라': (43, 0, 0, 10.67, 10.67, None, 3),
    '허니버터칩': (542, 5.0, 31.67, 58.34, 5.0, None, 383), '치즈 소스': (442, 3.6, 39.5, 18.1, 11.7, None, 1759),
    '살사 소스': (32, 2.09, 0.05, 5.87, 4.88, None, 552), '새우깡블랙': (494, 8.75, 22.5, 63.75, 5.75, None, 713),
    '신라면블랙': (429, 8.96, 13.43, 67.91, 5.97, None, 1328), '햇반참기름': (900, 0, 100, 0, 0, None, 0),
    '진라면 매운맛 업소용': (417, 10.0, 13.33, 64.17, 3.83, None, 1467), '고추장새우깡': (493, 6.67, 22.67, 65.33, 10.67, None, 960),
    '바나나맛우유': (90, 2.63, 3.02, 13.13, 9.18, 0.0, 34), '신라면건면': (361, 9.28, 4.74, 70.10, 4.12, None, 1845),
    '카라멜팝콘': (470, 2.0, 16.0, 79.0, 55.0, None, 320), '코카콜라제로': (0, 0, 0, 0, 0, None, 6),
    '새우깡 그린': (483, 7.22, 21.11, 66.67, 6.67, None, 667), '서울우유 크림도넛': (333, 6.0, 21.0, 30.0, 11.0, None, 280),
}
c = sqlite3.connect(DB)
for nm, (k, p, f, ca, su, fi, na) in TRUTH.items():
    r = c.execute('select calories_kcal,protein_g,fat_g,carbs_g,sugar_g,fiber_g,sodium_mg from foods where name_ko=?', (nm,)).fetchone()
    check(r is not None, f'D0 {nm} 행 없음')
    if not r: continue
    check(near(r[0], k) and near(r[1], p) and near(r[2], f), f'D1 {nm} kcal·단백·지방 {r[:3]} ≠ {(k, p, f)}')
    check(near(r[3], ca), f'D2 {nm} 탄수 {r[3]} ≠ {ca}')
    check(near(r[4], su), f'D3 {nm} 당류 {r[4]} ≠ {su}')
    check((r[5] is None) if fi is None else near(r[5], fi), f'D4 {nm} 식이섬유 {r[5]} ≠ {fi}(없으면 NULL)')
    check(near(r[6], na, 1), f'D5 {nm} 나트륨 {r[6]} ≠ {na}')

# 전체 불변식
bad = c.execute("select count(*) from foods where food_id like 'MFDS_%' and carbs_g>1 and abs(sodium_mg-carbs_g)<0.11").fetchone()[0]
tot = c.execute("select count(*) from foods where food_id like 'MFDS_%'").fetchone()[0]
check(bad / tot < 0.01, f'I1 나트륨=탄수 행 {bad}/{tot} (1% 미만이어야)')
nz = c.execute("select count(*) from foods where food_id like 'MFDS_%' and sugar_g>0").fetchone()[0]
check(nz / tot > 0.3, f'I2 당류>0 행 {nz}/{tot} (지금은 0 — 채워져야)')

# 엔진(2순위): 나트륨·당은 DB 값, 식이섬유 NULL 이면 AI 값
AI = dict(calories_kcal=300.0, protein_g=10.0, carbs_g=20.0, fat_g=15.0, fiber_g=1.5, sodium_mg=600.0, sugar_g=4.0)
def run(name, g):
    f = dict(AI, name_ko=name, estimated_serving_g=g)
    with contextlib.redirect_stdout(io.StringIO()):
        return fa.match_with_db({'foods': [f]}, None)['foods'][0]
for nm, g in [('치즈 소스', 80), ('살사 소스', 50), ('코카콜라', 250), ('허니버터칩', 60), ('코카콜라제로', 500)]:
    k, p, f, ca, su, fi, na = TRUTH[nm]
    r = run(nm, g)
    check(r.get('source') == 'DB_MATCHED', f'E0 {nm} source {r.get("source")}')
    check(near(r.get('sodium_mg'), round(na * g / 100, 1), 1), f'E1 {nm} {g}g 나트륨 {r.get("sodium_mg")} ≠ {round(na*g/100,1)}')
    check(near(r.get('sugar_g'), round(su * g / 100, 1), 0.2), f'E2 {nm} {g}g 당 {r.get("sugar_g")} ≠ {round(su*g/100,1)}')
    check(r.get('fiber_g') == AI['fiber_g'], f'E3 {nm} 섬유 {r.get("fiber_g")} — DB 없음 → AI 값')
    check(near(r.get('carbs_g'), round(ca * g / 100, 1), 0.2), f'E4 {nm} 탄수 {r.get("carbs_g")}')
# 운영 사례: 치즈 소스 80g 나트륨 1,407mg (AI 400 이 아니라)
check(near(run('치즈 소스', 80).get('sodium_mg'), 1407.2, 1), 'E5 치즈 소스 80g 나트륨 1,407')

print(f'test_sqlite_nutrients_v1: {n - len(fails)}/{n} 통과')
for m in fails: print('  ✗', m)
sys.exit(1 if fails else 0)
