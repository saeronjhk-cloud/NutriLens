#!/usr/bin/env python3
"""
스모크 테스트 — 디버그 엔드포인트 게이트 (DEBUG_ENDPOINTS)
──────────────────────────────────────────────────────────
보안 invariant 박제: "환경변수 미설정(off) = /refcheck·/dbcheck 는 404".

이 테스트가 지키는 것:
  - DEBUG_ENDPOINTS unset/0/false  →  POST /dbcheck·/refcheck = 404 (미지정 경로와 동일)
  - DEBUG_ENDPOINTS=1               →  핸들러 진입(= 404 아님)
  - /analyze 등 일반 경로는 게이트와 무관 (404 아님)
  - GET /dbcheck·/refcheck         →  본문 없는 404 (내부정보 무누설)
  - OPTIONS /dbcheck               →  204 (경로 존재 암시 안 함)

무거운 의존성(food_analyzer=torch/opencv, sessions_store, metrics_store)은
stub 모듈로 격리 → torch 없이 test_server 를 실제 기동해 HTTP 로 검증한다.

실행:  python tests/test_debug_endpoint_gate.py
종료코드 0 = 전부 통과.
"""
import os
import sys
import types
import time
import tempfile
import threading
import importlib
import urllib.request
import urllib.error
from pathlib import Path
from http.server import ThreadingHTTPServer

TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS_DIR))

_TMP = Path(tempfile.mkdtemp(prefix="nl_gate_test_"))


def _install_stubs():
    """torch/sqlite/web-volume 없이 test_server 가 import·기동되도록 가짜 모듈 주입."""
    # ── food_analyzer (모듈로드 시 load_food_db() 1회 실행됨) ──
    fa = types.ModuleType("food_analyzer")
    fa.load_food_db = lambda *a, **k: {}
    fa.match_with_db = lambda analysis, *a, **k: analysis
    fa.SYSTEM_PROMPT = ""
    fa.gold_match_class = lambda *a, **k: ("none", None)
    fa.CORE_FOODS = {}
    fa._search_core_foods = lambda nm: (None, None)
    fa._search_gold = lambda nm: (None, None)
    fa._get_reference_model = lambda: None
    fa.detect_reference_objects = lambda *a, **k: []
    fa.calculate_ppcm = lambda *a, **k: None
    fa._build_reference_hint = lambda *a, **k: ""
    sys.modules["food_analyzer"] = fa

    # ── sessions_store (모듈로드 시 init_db/cleanup/load_all_active 실행됨) ──
    ss = types.ModuleType("sessions_store")
    ss.init_db = lambda *a, **k: None
    ss.cleanup_old_sessions = lambda *a, **k: 0
    ss.load_all_active = lambda *a, **k: {}
    ss.save_session = lambda *a, **k: None
    ss.delete_session = lambda *a, **k: None
    sys.modules["sessions_store"] = ss

    # ── metrics_store ──
    ms = types.ModuleType("metrics_store")
    ms.init_db = lambda *a, **k: None
    ms.load_state = lambda *a, **k: {}
    ms.save_state = lambda *a, **k: None
    ms._metrics_db_path = lambda *a, **k: _TMP / "metrics.db"
    sys.modules["metrics_store"] = ms


def _load_server(debug_value):
    """DEBUG_ENDPOINTS 를 설정/해제하고 test_server 를 (재)로드. 모듈 반환."""
    if debug_value is None:
        os.environ.pop("DEBUG_ENDPOINTS", None)
    else:
        os.environ["DEBUG_ENDPOINTS"] = debug_value
    if "test_server" in sys.modules:
        return importlib.reload(sys.modules["test_server"])
    return importlib.import_module("test_server")


class _Server:
    """랜덤 포트에 NutriLensHandler 를 백그라운드로 기동하는 컨텍스트."""

    def __init__(self, mod):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), mod.NutriLensHandler)
        self.port = self.httpd.server_address[1]

    def __enter__(self):
        self.t = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.t.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()

    def request(self, method, path, data=None):
        """(status_code, body_bytes) 반환. 4xx/5xx 도 예외 없이 코드로 받음."""
        url = f"http://127.0.0.1:{self.port}{path}"
        req = urllib.request.Request(url, data=data, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()


# ── 단언 헬퍼 ──
_failures = []


def check(label, cond):
    mark = "PASS" if cond else "FAIL"
    print(f"  [{mark}] {label}")
    if not cond:
        _failures.append(label)


def run():
    _install_stubs()

    # ===== OFF: DEBUG_ENDPOINTS 미설정 (보안 기본값) =====
    print("OFF — DEBUG_ENDPOINTS 미설정 (기본 보안 상태)")
    mod = _load_server(None)
    with _Server(mod) as s:
        c, _ = s.request("POST", "/dbcheck", b"")
        check("POST /dbcheck = 404", c == 404)
        c, _ = s.request("POST", "/refcheck", b"")
        check("POST /refcheck = 404", c == 404)
        c, b = s.request("GET", "/dbcheck")
        check("GET /dbcheck = 404 & 본문 무누설", c == 404 and len(b) == 0)
        c, b = s.request("GET", "/refcheck")
        check("GET /refcheck = 404 & 본문 무누설", c == 404 and len(b) == 0)
        c, _ = s.request("OPTIONS", "/dbcheck")
        check("OPTIONS /dbcheck = 204 (경로 무관)", c == 204)
        c, _ = s.request("POST", "/nonexistent-xyz", b"")
        check("대조군 POST /nonexistent = 404", c == 404)
        c, _ = s.request("POST", "/analyze", b"")
        check("일반경로 POST /analyze != 404 (게이트 무관)", c != 404)

    # ===== OFF 변형: DEBUG_ENDPOINTS=0 / false =====
    print("OFF 변형 — DEBUG_ENDPOINTS=0, false")
    for val in ("0", "false"):
        mod = _load_server(val)
        with _Server(mod) as s:
            c, _ = s.request("POST", "/dbcheck", b"")
            check(f"DEBUG_ENDPOINTS={val!r} → POST /dbcheck = 404", c == 404)

    # ===== ON: DEBUG_ENDPOINTS=1 =====
    print("ON — DEBUG_ENDPOINTS=1 (진단 활성)")
    mod = _load_server("1")
    with _Server(mod) as s:
        c, _ = s.request("POST", "/dbcheck", b"")
        check("POST /dbcheck != 404 (핸들러 진입)", c != 404)
        c, _ = s.request("POST", "/refcheck", b"")
        check("POST /refcheck != 404 (핸들러 진입)", c != 404)

    # ===== 정리 =====
    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + "; ".join(_failures))
        return 1
    print("ALL PASS — 디버그 엔드포인트 게이트 invariant 통과")
    return 0


if __name__ == "__main__":
    sys.exit(run())
