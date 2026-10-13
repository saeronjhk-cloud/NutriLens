"""국밥 밥 이중계산 방지 v1 (2026-10-13)
결함: 돼지국밥·굴국밥·국밥·순댓국·콩나물국밥 등 CORE 값은 «밥 포함»(탄수 55~135g)인데, GPT 는 국밥과 밥을 따로 잡는다
      (운영 meal_log: «순대국 + 흰밥(315)», «설렁탕 + 밥(315)»). → 밥 315kcal 가 두 번 들어간다.
수정: 같은 끼니에 밥(_F30_RICE_ITEMS)이 따로 있으면, 밥 포함 국밥은 «국만» 값 = 전체 − 흰밥 210g (1인분도 −210g).
      밥이 따로 없으면 지금 값 그대로(밥이 그릇 안에 있는 사진). 대상 = overrides json gukbap_rice.items(데이터로 판정).
기대값은 수정 «전»에 고정. 실행: python tools/tests/test_gukbap_rice_v1.py
"""
import os, sys, io, contextlib
sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')))
with contextlib.redirect_stdout(io.StringIO()):
    import food_analyzer as fa

def meal(*items):
    foods = [{'name_ko': n, 'estimated_serving_g': g} for n, g in items]
    with contextlib.redirect_stdout(io.StringIO()):
        fa.match_with_db({'foods': foods}, None)
    return foods

CASES = [  # (끼니 구성, 확인할 항목 index, 기대 kcal, 기대 rice_separated)
    ([('돼지국밥', None), ('흰밥', None)], 0, 260, True),
    ([('돼지국밥', None), ('흰밥', None)], 1, 315, None),      # 밥은 그대로
    ([('돼지국밥', None)], 0, 575, None),                      # 밥 따로 없음 → 그대로
    ([('돼지국밥', 640), ('쌀밥', 210)], 0, 260, True),
    ([('돼지국밥', 800), ('흰밥', 210)], 0, 325, True),       # 800/640=1.25 범위 안 → GPT 그대로 × 국만 100g당
    ([('국밥', None), ('잡곡밥', None)], 0, 103, True),
    ([('굴국밥', None), ('공기밥', None)], 0, 368, True),
    ([('콩나물국밥', None)], 0, 403, None),
    ([('순댓국', None), ('밥', None)], 0, 194, True),
    ([('순대국', None), ('흰밥', None)], 0, 300, None),       # 밥 미포함 값 → 대상 아님
    ([('소고기국밥', None), ('흰밥', None)], 0, 332, None),   # 데이터 불일치 → 대상 아님
    ([('설렁탕', None), ('밥', None)], 0, 275, None),
]
fails = []
for items, i, kcal, flag in CASES:
    f = meal(*items)[i]
    if abs((f.get('calories_kcal') or 0) - kcal) > 2:
        fails.append(f'{items} [{i}]: 기대 {kcal}kcal · 실제 {f.get("calories_kcal")}')
    if f.get('rice_separated') != flag:
        fails.append(f'{items} [{i}]: rice_separated 기대 {flag} · 실제 {f.get("rice_separated")}')
for x in fails: print('FAIL', x)
print(f'{len(CASES) - len({x.split(":")[0] for x in fails})}/{len(CASES)}')
sys.exit(1 if fails else 0)
