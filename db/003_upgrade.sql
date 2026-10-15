-- Upgrade for installs made before the runner existed. Small, and safe to run more than once.
-- Run this in the Supabase SQL editor. Then look at the table it prints at the bottom.

alter table watch_company add column if not exists note text;
alter table watch_company add column if not exists careers_url text;
alter table watch_company add column if not exists ats_checked_at timestamptz;

alter table job_posting add column if not exists is_match boolean not null default false;
alter table job_posting add column if not exists normalised_title text;
alter table job_posting add column if not exists seniority_flag text;
alter table job_posting add column if not exists likely_repost boolean not null default false;
alter table job_posting add column if not exists role_kind text;
alter table job_posting drop constraint if exists job_posting_role_kind_check;
alter table job_posting add constraint job_posting_role_kind_check
  check (role_kind in ('match', 'marketing', 'adjacent'));

alter table job_event add column if not exists dedupe_key text;
alter table job_event add column if not exists detail text;
alter table job_event drop constraint if exists job_event_event_type_check;
alter table job_event add constraint job_event_event_type_check
  check (event_type in
    ('matching_role_opened', 'matching_role_closed', 'first_marketing_role_posted',
     'adjacent_role_opened', 'careers_page_created', 'open_role_count_jump',
     'list_appearance', 'funding_announced', 'careers_page_changed'));

alter table digest_log add column if not exists error text;

-- Tell Supabase's web API to pick up the new columns now.
notify pgrst, 'reload schema';

-- Check. You should see these rows, with the column names listed on the right.
select table_name, string_agg(column_name, ', ' order by column_name) as new_columns_present
from information_schema.columns
where table_schema = 'public'
  and (table_name, column_name) in (
    ('watch_company', 'note'), ('watch_company', 'careers_url'), ('watch_company', 'ats_checked_at'),
    ('job_posting', 'role_kind'), ('job_posting', 'likely_repost'), ('job_posting', 'is_match'),
    ('job_event', 'dedupe_key'), ('job_event', 'detail'), ('digest_log', 'error'))
group by table_name
order by table_name;
