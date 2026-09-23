-- Apply once in a NEW Firebird project using Supabase SQL Editor.
-- Columns intentionally match the supplied CSV for direct dashboard import.
begin;
create table public.firebird_contractors (
  id text primary key,
  anon_name text not null check (length(anon_name) > 0),
  categories text not null,
  city text not null,
  city_imputed boolean not null,
  synthetic boolean not null,
  price_from_kzt integer not null check (price_from_kzt > 0),
  price_imputed boolean not null,
  event_formats text not null,
  languages text not null,
  max_hours double precision check (max_hours > 0 and max_hours < 'Infinity'::float8),
  busy_dates text not null default '',
  description text not null
);
alter table public.firebird_contractors enable row level security;
-- No public policies. The browser cannot read or edit the catalogue directly.
revoke all on public.firebird_contractors from anon, authenticated;
grant select on public.firebird_contractors to service_role;
comment on table public.firebird_contractors is
  'Fixed HackAlem CSV snapshot: 66 profiles, 13 synthetic. Lists use | separators. Backend validates every value against the supplied CSV.';
commit;
