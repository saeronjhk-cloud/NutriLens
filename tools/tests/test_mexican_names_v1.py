"""멕시코 음식 이름 v1 (2026-10-17) — 운영 사진 이름(10-15 나초 끼니) 미매칭·오값 정리
근거(규칙 136 ② USDA FDC FNDDS 2024-10-31, 100g당 · 1인분 = FNDDS «Quantity not specified»):
  치즈 소스 2705808 163kcal·Na656·32g  (기존: SQLite 제품 «치즈 소스» 442kcal·지방39.5 — 마요형 소스, 나초 치즈와 다름)
  치즈 퀘소 2705785 «Cheese dip with chili pepper»(=SR 174068 salsa con queso) 143·796·60g
  살사 소스 2709736 «Salsa, NFS» 34·656·32g · 피코 데 가요 2709737 17·443·32g · 나초 칩 2708202 «Tortilla chips, plain» 472·328·30g
  별칭: 치즈 딥(FNDDS 2705786 Cheese dip = 2705808 같은 값)·나초 치즈·나초 치즈 소스→치즈 소스 / 퀘소·퀘소 딥·케소→치즈 퀘소 /
        살사→살사 소스 / 나쵸칩·토르티야 칩·또띠아 칩→나초 칩
+ 검색 결함: 띄어쓰기 무시 정확(규칙 130)이 «입력에 공백 있을 때»만 → 양방향(F 61).
기대값은 수정 «전»에 고정. 실행: python tools/tests/test_mexican_names_v1.py
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
AI = dict(calories_kcal=300.0, protein_g=10.0, carbs_g=20.0, fat_g=15.0, fiber_g=1.5, sodium_mg=600.0, sugar_g=4.0)
def photo(nm, g):
    f = dict(AI, name_ko=nm, estimated_serving_g=g)
    with contextlib.redirect_stdout(io.StringIO()):
        return fa.match_with_db({'foods': [f]}, None)['foods'][0]
def res(nm):
    with contextlib.redirect_stdout(io.StringIO()):
        return fl.resolve_food(nm, None)
near = lambda a, b, tol=0.15: a is not None and abs(a - b) <= tol
sp = lambda s: (s or '').replace(' ', '')
# 100g당 kcal · Na · 1인분 g
NEW = {'치즈 소스': (163.0, 656.0, 32), '치즈 퀘소': (143.0, 796.0, 60), '살사 소스': (34.0, 656.0, 32),
       '피코 데 가요': (17.0, 443.0, 32), '나초 칩': (472.0, 328.0, 30)}
ALIAS = {'치즈 딥': '치즈 소스', '나초 치즈': '치즈 소스', '나초 치즈 소스': '치즈 소스',
         '퀘소': '치즈 퀘소', '퀘소 딥': '치즈 퀘소', '케소': '치즈 퀘소', '살사': '살사 소스',
         '나쵸칩': '나초 칩', '토르티야 칩': '나초 칩', '또띠아 칩': '나초 칩'}
NOSPACE = {'치즈소스': '치즈 소스', '치즈퀘소': '치즈 퀘소', '살사소스': '살사 소스', '피코데가요': '피코 데 가요', '나초칩': '나초 칩'}
# A 신규 5: 사진(50g) GOLD 값 · 사진 없이 기록(1인분)
for nm, (cal, na, sv) in NEW.items():
    p = photo(nm, 50)
    check(p.get('db_matched') and sp(p.get('db_name')) == sp(nm), f'A 사진 {nm} → {p.get("source")} {p.get("db_name")}')
    check(near(p.get('calories_kcal'), cal * .5) and near(p.get('sodium_mg'), na * .5, .6), f'A 사진값 {nm} kcal {p.get("calories_kcal")} Na {p.get("sodium_mg")} (기대 {cal*.5} · {na*.5})')
    r = res(nm)
    check(r is not None and sp(r.get('db_name')) == sp(nm) and r.get('estimated_serving_g') == sv, f'A 기록 {nm} → {r and (r.get("db_name"), r.get("estimated_serving_g"))} (기대 {sv}g)')
    check(r is not None and near(r.get('calories_kcal'), cal * sv / 100), f'A 기록값 {nm} {r and r.get("calories_kcal")} (기대 {cal*sv/100})')
# B 별칭 10: 대상과 같은 100g당 값(사진 50g)
for nm, tgt in list(ALIAS.items()) + list(NOSPACE.items()):
    cal, na, sv = NEW[tgt]
    p = photo(nm, 50)
    check(p.get('db_matched') and near(p.get('calories_kcal'), cal * .5) and near(p.get('sodium_mg'), na * .5, .6),
          f'B {nm}(→{tgt}) → {p.get("source")} {p.get("db_name")} kcal {p.get("calories_kcal")} (기대 {cal*.5})')
    r = res(nm)
    check(r is not None and r.get('estimated_serving_g') == sv, f'B 기록 {nm} → {r and (r.get("db_name"), r.get("estimated_serving_g"))} (기대 {sv}g)')
# C 운영 사례(10-15 11:47 사진 «치즈 소스» 80g · «살사 소스» 50g)
p = photo('치즈 소스', 80); check(near(p.get('calories_kcal'), 130.4) and near(p.get('sodium_mg'), 524.8, .6), f'C 치즈 소스 80g {p.get("calories_kcal")}·{p.get("sodium_mg")} (기대 130.4·524.8, 전 353.6·1,407.2)')
p = photo('살사 소스', 50); check(near(p.get('calories_kcal'), 17.0) and near(p.get('sodium_mg'), 328.0, .6), f'C 살사 소스 50g {p.get("calories_kcal")}·{p.get("sodium_mg")} (기대 17.0·328.0)')
# D 회귀: 그대로여야
p = photo('나초', 50); check(p.get('source') == 'GOLD_REF' and near(p.get('calories_kcal'), 175.0), f'D 나초 {p.get("source")} {p.get("calories_kcal")}')
p = photo('나쵸', 50); check(p.get('source') == 'DB_MATCHED' and near(p.get('calories_kcal'), 240.0), f'D 나쵸(제품) {p.get("source")} {p.get("calories_kcal")}')
p = photo('과카몰리', 50); check(near(p.get('calories_kcal'), 77.5), f'D 과카몰리 {p.get("calories_kcal")}')
p = photo('타코', 50); check(near(p.get('calories_kcal'), 85.0), f'D 타코 {p.get("calories_kcal")}')
p = photo('허니버터칩', 60); check(p.get('source') == 'DB_MATCHED' and near(p.get('calories_kcal'), 325.2), f'D 허니버터칩 {p.get("source")} {p.get("calories_kcal")}')
p = photo('치즈', 30); check(not sp(p.get('db_name')) == '치즈소스', f'D «치즈» 단독이 치즈 소스로 가면 안 됨 → {p.get("db_name")}')
# E 묶음 이름은 확정하지 않음(규칙 132)
r = res('나초와 치즈 소스'); check(r is None, f'E 묶음 → {r and r.get("db_name")}')
# F ★ 띄어쓰기 무시 양방향(규칙 130 결함): 공백 없이 들어온 이름이 띄어 쓴 CORE 키로 — 수정 전 66개 중 위 NOSPACE 5개 뺀 61개(전부 다른 키·값으로 갔음)
F_PAIRS = dict(x.split(':') for x in "까르보나라파스타:까르보나라 파스타|크림파스타:크림 파스타|토마토파스타:토마토 파스타|토마토소스파스타:토마토 소스 파스타|봉골레파스타:봉골레 파스타|리코타치즈샐러드:리코타 치즈 샐러드|시저샐러드:시저 샐러드|닭가슴살샐러드:닭가슴살 샐러드|그릭샐러드:그릭 샐러드|코브샐러드:코브 샐러드|월도프샐러드:월도프 샐러드|닭가슴살소시지:닭가슴살 소시지|닭가슴살스테이크:닭가슴살 스테이크|닭가슴살도시락:닭가슴살 도시락|소고기안심:소고기 안심|소고기등심:소고기 등심|돼지안심:돼지 안심|돼지등심:돼지 등심|소고기채끝:소고기 채끝|소고기우둔살:소고기 우둔살|소고기사태:소고기 사태|소고기홍두깨살:소고기 홍두깨살|양고기등심:양고기 등심|돼지목살살코기:돼지 목살살코기|고추장불고기삼각김밥:고추장불고기 삼각김밥|숯불갈비삼각김밥:숯불갈비 삼각김밥|참치마요네즈삼각김밥:참치마요네즈 삼각김밥|건취나물:건 취나물|미트볼토마토스파게티:미트볼 토마토 스파게티|참치김치볶음:참치 김치볶음|초코소라빵:초코 소라빵|도토리묵무침:도토리묵 무침|쇠고기메추리알장조림:쇠고기 메추리알 장조림|쇠고기샤브샤브:쇠고기 샤브샤브|칠리소스:칠리 소스|할라피뇨피클:할라피뇨 피클|나시레막:나시 레막|웨이퍼롤:웨이퍼 롤|크리스피포크:크리스피 포크|감자퓌레:감자 퓌레|호박퓨레:호박 퓨레|튀긴마늘:튀긴 마늘|크랜베리소스:크랜베리 소스|감자경단:감자 경단|가지구이:가지 구이|애플파이:애플 파이|돼지고기육포:돼지고기 육포|그릴드치킨:그릴드 치킨|치킨스테이크:치킨 스테이크|치킨커틀릿:치킨 커틀릿|비엔나슈니첼:비엔나 슈니첼|소고기구이:소고기 구이|감자퓨레:감자 퓨레|호박퓌레:호박 퓌레|복숭아통조림:복숭아 통조림|치즈딥:치즈 딥|나초치즈:나초 치즈|나초치즈소스:나초 치즈 소스|퀘소딥:퀘소 딥|토르티야칩:토르티야 칩|또띠아칩:또띠아 칩".split('|'))
for nm, k in F_PAIRS.items():
    q = photo(k, 100)   # 띄어 쓴 이름으로 넣었을 때와 같은 결과여야(파스타 보정 등 뒤 단계 포함)
    p = photo(nm, 100)
    check(k in fa.CORE_FOODS and p.get('db_matched') and sp(p.get('db_name')) == sp(k)
          and near(p.get('calories_kcal'), q.get('calories_kcal'), .01) and near(p.get('sodium_mg'), q.get('sodium_mg'), .01),
          f'F 사진 {nm} → {p.get("db_name")} {p.get("calories_kcal")} (기대 {k} {q.get("calories_kcal")})')
    r = res(nm)
    check(r is not None and sp(r.get('db_name')) == sp(k), f'F 기록 {nm} → {r and r.get("db_name")} (기대 {k})')
print(f'{n - len(fails)}/{n}')
for f in fails: print('  FAIL', f)
sys.exit(1 if fails else 0)
