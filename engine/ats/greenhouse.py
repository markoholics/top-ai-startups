"""Greenhouse Job Board API.

Checked against https://developers.greenhouse.io/job-board.html
  GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs
  Response: {"jobs": [{id, title, location: {name}, absolute_url, updated_at,
                       company_name?, ...}], "meta": {"total": n}}
  Unknown board token: HTTP 404.
"""
import json

from .. import http
from .base import BoardError, BoardNotFound, Job

NAME = "greenhouse"


def fetch_jobs(token):
    r = http.get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
                 api=True, accept="application/json")
    if r.status == 404:
        raise BoardNotFound(token)
    if r.status != 200:
        raise BoardError(f"greenhouse {token}: HTTP {r.status}")
    try:
        data = json.loads(r.body)
        raw = data["jobs"]
    except (ValueError, KeyError, TypeError) as e:
        raise BoardError(f"greenhouse {token}: bad response ({e})")
    jobs = []
    for j in raw:
        jobs.append(Job(
            ats_job_id=str(j["id"]),
            title=(j.get("title") or "").strip(),
            location=((j.get("location") or {}).get("name") or "").strip(),
            url=j.get("absolute_url") or "",
            updated_at=j.get("updated_at"),
            company_name=j.get("company_name"),
        ))
    return jobs


def mentions_domain(token, domain):
    """True if any job description on the board names the company's own website.
    Used as a second proof that a board belongs to a company. Uses ?content=true,
    which returns the full description of every job."""
    r = http.get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true",
                 api=True, accept="application/json")
    if r.status != 200:
        return False
    needle = domain.lower().split("/")[0]
    # Descriptions are HTML escaped inside JSON, so match on the plain text of the page.
    return needle in r.text().lower().replace("\\/", "/")
