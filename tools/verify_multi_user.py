#!/usr/bin/env python3
"""Multi-user session + uid + SQLite 복원 검증 (API 호출 없음)."""

import json
import os
import sys
import tempfile
import threading
import time
import uuid
from http.client import HTTPConnection
from pathlib import Path

TOOLS_DIR = Path(__file__).parent
PROJECT_DIR = TOOLS_DIR.parent
sys.path.insert(0, str(TOOLS_DIR))

# 테스트용 임시 DB
_tmp = tempfile.mkdtemp()
TEST_DB = Path(_tmp) / "test_sessions.db"
os.environ["SESSION_DB_PATH"] = str(TEST_DB)
os.environ["ALLOWED_PARENT_ORIGIN"] = "https://example.lovableproject.com"

UID_A = "11111111-1111-4111-8111-111111111111"
UID_B = "22222222-2222-4222-8222-222222222222"
BAD_UID = "not-a-uuid"

# test_server 모듈 로드 (서버는 띄우지 않음)
import importlib
import test_server as ts
importlib.reload(ts)
import sessions_store

sessions_store.init_db()
ts.MEAL_SESSIONS = sessions_store.load_all_active()

passed = 0
failed = 0


def check(cond, name, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  OK {name}")
    else:
        failed += 1
        print(f"  FAIL {name}: {detail}")


def start_server(port):
    from http.server import ThreadingHTTPServer
    server = ThreadingHTTPServer(("127.0.0.1", port), ts.NutriLensHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


def post(conn, path, body=None, extra_headers=None):
    headers = {"Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    data = json.dumps(body or {}).encode("utf-8") if body is not None else b""
    conn.request("POST", path, body=data, headers=headers)
    resp = conn.getresponse()
    raw = resp.read().decode("utf-8")
    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        payload = {"_raw": raw}
    return resp.status, payload, dict(resp.getheaders())


def main():
    print("\n=== Multi-user / uid / SQLite 검증 ===\n")

    port = 18765
    server = start_server(port)
    time.sleep(0.3)
    conn = HTTPConnection("127.0.0.1", port, timeout=10)

    # uid 누락 → 400
    status, data, _ = post(conn, "/session/start", {})
    check(status == 400 and "uid" in data.get("error", ""), "uid 누락 시 400")

    # 잘못된 uid → 400
    status, data, _ = post(conn, f"/session/start?uid={BAD_UID}", {})
    check(status == 400, "잘못된 UUID 형식 400")

    # 두 사용자 동시 정찬 시작
    status_a, data_a, _ = post(conn, f"/session/start?uid={UID_A}", {})
    status_b, data_b, _ = post(conn, f"/session/start?uid={UID_B}", {})
    check(status_a == 200 and data_a.get("session_active"), "user A 세션 시작")
    check(status_b == 200 and data_b.get("session_active"), "user B 세션 시작")
    check(UID_A in ts.MEAL_SESSIONS and UID_B in ts.MEAL_SESSIONS, "메모리에 2개 세션 분리")

    # A에만 음식 추가 시뮬레이션
    with ts._SESSIONS_LOCK:
        ts.MEAL_SESSIONS[UID_A]["foods"].append({"name_ko": "쌀밥", "calories_kcal": 150})
        ts.MEAL_SESSIONS[UID_A]["photo_count"] = 1
    sessions_store.save_session(UID_A, ts.MEAL_SESSIONS[UID_A])

    foods_a = ts.MEAL_SESSIONS[UID_A]["foods"]
    foods_b = ts.MEAL_SESSIONS[UID_B]["foods"]
    check(len(foods_a) == 1 and len(foods_b) == 0, "uid별 foods 분리")

    # SQLite 확인
    loaded = sessions_store.load_session(UID_A)
    check(loaded and len(loaded.get("foods", [])) == 1, "SQLite에 user A foods 저장")

    # OPTIONS preflight + CORS
    conn.request("OPTIONS", f"/session/start?uid={UID_A}", headers={"Origin": "https://example.lovableproject.com"})
    opt = conn.getresponse()
    opt.read()
    cors = opt.getheader("Access-Control-Allow-Origin")
    check(opt.status == 204 and cors == "https://example.lovableproject.com", "OPTIONS 204 + CORS origin")

    # 서버 재시작 시뮬레이션 — 메모리 비우고 load_all_active
    ts.MEAL_SESSIONS.clear()
    restored = sessions_store.load_all_active()
    check(UID_A in restored and len(restored[UID_A]["foods"]) == 1, "재시작 후 SQLite에서 세션 복원")
    print(f"  [load_all_active] 복원 키: {list(restored.keys())}")

    server.shutdown()
    print(f"\n=== 결과: {passed} 통과, {failed} 실패 ===\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
