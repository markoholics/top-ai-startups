import pathlib
import re
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fixtures as fx  # noqa: E402
from engine.delivery import deliver  # noqa: E402
from engine.digest import ago, build_baseline, build_digest, ist  # noqa: E402
from engine.mail.gmail_smtp import GmailSMTP, MailError  # noqa: E402


def full():
    return build_digest(fx.NEW_ROLES, fx.MOVED, fx.CLOSED, fx.MANUAL, fx.FAILED, 12, fx.NOW)


class Content(unittest.TestCase):
    def test_subject_has_count_and_top_item(self):
        d = full()
        self.assertEqual(d.subject, "Job signals: 3 new matching roles (Fixture Robotics: Product Marketing Manager)")

    def test_single_role_subject(self):
        d = build_digest(fx.NEW_ROLES[:1], [], [], [], [], None, fx.NOW)
        self.assertEqual(d.subject, "Job signals: 1 new matching role (Fixture Robotics: Product Marketing Manager)")

    def test_sections_are_in_order(self):
        t = full().text
        order = ["NEW MATCHING ROLES", "COMPANIES THAT MOVED WITHOUT A MATCHING ROLE",
                 "ROLES CLOSED SINCE THE LAST RUN", "COMPANIES THAT NEED A MANUAL ATS FIX", "SOURCES THAT FAILED", "LOCATION"]
        pos = [t.index(x) for x in order]
        self.assertEqual(pos, sorted(pos))

    def test_role_has_every_field(self):
        t = full().text
        for needle in ["Fixture Robotics: Product Marketing Manager", "Location: Bengaluru", "ATS: ashby", "Tier: seed",
                       "Match score: 100", "Seed lists: frontier_list, breakout_list", "4 hours ago",
                       "(15 Oct 2026, 14:30 IST)", "Apply: https://example.com/fixture-robotics/pmm",
                       "[senior, check fit]"]:
            self.assertIn(needle, t)

    def test_moved_has_what_evidence_and_why(self):
        t = full().text
        self.assertIn("What changed: Marketing Operations Manager (Bengaluru)", t)
        self.assertIn("Evidence: https://example.com/demo-bots/role", t)
        self.assertIn("Why it may matter:", t)

    def test_hidden_by_location_line(self):
        self.assertIn("12 matching titles hidden by location.", full().text)
        self.assertNotIn("hidden by location", build_digest(fx.NEW_ROLES, [], [], [], [], None, fx.NOW).text)

    def test_never_sends_empty(self):
        self.assertIsNone(build_digest([], [], [], fx.MANUAL, fx.FAILED, 5, fx.NOW))     # housekeeping alone is not news

    def test_event_ids_are_returned(self):
        self.assertEqual(sorted(full().event_ids), ["e1", "e2", "e3", "e4", "e5", "e6"])

    def test_baseline(self):
        d = build_baseline(fx.NEW_ROLES, fx.MANUAL, [], 3, fx.NOW)
        self.assertEqual(d.subject, "Job Signal Engine: baseline complete")
        self.assertIn("Open matching roles you can apply to today".upper(), d.text)
        self.assertIn("Unresolved Example Co", d.text)
        self.assertNotIn("First seen", d.text)
        self.assertEqual(d.event_ids, [])
        empty = build_baseline([], fx.MANUAL, [], None, fx.NOW)                          # baseline is always sent
        self.assertIn("No open matching role passes", empty.text)

    def test_html_is_escaped(self):
        bad = dict(fx.NEW_ROLES[0], title="<script>alert(1)</script> Marketing Manager",
                   url="javascript:alert(1)")
        d = build_digest([bad], [], [], [], [], None, fx.NOW)
        self.assertNotIn("<script>", d.html)
        self.assertNotIn("href='javascript", d.html)

    def test_time_helpers(self):
        self.assertEqual(ago(fx.hours(4), fx.NOW), "4 hours ago")
        self.assertEqual(ago(fx.hours(1), fx.NOW), "1 hour ago")
        self.assertEqual(ago(fx.NOW, fx.NOW), "1 minute ago")
        self.assertEqual(ago(fx.hours(72), fx.NOW), "3 days ago")
        self.assertEqual(ist(fx.NOW), "15 Oct 2026, 18:30 IST")


class CopyRules(unittest.TestCase):
    """British English, no em dashes, no hyphens used as dashes."""
    def test_no_dashes_used_as_punctuation(self):
        for d in (full(), build_baseline(fx.NEW_ROLES, fx.MANUAL, fx.FAILED, 4, fx.NOW)):
            for body in (d.subject, d.text, d.html):
                self.assertNotIn("—", body)                 # em dash
                self.assertNotIn("–", body)                 # en dash
                self.assertNotRegex(body, r"\s-\s")              # spaced hyphen used as a dash

    def test_no_american_spellings_or_hype(self):
        t = (full().text + build_baseline(fx.NEW_ROLES, fx.MANUAL, [], 1, fx.NOW).text).lower()
        for word in ["organization", "optimize", "exciting", "amazing", "unlock", "supercharge", "leverage", "game-changing"]:
            self.assertNotIn(word, t)

    def test_sentences_are_short(self):
        for sentence in re.split(r"[.\n]", full().text):
            self.assertLess(len(sentence.split()), 30, sentence)


class FakeStore:
    def __init__(self, events):
        self.events, self.digests = set(events), []

    def log_digest(self, event_ids, subject):
        self.digests.append({"event_ids": list(event_ids), "subject": subject, "delivered": False, "error": None})
        return len(self.digests) - 1

    def finish_digest(self, i, delivered, error):
        self.digests[i].update(delivered=delivered, error=error)

    def unsent(self):
        sent = {e for d in self.digests if d["delivered"] for e in d["event_ids"]}
        return sorted(self.events - sent)


class GoodMailer:
    def __init__(self):
        self.sent = []

    def send(self, to, subject, text, html=None):
        self.sent.append((to, subject))


class BrokenMailer:
    def send(self, *a, **k):
        raise MailError("SMTPAuthenticationError: bad credentials")


class Delivery(unittest.TestCase):
    def test_failed_send_keeps_events_unsent_and_logs(self):
        store = FakeStore(["e1", "e2", "e3", "e4", "e5", "e6"])
        with self.assertLogs("jobsignals", level="ERROR") as cm:
            ok = deliver(store, BrokenMailer(), full(), "me@example.com")
        self.assertFalse(ok)
        self.assertTrue(any("stay unsent" in m for m in cm.output))
        self.assertFalse(store.digests[0]["delivered"])
        self.assertIn("bad credentials", store.digests[0]["error"])
        self.assertEqual(len(store.unsent()), 6)                       # nothing lost

    def test_next_run_retries_then_clears(self):
        store = FakeStore(["e1", "e2", "e3", "e4", "e5", "e6"])
        with self.assertLogs("jobsignals", level="ERROR"):
            deliver(store, BrokenMailer(), full(), "me@example.com")
        good = GoodMailer()
        self.assertTrue(deliver(store, good, full(), "me@example.com"))
        self.assertEqual(len(good.sent), 1)
        self.assertEqual(store.unsent(), [])                           # now cleared, and only now


class GmailAdapter(unittest.TestCase):
    def test_missing_credentials_fail_clearly(self):
        import os
        for k in ("SMTP_USER", "SMTP_PASSWORD"):
            os.environ.pop(k, None)
        with self.assertRaises(MailError) as cm:
            GmailSMTP().send("a@b.c", "s", "t")
        self.assertIn("SMTP_USER", str(cm.exception))

    def test_password_never_appears_in_errors(self):
        import os
        import smtplib
        from unittest import mock
        os.environ.update(SMTP_USER="me@gmail.com", SMTP_PASSWORD="supersecretpw")
        with mock.patch("smtplib.SMTP") as smtp:
            smtp.return_value.__enter__.return_value.login.side_effect = smtplib.SMTPAuthenticationError(535, b"bad supersecretpw")
            with self.assertRaises(MailError) as cm:
                GmailSMTP().send("a@b.c", "s", "t")
        self.assertNotIn("supersecretpw", str(cm.exception))
        for k in ("SMTP_USER", "SMTP_PASSWORD"):
            os.environ.pop(k, None)

    def test_sends_over_starttls_with_login(self):
        import os
        from unittest import mock
        os.environ.update(SMTP_USER="me@gmail.com", SMTP_PASSWORD="pw")
        with mock.patch("smtplib.SMTP") as smtp:
            GmailSMTP().send("to@example.com", "Subject", "Body", "<p>Body</p>")
            inst = smtp.return_value.__enter__.return_value
            inst.starttls.assert_called_once()
            inst.login.assert_called_once_with("me@gmail.com", "pw")
            inst.send_message.assert_called_once()
        smtp.assert_called_with("smtp.gmail.com", 587, timeout=30)
        for k in ("SMTP_USER", "SMTP_PASSWORD"):
            os.environ.pop(k, None)


if __name__ == "__main__":
    unittest.main()
