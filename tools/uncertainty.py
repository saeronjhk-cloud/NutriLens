#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
영양 불확실성(range) 모델 — 2-AI 자문 반영
근거: D:\헬스픽\IP\nutrilens_cleandb_verification_v1.md §3

핵심: 사진앱 오차의 주범은 DB 수치가 아니라 (출처 신뢰도 × 음식 변동성 × 분량 추정).
출력은 단일 point가 아니라 point + range(low/typical/high) + 신뢰도 + 사유.
미측정(NULL)은 0으로 합산하지 않고 '미상'으로 분리 → 식단 합계에 미상 포함 시 '판단보류'.
"""
import math

# 출처 등급별 상대오차 (clean DB quality_grade)
GRADE_REL = {"A": 0.10, "B": 0.20, "C": 0.35, "D": 0.50}
# 카테고리별 변동성 (general, sodium_extra) — 국물요리는 국물섭취량 탓에 나트륨 변동 큼
DISH_VAR = {
    "국 및 탕류": (0.15, 0.35), "찌개 및 전골류": (0.15, 0.35), "죽 및 스프류": (0.15, 0.30),
    "면 및 만두류": (0.12, 0.22), "볶음류": (0.12, 0.15), "조림류": (0.12, 0.25),
    "구이류": (0.10, 0.12), "찜류": (0.12, 0.18), "밥류": (0.08, 0.12),
    "김치류": (0.15, 0.25), "젓갈류": (0.20, 0.40), "장아찌·절임류": (0.18, 0.35),
    "가공식품": (0.05, 0.05),  # 표시값 기반이라 변동 작음
}
DISH_VAR_DEFAULT = (0.12, 0.15)
# 분량 추정 신뢰도(사용자 보정 여부)
PORTION_REL = {"user_corrected": 0.15, "dish_typical": 0.30, "default": 0.45}

def _combine(*rels):
    """독립 상대오차 결합(제곱합 제곱근)."""
    return math.sqrt(sum(r * r for r in rels if r))

def confidence_label(rel):
    if rel < 0.20: return "높음"
    if rel < 0.38: return "중간"
    return "낮음"

def nutrient_range(per100, serving_g, grade, category, portion_source, nutrient):
    """음식 1건의 특정 영양소 섭취량 range. per100=None이면 미상(known=False)."""
    if per100 is None or serving_g is None:
        return {"known": False, "nutrient": nutrient}
    point = per100 * serving_g / 100.0
    g = GRADE_REL.get(grade, 0.50)
    dv_gen, dv_na = DISH_VAR.get(category, DISH_VAR_DEFAULT)
    dv = dv_na if nutrient == "sodium" else dv_gen
    pr = PORTION_REL.get(portion_source, 0.45)
    rel = _combine(g, dv, pr)
    return {
        "known": True, "nutrient": nutrient,
        "point": round(point), "low": round(point * (1 - rel)), "high": round(point * (1 + rel)),
        "rel": round(rel, 2), "confidence": confidence_label(rel),
        "reason": {"source_grade": grade, "dish_variability": round(dv, 2), "portion": portion_source},
    }

def meal_nutrient(items, nutrient):
    """
    식단(items) 합계 range + 미상 처리.
    items: [{per100, serving_g, grade, category, portion_source}, ...]
    미상이 1건이라도 있으면 has_unknown=True → 추천엔진은 이 영양소 '판단보류'.
    """
    known = [nutrient_range(nutrient=nutrient, **it) for it in items]
    ok = [r for r in known if r["known"]]
    has_unknown = any(not r["known"] for r in known)
    point = sum(r["point"] for r in ok)
    # 합계 range: 개별 절대오차의 제곱합 제곱근(독립 가정)
    abs_err = math.sqrt(sum(((r["high"] - r["low"]) / 2.0) ** 2 for r in ok))
    rel = (abs_err / point) if point else 0.0
    return {
        "nutrient": nutrient, "point": round(point),
        "low": round(point - abs_err), "high": round(point + abs_err),
        "confidence": confidence_label(rel),
        "has_unknown": has_unknown,
        "engine_usable": (not has_unknown),  # 미상 포함 시 신호로 쓰지 말 것(판단보류)
        "n_known": len(ok), "n_unknown": len(known) - len(ok),
    }
