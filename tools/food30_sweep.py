#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""food30 엔진 오탐 스윕 — 로컬 추론만. OpenAI 미호출. 비용 $0.

세션46(2026-08-19) 신설.

왜 필요한가
───────────────────────────────────────────────────────────────────────────
IP/172 §1 이 「G4 3단계에서 142_김치찌개·148_순두부찌개가 닭볶음탕으로 바뀌지
않는지 보라」고 지시하는데, 그 두 장은 **기준선 32장 셋에 없습니다**
(세션43 에 추가된 59장 쪽). 그리고 G4 는 IP/165 §5 에 따라 32장으로만 판정해야
기준선 59.4% 와 비교가 됩니다.

즉 오탐 확인과 게이트 판정은 **애초에 같은 실행으로 할 수 없습니다.**

다행히 오탐 확인에는 GPT 가 필요 없습니다. 엔진이 무엇을 검출하는가는 순수
로컬 추론이고, IP/165 §3 「축2 오탐 게이트」도 그렇게 설계돼 있습니다($0 · API 미호출).
이 스크립트가 그 축2 를 91장 전체에 대해 되돌려 줍니다.

무엇을 보는가
───────────────────────────────────────────────────────────────────────────
1. 엔진이 τ=0.70 에서 무엇을 검출했는가 (사진별)
2. 파일명(=GT)으로 볼 때 밥/탕이 아닌 사진에서 검출됐는가  → 오탐 후보
3. 그 검출이 실제로 이름을 바꿀 수 있는가 → GT 이름이 허용목록을 통과하는가

⚠ 3번의 한계 (2026-08-19 독립감사 치명-2·3 반영)
───────────────────────────────────────────────────────────────────────────
초판은 「GT 이름이 허용목록을 통과하는가」만으로 위험을 갈랐습니다. 그런데
`_GT_*_LIKE ⊂ _F30_*_ITEMS` 라서 그 분기는 **구조적으로 항상 0건**이었습니다 —
엔진이 91장 전부에서 폭주해도 「오탐 0」이 나오는, 반증 불가능한 게이트였습니다.

그리고 「GT 가 허용목록 밖」은 **안전 보장이 아닙니다.** apply_food30_override 가
보는 것은 GT 가 아니라 **GPT 가 말한 이름**이기 때문입니다. 실측:

    순두부찌개 사진 + 엔진 soup='닭볶음탕' 일 때
      GPT '순두부찌개' → 침묵      GPT '육개장'  → 닭볶음탕으로 **교체**
      GPT '김치찌개'   → 침묵      GPT '해물탕'  → 닭볶음탕으로 **교체**

→ 지금은 **「밥/탕 아닌 사진에서 발화했는가」 자체**를 오탐으로 셉니다.
  GT 허용목록 통과 여부는 그 안의 하위 구분(즉시 교체 / GPT 응답에 달림)으로 둡니다.
→ 이 스윕은 **하한**입니다. 확정값은 4단계 accuracy_test 의 `applied` 로만 나옵니다.

실행
───────────────────────────────────────────────────────────────────────────
    python tools/food30_sweep.py            # 91장 전수
    python tools/food30_sweep.py --set baseline32
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

PROJECT_DIR = Path(__file__).parent.parent
TEST_DIR = PROJECT_DIR / ".tmp" / "test_images"
RESULT_DIR = PROJECT_DIR / ".tmp"


# 파일명(GT)이 밥/탕 계열인가 — 오탐 판정용 GT 라벨.
# ⚠ 이건 apply_food30_override 의 허용목록과 목적이 다릅니다.
#   여기는 「이 사진에 밥/탕이 찍혀 있는가」이고, 허용목록은 「GPT 가 말한 이름을
#   손대도 되는가」입니다. 섞지 마십시오.
_GT_SOUP_LIKE = {
    '갈비탕', '감자탕', '곰탕', '매운탕', '꼬리곰탕', '꽃게탕', '낙지탕', '내장탕',
    '닭곰탕', '닭볶음탕', '지리탕', '도가니탕', '삼계탕', '설렁탕', '알탕', '연포탕',
    '오리탕', '추어탕', '해물탕', '닭개장', '육개장', '뼈해장국',
}
_GT_RICE_LIKE = {
    '쌀밥', '잡곡밥', '기타잡곡밥', '콩밥', '보리밥', '돌솥밥', '현미밥', '흑미밥', '감자밥',
    # 비빔밥류는 food30 클래스가 아니지만 「밥이 찍힌 사진」이므로 밥류 검출이
    # 오탐이 아니라 혼동입니다. 허용목록에 없어 교체는 어차피 안 됩니다(감사 경-4).
    '비빔밥', '돌솥비빔밥',
}


def gt_from_stem(stem):
    """'142_김치찌개' → '김치찌개'"""
    if '_' in stem and stem.split('_')[0].isdigit():
        return stem.split('_', 1)[1]
    return stem


def main():
    import argparse
    ap = argparse.ArgumentParser(description="food30 오탐 스윕 (로컬 추론, $0)")
    ap.add_argument('--set', dest='photo_set', default='all',
                    choices=['all', 'baseline32'])
    args = ap.parse_args()

    from food_analyzer import (
        detect_food30, _get_food30_model, FOOD30_CONF_TAU,
        _f30_is_rice, _f30_is_soup,
    )

    if _get_food30_model() is None:
        print()
        print("=" * 66)
        print("  ★ 엔진이 로드되지 않았습니다. 스윕을 할 수 없습니다.")
        print("=" * 66)
        print("  위 [food30] 로그를 보십시오 — 모델 파일 없음 / 클래스 순서 불일치 /")
        print("  ultralytics 미설치 중 하나입니다.")
        return 1

    if not TEST_DIR.exists():
        print(f"  이미지 폴더 없음: {TEST_DIR}")
        return 1

    images = sorted(f for f in TEST_DIR.iterdir()
                    if f.suffix.lower() in ('.jpg', '.jpeg', '.png'))
    if args.photo_set == 'baseline32':
        from accuracy_test import BASELINE_V1_32
        want = set(BASELINE_V1_32)
        images = [f for f in images if f.stem in want]

    print()
    print("=" * 66)
    print(f"  food30 오탐 스윕 — {len(images)}장 · tau={FOOD30_CONF_TAU} · 비용 $0")
    print("=" * 66)
    print()

    rows = []
    n_detect = 0
    fp_silent = []      # 검출됐지만 허용목록이 막아 줌 → 피해 없음
    fp_live = []        # 검출됐고 허용목록도 통과 → ★ 실제 오탐 위험
    tp = []             # GT 가 밥/탕이고 검출됨

    for i, p in enumerate(images, 1):
        gt = gt_from_stem(p.stem)
        hits = detect_food30(str(p))
        got = {k: v for k, v in hits.items() if v}
        if not got:
            rows.append({'photo': p.stem, 'gt': gt, 'detected': {},
                         'verdict': 'silent'})
            print(f"  [{i:02d}/{len(images)}] {p.stem:<24} —")
            continue

        n_detect += 1
        gt_is_target = (gt in _GT_SOUP_LIKE) or (gt in _GT_RICE_LIKE)
        desc = ', '.join(f"{k}={v['class']} {v['confidence']:.2f}"
                         for k, v in got.items())

        # GT 이름 자체가 허용목록을 통과하는가 = 「GPT 가 GT 를 그대로 말했을 때」.
        # ⚠ 이건 상한이 아니라 **하한**입니다(2026-08-19 독립감사 치명-3).
        #    GPT 가 GT 와 다른 탕/밥 이름을 말하면 교체가 일어날 수 있습니다.
        #    실측: 순두부찌개 사진 + 엔진 '닭볶음탕' 일 때,
        #      GPT '순두부찌개' → 침묵   /   GPT '육개장'·'해물탕'·'갈비탕' → **교체됨**
        #    즉 「GT 가 허용목록 밖」은 안전 보장이 아닙니다.
        gt_would_touch = (
            (got.get('rice') is not None and _f30_is_rice(gt)) or
            (got.get('soup') is not None and _f30_is_soup(gt))
        )

        if gt_is_target:
            verdict = 'TP_or_confusion'
            tp.append((p.stem, desc, gt))
            mark = '   '
        else:
            # ★ 여기가 오탐입니다 — 밥/탕이 아닌 사진에서 엔진이 발화했습니다.
            #   초판은 「GT 가 허용목록을 통과하는가」로 위험을 갈랐는데,
            #   _GT_*_LIKE ⊂ _F30_*_ITEMS 이므로 그 분기는 **구조적으로 항상 0건**이었습니다.
            #   엔진이 91장 전부에서 폭주해도 통과하는, 반증 불가능한 게이트였습니다.
            #   (2026-08-19 독립감사 치명-2 — 실측으로 확인됨)
            verdict = ('FP_ON_NONTARGET_GT_IN_WHITELIST' if gt_would_touch
                       else 'FP_ON_NONTARGET')
            (fp_live if gt_would_touch else fp_silent).append((p.stem, gt, desc))
            mark = '★★★' if gt_would_touch else ' ★ '

        rows.append({'photo': p.stem, 'gt': gt,
                     'detected': {k: {'class': v['class'],
                                      'confidence': round(v['confidence'], 3)}
                                  for k, v in got.items()},
                     'gt_in_whitelist': gt_would_touch,
                     'verdict': verdict})
        print(f"  [{i:02d}/{len(images)}] {p.stem:<24} {mark} {desc}")

    n_fp = len(fp_live) + len(fp_silent)

    print()
    print("=" * 66)
    print("  요약")
    print("=" * 66)
    print(f"  검출된 사진:                  {n_detect}/{len(images)}장")
    print(f"  GT 가 밥/탕 (정탐 또는 혼동):  {len(tp)}장")
    print(f"  ★ 오탐 — 밥/탕 아닌 사진에서 발화: {n_fp}장")
    print(f"      그중 GT 가 허용목록 통과:    {len(fp_live)}장  (즉시 교체됨)")
    print(f"      그 외:                     {len(fp_silent)}장  "
          f"(GPT 가 뭐라 하느냐에 달림)")

    # IP/165 §7 · IP/172: v4 의 알려진 잔존 오탐은 김치찌개·순두부찌개 2건.
    KNOWN_FP = 2
    print()
    if n_fp == 0:
        print("  ▶ 오탐 0건.")
    elif n_fp <= KNOWN_FP:
        print(f"  ▶ 오탐 {n_fp}장 — v4 의 알려진 잔존 오탐({KNOWN_FP}건) 범위 안입니다.")
    else:
        print(f"  ▶ ★ 오탐 {n_fp}장 — 알려진 {KNOWN_FP}건보다 많습니다. 아래를 보십시오.")

    if fp_live:
        print()
        print("  ★★★ GT 이름이 허용목록에 있음 — 이 사진은 실제로 이름이 바뀝니다:")
        for stem, gt, desc in fp_live:
            print(f"      {stem}  (GT={gt})  →  {desc}")
        print("  → food_analyzer._F30_RICE_ITEMS / _F30_SOUP_ITEMS 에서")
        print("    이 GT 이름을 왜 허용했는지 확인하십시오.")

    if fp_silent:
        print()
        print("  ★ 밥/탕 아닌 사진에서 발화 (GT 는 허용목록 밖):")
        for stem, gt, desc in fp_silent:
            print(f"      {stem}  (GT={gt})  →  {desc}")
        print()
        print("  ⚠ 「GT 가 허용목록 밖」은 안전 보장이 아닙니다.")
        print("    GPT 가 GT 와 다른 밥/탕 이름을 말하면 교체가 일어납니다.")
        print("    예) 순두부찌개 사진 + 엔진 '닭볶음탕':")
        print("        GPT '순두부찌개' → 침묵  /  GPT '육개장'·'해물탕' → 교체됨")
        print("    확정값은 4단계(accuracy_test)의 applied 로만 나옵니다.")

    print("=" * 66)

    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULT_DIR / f"food30_sweep_{args.photo_set}.json"
    if out.exists():           # 이전 스윕을 덮지 않는다
        import shutil
        from datetime import datetime as _dt
        try:
            shutil.copy2(out, out.with_name(
                f"{out.stem}_{_dt.fromtimestamp(out.stat().st_mtime):%Y-%m-%d_%H%M%S}.json"))
        except Exception as e:
            print(f"  [경고] 이전 스윕 보관 실패 (새 결과는 저장합니다): {e}")
    with open(out, 'w', encoding='utf-8') as f:
        json.dump({'tau': FOOD30_CONF_TAU, 'photo_set': args.photo_set,
                   'total': len(images), 'detected': n_detect,
                   'false_positive_total': n_fp,
                   'fp_gt_in_whitelist': len(fp_live),
                   'fp_gt_outside_whitelist': len(fp_silent),
                   'known_fp_budget': KNOWN_FP,
                   'over_budget': n_fp > KNOWN_FP,
                   'rows': rows}, f, ensure_ascii=False, indent=2)
    print(f"\n  결과 저장: {out}")
    # ★ 종료코드 0 유지: 오탐이 있어도 $0 진단이 $0.16 게이트를 막지 않는다(감사 중-4).
    #    rc=1 은 인프라 실패(모델 미로드·폴더 없음)에만 씁니다.
    return 0


if __name__ == '__main__':
    sys.exit(main())
