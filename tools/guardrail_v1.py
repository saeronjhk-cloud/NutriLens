# -*- coding: utf-8 -*-
"""
가드레일 참조 구현 (v1) — 03_가드레일_recommendation_context_v1.md + 04_금지어사전_v1.json 기반.
목적: Eval 40케이스를 기계적으로 통과시키는 "판정 로직"의 참조 구현.
      실제 프로덕션(엔진/Edge)은 이 규칙을 그대로 이식하면 된다.
반환: dict(guardrail_passed: bool, violations: [str], normalized: {...})
"""
import json, re, unicodedata, os

DICT_PATH = os.path.join(os.path.dirname(__file__), "banned_dict_v1.json")

# 안전/알레르기 우선순위용 보조 맵 (계약 §5, priority_order)
ALLERGEN_KEYWORDS = {
    "peanut": ["땅콩", "peanut"],
    "milk":   ["우유", "유제품", "milk"],
    "egg":    ["계란", "달걀", "egg"],
    "shrimp": ["새우", "shrimp"],
}
SAFETY_SIGNAL_TEXT = {
    "hypoglycemia_risk": ["굶", "거르", "공복", "결식"],   # 결식 유도 문구 = 안전 위반
}

def _load_dict():
    with open(os.path.abspath(DICT_PATH), encoding="utf-8") as f:
        return json.load(f)

def normalize(text):
    """원문은 노출 유지. 매칭용 사본 3종 생성 (§2)."""
    orig = unicodedata.normalize("NFKC", text)
    no_space = re.sub(r"\s+", "", orig)
    # 특수문자/이모지 제거: 한글 완성형 + 영숫자만 남김 (초성 자모는 별도 abbrev로 처리)
    no_symbol = re.sub(r"[^0-9A-Za-z가-힣]", "", orig)
    return {"orig": orig, "no_space": no_space, "no_symbol": no_symbol}

def _hit(term, nv):
    t = unicodedata.normalize("NFKC", term)
    t_ns = re.sub(r"\s+", "", t)
    return (t in nv["orig"]) or (t_ns in nv["no_space"]) or (t_ns in nv["no_symbol"])

def evaluate(candidate_text, rc, user_context=None, D=None):
    """
    candidate_text: 소비자 노출 후보 문구
    rc: recommendation_context dict (purpose, commercial_recommendation, evidence_level,
        user_visible_disclosure_required, allowed_cta, ...)
    user_context: {allergy:[...], signal:"..."} (선택)
    """
    D = D or _load_dict()
    uc = user_context or {}
    nv = normalize(candidate_text)
    violations = []

    purpose   = rc.get("purpose")
    commercial= bool(rc.get("commercial_recommendation"))
    evidence  = rc.get("evidence_level")
    disc_req  = bool(rc.get("user_visible_disclosure_required"))
    cta       = rc.get("allowed_cta")
    is_official = (evidence == "official_claim")

    # ---- 0단계: 화이트리스트(정상 용어 보존, §3) ----
    whitelist_present = [w for w in D["whitelist_contexts"] if _hit(w, nv)]

    # ---- 질병어/축약 탐지 (화이트리스트로 excuse) ----
    disease_hits = [t for t in D["disease_terms"] if _hit(t, nv)]
    for k, v in D.get("disease_abbrev", {}).items():
        nk = unicodedata.normalize("NFKC", k)
        if (k in nv["orig"]) or (nk in nv["orig"]) or (nk in nv["no_space"]):
            disease_hits.append(v)
    # excuse: 질병어가 화이트리스트 문구의 부분문자열이면 정보 문맥으로 간주
    disease_hits = [d for d in disease_hits
                    if not any(re.sub(r"\s+","",d) in re.sub(r"\s+","",w) for w in whitelist_present)]
    has_disease = len(disease_hits) > 0

    # ---- 효능 단정 탐지 ----
    efficacy_hits = [p for p in D["efficacy_claim_patterns"] if _hit(p, nv)]
    has_efficacy = len(efficacy_hits) > 0

    # ---- G1: 상업추천 × 질병키워드 / 질병+효능 (최우선) ----
    if (has_disease and has_efficacy) or (commercial and has_disease):
        if commercial and has_disease:
            violations.append("G1_disease_in_commercial")
        else:
            violations.append("G1_disease")

    # ---- G3: 효능 단정 (공식근거 아니면 금지). 단 질병+효능은 G1로 이미 처리 ----
    if has_efficacy and not is_official and "G1_disease" not in violations and "G1_disease_in_commercial" not in violations:
        violations.append("G3_efficacy")
    # official_claim이라도 "완치/치료" 등 disease+efficacy는 G1으로 차단됨(위에서 처리)

    # ---- G2: 금지어 → 대체 필요 ----
    for be in D["banned_expressions"]:
        if _hit(be["term"], nv):
            violations.append("G2_banned")
            break

    # ---- G4: 제휴/광고 고지 의무 ----
    if disc_req:
        has_label = any(lbl in nv["orig"] for lbl in D["disclosure_labels"])
        if not has_label:
            violations.append("G4_disclosure_missing")

    # ---- 계약 파생규칙 ----
    if purpose == "affiliate" and not commercial:
        violations.append("contract_affiliate_must_be_commercial")
    allowed = D["cta_whitelist"].get(purpose, [])
    if cta is not None and cta not in allowed:
        violations.append("contract_cta_not_whitelisted")

    # ---- 우선순위: 안전/알레르기 (§5) ----
    for allergen in uc.get("allergy", []) or []:
        kws = ALLERGEN_KEYWORDS.get(allergen, [allergen])
        if any(k in nv["orig"] for k in kws):
            violations.append("priority_safety_over_affiliate")
            break
    sig = uc.get("signal")
    if sig in SAFETY_SIGNAL_TEXT:
        if any(k in nv["no_symbol"] for k in SAFETY_SIGNAL_TEXT[sig]):
            violations.append("priority_safety_over_goal")

    # 중복 제거(순서 유지)
    seen=set(); vs=[]
    for v in violations:
        if v not in seen: seen.add(v); vs.append(v)

    return {"guardrail_passed": len(vs) == 0, "violations": vs,
            "disease_hits": disease_hits, "efficacy_hits": efficacy_hits,
            "whitelist_present": whitelist_present}
