-- =====================================================================
-- meal-photos 프라이빗 버킷 + 본인 폴더만 접근 RLS
-- 경로 규약: {user_id}/{photo_sha256}.jpg (UI 사양 06 §4)
-- 2026-07-03 · 스키마 v1 보완 (meal_log.photo_path 대상 버킷)
-- =====================================================================

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('meal-photos', 'meal-photos', false, 8388608, array['image/jpeg','image/png','image/webp'])
on conflict (id) do nothing;

-- 본인 폴더(첫 세그먼트 = auth.uid())만 읽기/쓰기/삭제
create policy meal_photos_select on storage.objects for select to authenticated
  using (bucket_id = 'meal-photos' and (storage.foldername(name))[1] = auth.uid()::text);

create policy meal_photos_insert on storage.objects for insert to authenticated
  with check (bucket_id = 'meal-photos' and (storage.foldername(name))[1] = auth.uid()::text);

create policy meal_photos_update on storage.objects for update to authenticated
  using (bucket_id = 'meal-photos' and (storage.foldername(name))[1] = auth.uid()::text);

create policy meal_photos_delete on storage.objects for delete to authenticated
  using (bucket_id = 'meal-photos' and (storage.foldername(name))[1] = auth.uid()::text);
