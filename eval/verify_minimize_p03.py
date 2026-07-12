import io, sys
from PIL import Image
sys.path.insert(0, '.')
from image_minimize import minimize_for_openai, minimize_to_data_url, CropFailed

def make_img(w, h, with_exif=False):
    im = Image.new("RGB", (w, h), (120, 80, 40))
    buf = io.BytesIO()
    if with_exif:
        # 간단 EXIF(Orientation) 삽입
        exif = Image.Exif(); exif[274] = 6  # Orientation
        im.save(buf, format="JPEG", exif=exif.tobytes())
    else:
        im.save(buf, format="JPEG")
    return buf.getvalue()

p=f=0
def ok(c,l):
    global p,f
    print(("PASS " if c else "FAIL ")+l); 
    p+= 1 if c else 0; f+= 0 if c else 1

# 1) 큰 원본 → 최소화: 원본 미전송·detail low·crop·다운스케일
big = make_img(2000, 1500, with_exif=True)
m = minimize_for_openai(big)
ok(m["original_frame_sent"] is False, "1 original_frame_sent=False")
ok(m["detail"]=="low", "2 detail=low")
ok(m["crop_bounds_area_ratio"]<0.90, f"3 crop area_ratio<0.90 ({m['crop_bounds_area_ratio']})")
ok(m["out_sha256"]!=m["orig_sha256"], "4 출력 바이트 != 원본(재인코딩)")
ok(max(m["out_width"],m["out_height"])<=768, f"5 max_edge<=768 ({m['out_width']}x{m['out_height']})")
# EXIF 제거 확인: 출력 JPEG에 EXIF 없음
from PIL import Image as I2
outim = I2.open(io.BytesIO(m["bytes"]))
ok(not outim.getexif(), "6 출력에 EXIF 없음(재인코딩으로 제거)")

# 2) strict_bbox + bbox 없음 → CropFailed(fail-closed)
try:
    minimize_for_openai(big, strict_bbox=True)
    ok(False, "7 strict_bbox no-bbox → CropFailed")
except CropFailed:
    ok(True, "7 strict_bbox no-bbox → CropFailed(원본 전송 금지)")

# 3) bbox 지정 → 그 영역만 crop
m2 = minimize_for_openai(big, bbox=(500,400,900,700))
ok(m2["crop_mode"]=="bbox", "8 bbox crop_mode")
ok(m2["original_frame_sent"] is False, "9 bbox도 원본 미전송")

# 4) data_url 형식
url, meta = minimize_to_data_url(big)
ok(url.startswith("data:image/jpeg;base64,"), "10 data_url 형식")
ok(meta["detail"]=="low", "11 data_url detail=low")

print(f"\nSUMMARY: PASS={p} FAIL={f}")
sys.exit(1 if f else 0)
