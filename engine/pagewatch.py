"""Careers page watch, for companies with no ATS access.
Fetches the page politely (robots.txt obeyed), reduces it to its visible text
and hashes that. Scripts and styles are ignored so ordinary page noise does not
count as a change. A changed hash is only a weak signal."""
import hashlib
import re

from . import http


def visible_text(html):
    html = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?s)<!--.*?-->", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip().lower()


def check(url):
    """Return (http_status, text_hash or None). Status -1 means robots.txt forbids it,
    0 means a network error."""
    r = http.get(url)
    if r.status != 200:
        return r.status, None
    text = visible_text(r.text())
    return 200, hashlib.sha256(text.encode("utf-8")).hexdigest()
