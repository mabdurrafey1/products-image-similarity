"""On-disk store of title embeddings, so a title the text model has encoded once is never encoded again.

Layout, chosen for speed and size:
- One table, keyed by INTEGER PRIMARY KEY, which is SQLite's rowid: a lookup walks a single b-tree
  with no separate index. The id is the first 8 bytes of a SHA-1 of the model name and the title;
  the next 8 bytes are kept beside the vector and checked on read, so an id collision reads as a
  miss instead of handing back another title's vector.
- Vectors are raw float32 bytes (2 KB each), exactly what the model returned, so a stored vector
  scores bit-for-bit the same as a freshly encoded one.
- Large pages, so several 2 KB rows share a page instead of one row per page plus spill.
- WAL, so a search in one tab reads while another tab writes, with synchronous=NORMAL, which is
  crash-safe in WAL mode and skips a disk flush on every commit.
- Reads go through one query per chunk of ids, passed as a single JSON parameter, instead of one
  query per title.
"""
import hashlib
import json
import os
import sqlite3

import numpy as np

PAGE_SIZE = 16384
READ_CHUNK = 5000


def _split_hash(model_id, title):
    digest = hashlib.sha1(f"{model_id}\n{title}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=True), digest[8:16]


class TitleVectorStore:
    """Title -> float32 vector for one text model. Use from the thread that opened it."""

    def __init__(self, model_id, dim, path=None):
        """Without `path`, the store is the title_vectors table of the catalog database, on this
        thread's connection to it."""
        self.model_id = model_id
        self.dim = dim
        if path is None:
            import catalog_db
            self.conn = catalog_db.connect()
            if self.conn is None:
                raise RuntimeError("the catalog database is unavailable")
            self._own_conn = False
            return
        self._own_conn = True
        self.conn = sqlite3.connect(path, timeout=30)
        # page_size only takes effect before the first table exists, so it is set first
        self.conn.execute(f"PRAGMA page_size={PAGE_SIZE}")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("CREATE TABLE IF NOT EXISTS title_vectors "
                          "(id INTEGER PRIMARY KEY, check_bytes BLOB NOT NULL, vec BLOB NOT NULL)")

    def get_many(self, titles):
        """Return {title: vector} for the titles already stored."""
        wanted = {}
        for title in titles:
            row_id, check = _split_hash(self.model_id, title)
            wanted[row_id] = (title, check)

        # Sorted so each chunk walks the b-tree in order
        ids = sorted(wanted)
        found = {}
        for i in range(0, len(ids), READ_CHUNK):
            chunk = json.dumps(ids[i:i + READ_CHUNK])
            rows = self.conn.execute("SELECT id, check_bytes, vec FROM title_vectors "
                                     "WHERE id IN (SELECT value FROM json_each(?))", (chunk,))
            for row_id, check, vec in rows:
                title, expected = wanted[row_id]
                if check != expected:
                    continue
                emb = np.frombuffer(vec, dtype=np.float32)
                if emb.size == self.dim:
                    found[title] = emb
        return found

    def put_many(self, titles, vectors):
        """Store each title's vector; one another tab already stored is left as it is."""
        rows = []
        for title, emb in zip(titles, vectors):
            row_id, check = _split_hash(self.model_id, title)
            rows.append((row_id, check, np.asarray(emb, dtype=np.float32).tobytes()))
        try:
            self.conn.executemany("INSERT OR IGNORE INTO title_vectors (id, check_bytes, vec) VALUES (?, ?, ?)", rows)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def close(self):
        # The catalog connection belongs to the thread, not to this store
        if self._own_conn:
            self.conn.close()
