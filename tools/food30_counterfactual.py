# -*- coding: utf-8 -*-
"""엔진 기여 반사실 계산기 — 「엔진이 없었다면 몇 장이었나」. $0.

세션49가 손으로 한 계산(IP/177 §15-1: 실측 137 · 반사실 95 · 순기여 +42)을
재현 가능한 도구로 고정한다. **세션51 검산에서 137/95/+42 가 정확히 재현됐다.**

3층으로 쪼갠다 — 이 구분이 없으면 「엔진이 좋다」와 「확장이 좋다」가 섞인다:

    순수 GPT        : 엔진 교체를 «전부» 되돌린 이름으로 채점
    엔진 ON·확장 OFF : widened=True 교체만 되돌린 이름으로 채점
    엔진 ON·확장 ON  : 실측 그대로

⚠ 되돌리기는 «이름만» 되돌린다. 실제로는 이름이 바뀌면 match_with_db 가 다시 돌아
  칼로리가 달라지므로, 이 계산은 **EXACT(이름 일치) 기준에서만** 타당하다.
  칼로리 반사실은 이 도구로 재지 말 것.

⚠ 서로 다른 전처리(raw vs production)의 결과를 이 도구로 비교할 때는,
  GPT 에게 «다른 이미지»가 갔다는 사실을 잊지 말 것. 두 실행 간 차이에는
  전처리 효과와 GPT 실행 간 분산이 «분리 불가능하게» 섞여 있다(규칙72 계열).

실행:
  python tools/food30_counterfactual.py <결과.json> [<결과2.json> ...]
  인자 없으면 aihub300 3종을 자동으로 찾아 비교한다.
"""
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
import food_analyzer as fa  # noqa: E402

TMP = _HERE.parent / ".tmp"
DEFAULT = [
    ("49 raw 확장전", TMP / "photo_test_results_aihub300.json"),
    ("50 raw 확장후", TMP / "photo_test_results_aihub300_widened.json"),
    ("51 prod 확장후", TMP / "photo_test_results_aihub300_production.json"),
]


def undo(rec, only_widened):
    """교체를 되돌린 이름 리스트. only_widened=False 면 모든 교체를 되돌린다."""
    names = list(rec.get("ai_foods_detected") or [])
    eng = rec.get("food30_engine") or {}
    for a in (eng.get("applied") or []):
        if not a.get("changed"):
            continue
        if only_widened and not a.get("widened"):
            continue
        to, frm = a.get("to"), a.get("from")
        for i, n in enumerate(names):
            if fa._f30_norm(n) == fa._f30_norm(to):
                names[i] = frm
                break
    return names


def exact(gt, names):
    return fa._f30_norm(gt) in [fa._f30_norm(n) for n in names]


def analyze(label, path):
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    det = [r for r in d["details"] if "match" in r]      # 에러 레코드 제외
    err = len(d["details"]) - len(det)
    now = sum(1 for r in det if r["match"] == "EXACT")
    no_wide = sum(1 for r in det if exact(r["expected"], undo(r, True)))
    no_eng = sum(1 for r in det if exact(r["expected"], undo(r, False)))
    wid = [r for r in det
           if any(a.get("widened")
                  for a in ((r.get("food30_engine") or {}).get("applied") or []))]
    f30 = d.get("food30_engine_summary") or {}
    print(f"══ {label}   판정 {len(det)}장 (에러 {err})")
    print(f"   순수 GPT (엔진 전체 OFF)     {no_eng:3d}")
    print(f"   엔진 ON · 확장 OFF          {no_wide:3d}")
    print(f"   엔진 ON · 확장 ON (실측)     {now:3d}   = {now/len(det)*100:.1f}%")
    print(f"   → 엔진 순기여 {now-no_eng:+d}장 ({(now-no_eng)/len(det)*100:+.1f}%p)"
          f"   그중 확장분 {now-no_wide:+d}장")
    print(f"   교체 {f30.get('changed','?')}건 · 무변경 {f30.get('already_correct','?')}건"
          f" · 불일치 {f30.get('disagreement','?')}건")
    print(f"   widened 교체 {len(wid)}장 / EXACT {sum(1 for r in wid if r['match']=='EXACT')}장")
    print()
    return {"label": label, "n": len(det), "gpt": no_eng,
            "narrow": no_wide, "full": now}


def main():
    args = sys.argv[1:]
    targets = ([(Path(a).stem, Path(a)) for a in args] if args
               else [(l, p) for l, p in DEFAULT if p.exists()])
    if not targets:
        print("결과 파일을 찾지 못했습니다.")
        return 1
    print("=" * 66)
    print("엔진 기여 반사실 — 「엔진이 없었다면」")
    print("=" * 66)
    rows = [analyze(l, p) for l, p in targets]

    if len(rows) > 1:
        print("=" * 66)
        print("요약")
        print("=" * 66)
        print(f"  {'조건':<16}{'순수GPT':>8}{'확장OFF':>8}{'확장ON':>8}{'엔진기여':>9}")
        for r in rows:
            print(f"  {r['label']:<16}{r['gpt']:>8}{r['narrow']:>8}"
                  f"{r['full']:>8}{r['full']-r['gpt']:>+9}")
        print()
        print("  ⚠ 전처리가 다른 행끼리 비교할 때는 GPT 실행 간 분산이 섞여 있다.")
        print("    「줄었다/늘었다」를 단정하지 말고 방향과 크기만 말할 것.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
