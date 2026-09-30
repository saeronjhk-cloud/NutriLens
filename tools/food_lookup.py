# -*- coding: utf-8 -*-
"""
음식 검색·확정 (식사 결과 «음식 편집» v1) — 순수 로직, 네트워크·OpenAI 없음
────────────────────────────────────────────────────────────────────────
설계: IP/integration/meal_food_edit_design_v1.md §3-1 · 평가: meal_food_edit_eval_v1.md L01~L11

원칙
  - 영양 계산은 사진 분석과 «같은 함수» match_with_db() 로만 한다(단일 규칙, L07).
    이 파일에 별도 매칭·산식을 두지 않는다.
  - 확신 매칭(GOLD_REF / GOLD_DB)이 아니면 None — 이름만 바뀌고 숫자는 AI 값인
    «라벨·숫자 불일치»를 만들지 않는다.
  - 후보는 CORE_FOODS ∪ gold 키. 같은 이름은 1개(CORE 우선 — _search_gold 와 같은 우선순위).
"""
import re

import food_analyzer as fa

MAX_QUERY_LEN = 30
DEFAULT_LIMIT = 10
MAX_LIMIT = 20
_OK_SOURCES = ('GOLD_REF', 'GOLD_DB')
_NUTRIENTS = ('calories_kcal', 'protein_g', 'carbs_g', 'fat_g', 'sugar_g', 'sodium_mg', 'fiber_g')

_INDEX = None  # [(name, compact, source_rank)]


def _compact(s):
    return re.sub(r'\s+', '', s or '')


def _index():
    """(이름, 공백제거 이름, 0=core/1=gold) — 첫 호출 때 한 번 만든다."""
    global _INDEX
    if _INDEX is not None:
        return _INDEX
    seen = set()
    out = []
    for name in fa.CORE_FOODS.keys():
        if name and name not in seen:
            seen.add(name)
            out.append((name, _compact(name), 0))
    gold = fa._load_gold_db() or {}
    for name in gold.keys():
        if name and name not in seen:
            seen.add(name)
            out.append((name, _compact(name), 1))
    _INDEX = out
    return _INDEX


def resolve_food(name, serving_g=None):
    """이름 1개 → 엔진 확정 영양(dict) 또는 None.

    serving_g: 사진 추정량(g). None 이면 엔진의 현실 1인분으로 채운다.
    """
    name = (name or '').strip()
    if not name or len(name) > 40:
        return None
    if serving_g is not None:
        try:
            serving_g = float(serving_g)
        except (TypeError, ValueError):
            return None
        if not (0 < serving_g <= 2000):
            return None

    food = {'name_ko': name, 'estimated_serving_g': serving_g}
    fa.match_with_db({'foods': [food]}, None)
    if food.get('source') not in _OK_SOURCES:
        return None

    serving = food.get('estimated_serving_g')
    if not serving:
        key, data = fa._search_gold(name)
        base = (data or {}).get('serving') or 100
        serving = fa._estimate_realistic_serving(key or name, base)

    out = {
        'name_ko': food.get('db_name') or name,
        'db_name': food.get('db_name'),
        'db_matched': True,
        'source': food.get('source'),
        'estimated_serving_g': round(float(serving)),
        'shape': food.get('shape'),
    }
    for k in _NUTRIENTS:
        v = food.get(k)
        out[k] = round(float(v), 1) if isinstance(v, (int, float)) else 0.0
    return out


def search_foods(q, limit=DEFAULT_LIMIT):
    """질의 → 후보 목록. 순위: 정확 < 시작 < 포함 · CORE 먼저 · 짧은 이름 · 가나다."""
    qc = _compact(q)
    if not qc or len(qc) > MAX_QUERY_LEN:
        return []
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT
    limit = max(1, min(MAX_LIMIT, limit))

    ranked = []
    for name, comp, src in _index():
        if comp == qc:
            tier = 0
        elif comp.startswith(qc):
            tier = 1
        elif qc in comp:
            tier = 2
        else:
            continue
        ranked.append((tier, src, len(comp), name))
    ranked.sort()

    items = []
    shown = set()
    for tier, src, _len, name in ranked:
        r = resolve_food(name, None)
        # ★ 자기 일치 가드: 고른 이름이 «그 이름 그대로» 확정돼야 보여준다.
        #   match_with_db 는 CORE 우선·복합어 뒤쪽 매칭이라 gold 키 «김치찌개 어묵» 을 CORE «어묵» 으로
        #   확정할 수 있다. 그런 후보를 보이면 사용자가 고른 것과 저장되는 것이 달라진다(평가 L12).
        if r is None or r['name_ko'] != name or name in shown:
            continue
        shown.add(name)
        items.append({
            'name_ko': r['name_ko'],
            'source': 'core' if src == 0 else 'gold',
            'match': ('exact', 'prefix', 'contains')[tier],
            'serving_g': r['estimated_serving_g'],
            'calories_kcal': r['calories_kcal'],
        })
        if len(items) >= limit:
            break
    return items
