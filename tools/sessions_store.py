"""
NutriLens 정찬 세션 SQLite 영구 저장소.
Railway 재시작 시 MEAL_SESSIONS 메모리 상태를 복원한다.
"""

import json
import os
import sqlite3
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent
DEFAULT_DB_PATH = PROJECT_DIR / "data" / "sessions.db"


def _db_path() -> Path:
    raw = os.environ.get("SESSION_DB_PATH", "").strip()
    if raw:
        return Path(raw)
    return DEFAULT_DB_PATH


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """meal_sessions 테이블 생성."""
    try:
        with _connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS meal_sessions (
                    user_id TEXT PRIMARY KEY,
                    session_active INTEGER NOT NULL DEFAULT 0,
                    foods TEXT NOT NULL DEFAULT '[]',
                    started_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            conn.commit()
    except Exception as e:
        print(f"[sessions_store] init_db 실패: {e}")


def _parse_foods_payload(foods_raw: str) -> tuple[list, int]:
    """foods 컬럼 JSON → (foods list, photo_count)."""
    try:
        payload = json.loads(foods_raw or "[]")
    except (json.JSONDecodeError, TypeError):
        return [], 0
    if isinstance(payload, dict):
        foods = payload.get("foods", [])
        photo_count = int(payload.get("photo_count", 0))
        if not isinstance(foods, list):
            foods = []
        return foods, photo_count
    if isinstance(payload, list):
        return payload, 0
    return [], 0


def _row_to_dict(row: sqlite3.Row) -> dict:
    foods, photo_count = _parse_foods_payload(row["foods"])
    return {
        "session_active": bool(row["session_active"]),
        "foods": foods,
        "started_at": row["started_at"],
        "updated_at": row["updated_at"],
        "created": row["started_at"],
        "photo_count": photo_count,
    }


def load_session(user_id: str) -> dict | None:
    """단일 사용자 세션 로드."""
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT * FROM meal_sessions WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if not row:
            return None
        data = _row_to_dict(row)
        # photo_count는 foods 길이 기반 추정 불가 → updated_at 메타는 foods JSON에 포함 가능
        # save_session 시 photo_count를 foods 옆 메타로 foods 필드에 넣지 않고 별도 처리
        return data
    except Exception as e:
        print(f"[sessions_store] load_session({user_id}) 실패: {e}")
        return None


def save_session(user_id: str, data: dict) -> None:
    """write-through 저장."""
    try:
        now = time.time()
        foods = data.get("foods", [])
        if not isinstance(foods, list):
            foods = []
        payload = {
            "foods": foods,
            "photo_count": int(data.get("photo_count", 0)),
        }
        started_at = float(data.get("started_at") or data.get("created") or now)
        updated_at = float(data.get("updated_at") or now)
        session_active = 1 if data.get("session_active") else 0
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO meal_sessions (user_id, session_active, foods, started_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    session_active = excluded.session_active,
                    foods = excluded.foods,
                    started_at = excluded.started_at,
                    updated_at = excluded.updated_at
                """,
                (
                    user_id,
                    session_active,
                    json.dumps(payload, ensure_ascii=False),
                    started_at,
                    updated_at,
                ),
            )
            conn.commit()
    except Exception as e:
        print(f"[sessions_store] save_session({user_id}) 실패: {e}")


def delete_session(user_id: str) -> None:
    try:
        with _connect() as conn:
            conn.execute("DELETE FROM meal_sessions WHERE user_id = ?", (user_id,))
            conn.commit()
    except Exception as e:
        print(f"[sessions_store] delete_session({user_id}) 실패: {e}")


def cleanup_old_sessions(max_age_hours: int = 24) -> int:
    """오래된 세션 삭제. 삭제 건수 반환."""
    cutoff = time.time() - max_age_hours * 3600
    try:
        with _connect() as conn:
            cur = conn.execute(
                "DELETE FROM meal_sessions WHERE updated_at < ?",
                (cutoff,),
            )
            conn.commit()
            return cur.rowcount
    except Exception as e:
        print(f"[sessions_store] cleanup_old_sessions 실패: {e}")
        return 0


def load_all_active() -> dict:
    """서버 시작 시 활성 세션만 메모리 복원."""
    result = {}
    try:
        with _connect() as conn:
            rows = conn.execute(
                "SELECT * FROM meal_sessions WHERE session_active = 1"
            ).fetchall()
        for row in rows:
            uid = row["user_id"]
            data = _row_to_dict(row)
            data["session_active"] = True
            result[uid] = data
    except Exception as e:
        print(f"[sessions_store] load_all_active 실패: {e}")
    return result
