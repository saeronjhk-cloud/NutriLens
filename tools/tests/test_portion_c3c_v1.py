"""양 보정 v3 (C3c, 연속형) — 1.5배·0.7배 경계 «역전» 제거 (2026-10-12)
C3b: 범위 밖이면 GPT70% + 1인분(s)30% → GPT 1.49s=1.49s 인데 1.51s=1.357s (더 많은 사진이 더 적게).
C3c: 범위 밖이면 GPT70% + «범위 끝»30% (위 1.5s · 아래 0.7s) → g 에 대해 연속·단조.
채택 근거: 확인셋 q2(미사용 20종 359장) C3c 83.4% vs C3bP 80.4% (+3.0%p, 최악 −2.3%p, 검산 0) —
          판정 기준은 결과 보기 전 고정(영양공식 IP/integration/calorie_serving_diagnosis_v1.md §6).
기대값은 수정 «전»에 고정. 실행: python tools/tests/test_portion_c3c_v1.py
"""
import os, sys, io, contextlib
sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')))
with contextlib.redirect_stdout(io.StringIO()):
    import food_analyzer as fa

def g_of(name, g):
    f = {'name_ko': name, 'estimated_serving_g': g}
    with contextlib.redirect_stdout(io.StringIO()):
        fa.match_with_db({'foods': [f]}, None)
    cal100 = fa.CORE_FOODS[f['db_name']]['cal']
    return round(f['calories_kcal'] / cal100 * 100), f

S = 500   # 갈비탕 CORE 1인분
CASES = [  # (GPT g, 기대 유효 g)
    (740, 740),                              # 1.48s — 그대로
    (760, round(760 * 0.7 + S * 1.5 * 0.3)), # 1.52s → 757
    (800, round(800 * 0.7 + S * 1.5 * 0.3)), # 1.6s  → 785
    (1500, round(1500 * 0.7 + S * 1.5 * 0.3)),
    (360, 360),                              # 0.72s — 그대로
    (340, round(340 * 0.7 + S * 0.7 * 0.3)), # 0.68s → 343
    (300, round(300 * 0.7 + S * 0.7 * 0.3)), # 0.6s  → 315
    (100, round(100 * 0.7 + S * 0.7 * 0.3)),
]
fails = []
for gpt, exp in CASES:
    got, f = g_of('갈비탕', gpt)
    if abs(got - exp) > 1:
        fails.append(f'갈비탕 GPT {gpt}g: 기대 {exp}g · 실제 {got}g')
# 단조성: GPT g 가 늘면 유효 g 도 늘어야 함 (0.3s ~ 3s 구간 전수)
prev = -1
for gpt in range(150, 1501, 5):
    got, _ = g_of('갈비탕', gpt)
    if got < prev:
        fails.append(f'단조성 깨짐: GPT {gpt}g → {got}g < 이전 {prev}g'); break
    prev = got
# 대조: piece·포장 기준은 영향 없음
got, f = g_of('사과', 50)
if abs(f['calories_kcal'] - 28.5) > 1: fails.append(f'[대조] 사과 50g: {f["calories_kcal"]}kcal')
_, f = g_of('라면', 500)
if abs(f['calories_kcal'] - 500) > 1: fails.append(f'[대조] 라면 500g: {f["calories_kcal"]}kcal')   # 1봉 = 500kcal (10-13 라벨값 v1.1, 이전 450)
for x in fails: print('FAIL', x)
n = len(CASES) + 3
print(f'{n - len(fails)}/{n}')
sys.exit(1 if fails else 0)
