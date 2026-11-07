-- Run in Supabase SQL editor. The publishable/anon key is public; never expose a service-role key.
create table if not exists public.kage_profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  display_name text not null default '',
  avatar text not null default 'sakura',
  accent text not null default 'rose',
  updated_at timestamptz not null default now()
);
create table if not exists public.kage_history (
  user_id uuid not null references auth.users(id) on delete cascade,
  content_key text not null,
  title text not null,
  watch_url text not null,
  episode text not null default '',
  watched_at timestamptz not null default now(),
  primary key (user_id, content_key)
);
alter table public.kage_profiles enable row level security;
alter table public.kage_history enable row level security;
grant select, insert, update, delete on public.kage_profiles to authenticated;
grant select, insert, update, delete on public.kage_history to authenticated;
drop policy if exists "Users manage own profile" on public.kage_profiles;
create policy "Users manage own profile" on public.kage_profiles for all to authenticated using (auth.uid() = id) with check (auth.uid() = id);
drop policy if exists "Users manage own history" on public.kage_history;
create policy "Users manage own history" on public.kage_history for all to authenticated using (auth.uid() = user_id) with check (auth.uid() = user_id);
