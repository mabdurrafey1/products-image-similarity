"""The catalog database and the column-wise shortcuts must give exactly what the row-by-row code gave."""
import io
import os
import random
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import catalog_db
import downloader
import match_image_ai as m


def quietly(fn, *args, **kwargs):
    out = io.StringIO()
    with redirect_stdout(out):
        result = fn(*args, **kwargs)
    return result, out.getvalue()


def old_load_dataset(paths):
    """load_dataset as it was, straight from Excel."""
    dfs = []
    for p in paths:
        df = m.load_excel_with_sheets(p)
        df['Source File'] = os.path.basename(p)
        dfs.append(df)
    return dfs[0] if len(dfs) == 1 else pd.concat(dfs, ignore_index=True)


def brute_force_candidates(df, reference_title):
    out = []
    for idx, row in df.iterrows():
        title = str(row.get('Title', ''))
        if title and m.get_title_similarity(reference_title, title) > 0.0:
            out.append((idx, title))
    return out


def assert_rows_identical(test, a, b):
    test.assertEqual(a.name, b.name)
    test.assertEqual(list(a.index), list(b.index))
    test.assertEqual(a.dtype, b.dtype)
    test.assertEqual([type(v) for v in a.tolist()], [type(v) for v in b.tolist()])
    test.assertEqual([str(v) for v in a.tolist()], [str(v) for v in b.tolist()])


WORDS = ["car", "Mirror", "rear", "view", "the", "with", "1", "4", "X6", "d007", "Pro", "mini", "中文", "ç",
         "rearviewmirror", "a", "image", "--", "!!", "LED", "light", "Toy", "blue", "red"]


def random_title(rng):
    kind = rng.random()
    if kind < 0.05:
        return None
    if kind < 0.08:
        return ""
    if kind < 0.10:
        return "!!! ---"
    if kind < 0.12:
        return "the with and 1"
    return " ".join(rng.choice(WORDS) for _ in range(rng.randint(1, 7)))


class CatalogCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        catalog_db.set_path(os.path.join(self.tmp.name, "catalog.sqlite"))
        rng = random.Random(7)
        self.files = []
        # Store 1: two sheets, the useful one named as the real exports name it
        n = 300
        store1 = pd.DataFrame({
            "sku": [f"S{i % 250}" for i in range(n)],   # duplicate SKUs
            "PartnerSKU": [f"P{i}" for i in range(n)],
            "Product Title": [random_title(rng) for _ in range(n)],
            "Brand": [rng.choice(["Acme", None, "Zed"]) for _ in range(n)],
            "Price": [rng.choice([5.0, 10.5, None, 99.0, 250.0]) for _ in range(n)],
            "Main Image URL": [rng.choice(["http://x/a.jpg", "", None, "nan", " http://x/b.jpg "]) for _ in range(n)],
        })
        p1 = os.path.join(self.tmp.name, "store1.xlsx")
        with pd.ExcelWriter(p1) as w:
            pd.DataFrame({"junk": [1]}).to_excel(w, sheet_name="Other", index=False)
            store1.to_excel(w, sheet_name="Best_One_Row_Per_SKU", index=False)
        self.files.append(p1)
        # Store 2: numeric titles, SKUs overlapping store 1, single sheet
        store2 = pd.DataFrame({
            "sku": ["S1", "S2", "T3", "S1", "T5"],
            "Product Title": [123, 4.5, "car mirror", None, "blue toy"],
            "Price": [1, 2, 3, 4, 5],
            "Main Image URL": ["http://y/1.jpg"] * 5,
        })
        p2 = os.path.join(self.tmp.name, "store2.xlsx")
        store2.to_excel(p2, index=False)
        self.files.append(p2)
        # Store 3: no title column at all
        p3 = os.path.join(self.tmp.name, "store3.xlsx")
        pd.DataFrame({"sku": ["Z1", "Z2"], "Price": [1.0, 2.0]}).to_excel(p3, index=False)
        self.no_title = p3

    def tearDown(self):
        catalog_db.set_path(None)
        self.tmp.cleanup()

    def load_both(self, paths):
        expected, old_out = quietly(old_load_dataset, paths)
        catalog_db.clear_memory_cache()
        cold, cold_out = quietly(m.load_dataset_indexed, paths if len(paths) > 1 else paths[0])
        catalog_db.clear_memory_cache()
        warm, warm_out = quietly(m.load_dataset_indexed, paths if len(paths) > 1 else paths[0])
        return expected, cold, warm, old_out, cold_out, warm_out


class LoadDatasetTest(CatalogCase):
    def test_frames_equal_head(self):
        for paths in ([self.files[0]], self.files, self.files + [self.no_title]):
            expected, (cold, _), (warm, _), _, cold_out, warm_out = self.load_both(paths)
            for got in (cold, warm):
                self.assertTrue(expected.equals(got))
                self.assertEqual(list(expected.dtypes), list(got.dtypes))
                self.assertEqual(list(expected.columns), list(got.columns))
                for c in expected.columns:
                    self.assertEqual([type(v) for v in expected[c].tolist()], [type(v) for v in got[c].tolist()])
            self.assertEqual(cold_out, warm_out)
            self.assertIn("Detected multi-sheet Excel in 'store1.xlsx'", warm_out)

    def test_iterrows_rows_equal_head(self):
        expected, (cold, _), (warm, _), *_ = self.load_both(self.files)
        for got in (cold, warm):
            for (i1, r1), (i2, r2) in zip(expected.iterrows(), got.iterrows()):
                self.assertEqual(i1, i2)
                assert_rows_identical(self, r1, r2)

    def test_warm_load_skips_excel(self):
        quietly(m.load_dataset_indexed, self.files)
        catalog_db.clear_memory_cache()
        original = m.load_excel_with_sheets
        m.load_excel_with_sheets = None   # any parse now fails loudly
        try:
            df, index = quietly(m.load_dataset_indexed, self.files)[0]
        finally:
            m.load_excel_with_sheets = original
        self.assertIsNotNone(index)
        self.assertEqual(len(df), 305)

    def test_index_missing_when_a_file_has_no_title(self):
        (df, index), _ = quietly(m.load_dataset_indexed, self.files + [self.no_title])
        self.assertIsNone(index)

    def test_load_dataset_is_the_indexed_frame(self):
        (df, _), _ = quietly(m.load_dataset_indexed, self.files)
        other, _ = quietly(m.load_dataset, self.files)
        self.assertTrue(df.equals(other))


class TitleIndexTest(CatalogCase):
    REFS = ["car mirror", "Rear View Mirror X6", "the with 1", "", "!!!", "123", "4.5", "nan", "中文 toy",
            "blue LED light", "d007 pro", "rearviewmirror"]

    def price_filtered(self, df, lo=None, hi=None):
        df['Price'] = pd.to_numeric(df['Price'], errors='coerce')
        if lo is not None:
            df = df[df['Price'] >= lo]
        if hi is not None:
            df = df[df['Price'] <= hi]
        return df

    def test_candidates_equal_brute_force(self):
        for warm in (False, True):
            if warm:
                catalog_db.clear_memory_cache()
            for bounds in ((None, None), (6, None), (None, 50), (6, 50), (1000, None)):
                (df, index), _ = quietly(m.load_dataset_indexed, self.files)
                self.assertIsNotNone(index)
                if bounds != (None, None):
                    df = self.price_filtered(df, *bounds)
                for ref in self.REFS:
                    got = index.candidates(df, m.title_tokens(ref))
                    self.assertIsNotNone(got)
                    self.assertEqual(got, brute_force_candidates(df, ref), (ref, bounds))

    def test_changed_titles_fall_back(self):
        (df, index), _ = quietly(m.load_dataset_indexed, self.files)
        df.loc[3, 'Title'] = "something else"
        self.assertIsNone(index.candidates(df, m.title_tokens("car")))

    def test_reordered_frame_falls_back(self):
        (df, index), _ = quietly(m.load_dataset_indexed, self.files)
        self.assertIsNone(index.candidates(df.iloc[::-1], m.title_tokens("car")))

    def test_text_search_rows_identical(self):
        """run_semantic_text_search with and without the index, with the text model stubbed out."""
        class FakeModel:
            def ensure_downloaded(self):
                pass

            def compute_text_features(self, texts):
                out = []
                for t in texts:
                    v = np.zeros(8, dtype=np.float32)
                    for w in m.title_tokens(t):
                        v[hash(w) % 8] += 1
                    n = np.linalg.norm(v)
                    out.append(v / n if n else v)
                return np.array(out)

        import rclip.model
        original = rclip.model.Model
        rclip.model.Model = FakeModel
        tvs = os.environ.get("TITLE_VECTOR_STORE_DISABLED")
        try:
            (df, index), _ = quietly(m.load_dataset_indexed, self.files)
            df = self.price_filtered(df, 6, None)
            for ref in ("car mirror", "blue LED light", "123"):
                for strict in (False, True):
                    old, old_out = quietly(m.run_semantic_text_search, df, ref, {}, 0.3, strict)
                    new, new_out = quietly(m.run_semantic_text_search, df, ref, {}, 0.3, strict, title_index=index)
                    self.assertEqual([x["idx"] for x in old], [x["idx"] for x in new])
                    self.assertEqual([x["semantic_sim"] for x in old], [x["semantic_sim"] for x in new])
                    for a, b in zip(old, new):
                        assert_rows_identical(self, a["row"], b["row"])
                    self.assertEqual(old_out.split("candidate products")[0], new_out.split("candidate products")[0])
        finally:
            rclip.model.Model = original
            if tvs is None:
                os.environ.pop("TITLE_VECTOR_STORE_DISABLED", None)


class RowShortcutTest(CatalogCase):
    def test_download_tasks_equal_row_walk(self):
        (df, _), _ = quietly(m.load_dataset_indexed, self.files)
        images = os.path.join(self.tmp.name, "imgs")
        os.makedirs(images)
        open(os.path.join(images, "S1.jpg"), "wb").close()
        open(os.path.join(images, "s2.jpg"), "wb").close()     # case variant
        os.makedirs(os.path.join(images, "S3.jpg"))            # a folder, not a file
        os.symlink(os.path.join(images, "missing"), os.path.join(images, "S4.jpg"))   # dangling link
        os.symlink(os.path.join(images, "S1.jpg"), os.path.join(images, "S5.jpg"))    # live link
        for frame in (df, df[df['Price'].astype(float) > 6], df.iloc[:0]):
            self.assertEqual(downloader.build_download_tasks(frame, images),
                             downloader._build_download_tasks_by_row(frame, images))

    def test_download_tasks_numeric_frame_uses_row_walk(self):
        df = pd.DataFrame({"SKU": [5, 6], "Image URL": [1.5, 2.5]})
        images = os.path.join(self.tmp.name, "imgs2")
        os.makedirs(images)
        self.assertEqual(downloader.build_download_tasks(df, images),
                         downloader._build_download_tasks_by_row(df, images))
        self.assertEqual(downloader.build_download_tasks(df, images)[0][0], "5.0")

    def test_visual_only_equals_row_walk(self):
        (df, _), _ = quietly(m.load_dataset_indexed, self.files)
        scores = {"S1": 0.9, "S2": 0.5, "T3": 0.95, "NOPE": 0.99, "S100": 0.1}

        def old(frame):
            sku_to_idx = {}
            for idx, row in frame.iterrows():
                sku = str(row.get('SKU', '')).strip().upper()
                if sku and sku not in sku_to_idx:
                    sku_to_idx[sku] = idx
            return [(sku, sku_to_idx[sku]) for sku, s in scores.items()
                    if s >= 0.2 and sku not in {"S2"} and sku in sku_to_idx]

        for frame in (df, df.iloc[100:]):
            new = m.find_visual_only_matches(frame, scores, {"S2"}, 0.2)
            self.assertEqual([(str(x["row"]["SKU"]).upper(), x["idx"]) for x in new], old(frame))


class FallbackNotCachedTest(unittest.TestCase):
    def test_fallback_read_is_not_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            catalog_db.set_path(os.path.join(tmp, "catalog.sqlite"))
            try:
                path = os.path.join(tmp, "a.xlsx")
                pd.DataFrame({"sku": ["A"], "Product Title": ["x"]}).to_excel(path, index=False)
                original = pd.ExcelFile
                calls = []

                def broken(*a, **k):
                    calls.append(1)
                    raise OSError("locked")
                pd.ExcelFile = broken
                try:
                    quietly(m.load_dataset, path)
                finally:
                    pd.ExcelFile = original
                catalog_db.clear_memory_cache()
                (_, out) = quietly(m.load_dataset, path)
                self.assertNotIn("Error reading", out)
                row = catalog_db.connect().execute("SELECT frame FROM sources").fetchone()
                self.assertIsNotNone(row[0])
            finally:
                catalog_db.set_path(None)


if __name__ == "__main__":
    unittest.main()
