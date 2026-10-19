"""One run of the engine. Start it with:  python3 -m engine.run  [--dry-run]

Order of work
  1. Decide if this is the baseline run (no baseline email delivered yet).
  2. Find job boards for companies that have none (retried weekly).
  3. Read every board, work out what changed, save postings and events.
  4. Watch careers pages for companies with no ATS access.
  5. Build one email from every event not yet delivered, and send it.
A baseline run saves everything silently and emails the open matching roles.
"""
import datetime as dt
import json
import logging
import os
import pathlib
import sys

from . import pagewatch
from .ats import ADAPTERS
from .ats.base import BoardError, BoardNotFound
from .config import ROOT
from .delivery import deliver
from .config import load
from .digest import IST, build_baseline, build_digest, build_quiet
from .discover import RATE_LIMITED, discover
from .events import diff_company, page_events, score_company

log = logging.getLogger("jobsignals")
REDISCOVER_DAYS = 7
SCORE_WINDOW_DAYS = 60
STATE_FILE = ROOT / "state" / "last_run.json"


def _due(company, now):
    checked = company.get("ats_checked_at")
    return checked is None or (now - checked) >= dt.timedelta(days=REDISCOVER_DAYS)


def _apply_discovery(store, company, result, now, save=True):
    f = {"ats_checked_at": now}
    if result.get("careers_url"):
        f["careers_url"] = result["careers_url"]
    if result["confidence"] == "confirmed":
        f.update(ats_type=result["ats_type"], ats_token=result["ats_token"], verified=True,
                 needs_manual_ats=False, note=None)
    else:
        f["needs_manual_ats"] = True
        if result["confidence"] == "unconfirmed":
            said = f" (the board says '{result['reported_name']}')" if result.get("reported_name") else ""
            f["note"] = (f"Possible board {result['ats_type']}:{result['ats_token']}{said}. "
                         "Not used because nothing proves it belongs to this company.")
        else:
            f["note"] = "; ".join(result.get("notes") or []) or "No job board found."
    if save:
        store.update_company(company["id"], f)
    company.update(f)                     # a dry run keeps it in memory only, so boards can still be read


def run_once(store, mailer, to, now=None, discover_fn=discover, adapters=ADAPTERS, page_fn=pagewatch.check, dry_run=False,
             quiet_status=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    if quiet_status is None:
        quiet_status = bool(load("config.yaml")["email"].get("daily_status_when_quiet", False))
    baseline = not store.baseline_delivered()
    run_id = "" if dry_run else store.start_run("baseline" if baseline else "normal", now)
    log.info("Run started. Kind: %s%s", "baseline" if baseline else "normal", " (dry run)" if dry_run else "")
    RATE_LIMITED.clear()
    companies = store.companies()
    failed, notes, new_events = [], [], 0
    report = []                                   # one line per company that was looked up this run
    open_matches, hidden_matches = [], 0          # filled for a dry run, so it shows something useful

    # 1. Find boards.
    for c in companies:
        if c.get("ats_type") or not _due(c, now):
            continue
        log.info("Looking for a job board: %s", c["name"])
        try:
            result = discover_fn(c["name"], c.get("domain"))
        except Exception as e:                                          # one bad company never stops the run
            log.warning("Discovery failed for %s: %s", c["name"], e)
            continue
        log.info("  %s", result["confidence"] or "nothing found")
        _apply_discovery(store, c, result, now, save=not dry_run)
        report.append((c["name"], result))

    # 2. Read boards, diff, save.
    ok = 0
    for c in companies:
        if not c.get("ats_type"):
            continue
        adapter = adapters.get(c["ats_type"])
        try:
            if adapter is None:
                raise BoardError(f"no adapter for {c['ats_type']}")
            jobs = adapter.fetch_jobs(c["ats_token"])
        except (BoardNotFound, BoardError) as e:
            failed.append(c["name"])
            log.warning("Could not read %s: %s", c["name"], e)
            continue
        ok += 1
        res = diff_company(c, store.postings_for(c["id"]), store.last_snapshot(c["id"]), jobs, now, run_id, baseline)
        notes += res.notes
        new_events += len(res.events)
        log.info("%s: %d roles, %d new events", c["name"], len(jobs), len(res.events))
        if dry_run:
            for p in res.postings:
                if p.get("is_match") and p.get("location_ok"):
                    open_matches.append((c["name"], p["title"], p.get("location"), p.get("url")))
                elif p.get("is_match"):
                    hidden_matches += 1
            for e in res.events:
                log.info("  would create %s: %s", e["event_type"], e.get("detail"))
            continue
        full = [dict(p, company_id=c["id"]) for p in res.postings if "title" in p]
        closed = [p["ats_job_id"] for p in res.postings if "title" not in p]
        store.upsert_postings(full)
        if closed:
            store.close_postings(c["id"], closed, now)
        ids = store.posting_ids(c["id"])
        store.insert_events([_event_row(e, ids.get(e.get("ats_job_id"))) for e in res.events])
        store.insert_near_misses([{"company_id": c["id"], "posting_id": ids.get(n["ats_job_id"]), "title": n["title"],
                                   "best_match": n["best_match"], "score": n["score"], "reason": n["reason"], "observed_at": now}
                                  for n in res.near_misses if ids.get(n["ats_job_id"])])
        store.insert_snapshot(res.snapshot)

    # 3. Careers pages, only for companies we cannot read through an ATS.
    # This is a weak signal. Whatever goes wrong here must never stop the main email.
    for c in companies:
        if c.get("ats_type") or not c.get("careers_url"):
            continue
        try:
            status, h = page_fn(c["careers_url"])
            prev = store.last_snapshot(c["id"])
            evs = page_events(c, prev, status, h, now, run_id, baseline)
            new_events += len(evs)
            if dry_run:
                continue
            store.insert_events([_event_row(e, None) for e in evs])
            store.insert_snapshot({"company_id": c["id"], "run_id": run_id, "observed_at": now,
                                   "careers_status": status, "careers_hash": h})
        except Exception as e:                                   # noqa: BLE001
            log.warning("Careers page watch failed for %s, carrying on: %s", c["name"], e)

    _log_discovery_report(report)
    if dry_run:
        log.info("OPEN MATCHING ROLES THAT PASS YOUR LOCATION FILTER: %d", len(open_matches))
        for company, title, loc, url in open_matches:
            log.info("  %s | %s | %s | %s", company, title, loc, url)
        log.info("Matching titles hidden by location: %d", hidden_matches)
        log.info("Dry run finished. %d boards read, %d failed, %d events would be created.", ok, len(failed), new_events)
        return {"emailed": False, "dry_run": True, "open_matches": open_matches}

    # 4. Email.
    manual = [{"name": c["name"], "tier": c["tier"], "careers_url": c.get("careers_url"), "note": c.get("note")}
              for c in store.companies() if c.get("needs_manual_ats")]
    failed_lines = ([f"Could not read {len(failed)} job board{'s' if len(failed) != 1 else ''} this run: "
                     f"{', '.join(failed[:8])}{' and more' if len(failed) > 8 else ''}."] if failed else [])
    ist_now = now.astimezone(IST)
    hidden = store.hidden_count() if (baseline or (ist_now.weekday() == 0 and ist_now.hour < 12)) else None
    digest = None
    if baseline:
        roles = store.open_matches()
        digest = build_baseline([_role(r) for r in roles], manual, failed_lines, hidden, now)
    else:
        digest = _normal_digest(store, manual, failed_lines, hidden, now)
        if digest is None and quiet_status:
            roles = [_role(r) for r in store.open_matches()]
            digest = build_quiet(roles, len(manual), failed_lines, hidden, now)
    emailed = False
    if digest is None:
        log.info("Nothing new. No email sent.")
    else:
        emailed = deliver(store, mailer, digest, to)
        log.info("Email %s: %s", "delivered" if emailed else "FAILED, will retry next run", digest.subject)
    store.finish_run(run_id, ok, len(failed), {"notes": notes, "failed": failed, "new_events": new_events, "emailed": emailed}, now)
    _write_state(now, baseline, ok, len(failed), new_events, emailed)
    return {"emailed": emailed, "baseline": baseline, "ok": ok, "failed": failed, "new_events": new_events}


def _event_row(event, posting_id):
    """An event as the job_event table stores it. The engine's own helper key is removed."""
    return dict({k: v for k, v in event.items() if k != "ats_job_id"}, posting_id=posting_id)


def _log_discovery_report(report):
    """A plain summary of how each company was looked up, so the log explains the hit rate."""
    if not report:
        return
    groups = {"confirmed": [], "unconfirmed": [], None: []}
    for name, r in report:
        groups[r["confidence"]].append((name, r))
    log.info("JOB BOARD SEARCH SUMMARY: %d companies looked up. %d confirmed, %d unconfirmed, %d nothing found.",
             len(report), len(groups["confirmed"]), len(groups["unconfirmed"]), len(groups[None]))
    for name, r in groups["confirmed"]:
        log.info("  CONFIRMED   %s: %s:%s (%d open roles)", name, r["ats_type"], r["ats_token"], len(r["jobs"] or []))
    for name, r in groups["unconfirmed"]:
        said = f", board says '{r['reported_name']}'" if r.get("reported_name") else ""
        log.info("  UNCONFIRMED %s: possible %s:%s%s", name, r["ats_type"], r["ats_token"], said)
    for name, r in groups[None]:
        why = "; ".join(r.get("notes") or []) or "no board found"
        log.info("  NOTHING     %s: %s", name, why[:160])


def _role(p):
    return {"event_id": None, "company": p["company"], "tier": p["tier"], "title": p["title"], "location": p.get("location"),
            "ats_type": p["ats_type"], "first_seen_at": p.get("first_seen_at"), "url": p.get("url") or "",
            "score": float(p.get("title_match_score") or 0), "source_tags": p.get("source_tags") or [],
            "seniority_flag": "senior, check fit" if p.get("seniority_flag") else None,
            "company_score": 0.0}


def _normal_digest(st, manual, failed_lines, hidden, now):
    events = st.unsent_events()
    if not events:
        return None
    since = now - dt.timedelta(days=SCORE_WINDOW_DAYS)
    recent = st.recent_events({e["company_id"] for e in events}, since)
    by_company = {}
    for e in recent:
        by_company.setdefault(e["company_id"], []).append(e)
    tags = {e["company_id"]: e["source_tags"] for e in events}
    cscore = {cid: score_company(evs, tags.get(cid), now) for cid, evs in by_company.items()}
    opened_companies = {e["company_id"] for e in events if e["event_type"] == "matching_role_opened" and e.get("posting")}
    new_roles, moved, closed = [], [], []
    for e in events:
        p = e.get("posting")
        base = {"event_id": e["id"], "company": e["company"], "tier": e["tier"], "company_score": cscore.get(e["company_id"], 0.0)}
        if e["event_type"] == "matching_role_opened" and not p:
            moved.append(dict(base, event_type=e["event_type"], detail=e.get("detail") or "A matching role opened",
                              evidence_url=e.get("evidence_url")))
        elif e["event_type"] == "matching_role_opened":
            new_roles.append(dict(base, title=p["title"], location=p.get("location"), ats_type=e["ats_type"],
                                  first_seen_at=p.get("first_seen_at") or e["observed_at"], url=p.get("url") or e.get("evidence_url") or "",
                                  score=float(p.get("title_match_score") or 0), source_tags=e["source_tags"],
                                  seniority_flag="senior, check fit" if p.get("seniority_flag") else None))
        elif e["event_type"] == "matching_role_closed":
            closed.append(dict(base, title=(p or {}).get("title") or (e.get("detail") or "A role"), location=(p or {}).get("location"),
                               closed_at=(p or {}).get("closed_at") or e["observed_at"]))
        elif e["company_id"] not in opened_companies:
            moved.append(dict(base, event_type=e["event_type"], detail=e.get("detail") or "", evidence_url=e.get("evidence_url")))
    d = build_digest(new_roles, moved, closed, manual, failed_lines, hidden, now)
    if d is None and events:
        # Events exist but none is worth showing (all folded into a company that already has a new role).
        return None
    if d is not None:
        d.event_ids = [e["id"] for e in events]          # every event in this email counts as delivered with it
    return d


def _write_state(now, baseline, ok, failed, new_events, emailed):
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps({"last_run_at": now.isoformat(), "kind": "baseline" if baseline else "normal",
                                      "boards_read": ok, "boards_failed": failed, "new_events": new_events,
                                      "emailed": emailed}, indent=2) + "\n", encoding="utf-8")


def run_url():
    """Link to this GitHub run, when running in GitHub Actions."""
    srv, repo, rid = (os.environ.get(k) for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
    return f"{srv}/{repo}/actions/runs/{rid}" if srv and repo and rid else None


def main(argv):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    from .db import SupabaseStore, StoreError
    from .mail import get_mailer
    try:
        st = SupabaseStore()
    except StoreError as e:
        log.error("%s Set the GitHub secrets SUPABASE_URL and SUPABASE_SERVICE_KEY.", e)
        return 2
    dry = "--dry-run" in argv
    to = os.environ.get("NOTIFY_TO", "")
    mailer = get_mailer()
    try:
        problems = st.check_schema()
        if problems:
            for p in problems:
                log.error("DATABASE NOT READY: %s", p)
            log.error("Fix: open the Supabase SQL editor, paste the whole of db/003_upgrade.sql, and run it. "
                      "If the columns show in the table at the bottom but this error stays, open Supabase "
                      "Project Settings, then API, and click Reload schema.")
            fail = "The database is missing something the engine needs: " + "; ".join(problems)
            if not dry:
                send_failure_alert(mailer, to, fail)
            return 3
        run_once(st, mailer, to, dry_run=dry)
    except Exception as e:                                       # noqa: BLE001
        # Nothing that goes wrong may be silent. Log it, tell the owner by email, and fail the run.
        kind = "Database error" if isinstance(e, StoreError) else f"Unexpected error ({type(e).__name__})"
        log.error("%s: %s", kind, e)
        if not dry:
            send_failure_alert(mailer, to, f"{kind}: {e}")
        return 1
    return 0


def send_failure_alert(mailer, to, reason, now=None):
    """Email the owner that a run failed. Never raises: an alert that fails must not hide the real error."""
    now = now or dt.datetime.now(dt.timezone.utc)
    link = run_url()
    lines = ["The job signal run failed, so no new alerts were sent this time.", "",
             f"What went wrong: {reason}", "",
             "Nothing is lost. New roles and changes are kept and will be in the next email once the run works.", ""]
    if link:
        lines.append(f"Details: {link}")
    lines.append(f"Run time: {now.astimezone(IST).strftime('%d %b %Y, %H:%M IST')}")
    try:
        mailer.send(to, "Job signals: the run failed", "\n".join(lines) + "\n", None)
        log.info("Failure alert email sent.")
        return True
    except Exception as e:                                       # noqa: BLE001
        log.error("Could not send the failure alert either: %s", e)
        return False


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
