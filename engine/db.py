"""Supabase access over its REST API (PostgREST). No extra libraries.

SupabaseStore is the only class that talks to the database. The runner uses
the small set of methods below, so tests can swap in an in-memory store.
Reads SUPABASE_URL and SUPABASE_SERVICE_KEY from the environment.
"""
import datetime as dt
import json
import os
import urllib.error
import urllib.parse
import urllib.request

PAGE = 1000


class StoreError(Exception):
    pass


def _ts(value):
    """Datetime to ISO text for JSON. Leaves other values alone."""
    return value.isoformat() if isinstance(value, dt.datetime) else value


def _parse(value):
    if isinstance(value, str) and len(value) >= 19 and value[4] == "-" and value[10] == "T":
        try:
            return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    return value


def _row_in(row):
    return {k: _parse(v) for k, v in row.items()}


def _row_out(row):
    return {k: _ts(v) for k, v in row.items()}


class SupabaseStore:
    def __init__(self):
        self.base = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
        self.key = os.environ.get("SUPABASE_SERVICE_KEY") or ""
        if not self.base or not self.key:
            raise StoreError("SUPABASE_URL or SUPABASE_SERVICE_KEY is not set.")
        self._companies = None

    # -- plumbing -----------------------------------------------------------
    def _call(self, method, path, params=None, body=None, prefer=None):
        url = f"{self.base}/rest/v1/{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params, safe=",.()*:")
        headers = {"apikey": self.key, "Authorization": f"Bearer {self.key}",
                   "Content-Type": "application/json", "Accept": "application/json"}
        if prefer:
            headers["Prefer"] = prefer
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            raise StoreError(f"{method} {path}: HTTP {e.code} {detail}")
        except (urllib.error.URLError, OSError) as e:
            raise StoreError(f"{method} {path}: {e}")
        return json.loads(raw) if raw else None

    def select(self, table, params=None):
        out, offset = [], 0
        while True:
            p = dict(params or {})
            p.setdefault("select", "*")
            p["limit"], p["offset"] = PAGE, offset
            rows = self._call("GET", table, p) or []
            out += [_row_in(r) for r in rows]
            if len(rows) < PAGE:
                return out
            offset += PAGE

    def _chunks(self, items, n=80):
        items = list(items)
        for i in range(0, len(items), n):
            yield items[i:i + n]

    def _uniform(self, rows):
        """PostgREST rejects a bulk write whose rows have different keys. Give every
        row the same keys, with null for a missing one."""
        keys = []
        for r in rows:
            for k in r:
                if k not in keys:
                    keys.append(k)
        return [{k: r.get(k) for k in keys} for r in rows]

    def _in(self, values):
        return "in.(" + ",".join(str(v) for v in values) + ")"

    # -- schema check -------------------------------------------------------
    REQUIRED = {
        "watch_company": ["id", "name", "domain", "tier", "source_tags", "ats_type", "ats_token", "verified",
                          "needs_manual_ats", "careers_url", "ats_checked_at", "note"],
        "job_posting": ["company_id", "ats_job_id", "title", "location", "url", "first_seen_at", "last_seen_at", "closed_at",
                        "title_match_score", "location_ok", "is_match", "normalised_title", "seniority_flag",
                        "likely_repost", "role_kind"],
        "job_event": ["company_id", "posting_id", "event_type", "weight", "evidence_url", "observed_at", "dedupe_key", "detail"],
        "digest_log": ["event_ids", "subject", "delivered", "error"],
        "engine_run": ["kind", "started_at", "finished_at", "companies_ok", "companies_failed", "notes"],
        "company_snapshot": ["company_id", "run_id", "observed_at", "open_role_count", "careers_hash", "careers_status"],
        "title_near_miss": ["company_id", "posting_id", "title", "best_match", "score", "reason", "observed_at"],
    }

    def check_schema(self):
        """Return a list of plain problems, empty when every table and column the engine needs is there.
        Reads one row at most from each table, so it is cheap."""
        problems = []
        for table, cols in self.REQUIRED.items():
            try:
                self._call("GET", table, {"select": ",".join(cols), "limit": 1})
            except StoreError as e:
                text = str(e)
                if "PGRST205" in text or "does not exist" in text and "relation" in text:
                    problems.append(f"table {table} is missing")
                    continue
                # One column at a time, to name the ones that are missing.
                missing = []
                for c in cols:
                    try:
                        self._call("GET", table, {"select": c, "limit": 1})
                    except StoreError:
                        missing.append(c)
                problems.append(f"{table} is missing: {', '.join(missing) if missing else 'a column (' + text[:120] + ')'}")
        return problems

    # -- companies ----------------------------------------------------------
    def companies(self):
        rows = self.select("watch_company", {"order": "name"})
        self._companies = {r["id"]: r for r in rows}
        return rows

    def update_company(self, company_id, fields):
        self._call("PATCH", "watch_company", {"id": f"eq.{company_id}"}, _row_out(fields))

    # -- postings -----------------------------------------------------------
    def postings_for(self, company_id):
        rows = self.select("job_posting", {"company_id": f"eq.{company_id}"})
        return {r["ats_job_id"]: r for r in rows}

    def upsert_postings(self, rows):
        for chunk in self._chunks(rows, 200):
            self._call("POST", "job_posting", {"on_conflict": "company_id,ats_job_id"},
                       self._uniform([_row_out(r) for r in chunk]), "resolution=merge-duplicates,return=minimal")

    def close_postings(self, company_id, ats_job_ids, closed_at):
        for chunk in self._chunks(ats_job_ids, 50):
            self._call("PATCH", "job_posting",
                       {"company_id": f"eq.{company_id}", "ats_job_id": self._in(chunk)},
                       {"closed_at": _ts(closed_at)})

    def posting_ids(self, company_id):
        rows = self.select("job_posting", {"company_id": f"eq.{company_id}", "select": "id,ats_job_id"})
        return {r["ats_job_id"]: r["id"] for r in rows}

    # -- events and logs ----------------------------------------------------
    def insert_events(self, rows):
        for chunk in self._chunks(rows, 200):
            self._call("POST", "job_event", {"on_conflict": "dedupe_key"},
                       self._uniform([_row_out(r) for r in chunk]), "resolution=ignore-duplicates,return=minimal")

    def insert_near_misses(self, rows):
        for chunk in self._chunks(rows, 200):
            self._call("POST", "title_near_miss", {"on_conflict": "posting_id,reason"},
                       self._uniform([_row_out(r) for r in chunk]), "resolution=ignore-duplicates,return=minimal")

    def last_snapshot(self, company_id):
        rows = self._call("GET", "company_snapshot", {"company_id": f"eq.{company_id}", "order": "observed_at.desc", "limit": 1})
        return _row_in(rows[0]) if rows else None

    def insert_snapshot(self, row):
        self._call("POST", "company_snapshot", None, _row_out(row), "return=minimal")

    def start_run(self, kind, now):
        rows = self._call("POST", "engine_run", None, {"kind": kind, "started_at": _ts(now)}, "return=representation")
        return rows[0]["id"]

    def finish_run(self, run_id, ok, failed, notes, now):
        self._call("PATCH", "engine_run", {"id": f"eq.{run_id}"},
                   {"finished_at": _ts(now), "companies_ok": ok, "companies_failed": failed, "notes": notes})

    # -- digests ------------------------------------------------------------
    def baseline_delivered(self):
        rows = self._call("GET", "digest_log", {"delivered": "eq.true", "subject": "eq.Job Signal Engine: baseline complete",
                                                "select": "id", "limit": 1})
        return bool(rows)

    def log_digest(self, event_ids, subject):
        rows = self._call("POST", "digest_log", None, {"event_ids": list(event_ids), "subject": subject, "delivered": False},
                          "return=representation")
        return rows[0]["id"]

    def finish_digest(self, digest_id, delivered, error):
        self._call("PATCH", "digest_log", {"id": f"eq.{digest_id}"}, {"delivered": delivered, "error": error})

    # -- joined reads for the email ----------------------------------------
    def _company(self, company_id):
        if self._companies is None:
            self.companies()
        return self._companies.get(company_id, {})

    def unsent_events(self):
        events = self.select("v_unsent_events", {"order": "observed_at"})
        ids = [e["posting_id"] for e in events if e.get("posting_id")]
        postings = {}
        for chunk in self._chunks(set(ids)):
            for p in self.select("job_posting", {"id": self._in(chunk)}):
                postings[p["id"]] = p
        out = []
        for e in events:
            c = self._company(e["company_id"])
            out.append(dict(e, company=c.get("name", "Unknown"), tier=c.get("tier", ""), source_tags=c.get("source_tags") or [],
                            ats_type=c.get("ats_type") or "", posting=postings.get(e.get("posting_id"))))
        return out

    def recent_events(self, company_ids, since):
        out = []
        for chunk in self._chunks(set(company_ids)):
            out += self.select("job_event", {"company_id": self._in(chunk), "observed_at": f"gte.{_ts(since)}"})
        return out

    def open_matches(self):
        rows = self.select("job_posting", {"closed_at": "is.null", "is_match": "eq.true", "location_ok": "eq.true"})
        out = []
        for p in rows:
            c = self._company(p["company_id"])
            out.append(dict(p, company=c.get("name", "Unknown"), tier=c.get("tier", ""),
                            source_tags=c.get("source_tags") or [], ats_type=c.get("ats_type") or ""))
        return out

    def hidden_count(self):
        rows = self.select("job_posting", {"closed_at": "is.null", "is_match": "eq.true", "location_ok": "eq.false", "select": "id"})
        return len(rows)
