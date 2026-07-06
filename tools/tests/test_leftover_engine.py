#!/usr/bin/env python3
"""leftover_engine 회귀 — 29_정찬_잔반_Eval셋_v2.1.1.jsonl (25/25 + 3 SKIP)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = TOOLS_DIR.parent
sys.path.insert(0, str(TOOLS_DIR))

from leftover_engine import (  # noqa: E402
    approx,
    check_finite,
    compute_leftover_from_case,
    disp,
    display_totals,
)

# eval 하네스와 동일 — leftover_engine 외 케이스 타입 검증
CONFIRM_THRESHOLD = 0.70

EVAL_JSONL = PROJECT_DIR / "IP" / "통합앱_P1" / "29_정찬_잔반_Eval셋_v2.1.1.jsonl"


def check_leftover(case):
    try:
        result = compute_leftover_from_case(case)
        display = display_totals(result)
        errs = []
    except ValueError as e:
        return [f"예상외 reject: {e}"]
    for k, exp in case.get("expect_display_totals", {}).items():
        if not approx(display.get(k, 0), exp):
            errs.append(f"display {k}: exp {exp}, got {display.get(k)}")
    if result["minimization"].get("openai_called") is not False:
        errs.append("openai_called must be false")
    return errs


def check_leftover_invalid(case):
    try:
        compute_leftover_from_case(case)
    except ValueError as e:
        return [] if str(e) == case["expect_reject"] else [f"reject 사유: exp {case['expect_reject']}, got {e}"]
    return [f"reject 되어야 함(exp {case['expect_reject']})"]


def check_readjust(case):
    errs = []
    original = case["original_summary"]
    history, latest_ratio, latest_adj, prev = [], 1.0, None, None
    for adj in case["adjustments"]:
        r = check_finite(adj["eaten_ratio"])
        adjusted = {k: original[k] * r for k in original}
        history.append({"r": r, "orig": dict(original), "prev": prev, "adj": adjusted})
        prev, latest_ratio, latest_adj = adjusted, r, adjusted
    if not approx(latest_ratio, case["expect"]["final_eaten_ratio"]):
        errs.append("final ratio")
    if len(history) != case["expect"]["history_len"]:
        errs.append("history_len")
    if not approx(disp("calories_kcal", latest_adj["calories_kcal"]), case["expect"]["final_adjusted_kcal"]):
        errs.append(f"final kcal {latest_adj['calories_kcal']}(누적보정?)")
    for h in history:
        if h["orig"] != original:
            errs.append("original 변조")
    return errs


def check_session_leftover(case):
    if case["session_status"] != "closed":
        return [] if case.get("expect_reject") == "session_still_open" else ["open인데 통과"]
    return [] if case.get("expect", {}).get("ok") else ["closed인데 실패"]


def check_idempotency(case):
    store, reject = {}, None
    key = case["idempotency_key"]
    for req in case["requests"]:
        h = req["request_hash"]
        if key in store:
            if store[key] != h:
                reject = "IDEMPOTENCY_KEY_REUSE_MISMATCH"
                break
        else:
            store[key] = h
    if "expect_reject" in case:
        return [] if reject == case["expect_reject"] else [f"reject exp {case['expect_reject']}, got {reject}"]
    if reject:
        return [f"예상외 reject {reject}"]
    return [] if len(store) == case["expect"]["history_len"] else [f"history {len(store)}"]


def check_session(case):
    internal = {}
    for it in case["items"]:
        for k, v in it["summary"].items():
            internal[k] = internal.get(k, 0) + v
    return [
        f"session {k}: exp {exp}, got {disp(k, internal.get(k, 0))}"
        for k, exp in case["expect_totals"].items()
        if not approx(disp(k, internal.get(k, 0)), exp)
    ]


def check_provenance(case):
    r, errs = case["record"], []
    if r["source"] != case["expect"]["source"] or r["source"] == "leftover":
        errs.append("source 훼손")
    if r["leftover_method"] != case["expect"]["leftover_method"]:
        errs.append("method 불일치")
    return errs


def check_confirmation(case):
    req = case["confidence"] < CONFIRM_THRESHOLD
    return [] if req == case["expect"]["requires_user_confirmation"] else [f"confirm {req}"]


def run_eval(path: Path) -> tuple[int, int, int]:
    passed = failed = skipped = 0
    fns = {
        "leftover": check_leftover,
        "leftover_invalid": check_leftover_invalid,
        "readjust": check_readjust,
        "session_leftover": check_session_leftover,
        "idempotency": check_idempotency,
        "session": check_session,
        "provenance": check_provenance,
        "confirmation": check_confirmation,
    }
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        c = json.loads(line)
        if c["type"] == "integration":
            skipped += 1
            print(f"SKIP {c['id']} (통합: {c.get('expect_reject')})")
            continue
        fn = fns.get(c["type"])
        errs = fn(c) if fn else [f"unknown type {c['type']}"]
        if errs:
            failed += 1
            print(f"FAIL {c['id']}: " + "; ".join(errs))
        else:
            passed += 1
            print(f"PASS {c['id']}")
    total = passed + failed
    print(
        f"\n결과: {passed}/{total} 통과, {skipped} SKIP(통합)",
        "✅ 100%" if failed == 0 else f"❌ {failed} 실패",
    )
    return passed, failed, skipped


def test_eval_jsonl_25_25():
    passed, failed, skipped = run_eval(EVAL_JSONL)
    assert failed == 0, f"{failed} eval cases failed"
    assert passed == 25
    assert skipped == 3


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else EVAL_JSONL
    _, failed, _ = run_eval(path)
    sys.exit(0 if failed == 0 else 1)
