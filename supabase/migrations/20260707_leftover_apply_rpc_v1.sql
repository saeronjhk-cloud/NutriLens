-- =====================================================================
-- 36_leftover_apply_rpc_마이그레이션_v1.sql
-- 원자성 수정: meal_log 갱신 + meal_log_adjustment insert를 단일 트랜잭션 RPC로.
-- 동시성: uq_adj_idem(user_id, idempotency_key)가 가드. 멱등/충돌/부분쓰기 방지.
-- 근거: 35_원자성_RPC_Cursor작업지시서_v1.md (제미나이/시니어 리뷰 후속).
-- 전제: 28_v2.1 + 28_v2.1.1 증분 적용됨(meal_log_adjustment·컬럼 존재).
-- ★ create or replace라 재실행 안전(멱등). SQL Editor 직접 적용 가능.
-- =====================================================================

begin;

create or replace function public.apply_leftover_adjustment(p jsonb)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_user     uuid := (p->>'user_id')::uuid;
  v_key      text := p->>'idempotency_key';
  v_hash     text := p->>'request_hash';
  v_existing public.meal_log_adjustment%rowtype;
  v_upd      jsonb;
  v_adj_id   uuid;
begin
  -- 1) 멱등: 기존 이력 조회
  select * into v_existing
    from public.meal_log_adjustment
    where user_id = v_user and idempotency_key = v_key;
  if found then
    if v_existing.request_hash is distinct from v_hash then
      raise exception 'IDEMPOTENCY_KEY_REUSE_MISMATCH' using errcode = 'P0001';
    end if;
    return jsonb_build_object(
      'replay', true, 'adjustment_id', v_existing.id,
      'adjusted_summary', v_existing.adjusted_summary);
  end if;

  -- 2) 이력 insert (동시 2요청은 uq_adj_idem에서 unique_violation → 아래 handler)
  insert into public.meal_log_adjustment(
    meal_log_id, meal_session_id, user_id, adjustment_type, method, scope,
    eaten_ratio, original_summary_snapshot, previous_adjusted_summary, adjusted_summary,
    original_foods_snapshot, session_food_snapshot, confidence, user_confirmed,
    idempotency_key, request_hash, endpoint, engine_version)
  values (
    (p->>'meal_log_id')::uuid, (p->>'meal_session_id')::uuid, v_user,
    'leftover', p->>'method', p->>'scope',
    (p->>'eaten_ratio')::numeric, p->'original_summary_snapshot',
    p->'previous_adjusted_summary', p->'adjusted_summary',
    p->'original_foods_snapshot', p->'session_food_snapshot',
    nullif(p->>'confidence','')::numeric, coalesce((p->>'user_confirmed')::boolean, true),
    v_key, v_hash, p->>'endpoint', p->>'engine_version')
  returning id into v_adj_id;

  -- 3) meal_log 갱신들 (source 미변경 — 절대 넣지 않음)
  for v_upd in
    select value from jsonb_array_elements(coalesce(p->'meal_log_updates','[]'::jsonb))
  loop
    update public.meal_log set
      leftover_method         = v_upd->>'leftover_method',
      eaten_ratio             = (v_upd->>'eaten_ratio')::numeric,
      original_summary        = v_upd->'original_summary',
      adjusted_summary        = v_upd->'adjusted_summary',
      leftover_note           = v_upd->>'leftover_note',
      leftover_adjusted_at    = now(),
      leftover_engine_version = v_upd->>'leftover_engine_version'
    where id = (v_upd->>'id')::uuid and user_id = v_user;
  end loop;

  return jsonb_build_object(
    'replay', false, 'adjustment_id', v_adj_id,
    'adjusted_summary', p->'adjusted_summary');

exception
  when unique_violation then
    -- 동시 요청 경합: 승자가 먼저 insert. 패자는 기존값으로 멱등 반환(hash 다르면 409).
    select * into v_existing
      from public.meal_log_adjustment
      where user_id = v_user and idempotency_key = v_key;
    if v_existing.request_hash is distinct from v_hash then
      raise exception 'IDEMPOTENCY_KEY_REUSE_MISMATCH' using errcode = 'P0001';
    end if;
    return jsonb_build_object(
      'replay', true, 'adjustment_id', v_existing.id,
      'adjusted_summary', v_existing.adjusted_summary);
end;
$$;

revoke all on function public.apply_leftover_adjustment(jsonb) from public, anon;
grant execute on function public.apply_leftover_adjustment(jsonb) to service_role;

commit;

-- 되돌리기:
-- begin; drop function if exists public.apply_leftover_adjustment(jsonb); commit;
