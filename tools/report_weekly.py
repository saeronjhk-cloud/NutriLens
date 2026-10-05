# -*- coding: utf-8 -*-
"""
주간 리포트 룰 엔진 (API 계약 v1 §5-2) — stateless 순수 계산.
판정 기준의 원전은 IP/통합앱_P1/eval/06_주간리포트_룰_스냅샷_v1.json (Eval-First).
- avg = 일평균(주간 합계 / 기록일수)
- flags: sodium/sugar(max 초과=over, ≥1.25x=high) · calories(>1.15x over, ≥1.35x high / <0.7x under, <0.5x high)
         · protein/fiber(min 미달=under, ≤0.6x=high)
- next_action: 최심각 1개(severity high>medium, 동률 시 sodium>sugar>calories>protein>fiber)
  → 룰 템플릿 → 가드레일 스캔 → 위반 시 안전 폴백(fallback_safe, 빈칸 금지)
- top_food_groups: tools/food_groups_v1.json (IP 07 사본, 첫 매치 승리) 상위 3(count desc, 이름 asc)
- accumulation(v2): 나트륨·당류 주간 합계·기준 초과일·값 모르는 음식 수 + 가공식품 목록·몫
  (영양공식 IP/integration/weekly_accumulation_design_v1.md D3 · 평가 A01~A14)
"""
import json
import os

_DIR = os.path.dirname(os.path.abspath(__file__))
_GROUPS_PATH = os.path.join(_DIR, "food_groups_v1.json")

SUMMARY_FIELDS = [
    "total_calories_kcal", "total_protein_g", "total_carbs_g", "total_fat_g",
    "total_sodium_mg", "total_sugar_g", "total_fiber_g",
]

TEMPLATES = {
    "sodium_over":    "이번 주는 나트륨 섭취 빈도가 잦았어요. 다음 주엔 국물 요리를 한 끼 줄여보는 건 어떨까요?",
    "sugar_over":     "당류가 목표보다 많았던 한 주였어요. 음료나 간식에서 한 번만 줄여보면 좋겠어요.",
    "calories_over":  "전체 섭취량이 목표를 넘었어요. 한 끼 양을 조금만 조절해보는 건 어떨까요?",
    "calories_under": "전체 섭취량이 목표에 못 미쳤어요. 한 끼에 반찬 하나를 더해 천천히 늘려보세요.",
    "protein_under":  "단백질이 목표에 조금 못 미쳤어요. 한 끼에 달걀이나 두부를 더해보세요.",
    "fiber_under":    "식이섬유가 부족한 한 주였어요. 나물이나 잡곡을 한 끼 더해보세요.",
    "balanced":       "이번 주는 전반적으로 균형이 잘 잡혔어요. 다음 주도 지금처럼 이어가 보세요.",
    "insufficient_data": "아직 기록이 부족해요. 사흘만 기록해보면 흐름이 보이기 시작해요.",
}
FALLBACK_SAFE = "이번 주는 일반적인 식생활 균형을 참고해 주세요."
P2_TEASER_MSG = "제품을 스캔해 등록하면 가공식품 영양이 더 정확해져요."

NUTRIENT_PRIORITY = ["sodium", "sugar", "calories", "protein", "fiber"]

_groups_cache = None


def load_food_groups(path=None):
    global _groups_cache
    if _groups_cache is None or path:
        with open(path or _GROUPS_PATH, encoding="utf-8") as f:
            _groups_cache = json.load(f)
    return _groups_cache


def classify_food(name, groups=None):
    """음식명 → 음식군. 규칙은 정의 순서대로 첫 매치 승리, 공백 제거 사본 기준."""
    g = groups or load_food_groups()
    n = str(name or "").replace(" ", "")
    if not n:
        return g["default_group"]
    for rule in g["rules"]:
        for s in rule.get("suffix", []):
            if n.endswith(s):
                return rule["group"]
        for c in rule.get("contains", []):
            if c in n:
                return rule["group"]
    return g["default_group"]


def _food_name(food):
    if isinstance(food, dict):
        return food.get("name_ko") or food.get("name_en") or ""
    return str(food)


def _daily_avg(meals):
    days = {m.get("date") for m in meals if m.get("date")}
    n_days = len(days)
    avg = {}
    for f in SUMMARY_FIELDS:
        total = sum(float((m.get("summary") or {}).get(f) or 0) for m in meals)
        avg[f] = round(total / n_days, 1) if n_days else 0
    return avg, n_days


def _compute_flags(avg, targets):
    flags = []

    def add(nutrient, direction, severity, a, ref):
        flags.append({"nutrient": nutrient, "direction": direction,
                      "severity": severity, "avg": round(a, 1), "ref": ref})

    na, ref = avg.get("total_sodium_mg"), targets.get("sodium_max_mg")
    if ref and na is not None and na > ref:
        add("sodium", "over", "high" if na >= 1.25 * ref else "medium", na, ref)
    su, ref = avg.get("total_sugar_g"), targets.get("sugar_max_g")
    if ref and su is not None and su > ref:
        add("sugar", "over", "high" if su >= 1.25 * ref else "medium", su, ref)
    cal, ref = avg.get("total_calories_kcal"), targets.get("calories_kcal")
    if ref and cal is not None:
        if cal > 1.15 * ref:
            add("calories", "over", "high" if cal >= 1.35 * ref else "medium", cal, ref)
        elif cal < 0.7 * ref:
            add("calories", "under", "high" if cal < 0.5 * ref else "medium", cal, ref)
    pr, ref = avg.get("total_protein_g"), targets.get("protein_g")
    if ref and pr is not None and pr < ref:
        add("protein", "under", "high" if pr <= 0.6 * ref else "medium", pr, ref)
    fi, ref = avg.get("total_fiber_g"), targets.get("fiber_min_g")
    if ref and fi is not None and fi < ref:
        add("fiber", "under", "high" if fi <= 0.6 * ref else "medium", fi, ref)
    return flags


def _pick_template(flags, n_meals):
    if n_meals == 0:
        return "insufficient_data"
    if not flags:
        return "balanced"
    sev_rank = {"high": 0, "medium": 1}
    best = min(flags, key=lambda f: (sev_rank.get(f["severity"], 9),
                                     NUTRIENT_PRIORITY.index(f["nutrient"])))
    return "%s_%s" % (best["nutrient"], best["direction"])


def _guardrail_check(message):
    """가드레일 이중검사(03 §6 출력 직후). 가드레일 모듈 없으면 통과로 두되 사전 승인 템플릿만 쓴다."""
    try:
        from tools.guardrail_v1 import evaluate as _ge
    except ImportError:
        try:
            from guardrail_v1 import evaluate as _ge
        except ImportError:
            return True, None
    rc = {"purpose": "coaching", "commercial_recommendation": False,
          "evidence_level": "nutrition_db", "user_visible_disclosure_required": False,
          "allowed_cta": "none"}
    r = _ge(message, rc)
    return r["guardrail_passed"], (r["violations"][0] if r["violations"] else None)


def _top_food_groups(meals, groups=None, top_n=3):
    counts = {}
    for m in meals:
        for food in (m.get("foods") or []):
            grp = classify_food(_food_name(food), groups)
            counts[grp] = counts.get(grp, 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"name": k, "count": v} for k, v in ordered[:top_n]]


# ── 누적 v1 (weekly_accumulation_design_v1 D3) ─────────────────────────
CALC_VERSION = "weekly.v2"
PRODUCTS_TOP_N = 10
_ACC = [("sodium", "total_sodium_mg", "sodium_mg", "sodium_max_mg"),
        ("sugar", "total_sugar_g", "sugar_g", "sugar_max_g")]


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v


def _food_value(food, key):
    """음식의 영양값. 값을 모르면 None(문자열 음식·키 없음·missing_nutrients)."""
    if not isinstance(food, dict):
        return None
    if key in (food.get("missing_nutrients") or []):
        return None
    v = food.get(key)
    return float(v) if _num(v) else None


def _is_product(food):
    return isinstance(food, dict) and bool(food.get("barcode"))


def _adjust_kind(meal):
    """보정 종류 — 'none' | 'uniform' | 'per_food'. 음식별 비율은 저장되지 않으므로(R16)
    SQL 156a 와 같은 규칙으로 판정: 보정 열량 ≈ 원래 열량 × eaten_ratio (허용오차 0.5·음식수+0.5) 면 uniform.
    판정 재료가 모자라면 per_food(= 비율 계산에서 제외하는 안전한 쪽)."""
    if not meal.get("adjusted"):
        return "none"
    o = (meal.get("original_summary") or {}).get("total_calories_kcal")
    a = (meal.get("summary") or {}).get("total_calories_kcal")
    r = meal.get("eaten_ratio")
    if not (_num(o) and _num(a) and _num(r)):
        return "per_food"
    n = max(len(meal.get("foods") or []), 1)
    return "uniform" if abs(float(a) - float(o) * float(r)) <= 0.5 * n + 0.5 else "per_food"


def _accumulation(meals, targets):
    day_tot = {k: {} for k, _, _, _ in _ACC}
    week_tot = {k: 0.0 for k, _, _, _ in _ACC}
    unknown = {k: 0 for k, _, _, _ in _ACC}
    prod_part = {k: 0.0 for k, _, _, _ in _ACC}   # 가공식품 몫(실섭취 기준)
    share_den = {k: 0.0 for k, _, _, _ in _ACC}   # 비율 분모(제외 끼니 뺀 실섭취)
    excluded = 0
    pcount, pname, items = {}, {}, 0

    for m in meals:
        foods = m.get("foods") or []
        summ = m.get("summary") or {}
        has_product = any(_is_product(x) for x in foods)
        skip_share = has_product and _adjust_kind(m) == "per_food"
        if skip_share:
            excluded += 1
        for x in foods:
            if _is_product(x):
                items += 1
                bc = str(x.get("barcode"))
                pcount[bc] = pcount.get(bc, 0) + 1
                pname.setdefault(bc, _food_name(x) or "가공식품")
        for k, sk, fk, _ in _ACC:
            actual = float(summ.get(sk) or 0) if _num(summ.get(sk)) else 0.0
            week_tot[k] += actual
            d = m.get("date")
            if d:
                day_tot[k][d] = day_tot[k].get(d, 0.0) + actual
            unknown[k] += sum(1 for x in foods if _food_value(x, fk) is None)
            if skip_share:
                continue
            share_den[k] += actual
            orig = sum(_food_value(x, fk) or 0.0 for x in foods)
            prod = sum(_food_value(x, fk) or 0.0 for x in foods if _is_product(x))
            if orig > 0 and prod > 0:
                prod_part[k] += actual * prod / orig

    days = {m.get("date") for m in meals if m.get("date")}
    out = {}
    for k, _, _, tk in _ACC:
        ref = targets.get(tk)
        out[k] = {
            "week_total": round(week_tot[k], 1),
            "daily_avg": round(week_tot[k] / len(days), 1) if days else 0,
            "ref": ref,
            "days_over": sum(1 for v in day_tot[k].values() if ref and v > ref),
            "days_logged": len(days),
            "unknown_foods": unknown[k],
        }
    ordered = sorted(pcount.items(), key=lambda kv: (-kv[1], pname[kv[0]], kv[0]))
    out["processed"] = {
        "product_items": items,
        "distinct_products": len(pcount),
        "products": [{"barcode": bc, "name": pname[bc], "count": c} for bc, c in ordered[:PRODUCTS_TOP_N]],
        "sodium_share_pct": round(100.0 * prod_part["sodium"] / share_den["sodium"], 1) if share_den["sodium"] > 0 else None,
        "sugar_share_pct": round(100.0 * prod_part["sugar"] / share_den["sugar"], 1) if share_den["sugar"] > 0 else None,
        "share_excluded_meals": excluded,
    }
    return out


def compute_report(payload):
    """계약 §5-2 요청 payload → data dict(4요소 + coverage). 저장·인증 없음(stateless)."""
    meals = payload.get("meals") or []
    targets = payload.get("targets") or {}
    groups = load_food_groups()

    avg, days_logged = _daily_avg(meals)
    flags = _compute_flags(avg, targets) if meals else []  # 기록 없음 = 판정 대상 없음(0값 오탐 방지)
    template_id = _pick_template(flags, len(meals))
    message = TEMPLATES[template_id]

    passed, blocked = _guardrail_check(message)
    if passed:
        next_action = {"source": "rule", "message": message,
                       "evidence_level": "nutrition_db",
                       "guardrail_passed": True, "blocked_reason": None}
    else:
        next_action = {"source": "fallback_safe", "message": FALLBACK_SAFE,
                       "evidence_level": "nutrition_db",
                       "guardrail_passed": False, "blocked_reason": blocked}

    return {
        "top_food_groups": _top_food_groups(meals, groups),
        "macro_balance": {"avg": avg, "flags": flags},
        "next_action": next_action,
        "p2_teaser": {"show": len(meals) > 0, "message": P2_TEASER_MSG if meals else None},
        "coverage": {"days_logged": days_logged, "meals": len(meals)},
        "accumulation": _accumulation(meals, targets),
        "calc_version": CALC_VERSION,
    }
