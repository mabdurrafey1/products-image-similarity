from downloader import download_missing_images
import os
import argparse
import json
import re
import pandas as pd

import threading
from concurrent.futures import ThreadPoolExecutor

MATCH_WORKERS = min(32, (os.cpu_count() or 4) * 4)
# Not MATCH_WORKERS: each text-model call already spreads across every core, so parallel calls
# contend for them. Measured on 10 cores: 2 threads ran 1.34x faster, 4 ran 1.5x slower, 8 ran 6x slower.
TEXT_MATCH_WORKERS = 2

# Legacy global flag kept for backward compatibility with CLI usage.
# GUI tabs pass a per-tab threading.Event instead.
stop_requested = False
_stop_event_local = threading.local()

# Lock used ONLY for re-indexing when new images appear.
# Search is always fully parallel with independent DB connections.
_rclip_lock = threading.Lock()
_rclip_file_count = {}  # abs_image_dir → folder signature at the last finished index

def check_stop():
    """Raise StopRequested if the current tab's stop event is set, or the global flag is True."""
    local_event = getattr(_stop_event_local, 'event', None)
    if local_event is not None:
        if local_event.is_set():
            raise RuntimeError("StopRequested")
    elif stop_requested:
        raise RuntimeError("StopRequested")

def clean_title(title):
    """Clean and normalize product titles for keyword overlap comparison."""
    if not title or not isinstance(title, str):
        return ""
    t = title.lower()
    t = re.sub(r'[^a-z0-9\s-]', ' ', t)
    t = t.replace("rear view", "rearview")
    t = t.replace("rearviewmirror", "rearview mirror")
    return t

def extract_models(text):
    """Extract alphanumeric model identifiers (e.g., X6, D007, R36S)."""
    words = re.findall(r'\b[a-z0-9-]+\b', text)
    models = set()
    for w in words:
        if w.isdigit():
            continue
        has_digit = any(c.isdigit() for c in w)
        has_alpha = any(c.isalpha() for c in w)
        if has_digit and has_alpha:
            models.add(w)
    return models

def is_generic_mismatch(title_a, title_b):
    """Check if there is a generic model or category mismatch between two product titles."""
    t_a = clean_title(title_a)
    t_b = clean_title(title_b)
    
    # 1. Alphanumeric model identifier mismatch (e.g., X6 vs D007 vs R36S vs M21)
    models_a = extract_models(t_a)
    models_b = extract_models(t_b)
    if models_a and models_b:
        # If both contain models but they don't overlap, it's a mismatch
        if not models_a.intersection(models_b):
            return True

    # 2. Number/Model differences (normalized as integers to handle commas/zeros)
    numbers_a = {int(num) for num in re.findall(r'\b\d+\b', t_a)}
    numbers_b = {int(num) for num in re.findall(r'\b\d+\b', t_b)}
    diff_numbers = numbers_a.symmetric_difference(numbers_b)
    
    # Ignore common spec numbers (game count, storage, battery, dimensions, etc.)
    spec_numbers = {
        0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 15, 16, 18, 20, 22, 23, 24, 25, 26, 30, 32, 36, 40, 45, 50, 60, 64, 80, 128, 152, 203, 256, 268, 
        500, 512, 520, 666, 1000, 3500, 4000, 6000, 10000, 15000, 18000, 20000, 30000, 40000
    }
    diff_numbers = {num for num in diff_numbers if num not in spec_numbers}
    if diff_numbers:
        return True
        
    # 3. Generic model modifiers
    modifiers = {
        'pro', 'max', 'plus', 'ultra', 'mini', 'lite', 'se', 'air', 'series', 
        'generation', 'gen', 'active', 'sport'
    }
    words_a = set(t_a.split())
    words_b = set(t_b.split())
    for mod in modifiers:
        if (mod in words_a) != (mod in words_b):
            return True
            
    return False

# Generic words that never count as keyword overlap between two titles
TITLE_STOP_WORDS = frozenset({'with', 'in', 'and', 'for', 'of', 'on', 'at', 'a', 'an', 'the', 'to', 'from', 'by',
                              'is', 'it', 'or', 'image', '1', '2', '3', '4'})

def title_tokens(title):
    """The keywords get_title_similarity compares: two titles overlap exactly when these sets intersect."""
    return set(clean_title(title).split()) - TITLE_STOP_WORDS

def get_title_similarity(title_a, title_b):
    """Compute the overlap ratio of unique keywords between two titles."""
    words_a = title_tokens(title_a)
    words_b = title_tokens(title_b)
    
    if not words_a or not words_b:
        return 0.0
        
    intersection = words_a.intersection(words_b)
    overlap_ratio = len(intersection) / min(len(words_a), len(words_b))
    return overlap_ratio


def normalize_dataframe(df):
    """Normalize column names from the final Excel layout format."""
    mapping = {
        'sku': 'SKU',
        'Sku': 'SKU',
        'Product Title': 'Title',
        'Main Image URL': 'Image URL',
        'PartnerSKU': 'psku',
        'PartnerSku': 'psku',
        'partner_sku': 'psku',
        'Partner SKU': 'psku',
        'PSKU': 'psku',
        'psku': 'psku'
    }
    
    rename_dict = {}
    assigned_targets = set(df.columns)
    
    preferred_order = ['sku', 'Sku', 'Product Title', 'Main Image URL', 'PartnerSKU', 'PartnerSku', 'partner_sku', 'Partner SKU', 'PSKU', 'psku']
    
    for col in preferred_order:
        if col in df.columns and col in mapping:
            target = mapping[col]
            if target not in assigned_targets:
                rename_dict[col] = target
                assigned_targets.add(target)
                
    if rename_dict:
        df = df.rename(columns=rename_dict)
    return df

def load_excel_with_sheets(file_path, messages=None, quiet=False):
    """Load the Best_One_Row_Per_SKU sheet from an Excel file, falling back to default loading.

    Every line it prints is also appended to `messages` when one is given, so the catalog database
    can print the same lines when it serves this file from its cache. `quiet` prints only errors.
    """
    def say(message, error=False):
        if error or not quiet:
            print(message)
        if messages is not None:
            messages.append(message)

    try:
        xls = pd.ExcelFile(file_path)
        sheet_name = None
        if 'Best_One_Row_Per_SKU' in xls.sheet_names:
            sheet_name = 'Best_One_Row_Per_SKU'
            
        if sheet_name:
            say(f"Detected multi-sheet Excel in '{os.path.basename(file_path)}'. Loading sheet: '{sheet_name}'")
            df = pd.read_excel(xls, sheet_name=sheet_name)
        else:
            df = pd.read_excel(xls)
        return normalize_dataframe(df)
    except Exception as e:
        say(f"Error reading Excel file '{file_path}': {e}", error=True)
        if messages is not None:
            # Read around an error that may not happen next time, so it is never cached
            messages.cacheable = False
        # Fallback to direct reading
        df = pd.read_excel(file_path)
        return normalize_dataframe(df)

# Bumped by hand whenever loading changes in a way the fingerprint below can't see
PARSER_VERSION = 1

def _code_fingerprint(code, digest):
    import types
    digest.update(code.co_code)
    digest.update(repr(code.co_names).encode())
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            _code_fingerprint(const, digest)
        elif isinstance(const, frozenset):
            digest.update(repr(sorted(map(repr, const))).encode())
        else:
            digest.update(repr(const).encode())

def _catalog_tag():
    """What a cached frame depends on besides the file itself. Any change means a fresh import."""
    import hashlib
    import sys
    import numpy
    try:
        import openpyxl
        openpyxl_version = openpyxl.__version__
    except Exception:
        openpyxl_version = "none"
    digest = hashlib.sha256()
    for fn in (load_excel_with_sheets, normalize_dataframe, clean_title):
        _code_fingerprint(fn.__code__, digest)
    digest.update(repr(sorted(TITLE_STOP_WORDS)).encode())
    return (f"{PARSER_VERSION}|py {sys.version_info[:3]}|pandas {pd.__version__}|numpy {numpy.__version__}"
            f"|openpyxl {openpyxl_version}|{digest.hexdigest()[:16]}")

CATALOG_TAG = _catalog_tag()

def _load_source_entry(file_path, quiet=False):
    """(frame, tokens, titles) for one Excel file: the frame load_excel_with_sheets returns, served
    from the catalog database when the file hasn't changed. `tokens` and `titles` feed TitleIndex and
    are None when this file can't use it."""
    try:
        import catalog_db
    except Exception:
        return load_excel_with_sheets(file_path, quiet=quiet), None, None
    show = (lambda m: m.startswith("Error reading Excel file")) if quiet else None
    entry = catalog_db.load_source(file_path, lambda p, m: load_excel_with_sheets(p, m, quiet=quiet),
                                   title_tokens, CATALOG_TAG, show=show)
    if entry.tokens is None or 'Title' not in entry.frame.columns:
        return entry.frame, None, None
    return entry.frame, entry.tokens, [str(v) for v in entry.frame['Title'].tolist()]

def load_source_frame(file_path, quiet=False):
    """The frame load_excel_with_sheets(file_path) returns, from the catalog database when it can."""
    return _load_source_entry(file_path, quiet=quiet)[0]

def _rows_are_objects(df):
    """True when df.iterrows() hands out each value as the same object tolist() does.

    iterrows builds each row from df.values. With any text column that is an object array, so a row
    holds the column's own values. A frame of numbers only is upcast instead (an int SKU comes out
    as 5.0), and the column-wise shortcuts must not be used on it.
    """
    return df.columns.is_unique and df.iloc[:0].values.dtype == object

class TitleIndex:
    """Which rows can share a keyword with a reference title, without looking at every row.

    Built from each file's cached {token: [row positions]}. A row is a keyword candidate exactly when
    its title's tokens meet the reference's (see title_tokens), so the candidates are the union of
    the reference tokens' positions. Anything unexpected makes candidates() return None, and the
    caller then checks every row as before.
    """
    def __init__(self, parts, titles):
        self.parts = parts            # [(row offset, {token: [positions within that file]})]
        self.titles = titles          # str() of every Title value, in row order

    def candidates(self, df, ref_tokens):
        """[(row label, title)] in row order for every row whose title shares a keyword, or None."""
        try:
            if 'Title' not in df.columns or not _rows_are_objects(df):
                return None
            index = df.index
            if not (index.is_unique and index.is_monotonic_increasing and index.dtype.kind in "iu"):
                return None
            labels = index.tolist()
            if labels and (labels[0] < 0 or labels[-1] >= len(self.titles)):
                return None
            current = [str(v) for v in df['Title'].tolist()]
            titles = self.titles
            if current != [titles[i] for i in labels]:
                return None
            positions = set()
            for tok in ref_tokens:
                for offset, tokens in self.parts:
                    hits = tokens.get(tok)
                    if hits:
                        positions.update(offset + p for p in hits)
            if len(labels) != len(titles):
                positions.intersection_update(labels)
            return [(label, titles[label]) for label in sorted(positions)]
        except Exception:
            return None

def _load_dataset_parts(input_path):
    """load_dataset's frame, plus TitleIndex parts [(offset, tokens)] and titles (both None when any
    file can't be indexed). Mirrors load_dataset's nesting exactly, so the concat is the same."""
    if isinstance(input_path, (list, tuple)):
        paths = list(input_path)
    else:
        paths = [p.strip() for p in str(input_path).split(";") if p.strip()]

    if len(paths) > 1:
        print(f"Loading {len(paths)} selected Excel files...")
        loaded = []
        for p in paths:
            try:
                loaded.append(_load_dataset_parts(p))
            except Exception as e:
                print(f"Warning: Could not load '{p}': {e}")
        if not loaded:
            raise ValueError("Could not load any of the selected dataset files.")
        return (pd.concat([df for df, _, _ in loaded], ignore_index=True),) + _join_parts(loaded)

    input_path = paths[0] if paths else input_path

    if os.path.isdir(input_path):
        import glob
        excel_files = glob.glob(os.path.join(input_path, "*.xlsx"))
        if not excel_files:
            raise FileNotFoundError(f"No Excel (.xlsx) files found in directory '{input_path}'")
        print(f"Loading {len(excel_files)} Excel files from '{input_path}'...")
        loaded = []
        for f in sorted(excel_files):
            try:
                temp_df, tokens, titles = _load_source_entry(f)
                temp_df['Source File'] = os.path.basename(f)
                loaded.append((temp_df, None if tokens is None else [(0, tokens)], titles))
            except Exception as e:
                print(f"Warning: Could not read '{f}': {e}")
        if not loaded:
            raise ValueError(f"Could not load any Excel files from directory '{input_path}'")
        return (pd.concat([df for df, _, _ in loaded], ignore_index=True),) + _join_parts(loaded)
    else:
        df, tokens, titles = _load_source_entry(input_path)
        df['Source File'] = os.path.basename(input_path)
        return df, (None if tokens is None else [(0, tokens)]), titles

def _join_parts(loaded):
    parts, titles, offset = [], [], 0
    for df, sub_parts, sub_titles in loaded:
        if sub_parts is None or sub_titles is None or len(sub_titles) != len(df):
            return None, None
        parts.extend((offset + o, tokens) for o, tokens in sub_parts)
        titles.extend(sub_titles)
        offset += len(df)
    return parts, titles

def load_dataset_indexed(input_path):
    """(load_dataset(input_path), TitleIndex or None)."""
    df, parts, titles = _load_dataset_parts(input_path)
    index = None
    if parts is not None and titles is not None and len(titles) == len(df):
        index = TitleIndex(parts, titles)
    return df, index

def load_dataset(input_path):
    """Load dataset Excel files from a single file, a directory, or multiple files
    (either a list of paths or a single ';'-separated string of paths)."""
    return load_dataset_indexed(input_path)[0]

def resolve_reference_title(df, query_path, query_title, visual_scores=None):
    """Retrieve or fallback to baseline reference title for similarity checks."""
    if query_title:
        print(f"Baseline model reference specified by user: '{query_title}'\n")
        return query_title
        
    # Split query_path to check multiple SKUs in case of multiple query images
    query_list = [q.strip() for q in query_path.split(";") if q.strip()]
    for q in query_list:
        query_basename = os.path.splitext(os.path.basename(q))[0]
        query_matching_rows = df[df['SKU'].astype(str) == query_basename]
        if not query_matching_rows.empty:
            reference_title = str(query_matching_rows.iloc[0].get('Title', ''))
            print(f"Baseline model reference determined from query filename ({query_basename}): '{reference_title}'\n")
            return reference_title

    # Fallback to the highest visual match title if not resolved yet
    if visual_scores:
        sorted_visual = sorted(visual_scores.items(), key=lambda x: x[1], reverse=True)
        for sku, score in sorted_visual:
            matching_rows = df[df['SKU'].astype(str) == sku]
            if not matching_rows.empty:
                reference_title = str(matching_rows.iloc[0].get('Title', ''))
                print(f"Baseline model reference determined from Rank 1 visual match: '{reference_title}'\n")
                return reference_title
                
    return None

_INDEX_COMMIT_EVERY = 8  # batches of 32 between commits, so a stop keeps all but the last few


def _is_stop(error):
    return str(error) == "StopRequested"


def _folder_signature(abs_image_dir):
    """(entry count, newest mtime_ns of the folder or any entry in it), or None if unreadable.

    The count alone misses a picture overwritten in place (the downloader rewrites a file when a
    listing's image changes), which leaves the count and the folder's own mtime as they were but
    moves that file's mtime forward. Taking the newest mtime of everything catches that too.
    """
    try:
        newest = os.stat(abs_image_dir).st_mtime_ns
        count = 0
        with os.scandir(abs_image_dir) as entries:
            for entry in entries:
                count += 1
                try:
                    newest = max(newest, entry.stat().st_mtime_ns)
                except OSError:
                    pass
        return count, newest
    except OSError:
        return None


def _rclip_db_path():
    from rclip.utils import helpers
    return os.path.join(str(helpers.get_app_datadir()), "db.sqlite3")


def _rclip_vector_count(abs_image_dir):
    """How many live vectors rclip holds for this folder, read without touching its DB; None if unknown."""
    import sqlite3
    import pathlib
    from rclip import db as rclip_db
    path = _rclip_db_path()
    if not os.path.isfile(path):
        return None
    try:
        conn = sqlite3.connect(pathlib.Path(path).as_uri() + "?mode=ro", uri=True)
        try:
            row = conn.execute("SELECT COUNT(*) FROM images WHERE filepath LIKE ? ESCAPE '\\' AND deleted IS NULL",
                               (rclip_db.DB._get_dirpath_like_pattern(abs_image_dir),)).fetchone()
        finally:
            conn.close()
        return int(row[0])
    except Exception:
        return None


def _saved_index_state(abs_image_dir):
    try:
        import catalog_db
        return catalog_db.index_state_get(abs_image_dir)
    except Exception:
        return None


def _save_index_state(abs_image_dir, signature, vectors):
    try:
        import catalog_db
        if signature is None or vectors is None:
            catalog_db.index_state_clear(abs_image_dir)
        else:
            catalog_db.index_state_set(abs_image_dir, signature[0], signature[1], vectors)
    except Exception:
        pass


def _index_is_current(abs_image_dir, signature):
    """True only when the folder and rclip's vectors are both exactly as the last finished index left them."""
    if signature is None:
        return False
    if _rclip_file_count.get(abs_image_dir) == signature:
        return True
    saved = _saved_index_state(abs_image_dir)
    if saved is None or tuple(saved[:2]) != tuple(signature):
        return False
    return _rclip_vector_count(abs_image_dir) == saved[2]


def _rclip_class():
    from rclip.main import RClip

    class _SearchRClip(RClip):
        """rclip that commits as it indexes, stops when asked, and loads a folder's vectors once."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._batches = 0
            self._features = {}

        def _index_files(self, filepaths, metas):
            super()._index_files(filepaths, metas)
            self._batches += 1
            if self._batches % _INDEX_COMMIT_EVERY == 0:
                self._db.commit()
            check_stop()

        def _get_features(self, directory):
            if directory not in self._features:
                self._features[directory] = super()._get_features(directory)
            return self._features[directory]

    return _SearchRClip


def _open_rclip(indexing):
    from rclip import db as rclip_db, model as rclip_model
    database = rclip_db.DB(_rclip_db_path(), allow_vector_cache_reset=indexing)
    try:
        model_instance = rclip_model.Model()
        model_instance.ensure_downloaded()
    except BaseException:
        database.close()
        raise
    instance = _rclip_class()(model_instance=model_instance, database=database,
                              indexing_batch_size=32, exclude_dirs=None, enable_raw_support=False)
    return instance, model_instance, database


def _update_index(abs_image_dir):
    """Bring rclip's index of the folder up to date unless it provably already is. Caller holds _rclip_lock."""
    signature = _folder_signature(abs_image_dir)
    if _index_is_current(abs_image_dir, signature):
        _rclip_file_count[abs_image_dir] = signature
        return
    last = _rclip_file_count.get(abs_image_dir)
    last_count = last[0] if last else -1
    current_count = signature[0] if signature else 0
    print(f"[Index] Images count changed ({last_count} -> {current_count}). Running incremental index...")
    _rclip_file_count.pop(abs_image_dir, None)
    _save_index_state(abs_image_dir, None, None)
    instance, model_instance, database = _open_rclip(indexing=True)
    try:
        instance.ensure_index(abs_image_dir)
        model_instance.release_indexing_resources()
    finally:
        model_instance.close()
        database.close()
    vectors = _rclip_vector_count(abs_image_dir)
    _save_index_state(abs_image_dir, signature, vectors)
    if signature is not None:
        _rclip_file_count[abs_image_dir] = signature
    print(f"[Index] Indexing complete.")


def run_visual_search(image_dir, query_path, no_indexing=False):
    """Run rclip visual search in-process to get visual similarity scores.

    The index is refreshed only when the folder (entry count and newest mtime) or rclip's vector
    count for it differ from what the last finished index recorded in the catalog DB, so a
    restart no longer re-walks 100k pictures. Indexing commits as it goes and honours Stop.
    """
    # Split query_path by semicolon to support multiple reference images
    query_list = [q.strip() for q in query_path.split(";") if q.strip()]
    if not query_list:
        print("Error: No valid query paths provided.")
        return {}
        
    abs_image_dir = os.path.abspath(image_dir)
    abs_query_paths = [os.path.abspath(q) for q in query_list]
    visual_scores = {}
    print(f"Querying AI model for visual similarity scores (in-process) using {len(abs_query_paths)} reference images...")
    try:
        if not no_indexing and os.path.isdir(abs_image_dir):
            with _rclip_lock:
                try:
                    _update_index(abs_image_dir)
                except Exception as e:
                    if _is_stop(e):
                        raise
                    print(f"Warning: image indexing did not finish ({e}); searching what is indexed so far.")

        # Wrapped in _rclip_lock to prevent concurrent ONNX/CoreML model loading deadlocks.
        with _rclip_lock:
            rclip_instance, rclip_model, rclip_db = _open_rclip(indexing=False)
        try:
            for q_path in abs_query_paths:
                check_stop()
                print(f"Processing query image: {os.path.basename(q_path)}")
                search_results = rclip_instance.search(
                    query=q_path,
                    directory=abs_image_dir,
                    top_k=2000
                )
                for item in search_results:
                    filename = os.path.basename(item.filepath)
                    sku = os.path.splitext(filename)[0].strip().upper()
                    # Take the maximum similarity score across all query images
                    if sku not in visual_scores or item.score > visual_scores[sku]:
                        visual_scores[sku] = item.score
        finally:
            rclip_model.close()
            rclip_db.close()
        print(f"Successfully loaded {len(visual_scores)} visual similarity scores.")
    except Exception as e:
        if _is_stop(e):
            raise
        print(f"Warning: Could not run rclip visual search: {e}")
    return visual_scores

def run_semantic_text_search(df, reference_title, visual_scores, min_text_sim, strict=False,
                             min_strong_text=0.85, title_index=None):
    """Find products matching text criteria using semantic text similarity.

    `title_index` (from load_dataset_indexed) finds the keyword candidates without visiting every
    row; the rows and their order come out the same either way.
    """
    text_matches = []
    
    # Initialize CLIP model for semantic text similarity comparison
    print("Initializing CLIP text encoder for semantic text similarity...")
    try:
        with _rclip_lock:
            from rclip.model import Model as RClipModel
            clip_model = RClipModel()
            clip_model.ensure_downloaded()
            ref_emb = clip_model.compute_text_features([reference_title])[0]
        print("CLIP text encoder successfully initialized.\n")
    except Exception as e:
        print(f"Warning: Could not initialize CLIP text model: {e}")
        return text_matches

    print("Performing text similarity search across all products in Excel...")
    
    # Step 1: Pre-filter by quick keyword overlap and check if visual score is available to reduce candidates.
    # Every row is independent pure-Python work (regex/string comparisons, no shared state), so it is
    # split across a thread pool instead of run one row at a time.
    def score_row(item):
        idx, row = item
        title = str(row.get('Title', ''))
        if not title:
            return None
        if get_title_similarity(reference_title, title) > 0.0:
            return (idx, row, title)
        return None

    indexed = title_index.candidates(df, title_tokens(reference_title)) if title_index is not None else None
    if indexed is not None:
        # Rows are looked up at the end, only for the titles that are kept
        candidates = [(idx, None, title) for idx, title in indexed]
        check_stop()
    else:
        candidates = []
        with ThreadPoolExecutor(max_workers=MATCH_WORKERS) as executor:
            for result in executor.map(score_row, df.iterrows()):
                check_stop()
                if result is not None:
                    candidates.append(result)

    print(f"Found {len(candidates)} candidate products with keyword overlap. Computing semantic similarity...")
    
    # Pre-extract model codes from the query reference
    query_models = extract_models(clean_title(reference_title))

    # Step 2: Batch compute text embeddings for candidates
    # A title good enough to be kept on its own has to get through this stage first, because the
    # Keep-on-Title bar is not applied until save_and_display_results. Collecting at the text bar
    # alone meant the lower of the two sliders could never be reached: setting Keep-on-Title below
    # the text threshold did nothing, because those rows were already gone.
    text_bar = min_text_sim if min_text_sim > 0.0 else 0.70
    threshold = min(text_bar, min_strong_text)
    batch_size = 128

    # Stores list one product under many SKUs, so well under half the candidate titles are distinct.
    # Each distinct title is looked up once, and only the ones no earlier search has encoded go
    # through the text model.
    from rclip.model_download import MODEL_SUBDIR, TEXTUAL_ONNX
    from title_vector_store import TitleVectorStore
    unique_titles = list(dict.fromkeys(title for _, _, title in candidates))

    embeddings = {}
    try:
        cache = TitleVectorStore(f"{MODEL_SUBDIR}/{TEXTUAL_ONNX}", ref_emb.size)
    except Exception as e:
        print(f"Warning: Could not open the title cache, every title will be encoded: {e}")
        cache = None
    if cache is not None:
        try:
            embeddings = cache.get_many(unique_titles)
        except Exception as e:
            print(f"Warning: Could not read the title cache: {e}")
    to_encode = [title for title in unique_titles if title not in embeddings]
    print(f"{len(unique_titles)} distinct titles, {len(embeddings)} already encoded, "
          f"{len(to_encode)} to encode.")

    # Encoding the new titles is the longest silent stretch of a run on a large catalog, so it
    # reports as it goes. The carriage return keeps it redrawing one line, which is what the GUI
    # reads the counts off. One batch per task: onnxruntime lets several threads run the same text
    # session at once and releases the GIL while it does, so batches overlap instead of queueing.
    total_to_encode = len(to_encode)
    processed = 0
    batches = [to_encode[i:i + batch_size] for i in range(0, total_to_encode, batch_size)]
    executor = ThreadPoolExecutor(max_workers=TEXT_MATCH_WORKERS)
    try:
        futures = [executor.submit(clip_model.compute_text_features, batch) for batch in batches]
        # check_stop runs here, not in the workers, because the tab's stop event is thread-local and
        # they can't see it. The cache is written here too, since its connection belongs to this thread.
        for batch, future in zip(batches, futures):
            check_stop()
            try:
                batch_embs = future.result()
            except Exception as e:
                print(f"Warning: Error processing batch: {e}")
            else:
                embeddings.update(zip(batch, batch_embs))
                if cache is not None:
                    # Saved batch by batch so a stopped search still keeps what it encoded
                    try:
                        cache.put_many(batch, batch_embs)
                    except Exception as e:
                        print(f"\nWarning: Could not write the title cache: {e}")
                        cache.close()
                        cache = None
            processed += len(batch)
            pct = int(processed * 100 / total_to_encode)
            print(f"\rMatching titles: {processed}/{total_to_encode} ({pct}%)", end="", flush=True)
    finally:
        # On a stop, drop the batches not yet started rather than finishing the whole catalog
        executor.shutdown(wait=True, cancel_futures=True)
        if cache is not None:
            cache.close()

    if total_to_encode:
        print()

    # The verdict depends only on the title, so it is worked out once per distinct title
    kept_titles = {}
    for title, emb in embeddings.items():
        semantic_sim = float(ref_emb @ emb.T)

        # Check for exact model code overlap (Idea 1)
        candidate_models = extract_models(clean_title(title))
        model_match = bool(query_models.intersection(candidate_models))

        # Keep if semantic sim is high OR if it is an exact model match
        if semantic_sim >= threshold or model_match:
            # Apply strict model check if enabled
            if strict and is_generic_mismatch(reference_title, title):
                continue
            kept_titles[title] = semantic_sim

    # Built from the candidates so matches keep their order
    kept = [(idx, row, title) for idx, row, title in candidates if title in kept_titles]
    if indexed is not None and kept:
        # iterrows over the kept rows gives each the very row a full df.iterrows() would
        rows = dict(df.loc[[idx for idx, _, _ in kept]].iterrows())
        kept = [(idx, rows[idx], title) for idx, _, title in kept]
    for idx, row, title in kept:
        text_matches.append({"row": row, "idx": idx, "semantic_sim": kept_titles[title]})
    return text_matches

def find_visual_only_matches(df, visual_scores, covered_skus, min_score):
    """Products whose picture alone clears the bar, however their title reads.

    run_semantic_text_search never even considers a title with zero keyword overlap with the
    reference title -- so this is the only path back for a listing that is the same product,
    photographed the same way, but described in different words. Every row here carries no text
    similarity at all (never computed, not zero), which is what lets the report say so honestly
    instead of implying a title was checked and failed.
    """
    if not visual_scores:
        return []
    sku_to_idx = {}
    if _rows_are_objects(df):
        skus = df['SKU'].tolist() if 'SKU' in df.columns else [''] * len(df)
        for idx, value in zip(df.index.tolist(), skus):
            sku = str(value).strip().upper()
            if sku and sku not in sku_to_idx:
                sku_to_idx[sku] = idx
    else:
        for idx, row in df.iterrows():
            sku = str(row.get('SKU', '')).strip().upper()
            if sku and sku not in sku_to_idx:
                sku_to_idx[sku] = idx
    matches = []
    for sku, score in visual_scores.items():
        if score < min_score or sku in covered_skus:
            continue
        idx = sku_to_idx.get(sku)
        if idx is None:
            continue
        matches.append({"row": df.loc[idx], "idx": idx, "semantic_sim": None})
    return matches

def save_and_display_results(text_matches, visual_scores, output_path, top_limit, min_score=0.20,
                             strong_text=0.85, priority_keywords=None):
    """Format, sort, display, and save results to JSON.

    A result survives on either evidence, not on the picture alone. `min_score` is the visual bar,
    raised as the run goes on to sit near the best image match; `strong_text` is the bar a title has
    to clear to be kept in spite of its picture -- in spite of it, so a title at or above that bar
    is kept whatever the picture scored, including nothing at all. Holding it to the visual bar as
    well meant the only product a good title ever rescued was one whose image had never downloaded.

    `priority_keywords` are the words the user highlighted (Ctrl+B) in the query title -- a result
    whose title contains one is pinned ahead of every non-matching result, before the Top N cutoff
    below, so a highlighted match can never be truncated away in favor of an unhighlighted one.
    """
    priority_keywords = [str(k).strip().lower() for k in (priority_keywords or []) if str(k).strip()]
    results_data = []
    if text_matches:
        print(f"Evaluating {len(text_matches)} candidate products (matched by title or by picture). Attaching visual similarity scores...")
        for match in text_matches:
            row = match["row"]
            idx = match["idx"]
            semantic_sim = match["semantic_sim"]
            sku = str(row.get('SKU', '')).strip()
            sku_lookup = sku.upper()
            
            # Look up score from rclip visual search
            score = visual_scores.get(sku_lookup, None)
            visual_ok = score is not None and score >= min_score
            # Kept on the strength of the title even though the image disagrees, or is missing
            # entirely. This is what makes the "very high text match" tier below reachable: before,
            # those rows were dropped here, and the tier could never fire.
            text_ok = semantic_sim is not None and semantic_sim >= strong_text
            if not (visual_ok or text_ok):
                continue
            
            price = row.get('Price', '')
            source_file = row.get('Source File', 'Unknown')
            
            psku = row.get('psku', '')
            if pd.isna(psku):
                psku = ''
            else:
                psku = str(psku).strip()
            
            results_data.append({
                "Source File": str(source_file),
                "Row": int(idx + 1),
                "SKU": str(sku),
                "psku": psku,
                "Title": str(row.get('Title', '')),
                "Price": float(price) if not pd.isna(price) else None,
                "AI Score": score,
                "Text Similarity": semantic_sim,
                # Said plainly so a title match is never read as a picture match. "title" here means
                # the image disagreed or there was no image to compare.
                "Matched On": "image" if visual_ok else "title",
                "Image Filename": f"{sku}.jpg"
            })
            
        # Calculate max values to normalize both scores to [0, 1]
        # Defaulted rather than taken straight from max(), because every surviving row can now be a
        # title match with no image score at all, and max() of nothing raises.
        scored = [x["AI Score"] for x in results_data if x["AI Score"] is not None]
        max_visual = max(scored) if scored else 1.0
        if max_visual <= 0:
            max_visual = 1.0

        texts = [x["Text Similarity"] for x in results_data if x["Text Similarity"] is not None]
        max_text = max(texts) if texts else 1.0
        if max_text <= 0:
            max_text = 1.0
 
        for item in results_data:
            vis = item["AI Score"] if item["AI Score"] is not None else 0.0
            norm_vis = vis / max_visual
            text_sim = item["Text Similarity"] if item["Text Similarity"] is not None else 0.0
            
            combined = (norm_vis * 0.5) + (text_sim * 0.5)
            item["Combined Score"] = combined
            
            # Tier-based sorting:
            # Tier 3 (very high visual match, score >= 1.2): sorted strictly by visual score
            # Tier 2 (very high text match, score >= 0.9): sorted strictly by text similarity
            # Tier 1 (otherwise): sorted by combined visual + text similarity
            if vis >= 1.2:
                tier = (3, vis)
            elif text_sim >= 0.9:
                tier = (2, text_sim)
            else:
                tier = (1, combined)
            title_lower = item["Title"].lower()
            is_priority = any(kw in title_lower for kw in priority_keywords)
            item["Sort Key"] = (int(is_priority),) + tier
 
        # Sort results descending by Sort Key tuple
        results_data.sort(key=lambda x: x["Sort Key"], reverse=True)
        
        # Limit the results saved to the user's requested top_limit -- said out loud, because a
        # cap that trims silently reads identically to a run that only ever found this many.
        total_kept = len(results_data)
        if total_kept > top_limit:
            print(f"{total_kept} products passed the thresholds; showing the top {top_limit} "
                  f"(raise the Top N setting to see the rest).")
        results_data = results_data[:top_limit]
        
        # Assign rank based on final sorted order
        for rank_idx, item in enumerate(results_data, 1):
            item["Rank"] = rank_idx

    # Print top results
    print("\nAI Search Results (Text Search First, Then Visual Rank):")
    print("-" * 80)
    for item in results_data[:top_limit]:
        ai_score_str = f"{item['AI Score']:.3f}" if item['AI Score'] is not None else "None"
        text_sim_str = f"{item['Text Similarity']:.3f}" if item['Text Similarity'] is not None else "None"
        print(f"Rank: {item['Rank']} | Source: {item['Source File']} | Row: {item['Row']} | SKU: {item['SKU']} | Price: {item['Price']} | AI Score: {ai_score_str} | Text Sim: {text_sim_str}")
        print(f"Title: {item['Title']}")
        print(f"Image: {item['Source File']} (SKU: {item['SKU']})")
        print("-" * 80)

    if not results_data:
        print("No duplicate listings matching the criteria were found.")

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(results_data, f, indent=4, ensure_ascii=False)
    print(f"Saved AI search results to {output_path}")

def setup_global_input_data_dir():
    """
    Ensure the global input_data folder exists in the persistent user directory,
    and create a local symbolic link/junction in the current workspace so the
    relative 'input_data' paths resolve to the global folder transparently.
    """
    import platform
    sys_name = platform.system()
    
    home = os.path.expanduser("~")
    if sys_name == "Darwin":
        target_path = os.path.join(home, "Library", "Application Support", "DuplicateFinder", "input_data")
    elif sys_name == "Windows":
        target_path = os.path.join(home, "AppData", "Roaming", "DuplicateFinder", "input_data")
    else:
        target_path = os.path.join(home, ".local", "share", "DuplicateFinder", "input_data")

    target_path = os.path.abspath(target_path)
    os.makedirs(target_path, exist_ok=True)

    local_path = os.path.abspath("input_data")
    
    # If local_path exists but is a real directory (not a symlink/junction), migrate its contents
    if os.path.exists(local_path) and not os.path.islink(local_path):
        # On Windows, junctions can also report isdir=True, but they are links
        # Check if it's not a junction by trying to readlink or check attribute
        is_real_dir = True
        if sys_name == "Windows":
            try:
                # If it can readlink or is a junction, it's not a real directory
                os.readlink(local_path)
                is_real_dir = False
            except Exception:
                pass
        
        if is_real_dir:
            import shutil
            try:
                for item in os.listdir(local_path):
                    s = os.path.join(local_path, item)
                    d = os.path.join(target_path, item)
                    if os.path.isdir(s):
                        if not os.path.exists(d):
                            shutil.copytree(s, d)
                    else:
                        if not os.path.exists(d):
                            shutil.copy(s, d)
                shutil.rmtree(local_path)
            except Exception as e:
                print(f"Warning: Could not migrate local input_data to global: {e}")

    # Recreate the symlink/junction if it doesn't exist
    if not os.path.exists(local_path) and not os.path.islink(local_path):
        try:
            if sys_name == "Windows":
                import subprocess
                subprocess.run(f'mklink /J "{local_path}" "{target_path}"', shell=True, check=True)
            else:
                os.symlink(target_path, local_path)
            print(f"Created local symlink 'input_data' -> '{target_path}'")
        except Exception as e:
            print(f"Warning: Could not create local link to input_data folder: {e}.")

    return target_path

def setup_global_image_dir(image_dir):
    """
    Resolve image_dir (custom or default 'downloaded_images') and ensure a local 
    symlink 'downloaded_images' is created pointing to it for relative HTML references.
    """
    import platform
    sys_name = platform.system()
    
    if image_dir == "downloaded_images":
        home = os.path.expanduser("~")
        if sys_name == "Darwin":
            target_path = os.path.join(home, "Library", "Application Support", "DuplicateFinder", "downloaded_images")
        elif sys_name == "Windows":
            target_path = os.path.join(home, "AppData", "Roaming", "DuplicateFinder", "downloaded_images")
        else:
            target_path = os.path.join(home, ".local", "share", "DuplicateFinder", "downloaded_images")
    else:
        target_path = image_dir

    target_path = os.path.abspath(target_path)
    os.makedirs(target_path, exist_ok=True)

    local_path = os.path.abspath("downloaded_images")
    # If a symlink or file exists but points to a different location, recreate it
    # to match the newly selected user directory
    is_link = os.path.islink(local_path) or (sys_name == "Windows" and os.path.exists(local_path) and os.path.isdir(local_path) and not os.listdir(local_path))
    
    if os.path.exists(local_path) or os.path.islink(local_path):
        # If it's a symlink/junction, check if it points to target_path; if not, recreate it
        try:
            current_target = os.readlink(local_path) if os.path.islink(local_path) else ""
            if current_target and os.path.abspath(current_target) != target_path:
                if os.path.islink(local_path):
                    os.remove(local_path)
                else:
                    try:
                        os.rmdir(local_path)
                    except Exception:
                        os.remove(local_path)
        except Exception:
            pass

    if not os.path.exists(local_path) and not os.path.islink(local_path):
        try:
            if sys_name == "Windows":
                import subprocess
                subprocess.run(f'mklink /J "{local_path}" "{target_path}"', shell=True, check=True)
            else:
                os.symlink(target_path, local_path)
            print(f"Created local symlink 'downloaded_images' -> '{target_path}'")
        except Exception as e:
            print(f"Warning: Could not create local link to images folder: {e}. Indexing target path directly.")

    return target_path

def main(args=None, stop_event=None):
    """
    Run the AI duplicate search.

    Parameters
    ----------
    args : argparse.Namespace or None
        Pre-built argument namespace. When None (default / CLI usage), arguments
        are parsed from sys.argv as usual.
    stop_event : threading.Event or None
        Per-tab stop signal for GUI usage. When set the search aborts at the
        next check_stop() call without touching the global stop_requested flag.
        Pass None for CLI usage.
    """
    # Install per-thread stop event so check_stop() picks it up without globals
    _stop_event_local.event = stop_event
    setup_global_input_data_dir()

    try:
        if args is None:
            # CLI path — parse from sys.argv as before
            parser = argparse.ArgumentParser(description="AI-powered duplicate listing search using rclip (CLIP).")
            parser.add_argument("--query", default="/Users/mabdurrafey/Downloads/61ec5bb0-fe2c-4245-89fd-2f3e341e1e46.avif;/Users/mabdurrafey/Downloads/04429c3c-f63c-47a5-b709-06892999e7da.avif", help="Path to local query image")
            import glob
            excel_files = sorted(glob.glob("input_data/*.xlsx"))
            default_input = excel_files[0] if excel_files else "input_data"
            parser.add_argument("--input", default=default_input, help=f"Dataset Excel path or directory containing Excel files (default: {default_input})")
            parser.add_argument("--output", default="temp/search_results_ai.json", help="Path to save search results JSON")
            parser.add_argument("--top", type=int, default=500, help="Number of top visual matches to retrieve (default: 500)")
            parser.add_argument("--min-score", type=float, default=0.20, help="Minimum AI similarity score threshold (default: 0.20)")
            parser.add_argument("--min-text-sim", type=float, default=0.70, help="Minimum semantic text similarity score (default: 0.70, set to 0.0 to disable)")
            parser.add_argument("--min-strong-text", type=float, default=0.85, help="Text similarity at which a product is kept despite a poor or missing image (default: 0.85, set to 1.1 to disable)")
            parser.add_argument("--strict", action="store_true", help="Enforce strict alphanumeric model code matching")
            parser.add_argument("--query-title", default="", help="Pasted title text to use as reference baseline for semantic text similarity")
            parser.add_argument("--image-dir", default="downloaded_images", help="Directory where database images are stored")
            parser.add_argument("--workers", type=int, default=10, help="Number of download workers")
            parser.add_argument("--no-indexing", action="store_true", help="Skip checking/indexing images in the target directory")
            parser.add_argument("--min-price", type=float, default=None, help="Minimum product price threshold")
            parser.add_argument("--max-price", type=float, default=None, help="Maximum product price threshold")
            parser.add_argument("--priority-keywords", default="", help="Comma-separated keywords (highlighted in the query title) that pin a match to the top, ahead of the Top N cutoff")
            args = parser.parse_args()

        # The GUI builds a Namespace directly and may pass a list; the CLI parser always hands
        # back a comma-separated string. Normalize both into the list save_and_display_results wants.
        raw_priority_keywords = getattr(args, "priority_keywords", "") or ""
        if isinstance(raw_priority_keywords, str):
            priority_keywords = [k.strip() for k in raw_priority_keywords.split(",") if k.strip()]
        else:
            priority_keywords = [str(k).strip() for k in raw_priority_keywords if str(k).strip()]

        # Ensure output parent directory exists if a path is specified
        output_dir = os.path.dirname(args.output)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

        # Verify each query path exists individually
        query_paths = [q.strip() for q in args.query.split(";") if q.strip()]
        for q in query_paths:
            if not os.path.exists(q):
                print(f"Error: Query image '{q}' not found.")
                return

        # Check if input path(s) exist, or try falling back to input_data folder.
        # args.input may be a single path or multiple ';'-separated paths (multi-file selection).
        raw_input_paths = [p.strip() for p in str(args.input).split(";") if p.strip()]
        if not raw_input_paths:
            print("Error: No input dataset path provided.")
            return

        resolved_input_paths = []
        for p in raw_input_paths:
            if os.path.exists(p):
                resolved_input_paths.append(p)
            else:
                fallback_path = os.path.join("input_data", p)
                if os.path.exists(fallback_path):
                    resolved_input_paths.append(fallback_path)
                else:
                    print(f"Error: Input dataset path '{p}' not found.")
                    return
        input_path = resolved_input_paths

        args.image_dir = setup_global_image_dir(args.image_dir)

        # 1. Load spreadsheet database
        try:
            df, title_index = load_dataset_indexed(input_path)
        except Exception as e:
            print(f"Error loading dataset: {e}")
            return
        print(f"Loaded {len(df)} products from the database.")

        # Filter by Price Range if specified
        if (args.min_price is not None) or (args.max_price is not None):
            if 'Price' in df.columns:
                df['Price'] = pd.to_numeric(df['Price'], errors='coerce')
                original_len = len(df)
                if args.min_price is not None:
                    print(f"Filtering database: Min Price >= {args.min_price} AED")
                    df = df[df['Price'] >= args.min_price]
                if args.max_price is not None:
                    print(f"Filtering database: Max Price <= {args.max_price} AED")
                    df = df[df['Price'] <= args.max_price]
                print(f"Price filtering complete: kept {len(df)} of {original_len} products.")
            else:
                print("Warning: 'Price' column not found in dataset. Price filtering skipped.")

        # 2. Resolve query reference title for similarity checks
        reference_title = resolve_reference_title(df, args.query, args.query_title)

        check_stop()

        # 4. Download every product's image, not just the keyword-overlapping ones. A picture
        # match is the only way back for a product whose title shares no word with the reference
        # title (see find_visual_only_matches below) -- pre-filtering the download queue by keyword
        # would make that recovery only work when the image happened to already be cached from an
        # earlier, differently-worded search. download_missing_images skips whatever is already on
        # disk, so a repeat run against the same store only ever pays for what actually changed.
        print(f"Downloading images for all {len(df)} products in the database...")

        check_stop()

        # 5. Automatically download missing images
        download_missing_images(df, image_dir=args.image_dir, max_workers=args.workers)

        check_stop()

        # 6. Run visual similarity search (in-process rclip)
        visual_scores = run_visual_search(args.image_dir, args.query, no_indexing=args.no_indexing)

        check_stop()

        # 7. Fallback to Rank 1 match if reference title wasn't found earlier
        if not reference_title and visual_scores:
            reference_title = resolve_reference_title(df, args.query, args.query_title, visual_scores)

        check_stop()

        # 8. Run semantic text search
        strong_text = getattr(args, "min_strong_text", 0.85)
        text_matches = []
        if reference_title:
            text_matches = run_semantic_text_search(df, reference_title, visual_scores,
                                                    args.min_text_sim, args.strict,
                                                    min_strong_text=strong_text, title_index=title_index)

        check_stop()

        # 9. Format, sort, save and print results
        max_score = max(visual_scores.values()) if visual_scores else 0.0
        dynamic_min_score = max(args.min_score, max_score - 0.45)

        # Recover products whose picture matches well enough on its own, however their title reads
        # -- the other half of "never miss a product": step 8 never even considers a title with no
        # keyword overlap with the reference title, so this is the only way one of those comes
        # back. Uses the same visual bar as everything else in the report, so "matched by image"
        # means the same thing everywhere it appears.
        covered_skus = {str(m["row"].get("SKU", "")).strip().upper() for m in text_matches}
        visual_only_matches = find_visual_only_matches(df, visual_scores, covered_skus, dynamic_min_score)
        if visual_only_matches:
            print(f"Found {len(visual_only_matches)} more products by picture alone -- "
                  f"their titles never shared a keyword with '{reference_title}'.")

        print(f"Top visual score: {max_score:.3f} | Dynamic visual threshold: {dynamic_min_score:.3f} "
              f"| Strong-title threshold: {strong_text:.2f}")
        save_and_display_results(text_matches + visual_only_matches, visual_scores, args.output, args.top,
                                 dynamic_min_score, strong_text=strong_text, priority_keywords=priority_keywords)

    finally:
        # Always clear the per-thread stop event when done
        _stop_event_local.event = None

if __name__ == "__main__":
    main()
