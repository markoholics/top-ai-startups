import datetime as dt
import logging
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from engine import run as runmod  # noqa: E402
from engine.ats.base import BoardError, Job  # noqa: E402
from engine.mail.gmail_smtp import MailError  # noqa: E402
from memstore import MemoryStore  # noqa: E402

T0 = dt.datetime(2026, 10, 14, 1, 0, tzinfo=dt.timezone.utc)      # a Wednesday, 06:30 IST


def at(hours):
    return T0 + dt.timedelta(hours=hours)


def job(i, title, loc):
    return Job(ats_job_id=str(i), title=title, location=loc, url=f"https://fixture.example/jobs/{i}")


class FakeAts:
    def __init__(self):
        self.boards = {}

    def fetch_jobs(self, token):
        b = self.boards[token]
        if isinstance(b, Exception):
            raise b
        return list(b)


class Mailer:
    def __init__(self, fail=False):
        self.fail, self.sent = fail, []

    def send(self, to, subject, text, html=None):
        if self.fail:
            raise MailError("SMTPAuthenticationError: simulated outage")
        self.sent.append({"to": to, "subject": subject, "text": text})


BASE = [job(1, "Platform Engineer", "Bengaluru"),
        job(2, "Product Marketing Manager", "Bengaluru"),
        job(3, "Software Engineer", "San Francisco, CA"),
        job(4, "Product Marketing Manager", "San Francisco, CA")]


class Harness:
    def __init__(self):
        self.ats = FakeAts()
        self.ats.boards["fx"] = list(BASE)
        self.store = MemoryStore([{"name": "Fixture Co", "domain": "fixture.example", "source_tags": ["frontier_list"]},
                                  {"name": "Unresolvable Co", "tier": "india"}])
        self.mailer = Mailer()
        self.discover_calls = []
        runmod.STATE_FILE = pathlib.Path(tempfile.mkdtemp()) / "last_run.json"

    def discover(self, name, domain=None):
        self.discover_calls.append(name)
        if name == "Fixture Co":
            return {"ats_type": "ashby", "ats_token": "fx", "confidence": "confirmed", "jobs": [], "careers_url": "https://fixture.example/careers",
                    "notes": [], "errors": []}
        return {"ats_type": None, "ats_token": None, "confidence": None, "jobs": None, "careers_url": None,
                "notes": ["no ATS link in the page text"], "errors": []}

    def run(self, hours, mailer=None, dry=False, quiet=False):
        return runmod.run_once(self.store, mailer or self.mailer, "me@example.com", now=at(hours), discover_fn=self.discover,
                               adapters={"ashby": self.ats}, page_fn=lambda url: (200, "h"), dry_run=dry,
                               quiet_status=quiet)


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.h = Harness()

    def baseline(self):
        r = self.h.run(0)
        self.assertTrue(r["baseline"])
        return r

    def test_first_run_is_a_silent_baseline_with_one_email(self):
        self.baseline()
        self.assertEqual(self.h.store.events, [])                               # nothing announced as new
        self.assertEqual(len(self.h.mailer.sent), 1)
        mail = self.h.mailer.sent[0]
        self.assertEqual(mail["subject"], "Job Signal Engine: baseline complete")
        self.assertIn("Fixture Co: Product Marketing Manager", mail["text"])
        self.assertEqual(mail["text"].count("Fixture Co: Product Marketing Manager"), 1)   # only the Bengaluru one
        self.assertNotIn("Software Engineer", mail["text"])
        self.assertIn("1 matching titles hidden by location.", mail["text"])
        self.assertEqual(len(self.h.store.posts), 4)                            # all four are stored

    def test_company_with_no_ats_is_listed_not_dropped(self):
        self.baseline()
        self.assertIn("Unresolvable Co (india)", self.h.mailer.sent[0]["text"])
        c = [c for c in self.h.store.companies() if c["name"] == "Unresolvable Co"][0]
        self.assertTrue(c["needs_manual_ats"])
        fx = [c for c in self.h.store.companies() if c["name"] == "Fixture Co"][0]
        self.assertTrue(fx["verified"])
        self.assertEqual((fx["ats_type"], fx["ats_token"]), ("ashby", "fx"))

    def test_second_run_with_no_change_sends_nothing(self):
        self.baseline()
        r = self.h.run(12)
        self.assertFalse(r["emailed"])
        self.assertEqual(len(self.h.mailer.sent), 1)

    def test_quiet_day_sends_a_short_status_email_when_switched_on(self):
        self.baseline()
        r = self.h.run(12, quiet=True)
        self.assertTrue(r["emailed"])
        mail = self.h.mailer.sent[-1]
        self.assertEqual(mail["subject"], "Job signals: nothing new today (1 matching role still open)")
        self.assertIn("Fixture Co: Product Marketing Manager", mail["text"])
        self.assertNotIn("Software Engineer", mail["text"])
        self.assertIn("Unresolvable Co", "".join(c["name"] for c in self.h.store.companies()))
        # It carries no events, so a later real event is unaffected.
        self.assertEqual(self.h.store.digests[-1]["event_ids"], [])
        self.h.ats.boards["fx"] = BASE + [job(5, "Head of Marketing", "Bengaluru")]
        r = self.h.run(24, quiet=True)
        self.assertIn("1 new matching role", self.h.mailer.sent[-1]["subject"])      # real news wins over the status email

    def test_quiet_day_stays_silent_when_switched_off(self):
        self.baseline()
        self.assertFalse(self.h.run(12, quiet=False)["emailed"])
        self.assertEqual(len(self.h.mailer.sent), 1)

    def test_quiet_status_email_failure_is_retried_not_lost(self):
        self.baseline()
        with self.assertLogs("jobsignals", level="ERROR"):
            r = self.h.run(12, mailer=Mailer(fail=True), quiet=True)
        self.assertFalse(r["emailed"])
        self.assertEqual(self.h.store.unsent_events(), [])                           # nothing to lose, nothing stuck

    def test_new_matching_role_is_emailed_once(self):
        self.baseline()
        self.h.ats.boards["fx"] = BASE + [job(5, "Head of Marketing", "Bengaluru")]
        r = self.h.run(12)
        self.assertTrue(r["emailed"])
        mail = self.h.mailer.sent[-1]
        self.assertEqual(mail["subject"], "Job signals: 1 new matching role (Fixture Co: Head of Marketing)")
        self.assertIn("Apply: https://fixture.example/jobs/5", mail["text"])
        # And not again.
        r = self.h.run(24)
        self.assertFalse(r["emailed"])
        self.assertEqual(len(self.h.mailer.sent), 2)

    def test_san_francisco_roles_are_stored_never_emailed(self):
        self.baseline()
        self.h.ats.boards["fx"] = BASE + [job(6, "Software Engineer", "San Francisco, CA"), job(7, "Growth Marketing Manager", "New York")]
        self.h.run(12)
        self.h.run(24)
        self.assertEqual(len(self.h.mailer.sent), 1)                            # only the baseline email
        titles = {p["title"] for p in self.h.store.posts}
        self.assertIn("Software Engineer", titles)                              # stored
        for m in self.h.mailer.sent:
            self.assertNotIn("New York", m["text"])

    def test_closed_posting_appears_once(self):
        self.baseline()
        self.h.ats.boards["fx"] = [j for j in BASE if j.ats_job_id != "2"]
        r = self.h.run(12)
        self.assertTrue(r["emailed"])
        mail = self.h.mailer.sent[-1]
        self.assertIn("ROLES CLOSED SINCE THE LAST RUN", mail["text"])
        self.assertIn("Fixture Co: Product Marketing Manager (Bengaluru), closed", mail["text"])
        r = self.h.run(24)
        self.assertFalse(r["emailed"])

    def test_smtp_outage_loses_nothing(self):
        self.baseline()
        self.h.ats.boards["fx"] = BASE + [job(5, "Head of Marketing", "Bengaluru")]
        with self.assertLogs("jobsignals", level="ERROR"):
            r = self.h.run(12, mailer=Mailer(fail=True))
        self.assertFalse(r["emailed"])
        self.assertFalse(self.h.store.digests[-1]["delivered"])
        self.assertEqual(len(self.h.store.unsent_events()), 1)                  # still waiting
        r = self.h.run(24)                                                      # next run, SMTP is back
        self.assertTrue(r["emailed"])
        self.assertIn("Head of Marketing", self.h.mailer.sent[-1]["text"])
        self.assertEqual(self.h.store.unsent_events(), [])
        self.assertFalse(self.h.run(36)["emailed"])

    def test_failed_baseline_email_means_baseline_runs_again(self):
        with self.assertLogs("jobsignals", level="ERROR"):
            self.h.run(0, mailer=Mailer(fail=True))
        self.assertFalse(self.h.store.baseline_delivered())
        r = self.h.run(12)
        self.assertTrue(r["baseline"])
        self.assertTrue(self.h.store.baseline_delivered())

    def test_unreadable_board_closes_nothing_and_is_reported(self):
        self.baseline()
        self.h.ats.boards["fx"] = BoardError("ashby fx: HTTP 503")
        with self.assertLogs("jobsignals", level="WARNING"):
            r = self.h.run(12)
        self.assertEqual(r["failed"], ["Fixture Co"])
        self.assertTrue(all(p.get("closed_at") is None for p in self.h.store.posts))
        self.assertFalse(r["emailed"])

    def test_discovery_is_retried_weekly_not_every_run(self):
        self.baseline()
        self.h.run(12)
        self.assertEqual(self.h.discover_calls.count("Unresolvable Co"), 1)
        self.h.run(24 * 8)
        self.assertEqual(self.h.discover_calls.count("Unresolvable Co"), 2)
        self.assertEqual(self.h.discover_calls.count("Fixture Co"), 1)           # resolved once, then cached

    def test_dry_run_writes_and_sends_nothing(self):
        r = self.h.run(0, dry=True)
        self.assertTrue(r["dry_run"])
        self.assertEqual((self.h.store.posts, self.h.store.events, self.h.store.digests, self.h.mailer.sent), ([], [], [], []))
        self.assertTrue(all(c["ats_type"] is None for c in self.h.store.companies()))     # nothing saved
        # But it still reads the boards it found and reports what it sees.
        self.assertEqual([(m[0], m[1]) for m in r["open_matches"]], [("Fixture Co", "Product Marketing Manager")])

    def test_state_file_is_written(self):
        self.baseline()
        import json
        state = json.loads(runmod.STATE_FILE.read_text())
        self.assertEqual(state["kind"], "baseline")
        self.assertTrue(state["emailed"])

    def test_first_marketing_role_reaches_the_email(self):
        h = Harness()
        h.ats.boards["fx"] = [job(1, "Platform Engineer", "Bengaluru")]
        h.run(0)
        h.ats.boards["fx"] = [job(1, "Platform Engineer", "Bengaluru"), job(2, "Marketing Operations Manager", "Bengaluru")]
        r = h.run(12)
        self.assertTrue(r["emailed"])
        mail = h.mailer.sent[-1]
        self.assertIn("COMPANIES THAT MOVED WITHOUT A MATCHING ROLE", mail["text"])
        self.assertIn("First marketing role posted", mail["text"])


if __name__ == "__main__":
    unittest.main()
