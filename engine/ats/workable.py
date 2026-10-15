"""Workable public jobs endpoint.

Checked against https://workable.readme.io/reference/jobs-1 ("Public Endpoints")
  GET https://www.workable.com/api/accounts/{subdomain}
  Response: {"name", "description", "jobs": [{title, shortcode, country, state, city,
             telecommuting, published_on, url, application_url, created_at, ...}]}
The server redirects to apply.workable.com/api/v1/widget/accounts/{subdomain}.
Workable rate limits hard (Cloudflare 429). A 429 is reported as an error and
the company is retried on the next run. We do not try to get around it.
"""
from . import _util
from .base import BoardError, Job

NAME = "workable"


def _url(token):
    return f"https://apply.workable.com/api/v1/widget/accounts/{token}"


def fetch_jobs(token):
    data, _ = _util.get_json(f"workable {token}", _url(token))
    if not isinstance(data, dict) or "jobs" not in data:
        raise BoardError(f"workable {token}: no jobs key")
    jobs = []
    for j in data["jobs"]:
        code = j.get("shortcode") or j.get("code")
        if not code:
            continue
        loc = _util.join_locations(j.get("city"), j.get("state"), j.get("country"))
        jobs.append(Job(ats_job_id=str(code), title=(j.get("title") or "").strip(),
                        location=loc, url=j.get("url") or j.get("shortlink") or "",
                        published_at=j.get("published_on") or j.get("created_at"),
                        company_name=data.get("name"), country=j.get("country"),
                        remote=True if j.get("telecommuting") else None))
    return jobs


def mentions_domain(token, domain):
    try:
        _, raw = _util.get_json(f"workable {token}", _url(token))
    except Exception:
        return False
    return _util.text_mentions(raw, domain)
