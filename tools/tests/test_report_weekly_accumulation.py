#!/usr/bin/env python3
"""주간 리포트 누적 v1 — 평가 A01~A14 (영양공식 IP/integration/weekly_accumulation_eval_v1.md).
pytest 없이도 `python3 test_report_weekly_accumulation.py` 로 실행된다."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import report_weekly as RW  # noqa: E402

T = {"calories_kcal": 1800, "protein_g": 60, "sodium_max_mg": 2000, "sugar_max_g": 50, "fiber_min_g": 25}


def s(na=0, su=0):
    return {"total_calories_kcal": 500, "total_protein_g": 20, "total_carbs_g": 60, "total_fat_g": 10,
            "total_sodium_mg": na, "total_sugar_g": su, "total_fiber_g": 5}


def f(name, na=0, su=0, **kw):
    d = {"name_ko": name, "sodium_mg": na, "sugar_g": su, "fiber_g": 1}
    d.update(kw)
    return d


def acc(meals):
    return RW.compute_report({"meals": meals, "targets": T})["accumulation"]


def test_a01_boundary_not_over():
    a = acc([{"date": "2026-09-28", "foods": [f("국", 3000)], "summary": s(3000)},
             {"date": "2026-09-29", "foods": [f("국", 1000)], "summary": s(1000)}])["sodium"]
    assert a["week_total"] == 4000 and a["daily_avg"] == 2000.0 and a["days_over"] == 1  # 3000일은 초과
    a2 = acc([{"date": "2026-09-28", "foods": [f("국", 2000)], "summary": s(2000)}])["sodium"]
    assert a2["days_over"] == 0  # 정확히 기준 = 초과 아님


def test_a02_days_over():
    a = acc([{"date": "2026-09-28", "foods": [], "summary": s(2500)},
             {"date": "2026-09-29", "foods": [], "summary": s(1500)}])["sodium"]
    assert a["days_over"] == 1 and a["days_logged"] == 2 and a["ref"] == 2000


def test_a03_day_not_meal():
    a = acc([{"date": "2026-09-28", "foods": [], "summary": s(1200)},
             {"date": "2026-09-28", "foods": [], "summary": s(1200)}])["sodium"]
    assert a["days_over"] == 1 and a["days_logged"] == 1


def test_a04_unknown_foods():
    foods = [f("밥", 0, 0), {"name_ko": "국", "sodium_mg": 800},
             f("과자", 100, 0, barcode="8801", missing_nutrients=["sugar_g"])]
    a = acc([{"date": "2026-09-28", "foods": foods, "summary": s(900, 0)}])
    assert a["sugar"]["unknown_foods"] == 2 and a["sodium"]["unknown_foods"] == 0


def test_a05_share_full():
    a = acc([{"date": "2026-09-28", "foods": [f("밥", 0), f("라면", 1800, barcode="8801")],
              "summary": s(1800)}])["processed"]
    assert a["sodium_share_pct"] == 100.0


def test_a06_share_uniform_adjusted():
    a = acc([{"date": "2026-09-28", "adjusted": True, "eaten_ratio": 0.5, "original_summary": s(2000),
              "foods": [f("국", 1000), f("과자", 1000, barcode="8801")], "summary": dict(s(1000), total_calories_kcal=250)}])
    assert a["processed"]["sodium_share_pct"] == 50.0 and a["sodium"]["week_total"] == 1000


def test_a07_per_food_excluded():
    a = acc([{"date": "2026-09-28", "adjusted": True, "eaten_ratio": 0.6, "original_summary": s(2000),
              "foods": [f("국", 1000), f("과자", 1000, barcode="8801")], "summary": dict(s(1200), total_calories_kcal=200)},
             {"date": "2026-09-29", "foods": [f("국", 500), f("라면", 500, barcode="8802")], "summary": s(1000)}])
    p = a["processed"]
    assert p["share_excluded_meals"] == 1 and p["sodium_share_pct"] == 50.0
    assert a["sodium"]["week_total"] == 2200  # 합계에는 그대로 포함


def test_a08_per_food_without_product():
    p = acc([{"date": "2026-09-28", "adjusted": True, "eaten_ratio": 0.6, "original_summary": s(1000),
              "foods": [f("국", 1000)], "summary": dict(s(600), total_calories_kcal=100)}])["processed"]
    assert p["share_excluded_meals"] == 0 and p["sodium_share_pct"] == 0.0


def test_a09_no_products():
    p = acc([{"date": "2026-09-28", "foods": [f("국", 1000)], "summary": s(1000)}])["processed"]
    assert p["product_items"] == 0 and p["products"] == [] and p["sodium_share_pct"] == 0.0


def test_a10_zero_denominator():
    p = acc([{"date": "2026-09-28", "foods": [f("물", 0)], "summary": s(0)}])["processed"]
    assert p["sodium_share_pct"] is None


def test_a11_products_order():
    foods = [f("콜라", barcode="1"), f("콜라", barcode="1"), f("과자", barcode="2"), f("밥")]
    meals = [{"date": "2026-09-28", "foods": foods, "summary": s(10)},
             {"date": "2026-09-29", "foods": [f("콜라", barcode="1")], "summary": s(10)}]
    p = acc(meals)["processed"]
    assert p["product_items"] == 4 and p["distinct_products"] == 2
    assert [(x["barcode"], x["count"]) for x in p["products"]] == [("1", 3), ("2", 1)]
    assert p["products"][0]["name"] == "콜라"


def test_a12_products_cap():
    foods = [f("제품%02d" % i, barcode=str(1000 + i)) for i in range(12)]
    p = acc([{"date": "2026-09-28", "foods": foods, "summary": s(10)}])["processed"]
    assert len(p["products"]) == 10 and p["distinct_products"] == 12


def test_a13_empty():
    a = acc([])
    assert a["sodium"]["week_total"] == 0 and a["sodium"]["days_logged"] == 0
    assert a["processed"]["products"] == [] and a["processed"]["sodium_share_pct"] is None


def test_a15_adjust_kind():
    base = {"foods": [f("a"), f("b")], "original_summary": s(), "summary": dict(s(), total_calories_kcal=250)}
    assert RW._adjust_kind(dict(base)) == "none"
    assert RW._adjust_kind(dict(base, adjusted=True, eaten_ratio=0.5)) == "uniform"   # 500×0.5=250
    assert RW._adjust_kind(dict(base, adjusted=True, eaten_ratio=0.8)) == "per_food"  # 400≠250
    assert RW._adjust_kind(dict(base, adjusted=True)) == "per_food"                   # 재료 부족 → 안전한 쪽


def test_a14_version_and_legacy_string_foods():
    d = RW.compute_report({"meals": [{"date": "2026-09-28", "foods": ["설렁탕"], "summary": s(100)}], "targets": T})
    assert d["calc_version"] == "weekly.v3"
    assert d["accumulation"]["sodium"]["unknown_foods"] == 1  # 문자열 음식 = 값 모름


if __name__ == "__main__":
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_")]
    bad = 0
    for k, fn in tests:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            bad += 1
            print("FAIL", k, repr(e))
    print("[weekly-accumulation] %d/%d" % (len(tests) - bad, len(tests)))
    sys.exit(1 if bad else 0)
