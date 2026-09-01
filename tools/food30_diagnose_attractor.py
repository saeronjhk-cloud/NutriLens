#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""food30 흡수 클래스(attractor) · 화질 붕괴 · τ 양방향 비용 진단.
로컬 추론만. OpenAI 미호출. 비용 $0.

세션48(2026-08-24) 신설. 세션47의 `food30_diagnose_rice.py` 를 대체하지 않습니다 —
그건 세션47 실측의 재현 근거이므로 그대로 둡니다. 이건 **표본을 넓힌** 후속입니다.

═══════════════════════════════════════════════════════════════════════════
★ 이 스크립트가 존재하는 이유 — 세션48이 발견한 것
═══════════════════════════════════════════════════════════════════════════
IP/174 는 평가 사진 자산을 이렇게 적었습니다:

    평가 사진 — 탕류 2차 26    D:\\서박사의 영양공식\\Images\\탕류_2차\\

**26은 사진 수가 아니라 하위 폴더 개수였습니다.**
그 폴더에는 실제로 **185장**이 들어 있습니다:

    positive 125장 — 24개 클래스 폴더 (닭볶음탕 10장, 나머지 각 5장)
    negative  60장 — 6개 혼동 계열 폴더

그리고 `tools\\check_eval_photos.py --merge` 가 **한 번도 실행되지 않았습니다.**
즉 이 185장은 수집만 되고 **한 번도 측정에 쓰인 적이 없습니다.**

이것이 왜 중요한가:

  · IP/174 §1-2 는 현미밥 **3장**으로 「기타잡곡밥 흡수」를 규명하고,
    규칙3(n=3 으로 방향을 바꾸지 말 것)을 근거로 τ 를 동결했습니다.
    → 현미밥은 실제로 **8장**(밥류 3 + 2차 5), 흑미밥도 **8장** 있습니다.

  · IP/174 §1-5 「저해상도+재인코딩 conf 붕괴」는 **n=2** 라 미확정입니다.
    → 밥류 positive 는 총 **52장**입니다(Images/밥류 12 + 탕류_2차 밥 8종×5).
      격자를 돌리면 곡선이 나옵니다.

  · IP/174 는 「탕류에도 흡수가 있는지 모른다 — 밥류만 봤다」로 남겼습니다.
    → 탕류는 GT 가 붙은 사진이 **185장**(1차 60 + 2차 125) 있습니다.

⚠ 규칙38 을 지킵니다: 이 스크립트는 **무엇을 몇 장 셌는지 먼저 출력**합니다.
   센 것이 질문의 대상과 같은지 화면에서 확인할 수 있어야 합니다.

═══════════════════════════════════════════════════════════════════════════
무엇을 재는가 — 3부
═══════════════════════════════════════════════════════════════════════════
[1] 흡수 클래스 진단  (IP/174 미결 「탕류 기타 흡수 미조사」)

    흡수자(attractor) 정의:
      클래스 C 가 top-1 인데 GT ≠ C 인 사진이, **서로 다른 여러 GT** 에 걸쳐
      반복될 때 C 는 흡수자다. 한 GT 에서만 나오면 그건 그냥 짝혼동이다.

    지표:  absorb_photos  = C 가 top-1 이고 GT≠C 인 사진 수
           absorb_sources = 그 사진들의 서로 다른 GT 종류 수     ← 핵심
           self_top1      = C 가 GT 이면서 top-1 인 사진 수 (정상 동작량)

    ★ 지표 검산 (규칙40 정신): 밥류에서 `기타잡곡밥` 이 흡수자로 **재현되는지**
      먼저 확인합니다. 이미 아는 답을 못 맞히는 지표로 모르는 답을 찾을 수 없습니다.
      IP/174 §1-2 실측: 현미밥1·현미밥3·흑미밥2·흑미밥3 → 기타잡곡밥.
      즉 absorb_sources ≥ 2, absorb_photos ≥ 4 가 나와야 합니다.

[2] 화질 붕괴 곡선   (IP/174 §1-5 를 n=2 에서 확대)

    밥류 전수 × JPEG 품질 4단계 × 크기 5단계.
    「축소 없이 재인코딩만」과 「축소」를 분리해서 봅니다 —
    IP/174 §1-5 의 쌀밥3(312×234, 축소 없음)에서 0.93→0.17 이 났으므로
    범인은 축소가 아니라 **재인코딩**일 가능성이 있습니다. 아직 미확정입니다.

[3] τ 양방향 비용    (IP/174 §1-3 을 넓은 표본에서 재확인 · 규칙40)

    τ 를 내릴 때 **오탐이 늘어나는 양**과 **정탐이 흡수자에게 먹히는 양**을
    같은 표에 출력합니다.

    ⚠ 오탐 열을 **두 개로 나눕니다**(독립감사 중-1):
        오탐(게이트) — `.tmp/test_images` 기준. IP/165 §7-2 예산 2건의 대상.
                       IP/174 §1-3 이 이 셋으로 쟀으므로 숫자를 나란히 놓을 수 있습니다.
        오탐(신규)   — `Images/탕류_2차/negative` 60장. **한 번도 측정된 적이 없어
                       예산이 없습니다.** 참고 수치입니다.
      ⛔ 두 열을 더해서 2와 비교하지 마십시오.

    ⚠ near(비빔밥·돌솥비빔밥 등 근사 정답)는 오탐에서 제외합니다 —
      `food30_sweep._GT_RICE_LIKE`/`_GT_SOUP_LIKE`, 즉 IP/174 와 **같은 목록**입니다.
      기준이 다르면 두 세션의 숫자를 나란히 놓을 수 없습니다(규칙34).

⚠ τ 를 바꾸지 않습니다. 진단만 합니다(규칙3).
⚠ 산출물은 .tmp/diagnose/ 로만 씁니다(규칙26).
⚠ 이 결과는 G4 게이트 결과가 아닙니다. IP/165 §7 기록표에 넣지 마십시오.

실행
───────────────────────────────────────────────────────────────────────────
    python tools\\food30_diagnose_attractor.py              # 전체 (약 1,180회 추론, 8~15분)
    python tools\\food30_diagnose_attractor.py --skip-quality   # [1]+[3] 만 (약 350회, 3~5분)
    python tools\\food30_diagnose_attractor.py --inventory-only # [0] 만, 추론 없음
"""

import argparse
import io
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

PROJECT_DIR = Path(__file__).parent.parent          # backends/NutriLens
ROOT_DIR = PROJECT_DIR.parent.parent                # D:\서박사의 영양공식
IMAGES_DIR = ROOT_DIR / "Images"
RICE_DIR = IMAGES_DIR / "밥류"
SOUP_DIR = IMAGES_DIR / "탕류"
SOUP2_DIR = IMAGES_DIR / "탕류_2차"

# 세션48(2026-08-26): AI Hub Validation holdout.
# `build_aihub_val_evalset.py` 가 <음식명>/ 폴더 구조로 만든다 — 탕류_2차와 같은 모양이라
# 같은 로직으로 읽는다. 없으면 조용히 건너뛴다.
# ⚠ 이건 in-domain 평가셋이다. 학습과 같은 촬영 조건이므로 실사용 성능의
#   **상한**이지 실사용 성능이 아니다(IP/175 §3-5 · 규칙47).
AIHUB_VAL_DIR = IMAGES_DIR / "aihub_val"
TEST_DIR = PROJECT_DIR / ".tmp" / "test_images"
OUT_DIR = PROJECT_DIR / ".tmp" / "diagnose"

PROBE_CONF = 0.01
TAU_LADDER = [0.80, 0.75, 0.70, 0.65, 0.60, 0.55, 0.50, 0.45, 0.40, 0.35, 0.30]
FP_BUDGET = 2                       # IP/165 §7-2

# 화질 격자
Q_LADDER = [95, 80, 60, 40]
SIZE_LADDER = [None, 768, 640, 512, 384]     # None = 축소 없음(재인코딩만)

# 파일명 → food30 클래스 정규화.
# Images/밥류 는 '잡곡밥1.jpg' 인데 모델 클래스는 '기타잡곡밥' 이다.
GT_ALIAS = {
    "잡곡밥": "기타잡곡밥",
    "뼈다귀해장국": "뼈해장국",
}

try:
    from food_analyzer import (
        FOOD30_CLASS_NAMES, FOOD30_CONF_TAU, _F30_MODEL_PATH,
        _F30_RICE_IDX, _F30_SOUP_IDX,
    )
except Exception as e:                                # pragma: no cover
    print(f"[치명] food_analyzer 임포트 실패: {type(e).__name__}: {e}")
    sys.exit(2)

CLASS_SET = set(FOOD30_CLASS_NAMES)

# ★ 2026-08-24 독립감사 중-1.
# 초판은 「GT 가 30클래스가 아니면 negative」로 단순 분류했습니다. 그러면
# `02_비빔밥` · `110_돌솥비빔밥` · `109_김치전골` 같은 **근사 정답**이
# negative 풀에 들어가 엔진이 맞혀도 오탐으로 계상됩니다.
# 그건 IP/174 §1-3 이 쓴 기준과 다릅니다 — 174 는 `food30_sweep` 의
# `_GT_RICE_LIKE`/`_GT_SOUP_LIKE` 로 근사 정답을 오탐에서 제외했습니다.
# 기준이 다르면 두 세션의 숫자를 나란히 놓을 수 없습니다(규칙34).
# → 174 와 **같은 목록**을 가져옵니다. 3분류로 나눕니다:
#      positive  GT 가 food30 30클래스        → 정탐/흡수/침묵을 판정
#      near      30클래스는 아니지만 *_LIKE   → 검출돼도 오탐 아님(중립)
#      negative  그 밖                        → 검출되면 오탐
try:
    from food30_sweep import _GT_RICE_LIKE, _GT_SOUP_LIKE
except Exception:
    _GT_RICE_LIKE = {'쌀밥', '잡곡밥', '기타잡곡밥', '콩밥', '보리밥', '돌솥밥',
                     '현미밥', '흑미밥', '감자밥', '비빔밥', '돌솥비빔밥'}
    _GT_SOUP_LIKE = {'갈비탕', '감자탕', '곰탕', '매운탕', '꼬리곰탕', '꽃게탕',
                     '낙지탕', '내장탕', '닭곰탕', '닭볶음탕', '지리탕', '도가니탕',
                     '삼계탕', '설렁탕', '알탕', '연포탕', '오리탕', '추어탕',
                     '해물탕', '닭개장', '육개장', '뼈해장국'}
NEAR_SET = (_GT_RICE_LIKE | _GT_SOUP_LIKE) - CLASS_SET

# FP 예산이 적용되는 셋. IP/165 §7-2 의 「오탐 ≤ 2」는 **이 폴더 기준**으로
# 정해진 값입니다. Images/탕류_2차/negative 60장은 한 번도 측정된 적이 없어
# 예산이 없습니다 — 같은 숫자로 판정하면 근거 없는 게이트가 됩니다.
FP_BUDGET_SOURCE = ".tmp/test_images"


def out(s=""):
    """Windows 콘솔(CP949)에서 죽지 않게."""
    try:
        print(s)
    except UnicodeEncodeError:
        print(s.encode("cp949", "replace").decode("cp949"))
    sys.stdout.flush()


# ══════════════════════════════════════════════════════════════════════════
# 0. 자산 인벤토리 — 규칙38: 센 것이 무엇인지 먼저 밝힌다
# ══════════════════════════════════════════════════════════════════════════

def _gt_from_name(stem):
    """파일명에서 GT 클래스명을 뽑는다. 숫자 접두/접미를 제거."""
    s = stem
    if "_" in s and s.split("_", 1)[0].isdigit():     # 105_갈비탕
        s = s.split("_", 1)[1]
    s = s.rstrip("0123456789")                        # 현미밥101 → 현미밥
    s = s.strip()
    return GT_ALIAS.get(s, s)


def _kind(gt):
    """3분류. IP/174 와 같은 기준(§상단 주석)."""
    if gt in CLASS_SET:
        return "positive"
    if gt in NEAR_SET:
        return "near"
    return "negative"


def collect():
    """(path, gt, kind, source) 목록. kind ∈ positive / near / negative."""
    items = []

    for d, tag in ((RICE_DIR, "Images/밥류"), (SOUP_DIR, "Images/탕류")):
        if not d.exists():
            continue
        for p in sorted(d.iterdir()):
            if p.suffix.lower() not in (".jpg", ".jpeg", ".png"):
                continue
            gt = _gt_from_name(p.stem)
            items.append((p, gt, _kind(gt), tag))

    if SOUP2_DIR.exists():
        for sub in sorted(SOUP2_DIR.iterdir()):
            if not sub.is_dir():
                continue
            if sub.name == "negative":
                for nsub in sorted(sub.iterdir()):
                    if not nsub.is_dir():
                        continue
                    for p in sorted(nsub.iterdir()):
                        if p.suffix.lower() in (".jpg", ".jpeg", ".png"):
                            items.append((p, f"negative/{nsub.name}", "negative",
                                          "Images/탕류_2차/negative"))
                continue
            gt = GT_ALIAS.get(sub.name, sub.name)
            for p in sorted(sub.iterdir()):
                if p.suffix.lower() in (".jpg", ".jpeg", ".png"):
                    items.append((p, gt, _kind(gt), "Images/탕류_2차"))

    # AI Hub Validation holdout — 클래스별 폴더 구조(탕류_2차와 동일)
    if AIHUB_VAL_DIR.exists():
        for sub in sorted(AIHUB_VAL_DIR.iterdir()):
            if not sub.is_dir():
                continue
            gt = GT_ALIAS.get(sub.name, sub.name)
            for p in sorted(sub.iterdir()):
                if p.suffix.lower() in (".jpg", ".jpeg", ".png"):
                    items.append((p, gt, _kind(gt), "Images/aihub_val ★holdout"))

    if TEST_DIR.exists():
        for p in sorted(TEST_DIR.iterdir()):
            if p.suffix.lower() not in (".jpg", ".jpeg", ".png"):
                continue
            gt = _gt_from_name(p.stem)
            items.append((p, gt, _kind(gt), ".tmp/test_images"))

    return items


def inventory(items):
    out("=" * 78)
    out("0. 자산 인벤토리 — 무엇을 몇 장 셌는가 (규칙38)")
    out("=" * 78)

    by_src = defaultdict(lambda: {"positive": 0, "near": 0, "negative": 0})
    for _p, _gt, kind, src in items:
        by_src[src][kind] += 1
    out(f"{'출처':<28} {'positive':>9} {'near':>6} {'negative':>9} {'합계':>7}")
    out("-" * 78)
    tot = {"positive": 0, "near": 0, "negative": 0}
    for src in sorted(by_src):
        d = by_src[src]
        for k in tot:
            tot[k] += d[k]
        mark = "  ← FP 예산 2건 적용 셋" if src == FP_BUDGET_SOURCE else ""
        out(f"{src:<28} {d['positive']:>9} {d['near']:>6} {d['negative']:>9} "
            f"{sum(d.values()):>7}{mark}")
    out("-" * 78)
    out(f"{'합계':<28} {tot['positive']:>9} {tot['near']:>6} "
        f"{tot['negative']:>9} {sum(tot.values()):>7}")
    out()
    out("  positive = GT 가 food30 30클래스 (정탐/흡수/침묵을 판정)")
    out("  near     = 30클래스는 아니지만 비빔밥·돌솥비빔밥 등 근사 정답")
    out("             → 검출돼도 오탐으로 세지 않습니다(IP/174 와 같은 기준)")
    out("  negative = 그 밖. 검출되면 오탐")
    out(f"  ⚠ FP 예산 2건(IP/165 §7-2)은 `{FP_BUDGET_SOURCE}` 기준으로 정해진 값입니다.")
    out("    Images/탕류_2차/negative 60장은 **한 번도 측정된 적이 없어 예산이 없습니다.**")
    out("    두 셋을 합쳐 2건과 비교하지 마십시오 — 근거 없는 게이트가 됩니다.")

    per_gt = defaultdict(int)
    for _p, gt, kind, _s in items:
        if kind == "positive":
            per_gt[gt] += 1
    out()
    out("food30 30클래스별 GT 사진 보유량 (positive)")
    out("-" * 78)
    missing = []
    line = []
    for c in FOOD30_CLASS_NAMES:
        n = per_gt.get(c, 0)
        if n == 0:
            missing.append(c)
        line.append(f"{c} {n}")
        if len(line) == 5:
            out("  " + "   ".join(f"{x:<14}" for x in line))
            line = []
    if line:
        out("  " + "   ".join(f"{x:<14}" for x in line))
    if missing:
        out(f"\n  ⚠ GT 사진이 0장인 클래스 {len(missing)}종: {', '.join(missing)}")
        out("    → 이 클래스들의 흡수/정탐은 이 실행으로 판정할 수 없습니다.")
    out()
    return per_gt


# ══════════════════════════════════════════════════════════════════════════
# 추론 유틸
# ══════════════════════════════════════════════════════════════════════════

def load_model():
    """진단 전용 로드. 클래스 순서 대조는 운영과 동일(규칙11)."""
    if not _F30_MODEL_PATH.exists():
        out(f"[치명] 모델 파일 없음: {_F30_MODEL_PATH}")
        sys.exit(2)
    from ultralytics import YOLO
    m = YOLO(str(_F30_MODEL_PATH))
    names = [m.names[i] for i in range(len(m.names))]
    if names != FOOD30_CLASS_NAMES:
        out("[치명] 클래스 순서 불일치 — 진단을 중단합니다.")
        out(f"  모델: {names}")
        out(f"  코드: {FOOD30_CLASS_NAMES}")
        sys.exit(2)
    out(f"[food30] 진단 로드: {_F30_MODEL_PATH.name} "
        f"(운영 τ={FOOD30_CONF_TAU} · 변경하지 않음 · 진단 하한={PROBE_CONF})")
    return m


def boxes_of(model, img, conf=PROBE_CONF):
    res = model.predict(img, conf=conf, verbose=False)
    rows = []
    for r in res:
        b = getattr(r, "boxes", None)
        if b is None or len(b) == 0:
            continue
        for bb in b:
            cid = int(bb.cls[0])
            if cid >= len(FOOD30_CLASS_NAMES):
                continue
            rows.append({"class": FOOD30_CLASS_NAMES[cid], "cid": cid,
                         "conf": round(float(bb.conf[0]), 4)})
    rows.sort(key=lambda d: -d["conf"])
    return rows


# ══════════════════════════════════════════════════════════════════════════
# 1. 흡수 클래스 진단
# ══════════════════════════════════════════════════════════════════════════

def part1_attractor(model, items):
    out("=" * 78)
    out("1. 흡수 클래스(attractor) 진단 — 밥류에서 검산한 뒤 탕류에 적용")
    out("=" * 78)

    pos = [(p, gt, src) for p, gt, kind, src in items if kind == "positive"]
    out(f"positive {len(pos)}장을 conf={PROBE_CONF} 로 1회씩 추론합니다. 잠시 걸립니다.")

    recs = []
    for i, (p, gt, src) in enumerate(pos, 1):
        if i % 25 == 0:
            out(f"   … {i}/{len(pos)}")
        rows = boxes_of(model, str(p))
        top = rows[0] if rows else None
        recs.append({
            "photo": p.stem, "gt": gt, "source": src,
            "top1": top["class"] if top else None,
            "top1_conf": top["conf"] if top else 0.0,
            "gt_conf": next((r["conf"] for r in rows if r["class"] == gt), 0.0),
            "n_boxes": len(rows),
            "top4": rows[:4],
        })

    # ── 지표 산출 ────────────────────────────────────────────────────────
    stat = defaultdict(lambda: {"self_top1": 0, "absorb_photos": 0,
                                "absorb_sources": set(), "confs": []})
    for r in recs:
        t = r["top1"]
        if t is None:
            continue
        if t == r["gt"]:
            stat[t]["self_top1"] += 1
        else:
            stat[t]["absorb_photos"] += 1
            stat[t]["absorb_sources"].add(r["gt"])
            stat[t]["confs"].append(r["top1_conf"])

    # ★ 2026-08-24 독립감사 경-1: defaultdict 를 조회로 오염시키지 않는다.
    #   초판은 정렬 키에서 stat[c] 를 읽어 30클래스 전부를 0값 엔트리로 만들었고,
    #   그게 JSON attractor_stat 에 그대로 들어가 「한 번도 예측된 적 없음」과
    #   「예측됐지만 0」이 구별되지 않았습니다.
    _EMPTY = {"self_top1": 0, "absorb_photos": 0, "absorb_sources": set(), "confs": []}
    n_gt = defaultdict(int)
    for r in recs:
        n_gt[r["gt"]] += 1

    # 흡수자 판정 임계. IP/174 §1-2 실측(현미밥1·현미밥3·흑미밥2·흑미밥3
    # → 기타잡곡밥 = 4장·2종)에 맞춥니다. 표의 ★ 와 §검산 판정이 **같은 값**을
    # 써야 합니다 — 초판은 표가 3, 문서가 4 였습니다(독립감사 경-2).
    ABSORB_MIN_PHOTOS, ABSORB_MIN_SOURCES = 4, 2

    def _table(title, idx_range, note=""):
        out()
        out(f"── {title}")
        if note:
            out(f"   {note}")
        out(f"   {'클래스':<12} {'GT보유':>6} {'자기정탐':>8} {'흡수장수':>8} "
            f"{'흡수출처종수':>12} {'흡수conf평균':>12}  흡수한 GT")
        rows_sorted = sorted(
            (c for c in FOOD30_CLASS_NAMES if FOOD30_CLASS_NAMES.index(c) in idx_range),
            key=lambda c: (-len(stat.get(c, _EMPTY)["absorb_sources"]),
                           -stat.get(c, _EMPTY)["absorb_photos"]))
        for c in rows_sorted:
            s = stat.get(c, _EMPTY)
            if s["absorb_photos"] == 0 and s["self_top1"] == 0 and n_gt.get(c, 0) == 0:
                continue
            avg = sum(s["confs"]) / len(s["confs"]) if s["confs"] else 0.0
            srcs = ", ".join(sorted(s["absorb_sources"])[:5])
            if len(s["absorb_sources"]) > 5:
                srcs += f" 외 {len(s['absorb_sources'])-5}"
            flag = (" ★" if (len(s["absorb_sources"]) >= ABSORB_MIN_SOURCES
                             and s["absorb_photos"] >= ABSORB_MIN_PHOTOS) else "  ")
            out(f"  {flag}{c:<12} {n_gt.get(c, 0):>6} {s['self_top1']:>8} "
                f"{s['absorb_photos']:>8} {len(s['absorb_sources']):>12} "
                f"{avg:>12.3f}  {srcs}")

    _table("밥류 (index 0-7) — ★ 지표 검산 구간",
           set(_F30_RICE_IDX),
           "IP/174 §1-2 가 맞다면 `기타잡곡밥` 이 ★ 로 떠야 합니다. 안 뜨면 지표가 틀린 것입니다.")
    _table("탕류 (index 8-29) — ★ 이번에 새로 보는 구간",
           set(_F30_SOUP_IDX),
           "IP/174 는 이 구간을 「미조사」로 남겼습니다.")

    # ── 검산 판정 ────────────────────────────────────────────────────────
    ref = stat.get("기타잡곡밥", _EMPTY)
    ok = (len(ref["absorb_sources"]) >= ABSORB_MIN_SOURCES
          and ref["absorb_photos"] >= ABSORB_MIN_PHOTOS)
    out()
    out("-" * 78)
    if ok:
        out(f"  ✓ 지표 검산 통과 — 기타잡곡밥: 흡수 {ref['absorb_photos']}장 / "
            f"출처 {len(ref['absorb_sources'])}종 {sorted(ref['absorb_sources'])}")
        out("    IP/174 §1-2 를 넓은 표본에서 재현했습니다. 탕류 표의 ★ 도 같은 의미입니다.")
    else:
        out(f"  ⚠ 지표 검산 실패 — 기타잡곡밥 흡수 {ref['absorb_photos']}장 / "
            f"출처 {len(ref['absorb_sources'])}종")
        out("    IP/174 §1-2 는 현미밥1·현미밥3·흑미밥2·흑미밥3 이 기타잡곡밥으로")
        out("    갔다고 기록했습니다. 재현되지 않으면 **탕류 표를 신뢰하지 마십시오.**")
        out("    먼저 왜 다른지 규명해야 합니다(모델 파일? 사진 폴더? 지표 정의?).")
    out("-" * 78)

    # ── 침묵 사진 목록 (τ 미달) ──────────────────────────────────────────
    silent = [r for r in recs if r["top1_conf"] < FOOD30_CONF_TAU]
    out()
    out(f"τ={FOOD30_CONF_TAU} 에서 침묵하는 positive: {len(silent)}/{len(recs)}장 "
        f"({len(silent)/max(1,len(recs))*100:.0f}%)")
    by_gt = defaultdict(list)
    for r in silent:
        by_gt[r["gt"]].append(r)
    out(f"   {'GT':<12} {'침묵/보유':>10}  최고 클래스 분포")
    for gt in sorted(by_gt, key=lambda g: -len(by_gt[g])):
        rs = by_gt[gt]
        tot = sum(1 for r in recs if r["gt"] == gt)
        dist = defaultdict(int)
        for r in rs:
            dist[r["top1"] or "박스없음"] += 1
        d = ", ".join(f"{k}×{v}" for k, v in sorted(dist.items(), key=lambda kv: -kv[1]))
        out(f"   {gt:<12} {len(rs):>4}/{tot:<5}  {d}")

    return recs, {c: {"self_top1": s["self_top1"],
                      "absorb_photos": s["absorb_photos"],
                      "absorb_sources": sorted(s["absorb_sources"])}
                  for c, s in stat.items()}


# ══════════════════════════════════════════════════════════════════════════
# 2. 화질 붕괴 곡선
# ══════════════════════════════════════════════════════════════════════════

def part2_quality(model, items):
    out()
    out("=" * 78)
    out("2. 화질 붕괴 곡선 — IP/174 §1-5(n=2) 를 밥류 전수로 확대")
    out("=" * 78)

    _rice_idx = set(_F30_RICE_IDX)
    rice = [(p, gt) for p, gt, kind, _s in items
            if kind == "positive" and FOOD30_CLASS_NAMES.index(gt) in _rice_idx]
    if not rice:
        out("[경고] 밥류 positive 사진이 없습니다 — 건너뜁니다.")
        return []

    out(f"대상 {len(rice)}장 × 품질 {len(Q_LADDER)}단계 × 크기 {len(SIZE_LADDER)}단계")
    out("크기 None = 축소 없음(재인코딩만). IP/174 §1-5 의 쌀밥3 이 이 경우였습니다.")
    out()

    from PIL import Image
    recs = []
    for p, gt in rice:
        im0 = Image.open(p).convert("RGB")
        w0, h0 = im0.size
        base_rows = boxes_of(model, str(p))
        base = base_rows[0]["conf"] if base_rows else 0.0
        base_cls = base_rows[0]["class"] if base_rows else "-"
        rec = {"photo": p.stem, "gt": gt, "size": [w0, h0],
               "orig_conf": base, "orig_class": base_cls, "grid": {}}
        out(f"── {p.stem:<12} {w0}x{h0}  원본 top1 = {base_cls} {base:.3f}")
        for size in SIZE_LADDER:
            if size is not None and max(w0, h0) <= size:
                continue                      # 확대는 하지 않는다
            if size is None:
                im = im0
                label = f"{w0}x{h0}(무축소)"
            else:
                s = size / max(w0, h0)
                im = im0.resize((max(1, int(w0 * s)), max(1, int(h0 * s))))
                label = f"{im.size[0]}x{im.size[1]}"
            cells = []
            for q in Q_LADDER:
                buf = io.BytesIO()
                im.save(buf, format="JPEG", quality=q)
                buf.seek(0)
                rows = boxes_of(model, Image.open(buf).convert("RGB"))
                c = rows[0]["conf"] if rows else 0.0
                cl = rows[0]["class"] if rows else "-"
                rec["grid"][f"{size}_{q}"] = {"conf": c, "class": cl,
                                              "kb": round(len(buf.getvalue()) / 1024, 1)}
                mark = "" if c >= FOOD30_CONF_TAU else "!"
                cells.append(f"q{q}={c:.3f}{mark}")
            out(f"     {label:<18} " + "  ".join(cells))
        recs.append(rec)

    # ── 요약: 재인코딩만으로 얼마나 무너지는가 ──────────────────────────
    out()
    out("-" * 78)
    out("요약 — 「축소 없이 재인코딩만」의 낙차 (IP/174 §1-5 의 핵심 질문)")
    out("-" * 78)
    out(f"{'사진':<12} {'해상도':>11} {'원본':>7} {'q95':>7} {'q80':>7} "
        f"{'q60':>7} {'q40':>7} {'q80낙차':>8}")
    drops = []
    for r in recs:
        row = [f"{r['photo']:<12}", f"{r['size'][0]}x{r['size'][1]:<5}"[:11].rjust(11),
               f"{r['orig_conf']:>7.3f}"]
        vals = {}
        for q in Q_LADDER:
            g = r["grid"].get(f"None_{q}")
            v = g["conf"] if g else float("nan")
            vals[q] = v
            row.append(f"{v:>7.3f}" if v == v else f"{'-':>7}")
        d = (vals.get(80, float('nan')) - r["orig_conf"])
        row.append(f"{d:>+8.3f}" if d == d else f"{'-':>8}")
        if d == d:
            drops.append(d)
        out(" ".join(row))
    if drops:
        big = [d for d in drops if d <= -0.20]
        out()
        out(f"  q80 재인코딩만으로 conf 가 0.20 이상 떨어진 사진: {len(big)}/{len(drops)}장")
        out(f"  평균 낙차 {sum(drops)/len(drops):+.3f}")
        if len(big) >= 3:
            out("  ★ n≥3 입니다. IP/174 §1-5 를 「성질」로 승격할 근거가 생겼습니다.")
            out("    프로덕션은 GPT-4o 에만 q80 을 적용하고 엔진에는 원본을 줍니다 —")
            out("    즉 이 취약성은 **엔진에는 해당되지 않습니다.** 사용자가 애초에")
            out("    저품질 사진을 올리는 경우에만 문제가 됩니다.")
        else:
            out("  · 큰 낙차가 3장 미만입니다. IP/174 §1-5 는 여전히 미확정입니다.")
            out("    쌀밥3·잡곡밥3 은 원본이 640 미만인 특수 사례였을 수 있습니다.")
    return recs


# ══════════════════════════════════════════════════════════════════════════
# 3. τ 양방향 비용
# ══════════════════════════════════════════════════════════════════════════

def part3_tau(model, items, pos_recs):
    out()
    out("=" * 78)
    out("3. τ 양방향 비용 — 내리면 오탐이 늘고, 정탐이 흡수자에게 먹힌다 (규칙40)")
    out("=" * 78)

    negs = [(p, gt, src) for p, gt, kind, src in items if kind == "negative"]
    nears = [(p, gt, src) for p, gt, kind, src in items if kind == "near"]
    out(f"negative {len(negs)}장 + near {len(nears)}장을 conf={PROBE_CONF} 로 추론합니다.")
    neg_recs = []
    _all = [(t, "negative") for t in negs] + [(t, "near") for t in nears]
    for i, ((p, gt, src), kind) in enumerate(_all, 1):
        if i % 25 == 0:
            out(f"   … {i}/{len(_all)}")
        rows = boxes_of(model, str(p))
        neg_recs.append({"photo": p.stem, "gt": gt, "source": src, "kind": kind,
                         "top1": rows[0]["class"] if rows else None,
                         "top1_conf": rows[0]["conf"] if rows else 0.0})

    # ★ 2026-08-24 독립감사 중-1: 오탐을 **출처별로** 나눈다.
    #   IP/165 §7-2 의 「오탐 ≤ 2」는 `.tmp/test_images` 기준으로 정해진 값이다.
    #   Images/탕류_2차/negative 60장은 한 번도 측정된 적이 없어 예산이 없다.
    #   합쳐서 2건과 비교하면 근거 없는 게이트가 된다.
    gate_negs = [r for r in neg_recs
                 if r["kind"] == "negative" and r["source"] == FP_BUDGET_SOURCE]
    new_negs = [r for r in neg_recs
                if r["kind"] == "negative" and r["source"] != FP_BUDGET_SOURCE]
    out(f"   오탐 판정 분모: 게이트 셋 {len(gate_negs)}장(예산 {FP_BUDGET}) · "
        f"신규 셋 {len(new_negs)}장(예산 없음)")

    out()
    out(f"{'τ':>6} {'정탐(자기)':>10} {'흡수오답':>9} {'침묵':>6} "
        f"{'오탐(게이트)':>12} {'예산2':>7} {'오탐(신규)':>11}  비고")
    out("-" * 78)
    curve = {}
    for tau in TAU_LADDER:
        correct = absorbed = silent = 0
        for r in pos_recs:
            if r["top1_conf"] < tau:
                silent += 1
            elif r["top1"] == r["gt"]:
                correct += 1
            else:
                absorbed += 1
        fp = [r for r in gate_negs if r["top1_conf"] >= tau]
        fp_new = [r for r in new_negs if r["top1_conf"] >= tau]
        state = "OK" if len(fp) <= FP_BUDGET else "★초과"
        curve[tau] = {"fp_new": len(fp_new),
                      "fp_new_photos": [r["photo"] for r in fp_new][:20],
                      "correct": correct, "absorbed": absorbed,
                      "silent": silent, "fp": len(fp),
                      "fp_photos": [r["photo"] for r in fp][:20]}
        note = ""
        if tau == FOOD30_CONF_TAU:
            note = "← 현행 운영 τ"
        out(f"{tau:>6.2f} {correct:>10} {absorbed:>9} {silent:>6} "
            f"{len(fp):>12} {state:>7} {len(fp_new):>11}  {note}")

    out()
    out("  읽는 법 (IP/174 §1-3 이 밝힌 것):")
    out("   · 「정탐(자기)」가 늘어도 「흡수오답」이 같이 늘면 순이득이 아닙니다.")
    out("     흡수오답은 GPT 가 맞힌 이름을 엔진이 상위 개념으로 덮는 경우를 포함합니다.")
    out(f"   · 「오탐(게이트)」만 예산 {FP_BUDGET}건(IP/165 §7-2)의 대상입니다.")
    out("     「오탐(신규)」은 처음 측정하는 셋이라 예산이 없습니다 — 참고 수치입니다.")
    out("     ⛔ 두 열을 더해서 2와 비교하지 마십시오.")
    out("   · near(비빔밥·돌솥비빔밥 등 근사 정답)는 오탐에서 제외했습니다 —")
    out("     IP/174 와 같은 기준입니다. 그래야 두 세션의 숫자를 나란히 놓을 수 있습니다.")
    out("   · 이 표는 **하한**입니다 — 실제 교체 여부는 GPT 가 말한 이름에 달려 있어")
    out("     accuracy_test 의 `applied` 로만 확정됩니다(IP/173 치명-3).")
    return neg_recs, curve


# ══════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-quality", action="store_true", help="[2] 화질 격자 생략")
    ap.add_argument("--inventory-only", action="store_true", help="[0] 만, 추론 없음")
    args = ap.parse_args()

    out("=" * 78)
    out("food30 흡수·화질·τ 진단 — 비용 $0 (OpenAI 미호출)")
    out(f"모델 {_F30_MODEL_PATH.name} · 운영 τ={FOOD30_CONF_TAU} 는 **변경하지 않습니다**")
    out(f"실행 {datetime.now().isoformat(timespec='seconds')}")
    out("=" * 78)
    out()

    items = collect()
    per_gt = inventory(items)

    if args.inventory_only:
        out("--inventory-only: 추론 없이 종료합니다.")
        return

    model = load_model()
    pos_recs, stat = part1_attractor(model, items)
    qual = [] if args.skip_quality else part2_quality(model, items)
    neg_recs, curve = part3_tau(model, items, pos_recs)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    fp = OUT_DIR / f"attractor_diagnose_{stamp}.json"
    fp.write_text(json.dumps({
        "generated": datetime.now().isoformat(timespec="seconds"),
        "model": _F30_MODEL_PATH.name,
        "operating_tau": FOOD30_CONF_TAU,
        "probe_conf": PROBE_CONF,
        "fp_budget": FP_BUDGET,
        "inventory": {k: v for k, v in sorted(per_gt.items())},
        "positive": pos_recs,
        "attractor_stat": stat,
        "quality_grid": qual,
        "negative": neg_recs,
        "tau_curve": {str(k): v for k, v in curve.items()},
        "_NOTE": "진단 산출물. G4 게이트 결과가 아니며 IP/165 §7 기록표에 넣지 말 것.",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    out()
    out(f"저장: {fp}")
    out("이 파일을 다음 세션에 넘기면 재실행이 필요 없습니다.")


if __name__ == "__main__":
    main()
