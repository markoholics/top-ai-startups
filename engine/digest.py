"""Build the email. Pure functions: data in, subject and bodies out.

Copy rules: British English, no em dashes, no hyphens used as dashes, short
sentences, no marketing language. tests/test_digest.py checks the dashes.
"""
import datetime as dt
import html as _html
from dataclasses import dataclass, field
from typing import List, Optional

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))     # India has no daylight saving

WHY = {
    "first_marketing_role_posted": "First marketing or GTM role this company has posted. They may be building the team.",
    "adjacent_role_opened": "Sales, ops, content and design hires often come before a GTM hire.",
    "open_role_count_jump": "Hiring is speeding up. A marketing role may follow.",
    "careers_page_created": "A new careers page often means hiring is starting.",
    "careers_page_changed": "Weak signal. The page changed, but we cannot see which roles.",
    "list_appearance": "Newly on a seed list. More lists means more investor attention.",
    "funding_announced": "New funding often pays for the first marketing hires.",
}
WHAT = {
    "first_marketing_role_posted": "First marketing role posted",
    "adjacent_role_opened": "Adjacent role opened",
    "open_role_count_jump": "Open roles jumped",
    "careers_page_created": "Careers page created",
    "careers_page_changed": "Careers page changed",
    "list_appearance": "Added to a seed list",
    "funding_announced": "Funding announced",
}


@dataclass
class Digest:
    subject: str
    text: str
    html: str
    event_ids: List[str] = field(default_factory=list)
    kind: str = "normal"


def ago(then, now):
    secs = max((now - then).total_seconds(), 0)
    if secs < 3600:
        n = max(1, round(secs / 60))
        return f"{n} minute{'s' if n != 1 else ''} ago"
    if secs < 48 * 3600:
        n = round(secs / 3600)
        return f"{n} hour{'s' if n != 1 else ''} ago"
    n = round(secs / 86400)
    return f"{n} days ago"


def ist(when):
    return when.astimezone(IST).strftime("%d %b %Y, %H:%M IST")


def _lists(tags):
    return ", ".join(tags) if tags else "none"


def _role_lines(r, now, show_first_seen=True):
    """Lines for one role. Returns (headline, [detail lines])."""
    flag = f" [{r['seniority_flag']}]" if r.get("seniority_flag") else ""
    head = f"{r['company']}: {r['title']}{flag}"
    lines = [f"Location: {r.get('location') or 'not stated'}",
             f"Tier: {r['tier']}   ATS: {r['ats_type']}   Match score: {r['score']:.0f}",
             f"Seed lists: {_lists(r.get('source_tags'))}"]
    if show_first_seen and r.get("first_seen_at"):
        lines.append(f"First seen: {ago(r['first_seen_at'], now)} ({ist(r['first_seen_at'])})")
    lines.append(f"Apply: {r['url']}")
    return head, lines


def _top_item(new_roles, moved):
    if new_roles:
        r = sorted(new_roles, key=lambda x: (-(x.get("company_score") or 0), -x["score"]))[0]
        return f"{r['company']}: {r['title']}"
    m = sorted(moved, key=lambda x: -(x.get("company_score") or 0))[0]
    return f"{m['company']}: {WHAT.get(m['event_type'], m['event_type']).lower()}"


def _housekeeping(manual_ats, failed_sources, hidden_count):
    out = []
    if manual_ats:
        out.append(("Companies that need a manual ATS fix",
                    [f"{c['name']} ({c['tier']})" + (f", careers page: {c['careers_url']}" if c.get("careers_url") else "")
                     + (f". {c['note']}" if c.get("note") else "") for c in manual_ats]))
    if failed_sources:
        out.append(("Sources that failed", list(failed_sources)))
    if hidden_count is not None:
        out.append(("Location", [f"{hidden_count} matching titles hidden by location."]))
    return out


def _render(title_line, intro, sections, now):
    """sections: list of (heading, [item]) where item is (headline, [lines]) or a plain string."""
    t = [title_line, "=" * len(title_line), ""]
    if intro:
        t += [intro, ""]
    h = [f"<h2 style='font:600 18px system-ui;margin:0 0 8px'>{_html.escape(title_line)}</h2>"]
    if intro:
        h.append(f"<p style='font:14px system-ui;margin:0 0 16px'>{_html.escape(intro)}</p>")
    for heading, items in sections:
        t += [heading.upper(), "-" * len(heading), ""]
        h.append(f"<h3 style='font:600 15px system-ui;margin:20px 0 8px'>{_html.escape(heading)}</h3>")
        for it in items:
            if isinstance(it, str):
                t.append(f"* {it}")
                h.append(f"<p style='font:14px system-ui;margin:4px 0'>{_html.escape(it)}</p>")
                continue
            head, lines = it
            t += [head] + [f"  {x}" for x in lines] + [""]
            body = "".join(_linkify(x) for x in lines)
            h.append(f"<div style='font:14px/1.5 system-ui;margin:0 0 14px'><b>{_html.escape(head)}</b><br>{body}</div>")
        if items and isinstance(items[-1], str):
            t.append("")
    return "\n".join(t).rstrip() + "\n", "<div style='max-width:640px'>" + "".join(h) + "</div>"


def _linkify(line):
    esc = _html.escape(line)
    for tag in ("Apply: ", "Evidence: "):
        if line.startswith(tag):
            url = line[len(tag):]
            if url.startswith(("http://", "https://")):
                u = _html.escape(url, quote=True)
                return f"{tag}<a href='{u}'>{_html.escape(url)}</a><br>"
    return esc + "<br>"


def build_digest(new_roles, moved, closed, manual_ats, failed_sources, hidden_count, now) -> Optional[Digest]:
    """Normal run. Returns None when there is nothing new. Housekeeping alone
    never triggers an email."""
    if not (new_roles or moved or closed):
        return None
    n = len(new_roles)
    if n:
        subject = f"Job signals: {n} new matching role{'s' if n != 1 else ''} ({_top_item(new_roles, moved)})"
    elif moved:
        c = len(moved)
        subject = f"Job signals: {c} compan{'ies' if c != 1 else 'y'} moved ({_top_item([], moved)})"
    else:
        k = len(closed)
        subject = f"Job signals: {k} role{'s' if k != 1 else ''} closed ({closed[0]['company']}: {closed[0]['title']})"
    sections = []
    if new_roles:
        ordered = sorted(new_roles, key=lambda x: (-(x.get("company_score") or 0), -x["score"]))
        sections.append(("New matching roles", [_role_lines(r, now) for r in ordered]))
    if moved:
        items = []
        for m in sorted(moved, key=lambda x: -(x.get("company_score") or 0)):
            lines = [f"What changed: {m['detail']}", f"Tier: {m['tier']}"]
            if m.get("evidence_url"):
                lines.append(f"Evidence: {m['evidence_url']}")
            lines.append(f"Why it may matter: {WHY.get(m['event_type'], '')}")
            items.append((f"{m['company']}: {WHAT.get(m['event_type'], m['event_type'])}", lines))
        sections.append(("Companies that moved without a matching role", items))
    if closed:
        sections.append(("Roles closed since the last run",
                         [f"{c['company']}: {c['title']} ({c.get('location') or 'no location'}), closed {ago(c['closed_at'], now)}"
                          for c in closed]))
    sections += _housekeeping(manual_ats, failed_sources, hidden_count)
    text, html = _render("Job signals", f"Run time: {ist(now)}", sections, now)
    ids = [x["event_id"] for x in (new_roles + moved + closed) if x.get("event_id")]
    return Digest(subject, text, html, ids, "normal")


def build_baseline(open_roles, manual_ats, failed_sources, hidden_count, now) -> Digest:
    """First run. Lists every open matching role that passes the location filter.
    These are real openings you can apply to today. Always sent."""
    n = len(open_roles)
    intro = (f"{n} matching role{'s are' if n != 1 else ' is'} open now and pass your location filter. "
             "From the next run, only changes are emailed." if n else
             "No open matching role passes your location filter right now. From the next run, only changes are emailed.")
    sections = []
    if open_roles:
        ordered = sorted(open_roles, key=lambda x: (-(x.get("company_score") or 0), -x["score"]))
        sections.append(("Open matching roles you can apply to today",
                         [_role_lines(r, now, show_first_seen=False) for r in ordered]))
    sections += _housekeeping(manual_ats, failed_sources, hidden_count)
    text, html = _render("Job Signal Engine: baseline complete", intro, sections, now)
    return Digest("Job Signal Engine: baseline complete", text, html, [], "baseline")


def build_quiet(open_roles, manual_count, failed_lines, hidden_count, now) -> Digest:
    """A short status email for a day with nothing new. Not an empty email: it says what is
    still open. It carries no events, so it never marks anything as sent."""
    n = len(open_roles)
    if n:
        subject = f"Job signals: nothing new today ({n} matching role{'s' if n != 1 else ''} still open)"
        intro = "No new matching roles since the last run. These are still open and pass your location filter."
    else:
        subject = "Job signals: nothing new today (no matching roles open)"
        intro = "No new matching roles since the last run. None is open right now that passes your location filter."
    sections = []
    if open_roles:
        ordered = sorted(open_roles, key=lambda x: (-(x.get("company_score") or 0), -x["score"]))
        sections.append(("Still open and matching", [_role_lines(r, now, show_first_seen=False) for r in ordered]))
    notes = []
    if manual_count:
        notes.append(f"{manual_count} companies still need a manual ATS fix. Run select * from v_needs_manual_ats in Supabase.")
    notes += list(failed_lines)
    if hidden_count is not None:
        notes.append(f"{hidden_count} matching titles hidden by location.")
    if notes:
        sections.append(("Housekeeping", notes))
    text, html = _render("Job signals", f"{intro} Run time: {ist(now)}", sections, now)
    return Digest(subject, text, html, [], "quiet")
