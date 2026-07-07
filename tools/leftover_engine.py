"""
정찬+잔반 엔진 — 순수 결정론 산술(Path A) + 식후사진 AI 추정(Path B).

계약: IP/통합앱_P1/27_정찬_잔반_반영_설계계약_v2.1.1_LOCK_FINAL.md (+ v2 3절 Path B)
레퍼런스: IP/통합앱_P1/eval/leftover_session_eval_v2_2.py
- Path A: 슬라이더/비율 산술. OpenAI 미호출(openai_called=false).
- Path B: AI는 '전체 섭취 비율'만 추정, 영양은 엔진이 compute_leftover(원본 x ratio)로 결정론 재계산(원칙5).
"""

from __future__ import annotations

import json as _json
import math
import urllib.error
import urllib.request
from typing import Any

NUTRIENT_KEYS = ("calories_kcal", "protein_g", "carbs_g", "fat_g", "sodium_mg")
TOL = 0.05
# Path B(식후사진 AI): confidence가 이 값 미만이면 사용자 확인을 요구한다(계약 27 R3/P1-2).
CONFIRM_THRESHOLD = 0.70


def disp(key: str, val: float) -> int | float:
    """표시 반올림 — kcal, mg=정수, g=소수1자리."""
    base = key.replace("total_", "")
    return int(round(val)) if base in {"calories_kcal", "sodium_mg"} else round(val, 1)


def approx(a: float, b: float) -> bool:
    return abs(float(a) - float(b)) <= TOL


def check_finite(x: Any) -> float:
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
        raise ValueError("not_finite")
    return float(x)


def _food_originals(food: dict[str, Any]) -> dict[str, float]:
    """pre_result food -> original 영양 dict (flat 또는 nested original 지원)."""
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
    """식전 canonical pre_result + 먹은 비율 -> 실섭취 결과 (순수 함수).

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
        display_food: dict[str, Any] = {
            "food_item_id": fid,
            "name_ko": f.get("name_ko", ""),
            "eaten_ratio": r,
            "leftover_ratio": 1.0 - r,
            "leftover_note": _leftover_note(r),
        }

        for k, ov in originals.items():
            iv = ov * r
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


# ===========================================================================
# Path B — 식후사진 AI 추정 (계약 27 v2 3절 / v2.1.1)
# 설계: AI는 '전체 섭취 비율(eaten_ratio)'과 confidence만 추정한다.
#       영양 숫자는 신뢰하지 않고 엔진이 compute_leftover(원본 x ratio)로 결정론 재계산한다.
#       (원칙5: 엔진 안에서 결과 도출, AI 추론은 비율 추정에만.)
# ===========================================================================

PHOTO_AI_PROMPT = """당신은 NutriLens의 식후 잔반(먹은 양) 추정기입니다.
입력: 식후(먹은 후) 사진 1장 + 식전에 분석된 음식 목록(참고용).
임무: 식전 대비 사용자가 실제로 섭취한 전체 비율(overall eaten_ratio)을 0.0~1.0으로 추정합니다.

## 규칙
- 영양소 숫자(칼로리/단백질 등)는 계산하지 마세요. 오직 eaten_ratio, confidence(0~1), 짧은 note만 출력.
- 전부 먹었으면 1.0, 절반 남겼으면 0.5, 거의 안 먹었으면 0.05~0.1, 손대지 않았으면 0.0.
- 빈 접시/테이블만 보이면 1.0.
- 판단이 애매하거나 사진이 흐리면 confidence를 낮게(<0.7) 주세요.
- eaten_ratio는 반드시 0.0~1.0 범위의 숫자.

## 출력: 반드시 아래 JSON만 (설명 텍스트 금지)
{"estimated_eaten_ratio": 0.65, "confidence": 0.52, "note": "약 1/3 남긴 것으로 보입니다"}
"""


def _suggested_note(applied_ratio: float) -> str:
    """확인 UI에 노출할 안내 문구."""
    leftover_pct = int(round((1.0 - applied_ratio) * 100))
    if leftover_pct <= 0:
        return "거의 다 드신 것으로 보입니다. 맞나요?"
    if leftover_pct >= 100:
        return "거의 안 드신 것으로 보입니다. 맞나요?"
    return f"약 {leftover_pct}% 남기신 것으로 보입니다. 맞나요?"


def apply_photo_ai(
    pre_result: dict[str, Any],
    *,
    estimated_eaten_ratio: Any,
    confidence: Any,
) -> dict[str, Any]:
    """AI가 추정한 (eaten_ratio, confidence) -> 결정론 재계산 결과 (순수 함수).

    - AI가 보고했을 수 있는 영양 숫자는 절대 사용하지 않는다. 영양은 compute_leftover(원본 x ratio)로만 산출.
    - confidence < CONFIRM_THRESHOLD 또는 ratio 범위이탈 -> requires_user_confirmation=true.
    - ratio 범위이탈은 [0,1]로 clamp하되(무저장 preview라 DB 오염 없음) 반드시 확인을 강제한다.
    - 엔진은 stateless: 항상 state='photo_ai_suggested', meal_log_updated=false.
      (실제 meal_log 갱신/멱등/상태전이는 Edge 책임 — 계약 P1-2.)

    Raises:
        ValueError: confidence/ratio가 비유한(not_finite)일 때.
    """
    conf = check_finite(confidence)
    raw = check_finite(estimated_eaten_ratio)
    out_of_range = raw < 0.0 or raw > 1.0
    applied = min(1.0, max(0.0, raw))
    requires = (conf < CONFIRM_THRESHOLD) or out_of_range

    base = compute_leftover(pre_result, eaten_ratio=applied)
    base["minimization"] = {"openai_called": True, "minimization_mode": "post_meal_photo"}
    base["leftover_method"] = "photo_ai"
    base["estimated_eaten_ratio"] = applied
    base["confidence"] = conf
    base["requires_user_confirmation"] = requires
    base["state"] = "photo_ai_suggested"
    base["meal_log_updated"] = False
    base["suggested_note"] = _suggested_note(applied)
    return base


def estimate_eaten_ratio_from_photo(
    after_image_b64: str,
    mime: str,
    pre_result: dict[str, Any],
    api_key: str,
    *,
    model: str = "gpt-4o",
    timeout: int = 90,
) -> dict[str, Any]:
    """식후 사진 -> {estimated_eaten_ratio, confidence, note} (OpenAI 얇은 래퍼).

    AI는 비율만 추정한다(영양 계산 금지). 결과는 apply_photo_ai로 결정론 재계산한다.
    사진 미전송/크롭 실패/파싱 실패 = fail-closed(RuntimeError).
    store=False로 OpenAI 조직 로그에 사진을 남기지 않는다(개인정보).
    """
    if not api_key:
        raise RuntimeError("openai_key_missing")
    if not after_image_b64:
        raise RuntimeError("after_image_missing")

    hint_lines = []
    for f in (pre_result.get("foods") or []):
        nm = f.get("name_ko", "?")
        src = f.get("original") if isinstance(f.get("original"), dict) else f
        kcal = src.get("calories_kcal", "?")
        hint_lines.append(f"- {nm} ({kcal}kcal)")
    hint = "\n".join(hint_lines) or "(식전 목록 없음)"
    user_text = (
        "아래는 식전에 분석된 음식 목록입니다. 식후 사진과 비교해 전체 섭취 비율을 추정하세요.\n"
        f"[식전 음식]\n{hint}"
    )

    # 서버 강제 최소화(16_ L1 crop + L2 detail:low): 원본 프레임 미전송(17_ 축 A).
    from image_minimize import minimize_to_data_url, CropFailed  # lazy(PIL 의존)
    import base64 as _b64
    try:
        _data_url, _min_meta = minimize_to_data_url(_b64.b64decode(after_image_b64))
    except CropFailed as _e:
        raise RuntimeError(f"crop_failed:{_e}")

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": PHOTO_AI_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_text},
                    {
                        "type": "image_url",
                        "image_url": {"url": _data_url, "detail": _min_meta["detail"]},
                    },
                ],
            },
        ],
        "max_tokens": 300,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "store": False,
    }
    data = _json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions", data=data, method="POST"
    )
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = _json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"openai_http_{e.code}")
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"openai_call_failed:{e}")

    if isinstance(result, dict) and result.get("error"):
        raise RuntimeError("openai_error")
    try:
        content = result["choices"][0]["message"]["content"].strip().lstrip("﻿")
        parsed = _json.loads(content)
    except Exception:  # noqa: BLE001
        raise RuntimeError("openai_parse_failed")
    if "estimated_eaten_ratio" not in parsed or "confidence" not in parsed:
        raise RuntimeError("openai_missing_fields")
    return {
        "estimated_eaten_ratio": parsed["estimated_eaten_ratio"],
        "confidence": parsed["confidence"],
        "note": parsed.get("note", ""),
        "minimization": {
            "original_frame_sent": _min_meta["original_frame_sent"],
            "crop_mode": _min_meta["crop_mode"],
            "crop_bounds_area_ratio": _min_meta["crop_bounds_area_ratio"],
            "detail": _min_meta["detail"],
        },
    }


def apply_photo_ai_from_case(case: dict[str, Any]) -> dict[str, Any]:
    """Eval jsonl photo_ai 케이스 -> apply_photo_ai 호출 (테스트 브릿지)."""
    return apply_photo_ai(
        {"foods": case["foods"]},
        estimated_eaten_ratio=case["ai_estimated_ratio"],
        confidence=case["ai_confidence"],
    )


def compute_leftover_from_case(case: dict[str, Any]) -> dict[str, Any]:
    """Eval jsonl 케이스 형식 -> compute_leftover 호출 (테스트/하네스 브릿지)."""
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
