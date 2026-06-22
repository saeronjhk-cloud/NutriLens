#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
분량 보정 + range 엔진 — 정확도 #1 레버 (3-AI·20표본 공통 결론: 분량 추정이 오차 주범)
근거: D:\헬스픽\IP\nutrilens_photo_calorie_20sample_v1.md, nutrilens_production_readiness_v1.md

핵심: 사진 단일추정은 부피→중량에서 2~4배 빗나감.
→ ① 사용자 1-tap 보정(적게/보통/많게/매우많게)으로 중량을 배율 조정,
   ② 단일 점추정 대신 range(low/typical/high) + 신뢰도 표시.
의존: uncertainty.nutrient_range (출처등급×음식변동×분량 → range).
"""
from uncertainty import nutrient_range

# 1-tap 분량 배율 (기본 추정 중량 대비)
PORTION_FACTOR = {"적게": 0.6, "보통": 1.0, "많게": 1.4, "매우많게": 1.8}
NUTRIENTS = ["kcal", "protein_g", "carbs_g", "fat_g", "sugar_g", "sodium_mg", "fiber_g"]

def adjust_food(per100, est_serving_g, grade, category, portion_choice=None):
    """
    음식 1건의 분량 보정 + 영양소별 range.
    portion_choice: None(기본 추정) 또는 '적게/보통/많게/매우많게'(사용자 보정).
    반환: {serving_g, corrected(bool), nutrients:{n:{point,low,high,confidence,known}}}
    """
    if portion_choice in PORTION_FACTOR:
        factor = PORTION_FACTOR[portion_choice]
        serving_g = (est_serving_g or 0) * factor
        # 사용자가 분량을 확정 → 분량 불확실성 축소
        psrc = "user_corrected"
        corrected = True
    else:
        serving_g = est_serving_g or 0
        psrc = "default"
        corrected = False

    nutrients = {}
    for n in NUTRIENTS:
        key = "sodium" if n == "sodium_mg" else n
        r = nutrient_range(per100.get(n), serving_g, grade, category, psrc, key)
        if r.get("known"):
            nutrients[n] = {"point": r["point"], "low": r["low"], "high": r["high"],
                            "confidence": r["confidence"], "known": True}
        else:
            nutrients[n] = {"known": False}
    return {"serving_g": round(serving_g), "corrected": corrected, "nutrients": nutrients}

def display_text(adjusted, nutrient="kcal"):
    """UI 표기용 문자열. 점추정 금지 → 'X kcal (A~B, 신뢰 중간)'."""
    n = adjusted["nutrients"].get(nutrient, {})
    if not n.get("known"):
        return "데이터 없음 (판단 보류)"
    unit = "kcal" if nutrient == "kcal" else ("mg" if nutrient.endswith("_mg") else "g")
    return f"{n['point']}{unit} (약 {n['low']}~{n['high']}, 신뢰 {n['confidence']})"
