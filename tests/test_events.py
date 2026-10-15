import datetime as dt
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from engine.ats.base import Job  # noqa: E402
from engine.events import (diff_company, list_events, page_events, score_company)  # noqa: E402

CO = {"id": "c1", "name": "Fixture Co", "source_tags": ["a"], "careers_url": "https://fixture.example/careers"}
T0 = dt.datetime(2026, 10, 1, 1, 0, tzinfo=dt.timezone.utc)


def hrs(n):
    return T0 + dt.timedelta(hours=n)


def job(i, title, loc, **kw):
    return Job(ats_job_id=str(i), title=title, location=loc, url=f"https://fixture.example/jobs/{i}", **kw)


class Store:
    """A tiny stand in for the database, so runs can be chained."""
    def __init__(self):
        self.postings, self.snapshot = {}, None

    def run(self, jobs, now, run_id, baseline=False):
        res = diff_company(CO, self.postings, self.snapshot, jobs, now, run_id, baseline)
        for row in res.postings:
            self.postings.setdefault(row["ats_job_id"], {}).update(row)
        self.snapshot = res.snapshot
        return res


def types(res):
    return sorted(e["event_type"] for e in res.events)


class Acceptance(unittest.TestCase):
    def test_new_matching_role_appears_once(self):
        s = Store()
        s.run([job(1, "Platform Engineer", "Bengaluru")], hrs(0), "r1", baseline=True)
        res = s.run([job(1, "Platform Engineer", "Bengaluru"),
                     job(2, "Product Marketing Manager", "Bengaluru")], hrs(12), "r2")
        self.assertIn("matching_role_opened", types(res))
        ev = [e for e in res.events if e["event_type"] == "matching_role_opened"][0]
        self.assertEqual(ev["ats_job_id"], "2")
        self.assertTrue(ev["evidence_url"].endswith("/2"))
        # The next run: same posting, nothing new.
        res = s.run([job(1, "Platform Engineer", "Bengaluru"),
                     job(2, "Product Marketing Manager", "Bengaluru")], hrs(24), "r3")
        self.assertEqual(res.events, [])

    def test_san_francisco_engineer_is_stored_never_an_event(self):
        s = Store()
        s.run([], hrs(0), "r1", baseline=True)
        res = s.run([job(5, "Software Engineer", "San Francisco, CA")], hrs(12), "r2")
        self.assertEqual(res.events, [])
        row = s.postings["5"]
        self.assertFalse(row["location_ok"])
        self.assertFalse(row["is_match"])

    def test_matching_role_in_san_francisco_is_stored_not_emailed(self):
        s = Store()
        s.run([], hrs(0), "r1", baseline=True)
        res = s.run([job(6, "Product Marketing Manager", "San Francisco, CA")], hrs(12), "r2")
        self.assertEqual(res.events, [])
        self.assertTrue(s.postings["6"]["is_match"])
        self.assertFalse(s.postings["6"]["location_ok"])

    def test_closed_posting_appears_once(self):
        s = Store()
        s.run([job(2, "Product Marketing Manager", "Bengaluru")], hrs(0), "r1", baseline=True)
        res = s.run([job(9, "Office Manager", "Bengaluru"), job(8, "Designer", "Pune"),
                     job(7, "Analyst", "Pune")], hrs(12), "r2")
        self.assertEqual(types(res).count("matching_role_closed"), 1)
        res = s.run([job(9, "Office Manager", "Bengaluru"), job(8, "Designer", "Pune"),
                     job(7, "Analyst", "Pune")], hrs(24), "r3")
        self.assertNotIn("matching_role_closed", types(res))

    def test_baseline_is_silent(self):
        s = Store()
        res = s.run([job(2, "Product Marketing Manager", "Bengaluru"),
                     job(3, "Sales Manager", "Bengaluru")], hrs(0), "r1", baseline=True)
        self.assertEqual(res.events, [])
        self.assertEqual(len(res.postings), 2)
        self.assertTrue(s.postings["2"]["is_match"] and s.postings["2"]["location_ok"])


class Signals(unittest.TestCase):
    def test_first_marketing_role(self):
        s = Store()
        s.run([job(1, "Platform Engineer", "Bengaluru")], hrs(0), "r1", baseline=True)
        res = s.run([job(1, "Platform Engineer", "Bengaluru"),
                     job(2, "Marketing Operations Manager", "Bengaluru")], hrs(12), "r2")
        self.assertIn("first_marketing_role_posted", types(res))
        self.assertNotIn("matching_role_opened", types(res))     # not a target title
        # A second marketing role later is not "first".
        res = s.run([job(1, "Platform Engineer", "Bengaluru"),
                     job(2, "Marketing Operations Manager", "Bengaluru"),
                     job(3, "Brand Marketing Lead", "Bengaluru")], hrs(24), "r3")
        self.assertNotIn("first_marketing_role_posted", types(res))

    def test_adjacent_role(self):
        s = Store()
        s.run([], hrs(0), "r1", baseline=True)
        res = s.run([job(4, "Content Designer", "Remote India")], hrs(12), "r2")
        self.assertEqual(types(res), ["adjacent_role_opened"])

    def test_open_role_count_jump(self):
        s = Store()
        base = [job(i, f"Role {i}", "Remote") for i in range(10)]
        s.run(base, hrs(0), "r1", baseline=True)
        res = s.run(base + [job(100, "Role 100", "Remote")], hrs(12), "r2")           # +10 percent
        self.assertNotIn("open_role_count_jump", types(res))
        more = base + [job(100 + i, f"Role {100 + i}", "Remote") for i in range(4)]    # 11 to 14, +27 percent
        res = s.run(more, hrs(24), "r3")
        self.assertNotIn("open_role_count_jump", types(res))
        res = s.run(more + [job(200 + i, f"Role {200 + i}", "Remote") for i in range(5)], hrs(36), "r4")  # 14 to 19, +36 percent
        self.assertIn("open_role_count_jump", types(res))

    def test_tiny_boards_do_not_jump(self):
        s = Store()
        s.run([job(1, "A", "Remote")], hrs(0), "r1", baseline=True)
        res = s.run([job(1, "A", "Remote"), job(2, "B", "Remote")], hrs(12), "r2")   # 1 to 2 is +100 percent
        self.assertNotIn("open_role_count_jump", types(res))

    def test_repost_is_flagged_not_new(self):
        s = Store()
        s.run([job(2, "Product Marketing Manager", "Bengaluru")], hrs(0), "r1", baseline=True)
        filler = [job(9, "Office Manager", "Bengaluru"), job(8, "Designer", "Pune"), job(7, "Analyst", "Pune")]
        res = s.run(filler + [job(20, "Product Marketing Manager", "Bengaluru")], hrs(12), "r2")
        self.assertTrue(s.postings["20"]["likely_repost"])
        self.assertNotIn("matching_role_opened", types(res))
        # Outside the window it is a new role again.
        s2 = Store()
        s2.run([job(2, "Product Marketing Manager", "Bengaluru")], hrs(0), "r1", baseline=True)
        res = s2.run(filler + [job(20, "Product Marketing Manager", "Bengaluru")], hrs(24 * 40), "r2")
        self.assertIn("matching_role_opened", types(res))

    def test_second_live_opening_is_not_a_repost(self):
        s = Store()
        s.run([job(2, "Product Marketing Manager", "Bengaluru")], hrs(0), "r1", baseline=True)
        res = s.run([job(2, "Product Marketing Manager", "Bengaluru"),
                     job(3, "Product Marketing Manager", "Bengaluru")], hrs(12), "r2")
        self.assertFalse(s.postings["3"]["likely_repost"])
        self.assertIn("matching_role_opened", types(res))

    def test_empty_read_does_not_close_everything(self):
        s = Store()
        base = [job(i, "Product Marketing Manager" if i == 1 else f"Role {i}", "Bengaluru") for i in range(5)]
        s.run(base, hrs(0), "r1", baseline=True)
        res = s.run([], hrs(12), "r2")
        self.assertEqual(res.events, [])
        self.assertTrue(res.notes)
        self.assertTrue(all(p.get("closed_at") is None for p in s.postings.values()))

    def test_location_changing_to_a_pass_is_announced_once(self):
        s = Store()
        s.run([job(2, "Product Marketing Manager", "San Francisco")], hrs(0), "r1", baseline=True)
        res = s.run([job(2, "Product Marketing Manager", "San Francisco | Bengaluru")], hrs(12), "r2")
        self.assertIn("matching_role_opened", types(res))
        res = s.run([job(2, "Product Marketing Manager", "San Francisco | Bengaluru")], hrs(24), "r3")
        self.assertEqual(res.events, [])

    def test_senior_flag_is_stored(self):
        s = Store()
        s.run([], hrs(0), "r1", baseline=True)
        s.run([job(3, "Director of Marketing", "Bengaluru")], hrs(12), "r2")
        self.assertEqual(s.postings["3"]["seniority_flag"], "senior")

    def test_near_misses_are_collected(self):
        s = Store()
        res = s.run([job(3, "Growth Marketing Lead", "Bengaluru")], hrs(0), "r1", baseline=True)
        self.assertEqual(res.near_misses[0]["title"], "Growth Marketing Lead")

    def test_page_and_list_events(self):
        prev = {"careers_status": 404, "careers_hash": None}
        self.assertEqual([e["event_type"] for e in page_events(CO, prev, 200, "h1", T0)], ["careers_page_created"])
        prev = {"careers_status": 200, "careers_hash": "h1"}
        ev = page_events(CO, prev, 200, "h2", T0, "r9")
        self.assertEqual([e["event_type"] for e in ev], ["careers_page_changed"])
        self.assertIn("weak signal", ev[0]["detail"])
        self.assertEqual(page_events(CO, prev, 200, "h1", T0), [])
        self.assertEqual(page_events(CO, None, 200, "h1", T0), [])
        ev = list_events(CO, ["a"], ["a", "b"], T0)
        self.assertEqual([e["event_type"] for e in ev], ["list_appearance"])
        self.assertEqual(list_events(CO, ["a"], ["a", "b"], T0, baseline=True), [])


class Scoring(unittest.TestCase):
    def ev(self, w, days_ago):
        return {"weight": w, "observed_at": T0 - dt.timedelta(days=days_ago)}

    def test_half_life_is_14_days(self):
        self.assertAlmostEqual(score_company([self.ev(10, 0)], ["a"], T0), 10.0, places=2)
        self.assertAlmostEqual(score_company([self.ev(10, 14)], ["a"], T0), 5.0, places=2)
        self.assertAlmostEqual(score_company([self.ev(10, 28)], ["a"], T0), 2.5, places=2)

    def test_extra_lists_add_a_bonus(self):
        self.assertAlmostEqual(score_company([self.ev(10, 0)], ["a", "b", "c"], T0), 12.0, places=2)
        self.assertAlmostEqual(score_company([], ["a", "b"], T0), 1.0, places=2)

    def test_events_add_up(self):
        self.assertAlmostEqual(score_company([self.ev(10, 0), self.ev(8, 14)], ["a"], T0), 14.0, places=2)


if __name__ == "__main__":
    unittest.main()
