"""1인분 이름 규칙 구조 v1 (2026-10-18, 제이 «진행») — 규칙 164 해결 + 삼각김밥·주먹밥
① overrides(add·serving·label)로 1인분을 명시한 CORE 키는 100g 이어도 이름 규칙(«밥» 끝 400 · «구이» 200 …)이 덮어쓰지 않는다
② 별칭은 이름 규칙을 «표준 키 이름»으로 판단(약밥→약식 100g 이 «밥» 400g 으로 가던 것) · 별칭 약밥→약식 추가
③ 삼각김밥 110g · 주먹밥 150g(엔진 CORE 대표 «삼각김밥» 110 · «주먹밥» 150 과 일치) — 전에는 «밥» 끝 400g · «김밥» 230g 으로 가 갈비삼각김밥 732.8kcal
기대값은 수정 «전»에 고정(.tmp/agentA2/evalset.json + 10-18 측정 · 깻잎김치 kcal 은 10-18 자문 합의로 83→62). 허용 1kcal. 실행: python tools/tests/test_serving_fixed_v1.py
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
def res(nm):
    with contextlib.redirect_stdout(io.StringIO()):
        return fl.resolve_food(nm, None)
AI = dict(calories_kcal=300.0, protein_g=10.0, carbs_g=20.0, fat_g=15.0, fiber_g=1.5, sodium_mg=600.0, sugar_g=4.0)
def photo_noserv(nm):   # 사진인데 GPT 양이 없을 때(match_with_db 의 이름 규칙 경로)
    f = dict(AI, name_ko=nm); 
    with contextlib.redirect_stdout(io.StringIO()):
        return fa.match_with_db({'foods': [f]}, None)['foods'][0]
def ok(nm, g, kcal, tag):
    r = res(nm)
    check(r is not None and r.get('estimated_serving_g') == g and abs((r.get('calories_kcal') or 0) - kcal) <= 1,
          f"{tag} {nm} → {r and (r.get('db_name'), r.get('estimated_serving_g'), r.get('calories_kcal'))} (기대 {g}g {kcal})")
FIX = [('가지 구이', 100, 35.0), ('가지구이', 100, 35.0), ('약밥', 100, 232.2), ('약 밥', 100, 232.2), ('약식', 100, 232.2), ('닭똥집볶음', 100, 176.0), ('프레첼', 100, 312.0), ('호박 퓨레', 100, 52.0), ('호박 퓌레', 100, 52.0), ('상추겉절이', 100, 65.3), ('상추무침', 100, 65.3), ('깻잎김치', 100, 62.0), ('소시지볶음', 100, 279.5), ('소세지볶음', 100, 279.5), ('굴무침', 100, 76.2), ('찐만두', 100, 200), ('애플 파이', 100, 184.0)]
REG = [('홍백해장국', 500, 405.0), ('조개국', 500, 80.0), ('대구탕', 500, 225.0), ('어묵탕', 500, 105.0), ('참치찌개', 400, 168.0), ('도루묵찌개', 400, 252.0), ('만두전골', 400, 228.0), ('개고기전골', 400, 296.0), ('한치볶음덮밥', 400, 612.0), ('송이버섯덮밥', 400, 388.0), ('양념장비빔밥', 400, 344.0), ('참치비빔밥', 400, 488.0), ('소고기볶음밥', 400, 476.0), ('맛살볶음밥', 400, 536.0), ('오리죽', 300, 465.0), ('율무죽', 300, 231.0), ('도미찜', 250, 315.8), ('고등어구이', 200, 380.0), ('부추전', 200, 370.0), ('김치전', 200, 360.0)]
TRI = [('갈비삼각김밥', 110, 201.5), ('고추장불고기 삼각김밥', 110, 181.8), ('숯불갈비 삼각김밥', 110, 177.4), ('참치마요네즈 삼각김밥', 110, 189.6), ('삼각김밥 참치마요네즈', 110, 189.2), ('삼각김밥 고추장불고기', 110, 181.5), ('삼각김밥 숯불갈비', 110, 177.1), ('참치마요네즈삼각김밥', 110, 189.6), ('편의점주먹밥', 150, 247.5), ('콘치즈닭갈비주먹밥', 150, 330.0), ('소고기주먹밥', 150, 266.2), ('배추김치주먹밥', 150, 159.0), ('참치주먹밥', 150, 196.5), ('주먹밥 소고기', 150, 267.0), ('주먹밥 참치', 150, 196.5)]
KEEP = [('삼각김밥', 110, 187.0), ('참치마요삼각김밥', 115, 212.7), ('불고기삼각김밥', 115, 207.0), ('닭가슴살주먹밥', 120, 174.0), ('현미주먹밥', 120, 192.0), ('주먹밥', 150, 209.5), ('멸치주먹밥', 150, 312.0), ('쇠고기주먹밥', 200, 355.0), ('편의점 삼각김밥', 110, 180.0), ('김밥', 230, 460.0), ('참치김밥', 250, 387.5)]
for nm, g, k in FIX: ok(nm, g, k, 'A 명시 1인분')
for nm, g, k in TRI: ok(nm, g, k, 'B 삼각김밥·주먹밥')
for nm, g, k in KEEP:
    ok(nm, g, k, 'K 그대로')
for nm, g, k in REG: ok(nm, g, k, 'R 이름 규칙 그대로')
# 사진 경로(양 없음)도 같은 1인분
for nm, g in [('가지 구이', 100), ('약밥', 100), ('갈비삼각김밥', 110), ('소고기주먹밥', 150), ('홍백해장국', 500), ('도미찜', 250)]:
    p = photo_noserv(nm); check(p.get('portion_base_g') == g, f"P 사진(양 없음) {nm} {p.get('portion_base_g')} (기대 {g})")
print(f'test_serving_fixed_v1: {n - len(fails)}/{n} 통과')
for m in fails: print('  ✗', m)
sys.exit(1 if fails else 0)
