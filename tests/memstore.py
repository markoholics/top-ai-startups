"""An in-memory stand in for SupabaseStore. Same methods, no network."""
import datetime as dt
import itertools

_ids = itertools.count(1)


def _id(prefix):
    return f"{prefix}{next(_ids)}"


class MemoryStore:
    def __init__(self, companies):
        self.co = {}
        for c in companies:
            row = {"id": _id("c"), "domain": None, "tier": "seed", "source_tags": [], "ats_type": None, "ats_token": None,
                   "verified": False, "needs_manual_ats": False, "careers_url": None, "ats_checked_at": None, "note": None}
            row.update(c)
            self.co[row["id"]] = row
        self.posts, self.events, self.near, self.snaps, self.runs, self.digests = [], [], [], [], [], []

    # companies
    def companies(self):
        # Copies, like the real database returns. A dry run changes them in memory only.
        return [dict(c) for c in sorted(self.co.values(), key=lambda c: c["name"])]

    def update_company(self, cid, fields):
        self.co[cid].update(fields)

    # postings
    def postings_for(self, cid):
        return {p["ats_job_id"]: p for p in self.posts if p["company_id"] == cid}

    def upsert_postings(self, rows):
        for r in rows:
            cur = next((p for p in self.posts if p["company_id"] == r["company_id"] and p["ats_job_id"] == r["ats_job_id"]), None)
            if cur:
                cur.update(r)
            else:
                self.posts.append(dict(r, id=_id("p")))

    def close_postings(self, cid, ids, when):
        for p in self.posts:
            if p["company_id"] == cid and p["ats_job_id"] in ids:
                p["closed_at"] = when

    def posting_ids(self, cid):
        return {p["ats_job_id"]: p["id"] for p in self.posts if p["company_id"] == cid}

    # events
    def insert_events(self, rows):
        have = {e["dedupe_key"] for e in self.events}
        for r in rows:
            if r["dedupe_key"] not in have:
                self.events.append(dict(r, id=_id("e")))
                have.add(r["dedupe_key"])

    def insert_near_misses(self, rows):
        for r in rows:
            if not any(n["posting_id"] == r["posting_id"] and n["reason"] == r["reason"] for n in self.near):
                self.near.append(r)

    def last_snapshot(self, cid):
        mine = [s for s in self.snaps if s["company_id"] == cid]
        return mine[-1] if mine else None

    def insert_snapshot(self, row):
        self.snaps.append(row)

    def start_run(self, kind, now):
        self.runs.append({"kind": kind, "started": now})
        return f"run{len(self.runs)}"

    def finish_run(self, run_id, ok, failed, notes, now):
        self.runs[-1].update(ok=ok, failed=failed, notes=notes)

    # digests
    def baseline_delivered(self):
        return any(d["delivered"] and d["subject"] == "Job Signal Engine: baseline complete" for d in self.digests)

    def log_digest(self, event_ids, subject):
        self.digests.append({"event_ids": list(event_ids), "subject": subject, "delivered": False, "error": None})
        return len(self.digests) - 1

    def finish_digest(self, i, delivered, error):
        self.digests[i].update(delivered=delivered, error=error)

    # joined reads
    def unsent_events(self):
        sent = {e for d in self.digests if d["delivered"] for e in d["event_ids"]}
        out = []
        for e in self.events:
            if e["id"] in sent:
                continue
            c = self.co[e["company_id"]]
            post = next((p for p in self.posts if p["id"] == e.get("posting_id")), None)
            out.append(dict(e, company=c["name"], tier=c["tier"], source_tags=c["source_tags"], ats_type=c["ats_type"] or "", posting=post))
        return out

    def recent_events(self, company_ids, since):
        return [e for e in self.events if e["company_id"] in company_ids and e["observed_at"] >= since]

    def open_matches(self):
        out = []
        for p in self.posts:
            if p.get("closed_at") is None and p["is_match"] and p["location_ok"]:
                c = self.co[p["company_id"]]
                out.append(dict(p, company=c["name"], tier=c["tier"], source_tags=c["source_tags"], ats_type=c["ats_type"] or ""))
        return out

    def hidden_count(self):
        return sum(1 for p in self.posts if p.get("closed_at") is None and p["is_match"] and not p["location_ok"])
