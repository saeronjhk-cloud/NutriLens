"""
정찬+잔반 Path A — 순수 결정론 산술 엔진 (OpenAI/DB 금지).

계약: IP/통합앱_P1/27_정찬_잔반_반영_설계계약_v2.1.1_LOCK_FINAL.md
레퍼런스: IP/통합앱_P1/eval/leftover_session_eval_v2_1_1.py compute_leftover()
"""

from __future__ import annotations

import math
from typing import Any

NUTRIENT_KEYS = ("calories_kcal", "protein_g", "carbs_g", "fat_g", "sodium_mg")
TOL = 0.05


def disp(key: str, val: float) -> int | float:
    """표시 반올림 — kcal·mg=정수, g=소수1자리."""
    base = key.replace("total_", "")
    return int(round(val)) if base in {"calories_kcal", "sodium_mg"} else round(val, 1)


def approx(a: float, b: float) -> bool:
    return abs(float(a) - float(b)) <= TOL


def check_finite(x: Any) -> float:
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
        raise ValueError("not_finite")
    return float(x)


def _food_originals(food: dict[str, Any]) -> dict[str, float]:
    """pre_result food → original 영양 dict (flat 또는 nested original 지원)."""
    if "original" in food and isinstance(food["original"], dict):
        src = food["original"]
    else:
        src = food
    return {k: float(src[k]) for k in NUTRIENT_KEYS if k in src}


def _original_serving_g(food: dict[str, Any]) -> float | None:
    if "original_serving_g" in food:
        return float(food["original_serving_g"])
    orig = food.get("original")
    if isinstance(orig, dict) and "original_serving_g" in orig:
        return float(orig["original_serving_g"])
    return None


def _leftover_note(ratio: float) -> str:
    if ratio >= 1.0:
        return "전부 섭취"
    if ratio <= 0.0:
        return "먹지 않음"
    pct = int(round(ratio * 100))
    return f"{pct}% 섭취"


def compute_leftover(
    pre_result: dict[str, Any],
    *,
    eaten_ratio: float | None = None,
    session_eaten_ratio: float | None = None,
    per_food: list[dict[str, Any]] | None = None,
    pre_meal_session_id: str | None = None,
) -> dict[str, Any]:
    """
    식전 canonical pre_result + 먹은 비율 → 실섭취 결과 (순수 함수).

    Raises:
        ValueError: 검증 실패 또는 불변식 위반 (eval 하네스와 동일 reject 코드).
    """
    foods_list = pre_result.get("foods") or []
    foods = {f["food_item_id"]: f for f in foods_list}

    has_global = eaten_ratio is not None
    has_session = session_eaten_ratio is not None
    has_perfood = per_food is not None

    if pre_meal_session_id is not None and has_perfood:
        raise ValueError("session_perfood_forbidden")
    if has_perfood and (has_global or has_session):
        raise ValueError("mutually_exclusive")
    if has_session and has_global:
        raise ValueError("mutually_exclusive")

    ratios: dict[str, float] = {}
    if has_perfood:
        seen = [pf["food_item_id"] for pf in per_food]
        for pf in per_food:
            ratios[pf["food_item_id"]] = check_finite(pf["eaten_ratio"])
        if sorted(seen) != sorted(foods.keys()):
            raise ValueError("per_food_incomplete")
    else:
        g = check_finite(
            eaten_ratio if has_global else (session_eaten_ratio if has_session else 1.0)
        )
        ratios = {fid: g for fid in foods}

    for r in ratios.values():
        if r < 0 or r > 1:
            raise ValueError("out_of_range")

    internal_totals: dict[str, float] = {}
    pre_totals: dict[str, float] = {}
    out_foods: list[dict[str, Any]] = []
    errs: list[str] = []

    for fid, f in foods.items():
        r = ratios[fid]
        originals = _food_originals(f)
        internal_food: dict[str, float] = {}
        display_food: dict[str, Any] = {
            "food_item_id": fid,
            "name_ko": f.get("name_ko", ""),
            "eaten_ratio": r,
            "leftover_ratio": 1.0 - r,
            "leftover_note": _leftover_note(r),
        }

        for k, ov in originals.items():
            iv = ov * r
            internal_food[k] = iv
            internal_totals[k] = internal_totals.get(k, 0.0) + iv
            pre_totals[k] = pre_totals.get(k, 0.0) + ov
            display_food[k] = disp(k, iv)
            if iv - ov > TOL:
                errs.append(f"{fid}.{k} internal>original")
            if r == 0.0 and abs(iv) > TOL:
                errs.append(f"{fid}.{k} ratio0!=0")
            if r == 1.0 and not approx(iv, ov):
                errs.append(f"{fid}.{k} ratio1!=orig")

        orig_serving = _original_serving_g(f)
        if orig_serving is not None:
            internal_serving = orig_serving * r
            display_food["original_serving_g"] = orig_serving
            display_food["estimated_serving_g"] = disp("protein_g", internal_serving)

        out_foods.append(display_food)

    for k in internal_totals:
        if internal_totals[k] - pre_totals[k] > TOL:
            errs.append(f"adjusted>original@{k}")

    if errs:
        raise ValueError("; ".join(errs))

    summary = {k: disp(k, v) for k, v in internal_totals.items()}
    pre_summary = {k: disp(k, v) for k, v in pre_totals.items()}

    return {
        "foods": out_foods,
        "summary": summary,
        "pre_summary": pre_summary,
        "minimization": {
            "openai_called": False,
            "minimization_mode": "no_external_image",
        },
    }


def compute_leftover_from_case(case: dict[str, Any]) -> dict[str, Any]:
    """Eval jsonl 케이스 형식 → compute_leftover 호출 (테스트·하네스 브릿지)."""
    pre_result = {"foods": case["foods"]}
    kwargs: dict[str, Any] = {}
    if "eaten_ratio" in case:
        kwargs["eaten_ratio"] = case["eaten_ratio"]
    if "session_eaten_ratio" in case:
        kwargs["session_eaten_ratio"] = case["session_eaten_ratio"]
    if "per_food" in case:
        kwargs["per_food"] = case["per_food"]
    if "pre_meal_session_id" in case:
        kwargs["pre_meal_session_id"] = case["pre_meal_session_id"]
    return compute_leftover(pre_result, **kwargs)


def display_totals(result: dict[str, Any]) -> dict[str, int | float]:
    """summary를 eval expect_display_totals 키 형식으로 반환."""
    return dict(result.get("summary") or {})
