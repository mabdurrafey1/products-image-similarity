# Catalog DB and fast search: design

**Status:** approved in chat on 2026-09-25 ("approved, go ahead / make sure to leave no loopholes and no ripples").
**Goal:** a search over a 100k-product catalog finishes in 30 s or less once the catalog is prepared. It must return exactly
what it returns today: the same rows, in the same order, with the same scores and Row numbers.

## 1. What is actually slow (measured on this Mac, 2026-09-25/26)

| Step, per search today | Cost | Why |
|---|---|---|
| Indexing images that a store fetch downloaded but no search has indexed yet | **~10 min for 41,032 images at 68 img/s here, slower on the client PC** | rclip indexes every new image inside the search |
| The same indexing, repeated after the app is closed or a search fails midway | the whole cost again | rclip commits only at 50k images or at the end; Stop is never checked while indexing; an exception skips `close()` |
| Re-trying 348 images whose download failed before | 10 s to minutes (15 s timeout, 3 retries) | failures are not remembered; Stop does not reach the download threads |
| Parsing the Excel files twice (search, then report) | 1.4–1.9 s per file, twice | nothing is cached |
| `iterrows` over every row (downloader, keyword prefilter, visual-only lookup, report) | 2–5 s each at 100k | row-by-row Python |
| Re-walking the image folder on the first search after every app start | 1–3 s at 100k | the "already indexed" count lives in memory only |
| Re-reading all image vectors once per query image | 0.35 s per query image at 59k | rclip reloads them on each `search()` |

Also found: 11,054 junk files in the image folder with pandas text in their names (`SKU    N…\nName: 604, dtype: object.jpg`),
made on 2026-06-09 by an old duplicate-SKU-column bug that `normalize_dataframe` has since fixed. rclip already ignores
them (its filename regex does not match a newline), so they do not affect results. Deleting them is left to the user.

## 2. Architecture

A single SQLite file, `catalog.sqlite`, in the app folder next to `config.json`
(`~/Library/Application Support/DuplicateFinder` on macOS, `%APPDATA%\DuplicateFinder` on Windows). SQLite is in the
standard library, is indexed, and allows many readers alongside one writer in WAL mode. DuckDB was faster in the
benchmark but was rejected: it is another dependency to package, and it allows only one writing process.

The Excel files stay the source of truth. The fetch keeps writing `.xlsx` exactly as it does now. The DB mirrors each
file and re-imports it only when its path, size or modification time changes.

New module **`catalog_db.py`**. It owns the file and gives each thread its own connection.

| Table | Holds | Key |
|---|---|---|
| `sources` | one row per imported Excel file: size, mtime_ns, parser tag, the parsed frame (typed encoding, §3.1), and its title-token index (JSON) | absolute path |
| `title_vectors` | title embeddings; the same scheme as `title_vector_store.py`, which moves into this file | 64-bit hash id plus 8 check bytes |
| `index_state` | per image folder: file count and folder mtime_ns at the last *completed* index | absolute folder path |
| `image_failures` | SKU, URL and time of the last failed download | SKU |
| `searches` | one row per finished search: time, query images, query title, settings, source files, result count, JSON and report paths, and the results JSON | autoincrement id |

The schema version is kept in `PRAGMA user_version`. The parser tag is `"{PARSER_VERSION}|pandas {pd.__version__}"`.
A pandas upgrade or a change to `load_excel_with_sheets` (bump `PARSER_VERSION`) forces a re-import, so a cached frame
can never differ from what parsing the file would give now.

**Deviations from the design approved in chat, and why:**
- **No `products` table with a row per product.** Exactness depends on pandas' own concat, dtype and `to_numeric`
  semantics. The cached frames are exactly what `load_excel_with_sheets` returned, and in-memory vectorized pandas at
  100k rows takes milliseconds. A products table would reimplement pandas and risk differences for no measurable gain.
- **The price filter stays in pandas,** for the same reason.

## 3. Components

### 3.1 Frame cache: `catalog_db.load_source(path)`
- It stats the file. If a `sources` row has the same size, mtime_ns and parser tag, it decodes and returns the frame.
  Otherwise it calls `match_image_ai.load_excel_with_sheets(path)` (so it prints what it prints today), builds the
  token index, encodes, and upserts the row.
- **Encoding, with no pickle:**
  - A column list, and for each column its dtype name.
  - Numeric, bool and datetime64 columns are stored as raw array bytes.
  - `str` (pandas 3 StringDtype) columns are stored as a JSON list with `null` for NaN.
  - `object` columns are stored as JSON of type-tagged values (str, int, float including NaN and inf, bool, None, datetime).
  - Anything else (another dtype, a value of another type, or an index other than a RangeIndex) makes that file
    *uncacheable*. It is then parsed every time, exactly as today.
- **Exactness gate:** before a frame is stored, it is decoded again and must pass `DataFrame.equals`, identical dtypes,
  identical column names, and identical per-value Python types in object columns. If it fails, the file is uncacheable.
- The frame is stored *without* `Source File`. `load_dataset` adds that column exactly as it does today.
- `match_image_ai.load_dataset` calls it for each file, so concat order, `ignore_index`, and therefore Row numbers are unchanged.
- An in-process LRU cache (8 entries) keyed on (path, size, mtime_ns, parser tag) serves the report and repeat searches.
  Callers always get a `.copy()`, because the price filter assigns to `df['Price']`.
- Any DB error falls back to parsing the file directly, with a one-line warning. The DB is never required for a search to work.

### 3.2 Keyword prefilter from the token index
- For each source frame: `tokens(str(v))` for every value `v` of `Title`, where `tokens(t)` is
  `set(clean_title(t).split()) - STOP_WORDS`, the same sets `get_title_similarity` builds. It is stored as
  `{token: [row positions]}`.
- `get_title_similarity(ref, t) > 0` holds exactly when the two token sets intersect, because a non-empty intersection
  implies both sets are non-empty. So the candidate rows are the union of the positions of the reference's tokens.
  The source's cumulative offset is added, the rows removed by the price filter are dropped, and the rest are sorted
  ascending. That is the order `iterrows` used to visit them. An empty `str(v)` has no tokens, so it is never a
  candidate, the same as today.
- It is used only when every selected file came from the cache and the concatenated `Title` column's `str()` values
  equal the cached ones (a cheap check). Otherwise the old brute-force path runs.
- A frame with no `Title` column gives no candidates, the same as today.
- Rows (`df.loc[idx]`) are built only for titles that survive the text check, not for every candidate.
  `run_semantic_text_search` keeps its signature and return value.

### 3.3 Vectorized row loops
- `download_missing_images`: SKU and URL come from `tolist()` with the same `str(...).strip()` and `'nan'` rules. The
  existence check uses one `os.scandir` name set (with `os.path.exists` as a fallback for names the set cannot answer,
  such as case-insensitive filesystems). The same tasks are created in the same order.
- `find_visual_only_matches`: the SKU → first index map is built from `tolist()` instead of `iterrows`.
- Report `extra_attrs`: built from cached frames, only for SKUs present in the results. It is still keyed by
  (file, SKU upper-cased, not stripped), and the last row still wins.

### 3.4 Image indexing that is stoppable and never loses work
- `match_image_ai` builds rclip itself instead of calling `init_rclip(no_indexing=False)`, using a small `RClip`
  subclass whose `_index_files` commits after every batch and then calls a stop check. The rest of `ensure_index` is rclip's own code.
- The index DB and model are always closed in a `finally`; closing commits.
- Stop now interrupts indexing within one batch (32 images, about 0.5 s). Everything indexed up to that point is kept.
- On completion, `index_state` stores the folder's file count and mtime_ns. On the next search, including after an app
  restart, if both still match, the walk is skipped. That is the same count check as today, now persisted, with the
  folder mtime added so that a rename or same-count swap still triggers a walk.
- An indexing failure is printed and the search goes on with the vectors that exist. This is what happens today.
  Stop is re-raised.

### 3.5 One vector load per search
The image matrix is loaded once per search and shared across query images through a `_get_features` override on the
searching `RClip` instance. The scoring, sort, filters and `top_k` stay rclip's own, so scores and order are unchanged.

### 3.6 Background preparation (Stage 2)
- After a store fetch or refresh finishes, and once at app start, a background thread downloads missing images for
  every `.xlsx` in `input_data` and then indexes the image folder.
- It yields to searches: before each batch it checks whether a search is waiting for the index lock. If one is, it
  commits and steps aside, then resumes after the search.
- Progress shows in the main window ("Preparing images: 12,345 / 41,032").
- The minutes of indexing now happen right after a fetch, not inside the client's next search.

### 3.7 Download failures are remembered (Stage 2)
- A failed download is recorded in `image_failures`. Searches skip a SKU whose last failure is under 24 h old for the
  same URL. The background job retries them after 24 h.
- The search passes its stop event to the downloader as `should_stop`, so Stop reaches the download threads.
- This is a deliberate behaviour change: a transient CDN failure is retried the next day, not on every search.

### 3.8 Search history (Stage 3)
- Each finished search writes one `searches` row. The `--output` JSON and the HTML report are still written exactly as today.
- The GUI gets a "History" list (newest first, 200 kept) that re-opens a past report, or regenerates it from the stored
  results if the HTML file was deleted.

## 4. Error handling and safety
- **A corrupt DB** (`sqlite3.DatabaseError` on open or on the schema check) is renamed to
  `catalog.sqlite.corrupt-{timestamp}` and rebuilt. If even that fails, the search runs from Excel as today.
- **Schema versioning:** a `user_version` newer than this build knows means a newer app wrote the file. This build then
  uses no DB at all (falls back to Excel) rather than touching it.
- **Concurrency:** WAL, `busy_timeout=30000`, one connection per thread, and short transactions. Two tabs importing the
  same file at once both parse it; the second write is an `INSERT OR REPLACE` with identical content.
- **Stop:** every long loop (downloads, indexing, encoding) checks the tab's stop event.
- **Windows:** paths are stored as `os.path.normcase(os.path.abspath(p))`. There is no shell or path-separator logic in the DB layer.

## 5. Testing
- **Unit tests** in `tests/`, run with `unittest`:
  - The encoding round trip, including NaN, inf, mixed object columns, str dtype, and uncacheable cases.
  - Import is idempotent; a touched file or a changed parser tag re-imports.
  - The token-index prefilter equals brute force on random and edge titles: NaN, numbers, empty, punctuation only,
    stop words only, Unicode.
  - The prefilter matches brute force under price filtering.
  - The vectorized downloader task list equals the old one.
  - Visual-only matching equals the old path.
  - A corrupt DB is moved aside.
  - Indexing stop and resume keeps committed work.
  - Download failure backoff.
  - History writes and pruning.
- **Old-vs-new exactness:** HEAD `match_image_ai.py` against the new code on the 4 real stores. Normal, strict,
  price-filtered, and multi-image queries. The results JSON must be byte-identical (Row, order, scores, all fields).
  The report HTML must be identical apart from the timestamp.
- **Speed:** a warm search over 100k rows (catalog prepared, index current) takes 30 s or less end to end, report
  included, with stage timings printed.
- The existing suite (145 tests, 4 skipped) stays green.

## 6. Stages
1. `catalog_db.py` (sources, title vectors, index state), cached frames in search and report, token-index prefilter,
   vectorized loops, stoppable and resumable indexing, one vector load per search.
2. Background preparation after fetch and at start, download-failure memory, Stop reaching the downloads.
3. Search history table and GUI list.

Nothing is committed until the user says "release".
