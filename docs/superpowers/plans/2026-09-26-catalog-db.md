# Catalog DB and fast search: Stage 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** a warm search over 100k products finishes in 30 s or less. It returns results JSON byte-identical to what HEAD returns.

**Architecture:** a new `catalog_db.py` owns `catalog.sqlite` in the DuplicateFinder app folder. It caches:
- each parsed Excel frame, behind an exactness gate;
- that frame's title-token index;
- the title vectors;
- the image-index state.

`match_image_ai.py` and `generate_report.py` read frames through it and fall back to parsing, as today, when the DB can't be used. The `iterrows` loops become column reads with identical `str()` semantics. rclip indexing is built here: it commits as it goes, stops on request, and persists its completion state.

**Tech Stack:** Python 3.12, sqlite3 (WAL), pandas 3.0.3, numpy 2.1.3, rclip 3.0.9, unittest.

**Spec:** `docs/superpowers/specs/2026-09-26-catalog-db-design.md`

## Global Constraints
- The results JSON is byte-identical to what HEAD `match_image_ai.py` writes for the same inputs: Row, order, scores and every field.
- No Python object serialization. Frames use the typed encoding in spec §3.1.
- Any DB error falls back to parsing the Excel file. A search never requires the DB.
- The GUI progress strings are unchanged:
  - `[Download Progress] P% (d/t)`
  - `Matching titles: d/t (P%)`
  - `[Index] ...`
  - `Loaded N products from the database.`
- Do not commit, tag or push (release-only rule). Never stage `input_data/data set ahmad.xlsx`.
- Run tests with `.venv/bin/python3 -m unittest discover -s tests`. The existing 145 tests (4 skipped) stay green.

---

### Task 1: `catalog_db.py`: connection, schema, frame encoding with exactness gate

**Files:** Create `catalog_db.py`, Test `tests/test_catalog_db.py`

**Produces:**
- `set_path(path_or_None)`: a test hook that resets the per-thread connections.
- `db_path() -> str`
- `connect() -> sqlite3.Connection | None`: one connection per thread. Returns None when the DB is disabled (a newer schema, or an unrecoverable error).
- `encode_frame(df) -> bytes | None`: None means uncacheable. A non-None result has passed the gate.
- `decode_frame(blob) -> DataFrame`

Steps:
- [ ] **Tests.**
  - Round trips:
    - a str dtype with NaN;
    - float64 with NaN, inf and -0.0;
    - int64;
    - bool;
    - datetime64 with NaT;
    - an object column mixing str, int, float, NaN, None, bool and datetime.
  - Uncacheable cases, which must return None:
    - a non-Range index;
    - a non-str column name;
    - the category dtype;
    - a tz-aware datetime in an object column;
    - a `pd.Timestamp` in an object column.
  - Checks for each round trip: `equals`, identical dtypes, identical names, and identical per-value types.
  - A corrupt DB file is moved to `catalog.sqlite.corrupt-*`, and a fresh one works.
  - A `user_version` above `SCHEMA_VERSION` makes `connect()` return None and leaves the file untouched.
- [ ] **Run.** `.venv/bin/python3 -m unittest tests.test_catalog_db -v` fails with ImportError.
- [ ] **Implement.**
  - Layout: an 8-byte big-endian length, then a JSON header, then payload chunks.
  - Column kinds:
    - `raw`: numpy bool, int, uint, float, and naive datetime64/timedelta64 viewed as int64.
    - `str`: a JSON list with null for NaN.
    - `obj`: a type string plus a JSON list. Tags:
      - `s`: str
      - `i`: int
      - `f`: float, stored as `float.hex`
      - `b`: bool
      - `n`: None
      - `d`: naive `datetime.datetime`
      - `T`: `datetime.time`
      - `F`: np.float64
      - `I`: np.int64
  - The gate decodes again and compares.
- [ ] **Run.** Tests pass.
- [ ] **No commit** (release-only rule).

### Task 2: `load_source` with LRU, replayed parse messages and token index

**Files:** Modify `catalog_db.py`, Test `tests/test_catalog_db.py`

**Produces:**
- `SourceEntry(frame, tokens, from_cache)`:
  - `frame` is a fresh deep copy.
  - `tokens` is `{token: [row positions]}`, or None when there is no Title column or the frame is uncacheable.
- `load_source(path, parse, tokenize, tag) -> SourceEntry`:
  - `parse(path, messages)` prints each line and appends it to `messages`.
  - A hit prints the stored messages again, so the log reads the same.
  - The key is `normcase(abspath)`, size, mtime_ns and tag. A touched file or a new tag re-imports.
  - On any DB exception it prints a single warning and parses.
- The LRU is capped at 300k rows in total.

Steps:
- [ ] **Tests.**
  - A second call skips `parse` and prints the same messages.
  - Touching the file re-parses.
  - A new tag re-parses.
  - An uncacheable frame is parsed every time.
  - A disabled DB still returns the parsed frame.
  - Mutating a returned frame doesn't leak into the next call.
  - The tokens equal the brute-force `{t: positions}` over `str(v)` of Title.
- [ ] **Run.** Tests fail.
- [ ] **Implement.** `sources` table: `path` (PK), `size`, `mtime_ns`, `tag`, `rows`, `frame`, `tokens` (JSON), `messages` (JSON) and `imported_at`.
- [ ] **Run.** Tests pass.
- [ ] **No commit.**

### Task 3: wire `load_dataset` and add the token-index prefilter

**Files:** Modify `match_image_ai.py`, Test `tests/test_match_catalog.py`

**Interfaces:**
- `TITLE_STOP_WORDS` (a frozenset), and `title_tokens(t) = set(clean_title(t).split()) - TITLE_STOP_WORDS`.
- `load_excel_with_sheets(file_path, messages=None, quiet=False)`. Unchanged when called the old way.
- `load_dataset_indexed(input_path) -> (df, TitleIndex | None)`. `load_dataset(input_path)` returns the df only, so GUI sync is unchanged.
- `TitleIndex.candidates(df, ref_tokens) -> list[label] | None`:
  - None means brute force.
  - It validates that `[str(v) for v in df['Title'].tolist()]` equals the stored titles at `df.index`.
- `run_semantic_text_search(..., title_index=None)`. Rows for kept titles come from `df.loc[kept].iterrows()`.
- `CATALOG_TAG` combines:
  - the parser version;
  - the pandas and openpyxl versions;
  - a bytecode fingerprint of `load_excel_with_sheets`, `normalize_dataframe` and `clean_title`;
  - the stop words.

Steps:
- [ ] **Tests.**
  - Inputs: synthetic xlsx files with a float Title, a missing Title in one file, NaN, empty, punctuation-only, stop-words-only and Unicode titles, and duplicate SKUs.
  - The index candidates equal brute force, with and without the price filter.
  - The frames equal the HEAD algorithm's frames.
  - The rows are equal, including value types.
- [ ] **Run.** Tests fail.
- [ ] **Implement.**
- [ ] **Run.** Tests pass.
- [ ] **No commit.**

### Task 4: vectorized downloader task list and visual-only lookup

**Files:** Modify `downloader.py` and `match_image_ai.find_visual_only_matches`

**Interfaces:**
- `downloader.build_download_tasks(df, image_dir) -> list[(sku, url, path)]`.
- It returns the same list, in the same order, as the old loop.
- A scandir name set answers for regular files. Anything else falls back to `os.path.exists`.

Steps:
- [ ] **Tests.**
  - Downloader, old vs new, on a frame with NaN, 'nan', blanks, spaces, a symlink, an existing file and a case variant.
  - Visual-only map, old vs new, with duplicate SKUs.
- [ ] **Run.** Tests fail.
- [ ] **Implement.**
- [ ] **Run.** Tests pass.
- [ ] **No commit.**

### Task 5: stoppable, resumable rclip indexing, persisted state, one vector load per search

**Files:** Modify `catalog_db.py` (`index_state_get`, `index_state_set`) and `match_image_ai.run_visual_search`

**Interfaces:**
- An `RClip` subclass:
  - `_index_files` calls super, commits every 8 batches, then calls `check_stop()`.
  - `_get_features` is memoized per directory.
- `index_state` holds three values: the listdir count, the dir mtime_ns, and the vector count in the rclip DB for that folder. The state is stale unless all three match.

Steps:
- [ ] **Tests.**
  - Stopping mid-index keeps the finished batches, closes the DB, and propagates StopRequested.
  - An unchanged folder skips indexing.
  - A missing rclip DB, or a changed vector count, re-indexes.
  - Features load once for two query images.
- [ ] **Run.** Tests fail.
- [ ] **Implement.**
- [ ] **Run.** Tests pass.
- [ ] **No commit.**

### Task 6: report reads cached frames; extra_attrs only for result SKUs

**Files:** Modify `generate_report.py:126-144`

- [ ] **Test.** Old vs new `extra_attrs` restricted to the results' SKUs, with duplicate SKUs across and within files. The last row wins.
- [ ] **Implement.**
  - The frame comes from `match_image_ai.load_source_frame(ep, quiet=True)` when `ep` is a file; otherwise it takes the old path.
  - Rows come from `df[mask].iterrows()`.
- [ ] **Run.** Tests pass.

### Task 7: title vectors move into `catalog.sqlite`
- [ ] `TitleVectorStore`'s default path becomes `catalog_db.db_path()`. The schema is unchanged.

### Task 8: verification
- [ ] Old vs new on the 4 real stores, both cold (empty catalog) and warm. Modes: normal, strict, price-filtered and multi-image. The JSON is byte-identical.
- [ ] The report HTML is identical once the timestamp is stripped.
- [ ] A warm search over 100k rows takes ≤ 30 s, with stage timings.
- [ ] The full suite is green.
