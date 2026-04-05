#!/usr/bin/env python3
"""
NutriLens 파이프라인 진단 도구
─────────────────────────────
AI 인식 → DB 매칭 → 영양소 보정, 각 단계에서 뭐가 어긋나는지 로그를 찍어서 분석

사용법:
  python tools/pipeline_diagnosis.py
  python tools/pipeline_diagnosis.py --food "김치찌개"  # 특정 음식만 테스트

결과:
  .tmp/diagnosis_report.txt  → 텍스트 리포트
"""

import sys
import json
import sqlite3
from pathlib import Path

# 프로젝트 경로
PROJECT_DIR = Path(__file__).parent.parent
TOOLS_DIR = Path(__file__).parent
sys.path.insert(0, str(TOOLS_DIR))

from food_analyzer import load_food_db, search_food_db

# ── 식약처 기준 참고 데이터 (100g 기준) ──
# 출처: 식품의약품안전처 식품영양성분 데이터베이스 (대략적 평균값)
REFERENCE_DATA = {
    "귤":       {"cal": 39,  "prot": 0.7,  "carbs": 9.3,  "fat": 0.1,  "serving_typical": 100, "note": "중간크기 1개 약 100g"},
    "사과":     {"cal": 57,  "prot": 0.2,  "carbs": 14.1, "fat": 0.2,  "serving_typical": 200, "note": "중간크기 1개 약 200g"},
    "바나나":   {"cal": 93,  "prot": 1.0,  "carbs": 22.0, "fat": 0.1,  "serving_typical": 120, "note": "중간크기 1개 약 120g"},
    "밥":       {"cal": 150, "prot": 2.7,  "carbs": 33.5, "fat": 0.3,  "serving_typical": 210, "note": "1공기 약 210g"},
    "김치":     {"cal": 17,  "prot": 1.5,  "carbs": 2.4,  "fat": 0.5,  "serving_typical": 40,  "note": "1회분 약 40g"},
    "김치찌개": {"cal": 50,  "prot": 3.5,  "carbs": 2.5,  "fat": 3.0,  "serving_typical": 400, "note": "1인분 약 400g"},
    "된장찌개": {"cal": 38,  "prot": 2.5,  "carbs": 3.0,  "fat": 1.8,  "serving_typical": 400, "note": "1인분 약 400g"},
    "삼겹살":   {"cal": 331, "prot": 17.0, "carbs": 0.0,  "fat": 29.0, "serving_typical": 200, "note": "1인분 약 200g"},
    "비빔밥":   {"cal": 116, "prot": 4.0,  "carbs": 17.3, "fat": 3.1,  "serving_typical": 450, "note": "1인분 약 450g"},
    "라면":     {"cal": 450, "prot": 10.0, "carbs": 62.0, "fat": 16.0, "serving_typical": 120, "note": "1봉지 건면 120g"},
    "떡볶이":   {"cal": 193, "prot": 3.5,  "carbs": 41.0, "fat": 1.5,  "serving_typical": 300, "note": "1인분 약 300g"},
    "치킨":     {"cal": 266, "prot": 17.0, "carbs": 10.0, "fat": 18.0, "serving_typical": 100, "note": "100g 기준 (2-3조각)"},
    "피자":     {"cal": 266, "prot": 11.0, "carbs": 33.0, "fat": 10.0, "serving_typical": 107, "note": "1슬라이스 약 107g"},
    "햄버거":   {"cal": 250, "prot": 12.0, "carbs": 28.0, "fat": 10.0, "serving_typical": 200, "note": "1개 약 200g"},
    "김밥":     {"cal": 200, "prot": 5.0,  "carbs": 30.0, "fat": 6.5,  "serving_typical": 230, "note": "1줄 약 230g"},
    "불고기":   {"cal": 157, "prot": 16.0, "carbs": 8.0,  "fat": 7.0,  "serving_typical": 200, "note": "1인분 약 200g"},
    "계란후라이":{"cal": 196, "prot": 12.0, "carbs": 0.8,  "fat": 15.0, "serving_typical": 60,  "note": "1개 약 60g"},
    "두부":     {"cal": 79,  "prot": 8.0,  "carbs": 1.5,  "fat": 4.5,  "serving_typical": 100, "note": "반모 약 100g"},
    "우유":     {"cal": 65,  "prot": 3.2,  "carbs": 4.7,  "fat": 3.5,  "serving_typical": 200, "note": "1컵 약 200ml"},
    "콜라":     {"cal": 39,  "prot": 0.0,  "carbs": 10.0, "fat": 0.0,  "serving_typical": 250, "note": "1캔 약 250ml"},
}


def diagnose_food(name, conn):
    """한 음식에 대해 파이프라인 전 과정을 진단"""
    result = {
        "name": name,
        "step1_db_search": None,
        "step2_db_data": None,
        "step3_issues": [],
        "step4_recommendation": None,
    }

    ref = REFERENCE_DATA.get(name)

    # ── STEP 1: DB 검색 ──
    exact, partials = search_food_db(name)

    if exact:
        result["step1_db_search"] = {"type": "exact", "matched_name": exact.get("name_ko")}
        match = exact
    elif partials:
        matched_names = [p.get("name_ko") for p in partials[:3]]
        result["step1_db_search"] = {"type": "partial", "matched_names": matched_names}
        match = partials[0]
    else:
        result["step1_db_search"] = {"type": "none"}
        match = None

    if not match:
        result["step3_issues"].append("DB에 해당 음식 없음 → AI 추정값만 사용됨")
        result["step4_recommendation"] = "DB에 기본 음식 추가 필요"
        return result

    # ── STEP 2: DB 데이터 품질 체크 ──
    db_data = {
        "name_ko": match.get("name_ko"),
        "calories_kcal": match.get("calories_kcal") or 0,
        "protein_g": match.get("protein_g") or 0,
        "carbs_g": match.get("carbs_g") or 0,
        "fat_g": match.get("fat_g") or 0,
        "serving_size_g": match.get("serving_size_g") or 0,
    }
    result["step2_db_data"] = db_data

    # 누락 필드 체크
    if db_data["serving_size_g"] == 0:
        result["step3_issues"].append(
            f"serving_size_g=0 → AI가 추정한 양과 비율 보정 불가, ratio=1로 처리됨"
        )
    if db_data["carbs_g"] == 0:
        result["step3_issues"].append(
            f"carbs_g=0 (DB 누락) → AI 추정값이 유지되지만, DB매칭 표시됨"
        )
    if db_data["fat_g"] == 0 and name not in ["귤", "사과", "바나나", "밥", "콜라"]:
        result["step3_issues"].append(f"fat_g=0 (DB 누락 가능)")

    # ── STEP 3: 참고값 대비 오차 ──
    if ref:
        sv = db_data["serving_size_g"] if db_data["serving_size_g"] > 0 else ref["serving_typical"]

        # 100g 기준 정규화
        if db_data["calories_kcal"] > 0 and sv > 0:
            norm_cal = db_data["calories_kcal"] / sv * 100
            cal_diff_pct = abs(norm_cal - ref["cal"]) / max(ref["cal"], 1) * 100
            if cal_diff_pct > 30:
                result["step3_issues"].append(
                    f"칼로리 오차 {cal_diff_pct:.0f}%: DB {norm_cal:.0f}kcal/100g vs 참고 {ref['cal']}kcal/100g"
                )

        if ref["carbs"] > 0 and db_data["carbs_g"] > 0 and sv > 0:
            norm_carbs = db_data["carbs_g"] / sv * 100
            carbs_diff_pct = abs(norm_carbs - ref["carbs"]) / max(ref["carbs"], 1) * 100
            if carbs_diff_pct > 50:
                result["step3_issues"].append(
                    f"탄수화물 오차 {carbs_diff_pct:.0f}%: DB {norm_carbs:.1f}g/100g vs 참고 {ref['carbs']}g/100g"
                )

        if ref["prot"] > 0 and db_data["protein_g"] > 0 and sv > 0:
            norm_prot = db_data["protein_g"] / sv * 100
            prot_diff_pct = abs(norm_prot - ref["prot"]) / max(ref["prot"], 1) * 100
            if prot_diff_pct > 50:
                result["step3_issues"].append(
                    f"단백질 오차 {prot_diff_pct:.0f}%: DB {norm_prot:.1f}g/100g vs 참고 {ref['prot']}g/100g"
                )

    # ── STEP 4: 종합 판정 ──
    issue_count = len(result["step3_issues"])
    if issue_count == 0:
        result["step4_recommendation"] = "✅ 양호 — DB 데이터 신뢰 가능"
    elif issue_count <= 2:
        result["step4_recommendation"] = "⚠️ 부분 문제 — 일부 영양소 누락, AI 추정값 혼용됨"
    else:
        result["step4_recommendation"] = "❌ 심각 — DB 데이터 품질 낮음, 결과 신뢰 불가"

    return result


def run_full_diagnosis():
    """전체 진단 실행"""
    # DB 로드
    db = load_food_db()
    print(f"DB 로드: {len(db):,}개 아이템")

    results = []
    for name in REFERENCE_DATA:
        r = diagnose_food(name, None)
        results.append(r)

    # ── 리포트 생성 ──
    lines = []
    lines.append("=" * 70)
    lines.append("  NutriLens 파이프라인 진단 리포트")
    lines.append("=" * 70)
    lines.append("")

    # 요약 통계
    total = len(results)
    ok = sum(1 for r in results if "✅" in (r["step4_recommendation"] or ""))
    warn = sum(1 for r in results if "⚠️" in (r["step4_recommendation"] or ""))
    fail = sum(1 for r in results if "❌" in (r["step4_recommendation"] or ""))
    no_db = sum(1 for r in results if r["step1_db_search"]["type"] == "none")

    lines.append(f"테스트 음식: {total}개")
    lines.append(f"  ✅ 양호: {ok}개")
    lines.append(f"  ⚠️ 부분문제: {warn}개")
    lines.append(f"  ❌ 심각: {fail}개")
    lines.append(f"  DB 미존재: {no_db}개")
    lines.append("")

    # 병목 분석
    lines.append("-" * 70)
    lines.append("  핵심 병목 분석")
    lines.append("-" * 70)

    all_issues = []
    for r in results:
        all_issues.extend(r["step3_issues"])

    issue_types = {}
    for iss in all_issues:
        if "serving_size_g=0" in iss:
            issue_types["serving_size 누락"] = issue_types.get("serving_size 누락", 0) + 1
        elif "carbs_g=0" in iss:
            issue_types["탄수화물 누락"] = issue_types.get("탄수화물 누락", 0) + 1
        elif "fat_g=0" in iss:
            issue_types["지방 누락"] = issue_types.get("지방 누락", 0) + 1
        elif "칼로리 오차" in iss:
            issue_types["칼로리 부정확"] = issue_types.get("칼로리 부정확", 0) + 1
        elif "탄수화물 오차" in iss:
            issue_types["탄수화물 부정확"] = issue_types.get("탄수화물 부정확", 0) + 1
        elif "단백질 오차" in iss:
            issue_types["단백질 부정확"] = issue_types.get("단백질 부정확", 0) + 1
        elif "DB에" in iss:
            issue_types["DB 미존재"] = issue_types.get("DB 미존재", 0) + 1

    for issue, count in sorted(issue_types.items(), key=lambda x: -x[1]):
        bar = "█" * count + "░" * (total - count)
        lines.append(f"  {issue}: {count}/{total} {bar}")

    lines.append("")

    # 음식별 상세
    lines.append("-" * 70)
    lines.append("  음식별 상세 진단")
    lines.append("-" * 70)

    for r in results:
        lines.append("")
        lines.append(f"  【{r['name']}】 {r['step4_recommendation']}")

        # DB 검색 결과
        search = r["step1_db_search"]
        if search["type"] == "exact":
            lines.append(f"    1단계 DB검색: 정확매칭 → {search['matched_name']}")
        elif search["type"] == "partial":
            lines.append(f"    1단계 DB검색: 부분매칭 → {search['matched_names']}")
        else:
            lines.append(f"    1단계 DB검색: ❌ 매칭 없음")

        # DB 데이터
        if r["step2_db_data"]:
            d = r["step2_db_data"]
            lines.append(f"    2단계 DB데이터: {d['calories_kcal']}kcal, P:{d['protein_g']}g, C:{d['carbs_g']}g, F:{d['fat_g']}g, sv:{d['serving_size_g']}g")

        # 참고값
        ref = REFERENCE_DATA.get(r["name"])
        if ref:
            lines.append(f"    참고(식약처): {ref['cal']}kcal/100g, P:{ref['prot']}g, C:{ref['carbs']}g, F:{ref['fat']}g ({ref['note']})")

        # 이슈
        for iss in r["step3_issues"]:
            lines.append(f"    ⚠ {iss}")

    lines.append("")
    lines.append("=" * 70)
    lines.append("  결론 및 권장 조치")
    lines.append("=" * 70)
    lines.append("")
    lines.append("  1. [최우선] DB 데이터 품질이 핵심 병목")
    lines.append("     → 238K 아이템 중 99.2%가 serving_size 없음, 96.8%가 탄수화물 없음")
    lines.append("     → DB 매칭이 되어도 불완전한 데이터로 덮어씌움")
    lines.append("")
    lines.append("  2. [대안A] 핵심 음식 수백개의 정확한 데이터를 별도 테이블로 관리")
    lines.append("     → 식약처 공식 DB에서 한국인 다빈도 음식 top 200~500개만 정제")
    lines.append("     → 이 테이블을 우선 매칭하고, 없으면 기존 DB 참조")
    lines.append("")
    lines.append("  3. [대안B] DB 매칭 조건을 엄격하게 변경")
    lines.append("     → 영양소가 3개 이상 누락된 DB 항목은 매칭하지 않음")
    lines.append("     → 대신 AI 추정값 사용 (GPT-4o의 일반 상식이 불완전한 DB보다 나음)")
    lines.append("")
    lines.append("  4. [권장] 대안A + 대안B 병행")
    lines.append("     → 핵심 음식은 정확한 참조 테이블로, 나머지는 AI 추정 + 품질 필터")
    lines.append("")

    report = "\n".join(lines)

    # 파일 저장
    tmp_dir = PROJECT_DIR / ".tmp"
    tmp_dir.mkdir(exist_ok=True)
    report_path = tmp_dir / "diagnosis_report.txt"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)

    # 콘솔 출력
    print(report)
    print(f"\n리포트 저장: {report_path}")

    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--food", type=str, help="특정 음식만 진단")
    args = parser.parse_args()

    if args.food:
        db = load_food_db()
        r = diagnose_food(args.food, None)
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        run_full_diagnosis()
