"""Find a company's ATS by reading its own careers page.

Fetches the home page and up to two careers links, through the polite client
(robots.txt obeyed). Looks for links to known ATS hosts and pulls out the
board token. A page that builds its links with JavaScript will show nothing.
That is a known limit, and such companies go to needs_manual_ats.
"""
import re
import urllib.parse

from . import http

# ats name -> regex with one capture group for the board token
PATTERNS = {
    "greenhouse": re.compile(
        r"(?:boards|job-boards)(?:-api)?\.(?:eu\.)?greenhouse\.io/(?:embed/job_board\?for=|v1/boards/)?([A-Za-z0-9_-]+)", re.I),
    "lever": re.compile(r"jobs\.(?:eu\.)?lever\.co/([A-Za-z0-9_.-]+)", re.I),
    "ashby": re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)", re.I),
    "workable": re.compile(r"apply\.workable\.com/([A-Za-z0-9_-]+)", re.I),
    "smartrecruiters": re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([A-Za-z0-9_-]+)", re.I),
    "recruitee": re.compile(r"([A-Za-z0-9-]+)\.recruitee\.com", re.I),
}
IGNORE_TOKENS = {"embed", "v1", "boards", "api", "www", "careers", "jobs", "static", "assets"}
HREF = re.compile(r'href=["\']([^"\']+)["\']', re.I)
CAREERS_HINT = re.compile(r"career|jobs|join-us|join|work-with-us|hiring", re.I)


def tokens_in(html):
    found = []
    for ats, pat in PATTERNS.items():
        for m in pat.finditer(html):
            tok = m.group(1)
            if tok.lower() not in IGNORE_TOKENS and (ats, tok) not in found:
                found.append((ats, tok))
    return found


def find_ats(domain):
    """Return (list of (ats, token), careers_url or None, notes list)."""
    notes, base = [], f"https://{domain}"
    home = http.get(base)
    if home.status != 200:
        notes.append(f"home page: {'robots.txt forbids' if home.status == -1 else 'HTTP %s' % home.status}")
        return [], None, notes
    html = home.text()
    found = tokens_in(html)
    careers_url = None
    candidates = []
    for href in HREF.findall(html):
        if CAREERS_HINT.search(href) and not href.startswith(("mailto:", "#", "javascript:")):
            full = urllib.parse.urljoin(home.url, href)
            host = urllib.parse.urlsplit(full).netloc
            if full not in candidates and host.endswith(domain.split("/")[0]):
                candidates.append(full)
    for url in candidates[:2]:
        careers_url = careers_url or url
        page = http.get(url)
        if page.status == 200:
            for pair in tokens_in(page.text()):
                if pair not in found:
                    found.append(pair)
        else:
            notes.append(f"{url}: {'robots.txt forbids' if page.status == -1 else 'HTTP %s' % page.status}")
    if not found:
        notes.append("no ATS link in the page text (it may be built with JavaScript)")
    return found, careers_url, notes
