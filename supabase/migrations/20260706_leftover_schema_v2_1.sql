-- =====================================================================
-- 28_정찬_잔반_스키마_마이그레이션_v2.1.sql  (LOCK 후보)
-- v2 + 시니어 리뷰 P0/P1: 재조정=원본기준 이력(original_summary_snapshot·previous_adjusted_summary),
--   멱등(idempotency_key unique), 세션 leftover closed-only(session_food_snapshot).
-- 근거: 27_..._v2.1_LOCK.md. v2를 미적용했다면 이 v2.1 단독 적용.
-- ★ 적용 전 nutriformula-app(ndxgxxrklkltizrnfkcx) 검토.
-- =====================================================================

begin;

-- ---------------------------------------------------------------------
-- 1. 정찬 세션 (open 유일성 DB 강제 + closed_reason) — v2와 동일
-- ---------------------------------------------------------------------
create table if not exists public.meal_session (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references auth.users(id) on delete cascade,
  status        text not null default 'open' check (status in ('open','closed','abandoned')),
  closed_reason text check (closed_reason in
                  ('user_ended','auto_closed_by_new_session','auto_abandoned_timeout')),
  meal_slot     text check (meal_slot in ('breakfast','lunch','dinner','snack')),
  started_at    timestamptz not null default now(),
  ended_at      timestamptz,
  note          text,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);
create index if not exists idx_session_user_status on public.meal_session(user_id, status);
create unique index if not exists uq_session_one_open
  on public.meal_session(user_id) where status = 'open';   -- ★ open 1개 DB 강제
drop trigger if exists trg_session_updated on public.meal_session;
create trigger trg_session_updated before update on public.meal_session
  for each row execute function public.set_updated_at();

insert into public.data_domain_policy
  (domain,classification,required_consent,retention_days,
   can_be_used_for_ai,can_be_used_for_affiliate,can_be_used_for_b2b_aggregate,note)
values
  ('meal_session','health_inferred','privacy_basic',730,true,false,true,'정찬 세션(한 끼 묶음)'),
  ('meal_log_adjustment','health_inferred','privacy_basic',730,true,false,true,'식후 잔반 보정 이력')
on conflict (domain) do nothing;

-- ---------------------------------------------------------------------
-- 2. meal_log 확장 (source 유지 + leftover_method 분리) — v2와 동일
--    original_summary: 잔반 계산의 불변 기준(식전 canonical). 최초 저장 시 summary와 동일.
-- ---------------------------------------------------------------------
alter table public.meal_log
  add column if not exists meal_session_id uuid references public.meal_session(id) on delete set null,
  add column if not exists leftover_method text not null default 'none'
      check (leftover_method in ('none','slider','photo_ai','photo_ai_suggested')),
  add column if not exists eaten_ratio numeric not null default 1.0
      check (eaten_ratio >= 0 and eaten_ratio <= 1),
  add column if not exists original_summary jsonb,    -- ★불변 계산기준(식전). null=최초 미보정
  add column if not exists adjusted_summary jsonb,     -- 실섭취(보정 후, current)
  add column if not exists leftover_note text,
  add column if not exists leftover_confidence numeric
      check (leftover_confidence is null or (leftover_confidence >= 0 and leftover_confidence <= 1)),
  add column if not exists leftover_adjusted_at timestamptz,
  add column if not exists leftover_engine_version text;

alter table public.meal_log drop constraint if exists meal_log_source_check;
alter table public.meal_log
  add constraint meal_log_source_check
  check (source in ('photo','manual','edited','image_analysis','barcode','imported'));

create index if not exists idx_meal_session_link
  on public.meal_log(meal_session_id) where meal_session_id is not null;

-- ---------------------------------------------------------------------
-- 3. 잔반 보정 이력 (append-only) — v2.1: 원본기준·멱등·세션스냅샷
--    P0-1: 계산 입력은 original_summary_snapshot. previous/adjusted 모두 보존.
--    P1-4: (user_id, idempotency_key) unique → 중복 재시도 이력 0.
--    P0-4: 세션 leftover 시 session_food_snapshot 저장.
-- ---------------------------------------------------------------------
create table if not exists public.meal_log_adjustment (
  id                        uuid primary key default gen_random_uuid(),
  meal_log_id               uuid references public.meal_log(id) on delete cascade,
  meal_session_id           uuid references public.meal_session(id) on delete cascade,
  user_id                   uuid not null references auth.users(id) on delete cascade,
  adjustment_type           text not null default 'leftover' check (adjustment_type in ('leftover')),
  method                    text not null check (method in ('slider','photo_ai')),
  scope                     text not null default 'meal' check (scope in ('meal','session')),
  eaten_ratio               numeric not null check (eaten_ratio >= 0 and eaten_ratio <= 1),
  original_summary_snapshot jsonb not null,   -- ★불변 계산기준(누적보정 금지의 근거)
  previous_adjusted_summary jsonb,            -- 직전 보정값(감사용, 계산엔 미사용)
  adjusted_summary          jsonb not null,   -- 신규 보정값 = original × ratio
  session_food_snapshot     jsonb,            -- scope=session일 때 보정시점 세션 구성
  confidence                numeric check (confidence is null or (confidence >= 0 and confidence <= 1)),
  user_confirmed            boolean not null default false,
  idempotency_key           text not null,
  engine_version            text,
  created_at                timestamptz not null default now(),
  -- 대상은 meal 또는 session 중 하나
  constraint adj_target_one check (
    (meal_log_id is not null and meal_session_id is null) or
    (meal_log_id is null and meal_session_id is not null)
  )
);
create index if not exists idx_adj_meallog on public.meal_log_adjustment(meal_log_id, created_at desc);
create index if not exists idx_adj_session on public.meal_log_adjustment(meal_session_id, created_at desc);
-- ★ 멱등: 동일 유저·동일 키 재시도는 이력 1건만
create unique index if not exists uq_adj_idem
  on public.meal_log_adjustment(user_id, idempotency_key);

-- ---------------------------------------------------------------------
-- 4. RLS (meal_session·adjustment = meal_log 동형)
-- ---------------------------------------------------------------------
alter table public.meal_session        enable row level security;
alter table public.meal_log_adjustment enable row level security;

drop policy if exists session_select on public.meal_session;
drop policy if exists session_delete on public.meal_session;
drop policy if exists session_write  on public.meal_session;
drop policy if exists session_update on public.meal_session;
create policy session_select on public.meal_session for select using (auth.uid() = user_id);
create policy session_delete on public.meal_session for delete using (auth.uid() = user_id);
create policy session_write  on public.meal_session for insert
  with check (auth.uid() = user_id and public.has_consent(auth.uid(),'privacy_basic'));
create policy session_update on public.meal_session for update
  using (auth.uid() = user_id and public.has_consent(auth.uid(),'privacy_basic'));

drop policy if exists adj_select on public.meal_log_adjustment;
drop policy if exists adj_delete on public.meal_log_adjustment;
drop policy if exists adj_write  on public.meal_log_adjustment;
create policy adj_select on public.meal_log_adjustment for select using (auth.uid() = user_id);
create policy adj_delete on public.meal_log_adjustment for delete using (auth.uid() = user_id);
create policy adj_write  on public.meal_log_adjustment for insert
  with check (auth.uid() = user_id and public.has_consent(auth.uid(),'privacy_basic'));
-- append-only: update 정책 없음.

-- ---------------------------------------------------------------------
-- 5. 세션 합계 뷰 (내부합; 보정된 항목은 adjusted_summary, 없으면 summary)
-- ---------------------------------------------------------------------
create or replace view public.v_meal_session_totals as
select
  s.id as session_id, s.user_id, s.status, s.meal_slot, s.started_at, s.ended_at,
  count(m.id) as item_count,
  coalesce(sum((coalesce(m.adjusted_summary,m.summary)->>'total_calories_kcal')::numeric),0) as total_calories_kcal,
  coalesce(sum((coalesce(m.adjusted_summary,m.summary)->>'total_protein_g')::numeric),0)     as total_protein_g,
  coalesce(sum((coalesce(m.adjusted_summary,m.summary)->>'total_carbs_g')::numeric),0)        as total_carbs_g,
  coalesce(sum((coalesce(m.adjusted_summary,m.summary)->>'total_fat_g')::numeric),0)          as total_fat_g,
  coalesce(sum((coalesce(m.adjusted_summary,m.summary)->>'total_sodium_mg')::numeric),0)      as total_sodium_mg
from public.meal_session s
left join public.meal_log m on m.meal_session_id = s.id
group by s.id, s.user_id, s.status, s.meal_slot, s.started_at, s.ended_at;

-- ---------------------------------------------------------------------
-- 6. Edge 강제 규칙(SQL로 강제 불가 → 애플리케이션/Edge에서 검증, 주석 명시)
--    · 세션 leftover는 meal_session.status='closed'에서만(open이면 409 SESSION_STILL_OPEN)
--    · session_eaten_ratio ⊕ per_food (상호배타)
--    · per_food 전 항목 필수(누락/중복/존재X = VALIDATION_ERROR)
--    · ratio finite only, 재조정은 original_summary 기준(누적 금지)
--    · 클라 pre_result 비신뢰 — pre_meal_log_id/session_id로 canonical 조회, 소유권 불일치 403
-- ---------------------------------------------------------------------

commit;

-- =====================================================================
-- 되돌리기
-- =====================================================================
-- begin;
--   drop view if exists public.v_meal_session_totals;
--   drop table if exists public.meal_log_adjustment cascade;
--   alter table public.meal_log drop constraint if exists meal_log_source_check;
--   alter table public.meal_log add constraint meal_log_source_check
--     check (source in ('photo','manual','edited'));
--   alter table public.meal_log
--     drop column if exists meal_session_id, drop column if exists leftover_method,
--     drop column if exists eaten_ratio, drop column if exists original_summary,
--     drop column if exists adjusted_summary, drop column if exists leftover_note,
--     drop column if exists leftover_confidence, drop column if exists leftover_adjusted_at,
--     drop column if exists leftover_engine_version;
--   delete from public.data_domain_policy where domain in ('meal_session','meal_log_adjustment');
--   drop table if exists public.meal_session cascade;
-- commit;
