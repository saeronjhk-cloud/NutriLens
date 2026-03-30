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

## 규칙
1. 첫 번째 사진(전)의 모든 음식을 식별하세요
2. 두 번째 사진(후)에서 남은 양을 추정하세요
3. eaten_pct = 실제 먹은 비율 (0~100). 다 먹었으면 100, 절반 남겼으면 50
4. 영양소는 원래 양 × (eaten_pct / 100) 으로 계산
5. 두 사진이 비슷해 보여도, 그릇의 내용물 높이나 양의 차이를 세심하게 비교하세요

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
    # 1) ```json ... ``` 블록
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0]
    elif "```" in content:
        content = content.split("```")[1].split("```")[0]

    # 2) 직접 파싱 시도
    try:
        return json.loads(content.strip())
    except json.JSONDecodeError:
        pass

    # 3) { 로 시작하는 부분 찾기
    start = content.find('{')
    if start >= 0:
        # 마지막 } 찾기
        end = content.rfind('}')
        if end > start:
            try:
                return json.loads(content[start:end+1])
            except json.JSONDecodeError:
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
<title>NutriLens - AI 음식 분석 테스트</title>
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
  .header {
    text-align: center;
    padding: 30px 0 20px;
  }
  .header h1 {
    font-size: 2em;
    background: linear-gradient(135deg, #6ee7b7, #3b82f6);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    margin-bottom: 8px;
  }
  .header p {
    color: #888;
    font-size: 0.95em;
  }
  .badge {
    display: inline-block;
    background: rgba(59, 130, 246, 0.15);
    color: #60a5fa;
    padding: 4px 12px;
    border-radius: 20px;
    font-size: 0.8em;
    margin-top: 8px;
  }

  /* API 키 입력 */
  .api-section {
    background: #1a1a2e;
    border: 1px solid #2a2a4a;
    border-radius: 12px;
    padding: 20px;
    margin-bottom: 20px;
  }
  .api-section label {
    display: block;
    font-size: 0.9em;
    color: #aaa;
    margin-bottom: 8px;
  }
  .api-section input {
    width: 100%;
    padding: 10px 14px;
    background: #0f0f1a;
    border: 1px solid #3a3a5a;
    border-radius: 8px;
    color: #e0e0e0;
    font-size: 0.95em;
  }
  .api-section input:focus {
    outline: none;
    border-color: #3b82f6;
  }
  .api-hint {
    font-size: 0.8em;
    color: #666;
    margin-top: 6px;
  }
  .api-hint a { color: #60a5fa; text-decoration: none; }

  /* 업로드 영역 */
  .upload-area {
    border: 2px dashed #3a3a5a;
    border-radius: 16px;
    padding: 50px 20px;
    text-align: center;
    cursor: pointer;
    transition: all 0.3s;
    background: #1a1a2e;
    margin-bottom: 20px;
  }
  .upload-area:hover, .upload-area.dragover {
    border-color: #3b82f6;
    background: rgba(59, 130, 246, 0.05);
  }
  .upload-area .icon { font-size: 3em; margin-bottom: 12px; }
  .upload-area .text { color: #888; font-size: 1em; }
  .upload-area .subtext { color: #555; font-size: 0.85em; margin-top: 6px; }

  /* 미리보기 */
  .preview-section {
    display: none;
    margin-bottom: 20px;
    text-align: center;
  }
  .preview-section img {
    max-width: 100%;
    max-height: 400px;
    border-radius: 12px;
    border: 1px solid #2a2a4a;
  }
  .preview-actions {
    margin-top: 12px;
    display: flex;
    gap: 10px;
    justify-content: center;
  }

  /* 버튼 */
  .btn {
    padding: 12px 28px;
    border: none;
    border-radius: 10px;
    font-size: 1em;
    cursor: pointer;
    font-weight: 600;
    transition: all 0.3s;
  }
  .btn-primary {
    background: linear-gradient(135deg, #3b82f6, #6366f1);
    color: white;
  }
  .btn-primary:hover { transform: translateY(-1px); box-shadow: 0 4px 15px rgba(59, 130, 246, 0.3); }
  .btn-primary:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }
  .btn-secondary {
    background: #2a2a4a;
    color: #ccc;
  }
  .btn-secondary:hover { background: #3a3a5a; }

  /* 로딩 */
  .loading {
    display: none;
    text-align: center;
    padding: 40px;
  }
  .spinner {
    width: 50px; height: 50px;
    border: 4px solid #2a2a4a;
    border-top-color: #3b82f6;
    border-radius: 50%;
    animation: spin 1s linear infinite;
    margin: 0 auto 16px;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  .loading p { color: #888; }

  /* 결과 */
  .result-section { display: none; }

  .result-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 16px;
  }
  .result-header h2 {
    font-size: 1.3em;
    color: #6ee7b7;
  }

  .food-card {
    background: #1a1a2e;
    border: 1px solid #2a2a4a;
    border-radius: 12px;
    padding: 20px;
    margin-bottom: 12px;
    transition: border-color 0.3s;
  }
  .food-card:hover { border-color: #3a3a5a; }

  .food-title {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 12px;
  }
  .food-name {
    font-size: 1.15em;
    font-weight: 600;
  }
  .food-name-en {
    font-size: 0.85em;
    color: #888;
    margin-left: 8px;
  }
  .source-badge {
    font-size: 0.75em;
    padding: 3px 10px;
    border-radius: 20px;
    font-weight: 600;
  }
  .source-db {
    background: rgba(110, 231, 183, 0.15);
    color: #6ee7b7;
  }
  .source-ai {
    background: rgba(251, 191, 36, 0.15);
    color: #fbbf24;
  }

  .confidence-bar {
    height: 4px;
    background: #2a2a4a;
    border-radius: 2px;
    margin-bottom: 14px;
    overflow: hidden;
  }
  .confidence-fill {
    height: 100%;
    border-radius: 2px;
    transition: width 0.8s ease;
  }

  .nutrition-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 10px;
  }
  .nutrition-item {
    text-align: center;
    padding: 10px 6px;
    background: #0f0f1a;
    border-radius: 8px;
  }
  .nutrition-value {
    font-size: 1.2em;
    font-weight: 700;
    color: #fff;
  }
  .nutrition-label {
    font-size: 0.75em;
    color: #888;
    margin-top: 2px;
  }

  /* 요약 카드 */
  .summary-card {
    background: linear-gradient(135deg, #1a1a2e, #1e1e3a);
    border: 1px solid #3b82f6;
    border-radius: 16px;
    padding: 24px;
    margin-top: 20px;
  }
  .summary-title {
    font-size: 1.1em;
    color: #60a5fa;
    margin-bottom: 16px;
  }
  .summary-stats {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 12px;
    margin-bottom: 16px;
  }
  .stat-item {
    text-align: center;
  }
  .stat-value {
    font-size: 1.5em;
    font-weight: 700;
    color: #fff;
  }
  .stat-label {
    font-size: 0.8em;
    color: #888;
  }
  .health-score {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 14px;
    background: rgba(0,0,0,0.2);
    border-radius: 10px;
    margin-bottom: 12px;
  }
  .score-circle {
    width: 52px; height: 52px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 1.4em;
    font-weight: 800;
    color: #fff;
    flex-shrink: 0;
  }
  .comment {
    font-size: 0.95em;
    color: #ccc;
    line-height: 1.5;
    padding: 10px 0;
  }

  /* 에러 */
  .error-box {
    display: none;
    background: rgba(239, 68, 68, 0.1);
    border: 1px solid rgba(239, 68, 68, 0.3);
    border-radius: 12px;
    padding: 20px;
    color: #f87171;
  }

  /* 모드 선택 */
  .mode-selector {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
    margin-bottom: 20px;
  }
  .mode-card {
    background: #1a1a2e;
    border: 2px solid #2a2a4a;
    border-radius: 12px;
    padding: 16px;
    text-align: center;
    cursor: pointer;
    transition: all 0.3s;
  }
  .mode-card:hover { border-color: #3b82f6; }
  .mode-card.active { border-color: #3b82f6; background: rgba(59,130,246,0.08); }
  .mode-icon { font-size: 1.5em; margin-bottom: 6px; }
  .mode-title { font-weight: 600; font-size: 0.95em; }
  .mode-desc { font-size: 0.8em; color: #888; margin-top: 4px; }

  /* 전/후 업로드 */
  .dual-upload {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 10px;
  }
  .upload-box {
    flex: 1;
    max-width: 45%;
    background: #1a1a2e;
    border: 2px dashed #3a3a5a;
    border-radius: 12px;
    padding: 12px;
    text-align: center;
    cursor: pointer;
    position: relative;
    min-height: 160px;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
  }
  .upload-box:hover { border-color: #3b82f6; }
  .upload-box img {
    max-width: 100%;
    max-height: 140px;
    border-radius: 8px;
  }
  .upload-box-label {
    font-size: 0.8em;
    color: #60a5fa;
    font-weight: 600;
    margin-bottom: 8px;
  }
  .upload-box-placeholder { color: #888; font-size: 0.85em; }
  .upload-arrow { font-size: 1.5em; color: #555; }

  /* 나눠먹기 인원수 */
  .sharing-section {
    background: #1a1a2e;
    border: 1px solid #2a2a4a;
    border-radius: 12px;
    padding: 16px;
    margin-bottom: 12px;
  }
  .sharing-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 8px;
  }
  .sharing-label { font-size: 0.9em; color: #ccc; }
  .sharing-controls {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .sharing-btn {
    width: 32px; height: 32px;
    border-radius: 50%;
    border: 1px solid #3a3a5a;
    background: #0f0f1a;
    color: #e0e0e0;
    font-size: 1.2em;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .sharing-btn:hover { border-color: #3b82f6; }
  .sharing-count { font-size: 1.1em; font-weight: 700; min-width: 24px; text-align: center; }

  /* 남은 양 표시 */
  .eaten-bar {
    height: 6px;
    background: #2a2a4a;
    border-radius: 3px;
    margin: 8px 0;
    overflow: hidden;
  }
  .eaten-fill {
    height: 100%;
    border-radius: 3px;
    background: #6ee7b7;
  }

  /* 반응형 */
  @media (max-width: 600px) {
    .nutrition-grid { grid-template-columns: repeat(2, 1fr); }
    .summary-stats { grid-template-columns: repeat(2, 1fr); }
    .header h1 { font-size: 1.5em; }
    .mode-title { font-size: 0.85em; }
    .mode-desc { font-size: 0.75em; }
  }
</style>
</head>
<body>
<div class="container">

  <!-- 헤더 -->
  <div class="header">
    <h1>NutriLens</h1>
    <p>AI 음식 영양 분석기</p>
    <span class="badge">Phase 2 테스트</span>
  </div>

  <!-- API 키 입력 -->
  <div class="api-section">
    <label>OpenAI API Key</label>
    <input type="password" id="apiKey" placeholder="sk-..." />
    <p class="api-hint">
      API 키가 없으시면 <a href="https://platform.openai.com/api-keys" target="_blank">OpenAI 사이트</a>에서 발급받으세요.
      키는 서버로만 전송되며 어디에도 저장되지 않습니다.
    </p>
  </div>

  <!-- 모드 선택 -->
  <div class="mode-selector" id="modeSelector">
    <div class="mode-card active" id="modeNormal" onclick="setMode('normal')">
      <div class="mode-icon">📸</div>
      <div class="mode-title">일반 분석</div>
      <div class="mode-desc">음식 사진 1장으로 분석</div>
    </div>
    <div class="mode-card" id="modeLeftover" onclick="setMode('leftover')">
      <div class="mode-icon">📸➡📸</div>
      <div class="mode-title">남은 음식 비교</div>
      <div class="mode-desc">전/후 사진 2장 비교</div>
    </div>
  </div>

  <!-- 일반 모드: 업로드 영역 -->
  <div class="upload-area" id="uploadArea">
    <div class="icon">📸</div>
    <div class="text">음식 사진을 여기에 끌어다 놓거나 클릭하세요</div>
    <div class="subtext">JPG, PNG, WebP 지원 · 최대 10MB</div>
    <input type="file" id="fileInput" accept="image/*" style="display:none" />
  </div>

  <!-- 남은음식 모드: 전/후 업로드 -->
  <div class="leftover-upload" id="leftoverUpload" style="display:none">
    <div class="dual-upload">
      <div class="upload-box" id="beforeBox" onclick="document.getElementById('beforeInput').click()">
        <div class="upload-box-label">먹기 전</div>
        <img id="beforePreview" style="display:none" />
        <div class="upload-box-placeholder" id="beforePlaceholder">📸 터치하여 선택</div>
        <input type="file" id="beforeInput" accept="image/*" style="display:none" />
      </div>
      <div class="upload-arrow">➡</div>
      <div class="upload-box" id="afterBox" onclick="document.getElementById('afterInput').click()">
        <div class="upload-box-label">먹은 후</div>
        <img id="afterPreview" style="display:none" />
        <div class="upload-box-placeholder" id="afterPlaceholder">📸 터치하여 선택</div>
        <input type="file" id="afterInput" accept="image/*" style="display:none" />
      </div>
    </div>
    <div style="text-align:center; margin-top:14px">
      <button class="btn btn-primary" id="leftoverBtn" onclick="analyzeLeftover()" disabled>🔍 남은 음식 분석</button>
      <button class="btn btn-secondary" onclick="resetUpload()">다시 선택</button>
    </div>
  </div>

  <!-- 일반 모드: 미리보기 -->
  <div class="preview-section" id="previewSection">
    <img id="previewImg" />
    <div class="preview-actions">
      <button class="btn btn-primary" id="analyzeBtn" onclick="analyzeFood()">🔍 분석 시작</button>
      <button class="btn btn-secondary" onclick="resetUpload()">다시 선택</button>
    </div>
  </div>

  <!-- 로딩 -->
  <div class="loading" id="loading">
    <div class="spinner"></div>
    <p>AI가 음식을 분석하고 있습니다...</p>
    <p style="font-size:0.85em; color:#555; margin-top:8px">GPT-4o Vision + DB 매칭 (약 5~15초)</p>
  </div>

  <!-- 에러 -->
  <div class="error-box" id="errorBox"></div>

  <!-- 결과 -->
  <div class="result-section" id="resultSection">
    <div class="result-header">
      <h2>분석 결과</h2>
      <button class="btn btn-secondary" onclick="resetUpload()" style="padding:8px 16px; font-size:0.85em">새 사진 분석</button>
    </div>
    <div id="foodCards"></div>
    <div id="summaryCard"></div>
  </div>

</div>

<script>
// ── 상태 ──
let currentMode = 'normal';
let selectedFile = null;
let beforeFile = null;
let afterFile = null;
let currentAnalysis = null;  // 나눠먹기용

// ── 모드 전환 ──
function setMode(mode) {
  currentMode = mode;
  document.getElementById('modeNormal').className = 'mode-card' + (mode === 'normal' ? ' active' : '');
  document.getElementById('modeLeftover').className = 'mode-card' + (mode === 'leftover' ? ' active' : '');
  resetUpload();
  if (mode === 'normal') {
    document.getElementById('uploadArea').style.display = 'block';
    document.getElementById('leftoverUpload').style.display = 'none';
  } else {
    document.getElementById('uploadArea').style.display = 'none';
    document.getElementById('leftoverUpload').style.display = 'block';
  }
}

// ── 일반 모드: 파일 선택 ──
const uploadArea = document.getElementById('uploadArea');
const fileInput = document.getElementById('fileInput');

uploadArea.addEventListener('click', () => fileInput.click());
fileInput.addEventListener('change', (e) => handleFile(e.target.files[0]));
uploadArea.addEventListener('dragover', (e) => { e.preventDefault(); uploadArea.classList.add('dragover'); });
uploadArea.addEventListener('dragleave', () => uploadArea.classList.remove('dragover'));
uploadArea.addEventListener('drop', (e) => {
  e.preventDefault(); uploadArea.classList.remove('dragover');
  if (e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0]);
});

function handleFile(file) {
  if (!file || !file.type.startsWith('image/')) return alert('이미지 파일만 가능합니다.');
  if (file.size > 10*1024*1024) return alert('10MB 이하만 가능합니다.');
  selectedFile = file;
  const reader = new FileReader();
  reader.onload = (e) => {
    document.getElementById('previewImg').src = e.target.result;
    uploadArea.style.display = 'none';
    document.getElementById('previewSection').style.display = 'block';
    document.getElementById('resultSection').style.display = 'none';
    document.getElementById('errorBox').style.display = 'none';
  };
  reader.readAsDataURL(file);
}

// ── 남은음식 모드: 전/후 파일 선택 ──
document.getElementById('beforeInput').addEventListener('change', (e) => {
  beforeFile = e.target.files[0];
  if (beforeFile) {
    const r = new FileReader();
    r.onload = (ev) => {
      document.getElementById('beforePreview').src = ev.target.result;
      document.getElementById('beforePreview').style.display = 'block';
      document.getElementById('beforePlaceholder').style.display = 'none';
    };
    r.readAsDataURL(beforeFile);
  }
  checkLeftoverReady();
});
document.getElementById('afterInput').addEventListener('change', (e) => {
  afterFile = e.target.files[0];
  if (afterFile) {
    const r = new FileReader();
    r.onload = (ev) => {
      document.getElementById('afterPreview').src = ev.target.result;
      document.getElementById('afterPreview').style.display = 'block';
      document.getElementById('afterPlaceholder').style.display = 'none';
    };
    r.readAsDataURL(afterFile);
  }
  checkLeftoverReady();
});
function checkLeftoverReady() {
  document.getElementById('leftoverBtn').disabled = !(beforeFile && afterFile);
}

function resetUpload() {
  selectedFile = null; beforeFile = null; afterFile = null; currentAnalysis = null;
  fileInput.value = '';
  document.getElementById('beforeInput').value = '';
  document.getElementById('afterInput').value = '';
  document.getElementById('beforePreview').style.display = 'none';
  document.getElementById('afterPreview').style.display = 'none';
  document.getElementById('beforePlaceholder').style.display = 'block';
  document.getElementById('afterPlaceholder').style.display = 'block';
  if (currentMode === 'normal') {
    uploadArea.style.display = 'block';
    document.getElementById('leftoverUpload').style.display = 'none';
  } else {
    uploadArea.style.display = 'none';
    document.getElementById('leftoverUpload').style.display = 'block';
  }
  document.getElementById('previewSection').style.display = 'none';
  document.getElementById('resultSection').style.display = 'none';
  document.getElementById('errorBox').style.display = 'none';
  document.getElementById('loading').style.display = 'none';
  document.getElementById('leftoverBtn').disabled = true;
}

// ── 일반 분석 API ──
async function analyzeFood() {
  const apiKey = document.getElementById('apiKey').value.trim();
  if (!selectedFile) return alert('사진을 먼저 선택해주세요.');

  document.getElementById('previewSection').style.display = 'none';
  document.getElementById('loading').style.display = 'block';
  document.getElementById('errorBox').style.display = 'none';
  document.getElementById('resultSection').style.display = 'none';

  const formData = new FormData();
  formData.append('image', selectedFile);
  formData.append('api_key', apiKey);

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
    currentAnalysis = data;
    renderResult(data);
  } catch (err) {
    document.getElementById('loading').style.display = 'none';
    document.getElementById('errorBox').innerHTML = '<strong>네트워크 오류:</strong> ' + err.message;
    document.getElementById('errorBox').style.display = 'block';
    document.getElementById('previewSection').style.display = 'block';
  }
}

// ── 남은 음식 비교 API ──
async function analyzeLeftover() {
  const apiKey = document.getElementById('apiKey').value.trim();
  if (!beforeFile || !afterFile) return alert('전/후 사진 2장을 모두 선택해주세요.');

  document.getElementById('leftoverUpload').style.display = 'none';
  document.getElementById('loading').style.display = 'block';
  document.getElementById('errorBox').style.display = 'none';

  const formData = new FormData();
  formData.append('before', beforeFile);
  formData.append('after', afterFile);
  formData.append('api_key', apiKey);

  try {
    const resp = await fetch('/analyze-leftover', { method: 'POST', body: formData });
    const data = await resp.json();
    document.getElementById('loading').style.display = 'none';

    if (data.error) {
      document.getElementById('errorBox').innerHTML = '<strong>오류:</strong> ' + data.error;
      document.getElementById('errorBox').style.display = 'block';
      document.getElementById('leftoverUpload').style.display = 'block';
      return;
    }
    currentAnalysis = data;
    renderResult(data, true);
  } catch (err) {
    document.getElementById('loading').style.display = 'none';
    document.getElementById('errorBox').innerHTML = '<strong>네트워크 오류:</strong> ' + err.message;
    document.getElementById('errorBox').style.display = 'block';
    document.getElementById('leftoverUpload').style.display = 'block';
  }
}

// ── 나눠먹기: 인원수 조절 ──
let sharingCount = 1;

function adjustSharing(delta) {
  sharingCount = Math.max(1, Math.min(20, sharingCount + delta));
  document.getElementById('sharingNum').textContent = sharingCount;
  if (currentAnalysis) recalcSummary();
}

function recalcSummary() {
  if (!currentAnalysis) return;
  const foods = currentAnalysis.foods || [];
  const div = sharingCount;
  let tc=0, tp=0, tca=0, tf=0;
  foods.forEach(f => {
    tc += (f.calories_kcal||0)/div;
    tp += (f.protein_g||0)/div;
    tca += (f.carbs_g||0)/div;
    tf += (f.fat_g||0)/div;
  });
  const el = document.getElementById('sharingSummary');
  if (el) {
    el.innerHTML = sharingCount > 1
      ? `<span style="color:#6ee7b7">${sharingCount}명</span>이서 나눠 먹기: 1인당 <strong>${Math.round(tc)} kcal</strong> · 단백질 ${Math.round(tp)}g · 탄수화물 ${Math.round(tca)}g · 지방 ${Math.round(tf)}g`
      : '';
  }
  // 요약카드도 업데이트
  const sv = document.getElementById('sumCal'); if(sv) sv.textContent = Math.round(tc);
  const sp = document.getElementById('sumPro'); if(sp) sp.textContent = Math.round(tp)+'g';
  const sc = document.getElementById('sumCarb'); if(sc) sc.textContent = Math.round(tca)+'g';
  const sfat = document.getElementById('sumFat'); if(sfat) sfat.textContent = Math.round(tf)+'g';
}

// ── 결과 렌더링 ──
function renderResult(data, isLeftover) {
  const foods = data.foods || [];
  const summary = data.meal_summary || {};
  sharingCount = 1;

  // 나눠먹기 섹션 (일반 모드만)
  let sharingHtml = '';
  if (!isLeftover) {
    sharingHtml = `
      <div class="sharing-section">
        <div class="sharing-row">
          <span class="sharing-label">함께 먹은 인원</span>
          <div class="sharing-controls">
            <button class="sharing-btn" onclick="adjustSharing(-1)">−</button>
            <span class="sharing-count" id="sharingNum">1</span>
            <button class="sharing-btn" onclick="adjustSharing(1)">+</button>
          </div>
        </div>
        <div id="sharingSummary" style="font-size:0.85em; color:#aaa; text-align:center"></div>
      </div>
    `;
  }

  // 음식 카드들
  let cardsHtml = sharingHtml;
  foods.forEach((food, i) => {
    const isDb = food.db_matched || food.source === 'DB_MATCHED';
    const conf = (food.confidence || 0);
    const confPct = Math.round(conf * 100);
    const confColor = conf >= 0.8 ? '#6ee7b7' : conf >= 0.5 ? '#fbbf24' : '#f87171';
    const eatenPct = food.eaten_pct || 100;

    // 남은 음식 바
    let eatenBarHtml = '';
    if (isLeftover && food.eaten_pct !== undefined) {
      eatenBarHtml = `
        <div style="font-size:0.8em; color:#6ee7b7; margin-bottom:4px">
          섭취량: ${eatenPct}% ${food.leftover_note ? '· ' + food.leftover_note : ''}
        </div>
        <div class="eaten-bar">
          <div class="eaten-fill" style="width:${eatenPct}%"></div>
        </div>
      `;
    }

    cardsHtml += `
      <div class="food-card">
        <div class="food-title">
          <div>
            <span class="food-name">${food.name_ko || '?'}</span>
            <span class="food-name-en">${food.name_en || ''}</span>
          </div>
          <span class="source-badge ${isDb ? 'source-db' : 'source-ai'}">
            ${isDb ? 'DB 검증' : 'AI 추정'}
          </span>
        </div>
        <div class="confidence-bar">
          <div class="confidence-fill" style="width:${confPct}%; background:${confColor}"></div>
        </div>
        <div style="font-size:0.8em; color:#888; margin-bottom:10px; margin-top:-8px">
          확신도 ${confPct}% · 약 ${food.estimated_serving_g || '?'}g
        </div>
        ${eatenBarHtml}
        <div class="nutrition-grid">
          <div class="nutrition-item">
            <div class="nutrition-value">${food.calories_kcal || 0}</div>
            <div class="nutrition-label">칼로리 kcal</div>
          </div>
          <div class="nutrition-item">
            <div class="nutrition-value" style="color:#60a5fa">${food.protein_g || 0}g</div>
            <div class="nutrition-label">단백질</div>
          </div>
          <div class="nutrition-item">
            <div class="nutrition-value" style="color:#fbbf24">${food.carbs_g || 0}g</div>
            <div class="nutrition-label">탄수화물</div>
          </div>
          <div class="nutrition-item">
            <div class="nutrition-value" style="color:#f87171">${food.fat_g || 0}g</div>
            <div class="nutrition-label">지방</div>
          </div>
        </div>
      </div>
    `;
  });
  document.getElementById('foodCards').innerHTML = cardsHtml;

  // 요약 카드
  const score = summary.health_score || 0;
  const scoreColor = score >= 7 ? '#6ee7b7' : score >= 5 ? '#fbbf24' : '#f87171';

  document.getElementById('summaryCard').innerHTML = `
    <div class="summary-card">
      <div class="summary-title">${isLeftover ? '실제 섭취 요약' : '식사 요약'}</div>
      <div class="summary-stats">
        <div class="stat-item">
          <div class="stat-value" id="sumCal">${summary.total_calories || 0}</div>
          <div class="stat-label">총 칼로리 (kcal)</div>
        </div>
        <div class="stat-item">
          <div class="stat-value" style="color:#60a5fa" id="sumPro">${summary.total_protein || 0}g</div>
          <div class="stat-label">단백질</div>
        </div>
        <div class="stat-item">
          <div class="stat-value" style="color:#fbbf24" id="sumCarb">${summary.total_carbs || 0}g</div>
          <div class="stat-label">탄수화물</div>
        </div>
        <div class="stat-item">
          <div class="stat-value" style="color:#f87171" id="sumFat">${summary.total_fat || 0}g</div>
          <div class="stat-label">지방</div>
        </div>
      </div>
      <div class="health-score">
        <div class="score-circle" style="background:${scoreColor}33; border: 2px solid ${scoreColor}">
          <span style="color:${scoreColor}">${score}</span>
        </div>
        <div>
          <div style="font-weight:600; margin-bottom:4px">건강 점수 ${score}/10</div>
          <div style="font-size:0.85em; color:#888">
            ${summary.meal_type ? summary.meal_type + ' 식사' : ''}
          </div>
        </div>
      </div>
      <div class="comment">${summary.one_line_comment || ''}</div>
    </div>
  `;

  document.getElementById('resultSection').style.display = 'block';
  document.getElementById('analyzeBtn').disabled = false;
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
                self._json_response(200, {"error": f"OpenAI API 에러 ({e.code}): {body}"})
                return

            content = result["choices"][0]["message"]["content"]
            analysis = _parse_ai_json(content)
            if analysis is None:
                self._json_response(200, {"error": "AI 응답 파싱 실패", "raw": content[:300]})
                return

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
