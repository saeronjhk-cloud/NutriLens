# -*- coding: utf-8 -*-
"""세션52 — 해상도 프로브. 재학습 «전»에 값싸게 답을 떠본다.  API 호출 0건.

왜 이걸 먼저 하는가
──────────────────
IP/178 §17-8 은 v5 재학습의 마지막 관문을 「해상도 640 → 1280 으로 1회 학습」이라 적었다.
근거는 「국물 색·건더기 질감이 640px 에서 뭉개졌을 수 있다」는 «가설»이다.

그런데 그 학습은 비싸다. food30 데이터셋은 39,854장(train 35,764 / val 4,090)이고
1280 은 640 대비 픽셀이 4배다. 30 epoch 이면 Colab 세션 여러 개를 소모한다.

**가설을 먼저 값싸게 떠볼 수 있다.** v4 가중치를 그대로 두고 «추론 해상도»만 640↔1280 으로
바꿔 같은 사진을 다시 보는 것이다. 해상도가 이 혼동의 원인이라면, 더 큰 입력에서
혼동 구조가 «조금이라도» 흔들려야 한다.

⛔⛔ 세션52 실측 — 프로브의 위상이 «떠보기»에서 «유일한 방법»으로 바뀌었다
────────────────────────────────────────────────────────────────────
   **1280 학습은 지금 데이터로 불가능하다.** 학습 이미지가 전부 640 으로 잘려 있다.
     .tmp/food30/images/{train,val} · food30_021/images/train 에서 무작위 180장 →
     **전부 최대변 정확히 640** (food30_021 의 build_report_021.json 이 `max_side: 640`).
   640 이미지를 imgsz=1280 으로 학습하면 업스케일일 뿐 새 정보가 없다.
   가설을 제대로 시험하려면 **AI Hub 원본에서 다시 추출**해야 한다(§ 아래 비용).

   반면 holdout(`Images/aihub_val/`, 1,800장)은 **원본 해상도 그대로**다(4032·3024…).
   그래서 「해상도가 이 혼동에 영향을 주는가」는 **추론 해상도로만** 물을 수 있다.
   이 스크립트가 하는 게 정확히 그것이다.

★ 결과 해석 — 비대칭이다. 반드시 이대로 인용할 것:
   - 1280 에서 혼동이 «줄어든다»   → 해상도가 실제로 정보를 준다는 뜻.
     그러면 데이터 재추출(≈12GB 재빌드·재업로드)을 검토할 값어치가 생긴다.
   - 1280 에서 «아무것도 안 변한다» → 약한 반증. 결정적이지는 않다
     (모델이 640 스케일 특징으로 학습됐으므로 큰 입력을 줘도 그 특징만 본다).
     하지만 **비용이 이미 크다는 걸 알므로**, 이 경우 해상도 가설은 접는 것이 맞다.
   ⇒ 어느 쪽이든 이 프로브가 «재학습 투자 결정»의 관문이다.

실행:
  python tools/food30_resolution_probe.py "D:\\서박사의 영양공식" [--limit 300]
"""
import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

DEFAULT_ROOT = Path(r'D:\서박사의 영양공식')
SIZES = (640, 1280)

# IP/178 §17-5 가 「구별 불가」로 지목한 대칭 혼동 쌍. 여기가 움직이는지가 관심사다.
WATCH_PAIRS = [
    ('곰탕', '설렁탕'), ('감자탕', '뼈해장국'), ('갈비탕', '곰탕'),
    ('꽃게탕', '해물탕'), ('닭개장', '육개장'), ('낙지탕', '연포탕'),
    ('매운탕', '알탕'), ('닭곰탕', '지리탕'),
]


def load_engine(nl_dir):
    sys.path.insert(0, str(nl_dir / 'tools'))
    import food_analyzer as fa                      # noqa: E402
    model = fa._get_food30_model()
    if model is None:
        raise SystemExit(
            '⛔ food30 모델을 못 불러왔다.\n'
            f'   확인: {nl_dir / "models" / "food30_detection_v4.pt"}\n'
            '   ultralytics 가 설치돼 있는지도 확인할 것 (pip install ultralytics).')
    return fa, model


def collect_photos(root, limit):
    """holdout 1,800장(`Images/aihub_val/<음식명>/`)에서 클래스별로 고르게 뽑는다.

    ★ 왜 «학습 val»(.tmp/food30/images/val)이 아니라 여기인가 — 세션52 실측:
      학습 데이터는 **전부 max_side 640 으로 잘려 있다**(무작위 180장 전수 640).
      640 이미지를 1280 으로 넣으면 업스케일일 뿐 새 정보가 없다 — 프로브가 무의미해진다.
      holdout 은 원본 그대로다(최대변 4032·3024·2048…). 해상도 가설은 여기서만 물을 수 있다.

    ⚠ 학습 val 은 애초에 쓸 수도 없었다 — 30클래스 중 5종(추어탕·해물탕·닭개장·
      육개장·뼈해장국)의 라벨이 «0건»이다. 그 5종은 food30_021 에 따로 있다.
    """
    base = root / 'Images' / 'aihub_val'
    if not base.exists():
        raise SystemExit(
            f'⛔ holdout 을 못 찾았다: {base}\n'
            '   IP/178 §11-3 의 「holdout 1,800장」 경로다. 원본 해상도가 있어야 한다.')
    by_class = {}
    for d in sorted(base.iterdir()):
        if not d.is_dir():
            continue
        imgs = [p for p in sorted(d.iterdir())
                if p.suffix.lower() in ('.jpg', '.jpeg', '.png')]
        if imgs:
            by_class[d.name] = imgs

    per = max(1, limit // max(1, len(by_class)))
    out = []
    for cls in sorted(by_class):
        out.extend((cls, p) for p in by_class[cls][:per])
    return out


def top1(model, path, imgsz, names):
    """이 사진에서 «가장 강한» food30 클래스 하나. 게이트 화이트리스트는 여기선 안 건다
       — 우리가 보려는 건 모델의 표현이지 운영 정책이 아니다."""
    try:
        res = model.predict(str(path), imgsz=imgsz, conf=0.05, verbose=False)
    except Exception as e:
        return None, 0.0, str(e)
    best, bconf = None, 0.0
    for r in res:
        if getattr(r, 'boxes', None) is None:
            continue
        for b in r.boxes:
            c = float(b.conf[0])
            cid = int(b.cls[0])
            if c > bconf and cid < len(names):
                best, bconf = names[cid], c
    return best, bconf, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('root', nargs='?', default=str(DEFAULT_ROOT))
    ap.add_argument('--limit', type=int, default=300)
    args = ap.parse_args()

    root = Path(args.root)
    nl = root / 'backends' / 'NutriLens'
    fa, model = load_engine(nl)
    names = fa.FOOD30_CLASS_NAMES

    photos = collect_photos(root, args.limit)
    print(f'표본 {len(photos)}장 · 클래스 {len(set(c for c, _ in photos))}종\n')

    stats = {}
    for size in SIZES:
        t0 = time.time()
        hit, seen = 0, 0
        conf_pairs = Counter()
        errs = 0
        rows = []
        for gt, path in photos:
            pred, conf, err = top1(model, path, size, names)
            if err:
                errs += 1
                continue
            seen += 1
            rows.append((gt, pred, conf))
            if pred == gt:
                hit += 1
            elif pred is not None:
                conf_pairs[(gt, pred)] += 1
        stats[size] = {'hit': hit, 'seen': seen, 'pairs': conf_pairs,
                       'sec': time.time() - t0, 'errs': errs, 'rows': rows}
        print(f'imgsz {size:>4}: top1 {hit}/{seen} = {hit/max(seen,1)*100:.1f}%  '
              f'({stats[size]["sec"]:.0f}초, 오류 {errs})')

    a, b = stats[SIZES[0]], stats[SIZES[1]]
    print(f'\n{"=" * 68}\n주목 쌍 — 「구별 불가」가 해상도로 흔들리는가\n{"=" * 68}')
    print(f'  {"쌍":<20}{"640":>8}{"1280":>8}{"Δ":>8}')
    tot_a = tot_b = 0
    for x, y in WATCH_PAIRS:
        ca = a['pairs'][(x, y)] + a['pairs'][(y, x)]
        cb = b['pairs'][(x, y)] + b['pairs'][(y, x)]
        tot_a += ca
        tot_b += cb
        print(f'  {x + "↔" + y:<20}{ca:>8}{cb:>8}{cb - ca:>+8}')
    print(f'  {"합계":<20}{tot_a:>8}{tot_b:>8}{tot_b - tot_a:>+8}')

    d_acc = b['hit'] / max(b['seen'], 1) - a['hit'] / max(a['seen'], 1)
    print(f'\n{"=" * 68}\n판정\n{"=" * 68}')
    print(f'  top1 정확도 Δ  {d_acc * 100:+.1f}%p')
    print(f'  주목 쌍 혼동 Δ {tot_b - tot_a:+d}건')
    if tot_b < tot_a or d_acc > 0.01:
        print('\n  ▶ 해상도가 «무언가»를 바꾼다. 1280 재학습에 값어치가 있다.')
    else:
        print('\n  ▶ 추론 해상도만으로는 안 움직인다.')
        print('    ⚠ 이것은 «약한» 반증이다. 640 으로 학습된 특징만 보고 있기 때문이다.')
        print('      재학습을 기각하는 근거로 쓰지 마라 — 우선순위를 낮추는 근거일 뿐이다.')

    out = nl / '.tmp' / 'resolution_probe.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        'generated': time.strftime('%Y-%m-%d %H:%M:%S'),
        'model': 'food30_detection_v4', 'n_photos': len(photos),
        'sizes': {str(s): {'top1_hit': stats[s]['hit'], 'seen': stats[s]['seen'],
                           'seconds': round(stats[s]['sec'], 1),
                           'errors': stats[s]['errs'],
                           'confusions': {f'{x}->{y}': n
                                          for (x, y), n in stats[s]['pairs'].most_common()}}
                  for s in SIZES},
    }, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'\n  기록: {out}')


if __name__ == '__main__':
    main()
