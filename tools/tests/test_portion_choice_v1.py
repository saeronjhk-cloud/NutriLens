"""분량 조절 v1 (2026-10-13) — 저장 전 음식별 «적게·보통·많게» (제이 결정: 저장 전 칩)
엔진 단일 소스: resolve_food(name, serving_g=기준 g, portion_choice=…) → 기준 g × portion.PORTION_FACTOR,
  사용자가 고른 양은 «믿는다»(C3c 가중 생략). 포장 기준은 봉지 수 환산(사용자 선택이면 최소 0.5봉).
  결과에 portion_base_g(기준 g, 반복 탭해도 누적되지 않게) · portion_choice.
match_with_db 는 모든 GOLD 결과에 portion_base_g(지금 숫자를 만든 g — 포장은 봉지×완성 g)를 남긴다.
rice_separated=True 로 부르면 «밥 따로» 끼니처럼 국밥은 국만 값.
기대값은 수정 «전»에 고정. 실행: python tools/tests/test_portion_choice_v1.py
"""
import os, sys, io, contextlib
sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')))
with contextlib.redirect_stdout(io.StringIO()):
    import food_analyzer as fa, food_lookup as fl
fails = []
def chk(cond, msg):
    if not cond: fails.append(msg)
def meal(*items):
    foods = [{'name_ko': n, 'estimated_serving_g': g} for n, g in items]
    with contextlib.redirect_stdout(io.StringIO()):
        fa.match_with_db({'foods': foods}, None)
    return foods
def res(*a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fl.resolve_food(*a, **k)

# ① 사진 분석 결과에 portion_base_g
f = meal(('갈비탕', 800))[0]                 # C3c: 800 → 0.7*800+0.3*750 = 785
chk(f.get('portion_base_g') == 785, f'갈비탕 800 base {f.get("portion_base_g")}')
f = meal(('사과', 150))[0]
chk(f.get('portion_base_g') == 150, f'사과 base {f.get("portion_base_g")}')
f = meal(('라면', 1200))[0]                  # 2봉 → 완성 g 기준 2×600
chk(f.get('portion_base_g') == 1200, f'라면 base {f.get("portion_base_g")}')
f = meal(('돼지국밥', None), ('흰밥', None))[0]   # 국만 640g
chk(f.get('portion_base_g') == 640, f'돼지국밥(밥 따로) base {f.get("portion_base_g")}')

# ② 분량 선택 — 기준 g × 배율, C3c 생략
r = res('갈비탕', 785, portion_choice='많게')      # 785×1.4 = 1099 (C3c 였다면 끌려감)
chk(r and r['estimated_serving_g'] == 1099 and abs(r['calories_kcal'] - 879.2) < 1, f'갈비탕 많게 {r and (r["estimated_serving_g"], r["calories_kcal"])}')
chk(r and r.get('portion_base_g') == 785 and r.get('portion_choice') == '많게', f'갈비탕 base/choice {r and (r.get("portion_base_g"), r.get("portion_choice"))}')
r = res('갈비탕', 785, portion_choice='적게')      # 471g → 376.8
chk(r and r['estimated_serving_g'] == 471 and abs(r['calories_kcal'] - 376.8) < 1, f'갈비탕 적게 {r and r["calories_kcal"]}')
r = res('갈비탕', 785, portion_choice='보통')
chk(r and r['estimated_serving_g'] == 785 and abs(r['calories_kcal'] - 628.0) < 1, f'갈비탕 보통 {r and r["calories_kcal"]}')
r = res('사과', 150, portion_choice='많게')        # 210g → 119.7
chk(r and abs(r['calories_kcal'] - 119.7) < 1, f'사과 많게 {r and r["calories_kcal"]}')
# 포장: 1봉(600) 적게 → 0.5봉 · 많게 → 1.5봉
r = res('라면', 600, portion_choice='적게')
chk(r and abs(r['calories_kcal'] - 250) < 1, f'라면 적게 {r and r["calories_kcal"]}')
r = res('라면', 600, portion_choice='많게')
chk(r and abs(r['calories_kcal'] - 750) < 1, f'라면 많게 {r and r["calories_kcal"]}')
# 밥 따로인 국밥은 국만 값으로
r = res('돼지국밥', 640, portion_choice='많게', rice_separated=True)   # 896g × 국만 100g당
chk(r and abs(r['calories_kcal'] - 364.0) < 2 and r.get('rice_separated') is True, f'돼지국밥 많게(밥 따로) {r and r["calories_kcal"]}')
# 잘못된 입력
chk(res('갈비탕', 785, portion_choice='엄청많이') is None, '알 수 없는 선택은 None')
chk(res('갈비탕', None, portion_choice='많게') is None, '기준 g 없이 선택은 None')
# 기존 호출(선택 없음)은 그대로
r = res('갈비탕', 800)
chk(r and r['estimated_serving_g'] == 800 and abs(r['calories_kcal'] - 628.0) < 1, f'기존 resolve 갈비탕 800 {r and (r["estimated_serving_g"], r["calories_kcal"])}')
for x in fails: print('FAIL', x)
N = 16
print(f'{N - len(fails)}/{N}')
sys.exit(1 if fails else 0)
