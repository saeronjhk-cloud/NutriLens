# -*- coding: utf-8 -*-
"""세션55 — 「한 방향 흡수 103건」(IP/178 §17-5·17-8) 을 교체 정책으로 다룰 수 있는가.  $0 · API 0건.

결론(2026-09-28): **다룰 것이 없다. 정책을 추가하지 않는다.**
  - 103건은 «엔진 단독 top1» 오답(τ 무관)이다. 프로덕션은 τ=0.70 + «GPT 가 낸 같은 계열 항목만
    바꾼다» 가드를 거친다. 그 뒤에 남는 엔진 유발 손실은 aihub300 production 기준 **7건**(이득 79).
  - 정책 A «흡수 방향 쌍 양보» — 쌍 목록을 aihub300 을 «뺀» holdout 1,698장에서 도출(누수 없음).
      n>=2 쌍 11개: 3개 실행 모두 발동 1회(알탕→매운탕) · +1.   n>=3·n>=4: 발동 0.
      → n=1 이득이라 채택 불가(규칙56).
  - 정책 B «GPT 가 30종 이름을 냈으면 τ2 미만 엔진은 양보» — τ2 0.80~1.0 전 구간 순증 +1 ~ −14.
      → 엔진은 in-vocab 분쟁에서도 GPT 를 더 자주 이긴다. 양보는 손해.
실행:  python tools/food30_absorption_policy_sim.py   (backends/NutriLens 에서)
"""
import collections
import json
from pathlib import Path

T = Path(__file__).resolve().parent.parent / '.tmp'
TAU = 0.70
C30 = {'쌀밥', '기타잡곡밥', '콩밥', '보리밥', '돌솥밥', '현미밥', '흑미밥', '감자밥', '갈비탕', '감자탕',
       '곰탕', '매운탕', '꼬리곰탕', '꽃게탕', '낙지탕', '내장탕', '닭곰탕', '닭볶음탕', '지리탕', '도가니탕',
       '삼계탕', '설렁탕', '알탕', '연포탕', '오리탕', '추어탕', '해물탕', '닭개장', '육개장', '뼈해장국'}
RUNS = [('raw', 'photo_test_results_aihub300.json'),
        ('widened', 'photo_test_results_aihub300_widened.json'),
        ('production', 'photo_test_results_aihub300_production.json')]


def changes(run):
    out = []
    for row in run['details']:
        eng = row.get('food30_engine') or {}
        for ap in eng.get('applied', []):
            if ap.get('changed'):
                out.append((row['expected'], ap['from'], ap['to'],
                            eng['detected'][ap['slot']]['confidence']))
    return out


def score(rows, yield_fn):
    g = l = 0
    for gt, fr, to, cf in rows:
        if yield_fn(fr, to, cf):
            if fr == gt and to != gt:
                g += 1
            elif to == gt and fr != gt:
                l += 1
    return g, l


def main():
    diag = json.load(open(T / 'diagnose' / 'attractor_diagnose_2026-08-28_135701.json', encoding='utf-8'))
    runs = {k: json.load(open(T / f, encoding='utf-8')) for k, f in RUNS}
    test = {e['photo'] for e in runs['production']['food30_engine_summary']['events']}
    dev = [p for p in diag['positive'] if p['photo'] not in test]
    c = collections.Counter((p['gt'], p['top1']) for p in dev
                            if p['top1'] and p['top1'] != p['gt'] and (p['top1_conf'] or 0) >= TAU)
    print(f'도출셋 {len(dev)}장 (aihub300 제외) · 평가셋 aihub300 {len(test)}장\n')

    print('=== 현행 엔진 교체의 이득/손실 ===')
    for k, r in runs.items():
        rows = changes(r)
        g = sum(1 for gt, fr, to, _ in rows if to == gt and fr != gt)
        l = sum(1 for gt, fr, to, _ in rows if fr == gt and to != gt)
        print(f'  {k:<11} 이득 {g:3d} · 손실 {l:2d}')

    print('\n=== 정책 A: 흡수 방향 쌍 양보 ===')
    for mn in (2, 3, 4):
        Y = {(a, b) for (a, b), n in c.items()
             if n >= mn and min(n, c.get((b, a), 0)) / n <= 0.34}
        for k, r in runs.items():
            g, l = score(changes(r), lambda fr, to, cf: (fr, to) in Y)
            print(f'  n>={mn} 쌍 {len(Y):2d}개  {k:<11} 이득 {g} 손실 {l} 순 {g - l:+d}')

    print('\n=== 정책 B: GPT 가 30종 이름이면 τ2 미만 엔진 양보 ===')
    for th in (0.80, 0.85, 0.90, 0.95, 1.01):
        res = []
        for k, r in runs.items():
            g, l = score(changes(r), lambda fr, to, cf: fr in C30 and cf < th)
            res.append(f'{k} {g - l:+d}')
        print(f'  τ2={th:.2f}  ' + ' · '.join(res))


if __name__ == '__main__':
    main()
