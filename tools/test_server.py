#!/usr/bin/env python3
"""
NutriLens 테스트 서버
────────────────────
브라우저에서 음식 사진을 업로드하면 AI가 분석 결과를 보여줍니다.

실행법:
  python tools/test_server.py

그러면 브라우저에서 http://localhost:8080 을 열면 됩니다.
Ctrl+C 로 종료.
"""

import os
import sys
import json
import base64
import tempfile
import urllib.request
import urllib.error
import uuid
import time
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs
import io
import re

# ── 프로젝트 경로 설정 ──
PROJECT_DIR = Path(__file__).parent.parent
TOOLS_DIR = Path(__file__).parent
sys.path.insert(0, str(TOOLS_DIR))

# food_analyzer 모듈 임포트
from food_analyzer import load_food_db, match_with_db, SYSTEM_PROMPT

# ── 식사 세션 관리 (메모리 저장, 1시간 TTL) ──
MEAL_SESSIONS = {}   # { session_id: { foods:[], created: timestamp } }
SESSION_TTL = 3600   # 1시간

def _cleanup_sessions():
    """만료된 세션 정리"""
    now = time.time()
    expired = [sid for sid, s in MEAL_SESSIONS.items() if now - s['created'] > SESSION_TTL]
    for sid in expired:
        del MEAL_SESSIONS[sid]

# ── 피드백 학습 시스템 ──
FEEDBACK_LOG_PATH = None  # 서버 시작 시 설정

def _load_feedback_log():
    """피드백 로그 로드"""
    if FEEDBACK_LOG_PATH and FEEDBACK_LOG_PATH.exists():
        try:
            with open(FEEDBACK_LOG_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return {"corrections": [], "patterns": {}}

def _save_feedback_log(log):
    """피드백 로그 저장"""
    if FEEDBACK_LOG_PATH:
        with open(FEEDBACK_LOG_PATH, 'w', encoding='utf-8') as f:
            json.dump(log, f, ensure_ascii=False, indent=1)

def _record_correction(original_name, corrected_name, corrected_serving=None):
    """음식 수정 기록 — 패턴 학습용"""
    log = _load_feedback_log()
    # 수정 기록 추가
    log["corrections"].append({
        "original": original_name,
        "corrected": corrected_name,
        "serving": corrected_serving,
        "timestamp": time.time()
    })
    # 패턴 집계 (AI가 X라고 했을 때 실제 Y인 횟수)
    key = f"{original_name}→{corrected_name}"
    if original_name != corrected_name:
        log["patterns"][key] = log["patterns"].get(key, 0) + 1
    _save_feedback_log(log)
    return log["patterns"].get(key, 1)

def _get_correction_hints():
    """자주 틀리는 패턴을 AI 프롬프트 힌트로 변환"""
    log = _load_feedback_log()
    hints = []
    for pattern, count in sorted(log.get("patterns", {}).items(), key=lambda x: -x[1]):
        if count >= 3:  # 3회 이상 같은 수정이면 힌트로 추가
            original, corrected = pattern.split("→", 1)
            hints.append(f"- '{original}'으로 보이면 '{corrected}'일 가능성이 높음 (사용자 피드백 {count}회)")
    return hints[:10]  # 최대 10개

# ── 신고 → 구글시트 웹훅 ──
def _send_report_to_sheets(nickname, food_name, food_data, report_text):
    """Apps Script 웹훅으로 신고 데이터 전송 (구글시트 기록 + 자동 알림)"""
    webhook_url = os.environ.get('REPORT_WEBHOOK_URL', '')
    webhook_secret = os.environ.get('REPORT_WEBHOOK_SECRET', '')
    if not webhook_url:
        print("[신고-시트] REPORT_WEBHOOK_URL 미설정 — 로컬 저장만 완료")
        return

    payload = {
        "secret": webhook_secret,
        "data": {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "nickname": nickname,
            "food_name": food_name,
            "calories": food_data.get("calories", ""),
            "protein": food_data.get("protein", ""),
            "carbs": food_data.get("carbs", ""),
            "fat": food_data.get("fat", ""),
            "serving_g": food_data.get("serving_g", ""),
            "source": food_data.get("source", ""),
            "report_text": report_text
        }
    }

    try:
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(webhook_url, data=data, method='POST')
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = resp.read().decode('utf-8')
            print(f"[신고-시트] 전송 성공: {result[:100]}")
    except Exception as e:
        print(f"[신고-시트] 전송 실패 (로컬 저장은 완료): {e}")

# ── 식사 기록 저장 시스템 ──
MEAL_LOG_PATH = None  # 서버 시작 시 설정
REPORT_LOG_PATH = None  # 서버 시작 시 설정

def _load_meal_log():
    """식사 기록 로드"""
    if MEAL_LOG_PATH and MEAL_LOG_PATH.exists():
        try:
            with open(MEAL_LOG_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return {"users": {}}

def _save_meal_log(log):
    """식사 기록 저장"""
    if MEAL_LOG_PATH:
        with open(MEAL_LOG_PATH, 'w', encoding='utf-8') as f:
            json.dump(log, f, ensure_ascii=False, indent=1)

def _save_meal_record(nickname, meal_data):
    """한 끼 식사 기록 저장"""
    log = _load_meal_log()
    if nickname not in log["users"]:
        log["users"][nickname] = {"created": time.strftime("%Y-%m-%d"), "meals": []}

    foods_list = []
    for food in meal_data.get("foods", []):
        foods_list.append({
            "name": food.get("name_ko", "?"),
            "calories": food.get("calories_kcal", 0) or 0,
            "protein": food.get("protein_g", 0) or 0,
            "carbs": food.get("carbs_g", 0) or 0,
            "fat": food.get("fat_g", 0) or 0,
            "sodium": food.get("sodium_mg", 0) or 0,
            "sugar": food.get("sugar_g", 0) or 0,
            "fiber": food.get("fiber_g", 0) or 0,
            "serving_g": food.get("estimated_serving_g", 0) or 0
        })

    summary = meal_data.get("meal_summary", {})
    record = {
        "id": str(uuid.uuid4())[:8],
        "date": time.strftime("%Y-%m-%d"),
        "time": time.strftime("%H:%M"),
        "meal_type": summary.get("meal_type", "식사"),
        "foods": foods_list,
        "summary": {
            "total_calories": round(summary.get("total_calories", 0) or 0),
            "total_protein": round(summary.get("total_protein", 0) or 0, 1),
            "total_carbs": round(summary.get("total_carbs", 0) or 0, 1),
            "total_fat": round(summary.get("total_fat", 0) or 0, 1),
            "total_sodium": round(sum(f["sodium"] for f in foods_list)),
            "total_sugar": round(sum(f["sugar"] for f in foods_list), 1),
            "total_fiber": round(sum(f["fiber"] for f in foods_list), 1)
        }
    }

    log["users"][nickname]["meals"].append(record)
    _save_meal_log(log)
    print(f"[기록] {nickname}: {record['date']} {record['time']} - {len(foods_list)}개 음식, {record['summary']['total_calories']}kcal 저장")
    return record["id"]

# ── 목표 설정 및 조회 ──
def _get_user_goals(nickname):
    """사용자의 일일 목표 조회"""
    log = _load_meal_log()
    user = log.get("users", {}).get(nickname, {})
    goals = user.get("goals", {})
    # 기본값: 1800kcal, 120g 단백질
    return {
        "calories": goals.get("calories", 1800),
        "protein": goals.get("protein", 120)
    }

def _save_user_goals(nickname, calories, protein):
    """사용자의 일일 목표 저장"""
    log = _load_meal_log()
    if nickname not in log["users"]:
        log["users"][nickname] = {"created": time.strftime("%Y-%m-%d"), "meals": []}
    log["users"][nickname]["goals"] = {"calories": calories, "protein": protein}
    _save_meal_log(log)
    print(f"[목표] {nickname}: {calories}kcal, {protein}g 단백질")

def _get_today_progress(nickname):
    """오늘의 진행 상황 조회"""
    from datetime import datetime
    log = _load_meal_log()
    user = log.get("users", {}).get(nickname)
    today_str = datetime.now().strftime("%Y-%m-%d")

    # 오늘의 식사 데이터
    today_meals = []
    if user:
        today_meals = [m for m in user.get("meals", []) if m.get("date") == today_str]

    # 합계
    total_calories = sum(m["summary"].get("total_calories", 0) for m in today_meals)
    total_protein = sum(m["summary"].get("total_protein", 0) for m in today_meals)

    # 목표
    goals = _get_user_goals(nickname)

    return {
        "calories": total_calories,
        "protein": total_protein,
        "goal_calories": goals["calories"],
        "goal_protein": goals["protein"],
        "remaining_calories": max(0, goals["calories"] - total_calories),
        "remaining_protein": max(0, goals["protein"] - total_protein),
        "calorie_pct": round(total_calories / goals["calories"] * 100, 1) if goals["calories"] > 0 else 0,
        "protein_pct": round(total_protein / goals["protein"] * 100, 1) if goals["protein"] > 0 else 0
    }

def _get_weekly_macro_analysis(nickname):
    """주간 매크로 분석 (일별 탄단지 비율)"""
    from datetime import datetime, timedelta
    log = _load_meal_log()
    user = log.get("users", {}).get(nickname)

    if not user:
        return {"days": [], "avg_ratio": {}, "feedback": ""}

    # 지난 7일 데이터
    meals = user.get("meals", [])
    cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    recent = [m for m in meals if m.get("date", "") >= cutoff]

    # 일별 집계
    daily_data = {}
    for m in recent:
        d = m["date"]
        if d not in daily_data:
            daily_data[d] = {"carbs": 0, "protein": 0, "fat": 0}
        s = m.get("summary", {})
        daily_data[d]["carbs"] += s.get("total_carbs", 0)
        daily_data[d]["protein"] += s.get("total_protein", 0)
        daily_data[d]["fat"] += s.get("total_fat", 0)

    # 일별 비율 계산
    days_data = []
    for date in sorted(daily_data.keys()):
        d = daily_data[date]
        total_cal = d["carbs"] * 4 + d["protein"] * 4 + d["fat"] * 9
        if total_cal > 0:
            carb_pct = round(d["carbs"] * 4 / total_cal * 100, 1)
            prot_pct = round(d["protein"] * 4 / total_cal * 100, 1)
            fat_pct = round(d["fat"] * 9 / total_cal * 100, 1)
        else:
            carb_pct = prot_pct = fat_pct = 0

        days_data.append({
            "date": date,
            "carb_pct": carb_pct,
            "protein_pct": prot_pct,
            "fat_pct": fat_pct
        })

    # 주간 평균
    if days_data:
        avg_carb = sum(d["carb_pct"] for d in days_data) / len(days_data)
        avg_protein = sum(d["protein_pct"] for d in days_data) / len(days_data)
        avg_fat = sum(d["fat_pct"] for d in days_data) / len(days_data)
    else:
        avg_carb = avg_protein = avg_fat = 0

    # 목표 판단 및 피드백
    goals = _get_user_goals(nickname)
    feedback = ""
    if goals["protein"] > 100:
        # 고단백 목표: 탄40/단30/지30
        target_carb, target_protein, target_fat = 40, 30, 30
        feedback = f"고단백 목표 기준 "
    else:
        # 표준 식단: 탄50/단25/지25
        target_carb, target_protein, target_fat = 50, 25, 25
        feedback = f"표준 식단 기준 "

    prot_diff = avg_protein - target_protein
    if prot_diff < -5:
        feedback += f"단백질 비율이 목표보다 {abs(prot_diff):.0f}%p 부족합니다."
    elif prot_diff > 5:
        feedback += f"단백질 비율이 목표보다 {prot_diff:.0f}%p 높습니다."
    else:
        feedback += f"단백질 비율이 목표에 가깝습니다."

    return {
        "days": days_data,
        "avg_ratio": {
            "carb_pct": round(avg_carb, 1),
            "protein_pct": round(avg_protein, 1),
            "fat_pct": round(avg_fat, 1)
        },
        "feedback": feedback
    }

def _get_protein_timing_analysis(nickname):
    """단백질 섭취 타이밍 분석"""
    from datetime import datetime, timedelta
    log = _load_meal_log()
    user = log.get("users", {}).get(nickname)

    if not user:
        return {"timing_slots": {}, "avg_per_slot": {}, "feedback": []}

    # 지난 7일 데이터
    meals = user.get("meals", [])
    cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    recent = [m for m in meals if m.get("date", "") >= cutoff]

    # 시간대별 분류
    timing_data = {
        "아침": {"count": 0, "total_protein": 0},
        "점심": {"count": 0, "total_protein": 0},
        "저녁": {"count": 0, "total_protein": 0},
        "간식": {"count": 0, "total_protein": 0}
    }

    for m in recent:
        t = m.get("time", "12:00")
        hour = int(t.split(":")[0]) if ":" in t else 12
        protein = m["summary"].get("total_protein", 0)

        if 6 <= hour < 11:
            slot = "아침"
        elif 11 <= hour < 15:
            slot = "점심"
        elif 17 <= hour < 21:
            slot = "저녁"
        else:
            slot = "간식"

        timing_data[slot]["count"] += 1
        timing_data[slot]["total_protein"] += protein

    # 평균 계산
    avg_per_slot = {}
    for slot, data in timing_data.items():
        if data["count"] > 0:
            avg_per_slot[slot] = round(data["total_protein"] / data["count"], 1)
        else:
            avg_per_slot[slot] = 0

    # 피드백 생성
    feedbacks = []
    protein_threshold = 30  # 30g 권장
    recommendations = {
        "닭가슴살": "lean_protein",
        "두부": "plant_protein",
        "계란": "egg",
        "생선": "fish"
    }

    for slot in ["아침", "점심", "저녁"]:
        if avg_per_slot[slot] < protein_threshold:
            shortage = protein_threshold - avg_per_slot[slot]
            feedbacks.append(f"{slot}에 단백질이 {avg_per_slot[slot]}g으로 부족합니다. 닭가슴살, 두부, 계란 등으로 보충해보세요.")

    return {
        "timing_slots": timing_data,
        "avg_per_slot": avg_per_slot,
        "threshold": protein_threshold,
        "feedback": feedbacks
    }

def _get_dashboard_data(nickname, days=30):
    """대시보드용 데이터 집계"""
    from datetime import datetime, timedelta
    log = _load_meal_log()
    user = log.get("users", {}).get(nickname)
    empty = {"daily": [], "meal_pattern": {}, "balance": {}, "top_foods": [], "warnings": [], "weekly_report": "기록이 없습니다.", "total_meals": 0, "total_days": 0}
    if not user:
        return empty

    meals = user.get("meals", [])
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    recent = [m for m in meals if m.get("date", "") >= cutoff]
    if not recent:
        return empty

    # 1. 일별 집계
    daily = {}
    for m in recent:
        d = m["date"]
        if d not in daily:
            daily[d] = {"calories": 0, "protein": 0, "carbs": 0, "fat": 0, "sodium": 0, "sugar": 0, "fiber": 0, "meal_count": 0}
        s = m.get("summary", {})
        for k in ["calories", "protein", "carbs", "fat", "sodium", "sugar", "fiber"]:
            daily[d][k] += s.get(f"total_{k}", 0)
        daily[d]["meal_count"] += 1
    daily_sorted = [{"date": k, **v} for k, v in sorted(daily.items())]

    # 2. 식사 패턴 (시간대별)
    meal_times = {"아침(6-10시)": 0, "점심(11-14시)": 0, "저녁(17-21시)": 0, "야식(21-6시)": 0, "간식(기타)": 0}
    for m in recent:
        t = m.get("time", "12:00")
        hour = int(t.split(":")[0]) if ":" in t else 12
        if 6 <= hour < 10: meal_times["아침(6-10시)"] += 1
        elif 11 <= hour < 14: meal_times["점심(11-14시)"] += 1
        elif 17 <= hour < 21: meal_times["저녁(17-21시)"] += 1
        elif hour >= 21 or hour < 6: meal_times["야식(21-6시)"] += 1
        else: meal_times["간식(기타)"] += 1

    # 3. 영양 균형 (일 평균)
    n = len(daily_sorted)
    avg = {k: round(sum(d[k] for d in daily_sorted) / n, 1) for k in ["calories", "protein", "carbs", "fat", "sodium", "sugar", "fiber"]}
    total_macro_cal = avg["carbs"] * 4 + avg["protein"] * 4 + avg["fat"] * 9
    if total_macro_cal > 0:
        carb_pct = round(avg["carbs"] * 4 / total_macro_cal * 100, 1)
        prot_pct = round(avg["protein"] * 4 / total_macro_cal * 100, 1)
        fat_pct = round(avg["fat"] * 9 / total_macro_cal * 100, 1)
    else:
        carb_pct = prot_pct = fat_pct = 0
    balance = {
        "avg_calories": round(avg["calories"]), "avg_protein": avg["protein"],
        "avg_carbs": avg["carbs"], "avg_fat": avg["fat"],
        "avg_sodium": round(avg["sodium"]), "avg_sugar": avg["sugar"], "avg_fiber": avg["fiber"],
        "carb_pct": carb_pct, "prot_pct": prot_pct, "fat_pct": fat_pct
    }

    # 4. 자주 먹는 음식 TOP 10
    food_counts = {}
    for m in recent:
        for f in m.get("foods", []):
            name = f.get("name", "?")
            if name not in food_counts:
                food_counts[name] = {"count": 0, "total_cal": 0}
            food_counts[name]["count"] += 1
            food_counts[name]["total_cal"] += f.get("calories", 0)
    top_foods = sorted(food_counts.items(), key=lambda x: -x[1]["count"])[:10]
    top_foods = [{"name": k, "count": v["count"], "avg_cal": round(v["total_cal"] / v["count"]) if v["count"] > 0 else 0} for k, v in top_foods]

    # 5. 경고 (일일 권장량 대비)
    warnings = []
    if balance["avg_sodium"] > 2000:
        warnings.append({"type": "danger", "icon": "🧂", "msg": f"나트륨 일평균 {balance['avg_sodium']}mg", "detail": f"권장 2,000mg 대비 +{balance['avg_sodium'] - 2000}mg"})
    if balance["avg_sugar"] > 50:
        warnings.append({"type": "danger", "icon": "🍬", "msg": f"당류 일평균 {balance['avg_sugar']}g", "detail": f"권장 50g 대비 +{round(balance['avg_sugar'] - 50)}g"})
    if balance["avg_fiber"] < 25:
        warnings.append({"type": "warning", "icon": "🥦", "msg": f"식이섬유 일평균 {balance['avg_fiber']}g", "detail": f"권장 25g 대비 -{round(25 - balance['avg_fiber'])}g"})
    if balance["avg_protein"] < 50:
        warnings.append({"type": "warning", "icon": "🥩", "msg": f"단백질 일평균 {balance['avg_protein']}g", "detail": f"권장 50g 대비 -{round(50 - balance['avg_protein'])}g"})
    if carb_pct > 65:
        warnings.append({"type": "warning", "icon": "🍚", "msg": f"탄수화물 비율 {carb_pct}%", "detail": "권장 50-65% 초과"})

    # 6. 주간 리포트
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    this_week = [d for d in daily_sorted if d["date"] >= week_ago]
    if this_week:
        wn = len(this_week)
        wc = round(sum(d["calories"] for d in this_week) / wn)
        wp = round(sum(d["protein"] for d in this_week) / wn, 1)
        ws = round(sum(d["sodium"] for d in this_week) / wn)
        parts = [f"이번 주 {wn}일 기록, 일평균 {wc}kcal"]
        if wp < 50: parts.append(f"단백질 부족(-{round(50 - wp)}g)")
        elif wp >= 50: parts.append(f"단백질 양호({wp}g)")
        if ws > 2000: parts.append(f"나트륨 주의(+{ws - 2000}mg)")
        weekly_report = ", ".join(parts)
    else:
        weekly_report = "이번 주 기록이 없습니다."

    return {
        "daily": daily_sorted, "meal_pattern": meal_times, "balance": balance,
        "top_foods": top_foods, "warnings": warnings, "weekly_report": weekly_report,
        "total_meals": len(recent), "total_days": n
    }

# ── 전/후 비교 분석 프롬프트 ──
LEFTOVER_PROMPT = """당신은 NutriLens의 AI 음식 잔량 분석 전문가입니다.

## 역할
사용자가 먹기 전(첫 번째 사진)과 먹은 후(두 번째 사진)를 보내줍니다.
두 사진을 비교하여 **실제로 섭취한 양**을 계산하세요.

## 핵심 규칙
1. 첫 번째 사진(전)의 모든 음식을 식별하세요
2. 두 번째 사진(후)에서 남은 양을 추정하세요
3. eaten_pct = 실제 먹은 비율 (0~100)
   - 다 먹었으면 100
   - 절반 남겼으면 50
   - 거의 안 먹었으면 5~10
   - **전혀 안 먹었거나 두 사진이 동일/거의 동일하면 반드시 0**
4. 영양소는 반드시 원래 양 × (eaten_pct / 100) 으로 계산
   - eaten_pct가 0이면 모든 영양소도 반드시 0
   - eaten_pct가 50이면 모든 영양소는 원래의 절반
5. 두 사진이 비슷해 보여도, 그릇의 내용물 높이나 양의 차이를 세심하게 비교하세요
6. **eaten_pct와 영양소 값은 반드시 일관성이 있어야 합니다**

## 중요: 반드시 JSON만 출력하세요. 설명 텍스트 없이 순수 JSON만 응답하세요.

```json
{
  "foods": [
    {
      "name_ko": "음식명",
      "name_en": "English",
      "category": "korean",
      "original_serving_g": 300,
      "eaten_pct": 75,
      "estimated_serving_g": 225,
      "calories_kcal": 338,
      "protein_g": 11,
      "carbs_g": 49,
      "fat_g": 9,
      "fiber_g": 1.5,
      "sodium_mg": 600,
      "sugar_g": 2,
      "confidence": 0.85,
      "leftover_note": "1/4 정도 남김"
    }
  ],
  "meal_summary": {
    "total_calories": 338,
    "total_protein": 11,
    "total_carbs": 49,
    "total_fat": 9,
    "meal_type": "점심",
    "health_score": 7,
    "one_line_comment": "약 75% 섭취했습니다."
  }
}
```"""

# ── .env 로드 ──
def load_env():
    env_paths = [PROJECT_DIR / '.env', Path.cwd() / '.env']
    for p in env_paths:
        if p.exists():
            with open(p) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, val = line.split('=', 1)
                        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))
            break

load_env()

# ── AI JSON 파싱 (강화) ──
def _parse_ai_json(content):
    """AI 응답에서 JSON을 추출 — 여러 형식 대응"""
    if not content or not content.strip():
        return None

    # BOM 제거 + 양쪽 공백 제거
    content = content.strip().lstrip('\ufeff')

    # 1) 직접 파싱 시도 (response_format: json_object 사용 시 보통 여기서 성공)
    try:
        return json.loads(content)
    except (json.JSONDecodeError, ValueError):
        pass

    # 2) ```json ... ``` 블록
    if "```json" in content:
        try:
            extracted = content.split("```json")[1].split("```")[0]
            return json.loads(extracted.strip())
        except (json.JSONDecodeError, ValueError, IndexError):
            pass
    elif "```" in content:
        try:
            extracted = content.split("```")[1].split("```")[0]
            return json.loads(extracted.strip())
        except (json.JSONDecodeError, ValueError, IndexError):
            pass

    # 3) { 로 시작하는 부분 찾기 (중첩 브레이스 대응)
    start = content.find('{')
    if start >= 0:
        end = content.rfind('}')
        if end > start:
            try:
                return json.loads(content[start:end+1])
            except (json.JSONDecodeError, ValueError):
                pass

    # 4) [ 로 시작하는 배열 형태
    start = content.find('[')
    if start >= 0:
        end = content.rfind(']')
        if end > start:
            try:
                arr = json.loads(content[start:end+1])
                return {"foods": arr}
            except (json.JSONDecodeError, ValueError):
                pass

    return None


# ── 음식 DB 미리 로드 ──
print("음식 DB 로딩 중...")
FOODS_DB = load_food_db()
print(f"DB 로드 완료: {len(FOODS_DB)}종")

# 피드백 로그 경로 설정
FEEDBACK_LOG_PATH = PROJECT_DIR / 'feedback_log.json'
MEAL_LOG_PATH = PROJECT_DIR / 'meal_log.json'
REPORT_LOG_PATH = PROJECT_DIR / 'report_log.json'

# ── OpenAI API 호출 (urllib 사용, 외부 패키지 불필요) ──
def call_openai_vision(base64_image, media_type, api_key, model="gpt-4o"):
    """GPT-4o Vision API 호출"""
    url = "https://api.openai.com/v1/chat/completions"

    # 피드백 학습 힌트 반영
    hints = _get_correction_hints()
    system_content = SYSTEM_PROMPT
    if hints:
        hint_text = "\n\n## 사용자 피드백 기반 주의사항\n다음은 자주 잘못 인식되는 음식입니다:\n" + "\n".join(hints)
        system_content += hint_text

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_content},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "이 사진에 있는 음식을 분석해주세요. 모든 음식을 개별적으로 식별하고 영양 성분을 알려주세요."
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{media_type};base64,{base64_image}",
                            "detail": "high"
                        }
                    }
                ]
            }
        ],
        "max_tokens": 2000,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }

    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=data, method='POST')
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')
        return {"error": f"OpenAI API 에러 ({e.code}): {body}"}
    except Exception as e:
        return {"error": f"API 호출 실패: {str(e)}"}

    if "error" in result:
        return {"error": f"OpenAI: {result['error'].get('message', str(result['error']))}"}

    content = result["choices"][0]["message"]["content"]

    parsed = _parse_ai_json(content)
    if parsed is not None:
        return parsed
    return {"error": "AI 응답 파싱 실패", "raw": content[:300]}


# ── HTML 페이지 ──
HTML_PAGE = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NutriLens - AI 음식 영양 분석</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }

  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans KR', sans-serif;
    background: #0f0f1a;
    color: #e0e0e0;
    min-height: 100vh;
  }

  .container {
    max-width: 800px;
    margin: 0 auto;
    padding: 20px;
  }

  /* 헤더 */
  .header { text-align: center; padding: 30px 0 20px; }
  .header h1 { font-size: 2em; background: linear-gradient(135deg, #6ee7b7, #3b82f6); -webkit-background-clip: text; -webkit-text-fill-color: transparent; margin-bottom: 8px; }
  .header p { color: #888; font-size: 0.95em; }

  /* 업로드 영역 */
  .upload-area { border: 2px dashed #3a3a5a; border-radius: 16px; padding: 50px 20px; text-align: center; cursor: pointer; transition: all 0.3s; background: #1a1a2e; margin-bottom: 20px; }
  .upload-area:hover, .upload-area.dragover { border-color: #3b82f6; background: rgba(59,130,246,0.05); }
  .upload-area .icon { font-size: 3em; margin-bottom: 12px; }
  .upload-area .text { color: #888; font-size: 1em; }
  .upload-area .subtext { color: #555; font-size: 0.85em; margin-top: 6px; }

  /* 미리보기 */
  .preview-section { display: none; margin-bottom: 20px; text-align: center; }
  .preview-section img { max-width: 100%; max-height: 400px; border-radius: 12px; border: 1px solid #2a2a4a; }
  .preview-actions { margin-top: 12px; display: flex; gap: 10px; justify-content: center; }

  /* 버튼 */
  .btn { padding: 12px 28px; border: none; border-radius: 10px; font-size: 1em; cursor: pointer; font-weight: 600; transition: all 0.3s; }
  .btn-primary { background: linear-gradient(135deg, #3b82f6, #6366f1); color: white; }
  .btn-primary:hover { transform: translateY(-1px); box-shadow: 0 4px 15px rgba(59,130,246,0.3); }
  .btn-primary:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }
  .btn-secondary { background: #2a2a4a; color: #ccc; }
  .btn-secondary:hover { background: #3a3a5a; }
  .btn-green { background: linear-gradient(135deg, #059669, #10b981); color: white; }
  .btn-green:hover { transform: translateY(-1px); box-shadow: 0 4px 15px rgba(16,185,129,0.3); }
  .btn-orange { background: linear-gradient(135deg, #d97706, #f59e0b); color: white; }
  .btn-orange:hover { transform: translateY(-1px); }

  /* 로딩 */
  .loading { display: none; text-align: center; padding: 40px; }
  .spinner { width: 50px; height: 50px; border: 4px solid #2a2a4a; border-top-color: #3b82f6; border-radius: 50%; animation: spin 1s linear infinite; margin: 0 auto 16px; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .loading p { color: #888; }

  /* 에러 */
  .error-box { display: none; background: rgba(239,68,68,0.1); border: 1px solid rgba(239,68,68,0.3); border-radius: 12px; padding: 20px; color: #f87171; }

  /* 결과 */
  .result-section { display: none; }
  .result-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
  .result-header h2 { font-size: 1.3em; color: #6ee7b7; }

  /* 음식 카드 */
  .food-card { background: #1a1a2e; border: 1px solid #2a2a4a; border-radius: 12px; padding: 20px; margin-bottom: 12px; }
  .food-title { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
  .food-name { font-size: 1.15em; font-weight: 600; }
  .food-name-en { font-size: 0.85em; color: #888; margin-left: 8px; }
  .source-badge { font-size: 0.75em; padding: 3px 10px; border-radius: 20px; font-weight: 600; }
  .source-db { background: rgba(110,231,183,0.15); color: #6ee7b7; }
  .source-ai { background: rgba(251,191,36,0.15); color: #fbbf24; }
  .confidence-bar { height: 4px; background: #2a2a4a; border-radius: 2px; margin-bottom: 14px; overflow: hidden; }
  .confidence-fill { height: 100%; border-radius: 2px; transition: width 0.8s ease; }
  .nutrition-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
  .nutrition-item { text-align: center; padding: 10px 6px; background: #0f0f1a; border-radius: 8px; }
  .nutrition-value { font-size: 1.2em; font-weight: 700; color: #fff; }
  .nutrition-label { font-size: 0.75em; color: #888; margin-top: 2px; }

  /* 요약 카드 */
  .summary-card { background: linear-gradient(135deg, #1a1a2e, #1e1e3a); border: 1px solid #3b82f6; border-radius: 16px; padding: 24px; margin-top: 20px; }
  .summary-title { font-size: 1.1em; color: #60a5fa; margin-bottom: 16px; }
  .summary-stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 16px; }
  .stat-item { text-align: center; }
  .stat-value { font-size: 1.5em; font-weight: 700; color: #fff; }
  .stat-label { font-size: 0.8em; color: #888; }
  .health-score { display: flex; align-items: center; gap: 12px; padding: 14px; background: rgba(0,0,0,0.2); border-radius: 10px; margin-bottom: 12px; }
  .score-circle { width: 52px; height: 52px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 1.4em; font-weight: 800; color: #fff; flex-shrink: 0; }
  .comment { font-size: 0.95em; color: #ccc; line-height: 1.5; padding: 10px 0; }

  /* 식후 안내 배너 */
  .after-meal-banner { background: linear-gradient(135deg, #1a2e1a, #1e3a1e); border: 1px solid #059669; border-radius: 12px; padding: 20px; margin-top: 16px; text-align: center; }
  .after-meal-banner .banner-title { color: #6ee7b7; font-size: 1.05em; font-weight: 600; margin-bottom: 10px; }
  .after-meal-banner .banner-desc { color: #aaa; font-size: 0.85em; margin-bottom: 14px; line-height: 1.5; }
  .after-meal-actions { display: flex; gap: 10px; justify-content: center; flex-wrap: wrap; }

  /* 식후 업로드 */
  .after-upload-section { display: none; text-align: center; margin-top: 16px; }
  .after-upload-box { border: 2px dashed #059669; border-radius: 12px; padding: 30px; cursor: pointer; background: #1a2e1a; margin-bottom: 12px; }
  .after-upload-box:hover { border-color: #10b981; background: rgba(16,185,129,0.05); }

  /* 수동 입력 */
  .manual-section { display: none; margin-top: 16px; }
  .manual-food-item { background: #1a1a2e; border: 1px solid #2a2a4a; border-radius: 10px; padding: 14px; margin-bottom: 10px; }
  .manual-food-name { font-weight: 600; margin-bottom: 8px; }
  .eaten-slider { width: 100%; margin: 6px 0; accent-color: #3b82f6; }
  .eaten-value { color: #60a5fa; font-weight: 600; }

  /* 나눠먹기 팝업 */
  .sharing-overlay { display:none; position:fixed; top:0; left:0; right:0; bottom:0; background:rgba(0,0,0,0.7); z-index:100; align-items:center; justify-content:center; }
  .sharing-overlay.active { display:flex; }
  .sharing-popup { background:#1a1a2e; border:1px solid #3b82f6; border-radius:16px; padding:28px 24px; max-width:340px; width:90%; text-align:center; }
  .sharing-popup h3 { color:#e0e0e0; margin-bottom:6px; font-size:1.1em; }
  .sharing-popup .sub { color:#888; font-size:0.85em; margin-bottom:18px; }
  .people-grid { display:grid; grid-template-columns:repeat(5,1fr); gap:8px; margin-bottom:18px; }
  .people-btn { padding:12px 0; border-radius:10px; border:1px solid #3a3a5a; background:#0f0f1a; color:#e0e0e0; font-size:1.05em; font-weight:600; cursor:pointer; transition:all 0.2s; }
  .people-btn:hover { border-color:#3b82f6; background:rgba(59,130,246,0.1); }
  .people-btn.selected { border-color:#3b82f6; background:#3b82f6; color:#fff; }

  /* 음식별 나눠먹기 슬라이더 */
  .food-sharing { margin-top:10px; padding-top:10px; border-top:1px solid #2a2a4a; }
  .sharing-header { display:flex; justify-content:space-between; align-items:center; margin-bottom:6px; }
  .sharing-label { font-size:0.82em; color:#888; }
  .sharing-pct { font-size:0.95em; font-weight:700; color:#60a5fa; min-width:42px; text-align:right; }
  .sharing-slider { width:100%; margin:4px 0 6px; accent-color:#3b82f6; height:6px; cursor:pointer; }
  .sharing-presets { display:flex; gap:6px; flex-wrap:wrap; }
  .preset-btn { padding:4px 10px; border-radius:14px; border:1px solid #3a3a5a; background:#0f0f1a; color:#aaa; font-size:0.75em; cursor:pointer; transition:all 0.2s; }
  .preset-btn:hover { border-color:#3b82f6; color:#60a5fa; }
  .preset-btn.active { border-color:#3b82f6; background:rgba(59,130,246,0.15); color:#60a5fa; }
  .sharing-summary { display:flex; align-items:center; gap:6px; margin-top:4px; }
  .sharing-summary-text { font-size:0.78em; color:#6ee7b7; }

  /* 식사 세션 바 */
  .session-bar { display:none; background:linear-gradient(135deg,#1a2e1a,#1e3a1e); border:1px solid #059669; border-radius:12px; padding:14px 20px; margin-bottom:16px; }
  .session-bar.active { display:flex; align-items:center; justify-content:space-between; }
  .session-info { display:flex; align-items:center; gap:10px; }
  .session-dot { width:10px; height:10px; border-radius:50%; background:#10b981; animation:pulse 1.5s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }
  .session-stats { font-size:0.85em; color:#6ee7b7; }
  .session-cal { font-size:1.1em; font-weight:700; color:#fff; }

  /* 음식 수정 버튼 */
  .edit-btn { background:none; border:1px solid #3a3a5a; color:#888; font-size:0.78em; padding:4px 10px; border-radius:14px; cursor:pointer; transition:all 0.2s; }
  .edit-btn:hover { border-color:#f59e0b; color:#f59e0b; }

  /* 수정 모달 */
  .edit-overlay { display:none; position:fixed; top:0; left:0; right:0; bottom:0; background:rgba(0,0,0,0.7); z-index:200; align-items:center; justify-content:center; }
  .edit-overlay.active { display:flex; }
  .edit-popup { background:#1a1a2e; border:1px solid #f59e0b; border-radius:16px; padding:24px; max-width:380px; width:90%; }
  .edit-popup h3 { color:#f59e0b; margin-bottom:4px; font-size:1.05em; }
  .edit-popup .sub { color:#888; font-size:0.82em; margin-bottom:16px; }
  .edit-field { margin-bottom:14px; }
  .edit-field label { display:block; color:#aaa; font-size:0.82em; margin-bottom:4px; }
  .edit-field input { width:100%; padding:10px 12px; background:#0f0f1a; border:1px solid #3a3a5a; border-radius:8px; color:#fff; font-size:1em; outline:none; }
  .edit-field input:focus { border-color:#f59e0b; }
  .edit-slider-row { display:flex; align-items:center; gap:10px; }
  .edit-slider-row input[type=range] { flex:1; accent-color:#f59e0b; }
  .edit-slider-val { font-size:1.1em; font-weight:700; color:#f59e0b; min-width:48px; text-align:right; }
  .edit-actions { display:flex; gap:10px; margin-top:18px; }
  .edit-actions .btn { flex:1; text-align:center; }
  .btn-warn { background:linear-gradient(135deg,#d97706,#f59e0b); color:white; }
  .btn-danger { background:rgba(239,68,68,0.15); color:#f87171; border:1px solid rgba(239,68,68,0.3); }

  /* 남은 양 바 */
  .eaten-bar { height: 6px; background: #2a2a4a; border-radius: 3px; margin: 8px 0; overflow: hidden; }
  .eaten-fill { height: 100%; border-radius: 3px; background: #6ee7b7; }

  /* 로그인 화면 */
  .login-overlay { position:fixed; top:0; left:0; right:0; bottom:0; background:#0f0f1a; z-index:500; display:flex; align-items:center; justify-content:center; }
  .login-box { text-align:center; max-width:340px; width:90%; }
  .login-box h1 { font-size:2.2em; background:linear-gradient(135deg,#6ee7b7,#3b82f6); -webkit-background-clip:text; -webkit-text-fill-color:transparent; margin-bottom:8px; }
  .login-box .sub { color:#888; margin-bottom:28px; font-size:0.95em; }
  .login-box input { width:100%; padding:14px 16px; background:#1a1a2e; border:1px solid #3a3a5a; border-radius:12px; color:#fff; font-size:1.05em; text-align:center; outline:none; margin-bottom:14px; }
  .login-box input:focus { border-color:#3b82f6; }
  .login-box .login-btn { width:100%; padding:14px; background:linear-gradient(135deg,#3b82f6,#6366f1); color:#fff; border:none; border-radius:12px; font-size:1.05em; font-weight:600; cursor:pointer; }
  .login-box .login-btn:hover { transform:translateY(-1px); }

  /* 탭 네비게이션 */
  .tab-nav { display:flex; gap:4px; background:#1a1a2e; border-radius:12px; padding:4px; margin-bottom:20px; }
  .tab-btn { flex:1; padding:10px; border:none; border-radius:10px; background:transparent; color:#888; font-size:0.92em; font-weight:600; cursor:pointer; transition:all 0.2s; }
  .tab-btn.active { background:linear-gradient(135deg,#3b82f6,#6366f1); color:#fff; }
  .tab-btn:hover:not(.active) { color:#ccc; }

  /* 대시보드 */
  .dash-header { display:flex; justify-content:space-between; align-items:center; margin-bottom:16px; }
  .dash-header h2 { font-size:1.3em; color:#6ee7b7; }
  .period-btns { display:flex; gap:6px; }
  .period-btn { padding:6px 14px; border-radius:16px; border:1px solid #3a3a5a; background:transparent; color:#888; font-size:0.8em; cursor:pointer; }
  .period-btn.active { border-color:#3b82f6; color:#3b82f6; background:rgba(59,130,246,0.1); }
  .dash-empty { text-align:center; padding:60px 20px; color:#666; }
  .dash-empty .icon { font-size:3em; margin-bottom:12px; }

  /* 주간 리포트 카드 */
  .weekly-card { background:linear-gradient(135deg,#1a1a2e,#1e1e3a); border:1px solid #3b82f6; border-radius:14px; padding:18px 20px; margin-bottom:16px; }
  .weekly-card .title { color:#60a5fa; font-size:0.85em; font-weight:600; margin-bottom:6px; }
  .weekly-card .report { color:#e0e0e0; font-size:0.95em; line-height:1.5; }
  .weekly-card .meta { color:#666; font-size:0.78em; margin-top:8px; }

  /* 경고 카드 */
  .warning-list { display:flex; flex-direction:column; gap:8px; margin-bottom:16px; }
  .warning-item { display:flex; align-items:center; gap:12px; padding:12px 16px; border-radius:10px; }
  .warning-item.danger { background:rgba(239,68,68,0.08); border:1px solid rgba(239,68,68,0.2); }
  .warning-item.warning { background:rgba(251,191,36,0.08); border:1px solid rgba(251,191,36,0.2); }
  .warning-icon { font-size:1.4em; }
  .warning-text .wmsg { font-size:0.88em; color:#e0e0e0; font-weight:600; }
  .warning-text .wdetail { font-size:0.78em; color:#888; }

  /* 차트 카드 */
  .chart-card { background:#1a1a2e; border:1px solid #2a2a4a; border-radius:14px; padding:18px; margin-bottom:16px; }
  .chart-card .chart-title { font-size:0.95em; color:#e0e0e0; font-weight:600; margin-bottom:14px; }
  .chart-card canvas { max-height:260px; }

  /* 일평균 요약 */
  .avg-grid { display:grid; grid-template-columns:repeat(4,1fr); gap:8px; margin-bottom:16px; }
  .avg-item { text-align:center; background:#1a1a2e; border:1px solid #2a2a4a; border-radius:10px; padding:14px 6px; }
  .avg-val { font-size:1.3em; font-weight:700; color:#fff; }
  .avg-label { font-size:0.72em; color:#888; margin-top:2px; }

  /* TOP 음식 리스트 */
  .top-food-item { display:flex; justify-content:space-between; align-items:center; padding:10px 14px; border-bottom:1px solid #1a1a2e; }
  .top-food-item:last-child { border-bottom:none; }
  .tf-rank { width:24px; height:24px; border-radius:50%; background:#2a2a4a; color:#888; font-size:0.75em; font-weight:700; display:flex; align-items:center; justify-content:center; margin-right:10px; }
  .tf-rank.top3 { background:rgba(59,130,246,0.2); color:#60a5fa; }
  .tf-name { flex:1; font-size:0.9em; color:#e0e0e0; }
  .tf-count { font-size:0.82em; color:#888; margin-right:10px; }
  .tf-cal { font-size:0.82em; color:#6ee7b7; }

  /* 반응형 */
  @media (max-width: 600px) {
    .nutrition-grid { grid-template-columns: repeat(2, 1fr); }
    .summary-stats { grid-template-columns: repeat(2, 1fr); }
    .header h1 { font-size: 1.5em; }
    .after-meal-actions { flex-direction: column; }
    .avg-grid { grid-template-columns: repeat(2, 1fr); }
  }

  /* 수동 저장 버튼 */
  .save-meal-area { text-align:center; margin:20px 0; padding:20px; background:linear-gradient(135deg,#1a2e1a,#1e3a1e); border:1px solid #059669; border-radius:12px; }
  .save-meal-btn { background:linear-gradient(135deg,#059669,#10b981); color:#fff; border:none; padding:14px 40px; font-size:1em; font-weight:700; border-radius:30px; cursor:pointer; transition:all 0.3s; }
  .save-meal-btn:hover { transform:scale(1.05); box-shadow:0 4px 15px rgba(5,150,105,0.4); }
  .save-meal-btn:disabled { background:#2a2a4a; color:#666; cursor:default; transform:none; box-shadow:none; }
  .save-meal-btn.saved { background:#2a2a4a; }

  /* 이상해요 신고 버튼 */
  .report-btn { background:none; border:1px solid #3a3a5a; color:#888; font-size:0.78em; padding:4px 10px; border-radius:14px; cursor:pointer; transition:all 0.2s; margin-left:4px; }
  .report-btn:hover { border-color:#f87171; color:#f87171; }

  /* 신고 오버레이 */
  .report-overlay { display:none; position:fixed; top:0; left:0; right:0; bottom:0; background:rgba(0,0,0,0.7); z-index:1000; justify-content:center; align-items:center; }
  .report-overlay.active { display:flex; }
  .report-box { background:#1e1e2e; border:1px solid #3a3a5a; border-radius:16px; padding:24px; width:90%; max-width:400px; }
  .report-box h3 { margin:0 0 8px; color:#f87171; font-size:1em; }
  .report-box .report-food-name { color:#6ee7b7; font-size:0.9em; margin-bottom:12px; }
  .report-box textarea { width:100%; height:80px; background:#0d0d1a; border:1px solid #3a3a5a; border-radius:8px; color:#fff; padding:10px; font-size:0.9em; resize:none; box-sizing:border-box; }
  .report-box textarea::placeholder { color:#555; }
  .report-box .report-actions { display:flex; gap:10px; margin-top:12px; }
  .report-box .report-actions button { flex:1; padding:10px; border-radius:10px; font-size:0.9em; cursor:pointer; border:none; font-weight:600; }
  .report-submit-btn { background:#f87171; color:#fff; }
  .report-cancel-btn { background:#2a2a4a; color:#aaa; }
</style>
</head>
<body>
<!-- 로그인 화면 (깜빡임 방지: 기본 숨김 → JS에서 필요 시 표시) -->
<div class="login-overlay" id="loginOverlay" style="display:none">
  <div class="login-box">
    <h1>NutriLens</h1>
    <div class="sub">AI 음식 영양 분석기</div>
    <input type="text" id="nicknameInput" placeholder="닉네임을 입력하세요" maxlength="20" />
    <button class="login-btn" onclick="doLogin()">시작하기</button>
  </div>
</div>
<script>
// 로그인 안 된 상태면 즉시 오버레이 표시 (깜빡임 방지)
if (!localStorage.getItem('nutrilens_user')) {
  document.getElementById('loginOverlay').style.display = '';
}
</script>

<div class="container">

  <div class="header">
    <h1>NutriLens</h1>
    <p>AI 음식 영양 분석기</p>
    <div style="margin-top:10px">
      <button class="btn btn-green" id="sessionToggleBtn" onclick="toggleSession()" style="padding:8px 18px; font-size:0.85em; border-radius:20px">🍽️ 정찬 모드 시작</button>
    </div>
  </div>

  <!-- 탭 네비게이션 -->
  <div class="tab-nav">
    <button class="tab-btn active" id="tabAnalyze" onclick="switchTab('analyze')">📸 분석</button>
    <button class="tab-btn" id="tabDashboard" onclick="switchTab('dashboard')">📊 내 기록</button>
  </div>

  <!-- 탭: 분석 -->
  <div id="tab-analyze">

  <!-- 식사 세션 바 -->
  <div class="session-bar" id="sessionBar">
    <div class="session-info">
      <div class="session-dot"></div>
      <span class="session-stats">사진 <strong id="sessionPhotoCount">0</strong>장 · 음식 <strong id="sessionFoodCount">0</strong>개</span>
    </div>
    <div>
      <span class="session-cal" id="sessionTotalCal">0</span><span style="color:#888; font-size:0.8em"> kcal</span>
      <button class="btn btn-orange" onclick="endSession()" style="padding:6px 14px; font-size:0.8em; margin-left:10px; border-radius:20px">식사 끝</button>
    </div>
  </div>

  <!-- 음식 수정 모달 -->
  <div class="edit-overlay" id="editOverlay">
    <div class="edit-popup">
      <h3>음식 수정</h3>
      <div class="sub">AI가 잘못 인식했나요? 음식명과 양을 수정하세요.</div>
      <div class="edit-field">
        <label>음식명</label>
        <input type="text" id="editFoodName" placeholder="예: 김치찌개" />
      </div>
      <div class="edit-field">
        <label>먹은 양</label>
        <div class="edit-slider-row">
          <input type="range" id="editServingPct" min="10" max="200" value="100" step="10" oninput="document.getElementById('editServingVal').textContent=this.value+'%'" />
          <span class="edit-slider-val" id="editServingVal">100%</span>
        </div>
        <div style="display:flex; gap:6px; margin-top:6px">
          <button class="preset-btn" onclick="setEditPct(50)">반그릇</button>
          <button class="preset-btn" onclick="setEditPct(75)">3/4</button>
          <button class="preset-btn" onclick="setEditPct(100)">1인분</button>
          <button class="preset-btn" onclick="setEditPct(150)">1.5인분</button>
          <button class="preset-btn" onclick="setEditPct(200)">2인분</button>
        </div>
      </div>
      <input type="hidden" id="editFoodIndex" value="" />
      <input type="hidden" id="editOriginalName" value="" />
      <div class="edit-actions">
        <button class="btn btn-secondary" onclick="closeEditModal()">취소</button>
        <button class="btn btn-warn" onclick="submitCorrection()">수정 적용</button>
        <button class="btn btn-danger" onclick="deleteFood()" style="flex:0.5">삭제</button>
      </div>
    </div>
  </div>

  <!-- 1단계: 식전 사진 업로드 -->
  <div class="upload-area" id="uploadArea">
    <div class="icon">📸</div>
    <div class="text">음식 사진을 촬영하거나 선택하세요</div>
    <div class="subtext">식사 전 음식 사진을 찍어주세요</div>
    <div style="display:flex; gap:10px; justify-content:center; margin-top:16px">
      <button class="btn btn-primary" onclick="event.stopPropagation(); document.getElementById('cameraInput').click()" style="padding:10px 20px; font-size:0.9em">📷 카메라 촬영</button>
      <button class="btn btn-secondary" onclick="event.stopPropagation(); document.getElementById('galleryInput').click()" style="padding:10px 20px; font-size:0.9em">🖼️ 갤러리 선택</button>
    </div>
    <input type="file" id="cameraInput" accept="image/*" capture="environment" style="display:none" />
    <input type="file" id="galleryInput" accept="image/*" style="display:none" />
    <input type="file" id="fileInput" accept="image/*" style="display:none" />
  </div>

  <!-- 미리보기 -->
  <div class="preview-section" id="previewSection">
    <img id="previewImg" />
    <div class="preview-actions">
      <button class="btn btn-primary" id="analyzeBtn" onclick="analyzeFood()">🔍 영양 분석</button>
      <button class="btn btn-secondary" onclick="resetAll()">다시 촬영</button>
    </div>
  </div>

  <!-- 로딩 -->
  <div class="loading" id="loading">
    <div class="spinner"></div>
    <p id="loadingText">AI가 음식을 분석하고 있습니다...</p>
    <p style="font-size:0.85em; color:#555; margin-top:8px">GPT-4o Vision + DB 매칭 (약 5~15초)</p>
  </div>

  <!-- 에러 -->
  <div class="error-box" id="errorBox"></div>

  <!-- 나눠먹기 인원 선택 팝업 -->
  <div class="sharing-overlay" id="sharingOverlay">
    <div class="sharing-popup">
      <h3>몇 명이서 드셨나요?</h3>
      <div class="sub">인원을 선택하면 균등 분배되고, 이후 음식별로 세부 조정할 수 있어요</div>
      <div class="people-grid" id="peopleGrid"></div>
      <button class="btn btn-secondary" onclick="closeSharingPopup()" style="width:100%; padding:10px">취소</button>
    </div>
  </div>

  <!-- 이상해요 신고 오버레이 -->
  <div class="report-overlay" id="reportOverlay">
    <div class="report-box">
      <h3>⚠️ 이상해요 신고</h3>
      <div class="report-food-name" id="reportFoodName"></div>
      <textarea id="reportText" placeholder="어떤 점이 이상한가요? (예: 음식 이름이 다릅니다, 칼로리가 너무 높아요, 양이 이상해요 등)"></textarea>
      <div class="report-actions">
        <button class="report-cancel-btn" onclick="closeReportModal()">취소</button>
        <button class="report-submit-btn" onclick="submitReport()">신고하기</button>
      </div>
    </div>
  </div>

  <!-- 분석 결과 -->
  <div class="result-section" id="resultSection">
    <div class="result-header">
      <h2 id="resultTitle">분석 결과</h2>
      <button class="btn btn-secondary" onclick="resetAll()" style="padding:8px 16px; font-size:0.85em">새 사진</button>
    </div>

    <!-- Feature 1: 오늘의 현황 카드 -->
    <div id="todayProgressCard" style="display:none"></div>

    <div id="foodCards"></div>
    <div id="summaryCard"></div>
    <div id="saveMealArea"></div>

    <!-- 식후 안내 배너 (첫 분석 후에만 보임) -->
    <div class="after-meal-banner" id="afterMealBanner">
      <div class="banner-title">식사를 마치셨나요?</div>
      <div class="banner-desc">
        식사 후 남은 음식 사진을 찍으면 <strong>실제 섭취한 영양소</strong>를 정확하게 계산해드려요.<br>
        사진을 찍기 어려우면 직접 섭취량을 입력할 수도 있어요.
      </div>
      <div class="after-meal-actions">
        <button class="btn btn-green" onclick="showAfterUpload()">📸 식후 사진 찍기</button>
        <button class="btn btn-orange" onclick="showManualEntry()">✏️ 직접 입력하기</button>
      </div>
    </div>
  </div>

  <!-- 식후 사진 업로드 -->
  <div class="after-upload-section" id="afterUploadSection">
    <div class="after-upload-box">
      <div style="font-size:2em; margin-bottom:8px">📸</div>
      <div style="color:#6ee7b7; font-weight:600">식후 사진을 촬영하거나 선택하세요</div>
      <div style="color:#888; font-size:0.85em; margin-top:6px">남은 음식이 보이도록 촬영해주세요</div>
      <div style="display:flex; gap:10px; justify-content:center; margin-top:12px">
        <button class="btn btn-green" onclick="document.getElementById('afterCameraInput').click()" style="padding:8px 16px; font-size:0.85em">📷 카메라</button>
        <button class="btn btn-secondary" onclick="document.getElementById('afterGalleryInput').click()" style="padding:8px 16px; font-size:0.85em">🖼️ 갤러리</button>
      </div>
      <input type="file" id="afterInput" accept="image/*" style="display:none" />
      <input type="file" id="afterCameraInput" accept="image/*" capture="environment" style="display:none" />
      <input type="file" id="afterGalleryInput" accept="image/*" style="display:none" />
    </div>
    <img id="afterPreviewImg" style="display:none; max-width:100%; max-height:300px; border-radius:12px; margin-bottom:12px" />
    <div id="afterActions" style="display:none">
      <button class="btn btn-green" onclick="analyzeLeftover()">🔍 실제 섭취량 분석</button>
      <button class="btn btn-secondary" onclick="hideAfterUpload()">취소</button>
    </div>
  </div>

  <!-- 수동 섭취량 입력 -->
  <div class="manual-section" id="manualSection">
    <h3 style="color:#f59e0b; margin-bottom:12px">각 음식을 얼마나 드셨나요?</h3>
    <div id="manualFoodList"></div>
    <div style="text-align:center; margin-top:14px">
      <button class="btn btn-orange" onclick="applyManualEntry()">✅ 섭취량 반영</button>
      <button class="btn btn-secondary" onclick="hideManualEntry()">취소</button>
    </div>
  </div>

  </div><!-- /tab-analyze -->

  <!-- 탭: 대시보드 -->
  <div id="tab-dashboard" style="display:none">

    <div class="dash-header">
      <h2>내 영양 기록</h2>
      <div style="display:flex; gap:10px; align-items:center;">
        <button class="btn btn-secondary" onclick="openGoalModal()" style="padding:8px 12px; font-size:0.8em">⚙️ 목표 설정</button>
        <div class="period-btns">
          <button class="period-btn" onclick="loadDashboard(7)">7일</button>
          <button class="period-btn active" onclick="loadDashboard(30)">30일</button>
          <button class="period-btn" onclick="loadDashboard(90)">90일</button>
        </div>
      </div>
    </div>

    <!-- Feature 1: 목표 설정 모달 -->
    <div id="goalModal" style="display:none; position:fixed; top:0; left:0; right:0; bottom:0; background:rgba(0,0,0,0.8); z-index:200; align-items:center; justify-content:center;">
      <div style="background:#1a1a2e; border:1px solid #3b82f6; border-radius:16px; padding:28px; max-width:360px; width:90%;">
        <h3 style="color:#60a5fa; margin-bottom:20px; font-size:1.1em">일일 목표 설정</h3>
        <div style="margin-bottom:16px;">
          <label style="display:block; color:#888; font-size:0.85em; margin-bottom:6px">칼로리 (kcal)</label>
          <input type="number" id="goalCalories" value="1800" style="width:100%; padding:10px; background:#0f0f1a; border:1px solid #2a2a4a; border-radius:8px; color:#fff; font-size:1em;">
        </div>
        <div style="margin-bottom:20px;">
          <label style="display:block; color:#888; font-size:0.85em; margin-bottom:6px">단백질 (g)</label>
          <input type="number" id="goalProtein" value="120" style="width:100%; padding:10px; background:#0f0f1a; border:1px solid #2a2a4a; border-radius:8px; color:#fff; font-size:1em;">
        </div>
        <div style="display:flex; gap:10px;">
          <button class="btn btn-secondary" onclick="closeGoalModal()" style="flex:1; padding:10px;">취소</button>
          <button class="btn btn-primary" onclick="saveGoal()" style="flex:1; padding:10px;">저장</button>
        </div>
      </div>
    </div>

    <!-- 데이터 없을 때 -->
    <div class="dash-empty" id="dashEmpty">
      <div class="icon">📊</div>
      <div>아직 식사 기록이 없습니다.</div>
      <div style="font-size:0.85em; color:#555; margin-top:8px">음식 사진을 분석하면 자동으로 기록됩니다.</div>
    </div>

    <!-- 대시보드 콘텐츠 -->
    <div id="dashContent" style="display:none">

      <!-- 주간 리포트 -->
      <div class="weekly-card">
        <div class="title">📋 주간 리포트</div>
        <div class="report" id="weeklyReport"></div>
        <div class="meta" id="dashMeta"></div>
      </div>

      <!-- 경고 -->
      <div class="warning-list" id="warningList"></div>

      <!-- 일평균 요약 -->
      <div class="avg-grid" id="avgGrid"></div>

      <!-- 칼로리 추이 -->
      <div class="chart-card">
        <div class="chart-title">칼로리 추이</div>
        <canvas id="calChart"></canvas>
      </div>

      <!-- 영양소 추이 -->
      <div class="chart-card">
        <div class="chart-title">영양소 추이 (단백질·탄수화물·지방)</div>
        <canvas id="macroChart"></canvas>
      </div>

      <!-- 탄단지 비율 -->
      <div class="chart-card" style="max-width:360px; margin:0 auto 16px;">
        <div class="chart-title">탄단지 비율 (일평균)</div>
        <canvas id="balanceChart"></canvas>
      </div>

      <!-- 식사 패턴 -->
      <div class="chart-card">
        <div class="chart-title">식사 시간대 패턴</div>
        <canvas id="patternChart"></canvas>
      </div>

      <!-- 자주 먹는 음식 TOP 10 -->
      <div class="chart-card">
        <div class="chart-title">자주 먹는 음식 TOP 10</div>
        <div id="topFoodsList"></div>
      </div>

      <!-- Feature 2: 주간 매크로 비율 분석 -->
      <div class="chart-card">
        <div class="chart-title">주간 매크로 비율</div>
        <canvas id="weeklyMacroChart"></canvas>
        <div id="weeklyMacroFeedback" style="color:#888; font-size:0.85em; margin-top:12px; padding-top:12px; border-top:1px solid #2a2a4a;"></div>
      </div>

      <!-- Feature 3: 단백질 섭취 타이밍 -->
      <div class="chart-card">
        <div class="chart-title">단백질 섭취 타이밍</div>
        <canvas id="proteinTimingChart"></canvas>
        <div id="proteinTimingFeedback" style="margin-top:12px; padding-top:12px; border-top:1px solid #2a2a4a;"></div>
      </div>

    </div><!-- /dashContent -->
  </div><!-- /tab-dashboard -->

</div>

<script>
// ── 상태 ──
let selectedFile = null;
let afterFile = null;
let currentAnalysis = null;
let originalAnalysis = null; // 원본 분석 (100% 기준)
// 나눠먹기는 sharingPcts / sharingPeople / sharingMode로 관리
let isAfterMealDone = false;
let mealSaved = false; // 수동 저장 완료 여부

// ── 식사 세션 상태 (상단 선언 — 복원 함수에서 필요) ──
let sessionId = null;
let sessionActive = false;
let sessionPhotoCount = 0;
let sessionFoodCount = 0;
let sessionTotalCal = 0;

// ── 세션 복원 함수 ──
function saveStateToSession() {
  try {
    const state = {
      currentAnalysis, originalAnalysis, isAfterMealDone, mealSaved,
      sessionId, sessionActive, sessionPhotoCount, sessionFoodCount, sessionTotalCal
    };
    sessionStorage.setItem('nutrilens_state', JSON.stringify(state));
  } catch(e) { console.warn('상태 저장 실패:', e); }
}
// 식전 사진 base64를 별도 저장 (모바일 카메라 전환 시 페이지 리로드 대비)
function saveBeforeImageToSession() {
  try {
    const img = document.getElementById('previewImg');
    if (img && img.src && img.src.startsWith('data:')) {
      sessionStorage.setItem('nutrilens_before_img', img.src);
    }
  } catch(e) { console.warn('식전 이미지 저장 실패:', e); }
}
function restoreBeforeImageFromSession() {
  try {
    const src = sessionStorage.getItem('nutrilens_before_img');
    if (!src) return false;
    // base64 → File 객체 복원
    const arr = src.split(',');
    const mime = arr[0].match(/:(.*?);/)[1];
    const bstr = atob(arr[1]);
    let n = bstr.length;
    const u8arr = new Uint8Array(n);
    while(n--) u8arr[n] = bstr.charCodeAt(n);
    selectedFile = new File([u8arr], 'before.jpg', {type: mime});
    return true;
  } catch(e) { return false; }
}
function restoreStateFromSession() {
  try {
    const raw = sessionStorage.getItem('nutrilens_state');
    if (!raw) return false;
    const state = JSON.parse(raw);
    if (!state.currentAnalysis) return false;
    currentAnalysis = state.currentAnalysis;
    originalAnalysis = state.originalAnalysis;
    isAfterMealDone = state.isAfterMealDone || false;
    mealSaved = state.mealSaved || false;
    sessionId = state.sessionId || null;
    sessionActive = state.sessionActive || false;
    sessionPhotoCount = state.sessionPhotoCount || 0;
    sessionFoodCount = state.sessionFoodCount || 0;
    sessionTotalCal = state.sessionTotalCal || 0;
    // 식전 사진 복원 (모바일 카메라 전환 후 selectedFile 유실 대비)
    if (!selectedFile) restoreBeforeImageFromSession();
    return true;
  } catch(e) { return false; }
}
function clearSessionState() {
  sessionStorage.removeItem('nutrilens_state');
}

// ── 로그인 상태 ──
let currentUser = localStorage.getItem('nutrilens_user') || '';

// 로그인 확인
if (currentUser) {
  document.getElementById('loginOverlay').style.display = 'none';
}

// ── 이전 분석 결과 복원 ──
if (currentUser && restoreStateFromSession()) {
  // 세션 모드 복원
  if (sessionActive) {
    document.getElementById('sessionBar').classList.add('active');
    document.getElementById('sessionToggleBtn').textContent = '🍽️ 정찬 모드 진행중';
    document.getElementById('sessionToggleBtn').className = 'btn btn-orange';
    document.getElementById('sessionToggleBtn').style.cssText += 'padding:8px 18px;font-size:0.85em;border-radius:20px';
    updateSessionBar();
  }
  // 분석 결과 복원
  if (currentAnalysis) {
    document.getElementById('uploadArea').style.display = 'none';
    renderResult(currentAnalysis, isAfterMealDone);
  }
}

async function doLogin() {
  const nickname = document.getElementById('nicknameInput').value.trim();
  if (!nickname) return alert('닉네임을 입력하세요.');
  try {
    const resp = await fetch('/login', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ nickname: nickname })
    });
    const data = await resp.json();
    if (data.error) return alert(data.error);
    currentUser = data.nickname;
    localStorage.setItem('nutrilens_user', currentUser);
    document.getElementById('loginOverlay').style.display = 'none';
  } catch(e) { alert('로그인 실패: ' + e.message); }
}
document.getElementById('nicknameInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') doLogin(); });

// ── 탭 전환 ──
function switchTab(tab) {
  document.getElementById('tab-analyze').style.display = tab === 'analyze' ? 'block' : 'none';
  document.getElementById('tab-dashboard').style.display = tab === 'dashboard' ? 'block' : 'none';
  document.getElementById('tabAnalyze').classList.toggle('active', tab === 'analyze');
  document.getElementById('tabDashboard').classList.toggle('active', tab === 'dashboard');
  if (tab === 'dashboard') loadDashboard(30);
}

// ── 식사 기록 자동 저장 ──
async function saveMealRecord(analysisData) {
  if (!currentUser || !analysisData || !analysisData.foods || analysisData.foods.length === 0) return;
  try {
    await fetch('/save-meal', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ nickname: currentUser, meal_data: analysisData })
    });
  } catch(e) { console.error('식사 기록 저장 실패:', e); }
}

// ── 수동 저장 + 자동저장 타이머 (1시간) ──
let autoSaveTimer = null;
const AUTO_SAVE_DELAY = 60 * 60 * 1000; // 1시간 (밀리초)

function startAutoSaveTimer() {
  clearAutoSaveTimer();
  if (sessionActive) return; // 정찬 모드에서는 타이머 안 씀
  autoSaveTimer = setTimeout(() => {
    if (!mealSaved && currentAnalysis) {
      console.log('[자동저장] 1시간 경과 — 자동 저장 실행');
      saveManually(true);
    }
  }, AUTO_SAVE_DELAY);
}
function clearAutoSaveTimer() {
  if (autoSaveTimer) { clearTimeout(autoSaveTimer); autoSaveTimer = null; }
}

async function saveManually(isAuto) {
  if (!currentAnalysis || mealSaved) return;
  await saveMealRecord(currentAnalysis);
  mealSaved = true;
  clearAutoSaveTimer();
  saveStateToSession();
  const btn = document.getElementById('saveMealBtn');
  if (btn) {
    btn.textContent = isAuto ? '⏰ 자동 저장됨' : '✅ 저장 완료!';
    btn.disabled = true;
    btn.classList.add('saved');
  }
  const hint = document.getElementById('saveMealHint');
  if (hint) hint.textContent = isAuto ? '1시간이 지나 자동으로 저장되었습니다' : '이 식사가 기록에 저장되었습니다';
}

// ── 이상해요 신고 ──
let reportFoodIndex = -1;
function openReportModal(idx) {
  const food = currentAnalysis.foods[idx];
  reportFoodIndex = idx;
  document.getElementById('reportFoodName').textContent = (food.name_ko || '음식') + ' — ' + (food.calories_kcal||0) + 'kcal, ' + (food.estimated_serving_g||'?') + 'g';
  document.getElementById('reportText').value = '';
  document.getElementById('reportOverlay').classList.add('active');
}
function closeReportModal() {
  document.getElementById('reportOverlay').classList.remove('active');
  reportFoodIndex = -1;
}
async function submitReport() {
  const text = document.getElementById('reportText').value.trim();
  if (!text) return alert('어떤 점이 이상한지 입력해주세요.');
  const food = currentAnalysis.foods[reportFoodIndex];
  try {
    const resp = await fetch('/report', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        nickname: currentUser,
        food_name: food.name_ko || '?',
        food_index: reportFoodIndex,
        food_data: { calories: food.calories_kcal, protein: food.protein_g, carbs: food.carbs_g, fat: food.fat_g, serving_g: food.estimated_serving_g, source: food.source || '' },
        report_text: text
      })
    });
    const data = await resp.json();
    closeReportModal();
    // 버튼 상태 변경 — 신고 완료 표시
    const btn = document.getElementById('report_btn_' + reportFoodIndex);
    if (btn) { btn.textContent = '✅ 신고됨'; btn.style.color = '#6ee7b7'; btn.style.borderColor = '#6ee7b7'; btn.disabled = true; }
    alert('신고가 접수되었습니다. 검토 후 개선하겠습니다!');
  } catch(e) { alert('신고 실패: ' + e.message); }
}

// ── 대시보드 ──
let dashCharts = {};
async function loadDashboard(days) {
  if (!currentUser) return;
  // 기간 버튼 활성화
  document.querySelectorAll('.period-btn').forEach(b => {
    b.classList.toggle('active', b.textContent === days+'일');
  });
  try {
    const resp = await fetch('/dashboard-data?user=' + encodeURIComponent(currentUser) + '&days=' + days);
    const data = await resp.json();
    if (!data.daily || data.daily.length === 0) {
      document.getElementById('dashEmpty').style.display = 'block';
      document.getElementById('dashContent').style.display = 'none';
      return;
    }
    document.getElementById('dashEmpty').style.display = 'none';
    document.getElementById('dashContent').style.display = 'block';
    renderDashboard(data);
  } catch(e) { console.error('대시보드 로드 실패:', e); }
}

function renderDashboard(data) {
  // 주간 리포트
  document.getElementById('weeklyReport').textContent = data.weekly_report;
  document.getElementById('dashMeta').textContent = '총 ' + data.total_days + '일, ' + data.total_meals + '끼 기록';

  // 경고
  let warnHtml = '';
  (data.warnings || []).forEach(w => {
    warnHtml += '<div class="warning-item ' + w.type + '"><span class="warning-icon">' + w.icon + '</span><div class="warning-text"><div class="wmsg">' + w.msg + '</div><div class="wdetail">' + w.detail + '</div></div></div>';
  });
  document.getElementById('warningList').innerHTML = warnHtml;

  // 일평균 요약
  const b = data.balance || {};
  document.getElementById('avgGrid').innerHTML =
    '<div class="avg-item"><div class="avg-val">' + (b.avg_calories||0) + '</div><div class="avg-label">일평균 kcal</div></div>'
    + '<div class="avg-item"><div class="avg-val" style="color:#60a5fa">' + (b.avg_protein||0) + 'g</div><div class="avg-label">단백질</div></div>'
    + '<div class="avg-item"><div class="avg-val" style="color:#fbbf24">' + (b.avg_carbs||0) + 'g</div><div class="avg-label">탄수화물</div></div>'
    + '<div class="avg-item"><div class="avg-val" style="color:#f87171">' + (b.avg_fat||0) + 'g</div><div class="avg-label">지방</div></div>';

  // 차트 그리기
  renderCalChart(data.daily);
  renderMacroChart(data.daily);
  renderBalanceChart(b);
  renderPatternChart(data.meal_pattern);
  renderTopFoods(data.top_foods);

  // Feature 1: 오늘의 현황 로드
  loadTodayProgress();

  // Feature 2: 주간 매크로 분석
  loadWeeklyMacro();

  // Feature 3: 단백질 타이밍 분석
  loadProteinTiming();
}

function destroyChart(key) {
  if (dashCharts[key]) { dashCharts[key].destroy(); dashCharts[key] = null; }
}

function renderCalChart(daily) {
  destroyChart('cal');
  const ctx = document.getElementById('calChart').getContext('2d');
  dashCharts.cal = new Chart(ctx, {
    type: 'line',
    data: {
      labels: daily.map(d => d.date.slice(5)),
      datasets: [{
        label: '칼로리 (kcal)',
        data: daily.map(d => d.calories),
        borderColor: '#6ee7b7', backgroundColor: 'rgba(110,231,183,0.1)',
        fill: true, tension: 0.3, pointRadius: 3
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: true,
      plugins: { legend: { labels: { color: '#888' } } },
      scales: {
        x: { ticks: { color: '#666', maxTicksLimit: 10 }, grid: { color: '#1a1a2e' } },
        y: { ticks: { color: '#666' }, grid: { color: '#1a1a2e' } }
      }
    }
  });
}

function renderMacroChart(daily) {
  destroyChart('macro');
  const ctx = document.getElementById('macroChart').getContext('2d');
  dashCharts.macro = new Chart(ctx, {
    type: 'line',
    data: {
      labels: daily.map(d => d.date.slice(5)),
      datasets: [
        { label: '단백질 (g)', data: daily.map(d => d.protein), borderColor: '#60a5fa', tension: 0.3, pointRadius: 2 },
        { label: '탄수화물 (g)', data: daily.map(d => d.carbs), borderColor: '#fbbf24', tension: 0.3, pointRadius: 2 },
        { label: '지방 (g)', data: daily.map(d => d.fat), borderColor: '#f87171', tension: 0.3, pointRadius: 2 }
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: true,
      plugins: { legend: { labels: { color: '#888' } } },
      scales: {
        x: { ticks: { color: '#666', maxTicksLimit: 10 }, grid: { color: '#1a1a2e' } },
        y: { ticks: { color: '#666' }, grid: { color: '#1a1a2e' } }
      }
    }
  });
}

function renderBalanceChart(balance) {
  destroyChart('balance');
  if (!balance.carb_pct) return;
  const ctx = document.getElementById('balanceChart').getContext('2d');
  dashCharts.balance = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: ['탄수화물 ' + balance.carb_pct + '%', '단백질 ' + balance.prot_pct + '%', '지방 ' + balance.fat_pct + '%'],
      datasets: [{
        data: [balance.carb_pct, balance.prot_pct, balance.fat_pct],
        backgroundColor: ['#fbbf24', '#60a5fa', '#f87171'],
        borderWidth: 0
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: true,
      plugins: {
        legend: { position: 'bottom', labels: { color: '#ccc', padding: 16, font: { size: 13 } } }
      }
    }
  });
}

function renderPatternChart(pattern) {
  destroyChart('pattern');
  if (!pattern) return;
  const labels = Object.keys(pattern);
  const values = Object.values(pattern);
  const ctx = document.getElementById('patternChart').getContext('2d');
  dashCharts.pattern = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: labels,
      datasets: [{
        label: '식사 횟수',
        data: values,
        backgroundColor: ['#fbbf24', '#6ee7b7', '#60a5fa', '#a78bfa', '#f87171'],
        borderRadius: 6
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: true,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: '#888' }, grid: { display: false } },
        y: { ticks: { color: '#666', stepSize: 1 }, grid: { color: '#1a1a2e' } }
      }
    }
  });
}

function renderTopFoods(foods) {
  if (!foods || foods.length === 0) {
    document.getElementById('topFoodsList').innerHTML = '<div style="text-align:center;color:#666;padding:20px">데이터 없음</div>';
    return;
  }
  let html = '';
  foods.forEach((f, i) => {
    html += '<div class="top-food-item">'
      + '<span class="tf-rank ' + (i < 3 ? 'top3' : '') + '">' + (i + 1) + '</span>'
      + '<span class="tf-name">' + f.name + '</span>'
      + '<span class="tf-count">' + f.count + '회</span>'
      + '<span class="tf-cal">평균 ' + f.avg_cal + 'kcal</span>'
      + '</div>';
  });
  document.getElementById('topFoodsList').innerHTML = html;
}

// ── Feature 1: 오늘의 현황 + 목표 설정 함수 ──
function openGoalModal() {
  document.getElementById('goalModal').style.display = 'flex';
}
function closeGoalModal() {
  document.getElementById('goalModal').style.display = 'none';
}
async function saveGoal() {
  if (!currentUser) { alert('로그인이 필요합니다.'); return; }
  const calories = parseInt(document.getElementById('goalCalories').value);
  const protein = parseInt(document.getElementById('goalProtein').value);
  if (!calories || !protein) { alert('모든 필드를 입력하세요.'); return; }
  try {
    const resp = await fetch('/save-goal', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({nickname: currentUser, calories: calories, protein: protein})
    });
    const data = await resp.json();
    if (data.saved) {
      alert('목표가 저장되었습니다.');
      closeGoalModal();
      loadDashboard(30); // 대시보드 새로고침
    }
  } catch(e) { alert('목표 저장 실패: ' + e.message); }
}
async function loadTodayProgress() {
  if (!currentUser) return;
  try {
    const resp = await fetch('/today-progress?user=' + encodeURIComponent(currentUser));
    const data = await resp.json();
    renderTodayProgress(data);
  } catch(e) { console.error('오늘의 현황 로드 실패:', e); }
}
function renderTodayProgress(data) {
  const card = document.getElementById('todayProgressCard');
  if (!card) return;

  const calorieColor = data.calorie_pct < 100 ? '#6ee7b7' : data.calorie_pct < 110 ? '#fbbf24' : '#f87171';
  const proteinColor = data.protein_pct < 100 ? '#6ee7b7' : data.protein_pct < 110 ? '#fbbf24' : '#f87171';

  const html = '<div style="background:linear-gradient(135deg,#1a2e2e,#1e3a3e); border:1px solid #059669; border-radius:14px; padding:18px; margin-bottom:16px;">'
    + '<div style="color:#6ee7b7; font-weight:600; margin-bottom:14px; font-size:0.95em">오늘의 현황</div>'
    + '<div style="display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-bottom:12px;">'
    + '<div>'
    + '<div style="font-size:0.8em; color:#888; margin-bottom:4px">칼로리</div>'
    + '<div style="height:6px; background:#1a1a2e; border-radius:3px; margin-bottom:4px; overflow:hidden;">'
    + '<div style="width:' + Math.min(data.calorie_pct, 100) + '%; height:100%; background:' + calorieColor + '; border-radius:3px;"></div>'
    + '</div>'
    + '<div style="font-size:0.85em; color:#e0e0e0; font-weight:600;">' + data.calories + ' / ' + data.goal_calories + ' kcal</div>'
    + '<div style="font-size:0.75em; color:#888; margin-top:2px;">남은 ' + Math.max(0, data.remaining_calories) + ' kcal</div>'
    + '</div>'
    + '<div>'
    + '<div style="font-size:0.8em; color:#888; margin-bottom:4px">단백질</div>'
    + '<div style="height:6px; background:#1a1a2e; border-radius:3px; margin-bottom:4px; overflow:hidden;">'
    + '<div style="width:' + Math.min(data.protein_pct, 100) + '%; height:100%; background:' + proteinColor + '; border-radius:3px;"></div>'
    + '</div>'
    + '<div style="font-size:0.85em; color:#e0e0e0; font-weight:600;">' + data.protein.toFixed(1) + ' / ' + data.goal_protein + 'g</div>'
    + '<div style="font-size:0.75em; color:#888; margin-top:2px;">남은 ' + Math.max(0, data.remaining_protein).toFixed(1) + 'g</div>'
    + '</div>'
    + '</div>'
    + '</div>';
  card.innerHTML = html;
  card.style.display = 'block';
}

// ── Feature 2: 주간 매크로 분석 함수 ──
async function loadWeeklyMacro() {
  if (!currentUser) return;
  try {
    const resp = await fetch('/weekly-macro?user=' + encodeURIComponent(currentUser));
    const data = await resp.json();
    renderWeeklyMacro(data);
  } catch(e) { console.error('주간 매크로 로드 실패:', e); }
}
function renderWeeklyMacro(data) {
  if (!data.days || data.days.length === 0) return;

  destroyChart('weeklyMacro');
  const ctx = document.getElementById('weeklyMacroChart').getContext('2d');

  const dayLabels = data.days.map((d, i) => {
    const date = new Date(d.date);
    const dayNames = ['일', '월', '화', '수', '목', '금', '토'];
    return date.getMonth() + 1 + '/' + date.getDate() + ' (' + dayNames[date.getDay()] + ')';
  });

  dashCharts.weeklyMacro = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: dayLabels,
      datasets: [
        {
          label: '탄수화물',
          data: data.days.map(d => d.carb_pct),
          backgroundColor: '#fbbf24',
          stack: 'macro'
        },
        {
          label: '단백질',
          data: data.days.map(d => d.protein_pct),
          backgroundColor: '#60a5fa',
          stack: 'macro'
        },
        {
          label: '지방',
          data: data.days.map(d => d.fat_pct),
          backgroundColor: '#f87171',
          stack: 'macro'
        }
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: true,
      indexAxis: 'x',
      scales: {
        x: { stacked: true, ticks: { color: '#888' }, grid: { display: false } },
        y: { stacked: true, ticks: { color: '#666', max: 100 }, grid: { color: '#1a1a2e' } }
      },
      plugins: { legend: { labels: { color: '#888' } } }
    }
  });

  document.getElementById('weeklyMacroFeedback').textContent = data.feedback;
}

// ── Feature 3: 단백질 타이밍 분석 함수 ──
async function loadProteinTiming() {
  if (!currentUser) return;
  try {
    const resp = await fetch('/protein-timing?user=' + encodeURIComponent(currentUser));
    const data = await resp.json();
    renderProteinTiming(data);
  } catch(e) { console.error('단백질 타이밍 로드 실패:', e); }
}
function renderProteinTiming(data) {
  if (!data.avg_per_slot) return;

  destroyChart('proteinTiming');
  const ctx = document.getElementById('proteinTimingChart').getContext('2d');

  const slots = ['아침', '점심', '저녁', '간식'];
  const avgValues = slots.map(s => data.avg_per_slot[s] || 0);

  dashCharts.proteinTiming = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: slots,
      datasets: [
        {
          label: '평균 단백질 (g)',
          data: avgValues,
          backgroundColor: '#60a5fa',
          borderRadius: 6
        }
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: true,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: '#888' }, grid: { display: false } },
        y: { ticks: { color: '#666' }, grid: { color: '#1a1a2e' } }
      }
    }
  });

  // 선택적: 임계선 그리기 (플러그인 사용 - 간단 버전)
  let feedbackHtml = '';
  if (data.feedback && data.feedback.length > 0) {
    feedbackHtml = data.feedback.map(f => '<div style="font-size:0.85em; color:#f87171; margin-bottom:6px;">⚠️ ' + f + '</div>').join('');
  } else {
    feedbackHtml = '<div style="font-size:0.85em; color:#6ee7b7;">✓ 단백질 섭취가 균형잡혀 있습니다.</div>';
  }
  document.getElementById('proteinTimingFeedback').innerHTML = feedbackHtml;
}

// ── 식사 세션 함수 ──
async function toggleSession() {
  if (sessionActive) {
    if (confirm('정찬 모드를 종료할까요?')) endSession();
    return;
  }
  try {
    const resp = await fetch('/session/start', { method: 'POST' });
    const data = await resp.json();
    sessionId = data.session_id;
    sessionActive = true;
    sessionPhotoCount = 0; sessionFoodCount = 0; sessionTotalCal = 0;
    document.getElementById('sessionBar').classList.add('active');
    document.getElementById('sessionToggleBtn').textContent = '🍽️ 정찬 모드 진행중';
    document.getElementById('sessionToggleBtn').className = 'btn btn-orange';
    document.getElementById('sessionToggleBtn').style.cssText += 'padding:8px 18px;font-size:0.85em;border-radius:20px';
    updateSessionBar();
  } catch(e) { alert('세션 시작 실패: ' + e.message); }
}

async function endSession() {
  if (!sessionId) return;
  try {
    const resp = await fetch('/session/end', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ session_id: sessionId })
    });
    const data = await resp.json();
    sessionActive = false;
    document.getElementById('sessionBar').classList.remove('active');
    document.getElementById('sessionToggleBtn').textContent = '🍽️ 정찬 모드 시작';
    document.getElementById('sessionToggleBtn').className = 'btn btn-green';
    document.getElementById('sessionToggleBtn').style.cssText += 'padding:8px 18px;font-size:0.85em;border-radius:20px';
    // 최종 결과 표시 (저장은 사용자가 수동으로)
    if (data.foods && data.foods.length > 0) {
      currentAnalysis = data;
      originalAnalysis = JSON.parse(JSON.stringify(data));
      mealSaved = false;
      renderResult(data, false);
      saveStateToSession();
      startAutoSaveTimer();
    }
    sessionId = null;
  } catch(e) { alert('세션 종료 실패: ' + e.message); }
}

function updateSessionBar() {
  document.getElementById('sessionPhotoCount').textContent = sessionPhotoCount;
  document.getElementById('sessionFoodCount').textContent = sessionFoodCount;
  document.getElementById('sessionTotalCal').textContent = Math.round(sessionTotalCal);
}

// ── 음식 수정 함수 ──
function openEditModal(idx) {
  const food = currentAnalysis.foods[idx];
  document.getElementById('editFoodName').value = food.name_ko || '';
  document.getElementById('editServingPct').value = 100;
  document.getElementById('editServingVal').textContent = '100%';
  document.getElementById('editFoodIndex').value = idx;
  document.getElementById('editOriginalName').value = food.name_ko || '';
  document.getElementById('editOverlay').classList.add('active');
}

function closeEditModal() {
  document.getElementById('editOverlay').classList.remove('active');
}

function setEditPct(pct) {
  document.getElementById('editServingPct').value = pct;
  document.getElementById('editServingVal').textContent = pct + '%';
}

async function submitCorrection() {
  const idx = parseInt(document.getElementById('editFoodIndex').value);
  const originalName = document.getElementById('editOriginalName').value;
  const correctedName = document.getElementById('editFoodName').value.trim();
  const servingPct = parseInt(document.getElementById('editServingPct').value);

  if (!correctedName) return alert('음식명을 입력하세요.');

  try {
    const resp = await fetch('/correct', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ original_name: originalName, corrected_name: correctedName, serving_pct: servingPct })
    });
    const data = await resp.json();

    if (data.matched) {
      // DB 매칭 성공 — 영양소 교체
      currentAnalysis.foods[idx].name_ko = correctedName;
      currentAnalysis.foods[idx].db_name = data.db_name;
      currentAnalysis.foods[idx].source = data.source;
      currentAnalysis.foods[idx].db_matched = true;
      currentAnalysis.foods[idx].calories_kcal = data.calories_kcal;
      currentAnalysis.foods[idx].protein_g = data.protein_g;
      currentAnalysis.foods[idx].carbs_g = data.carbs_g;
      currentAnalysis.foods[idx].fat_g = data.fat_g;
      currentAnalysis.foods[idx].fiber_g = data.fiber_g;
      currentAnalysis.foods[idx].sodium_mg = data.sodium_mg;
      currentAnalysis.foods[idx].sugar_g = data.sugar_g;
      currentAnalysis.foods[idx].estimated_serving_g = data.estimated_serving_g;
    } else {
      // DB에 없으면 이름만 바꾸고 양 조절
      currentAnalysis.foods[idx].name_ko = correctedName;
      const ratio = servingPct / 100;
      const orig = originalAnalysis.foods[idx];
      if (orig) {
        currentAnalysis.foods[idx].calories_kcal = Math.round((orig.calories_kcal||0) * ratio * 10) / 10;
        currentAnalysis.foods[idx].protein_g = Math.round((orig.protein_g||0) * ratio * 10) / 10;
        currentAnalysis.foods[idx].carbs_g = Math.round((orig.carbs_g||0) * ratio * 10) / 10;
        currentAnalysis.foods[idx].fat_g = Math.round((orig.fat_g||0) * ratio * 10) / 10;
      }
    }

    closeEditModal();
    renderResult(currentAnalysis, isAfterMealDone);
    saveStateToSession();
  } catch(e) { alert('수정 실패: ' + e.message); }
}

function deleteFood() {
  const idx = parseInt(document.getElementById('editFoodIndex').value);
  if (confirm('이 음식을 삭제할까요?')) {
    currentAnalysis.foods.splice(idx, 1);
    if (originalAnalysis && originalAnalysis.foods) originalAnalysis.foods.splice(idx, 1);
    closeEditModal();
    renderResult(currentAnalysis, isAfterMealDone);
    saveStateToSession();
  }
}

// ── 파일 선택 ──
const uploadArea = document.getElementById('uploadArea');
const fileInput = document.getElementById('fileInput');
const cameraInput = document.getElementById('cameraInput');
const galleryInput = document.getElementById('galleryInput');
// 영역 클릭 시 기본 파일 선택 (PC 등 버튼 외 영역 클릭)
uploadArea.addEventListener('click', (e) => { if (e.target === uploadArea || e.target.closest('.icon') || e.target.closest('.text') || e.target.closest('.subtext')) fileInput.click(); });
fileInput.addEventListener('change', (e) => handleFile(e.target.files[0]));
cameraInput.addEventListener('change', (e) => handleFile(e.target.files[0]));
galleryInput.addEventListener('change', (e) => handleFile(e.target.files[0]));
uploadArea.addEventListener('dragover', (e) => { e.preventDefault(); uploadArea.classList.add('dragover'); });
uploadArea.addEventListener('dragleave', () => uploadArea.classList.remove('dragover'));
uploadArea.addEventListener('drop', (e) => { e.preventDefault(); uploadArea.classList.remove('dragover'); if (e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0]); });

function handleFile(file) {
  if (!file || !file.type.startsWith('image/')) return alert('이미지 파일만 가능합니다.');
  if (file.size > 10*1024*1024) return alert('10MB 이하만 가능합니다.');
  selectedFile = file;
  const reader = new FileReader();
  reader.onload = (e) => {
    document.getElementById('previewImg').src = e.target.result;
    uploadArea.style.display = 'none';
    document.getElementById('previewSection').style.display = 'block';
  };
  reader.readAsDataURL(file);
}

function addNextPhoto() {
  // 세션 모드에서 다음 사진 추가 — 세션 유지, 업로드만 초기화
  selectedFile = null; afterFile = null;
  currentAnalysis = null; originalAnalysis = null;
  sharingPcts = {}; sharingMode = false; sharingPeople = 1;
  fileInput.value = ''; cameraInput.value = ''; galleryInput.value = '';
  uploadArea.style.display = 'block';
  document.getElementById('previewSection').style.display = 'none';
  document.getElementById('resultSection').style.display = 'none';
  document.getElementById('errorBox').style.display = 'none';
  document.getElementById('loading').style.display = 'none';
}

function resetAll() {
  selectedFile = null; afterFile = null; currentAnalysis = null; originalAnalysis = null;
  sharingPcts = {}; sharingMode = false; sharingPeople = 1; isAfterMealDone = false; mealSaved = false;
  clearAutoSaveTimer();
  clearSessionState();
  sessionStorage.removeItem('nutrilens_before_img');
  fileInput.value = ''; cameraInput.value = ''; galleryInput.value = '';
  uploadArea.style.display = 'block';
  document.getElementById('previewSection').style.display = 'none';
  document.getElementById('resultSection').style.display = 'none';
  document.getElementById('errorBox').style.display = 'none';
  document.getElementById('loading').style.display = 'none';
  document.getElementById('afterUploadSection').style.display = 'none';
  document.getElementById('manualSection').style.display = 'none';
  const sessionNextBtn = document.getElementById('sessionNextPhotoArea');
  if (sessionNextBtn) sessionNextBtn.style.display = 'none';
}

// ── 식전 분석 API ──
async function analyzeFood() {
  if (!selectedFile) return alert('사진을 먼저 선택해주세요.');
  document.getElementById('previewSection').style.display = 'none';
  document.getElementById('loading').style.display = 'block';
  document.getElementById('loadingText').textContent = 'AI가 음식을 분석하고 있습니다...';
  document.getElementById('errorBox').style.display = 'none';

  const formData = new FormData();
  formData.append('image', selectedFile);
  formData.append('api_key', '');
  if (sessionActive && sessionId) formData.append('session_id', sessionId);

  try {
    const resp = await fetch('/analyze', { method: 'POST', body: formData });
    const data = await resp.json();
    document.getElementById('loading').style.display = 'none';
    if (data.error) {
      document.getElementById('errorBox').innerHTML = '<strong>오류:</strong> ' + data.error;
      document.getElementById('errorBox').style.display = 'block';
      document.getElementById('previewSection').style.display = 'block';
      return;
    }
    originalAnalysis = JSON.parse(JSON.stringify(data));
    currentAnalysis = data;
    isAfterMealDone = false;
    mealSaved = false;

    // 세션 모드면 누적 바 업데이트
    if (sessionActive && data.session_summary) {
      sessionPhotoCount = data.session_photo_count || 0;
      sessionFoodCount = data.session_total_foods || 0;
      sessionTotalCal = data.session_summary.total_calories || 0;
      updateSessionBar();
    }

    renderResult(data, false);
    saveStateToSession();
    saveBeforeImageToSession();  // 모바일 카메라 전환 시 식전 사진 보존
    startAutoSaveTimer();
  } catch (err) {
    document.getElementById('loading').style.display = 'none';
    document.getElementById('errorBox').innerHTML = '<strong>네트워크 오류:</strong> ' + err.message;
    document.getElementById('errorBox').style.display = 'block';
    document.getElementById('previewSection').style.display = 'block';
  }
}

// ── 식후 사진 업로드 ──
function showAfterUpload() {
  document.getElementById('afterMealBanner').style.display = 'none';
  document.getElementById('afterUploadSection').style.display = 'block';
}
function hideAfterUpload() {
  document.getElementById('afterUploadSection').style.display = 'none';
  document.getElementById('afterMealBanner').style.display = 'block';
}

function handleAfterFile(e) {
  afterFile = e.target.files[0];
  if (afterFile) {
    const r = new FileReader();
    r.onload = (ev) => {
      const img = document.getElementById('afterPreviewImg');
      img.src = ev.target.result;
      img.style.display = 'block';
      document.getElementById('afterActions').style.display = 'block';
    };
    r.readAsDataURL(afterFile);
  }
}
document.getElementById('afterInput').addEventListener('change', handleAfterFile);
document.getElementById('afterCameraInput').addEventListener('change', handleAfterFile);
document.getElementById('afterGalleryInput').addEventListener('change', handleAfterFile);

async function analyzeLeftover() {
  if (!selectedFile || !afterFile) return alert('식후 사진이 필요합니다.');
  document.getElementById('afterUploadSection').style.display = 'none';
  document.getElementById('loading').style.display = 'block';
  document.getElementById('loadingText').textContent = '식전/식후 사진을 비교하고 있습니다...';

  const formData = new FormData();
  formData.append('before', selectedFile);
  formData.append('after', afterFile);
  formData.append('api_key', '');

  try {
    const resp = await fetch('/analyze-leftover', { method: 'POST', body: formData });
    const data = await resp.json();
    document.getElementById('loading').style.display = 'none';
    if (data.error) {
      let errMsg = '<strong>오류:</strong> ' + data.error;
      if (data.raw) errMsg += '<br><small style="color:#888">' + data.raw.substring(0,200) + '</small>';
      document.getElementById('errorBox').innerHTML = errMsg;
      document.getElementById('errorBox').style.display = 'block';
      document.getElementById('afterUploadSection').style.display = 'block';
      return;
    }
    currentAnalysis = data;
    isAfterMealDone = true;
    mealSaved = false;
    renderResult(data, true);
    saveStateToSession();
    startAutoSaveTimer();
  } catch (err) {
    document.getElementById('loading').style.display = 'none';
    document.getElementById('errorBox').innerHTML = '<strong>네트워크 오류:</strong> ' + err.message;
    document.getElementById('errorBox').style.display = 'block';
    document.getElementById('afterUploadSection').style.display = 'block';
  }
}

// ── 수동 섭취량 입력 ──
function showManualEntry() {
  document.getElementById('afterMealBanner').style.display = 'none';
  const foods = originalAnalysis ? originalAnalysis.foods || [] : [];
  let html = '';
  foods.forEach((f, i) => {
    html += `
      <div class="manual-food-item">
        <div class="manual-food-name">${f.name_ko || '음식 '+(i+1)}</div>
        <div style="display:flex; align-items:center; gap:10px">
          <input type="range" class="eaten-slider" id="slider_${i}" min="0" max="100" value="100" oninput="updateSliderLabel(${i})">
          <span class="eaten-value" id="sliderVal_${i}">100%</span>
        </div>
      </div>
    `;
  });
  document.getElementById('manualFoodList').innerHTML = html;
  document.getElementById('manualSection').style.display = 'block';
}
function hideManualEntry() {
  document.getElementById('manualSection').style.display = 'none';
  document.getElementById('afterMealBanner').style.display = 'block';
}
function updateSliderLabel(i) {
  const val = document.getElementById('slider_'+i).value;
  document.getElementById('sliderVal_'+i).textContent = val + '%';
}
function applyManualEntry() {
  if (!originalAnalysis) return;
  const data = JSON.parse(JSON.stringify(originalAnalysis));
  (data.foods || []).forEach((f, i) => {
    const pct = parseInt(document.getElementById('slider_'+i).value) || 0;
    const ratio = pct / 100;
    f.eaten_pct = pct;
    f.calories_kcal = Math.round((f.calories_kcal||0) * ratio);
    f.protein_g = Math.round((f.protein_g||0) * ratio * 10) / 10;
    f.carbs_g = Math.round((f.carbs_g||0) * ratio * 10) / 10;
    f.fat_g = Math.round((f.fat_g||0) * ratio * 10) / 10;
    f.estimated_serving_g = Math.round((f.estimated_serving_g||0) * ratio);
    f.leftover_note = pct === 100 ? '전부 섭취' : pct === 0 ? '먹지 않음' : pct + '% 섭취';
  });
  const s = data.meal_summary || {};
  s.total_calories = Math.round(data.foods.reduce((a,f) => a+(f.calories_kcal||0), 0));
  s.total_protein = Math.round(data.foods.reduce((a,f) => a+(f.protein_g||0), 0) * 10) / 10;
  s.total_carbs = Math.round(data.foods.reduce((a,f) => a+(f.carbs_g||0), 0) * 10) / 10;
  s.total_fat = Math.round(data.foods.reduce((a,f) => a+(f.fat_g||0), 0) * 10) / 10;
  data.meal_summary = s;
  currentAnalysis = data;
  isAfterMealDone = true;
  mealSaved = false;
  document.getElementById('manualSection').style.display = 'none';
  renderResult(data, true);
  saveStateToSession();
  startAutoSaveTimer();
}

// ── 나눠먹기 시스템 ──
let sharingPeople = 1;    // 총 인원수
let sharingPcts = {};     // 음식별 내 비율 { index: 0~100 }
let sharingMode = false;  // 나눠먹기 활성화 여부

function openSharingPopup() {
  const grid = document.getElementById('peopleGrid');
  grid.innerHTML = '';
  for (let n = 1; n <= 10; n++) {
    const btn = document.createElement('button');
    btn.className = 'people-btn' + (n === sharingPeople ? ' selected' : '');
    btn.textContent = n + '명';
    btn.onclick = () => selectPeople(n);
    grid.appendChild(btn);
  }
  document.getElementById('sharingOverlay').classList.add('active');
}
function closeSharingPopup() {
  document.getElementById('sharingOverlay').classList.remove('active');
}
function selectPeople(n) {
  sharingPeople = n;
  sharingMode = (n > 1);
  const defaultPct = Math.round(100 / n);
  // 모든 음식에 균등 비율 적용
  if (currentAnalysis && currentAnalysis.foods) {
    currentAnalysis.foods.forEach((f, i) => {
      sharingPcts[i] = defaultPct;
    });
  }
  closeSharingPopup();
  updateSharingUI();
  recalcTotal();
}
function setSharingPct(idx, val) {
  val = Math.max(0, Math.min(100, parseInt(val) || 0));
  sharingPcts[idx] = val;
  const slider = document.getElementById('share_slider_'+idx);
  const label = document.getElementById('share_pct_'+idx);
  const summary = document.getElementById('share_summary_'+idx);
  if (slider) slider.value = val;
  if (label) label.textContent = val + '%';
  if (summary) {
    const food = currentAnalysis.foods[idx];
    const cal = Math.round((food.calories_kcal||0) * val / 100);
    summary.textContent = '내 섭취: ' + cal + 'kcal';
  }
  // 프리셋 버튼 활성 상태 업데이트
  document.querySelectorAll('.preset-btn[data-idx="'+idx+'"]').forEach(b => {
    b.classList.toggle('active', parseInt(b.dataset.val) === val);
  });
  recalcTotal();
}
function updateSharingUI() {
  if (!currentAnalysis) return;
  (currentAnalysis.foods||[]).forEach((f, i) => {
    const container = document.getElementById('sharing_container_'+i);
    if (!container) return;
    if (!sharingMode) {
      container.style.display = 'none';
      sharingPcts[i] = 100;
      return;
    }
    container.style.display = 'block';
    const pct = sharingPcts[i] || 100;
    const slider = document.getElementById('share_slider_'+i);
    const label = document.getElementById('share_pct_'+i);
    if (slider) slider.value = pct;
    if (label) label.textContent = pct + '%';
    // 프리셋 버튼 상태
    document.querySelectorAll('.preset-btn[data-idx="'+i+'"]').forEach(b => {
      b.classList.toggle('active', parseInt(b.dataset.val) === pct);
    });
  });
  // 나눠먹기 버튼 텍스트 업데이트
  const btn = document.getElementById('sharingToggleBtn');
  if (btn) btn.textContent = sharingMode ? '👥 ' + sharingPeople + '명 나눠먹기 (변경)' : '👥 나눠먹기';
}
function recalcTotal() {
  if (!currentAnalysis) return;
  let tc=0, tp=0, tca=0, tf=0;
  (currentAnalysis.foods||[]).forEach((f, i) => {
    const pct = (sharingMode ? (sharingPcts[i] ?? 100) : 100) / 100;
    tc += (f.calories_kcal||0) * pct;
    tp += (f.protein_g||0) * pct;
    tca += (f.carbs_g||0) * pct;
    tf += (f.fat_g||0) * pct;
  });
  const el = id => document.getElementById(id);
  if(el('sumCal')) el('sumCal').textContent = Math.round(tc);
  if(el('sumPro')) el('sumPro').textContent = Math.round(tp)+'g';
  if(el('sumCarb')) el('sumCarb').textContent = Math.round(tca)+'g';
  if(el('sumFat')) el('sumFat').textContent = Math.round(tf)+'g';
}

// ── 결과 렌더링 ──
function renderResult(data, isAfterMeal) {
  const foods = data.foods || [];
  const summary = data.meal_summary || {};
  sharingPcts = {};
  sharingMode = false;
  sharingPeople = 1;

  let cardsHtml = '';
  foods.forEach((food, i) => {
    sharingPcts[i] = 100;
    const isDb = food.db_matched || food.source === 'DB_MATCHED';
    const conf = food.confidence || 0;
    const confPct = Math.round(conf * 100);
    const confColor = conf >= 0.8 ? '#6ee7b7' : conf >= 0.5 ? '#fbbf24' : '#f87171';

    let eatenBarHtml = '';
    if (isAfterMeal && food.eaten_pct !== undefined) {
      const ep = food.eaten_pct;
      eatenBarHtml = '<div style="font-size:0.8em; color:#6ee7b7; margin-bottom:4px">섭취량: '+ep+'% '+(food.leftover_note ? '· '+food.leftover_note : '')+'</div><div class="eaten-bar"><div class="eaten-fill" style="width:'+ep+'%"></div></div>';
    }

    // 나눠먹기 프리셋 (인원수 기반 + 고정값)
    const presetBtns = [25, 33, 50, 75, 100].map(v =>
      '<button class="preset-btn" data-idx="'+i+'" data-val="'+v+'" onclick="setSharingPct('+i+','+v+')">'+v+'%</button>'
    ).join('');

    const sharingHtml = '<div class="food-sharing" id="sharing_container_'+i+'" style="display:none">'
      + '<div class="sharing-header">'
      + '<span class="sharing-label">내가 먹은 비율</span>'
      + '<span class="sharing-pct" id="share_pct_'+i+'">100%</span>'
      + '</div>'
      + '<input type="range" class="sharing-slider" id="share_slider_'+i+'" min="0" max="100" value="100" '
      + 'oninput="setSharingPct('+i+', this.value)">'
      + '<div class="sharing-presets">'+presetBtns+'</div>'
      + '<div class="sharing-summary"><span class="sharing-summary-text" id="share_summary_'+i+'"></span></div>'
      + '</div>';

    cardsHtml += '<div class="food-card">'
      + '<div class="food-title"><div><span class="food-name">'+(food.name_ko||'?')+'</span>'
      + '<span class="food-name-en">'+(food.name_en||'')+'</span></div>'
      + '<div><button class="edit-btn" onclick="openEditModal('+i+')">✏️ 수정</button>'
      + '<button class="report-btn" id="report_btn_'+i+'" onclick="openReportModal('+i+')">⚠️ 이상해요</button> '
      + '<span class="source-badge '+(isDb?'source-db':'source-ai')+'">'+(isDb?'DB 검증':'AI 추정')+'</span></div></div>'
      + '<div class="confidence-bar"><div class="confidence-fill" style="width:'+confPct+'%;background:'+confColor+'"></div></div>'
      + '<div style="font-size:0.8em;color:#888;margin-bottom:10px;margin-top:-8px">확신도 '+confPct+'% · 약 '+(food.estimated_serving_g||'?')+'g</div>'
      + eatenBarHtml
      + '<div class="nutrition-grid">'
      + '<div class="nutrition-item"><div class="nutrition-value">'+(food.calories_kcal||0)+'</div><div class="nutrition-label">칼로리 kcal</div></div>'
      + '<div class="nutrition-item"><div class="nutrition-value" style="color:#60a5fa">'+(food.protein_g||0)+'g</div><div class="nutrition-label">단백질</div></div>'
      + '<div class="nutrition-item"><div class="nutrition-value" style="color:#fbbf24">'+(food.carbs_g||0)+'g</div><div class="nutrition-label">탄수화물</div></div>'
      + '<div class="nutrition-item"><div class="nutrition-value" style="color:#f87171">'+(food.fat_g||0)+'g</div><div class="nutrition-label">지방</div></div>'
      + '</div>'
      + sharingHtml
      + '</div>';
  });
  document.getElementById('foodCards').innerHTML = cardsHtml;

  // 요약 (나눠먹기 버튼 포함)
  const score = summary.health_score || 0;
  const scoreColor = score >= 7 ? '#6ee7b7' : score >= 5 ? '#fbbf24' : '#f87171';
  document.getElementById('resultTitle').textContent = isAfterMeal ? '실제 섭취 결과' : '분석 결과';
  document.getElementById('summaryCard').innerHTML = '<div class="summary-card">'
    + '<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:16px">'
    + '<div class="summary-title" style="margin-bottom:0">'+(isAfterMeal?'실제 섭취 요약':'식사 영양 정보')+'</div>'
    + '<button class="btn btn-secondary" id="sharingToggleBtn" onclick="openSharingPopup()" style="padding:6px 14px; font-size:0.82em; border-radius:20px">👥 나눠먹기</button>'
    + '</div>'
    + '<div class="summary-stats">'
    + '<div class="stat-item"><div class="stat-value" id="sumCal">'+(summary.total_calories||0)+'</div><div class="stat-label">총 칼로리 (kcal)</div></div>'
    + '<div class="stat-item"><div class="stat-value" style="color:#60a5fa" id="sumPro">'+(summary.total_protein||0)+'g</div><div class="stat-label">단백질</div></div>'
    + '<div class="stat-item"><div class="stat-value" style="color:#fbbf24" id="sumCarb">'+(summary.total_carbs||0)+'g</div><div class="stat-label">탄수화물</div></div>'
    + '<div class="stat-item"><div class="stat-value" style="color:#f87171" id="sumFat">'+(summary.total_fat||0)+'g</div><div class="stat-label">지방</div></div>'
    + '</div>'
    + '<div class="health-score"><div class="score-circle" style="background:'+scoreColor+'33;border:2px solid '+scoreColor+'"><span style="color:'+scoreColor+'">'+score+'</span></div>'
    + '<div><div style="font-weight:600;margin-bottom:4px">건강 점수 '+score+'/10</div>'
    + '<div style="font-size:0.85em;color:#888">'+(summary.meal_type?summary.meal_type+' 식사':'')+'</div></div></div>'
    + '<div class="comment">'+(summary.one_line_comment||'')+'</div></div>';

  // 세션 모드면 afterMealBanner 대신 "다음 사진 추가" 버튼 표시
  if (sessionActive) {
    document.getElementById('afterMealBanner').style.display = 'none';
    // 세션 모드 전용 "다음 사진 추가" 버튼
    let sessionNextBtn = document.getElementById('sessionNextPhotoArea');
    if (!sessionNextBtn) {
      sessionNextBtn = document.createElement('div');
      sessionNextBtn.id = 'sessionNextPhotoArea';
      sessionNextBtn.style.cssText = 'text-align:center; margin-top:16px; padding:20px; background:linear-gradient(135deg,#1a2e1a,#1e3a1e); border:1px solid #059669; border-radius:12px;';
      sessionNextBtn.innerHTML = '<div style="color:#6ee7b7; font-weight:600; margin-bottom:8px">이 사진의 음식이 추가되었습니다!</div>'
        + '<button class="btn btn-green" onclick="addNextPhoto()" style="padding:10px 24px; font-size:0.95em">📸 다음 사진 추가</button>'
        + '<div style="color:#888; font-size:0.8em; margin-top:8px">더 찍을 음식이 없으면 위 세션바에서 "식사 끝"을 눌러주세요</div>';
      document.getElementById('resultSection').appendChild(sessionNextBtn);
    }
    sessionNextBtn.style.display = 'block';
  } else {
    // 일반 모드: 식후 배너
    document.getElementById('afterMealBanner').style.display = isAfterMeal ? 'none' : 'block';
    const sessionNextBtn = document.getElementById('sessionNextPhotoArea');
    if (sessionNextBtn) sessionNextBtn.style.display = 'none';
  }
  document.getElementById('afterUploadSection').style.display = 'none';
  document.getElementById('manualSection').style.display = 'none';

  // 수동 저장 버튼 (세션 모드가 아닐 때만)
  const saveArea = document.getElementById('saveMealArea');
  if (saveArea) {
    if (sessionActive) {
      saveArea.innerHTML = '';
    } else if (mealSaved) {
      saveArea.innerHTML = '<div class="save-meal-area"><button class="save-meal-btn saved" id="saveMealBtn" disabled>✅ 저장 완료!</button><div id="saveMealHint" style="color:#888;font-size:0.8em;margin-top:8px">이 식사가 기록에 저장되었습니다</div></div>';
    } else {
      saveArea.innerHTML = '<div class="save-meal-area"><div style="color:#6ee7b7;font-size:0.85em;margin-bottom:10px">식사를 마쳤으면 저장해주세요</div><button class="save-meal-btn" id="saveMealBtn" onclick="saveManually(false)">💾 식사 기록 저장</button><div id="saveMealHint" style="color:#555;font-size:0.75em;margin-top:8px">저장하지 않으면 1시간 후 자동 저장됩니다</div></div>';
    }
  }

  document.getElementById('resultSection').style.display = 'block';

  // Feature 1: 오늘의 현황 로드 (분석 후)
  loadTodayProgress();
}
</script>
</body>
</html>"""


class NutriLensHandler(BaseHTTPRequestHandler):
    """HTTP 요청 핸들러"""

    def do_GET(self):
        """메인 페이지 서빙"""
        path = self.path.split('?')[0]  # 쿼리 파라미터 제거
        if path == '/' or path == '/index.html':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode('utf-8'))
        elif path == '/version':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps({"version": "4.0", "json_mode": True, "db_count": len(FOODS_DB)}, ensure_ascii=False).encode('utf-8'))
        elif path.startswith('/dashboard-data'):
            self._handle_dashboard_data()
        else:
            self.send_response(404)
            self.end_headers()

    def _parse_multipart(self):
        """multipart form data 파싱 — 파일 여러개 지원"""
        content_type = self.headers.get('Content-Type', '')
        if 'multipart/form-data' not in content_type:
            return None, None, "multipart/form-data 필요"

        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)

        boundary_match = re.search(r'boundary=([^\s;]+)', content_type)
        if not boundary_match:
            return None, None, "boundary를 찾을 수 없습니다."
        boundary = boundary_match.group(1).encode()

        parts = body.split(b'--' + boundary)
        fields = {}
        files = {}

        for part in parts:
            if b'Content-Disposition' not in part:
                continue
            header_end = part.find(b'\r\n\r\n')
            if header_end == -1:
                continue
            header_section = part[:header_end].decode('utf-8', errors='replace')
            body_section = part[header_end + 4:]
            if body_section.endswith(b'\r\n'):
                body_section = body_section[:-2]

            name_match = re.search(r'name="([^"]+)"', header_section)
            if not name_match:
                continue
            field_name = name_match.group(1)

            filename_match = re.search(r'filename="([^"]*)"', header_section)
            if filename_match and len(body_section) > 0:
                files[field_name] = {
                    'data': body_section,
                    'filename': filename_match.group(1) or 'image.jpg'
                }
            else:
                fields[field_name] = body_section.decode('utf-8', errors='replace')

        return fields, files, None

    def _get_api_key(self, fields):
        """API 키 추출"""
        api_key = (fields or {}).get('api_key', '').strip()
        if not api_key:
            api_key = os.environ.get('OPENAI_API_KEY', '')
        return api_key

    def _image_to_base64(self, file_info):
        """파일 정보 → base64 + media_type"""
        ext = Path(file_info['filename']).suffix.lower()
        media_type = {
            '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
            '.png': 'image/png', '.gif': 'image/gif',
            '.webp': 'image/webp',
        }.get(ext, 'image/jpeg')
        b64 = base64.b64encode(file_info['data']).decode('utf-8')
        return b64, media_type

    def do_POST(self):
        """음식 사진 분석 API"""
        path = self.path.split('?')[0]
        if path == '/analyze':
            self._handle_analyze()
        elif path == '/analyze-leftover':
            self._handle_leftover()
        elif path == '/session/start':
            self._handle_session_start()
        elif path == '/session/end':
            self._handle_session_end()
        elif path == '/correct':
            self._handle_correct()
        elif path == '/save-meal':
            self._handle_save_meal()
        elif path == '/login':
            self._handle_login()
        elif path == '/report':
            self._handle_report()
        elif path == '/save-goal':
            self._handle_save_goal()
        elif path == '/today-progress':
            self._handle_today_progress()
        elif path == '/weekly-macro':
            self._handle_weekly_macro()
        elif path == '/protein-timing':
            self._handle_protein_timing()
        else:
            self.send_response(404)
            self.end_headers()
            return

    def _handle_leftover(self):
        """전/후 사진 비교 — 남은 음식 분석"""
        try:
            fields, files, err = self._parse_multipart()
            if err:
                self._json_response(400, {"error": err})
                return

            api_key = self._get_api_key(fields)
            if not api_key:
                self._json_response(400, {"error": "API 키가 없습니다."})
                return

            before_file = files.get('before')
            after_file = files.get('after')

            if not before_file or not after_file:
                self._json_response(400, {"error": "전/후 사진 2장이 필요합니다."})
                return

            before_b64, before_mt = self._image_to_base64(before_file)
            after_b64, after_mt = self._image_to_base64(after_file)

            print(f"\n남은 음식 분석 요청")
            print(f"  전: {before_file['filename']} ({len(before_file['data'])/1024:.0f}KB)")
            print(f"  후: {after_file['filename']} ({len(after_file['data'])/1024:.0f}KB)")

            # 전/후 비교 전용 프롬프트
            leftover_prompt = LEFTOVER_PROMPT

            url = "https://api.openai.com/v1/chat/completions"
            payload = {
                "model": "gpt-4o",
                "messages": [
                    {"role": "system", "content": leftover_prompt},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "첫 번째 사진은 먹기 전, 두 번째 사진은 먹은 후입니다. 실제로 먹은 양을 분석해주세요."},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:{before_mt};base64,{before_b64}", "detail": "high"}
                            },
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:{after_mt};base64,{after_b64}", "detail": "high"}
                            }
                        ]
                    }
                ],
                "max_tokens": 2000,
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
            }

            data = json.dumps(payload).encode('utf-8')
            req = urllib.request.Request(url, data=data, method='POST')
            req.add_header("Authorization", f"Bearer {api_key}")
            req.add_header("Content-Type", "application/json")

            try:
                with urllib.request.urlopen(req, timeout=90) as resp:
                    result = json.loads(resp.read().decode('utf-8'))
            except urllib.error.HTTPError as e:
                body = e.read().decode('utf-8', errors='replace')
                self._json_response(200, {"error": f"OpenAI API 에러 ({e.code}): {body}"})
                return
            except Exception as e:
                self._json_response(200, {"error": f"API 호출 실패: {str(e)}"})
                return

            if "error" in result:
                self._json_response(200, {"error": f"OpenAI: {result['error'].get('message', str(result['error']))}"})
                return

            content = result["choices"][0]["message"]["content"]
            print(f"  AI 응답 길이: {len(content)}자")
            print(f"  AI 응답 앞부분: {content[:200]}")
            analysis = _parse_ai_json(content)
            if analysis is None:
                print(f"  [ERROR] 파싱 실패! 전체 응답:\n{content}")
                self._json_response(200, {"error": "AI 응답 파싱 실패", "raw": content[:500]})
                return

            # eaten_pct 기반 영양소 강제 재계산 (AI 실수 방지)
            if "foods" in analysis:
                for food in analysis["foods"]:
                    pct = food.get("eaten_pct")
                    if pct is not None and isinstance(pct, (int, float)):
                        ratio = pct / 100.0
                        orig_g = food.get("original_serving_g", 0) or 0
                        food["estimated_serving_g"] = round(orig_g * ratio)
                        # original 값이 있으면 그걸로 재계산, 없으면 현재값을 original로 간주
                        for key in ["calories_kcal", "protein_g", "carbs_g", "fat_g", "fiber_g", "sodium_mg", "sugar_g"]:
                            val = food.get(key, 0) or 0
                            if pct == 0:
                                food[key] = 0
                            # pct가 100이 아닌데 값이 원본 그대로인 경우 재계산
                            elif pct < 100 and val > 0:
                                food[key] = round(val * ratio, 1)

                # meal_summary도 재계산
                foods = analysis["foods"]
                summary = analysis.get("meal_summary", {})
                summary["total_calories"] = round(sum(f.get("calories_kcal", 0) or 0 for f in foods))
                summary["total_protein"] = round(sum(f.get("protein_g", 0) or 0 for f in foods))
                summary["total_carbs"] = round(sum(f.get("carbs_g", 0) or 0 for f in foods))
                summary["total_fat"] = round(sum(f.get("fat_g", 0) or 0 for f in foods))
                analysis["meal_summary"] = summary

            # DB 매칭
            if FOODS_DB and "foods" in analysis:
                analysis = match_with_db(analysis, FOODS_DB)

            print("남은 음식 분석 완료!")
            self._json_response(200, analysis)

        except Exception as e:
            import traceback
            traceback.print_exc()
            self._json_response(500, {"error": f"서버 에러: {str(e)}"})

    def _handle_analyze(self):
        """일반 음식 분석"""

        try:
            fields, files, err = self._parse_multipart()
            if err:
                self._json_response(400, {"error": err})
                return

            api_key = self._get_api_key(fields)
            if not api_key:
                self._json_response(400, {
                    "error": "API 키가 없습니다.",
                    "help": "OpenAI API 키를 입력하거나 .env 파일에 OPENAI_API_KEY를 설정하세요."
                })
                return

            image_file = files.get('image')
            if not image_file:
                self._json_response(400, {"error": "이미지가 업로드되지 않았습니다."})
                return

            base64_image, media_type = self._image_to_base64(image_file)
            filename = image_file['filename']
            image_data = image_file['data']

            print(f"\n분석 요청: {filename} ({len(image_data)/1024:.0f}KB)")
            print("GPT-4o Vision API 호출 중...")

            # 1. AI 분석
            analysis = call_openai_vision(base64_image, media_type, api_key)

            if "error" in analysis:
                print(f"에러: {analysis['error']}")
                self._json_response(200, analysis)
                return

            # 2. DB 매칭
            if FOODS_DB:
                analysis = match_with_db(analysis, FOODS_DB)
                matched = sum(1 for f in analysis.get('foods', []) if f.get('db_matched'))
                total = len(analysis.get('foods', []))
                print(f"DB 매칭: {matched}/{total}개 음식 매칭됨")

            # 3. 세션 모드: session_id가 있으면 세션에 누적
            session_id = (fields or {}).get('session_id', '').strip()
            if session_id and session_id in MEAL_SESSIONS:
                session = MEAL_SESSIONS[session_id]
                new_foods = analysis.get('foods', [])
                session['foods'].extend(new_foods)
                session['photo_count'] += 1

                # 누적 합산 계산
                all_foods = session['foods']
                total_cal = sum(f.get('calories_kcal', 0) for f in all_foods)
                total_prot = sum(f.get('protein_g', 0) for f in all_foods)
                total_carbs = sum(f.get('carbs_g', 0) for f in all_foods)
                total_fat = sum(f.get('fat_g', 0) for f in all_foods)

                analysis['session_id'] = session_id
                analysis['session_photo_count'] = session['photo_count']
                analysis['session_total_foods'] = len(all_foods)
                analysis['session_summary'] = {
                    "total_calories": round(total_cal, 1),
                    "total_protein": round(total_prot, 1),
                    "total_carbs": round(total_carbs, 1),
                    "total_fat": round(total_fat, 1)
                }
                print(f"[세션] {session_id}: 사진 {session['photo_count']}장, 총 {len(all_foods)}개 음식, {total_cal:.0f}kcal")

            # 4. 결과 반환
            print("분석 완료!")
            self._json_response(200, analysis)

        except Exception as e:
            import traceback
            traceback.print_exc()
            self._json_response(500, {"error": f"서버 에러: {str(e)}"})

    # ── 식사 세션 API ──
    def _handle_session_start(self):
        """식사 세션 시작 — 빈 세션 생성"""
        _cleanup_sessions()
        session_id = str(uuid.uuid4())[:8]
        MEAL_SESSIONS[session_id] = {
            'foods': [],
            'created': time.time(),
            'photo_count': 0
        }
        print(f"[세션] 새 식사 세션 시작: {session_id}")
        self._json_response(200, {"session_id": session_id})

    def _handle_session_end(self):
        """식사 세션 종료 — 누적 결과 반환 후 세션 삭제"""
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8') if content_length > 0 else '{}'
            req = json.loads(body) if body.strip() else {}
            session_id = req.get('session_id', '')

            if session_id not in MEAL_SESSIONS:
                self._json_response(404, {"error": "세션을 찾을 수 없습니다."})
                return

            session = MEAL_SESSIONS[session_id]
            foods = session['foods']

            # 합산 계산
            total_cal = sum(f.get('calories_kcal', 0) for f in foods)
            total_prot = sum(f.get('protein_g', 0) for f in foods)
            total_carbs = sum(f.get('carbs_g', 0) for f in foods)
            total_fat = sum(f.get('fat_g', 0) for f in foods)

            result = {
                "session_id": session_id,
                "photo_count": session['photo_count'],
                "foods": foods,
                "meal_summary": {
                    "total_calories": round(total_cal, 1),
                    "total_protein": round(total_prot, 1),
                    "total_carbs": round(total_carbs, 1),
                    "total_fat": round(total_fat, 1),
                    "meal_type": "정찬",
                    "health_score": 7,
                    "one_line_comment": f"총 {session['photo_count']}장의 사진에서 {len(foods)}개 음식을 분석했습니다."
                }
            }

            del MEAL_SESSIONS[session_id]
            print(f"[세션] 세션 종료: {session_id} → {len(foods)}개 음식, {total_cal:.0f}kcal")
            self._json_response(200, result)

        except Exception as e:
            self._json_response(500, {"error": f"세션 종료 에러: {str(e)}"})

    # ── 음식 수정 API ──
    def _handle_correct(self):
        """음식명/양 수정 — DB 재매칭 + 피드백 기록"""
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            req = json.loads(body)

            original_name = req.get('original_name', '')
            corrected_name = req.get('corrected_name', '').strip()
            serving_pct = req.get('serving_pct', 100)  # 양 비율 (50 = 반 그릇)

            if not corrected_name:
                self._json_response(400, {"error": "수정할 음식명을 입력하세요."})
                return

            # 1. 피드백 기록
            correction_count = _record_correction(original_name, corrected_name)
            print(f"[수정] '{original_name}' → '{corrected_name}' (피드백 {correction_count}회)")

            # 2. 골드 테이블/DB에서 새 음식 검색
            from food_analyzer import _search_gold, _search_core_foods
            gold_key, gold_data = _search_gold(corrected_name)

            if gold_data:
                # 골드 테이블 매칭 성공
                serving_ratio = (serving_pct / 100)
                result = {
                    "matched": True,
                    "name_ko": corrected_name,
                    "db_name": gold_key,
                    "source": gold_data.get('source', 'GOLD_DB'),
                    "calories_kcal": round(gold_data['cal'] * serving_ratio, 1),
                    "protein_g": round(gold_data['prot'] * serving_ratio, 1),
                    "carbs_g": round(gold_data['carbs'] * serving_ratio, 1),
                    "fat_g": round(gold_data['fat'] * serving_ratio, 1),
                    "fiber_g": round(gold_data.get('fiber', 0) * serving_ratio, 1),
                    "sodium_mg": round(gold_data.get('sodium', 0) * serving_ratio, 1),
                    "sugar_g": round(gold_data.get('sugar', 0) * serving_ratio, 1),
                    "estimated_serving_g": round(gold_data.get('serving', 100) * serving_ratio),
                    "serving_pct": serving_pct,
                    "correction_count": correction_count
                }
            else:
                # DB에 없음 — AI 값 유지하되 양만 조절
                result = {
                    "matched": False,
                    "name_ko": corrected_name,
                    "source": "AI_ESTIMATED",
                    "serving_pct": serving_pct,
                    "correction_count": correction_count,
                    "message": "DB에 없는 음식이라 영양정보는 기존 AI 추정치를 사용합니다."
                }

            self._json_response(200, result)

        except Exception as e:
            import traceback
            traceback.print_exc()
            self._json_response(500, {"error": f"수정 에러: {str(e)}"})

    # ── 로그인 API ──
    def _handle_login(self):
        """닉네임 로그인 — 사용자 등록/확인"""
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            req = json.loads(body)
            nickname = req.get('nickname', '').strip()
            if not nickname or len(nickname) < 1:
                self._json_response(400, {"error": "닉네임을 입력하세요."})
                return
            log = _load_meal_log()
            is_new = nickname not in log.get("users", {})
            if is_new:
                log.setdefault("users", {})[nickname] = {"created": time.strftime("%Y-%m-%d"), "meals": []}
                _save_meal_log(log)
            meal_count = len(log["users"][nickname].get("meals", []))
            print(f"[로그인] {nickname} ({'신규' if is_new else '기존'}, 기록 {meal_count}건)")
            self._json_response(200, {"nickname": nickname, "is_new": is_new, "meal_count": meal_count})
        except Exception as e:
            self._json_response(500, {"error": f"로그인 에러: {str(e)}"})

    # ── 식사 기록 저장 API ──
    def _handle_save_meal(self):
        """분석 결과를 식사 기록으로 저장"""
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            req = json.loads(body)
            nickname = req.get('nickname', '').strip()
            meal_data = req.get('meal_data', {})
            if not nickname:
                self._json_response(400, {"error": "닉네임이 필요합니다."})
                return
            if not meal_data.get('foods'):
                self._json_response(400, {"error": "저장할 음식 데이터가 없습니다."})
                return
            record_id = _save_meal_record(nickname, meal_data)
            self._json_response(200, {"saved": True, "record_id": record_id})
        except Exception as e:
            self._json_response(500, {"error": f"저장 에러: {str(e)}"})

    # ── 대시보드 데이터 API ──
    def _handle_report(self):
        """이상해요 신고 접수"""
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            req = json.loads(body)
            nickname = req.get('nickname', '').strip()
            food_name = req.get('food_name', '?')
            report_text = req.get('report_text', '').strip()
            food_data = req.get('food_data', {})

            if not report_text:
                self._json_response(400, {"error": "신고 내용이 필요합니다."})
                return

            # 신고 로그 저장
            report_path = REPORT_LOG_PATH
            reports = []
            if report_path and report_path.exists():
                try:
                    with open(report_path, 'r', encoding='utf-8') as f:
                        reports = json.load(f)
                except:
                    reports = []

            reports.append({
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "nickname": nickname,
                "food_name": food_name,
                "food_data": food_data,
                "report_text": report_text
            })

            if report_path:
                with open(report_path, 'w', encoding='utf-8') as f:
                    json.dump(reports, f, ensure_ascii=False, indent=1)

            print(f"[신고] {nickname}: {food_name} — {report_text}")

            # 구글시트 웹훅 전송 (비동기 — 실패해도 로컬 저장은 유지)
            _send_report_to_sheets(nickname, food_name, food_data, report_text)

            self._json_response(200, {"reported": True, "total_reports": len(reports)})
        except Exception as e:
            self._json_response(500, {"error": f"신고 에러: {str(e)}"})

    # ── Feature 1: 목표 저장 API ──
    def _handle_save_goal(self):
        """일일 목표 저장"""
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            req = json.loads(body)
            nickname = req.get('nickname', '').strip()
            calories = int(req.get('calories', 1800))
            protein = int(req.get('protein', 120))
            if not nickname:
                self._json_response(400, {"error": "닉네임이 필요합니다."})
                return
            _save_user_goals(nickname, calories, protein)
            self._json_response(200, {"saved": True, "calories": calories, "protein": protein})
        except Exception as e:
            self._json_response(500, {"error": f"목표 저장 에러: {str(e)}"})

    # ── Feature 1: 오늘 진행 상황 API ──
    def _handle_today_progress(self):
        """오늘의 칼로리·단백질 진행 상황"""
        try:
            qs = parse_qs(self.path.split('?', 1)[1]) if '?' in self.path else {}
            nickname = qs.get('user', [''])[0]
            if not nickname:
                self._json_response(400, {"error": "사용자명이 필요합니다."})
                return
            data = _get_today_progress(nickname)
            self._json_response(200, data)
        except Exception as e:
            self._json_response(500, {"error": f"진행 상황 조회 에러: {str(e)}"})

    # ── Feature 2: 주간 매크로 분석 API ──
    def _handle_weekly_macro(self):
        """주간 매크로 비율 분석"""
        try:
            qs = parse_qs(self.path.split('?', 1)[1]) if '?' in self.path else {}
            nickname = qs.get('user', [''])[0]
            if not nickname:
                self._json_response(400, {"error": "사용자명이 필요합니다."})
                return
            data = _get_weekly_macro_analysis(nickname)
            self._json_response(200, data)
        except Exception as e:
            self._json_response(500, {"error": f"주간 매크로 분석 에러: {str(e)}"})

    # ── Feature 3: 단백질 타이밍 분석 API ──
    def _handle_protein_timing(self):
        """단백질 섭취 타이밍 분석"""
        try:
            qs = parse_qs(self.path.split('?', 1)[1]) if '?' in self.path else {}
            nickname = qs.get('user', [''])[0]
            if not nickname:
                self._json_response(400, {"error": "사용자명이 필요합니다."})
                return
            data = _get_protein_timing_analysis(nickname)
            self._json_response(200, data)
        except Exception as e:
            self._json_response(500, {"error": f"단백질 타이밍 분석 에러: {str(e)}"})

    # ── 대시보드 데이터 API ──
    def _handle_dashboard_data(self):
        """대시보드용 집계 데이터 반환"""
        try:
            qs = parse_qs(self.path.split('?', 1)[1]) if '?' in self.path else {}
            nickname = qs.get('user', [''])[0]
            days = int(qs.get('days', ['30'])[0])
            if not nickname:
                self._json_response(400, {"error": "사용자명이 필요합니다."})
                return
            data = _get_dashboard_data(nickname, days)
            self._json_response(200, data)
        except Exception as e:
            self._json_response(500, {"error": f"대시보드 에러: {str(e)}"})

    def _json_response(self, code, data):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))

    def log_message(self, format, *args):
        """로그를 간결하게"""
        if '/analyze' in str(args):
            print(f"[서버] {args[0]}")


# ── 서버 실행 ──
if __name__ == '__main__':
    PORT = int(os.environ.get('PORT', 8080))
    print()
    print("=" * 50)
    print("  NutriLens 테스트 서버")
    print("=" * 50)
    print(f"  DB: {len(FOODS_DB)}종 음식 로드됨")
    print(f"  주소: http://localhost:{PORT}")
    print(f"  종료: Ctrl+C")
    print("=" * 50)
    print()

    try:
        server = HTTPServer(('0.0.0.0', PORT), NutriLensHandler)
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n서버 종료.")
        server.server_close()
