"""Polite HTTP client.

- One request per host per second.
- An honest user agent, from config.
- robots.txt is obeyed for company web pages.
- Documented public job board APIs (api=True) skip robots.txt. They exist for
  programmatic reads, and some of them serve no robots.txt at all.
"""
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser

from .config import load

_cfg = load("config.yaml")["http"]
USER_AGENT = _cfg["user_agent"]
MIN_GAP = float(_cfg["min_seconds_between_requests_per_host"])
TIMEOUT = float(_cfg["timeout_seconds"])
RESPECT_ROBOTS = bool(_cfg["respect_robots_txt"])

_last_hit = {}      # host -> monotonic time of last request
_robots = {}        # host -> RobotFileParser or None (no rules)


class Response:
    def __init__(self, status, body, url):
        self.status, self.body, self.url = status, body, url

    def text(self):
        return self.body.decode("utf-8", errors="replace")


def _throttle(host):
    wait = MIN_GAP - (time.monotonic() - _last_hit.get(host, 0))
    if wait > 0:
        time.sleep(wait)
    _last_hit[host] = time.monotonic()


def _raw_get(url, accept="*/*"):
    host = urllib.parse.urlsplit(url).netloc
    _throttle(host)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return Response(r.status, r.read(), url)
    except urllib.error.HTTPError as e:
        return Response(e.code, e.read() or b"", url)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return Response(0, str(e).encode(), url)


def _allowed(url):
    parts = urllib.parse.urlsplit(url)
    host = parts.netloc
    if host not in _robots:
        r = _raw_get(f"{parts.scheme}://{host}/robots.txt", "text/plain")
        if r.status == 200:
            rp = urllib.robotparser.RobotFileParser()
            rp.parse(r.text().splitlines())
            _robots[host] = rp
        elif 400 <= r.status < 500:
            _robots[host] = None          # no robots.txt: nothing is forbidden
        else:
            _robots[host] = "deny"        # server error or unreachable: be careful
    rp = _robots[host]
    if rp == "deny":
        return False
    return True if rp is None else rp.can_fetch(USER_AGENT, url)


def get(url, api=False, accept="*/*", retries=1):
    """GET with politeness. Returns Response. status 0 means a network error,
    status -1 means robots.txt forbids the page."""
    if RESPECT_ROBOTS and not api and not _allowed(url):
        return Response(-1, b"blocked by robots.txt", url)
    r = _raw_get(url, accept)
    if r.status in (429, 500, 502, 503, 504) and retries > 0:
        time.sleep(3)
        return get(url, api, accept, retries - 1)
    return r
