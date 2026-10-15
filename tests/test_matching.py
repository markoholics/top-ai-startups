import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from engine.matching import location_ok, match_title  # noqa: E402


class TitleTests(unittest.TestCase):
    def test_targets_match(self):
        for t in ["Product Marketing Manager", "Senior PMM", "Growth Marketing Manager",
                  "Head of Marketing", "GTM Manager", "Go-To-Market Strategist",
                  "Marketing Manager, APAC", "Demand Generation Manager", "GTM Engineer",
                  "Product Marketing Manger"]:          # a typo still matches
            self.assertTrue(match_title(t).is_match, t)

    def test_exclusions_win(self):
        for t in ["Software Engineer", "Marketing Intern", "Account Executive, GTM",
                  "Technical Recruiter", "Director of Engineering"]:
            self.assertFalse(match_title(t).is_match, t)

    def test_unrelated_titles_do_not_match(self):
        for t in ["Manufacturing Engineer", "ML Infra Engineer, Data Systems", "Office Manager",
                  "Machine Learning Researcher", "Marketing"]:
            self.assertFalse(match_title(t).is_match, t)

    def test_seniority_is_a_flag_not_a_filter(self):
        r = match_title("Director of Product Marketing")
        self.assertTrue(r.is_match)
        self.assertEqual(r.seniority_flag, "senior, check fit")
        r = match_title("VP Marketing")
        self.assertTrue(r.is_match)
        self.assertEqual(r.seniority_flag, "senior, check fit")
        self.assertIsNone(match_title("Product Marketing Manager").seniority_flag)

    def test_near_misses_are_logged_not_matched(self):
        r = match_title("Growth Marketing Lead")
        self.assertFalse(r.is_match)
        self.assertEqual(r.near_miss_reason, "below threshold")
        r = match_title("Account Executive, GTM")
        self.assertEqual(r.near_miss_reason, "excluded term: account executive")
        # Plain noise is not logged.
        self.assertIsNone(match_title("Software Engineer").near_miss_reason)
        self.assertIsNone(match_title("Product Manager, Billing").near_miss_reason)
        self.assertIsNone(match_title("Engineering Manager, Search").near_miss_reason)

    def test_kinds(self):
        self.assertEqual(match_title("Product Marketing Manager").kind, "match")
        self.assertEqual(match_title("Content Designer").kind, "adjacent")
        self.assertEqual(match_title("Marketing Operations Manager").kind, "marketing")


class LocationTests(unittest.TestCase):
    def ok(self, loc, **kw):
        return location_ok(loc, **kw)[0]

    def test_india(self):
        for loc in ["Bengaluru", "Bangalore, KA", "Bengaluru, India", "India", "Mumbai, India",
                    "San Francisco | Bengaluru", "San Francisco, CA; Bangalore"]:
            self.assertTrue(self.ok(loc), loc)

    def test_remote_regions(self):
        for loc in ["Remote", "Remote India", "Remote (India)", "Remote - APAC", "Remote, Asia Pacific",
                    "Remote Worldwide", "Remote (Global)", "Fully Remote", "Anywhere (Remote)"]:
            self.assertTrue(self.ok(loc), loc)
        for loc in ["Remote - US", "Remote (United States)", "Remote, EMEA", "Remote - Canada",
                    "Remote, London", "Remote (Europe)"]:
            self.assertFalse(self.ok(loc), loc)

    def test_onsite_elsewhere_fails(self):
        for loc in ["San Francisco, CA", "New York City, NY; San Francisco, CA", "Fremont",
                    "Singapore", "London, United Kingdom", "Indianapolis, IN", "Seoul, South Korea"]:
            self.assertFalse(self.ok(loc), loc)

    def test_country_code_and_flags(self):
        self.assertTrue(self.ok("Pune", country="IN"))
        self.assertTrue(self.ok("Remote", country="US"))          # text names no region, so it passes
        self.assertTrue(self.ok("", remote=True))                 # remote, no region
        self.assertFalse(self.ok("", remote=True, country="US"))  # remote but in the US
        self.assertFalse(self.ok(""))                             # nothing at all


if __name__ == "__main__":
    unittest.main()
