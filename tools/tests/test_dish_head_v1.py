"""복합 이름 요리명 우선 v1 (규칙 163 일반화, 2026-10-19) — «A(요리) B(재료)» → A
결함: 정확한 «A B» 키가 없으면 뒤쪽 단어 매칭이 재료(B)로 «확신» 매칭 — «김밥 스팸»→스팸 50g 130kcal ·
      «닭볶음탕 감자»→감자. 또는 앞 요리명을 찾고도 확신 낮음 → AI 값(«계란말이 치즈»·«라면 계란»).
기대값 = 요리명(A)만 넣었을 때의 엔진 결과(이름·1인분·kcal) — 숫자 고정 없음(값이 바뀌어도 규칙 검사는 유지).
A 바뀜 16 · B 정확 키 그대로 9 · C 지킬 것(두 음식·양념·음료·맨밥·수식어·완성 메뉴·요리 일부) 20 · D 확신 함수 3
평가 셋 원본: IP/통합앱_P1/eval/dish_head_eval_v1.json · 설계: IP/integration/dish_head_design_v1.md
실행: python tools/tests/test_dish_head_v1.py
"""
import os, sys, io, json, contextlib
T = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, T)
with contextlib.redirect_stdout(io.StringIO()):
    import food_analyzer as fa
    import food_lookup as fl
    fa.load_food_db()
E = json.load(open(os.path.join(T, 'dish_head_eval_v1.json'), encoding='utf-8'))
fails, n = [], 0
def check(ok, msg):
    global n
    n += 1
    if not ok: fails.append(msg)
AI = dict(calories_kcal=300.0, protein_g=10.0, carbs_g=20.0, fat_g=15.0, fiber_g=1.5, sodium_mg=600.0, sugar_g=4.0)
def photo(nm):
    with contextlib.redirect_stdout(io.StringIO()):
        return fa.match_with_db({'foods': [dict(AI, name_ko=nm, estimated_serving_g=None)]}, None)['foods'][0]
def res(nm):
    with contextlib.redirect_stdout(io.StringIO()):
        return fl.resolve_food(nm, None)
sig = lambda r: r and (r.get('db_name'), r.get('estimated_serving_g'), r.get('calories_kcal'), r.get('sodium_mg'))
# A 바뀜: «A B» → 요리명 A 와 같은 결과(기록·사진 모두)
for x in E['A_change']:
    nm, head = x['input'], x['head']
    r, h = res(nm), res(head)
    check(h is not None and sig(r) == sig(h), f"A1 {nm} → {sig(r)} (기대 {head} {sig(h)})")
    p = photo(nm)
    check(p.get('source') in ('GOLD_REF', 'GOLD_DB') and p.get('db_name') == (h or {}).get('db_name'), f"A2 {nm} 사진 → {p.get('source')} {p.get('db_name')} (기대 {head})")
# B 정확 키(gold «요리 재료» · CORE · 별칭)는 그대로
for x in E['B_exact']:
    r = res(x['input'])
    check(r is not None and r.get('db_name') == x['db_name'], f"B {x['input']} → {sig(r)} (기대 {x['db_name']})")
# C 지킬 것: 지금 결과 그대로(기준선 기록값)
for x in E['C_keep']:
    r = res(x['input'])
    got = r and r.get('db_name')
    check(got == x['db_name'], f"C {x['input']} → {got} (그대로 {x['db_name']} — {x['why']})")
# D 확신 함수
for x in E['D_conf']:
    c = fa._match_confidence(fa._normalize_food_name(x['input']), x['key'])
    check(c == x['conf'], f"D {x['input']}→{x['key']} 확신 {c} (기대 {x['conf']})")
print(f"test_dish_head_v1: {n - len(fails)}/{n}" + (" 통과" if not fails else ""))
for f in fails: print('  FAIL', f)
sys.exit(1 if fails else 0)
