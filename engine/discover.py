"""ATS auto-discovery: try likely board tokens on each known ATS.

A guessed token can land on the wrong company (a board called "sunday" may
belong to someone else). So a hit is only accepted when the ATS reports a
company name that is close to ours. Otherwise it is returned as 'unconfirmed'.
"""
import re
from difflib import SequenceMatcher

from . import careers
from .ats import ADAPTERS
from .ats.base import BoardError, BoardNotFound


# Vendors that answered 429 this run. Not retried until the next run.
RATE_LIMITED = set()


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def slug_candidates(name, domain=None):
    words = re.findall(r"[a-z0-9]+", (name or "").lower().replace("&", " and "))
    words_no_the = [w for w in words if w != "the"]
    out = []

    def add(s):
        if s and s not in out:
            out.append(s)

    add("".join(words))
    add("".join(words_no_the))
    add("-".join(words_no_the))
    add("_".join(words_no_the))
    if len(words_no_the) > 1 and words_no_the[-1] in ("ai", "labs", "robotics", "inc", "co"):
        add("".join(words_no_the[:-1]))
    if domain:
        label = domain.lower().split("/")[0].split(".")[0]
        add(re.sub(r"[^a-z0-9-]", "", label))
    return out


def same_company(ours, reported):
    a, b = norm(ours), norm(reported)
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    return SequenceMatcher(None, a, b).ratio() >= 0.85


def discover(name, domain=None, adapters=None):
    """Work out a company's ATS.

    Returns a dict with:
      ats_type, ats_token, jobs
      confidence: 'confirmed'    the company's careers page links to this board, or the
                                 board's job descriptions name the company's website
                  'unconfirmed'  a board with a guessed token exists, but nothing ties it to
                                 this company. Shown to you, never used.
                  None           nothing found
      careers_url, notes, errors
    Only 'confirmed' is used by the engine.
    """
    adapters = adapters or ADAPTERS
    out = {"ats_type": None, "ats_token": None, "confidence": None, "jobs": None,
           "careers_url": None, "notes": [], "errors": []}

    # 1. The company's own careers page.
    if domain:
        found, careers_url, notes = careers.find_ats(domain)
        out["careers_url"], out["notes"] = careers_url, notes
        for ats_name, token in found:
            if ats_name not in adapters:
                out["notes"].append(f"page links to {ats_name}:{token} (adapter not built yet)")
                continue
            try:
                jobs = adapters[ats_name].fetch_jobs(token)
            except BoardNotFound:
                out["notes"].append(f"page links to {ats_name}:{token} but the board does not exist")
                continue
            except BoardError as e:
                out["errors"].append(str(e))
                continue
            out.update(ats_type=ats_name, ats_token=token, confidence="confirmed", jobs=jobs)
            return out

    # 2. Guess likely tokens.
    guess = None
    for slug in slug_candidates(name, domain):
        for ats_name, adapter in adapters.items():
            if ats_name in RATE_LIMITED:
                continue
            if ats_name in ("recruitee", "workable") and not re.fullmatch(r"[a-z0-9-]+", slug):
                continue                      # these tokens are DNS labels or URL parts
            try:
                jobs = adapter.fetch_jobs(slug)
            except BoardNotFound:
                continue
            except BoardError as e:
                out["errors"].append(str(e))
                if "HTTP 429" in str(e):
                    RATE_LIMITED.add(ats_name)      # stop asking for the rest of this run
                continue
            reported = next((j.company_name for j in jobs if j.company_name), None)
            name_ok = bool(reported) and same_company(name, reported)
            # A name match alone is never trusted. "Figure" matched a lender,
            # not the robotics company. Only the careers page can confirm.
            hit = {"ats_type": ats_name, "ats_token": slug, "confidence": "unconfirmed",
                   "jobs": jobs, "reported_name": reported, "name_looks_right": name_ok}
            # Second proof: the board's own job descriptions name the company's website.
            check = getattr(adapter, "mentions_domain", None)
            # Some ATSs (Ashby, Lever) never report a company name. Then the website
            # check is the only proof available, so run it. A reported name that
            # does not match (Team Rubicon) is a firm no.
            if domain and check and (name_ok or reported is None) and check(slug, domain):
                hit["confidence"] = "confirmed"
                out["notes"].append(f"confirmed because job descriptions mention {domain}")
                out.update(hit)
                return out
            guess = guess or hit
    if guess:
        out.update(guess)
    return out
