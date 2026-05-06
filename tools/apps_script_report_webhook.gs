/**
 * NutriLens — 신고 + 피드백 + 식사·목표 백업 Apps Script 웹앱
 * ──────────────────────────────────────────────────────────────
 *
 * 역할:
 *   POST 요청의 type 필드를 보고 4가지로 분기:
 *      - type === 'report'   → "이상해요" 신고를 'Reports' 탭에 기록 + 3회 누적 시 이메일 알림
 *      - type === 'feedback' → 음식명 수정 피드백을 'Feedback' 탭에 기록 (P0-4)
 *      - type === 'meal'     → 식사 기록을 'Meals' 탭에 영구 백업 (P0-3)
 *      - type === 'goal'     → 일일 목표 변경을 'Goals' 탭에 영구 백업 (P0-3)
 *      - type 없음           → 'report'로 간주 (이전 버전 호환)
 *
 * P0-3 (2026-05-05):
 *   localStorage 단일 저장소 위험(캐시 삭제·기기 변경 = 영구 손실)을 해소하기 위해
 *   사용자 식사·목표를 같은 웹훅으로 받아 시트에 영구 백업.
 *   사용자 식별: 익명 UUID (uid) — 첫 접속 시 클라이언트가 자동 생성.
 */

// ── 설정 ──
const SECRET = 'nutrilens_report_2026_x9k3m';
const REPORT_SHEET_NAME = 'Reports';
const FEEDBACK_SHEET_NAME = 'Feedback';
const MEAL_SHEET_NAME = 'Meals';      // P0-3 신설
const GOAL_SHEET_NAME = 'Goals';      // P0-3 신설
const ALERT_EMAIL = 'saeronjhk@gmail.com';
const ALERT_THRESHOLD = 3;

// ── 웹앱 엔트리포인트 ──
function doPost(e) {
  try {
    const payload = JSON.parse(e.postData.contents);

    if (payload.secret !== SECRET) {
      return jsonResponse({ error: 'unauthorized' });
    }

    const type = payload.type || 'report';

    if (type === 'feedback') return handleFeedback(payload.data || {});
    if (type === 'meal')     return handleMeal(payload.data || {});
    if (type === 'goal')     return handleGoal(payload.data || {});
    return handleReport(payload.data || {});

  } catch (err) {
    return jsonResponse({ error: err.message });
  }
}

// ── 신고 처리 (Reports 탭) ──
function handleReport(data) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sheet = ss.getSheetByName(REPORT_SHEET_NAME);

  if (!sheet) {
    sheet = ss.insertSheet(REPORT_SHEET_NAME);
    sheet.appendRow([
      '시간', '닉네임', '음식명',
      '칼로리', '단백질(g)', '탄수화물(g)', '지방(g)', '1인분(g)', '출처',
      '신고 내용', '처리 상태'
    ]);
    sheet.getRange(1, 1, 1, 11).setFontWeight('bold');
    sheet.setFrozenRows(1);
  }

  sheet.appendRow([
    data.timestamp || new Date().toISOString(),
    data.nickname || '',
    data.food_name || '',
    data.calories || '',
    data.protein || '',
    data.carbs || '',
    data.fat || '',
    data.serving_g || '',
    data.source || '',
    data.report_text || '',
    '미처리'
  ]);

  const foodName = (data.food_name || '').trim();
  if (foodName) {
    const allData = sheet.getDataRange().getValues();
    let count = 0;
    for (let i = 1; i < allData.length; i++) {
      if (String(allData[i][2]).trim() === foodName) count++;
    }
    if (count === ALERT_THRESHOLD) {
      sendReportAlertEmail(foodName, count, data);
    }
  }

  return jsonResponse({ ok: true, type: 'report' });
}

// ── 피드백 처리 (Feedback 탭) ──
function handleFeedback(data) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sheet = ss.getSheetByName(FEEDBACK_SHEET_NAME);

  if (!sheet) {
    sheet = ss.insertSheet(FEEDBACK_SHEET_NAME);
    sheet.appendRow([
      '시간', 'AI 인식', '사용자 수정', '누적 횟수', '닉네임'
    ]);
    sheet.getRange(1, 1, 1, 5).setFontWeight('bold');
    sheet.setFrozenRows(1);
  }

  sheet.appendRow([
    data.timestamp || new Date().toISOString(),
    data.original_name || '',
    data.corrected_name || '',
    data.count || 1,
    data.nickname || ''
  ]);

  const count = data.count || 1;
  if (count === ALERT_THRESHOLD) {
    sendFeedbackAlertEmail(data.original_name, data.corrected_name, count);
  }

  return jsonResponse({ ok: true, type: 'feedback' });
}

// ── 식사 기록 백업 (Meals 탭) — P0-3 신설 ──
function handleMeal(data) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sheet = ss.getSheetByName(MEAL_SHEET_NAME);

  if (!sheet) {
    sheet = ss.insertSheet(MEAL_SHEET_NAME);
    sheet.appendRow([
      '시간', 'UID', '닉네임', '날짜', '식사시간', '식사구분',
      '음식 요약', '음식수',
      '총 칼로리', '단백질(g)', '탄수화물(g)', '지방(g)',
      '나트륨(mg)', '당류(g)', '식이섬유(g)',
      '레코드 ID'
    ]);
    sheet.getRange(1, 1, 1, 16).setFontWeight('bold');
    sheet.setFrozenRows(1);
  }

  sheet.appendRow([
    data.timestamp || new Date().toISOString(),
    data.uid || '',
    data.nickname || '',
    data.date || '',
    data.time || '',
    data.meal_type || '',
    data.foods_summary || '',
    data.food_count || 0,
    data.total_calories || 0,
    data.total_protein || 0,
    data.total_carbs || 0,
    data.total_fat || 0,
    data.total_sodium || 0,
    data.total_sugar || 0,
    data.total_fiber || 0,
    data.record_id || ''
  ]);

  return jsonResponse({ ok: true, type: 'meal' });
}

// ── 목표 변경 백업 (Goals 탭) — P0-3 신설 ──
function handleGoal(data) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sheet = ss.getSheetByName(GOAL_SHEET_NAME);

  if (!sheet) {
    sheet = ss.insertSheet(GOAL_SHEET_NAME);
    sheet.appendRow([
      '시간', 'UID', '닉네임', '칼로리 목표', '단백질 목표(g)'
    ]);
    sheet.getRange(1, 1, 1, 5).setFontWeight('bold');
    sheet.setFrozenRows(1);
  }

  sheet.appendRow([
    data.timestamp || new Date().toISOString(),
    data.uid || '',
    data.nickname || '',
    data.goal_calories || 0,
    data.goal_protein || 0
  ]);

  return jsonResponse({ ok: true, type: 'goal' });
}

// ── 신고 이메일 알림 ──
function sendReportAlertEmail(foodName, count, latestData) {
  const subject = '[NutriLens] 반복 신고: ' + foodName + ' (' + count + '회)';
  const body = '같은 음식에 대해 ' + count + '회 이상 "이상해요" 신고가 접수되었습니다.\n\n'
    + '음식명: ' + foodName + '\n'
    + '누적 신고: ' + count + '건\n\n'
    + '최근 신고 내용: "' + (latestData.report_text || '') + '"\n\n'
    + '현재 DB 데이터:\n'
    + '  칼로리: ' + (latestData.calories || '?') + ' kcal\n'
    + '  단백질: ' + (latestData.protein || '?') + 'g\n'
    + '  탄수화물: ' + (latestData.carbs || '?') + 'g\n'
    + '  지방: ' + (latestData.fat || '?') + 'g\n'
    + '  1인분: ' + (latestData.serving_g || '?') + 'g\n'
    + '  출처: ' + (latestData.source || '?') + '\n\n'
    + '구글시트 Reports 탭에서 확인하세요.';

  MailApp.sendEmail(ALERT_EMAIL, subject, body);
  Logger.log('신고 알림 발송: ' + foodName + ' (' + count + '회)');
}

// ── 피드백 이메일 알림 ──
function sendFeedbackAlertEmail(originalName, correctedName, count) {
  const subject = '[NutriLens] 반복 수정 패턴: ' + originalName + ' -> ' + correctedName + ' (' + count + '회)';
  const body = '같은 음식 수정 패턴이 ' + count + '회 누적되었습니다.\n\n'
    + 'AI가 "' + originalName + '"으로 인식한 음식을\n'
    + '사용자가 "' + correctedName + '"으로 ' + count + '회 수정했습니다.\n\n'
    + '검토 사항:\n'
    + '  1) CORE_FOODS에 별칭 추가 검토\n'
    + '  2) 또는 AI 프롬프트 힌트로 영구 등록 검토\n\n'
    + '구글시트 Feedback 탭에서 전체 이력 확인 가능.';

  MailApp.sendEmail(ALERT_EMAIL, subject, body);
  Logger.log('피드백 알림 발송: ' + originalName + '->' + correctedName + ' (' + count + '회)');
}

// ── 공통 JSON 응답 헬퍼 ──
function jsonResponse(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

// ── GET 요청 (헬스체크) ──
function doGet(e) {
  return jsonResponse({
    status: 'ok',
    service: 'NutriLens Webhook (Report+Feedback+Meal+Goal)',
    handlers: ['report', 'feedback', 'meal', 'goal']
  });
}


/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   [P0-3 업데이트 가이드]

   1. 이 파일을 Apps Script 편집기에 통째로 붙여넣어 기존 코드 교체
   2. SECRET 값(17번 줄)은 기존 값 그대로 유지
   3. 메뉴 → 배포 → 배포 관리
      - 기존 활성 배포의 ✏ 편집 버튼 클릭
      - 버전: "새 버전" 선택
      - 배포 (URL은 자동으로 유지됨)
   4. Railway 환경변수: 기존 그대로 (변경 없음)
   5. 끝! Reports + Feedback + Meals + Goals 네 탭이 첫 데이터 들어올 때 자동 생성됨.

   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
