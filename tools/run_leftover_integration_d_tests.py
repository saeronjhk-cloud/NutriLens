#!/usr/bin/env python3
"""
Doc 37 §D — meal-leftover 통합테스트 (D1~D9) 자동화.
테스트 전용 계정만 사용. 실계정 금지.

필수 env (.env 또는 환경변수):
  ENGINE_API_KEY              — Railway 엔진 키 (Supabase secret과 동기화)
  INTEGRATION_TEST_EMAIL      — 테스트 계정 이메일
  INTEGRATION_TEST_PASSWORD   — 테스트 계정 비밀번호

선택:
  SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY
  (미설정 시 supabase CLI api-keys / 기본 URL 사용)
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

PROJECT_DIR = Path(__file__).resolve().parent.parent
PROJECT_REF = "ndxgxxrklkltizrnfkcx"
DEFAULT_SUPABASE_URL = f"https://{PROJECT_REF}.supabase.co"
LEFTOVER_URL = f"{DEFAULT_SUPABASE_URL}/functions/v1/meal-leftover"
ENGINE_HEALTH_URL = "https://web-production-0cbc5.up.railway.app/v1/health"

TOL = 0.05


def load_dotenv() -> None:
    env_path = PROJECT_DIR / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def supabase_cli(*args: str) -> str:
    try:
        if os.name == "nt":
            cmd = "supabase " + subprocess.list2cmdline(list(args))
            return subprocess.check_output(
                cmd,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=PROJECT_DIR,
                shell=True,
            )
        return subprocess.check_output(
            ["supabase", *args],
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=PROJECT_DIR,
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"supabase {' '.join(args)} failed:\n{e.output}") from e


def parse_secrets_digest(name: str) -> str | None:
    out = strip_ansi(supabase_cli("secrets", "list", "--project-ref", PROJECT_REF))
    for line in out.splitlines():
        if "|" not in line or name not in line:
            continue
        parts = [p.strip() for p in line.split("|") if p.strip()]
        if len(parts) >= 2 and parts[0] == name:
            return parts[1]
    return None


def strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", text)


def fetch_api_keys() -> tuple[str, str]:
    out = strip_ansi(supabase_cli("projects", "api-keys", "--project-ref", PROJECT_REF))
    jwt_tokens = re.findall(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", out)
    anon = service = None
    for line in out.splitlines():
        if "| anon" in line and "KEY VALUE" not in line:
            parts = [p.strip() for p in line.split("|") if p.strip()]
            if len(parts) >= 2:
                anon = parts[-1]
        elif "| service_role" in line:
            parts = [p.strip() for p in line.split("|") if p.strip()]
            if len(parts) >= 2:
                service = parts[-1]
    # Fallback: first two JWT-looking tokens are anon then service_role in CLI output
    if (not anon or not service) and len(jwt_tokens) >= 2:
        anon = anon or jwt_tokens[0]
        service = service or jwt_tokens[1]
    if not anon or not service:
        anon = os.environ.get("SUPABASE_ANON_KEY", "").strip()
        service = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not anon or not service:
        raise RuntimeError("Could not resolve anon/service_role (CLI parse + env both empty)")
    return anon, service


def ensure_engine_api_key_sync() -> None:
    local = os.environ.get("ENGINE_API_KEY", "").strip()
    if not local:
        raise RuntimeError("ENGINE_API_KEY missing in .env / environment")
    digest = parse_secrets_digest("ENGINE_API_KEY")
    if not digest:
        print("[secrets] ENGINE_API_KEY not in Supabase — setting...")
        supabase_cli("secrets", "set", f"ENGINE_API_KEY={local}", "--project-ref", PROJECT_REF)
        print("[secrets] ENGINE_API_KEY set OK")
        return
    local_digest = sha256_hex(local)
    if local_digest != digest:
        print("[secrets] ENGINE_API_KEY mismatch — updating Supabase secret...")
        supabase_cli("secrets", "set", f"ENGINE_API_KEY={local}", "--project-ref", PROJECT_REF)
        print("[secrets] ENGINE_API_KEY synced OK")
    else:
        print("[secrets] ENGINE_API_KEY matches Railway/.env digest OK")


def http_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: dict | None = None,
    timeout: float = 30.0,
) -> tuple[int, dict[str, Any]]:
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode()
        hdrs.setdefault("Content-Type", "application/json")
    req = Request(url, data=data, headers=hdrs, method=method)
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw else {}
    except HTTPError as e:
        raw = e.read().decode()
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {"raw": raw}
        return e.code, parsed


def auth_login(email: str, password: str, anon_key: str) -> str:
    url = f"{os.environ.get('SUPABASE_URL', DEFAULT_SUPABASE_URL)}/auth/v1/token?grant_type=password"
    status, body = http_json(
        "POST",
        url,
        headers={"apikey": anon_key, "Content-Type": "application/json"},
        body={"email": email, "password": password},
    )
    if status != 200 or not body.get("access_token"):
        raise RuntimeError(f"Auth login failed ({status}): {body}")
    return body["access_token"]


def admin_headers(service_key: str) -> dict[str, str]:
    return {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def rest_select(service_key: str, table: str, query: str) -> list[dict]:
    base = os.environ.get("SUPABASE_URL", DEFAULT_SUPABASE_URL)
    url = f"{base}/rest/v1/{table}?{query}"
    status, body = http_json("GET", url, headers=admin_headers(service_key))
    if status != 200:
        raise RuntimeError(f"REST select {table} failed ({status}): {body}")
    return body if isinstance(body, list) else []


def rest_insert(service_key: str, table: str, row: dict) -> dict:
    base = os.environ.get("SUPABASE_URL", DEFAULT_SUPABASE_URL)
    url = f"{base}/rest/v1/{table}"
    status, body = http_json("POST", url, headers=admin_headers(service_key), body=row)
    if status not in (200, 201):
        raise RuntimeError(f"REST insert {table} failed ({status}): {body}")
    if isinstance(body, list) and body:
        return body[0]
    return body if isinstance(body, dict) else {}


def rest_delete(service_key: str, table: str, query: str) -> None:
    base = os.environ.get("SUPABASE_URL", DEFAULT_SUPABASE_URL)
    url = f"{base}/rest/v1/{table}?{query}"
    status, body = http_json("DELETE", url, headers=admin_headers(service_key))
    if status not in (200, 204):
        raise RuntimeError(f"REST delete {table} failed ({status}): {body}")


def ensure_user_consent(user_id: str, service_key: str) -> None:
    rows = rest_select(
        service_key,
        "user_consent",
        f"user_id=eq.{user_id}&purpose=eq.privacy_basic&granted=eq.true&revoked_at=is.null&select=id&limit=1",
    )
    if rows:
        return
    rest_insert(
        service_key,
        "user_consent",
        {
            "user_id": user_id,
            "purpose": "privacy_basic",
            "version": "integration-test-v1",
            "granted": True,
        },
    )


def decode_jwt_sub(jwt: str) -> str:
    import base64

    payload = jwt.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    data = json.loads(base64.urlsafe_b64decode(payload))
    return data["sub"]


INTEGRATION_TEST_EMAIL_DEFAULT = "d-leftover-integration@test.nutriformula.local"


def admin_create_user(service_key: str, email: str, password: str) -> str:
    base = os.environ.get("SUPABASE_URL", DEFAULT_SUPABASE_URL)
    url = f"{base}/auth/v1/admin/users"
    status, body = http_json(
        "POST",
        url,
        headers=admin_headers(service_key),
        body={"email": email, "password": password, "email_confirm": True},
    )
    if status not in (200, 201):
        raise RuntimeError(f"admin create user failed ({status}): {body}")
    return body["id"]


def resolve_test_credentials(service_key: str) -> tuple[str, str]:
    email = os.environ.get("INTEGRATION_TEST_EMAIL", "").strip()
    password = os.environ.get("INTEGRATION_TEST_PASSWORD", "").strip()
    cred_path = PROJECT_DIR / ".tmp" / "leftover_d_test_account.json"

    if email and password:
        return email, password

    if cred_path.exists():
        data = json.loads(cred_path.read_text(encoding="utf-8"))
        return data["email"], data["password"]

    email = INTEGRATION_TEST_EMAIL_DEFAULT
    password = f"Nl-D-Test-{uuid.uuid4().hex[:16]}!"
    print(f"  Creating dedicated integration test user: {email}")
    uid = admin_create_user(service_key, email, password)
    ensure_user_consent(uid, service_key)
    cred_path.parent.mkdir(parents=True, exist_ok=True)
    cred_path.write_text(
        json.dumps({"email": email, "password": password, "user_id": uid}, indent=2),
        encoding="utf-8",
    )
    print(f"  Credentials saved to {cred_path} (gitignored .tmp)")
    return email, password


def sample_meal_payload() -> dict[str, Any]:
    foods = [
        {
            "food_item_id": "food_01",
            "name_ko": "제육볶음",
            "calories_kcal": 480,
            "protein_g": 28,
            "carbs_g": 22,
            "fat_g": 30,
            "sodium_mg": 1200,
        }
    ]
    summary = {
        "total_calories_kcal": 480,
        "total_protein_g": 28,
        "total_carbs_g": 22,
        "total_fat_g": 30,
        "total_sodium_mg": 1200,
    }
    return {"foods": foods, "summary": summary}


def ensure_test_meal_log(user_id: str, service_key: str, *, fresh: bool = True) -> str:
    """테스트용 meal_log 1건 확보. fresh=True면 기존 leftover 테스트 데이터 정리 후 신규."""
    client_tag = "leftover-d-integration-test"
    if fresh:
        existing = rest_select(
            service_key,
            "meal_log",
            f"user_id=eq.{user_id}&client_meal_id=eq.{client_tag}&select=id&limit=5",
        )
        for row in existing:
            rest_delete(service_key, "meal_log_adjustment", f"meal_log_id=eq.{row['id']}")
            rest_delete(service_key, "meal_log", f"id=eq.{row['id']}")

    payload = sample_meal_payload()
    from datetime import datetime, timezone

    row = rest_insert(
        service_key,
        "meal_log",
        {
            "user_id": user_id,
            "client_meal_id": client_tag,
            "source": "manual",
            "foods": payload["foods"],
            "summary": payload["summary"],
            "eaten_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return row["id"]


def leftover_post(
    jwt: str,
    anon_key: str,
    idem_key: str,
    body: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    return http_json(
        "POST",
        LEFTOVER_URL,
        headers={
            "Authorization": f"Bearer {jwt}",
            "apikey": anon_key,
            "Content-Type": "application/json",
            "X-Idempotency-Key": idem_key,
        },
        body=body,
    )


def approx(a: float, b: float) -> bool:
    return abs(float(a) - float(b)) <= TOL


def get_meal_log_state(service_key: str, log_id: str) -> dict:
    rows = rest_select(
        service_key,
        "meal_log",
        f"id=eq.{log_id}&select=id,source,leftover_method,eaten_ratio,original_summary,adjusted_summary,summary",
    )
    if not rows:
        raise RuntimeError(f"meal_log {log_id} not found")
    return rows[0]


def count_adjustments(service_key: str, log_id: str) -> int:
    rows = rest_select(
        service_key,
        "meal_log_adjustment",
        f"meal_log_id=eq.{log_id}&select=id",
    )
    return len(rows)


def count_adjustments_by_keys(service_key: str, log_id: str, keys: list[str]) -> int:
    keys_filter = ",".join(keys)
    rows = rest_select(
        service_key,
        "meal_log_adjustment",
        f"meal_log_id=eq.{log_id}&idempotency_key=in.({keys_filter})&select=id",
    )
    return len(rows)


def find_other_user_meal_log(service_key: str, exclude_user: str) -> str | None:
    rows = rest_select(
        service_key,
        "meal_log",
        f"user_id=neq.{exclude_user}&select=id,user_id&limit=1",
    )
    return rows[0]["id"] if rows else None


@dataclass
class TestResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class RunContext:
    jwt: str
    uid: str
    anon_key: str
    service_key: str
    log_id: str
    original_source: str
    original_summary: dict
    results: list[TestResult] = field(default_factory=list)

    def record(self, name: str, passed: bool, detail: str = "") -> None:
        self.results.append(TestResult(name, passed, detail))
        mark = "PASS" if passed else "FAIL"
        print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))


def run_d1(ctx: RunContext) -> None:
    status, body = leftover_post(
        ctx.jwt,
        ctx.anon_key,
        "t1",
        {
            "pre_meal_log_id": ctx.log_id,
            "leftover_method": "slider",
            "eaten_ratio": 0.5,
        },
    )
    ok = status == 200 and body.get("ok") is True
    adj = (body.get("data") or {}).get("adjusted_summary") or {}
    cal = adj.get("total_calories_kcal")
    ok = ok and approx(cal, 240)
    ctx.record("D1 normal slider 200 adjusted=original×0.5", ok, f"status={status} cal={cal}")

    ml = get_meal_log_state(ctx.service_key, ctx.log_id)
    src_ok = ml.get("source") == ctx.original_source
    orig = ml.get("original_summary") or ml.get("summary")
    orig_ok = json.dumps(orig, sort_keys=True) == json.dumps(ctx.original_summary, sort_keys=True)
    ctx.record("D1 DB source unchanged + original_summary materialized", src_ok and orig_ok)


def run_d2(ctx: RunContext) -> None:
    body_payload = {
        "pre_meal_log_id": ctx.log_id,
        "leftover_method": "slider",
        "eaten_ratio": 0.5,
    }
    status, body = leftover_post(ctx.jwt, ctx.anon_key, "t1", body_payload)
    replay = (body.get("data") or {}).get("idempotent_replay") is True
    cnt = count_adjustments(ctx.service_key, ctx.log_id)
    ctx.record(
        "D2 idempotent replay same body",
        status == 200 and replay and cnt == 1,
        f"status={status} replay={replay} adj_rows={cnt}",
    )


def run_d3(ctx: RunContext) -> None:
    status, body = leftover_post(
        ctx.jwt,
        ctx.anon_key,
        "t1",
        {
            "pre_meal_log_id": ctx.log_id,
            "leftover_method": "slider",
            "eaten_ratio": 0.7,
        },
    )
    code = (body.get("error") or {}).get("code")
    ctx.record(
        "D3 idempotency mismatch 409",
        status == 409 and code == "IDEMPOTENCY_KEY_REUSE_MISMATCH",
        f"status={status} code={code}",
    )


def run_d4(ctx: RunContext) -> None:
    status1, body1 = leftover_post(
        ctx.jwt,
        ctx.anon_key,
        "t2",
        {"pre_meal_log_id": ctx.log_id, "leftover_method": "slider", "eaten_ratio": 0.8},
    )
    status2, body2 = leftover_post(
        ctx.jwt,
        ctx.anon_key,
        "t3",
        {"pre_meal_log_id": ctx.log_id, "leftover_method": "slider", "eaten_ratio": 0.5},
    )
    adj = (body2.get("data") or {}).get("adjusted_summary") or {}
    cal = adj.get("total_calories_kcal")
    d4_rows = count_adjustments_by_keys(ctx.service_key, ctx.log_id, ["t2", "t3"])
    ok = (
        status1 == 200
        and status2 == 200
        and approx(cal, 240)
        and d4_rows == 2
    )
    ctx.record(
        "D4 re-adjust from original (0.5 not cumulative) + 2 adjustment rows",
        ok,
        f"final_cal={cal} d4_rows={d4_rows}",
    )


def run_d5(ctx: RunContext) -> None:
    other_log = find_other_user_meal_log(ctx.service_key, ctx.uid)
    if not other_log:
        fake = str(uuid.uuid4())
        status, body = leftover_post(
            ctx.jwt,
            ctx.anon_key,
            "d5-other",
            {"pre_meal_log_id": fake, "leftover_method": "slider", "eaten_ratio": 0.5},
        )
        msg = (body.get("error") or {}).get("message", "")
        ctx.record(
            "D5 owner mismatch 403",
            status == 403 and "forbidden_owner_mismatch" in msg,
            f"status={status} (no other user log; used random uuid)",
        )
        return
    status, body = leftover_post(
        ctx.jwt,
        ctx.anon_key,
        "d5-other",
        {"pre_meal_log_id": other_log, "leftover_method": "slider", "eaten_ratio": 0.5},
    )
    msg = (body.get("error") or {}).get("message", "")
    ctx.record(
        "D5 owner mismatch 403",
        status == 403 and "forbidden_owner_mismatch" in msg,
        f"status={status} other_log={other_log[:8]}...",
    )


def run_d6(ctx: RunContext) -> None:
    status, body = leftover_post(
        ctx.jwt,
        ctx.anon_key,
        "d6-pre",
        {
            "pre_meal_log_id": ctx.log_id,
            "leftover_method": "slider",
            "eaten_ratio": 0.5,
            "pre_result": {"foods": []},
        },
    )
    msg = (body.get("error") or {}).get("message", "")
    ctx.record(
        "D6 client pre_result rejected 400",
        status == 400 and "client_pre_result_not_trusted" in msg,
        f"status={status}",
    )


def run_d7(ctx: RunContext) -> None:
    session = rest_insert(
        ctx.service_key,
        "meal_session",
        {"user_id": ctx.uid, "status": "open", "meal_slot": "lunch"},
    )
    session_id = session["id"]
    try:
        status, body = leftover_post(
            ctx.jwt,
            ctx.anon_key,
            "d7-open",
            {
                "pre_meal_session_id": session_id,
                "leftover_method": "slider",
                "session_eaten_ratio": 0.5,
            },
        )
        code = (body.get("error") or {}).get("code")
        ctx.record(
            "D7 open session 409 SESSION_STILL_OPEN",
            status == 409 and code == "SESSION_STILL_OPEN",
            f"status={status} code={code}",
        )
    finally:
        rest_delete(ctx.service_key, "meal_session", f"id=eq.{session_id}")


def run_d8(ctx: RunContext) -> None:
    """동시 2요청 — 같은 idempotency key, adjustment 1행."""
    # 별도 log로 D4 이력과 분리
    log_id = ensure_test_meal_log(ctx.uid, ctx.service_key, fresh=True)
    payload = {
        "pre_meal_log_id": log_id,
        "leftover_method": "slider",
        "eaten_ratio": 0.6,
    }
    idem = f"d8-concurrent-{uuid.uuid4().hex[:8]}"

    def one_call() -> tuple[int, dict]:
        return leftover_post(ctx.jwt, ctx.anon_key, idem, payload)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        futs = [ex.submit(one_call), ex.submit(one_call)]
        outcomes = [f.result() for f in futs]

    statuses = [o[0] for o in outcomes]
    success = sum(1 for s in statuses if s == 200)
    cnt = count_adjustments(ctx.service_key, log_id)
    ctx.record(
        "D8 concurrent same key → one success, 1 adjustment row",
        success >= 1 and cnt == 1,
        f"statuses={statuses} adj_rows={cnt}",
    )
    rest_delete(ctx.service_key, "meal_log", f"id=eq.{log_id}")


def run_d9(ctx: RunContext) -> None:
    """open 세션 2개 insert → uq_session_one_open 위반."""
    s1 = rest_insert(
        ctx.service_key,
        "meal_session",
        {"user_id": ctx.uid, "status": "open", "meal_slot": "dinner"},
    )
    blocked = False
    detail = ""
    try:
        try:
            rest_insert(
                ctx.service_key,
                "meal_session",
                {"user_id": ctx.uid, "status": "open", "meal_slot": "snack"},
            )
            detail = "second open insert unexpectedly succeeded"
        except RuntimeError as e:
            blocked = True
            detail = str(e)[:120]
    finally:
        rest_delete(ctx.service_key, "meal_session", f"id=eq.{s1['id']}")
    ctx.record("D9 duplicate open session blocked (uq_session_one_open)", blocked, detail)


def get_adjustment_row(service_key: str, log_id: str, idem_key: str) -> dict | None:
    rows = rest_select(
        service_key,
        "meal_log_adjustment",
        f"meal_log_id=eq.{log_id}&idempotency_key=eq.{idem_key}&select=id,method,user_confirmed,adjusted_summary",
    )
    return rows[0] if rows else None


# 1x1 PNG (fixture 미지정 시 placeholder). 실 식후사진은 PHOTO_AI_TEST_IMAGE로 지정 권장.
TINY_PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="


def run_pb2(ctx: RunContext) -> None:
    """Path B confirm — 결정론(원본 x confirmed_ratio) + meal_log 갱신. OpenAI 미호출."""
    log_id = ensure_test_meal_log(ctx.uid, ctx.service_key, fresh=True)
    idem = f"pb2-confirm-{uuid.uuid4().hex[:8]}"
    status, body = leftover_post(
        ctx.jwt, ctx.anon_key, idem,
        {"pre_meal_log_id": log_id, "leftover_method": "photo_ai", "confirmed_eaten_ratio": 0.5},
    )
    data = body.get("data") or {}
    cal = (data.get("adjusted_summary") or {}).get("total_calories_kcal")
    state_ok = data.get("state") == "user_confirmed" and data.get("meal_log_updated") is True
    row = get_adjustment_row(ctx.service_key, log_id, idem)
    ml = get_meal_log_state(ctx.service_key, log_id)
    db_ok = (
        row is not None
        and row.get("method") == "photo_ai"
        and row.get("user_confirmed") is True
        and approx((ml.get("adjusted_summary") or {}).get("total_calories_kcal", 0), 240)
    )
    ok = status == 200 and approx(cal, 240) and state_ok and db_ok
    ctx.record(
        "PB2 photo_ai confirm updates meal_log (original x0.5=240, user_confirmed)",
        ok,
        f"status={status} cal={cal} state={data.get('state')} updated={data.get('meal_log_updated')} err={(body.get('error') or {}).get('message')}",
    )
    rest_delete(ctx.service_key, "meal_log_adjustment", f"meal_log_id=eq.{log_id}")
    rest_delete(ctx.service_key, "meal_log", f"id=eq.{log_id}")


def run_pb3(ctx: RunContext) -> None:
    """Path B confirm 멱등 — 동일 key+동일 body 재호출 이력1, 동일 key+다른 ratio 409."""
    log_id = ensure_test_meal_log(ctx.uid, ctx.service_key, fresh=True)
    idem = f"pb3-confirm-{uuid.uuid4().hex[:8]}"
    payload = {"pre_meal_log_id": log_id, "leftover_method": "photo_ai", "confirmed_eaten_ratio": 0.5}
    s1, _ = leftover_post(ctx.jwt, ctx.anon_key, idem, payload)
    s2, b2 = leftover_post(ctx.jwt, ctx.anon_key, idem, payload)
    replay = (b2.get("data") or {}).get("idempotent_replay") is True
    cnt = count_adjustments(ctx.service_key, log_id)
    s3, b3 = leftover_post(
        ctx.jwt, ctx.anon_key, idem,
        {"pre_meal_log_id": log_id, "leftover_method": "photo_ai", "confirmed_eaten_ratio": 0.7},
    )
    code = (b3.get("error") or {}).get("code")
    ok = s1 == 200 and s2 == 200 and replay and cnt == 1 and s3 == 409 and code == "IDEMPOTENCY_KEY_REUSE_MISMATCH"
    ctx.record(
        "PB3 photo_ai confirm idempotent (replay 1 row) + mismatch 409",
        ok,
        f"s1={s1} s2={s2} replay={replay} rows={cnt} s3={s3} code={code} err1={(b2.get('error') or {}).get('message')}",
    )
    rest_delete(ctx.service_key, "meal_log_adjustment", f"meal_log_id=eq.{log_id}")
    rest_delete(ctx.service_key, "meal_log", f"id=eq.{log_id}")


def run_pb1_live(ctx: RunContext) -> None:
    """Path B suggest — 식후사진 AI 추정. photo_ai_suggested + meal_log 미갱신(P1-2).
    OpenAI 호출(크레딧 소모) — RUN_PHOTO_AI_LIVE=1일 때만 호출됨."""
    import base64 as _b64
    log_id = ensure_test_meal_log(ctx.uid, ctx.service_key, fresh=True)
    before = get_meal_log_state(ctx.service_key, log_id)
    img_path = os.environ.get("PHOTO_AI_TEST_IMAGE", "").strip()
    if img_path and Path(img_path).exists():
        after_b64 = _b64.b64encode(Path(img_path).read_bytes()).decode()
        mime = "image/jpeg"
    else:
        after_b64, mime = TINY_PNG_B64, "image/png"
    idem = f"pb1-suggest-{uuid.uuid4().hex[:8]}"
    status, body = leftover_post(
        ctx.jwt, ctx.anon_key, idem,
        {"pre_meal_log_id": log_id, "leftover_method": "photo_ai",
         "after_image": after_b64, "after_image_mime": mime},
    )
    data = body.get("data") or {}
    after = get_meal_log_state(ctx.service_key, log_id)
    ml_unchanged = (
        json.dumps(after.get("adjusted_summary"), sort_keys=True)
        == json.dumps(before.get("adjusted_summary"), sort_keys=True)
    )
    if status == 200:
        ok = (
            data.get("state") == "photo_ai_suggested"
            and data.get("meal_log_updated") is False
            and ml_unchanged
        )
        ctx.record(
            "PB1 photo_ai suggest -> suggested + meal_log 미갱신 (P1-2)",
            ok,
            f"state={data.get('state')} updated={data.get('meal_log_updated')} "
            f"ml_unchanged={ml_unchanged} confirm={data.get('requires_user_confirmation')}",
        )
    else:
        code = (body.get("error") or {}).get("code")
        ctx.record(
            "PB1 photo_ai suggest (live AI)",
            ml_unchanged,
            f"status={status} code={code} (AI 실패해도 meal_log 미변경={ml_unchanged}; 실사진 fixture 권장)",
        )
    rest_delete(ctx.service_key, "meal_log_adjustment", f"meal_log_id=eq.{log_id}")
    rest_delete(ctx.service_key, "meal_log", f"id=eq.{log_id}")


def main() -> int:
    load_dotenv()

    print("=== Doc 37 D integration tests (meal-leftover) ===")
    print(f"Target: {LEFTOVER_URL}")

    print("\n[1/4] ENGINE_API_KEY secrets sync...")
    ensure_engine_api_key_sync()

    anon, service = fetch_api_keys()
    os.environ.setdefault("SUPABASE_URL", DEFAULT_SUPABASE_URL)
    os.environ.setdefault("SUPABASE_ANON_KEY", anon)
    os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", service)

    email, password = resolve_test_credentials(service)
    print(f"Test account: {email}")

    print("\n[2/4] Auth login...")
    jwt = auth_login(email, password, anon)
    uid = decode_jwt_sub(jwt)
    print(f"  user_id={uid}")

    print("\n[3/4] Setup test meal_log + consent...")
    ensure_user_consent(uid, service)
    log_id = ensure_test_meal_log(uid, service, fresh=True)
    ml = get_meal_log_state(service, log_id)
    original_source = ml["source"]
    original_summary = ml.get("summary") or sample_meal_payload()["summary"]
    print(f"  meal_log id={log_id} source={original_source}")

    print("\n[4/4] Running D1~D9 + Path B (PB2/PB3 결정론, PB1 gated)...")
    ctx = RunContext(
        jwt=jwt,
        uid=uid,
        anon_key=anon,
        service_key=service,
        log_id=log_id,
        original_source=original_source,
        original_summary=original_summary,
    )

    run_d1(ctx)
    run_d2(ctx)
    run_d3(ctx)
    run_d4(ctx)
    run_d5(ctx)
    run_d6(ctx)
    run_d7(ctx)
    run_d8(ctx)
    run_d9(ctx)

    run_pb2(ctx)
    run_pb3(ctx)
    if os.environ.get("RUN_PHOTO_AI_LIVE", "").strip().lower() in ("1", "true", "yes"):
        print("  [PB1] RUN_PHOTO_AI_LIVE set -> OpenAI 호출(크레딧 소모)...")
        run_pb1_live(ctx)
    else:
        print("  [PB1] SKIP (live AI). 실행하려면 RUN_PHOTO_AI_LIVE=1 (OpenAI 크레딧 소모, 실사진은 PHOTO_AI_TEST_IMAGE).")

    passed = sum(1 for r in ctx.results if r.passed)
    total = len(ctx.results)
    print(f"\n=== Result: {passed}/{total} checks passed ===")
    if passed < total:
        for r in ctx.results:
            if not r.passed:
                print(f"  FAILED: {r.name} — {r.detail}")
        return 1
    print("All D integration checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
