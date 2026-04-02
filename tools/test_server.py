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

# ── OpenAI API 호출 (urllib 사용, 외부 패키지 불필요) ──
def call_openai_vision(base64_image, media_type, api_key, model="gpt-4o"):
    """GPT-4o Vision API 호출"""
    url = "https://api.openai.com/v1/chat/completions"

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

  /* 음식별 나눠먹기 */
  .food-sharing { display: flex; align-items: center; gap: 8px; margin-top: 10px; padding-top: 10px; border-top: 1px solid #2a2a4a; }
  .food-sharing-label { font-size: 0.8em; color: #888; }
  .food-sharing-btn { width: 26px; height: 26px; border-radius: 50%; border: 1px solid #3a3a5a; background: #0f0f1a; color: #e0e0e0; font-size: 1em; cursor: pointer; display: flex; align-items: center; justify-content: center; }
  .food-sharing-btn:hover { border-color: #3b82f6; }
  .food-sharing-num { font-size: 0.95em; font-weight: 700; min-width: 20px; text-align: center; }

  /* 남은 양 바 */
  .eaten-bar { height: 6px; background: #2a2a4a; border-radius: 3px; margin: 8px 0; overflow: hidden; }
  .eaten-fill { height: 100%; border-radius: 3px; background: #6ee7b7; }

  /* 반응형 */
  @media (max-width: 600px) {
    .nutrition-grid { grid-template-columns: repeat(2, 1fr); }
    .summary-stats { grid-template-columns: repeat(2, 1fr); }
    .header h1 { font-size: 1.5em; }
    .after-meal-actions { flex-direction: column; }
  }
</style>
</head>
<body>
<div class="container">

  <div class="header">
    <h1>NutriLens</h1>
    <p>AI 음식 영양 분석기</p>
  </div>

  <!-- 1단계: 식전 사진 업로드 -->
  <div class="upload-area" id="uploadArea">
    <div class="icon">📸</div>
    <div class="text">음식 사진을 촬영하거나 선택하세요</div>
    <div class="subtext">식사 전 음식 사진을 찍어주세요</div>
    <input type="file" id="fileInput" accept="image/*" capture="environment" style="display:none" />
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

  <!-- 분석 결과 -->
  <div class="result-section" id="resultSection">
    <div class="result-header">
      <h2 id="resultTitle">분석 결과</h2>
      <button class="btn btn-secondary" onclick="resetAll()" style="padding:8px 16px; font-size:0.85em">새 사진</button>
    </div>
    <div id="foodCards"></div>
    <div id="summaryCard"></div>

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
    <div class="after-upload-box" onclick="document.getElementById('afterInput').click()">
      <div style="font-size:2em; margin-bottom:8px">📸</div>
      <div style="color:#6ee7b7; font-weight:600">식후 사진을 선택하세요</div>
      <div style="color:#888; font-size:0.85em; margin-top:6px">남은 음식이 보이도록 촬영해주세요</div>
      <input type="file" id="afterInput" accept="image/*" capture="environment" style="display:none" />
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

</div>

<script>
// ── 상태 ──
let selectedFile = null;
let afterFile = null;
let currentAnalysis = null;
let originalAnalysis = null; // 원본 분석 (100% 기준)
let foodSharing = {}; // 음식별 나눠먹기 인원수 { index: count }
let isAfterMealDone = false;

// ── 파일 선택 ──
const uploadArea = document.getElementById('uploadArea');
const fileInput = document.getElementById('fileInput');
uploadArea.addEventListener('click', () => fileInput.click());
fileInput.addEventListener('change', (e) => handleFile(e.target.files[0]));
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

function resetAll() {
  selectedFile = null; afterFile = null; currentAnalysis = null; originalAnalysis = null;
  foodSharing = {}; isAfterMealDone = false;
  fileInput.value = '';
  uploadArea.style.display = 'block';
  document.getElementById('previewSection').style.display = 'none';
  document.getElementById('resultSection').style.display = 'none';
  document.getElementById('errorBox').style.display = 'none';
  document.getElementById('loading').style.display = 'none';
  document.getElementById('afterUploadSection').style.display = 'none';
  document.getElementById('manualSection').style.display = 'none';
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
    renderResult(data, false);
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

document.getElementById('afterInput').addEventListener('change', (e) => {
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
});

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
    renderResult(data, true);
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
  document.getElementById('manualSection').style.display = 'none';
  renderResult(data, true);
}

// ── 음식별 나눠먹기 ──
function adjustFoodSharing(idx, delta) {
  foodSharing[idx] = Math.max(1, Math.min(20, (foodSharing[idx]||1) + delta));
  document.getElementById('fs_num_'+idx).textContent = foodSharing[idx];
  recalcTotal();
}
function recalcTotal() {
  if (!currentAnalysis) return;
  let tc=0, tp=0, tca=0, tf=0;
  (currentAnalysis.foods||[]).forEach((f, i) => {
    const div = foodSharing[i] || 1;
    tc += (f.calories_kcal||0)/div;
    tp += (f.protein_g||0)/div;
    tca += (f.carbs_g||0)/div;
    tf += (f.fat_g||0)/div;
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
  foodSharing = {};

  let cardsHtml = '';
  foods.forEach((food, i) => {
    foodSharing[i] = 1;
    const isDb = food.db_matched || food.source === 'DB_MATCHED';
    const conf = food.confidence || 0;
    const confPct = Math.round(conf * 100);
    const confColor = conf >= 0.8 ? '#6ee7b7' : conf >= 0.5 ? '#fbbf24' : '#f87171';

    let eatenBarHtml = '';
    if (isAfterMeal && food.eaten_pct !== undefined) {
      const ep = food.eaten_pct;
      eatenBarHtml = '<div style="font-size:0.8em; color:#6ee7b7; margin-bottom:4px">섭취량: '+ep+'% '+(food.leftover_note ? '· '+food.leftover_note : '')+'</div><div class="eaten-bar"><div class="eaten-fill" style="width:'+ep+'%"></div></div>';
    }

    cardsHtml += '<div class="food-card"><div class="food-title"><div><span class="food-name">'+(food.name_ko||'?')+'</span><span class="food-name-en">'+(food.name_en||'')+'</span></div><span class="source-badge '+(isDb?'source-db':'source-ai')+'">'+(isDb?'DB 검증':'AI 추정')+'</span></div><div class="confidence-bar"><div class="confidence-fill" style="width:'+confPct+'%;background:'+confColor+'"></div></div><div style="font-size:0.8em;color:#888;margin-bottom:10px;margin-top:-8px">확신도 '+confPct+'% · 약 '+(food.estimated_serving_g||'?')+'g</div>'+eatenBarHtml+'<div class="nutrition-grid"><div class="nutrition-item"><div class="nutrition-value">'+(food.calories_kcal||0)+'</div><div class="nutrition-label">칼로리 kcal</div></div><div class="nutrition-item"><div class="nutrition-value" style="color:#60a5fa">'+(food.protein_g||0)+'g</div><div class="nutrition-label">단백질</div></div><div class="nutrition-item"><div class="nutrition-value" style="color:#fbbf24">'+(food.carbs_g||0)+'g</div><div class="nutrition-label">탄수화물</div></div><div class="nutrition-item"><div class="nutrition-value" style="color:#f87171">'+(food.fat_g||0)+'g</div><div class="nutrition-label">지방</div></div></div><div class="food-sharing"><span class="food-sharing-label">나눠먹기</span><button class="food-sharing-btn" onclick="adjustFoodSharing('+i+',-1)">−</button><span class="food-sharing-num" id="fs_num_'+i+'">1</span><button class="food-sharing-btn" onclick="adjustFoodSharing('+i+',1)">+</button><span class="food-sharing-label">명</span></div></div>';
  });
  document.getElementById('foodCards').innerHTML = cardsHtml;

  // 요약
  const score = summary.health_score || 0;
  const scoreColor = score >= 7 ? '#6ee7b7' : score >= 5 ? '#fbbf24' : '#f87171';
  document.getElementById('resultTitle').textContent = isAfterMeal ? '실제 섭취 결과' : '분석 결과';
  document.getElementById('summaryCard').innerHTML = '<div class="summary-card"><div class="summary-title">'+(isAfterMeal?'실제 섭취 요약':'식사 영양 정보')+'</div><div class="summary-stats"><div class="stat-item"><div class="stat-value" id="sumCal">'+(summary.total_calories||0)+'</div><div class="stat-label">총 칼로리 (kcal)</div></div><div class="stat-item"><div class="stat-value" style="color:#60a5fa" id="sumPro">'+(summary.total_protein||0)+'g</div><div class="stat-label">단백질</div></div><div class="stat-item"><div class="stat-value" style="color:#fbbf24" id="sumCarb">'+(summary.total_carbs||0)+'g</div><div class="stat-label">탄수화물</div></div><div class="stat-item"><div class="stat-value" style="color:#f87171" id="sumFat">'+(summary.total_fat||0)+'g</div><div class="stat-label">지방</div></div></div><div class="health-score"><div class="score-circle" style="background:'+scoreColor+'33;border:2px solid '+scoreColor+'"><span style="color:'+scoreColor+'">'+score+'</span></div><div><div style="font-weight:600;margin-bottom:4px">건강 점수 '+score+'/10</div><div style="font-size:0.85em;color:#888">'+(summary.meal_type?summary.meal_type+' 식사':'')+'</div></div></div><div class="comment">'+(summary.one_line_comment||'')+'</div></div>';

  // 식후 배너: 첫 분석 후에만 표시, 이미 식후 분석 완료되면 숨김
  document.getElementById('afterMealBanner').style.display = isAfterMeal ? 'none' : 'block';
  document.getElementById('afterUploadSection').style.display = 'none';
  document.getElementById('manualSection').style.display = 'none';
  document.getElementById('resultSection').style.display = 'block';
}
</script>
</body>
</html>"""


class NutriLensHandler(BaseHTTPRequestHandler):
    """HTTP 요청 핸들러"""

    def do_GET(self):
        """메인 페이지 서빙"""
        if self.path == '/' or self.path == '/index.html':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode('utf-8'))
        elif self.path == '/version':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps({"version": "2.1", "json_mode": True, "db_count": len(FOODS_DB)}, ensure_ascii=False).encode('utf-8'))
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
        if self.path == '/analyze':
            self._handle_analyze()
        elif self.path == '/analyze-leftover':
            self._handle_leftover()
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

            # 3. 결과 반환
            print("분석 완료!")
            self._json_response(200, analysis)

        except Exception as e:
            import traceback
            traceback.print_exc()
            self._json_response(500, {"error": f"서버 에러: {str(e)}"})

    def _json_response(self, code, data):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
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
