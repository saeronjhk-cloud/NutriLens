"""반찬 혼동표 v1.1 — H3(이름 불변)·H5(food30 회귀 없음)·끄기 스위치. DB 없이 돈다(가짜 DB).
실행: python tools/tests/test_banchan_alternates.py
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import food_analyzer as fa


fails = 0
def check(name, cond):
    global fails
    print(('PASS ' if cond else 'FAIL ') + name)
    if not cond: fails += 1

# 가짜 match_with_db: 이름별 고정 칼로리 (DB 없이 배선만 본다)
KCAL = {'감자조림': 54.0, '숙주나물': 12.0, '곰탕': 275.0, '설렁탕': 300.0, '취나물': 18.0}
def fake_match(analysis, db):
    for f in analysis['foods']:
        if f['name_ko'] in KCAL:
            f['calories_kcal'] = KCAL[f['name_ko']]; f['source'] = 'GOLD_REF'
    return analysis
fa.match_with_db = fake_match

def attach(name):
    a = {'foods': [{'name_ko': name, 'calories_kcal': 1.0}]}
    return fa.attach_food30_alternates(a, [])['foods'][0]

os.environ.pop('BANCHAN_ALT', None)
check('B0 기본 켜짐(BANCHAN_ALT 미설정) — 2026-10-09 채택', 'alternates' in attach('연근조림'))
os.environ['BANCHAN_ALT'] = '1'
f = attach('연근조림')
check('B1 연근조림 → 후보 감자조림', [x['name_ko'] for x in f.get('alternates', [])] == ['감자조림'])
check('B2 후보에 영양 동봉', f['alternates'][0].get('calories_kcal') == 54.0)
check('B3 이유 = photo_confusable', f.get('alternates_reason') == 'photo_confusable')
check('H3 이름·칼로리 불변', f['name_ko'] == '연근조림' and f['calories_kcal'] == 1.0)
check('B4 공백·괄호 정규화 (콩나물무침 (70g))', [x['name_ko'] for x in attach('콩나물무침 (70g)').get('alternates', [])] == ['숙주나물'])
check('B5 방향성: 감자조림 → 후보 없음', 'alternates' not in attach('감자조림'))
check('B6 v1.1 탈락쌍: 동태전·무생채·고사리나물 → 후보 없음', all('alternates' not in attach(n) for n in ('동태전', '무생채', '고사리나물')))
check('B7 표 5쌍', len(fa._banchan_confusion()) == 5)
g = attach('설렁탕')
check('H5 food30 설렁탕 → 곰탕 · indistinguishable_pair', [x['name_ko'] for x in g['alternates']] == ['곰탕'] and g['alternates_reason'] == 'indistinguishable_pair')
os.environ['BANCHAN_ALT'] = '0'
check('B8 BANCHAN_ALT=0 이면 꺼짐', 'alternates' not in attach('연근조림'))
check('B9 끔 상태에도 food30 유지', 'alternates' in attach('설렁탕'))
del os.environ['BANCHAN_ALT']
print(f"\n{12 - fails}/12")
sys.exit(1 if fails else 0)
