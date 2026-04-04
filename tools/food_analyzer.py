#!/usr/bin/env python3
"""
NutriLens AI 음식 분석 엔진 (2단계 핵심 도구)
─────────────────────────────────────────────
사진 한 장 → AI가 음식 인식 → DB에서 영양소 매칭 → 결과 반환

사용법:
  python food_analyzer.py --image 사진경로.jpg
  python food_analyzer.py --image 사진경로.jpg --api-key YOUR_OPENAI_KEY

환경변수:
  OPENAI_API_KEY: OpenAI API 키 (.env 파일에 저장)
"""

import os
import sys
import json
import base64
import argparse
from pathlib import Path

# ── .env 파일 로드 ──
def load_env():
    env_paths = [
        Path(__file__).parent.parent / '.env',
        Path.cwd() / '.env',
    ]
    for env_path in env_paths:
        if env_path.exists():
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, val = line.split('=', 1)
                        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))
            break

load_env()

# ── 음식 DB 로드 ──
# DB 연결을 전역으로 유지 (SQLite 검색용)
_DB_CONN = None
_DB_PATH = None

def load_food_db(db_path=None):
    """SQLite DB 연결을 열고 아이템 수만 반환 (전체를 메모리에 올리지 않음)"""
    global _DB_CONN, _DB_PATH
    import sqlite3

    if db_path is None:
        sqlite_path = Path(__file__).parent.parent / 'nutrilens_db.sqlite'
        xlsx_path = Path(__file__).parent.parent / 'NutriLens_음식DB.xlsx'

        if sqlite_path.exists():
            db_path = sqlite_path
        elif xlsx_path.exists():
            return _load_xlsx(xlsx_path)
        else:
            print("DB 파일을 찾을 수 없습니다.")
            return []

    p = Path(db_path)
    if p.suffix != '.sqlite':
        return _load_xlsx(p)

    # SQLite: 연결만 열고 전체 로드 안 함
    _DB_CONN = sqlite3.connect(str(p))
    _DB_CONN.row_factory = sqlite3.Row
    _DB_PATH = str(p)

    cur = _DB_CONN.cursor()
    cur.execute("SELECT COUNT(*) FROM foods")
    count = cur.fetchone()[0]

    # 호환성: 기존 코드에서 len(FOODS_DB) 쓰므로 count를 가진 리스트처럼 보이는 객체 반환
    return _FoodDBProxy(count)


class _FoodDBProxy:
    """len()과 bool() 호환용 프록시 — 실제 데이터는 SQLite에서 검색"""
    def __init__(self, count):
        self._count = count
    def __len__(self):
        return self._count
    def __bool__(self):
        return self._count > 0


def _is_relevant_match(query, db_name):
    """부분 매칭 결과가 실제로 관련 있는지 판단
    예: "귤" → "레귤러 피자"는 관련 없음 (False)
    예: "김치찌개" → "김치찌개(돼지고기)"는 관련 있음 (True)
    """
    query = query.strip()
    db_name = db_name.strip()

    # DB 이름이 query로 시작하면 → 관련 있음 (예: "귤" → "귤차")
    if db_name.startswith(query):
        return True

    # DB 이름에 query가 독립적 단어로 포함되는지 확인
    # 구분자: 공백, _, (, ), /, 등
    import re
    # query 앞뒤가 단어 경계이거나 구분자인 경우만 허용
    # 예: "귤"이 "레귤러"에 포함 → 앞에 "레"가 붙어있으므로 False
    # 예: "귤"이 "귤차"에 포함 → 귤로 시작하므로 True (위에서 이미 처리)
    # 예: "김치"가 "김치찌개"에 포함 → 김치로 시작하므로 True
    idx = db_name.find(query)
    if idx < 0:
        return False

    # query 앞의 문자가 구분자이면 OK
    if idx > 0:
        prev_char = db_name[idx - 1]
        if prev_char not in ' _(/·\t':
            return False

    return True


def search_food_db(name):
    """SQLite에서 음식 이름으로 빠르게 검색 (인덱스 활용)"""
    global _DB_CONN
    if _DB_CONN is None:
        return None, []

    cur = _DB_CONN.cursor()

    # 1) 정확 매칭 (인덱스 사용 → 즉시)
    cur.execute("SELECT * FROM foods WHERE name_ko = ? LIMIT 1", (name,))
    row = cur.fetchone()
    if row:
        return dict(row), []

    # 2) 부분 매칭 (LIKE) + 관련성 필터
    cur.execute("SELECT * FROM foods WHERE name_ko LIKE ? LIMIT 20", (f"%{name}%",))
    raw_partials = [dict(r) for r in cur.fetchall()]
    # 엉뚱한 매칭 걸러내기 (예: "귤" → "레귤러 피자" 제거)
    partials = [r for r in raw_partials if _is_relevant_match(name, r.get('name_ko', ''))]
    if partials:
        return None, partials[:5]

    # 3) 역방향 부분 매칭: DB 이름이 AI 이름에 포함되는 경우
    # 예: AI가 "김치찌개" → DB에 "김치" 매칭
    if len(name) >= 3:  # 최소 3글자 이상일 때만 역방향 매칭
        cur.execute("SELECT * FROM foods WHERE ? LIKE '%' || name_ko || '%' AND length(name_ko) >= 2 LIMIT 5", (name,))
        reverse_partials = [dict(r) for r in cur.fetchall()]
        if reverse_partials:
            return None, reverse_partials

    return None, []


def _load_sqlite(db_path):
    """SQLite에서 전체 로드 — 하위 호환용 (accuracy_test 등)"""
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM foods")
    foods = [dict(row) for row in cur.fetchall()]
    conn.close()
    return foods


def _load_xlsx(db_path):
    """엑셀에서 로드 — SQLite 없을 때 fallback"""
    try:
        import openpyxl
    except ImportError:
        print("openpyxl 필요: pip install openpyxl --break-system-packages")
        sys.exit(1)

    if not Path(db_path).exists():
        print(f"DB 파일을 찾을 수 없습니다: {db_path}")
        return []

    wb = openpyxl.load_workbook(db_path, read_only=True, data_only=True)
    ws = wb['음식DB_전체']

    headers = [cell.value for cell in next(ws.iter_rows(min_row=1, max_row=1))]
    foods = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row[0]:
            food = dict(zip(headers, row))
            foods.append(food)
    wb.close()
    return foods


def build_food_list_for_prompt(foods):
    """DB 음식 목록을 프롬프트에 넣을 간결한 텍스트로 변환"""
    categories = {}
    for f in foods:
        cat = f.get('category', 'other')
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(f['name_ko'])

    lines = []
    cat_names = {
        'korean': '한식',
        'diet_fitness': '다이어트/피트니스',
        'foreign_popular': '외국음식',
        'franchise': '프랜차이즈',
        'snack_drink': '간식/음료',
    }
    for cat, names in categories.items():
        label = cat_names.get(cat, cat)
        lines.append(f"[{label}] {', '.join(names[:50])}")  # 카테고리당 최대 50개
    return '\n'.join(lines)


# ── AI 분석 프롬프트 ──
SYSTEM_PROMPT = """당신은 NutriLens의 AI 음식 영양 분석 전문가입니다.

## 역할
사진에 보이는 모든 음식을 정확하게 식별하고, 각 음식의 영양 성분을 분석합니다.

## 규칙
1. 사진에 보이는 음식을 **모두** 개별적으로 식별하세요 (메인, 사이드, 음료 포함)
2. 각 음식의 **예상 양(g)**을 추정하세요 (접시/그릇 크기 참고)
3. 영양소는 추정 양 기준으로 계산하세요
4. 한국 음식이면 category를 "korean", 다이어트 식단이면 "diet_fitness", 외국 음식이면 "foreign_popular", 프랜차이즈면 "franchise", 간식/음료면 "snack_drink"으로 분류
5. confidence는 0.0~1.0 (1.0 = 100% 확신)
6. 프랜차이즈 메뉴가 보이면 brand 필드에 브랜드명 포함
7. 반드시 아래 JSON 형식으로만 답변하세요. 다른 텍스트 없이 JSON만 출력하세요.

## 응답 형식
```json
{
  "foods": [
    {
      "name_ko": "음식 한국어 이름",
      "name_en": "English Name",
      "category": "korean",
      "subcategory": "밥류",
      "estimated_serving_g": 300,
      "calories_kcal": 450,
      "protein_g": 15,
      "carbs_g": 65,
      "fat_g": 12,
      "fiber_g": 2,
      "sodium_mg": 800,
      "sugar_g": 3,
      "confidence": 0.92,
      "brand": "",
      "cooking_method": "볶음",
      "tags": "한끼,매운맛"
    }
  ],
  "meal_summary": {
    "total_calories": 450,
    "total_protein": 15,
    "total_carbs": 65,
    "total_fat": 12,
    "meal_type": "점심",
    "health_score": 7,
    "one_line_comment": "균형 잡힌 한끼입니다. 식이섬유를 위해 채소를 추가하면 더 좋아요."
  }
}
```

## health_score 기준 (1~10)
- 9~10: 영양 균형 우수, 저나트륨, 적정 칼로리
- 7~8: 대체로 양호, 약간의 개선 여지
- 5~6: 보통, 특정 영양소 과다/부족
- 3~4: 영양 불균형, 고칼로리/고나트륨
- 1~2: 매우 불균형, 과도한 열량/지방/나트륨"""


def encode_image(image_path):
    """이미지를 base64로 인코딩"""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def analyze_food_image(image_path, api_key=None, model="gpt-4o"):
    """
    음식 사진을 GPT-4o Vision으로 분석

    Args:
        image_path: 이미지 파일 경로
        api_key: OpenAI API 키 (없으면 환경변수에서 가져옴)
        model: 사용할 모델 (기본: gpt-4o)

    Returns:
        dict: 분석 결과 (JSON)
    """
    try:
        import httpx
    except ImportError:
        try:
            import requests as httpx
            httpx.Client = None  # fallback marker
        except ImportError:
            print("httpx 또는 requests 필요: pip install httpx --break-system-packages")
            sys.exit(1)

    if api_key is None:
        api_key = os.environ.get('OPENAI_API_KEY')

    if not api_key:
        return {
            "error": "OPENAI_API_KEY가 설정되지 않았습니다.",
            "help": "1) .env 파일에 OPENAI_API_KEY=sk-... 추가하거나\n"
                    "2) --api-key 옵션으로 전달하세요.\n"
                    "3) OpenAI API 키는 https://platform.openai.com/api-keys 에서 발급"
        }

    if not Path(image_path).exists():
        return {"error": f"이미지 파일을 찾을 수 없습니다: {image_path}"}

    # 이미지 인코딩
    base64_image = encode_image(image_path)
    ext = Path(image_path).suffix.lower()
    media_type = {
        '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
        '.png': 'image/png', '.gif': 'image/gif',
        '.webp': 'image/webp',
    }.get(ext, 'image/jpeg')

    # API 호출
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
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
    }

    try:
        import httpx
        response = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=30.0,
        )
        result = response.json()
    except Exception as e:
        return {"error": f"API 호출 실패: {str(e)}"}

    if "error" in result:
        return {"error": f"OpenAI API 에러: {result['error'].get('message', str(result['error']))}"}

    # 응답 파싱
    content = result["choices"][0]["message"]["content"]

    # JSON 추출 (```json ... ``` 블록이 있을 수 있음)
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0]
    elif "```" in content:
        content = content.split("```")[1].split("```")[0]

    try:
        analysis = json.loads(content.strip())
    except json.JSONDecodeError:
        return {
            "error": "AI 응답을 파싱할 수 없습니다",
            "raw_response": content
        }

    return analysis


def _db_quality_score(match):
    """DB 항목의 데이터 품질을 0~5점으로 평가
    핵심 영양소(칼로리, 단백질, 탄수화물, 지방, serving_size)가 있으면 각 1점
    """
    score = 0
    if (match.get('calories_kcal') or 0) > 0: score += 1
    if (match.get('protein_g') or 0) > 0: score += 1
    if (match.get('carbs_g') or 0) > 0: score += 1
    if (match.get('fat_g') or 0) > 0: score += 1
    if (match.get('serving_size_g') or 0) > 0: score += 1
    return score


# ── 핵심 음식 참조 테이블 (식약처 기준, 100g당) ──
# 우선순위 1: 이 테이블에서 먼저 매칭
CORE_FOODS = {
    # ── 과일 ──
    "귤": {"cal":39,"prot":0.7,"carbs":9.3,"fat":0.1,"fiber":0.5,"sodium":1,"sugar":7.8,"serving":100,"category":"fruit"},
    "감귤": {"cal":39,"prot":0.7,"carbs":9.3,"fat":0.1,"fiber":0.5,"sodium":1,"sugar":7.8,"serving":100,"category":"fruit"},
    "오렌지": {"cal":47,"prot":0.9,"carbs":11.8,"fat":0.1,"fiber":2.4,"sodium":0,"sugar":9.4,"serving":200,"category":"fruit"},
    "사과": {"cal":57,"prot":0.2,"carbs":14.1,"fat":0.2,"fiber":2.4,"sodium":1,"sugar":10.4,"serving":200,"category":"fruit"},
    "바나나": {"cal":93,"prot":1.0,"carbs":22.0,"fat":0.1,"fiber":2.6,"sodium":1,"sugar":12.2,"serving":120,"category":"fruit"},
    "포도": {"cal":60,"prot":0.6,"carbs":15.5,"fat":0.1,"fiber":0.9,"sodium":2,"sugar":15.5,"serving":100,"category":"fruit"},
    "딸기": {"cal":32,"prot":0.7,"carbs":7.7,"fat":0.3,"fiber":2.0,"sodium":1,"sugar":4.9,"serving":150,"category":"fruit"},
    "수박": {"cal":30,"prot":0.6,"carbs":7.6,"fat":0.2,"fiber":0.4,"sodium":1,"sugar":6.2,"serving":200,"category":"fruit"},
    "참외": {"cal":31,"prot":1.1,"carbs":6.8,"fat":0.1,"fiber":0.9,"sodium":3,"sugar":5.6,"serving":200,"category":"fruit"},
    "배": {"cal":51,"prot":0.3,"carbs":13.1,"fat":0.1,"fiber":2.1,"sodium":2,"sugar":8.5,"serving":200,"category":"fruit"},
    "복숭아": {"cal":39,"prot":0.9,"carbs":9.5,"fat":0.1,"fiber":1.5,"sodium":0,"sugar":8.4,"serving":200,"category":"fruit"},
    "감": {"cal":64,"prot":0.5,"carbs":15.9,"fat":0.2,"fiber":2.5,"sodium":1,"sugar":12.5,"serving":150,"category":"fruit"},
    "키위": {"cal":61,"prot":1.1,"carbs":14.7,"fat":0.5,"fiber":3.0,"sodium":3,"sugar":9.0,"serving":100,"category":"fruit"},
    "망고": {"cal":60,"prot":0.8,"carbs":15.0,"fat":0.4,"fiber":1.6,"sodium":1,"sugar":13.7,"serving":150,"category":"fruit"},
    "블루베리": {"cal":57,"prot":0.7,"carbs":14.5,"fat":0.3,"fiber":2.4,"sodium":1,"sugar":10.0,"serving":100,"category":"fruit"},
    "체리": {"cal":63,"prot":1.1,"carbs":16.0,"fat":0.2,"fiber":2.1,"sodium":0,"sugar":12.8,"serving":100,"category":"fruit"},
    "자두": {"cal":46,"prot":0.7,"carbs":11.4,"fat":0.3,"fiber":1.4,"sodium":0,"sugar":9.9,"serving":100,"category":"fruit"},
    "파인애플": {"cal":50,"prot":0.5,"carbs":13.1,"fat":0.1,"fiber":1.4,"sodium":1,"sugar":9.9,"serving":150,"category":"fruit"},
    "메론": {"cal":34,"prot":0.8,"carbs":8.2,"fat":0.2,"fiber":0.9,"sodium":16,"sugar":7.9,"serving":200,"category":"fruit"},
    "한라봉": {"cal":42,"prot":0.8,"carbs":10.0,"fat":0.1,"fiber":0.6,"sodium":1,"sugar":8.0,"serving":150,"category":"fruit"},

    # ── 밥/곡류 ──
    "밥": {"cal":150,"prot":2.7,"carbs":33.5,"fat":0.3,"fiber":0.4,"sodium":2,"sugar":0,"serving":210,"category":"korean"},
    "흰밥": {"cal":150,"prot":2.7,"carbs":33.5,"fat":0.3,"fiber":0.4,"sodium":2,"sugar":0,"serving":210,"category":"korean"},
    "현미밥": {"cal":149,"prot":3.2,"carbs":32.0,"fat":1.0,"fiber":1.8,"sodium":2,"sugar":0,"serving":210,"category":"korean"},
    "잡곡밥": {"cal":152,"prot":3.5,"carbs":32.5,"fat":0.8,"fiber":1.5,"sodium":2,"sugar":0,"serving":210,"category":"korean"},
    "볶음밥": {"cal":180,"prot":5.0,"carbs":28.0,"fat":5.5,"fiber":0.8,"sodium":350,"sugar":1.0,"serving":250,"category":"korean"},
    "김밥": {"cal":200,"prot":5.0,"carbs":30.0,"fat":6.5,"fiber":1.2,"sodium":450,"sugar":2.5,"serving":230,"category":"korean"},
    "비빔밥": {"cal":116,"prot":4.0,"carbs":17.3,"fat":3.1,"fiber":1.5,"sodium":300,"sugar":2.0,"serving":450,"category":"korean"},
    "죽": {"cal":46,"prot":1.0,"carbs":10.0,"fat":0.2,"fiber":0.2,"sodium":200,"sugar":0,"serving":300,"category":"korean"},

    # ── 국/찌개 ──
    "김치찌개": {"cal":50,"prot":3.5,"carbs":2.5,"fat":3.0,"fiber":0.5,"sodium":500,"sugar":1.0,"serving":400,"category":"korean"},
    "된장찌개": {"cal":38,"prot":2.5,"carbs":3.0,"fat":1.8,"fiber":0.8,"sodium":480,"sugar":0.5,"serving":400,"category":"korean"},
    "순두부찌개": {"cal":45,"prot":3.0,"carbs":2.5,"fat":2.5,"fiber":0.5,"sodium":450,"sugar":0.5,"serving":400,"category":"korean"},
    "부대찌개": {"cal":65,"prot":4.0,"carbs":4.0,"fat":3.5,"fiber":0.5,"sodium":550,"sugar":1.5,"serving":400,"category":"korean"},
    "미역국": {"cal":20,"prot":1.5,"carbs":1.5,"fat":1.0,"fiber":0.5,"sodium":400,"sugar":0,"serving":300,"category":"korean"},
    "갈비탕": {"cal":55,"prot":4.5,"carbs":1.0,"fat":3.5,"fiber":0,"sodium":400,"sugar":0,"serving":500,"category":"korean"},
    "설렁탕": {"cal":45,"prot":4.0,"carbs":0.5,"fat":2.5,"fiber":0,"sodium":350,"sugar":0,"serving":500,"category":"korean"},
    "떡국": {"cal":70,"prot":3.0,"carbs":11.0,"fat":1.5,"fiber":0.3,"sodium":400,"sugar":0.5,"serving":400,"category":"korean"},
    "삼계탕": {"cal":60,"prot":5.0,"carbs":3.0,"fat":3.0,"fiber":0.2,"sodium":300,"sugar":0.5,"serving":600,"category":"korean"},

    # ── 고기/구이 ──
    "삼겹살": {"cal":331,"prot":17.0,"carbs":0.0,"fat":29.0,"fiber":0,"sodium":60,"sugar":0,"serving":200,"category":"korean"},
    "목살": {"cal":220,"prot":18.0,"carbs":0.0,"fat":16.0,"fiber":0,"sodium":55,"sugar":0,"serving":200,"category":"korean"},
    "소고기": {"cal":250,"prot":20.0,"carbs":0.0,"fat":18.0,"fiber":0,"sodium":60,"sugar":0,"serving":150,"category":"korean"},
    "불고기": {"cal":157,"prot":16.0,"carbs":8.0,"fat":7.0,"fiber":0.5,"sodium":400,"sugar":5.0,"serving":200,"category":"korean"},
    "갈비": {"cal":280,"prot":18.0,"carbs":5.0,"fat":20.0,"fiber":0,"sodium":350,"sugar":3.0,"serving":200,"category":"korean"},
    "닭가슴살": {"cal":109,"prot":23.0,"carbs":0.0,"fat":1.2,"fiber":0,"sodium":65,"sugar":0,"serving":100,"category":"diet_fitness"},
    "닭다리": {"cal":190,"prot":16.0,"carbs":0.0,"fat":14.0,"fiber":0,"sodium":75,"sugar":0,"serving":100,"category":"korean"},
    "돼지갈비": {"cal":240,"prot":15.0,"carbs":6.0,"fat":17.0,"fiber":0,"sodium":400,"sugar":4.0,"serving":200,"category":"korean"},
    "제육볶음": {"cal":150,"prot":12.0,"carbs":8.0,"fat":8.0,"fiber":0.5,"sodium":450,"sugar":4.0,"serving":200,"category":"korean"},
    "오리고기": {"cal":200,"prot":16.0,"carbs":0.0,"fat":15.0,"fiber":0,"sodium":60,"sugar":0,"serving":150,"category":"korean"},

    # ── 해산물 ──
    "연어": {"cal":208,"prot":20.0,"carbs":0.0,"fat":13.0,"fiber":0,"sodium":60,"sugar":0,"serving":100,"category":"foreign_popular"},
    "참치": {"cal":132,"prot":28.0,"carbs":0.0,"fat":1.3,"fiber":0,"sodium":40,"sugar":0,"serving":100,"category":"korean"},
    "고등어": {"cal":202,"prot":19.0,"carbs":0.0,"fat":13.0,"fiber":0,"sodium":70,"sugar":0,"serving":100,"category":"korean"},
    "새우": {"cal":85,"prot":18.0,"carbs":0.8,"fat":0.7,"fiber":0,"sodium":150,"sugar":0,"serving":100,"category":"korean"},
    "오징어": {"cal":84,"prot":17.5,"carbs":0.8,"fat":1.0,"fiber":0,"sodium":200,"sugar":0,"serving":100,"category":"korean"},
    "광어": {"cal":110,"prot":21.0,"carbs":0.0,"fat":2.5,"fiber":0,"sodium":55,"sugar":0,"serving":100,"category":"korean"},
    "조개": {"cal":78,"prot":12.0,"carbs":3.5,"fat":1.5,"fiber":0,"sodium":250,"sugar":0,"serving":100,"category":"korean"},
    "회": {"cal":100,"prot":20.0,"carbs":0.5,"fat":1.5,"fiber":0,"sodium":50,"sugar":0,"serving":150,"category":"korean"},

    # ── 반찬/김치 ──
    "김치": {"cal":17,"prot":1.5,"carbs":2.4,"fat":0.5,"fiber":1.5,"sodium":600,"sugar":1.0,"serving":40,"category":"korean"},
    "배추김치": {"cal":17,"prot":1.5,"carbs":2.4,"fat":0.5,"fiber":1.5,"sodium":600,"sugar":1.0,"serving":40,"category":"korean"},
    "깍두기": {"cal":23,"prot":1.2,"carbs":4.2,"fat":0.3,"fiber":1.2,"sodium":550,"sugar":2.0,"serving":40,"category":"korean"},
    "계란후라이": {"cal":196,"prot":12.0,"carbs":0.8,"fat":15.0,"fiber":0,"sodium":180,"sugar":0.3,"serving":60,"category":"korean"},
    "계란말이": {"cal":160,"prot":11.0,"carbs":2.0,"fat":12.0,"fiber":0,"sodium":300,"sugar":1.0,"serving":80,"category":"korean"},
    "삶은계란": {"cal":155,"prot":13.0,"carbs":1.1,"fat":11.0,"fiber":0,"sodium":125,"sugar":1.1,"serving":50,"category":"korean"},
    "두부": {"cal":79,"prot":8.0,"carbs":1.5,"fat":4.5,"fiber":0.5,"sodium":10,"sugar":0.5,"serving":100,"category":"korean"},
    "콩나물": {"cal":30,"prot":3.5,"carbs":3.0,"fat":0.5,"fiber":1.5,"sodium":5,"sugar":0,"serving":70,"category":"korean"},
    "시금치": {"cal":23,"prot":2.9,"carbs":3.6,"fat":0.4,"fiber":2.2,"sodium":79,"sugar":0.4,"serving":70,"category":"korean"},
    "멸치볶음": {"cal":200,"prot":25.0,"carbs":10.0,"fat":7.0,"fiber":0.5,"sodium":800,"sugar":6.0,"serving":30,"category":"korean"},
    "잡채": {"cal":130,"prot":3.0,"carbs":20.0,"fat":4.5,"fiber":1.0,"sodium":350,"sugar":5.0,"serving":150,"category":"korean"},
    "나물": {"cal":35,"prot":2.5,"carbs":4.0,"fat":1.5,"fiber":2.0,"sodium":250,"sugar":1.0,"serving":70,"category":"korean"},

    # ── 면류 ──
    "라면": {"cal":375,"prot":8.3,"carbs":51.7,"fat":13.3,"fiber":2.0,"sodium":1500,"sugar":3.0,"serving":120,"category":"korean"},
    "신라면": {"cal":375,"prot":8.3,"carbs":51.7,"fat":13.3,"fiber":2.0,"sodium":1500,"sugar":3.0,"serving":120,"category":"franchise"},
    "짜파게티": {"cal":370,"prot":9.0,"carbs":52.0,"fat":13.0,"fiber":2.5,"sodium":1200,"sugar":4.0,"serving":140,"category":"franchise"},
    "잔치국수": {"cal":120,"prot":4.0,"carbs":20.0,"fat":2.0,"fiber":1.0,"sodium":500,"sugar":2.0,"serving":400,"category":"korean"},
    "칼국수": {"cal":110,"prot":4.5,"carbs":18.0,"fat":2.0,"fiber":0.8,"sodium":450,"sugar":1.5,"serving":450,"category":"korean"},
    "냉면": {"cal":115,"prot":4.0,"carbs":22.0,"fat":1.0,"fiber":1.0,"sodium":400,"sugar":3.0,"serving":500,"category":"korean"},
    "자장면": {"cal":130,"prot":4.0,"carbs":22.0,"fat":3.0,"fiber":1.0,"sodium":500,"sugar":4.0,"serving":450,"category":"korean"},
    "짬뽕": {"cal":75,"prot":4.5,"carbs":10.0,"fat":2.0,"fiber":1.0,"sodium":600,"sugar":2.0,"serving":500,"category":"korean"},
    "파스타": {"cal":160,"prot":6.0,"carbs":28.0,"fat":3.0,"fiber":1.5,"sodium":350,"sugar":2.5,"serving":300,"category":"foreign_popular"},
    "우동": {"cal":105,"prot":3.5,"carbs":20.0,"fat":1.0,"fiber":0.8,"sodium":500,"sugar":2.0,"serving":400,"category":"foreign_popular"},

    # ── 분식 ──
    "떡볶이": {"cal":193,"prot":3.5,"carbs":41.0,"fat":1.5,"fiber":0.8,"sodium":600,"sugar":8.0,"serving":300,"category":"korean"},
    "순대": {"cal":180,"prot":10.0,"carbs":18.0,"fat":7.0,"fiber":0.5,"sodium":400,"sugar":1.0,"serving":150,"category":"korean"},
    "튀김": {"cal":220,"prot":5.0,"carbs":25.0,"fat":11.0,"fiber":1.0,"sodium":300,"sugar":2.0,"serving":100,"category":"korean"},
    "만두": {"cal":200,"prot":8.0,"carbs":25.0,"fat":7.0,"fiber":1.0,"sodium":400,"sugar":2.0,"serving":100,"category":"korean"},
    "어묵": {"cal":95,"prot":8.0,"carbs":10.0,"fat":2.5,"fiber":0,"sodium":500,"sugar":2.0,"serving":100,"category":"korean"},
    "붕어빵": {"cal":260,"prot":6.0,"carbs":45.0,"fat":6.0,"fiber":1.0,"sodium":200,"sugar":15.0,"serving":80,"category":"snack_drink"},
    "호떡": {"cal":300,"prot":4.0,"carbs":50.0,"fat":10.0,"fiber":0.5,"sodium":200,"sugar":20.0,"serving":100,"category":"snack_drink"},

    # ── 외식/프랜차이즈 ──
    "치킨": {"cal":266,"prot":17.0,"carbs":10.0,"fat":18.0,"fiber":0.5,"sodium":500,"sugar":1.0,"serving":100,"category":"franchise"},
    "후라이드치킨": {"cal":266,"prot":17.0,"carbs":10.0,"fat":18.0,"fiber":0.5,"sodium":500,"sugar":1.0,"serving":100,"category":"franchise"},
    "양념치킨": {"cal":280,"prot":16.0,"carbs":15.0,"fat":17.0,"fiber":0.5,"sodium":550,"sugar":8.0,"serving":100,"category":"franchise"},
    "피자": {"cal":266,"prot":11.0,"carbs":33.0,"fat":10.0,"fiber":2.0,"sodium":600,"sugar":3.5,"serving":107,"category":"franchise"},
    "햄버거": {"cal":250,"prot":12.0,"carbs":28.0,"fat":10.0,"fiber":1.5,"sodium":500,"sugar":5.0,"serving":200,"category":"franchise"},
    "감자튀김": {"cal":312,"prot":3.4,"carbs":41.0,"fat":15.0,"fiber":3.8,"sodium":210,"sugar":0.3,"serving":115,"category":"franchise"},
    "핫도그": {"cal":290,"prot":10.0,"carbs":24.0,"fat":17.0,"fiber":1.0,"sodium":700,"sugar":4.0,"serving":100,"category":"franchise"},
    "돈까스": {"cal":230,"prot":14.0,"carbs":18.0,"fat":12.0,"fiber":1.0,"sodium":500,"sugar":2.0,"serving":200,"category":"foreign_popular"},
    "초밥": {"cal":150,"prot":6.0,"carbs":25.0,"fat":3.0,"fiber":0.3,"sodium":350,"sugar":4.0,"serving":200,"category":"foreign_popular"},
    "카레": {"cal":120,"prot":4.0,"carbs":16.0,"fat":5.0,"fiber":1.5,"sodium":400,"sugar":3.0,"serving":350,"category":"foreign_popular"},
    "짜장밥": {"cal":140,"prot":4.5,"carbs":22.0,"fat":4.0,"fiber":1.0,"sodium":500,"sugar":4.0,"serving":400,"category":"korean"},
    "덮밥": {"cal":130,"prot":5.0,"carbs":20.0,"fat":3.5,"fiber":1.0,"sodium":400,"sugar":3.0,"serving":400,"category":"korean"},
    "샐러드": {"cal":25,"prot":1.5,"carbs":3.5,"fat":0.5,"fiber":2.0,"sodium":50,"sugar":2.0,"serving":200,"category":"diet_fitness"},
    "샌드위치": {"cal":210,"prot":9.0,"carbs":25.0,"fat":8.0,"fiber":1.5,"sodium":450,"sugar":4.0,"serving":200,"category":"foreign_popular"},

    # ── 빵/디저트 ──
    "식빵": {"cal":265,"prot":9.0,"carbs":49.0,"fat":3.2,"fiber":2.7,"sodium":500,"sugar":5.0,"serving":30,"category":"snack_drink"},
    "크로와상": {"cal":406,"prot":8.0,"carbs":45.0,"fat":21.0,"fiber":2.3,"sodium":350,"sugar":6.0,"serving":60,"category":"snack_drink"},
    "케이크": {"cal":300,"prot":5.0,"carbs":38.0,"fat":15.0,"fiber":0.5,"sodium":200,"sugar":25.0,"serving":100,"category":"snack_drink"},
    "도넛": {"cal":400,"prot":5.0,"carbs":50.0,"fat":20.0,"fiber":1.0,"sodium":350,"sugar":20.0,"serving":75,"category":"snack_drink"},
    "와플": {"cal":310,"prot":6.0,"carbs":40.0,"fat":14.0,"fiber":1.0,"sodium":400,"sugar":12.0,"serving":100,"category":"snack_drink"},
    "떡": {"cal":230,"prot":4.0,"carbs":52.0,"fat":0.5,"fiber":0.5,"sodium":150,"sugar":8.0,"serving":100,"category":"korean"},
    "아이스크림": {"cal":207,"prot":3.5,"carbs":24.0,"fat":11.0,"fiber":0.5,"sodium":80,"sugar":21.0,"serving":100,"category":"snack_drink"},
    "초콜릿": {"cal":546,"prot":5.0,"carbs":60.0,"fat":31.0,"fiber":3.4,"sodium":24,"sugar":48.0,"serving":40,"category":"snack_drink"},

    # ── 음료 ──
    "우유": {"cal":65,"prot":3.2,"carbs":4.7,"fat":3.5,"fiber":0,"sodium":40,"sugar":4.7,"serving":200,"category":"snack_drink"},
    "두유": {"cal":50,"prot":3.5,"carbs":5.0,"fat":1.8,"fiber":0.5,"sodium":40,"sugar":3.5,"serving":200,"category":"snack_drink"},
    "콜라": {"cal":39,"prot":0.0,"carbs":10.6,"fat":0.0,"fiber":0,"sodium":5,"sugar":10.6,"serving":250,"category":"snack_drink"},
    "사이다": {"cal":40,"prot":0.0,"carbs":10.0,"fat":0.0,"fiber":0,"sodium":10,"sugar":10.0,"serving":250,"category":"snack_drink"},
    "커피": {"cal":2,"prot":0.3,"carbs":0.0,"fat":0.0,"fiber":0,"sodium":2,"sugar":0,"serving":200,"category":"snack_drink"},
    "아메리카노": {"cal":5,"prot":0.3,"carbs":0.5,"fat":0.0,"fiber":0,"sodium":2,"sugar":0,"serving":350,"category":"snack_drink"},
    "카페라떼": {"cal":50,"prot":3.0,"carbs":4.5,"fat":2.5,"fiber":0,"sodium":50,"sugar":4.5,"serving":350,"category":"snack_drink"},
    "녹차": {"cal":1,"prot":0.2,"carbs":0.0,"fat":0.0,"fiber":0,"sodium":1,"sugar":0,"serving":200,"category":"snack_drink"},
    "오렌지주스": {"cal":45,"prot":0.7,"carbs":10.5,"fat":0.2,"fiber":0.2,"sodium":2,"sugar":8.5,"serving":200,"category":"snack_drink"},
    "맥주": {"cal":43,"prot":0.5,"carbs":3.6,"fat":0.0,"fiber":0,"sodium":4,"sugar":0,"serving":500,"category":"snack_drink"},
    "소주": {"cal":87,"prot":0.0,"carbs":0.0,"fat":0.0,"fiber":0,"sodium":0,"sugar":0,"serving":360,"category":"snack_drink"},
    "막걸리": {"cal":56,"prot":0.8,"carbs":5.0,"fat":0.1,"fiber":0,"sodium":5,"sugar":3.5,"serving":300,"category":"snack_drink"},

    # ── 간식 ──
    "라면땅": {"cal":450,"prot":8.0,"carbs":55.0,"fat":22.0,"fiber":1.0,"sodium":500,"sugar":5.0,"serving":90,"category":"snack_drink"},
    "과자": {"cal":480,"prot":5.0,"carbs":62.0,"fat":24.0,"fiber":1.0,"sodium":400,"sugar":10.0,"serving":100,"category":"snack_drink"},
    "팝콘": {"cal":375,"prot":11.0,"carbs":74.0,"fat":4.5,"fiber":15.0,"sodium":5,"sugar":1.0,"serving":50,"category":"snack_drink"},
    "견과류": {"cal":607,"prot":20.0,"carbs":21.0,"fat":54.0,"fiber":7.0,"sodium":5,"sugar":4.0,"serving":30,"category":"snack_drink"},
    "아몬드": {"cal":579,"prot":21.0,"carbs":22.0,"fat":50.0,"fiber":12.0,"sodium":1,"sugar":4.0,"serving":30,"category":"snack_drink"},
    "고구마": {"cal":111,"prot":1.5,"carbs":26.0,"fat":0.1,"fiber":3.0,"sodium":10,"sugar":5.5,"serving":150,"category":"korean"},
    "감자": {"cal":77,"prot":2.0,"carbs":17.5,"fat":0.1,"fiber":2.2,"sodium":6,"sugar":0.8,"serving":150,"category":"korean"},
    "감자탕": {"cal":55,"prot":4.0,"carbs":3.0,"fat":3.0,"fiber":0.5,"sodium":500,"sugar":1.0,"serving":500,"category":"korean"},
    "김치볶음밥": {"cal":170,"prot":5.0,"carbs":26.0,"fat":5.0,"fiber":1.0,"sodium":450,"sugar":2.0,"serving":300,"category":"korean"},
    "순대국": {"cal":60,"prot":5.0,"carbs":3.0,"fat":3.5,"fiber":0.3,"sodium":500,"sugar":0.5,"serving":500,"category":"korean"},
    "닭볶음탕": {"cal":90,"prot":8.0,"carbs":5.0,"fat":4.0,"fiber":0.5,"sodium":450,"sugar":3.0,"serving":400,"category":"korean"},
    "보쌈": {"cal":180,"prot":18.0,"carbs":2.0,"fat":11.0,"fiber":0,"sodium":350,"sugar":1.0,"serving":200,"category":"korean"},
    "족발": {"cal":220,"prot":22.0,"carbs":0.5,"fat":14.0,"fiber":0,"sodium":300,"sugar":0.5,"serving":200,"category":"korean"},
    "해물파전": {"cal":160,"prot":6.0,"carbs":20.0,"fat":6.0,"fiber":1.0,"sodium":400,"sugar":2.0,"serving":200,"category":"korean"},
    "빈대떡": {"cal":200,"prot":8.0,"carbs":20.0,"fat":10.0,"fiber":1.5,"sodium":350,"sugar":1.0,"serving":150,"category":"korean"},
    "옥수수": {"cal":96,"prot":3.2,"carbs":19.0,"fat":1.2,"fiber":2.7,"sodium":15,"sugar":3.2,"serving":150,"category":"korean"},
    "토스트": {"cal":280,"prot":8.0,"carbs":35.0,"fat":12.0,"fiber":1.5,"sodium":400,"sugar":5.0,"serving":150,"category":"snack_drink"},
}


def _search_core_foods(name):
    """핵심 음식 참조 테이블에서 검색 (정확 → 관련성 높은 부분 매칭)"""
    # 1) 정확 매칭
    if name in CORE_FOODS:
        return name, CORE_FOODS[name]

    # 2) 부분 매칭: name이 CORE_FOODS 키로 시작하는 경우 (가장 긴 매칭 우선)
    #    예: "김치찌개" → "김치찌개" (O), "김치볶음밥" → "김치" (O)
    #    예: "레귤러피자" → "귤" (X — 시작이 아님)
    best_key = None
    best_len = 0
    for key in CORE_FOODS:
        # name이 key로 시작: "김치볶음밥".startswith("김치") → True
        if name.startswith(key) and len(key) > best_len:
            best_key = key
            best_len = len(key)
        # key가 name으로 시작: "삼겹살구이".startswith("삼겹살") — key측에서 시작
        # → name이 key의 앞부분인 경우만 (최소 2글자)
        elif key.startswith(name) and len(name) >= 2 and len(name) > best_len:
            best_key = key
            best_len = len(name)

    if best_key:
        return best_key, CORE_FOODS[best_key]

    # 3) 공백/구분자 뒤에 키가 나오는 경우
    #    예: "매운 김치찌개" → "김치찌개"
    for key in sorted(CORE_FOODS.keys(), key=len, reverse=True):  # 긴 키 우선
        if len(key) >= 2:
            idx = name.find(key)
            if idx > 0:
                prev = name[idx - 1]
                if prev in ' _(/·\t':
                    return key, CORE_FOODS[key]

    return None, None


def match_with_db(analysis, foods_db):
    """
    AI 분석 결과를 DB와 매칭하여 보정
    우선순위: 핵심 참조 테이블 → 기존 DB(품질 통과만) → AI 추정값 유지
    """
    if "error" in analysis or "foods" not in analysis:
        return analysis

    for food in analysis["foods"]:
        ai_name = (food.get("name_ko") or "").strip()
        if not ai_name:
            food['db_matched'] = False
            food['source'] = 'AI_ESTIMATED'
            continue

        # ── 1순위: 핵심 음식 참조 테이블 ──
        core_key, core_data = _search_core_foods(ai_name)
        if core_data:
            ai_serving = food.get('estimated_serving_g') or core_data['serving']
            ratio = ai_serving / 100  # CORE_FOODS는 100g 기준

            food['db_matched'] = True
            food['db_name'] = core_key
            food['calories_kcal'] = round(core_data['cal'] * ratio, 1)
            food['protein_g'] = round(core_data['prot'] * ratio, 1)
            food['carbs_g'] = round(core_data['carbs'] * ratio, 1)
            food['fat_g'] = round(core_data['fat'] * ratio, 1)
            food['fiber_g'] = round(core_data['fiber'] * ratio, 1)
            food['sodium_mg'] = round(core_data['sodium'] * ratio, 1)
            food['sugar_g'] = round(core_data['sugar'] * ratio, 1)
            food['source'] = 'CORE_REF'
            continue

        # ── 2순위: 기존 SQLite DB (품질 필터 적용) ──
        exact, partials = search_food_db(ai_name)
        match = exact or (partials[0] if partials else None)

        if match:
            quality = _db_quality_score(match)

            # 품질 3점 이상만 사용 (칼로리+단백질+탄수+지방+서빙 중 3개 이상)
            if quality >= 3:
                db_serving = match.get('serving_size_g') or 0
                ai_serving = food.get('estimated_serving_g') or 0

                if db_serving > 0 and ai_serving > 0:
                    ratio = ai_serving / db_serving
                else:
                    ratio = 1.0

                food['db_matched'] = True
                food['db_food_id'] = match.get('food_id', '')
                food['db_name'] = match.get('name_ko', '')

                for field_pair in [
                    ('calories_kcal', 'calories_kcal'),
                    ('protein_g', 'protein_g'),
                    ('carbs_g', 'carbs_g'),
                    ('fat_g', 'fat_g'),
                    ('fiber_g', 'fiber_g'),
                    ('sodium_mg', 'sodium_mg'),
                    ('sugar_g', 'sugar_g'),
                ]:
                    ai_field, db_field = field_pair
                    db_val = match.get(db_field)
                    if db_val is not None and isinstance(db_val, (int, float)) and db_val > 0:
                        food[ai_field] = round(db_val * ratio, 1)

                food['source'] = 'DB_MATCHED'
                continue

        # ── 3순위: AI 추정값 그대로 유지 ──
        food['db_matched'] = False
        food['source'] = 'AI_ESTIMATED'

    # meal_summary 재계산
    if "meal_summary" in analysis:
        analysis["meal_summary"]["total_calories"] = round(sum(f.get("calories_kcal", 0) or 0 for f in analysis["foods"]), 1)
        analysis["meal_summary"]["total_protein"] = round(sum(f.get("protein_g", 0) or 0 for f in analysis["foods"]), 1)
        analysis["meal_summary"]["total_carbs"] = round(sum(f.get("carbs_g", 0) or 0 for f in analysis["foods"]), 1)
        analysis["meal_summary"]["total_fat"] = round(sum(f.get("fat_g", 0) or 0 for f in analysis["foods"]), 1)

    return analysis


def format_result(analysis):
    """분석 결과를 사람이 읽기 좋은 텍스트로 변환"""
    if "error" in analysis:
        return f"오류: {analysis['error']}"

    lines = []
    lines.append("=" * 50)
    lines.append("  NutriLens 음식 분석 결과")
    lines.append("=" * 50)

    for i, food in enumerate(analysis.get("foods", []), 1):
        matched = "DB" if food.get('db_matched') else "AI추정"
        conf = food.get('confidence', 0)
        lines.append(f"\n[{i}] {food['name_ko']} ({food.get('name_en', '')})  [{matched}] 확신도:{conf:.0%}")
        lines.append(f"    양: {food.get('estimated_serving_g', '?')}g")
        lines.append(f"    칼로리: {food.get('calories_kcal', '?')} kcal")
        lines.append(f"    단백질: {food.get('protein_g', '?')}g  |  탄수화물: {food.get('carbs_g', '?')}g  |  지방: {food.get('fat_g', '?')}g")
        lines.append(f"    식이섬유: {food.get('fiber_g', '?')}g  |  나트륨: {food.get('sodium_mg', '?')}mg  |  당류: {food.get('sugar_g', '?')}g")

    summary = analysis.get("meal_summary", {})
    if summary:
        lines.append(f"\n{'─' * 50}")
        lines.append(f"  총 칼로리: {summary.get('total_calories', '?')} kcal")
        lines.append(f"  단백질 {summary.get('total_protein', '?')}g / 탄수 {summary.get('total_carbs', '?')}g / 지방 {summary.get('total_fat', '?')}g")
        lines.append(f"  식사 유형: {summary.get('meal_type', '?')}  |  건강 점수: {summary.get('health_score', '?')}/10")
        lines.append(f"\n  AI 코멘트: {summary.get('one_line_comment', '')}")
    lines.append("=" * 50)

    return '\n'.join(lines)


# ── 메인 실행 ──
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NutriLens AI 음식 분석기")
    parser.add_argument("--image", "-i", required=True, help="분석할 음식 사진 경로")
    parser.add_argument("--api-key", "-k", default=None, help="OpenAI API 키")
    parser.add_argument("--model", "-m", default="gpt-4o", help="사용할 모델 (기본: gpt-4o)")
    parser.add_argument("--json", action="store_true", help="JSON 형식으로 출력")
    parser.add_argument("--no-db", action="store_true", help="DB 매칭 건너뛰기")
    args = parser.parse_args()

    print(f"이미지 분석 중: {args.image}")
    print("GPT-4o Vision API에 요청 중...")

    # 1. AI 분석
    analysis = analyze_food_image(args.image, api_key=args.api_key, model=args.model)

    # 2. DB 매칭 (선택)
    if not args.no_db and "error" not in analysis:
        print("DB 매칭 중...")
        foods_db = load_food_db()
        if foods_db:
            analysis = match_with_db(analysis, foods_db)
            print(f"DB 매칭 완료 ({len(foods_db)}종 DB 사용)")

    # 3. 출력
    if args.json:
        print(json.dumps(analysis, ensure_ascii=False, indent=2))
    else:
        print(format_result(analysis))
