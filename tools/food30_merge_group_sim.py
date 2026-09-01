# -*- coding: utf-8 -*-
"""세션51 · $0 — 「v5 재학습」 대신 «그룹 병합»이 답인지 잰다.

배경 (세션51 실측으로 확정된 것):
  · 학습 데이터는 **문제가 아니다**. 30클래스 1,542~1,631장, 불균형 1.06배.
    혼동 쌍(설렁탕1623↔곰탕1592, 닭개장1606↔육개장1586)도 전부 균형.
    오히려 최소 데이터(닭볶음탕1542·삼계탕1557)가 성능 최상위다.
    출처: .tmp/{bap,tang}_yolo/build_report.json 의 per_code_kept
  · 따라서 「데이터를 더 모은다」도 「같은 데이터로 재학습한다」도 답이 아닐 수 있다.
    남은 가설: **애초에 사진으로 구별 불가한 클래스가 섞여 있다.**

이 스크립트가 재는 것:
  1) 엔진 자기 혼동 그래프 (attractor_diagnose 1,998장, 엔진 단독 판정)
  2) 대칭 혼동 쌍 — 「A→B 이면서 B→A」. 방향이 한쪽뿐이면 흡수, 양방향이면 구별 불가.
  3) 그룹을 묶었을 때 되찾는 장수와 «치르는 대가»(그룹 내 칼로리 편차)

⚠ 병합은 «평가 기준 완화»가 아니다. 실제로는 사용자에게 「설렁탕/곰탕 계열」로 보여주고
  영양은 대표값을 쓰는 것이다. 그러므로 **그룹 내 칼로리 편차가 대가**다.
  편차가 크면 병합은 「거짓 초록」이 된다 — 그때는 묶지 말아야 한다.

API 호출 없음. $0.
"""
import json
import sys
import collections
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r"D:\서박사의 영양공식")
NL = ROOT / "backends" / "NutriLens"

DIAG = sorted((NL / ".tmp" / "diagnose").glob("attractor_diagnose_*.json"))[-1]

# expected_kcal 은 accuracy_test 가 쓰는 기대 칼로리(100g/1인분 기준 혼재 없음 — gold 기준).
# 여기서는 aihub300 결과에 실린 kcal_expected 를 그대로 재사용한다(별도 표 안 만든다).
AIHUB = NL / ".tmp" / "photo_test_results_aihub300_widened.json"


def load_expected_kcal():
    det = json.loads(AIHUB.read_text(encoding="utf-8"))["details"]
    out = {}
    for r in det:
        k = r.get("kcal_expected")
        if isinstance(k, (int, float)) and k:
            out[r["expected"]] = k
    return out


def main():
    diag = json.loads(DIAG.read_text(encoding="utf-8"))
    pos = diag["positive"]
    tau = diag["operating_tau"]
    kcal = load_expected_kcal()

    print("=" * 72)
    print(f"엔진 자기 혼동 구조 — {DIAG.name}")
    print(f"  사진 {len(pos)}장 · τ={tau}")
    print("=" * 72)

    # ── 혼동 행렬 (top1 기준) ──────────────────────────────────────
    conf = collections.Counter()
    seen = collections.Counter()
    for r in pos:
        gt, t1 = r.get("gt"), r.get("top1")
        if not gt:
            continue
        seen[gt] += 1
        if t1 and t1 != gt:
            conf[(gt, t1)] += 1

    # ── 대칭성: A→B 와 B→A 가 «둘 다» 있는가 ────────────────────────
    print()
    print("=== 대칭 혼동 쌍 (양방향) — «구별 불가»의 서명 ===")
    print("   흡수(한 방향)와 구별불가(양방향)는 다른 병이다. 처방도 다르다.")
    print()
    pairs = {}
    for (a, b), n in conf.items():
        if a == b:
            continue
        key = tuple(sorted((a, b)))
        pairs.setdefault(key, {})[(a, b)] = n
    sym = []
    for (x, y), dirs in pairs.items():
        ab = dirs.get((x, y), 0)
        ba = dirs.get((y, x), 0)
        if ab and ba:
            sym.append((ab + ba, x, y, ab, ba))
    sym.sort(reverse=True)
    print(f"  {'쌍':<20}{'합계':>5}{'→':>7}{'←':>6}   {'대칭도':>7}   그룹 내 kcal")
    for tot, x, y, ab, ba in sym[:16]:
        symmetry = min(ab, ba) / max(ab, ba)
        kx, ky = kcal.get(x), kcal.get(y)
        kt = (f"{kx}/{ky}  Δ{abs(kx-ky)/max(kx,ky)*100:.0f}%"
              if kx and ky else "—")
        print(f"  {x+'↔'+y:<20}{tot:>5}{ab:>7}{ba:>6}   {symmetry:>6.2f}   {kt}")

    print()
    print("=== 한 방향뿐 (흡수) — 교체 정책으로 다룰 수 있는 종류 ===")
    oneway = []
    for (x, y), dirs in pairs.items():
        ab = dirs.get((x, y), 0)
        ba = dirs.get((y, x), 0)
        if bool(ab) != bool(ba):
            src, dst, n = (x, y, ab) if ab else (y, x, ba)
            oneway.append((n, src, dst))
    oneway.sort(reverse=True)
    for n, src, dst in oneway[:10]:
        print(f"  {src} → {dst}  {n}건 (역방향 0)")

    # ── 그룹 후보 평가 ────────────────────────────────────────────
    GROUPS = [
        ("뽀얀국물", ["설렁탕", "곰탕"]),
        ("해물탕류", ["꽃게탕", "해물탕"]),          # 대칭도 1.00 — 초판에서 빠뜨렸다
        ("뽀얀국물+", ["설렁탕", "곰탕", "꼬리곰탕", "도가니탕"]),
        ("얼큰장국", ["육개장", "닭개장"]),
        ("돼지등뼈", ["감자탕", "뼈해장국"]),
        ("문어낙지", ["낙지탕", "연포탕"]),
        ("생선매운", ["매운탕", "지리탕", "알탕"]),
    ]
    print()
    print("=" * 72)
    print("그룹 병합 시뮬레이션 — 되찾는 장수 vs 치르는 대가")
    print("=" * 72)
    print(f"  {'그룹':<12}{'구성':<28}{'되찾음':>7}{'kcal 편차':>11}  판정")
    for label, members in GROUPS:
        ms = set(members)
        recovered = sum(n for (a, b), n in conf.items() if a in ms and b in ms)
        ks = [kcal[m] for m in members if m in kcal]
        spread = (max(ks) - min(ks)) / max(ks) * 100 if len(ks) > 1 else None
        if spread is None:
            verdict = "kcal 미상"
        elif spread <= 15:
            verdict = "✅ 묶을 만함"
        elif spread <= 30:
            verdict = "⚠ 경계"
        else:
            verdict = "⛔ 영양이 갈림"
        sp = f"{spread:.0f}%" if spread is not None else "—"
        print(f"  {label:<12}{'+'.join(members):<28}{recovered:>7}{sp:>11}  {verdict}")

    # ── 전체 혼동 중 «대칭 / 한방향» 비중 ──────────────────────────
    tot_conf = sum(conf.values())
    sym_n = sum(t for t, *_ in sym)
    one_n = sum(n for n, *_ in oneway)
    print()
    print("=" * 72)
    print("★ 처방이 갈리는 지점 — 전체 오답의 성격 분해")
    print("=" * 72)
    print(f"  엔진 오답 총 {tot_conf}건")
    print(f"    대칭 혼동(구별 불가)      {sym_n:4d}건 = {sym_n/tot_conf*100:.0f}%"
          "   → 재학습해도 잘 안 풀린다. 병합 or 포기")
    print(f"    한 방향 흡수              {one_n:4d}건 = {one_n/tot_conf*100:.0f}%"
          "   → **재학습 없이** 정책·임계값으로 다룰 수 있다")
    print(f"    나머지(산발)              {tot_conf-sym_n-one_n:4d}건")

    print()
    print("※ 「되찾음」은 «엔진 단독 판정»에서 그룹 안으로 들어오는 오답 수다.")
    print("  최종 EXACT 로 얼마나 옮겨지는지는 별개다 — 교체 정책을 타야 한다.")
    print("※ kcal 편차가 크면 병합은 「거짓 초록」이 된다. 그때는 묶지 말 것.")


if __name__ == "__main__":
    main()
