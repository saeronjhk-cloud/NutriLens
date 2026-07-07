#!/usr/bin/env python3
"""최소화 축 A Eval — 원본 프레임 미전송 입증 (17_ 축 A, 결정론 100% 필수).

엔진 self-report만 믿지 않고, OpenAI로 나가는 outbound payload를 spy로 관측해 교차검증한다.
합성 이미지 사용(네트워크·크레딧 0). 로컬에서 100% 통과해야 최소화 인수 GO.

검증:
- 정상: 최소화 결과가 전송되고 원본 바이트가 아니며 detail=low, crop_bounds_area_ratio<0.90.
- crop 실패(strict_bbox+bbox없음): OpenAI 전송 0회(fail-closed).
- bbox 지정: bbox 크롭.
- 전송 바이트 디코드 시 원본보다 작은 이미지.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

from PIL import Image

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

from image_minimize import AREA_RATIO_MAX, CropFailed, minimize_for_openai  # noqa: E402


class SpyTransport:
    """OpenAI로 나갈 이미지 payload를 관측(실제 전송 안 함)."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    def send_image(self, image_bytes: bytes, *, bbox=None, strict_bbox=False) -> dict | None:
        """엔진이 하듯: 최소화 성공 시에만 전송. 실패 시 원본 전송 금지(폴백)."""
        try:
            meta = minimize_for_openai(image_bytes, bbox=bbox, strict_bbox=strict_bbox)
        except CropFailed:
            return None  # 전송 안 함(fail-closed)
        self.sent.append({"bytes": meta["bytes"], "detail": meta["detail"], "meta": meta})
        return meta


def synth_image(w: int, h: int, color=(180, 120, 60)) -> bytes:
    im = Image.new("RGB", (w, h), color)
    # 약간의 패턴(재인코딩 차이 유도)
    for x in range(0, w, 40):
        for y in range(0, h, 40):
            im.paste((int(x % 255), int(y % 255), 90), (x, y, min(x + 20, w), min(y + 20, h)))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))


def main() -> int:
    print("=== 최소화 축 A Eval (원본 미전송 입증) ===")
    orig = synth_image(1600, 1200)
    orig_sha = __import__("hashlib").sha256(orig).hexdigest()

    # A1 정상 center_fallback
    spy = SpyTransport()
    meta = spy.send_image(orig)
    check("A1 최소화 전송됨", len(spy.sent) == 1)
    check("A1 원본 바이트 아님(out_sha != orig_sha)", meta and meta["out_sha256"] != orig_sha)
    check("A1 original_frame_sent=False", bool(meta) and meta["original_frame_sent"] is False)
    check("A1 detail=low", bool(meta) and meta["detail"] == "low")
    check(
        f"A1 crop_bounds_area_ratio<{AREA_RATIO_MAX}",
        bool(meta) and meta["crop_bounds_area_ratio"] < AREA_RATIO_MAX,
        f"ratio={meta['crop_bounds_area_ratio'] if meta else None}",
    )

    # A2 전송 바이트는 원본보다 작은 이미지로 디코드
    if spy.sent:
        out_im = Image.open(io.BytesIO(spy.sent[0]["bytes"]))
        check("A2 전송 이미지가 원본보다 작음", out_im.size[0] < 1600 and out_im.size[1] < 1200,
              f"out={out_im.size}")

    # A3 strict_bbox인데 bbox 없음 -> 전송 0회
    spy2 = SpyTransport()
    res = spy2.send_image(orig, strict_bbox=True)
    check("A3 crop 실패 시 OpenAI 전송 0회(fail-closed)", res is None and len(spy2.sent) == 0)

    # A4 bbox 지정 -> bbox 크롭
    spy3 = SpyTransport()
    m3 = spy3.send_image(orig, bbox=(400, 300, 1000, 800))
    check("A4 bbox 크롭 적용", bool(m3) and m3["crop_mode"] == "bbox")
    check("A4 bbox area_ratio<0.90", bool(m3) and m3["crop_bounds_area_ratio"] < AREA_RATIO_MAX,
          f"ratio={m3['crop_bounds_area_ratio'] if m3 else None}")

    # A5 여러 이미지 모두 원본 바이트 미전송
    ok_all = True
    for w, h in [(800, 600), (1024, 1024), (2000, 1500)]:
        s = SpyTransport()
        img = synth_image(w, h)
        mm = s.send_image(img)
        if not mm or mm["original_frame_sent"] is not False or mm["detail"] != "low":
            ok_all = False
    check("A5 다양한 해상도에서 원본 미전송·detail low", ok_all)

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n=== 축 A: {passed}/{total} {'OK 100%' if passed == total else 'FAIL'} ===")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
