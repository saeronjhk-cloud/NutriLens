#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
평가 L01~L12 — 식사 결과 «음식 편집» v1 엔진 쪽 (IP/integration/meal_food_edit_eval_v1.md)
실행:  python -m unittest tests/test_food_lookup.py -v   (또는 python tests/test_food_lookup.py)
"""
import copy
import sys
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
_NUTRILENS = _HERE.parent
sys.path.insert(0, str(_NUTRILENS / 'tools'))

import food_analyzer as fa  # noqa: E402
import food_lookup as fl  # noqa: E402

NUTR = ('calories_kcal', 'protein_g', 'carbs_g', 'fat_g', 'sugar_g', 'sodium_mg', 'fiber_g')
TIER = {'exact': 0, 'prefix': 1, 'contains': 2}


class TestSearch(unittest.TestCase):
    def test_L01_exact_first(self):
        self.assertEqual(fl.search_foods('김치찌개')[0]['name_ko'], '김치찌개')

    def test_L02_gukbap(self):
        items = fl.search_foods('국밥')
        self.assertEqual(items[0]['name_ko'], '국밥')
        self.assertIn('소고기국밥', [i['name_ko'] for i in items])

    def test_L03_no_duplicate_names(self):
        for q in ['소고기국밥', '김치찌개', '밥', '국']:
            names = [i['name_ko'] for i in fl.search_foods(q, 20)]
            self.assertEqual(len(names), len(set(names)), q)

    def test_L04_empty_or_too_long(self):
        self.assertEqual(fl.search_foods(''), [])
        self.assertEqual(fl.search_foods('   '), [])
        self.assertEqual(fl.search_foods('가' * 31), [])
        self.assertEqual(fl.search_foods(None), [])

    def test_L05_limit_and_rank_order(self):
        items = fl.search_foods('밥')
        self.assertLessEqual(len(items), 10)
        self.assertEqual(items[0]['name_ko'], '밥')
        tiers = [TIER[i['match']] for i in items]
        self.assertEqual(tiers, sorted(tiers))
        self.assertLessEqual(len(fl.search_foods('밥', 3)), 3)
        self.assertLessEqual(len(fl.search_foods('밥', 999)), fl.MAX_LIMIT)

    def test_L06_preview_filled(self):
        for i in fl.search_foods('찌개'):
            self.assertGreater(i['serving_g'], 0)
            self.assertGreaterEqual(i['calories_kcal'], 0)

    def test_L12_self_consistent_candidates(self):
        """보이는 후보는 «그 이름 그대로» 확정돼야 한다(고른 것 = 저장되는 것)."""
        for q in ['김치찌개', '국밥', '밥', '돈까스', '샐러드', '라면']:
            for i in fl.search_foods(q, 20):
                r = fl.resolve_food(i['name_ko'], None)
                self.assertIsNotNone(r, i)
                self.assertEqual(r['name_ko'], i['name_ko'])
                self.assertEqual(r['calories_kcal'], i['calories_kcal'])


class TestResolve(unittest.TestCase):
    def test_L07_same_rule_as_photo_analysis(self):
        for name, sv in [('김치찌개', 300), ('밥', 210), ('소고기국밥', 500), ('돈까스', 150)]:
            ref = {'name_ko': name, 'estimated_serving_g': sv}
            fa.match_with_db({'foods': [ref]}, None)
            got = fl.resolve_food(name, sv)
            self.assertIsNotNone(got, name)
            self.assertEqual(got['source'], ref['source'])
            for k in NUTR:
                self.assertAlmostEqual(got[k], round(float(ref.get(k) or 0), 1), places=1, msg=f'{name}.{k}')

    def test_L08_default_serving(self):
        r = fl.resolve_food('소고기국밥', None)
        key, data = fa._search_gold('소고기국밥')
        self.assertEqual(r['estimated_serving_g'], fa._estimate_realistic_serving(key, data.get('serving') or 100))
        self.assertTrue(r['db_matched'])
        self.assertIn(r['source'], ('GOLD_REF', 'GOLD_DB'))

    def test_L09_unknown_or_invalid(self):
        self.assertIsNone(fl.resolve_food('없는음식xyz', 200))
        self.assertIsNone(fl.resolve_food('', 200))
        self.assertIsNone(fl.resolve_food('밥', 0))
        self.assertIsNone(fl.resolve_food('밥', 2001))
        self.assertIsNone(fl.resolve_food('밥', 'abc'))

    def test_L10_no_network(self):
        with mock.patch.object(urllib.request, 'urlopen', side_effect=AssertionError('network')):
            fl.search_foods('국밥')
            fl.resolve_food('김치찌개', 300)

    def test_resolve_does_not_mutate_core(self):
        before = copy.deepcopy(fa.CORE_FOODS.get('밥'))
        fl.resolve_food('밥', 999)
        self.assertEqual(fa.CORE_FOODS.get('밥'), before)


class TestRoutes(unittest.TestCase):
    def test_L11_routes_use_engine_key(self):
        src = (_NUTRILENS / 'tools' / 'test_server.py').read_text(encoding='utf-8')
        self.assertIn("path == '/v1/food/search'", src)
        self.assertIn("path == '/v1/food/resolve'", src)
        body = src.split('def _handle_v1_food_lookup', 1)[1].split('\n    def ', 1)[0]
        self.assertIn('self._check_engine_key(', body)


if __name__ == '__main__':
    unittest.main(verbosity=2)
