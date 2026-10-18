"""흔한 이름·근거 정리 v1 (2026-10-18, 제이 결정 «추천대로») — 10-17 병행 조사 A·B·C 반영
① gold 검색키(띄어쓰기·기호 무시) 정확도 양방향 — 공백 없는 입력이 공백·괄호 있는 gold 키를 놓침(2,872개). 규칙 157 후속
② 새우깡 → 국내 제품 라벨(SQLite MFDS_P101-106000100-0968 517kcal·Na 678/100g, 1회 30g). 섬유 라벨 미표시 → FNDDS 값 유지
③ «계란말이 소시지»·«소시지 계란말이» → 계란말이(이름 순서에 따라 소시지/계란말이로 갈리던 것)
④ 1인분 = FNDDS Quantity not specified(규칙 156): 굴라쉬 300→255 · 나시 레막 200→150 · 감자 경단 150→188 · 슈페츨레 200→160
⑤ 범주 이름 «소스»·«채소»는 SQLite 단일 제품(0kcal·506kcal 과자)으로 확정 안 함
⑥ 별칭(기존 값): 라떼·크림·모짜렐라·명란·찐만두·계란국·묵은지·맛김치·단팥죽 (약밥 보류 — 이름 규칙 1인분 구조)
⑦ 신규(USDA FNDDS 2024-10-31): 간장 2707442 · 버터 2710154 · 참기름 2710190 · 딸기잼 2710301 · 크림치즈 2705760
기대값은 수정 «전»에 고정(A 는 .tmp/agentA/evalset.json, 회귀는 .tmp/cn_v1/fixed.json). 실행: python tools/tests/test_common_names_v1.py
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
    with contextlib.redirect_stdout(io.StringIO()):
        return fa.match_with_db({'foods': [dict(AI, name_ko=nm, estimated_serving_g=g)]}, None)['foods'][0]
def res(nm):
    with contextlib.redirect_stdout(io.StringIO()):
        return fl.resolve_food(nm, None)
def sg(nm):
    with contextlib.redirect_stdout(io.StringIO()):
        return fa._search_gold(nm)
near = lambda a, b, tol=0.15: a is not None and b is not None and abs(a - b) <= tol
sp = lambda s: (s or '').replace(' ', '')
A_FIX = [{"input": "김밥1줄", "expected_key": "김밥 (1줄)", "expected_kcal": 164.0, "current_key": "김밥", "current_kcal": 200, "expected_confidence": "high"}, {"input": "김치배추", "expected_key": "김치 (배추)", "expected_kcal": 22.0, "current_key": "김치", "current_kcal": 17, "expected_confidence": "high"}, {"input": "국밥돼지", "expected_key": "국밥 (돼지)", "expected_kcal": 96.0, "current_key": "국밥", "current_kcal": 52.3, "expected_confidence": "high"}, {"input": "고구마찐것", "expected_key": "고구마 찐 것", "expected_kcal": 93.3, "current_key": "고구마", "current_kcal": 86, "expected_confidence": "high"}, {"input": "단호박찐것", "expected_key": "단호박 찐 것", "expected_kcal": 60.0, "current_key": "단호박", "current_kcal": 40, "expected_confidence": "high"}, {"input": "견과류믹스", "expected_key": "견과류 믹스", "expected_kcal": 583.3, "current_key": "견과류", "current_kcal": 607, "expected_confidence": "high"}, {"input": "그릭요거트무지방", "expected_key": "그릭 요거트 (무지방)", "expected_kcal": 60.0, "current_key": "그릭요거트", "current_kcal": 60, "expected_confidence": "high"}, {"input": "그릭요거트일반", "expected_key": "그릭 요거트 (일반)", "expected_kcal": 93.3, "current_key": "그릭요거트", "current_kcal": 60, "expected_confidence": "high"}, {"input": "그린티라떼톨", "expected_key": "그린티 라떼 (톨)", "expected_kcal": 64.8, "current_key": "그린티라떼", "current_kcal": 76.0, "expected_confidence": "high"}, {"input": "감자튀김미디엄", "expected_key": "감자튀김 (미디엄)", "expected_kcal": 253.3, "current_key": "감자튀김", "current_kcal": 312, "expected_confidence": "high"}, {"input": "규동소고기덮밥", "expected_key": "규동 (소고기덮밥)", "expected_kcal": 137.5, "current_key": "덮밥", "current_kcal": 130, "expected_confidence": "high"}, {"input": "곤약면비빔면", "expected_key": "곤약면 비빔면", "expected_kcal": 30.0, "current_key": "곤약면", "current_kcal": 10, "expected_confidence": "high"}, {"input": "로제파스타", "expected_key": "로제 파스타", "expected_kcal": 177.1, "current_key": "파스타", "current_kcal": 160, "expected_confidence": "high"}, {"input": "딸기스무디", "expected_key": "딸기 스무디", "expected_kcal": 62.5, "current_key": "스무디", "current_kcal": 60, "expected_confidence": "high"}, {"input": "두부반모", "expected_key": "두부 (반모)", "expected_kcal": 80.0, "current_key": "두부", "current_kcal": 79, "expected_confidence": "high"}, {"input": "식빵1장", "expected_key": "식빵 (1장)", "expected_kcal": 250.0, "current_key": "식빵", "current_kcal": 265, "expected_confidence": "high"}, {"input": "호두5알", "expected_key": "호두 (5알)", "expected_kcal": 650.0, "current_key": "호두", "current_kcal": 654, "expected_confidence": "high"}, {"input": "카페라떼톨", "expected_key": "카페라떼 (톨)", "expected_kcal": 42.3, "current_key": "카페라떼", "current_kcal": 38, "expected_confidence": "high"}, {"input": "라멘쇼유", "expected_key": "라멘 (쇼유)", "expected_kcal": 96.0, "current_key": "라멘", "current_kcal": 105, "expected_confidence": "high"}, {"input": "리조또버섯", "expected_key": "리조또 (버섯)", "expected_kcal": 120.0, "current_key": "리조또", "current_kcal": 140, "expected_confidence": "high"}, {"input": "볶음밥중식", "expected_key": "볶음밥 (중식)", "expected_kcal": 163.3, "current_key": "볶음밥", "current_kcal": 180, "expected_confidence": "high"}, {"input": "타코비프", "expected_key": "타코 (비프)", "expected_kcal": 186.7, "current_key": "타코", "current_kcal": 170, "expected_confidence": "high"}, {"input": "튀김모듬", "expected_key": "튀김 (모듬)", "expected_kcal": 225.0, "current_key": "튀김", "current_kcal": 220, "expected_confidence": "high"}, {"input": "소고기덮밥", "expected_key": "소고기 덮밥", "expected_kcal": 109.0, "current_key": "덮밥", "current_kcal": 130, "expected_confidence": "high"}, {"input": "달걀샐러드", "expected_key": "달걀 샐러드", "expected_kcal": 158.0, "current_key": "샐러드", "current_kcal": 25, "expected_confidence": "high"}, {"input": "코코넛워터파우더", "expected_key": "코코넛 워터 파우더", "expected_kcal": 375.0, "current_key": "코코넛워터", "current_kcal": 19, "expected_confidence": "high"}, {"input": "삼색오이냉국", "expected_key": "삼색 오이냉국", "expected_kcal": 17.0, "current_key": None, "current_kcal": None, "expected_confidence": "high"}, {"input": "스파게티뇨끼", "expected_key": "스파게티 뇨끼", "expected_kcal": 172.0, "current_key": "스파게티", "current_kcal": 155, "expected_confidence": "high"}, {"input": "김밥참치", "expected_key": "김밥 참치", "expected_kcal": 128.0, "current_key": "김밥", "current_kcal": 200, "expected_confidence": "high"}, {"input": "볶음밥김치", "expected_key": "볶음밥 김치", "expected_kcal": 167.0, "current_key": "볶음밥", "current_kcal": 180, "expected_confidence": "high"}]
A_KEEP = [{"input": "1955버거", "expected_key": "1955버거", "expected_kcal": 260, "why": "CORE 우선"}, {"input": "곤약떡볶이", "expected_key": "곤약떡볶이", "expected_kcal": 30, "why": "CORE 우선"}, {"input": "교촌허니콤보", "expected_key": "교촌허니콤보", "expected_kcal": 310, "why": "CORE 우선"}, {"input": "두부샐러드", "expected_key": "두부샐러드", "expected_kcal": 55, "why": "CORE 우선"}, {"input": "닭가슴살큐브", "expected_key": "닭가슴살큐브", "expected_kcal": 115, "why": "CORE 우선"}, {"input": "프리미엄백김치", "expected_key": "프리미엄백김치", "expected_kcal": 20.0, "why": "입력 자체가 gold 정확 키"}, {"input": "덮밥낙지", "expected_key": "덮밥낙지", "expected_kcal": 92.0, "why": "입력 자체가 gold 정확 키"}, {"input": "소고기무국", "expected_key": "소고기무국", "expected_kcal": 31.3, "why": "CORE 우선"}, {"input": "톡톡유기농레몬즙nfc100", "expected_key": "톡톡 유기농 레몬즙 NFC 100", "expected_kcal": 20.0, "why": "같은 검색키 첫 키 우선"}, {"input": "퓨어유기농레몬즙100", "expected_key": "퓨어 유기농 레몬즙 100", "expected_kcal": 30.0, "why": "같은 검색키 첫 키 우선"}, {"input": "치즈", "expected_key": "치즈돈까스", "expected_kcal": 260, "why": "짧은 일반어/실사용 이름 — 바뀌면 안 됨"}, {"input": "소스", "expected_key": None, "expected_kcal": None, "why": "짧은 일반어/실사용 이름 — 바뀌면 안 됨"}, {"input": "라면", "expected_key": "라면", "expected_kcal": 416.667, "why": "짧은 일반어/실사용 이름 — 바뀌면 안 됨"}, {"input": "김", "expected_key": "김", "expected_kcal": 470.0, "why": "짧은 일반어/실사용 이름 — 바뀌면 안 됨"}, {"input": "밥", "expected_key": "밥", "expected_kcal": 150, "why": "짧은 일반어/실사용 이름 — 바뀌면 안 됨"}, {"input": "감자 샐러드", "expected_key": "감자샐러드", "expected_kcal": 126.0, "why": "짧은 일반어/실사용 이름 — 바뀌면 안 됨"}, {"input": "시저 샐러드", "expected_key": "시저 샐러드", "expected_kcal": 80, "why": "짧은 일반어/실사용 이름 — 바뀌면 안 됨"}]
REG = {"콜라": ["콜라", 250, 97.5, 12.5], "커피": ["커피", 200, 4.0, 4.0], "아메리카노": ["아메리카노", 355, 17.8, 17.8], "사이다": ["사이다", 250, 100.0, 25.0], "우유": ["우유", 200, 130.0, 80.0], "생크림": ["생크림", 20, 69.2, 4.0], "칠리 소스": ["칠리 소스", 15, 27.0, 165.0], "김밥": ["김밥", 230, 460.0, 1035.0], "떡볶이": ["떡볶이", 300, 579.0, 1800.0], "배추김치": ["배추김치", 40, 6.8, 240.0], "계란말이": ["계란말이", 80, 128.0, 240.0], "소시지": ["소시지", 50, 140.0, 400.0], "어묵볶음": ["어묵볶음", 80, 80.0, 480.0], "치즈 소스": ["치즈 소스", 32, 52.2, 209.9], "나초": ["나초", 100, 350.0, 400.0], "간장게장": ["간장게장", 150, 120.0, 750.0], "버터크림빵": ["버터크림빵", 60, 230.0, 191.2], "크림 파스타": ["크림 파스타", 300, 510.0, 1140.0], "김치볶음밥": ["김치볶음밥", 300, 510.0, 1350.0], "만두국": ["만두국", 500, 310.0, 1690.0], "달걀말이": ["달걀말이", 100, 172.2, 323.3], "라면": ["라면", 120, 500.0, 1790.0], "감자 샐러드": ["감자샐러드", 150, 189.0, 354.0], "시저 샐러드": ["시저 샐러드", 200, 160.0, 600.0]}
REGP = {"치즈": ["DB_MATCHED", "치즈", 357.0, 290.0], "요거트": ["DB_MATCHED", "요거트", 138.0, 88.0], "햄": ["DB_MATCHED", "햄", 240.0, 520.0], "스낵": ["DB_MATCHED", "스낵", 390.0, 18.0], "나쵸": ["DB_MATCHED", "나쵸", 480.0, 470.0], "허니버터칩": ["DB_MATCHED", "허니버터칩", 542.0, 383.0], "신라면블랙": ["DB_MATCHED", "신라면블랙", 429.0, 1328.0]}
# ① gold 검색키 양방향
for x in A_FIX:
    k, d = sg(x['input'])
    check(k == x['expected_key'] and d is not None and near(d.get('cal'), x['expected_kcal'], .6), f"A1 {x['input']} → {k} (기대 {x['expected_key']}, 전 {x['current_key']})")
    check(fa._match_confidence(x['input'], k) == 'high' if k else False, f"A2 {x['input']} 확신 {k and fa._match_confidence(x['input'], k)}")
for x in A_KEEP:
    k, d = sg(x['input'])
    check(k == x['expected_key'], f"A3 {x['input']} → {k} (그대로 {x['expected_key']} — {x.get('why')})")
# ② 새우깡 30g
p = photo('새우깡', 30); check(near(p.get('calories_kcal'), 155.1) and near(p.get('sodium_mg'), 203.4, .6), f"B1 새우깡 30g {p.get('calories_kcal')}·{p.get('sodium_mg')} (기대 155.1·203.4, 전 127.8·171.3)")
r = res('새우깡'); check(r is not None and r.get('estimated_serving_g') == 30 and near(r.get('calories_kcal'), 155.1), f"B2 새우깡 기록 {r and (r.get('estimated_serving_g'), r.get('calories_kcal'))}")
check(near(p.get('sugar_g'), 1.7, .1), f"B3 새우깡 당 {p.get('sugar_g')} (라벨 5.56/100g, 전 21.4)")
# ③ 계란말이 소시지
for nm in ['계란말이 소시지', '소시지 계란말이']:
    r = res(nm); check(r is not None and r.get('estimated_serving_g') == 80 and near(r.get('calories_kcal'), 128.0), f"C {nm} → {r and (r.get('db_name'), r.get('estimated_serving_g'), r.get('calories_kcal'))} (기대 계란말이 80g 128)")
# ④ 1인분
for nm, g, cal in [('굴라쉬', 255, 107.0), ('나시 레막', 150, 267.0), ('감자 경단', 188, 135.0), ('슈페츨레', 160, 138.0)]:
    r = res(nm); check(r is not None and r.get('estimated_serving_g') == g and near(r.get('calories_kcal'), round(cal * g / 100, 1)), f"D {nm} → {r and (r.get('estimated_serving_g'), r.get('calories_kcal'))} (기대 {g}g {round(cal*g/100,1)})")
# ⑤ 범주 이름은 SQLite 단일 제품으로 확정 안 함
for nm in ['소스', '채소']:
    p = photo(nm, 100); check(p.get('source') == 'AI_ESTIMATED' and p.get('calories_kcal') == 300.0, f"E1 {nm} → {p.get('source')} {p.get('db_name')} {p.get('calories_kcal')}")
for nm, v in REGP.items():
    p = photo(nm, 100); check([p.get('source'), p.get('db_name'), p.get('calories_kcal'), p.get('sodium_mg')] == v, f"E2 {nm} 그대로여야 {v} → {[p.get('source'), p.get('db_name'), p.get('calories_kcal'), p.get('sodium_mg')]}")
# ⑥ 별칭 — 대상과 같은 값·1인분
ALIAS = {'라떼': '카페라떼', '크림': '생크림', '모짜렐라': '모짜렐라치즈', '명란': '명란젓', '찐만두': '만두', '계란국': '달걀국',
         '묵은지': '김치', '맛김치': '김치', '단팥죽': '팥죽'}
for nm, tgt in ALIAS.items():
    b = fa.CORE_FOODS.get(tgt); r = res(nm)
    check(b is not None and r is not None and sp(r.get('db_name')) == sp(nm) and r.get('estimated_serving_g') == b['serving']
          and near(r.get('calories_kcal'), round(b['cal'] * b['serving'] / 100, 1), .2), f"F {nm}(→{tgt}) → {r and (r.get('db_name'), r.get('estimated_serving_g'), r.get('calories_kcal'))}")
    p, q = photo(nm, 100), photo(tgt, 100)   # 사진은 대상 이름과 같은 결과(C3c 가중 포함)
    check(p.get('db_matched') and near(p.get('calories_kcal'), q.get('calories_kcal'), .01) and near(p.get('sodium_mg'), q.get('sodium_mg'), .01), f"F 사진 {nm} {p.get('source')} {p.get('calories_kcal')} (기대 {tgt} {q.get('calories_kcal')})")
# 약밥 보류(약식 1인분 100g → «밥» 이름 규칙 400g 덮어씀 = 928kcal). 그대로 미매칭
check(res('약밥') is None, 'F0 약밥 보류 — 미매칭 그대로')
# ⑦ 신규 USDA — 100g당 kcal·Na · 1인분
NEW = {'간장': (53.0, 5490.0, 16), '버터': (743.0, 524.0, 14), '참기름': (884.0, 0.0, 14), '딸기잼': (278.0, 32.0, 20), '크림치즈': (343.0, 368.0, 30)}
for nm, (cal, na, g) in NEW.items():
    r = res(nm); check(r is not None and sp(r.get('db_name')) == sp(nm) and r.get('estimated_serving_g') == g and near(r.get('calories_kcal'), round(cal * g / 100, 1)) and near(r.get('sodium_mg'), round(na * g / 100, 1), .6),
                       f"G {nm} → {r and (r.get('db_name'), r.get('estimated_serving_g'), r.get('calories_kcal'), r.get('sodium_mg'))} (기대 {g}g {round(cal*g/100,1)})")
    p = photo(nm, 100); check(p.get('db_matched') and near(p.get('calories_kcal'), cal, .6), f"G 사진 {nm} {p.get('source')} {p.get('calories_kcal')}")
# 회귀 — 기록 경로 그대로
for nm, v in REG.items():
    r = res(nm); got = None if r is None else [r.get('db_name'), r.get('estimated_serving_g'), r.get('calories_kcal'), r.get('sodium_mg')]
    check(got == v, f"R {nm} 그대로여야 {v} → {got}")
print(f'test_common_names_v1: {n - len(fails)}/{n} 통과')
for m in fails: print('  ✗', m)
sys.exit(1 if fails else 0)
