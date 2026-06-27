"""
NutriLens 모니터링 메트릭 SQLite 영속 저장소.
Railway 재시작/재배포 시 카운터·전이표(모니터링 #3/#4)를 복원한다.
sessions.db와 같은 영속 볼륨 디렉토리에 metrics.db로 저장 → 재배포에도 유지.
"""

import json
import os
import sqlite3
import time
from pathlib import Path

try:
    from sessions_store import _db_path as _sessions_db_path
except Exception:
    _sessions_db_path = None


def _metrics_db_path() -> Path:
    """metrics.db 경로. 우선순위: METRICS_DB_PATH 환경변수 → sessions.db와 같은 디렉토리 → 기본."""
    raw = os.environ.get("METRICS_DB_PATH", "").strip()
    if raw:
        return Path(raw)
    if _sessions_db_path is not None:
        try:
            return _sessions_db_path().parent / "metrics.db"
        except Exception:
            pass
    return Path(__file__).parent.parent / "data" / "metrics.db"


def _connect() -> sqlite3.Connection:
    path = _metrics_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10)
    return conn


def init_db() -> None:
    """key-value 메트릭 테이블 생성."""
    try:
        with _connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS metrics_kv (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            conn.commit()
    except Exception as e:
        print(f"[metrics_store] init_db 실패: {e}")


def save_state(state: dict) -> None:
    """state: {key: json직렬화가능}. 원자적 upsert (key별 덮어쓰기)."""
    try:
        now = time.time()
        with _connect() as conn:
            for k, v in state.items():
                conn.execute(
                    "INSERT INTO metrics_kv(key, value, updated_at) VALUES(?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                    (k, json.dumps(v, ensure_ascii=False), now),
                )
            conn.commit()
    except Exception as e:
        print(f"[metrics_store] save 실패: {e}")


def load_state() -> dict:
    """저장된 모든 key→값(역직렬화) 반환. 없으면 빈 dict."""
    try:
        with _connect() as conn:
            rows = conn.execute("SELECT key, value FROM metrics_kv").fetchall()
        out = {}
        for k, v in rows:
            try:
                out[k] = json.loads(v)
            except Exception:
                pass
        return out
    except Exception as e:
        print(f"[metrics_store] load 실패: {e}")
        return {}
