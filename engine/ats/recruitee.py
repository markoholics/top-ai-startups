"""Recruitee Careers Site API.

Checked against https://docs.recruitee.com/reference/offers
  GET https://{company}.recruitee.com/api/offers/
  Response: {"offers": [{id, title, location, city, country, country_code,
             remote, careers_url, published_at, status, ...}]}
  Unknown company: HTTP 404.
"""
from . import _util
from .base import BoardError, Job

NAME = "recruitee"


def _url(token):
    return f"https://{token}.recruitee.com/api/offers/"


def fetch_jobs(token):
    data, _ = _util.get_json(f"recruitee {token}", _url(token))
    if not isinstance(data, dict) or "offers" not in data:
        raise BoardError(f"recruitee {token}: no offers key")
    jobs = []
    for j in data["offers"]:
        if j.get("status") not in (None, "published"):
            continue
        locs = [x.get("location") or x.get("city") for x in (j.get("locations") or [])]
        loc = _util.join_locations(j.get("location"), *locs) or _util.join_locations(j.get("city"), j.get("country"))
        jobs.append(Job(ats_job_id=str(j["id"]), title=(j.get("title") or "").strip(),
                        location=loc, url=j.get("careers_url") or "", published_at=j.get("published_at"),
                        company_name=j.get("company_name"),
                        country=j.get("country_code") or j.get("country"), remote=j.get("remote")))
    return jobs


def mentions_domain(token, domain):
    try:
        _, raw = _util.get_json(f"recruitee {token}", _url(token))
    except Exception:
        return False
    return _util.text_mentions(raw, domain)
