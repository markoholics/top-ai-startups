"""Lever postings API.

Checked against https://github.com/lever/postings-api (README)
  GET https://api.lever.co/v0/postings/{site}?mode=json
  Response: a list of {id, text, categories{location, allLocations[], team},
            country, workplaceType, hostedUrl, applyUrl, createdAt (ms)}
  Unknown site: HTTP 404 {"ok": false, "error": "Document not found"}.
EU hosted accounts use api.eu.lever.co. Only the global host is tried here.
"""
import datetime

from . import _util
from .base import BoardError, Job

NAME = "lever"


def _url(token):
    return f"https://api.lever.co/v0/postings/{token}?mode=json"


def fetch_jobs(token):
    data, _ = _util.get_json(f"lever {token}", _url(token))
    if not isinstance(data, list):
        raise BoardError(f"lever {token}: expected a list")
    jobs = []
    for j in data:
        cats = j.get("categories") or {}
        loc = _util.join_locations(cats.get("location"), *(cats.get("allLocations") or []))
        created = j.get("createdAt")
        published = (datetime.datetime.fromtimestamp(created / 1000, datetime.timezone.utc).isoformat()
                     if isinstance(created, (int, float)) else None)
        wt = (j.get("workplaceType") or "").lower()
        jobs.append(Job(ats_job_id=str(j["id"]), title=(j.get("text") or "").strip(),
                        location=loc, url=j.get("hostedUrl") or "", published_at=published,
                        country=j.get("country"), remote=True if wt == "remote" else None))
    return jobs


def mentions_domain(token, domain):
    try:
        _, raw = _util.get_json(f"lever {token}", _url(token))
    except Exception:
        return False
    return _util.text_mentions(raw, domain)
