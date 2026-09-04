# -*- coding: utf-8 -*-
"""세션52 — 불일치(disagreement) 잔여분 감사 + 밥류 확장(L3) 변형 실측.  $0 · API 호출 없음.

배경
────
IP/178 §17-8 이 남긴 첫 번째 값싼 항목: 「불일치 12건 — 엔진이 검출했는데 못 고친 것.
확장 대상이 더 있는지」. 그리고 §4-3 이 보류한 밥류 확장(L3).

이 스크립트가 하는 일
────────────────────
1. 불일치 전수를 열어 «어떤 규칙이 있어야 도달하는가»로 분류한다.
   - 접미사 확장으로 닿는가(밥/탕/국/찌개/전골)
   - 닿는다 해도 «실제로 EXACT 가 되는가» (엔진 자신이 틀렸으면 교체해도 소용없다)
2. 밥류 확장 변형을 프로덕션 함수로 직접 재채점한다(재구현 금지 — 규칙55 계열).
   R0 현행 · R1 트랩만 · R2 트랩+비빔밥계열 제외
   획득은 aihub300 에서, 손실은 게이트91 에서 잰다(규칙64).

★ 세션52 결론 (이 스크립트가 내놓은 답)
────────────────────────────────────
1. 불일치 12건 중 «접미사 확장으로 닿는» 것은 밥류 5건뿐. 탕류 6건 + 밥류 1건은
   GPT 가 아예 다른 요리 계열(죽·칼국수·짬뽕·숙회·디저트·리조또)을 댄 것이라
   어떤 접미사 규칙으로도 못 닿는다. 도달하려면 «무조건 덮어쓰기»여야 하는데
   그건 IP/177 §15-4-A 가 0.88:1 로 이미 기각했다.
2. 닿는 5건도 «켜면 안 된다». 획득 내역이 전부 별미밥 흡수(고구마밥·밤밥·영양밥·
   전복돌솥밥)이고, 이는 test_specialty_rice_keeps_its_own_nutrition 이 이미
   금지한 것이다. 구현하는 즉시 그 테스트가 깨졌다.
   → food_analyzer._f30_is_rice 위 주석에 기각 근거 전문이 있다.
⇒ **불일치 12건에서 더 짜낼 것은 없다.** 이 스크립트는 그 결론의 근거로 남긴다.

실행:
  python tools/food30_disagreement_audit.py "D:\\서박사의 영양공식"
"""
import json
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r'D:\서박사의 영양공식')
NL = ROOT / 'backends' / 'NutriLens'
sys.path.insert(0, str(NL / 'tools'))

import food_analyzer as fa  # noqa: E402

TMP = NL / '.tmp'
RUNS = {
    'session49 raw':   TMP / 'photo_test_results_aihub300.json',
    'session50 widened': TMP / 'photo_test_results_aihub300_widened.json',
    'session51 production': TMP / 'photo_test_results_aihub300_production.json',
}
GATE = TMP / 'food30_sweep_all.json'

# ── 밥류 확장 후보 트랩 ────────────────────────────────────────────────
# 실측 근거: 위 3개 실행 + 무손상 4개 실행에서 GPT 가 낸 «밥으로 끝나는» 이름 21종을
# 전수로 뽑아 분류했다. 접미사만 보면 국밥(국물)·덮밥(반찬 얹은 것)이 섞여 들어온다.
_RICE_TRAP_SUFFIX = ('국밥', '덮밥', '김밥', '초밥', '볶음밥', '주먹밥', '쌈밥', '알밥')
_BIBIM_SUFFIX = ('비빔밥',)


def _make_rice_test(trap_suffix, extra_trap=()):
    """확장 모드 _f30_is_rice 를 만든다. engine_class=None 이면 종전(좁은) 동작."""
    def _test(nm, engine_class=None):
        n = fa._f30_norm(nm)
        if n in fa._F30_RICE_ITEMS:
            return True
        if engine_class is None or engine_class in fa._F30_FP_PRONE_CLASSES:
            return False
        if n.endswith(tuple(trap_suffix) + tuple(extra_trap)):
            return False
        return n.endswith('밥')
    return _test


VARIANTS = {
    'R0 현행 (밥류 확장 없음)': None,
    'R1 밥류확장 + 트랩': _make_rice_test(_RICE_TRAP_SUFFIX),
    'R2 밥류확장 + 트랩 + 비빔밥계열 제외': _make_rice_test(_RICE_TRAP_SUFFIX, _BIBIM_SUFFIX),
}


def run_override(names, detected):
    analysis = {'foods': [{'name_ko': n} for n in names]}
    fa.apply_food30_override(analysis, detected)
    return [f['name_ko'] for f in analysis['foods']], analysis['food30_engine']


def grade(gt, names):
    ns = [fa._f30_norm(n) for n in names]
    return 'EXACT' if fa._f30_norm(gt) in ns else 'NONE'


class rice_test:
    """_F30_CATEGORY_TEST['rice'] 를 임시 교체하는 컨텍스트."""

    def __init__(self, fn):
        self.fn = fn

    def __enter__(self):
        self.orig = fa._F30_CATEGORY_TEST['rice']
        if self.fn is not None:
            fa._F30_CATEGORY_TEST['rice'] = self.fn
        return self

    def __exit__(self, *a):
        fa._F30_CATEGORY_TEST['rice'] = self.orig
        return False


# ══════════════════════════════════════════════════════════════════════
# 1. 불일치 전수 감사
# ══════════════════════════════════════════════════════════════════════
def audit_disagreements(path, label):
    det = json.loads(path.read_text(encoding='utf-8'))['details']
    rows = [r for r in det if (r['food30_engine'] or {}).get('disagreement')]
    print(f'\n{"=" * 78}\n1. 불일치 감사 — {label}  ({len(rows)}건)\n{"=" * 78}')

    buckets = {'접미사로 닿고 이득': [], '접미사로 닿지만 무익(엔진이 틀림)': [],
               '접미사로 못 닿음': []}
    for r in rows:
        eng = r['food30_engine']
        for d in eng['disagreement']:
            slot, cls = d['slot'], d['class']
            db = fa.FOOD30_DB_KEY.get(cls, cls)
            gt = fa._f30_norm(r['expected'])
            names = [fa._f30_norm(n) for n in (r['ai_foods_detected'] or [])]
            suffix = '밥' if slot == 'rice' else fa._F30_SOUP_SUFFIX
            reachable = [n for n in names if n.endswith(suffix)]
            useful = fa._f30_norm(db) == gt
            line = (f'GT={r["expected"]:<6} 엔진={cls}({eng["detected"][slot]["confidence"]}) '
                    f'GPT={r["ai_foods_detected"]}')
            if not reachable:
                buckets['접미사로 못 닿음'].append((line, slot))
            elif useful:
                buckets['접미사로 닿고 이득'].append((line, slot))
            else:
                buckets['접미사로 닿지만 무익(엔진이 틀림)'].append((line, slot))

    for k, v in buckets.items():
        print(f'\n  [{k}]  {len(v)}건')
        for line, slot in v:
            print(f'    ({slot}) {line}')
    return buckets


# ══════════════════════════════════════════════════════════════════════
# 2. 밥류 확장 변형 — 획득(aihub300) × 손실(게이트91)
# ══════════════════════════════════════════════════════════════════════
def measure(variant_fn, path):
    """변형을 적용해 재채점. (EXACT수, 판정가능수, 행별판정) 반환."""
    det = json.loads(path.read_text(encoding='utf-8'))['details']
    verdicts, exact, usable = [], 0, 0
    with rice_test(variant_fn):
        for i, r in enumerate(det):
            eng = r.get('food30_engine') or {}
            if not eng:                       # API 에러 행 — 판정 불가
                verdicts.append(None)
                continue
            usable += 1
            names, _ = run_override(list(r.get('ai_foods_detected') or []), eng.get('detected') or {})
            g = grade(r['expected'], names)
            verdicts.append((g, r['expected'], tuple(r.get('ai_foods_detected') or []), tuple(names)))
            if g == 'EXACT':
                exact += 1
    return exact, usable, verdicts


def measure_gate(variant_fn):
    """게이트91 — GT 는 food30 «밖»이다. 엔진이 이름을 바꾸면 그 자체가 손실이다."""
    gate = json.loads(GATE.read_text(encoding='utf-8'))
    bad = []
    with rice_test(variant_fn):
        for row in gate['rows']:
            if not row['detected']:
                continue
            names, info = run_override([row['gt']], row['detected'])
            if names != [row['gt']]:
                bad.append((row['photo'], row['gt'], row['detected'], names))
    return bad


# ══════════════════════════════════════════════════════════════════════
# 3. 반증 확인 — 트랩이 «실제로» 일하는가
# ══════════════════════════════════════════════════════════════════════
# 트랩을 끄면 아래가 전부 흡수돼야 한다. 흡수되지 않으면 트랩이 아니라
# 다른 무언가(접미사 불일치 등)가 막고 있었다는 뜻이다.
TRAP_PROBES = [
    ('돼지국밥', '쌀밥'), ('시래기국밥', '쌀밥'), ('치즈덮밥', '쌀밥'),
    ('참치김치찌개덮밥', '쌀밥'), ('김밥', '쌀밥'), ('김치볶음밥', '쌀밥'),
    ('비빔밥', '돌솥밥'), ('돌솥비빔밥', '돌솥밥'),
]


def falsify():
    print(f'\n{"=" * 78}\n3. 반증 확인 — 트랩을 끄면 실제로 흡수되는가\n{"=" * 78}')
    no_trap = _make_rice_test(())          # 트랩 전부 해제
    r2 = VARIANTS['R2 밥류확장 + 트랩 + 비빔밥계열 제외']
    ok = True
    print(f'  {"GPT 이름":<18}{"엔진":<8}{"트랩 OFF":<12}{"R2 (트랩 ON)"}')
    for name, cls in TRAP_PROBES:
        with rice_test(no_trap):
            off, _ = run_override([name], {'rice': {'class': cls, 'confidence': 0.95}})
        with rice_test(r2):
            on, _ = run_override([name], {'rice': {'class': cls, 'confidence': 0.95}})
        absorbed_off = off != [name]
        absorbed_on = on != [name]
        mark = '✅' if (absorbed_off and not absorbed_on) else '⛔'
        if mark == '⛔':
            ok = False
        print(f'  {mark} {name:<16}{cls:<8}{str(off):<12}{on}')
    print('\n  → ✅ = 트랩이 없으면 흡수되고, R2 에서는 막힌다 (트랩이 일하고 있음)')
    if not ok:
        print('  ⛔ 트랩이 일하지 않는 항목이 있다. 위 표를 확인하라.')
    return ok


def main():
    audit_disagreements(RUNS['session51 production'], 'aihub300 · production (세션51)')

    print(f'\n{"=" * 78}\n2. 밥류 확장(L3) 변형 — R0 대비 «순증분»만 표시\n{"=" * 78}')
    base_v = {}
    for name, fn in VARIANTS.items():
        print(f'\n── {name} ' + '─' * max(1, 70 - len(name)))
        for label, path in RUNS.items():
            if not path.exists():
                continue
            exact, n, verdicts = measure(fn, path)
            if fn is None:
                base_v[label] = (exact, verdicts)
                print(f'   {label:<22} EXACT {exact}/{n}   (기준선)')
                continue
            b_exact, b_verd = base_v[label]
            gains = [v for v, b in zip(verdicts, b_verd)
                     if v and b and v[0] == 'EXACT' and b[0] != 'EXACT']
            losses = [(v, b) for v, b in zip(verdicts, b_verd)
                      if v and b and v[0] != 'EXACT' and b[0] == 'EXACT']
            print(f'   {label:<22} EXACT {exact}/{n}   Δ{exact - b_exact:+d}  '
                  f'(획득 {len(gains)} · 상실 {len(losses)})')
            for g, gt, before, after in gains:
                print(f'        ＋ GT={gt:<6} {list(before)} → {list(after)}')
            for (v, b) in losses:
                print(f'        － GT={v[1]:<6} {list(b[3])} → {list(v[3])}')
        bad = measure_gate(fn)
        print(f'   {"게이트91 (손실 측정)":<22} 거짓 교체 {len(bad)}장')
        for photo, gt, detected, names in bad:
            print(f'        ⛔ {photo}  GT={gt} {detected} → {names}')

    falsify()


if __name__ == '__main__':
    main()
