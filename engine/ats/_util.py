import json

from .. import http
from .base import BoardError, BoardNotFound


def get_json(label, url):
    """GET a JSON document from a documented public job board API.
    404 means the board does not exist. Anything else unexpected is a BoardError."""
    r = http.get(url, api=True, accept="application/json")
    if r.status == 404:
        raise BoardNotFound(label)
    if r.status != 200:
        raise BoardError(f"{label}: HTTP {r.status}")
    try:
        return json.loads(r.body), r.text()
    except ValueError as e:
        raise BoardError(f"{label}: not JSON ({e})")


def join_locations(*parts):
    seen, out = set(), []
    for p in parts:
        p = (p or "").strip()
        if p and p.lower() not in seen:
            seen.add(p.lower())
            out.append(p)
    return " | ".join(out)


def text_mentions(raw_text, domain):
    needle = domain.lower().split("/")[0]
    return needle in raw_text.lower().replace("\\/", "/")
