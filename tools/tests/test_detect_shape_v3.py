"""_detect_shape v3 — volume 키워드 «이름 중간» 부분일치 오분류 수정 (2026-10-12)
결함: '탕'⊂탕수육·무설탕(캔디·그래놀라·젤리), '면'⊂칠면조·라면땅·당면 → volume(국물·무정형)으로 판정되어
      C3b 양 가중(GPT70+CORE30)이 걸림. 이 음식들은 개수·조각 음식 → piece(GPT 그대로).
보류(이번에 바꾸지 않음, 근거 없음): 떡볶이·육개장·수제비·콘스프·찜류의 piece→volume —
      양 순서 평가에서 GPT 그대로(OFF 80.0%)가 C3b(77.1%)보다 높아 volume 전환의 이득을 보일 측정이 없음.
기대값은 수정 «전»에 고정. 실행: python tools/tests/test_detect_shape_v3.py
"""
import os, sys
sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')))
from food_analyzer import _detect_shape

CASES = [
    # 고칠 것 — piece
    ('탕수육', 'piece'), ('새우탕수육', 'piece'), ('무설탕캔디', 'piece'), ('무설탕그래놀라', 'piece'),
    ('무설탕젤리', 'piece'), ('라면땅', 'piece'), ('칠면조가슴살', 'piece'), ('칠면조햄', 'piece'), ('순대(당면)', 'piece'),
    # 회귀 — volume 유지
    ('탕평채', 'volume'), ('청국장', 'volume'), ('게국지', 'volume'), ('갈비탕', 'volume'), ('매운탕', 'volume'),
    ('설렁탕', 'volume'), ('칼국수', 'volume'), ('콩국수', 'volume'), ('라면', 'volume'), ('짜장면', 'volume'),
    ('냉면', 'volume'), ('잡채', 'volume'), ('호박죽', 'volume'), ('가죽나물무침', 'volume'), ('죽순쇠고기볶음', 'volume'),
    ('김치찌개백반', 'volume'), ('된장찌개', 'volume'), ('카레라이스', 'volume'),
    # 회귀 — piece 유지(보류 항목 포함)
    ('떡볶이', 'piece'), ('육개장', 'piece'), ('수제비', 'piece'), ('콘스프', 'piece'), ('해물찜', 'piece'),
    ('달걀찜(우유)', 'piece'), ('사과', 'piece'), ('김밥', 'piece'),
    # 회귀 — drink 유지
    ('콜라', 'drink'), ('녹차', 'drink'), ('물', 'drink'),
]
fails = [(n, e, _detect_shape(n)) for n, e in CASES if _detect_shape(n) != e]
for n, e, a in fails:
    print(f'FAIL {n}: 기대 {e} · 실제 {a}')
print(f'{len(CASES) - len(fails)}/{len(CASES)}')
sys.exit(1 if fails else 0)
