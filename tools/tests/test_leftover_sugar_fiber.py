#!/usr/bin/env python3
"""먹은 양 보정 당류·식이섬유 — 평가 E01~E06 (IP/integration/leftover_sugar_fiber_eval_v1.md).
E07 = 기존 test_leftover_engine.py(29 Eval 32/32) 회귀."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from leftover_engine import apply_photo_ai, compute_leftover  # noqa: E402


def meal():
    return {"foods": [
        {"food_item_id": "food_01", "name_ko": "김밥", "calories_kcal": 300, "protein_g": 8, "carbs_g": 50,
         "fat_g": 7, "sodium_mg": 700, "sugar_g": 4, "fiber_g": 2},
        {"food_item_id": "food_02", "name_ko": "콜라", "calories_kcal": 110, "protein_g": 0, "carbs_g": 27,
         "fat_g": 0, "sodium_mg": 10, "sugar_g": 27, "fiber_g": 0},
    ]}


def test_e01_global_half():
    r = compute_leftover(meal(), eaten_ratio=0.5)
    assert r["summary"]["sugar_g"] == 15.5
    assert r["summary"]["fiber_g"] == 1.0
    assert r["foods"][1]["sugar_g"] == 13.5
    assert r["summary"]["calories_kcal"] == 205


def test_e02_per_food():
    r = compute_leftover(meal(), per_food=[{"food_item_id": "food_01", "eaten_ratio": 1.0},
                                          {"food_item_id": "food_02", "eaten_ratio": 0.0}])
    assert r["summary"]["sugar_g"] == 4.0
    assert r["summary"]["fiber_g"] == 2.0


def test_e03_old_input_without_keys():
    m = meal()
    for f in m["foods"]:
        del f["sugar_g"]
        del f["fiber_g"]
    r = compute_leftover(m, eaten_ratio=0.5)
    assert "sugar_g" not in r["summary"] and "fiber_g" not in r["summary"]
    assert set(r["summary"]) == {"calories_kcal", "protein_g", "carbs_g", "fat_g", "sodium_mg"}


def test_e04_rounding():
    m = {"foods": [{"food_item_id": "food_01", "calories_kcal": 100, "sugar_g": 3.33}]}
    assert compute_leftover(m, eaten_ratio=0.5)["summary"]["sugar_g"] == 1.7


def test_e05_photo_ai():
    r = apply_photo_ai(meal(), estimated_eaten_ratio=0.6, confidence=0.9)
    assert r["summary"]["sugar_g"] == round(31 * 0.6, 1)


def test_e06_ratio_one():
    r = compute_leftover(meal(), eaten_ratio=1.0)
    assert r["summary"]["sugar_g"] == 31
    assert r["pre_summary"]["fiber_g"] == 2


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn(); print("PASS", name)
            except Exception as e:  # noqa: BLE001
                fails += 1; print("FAIL", name, e)
    print(f"{6 - fails}/6")
    sys.exit(1 if fails else 0)
