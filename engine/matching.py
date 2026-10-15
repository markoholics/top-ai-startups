"""Role matching and location filtering. All rules come from config/roles.yaml
and config/config.yaml. Nothing about your target roles is written in code.

Title matching
  1. Normalise: lowercase, punctuation to spaces, abbreviations expanded.
  2. Score against every include title, 0 to 100. A whole word run that
     contains the include title scores 100. Otherwise the closest run of the
     same length is compared with fuzzy matching.
  3. At or above the threshold it is a match, unless an exclude term is present.
  4. Between the floor and the threshold, or matched but excluded, it is logged
     as a near miss so you can tune the lists.
Seniority never filters. VP and Director add a flag and lower the score a little.
"""
import re
from dataclasses import dataclass, field
from typing import List, Optional

from rapidfuzz import fuzz

from .config import load

_roles = load("roles.yaml")
_cfg = load("config.yaml")

MATCH_AT = float(_roles["fuzzy"]["match_threshold"])
FLOOR = float(_roles["fuzzy"]["near_miss_floor"])
SENIOR_LABEL = _roles["seniority"]["senior_label"]
SENIOR_MOD = float(_roles["seniority"]["score_modifier"]["senior"])


def normalise(text):
    t = (text or "").lower().replace("&", " and ")
    t = re.sub(r"[^a-z0-9]+", " ", t)           # punctuation and hyphens become spaces
    words = []
    for w in t.split():
        words.extend(_ABBREV.get(w, w).split())
    return " ".join(words)


_ABBREV = {k.lower(): re.sub(r"[^a-z0-9]+", " ", v.lower()).strip()
           for k, v in _roles["abbreviations"].items()}


def _phrases(key):
    return [normalise(p) for p in _roles[key]]


INCLUDE = _phrases("include_titles")
EXCLUDE = _phrases("exclude_titles")
SENIOR = [normalise(t) for t in _roles["seniority"]["senior_terms"]]
MARKETING = [normalise(t) for t in _roles["marketing_terms"]]
ADJACENT = [normalise(t) for t in _roles["adjacent_terms"]]


def _has_phrase(norm_title, phrase):
    """Whole word run match."""
    return bool(phrase) and re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", norm_title) is not None


def _score_one(words, phrase):
    n = len(phrase.split())
    if _has_phrase(" ".join(words), phrase):
        return 100.0
    best = 0.0
    for size in {max(1, n - 1), n, n + 1}:
        for i in range(0, max(1, len(words) - size + 1)):
            window = " ".join(words[i:i + size])
            best = max(best, fuzz.ratio(window, phrase))
    return float(best)


@dataclass
class TitleResult:
    title: str
    normalised: str
    is_match: bool
    score: float                      # 0 to 100, after the seniority modifier
    best_include: Optional[str]
    seniority_flag: Optional[str] = None
    excluded_by: Optional[str] = None
    near_miss_reason: Optional[str] = None
    kind: Optional[str] = None        # 'match' | 'marketing' | 'adjacent' | None


def match_title(title):
    norm = normalise(title)
    words = norm.split()
    best, best_phrase = 0.0, None
    for phrase in INCLUDE:
        s = _score_one(words, phrase)
        if s > best:
            best, best_phrase = s, phrase
    excluded_by = next((e for e in EXCLUDE if _has_phrase(norm, e)), None)
    senior = next((s for s in SENIOR if _has_phrase(norm, s)), None)
    is_match = best >= MATCH_AT and excluded_by is None
    reason = None
    if not is_match and best >= FLOOR:
        if excluded_by is None:
            # Only worth logging when the title has a marketing or growth word.
            # Otherwise "Product Manager" and "Engineering Manager" bury the useful ones.
            if any(_has_phrase(norm, m) for m in MARKETING):
                reason = "below threshold"
        elif best >= MATCH_AT:
            reason = f"excluded term: {excluded_by}"   # would have matched but for the exclusion
    kind = None
    if is_match:
        kind = "match"
    elif excluded_by is None:
        if any(_has_phrase(norm, m) for m in MARKETING):
            kind = "marketing"
        elif any(_has_phrase(norm, a) for a in ADJACENT):
            kind = "adjacent"
    score = round(best + (SENIOR_MOD if senior else 0), 2)
    return TitleResult(title=title, normalised=norm, is_match=is_match, score=score,
                       best_include=best_phrase, seniority_flag=SENIOR_LABEL if senior else None,
                       excluded_by=excluded_by, near_miss_reason=reason, kind=kind)


# ---------------------------------------------------------------------------
# Location
# ---------------------------------------------------------------------------
_INDIA = re.compile(r"(?<![a-z])(india|bengaluru|bangalore)(?![a-z])")
_WORLDWIDE = re.compile(r"(?<![a-z])(worldwide|global|globally|anywhere|world wide)(?![a-z])")
_APAC = re.compile(r"(?<![a-z])(apac|asia pacific|asia-pacific|asia)(?![a-z])")
_REMOTE = re.compile(r"(?<![a-z])(remote|remotely|work from home|wfh|telecommute|distributed)(?![a-z])")
_FILLER = re.compile(
    r"(?<![a-z])(remote|remotely|fully|flexible|full time|full-time|part time|part-time|permanent|"
    r"contract|contractor|work from home|wfh|telecommute|distributed|location|based|or|and|from|"
    r"anywhere in|eligible|time|hours|only|role|position|job|the|a|an|in|within|for|of)(?![a-z])")
_SPLIT = re.compile(r"\s*(?:\||;|/|\n|\bor\b)\s*", re.I)


def _segment_ok(seg):
    s = seg.lower().strip()
    if not s:
        return False, ""
    if _INDIA.search(s):
        return True, "India named"
    if _REMOTE.search(s):
        if _WORLDWIDE.search(s):
            return True, "remote worldwide"
        if _APAC.search(s):
            return True, "remote APAC"
        rest = re.sub(r"[^a-z0-9 ]+", " ", _FILLER.sub(" ", s))
        rest = re.sub(r"\s+", " ", rest).strip()
        if not rest:
            return True, "remote, no region stated"
        return False, f"remote but region is '{rest}'"
    return False, f"onsite or hybrid in '{s}'"


def location_ok(location, country=None, remote=None):
    """Return (ok, reason). A role passes if any one of its locations passes."""
    country = (country or "").strip().lower()
    if country in ("in", "ind", "india"):
        return True, "country is India"
    segments = [x for x in _SPLIT.split(location or "") if x.strip()]
    reasons = []
    for seg in segments:
        ok, why = _segment_ok(seg)
        if ok:
            return True, why
        reasons.append(why)
    if not segments:
        # No location text at all.
        if remote is True and not country:
            return True, "remote, no region stated"
        if remote is True and country:
            return False, f"remote, country is {country}"
        return False, "no location given"
    # Text says a place, but the ATS flags the role as remote with no better detail.
    return False, "; ".join(reasons) or "no match"
