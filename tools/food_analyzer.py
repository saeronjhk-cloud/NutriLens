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
def load_food_db(db_path=None):
    """SQLite DB를 로드 (빠르고 메모리 효율적)"""
    import sqlite3

    if db_path is None:
        # SQLite 우선, 없으면 엑셀 fallback
        sqlite_path = Path(__file__).parent.parent / 'nutrilens_db.sqlite'
        xlsx_path = Path(__file__).parent.parent / 'NutriLens_음식DB.xlsx'

        if sqlite_path.exists():
            return _load_sqlite(sqlite_path)
        elif xlsx_path.exists():
            return _load_xlsx(xlsx_path)
        else:
            print("DB 파일을 찾을 수 없습니다.")
            return []
    else:
        p = Path(db_path)
        if p.suffix == '.sqlite':
            return _load_sqlite(p)
        else:
            return _load_xlsx(p)


def _load_sqlite(db_path):
    """SQLite에서 로드 — 빠르고 메모리 효율적"""
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


def match_with_db(analysis, foods_db):
    """
    AI 분석 결과를 DB와 매칭하여 보정
    AI가 인식한 음식이 DB에 있으면 DB의 공인 데이터를 사용
    """
    if "error" in analysis or "foods" not in analysis:
        return analysis

    for food in analysis["foods"]:
        ai_name = food.get("name_ko", "")

        # DB에서 이름으로 검색 (정확 매칭 → 부분 매칭)
        exact_match = None
        partial_matches = []

        for db_food in foods_db:
            db_name = db_food.get("name_ko", "")
            if ai_name == db_name:
                exact_match = db_food
                break
            elif ai_name in db_name or db_name in ai_name:
                partial_matches.append(db_food)

        match = exact_match or (partial_matches[0] if partial_matches else None)

        if match:
            # DB 매칭 성공: 1인분 영양소를 AI 추정 양에 비례하여 보정
            db_serving = match.get('serving_size_g', 100) or 100
            ai_serving = food.get('estimated_serving_g', db_serving) or db_serving
            ratio = ai_serving / db_serving

            food['db_matched'] = True
            food['db_food_id'] = match.get('food_id', '')
            food['db_name'] = match.get('name_ko', '')

            # DB 값으로 보정 (양 비율 적용)
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
                if db_val is not None and isinstance(db_val, (int, float)):
                    food[ai_field] = round(db_val * ratio, 1)

            food['source'] = 'DB_MATCHED'
        else:
            food['db_matched'] = False
            food['source'] = 'AI_ESTIMATED'

    # meal_summary 재계산
    if "meal_summary" in analysis:
        analysis["meal_summary"]["total_calories"] = round(sum(f.get("calories_kcal", 0) for f in analysis["foods"]), 1)
        analysis["meal_summary"]["total_protein"] = round(sum(f.get("protein_g", 0) for f in analysis["foods"]), 1)
        analysis["meal_summary"]["total_carbs"] = round(sum(f.get("carbs_g", 0) for f in analysis["foods"]), 1)
        analysis["meal_summary"]["total_fat"] = round(sum(f.get("fat_g", 0) for f in analysis["foods"]), 1)

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
