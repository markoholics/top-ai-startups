# Job Signal Engine

Watches a list of AI startups and emails you when one opens a role you would apply to.
It runs twice a day. The point is speed: you see a matching role within hours of it going live.

It is deliberately small. Delete it by removing the repo, the Supabase tables and the Vercel project.

## What it answers on every run
1. Which watched companies have a matching role open now?
2. Which are new since the last run, and how many hours ago did the engine first see them?
3. Which companies changed in a way that points to a marketing or GTM hire, even with no matching title yet?

## How it works
1. **Companies** live in the Supabase table `watch_company`. Two tiers: `seed` (from `seed/companies.csv`) and `india` (copied from your `company` table).
2. **Job boards** are found by reading each company's careers page, or by trying likely board names on Greenhouse, Lever, Ashby, Workable and Recruitee. A board is used only when the company's own page links to it, or its job descriptions name the company's website. A name match alone is never trusted.
3. **Roles** are matched against `config/roles.yaml`. Locations are filtered against `config/config.yaml`. Roles in other places are stored but never emailed. Once a week the email says how many matching titles were hidden by location.
4. **Events** (new matching role, role closed, first marketing role, and so on) are saved with a weight. Score is weight times recency, with a 14 day half life, plus a bonus for each extra seed list.
5. **One email** goes out per run, only when there is something new. An event counts as sent only when its email was delivered. If Gmail fails, the events wait and go out next run.
6. **The first run** is a baseline. It saves everything silently and emails every open matching role that passes your location filter, plus the companies that need a manual fix.

## Set up
1. In Supabase, run `db/001_schema.sql`, then `db/002_seed.sql`, in the SQL editor. Both are safe to run again.
2. In the GitHub repo, add these Actions secrets: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `SMTP_USER`, `SMTP_PASSWORD`, `NOTIFY_TO`. `SMTP_PASSWORD` is a Google app password.
3. Actions tab, **Job signals**, **Run workflow**. Tick `dry_run` the first time to see what it finds without saving or emailing.
4. Run it again without `dry_run`. That is the baseline run. It can take about half an hour because it checks every company politely, one request a second per site.
5. After that it runs by itself at 06:30 and 18:30 IST.

Send a test email with fake data: Actions tab, **Send test email**.

## The dashboard
The Vercel site shows the same data as a set of tables. Set `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` and `DASHBOARD_PASSWORD` in the Vercel project, then redeploy. The Supabase SQL editor also works: run `select * from v_open_matches;` and the other `v_` views listed in `db/001_schema.sql`.

## Fixing a company by hand
Companies the engine cannot match to a job board are listed in the email under "Companies that need a manual ATS fix". Open the company's careers page, find the link to its job board, and run this in the SQL editor:

```sql
update watch_company
set ats_type = 'ashby', ats_token = 'the-board-name', verified = true, needs_manual_ats = false
where name = 'Company Name';
```

`ats_type` is one of `greenhouse`, `lever`, `ashby`, `workable`, `recruitee`. The token is the last part of the board link.

## Two limits of GitHub Actions
- **Cron can run late.** GitHub may start a scheduled run several minutes after the set time, and later when GitHub is busy. A run set for 06:30 IST may start at 06:45.
- **Scheduled workflows are switched off after 60 days without a commit.** Each run commits `state/last_run.json`, so the repo stays active. If the emails ever stop, press **Run workflow** once and check that the schedule is still listed.

## Other known limits
- Some careers pages build their links with JavaScript. The engine cannot see those links, so those companies go on the manual list.
- Workable rate limits automated requests hard. The Workable adapter follows its documentation but could not be tested live from the build environment.
- SmartRecruiters is not supported. Its documented job feed needs a private key.
- Lever accounts hosted in the EU use a different address. Only the global address is tried.
- A location that names an Indian city not in `locations_ok`, with no country, is hidden.
- `funding_announced` is in the schema and config but is not detected yet.
- Seed source ingestion (Breakout List, Frontier List, Cosign, Harmonic) is not built yet. Until then, edit `seed/companies.csv` by hand, run `python3 scripts/make_seed_sql.py`, and paste `db/002_seed.sql` into Supabase.

## Politeness
One request per host per second. An honest user agent. robots.txt is obeyed for company web pages. Documented public job board APIs are called directly, since they exist for that. LinkedIn, Indeed and Glassdoor are never touched.

## Files
- `config/config.yaml`, `config/roles.yaml`: all the settings. No target role is written in code.
- `engine/`: the code. `run.py` is one run. `ats/` has one file per job board vendor.
- `db/`: the schema and seed SQL.
- `tests/`: run with `python3 -m unittest discover -s tests`.
- `api/`, `public/`: the dashboard for Vercel.
