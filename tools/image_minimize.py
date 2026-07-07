"""OpenAI Vision 전송용 서버 강제 최소화 (16_ L1 crop + L2 detail:low).

원칙: 원본 프레임은 OpenAI로 절대 나가지 않는다(17_ 축 A).
- 다운스케일(max edge) + JPEG 재인코딩 -> 원본 바이트 미전송 보장.
- crop: bbox 있으면 음식영역+패딩, 없으면 center-crop(가장자리 배경/프라이버시 큐 축소).
- detail=low 강제.
- strict_bbox=True에서 bbox 없으면 CropFailed(계약 16_ 4-2: 원본 전송 금지, fail-closed).

반환 dict를 outbound wrapper가 검증: original_frame_sent=False, crop_bounds_area_ratio<0.90.
"""
from __future__ import annotations

import hashlib
import io
from typing import Any

from PIL import Image

MAX_EDGE = 768          # 다운스케일 최대 변
CENTER_INSET = 0.10     # center-crop 시 각 변에서 10% 제거 -> 면적비 ~0.64
BBOX_PAD = 0.10         # bbox 각 변 10% 패딩(컨텍스트 보존)
AREA_RATIO_MAX = 0.90   # 축 A: 크롭 후 면적비가 이 값 미만이어야 '크롭됨' 입증
JPEG_QUALITY = 80


class CropFailed(Exception):
    """crop 불가(예: strict_bbox인데 bbox 없음, 빈 crop). 원본 전송 금지 -> 폴백."""


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def minimize_for_openai(
    image_bytes: bytes,
    *,
    bbox: tuple[float, float, float, float] | None = None,
    strict_bbox: bool = False,
    max_edge: int = MAX_EDGE,
    inset: float = CENTER_INSET,
) -> dict[str, Any]:
    """원본 이미지 -> 최소화 crop 바이트 + 메타. OpenAI엔 이 결과만 보낸다.

    Raises:
        CropFailed: strict_bbox인데 bbox 없음 / 빈 crop / 디코드 실패.
    """
    orig_sha = _sha(image_bytes)
    try:
        im = Image.open(io.BytesIO(image_bytes))
        im = im.convert("RGB")
    except Exception as e:  # noqa: BLE001
        raise CropFailed(f"decode_failed:{e}")

    ow, oh = im.size
    orig_area = float(ow * oh) or 1.0

    if bbox is not None:
        x1, y1, x2, y2 = bbox
        pad_x = (x2 - x1) * BBOX_PAD
        pad_y = (y2 - y1) * BBOX_PAD
        cx1 = max(0, int(x1 - pad_x))
        cy1 = max(0, int(y1 - pad_y))
        cx2 = min(ow, int(x2 + pad_x))
        cy2 = min(oh, int(y2 + pad_y))
        crop_mode = "bbox"
    elif strict_bbox:
        raise CropFailed("no_bbox")
    else:
        cx1 = int(ow * inset)
        cy1 = int(oh * inset)
        cx2 = int(ow * (1.0 - inset))
        cy2 = int(oh * (1.0 - inset))
        crop_mode = "center_fallback"

    if cx2 <= cx1 or cy2 <= cy1:
        raise CropFailed("empty_crop")

    crop = im.crop((cx1, cy1, cx2, cy2))
    cw, ch = crop.size
    area_ratio = (cw * ch) / orig_area

    scale = min(1.0, max_edge / float(max(cw, ch)))
    if scale < 1.0:
        crop = crop.resize((max(1, int(cw * scale)), max(1, int(ch * scale))))

    buf = io.BytesIO()
    crop.save(buf, format="JPEG", quality=JPEG_QUALITY)
    out = buf.getvalue()
    out_sha = _sha(out)

    return {
        "bytes": out,
        "mime": "image/jpeg",
        "detail": "low",
        "crop_mode": crop_mode,
        "crop_bounds_area_ratio": round(area_ratio, 4),
        "original_frame_sent": False,      # 재인코딩+crop -> 원본 바이트 아님
        "out_width": crop.size[0],
        "out_height": crop.size[1],
        "orig_sha256": orig_sha,
        "out_sha256": out_sha,
    }


def minimize_to_data_url(image_bytes: bytes, **kwargs: Any) -> tuple[str, dict[str, Any]]:
    """OpenAI image_url용 data URL + 메타. (base64는 최소화 결과만 인코딩)."""
    import base64

    meta = minimize_for_openai(image_bytes, **kwargs)
    b64 = base64.b64encode(meta["bytes"]).decode()
    data_url = f"data:{meta['mime']};base64,{b64}"
    return data_url, meta
