/**
 * NutriLens — "이상해요" 신고 수집 Apps Script 웹앱
 * ──────────────────────────────────────────────────
 *
 * 역할:
 *   1) POST 요청으로 들어온 신고 데이터를 구글시트에 기록
 *   2) 같은 음식에 대해 신고가 3건 이상 누적되면 사장에게 이메일 알림
 *
 * 셋업 방법은 아래 "셋업 가이드" 참고
 */

// ── 설정 ──
const SECRET = 'CHANGE_ME_TO_RANDOM_STRING';      // .env의 REPORT_WEBHOOK_SECRET과 동일하게
const SHEET_NAME = 'Reports';                       // 시트 탭 이름
const ALERT_EMAIL = 'saeronjhk@gmail.com';          // 알림 받을 이메일
const ALERT_THRESHOLD = 3;                          // 같은 음식 N회 이상이면 알림

// ── 웹앱 엔트리포인트 ──
function doPost(e) {
  try {
    const payload = JSON.parse(e.postData.contents);

    // 시크릿 검증
    if (payload.secret !== SECRET) {
      return ContentService.createTextOutput(JSON.stringify({ error: 'unauthorized' }))
        .setMimeType(ContentService.MimeType.JSON);
    }

    const data = payload.data;
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    let sheet = ss.getSheetByName(SHEET_NAME);

    // 시트가 없으면 생성 + 헤더
    if (!sheet) {
      sheet = ss.insertSheet(SHEET_NAME);
      sheet.appendRow([
        '시간', '닉네임', '음식명',
        '칼로리', '단백질(g)', '탄수화물(g)', '지방(g)', '1인분(g)', '출처',
        '신고 내용', '처리 상태'
      ]);
      sheet.getRange(1, 1, 1, 11).setFontWeight('bold');
      sheet.setFrozenRows(1);
    }

    // 행 추가
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
      '미처리'                // 처리 상태 기본값
    ]);

    // ── 같은 음식 신고 횟수 집계 ──
    const foodName = (data.food_name || '').trim();
    if (foodName) {
      const allData = sheet.getDataRange().getValues();
      let count = 0;
      for (let i = 1; i < allData.length; i++) {   // 헤더 제외
        if (String(allData[i][2]).trim() === foodName) count++;
      }

      // 정확히 threshold에 도달했을 때만 알림 (중복 발송 방지)
      if (count === ALERT_THRESHOLD) {
        sendAlertEmail(foodName, count, data);
      }
    }

    return ContentService.createTextOutput(JSON.stringify({ ok: true }))
      .setMimeType(ContentService.MimeType.JSON);

  } catch (err) {
    return ContentService.createTextOutput(JSON.stringify({ error: err.message }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

// ── 이메일 알림 ──
function sendAlertEmail(foodName, count, latestData) {
  const subject = '[NutriLens] 🚨 반복 신고: ' + foodName + ' (' + count + '회)';
  const body = '같은 음식에 대해 ' + count + '회 이상 "이상해요" 신고가 접수되었습니다.\n\n'
    + '━━━━━━━━━━━━━━━━━━━━\n'
    + '음식명: ' + foodName + '\n'
    + '누적 신고: ' + count + '건\n'
    + '━━━━━━━━━━━━━━━━━━━━\n\n'
    + '최근 신고 내용:\n'
    + '"' + (latestData.report_text || '') + '"\n\n'
    + '현재 DB 데이터:\n'
    + '  칼로리: ' + (latestData.calories || '?') + ' kcal\n'
    + '  단백질: ' + (latestData.protein || '?') + 'g\n'
    + '  탄수화물: ' + (latestData.carbs || '?') + 'g\n'
    + '  지방: ' + (latestData.fat || '?') + 'g\n'
    + '  1인분: ' + (latestData.serving_g || '?') + 'g\n'
    + '  출처: ' + (latestData.source || '?') + '\n\n'
    + '→ 구글시트에서 확인하고 Claude에게 수정 지시해주세요.';

  MailApp.sendEmail(ALERT_EMAIL, subject, body);
  Logger.log('알림 이메일 발송: ' + foodName + ' (' + count + '회)');
}

// ── GET 요청 (헬스체크) ──
function doGet(e) {
  return ContentService.createTextOutput(JSON.stringify({
    status: 'ok',
    service: 'NutriLens Report Webhook'
  })).setMimeType(ContentService.MimeType.JSON);
}


/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   셋업 가이드
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

   1. 구글 드라이브에서 새 구글시트를 만든다
      이름: "NutriLens 신고 관리"

   2. 메뉴 → 확장 프로그램 → Apps Script 클릭

   3. 기본 코드를 전부 지우고, 이 파일 내용을 통째로 붙여넣기

   4. 위의 SECRET 값을 아무도 모르는 랜덤 문자열로 바꾸기
      예: 'nutrilens_report_a8f3k2x9m1'

   5. 메뉴 → 배포 → 새 배포
      - 유형: "웹 앱"
      - 실행 사용자: "나"
      - 액세스 권한: "모든 사용자"
      → 배포 클릭 → URL 복사

   6. NutriLens/.env 파일에 추가:
      REPORT_WEBHOOK_URL=복사한_URL
      REPORT_WEBHOOK_SECRET=위에서_설정한_SECRET값

   7. 서버 재시작 → 끝!

   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
