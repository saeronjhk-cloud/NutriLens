#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OpenAI 연결 진단 — 어디서 막히는지 단계별로 좁힌다. (세션46, 2026-08-19)

왜 이게 필요한가
───────────────────────────────────────────────────────────────────────────
G4 4단계에서 32장 전부가 「API 호출 실패: timed out」으로 죽었습니다.
`food_analyzer.py` 의 예외 처리는 httpx 예외를 문자열로만 남기므로
**DNS · TCP · TLS · 인증 · 응답지연 중 무엇이 원인인지 구분이 안 됩니다.**

이 스크립트는 그걸 단계로 쪼개 각 단계의 소요시간을 찍습니다.
미봉책(타임아웃만 늘리기)으로 넘어가지 않기 위한 도구입니다.

실행
───────────────────────────────────────────────────────────────────────────
    cd /d "D:\\서박사의 영양공식\\backends\\NutriLens"
    python tools\\diagnose_openai.py

단계
  0  키 로드 (.env)                        — 있는가, 형식이 맞는가
  1  DNS  api.openai.com                   — 이름이 풀리는가
  2  TCP  443 연결                          — 포트가 열리는가
  3  TLS  핸드셰이크                        — 중간자/방화벽이 끊는가
  4  API  텍스트 전용 호출 (이미지 없음)      — 키가 유효한가 · API 가 살아 있는가
  5  API  작은 이미지 (01_김치, detail=low)  — 이미지 경로가 되는가
  6  API  작은 이미지 (detail=high)          — high 가 느린가
  7  API  큰 이미지  (101_콘치즈 7MB, high)  — 크기가 원인인가

읽는 법
  1~3 에서 막힘  → 네트워크(방화벽·VPN·프록시). 코드 문제 아님.
  4 에서 401     → 키 문제.  4 에서 timeout → API 도달 불가 또는 장애.
  4 는 되는데 5~7 이 실패 → 이미지 페이로드 문제. 여기서 비로소 타임아웃/축소를 논한다.
"""

import os
import sys
import ssl
import json
import time
import socket
import base64
from pathlib import Path

HOST = "api.openai.com"
PORT = 443
ROOT = Path(__file__).parent.parent


def load_key():
    """food_analyzer 와 같은 방식으로 .env 를 읽는다."""
    if os.environ.get('OPENAI_API_KEY'):
        return os.environ['OPENAI_API_KEY'], '환경변수'
    for p in (ROOT / '.env', Path.cwd() / '.env'):
        if not p.exists():
            continue
        for line in p.read_text(encoding='utf-8', errors='ignore').splitlines():
            line = line.strip()
            if line.startswith('OPENAI_API_KEY') and '=' in line:
                v = line.split('=', 1)[1].strip().strip('"').strip("'")
                if v:
                    return v, str(p)
    return None, None


def step(n, title):
    print(f"\n[{n}] {title}")
    print("    " + "-" * 60)


def ok(t0, msg=""):
    print(f"    ✓ {(time.time()-t0)*1000:7.0f} ms   {msg}")


def fail(t0, e):
    print(f"    ✗ {(time.time()-t0)*1000:7.0f} ms   {type(e).__name__}: {e}")


def api_call(key, payload, timeout, label):
    """httpx 로 호출하되 실패 유형을 구분해 찍는다."""
    import httpx
    t0 = time.time()
    try:
        r = httpx.post(f"https://{HOST}/v1/chat/completions",
                       headers={"Authorization": f"Bearer {key}",
                                "Content-Type": "application/json"},
                       json=payload, timeout=timeout)
    except Exception as e:
        # httpx 예외 클래스명이 원인을 말해 준다:
        #   ConnectTimeout=연결 못 함 / ReadTimeout=응답이 늦음 / ConnectError=경로 없음
        fail(t0, e)
        return None
    dt = (time.time() - t0) * 1000
    if r.status_code != 200:
        body = r.text[:200].replace('\n', ' ')
        print(f"    ✗ {dt:7.0f} ms   HTTP {r.status_code}  {body}")
        return None
    try:
        j = r.json()
        usage = j.get('usage', {})
        print(f"    ✓ {dt:7.0f} ms   {label}  "
              f"(prompt {usage.get('prompt_tokens','?')} tok)")
        return j
    except Exception as e:
        fail(t0, e)
        return None


def img_payload(path, detail, max_tokens=50):
    b64 = base64.b64encode(Path(path).read_bytes()).decode()
    return {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": "이 사진의 음식 이름만 한 단어로 답하세요."},
            {"type": "image_url",
             "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": detail}},
        ]}],
        "max_tokens": max_tokens,
        "temperature": 0,
    }, len(b64)


def main():
    print("=" * 66)
    print("  OpenAI 연결 진단 — food30 G4 4단계 타임아웃 원인 규명")
    print("=" * 66)

    # 프록시 환경변수는 httpx 가 자동으로 씁니다. 있으면 그게 원인일 수 있습니다.
    proxies = {k: v for k, v in os.environ.items()
               if k.lower() in ('http_proxy', 'https_proxy', 'all_proxy', 'no_proxy')}
    print(f"\n  프록시 환경변수: {proxies if proxies else '없음'}")

    step(0, "키 로드")
    key, src = load_key()
    if not key:
        print("    ✗ OPENAI_API_KEY 를 찾지 못했습니다 (.env · 환경변수 모두).")
        return 1
    print(f"    ✓ 출처={src}  길이={len(key)}  시작={key[:7]}…  끝=…{key[-4:]}")
    if not key.startswith('sk-'):
        print("    ⚠ 'sk-' 로 시작하지 않습니다. 키 형식을 확인하십시오.")

    step(1, f"DNS 조회  {HOST}")
    t0 = time.time()
    try:
        addrs = sorted({a[4][0] for a in socket.getaddrinfo(HOST, PORT)})
        ok(t0, ", ".join(addrs[:4]))
    except Exception as e:
        fail(t0, e)
        print("\n  ▶ DNS 가 안 됩니다. 네트워크·DNS 설정 문제이며 코드와 무관합니다.")
        return 1

    step(2, f"TCP 연결  {HOST}:{PORT}")
    t0 = time.time()
    try:
        s = socket.create_connection((HOST, PORT), timeout=15)
        ok(t0)
    except Exception as e:
        fail(t0, e)
        print("\n  ▶ 443 이 막혀 있습니다. 방화벽·VPN·회사망을 확인하십시오.")
        return 1

    step(3, "TLS 핸드셰이크")
    t0 = time.time()
    try:
        ctx = ssl.create_default_context()
        with ctx.wrap_socket(s, server_hostname=HOST) as ss:
            cert = ss.getpeercert()
            issuer = dict(x[0] for x in cert.get('issuer', ()))
            ok(t0, f"발급자={issuer.get('organizationName', '?')}")
    except Exception as e:
        fail(t0, e)
        print("\n  ▶ TLS 가 끊깁니다. 백신·기업 프록시의 SSL 검사를 의심하십시오.")
        return 1
    finally:
        try:
            s.close()
        except Exception:
            pass

    step(4, "API 호출 — 텍스트 전용 (이미지 없음, timeout=30)")
    r = api_call(key, {"model": "gpt-4o",
                       "messages": [{"role": "user", "content": "1+1? 숫자만."}],
                       "max_tokens": 5, "temperature": 0}, 30.0, "텍스트 OK")
    if r is None:
        print("\n  ▶ 1~3 은 됐는데 API 호출이 실패했습니다.")
        print("    · HTTP 401 → 키가 무효/만료. 재발급 필요.")
        print("    · HTTP 429 → 한도 초과. 결제·쿼터 확인.")
        print("    · ReadTimeout → OpenAI 응답 지연 또는 장애.")
        print("    이미지와 무관한 호출이므로 이미지 크기는 원인이 아닙니다.")
        return 1

    small = ROOT / '.tmp' / 'test_images' / '01_김치.jpg'
    big = ROOT / '.tmp' / 'test_images' / '101_콘치즈.jpg'

    if small.exists():
        p, n = img_payload(small, "low")
        step(5, f"API 호출 — 작은 이미지 detail=low  (base64 {n/1024:.0f} KB, timeout=30)")
        api_call(key, p, 30.0, "저해상도 OK")

        p, n = img_payload(small, "high")
        step(6, f"API 호출 — 작은 이미지 detail=high (base64 {n/1024:.0f} KB, timeout=60)")
        api_call(key, p, 60.0, "고해상도 OK")
    else:
        print(f"\n  [5,6] 건너뜀 — {small} 없음")

    if big.exists():
        p, n = img_payload(big, "high")
        step(7, f"API 호출 — 큰 이미지 detail=high  (base64 {n/1024:.0f} KB, timeout=120)")
        t0 = time.time()
        res = api_call(key, p, 120.0, "대용량 OK")
        if res is not None:
            el = time.time() - t0
            print(f"\n    ※ 이 한 장에 {el:.1f}초 걸렸습니다. "
                  f"현재 코드 타임아웃은 30초입니다.")
            if el > 30:
                print("      → 30초로는 이 사진을 절대 못 받습니다. 원인 확정.")
    else:
        print(f"\n  [7] 건너뜀 — {big} 없음")

    print("\n" + "=" * 66)
    print("  진단 끝. 이 출력을 통째로 Claude 에게 보내십시오.")
    print("=" * 66)
    return 0


if __name__ == '__main__':
    sys.exit(main())
