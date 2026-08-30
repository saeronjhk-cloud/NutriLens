#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
회귀 테스트 — food30 엔진 통합 (IP/166 v2, 세션45 · 2026-08-16)
──────────────────────────────────────────────────────────────────────
지키는 invariant:

  [클래스 계약]
  - FOOD30_CLASS_NAMES 30종·순서가 datasets/food30_021/data.yaml 과 완전히 같다.
    (순서가 어긋나면 학습·추론은 정상으로 보이면서 전 클래스가 조용히 오답 — 규칙11)
  - 모델의 names 가 코드와 다르면 _get_food30_model() 이 로드를 거부한다 (규칙18)

  [DB 키 계약]
  - FOOD30_DB_KEY 의 모든 값이 _search_gold() 에서 high-confidence 로 잡힌다.
    → 이름 교체가 칼로리를 「정정」하는 게 아니라 「소실」시키는 사고(IP/166 §2-B) 차단
  - '기타잡곡밥' 은 gold 미등재라 '잡곡밥' 으로 매핑된다

  [항목 판별 계약]  ★ 이 프로젝트에서 가장 비싼 부분
  ── 세션50 (2026-08-30) 개정 ─────────────────────────────────────────
  판별에 «두 모드»가 있다. 구분을 흐리지 말 것.

    좁은 모드  _f30_is_soup(nm)                 ← engine_class 없음. 종전 그대로.
               「이 이름이 food30 30종에 속하는가」. food30_sweep 등이 쓴다.
    확장 모드  _f30_is_soup(nm, engine_class)   ← apply_food30_override 만 쓴다.
               「이 이름이 국물 한 그릇인가」. 접미사 탕/국/찌개/전골.

  - 좁은 모드에서는 찌개·된장국·대구탕·콩나물해장국 전부 여전히 False 다 (종전 계약 유지)
  - 확장 모드에서는 교체 «후보»가 된다. 다만 다음 두 가드가 걸린다:
      · 엔진 클래스가 _F30_FP_PRONE_CLASSES(현재 닭볶음탕)면 확장을 끈다
        → 김치찌개·순두부찌개 오탐 2건이 여기서 막힌다 (게이트91 실측)
      · _F30_SOUP_TRAP(설탕·탕수육·국수류)은 접미사가 맞아도 후보가 아니다
  - 밥류는 확장하지 않는다. 비빔밥↔돌솥밥이 동전던지기라 손실 표본 n=1 (규칙56)
  - 비빔밥·볶음밥·덮밥류는 여전히 대상이 아니다 (IP/165 §3-1 회색지대)

  근거: aihub300 재채점 EXACT 137→161 (+24, 신규 오답 0) · 게이트91 거짓 교체 0
        재현 스크립트 outputs/verify_widening_real.py (프로덕션 함수 직접 호출)

  ⚠ 미결: 콩나물해장국·선지해장국 등 «비-food30 해장국»은 두 평가셋 어디에도
     사진이 없다. 확장 모드에서 뼈해장국으로 교체될 수 있으나 실측 근거가 없다(규칙57).

  [교체 정책 계약]  제이 확정 2026-08-16
  - 카테고리(밥/탕)당 최대 1건 교체. 밥 1건 + 탕 1건 동시 교체 가능
  - 같은 항목을 두 슬롯이 중복 교체하지 않는다
  - 엔진은 검출했는데 GPT 응답에 해당 계열이 없으면 추가하지 않고 disagreement 기록 (IP/166 §2)
  - 이름만 바꾼다. 칼로리는 뒤따르는 match_with_db 가 재계산한다

  [안전장치 계약]
  - FOOD30_ENGINE=0 이면 추론 자체를 하지 않는다 (Railway 즉시 롤백)
  - τ = 0.70 (IP/165 §7 실측 확정)

torch/ultralytics 없이 돌아간다 — 순수 함수와 모듈 상수만 검증하고, 모델은 stub 로 대체한다.

실행:
  cd "D:\\서박사의 영양공식\\backends\\NutriLens"
  python -m pytest tests/test_food30_override.py -v
  또는  python tests/test_food30_override.py
"""
import ast          # ★ 세션49: 모듈 최상위에 둔다. 여러 테스트가 함수 안에서
                    #   `import ast as _ast` 를 하는데, 그러면 같은 함수 안에서
                    #   `ast.` 를 쓰는 순간 NameError 가 난다. 실제로 세션49에서
                    #   두 번 그랬다(규칙46 계열). 여기 두면 둘 다 동작한다.
import os
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_NUTRILENS = _HERE.parent
sys.path.insert(0, str(_NUTRILENS / 'tools'))

import food_analyzer as fa  # noqa: E402


DATA_YAML = _NUTRILENS / 'training' / 'datasets' / 'food30_021' / 'data.yaml'


def _mk(*names):
    """GPT-4o 응답 모양의 최소 analysis dict."""
    return {'foods': [{'name_ko': n, 'estimated_serving_g': 300} for n in names]}


def _names(analysis):
    return [f['name_ko'] for f in analysis['foods']]


# ══════════════════════════════════════════════════════════════════
class TestClassContract(unittest.TestCase):
    """클래스 30종·순서 계약"""

    def test_30_classes(self):
        self.assertEqual(len(fa.FOOD30_CLASS_NAMES), 30)
        self.assertEqual(len(set(fa.FOOD30_CLASS_NAMES)), 30, "중복 클래스명")

    def test_order_matches_data_yaml(self):
        if not DATA_YAML.exists():
            self.skipTest(f"data.yaml 없음: {DATA_YAML}")
        import re
        got = {}
        for line in DATA_YAML.read_text(encoding='utf-8').splitlines():
            m = re.match(r'\s*(\d+):\s*(\S+)\s*$', line)
            if m:
                got[int(m.group(1))] = m.group(2)
        self.assertEqual(len(got), 30, "data.yaml 클래스 수가 30이 아님")
        for i, n in enumerate(fa.FOOD30_CLASS_NAMES):
            self.assertEqual(got[i], n, f"인덱스 {i} 불일치: yaml={got[i]} code={n}")

    def test_category_split(self):
        self.assertEqual(list(fa._F30_RICE_IDX), list(range(0, 8)))
        self.assertEqual(list(fa._F30_SOUP_IDX), list(range(8, 30)))
        for i in fa._F30_RICE_IDX:
            self.assertTrue(fa.FOOD30_CLASS_NAMES[i].endswith('밥'))

    def test_tau_and_whitelist(self):
        self.assertEqual(fa.FOOD30_CONF_TAU, 0.70,
                         "τ 는 IP/165 §7 실측 확정값 0.70")
        self.assertEqual(fa.FOOD30_WHITELIST, set(fa.FOOD30_CLASS_NAMES),
                         "제이 확정 2026-08-16: 30종 전부 개방")

    def test_model_path_is_v4(self):
        # v3 는 negative 오염본. 절대 쓰지 않는다.
        self.assertIn('v4', fa._F30_MODEL_PATH.name)
        self.assertNotIn('v3', fa._F30_MODEL_PATH.name)


# ══════════════════════════════════════════════════════════════════
class TestDbKeyContract(unittest.TestCase):
    """이름 교체가 칼로리를 소실시키지 않는가 (IP/166 §2-B)"""

    def test_all_keys_defined(self):
        self.assertEqual(set(fa.FOOD30_DB_KEY), set(fa.FOOD30_CLASS_NAMES))

    def test_gitajapgok_mapped(self):
        self.assertEqual(fa.FOOD30_DB_KEY['기타잡곡밥'], '잡곡밥')

    def test_every_db_key_resolves_high(self):
        """모든 DB 키가 gold 에서 high-confidence 로 잡혀야 한다."""
        fails = []
        for cls, key in fa.FOOD30_DB_KEY.items():
            if key is None:
                continue
            k, d = fa._search_gold(key)
            if not d:
                fails.append(f"{cls}->{key}: gold 미매칭")
                continue
            conf = fa._match_confidence(fa._normalize_food_name(key), k)
            if conf != 'high':
                fails.append(f"{cls}->{key}: conf={conf} (matched={k})")
        self.assertEqual(fails, [], "gold 매칭 실패:\n  " + "\n  ".join(fails))

    def test_calories_are_per_100g_and_sane(self):
        """gold 는 100g 기준 통일. 탕·밥이 상식 범위인지 확인(단위 혼재 조기 발견)."""
        odd = []
        for cls, key in fa.FOOD30_DB_KEY.items():
            if key is None:
                continue
            _, d = fa._search_gold(key)
            if not d:
                odd.append(f"{cls}({key}) gold 미매칭")
                continue
            cal = d['cal']
            lo, hi = (100, 220) if cls.endswith('밥') else (20, 160)
            if not (lo <= cal <= hi):
                odd.append(f"{cls}({key}) {cal} kcal/100g — 기대 {lo}~{hi}")
        self.assertEqual(odd, [], "칼로리 단위/값 이상:\n  " + "\n  ".join(odd))


# ══════════════════════════════════════════════════════════════════
class TestItemClassifier(unittest.TestCase):
    """GPT 응답 항목을 밥/탕으로 볼 것인가"""

    RICE_YES = ['쌀밥', '현미밥', '흑미밥', '보리밥', '콩밥', '돌솥밥', '감자밥',
                '잡곡밥', '기타잡곡밥', '흰쌀밥', '흰밥', '백미밥', '맨밥',
                '공기밥', '공깃밥', '밥', '검은쌀밥', '현미잡곡밥', '서리태콩밥']
    RICE_NO = ['비빔밥', '돌솥비빔밥', '김치볶음밥', '김밥', '주먹밥', '제육덮밥',
               '초밥', '알밥', '회덮밥', '순대국밥', '밥버거', '쌈밥',
               # ★ 2026-08-16 검증에서 블랙리스트 방식이 전부 놓쳤던 별미밥 계열.
               #   실측: 곤드레밥 420kcal → 쌀밥 478kcal 로 바뀌며 나물 영양이 소실됐다.
               '곤드레밥', '약밥', '굴밥', '영양밥', '콩나물밥', '취나물밥', '무밥',
               '솥밥', '가마솥밥', '즉석밥', '볶음김치밥', '카레라이스', '오므라이스',
               '갈비탕', '라면', '']

    SOUP_YES = ['갈비탕', '곰탕', '설렁탕', '꼬리곰탕', '닭볶음탕', '육개장',
                '닭개장', '뼈해장국', '매운탕', '알탕', '연포탕', '추어탕',
                '닭도리탕', '왕갈비탕', '설농탕', '육계장', '뼈다귀해장국', '미꾸라지탕']
    SOUP_NO = ['김치찌개', '순두부찌개', '된장찌개', '부대찌개',      # ★ v4 잔존 오탐 2건이 여기
               '된장국', '미역국', '콩나물국', '국수', '순대국밥',
               '탕수육', '설탕', '사탕', '탕평채', '쌀밥', '',
               # ★ food30 22종에 없는 탕 — 엔진이 뭘 검출했든 교체하면 안 된다
               '대구탕', '동태탕', '복지리탕', '조개탕', '홍합탕', '우거지탕', '어묵탕',
               # ★ food30 에는 '뼈해장국' 하나뿐. 다른 해장국은 다른 음식이다
               '콩나물해장국', '선지해장국', '황태해장국', '해장국', '양평해장국',
               # 한약·음료 계열
               '쌍화탕', '생강탕', '십전대보탕']

    def test_rice(self):
        for n in self.RICE_YES:
            self.assertTrue(fa._f30_is_rice(n), f"밥으로 봐야 함: {n}")
        for n in self.RICE_NO:
            self.assertFalse(fa._f30_is_rice(n), f"밥이 아니어야 함: {n}")

    def test_soup(self):
        for n in self.SOUP_YES:
            self.assertTrue(fa._f30_is_soup(n), f"탕으로 봐야 함: {n}")
        for n in self.SOUP_NO:
            self.assertFalse(fa._f30_is_soup(n), f"탕이 아니어야 함: {n}")

    def test_allowlist_covers_all_30_classes(self):
        """30종 전부가 자기 이름으로는 반드시 매칭돼야 한다 (클래스 추가 시 누락 방지)."""
        for i, n in enumerate(fa.FOOD30_CLASS_NAMES):
            if i < 8:
                self.assertTrue(fa._f30_is_rice(n), f"밥류 누락: {n}")
            else:
                self.assertTrue(fa._f30_is_soup(n), f"탕류 누락: {n}")
        for key in fa.FOOD30_DB_KEY.values():
            if key is None:
                continue
            self.assertTrue(fa._f30_is_rice(key) or fa._f30_is_soup(key),
                            f"DB 키가 허용목록에 없다: {key}")

    def test_quantity_and_size_suffixes(self):
        """GPT-4o 가 붙이는 수량·크기 표현을 벗겨 낸다."""
        for n in ['쌀밥 (210g)', '쌀밥 210g', '쌀밥 1공기', '현미밥 (공기)',
                  '쌀밥(대)', '흰쌀밥 한 공기', '보리밥 1인분']:
            self.assertTrue(fa._f30_is_rice(n), f"밥으로 봐야 함: {n!r}")
        for n in ['갈비탕 (대)', '갈비탕 500g', '설렁탕 1인분', '곰탕 한 그릇',
                  '육개장(중)']:
            self.assertTrue(fa._f30_is_soup(n), f"탕으로 봐야 함: {n!r}")

    def test_norm_is_type_safe(self):
        for bad in [None, 123, [], {}]:
            self.assertEqual(fa._f30_norm(bad), '')
            self.assertFalse(fa._f30_is_rice(bad))
            self.assertFalse(fa._f30_is_soup(bad))

    def test_jjigae_never_replaced(self):
        """v4 잔존 오탐 2건 — 김치찌개/순두부찌개가 닭볶음탕으로 바뀌면 안 된다."""
        a = _mk('김치찌개')
        fa.apply_food30_override(a, {'soup': {'class': '닭볶음탕', 'confidence': 0.93}})
        self.assertEqual(_names(a), ['김치찌개'])
        self.assertEqual(a['food30_engine']['applied'], [])
        self.assertEqual(len(a['food30_engine']['disagreement']), 1)


# ══════════════════════════════════════════════════════════════════
class TestOverridePolicy(unittest.TestCase):
    """교체 정책 — 제이 확정 2026-08-16"""

    def test_rice_and_soup_both_replaced(self):
        a = _mk('쌀밥', '갈비탕')
        fa.apply_food30_override(a, {
            'rice': {'class': '현미밥', 'confidence': 0.91},
            'soup': {'class': '곰탕', 'confidence': 0.85},
        })
        self.assertEqual(_names(a), ['현미밥', '곰탕'])
        self.assertEqual(len(a['food30_engine']['applied']), 2)
        self.assertTrue(all(f['name_source'] == 'food30_v4' for f in a['foods']))

    def test_one_per_category(self):
        """밥 항목이 둘이어도 한 건만 교체한다."""
        a = _mk('쌀밥', '보리밥')
        fa.apply_food30_override(a, {'rice': {'class': '현미밥', 'confidence': 0.9}})
        self.assertEqual(_names(a), ['현미밥', '보리밥'])

    def test_no_double_claim(self):
        """rice 가 이미 잡은 항목을 soup 가 다시 잡지 않는다."""
        a = _mk('쌀밥')
        fa.apply_food30_override(a, {
            'rice': {'class': '현미밥', 'confidence': 0.9},
            'soup': {'class': '갈비탕', 'confidence': 0.8},
        })
        self.assertEqual(_names(a), ['현미밥'])
        self.assertEqual(len(a['food30_engine']['disagreement']), 1)

    def test_preemption_guard_actually_fires(self):
        """선점 가드를 직접 도달시킨다.

        허용목록이 카테고리를 나누므로 정상 흐름에서는 도달하지 않지만,
        재호출·향후 카테고리 추가에서 살아나는 경로다. disagreement 로 잘못
        기록되지 않고 preempted 에 남아야 한다(텔레메트리가 거짓말하면 안 된다).
        """
        a = _mk('쌀밥')
        a['foods'][0]['name_source'] = 'food30_v4'
        fa.apply_food30_override(a, {'rice': {'class': '현미밥', 'confidence': 0.9}})
        info = a['food30_engine']
        self.assertEqual(_names(a), ['쌀밥'], "선점된 항목은 건드리지 않는다")
        self.assertEqual(info['applied'], [])
        self.assertEqual(info['disagreement'], [], "선점은 disagreement 가 아니다")
        self.assertEqual(len(info['preempted']), 1)

    def test_no_duplicate_names_created(self):
        """교체가 같은 이름을 둘 만들면 meal_summary 가 이중계상된다."""
        a = _mk('잡곡밥', '쌀밥')
        fa.apply_food30_override(a, {'rice': {'class': '쌀밥', 'confidence': 0.9}})
        self.assertEqual(_names(a), ['잡곡밥', '쌀밥'])
        self.assertEqual(len(set(_names(a))), 2)
        rec = a['food30_engine']['applied'][0]
        self.assertFalse(rec['changed'], "이미 정답인 항목이 있으면 아무것도 바꾸지 않는다")

    def test_no_duplicate_names_created_soup(self):
        a = _mk('설렁탕', '갈비탕')
        fa.apply_food30_override(a, {'soup': {'class': '갈비탕', 'confidence': 0.9}})
        self.assertEqual(_names(a), ['설렁탕', '갈비탕'])

    def test_malformed_foods_do_not_raise(self):
        """GPT 이상 응답에도 예외를 던지지 않는다 (텔레메트리는 남는다)."""
        cases = [
            {'foods': [{'name_ko': 123}, {'name_ko': '쌀밥'}]},
            {'foods': ['쌀밥', {'name_ko': '쌀밥'}]},
            {'foods': [{'name_ko': None}]},
            {'foods': [{'name_ko': '쌀밥', 'name_source': None}]},
            {'foods': None},
            {},
            {'error': 'API 실패'},
        ]
        for a in cases:
            fa.apply_food30_override(a, {'rice': {'class': '현미밥', 'confidence': 0.9}})
            self.assertIn('food30_engine', a)

    def test_hit_without_confidence_does_not_raise(self):
        a = _mk('쌀밥')
        fa.apply_food30_override(a, {'rice': {'class': '현미밥'}})
        self.assertEqual(_names(a), ['현미밥'])
        self.assertIsNone(a['food30_engine']['detected']['rice']['confidence'])

    def test_non_dict_analysis_returned_untouched(self):
        self.assertEqual(fa.apply_food30_override(None, {}), None)
        self.assertEqual(fa.apply_food30_override([1, 2], {}), [1, 2])

    def test_no_phantom_food_added(self):
        """IP/166 §2 — 엔진이 검출해도 GPT 응답에 없으면 추가하지 않는다."""
        a = _mk('라면', '김치')
        fa.apply_food30_override(a, {'soup': {'class': '갈비탕', 'confidence': 0.88}})
        self.assertEqual(_names(a), ['라면', '김치'])
        self.assertEqual(a['food30_engine']['disagreement'],
                         [{'slot': 'soup', 'class': '갈비탕'}])

    def test_already_correct_not_changed(self):
        a = _mk('갈비탕')
        fa.apply_food30_override(a, {'soup': {'class': '갈비탕', 'confidence': 0.9}})
        self.assertEqual(_names(a), ['갈비탕'])
        rec = a['food30_engine']['applied'][0]
        self.assertFalse(rec['changed'])
        self.assertNotIn('name_source', a['foods'][0])

    def test_db_key_used_not_class_name(self):
        """'기타잡곡밥' 이 아니라 '잡곡밥' 이 들어가야 DB 가 조회된다."""
        a = _mk('쌀밥')
        fa.apply_food30_override(a, {'rice': {'class': '기타잡곡밥', 'confidence': 0.9}})
        self.assertEqual(_names(a), ['잡곡밥'])

    def test_serving_and_other_fields_untouched(self):
        """이름만 바꾼다 — 칼로리는 match_with_db 담당."""
        a = _mk('쌀밥')
        a['foods'][0]['calories_kcal'] = 999
        fa.apply_food30_override(a, {'rice': {'class': '현미밥', 'confidence': 0.9}})
        self.assertEqual(a['foods'][0]['estimated_serving_g'], 300)
        self.assertEqual(a['foods'][0]['calories_kcal'], 999)

    def test_name_with_parenthesized_amount(self):
        a = _mk('쌀밥 (210g)')
        fa.apply_food30_override(a, {'rice': {'class': '현미밥', 'confidence': 0.9}})
        self.assertEqual(_names(a), ['현미밥'])

    def test_no_hits_is_noop(self):
        a = _mk('쌀밥', '갈비탕')
        fa.apply_food30_override(a, {'rice': None, 'soup': None})
        self.assertEqual(_names(a), ['쌀밥', '갈비탕'])
        self.assertEqual(a['food30_engine']['detected'], {})

    def test_empty_foods_is_safe(self):
        a = {'foods': []}
        fa.apply_food30_override(a, {'rice': {'class': '현미밥', 'confidence': 0.9}})
        self.assertEqual(len(a['food30_engine']['disagreement']), 1)

    def test_none_db_key_records_and_skips(self):
        """DB 근거 없는 클래스는 교체하지 않고 기록만 한다."""
        orig = fa.FOOD30_DB_KEY.copy()
        try:
            fa.FOOD30_DB_KEY['현미밥'] = None
            a = _mk('쌀밥')
            fa.apply_food30_override(a, {'rice': {'class': '현미밥', 'confidence': 0.9}})
            self.assertEqual(_names(a), ['쌀밥'])
            self.assertEqual(a['food30_engine']['no_db_key'], ['현미밥'])
        finally:
            fa.FOOD30_DB_KEY.clear()
            fa.FOOD30_DB_KEY.update(orig)

    def test_telemetry_shape(self):
        a = _mk('쌀밥')
        fa.apply_food30_override(a, {'rice': {'class': '현미밥', 'confidence': 0.912}})
        info = a['food30_engine']
        self.assertEqual(info['model'], 'food30_detection_v4')
        self.assertEqual(info['tau'], 0.70)
        self.assertEqual(info['detected']['rice']['confidence'], 0.91)


# ══════════════════════════════════════════════════════════════════
class TestSafetySwitches(unittest.TestCase):
    """롤백 수단 (IP/166 §4)"""

    def setUp(self):
        self._saved_model = fa._F30_MODEL
        self._saved_env = os.environ.get('FOOD30_ENGINE')

    def tearDown(self):
        fa._F30_MODEL = self._saved_model
        if self._saved_env is None:
            os.environ.pop('FOOD30_ENGINE', None)
        else:
            os.environ['FOOD30_ENGINE'] = self._saved_env

    def test_kill_switch_skips_inference(self):
        called = []
        fa._F30_MODEL = type('M', (), {'predict': lambda s, *a, **k: called.append(1) or []})()
        os.environ['FOOD30_ENGINE'] = '0'
        self.assertEqual(fa.detect_food30('/nonexistent.jpg'), {'rice': None, 'soup': None})
        self.assertEqual(called, [], "킬스위치가 켜졌는데 추론이 돌았다")

    def test_missing_model_file_disables_quietly(self):
        fa._F30_MODEL = None
        saved = fa._F30_MODEL_PATH
        try:
            fa._F30_MODEL_PATH = Path('/definitely/not/here.pt')
            self.assertIsNone(fa._get_food30_model())
            self.assertIs(fa._F30_MODEL, False)
            self.assertEqual(fa.detect_food30('/x.jpg'), {'rice': None, 'soup': None})
        finally:
            fa._F30_MODEL_PATH = saved

    def test_predict_failure_is_swallowed(self):
        class Boom:
            def predict(self, *a, **k):
                raise RuntimeError("CUDA 없음")
        fa._F30_MODEL = Boom()
        os.environ['FOOD30_ENGINE'] = '1'
        self.assertEqual(fa.detect_food30('/x.jpg'), {'rice': None, 'soup': None})


# ══════════════════════════════════════════════════════════════════
class TestDetectPicksBestPerCategory(unittest.TestCase):
    """detect_food30 이 카테고리별 최고 conf 1건씩 고르는가 (모델 stub)"""

    def setUp(self):
        self._saved = fa._F30_MODEL
        self._saved_env = os.environ.get('FOOD30_ENGINE')
        os.environ['FOOD30_ENGINE'] = '1'

    def tearDown(self):
        fa._F30_MODEL = self._saved
        if self._saved_env is None:
            os.environ.pop('FOOD30_ENGINE', None)
        else:
            os.environ['FOOD30_ENGINE'] = self._saved_env

    @staticmethod
    def _stub(boxes, honor_conf=False):
        """honor_conf=False 면 predict(conf=) 를 무시한다 — 사후 필터를 시험하기 위함."""
        class B:
            def __init__(self, cid, conf):
                self.cls = [cid]
                self.conf = [conf]
        class R:
            def __init__(self, bs):
                self.boxes = [B(c, f) for c, f in bs]
        class M:
            seen = {}
            def predict(self, *a, **k):
                M.seen = dict(k)
                bs = boxes
                if honor_conf and 'conf' in k:
                    bs = [(c, f) for c, f in boxes if f >= k['conf']]
                return [R(bs)]
        return M()

    def test_best_per_category(self):
        # 0 쌀밥 0.72 · 5 현미밥 0.91 · 8 갈비탕 0.80 · 10 곰탕 0.95
        fa._F30_MODEL = self._stub([(0, 0.72), (5, 0.91), (8, 0.80), (10, 0.95)])
        out = fa.detect_food30('/x.jpg')
        self.assertEqual(out['rice']['class'], '현미밥')
        self.assertAlmostEqual(out['rice']['confidence'], 0.91)
        self.assertEqual(out['soup']['class'], '곰탕')

    def test_out_of_range_class_id_ignored(self):
        fa._F30_MODEL = self._stub([(99, 0.99), (5, 0.80)])
        out = fa.detect_food30('/x.jpg')
        self.assertEqual(out['rice']['class'], '현미밥')
        self.assertIsNone(out['soup'])

    def test_whitelist_filters(self):
        saved = fa.FOOD30_WHITELIST
        try:
            fa.FOOD30_WHITELIST = set(fa.FOOD30_CLASS_NAMES) - {'현미밥'}
            fa._F30_MODEL = self._stub([(5, 0.95)])
            self.assertIsNone(fa.detect_food30('/x.jpg')['rice'])
        finally:
            fa.FOOD30_WHITELIST = saved

    def test_tau_is_passed_to_predict(self):
        m = self._stub([(5, 0.95)])
        fa._F30_MODEL = m
        fa.detect_food30('/x.jpg')
        self.assertEqual(type(m).seen.get('conf'), fa.FOOD30_CONF_TAU,
                         "predict 에 τ 를 넘겨야 한다")

    def test_tau_post_filter(self):
        """★ predict(conf=) 가 무시되더라도 τ 미만은 버려야 한다.

        이 사후 필터가 없으면 ultralytics 버전이 바뀌어 conf 인자 해석이 달라졌을 때
        τ 가 조용히 무력화되고, G1 「오탐 2건」 보장이 통째로 깨진다.
        """
        fa._F30_MODEL = self._stub([(5, 0.10), (8, 0.05)])   # τ=0.70 미만
        self.assertEqual(fa.detect_food30('/x.jpg'), {'rice': None, 'soup': None})

    def test_tau_boundary(self):
        fa._F30_MODEL = self._stub([(5, fa.FOOD30_CONF_TAU)])
        self.assertIsNotNone(fa.detect_food30('/x.jpg')['rice'], "τ 정확히 같으면 채택")
        fa._F30_MODEL = self._stub([(5, fa.FOOD30_CONF_TAU - 0.001)])
        self.assertIsNone(fa.detect_food30('/x.jpg')['rice'], "τ 미만은 기각")


# ══════════════════════════════════════════════════════════════════
class TestWiring(unittest.TestCase):
    """호출 순서 계약 — 교체는 반드시 match_with_db 앞"""

    @staticmethod
    def _handler_src(name):
        """test_server.py 에서 해당 핸들러 본문만 잘라낸다.

        파일 전체 index() 로 순서를 보면 다른 핸들러(_handle_leftover)의 같은 이름 호출을
        집어 오답이 난다 — 규칙21(같은 결함을 한 군데만 보지 않는다)의 측정판.
        """
        src = (_NUTRILENS / 'tools' / 'test_server.py').read_text(encoding='utf-8')
        i = src.index(f'def {name}(self)')
        j = src.find('\n    def ', i + 1)
        return src[i:j if j > 0 else len(src)]

    def test_analyze_handler_order(self):
        h = self._handler_src('_handle_analyze')
        i_det = h.index('detect_food30(')
        # 주석에도 같은 문자열이 있으므로 대입문으로 못박는다
        i_gpt = h.index('analysis = call_openai_vision(image_data')
        i_app = h.index('apply_food30_override(')
        i_db = h.index('match_with_db(analysis, FOODS_DB)')
        self.assertLess(i_det, i_gpt, "detect 는 GPT 호출 앞")
        self.assertLess(i_gpt, i_app, "override 는 GPT 응답 뒤")
        self.assertLess(i_app, i_db, "★ override 는 match_with_db 앞이어야 칼로리가 재계산된다")

    def test_leftover_handler_has_no_engine(self):
        """식후 사진 경로에는 엔진을 붙이지 않는다 (측정 안 한 조건 — IP/165 §8).

        나중에 붙이려면 식후 사진으로 G1·G2 를 먼저 측정하고 이 테스트를 고칠 것.
        """
        h = self._handler_src('_handle_leftover')
        self.assertNotIn('detect_food30(', h)
        self.assertNotIn('apply_food30_override(', h)

    # 이미지를 받는 핸들러 중 엔진을 «의도적으로» 붙이지 않은 것.
    # 새 이미지 경로가 생기면 이 목록에 없으므로 아래 테스트가 실패한다.
    IMAGE_HANDLER_EXEMPT = {
        '_handle_leftover',   # 식후 사진 — 측정 안 한 조건 (IP/165 §8, IP/166 v2 §3-3)
        '_handle_refcheck',   # 디버그 전용, reference 검출만
    }

    def test_every_image_handler_is_wired_or_exempt(self):
        """이미지를 읽는 핸들러가 늘면 엔진 배선 누락을 여기서 잡는다.

        ★ `call_openai_vision` 호출부만 세면 안 된다 — `_handle_leftover` 는 그 함수를
          쓰지 않고 urllib 로 직접 OpenAI 를 부른다. 같은 방식의 새 경로는 영원히 안 걸린다.
          그래서 「이미지 바이트를 읽는가」로 판정한다. (규칙21)
        """
        import re as _re
        src = (_NUTRILENS / 'tools' / 'test_server.py').read_text(encoding='utf-8')
        chunks = _re.split(r'\n    def ', src)
        missing = []
        for ch in chunks:
            name = ch.split('(')[0].strip()
            if not name.startswith('_handle'):
                continue
            reads_image = ("image_file['data']" in ch or 'image_file["data"]' in ch
                           or '_read_image_upload' in ch)
            if not reads_image:
                continue
            if name in self.IMAGE_HANDLER_EXEMPT:
                continue
            if 'detect_food30(' not in ch:
                missing.append(name)
        self.assertEqual(missing, [],
                         "이미지 핸들러에 food30 배선이 없다(또는 면제 목록에 넣어야 한다): "
                         + ', '.join(missing))

    def test_offline_cli_has_override(self):
        src = (_NUTRILENS / 'tools' / 'food_analyzer.py').read_text(encoding='utf-8')
        self.assertIn('apply_food30_override(analysis, detect_food30(image_path))', src,
                      "오프라인 CLI 경로에도 넣어야 accuracy_test 회귀가 프로덕션과 같아진다")

    def test_kill_switch_documented_in_code(self):
        src = (_NUTRILENS / 'tools' / 'food_analyzer.py').read_text(encoding='utf-8')
        self.assertIn("os.environ.get('FOOD30_ENGINE', '1') == '0'", src)


# ══════════════════════════════════════════════════════════════════
class TestEndToEndWithMatchDb(unittest.TestCase):
    """apply_food30_override → match_with_db 연결. 칼로리가 실제로 어떻게 바뀌는가.

    TestWiring 은 소스의 «순서»만 봅니다. 여기서는 두 함수를 실제로 이어 돌립니다.
    """

    @staticmethod
    def _run(foods, hits):
        a = {'foods': [dict(f) for f in foods]}
        fa.apply_food30_override(a, hits)
        fa.match_with_db(a, {})
        return ([(f['name_ko'], f.get('calories_kcal')) for f in a['foods']],
                a['food30_engine'])

    def test_rice_correction_changes_calories(self):
        """이 통합의 실질 가치 — 이름이 바뀌면 칼로리가 다시 계산된다."""
        out, info = self._run([{'name_ko': '쌀밥', 'estimated_serving_g': 210,
                                'calories_kcal': 335}],
                              {'rice': {'class': '현미밥', 'confidence': 0.91}})
        self.assertEqual(out[0][0], '현미밥')
        self.assertAlmostEqual(out[0][1], 312.9, places=1)
        self.assertTrue(info['applied'][0]['changed'])

    def test_rice_and_soup_end_to_end(self):
        out, info = self._run(
            [{'name_ko': '쌀밥 (210g)', 'estimated_serving_g': 210},
             {'name_ko': '설렁탕 1인분', 'estimated_serving_g': 500}],
            {'rice': {'class': '현미밥', 'confidence': 0.9},
             'soup': {'class': '곰탕', 'confidence': 0.85}})
        self.assertEqual([n for n, _ in out], ['현미밥', '곰탕'])
        self.assertEqual(len(info['applied']), 2)

    def test_specialty_rice_keeps_its_own_nutrition(self):
        """★ 곤드레밥이 쌀밥으로 바뀌면 나물 영양이 소실되고 칼로리가 +58 된다."""
        out, info = self._run([{'name_ko': '곤드레밥', 'estimated_serving_g': 300,
                                'calories_kcal': 420}],
                              {'rice': {'class': '쌀밥', 'confidence': 0.9}})
        self.assertEqual(out[0][0], '곤드레밥')
        self.assertEqual(info['applied'], [])
        self.assertEqual(len(info['disagreement']), 1)

    def test_fp_prone_class_never_absorbs(self):
        """★ 가드의 존재 이유. 게이트91 실측 오탐 2건이 여기서 막혀야 한다.

        김치찌개 0.929 · 순두부찌개 0.907 — 둘 다 닭볶음탕發이고, 정탐 «분포 안»에 있다
        (정탐 중앙값 0.90 · 최대 0.96). τ=0.93 으로 오탐을 죽이면 확장이 살린 29장 중
        24장이 같이 죽는다. 그래서 τ 로는 못 막고, 클래스 조건부 가드만 유효하다.
        """
        for name in ['김치찌개', '순두부찌개', '된장찌개', '부대찌개']:
            out, info = self._run([{'name_ko': name, 'estimated_serving_g': 500}],
                                  {'soup': {'class': '닭볶음탕', 'confidence': 0.93}})
            self.assertEqual(out[0][0], name, f"{name} 이 닭볶음탕으로 바뀌었다")
            self.assertEqual(info['applied'], [])
            self.assertEqual(len(info['disagreement']), 1)

    def test_soup_trap_never_absorbed(self):
        """접미사가 '탕/국'이어도 국물 한 그릇이 아닌 것은 후보가 아니다."""
        for name in ['설탕', '탕수육', '탕평채', '국수', '칼국수', '콩국수', '수제비']:
            out, info = self._run([{'name_ko': name, 'estimated_serving_g': 300}],
                                  {'soup': {'class': '매운탕', 'confidence': 0.93}})
            self.assertEqual(out[0][0], name, f"{name} 이 매운탕으로 바뀌었다")
            self.assertEqual(info['applied'], [])

    def test_widened_replacement_recovers_measured_cases(self):
        """★ 세션50 확장이 실제로 살려낸 케이스. aihub300 실측에서 그대로 뽑았다.

        (GT, GPT 가 낸 이름, 엔진 검출) — 셋 다 종전에는 disagreement 로 버려졌다.
        """
        for gt, gpt_name, conf in [('추어탕', '된장찌개', 0.95),
                                   ('추어탕', '시래기 된장국', 0.93),
                                   ('지리탕', '대구탕', 0.86),
                                   ('낙지탕', '문어탕', 0.92),
                                   ('육개장', '김치찌개', 0.88),
                                   ('매운탕', '생선찌개', 0.92),
                                   ('감자탕', '김치찌개', 0.80),
                                   ('닭곰탕', '닭고기 배추국', 0.89),
                                   ('낙지탕', '문어 전골', 0.73),
                                   ('곰탕', '파국', 0.81)]:
            out, info = self._run([{'name_ko': gpt_name, 'estimated_serving_g': 500}],
                                  {'soup': {'class': gt, 'confidence': conf}})
            self.assertEqual(out[0][0], gt, f"{gpt_name} → {gt} 교체가 안 됐다")
            self.assertEqual(len(info['applied']), 1)
            self.assertTrue(info['applied'][0]['changed'])
            self.assertTrue(info['applied'][0]['widened'],
                            "확장으로 살아난 교체인데 widened 표시가 없다")

    def test_narrow_replacement_is_not_marked_widened(self):
        """종전부터 되던 교체는 widened=False 여야 한다 — 다음 유료 실행에서
        «확장분만» 따로 채점하려면 이 구분이 정확해야 한다."""
        out, info = self._run([{'name_ko': '설렁탕', 'estimated_serving_g': 500}],
                              {'soup': {'class': '곰탕', 'confidence': 0.9}})
        self.assertEqual(out[0][0], '곰탕')
        self.assertFalse(info['applied'][0]['widened'])

    def test_rice_is_not_widened(self):
        """밥류 확장은 «의도적으로» 안 한다 — 비빔밥↔돌솥밥 동전던지기(규칙56).

        게이트91 실측: 비빔밥 사진에 엔진이 돌솥밥 0.862. 확장하면 여기서 잃는다.
        """
        out, info = self._run([{'name_ko': '비빔밥', 'estimated_serving_g': 400}],
                              {'rice': {'class': '돌솥밥', 'confidence': 0.862}})
        self.assertEqual(out[0][0], '비빔밥')
        self.assertEqual(info['applied'], [])
        self.assertEqual(len(info['disagreement']), 1)

    def test_engine_silent_changes_nothing(self):
        """확장은 «엔진이 검출했을 때»만 작동한다. 침묵이면 GPT 이름 그대로."""
        for name in ['대구탕', '콩나물해장국', '미역국', '김치찌개']:
            out, info = self._run([{'name_ko': name, 'estimated_serving_g': 500}], {})
            self.assertEqual(out[0][0], name)
            self.assertEqual(info['applied'], [])
            self.assertEqual(info['disagreement'], [])

    def test_no_double_counting_in_summary(self):
        a = {'foods': [{'name_ko': '잡곡밥', 'estimated_serving_g': 210},
                       {'name_ko': '쌀밥', 'estimated_serving_g': 210}]}
        fa.apply_food30_override(a, {'rice': {'class': '쌀밥', 'confidence': 0.9}})
        fa.match_with_db(a, {})
        names = [f['name_ko'] for f in a['foods']]
        self.assertEqual(len(set(names)), 2, f"같은 이름이 둘 생겼다: {names}")


# ══════════════════════════════════════════════════════════════════
# 구체성 등급(Specificity Rank) — IP/176 §4-1 평가 셋 20케이스
# 세션48(2026-08-24) 신설. ★ 구현보다 **먼저** 들어왔습니다 (원칙4 Eval-First).
# ══════════════════════════════════════════════════════════════════
#
# 무엇을 지키려는가
# ─────────────────────────────────────────────────────────────────
# 엔진이 `기타잡곡밥`(→DB '잡곡밥')을 τ 이상으로 낼 때, GPT 가 이미
# **현미밥**이라고 맞혔다면 교체는 정답을 오답으로 바꾸는 것입니다.
# `applied changed=True` 로 「엔진이 기여함」으로 집계되기까지 합니다.
#
# 지금 안 터지는 이유는 설계가 막아서가 아니라 **τ 가 우연히 그 위에 있어서**입니다.
# IP/174 §1-2 실측: 흑미밥2 는 기타잡곡밥 **0.690** — τ=0.70 에 0.010 부족.
# τ 를 0.68 로만 내려도 「흑미밥 → 잡곡밥」이 실제로 뒤집힙니다.
#
# ★ expectedFailure 를 쓰는 이유 (읽고 지나가지 마십시오)
# ─────────────────────────────────────────────────────────────────
# 구현 전이므로 신규 규칙 케이스는 **반드시 FAIL 해야 합니다.**
# 실패하지 않는 테스트는 아무것도 증명하지 않습니다(규칙30).
# 그렇다고 생으로 FAIL 시키면 STEP 1 회귀가 깨져 배포 게이트를 막습니다.
#
# → `@unittest.expectedFailure` 로 둡니다. 그러면:
#     구현 전 : expected failure  → 스위트는 OK. 게이트를 막지 않음
#     구현 후 : UNEXPECTED SUCCESS → **스위트가 실패**. 데코레이터를 지우라는 신호
#   즉 이 테스트들은 구현이 되는 순간 스스로 손을 듭니다. 잊혀지지 않습니다.
#
# ⛔ 구현했다면 아래 데코레이터를 **전부 지우십시오.** 남겨 두면
#   「통과했는데 실패로 보이는」 상태가 되고, 다음 사람이 게이트를 불신하게 됩니다.
#
# ⛔ τ 를 내리면서 이 규칙을 같이 넣지 마십시오(IP/176 §5-1).
#   두 변경을 한 번에 하면 어느 쪽이 무엇을 했는지 분리되지 않습니다.

def _kept(analysis):
    """specificity_kept 텔레메트리. 구현 전에는 키가 없다."""
    return (analysis.get('food30_engine') or {}).get('specificity_kept', [])


# ══════════════════════════════════════════════════════════════════
# 축 A 런타임 게이트 — 세션48(2026-08-24) 제이 확정으로 신설
# ══════════════════════════════════════════════════════════════════
class TestAxisARuntimeGate(unittest.TestCase):
    """프로덕션 전송 직전에 「원본 미전송」이 실제로 보증되는지.

    왜 있는가
    ─────────────────────────────────────────────────────────────
    IP/174 §4-2 는 「outbound wrapper 가 original_frame_sent 와
    crop_bounds_area_ratio 를 검증한다」고 적었지만, 세션48이 전수 확인한 결과
    **런타임에는 그 검증이 없었습니다.** assert 는 eval/ 아래 오프라인
    스크립트 2개에만 있었고, 그건 배포된 코드를 지키지 못합니다.

    세션48이 `call_openai_vision` 에 fail-closed 게이트를 넣었습니다.
    이 테스트는 **그 게이트가 지워지거나 약해지는 것**을 잡습니다.

    소스 문자열 검사인 이유: test_server 를 import 하면 음식 DB 238,054종을
    로드해 STEP 1 회귀가 몇 초씩 느려집니다. 실제 동작(위반 6종 차단 · 정상 통과)은
    2026-08-24 에 실측했고 IP/175 §7-B 에 기록돼 있습니다.
    """

    @classmethod
    def setUpClass(cls):
        src = (_NUTRILENS / 'tools' / 'test_server.py').read_text(
            encoding='utf-8', errors='replace')
        lines = src.splitlines()
        start = next((n for n, ln in enumerate(lines)
                      if ln.startswith('def call_openai_vision')), None)
        assert start is not None, 'call_openai_vision 을 찾지 못했다'
        # 함수 끝 = **들여쓰기 0 인 비어있지 않은 첫 줄.**
        #   초판1: `\ndef ` 로 찾다 못 찾으면 파일 끝까지 → 전체가 검사 대상
        #   초판2: 「다음 최상위 def/class」 → test_server.py 는 이 함수 뒤로
        #          2,600줄 동안 최상위 def/class 가 없다(HTML 문자열·상수 구간).
        #   실측(2026-08-24): 함수는 988행, 다음 최상위 줄은 1105행. 117줄.
        end = next((n for n in range(start + 1, len(lines))
                    if lines[n].strip() and not lines[n].startswith((' ', '\t'))),
                   len(lines))
        cls.lines = lines[start:end]
        cls.fn = '\n'.join(cls.lines)
        # 주석을 걷어낸 «실행되는 코드»만. 주석의 0.90 을 하드코딩으로 오인하지 않게.
        cls.code = '\n'.join(ln.split('#', 1)[0] for ln in cls.lines)

    def test_gate_exists_in_call_openai_vision(self):
        for token, why in [
            ('original_frame_sent', '원본 프레임 전송 여부를 확인하지 않는다'),
            ('crop_bounds_area_ratio', '크롭이 실제로 걸렸는지 확인하지 않는다'),
            ('AREA_RATIO_MAX', '면적비 한계를 모듈 상수에서 가져오지 않는다'),
        ]:
            self.assertIn(token, self.fn, f"축A 게이트가 사라졌다 — {why}")

    def test_gate_is_fail_closed(self):
        """위반 시 전송을 **중단**해야 한다. 경고만 찍고 보내면 통제가 아니다."""
        self.assertIn('raise RuntimeError', self.fn,
                      "축A 위반이 예외가 아니다 — 경고만 하고 전송하면 통제가 아니다")
        self.assertGreaterEqual(self.fn.count('raise RuntimeError'), 3,
                                "축A 검사 3종(원본·크롭·detail) 중 일부가 예외를 던지지 않는다")

    def test_gate_precedes_payload(self):
        """게이트가 payload 조립보다 앞에 있어야 한다.

        뒤에 있으면 「막았지만 이미 만들어진」 상태가 되고,
        나중에 누가 순서를 바꿀 때 조용히 무력화된다.
        """
        g = self.fn.find('original_frame_sent')
        p = self.fn.find('payload = {')
        self.assertGreater(g, 0, '게이트를 찾지 못했다')
        self.assertGreater(p, 0, 'payload 조립을 찾지 못했다')
        self.assertLess(g, p, '축A 게이트가 payload 조립보다 뒤에 있다')

    def test_area_ratio_max_is_not_hardcoded(self):
        """0.90 을 하드코딩하면 image_minimize 가 바뀔 때 조용히 어긋난다.

        주석은 검사에서 뺀다 — 설명문의 0.90 은 하드코딩이 아니다.
        실패 메시지에 함수 전체를 덤프하지 않는다(초판이 그래서 출력이 19만 자였다).
        """
        bad = [n for n, ln in enumerate(self.code.splitlines())
               if '0.90' in ln or '0.9 ' in ln.replace('AREA_RATIO_MAX', '')]
        self.assertEqual(bad, [],
                         f'면적비 한계가 하드코딩된 줄 {bad} — AREA_RATIO_MAX 를 쓰라')

    def test_function_slice_is_sane(self):
        """이 클래스의 검사들이 «함수 하나»를 보고 있는지.

        초판은 슬라이싱이 빗나가 test_server.py 전체(4천여 줄)를 검사 대상으로
        삼았고, 그래서 무관한 곳의 문자열에 걸려 FAIL 했습니다.
        검사기 자신이 멀쩡한지 먼저 확인합니다.
        """
        self.assertLess(len(self.lines), 200,
                        f'함수 슬라이스가 {len(self.lines)}줄 — 범위가 빗나갔다')
        self.assertTrue(self.lines[0].startswith('def call_openai_vision'))
        self.assertIn('payload = {', self.fn)


class TestSpecificityRankCurrent(unittest.TestCase):
    """구현 전에도 **반드시 통과해야 하는** 현행 동작 (회귀 방어).

    구체성 등급을 넣을 때 이 8건이 깨지면 그 구현은 틀렸습니다.
    변경 범위는 「엔진 rank1 + GPT covers 안」 한 줄뿐이어야 합니다.
    """

    # 케이스 1
    def test_c01_exact_match_not_changed(self):
        a = _mk('잡곡밥')
        fa.apply_food30_override(a, {'rice': {'class': '기타잡곡밥', 'confidence': 0.9}})
        self.assertEqual(_names(a), ['잡곡밥'])
        ap = a['food30_engine']['applied']
        self.assertEqual(len(ap), 1)
        self.assertFalse(ap[0]['changed'])

    # 케이스 2
    def test_c02_self_exact(self):
        a = _mk('쌀밥')
        fa.apply_food30_override(a, {'rice': {'class': '쌀밥', 'confidence': 0.95}})
        self.assertEqual(_names(a), ['쌀밥'])
        self.assertFalse(a['food30_engine']['applied'][0]['changed'])

    # 케이스 3 — 일반 → 구체 = 정보 이득. 반드시 교체
    def test_c03_general_to_specific_replaces(self):
        a = _mk('잡곡밥')
        fa.apply_food30_override(a, {'rice': {'class': '현미밥', 'confidence': 0.88}})
        self.assertEqual(_names(a), ['현미밥'])
        self.assertTrue(a['food30_engine']['applied'][0]['changed'])

    # 케이스 4 — 형제 혼동 정정
    def test_c04_sibling_correction(self):
        a = _mk('쌀밥')
        fa.apply_food30_override(a, {'rice': {'class': '현미밥', 'confidence': 0.9}})
        self.assertEqual(_names(a), ['현미밥'])

    # 케이스 10 — ★ covers 밖: 쌀밥은 010110 쌀밥류로 다른 중분류. 교체해야 함
    def test_c10_ssalbap_is_not_covered_still_replaced(self):
        a = _mk('쌀밥')
        fa.apply_food30_override(a, {'rice': {'class': '기타잡곡밥', 'confidence': 0.9}})
        self.assertEqual(_names(a), ['잡곡밥'],
                         "쌀밥은 잡곡밥류 형제가 아니다 — 교체가 유지돼야 한다")
        self.assertTrue(a['food30_engine']['applied'][0]['changed'])
        self.assertEqual(_kept(a), [])

    # 케이스 11 — ★ 감자밥은 010130 채소밥류. covers 밖
    def test_c11_gamjabap_is_not_covered_still_replaced(self):
        a = _mk('감자밥')
        fa.apply_food30_override(a, {'rice': {'class': '기타잡곡밥', 'confidence': 0.9}})
        self.assertEqual(_names(a), ['잡곡밥'])
        self.assertEqual(_kept(a), [])

    # 케이스 12 — rank0 ↔ rank0 는 현행 유지
    def test_c12_rank0_to_rank0(self):
        a = _mk('현미밥')
        fa.apply_food30_override(a, {'rice': {'class': '흑미밥', 'confidence': 0.9}})
        self.assertEqual(_names(a), ['흑미밥'])

    # 케이스 13 — 허용목록 밖은 침묵 (v4 잔존 오탐 2건의 방어선)
    def test_c13_stew_stays_silent(self):
        a = _mk('김치찌개')
        fa.apply_food30_override(a, {'soup': {'class': '닭볶음탕', 'confidence': 0.93}})
        self.assertEqual(_names(a), ['김치찌개'])
        self.assertEqual(len(a['food30_engine']['disagreement']), 1)

    # 케이스 14 — 탕류 현행 동작
    def test_c14_soup_replacement_unaffected(self):
        a = _mk('갈비탕')
        fa.apply_food30_override(a, {'soup': {'class': '설렁탕', 'confidence': 0.9}})
        self.assertEqual(_names(a), ['설렁탕'])

    # 케이스 15 — 빈 응답
    def test_c15_empty_foods(self):
        a = {'foods': []}
        fa.apply_food30_override(a, {'rice': {'class': '기타잡곡밥', 'confidence': 0.9}})
        self.assertEqual(len(a['food30_engine']['disagreement']), 1)

    # 케이스 17 — 무검출
    def test_c17_no_detection(self):
        a = _mk('현미밥')
        fa.apply_food30_override(a, {'rice': None, 'soup': None})
        self.assertEqual(_names(a), ['현미밥'])
        self.assertEqual(a['food30_engine']['applied'], [])

    # 케이스 20 — exact 가 구체성보다 먼저 이긴다
    def test_c20_exact_wins_first(self):
        a = _mk('잡곡밥', '현미밥')
        fa.apply_food30_override(a, {'rice': {'class': '기타잡곡밥', 'confidence': 0.9}})
        self.assertEqual(_names(a), ['잡곡밥', '현미밥'],
                         "exact 후보가 있으면 아무것도 바꾸지 않는다")
        self.assertFalse(a['food30_engine']['applied'][0]['changed'])


class TestSpecificityRankNew(unittest.TestCase):
    """⛔ IP/176 «구체성 등급» — 2026-08-28 aihub300 실측으로 **기각**된 설계.

    ── 상태: 「보류」 아님. 「측정 기각」임 (세션49 확정 · 세션50 주석 갱신) ──
    IP/176 §3-2 첫 줄: 엔진이 rank1(기타잡곡밥)을 냈고 GPT 가 covers 안의
    rank0(현미밥·흑미밥·콩밥·보리밥·돌솥밥)을 냈으면 교체하지 않는다 — 는 설계였다.

    aihub300 300장 실측(IP/177 §15-2): 엔진이 이름을 바꾼 70건 중
      · 이 설계가 막으려던 사고(엔진이 GPT 의 구체명을 잡곡밥으로 덮음) = **0건**
      · 반대 방향(GPT 의 뭉뚱그린 '잡곡밥'을 엔진이 구체화해 정답) = **20건 개선**
        잡곡밥→콩밥 8 · →흑미밥 6 · →보리밥 4 · →돌솥밥 2
    구현하면 **2건 구하고 20건 잃는다.** IP/176 §5-4 의 자체 기준을 충족해 기각.

    ★ 그래도 테스트를 지우지 않는 이유: v5 재학습으로 엔진의 밥류 판별이 바뀌면
      이 계산도 바뀐다. 그때 다시 재는 «질문의 형태»로 남겨 둔다.
      되살릴 조건 = 위 20건이 5건 미만으로 줄어들 때.

    구현하면 이 클래스가 UNEXPECTED SUCCESS 로 스위트를 실패시킵니다.
    그때는 데코레이터를 지우는 게 아니라 **위 재측정을 먼저** 하십시오.
    """

    def _assert_kept(self, gpt_name):
        a = _mk(gpt_name)
        fa.apply_food30_override(a, {'rice': {'class': '기타잡곡밥', 'confidence': 0.72}})
        # ① 이름이 그대로여야 한다 (정답 보존)
        self.assertEqual(_names(a), [gpt_name],
                         f"{gpt_name} 이 잡곡밥으로 덮였다 — 정보 손실")
        # ② 아무것도 교체하지 않았어야 한다
        self.assertEqual(a['food30_engine']['applied'], [])
        # ③ 침묵을 텔레메트리에 남겨야 한다 (조용히 넘어가면 안 됨)
        kept = _kept(a)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]['engine'], '기타잡곡밥')
        self.assertIn(gpt_name, kept[0]['kept'])
        self.assertEqual(kept[0]['reason'], 'gpt_more_specific')
        # ④ disagreement 와 섞이면 안 된다 — 「놓쳤다」와 「양보했다」는 다른 사건
        self.assertEqual(a['food30_engine']['disagreement'], [])
        return a

    @unittest.expectedFailure
    def test_c05_hyeonmibap_kept(self):        # 케이스 5
        self._assert_kept('현미밥')

    @unittest.expectedFailure
    def test_c06_heukmibap_kept(self):         # 케이스 6
        self._assert_kept('흑미밥')

    @unittest.expectedFailure
    def test_c07_kongbap_kept(self):           # 케이스 7
        self._assert_kept('콩밥')

    @unittest.expectedFailure
    def test_c08_boribap_kept(self):           # 케이스 8
        self._assert_kept('보리밥')

    @unittest.expectedFailure
    def test_c09_dolsotbap_kept(self):         # 케이스 9
        self._assert_kept('돌솥밥')

    @unittest.expectedFailure
    def test_c16_slot_independence(self):
        """케이스 16 — 슬롯 독립성.

        밥은 양보하고, 탕은 정상 교체돼야 한다.
        한쪽 규칙이 다른 슬롯을 마비시키면 안 된다.
        """
        a = _mk('현미밥', '갈비탕')
        fa.apply_food30_override(a, {
            'rice': {'class': '기타잡곡밥', 'confidence': 0.72},
            'soup': {'class': '설렁탕', 'confidence': 0.9},
        })
        self.assertEqual(_names(a), ['현미밥', '설렁탕'])
        self.assertEqual(len(_kept(a)), 1)
        self.assertEqual(len(a['food30_engine']['applied']), 1)

    @unittest.expectedFailure
    def test_c18_multiple_specific_candidates(self):
        """케이스 18 — 후보가 둘이면 둘 다 보존하고 둘 다 기록한다."""
        a = _mk('현미밥', '흑미밥')
        fa.apply_food30_override(a, {'rice': {'class': '기타잡곡밥', 'confidence': 0.72}})
        self.assertEqual(_names(a), ['현미밥', '흑미밥'])
        kept = _kept(a)
        self.assertEqual(len(kept), 1)
        self.assertCountEqual(kept[0]['kept'], ['현미밥', '흑미밥'])

    @unittest.expectedFailure
    def test_c19_specificity_checked_before_preempted(self):
        """케이스 19 — ★ 제이 확정 2026-08-24: **구체성 검사가 먼저다.**

        상황: GPT 가 낸 '현미밥' 에 이미 name_source='food30_v4' 가 붙어 있고
              (= 다른 슬롯이 손댄 항목), 엔진이 기타잡곡밥을 냈다.

        두 검사가 모두 「교체하지 않음」을 낳지만 **남는 기록이 다르다**:
            preempted          "다른 슬롯이 이미 가져갔다"      — 기계적 사정
            specificity_kept   "GPT 가 더 구체적이라 양보했다"  — 의미적 판단

        제이 확정: 의미적 판단이 기계적 사정보다 앞선다.
        그래야 나중에 로그를 읽을 때 **왜 안 바꿨는지**가 남는다.
        preempted 로 기록되면 「슬롯 충돌이었나 보다」로 읽히고,
        구체성 규칙이 실제로 몇 번 일했는지 셀 수 없게 된다.

        → 구현 시 구체성 블록을 preempted 검사(L764~768)보다 **앞에** 두십시오.
        """
        a = {'foods': [{'name_ko': '현미밥', 'name_source': 'food30_v4',
                        'estimated_serving_g': 210}]}
        fa.apply_food30_override(a, {'rice': {'class': '기타잡곡밥', 'confidence': 0.72}})
        self.assertEqual(_names(a), ['현미밥'])
        kept = _kept(a)
        self.assertEqual(len(kept), 1, "구체성 검사가 먼저 걸려야 한다")
        self.assertEqual(kept[0]['reason'], 'gpt_more_specific')
        self.assertEqual(a['food30_engine']['preempted'], [],
                         "preempted 가 아니라 specificity_kept 로 기록돼야 한다")


class TestSpecificityRankTable(unittest.TestCase):
    """구현이 들어왔을 때 상수 테이블이 IP/176 §3-1 과 일치하는지.

    테이블이 없으면(구현 전) 통과합니다 — 이건 구현 후를 위한 계약입니다.
    """

    def test_covers_matches_aihub_taxonomy(self):
        spec = getattr(fa, '_F30_SPECIFICITY', None)
        if spec is None:
            self.skipTest('_F30_SPECIFICITY 미구현 — IP/176 §3-1')
        self.assertIn('기타잡곡밥', spec)
        e = spec['기타잡곡밥']
        self.assertEqual(e['rank'], 1)
        # AI Hub 010120 잡곡밥류의 형제. 쌀밥(010110)·감자밥(010130)은 제외.
        self.assertEqual(set(e['covers']),
                         {'콩밥', '보리밥', '돌솥밥', '현미밥', '흑미밥'})
        self.assertNotIn('쌀밥', e['covers'])
        self.assertNotIn('감자밥', e['covers'])

    def test_only_gitajapgokbap_is_rank1(self):
        """탕류에 rank1 을 임의로 넣지 않았는지 — IP/176 §5-4.

        food30 30종 중 이름에 「기타」가 있는 것은 기타잡곡밥 하나뿐이고,
        탕류 22종은 전부 AI Hub 040130 곰국/탕류의 구체 음식명이다.
        시각적 흡수는 taxonomy 와 다른 문제이고, 실측 전에는 넣지 않는다.
        """
        spec = getattr(fa, '_F30_SPECIFICITY', None)
        if spec is None:
            self.skipTest('_F30_SPECIFICITY 미구현')
        rank1 = {k for k, v in spec.items() if v.get('rank') == 1}
        self.assertEqual(rank1, {'기타잡곡밥'},
                         f"실측 없이 rank1 이 추가됐다: {rank1 - {'기타잡곡밥'}}")


class TestArgparseHelpStrings(unittest.TestCase):
    """argparse help 안의 % 가 이스케이프됐는지.

    2026-08-26 제이 PC 실측 사고
    ─────────────────────────────────────────────────────────────
    `--preprocess` 의 help 에 「59.4% 기준선」이라고 썼습니다.
    argparse 는 help 를 `help_string % params` 로 포맷하므로
    `% 기` 를 포맷 지정자로 읽고 죽습니다:

        ValueError: unsupported format character '기' (0xae30)
        ValueError: badly formed help string

    Python 3.14 부터 add_argument 시점에 검사하므로 `--help` 를
    치지 않아도 **파서 생성만으로 즉시** 터집니다. 제이가
    run-production-remeasure.bat 을 돌렸을 때 STEP 2 진입 직후
    크래시했습니다(다행히 API 호출 전이라 비용 0).

    왜 기존 테스트가 못 잡았나
    ─────────────────────────────────────────────────────────────
    argparse 는 main() 안에서만 실행됩니다. 유닛 테스트는 순수 함수와
    모듈 상수만 보므로 main() 에 닿지 않습니다. 79개가 전부 통과하는
    상태에서 실행 파일이 죽었습니다.

    → import 도 실행도 하지 않고 **소스를 AST 로 읽어** 검사합니다.
      무거운 DB 로드 없이 STEP 1 회귀에 얹을 수 있습니다.
    """

    TARGETS = [
        _NUTRILENS / 'tools' / 'accuracy_test.py',
        _NUTRILENS / 'tools' / 'food30_diagnose_attractor.py',
        _NUTRILENS / 'tools' / 'food30_diagnose_rice.py',
        _NUTRILENS / 'tools' / 'food30_sweep.py',
    ]

    @staticmethod
    def _literal(node):
        """help= 에 들어간 문자열. 암묵적 연결(인접 리터럴)도 이어붙인다."""
        import ast
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            l = TestArgparseHelpStrings._literal(node.left)
            r = TestArgparseHelpStrings._literal(node.right)
            return None if l is None or r is None else l + r
        return None

    def test_percent_is_escaped_in_help(self):
        import ast
        bad = []
        checked = 0
        for path in self.TARGETS:
            if not path.exists():
                continue
            tree = ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                if not (isinstance(fn, ast.Attribute) and fn.attr == 'add_argument'):
                    continue
                for kw in node.keywords:
                    if kw.arg != 'help':
                        continue
                    s = self._literal(kw.value)
                    if s is None:
                        continue
                    checked += 1
                    # % 뒤가 % 가 아니고 (, 나 유효 지정자도 아니면 폭탄.
                    # 한국어 문서에서는 사실상 «%% 로 쓰지 않은 것»이 전부 폭탄이다.
                    i = 0
                    while i < len(s):
                        if s[i] == '%':
                            if i + 1 < len(s) and s[i + 1] == '%':
                                i += 2
                                continue
                            bad.append((path.name, node.lineno,
                                        s[max(0, i - 12):i + 6]))
                        i += 1
        self.assertEqual(
            bad, [],
            "argparse help 안의 %가 이스케이프되지 않았다(%% 로 쓸 것). "
            f"argparse 가 파서 생성 시점에 죽는다. 문제 위치: {bad}")
        self.assertGreater(checked, 0, 'help 문자열을 하나도 검사하지 못했다 — 검사기가 고장')

    def test_parser_actually_builds(self):
        """AST 검사를 통과해도 실제로 만들어지는지 확인한다.

        accuracy_test 를 import 하면 음식 DB 23만종을 로드해 느리므로,
        add_argument 호출부만 떼어내 같은 인자로 재현합니다.
        """
        import ast, argparse
        src = (_NUTRILENS / 'tools' / 'accuracy_test.py').read_text(encoding='utf-8')
        tree = ast.parse(src)
        calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute)
                 and n.func.attr == 'add_argument']
        self.assertGreaterEqual(len(calls), 4, 'add_argument 를 찾지 못했다')
        p = argparse.ArgumentParser()
        built = 0
        for c in calls:
            try:
                args = [ast.literal_eval(a) for a in c.args]
                kwargs = {kw.arg: ast.literal_eval(kw.value)
                          for kw in c.keywords if kw.arg}
            except (ValueError, SyntaxError):
                continue          # 리터럴이 아닌 인자는 건너뛴다
            p.add_argument(*args, **kwargs)
            built += 1
        self.assertGreater(built, 0, '재현 가능한 add_argument 가 없다')
        # help 포맷을 실제로 돌려 본다 — 여기서 죽으면 실행 파일도 죽는다.
        p.format_help()


class TestNoParameterShadowing(unittest.TestCase):
    """★ 세션49 신설 — 함수 파라미터가 자기 함수 안에서 재할당되는가.

    2026-08-28 실측 사고: `run_photo_test(..., tag="")` 의 `tag` 를
    같은 함수의 사진 루프가 `tag = "✓ EXACT"` 로 덮었다. 그 결과
    결과 파일이 `photo_test_results_✗ MISS.json` 으로 저장되고,
    `.bat` 이 약속한 `photo_test_results_run2.json` 은 생기지 않았다.
    화면에는 "DONE. Saved: ..." 가 그대로 찍힌다.

    세션48의 `out = m["bytes"]` (출력 함수를 지역 변수가 가림)와 같은 사고이고,
    규칙46 이 「일괄 치환 후 이름 충돌을 보라」고 적어 둔 유형이다.
    문법 오류가 아니므로 py_compile 도, 기존 81건도 잡지 못했다.

    ⚠ 이 검사는 «파라미터로 받은 값을 함수 안에서 다시 대입하지 않는다»는
      규율이다. 정당한 재대입(기본값 보정 등)이 필요하면 다른 이름을 쓰라 —
      그게 읽는 사람에게도 안전하다.
    """

    # 검사 대상: (파일, 함수명). 값이 파일명·경로가 되는 함수를 우선한다.
    TARGETS = [
        ('accuracy_test.py', 'run_photo_test'),
    ]

    def _params_and_assigns(self, path, func_name):
        import ast as _ast
        src = (_NUTRILENS / 'tools' / path).read_text(encoding='utf-8')
        tree = _ast.parse(src)
        fns = [n for n in _ast.walk(tree)
               if isinstance(n, _ast.FunctionDef) and n.name == func_name]
        self.assertEqual(len(fns), 1,
                         f'{path} 에서 {func_name} 을 정확히 하나 찾지 못했다')
        fn = fns[0]
        a = fn.args
        params = {x.arg for x in list(a.args) + list(a.kwonlyargs) + list(a.posonlyargs)}
        if a.vararg:
            params.add(a.vararg.arg)
        if a.kwarg:
            params.add(a.kwarg.arg)
        assigned = set()
        for n in _ast.walk(fn):
            if isinstance(n, _ast.Assign):
                for t in n.targets:
                    for x in _ast.walk(t):
                        if isinstance(x, _ast.Name) and isinstance(x.ctx, _ast.Store):
                            assigned.add(x.id)
            elif isinstance(n, (_ast.AugAssign, _ast.AnnAssign)):
                if isinstance(n.target, _ast.Name):
                    assigned.add(n.target.id)
            elif isinstance(n, _ast.For):
                for x in _ast.walk(n.target):
                    if isinstance(x, _ast.Name):
                        assigned.add(x.id)
        return params, assigned

    def test_run_photo_test_params_not_shadowed(self):
        for path, func in self.TARGETS:
            with self.subTest(func=func):
                params, assigned = self._params_and_assigns(path, func)
                clash = sorted(params & assigned)
                self.assertEqual(
                    clash, [],
                    f'{func}() 의 파라미터 {clash} 가 함수 안에서 재할당된다. '
                    '파일명·조건이 조용히 바뀐다 — 다른 이름을 쓰라.')

    def test_detector_itself_is_sane(self):
        """★ 검사기가 멀쩡한지 먼저 본다(규칙50).

        위 검사가 «아무것도 안 보고 통과»하는 상태면 의미가 없다.
        파라미터를 실제로 찾았는지, 그리고 일부러 만든 충돌을 잡는지 확인한다.
        """
        params, assigned = self._params_and_assigns('accuracy_test.py',
                                                    'run_photo_test')
        # 파라미터를 실제로 읽었는가
        self.assertIn('run_tag', params)
        self.assertIn('photo_set', params)
        # 함수 안에 대입문이 실제로 있는가 (파싱 범위가 비어 있지 않다)
        self.assertGreater(len(assigned), 10)
        # ★ 루프 변수 tag 는 여전히 존재한다 — 그게 이 검사가 감시하는 이름이다.
        self.assertIn('tag', assigned)
        # 반증: 파라미터 이름을 tag 로 되돌리면 충돌이 잡혀야 한다.
        self.assertTrue({'tag'} & assigned,
                        '루프 변수 tag 가 사라졌다면 이 회귀 테스트의 전제가 바뀐 것이다')


class TestLoadEnvEncoding(unittest.TestCase):
    """★ 세션49 신설 — load_env() 가 .env 를 UTF-8 로 읽는가.

    2026-08-28 실측 사고: `run-variance-3x.bat` STEP 1 이 시작하자마자
    `UnicodeDecodeError: 'cp949' codec can't decode byte 0xec in position 567`
    로 죽었습니다. `.env` 8행에 UTF-8 한글 주석
    (`# --- 세션42 20260801_101838: ...`, 2026-08-01 작성)이 있는데
    `open(p)` 가 Windows 기본 인코딩(cp949)으로 읽으려 했기 때문입니다.

    **모듈 최상위에서 load_env() 를 호출하므로 import 단계에서 죽습니다.**
    즉 그 파일을 쓰는 모든 도구가 한꺼번에 멈춥니다. 실제로 7개 파일 중
    6개가 같은 코드를 복사해 갖고 있었고, `bap_gate_eval.py` 하나만
    이미 encoding 을 지정하고 있었습니다 — 누군가 전에 같은 일을 겪었다는 뜻입니다.

    ⚠ 이 사고가 무서운 이유: 같은 날 13:00·13:51 실행은 **성공**했습니다.
      코드도 .env 도 그대로였습니다. 환경(기본 인코딩)이 바뀌면 터지는
      **잠복 버그**였고, 언제 터질지 예측할 수 없었습니다.
    """

    ENV_READERS = [
        'accuracy_test.py', 'food_analyzer.py', 'test_server.py',
        'pre_deploy_test.py', 'fetch_mfds_data.py', 'mfds_importer.py',
        'bap_gate_eval.py',
    ]

    def _open_calls_in_load_env(self, path):
        import ast as _ast
        f = _NUTRILENS / 'tools' / path
        if not f.exists():
            return None
        tree = _ast.parse(f.read_text(encoding='utf-8'))
        fns = [n for n in _ast.walk(tree)
               if isinstance(n, _ast.FunctionDef) and n.name == 'load_env']
        if not fns:
            return None
        return [n for n in _ast.walk(fns[0])
                if isinstance(n, _ast.Call)
                and isinstance(n.func, _ast.Name) and n.func.id == 'open']

    def test_load_env_opens_utf8(self):
        checked = 0
        for path in self.ENV_READERS:
            calls = self._open_calls_in_load_env(path)
            if calls is None:
                continue
            checked += 1
            for c in calls:
                kw = {k.arg: k.value for k in c.keywords}
                with self.subTest(file=path, line=c.lineno):
                    self.assertIn(
                        'encoding', kw,
                        f'{path}:{c.lineno} load_env() 의 open() 에 encoding 이 없다. '
                        'Windows 에서 .env 에 한글이 있으면 import 단계에서 죽는다.')
                    v = kw['encoding']
                    self.assertTrue(
                        isinstance(v, ast.Constant)
                        and str(v.value).lower().replace('-', '') == 'utf8',
                        f"{path}:{c.lineno} encoding 이 utf-8 이 아니다")
        # ★ 검사기 자기점검(규칙50): 대상 파일을 실제로 찾았는가.
        self.assertGreaterEqual(
            checked, 5,
            f'load_env 를 가진 파일을 {checked}개밖에 못 찾았다 — '
            '경로나 함수명이 바뀌었다면 이 검사는 아무것도 지키지 않는다')


if __name__ == '__main__':
    unittest.main(verbosity=2)
