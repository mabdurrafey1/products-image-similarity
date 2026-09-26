import io
import os
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import catalog_db
import match_image_ai as m


class FakeModel:
    """Stands in for the CLIP model: vectors come from the picture's colour, no download needed."""
    instances = []
    features_calls = 0

    def __init__(self):
        self.images_seen = 0
        self.closed = False
        FakeModel.instances.append(self)

    def ensure_downloaded(self):
        pass

    def release_indexing_resources(self):
        pass

    def close(self):
        self.closed = True

    def compute_image_features(self, images, for_indexing=False):
        self.images_seen += len(images)
        out = []
        for img in images:
            v = np.zeros(512, dtype=np.float32)
            v[:3] = np.asarray(img.convert("RGB").getpixel((0, 0)), dtype=np.float32) / 255.0 + 0.01
            out.append(v / np.linalg.norm(v))
        return out

    def compute_similarities_to_text(self, features, positive, negative):
        q = np.zeros(512, dtype=np.float32)
        q[0] = 1.0
        sims = features @ q
        return sorted(((float(s), i) for i, s in enumerate(sims)), reverse=True)


class VisualIndexTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        catalog_db.set_path(os.path.join(self.tmp.name, "catalog.sqlite"))
        self.env = mock.patch.dict(os.environ, {"RCLIP_DATADIR": os.path.join(self.tmp.name, "rclip")})
        self.env.start()
        self.model = mock.patch("rclip.model.Model", FakeModel)
        self.model.start()
        FakeModel.instances = []
        m._rclip_file_count.clear()
        self.images = os.path.join(self.tmp.name, "imgs")
        os.makedirs(self.images)
        for i in range(70):
            self.add_image(f"SKU{i}", (i * 3 % 256, 10, 20))
        self.query = os.path.join(self.tmp.name, "query.png")
        Image.new("RGB", (4, 4), (200, 0, 0)).save(self.query)

    def tearDown(self):
        m._stop_event_local.event = None
        m._rclip_file_count.clear()
        self.model.stop()
        self.env.stop()
        catalog_db.set_path(None)
        self.tmp.cleanup()

    def add_image(self, name, colour):
        Image.new("RGB", (4, 4), colour).save(os.path.join(self.images, name + ".png"))

    def search(self, query=None):
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            scores = m.run_visual_search(self.images, query or self.query)
        return scores, out.getvalue()

    def indexed_images(self):
        return sum(x.images_seen for x in FakeModel.instances)

    def test_first_search_indexes_and_scores(self):
        scores, out = self.search()
        self.assertIn("[Index] Indexing complete.", out)
        self.assertEqual(len(scores), 70)
        self.assertEqual(self.indexed_images(), 70)
        self.assertTrue(all(x.closed for x in FakeModel.instances))

    def test_unchanged_folder_skips_indexing_after_restart(self):
        first, _ = self.search()
        m._rclip_file_count.clear()  # a new process
        FakeModel.instances = []
        second, out = self.search()
        self.assertNotIn("[Index]", out)
        self.assertEqual(first, second)

    def test_new_image_reindexes(self):
        self.search()
        m._rclip_file_count.clear()
        FakeModel.instances = []
        self.add_image("NEW1", (255, 0, 0))
        scores, out = self.search()
        self.assertIn("[Index] Images count changed", out)
        self.assertEqual(self.indexed_images(), 1)
        self.assertIn("NEW1", scores)

    def test_overwritten_image_reindexes(self):
        self.search()
        m._rclip_file_count.clear()
        path = os.path.join(self.images, "SKU5.png")
        Image.new("RGB", (4, 4), (255, 255, 0)).save(path)
        st = os.stat(path)
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
        _, out = self.search()
        self.assertIn("[Index]", out)

    def test_missing_rclip_db_reindexes(self):
        self.search()
        m._rclip_file_count.clear()
        os.remove(m._rclip_db_path())
        FakeModel.instances = []
        scores, out = self.search()
        self.assertIn("[Index]", out)
        self.assertEqual(len(scores), 70)

    def test_changed_vector_count_reindexes(self):
        self.search()
        m._rclip_file_count.clear()
        import sqlite3
        conn = sqlite3.connect(m._rclip_db_path())
        conn.execute("DELETE FROM images WHERE filepath LIKE '%SKU1.png'")
        conn.commit()
        conn.close()
        scores, out = self.search()
        self.assertIn("[Index]", out)
        self.assertIn("SKU1", scores)

    def test_stop_keeps_finished_batches_and_propagates(self):
        event = threading.Event()
        m._stop_event_local.event = event
        real = m._rclip_class()

        def stopping_class():
            class Stopping(real):
                def _index_files(self, filepaths, metas):
                    super()._index_files(filepaths, metas)
                    event.set()
                    m.check_stop()
            return Stopping

        with mock.patch.object(m, "_rclip_class", stopping_class):
            with self.assertRaises(RuntimeError) as ctx:
                self.search()
        self.assertEqual(str(ctx.exception), "StopRequested")
        self.assertTrue(all(x.closed for x in FakeModel.instances))
        self.assertEqual(m._rclip_vector_count(os.path.abspath(self.images)), 32)
        self.assertIsNone(catalog_db.index_state_get(self.images))
        m._stop_event_local.event = None
        FakeModel.instances = []
        scores, out = self.search()
        self.assertIn("[Index]", out)
        self.assertEqual(self.indexed_images(), 38)
        self.assertEqual(len(scores), 70)

    def test_features_load_once_for_two_queries(self):
        self.search()
        calls = []
        real = m._rclip_class()
        base_get = real.__mro__[1]._get_features

        def counting(self, directory):
            calls.append(directory)
            return base_get(self, directory)

        with mock.patch.object(real.__mro__[1], "_get_features", counting), \
                mock.patch.object(m, "_rclip_class", lambda: real):
            self.search(self.query + ";" + self.query)
        self.assertEqual(len(calls), 1)

    def test_indexing_error_still_searches(self):
        self.search()
        m._rclip_file_count.clear()
        self.add_image("NEW2", (1, 2, 3))
        with mock.patch("rclip.main.RClip.ensure_index", side_effect=OSError("disk")):
            scores, out = self.search()
        self.assertIn("image indexing did not finish", out)
        self.assertEqual(len(scores), 70)
        self.assertIsNone(catalog_db.index_state_get(self.images))


if __name__ == "__main__":
    unittest.main()
