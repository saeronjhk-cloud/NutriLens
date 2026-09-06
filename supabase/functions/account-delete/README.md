# account-delete — 본인 계정 삭제 (개인정보보호법 삭제권)

> L3(Cursor/Supabase) 트랙. **Lovable 트랙과 분리.** 운영 대시보드와 동일한 2-AI 보안 리뷰 후 배포.
> 사양 IP 원본: `IP/통합앱_P1/08_Lovable_앱셸_디자인시스템_UI폴리시_v1.md` (§5.3 / §6 Step E)
> 스키마 근거: `IP/통합앱_P1/02_Supabase_스키마_v1.sql` §8(삭제권 범위)

## 무엇을 하나
로그인한 **본인** 계정과 전 개인정보 도메인을 삭제한다.
- Storage `meal-photos/{uid}/` 전체(§8-(2), cascade 대상 아님 → 명시 삭제)
- 유저 소유 테이블 10종: `app_event, affiliate_click, coaching_memory, weekly_report, meal_log, product_scan, survey_health, user_goal, user_consent, profiles`
- `auth.users`(deleteUser) — 모든 FK가 `on delete cascade`라 DB 행은 자동 정리되지만, **명시 삭제 + 0행 검증**으로 계약을 보장하고 cascade는 백스톱으로 둔다.
- `data_domain_policy`는 거버넌스 참조(유저 데이터 아님) → 제외.

## 보안 설계 (운영 대시보드 기준 정합)
- 인증: 요청자 JWT(getUser) → `email_confirmed_at` 필수(미인증 도용 차단) → **본인 uid만** 삭제.
- 오작동 방지: body `{"confirm":"DELETE"}` 필수.
- CORS: 운영 도메인만(`nutrition-diary-shell.lovable.app`), `*` 금지.
- 감사 로그: `account_deleted` 이벤트에 **user_id 원본 미기록** — SHA-256 해시 + 타임스탬프만.
- service_role 사용(클라 직접 삭제 금지 = RLS·권한 회피 불가).

## 멱등·재시도 안전
- Storage remove·테이블 delete 모두 재실행 성공(멱등).
- 5단계에서 0행 검증 실패 시 `409 DELETE_INCOMPLETE`(남은 목록 포함) → 사용자가 재요청 가능.
- `deleteUser`는 종단 작업. 이후 재호출은 토큰 무효 → `401`(계정 이미 삭제, 정상).

## 배포 (Cursor/Supabase)
```bash
supabase functions deploy account-delete
```
- Secrets: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` 자동 주입. 추가 시크릿 없음.
- verify_jwt: 함수 내부에서 getUser로 직접 검증하므로 platform verify_jwt는 OFF 가능(무인증 → 자체 401).

## Eval (보안 — 배포 전 100% 통과)
| # | 케이스 | 기대 |
|---|---|---|
| 1 | 무인증 POST | 401 UNAUTHORIZED |
| 2 | 미인증 이메일 계정 JWT | 403 EMAIL_NOT_CONFIRMED |
| 3 | confirm 누락/오타 | 400 CONFIRM_REQUIRED |
| 4 | 본인 JWT + confirm=DELETE (스테이징) | 200, 각 테이블 count=0 + storage prefix empty |
| 5 | 동일 계정 재호출(토큰 유효 상태 가정) | 멱등 성공(또는 이미 삭제 시 401) |
| 6 | 삭제 중 강제 실패 주입 후 재호출 | 재시도로 완결(부분삭제 잔존 0) |
| 7 | 타 유저 데이터 | 절대 미삭제(본인 uid로만 delete) |

## 검증 SQL (스테이징, 삭제 후)
```sql
select
 (select count(*) from meal_log       where user_id = :uid) meal,
 (select count(*) from app_event       where user_id = :uid) evt,
 (select count(*) from user_consent    where user_id = :uid) consent,
 (select count(*) from weekly_report   where user_id = :uid) rpt,
 (select count(*) from profiles        where id      = :uid) prof;
-- 전부 0 기대. storage는 대시보드/list로 prefix empty 확인.
```

## 미해결(스키마 §8 이월 — 코드 밖)
- Edge/Railway 요청 로그: payload 미로깅 원칙으로 최소화, 보존기간 후 파기.
- Google Sheets 레거시 백업: 통합앱 P1에서 **차단**(민감정보 이중보관 금지).
- 고아 사진 정리 크론(X3): 참조 없는 Storage 객체 주기 파기 — 별도.
