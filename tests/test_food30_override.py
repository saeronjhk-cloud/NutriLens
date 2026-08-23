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
  - 찌개는 탕류 교체 대상이 아니다 — v4 에 남은 오탐 2건이 김치찌개·순두부찌개(→닭볶음탕)다
  - 된장국·미역국·국수·국밥은 대상이 아니다 ('해장국'만 허용)
  - 탕수육·설탕은 대상이 아니다
  - 비빔밥·볶음밥·덮밥류는 대상이 아니다 (IP/165 §3-1 회색지대)

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

    def test_unknown_soup_not_absorbed(self):
        for name, cls in [('대구탕', '매운탕'), ('콩나물해장국', '뼈해장국'),
                          ('김치찌개', '닭볶음탕')]:
            out, info = self._run([{'name_ko': name, 'estimated_serving_g': 500}],
                                  {'soup': {'class': cls, 'confidence': 0.93}})
            self.assertEqual(out[0][0], name, f"{name} 이 {cls} 로 바뀌었다")
            self.assertEqual(info['applied'], [])

    def test_no_double_counting_in_summary(self):
        a = {'foods': [{'name_ko': '잡곡밥', 'estimated_serving_g': 210},
                       {'name_ko': '쌀밥', 'estimated_serving_g': 210}]}
        fa.apply_food30_override(a, {'rice': {'class': '쌀밥', 'confidence': 0.9}})
        fa.match_with_db(a, {})
        names = [f['name_ko'] for f in a['foods']]
        self.assertEqual(len(set(names)), 2, f"같은 이름이 둘 생겼다: {names}")


if __name__ == '__main__':
    unittest.main(verbosity=2)
