#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
--preprocess production 자기검증.  비용 $0 (OpenAI 미호출).  torch 불필요 (PIL만).

왜 있는가
---------
`accuracy_test.py --preprocess production` 은 돈을 씁니다(~$0.16).
그런데 최소화가 **조용히 안 걸린 채로** 32장을 다 돌면, 나온 숫자는
raw 실행과 구별되지 않으면서 파일 이름만 production 입니다.
그러면 다음 세션이 그 숫자를 「프로덕션 조건 값」으로 인용합니다.
IP/174 §2 가 저지른 사고와 정확히 같은 형태입니다 — 낡거나 틀린 값을
최신 근거로 인용하는 것.

→ 돈을 쓰기 **전에** 최소화가 실제로 걸리는지 증명합니다.

무엇을 검사하는가 (IP/128 축 A · eval/verify_minimize_p03.py 와 같은 기준)
  1. original_frame_sent is False        원본 바이트가 아님
  2. crop_bounds_area_ratio < 0.90       크롭이 실제로 일어남
  3. detail == "low"                     L2 통제
  4. max(out_width, out_height) <= 768   다운스케일 상한
  5. 출력 바이트 != 입력 바이트           재인코딩 확인 (EXIF 제거 포함)
  6. accuracy_test 가 부르는 것과 **같은 함수**인지 (import 경로 동일성)

그리고 규칙40 — 완화의 대가를 같은 실행에서 잰다:
  전송 용량이 얼마나 줄어드는지도 같이 출력합니다. 이게 「프로덕션에서
  GPT-4o 가 지는 핸디캡」의 크기이고, 이번 측정이 재려는 대상입니다.

사용:  python tools\\verify_preprocess_production.py
종료:  0 = 전부 통과,  1 = 하나라도 실패(호출 스크립트가 중단해야 함)
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
NUTRILENS = HERE.parent
PROJECT = NUTRILENS.parent.parent
sys.path.insert(0, str(HERE))

AREA_RATIO_MAX = 0.90
MAX_EDGE = 768


def out(s=""):
    """Windows 콘솔(CP949)에서 죽지 않게.

    ★ 2026-08-24 독립감사 경-4. 초판은 ✓·✗·⚠ 를 그대로 print() 했고,
    이 세 글자는 CP949 에 없습니다. .bat 이 PYTHONIOENCODING=utf-8 을
    깔아 줄 때만 안전했는데, 독스트링은 단독 실행도 안내하고 있었습니다.
    전부 통과한 뒤 마지막 ✓ 줄에서 죽으면 exit 1 이 되어
    **통과했는데 실패로 보입니다.** 그게 이 래퍼의 이유입니다.
    """
    try:
        print(s)
    except UnicodeEncodeError:
        print(s.encode("cp949", "replace").decode("cp949"))
    sys.stdout.flush()

# 검사에 쓸 사진: 기준선 32장이 있는 폴더를 먼저 보고, 없으면 밥류 12장.
CANDIDATE_DIRS = [
    NUTRILENS / ".tmp" / "test_images",
    PROJECT / "Images" / "밥류",
    PROJECT / "Images" / "탕류",
]


def _pick_images(n=6):
    for d in CANDIDATE_DIRS:
        if not d.exists():
            continue
        imgs = sorted(p for p in d.iterdir()
                      if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
        if imgs:
            # 앞·중간·뒤를 섞어 뽑는다. 앞 n장만 보면 한 종류만 검사하게 된다.
            if len(imgs) <= n:
                return d, imgs
            step = len(imgs) // n
            return d, [imgs[i * step] for i in range(n)]
    return None, []


def main():
    out()
    out("=" * 70)
    out("  --preprocess production 자기검증   (비용 $0 · OpenAI 미호출)")
    out("=" * 70)

    # ── 0. import 동일성 ────────────────────────────────────────────────
    try:
        from image_minimize import minimize_to_data_url, AREA_RATIO_MAX as MOD_RATIO, MAX_EDGE as MOD_EDGE
    except Exception as e:
        out(f"\n  ✗ image_minimize 를 import 할 수 없습니다: {type(e).__name__}: {e}")
        out("    accuracy_test --preprocess production 도 같은 이유로 실패합니다.")
        return 1

    try:
        import food_analyzer  # noqa: F401
    except Exception as e:
        out(f"\n  ✗ food_analyzer import 실패: {type(e).__name__}: {e}")
        return 1

    import inspect
    sig = inspect.signature(food_analyzer.analyze_food_image)
    if "preprocess" not in sig.parameters:
        out("\n  ✗ analyze_food_image 에 preprocess 파라미터가 없습니다.")
        out("    food_analyzer.py 가 세션48 변경분이 아닙니다. git 상태를 확인하십시오.")
        return 1
    out(f"\n  · analyze_food_image{sig}")
    out(f"  · image_minimize 모듈 상수: AREA_RATIO_MAX={MOD_RATIO}  MAX_EDGE={MOD_EDGE}")

    # ★ 2026-08-24 독립감사 중-2.
    # 초판은 모듈 상수를 **출력만** 하고 판정에는 이 파일의 로컬 상수를 썼습니다.
    # 즉 image_minimize.MAX_EDGE 를 512 로 바꿔도 검사는 <=768 로 통과했고,
    # 「프로덕션과 같은 조건」이라는 결론이 조용히 거짓이 됐습니다.
    # 드리프트 검출기가 드리프트를 못 잡는 상태였습니다 — 지금 잡습니다.
    if (MOD_RATIO, MOD_EDGE) != (AREA_RATIO_MAX, MAX_EDGE):
        out("\n  ✗ 상수 드리프트 — 이 검사 스크립트가 낡았습니다.")
        out(f"    image_minimize:  AREA_RATIO_MAX={MOD_RATIO}  MAX_EDGE={MOD_EDGE}")
        out(f"    이 스크립트:      AREA_RATIO_MAX={AREA_RATIO_MAX}  MAX_EDGE={MAX_EDGE}")
        out("    프로덕션 전처리가 바뀌었습니다. 무엇이 왜 바뀌었는지 IP 문서에서")
        out("    확인한 뒤 이 파일의 상수를 맞추십시오. 그때까지 측정하지 마십시오.")
        return 1

    # test_server 가 부르는 것과 같은 함수인지 — 사본이 갈라지면 비교가 무의미해진다.
    ts = NUTRILENS / "tools" / "test_server.py"
    if ts.exists():
        src = ts.read_text(encoding="utf-8", errors="replace")
        if "minimize_to_data_url" in src:
            out("  · test_server.py 도 minimize_to_data_url 을 사용합니다 (같은 경로 확인)")
        else:
            out("  ⚠ test_server.py 에서 minimize_to_data_url 을 찾지 못했습니다.")
            out("    프로덕션 전처리가 바뀌었을 수 있습니다 — IP 문서를 확인하십시오.")

    # ── 1. 실제 이미지로 검사 ───────────────────────────────────────────
    src_dir, images = _pick_images()
    if not images:
        out("\n  ✗ 검사할 이미지를 찾지 못했습니다. 확인한 경로:")
        for d in CANDIDATE_DIRS:
            out(f"      {d}")
        return 1

    out(f"\n  검사 대상: {src_dir}  ({len(images)}장 표본)")
    out()
    out(f"  {'파일':<22s} {'입력KB':>7s} {'출력KB':>7s} {'축소':>6s} "
          f"{'해상도':>11s} {'면적비':>7s} {'detail':>7s}  판정")
    out("  " + "-" * 88)

    failures = []
    tot_in = tot_out = 0

    for p in images:
        try:
            raw = p.read_bytes()
            url, m = minimize_to_data_url(raw)
        except Exception as e:
            failures.append((p.name, f"{type(e).__name__}: {e}"))
            out(f"  {p.name:<22s} {'':>7s} {'':>7s} {'':>6s} {'':>11s} {'':>7s} {'':>7s}  ✗ 예외")
            continue

        # ★ 변수명이 out_b 인 이유: 이 모듈의 `out()` 은 출력 함수다.
        #   지역 변수를 `out` 으로 두면 함수를 가려 이후 모든 출력이 TypeError 가 된다.
        out_b = m["bytes"]
        tot_in += len(raw)
        tot_out += len(out_b)

        # ★ 2026-08-24 독립감사 경-5: m 에 키가 없으면 아래 포맷이 None 에
        #   적용돼 TypeError 로 죽는다. checks 가 «✗ 로 보고해야 할 상황»이
        #   대신 트레이스백이 된다. 먼저 안전한 값으로 꺼낸다.
        _w = m.get("out_width")
        _h = m.get("out_height")
        _ratio = m.get("crop_bounds_area_ratio")
        _detail = m.get("detail")

        checks = {
            "original_frame_sent": m.get("original_frame_sent") is False,
            "area_ratio<0.90": isinstance(_ratio, (int, float)) and _ratio < AREA_RATIO_MAX,
            "detail=low": _detail == "low",
            "max_edge<=768": (isinstance(_w, int) and isinstance(_h, int)
                              and max(_w, _h) <= MAX_EDGE),
            "re-encoded": out_b != raw,
            "data_url": url.startswith("data:image/") and ";base64," in url,
        }
        bad = [k for k, v in checks.items() if not v]
        if bad:
            failures.append((p.name, ", ".join(bad)))

        shrink = (1 - len(out_b) / len(raw)) * 100 if raw else 0
        _res = f"{_w}x{_h}" if _w is not None and _h is not None else "?"
        _rs = f"{_ratio:.4f}" if isinstance(_ratio, (int, float)) else "?"
        out(f"  {p.name:<22s} {len(raw)/1024:7.1f} {len(out_b)/1024:7.1f} {shrink:5.0f}% "
            f"{_res:>11s} {_rs:>7s} {str(_detail):>7s}  "
            f"{'✓' if not bad else '✗ ' + bad[0]}")

    # ── 2. 규칙40 — 완화(=핸디캡)의 크기를 같은 실행에서 출력 ────────────
    out()
    out("=" * 70)
    if tot_in:
        out(f"  전송 용량: {tot_in/1024:.0f}KB → {tot_out/1024:.0f}KB "
              f"({(1-tot_out/tot_in)*100:.0f}% 감소)")
        out(f"  이것이 프로덕션에서 GPT-4o 가 지는 핸디캡의 크기입니다.")
        out(f"  IP/174 §1-5 는 JPEG q80 재인코딩만으로 엔진 conf 가 0.93→0.17 로")
        out(f"  무너진 사례를 기록했습니다(n=2, 미확정). GPT-4o 가 같은 질감 손실에")
        out(f"  얼마나 취약한지는 **측정된 적이 없습니다** — 그게 다음 단계입니다.")
    out("=" * 70)

    if failures:
        out()
        out(f"  ✗ 실패 {len(failures)}건 — API 호출을 진행하지 마십시오.")
        for name, why in failures:
            out(f"      {name}: {why}")
        out()
        out("  최소화가 안 걸린 채로 32장을 돌리면 raw 결과와 구별되지 않는")
        out("  숫자가 production 이라는 이름으로 저장됩니다. 그게 이 검사의 이유입니다.")
        return 1

    out()
    out(f"  ✓ 표본 {len(images)}장 전부 통과 — 축 A 유지, 최소화 실동작 확인.")
    out("    이제 accuracy_test.py --preprocess production 을 돌려도 됩니다.")
    out()
    return 0


if __name__ == "__main__":
    sys.exit(main())
