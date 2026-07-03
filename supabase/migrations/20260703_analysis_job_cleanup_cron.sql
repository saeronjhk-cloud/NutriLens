-- =====================================================================
-- analysis_job 30일 정리 크론 (data_domain_policy: analysis_job=30일 보존)
-- pg_cron, 매일 03:17 KST 아님 — cron은 UTC: 18:17 UTC = 03:17 KST
-- 2026-07-03
-- =====================================================================

create extension if not exists pg_cron;

-- 동일 이름 잡이 있으면 제거 후 재등록(멱등)
do $$
declare _id bigint;
begin
  select jobid into _id from cron.job where jobname = 'analysis-job-30d-cleanup';
  if _id is not null then perform cron.unschedule(_id); end if;
end $$;

select cron.schedule(
  'analysis-job-30d-cleanup',
  '17 18 * * *',
  $$delete from public.analysis_job where created_at < now() - interval '30 days'$$
);
