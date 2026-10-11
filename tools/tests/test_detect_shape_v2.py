"""_detect_shape v2 — 음료 키워드 부분일치 오분류 수정 (2026-10-11)
결함: '물'이 부분일치라 나물·해물·물만두·물회가 drink, '콜라'⊂콜라비, '녹차'⊂녹차빙수, '우유'⊂달걀찜(우유).
영향: drink 는 volume 과 같은 보정 경로라 나물·국·탕은 결과가 같다. 실제 손해 = «piece 인데 drink» 로
      GPT 양이 CORE 표준으로 덮이는 것(해물파전·물만두·물회·콜라비 등).
기대값은 수정 «전»에 고정. 실행: python tools/tests/test_detect_shape_v2.py
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from food_analyzer import _detect_shape

CASES = [
    # 진짜 음료 — drink 유지
    ('물', 'drink'), ('생수', 'drink'), ('얼음물', 'drink'), ('레몬물', 'drink'), ('탄산수', 'drink'),
    ('보리차', 'drink'), ('콜라', 'drink'), ('제로콜라', 'drink'), ('콜라겐음료', 'drink'), ('녹차', 'drink'),
    ('녹차라떼', 'drink'), ('우유', 'drink'), ('두유(검은콩)', 'drink'), ('오렌지주스', 'drink'), ('아메리카노', 'drink'),
    ('막걸리', 'drink'), ('식혜', 'drink'), ('애플사이다비니거', 'drink'),
    # 나물·국물·해물 — volume (원래 의도)
    ('시금치나물', 'volume'), ('콩나물무침', 'volume'), ('숙주나물', 'volume'), ('취나물', 'volume'), ('나물비빔밥', 'volume'),
    ('콩나물국', 'volume'), ('해물탕', 'volume'), ('해물칼국수', 'volume'), ('잔치국수국물', 'volume'),
    ('물냉면', 'volume'), ('곤드레나물밥', 'volume'), ('돌나물물김치', 'volume'), ('해물볶음', 'volume'),
    # 조각·형태 있는 음식 — piece (손해 나던 쪽)
    ('해물파전', 'piece'), ('해물완자전', 'piece'), ('해물찜', 'piece'), ('물만두', 'piece'), ('물회', 'piece'),
    ('오징어물회', 'piece'), ('콜라비', 'piece'), ('녹차빙수', 'piece'), ('달걀찜(우유)', 'piece'), ('식물성프로틴', 'piece'),
    # 회귀 — 기존 분류 유지
    ('사과', 'piece'), ('김치찌개', 'volume'), ('된장국', 'volume'), ('제육볶음', 'volume'), ('김밥', 'piece'),
]

fails = [(n, e, _detect_shape(n)) for n, e in CASES if _detect_shape(n) != e]
for n, e, a in fails:
    print(f'FAIL {n}: 기대 {e} · 실제 {a}')
print(f'{len(CASES) - len(fails)}/{len(CASES)}')
sys.exit(1 if fails else 0)
