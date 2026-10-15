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


if __name__ == "__main__":
    unittest.main()
