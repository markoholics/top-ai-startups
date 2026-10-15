-- Job Signal Engine: schema.
-- Safe to run more than once. Paste into the Supabase SQL editor.
-- Four tables as briefed, plus three small support tables (marked "support").

create extension if not exists pgcrypto;

-- ---------------------------------------------------------------------------
-- watch_company
-- ---------------------------------------------------------------------------
create table if not exists watch_company (
  id               uuid primary key default gen_random_uuid(),
  name             text not null,
  domain           text,
  tier             text not null default 'seed' check (tier in ('seed', 'india')),
  source_tags      text[] not null default '{}',
  ats_type         text check (ats_type in
                     ('greenhouse', 'lever', 'ashby', 'workable',
                      'smartrecruiters', 'recruitee')),
  ats_token        text,
  verified         boolean not null default false,
  needs_manual_ats boolean not null default false,
  created_at       timestamptz not null default now(),
  -- Support columns. Small, and needed by discovery and the page fallback.
  careers_url      text,
  ats_checked_at   timestamptz,
  note             text
);

-- One row per company name, ignoring case and spacing.
create unique index if not exists watch_company_name_uq
  on watch_company (lower(regexp_replace(name, '\s+', '', 'g')));

-- ---------------------------------------------------------------------------
-- job_posting
-- ---------------------------------------------------------------------------
create table if not exists job_posting (
  id                uuid primary key default gen_random_uuid(),
  company_id        uuid not null references watch_company (id) on delete cascade,
  ats_job_id        text not null,
  title             text not null,
  location          text,
  url               text,
  first_seen_at     timestamptz not null default now(),
  last_seen_at      timestamptz not null default now(),
  closed_at         timestamptz,
  title_match_score numeric(5, 2),
  location_ok       boolean,
  -- Support columns for matching and repost detection.
  is_match          boolean not null default false,
  normalised_title  text,
  seniority_flag    text check (seniority_flag in ('senior')),
  likely_repost     boolean not null default false,
  role_kind         text check (role_kind in ('match', 'marketing', 'adjacent')),
  constraint job_posting_dedupe unique (company_id, ats_job_id)
);

create index if not exists job_posting_open_idx
  on job_posting (company_id) where closed_at is null;
create index if not exists job_posting_repost_idx
  on job_posting (company_id, normalised_title, location);

-- ---------------------------------------------------------------------------
-- job_event
-- ---------------------------------------------------------------------------
create table if not exists job_event (
  id           uuid primary key default gen_random_uuid(),
  company_id   uuid not null references watch_company (id) on delete cascade,
  posting_id   uuid references job_posting (id) on delete set null,
  event_type   text not null check (event_type in
                 ('matching_role_opened', 'matching_role_closed',
                  'first_marketing_role_posted', 'adjacent_role_opened',
                  'careers_page_created', 'open_role_count_jump',
                  'list_appearance', 'funding_announced',
                  'careers_page_changed')),
  weight       numeric(6, 2) not null,
  evidence_url text,
  observed_at  timestamptz not null default now(),
  -- Support: stops the same event being written twice across runs.
  dedupe_key   text not null,
  detail       text,
  constraint job_event_dedupe unique (dedupe_key)
);

create index if not exists job_event_company_idx
  on job_event (company_id, observed_at desc);

-- ---------------------------------------------------------------------------
-- digest_log
-- An event is "unsent" until it sits in the event_ids of a row with
-- delivered = true. A failed send leaves its events unsent for the next run.
-- ---------------------------------------------------------------------------
create table if not exists digest_log (
  id        uuid primary key default gen_random_uuid(),
  sent_at   timestamptz not null default now(),
  event_ids uuid[] not null default '{}',
  subject   text not null,
  delivered boolean not null default false,
  error     text
);

-- ---------------------------------------------------------------------------
-- Support tables
-- ---------------------------------------------------------------------------

-- support: one row per run. A run with kind = 'baseline' sets the baseline.
create table if not exists engine_run (
  id               uuid primary key default gen_random_uuid(),
  started_at       timestamptz not null default now(),
  finished_at      timestamptz,
  kind             text not null default 'normal'
                     check (kind in ('baseline', 'normal')),
  companies_ok     integer,
  companies_failed integer,
  notes            jsonb not null default '{}'::jsonb
);

-- support: per company, per run. Feeds open_role_count_jump and the
-- careers page diff.
create table if not exists company_snapshot (
  id              uuid primary key default gen_random_uuid(),
  company_id      uuid not null references watch_company (id) on delete cascade,
  run_id          uuid references engine_run (id) on delete set null,
  observed_at     timestamptz not null default now(),
  open_role_count integer,
  careers_hash    text,
  careers_status  integer
);
create index if not exists company_snapshot_idx
  on company_snapshot (company_id, observed_at desc);

-- support: titles that were close to a match but rejected, for tuning.
create table if not exists title_near_miss (
  id          uuid primary key default gen_random_uuid(),
  company_id  uuid not null references watch_company (id) on delete cascade,
  posting_id  uuid references job_posting (id) on delete cascade,
  title       text not null,
  best_match  text,
  score       numeric(5, 2),
  reason      text not null,
  observed_at timestamptz not null default now(),
  constraint near_miss_uq unique (posting_id, reason)
);

-- The engine connects with the service role key, which bypasses RLS.
-- Turning RLS on with no policies keeps the anon key out.
alter table watch_company    enable row level security;
alter table job_posting      enable row level security;
alter table job_event        enable row level security;
alter table digest_log       enable row level security;
alter table engine_run       enable row level security;
alter table company_snapshot enable row level security;
alter table title_near_miss  enable row level security;

-- ---------------------------------------------------------------------------
-- Dashboard views. Run these in the Supabase SQL editor.
-- ---------------------------------------------------------------------------

-- Events not yet in a delivered digest. The email reads this.
create or replace view v_unsent_events as
select e.*
from job_event e
where not exists (
  select 1 from digest_log d
  where d.delivered and e.id = any (d.event_ids)
);

-- Matching roles open now, in a location you would take.
create or replace view v_open_matches as
select c.name        as company,
       c.tier,
       p.title,
       p.location,
       c.ats_type,
       p.title_match_score as score,
       p.seniority_flag,
       p.likely_repost,
       p.first_seen_at,
       round(extract(epoch from (now() - p.first_seen_at)) / 3600, 1)
                     as hours_since_first_seen,
       (p.first_seen_at at time zone 'Asia/Kolkata') as first_seen_ist,
       c.source_tags,
       p.url        as apply_url
from job_posting p
join watch_company c on c.id = p.company_id
where p.closed_at is null and p.is_match and p.location_ok
order by p.first_seen_at desc;

-- Matching titles hidden by location. Stored, never emailed.
create or replace view v_hidden_by_location as
select c.name as company, c.tier, p.title, p.location, p.first_seen_at, p.url
from job_posting p
join watch_company c on c.id = p.company_id
where p.closed_at is null and p.is_match and not coalesce(p.location_ok, false)
order by p.first_seen_at desc;

-- Companies ranked by score: weight x recency decay (half life 14 days)
-- plus a bonus per extra seed list. Keep the numbers in step with
-- config/config.yaml. The engine uses the config values; this view is
-- for reading only.
create or replace view v_company_scores as
select c.name as company,
       c.tier,
       round(
         coalesce(sum(e.weight * power(0.5,
           extract(epoch from (now() - e.observed_at)) / 86400 / 14.0)), 0)
         + 1.0 * greatest(cardinality(c.source_tags) - 1, 0), 2) as score,
       count(e.id) as events,
       max(e.observed_at) as last_event_at,
       c.source_tags
from watch_company c
left join job_event e on e.company_id = c.id
group by c.id
order by score desc;

-- Latest events, newest first.
create or replace view v_recent_events as
select e.observed_at,
       (e.observed_at at time zone 'Asia/Kolkata') as observed_ist,
       c.name as company,
       c.tier,
       e.event_type,
       e.weight,
       e.detail,
       e.evidence_url,
       (e.id in (select id from v_unsent_events)) as unsent
from job_event e
join watch_company c on c.id = e.company_id
order by e.observed_at desc;

-- Roles closed, newest first.
create or replace view v_closed_roles as
select c.name as company, p.title, p.location, p.first_seen_at, p.closed_at
from job_posting p
join watch_company c on c.id = p.company_id
where p.closed_at is not null and p.is_match
order by p.closed_at desc;

-- Companies to fix by hand.
create or replace view v_needs_manual_ats as
select name, domain, tier, careers_url, source_tags, ats_checked_at
from watch_company
where needs_manual_ats
order by tier, name;

-- Coverage: how many companies resolved to an ATS.
create or replace view v_coverage as
select tier,
       count(*)                                             as companies,
       count(*) filter (where ats_type is not null)         as resolved_to_ats,
       count(*) filter (where needs_manual_ats)             as needs_manual,
       count(*) filter (where verified)                     as verified,
       count(*) filter (where ats_type is null
                          and not needs_manual_ats)         as not_yet_checked
from watch_company
group by tier;

-- Per ATS breakdown.
create or replace view v_coverage_by_ats as
select coalesce(ats_type, 'none') as ats_type, count(*) as companies
from watch_company
group by 1
order by 2 desc;

-- Near misses to tune roles.yaml, closest first.
create or replace view v_near_misses as
select c.name as company, n.title, n.best_match, n.score, n.reason, n.observed_at
from title_near_miss n
join watch_company c on c.id = n.company_id
order by n.score desc nulls last, n.observed_at desc;

-- Run history, including what each digest delivered.
create or replace view v_run_history as
select r.started_at, r.kind, r.companies_ok, r.companies_failed, r.notes
from engine_run r
order by r.started_at desc;

create or replace view v_digest_history as
select sent_at, subject, delivered, cardinality(event_ids) as events, error
from digest_log
order by sent_at desc;

-- ---------------------------------------------------------------------------
-- Lock the views down. By default a view runs with its owner's rights and
-- would let the public anon key read the data over the REST API.
-- security_invoker makes the views obey RLS, so only the service role reads.
-- ---------------------------------------------------------------------------
alter view v_unsent_events      set (security_invoker = on);
alter view v_open_matches       set (security_invoker = on);
alter view v_hidden_by_location set (security_invoker = on);
alter view v_company_scores     set (security_invoker = on);
alter view v_recent_events      set (security_invoker = on);
alter view v_closed_roles       set (security_invoker = on);
alter view v_needs_manual_ats   set (security_invoker = on);
alter view v_coverage           set (security_invoker = on);
alter view v_coverage_by_ats    set (security_invoker = on);
alter view v_near_misses        set (security_invoker = on);
alter view v_run_history        set (security_invoker = on);
alter view v_digest_history     set (security_invoker = on);

-- ---------------------------------------------------------------------------
-- Upgrades for installs made before step 6. Safe to run on a fresh install too.
-- ---------------------------------------------------------------------------
alter table watch_company add column if not exists note text;
alter table job_posting add column if not exists role_kind text;
alter table job_posting drop constraint if exists job_posting_role_kind_check;
alter table job_posting add constraint job_posting_role_kind_check
  check (role_kind in ('match', 'marketing', 'adjacent'));

alter table job_event drop constraint if exists job_event_event_type_check;
alter table job_event add constraint job_event_event_type_check
  check (event_type in
    ('matching_role_opened', 'matching_role_closed', 'first_marketing_role_posted',
     'adjacent_role_opened', 'careers_page_created', 'open_role_count_jump',
     'list_appearance', 'funding_announced', 'careers_page_changed'));
