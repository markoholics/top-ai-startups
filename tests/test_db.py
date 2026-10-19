import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import os  # noqa: E402

os.environ["SUPABASE_URL"] = "http://example.invalid"
os.environ["SUPABASE_SERVICE_KEY"] = "k"
from engine.db import StoreError, SupabaseStore  # noqa: E402


class FakeApi(SupabaseStore):
    """SupabaseStore with the network replaced by a table of known columns."""
    def __init__(self, have):
        super().__init__()
        self.have = have

    def _call(self, method, path, params=None, body=None, prefer=None):
        cols = (params or {}).get("select", "*").split(",")
        if path not in self.have:
            raise StoreError(f"GET {path}: HTTP 404 PGRST205 Could not find the table")
        for c in cols:
            if c != "*" and c not in self.have[path]:
                raise StoreError(f"GET {path}: HTTP 400 PGRST204 Could not find the '{c}' column of '{path}' in the schema cache")
        return []


def full():
    return {t: set(c) for t, c in SupabaseStore.REQUIRED.items()}


class SchemaCheck(unittest.TestCase):
    def test_complete_schema_has_no_problems(self):
        self.assertEqual(FakeApi(full()).check_schema(), [])

    def test_missing_column_is_named(self):
        have = full()
        have["watch_company"].discard("note")
        problems = FakeApi(have).check_schema()
        self.assertEqual(problems, ["watch_company is missing: note"])

    def test_several_missing_columns_and_a_missing_table(self):
        have = full()
        have["job_posting"] -= {"role_kind", "is_match"}
        del have["title_near_miss"]
        problems = FakeApi(have).check_schema()
        self.assertIn("job_posting is missing: is_match, role_kind", problems)
        self.assertIn("table title_near_miss is missing", problems)


class Retries(unittest.TestCase):
    """_call tries again on timeouts and server hiccups, never on our own mistakes."""
    def setUp(self):
        import engine.db as dbmod
        self.dbmod = dbmod
        self.real = (dbmod.urllib.request.urlopen, dbmod.time.sleep)
        self.calls = 0
        dbmod.time.sleep = lambda s: None

    def tearDown(self):
        self.dbmod.urllib.request.urlopen, self.dbmod.time.sleep = self.real

    def fake(self, failures, error):
        class Ok:
            def __enter__(s): return s
            def __exit__(s, *a): return False
            def read(s): return b"[]"

        def urlopen(req, timeout=None):
            self.calls += 1
            if self.calls <= failures:
                raise error() if callable(error) else error
            return Ok()
        self.dbmod.urllib.request.urlopen = urlopen

    def test_timeout_then_success(self):
        self.fake(2, lambda: TimeoutError("The read operation timed out"))
        self.assertEqual(SupabaseStore()._call("POST", "job_posting", body=[]), [])
        self.assertEqual(self.calls, 3)

    def test_gives_up_after_the_last_try(self):
        self.fake(99, lambda: TimeoutError("The read operation timed out"))
        with self.assertRaises(StoreError):
            SupabaseStore()._call("POST", "job_posting", body=[])
        self.assertEqual(self.calls, len(self.dbmod.RETRY_WAITS) + 1)

    def test_client_error_is_not_retried(self):
        import io
        import urllib.error
        self.fake(99, lambda: urllib.error.HTTPError("u", 400, "bad", {}, io.BytesIO(b"nope")))
        with self.assertRaises(StoreError):
            SupabaseStore()._call("POST", "job_event", body=[])
        self.assertEqual(self.calls, 1)


if __name__ == "__main__":
    unittest.main()
