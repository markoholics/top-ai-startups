"""Turn what we saw this run into postings, events and near misses.

Pure functions. No network and no database, so every rule can be tested with
fixtures. The caller reads the previous state, calls diff_company(), then
writes the results.

Rules
  matching_role_opened         new posting that matches your titles and passes the
                               location filter (also when a posting changes to pass)
  matching_role_closed         an open matching posting that has left the board
  first_marketing_role_posted  the company had no marketing or GTM role before
  adjacent_role_opened         sales, ops, content or design role (location passes)
  open_role_count_jump         open roles up by the configured percentage and number
  careers_page_created         a careers page that was missing now exists
  careers_page_changed         weak signal, a changed page with no ATS access
  list_appearance              company newly appears on a seed list
A posting with a new id but the same title and location as one seen within the
repost window is flagged likely_repost and is not treated as new.
The first run (baseline) writes postings and snapshots but no events.
"""
import datetime as dt
from dataclasses import dataclass, field
from typing import List

from .config import load
from .matching import location_ok, match_title

_sig = load("config.yaml")["signals"]
WEIGHTS = _sig["weights"]
HALF_LIFE_DAYS = float(_sig["decay_half_life_days"])
LIST_BONUS = float(_sig["seed_list_bonus"])
JUMP_PCT = float(_sig["open_role_count_jump_pct"])
JUMP_MIN = int(_sig["open_role_count_jump_min_increase"])
EMPTY_GUARD = int(_sig["empty_fetch_guard_min_open"])
REPOST_DAYS = int(load("config.yaml")["repost"]["window_days"])


@dataclass
class Result:
    postings: List[dict] = field(default_factory=list)     # rows to upsert, keyed by ats_job_id
    events: List[dict] = field(default_factory=list)
    near_misses: List[dict] = field(default_factory=list)
    snapshot: dict = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)


def _event(company, etype, now, key, evidence_url=None, ats_job_id=None, detail=None):
    return {"company_id": company["id"], "event_type": etype, "weight": WEIGHTS[etype],
            "evidence_url": evidence_url, "observed_at": now, "ats_job_id": ats_job_id,
            "detail": detail, "dedupe_key": f"{etype}:{company['id']}:{key}"}


def _iso(d):
    return d.isoformat() if d else ""


def diff_company(company, prev_postings, prev_snapshot, jobs, now, run_id="", baseline=False):
    """company: {id, name, source_tags...}
    prev_postings: {ats_job_id: row} as stored (needs title, location, normalised_title,
                   first_seen_at, closed_at, is_match, location_ok, role_kind)
    prev_snapshot: {open_role_count} or None
    jobs: list of engine.ats.base.Job from a successful read."""
    out = Result()
    seen_ids = set()
    current_ids = {j.ats_job_id for j in jobs}
    evidence_base = company.get("careers_url") or (jobs[0].url if jobs else None)
    had_marketing_before = any(p.get("role_kind") in ("match", "marketing") for p in prev_postings.values())
    marketing_event_done = False

    # Guard: a board that suddenly shows nothing is more likely a failed read than a mass closure.
    prev_open = [p for p in prev_postings.values() if p.get("closed_at") is None]
    suspicious_empty = (len(jobs) == 0 and len(prev_open) >= EMPTY_GUARD)
    if suspicious_empty:
        out.notes.append(f"{company['name']}: board returned 0 roles but {len(prev_open)} were open. "
                         "Treated as a failed read. Nothing closed.")

    for job in jobs:
        seen_ids.add(job.ats_job_id)
        t = match_title(job.title)
        ok, why = location_ok(job.location, job.country, job.remote)
        prev = prev_postings.get(job.ats_job_id)
        row = {"ats_job_id": job.ats_job_id, "title": job.title, "location": job.location,
               "url": job.url, "title_match_score": t.score,
               "location_ok": ok, "is_match": t.is_match, "normalised_title": t.normalised,
               "seniority_flag": "senior" if t.seniority_flag else None,
               "role_kind": t.kind, "last_seen_at": now, "closed_at": None,
               "likely_repost": bool(prev.get("likely_repost")) if prev else False,
               "first_seen_at": prev["first_seen_at"] if prev else now}

        if t.near_miss_reason:
            out.near_misses.append({"ats_job_id": job.ats_job_id, "title": job.title,
                                    "best_match": t.best_include, "score": t.score,
                                    "reason": t.near_miss_reason})

        if prev is None:
            # Reposts: the same title and place as a posting that has left the board
            # (under another id) inside the window. A second live opening is not a repost.
            for other_id, other in prev_postings.items():
                if other_id in current_ids:
                    continue
                if (other.get("normalised_title") == t.normalised and (other.get("location") or "") == (job.location or "")):
                    last = other.get("closed_at") or other.get("last_seen_at") or other.get("first_seen_at")
                    if last and (now - last) <= dt.timedelta(days=REPOST_DAYS):
                        row["likely_repost"] = True
                        break
            if not baseline and not row["likely_repost"]:
                _new_posting_events(out, company, row, t, ok, had_marketing_before, evidence_base)
                if t.kind in ("match", "marketing") and ok:
                    marketing_event_done = True
                    had_marketing_before = True
        else:
            was_closed = prev.get("closed_at") is not None
            if was_closed and not baseline and t.is_match and ok:
                out.events.append(_event(company, "matching_role_opened", now,
                                         f"{job.ats_job_id}:reopened:{_iso(prev['closed_at'])}",
                                         job.url, job.ats_job_id, f"{job.title} ({job.location}), reopened"))
            elif (not was_closed and not baseline and t.is_match and ok
                  and not prev.get("location_ok") and not prev.get("likely_repost")):
                # The location changed to one that passes. Worth telling you once.
                out.events.append(_event(company, "matching_role_opened", now, job.ats_job_id,
                                         job.url, job.ats_job_id, f"{job.title} ({job.location}), location updated"))
        out.postings.append(row)

    # Closures
    if not suspicious_empty:
        for ats_id, prev in prev_postings.items():
            if ats_id in seen_ids or prev.get("closed_at") is not None:
                continue
            out.postings.append({"ats_job_id": ats_id, "closed_at": now, "last_seen_at": prev.get("last_seen_at")})
            if not baseline and prev.get("is_match") and prev.get("location_ok"):
                out.events.append(_event(company, "matching_role_closed", now, f"{ats_id}:{_iso(now)}",
                                         prev.get("url"), ats_id, f"{prev.get('title')} ({prev.get('location')}) closed"))

    # Open role count jump
    open_now = len(jobs) if not suspicious_empty else len(prev_open)
    out.snapshot = {"company_id": company["id"], "run_id": run_id, "observed_at": now,
                    "open_role_count": open_now}
    if not baseline and prev_snapshot and prev_snapshot.get("open_role_count") is not None and not suspicious_empty:
        before = prev_snapshot["open_role_count"]
        gain = open_now - before
        if before > 0 and gain >= JUMP_MIN and (gain / before * 100) >= JUMP_PCT:
            out.events.append(_event(company, "open_role_count_jump", now, f"run:{run_id}", evidence_base, None,
                                     f"open roles {before} to {open_now}"))
    return out


def _new_posting_events(out, company, row, t, ok, had_marketing_before, evidence_base):
    now, url, jid = row["last_seen_at"], row["url"], row["ats_job_id"]
    label = f"{row['title']} ({row['location'] or 'no location'})"
    if t.is_match and ok:
        out.events.append(_event(company, "matching_role_opened", now, jid, url, jid, label))
    if t.kind in ("match", "marketing") and ok and not had_marketing_before:
        out.events.append(_event(company, "first_marketing_role_posted", now, jid, url, jid, label))
    if t.kind == "adjacent" and ok:
        out.events.append(_event(company, "adjacent_role_opened", now, jid, url, jid, label))


def page_events(company, prev_snapshot, page_status, page_hash, now, run_id="", baseline=False):
    """Careers page signals for companies with no ATS access.
    prev_snapshot: {careers_status, careers_hash} or None."""
    if baseline or not prev_snapshot:
        return []
    evs = []
    url = company.get("careers_url")
    prev_status, prev_hash = prev_snapshot.get("careers_status"), prev_snapshot.get("careers_hash")
    if prev_status in (404, 410) and page_status == 200:
        evs.append(_event(company, "careers_page_created", now, f"created", url, None,
                          "careers page now exists"))
    elif page_status == 200 and prev_status == 200 and prev_hash and page_hash and prev_hash != page_hash:
        evs.append(_event(company, "careers_page_changed", now, f"run:{run_id}", url, None,
                          "weak signal: careers page text changed, no ATS access to see which roles"))
    return evs


def list_events(company, old_tags, new_tags, now, baseline=False):
    if baseline:
        return []
    return [_event(company, "list_appearance", now, tag, None, None, f"newly on list: {tag}")
            for tag in sorted(set(new_tags) - set(old_tags))]


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def decay(weight, observed_at, now):
    age_days = max((now - observed_at).total_seconds() / 86400.0, 0.0)
    return weight * 0.5 ** (age_days / HALF_LIFE_DAYS)


def score_company(events, source_tags, now):
    """Sum of weight x recency decay, plus a bonus for each extra seed list."""
    base = sum(decay(e["weight"], e["observed_at"], now) for e in events)
    return round(base + LIST_BONUS * max(len(source_tags or []) - 1, 0), 2)
