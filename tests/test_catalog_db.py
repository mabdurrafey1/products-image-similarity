import datetime
import io
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import catalog_db


def value_types(df):
    return [[type(v) for v in df[c].tolist()] for c in df.columns]


class TempDB(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "catalog.sqlite")
        catalog_db.set_path(self.path)

    def tearDown(self):
        catalog_db.set_path(None)
        self.tmp.cleanup()


class EncodeFrameTest(unittest.TestCase):
    def assert_round_trip(self, df):
        blob = catalog_db.encode_frame(df)
        self.assertIsNotNone(blob, "frame should be cacheable")
        back = catalog_db.decode_frame(blob)
        self.assertTrue(df.equals(back))
        self.assertEqual(list(df.columns), list(back.columns))
        self.assertEqual(list(df.dtypes), list(back.dtypes))
        self.assertTrue(df.index.equals(back.index))
        self.assertEqual(type(df.index), type(back.index))
        self.assertEqual(value_types(df), value_types(back))
        for c in df.columns:
            self.assertEqual([str(v) for v in df[c].tolist()], [str(v) for v in back[c].tolist()])

    def test_str_column_with_nan(self):
        df = pd.DataFrame({"Title": ["a", None, "ç ü 中文", ""]})
        self.assertEqual(str(df["Title"].dtype), "str")
        self.assert_round_trip(df)

    def test_numeric_columns(self):
        df = pd.DataFrame({
            "f": [1.5, np.nan, np.inf, -0.0],
            "i": np.array([1, -2, 2**62, 0], dtype="int64"),
            "b": [True, False, True, False],
            "f32": np.array([0.1, 2, 3, 4], dtype="float32"),
        })
        self.assert_round_trip(df)
        back = catalog_db.decode_frame(catalog_db.encode_frame(df))
        self.assertEqual(np.signbit(back["f"].to_numpy()).tolist(), [False, False, False, True])

    def test_datetime_column(self):
        df = pd.DataFrame({"d": pd.to_datetime(["2024-01-02 03:04:05", None, "1999-12-31 00:00:00"])})
        self.assert_round_trip(df)

    def test_mixed_object_column(self):
        vals = ["x", 7, 2.5, float("nan"), None, True, datetime.datetime(2024, 5, 6, 7, 8, 9, 10),
                datetime.time(1, 2, 3), 10**30, np.float64(1.25), np.int64(-3), float("-inf")]
        df = pd.DataFrame({"o": pd.Series(vals, dtype=object)})
        self.assert_round_trip(df)

    def test_empty_frame(self):
        self.assert_round_trip(pd.DataFrame({"SKU": pd.Series([], dtype="str")}))

    def test_uncacheable(self):
        self.assertIsNone(catalog_db.encode_frame(pd.DataFrame({"a": [1, 2]}, index=[5, 6])))
        self.assertIsNone(catalog_db.encode_frame(pd.DataFrame({0: [1, 2]})))
        self.assertIsNone(catalog_db.encode_frame(pd.DataFrame({"a": pd.Categorical(["x", "y"])})))
        tz = datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc)
        self.assertIsNone(catalog_db.encode_frame(pd.DataFrame({"a": pd.Series([tz, "x"], dtype=object)})))
        self.assertIsNone(catalog_db.encode_frame(
            pd.DataFrame({"a": pd.Series([pd.Timestamp("2024-01-01"), "x"], dtype=object)})))
        self.assertIsNone(catalog_db.encode_frame(pd.DataFrame({"a": pd.Series([[1], "x"], dtype=object)})))


class ConnectTest(TempDB):
    def test_creates_schema(self):
        conn = catalog_db.connect()
        self.assertIsNotNone(conn)
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"sources", "title_vectors", "index_state"} <= names)

    def test_corrupt_file_is_moved_aside(self):
        catalog_db.set_path(None)
        with open(self.path, "wb") as f:
            f.write(b"this is not a database" * 100)
        catalog_db.set_path(self.path)
        conn = catalog_db.connect()
        self.assertIsNotNone(conn)
        conn.execute("SELECT COUNT(*) FROM sources").fetchone()
        moved = [n for n in os.listdir(self.tmp.name) if n.startswith("catalog.sqlite.corrupt-")]
        self.assertEqual(len(moved), 1)

    def test_newer_schema_disables_db(self):
        catalog_db.set_path(None)
        raw = sqlite3.connect(self.path)
        raw.execute(f"PRAGMA user_version={catalog_db.SCHEMA_VERSION + 1}")
        raw.execute("CREATE TABLE future (x)")
        raw.commit()
        raw.close()
        before = open(self.path, "rb").read()
        catalog_db.set_path(self.path)
        with redirect_stdout(io.StringIO()):
            self.assertIsNone(catalog_db.connect())
        self.assertEqual(before, open(self.path, "rb").read())


class LoadSourceTest(TempDB):
    def setUp(self):
        super().setUp()
        self.xlsx = os.path.join(self.tmp.name, "store.xlsx")
        with open(self.xlsx, "wb") as f:
            f.write(b"placeholder")
        self.calls = 0
        self.frame = pd.DataFrame({"SKU": ["A1", "B2", "C3"], "Title": ["Red Car", None, "blue car toy"],
                                   "Price": [1.0, np.nan, 3.0]})

    def parse(self, path, messages):
        self.calls += 1
        msg = f"Detected multi-sheet Excel in '{os.path.basename(path)}'. Loading sheet: 'X'"
        print(msg)
        messages.append(msg)
        return self.frame.copy()

    @staticmethod
    def tokenize(t):
        return set(t.lower().split()) - {"the"}

    def load(self, tag="t1"):
        out = io.StringIO()
        with redirect_stdout(out):
            entry = catalog_db.load_source(self.xlsx, self.parse, self.tokenize, tag)
        return entry, out.getvalue()

    def test_hit_skips_parse_and_replays_messages(self):
        first, out1 = self.load()
        catalog_db.clear_memory_cache()
        second, out2 = self.load()
        self.assertEqual(self.calls, 1)
        self.assertEqual(out1, out2)
        self.assertTrue(first.frame.equals(second.frame))
        self.assertEqual(list(first.frame.dtypes), list(second.frame.dtypes))
        self.assertFalse(first.from_cache)
        self.assertTrue(second.from_cache)

    def test_memory_cache_returns_independent_copies(self):
        a, _ = self.load()
        a.frame.loc[0, "SKU"] = "CHANGED"
        b, _ = self.load()
        self.assertEqual(b.frame.loc[0, "SKU"], "A1")
        self.assertEqual(self.calls, 1)

    def test_touch_reimports(self):
        self.load()
        st = os.stat(self.xlsx)
        os.utime(self.xlsx, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
        self.load()
        self.assertEqual(self.calls, 2)

    def test_new_tag_reimports(self):
        self.load("t1")
        self.load("t2")
        self.assertEqual(self.calls, 2)

    def test_uncacheable_is_parsed_every_time(self):
        self.frame = pd.DataFrame({"a": pd.Categorical(["x"])})
        self.load()
        entry, _ = self.load()
        self.assertEqual(self.calls, 2)
        self.assertIsNone(entry.tokens)

    def test_disabled_db_still_parses(self):
        catalog_db.set_path(os.path.join(self.tmp.name, "missing-dir", "sub", "catalog.sqlite"))
        catalog_db._force_disabled_for_tests = True
        try:
            entry, _ = self.load()
        finally:
            catalog_db._force_disabled_for_tests = False
        self.assertTrue(entry.frame.equals(self.frame))

    def test_tokens_match_brute_force(self):
        entry, _ = self.load()
        expected = {}
        for pos, v in enumerate(self.frame["Title"].tolist()):
            for tok in self.tokenize(str(v)):
                expected.setdefault(tok, []).append(pos)
        self.assertEqual(entry.tokens, expected)

    def test_no_title_column_has_no_tokens(self):
        self.frame = pd.DataFrame({"SKU": ["A"]})
        entry, _ = self.load()
        self.assertIsNone(entry.tokens)


class IndexStateTest(TempDB):
    def test_round_trip(self):
        self.assertIsNone(catalog_db.index_state_get("/x/imgs"))
        catalog_db.index_state_set("/x/imgs", 10, 123, 9)
        self.assertEqual(catalog_db.index_state_get("/x/imgs"), (10, 123, 9))
        catalog_db.index_state_set("/x/imgs", 11, 124, 10)
        self.assertEqual(catalog_db.index_state_get("/x/imgs"), (11, 124, 10))


if __name__ == "__main__":
    unittest.main()
