"""Ashby public job posting API.

Checked against https://developers.ashbyhq.com/docs/public-job-posting-api
  GET https://api.ashbyhq.com/posting-api/job-board/{JOB_BOARD_NAME}
  Response: {"apiVersion", "jobs": [{title, location, secondaryLocations[{location}],
             isListed, isRemote, workplaceType, publishedAt, jobUrl, applyUrl, ...}]}
  Unknown board: HTTP 404.
Note: the docs example shows no job id. Live responses carry "id", so we use it
and fall back to the last part of jobUrl.
"""
from . import _util
from .base import BoardError, Job

NAME = "ashby"


def _url(token):
    return f"https://api.ashbyhq.com/posting-api/job-board/{token}"


def fetch_jobs(token):
    data, _ = _util.get_json(f"ashby {token}", _url(token))
    if not isinstance(data, dict) or "jobs" not in data:
        raise BoardError(f"ashby {token}: no jobs key")
    jobs = []
    for j in data["jobs"]:
        if j.get("isListed") is False:
            continue                          # not shown on the public board
        url = j.get("jobUrl") or ""
        job_id = j.get("id") or url.rstrip("/").rsplit("/", 1)[-1]
        if not job_id:
            continue
        extra = [s.get("location") for s in (j.get("secondaryLocations") or [])]
        addr = ((j.get("address") or {}).get("postalAddress") or {})
        loc = _util.join_locations(j.get("location"), *extra)
        remote = True if (j.get("isRemote") or j.get("workplaceType") == "Remote") else j.get("isRemote")
        jobs.append(Job(ats_job_id=str(job_id), title=(j.get("title") or "").strip(),
                        location=loc, url=url, published_at=j.get("publishedAt"),
                        country=addr.get("addressCountry"), remote=remote))
    return jobs


def mentions_domain(token, domain):
    try:
        _, raw = _util.get_json(f"ashby {token}", _url(token))
    except Exception:
        return False
    return _util.text_mentions(raw, domain)
