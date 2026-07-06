-- =====================================================================
-- 28_정찬_잔반_스키마_마이그레이션_v2.1.1_증분.sql
-- v2.1 위 증분(2차 리뷰 B2 멱등충돌 + P1-a 원본 음식 스냅샷). 근거: 27_..._v2.1.1_LOCK_FINAL.md
-- 전제: 28_..._v2.1.sql 적용됨(meal_log_adjustment 존재). 미적용이면 v2.1 먼저.
-- ★ 적용 전 nutriformula-app(ndxgxxrklkltizrnfkcx) 검토.
-- =====================================================================

begin;

-- B2/P1-a: 멱등 충돌 판정용 request_hash·endpoint + 음식별 원본 스냅샷
alter table public.meal_log_adjustment
  add column if not exists endpoint text,               -- 예: 'analyze.leftover'
  add column if not exists request_hash text,           -- body 정규화 해시(멱등 충돌 대조)
  add column if not exists original_foods_snapshot jsonb; -- P1-a: food_item_id별 원본(재계산 근거)

-- 멱등: (user_id, idempotency_key) 유니크는 v2.1의 uq_adj_idem 유지.
--   · 동일 key + 동일 request_hash → 애플리케이션이 기존 adjusted 반환(이력 중복 0)
--   · 동일 key + 다른 request_hash → 409 IDEMPOTENCY_KEY_REUSE_MISMATCH (Edge 판정)
-- (유니크 인덱스만으로는 '반환 vs 409'를 구분 못하므로 request_hash 대조는 Edge 로직 필수)
comment on column public.meal_log_adjustment.request_hash is
  '동일 idempotency_key 재사용 시 body 일치 검증. 불일치면 409 IDEMPOTENCY_KEY_REUSE_MISMATCH.';

commit;

-- 되돌리기:
-- begin;
--   alter table public.meal_log_adjustment
--     drop column if exists endpoint,
--     drop column if exists request_hash,
--     drop column if exists original_foods_snapshot;
-- commit;
