# -*- coding: utf-8 -*-
"""세션50 검증 — «재구현»이 아니라 실제 food_analyzer.apply_food30_override 를 불러
   aihub300(획득)과 게이트91(손실)을 다시 채점한다.

시뮬레이션 스크립트(simulate_category_widening.py)는 분기를 손으로 옮겨 적은 것이라
원본과 어긋날 수 있다. 이 스크립트는 그 위험을 없앤다 — 프로덕션 함수를 그대로 쓴다.
API 호출 없음. $0.

실행:
  python verify_widening_real.py "D:\\서박사의 영양공식"
"""
import json
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r'D:\서박사의 영양공식')
NL = ROOT / 'backends' / 'NutriLens'
sys.path.insert(0, str(NL / 'tools'))

import food_analyzer as fa  # noqa: E402

AIHUB = NL / '.tmp' / 'photo_test_results_aihub300.json'
GATE = NL / '.tmp' / 'food30_sweep_all.json'


def run_override(names, detected):
    """이름 리스트 + 엔진 detected 로 실제 override 를 돌리고 (새 이름들, info) 반환."""
    analysis = {'foods': [{'name_ko': n} for n in names]}
    fa.apply_food30_override(analysis, detected)
    return [f['name_ko'] for f in analysis['foods']], analysis['food30_engine']


def grade(gt, names):
    ns = [fa._f30_norm(n) for n in names]
    g = fa._f30_norm(gt)
    return 'EXACT' if g in ns else 'NONE'


def main():
    ok = True

    # ── 1. 획득 — aihub300 ──────────────────────────────────────────────
    det = json.loads(AIHUB.read_text(encoding='utf-8'))['details']
    measured_exact = sum(1 for r in det if r['match'] == 'EXACT')

    new_exact = 0
    gains, losses, widened_rows = [], [], []
    for r in det:
        hits = (r['food30_engine'] or {}).get('detected') or {}
        names, info = run_override(list(r['ai_foods_detected']), hits)
        g = grade(r['expected'], names)
        if g == 'EXACT':
            new_exact += 1
        was = r['match'] == 'EXACT'
        if not was and g == 'EXACT':
            gains.append((r['expected'], r['ai_foods_detected'], names))
        if was and g != 'EXACT':
            losses.append((r['expected'], r['ai_foods_detected'], names))
        if any(a.get('widened') for a in info['applied']):
            widened_rows.append((r['expected'], r['ai_foods_detected'], names, g))

    print('=' * 70)
    print('1. 획득 — aihub300 (AI Hub holdout 30종 × 10장)')
    print('=' * 70)
    print(f'  실측 EXACT (세션49 유료 실행)      : {measured_exact}/300  '
          f'{measured_exact/3:.1f}%')
    print(f'  확장 적용 후 EXACT (재채점)        : {new_exact}/300  {new_exact/3:.1f}%')
    print(f'  획득 {len(gains)}장 · 상실 {len(losses)}장'
          f'   → {len(gains)}:{len(losses)}')
    print(f'  widened=True 로 표시된 교체        : {len(widened_rows)}장 '
          f'(그중 EXACT {sum(1 for w in widened_rows if w[3] == "EXACT")}장)')
    if losses:
        ok = False
        print('  ⛔ 새로 틀린 장:')
        for gt, before, after in losses:
            print(f'     GT={gt} {before} → {after}')

    # ── 2. 손실 — 게이트 91장 (food30 30종 «밖»의 도메인) ────────────────
    gate = json.loads(GATE.read_text(encoding='utf-8'))
    print()
    print('=' * 70)
    print('2. 손실 — 게이트 91장 (엔진이 건드리면 안 되는 도메인)')
    print('=' * 70)
    print(f"  τ={gate['tau']} · 총 {gate['total']}장 · 엔진 발화 {gate['detected']}장")
    fp = 0
    for row in gate['rows']:
        if not row['detected']:
            continue
        # GPT 가 정답 이름을 냈다고 가정한다 — 게이트셋에서 가장 불리한 가정이다.
        names, info = run_override([row['gt']], row['detected'])
        changed = names != [row['gt']]
        mark = '⛔ 교체됨' if changed else '✅ 지켜냄'
        if changed:
            fp += 1
            ok = False
        print(f"  {mark}  {row['gt']:10s} 엔진={list(row['detected'].values())}"
              f" → {names}")
    print(f'  거짓 교체 {fp}장 / 발화 {gate["detected"]}장')

    # ── 3. 반증 — 가드를 끄면 정말 터지는가 ──────────────────────────────
    print()
    print('=' * 70)
    print('3. 반증 확인 — _F30_FP_PRONE_CLASSES 를 비우면 다시 터져야 한다')
    print('=' * 70)
    saved = fa._F30_FP_PRONE_CLASSES
    fa._F30_FP_PRONE_CLASSES = set()
    broke = 0
    for row in gate['rows']:
        if not row['detected']:
            continue
        names, _ = run_override([row['gt']], row['detected'])
        if names != [row['gt']]:
            broke += 1
            print(f"     {row['gt']} → {names}")
    fa._F30_FP_PRONE_CLASSES = saved
    print(f'  가드 없이는 거짓 교체 {broke}장 — 가드가 실제로 일하고 있다: '
          f'{"✅" if broke > fp else "⛔ 가드가 무의미"}')
    if broke <= fp:
        ok = False

    print()
    print('=' * 70)
    print(f'  판정: {"✅ 통과" if ok else "⛔ 실패"}')
    print('=' * 70)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
