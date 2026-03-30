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

    # JSON 추출
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0]
    elif "```" in content:
        content = content.split("```")[1].split("```")[0]

    try:
        return json.loads(content.strip())
    except json.JSONDecodeError:
        return {"error": "AI 응답 파싱 실패", "raw": content}


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

  /* 반응형 */
  @media (max-width: 600px) {
    .nutrition-grid { grid-template-columns: repeat(2, 1fr); }
    .summary-stats { grid-template-columns: repeat(2, 1fr); }
    .header h1 { font-size: 1.5em; }
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

  <!-- 업로드 영역 -->
  <div class="upload-area" id="uploadArea">
    <div class="icon">📸</div>
    <div class="text">음식 사진을 여기에 끌어다 놓거나 클릭하세요</div>
    <div class="subtext">JPG, PNG, WebP 지원 · 최대 10MB</div>
    <input type="file" id="fileInput" accept="image/*" style="display:none" />
  </div>

  <!-- 미리보기 -->
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
// ── 파일 선택 & 드래그앤드롭 ──
const uploadArea = document.getElementById('uploadArea');
const fileInput = document.getElementById('fileInput');
let selectedFile = null;

uploadArea.addEventListener('click', () => fileInput.click());
fileInput.addEventListener('change', (e) => handleFile(e.target.files[0]));

uploadArea.addEventListener('dragover', (e) => {
  e.preventDefault();
  uploadArea.classList.add('dragover');
});
uploadArea.addEventListener('dragleave', () => uploadArea.classList.remove('dragover'));
uploadArea.addEventListener('drop', (e) => {
  e.preventDefault();
  uploadArea.classList.remove('dragover');
  if (e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0]);
});

function handleFile(file) {
  if (!file || !file.type.startsWith('image/')) {
    alert('이미지 파일만 업로드할 수 있습니다.');
    return;
  }
  if (file.size > 10 * 1024 * 1024) {
    alert('파일 크기는 10MB 이하여야 합니다.');
    return;
  }
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

function resetUpload() {
  selectedFile = null;
  fileInput.value = '';
  uploadArea.style.display = 'block';
  document.getElementById('previewSection').style.display = 'none';
  document.getElementById('resultSection').style.display = 'none';
  document.getElementById('errorBox').style.display = 'none';
  document.getElementById('loading').style.display = 'none';
}

// ── API 호출 ──
async function analyzeFood() {
  const apiKey = document.getElementById('apiKey').value.trim();
  if (!selectedFile) {
    alert('사진을 먼저 선택해주세요.');
    return;
  }

  // UI 전환
  document.getElementById('previewSection').style.display = 'none';
  document.getElementById('loading').style.display = 'block';
  document.getElementById('errorBox').style.display = 'none';
  document.getElementById('resultSection').style.display = 'none';
  document.getElementById('analyzeBtn').disabled = true;

  const formData = new FormData();
  formData.append('image', selectedFile);
  formData.append('api_key', apiKey);

  try {
    const resp = await fetch('/analyze', { method: 'POST', body: formData });
    const data = await resp.json();

    document.getElementById('loading').style.display = 'none';

    if (data.error) {
      document.getElementById('errorBox').innerHTML = '<strong>오류:</strong> ' + data.error;
      if (data.help) document.getElementById('errorBox').innerHTML += '<br><br>' + data.help.replace(/\\n/g, '<br>');
      document.getElementById('errorBox').style.display = 'block';
      document.getElementById('previewSection').style.display = 'block';
      document.getElementById('analyzeBtn').disabled = false;
      return;
    }

    renderResult(data);

  } catch (err) {
    document.getElementById('loading').style.display = 'none';
    document.getElementById('errorBox').innerHTML = '<strong>네트워크 오류:</strong> ' + err.message;
    document.getElementById('errorBox').style.display = 'block';
    document.getElementById('previewSection').style.display = 'block';
    document.getElementById('analyzeBtn').disabled = false;
  }
}

// ── 결과 렌더링 ──
function renderResult(data) {
  const foods = data.foods || [];
  const summary = data.meal_summary || {};

  // 음식 카드들
  let cardsHtml = '';
  foods.forEach((food, i) => {
    const isDb = food.db_matched || food.source === 'DB_MATCHED';
    const conf = (food.confidence || 0);
    const confPct = Math.round(conf * 100);
    const confColor = conf >= 0.8 ? '#6ee7b7' : conf >= 0.5 ? '#fbbf24' : '#f87171';

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
      <div class="summary-title">식사 요약</div>
      <div class="summary-stats">
        <div class="stat-item">
          <div class="stat-value">${summary.total_calories || 0}</div>
          <div class="stat-label">총 칼로리 (kcal)</div>
        </div>
        <div class="stat-item">
          <div class="stat-value" style="color:#60a5fa">${summary.total_protein || 0}g</div>
          <div class="stat-label">단백질</div>
        </div>
        <div class="stat-item">
          <div class="stat-value" style="color:#fbbf24">${summary.total_carbs || 0}g</div>
          <div class="stat-label">탄수화물</div>
        </div>
        <div class="stat-item">
          <div class="stat-value" style="color:#f87171">${summary.total_fat || 0}g</div>
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

    def do_POST(self):
        """음식 사진 분석 API"""
        if self.path != '/analyze':
            self.send_response(404)
            self.end_headers()
            return

        try:
            # multipart form data 파싱
            content_type = self.headers.get('Content-Type', '')
            if 'multipart/form-data' not in content_type:
                self._json_response(400, {"error": "multipart/form-data 필요"})
                return

            # multipart 파싱 (cgi 모듈 미사용 — Python 3.13 호환)
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)

            # boundary 추출
            boundary_match = re.search(r'boundary=([^\s;]+)', content_type)
            if not boundary_match:
                self._json_response(400, {"error": "boundary를 찾을 수 없습니다."})
                return
            boundary = boundary_match.group(1).encode()

            # 파트 분리
            parts = body.split(b'--' + boundary)
            fields = {}   # text fields
            file_data = None
            file_name = 'image.jpg'

            for part in parts:
                if b'Content-Disposition' not in part:
                    continue
                # 헤더와 바디 분리
                header_end = part.find(b'\r\n\r\n')
                if header_end == -1:
                    continue
                header_section = part[:header_end].decode('utf-8', errors='replace')
                body_section = part[header_end + 4:]
                if body_section.endswith(b'\r\n'):
                    body_section = body_section[:-2]

                # 필드명 추출
                name_match = re.search(r'name="([^"]+)"', header_section)
                if not name_match:
                    continue
                field_name = name_match.group(1)

                # 파일인지 텍스트인지 판별
                filename_match = re.search(r'filename="([^"]*)"', header_section)
                if filename_match:
                    file_data = body_section
                    file_name = filename_match.group(1) or 'image.jpg'
                else:
                    fields[field_name] = body_section.decode('utf-8', errors='replace')

            # API 키
            api_key = fields.get('api_key', '').strip()

            if not api_key:
                api_key = os.environ.get('OPENAI_API_KEY', '')

            if not api_key:
                self._json_response(400, {
                    "error": "API 키가 없습니다.",
                    "help": "OpenAI API 키를 입력하거나 .env 파일에 OPENAI_API_KEY를 설정하세요."
                })
                return

            # 이미지 데이터
            if not file_data:
                self._json_response(400, {"error": "이미지가 업로드되지 않았습니다."})
                return

            image_data = file_data
            filename = file_name

            # MIME 타입 결정
            ext = Path(filename).suffix.lower()
            media_type = {
                '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                '.png': 'image/png', '.gif': 'image/gif',
                '.webp': 'image/webp',
            }.get(ext, 'image/jpeg')

            # base64 인코딩
            base64_image = base64.b64encode(image_data).decode('utf-8')

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
