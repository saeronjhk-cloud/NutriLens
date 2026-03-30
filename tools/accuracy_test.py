#!/usr/bin/env python3
"""
NutriLens 정확도 테스트 도구
────────────────────────────
한국 음식 사진을 수집하고, AI 인식 정확도를 자동 측정합니다.

실행법:
  1단계: 사진 수집  →  python tools/accuracy_test.py --collect
  2단계: 정확도 측정 →  python tools/accuracy_test.py --test
  3단계: 결과 보기   →  .tmp/accuracy_report.txt

주의: OpenAI API 크레딧이 사용됩니다. --test 시 사진당 약 $0.01~0.02
"""

import os
import sys
import json
import time
import base64
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path
from datetime import datetime

# ── 경로 설정 ──
PROJECT_DIR = Path(__file__).parent.parent
TOOLS_DIR = Path(__file__).parent
TEST_DIR = PROJECT_DIR / ".tmp" / "test_images"
RESULT_DIR = PROJECT_DIR / ".tmp"
sys.path.insert(0, str(TOOLS_DIR))

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


# ── 테스트용 한국 음식 목록 + 위키미디어 커먼스 이미지 URL ──
# (CC 라이선스 이미지 — 자유 사용 가능)
TEST_FOODS = [
    {
        "name": "김치",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/4/44/Korean_cuisine-Gimchi-01.jpg/800px-Korean_cuisine-Gimchi-01.jpg",
    },
    {
        "name": "비빔밥",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/4/44/Dolsot-bibimbap.jpg/800px-Dolsot-bibimbap.jpg",
    },
    {
        "name": "불고기",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/0/0a/Korean_cuisine-Bulgogi-01.jpg/800px-Korean_cuisine-Bulgogi-01.jpg",
    },
    {
        "name": "김밥",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/7/7e/Gimbap_%28pixabay%29.jpg/800px-Gimbap_%28pixabay%29.jpg",
    },
    {
        "name": "떡볶이",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/4/4d/Tteok-bokki.jpg/800px-Tteok-bokki.jpg",
    },
    {
        "name": "된장찌개",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/4/44/Korean_cuisine-Doenjang_jjigae-01.jpg/800px-Korean_cuisine-Doenjang_jjigae-01.jpg",
    },
    {
        "name": "삼겹살",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/5/59/Korean_barbeque-Samgyeopsal-01.jpg/800px-Korean_barbeque-Samgyeopsal-01.jpg",
    },
    {
        "name": "잡채",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/5/54/Korean_cuisine-Japchae-01.jpg/800px-Korean_cuisine-Japchae-01.jpg",
    },
    {
        "name": "순두부찌개",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/3/31/Korean_cuisine-Sundubu_jjigae-01.jpg/800px-Korean_cuisine-Sundubu_jjigae-01.jpg",
    },
    {
        "name": "갈비탕",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/9/9e/Korean_cuisine-Galbitang-01.jpg/800px-Korean_cuisine-Galbitang-01.jpg",
    },
    {
        "name": "냉면",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ac/Korean_cuisine-Mul_naengmyeon-01.jpg/800px-Korean_cuisine-Mul_naengmyeon-01.jpg",
    },
    {
        "name": "김치찌개",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/3/34/Korean_cuisine-Kimchi_jjigae-01.jpg/800px-Korean_cuisine-Kimchi_jjigae-01.jpg",
    },
    {
        "name": "제육볶음",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/1/12/Korean_cuisine-Jeyuk_bokkeum-01.jpg/800px-Korean_cuisine-Jeyuk_bokkeum-01.jpg",
    },
    {
        "name": "파전",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e3/Korean_cuisine-Pajeon-01.jpg/800px-Korean_cuisine-Pajeon-01.jpg",
    },
    {
        "name": "감자탕",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/5/56/Gamjatang.jpg/800px-Gamjatang.jpg",
    },
    {
        "name": "칼국수",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/0/0c/Kalguksu.jpg/800px-Kalguksu.jpg",
    },
    {
        "name": "삼계탕",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/f/f8/Korean_cuisine-Samgyetang-01.jpg/800px-Korean_cuisine-Samgyetang-01.jpg",
    },
    {
        "name": "족발",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/9/96/Korean_cuisine-Jokbal-01.jpg/800px-Korean_cuisine-Jokbal-01.jpg",
    },
    {
        "name": "라면",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/b/bc/Ramyeon.jpg/800px-Ramyeon.jpg",
    },
    {
        "name": "닭갈비",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/1/1a/Dakgalbi.jpg/800px-Dakgalbi.jpg",
    },
    {
        "name": "볶음밥",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/2/2d/Bokkeumbap.jpg/800px-Bokkeumbap.jpg",
    },
    {
        "name": "만두",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/4/4b/Korean_cuisine-Mandu-01.jpg/800px-Korean_cuisine-Mandu-01.jpg",
    },
    {
        "name": "육회",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/8/89/Korean_cuisine-Yukhoe-01.jpg/800px-Korean_cuisine-Yukhoe-01.jpg",
    },
    {
        "name": "떡국",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a2/Tteokguk.jpg/800px-Tteokguk.jpg",
    },
    {
        "name": "순대",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/6/69/Korean_blood_sausage-Sundae-01.jpg/800px-Korean_blood_sausage-Sundae-01.jpg",
    },
    {
        "name": "치킨",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/5/5a/Korean_fried_chicken_%28cropped%29.jpg/800px-Korean_fried_chicken_%28cropped%29.jpg",
    },
    {
        "name": "해물탕",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/d/d2/Korean_cuisine-Haemultang-01.jpg/800px-Korean_cuisine-Haemultang-01.jpg",
    },
    {
        "name": "오징어볶음",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/0/0e/Korean_cuisine-Ojingeo_bokkeum-01.jpg/800px-Korean_cuisine-Ojingeo_bokkeum-01.jpg",
    },
    {
        "name": "떡",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/3/3d/Korean_rice_cake-Tteok-13.jpg/800px-Korean_rice_cake-Tteok-13.jpg",
    },
    {
        "name": "김치볶음밥",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/8/81/Kimchi_bokkeumbap.jpg/800px-Kimchi_bokkeumbap.jpg",
    },
    # ── 다이어트 / 피트니스 음식 ──
    {
        "name": "닭가슴살",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ae/Grilled_chicken_breast_%281%29.jpg/800px-Grilled_chicken_breast_%281%29.jpg",
    },
    {
        "name": "샐러드",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/9/94/Salad_platter.jpg/800px-Salad_platter.jpg",
    },
    {
        "name": "고구마",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/5/5e/GunGoguma.jpg/800px-GunGoguma.jpg",
    },
    {
        "name": "계란",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/4/44/Boiled_eggs.jpg/800px-Boiled_eggs.jpg",
    },
    {
        "name": "현미밥",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/f/fa/Brown_rice.jpg/800px-Brown_rice.jpg",
    },
    {
        "name": "오트밀",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/d/d2/Porridge.jpg/800px-Porridge.jpg",
    },
    {
        "name": "연어",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/3/39/SalmonSashimi.jpg/800px-SalmonSashimi.jpg",
    },
    {
        "name": "아보카도",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/6/6d/Good_Food_Display_-_NCI_Visuals_Online.jpg/800px-Good_Food_Display_-_NCI_Visuals_Online.jpg",
    },
    {
        "name": "두부",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/0/03/Korean_cuisine-Dubu-01.jpg/800px-Korean_cuisine-Dubu-01.jpg",
    },
    {
        "name": "바나나",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/8/8a/Banana-Single.jpg/800px-Banana-Single.jpg",
    },
    {
        "name": "프로틴쉐이크",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/6/6d/Protein_shake.jpg/800px-Protein_shake.jpg",
    },
    {
        "name": "그릭요거트",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/e/ea/Turkish_strained_yogurt.jpg/800px-Turkish_strained_yogurt.jpg",
    },
    {
        "name": "스테이크",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/4/4c/Filet_Mignon_Beef_Steak.jpg/800px-Filet_Mignon_Beef_Steak.jpg",
    },
    {
        "name": "브로콜리",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/0/03/Broccoli_and_cross_section_edit.jpg/800px-Broccoli_and_cross_section_edit.jpg",
    },
    {
        "name": "견과류",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/1/10/Mixed_nuts_small_pile.jpg/800px-Mixed_nuts_small_pile.jpg",
    },
]


def collect_images():
    """위키미디어 커먼스에서 한국 음식 이미지 다운로드"""
    print()
    print("=" * 55)
    print("  한국 음식 테스트 이미지 수집")
    print("=" * 55)

    TEST_DIR.mkdir(parents=True, exist_ok=True)

    success = 0
    fail = 0

    for i, food in enumerate(TEST_FOODS, 1):
        name = food["name"]
        url = food["url"]
        filename = f"{i:02d}_{name}.jpg"
        filepath = TEST_DIR / filename

        if filepath.exists():
            print(f"  [{i:02d}/{len(TEST_FOODS)}] {name} — 이미 있음 (스킵)")
            success += 1
            continue

        print(f"  [{i:02d}/{len(TEST_FOODS)}] {name} 다운로드 중...", end=" ")
        try:
            req = urllib.request.Request(url)
            req.add_header("User-Agent", "NutriLens-Test/1.0 (food recognition accuracy test)")
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read()
                with open(filepath, 'wb') as f:
                    f.write(data)
            size_kb = len(data) / 1024
            print(f"OK ({size_kb:.0f}KB)")
            success += 1
        except Exception as e:
            print(f"실패: {e}")
            fail += 1

        time.sleep(0.5)  # 서버 부하 방지

    print(f"\n  완료! 성공: {success}, 실패: {fail}")
    print(f"  저장 위치: {TEST_DIR}")
    return success


def call_openai_vision(base64_image, media_type, api_key):
    """GPT-4o Vision API 호출 (음식 이름만 간결하게)"""
    from food_analyzer import SYSTEM_PROMPT

    url = "https://api.openai.com/v1/chat/completions"
    payload = {
        "model": "gpt-4o",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "이 사진에 있는 음식을 분석해주세요."},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{media_type};base64,{base64_image}",
                            "detail": "low"  # 비용 절감
                        }
                    }
                ]
            }
        ],
        "max_tokens": 1000,
        "temperature": 0.1,
    }

    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=data, method='POST')
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')[:200]
        return None, f"API 에러 ({e.code}): {body}"
    except Exception as e:
        return None, f"요청 실패: {e}"

    content = result["choices"][0]["message"]["content"]

    # JSON 추출
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0]
    elif "```" in content:
        content = content.split("```")[1].split("```")[0]

    try:
        parsed = json.loads(content.strip())
        return parsed, None
    except json.JSONDecodeError:
        return None, f"파싱 실패: {content[:200]}"


def normalize(name):
    """비교용 이름 정규화"""
    name = name.strip().lower()
    # 일반적인 변형 처리
    name = name.replace(" ", "").replace("_", "")
    return name


def is_match(expected, ai_foods):
    """AI 결과에서 정답 음식이 포함되어 있는지 확인"""
    expected_norm = normalize(expected)

    for food in ai_foods:
        ai_name = normalize(food.get("name_ko", "") or food.get("name", ""))
        # 정확 일치
        if expected_norm == ai_name:
            return True, "정확 일치", ai_name
        # 부분 일치 (하나가 다른 하나에 포함)
        if expected_norm in ai_name or ai_name in expected_norm:
            return True, "부분 일치", ai_name
        # 핵심 단어 일치 (예: "김치찌개" ↔ "김치 찌개")
        if len(expected_norm) >= 2 and len(ai_name) >= 2:
            if expected_norm[:2] == ai_name[:2] and expected_norm[-2:] == ai_name[-2:]:
                return True, "유사 일치", ai_name

    # 모든 AI 결과의 이름 목록
    ai_names = [normalize(f.get("name_ko", "") or f.get("name", "")) for f in ai_foods]
    return False, "불일치", ", ".join(ai_names) if ai_names else "인식 없음"


def run_test():
    """수집된 이미지로 정확도 테스트 실행"""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("OPENAI_API_KEY가 설정되지 않았습니다.")
        sys.exit(1)

    # 테스트 이미지 확인
    if not TEST_DIR.exists():
        print("테스트 이미지가 없습니다. 먼저 --collect를 실행하세요.")
        sys.exit(1)

    images = sorted(TEST_DIR.glob("*.jpg"))
    if not images:
        print("테스트 이미지가 없습니다. 먼저 --collect를 실행하세요.")
        sys.exit(1)

    print()
    print("=" * 55)
    print("  NutriLens AI 정확도 테스트")
    print("=" * 55)
    print(f"  테스트 이미지: {len(images)}장")
    print(f"  모델: GPT-4o Vision")
    cost_est = len(images) * 0.01
    print(f"  예상 비용: ~${cost_est:.2f}")
    print()

    results = []
    correct = 0
    errors = 0

    for i, img_path in enumerate(images, 1):
        # 파일명에서 정답 추출 (예: "01_김치.jpg" → "김치")
        filename = img_path.stem
        parts = filename.split("_", 1)
        if len(parts) < 2:
            continue
        expected_name = parts[1]

        print(f"  [{i:02d}/{len(images)}] {expected_name}...", end=" ", flush=True)

        # 이미지 로드 & base64 인코딩
        with open(img_path, 'rb') as f:
            image_data = f.read()
        base64_image = base64.b64encode(image_data).decode('utf-8')

        # API 호출
        analysis, err = call_openai_vision(base64_image, "image/jpeg", api_key)

        if err:
            print(f"에러: {err}")
            results.append({
                "expected": expected_name,
                "result": "ERROR",
                "match_type": "에러",
                "ai_answer": str(err),
            })
            errors += 1
            time.sleep(1)
            continue

        # 결과 비교
        foods = analysis.get("foods", [])
        matched, match_type, ai_answer = is_match(expected_name, foods)

        if matched:
            correct += 1
            print(f"✓ {match_type} (AI: {ai_answer})")
        else:
            print(f"✗ 불일치 (AI: {ai_answer})")

        results.append({
            "expected": expected_name,
            "result": "CORRECT" if matched else "WRONG",
            "match_type": match_type,
            "ai_answer": ai_answer,
            "confidence": foods[0].get("confidence", 0) if foods else 0,
        })

        time.sleep(1)  # API 속도 제한 방지

    # ── 결과 요약 ──
    total = len(results)
    accuracy = (correct / total * 100) if total > 0 else 0

    print()
    print("=" * 55)
    print(f"  테스트 결과")
    print("=" * 55)
    print(f"  총 테스트: {total}장")
    print(f"  정답: {correct}장")
    print(f"  오답: {total - correct - errors}장")
    print(f"  에러: {errors}장")
    print(f"  정확도: {accuracy:.1f}%")
    print("=" * 55)

    # ── 상세 리포트 저장 ──
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = RESULT_DIR / "accuracy_report.txt"

    with open(report_path, 'w', encoding='utf-8') as f:
        f.write("NutriLens AI 정확도 테스트 리포트\n")
        f.write(f"테스트 일시: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        f.write(f"모델: GPT-4o Vision\n")
        f.write("=" * 60 + "\n\n")
        f.write(f"총 테스트: {total}장\n")
        f.write(f"정답: {correct}장\n")
        f.write(f"오답: {total - correct - errors}장\n")
        f.write(f"에러: {errors}장\n")
        f.write(f"정확도: {accuracy:.1f}%\n\n")
        f.write("-" * 60 + "\n")
        f.write(f"{'정답':<12} {'결과':<10} {'일치유형':<10} {'AI답변'}\n")
        f.write("-" * 60 + "\n")
        for r in results:
            status = "✓" if r["result"] == "CORRECT" else "✗" if r["result"] == "WRONG" else "!"
            f.write(f"{status} {r['expected']:<10} {r['result']:<10} {r['match_type']:<10} {r['ai_answer']}\n")
        f.write("-" * 60 + "\n")
        f.write(f"\n오답 목록:\n")
        for r in results:
            if r["result"] != "CORRECT":
                f.write(f"  - {r['expected']} → AI: {r['ai_answer']} ({r['match_type']})\n")

    print(f"\n  상세 리포트: {report_path}")

    # ── JSON 결과도 저장 ──
    json_path = RESULT_DIR / "accuracy_results.json"
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump({
            "date": datetime.now().isoformat(),
            "model": "gpt-4o",
            "total": total,
            "correct": correct,
            "errors": errors,
            "accuracy_pct": round(accuracy, 1),
            "details": results,
        }, f, ensure_ascii=False, indent=2)

    print(f"  JSON 결과: {json_path}")

    return accuracy


def main():
    import argparse
    parser = argparse.ArgumentParser(description="NutriLens 정확도 테스트")
    parser.add_argument("--collect", action="store_true", help="테스트 이미지 수집")
    parser.add_argument("--test", action="store_true", help="정확도 테스트 실행")
    parser.add_argument("--all", action="store_true", help="수집 + 테스트 한번에")
    args = parser.parse_args()

    if not args.collect and not args.test and not args.all:
        print("사용법:")
        print("  python tools/accuracy_test.py --collect   # 이미지 수집")
        print("  python tools/accuracy_test.py --test      # 정확도 테스트")
        print("  python tools/accuracy_test.py --all       # 수집 + 테스트")
        print()
        print(f"테스트 음식: {len(TEST_FOODS)}종")
        print(f"예상 API 비용: ~${len(TEST_FOODS) * 0.01:.2f}")
        sys.exit(0)

    if args.collect or args.all:
        collect_images()

    if args.test or args.all:
        print()
        accuracy = run_test()
        if accuracy >= 80:
            print(f"\n  ✓ 정확도 {accuracy:.1f}% — 양호!")
        elif accuracy >= 60:
            print(f"\n  △ 정확도 {accuracy:.1f}% — 개선 필요")
        else:
            print(f"\n  ✗ 정확도 {accuracy:.1f}% — 심각한 개선 필요")


if __name__ == "__main__":
    main()
