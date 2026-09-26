import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import catalog_db
import generate_report


def head_module():
    """generate_report.py as last committed, to compare against."""
    try:
        src = subprocess.run(["git", "show", "HEAD:generate_report.py"], cwd=ROOT, capture_output=True,
                             check=True, text=True).stdout
    except Exception:
        return None
    path = os.path.join(tempfile.mkdtemp(), "generate_report_head.py")
    with open(path, "w") as f:
        f.write(src)
    spec = importlib.util.spec_from_file_location("generate_report_head", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def visible_content(html):
    """What the report shows, ignoring markup: the rendering was made lazy on purpose, the data must not move."""
    html = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S | re.I)
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


class ReportExtraAttrsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        catalog_db.set_path(os.path.join(self.tmp.name, "catalog.sqlite"))
        self.a = os.path.join(self.tmp.name, "a.xlsx")
        self.b = os.path.join(self.tmp.name, "b.xlsx")
        pd.DataFrame({
            "sku": ["Z1", "z1", "Z2", "Z3", "Z1 ", np.nan],
            "PartnerSKU": ["P1", "P1b", None, "P3", "P1c", "P9"],
            "Product Title": ["red car", "red car 2", "blue", "green", "red car 3", "none"],
            "Price": [10, 11, np.nan, 5, 12, 1],
            "Brand": ["Acme", "Acme2", "B", None, "Acme3", "N"],
            "Colour": ["red", None, "blue", "green", "maroon", "x"],
        }).to_excel(self.a, index=False)
        pd.DataFrame({"sku": ["Z1", "Z4"], "Product Title": ["other red car", "x"], "Price": [9, 8],
                      "Brand": ["Other", "Q"], "Weight": [1.5, 2]}).to_excel(self.b, index=False)
        results = [
            {"Row": 1, "SKU": "Z1", "Title": "red car", "Price": 10, "Source File": "a.xlsx",
             "AI Score": 0.9, "Text Similarity": 0.8, "psku": "P1"},
            {"Row": 2, "SKU": "z1", "Title": "red car", "Price": 11, "Source File": "b.xlsx",
             "AI Score": 0.5, "Text Similarity": None},
            {"Row": 3, "SKU": "Z3", "Title": "green", "Price": 5, "Source File": "missing.xlsx",
             "AI Score": 0.4, "Text Similarity": 0.3},
            {"Row": 4, "SKU": "NOPE", "Title": "?", "Price": None, "Source File": "a.xlsx",
             "AI Score": 0.1, "Text Similarity": 0.1},
        ]
        self.json = os.path.join(self.tmp.name, "r.json")
        with open(self.json, "w") as f:
            json.dump(results, f)

    def tearDown(self):
        catalog_db.set_path(None)
        self.tmp.cleanup()

    def render(self, mod, name):
        out = os.path.join(self.tmp.name, name)
        with redirect_stdout(io.StringIO()):
            mod.generate_html_report(json_path=self.json, output_html=out,
                                     images_dir=os.path.join(self.tmp.name, "imgs"),
                                     excel_path=f"{self.a};{self.b}", query_title="red car")
        with open(out, encoding="utf-8") as f:
            return f.read()

    def test_html_matches_head_cold_and_warm(self):
        head = head_module()
        if head is None:
            self.skipTest("git not available")
        expected = visible_content(self.render(head, "old.html"))
        for brand in ("Acme2", "Other"):  # last duplicate wins; per-file lookup
            self.assertIn(brand, expected)
        self.assertEqual(visible_content(self.render(generate_report, "cold.html")), expected)
        catalog_db.clear_memory_cache()
        self.assertEqual(visible_content(self.render(generate_report, "warm.html")), expected)

    def test_cards_render_lazily(self):
        html = self.render(generate_report, "lazy.html")
        self.assertIn("content-visibility: auto", html)
        self.assertIn('loading="lazy"', html)


if __name__ == "__main__":
    unittest.main()
