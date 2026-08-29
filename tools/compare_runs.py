#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""compare_runs.py — accuracy_test.py 실행 결과 여러 개를 놓고 «실행 간 분산»을 잰다.

이 도구가 답해야 하는 질문은 하나뿐입니다:

    조건(preprocess) 을 바꿔서 생긴 차이인가,
    아니면 같은 조건에서 두 번 돌려도 생기는 GPT-4o 의 변덕인가?

2026-08-28 실측: 같은 32장을 raw 로 재면 59.4%, production 으로 재면 62.5% 였습니다.
그런데 판정이 바뀐 사진은 3장, 순증은 1장입니다. 3.1%p 는 1장짜리 차이입니다.
**1장이 조건 내 변동폭 안에 들어가면 «전처리 효과»라고 말할 수 없습니다.**
이 도구는 그것을 가려 주고, 가릴 수 없으면 «가릴 수 없다»고 말합니다.

비용 $0 · OpenAI 미호출 · stdlib 만 사용(torch·numpy·pandas 없음).

사용법
------
    python tools/compare_runs.py <json경로> [<json경로> ...]
    python tools/compare_runs.py --glob ".tmp/photo_test_results*.json"
    옵션: --json <출력경로>     요약을 JSON 으로도 저장

종료 코드
--------
    0  정상
    1  가드 발동(photo_set 혼입 / 쓸 수 있는 파일 0개)
    2  인자 없음(사용법 출력)
"""

import argparse
import glob as globmod
import json
import os
import sys


# ══════════════════════════════════════════════════════════════════════════
# 출력 래퍼
# ══════════════════════════════════════════════════════════════════════════
# ★ 제이 PC 는 Windows 이고 stdout 이 CP949 파이프로 갈 수 있습니다.
#   ✓ ✗ ⚠ ★ → 같은 글자를 print() 로 직접 쓰면 **전부 통과한 뒤 마지막 줄에서**
#   UnicodeEncodeError 로 죽어 exit 1 이 납니다 — 통과했는데 실패로 보입니다.
#   tools/verify_preprocess_production.py · tools/food30_diagnose_attractor.py
#   가 쓰는 것과 같은 패턴입니다.
#   ⚠ 이 이름은 절대 지역 변수로 가리지 마십시오(과거 `out = m["bytes"]` 로
#     출력 함수가 가려져 TypeError 가 난 적 있습니다).

def out(s=""):
    """Windows 콘솔(CP949)에서 죽지 않게.

    ★ BrokenPipeError 도 삼킵니다(2026-08-28 감사 경-5).
      `compare_runs.py ... | more` 처럼 파이프 상대가 먼저 닫으면
      print 가 BrokenPipeError(OSError) 를 던져 **전부 통과한 뒤 exit 1** 이
      납니다 — 이 래퍼가 막으려던 바로 그 증상입니다.
    """
    try:
        print(s)
    except UnicodeEncodeError:
        try:
            print(s.encode("cp949", "replace").decode("cp949"))
        except UnicodeEncodeError:
            # 최후의 안전망. stdout 이 cp949 도 아닐 때(예: ascii 파이프)
            # 여기서 죽으면 «통과했는데 exit 1» 이 그대로 재현됩니다.
            print(s.encode("ascii", "replace").decode("ascii"))
        except (BrokenPipeError, OSError):
            pass
    except (BrokenPipeError, OSError):
        pass
    try:
        sys.stdout.flush()
    except (ValueError, OSError):
        pass


MATCHES = ("EXACT", "CONTAINS", "LOOSE", "NONE")


# ══════════════════════════════════════════════════════════════════════════
# 1. 적재 — 깨진 파일 하나가 전체를 죽이지 않는다
# ══════════════════════════════════════════════════════════════════════════

def load_run(path):
    """파일 하나를 읽어 실행(run) 딕셔너리로. 실패하면 (None, 이유)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return None, "파일이 없습니다"
    except UnicodeDecodeError as e:
        return None, "UTF-8 로 못 읽습니다: %s" % e
    except json.JSONDecodeError as e:
        return None, "JSON 파싱 실패: %s" % e
    except OSError as e:
        return None, "읽기 실패: %s" % e

    if not isinstance(raw, dict):
        return None, "최상위가 객체(dict)가 아닙니다"

    details = raw.get("details")
    if not isinstance(details, list):
        return None, "details 키가 없거나 리스트가 아닙니다"
    if not details:
        return None, "details 가 비어 있습니다"

    per_photo = {}
    dup = []
    for d in details:
        if not isinstance(d, dict):
            continue
        key = d.get("expected")
        if key is None:
            continue
        key = str(key)
        if key in per_photo:
            dup.append(key)
            continue                      # 첫 번째만 채택 — 아래에서 경고
        engine = d.get("food30_engine")
        # ★ 2026-08-28 감사 중-3: 「엔진 필드가 아예 없다」(2026-07-24 등 구버전 파일)와
        #   「엔진이 돌았고 검출이 0이다」를 같은 {} 로 뭉개면, 구버전 파일을 섞었을 때
        #   detected 불일치 경고가 **오발동**합니다. 둘을 구분해 둡니다.
        engine_present = isinstance(engine, dict)
        if not engine_present:
            engine = {}
        det = engine.get("detected")
        if not isinstance(det, dict):
            det = {}
        applied = engine.get("applied")
        if not isinstance(applied, list):
            applied = []
        per_photo[key] = {
            "match": d.get("match"),
            "ai_name": d.get("ai_name"),
            "detected": det,
            "applied": applied,
            "engine_present": engine_present,
        }

    if not per_photo:
        return None, "details 안에 expected 키를 가진 항목이 없습니다"

    run = {
        "path": os.path.abspath(path),
        "name": os.path.basename(path),
        "date": raw.get("date"),
        # ★ 구버전 파일에는 preprocess 키가 아예 없습니다 → raw 로 간주.
        "preprocess": raw.get("preprocess", "raw") or "raw",
        # ★ 구버전 파일에는 usable 키가 없습니다 → true 로 간주.
        "usable": True if raw.get("usable") is None else bool(raw.get("usable")),
        "usable_missing": raw.get("usable") is None,
        "preprocess_missing": "preprocess" not in raw,
        "photo_set": raw.get("photo_set"),
        "total": raw.get("total"),
        "errors": raw.get("errors"),
        "strict": raw.get("name_accuracy_strict_pct"),
        "loose": raw.get("name_accuracy_loose_pct"),
        "broken_reason": raw.get("measurement_broken_reason"),
        "photos": per_photo,
        "dup": dup,
        "excluded": None,       # 제외 사유(있으면 집계에서 뺀다)
        "flags": [],
    }
    return run, None


def collect_paths(args_paths, pattern):
    paths = []
    for p in args_paths:
        paths.append(p)
    if pattern:
        hits = sorted(globmod.glob(pattern))
        if not hits:
            out("  (--glob '%s' 에 걸린 파일이 없습니다)" % pattern)
        paths.extend(hits)
    # 중복 제거(절대경로 기준), 순서 유지
    seen, uniq = set(), []
    for p in paths:
        ap = os.path.abspath(p)
        if ap in seen:
            continue
        seen.add(ap)
        uniq.append(p)
    return uniq


# ══════════════════════════════════════════════════════════════════════════
# 2. 작은 유틸
# ══════════════════════════════════════════════════════════════════════════

def fmt(v, dash="-"):
    return dash if v is None else str(v)


def pct(v):
    return "-" if v is None else ("%.1f%%" % float(v))


def short_date(v):
    if not v:
        return "-"
    s = str(v)
    return s[:19].replace("T", " ")


def mean(xs):
    return sum(xs) / float(len(xs))


def detected_signature(det):
    """detected 를 실행 간 비교 가능한 문자열로. 슬롯 → 클래스(신뢰도)."""
    if not det:
        return ""
    parts = []
    for slot in sorted(det.keys()):
        v = det[slot]
        if isinstance(v, dict):
            cls = v.get("class")
            conf = v.get("confidence")
            try:
                conf = round(float(conf), 4)
            except (TypeError, ValueError):
                pass
            parts.append("%s=%s(%s)" % (slot, cls, conf))
        else:
            parts.append("%s=%s" % (slot, v))
    return ", ".join(parts)


# ══════════════════════════════════════════════════════════════════════════
# 3. 본체
# ══════════════════════════════════════════════════════════════════════════

def main(argv=None):
    ap = argparse.ArgumentParser(add_help=True, description=__doc__)
    ap.add_argument("paths", nargs="*", help="accuracy_test 결과 JSON 경로")
    ap.add_argument("--glob", dest="pattern", default=None,
                    help='예: ".tmp/photo_test_results*.json"')
    ap.add_argument("--json", dest="json_out", default=None,
                    help="요약을 JSON 으로 저장할 경로(선택)")
    args = ap.parse_args(argv)

    if not args.paths and not args.pattern:
        out(__doc__)
        return 2

    out("=" * 74)
    out("compare_runs — 실행 간 분산 요약")
    out("=" * 74)

    paths = collect_paths(args.paths, args.pattern)
    if not paths:
        out("")
        out("[중단] 읽을 파일이 하나도 없습니다.")
        return 2

    # ── [0] 입력 인벤토리 ────────────────────────────────────────────────
    # 규칙: 추론 전에 «무엇을 읽었는지»부터 밝힌다.
    out("")
    out("[0] 입력 인벤토리 — 무엇을 읽었나 (추론 전에 먼저)")
    out("-" * 74)

    runs, skipped = [], []
    for p in paths:
        run, reason = load_run(p)
        if run is None:
            skipped.append((p, reason))
            out("  [건너뜀] %s" % os.path.basename(p))
            out("           이유: %s" % reason)
            continue
        runs.append(run)

    if not runs:
        out("")
        out("[중단] 읽을 수 있는 결과 파일이 0개입니다.")
        return 1

    hdr = "  %-40s %-19s %-11s %-11s %-6s %-5s %-7s %s"
    out(hdr % ("파일", "date", "photo_set", "prep", "total", "err", "usable",
               "strict"))
    out("  " + "-" * 70)
    for r in runs:
        prep = r["preprocess"] + ("*" if r["preprocess_missing"] else "")
        usable = ("true" if r["usable"] else "FALSE") + \
                 ("*" if r["usable_missing"] else "")
        out(hdr % (r["name"][:40], short_date(r["date"]),
                   fmt(r["photo_set"], "(없음)"), prep,
                   fmt(r["total"]), fmt(r["errors"]), usable,
                   pct(r["strict"])))
    out("  ( * = 파일에 그 키가 없어 기본값으로 간주: preprocess→raw, usable→true )")

    for r in runs:
        if r["dup"]:
            out("  [경고] %s: expected 가 중복된 사진 %d건 %s — 첫 항목만 씁니다."
                % (r["name"], len(r["dup"]), sorted(set(r["dup"]))[:5]))

    # ── 가드 A: photo_set 혼입 ──────────────────────────────────────────
    # 분모가 다른 EXACT% 를 나란히 놓는 것이 이 프로젝트의 알려진 사고 유형입니다.
    sets = {}
    for r in runs:
        sets.setdefault(r["photo_set"], []).append(r["name"])
    if len(sets) > 1:
        out("")
        out("[중단 · 가드] photo_set 이 서로 다른 파일이 섞였습니다.")
        for ps, names in sets.items():
            out("    photo_set=%s : %s" % (fmt(ps, "(없음)"), ", ".join(names)))
        out("  분모가 다른 정확도를 나란히 놓으면 «비교»가 아니라 «착시»입니다.")
        out("  같은 photo_set 끼리만 넘겨 주십시오.")
        return 1

    # ── 가드 B: usable == false 제외 ───────────────────────────────────
    out("")
    out("[0b] 집계 제외 판정")
    out("-" * 74)
    any_excluded = False
    for r in runs:
        if not r["usable"]:
            r["excluded"] = "usable=false" + (
                " (%s)" % r["broken_reason"] if r["broken_reason"] else "")
            out("  [제외] %s — %s" % (r["name"], r["excluded"]))
            any_excluded = True

    # ── 가드 C: errors ─────────────────────────────────────────────────
    for r in runs:
        if r["excluded"]:
            continue
        err = r["errors"]
        tot = r["total"] if isinstance(r["total"], (int, float)) else len(r["photos"])
        if not isinstance(err, (int, float)) or err == 0:
            continue
        if tot and err >= tot / 2.0:
            r["excluded"] = "errors=%s (total=%s 의 절반 이상)" % (err, tot)
            out("  [제외] %s — %s" % (r["name"], r["excluded"]))
            any_excluded = True
        else:
            r["flags"].append("errors=%s" % err)
            out("  [경고] %s — errors=%s. 집계에는 넣되 값을 의심하십시오." % (r["name"], err))
            any_excluded = True
    if not any_excluded:
        out("  (제외·경고 없음 — 모든 파일을 집계에 씁니다)")

    live = [r for r in runs if not r["excluded"]]
    if not live:
        out("")
        out("[중단 · 가드] 집계에 쓸 수 있는 실행이 0개입니다(전부 제외).")
        return 1

    # ── 사진 집합 교집합 ───────────────────────────────────────────────
    common = None
    for r in live:
        keys = set(r["photos"].keys())
        common = keys if common is None else (common & keys)
    common = sorted(common) if common else []
    if not common:
        out("")
        out("[중단 · 가드] 모든 실행에 공통으로 들어 있는 사진이 0장입니다.")
        return 1

    out("")
    out("  공통 사진 %d장으로 집계합니다." % len(common))
    for r in live:
        missing = len(r["photos"]) - len(common)
        if missing:
            dropped = sorted(set(r["photos"].keys()) - set(common))
            out("    - %s: %d장이 다른 실행에 없어 제외 %s"
                % (r["name"], missing, dropped[:6]))

    # ── [1] 실행별 요약 ─────────────────────────────────────────────────
    for r in live:
        r["exact"] = sum(1 for k in common if r["photos"][k]["match"] == "EXACT")
        r["strict_common"] = 100.0 * r["exact"] / len(common)
        r["dist"] = {m: sum(1 for k in common if r["photos"][k]["match"] == m)
                     for m in MATCHES}

    groups = {}
    for r in live:
        groups.setdefault(r["preprocess"], []).append(r)
    for g in groups.values():
        g.sort(key=lambda x: (str(x["date"] or ""), x["name"]))

    labels = {}
    order = []
    for prep in sorted(groups.keys()):
        for i, r in enumerate(groups[prep], 1):
            lab = "%s#%d" % (prep, i)
            labels[r["path"]] = lab
            order.append(r)

    out("")
    out("[1] 실행별 요약 (공통 %d장 기준)" % len(common))
    out("-" * 74)
    out("  %-14s %-34s %6s %8s   %s"
        % ("실행", "파일", "EXACT", "strict%", "EXACT/CONT/LOOSE/NONE"))
    out("  " + "-" * 70)
    for prep in sorted(groups.keys()):
        out("  [%s]  n=%d" % (prep, len(groups[prep])))
        for r in groups[prep]:
            d = r["dist"]
            flag = "  <ERR>" if r["flags"] else ""
            out("  %-14s %-34s %6d %7.1f%%   %d/%d/%d/%d%s"
                % (labels[r["path"]], r["name"][:34], r["exact"],
                   r["strict_common"],
                   d["EXACT"], d["CONTAINS"], d["LOOSE"], d["NONE"], flag))
            if abs(r["strict_common"] - (r["strict"] if r["strict"] is not None
                                         else r["strict_common"])) > 0.15:
                out("       (파일에 적힌 strict%%=%s — 공통집합이 좁아 값이 다릅니다)"
                    % pct(r["strict"]))

    # ── [2] 조건 내 변동폭 ──────────────────────────────────────────────
    out("")
    out("[2] 조건 내 변동폭 — 같은 조건을 여러 번 돌렸을 때 얼마나 흔들리나")
    out("-" * 74)
    within = {}
    for prep in sorted(groups.keys()):
        xs = [r["exact"] for r in groups[prep]]
        if len(xs) < 2:
            out("  [%s] n=1 — 변동폭을 잴 수 없습니다. (추정하지 않습니다)" % prep)
            within[prep] = {"n": len(xs), "exact": xs, "spread": None,
                            "min": xs[0] if xs else None,
                            "max": xs[0] if xs else None,
                            "mean": float(xs[0]) if xs else None}
            continue
        lo, hi, mu = min(xs), max(xs), mean(xs)
        out("  [%s] n=%d  EXACT %s  →  min=%d  max=%d  평균=%.2f  폭=%d장 (%.1f%%p)"
            % (prep, len(xs), xs, lo, hi, mu, hi - lo,
               100.0 * (hi - lo) / len(common)))
        within[prep] = {"n": len(xs), "exact": xs, "spread": hi - lo,
                        "min": lo, "max": hi, "mean": mu}

    # ── [3] 흔들린 사진 ─────────────────────────────────────────────────
    out("")
    out("[3] 흔들린 사진 — 실행에 따라 판정이 달라진 것만 (안정적인 사진은 안 찍습니다)")
    out("-" * 74)
    unstable = []
    for k in common:
        ms = [r["photos"][k]["match"] for r in order]
        if len(set(ms)) > 1:
            unstable.append(k)
    if not unstable:
        out("  없음 — 모든 사진의 판정이 전 실행에서 동일합니다.")
    else:
        out("  %d/%d장이 흔들렸습니다." % (len(unstable), len(common)))
        for k in unstable:
            out("")
            out("  · %s" % k)
            for r in order:
                cell = r["photos"][k]
                out("      %-14s %-9s ai_name=%s"
                    % (labels[r["path"]], fmt(cell["match"]),
                       fmt(cell["ai_name"], "(없음)")))

    # ── [4] ★ 판정 ─────────────────────────────────────────────────────
    out("")
    out("[4] * 판정 — 전처리 효과인가, 실행 간 변덕인가")
    out("-" * 74)
    raw_runs = groups.get("raw", [])
    prod_runs = groups.get("production", [])
    verdict = {"decided": False, "text": None}

    # ── 통제 확인을 판정 «앞»에서 한다 ──────────────────────────────────
    # 엔진 입력은 설계상 원본 고정이므로 전처리와 무관해야 합니다. 실행 간에
    # detected 가 다르면 통제가 깨진 것이고, 그러면 아래 판정 자체가 무의미합니다.
    # ★ 초판은 이 경고를 [5]에서, 즉 판정을 «확정적으로 출력한 뒤»에 찍었고
    #   verdict 에도 종료코드에도 반영하지 않았습니다(감사 중-3).
    #   engine_present 가 False 인 실행(구버전 파일)은 비교에서 제외합니다 —
    #   「필드가 없다」와 「검출이 0이다」는 다른 사건입니다.
    _eng_order = [r for r in order
                  if any(r["photos"][k].get("engine_present") for k in common)]
    engine_diff = []
    if len(_eng_order) > 1:
        for k in common:
            sigs = [detected_signature(r["photos"][k]["detected"]) for r in _eng_order]
            if len(set(sigs)) > 1:
                engine_diff.append((k, sigs))
    verdict["engine_control_ok"] = not engine_diff
    if engine_diff:
        out("  ★ 먼저 볼 것 — 엔진 입력 통제가 깨졌습니다 (%d장에서 detected 불일치)."
            % len(engine_diff))
        out("    엔진은 두 조건에서 같은 원본을 받아야 합니다. 다르다면 아래 판정은")
        out("    «전처리 차이»가 아니라 «다른 무언가»를 재고 있는 것입니다.")
        out("    자세한 목록은 아래 [5] 에 있습니다.")
        out("")

    if len(raw_runs) < 2 or len(prod_runs) < 1:
        need = []
        if len(raw_runs) < 2:
            need.append("raw 를 %d회 더 (현재 n=%d, 최소 2회 필요)"
                        % (2 - len(raw_runs), len(raw_runs)))
        if len(prod_runs) < 1:
            need.append("production 을 1회 이상 (현재 n=%d)" % len(prod_runs))
        out("  판정하지 않습니다. 판정 조건이 아직 안 됩니다.")
        for n in need:
            out("    필요: %s" % n)
        out("  조건 내 변동폭을 모르면 조건 간 차이는 «차이»인지 «잡음»인지 알 수 없습니다.")
        verdict["text"] = "판정 불가 — " + " / ".join(need)
        verdict["need"] = need
    else:
        raw_x = [r["exact"] for r in raw_runs]
        prod_x = [r["exact"] for r in prod_runs]
        raw_spread = max(raw_x) - min(raw_x)
        prod_spread = max(prod_x) - min(prod_x)
        # ★ 2026-08-28 감사 중-2: 초판은 raw 의 변동폭만 봤습니다. production 이
        #   EXACT [10,30] 처럼 62.5%p 로 흔들려도 판정식에 들어가지 않아,
        #   [2]에는 찍히는데 [4]는 「구별 불가」를 확정적으로 말했습니다.
        #   조건 «간» 차이를 잡음과 견주려면 두 조건의 잡음을 다 봐야 합니다.
        within = max(raw_spread, prod_spread)
        between = abs(mean(prod_x) - mean(raw_x))
        out("  raw        n=%d  EXACT %s  평균=%.2f  변동폭(max-min)=%d장"
            % (len(raw_x), raw_x, mean(raw_x), raw_spread))
        out("  production n=%d  EXACT %s  평균=%.2f  변동폭(max-min)=%d장"
            % (len(prod_x), prod_x, mean(prod_x), prod_spread))
        out("  조건 간 차이 |평균차| = %.2f장   /   조건 내 변동폭(둘 중 큰 쪽) = %d장"
            % (between, within))
        out("")
        verdict["decided"] = True
        verdict["raw_spread"] = raw_spread
        verdict["production_spread"] = prod_spread
        verdict["within_spread"] = within
        verdict["between"] = between
        verdict["raw_n"] = len(raw_x)
        verdict["production_n"] = len(prod_x)
        verdict["need"] = []          # 스키마를 분기마다 다르게 만들지 않는다(감사 경-6)

        if between <= within:
            verdict["text"] = ("구별 불가 — 조건 간 차이가 조건 내 변동폭 안에 있습니다. "
                               "전처리 효과라고 말할 수 없습니다.")
            out("  >> 구별 불가 — 조건 간 차이가 조건 내 변동폭 안에 있습니다.")
            out("     전처리 효과라고 말할 수 없습니다.")
        else:
            # ⛔ 여기서 «차이가 있다»고 말하지 않습니다.
            #   max-min 은 n 이 작을수록 참 변동폭을 «과소»추정합니다.
            #   raw 두 회차가 우연히 같으면 spread=0 이 되어, 1장 차이도
            #   이 분기로 떨어집니다(감사 중-1 실측: raw [19,19] vs prod [20]).
            #   그 상태로 「유의미」라고 적으면, 이 도구가 반박하려고 만들어진
            #   바로 그 주장(「62.5%가 더 높다」)을 도구가 대신 해 주게 됩니다.
            zero = [n for n, s in (("raw", raw_spread), ("production", prod_spread)) if s == 0]
            verdict["text"] = ("구별 불가라고 말할 근거는 없습니다. 그러나 n 이 작아 "
                               "«차이가 있다»고도 말할 수 없습니다 — 반복을 늘리십시오. "
                               "(raw n=%d, production n=%d)" % (len(raw_x), len(prod_x)))
            out("  >> 「구별 불가」라고 말할 근거는 없습니다.")
            out("     그러나 «차이가 있다»고도 말할 수 없습니다.")
            out("     변동폭 max-min 은 표본이 적을수록 참값을 작게 잡습니다 —")
            out("     지금 n 으로는 이 분기가 우연히 나올 수 있습니다.")
            if zero:
                out("     ★ %s 의 변동폭이 0장입니다. 회차들이 우연히 같았을 뿐"
                    % " · ".join(zero))
                out("       «흔들리지 않는다»는 증거가 아닙니다. 이 판정을 믿지 마십시오.")
            out("     다음: raw·production 을 각각 최소 5회까지 늘려 다시 보십시오.")
            out("     raw n=%d, production n=%d" % (len(raw_x), len(prod_x)))

    # ── [5] 엔진 활동 ───────────────────────────────────────────────────
    out("")
    out("[5] food30 엔진 활동")
    out("-" * 74)
    out("  %-14s %10s %10s" % ("실행", "detected>0", "applied"))
    engine_stats = {}
    for r in order:
        n_det = sum(1 for k in common if r["photos"][k]["detected"])
        n_app = sum(len(r["photos"][k]["applied"]) for k in common)
        engine_stats[labels[r["path"]]] = {"detected_photos": n_det,
                                           "applied": n_app}
        out("  %-14s %10d %10d" % (labels[r["path"]], n_det, n_app))

    # engine_diff 는 [4] 앞에서 이미 계산했습니다(감사 중-3). 여기서는 목록만 폅니다.
    out("")
    _skipped_eng = [labels[r["path"]] for r in order if r not in _eng_order]
    if _skipped_eng:
        out("  (엔진 필드가 없는 실행은 통제 비교에서 제외: %s)"
            % ", ".join(_skipped_eng))
    if len(_eng_order) < 2:
        out("  엔진 필드를 가진 실행이 2개 미만이라 통제 비교를 하지 않았습니다.")
    elif not engine_diff:
        out("  detected 는 전 실행에서 동일합니다 — 엔진 입력 통제가 지켜졌습니다.")
    else:
        out("  * 경고: detected 가 실행마다 다릅니다 (%d장)." % len(engine_diff))
        out("    엔진 입력은 전처리와 무관해야 합니다(설계상 원본 고정).")
        out("    다르다는 것은 통제가 깨졌다는 뜻입니다 — 이 비교는 신뢰할 수 없습니다.")
        for k, sigs in engine_diff:
            out("    · %s" % k)
            for r, s in zip(_eng_order, sigs):
                out("        %-14s %s" % (labels[r["path"]], s or "(없음)"))

    # ── JSON 저장 ──────────────────────────────────────────────────────
    if args.json_out:
        summary = {
            "generated_by": "tools/compare_runs.py",
            "photo_set": live[0]["photo_set"],
            "common_photos": len(common),
            "skipped_files": [{"path": p, "reason": why} for p, why in skipped],
            "excluded_runs": [{"file": r["name"], "reason": r["excluded"]}
                              for r in runs if r["excluded"]],
            "runs": [{
                "label": labels[r["path"]],
                "file": r["name"],
                "path": r["path"],
                "date": r["date"],
                "preprocess": r["preprocess"],
                "errors": r["errors"],
                "exact_common": r["exact"],
                "strict_pct_common": round(r["strict_common"], 2),
                "strict_pct_file": r["strict"],
                "match_distribution": r["dist"],
                "flags": r["flags"],
            } for r in order],
            "within_condition": within,
            "unstable_photos": [{
                "expected": k,
                "by_run": {labels[r["path"]]: {
                    "match": r["photos"][k]["match"],
                    "ai_name": r["photos"][k]["ai_name"]} for r in order},
            } for k in unstable],
            "verdict": verdict,
            "engine": {
                "per_run": engine_stats,
                "detected_differs": [k for k, _ in engine_diff],
            },
        }
        try:
            d = os.path.dirname(os.path.abspath(args.json_out))
            if d and not os.path.isdir(d):
                os.makedirs(d, exist_ok=True)
            with open(args.json_out, "w", encoding="utf-8") as f:
                json.dump(summary, f, ensure_ascii=False, indent=2)
            out("")
            out("  요약 JSON 저장: %s" % os.path.abspath(args.json_out))
        except OSError as e:
            out("")
            out("  [경고] 요약 JSON 저장 실패: %s" % e)

    out("")
    out("=" * 74)
    return 0


if __name__ == "__main__":
    # ★ BrokenPipe 최종 처리 (2026-08-28 감사 경-5).
    #   `... | more` 처럼 파이프 상대가 먼저 닫으면, out() 안에서 예외를 삼켜도
    #   **인터프리터가 종료하며 stdout 을 flush 할 때 다시 터져** exit 120 이 됩니다.
    #   실측: out() 수정만으로는 EXIT=120 그대로였습니다.
    #   표준 처리는 stdout 을 devnull 로 갈아끼워 종료 시 flush 가 성공하게 하는 것입니다.
    try:
        _rc = main()
        sys.stdout.flush()
    except BrokenPipeError:
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except Exception:
            pass
        _rc = 0
    sys.exit(_rc)
