"""The local catalog database: one SQLite file that remembers what was expensive to work out.

The Excel files stay the source of truth. This keeps, per imported file, the frame that parsing it
produced and the title-token index built from it, and re-imports a file only when its size, its
modification time or the parser changes. It also holds the title vectors and the state of the image
index, so none of that is redone after an app restart.

Nothing here is required for a search to work: every failure falls back to parsing the Excel file,
exactly as the app did before this existed.

Frames are stored without any Python object serialization. Each column is either raw array bytes
(numbers, booleans, dates) or a JSON list (text), and object columns carry a type tag per value. A
frame is stored only after decoding it again gives back the same columns, dtypes, values and value
types; anything this encoding can't reproduce exactly is simply parsed every time.
"""
import collections
import datetime
import json
import os
import sqlite3
import threading
import time

import numpy as np
import pandas as pd

SCHEMA_VERSION = 1
PAGE_SIZE = 16384
FILE_NAME = "catalog.sqlite"
MEMORY_CACHE_MAX_ROWS = 300_000

_path_override = None
_generation = 0
_local = threading.local()
_state_lock = threading.Lock()
_disabled_paths = set()
_warned = set()
_force_disabled_for_tests = False


class _NewerSchema(Exception):
    pass


class _Uncacheable(Exception):
    pass


def app_dir():
    import platform
    sys_name = platform.system()
    home = os.path.expanduser("~")
    if sys_name == "Darwin":
        return os.path.join(home, "Library", "Application Support", "DuplicateFinder")
    if sys_name == "Windows":
        return os.path.join(home, "AppData", "Roaming", "DuplicateFinder")
    return os.path.join(home, ".local", "share", "DuplicateFinder")


def db_path():
    return _path_override or os.path.join(app_dir(), FILE_NAME)


def set_path(path):
    """Point the module at another file (tests). Every thread reopens on its next call."""
    global _path_override, _generation
    with _state_lock:
        _path_override = path
        _generation += 1
        _disabled_paths.clear()
    clear_memory_cache()
    _close_local()


def _close_local():
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
    _local.conn = None


def _warn_once(key, message):
    if key not in _warned:
        _warned.add(key)
        print(message)


_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS sources (
        path TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, tag TEXT NOT NULL,
        rows INTEGER NOT NULL, frame BLOB, tokens BLOB, messages TEXT NOT NULL, imported_at REAL NOT NULL)""",
    "CREATE TABLE IF NOT EXISTS title_vectors (id INTEGER PRIMARY KEY, check_bytes BLOB NOT NULL, vec BLOB NOT NULL)",
    """CREATE TABLE IF NOT EXISTS index_state (
        folder TEXT PRIMARY KEY, file_count INTEGER NOT NULL, dir_mtime_ns INTEGER NOT NULL,
        vector_count INTEGER NOT NULL, indexed_at REAL NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS image_failures (
        sku TEXT PRIMARY KEY, url TEXT NOT NULL, failed_at REAL NOT NULL, error TEXT)""",
    """CREATE TABLE IF NOT EXISTS searches (
        id INTEGER PRIMARY KEY AUTOINCREMENT, created_at REAL NOT NULL, query_images TEXT, query_title TEXT,
        settings TEXT, sources TEXT, result_count INTEGER, json_path TEXT, report_path TEXT, results TEXT)""",
]


def _open(path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise _NewerSchema(version)
        # page_size only takes effect before the first table exists, so it goes first
        conn.execute(f"PRAGMA page_size={PAGE_SIZE}")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=30000")
        if version < SCHEMA_VERSION:
            for statement in _SCHEMA:
                conn.execute(statement)
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            conn.commit()
        conn.execute("SELECT COUNT(*) FROM sources").fetchone()
        return conn
    except BaseException:
        conn.close()
        raise


def _move_aside(path):
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = f"{path}.corrupt-{stamp}"
    os.replace(path, target)
    for suffix in ("-wal", "-shm"):
        if os.path.exists(path + suffix):
            try:
                os.replace(path + suffix, target + suffix)
            except OSError:
                pass
    return target


def connect():
    """This thread's connection, or None when the database can't or mustn't be used."""
    if _force_disabled_for_tests:
        return None
    path = db_path()
    if path in _disabled_paths:
        return None
    conn = getattr(_local, "conn", None)
    if conn is not None and getattr(_local, "generation", None) == _generation:
        return conn
    _close_local()
    try:
        conn = _open(path)
    except _NewerSchema:
        _disabled_paths.add(path)
        _warn_once(("newer", path), "Note: the catalog database was written by a newer version of the app; "
                                    "reading the Excel files directly.")
        return None
    except sqlite3.DatabaseError as error:
        try:
            moved = _move_aside(path)
            print(f"Warning: the catalog database was damaged ({error}); moved it to "
                  f"'{os.path.basename(moved)}' and started a new one.")
            conn = _open(path)
        except Exception as second:
            _warn_once(("broken", path), f"Warning: catalog database unavailable, reading the Excel files "
                                         f"directly: {second}")
            return None
    except Exception as error:
        _warn_once(("open", path), f"Warning: catalog database unavailable, reading the Excel files "
                                   f"directly: {error}")
        return None
    _local.conn = conn
    _local.generation = _generation
    return conn


# ---------------------------------------------------------------------------------------------
# Frame encoding


def _is_nan(v):
    return isinstance(v, float) and v != v


def _encode_object(values):
    tags = []
    out = []
    for v in values:
        t = type(v)
        if t is str:
            tags.append("s"); out.append(v)
        elif t is bool:
            tags.append("b"); out.append(int(v))
        elif t is int:
            tags.append("i"); out.append(v)
        elif t is float:
            tags.append("f"); out.append(v.hex())
        elif v is None:
            tags.append("n"); out.append(0)
        elif t is datetime.datetime and v.tzinfo is None:
            tags.append("d"); out.append(v.isoformat())
        elif t is datetime.time and v.tzinfo is None:
            tags.append("T"); out.append(v.isoformat())
        elif t is np.float64:
            tags.append("F"); out.append(float(v).hex())
        elif t is np.int64:
            tags.append("I"); out.append(int(v))
        elif t is np.bool_:
            tags.append("B"); out.append(int(v))
        else:
            raise _Uncacheable(f"value of type {t.__name__}")
    return "".join(tags), out


_OBJECT_DECODERS = {
    "s": lambda x: x,
    "b": lambda x: bool(x),
    "i": lambda x: x,
    "f": float.fromhex,
    "n": lambda x: None,
    "d": datetime.datetime.fromisoformat,
    "T": datetime.time.fromisoformat,
    "F": lambda x: np.float64(float.fromhex(x)),
    "I": lambda x: np.int64(x),
    "B": lambda x: np.bool_(bool(x)),
}


def _encode(df):
    if type(df.index) is not pd.RangeIndex or df.index.start != 0 or df.index.step != 1 or df.index.name is not None:
        raise _Uncacheable("index")
    names = list(df.columns)
    if type(df.columns) is not pd.Index or df.columns.name is not None:
        raise _Uncacheable("columns")
    if any(type(n) is not str for n in names) or len(set(names)) != len(names):
        raise _Uncacheable("column names")
    header = {"rows": len(df), "columns": names, "columns_dtype": str(df.columns.dtype), "cols": []}
    chunks = []
    offset = 0
    for i in range(len(names)):
        s = df.iloc[:, i]
        dt = s.dtype
        col = {}
        if isinstance(dt, pd.StringDtype):
            if dt.na_value is pd.NA:
                na = "NA"
            elif _is_nan(dt.na_value):
                na = "nan"
            else:
                raise _Uncacheable("string na")
            vals = []
            for v in s.tolist():
                if type(v) is str:
                    vals.append(v)
                elif v is pd.NA or _is_nan(v):
                    vals.append(None)
                else:
                    raise _Uncacheable("string value")
            data = json.dumps(vals, ensure_ascii=False).encode("utf-8")
            col = {"kind": "str", "storage": dt.storage, "na": na}
        elif isinstance(dt, np.dtype) and dt == np.dtype(object):
            tags, vals = _encode_object(s.tolist())
            data = json.dumps([tags, vals], ensure_ascii=False).encode("utf-8")
            col = {"kind": "obj"}
        elif isinstance(dt, np.dtype) and dt.kind in "biuf":
            data = np.ascontiguousarray(s.to_numpy()).tobytes()
            col = {"kind": "raw", "dtype": dt.str}
        elif isinstance(dt, np.dtype) and dt.kind in "Mm":
            data = np.ascontiguousarray(s.to_numpy()).view("i8").tobytes()
            col = {"kind": "time", "dtype": dt.str}
        else:
            raise _Uncacheable(f"dtype {dt}")
        col["offset"] = offset
        col["length"] = len(data)
        offset += len(data)
        chunks.append(data)
        header["cols"].append(col)
    head = json.dumps(header, ensure_ascii=False).encode("utf-8")
    return len(head).to_bytes(8, "big") + head + b"".join(chunks)


def decode_frame(blob):
    blob = bytes(blob)
    head_len = int.from_bytes(blob[:8], "big")
    header = json.loads(blob[8:8 + head_len].decode("utf-8"))
    base = 8 + head_len
    n = header["rows"]
    index = pd.RangeIndex(n)
    series = []
    for col in header["cols"]:
        data = blob[base + col["offset"]:base + col["offset"] + col["length"]]
        kind = col["kind"]
        if kind == "str":
            dtype = pd.StringDtype(storage=col["storage"], na_value=pd.NA if col["na"] == "NA" else np.nan)
            series.append(pd.Series(json.loads(data.decode("utf-8")), index=index, dtype=dtype))
        elif kind == "obj":
            tags, vals = json.loads(data.decode("utf-8"))
            arr = np.empty(n, dtype=object)
            for j, (tag, v) in enumerate(zip(tags, vals)):
                arr[j] = _OBJECT_DECODERS[tag](v)
            series.append(pd.Series(arr, index=index, dtype=object, copy=False))
        elif kind == "raw":
            arr = np.frombuffer(data, dtype=np.dtype(col["dtype"])).copy()
            series.append(pd.Series(arr, index=index, copy=False))
        elif kind == "time":
            arr = np.frombuffer(data, dtype="i8").copy().view(np.dtype(col["dtype"]))
            series.append(pd.Series(arr, index=index, copy=False))
        else:
            raise ValueError(f"unknown column kind {kind}")
    if series:
        df = pd.DataFrame({i: s for i, s in enumerate(series)}, index=index)
    else:
        df = pd.DataFrame(index=index)
    df.columns = pd.Index(header["columns"], dtype=header["columns_dtype"])
    return df


def _identical(a, b):
    if list(a.columns) != list(b.columns) or a.columns.dtype != b.columns.dtype:
        return False
    if list(a.dtypes) != list(b.dtypes) or not a.index.equals(b.index) or type(a.index) is not type(b.index):
        return False
    if not a.equals(b):
        return False
    for i in range(a.shape[1]):
        sa, sb = a.iloc[:, i], b.iloc[:, i]
        if sa.dtype == object or isinstance(sa.dtype, pd.StringDtype):
            if [type(v) for v in sa.tolist()] != [type(v) for v in sb.tolist()]:
                return False
    return True


def encode_frame(df):
    """Bytes that decode_frame turns back into exactly this frame, or None if that can't be promised."""
    try:
        blob = _encode(df)
        back = decode_frame(blob)
    except Exception:
        return None
    if not _identical(df, back):
        return None
    return blob


# ---------------------------------------------------------------------------------------------
# Sources


SourceEntry = collections.namedtuple("SourceEntry", "frame tokens from_cache")


class Messages(list):
    """The lines a parse printed. A parse that took a fallback path sets `cacheable` to False, so a
    frame read around a passing error (a file briefly locked, say) is never remembered."""
    cacheable = True

_memory = collections.OrderedDict()
_memory_lock = threading.Lock()
_memory_rows = 0


def clear_memory_cache():
    global _memory_rows
    with _memory_lock:
        _memory.clear()
        _memory_rows = 0


def _memory_get(key):
    with _memory_lock:
        hit = _memory.get(key)
        if hit is not None:
            _memory.move_to_end(key)
        return hit


def _memory_put(key, frame, tokens, messages):
    global _memory_rows
    with _memory_lock:
        if key in _memory:
            _memory_rows -= len(_memory.pop(key)[0])
        _memory[key] = (frame, tokens, messages)
        _memory_rows += len(frame)
        while _memory_rows > MEMORY_CACHE_MAX_ROWS and len(_memory) > 1:
            _, (old, _, _) = _memory.popitem(last=False)
            _memory_rows -= len(old)


def build_tokens(df, tokenize):
    """{token: [row positions]} over str() of each Title value, or None if there is no Title column."""
    if "Title" not in df.columns:
        return None
    tokens = {}
    for pos, value in enumerate(df["Title"].tolist()):
        for tok in tokenize(str(value)):
            tokens.setdefault(tok, []).append(pos)
    return tokens


def _replay(messages, show):
    for message in messages:
        if show is None or show(message):
            print(message)


def load_source(path, parse, tokenize, tag, show=None):
    """The frame `parse(path, messages)` would return for this file, from the cache when it is current.

    `parse` prints as it goes and appends each printed line to `messages`; a cache hit prints the
    same lines again, so the log reads the same either way. The frame returned is always the
    caller's own copy. `show`, when given, picks which of the stored lines a cache hit prints, for a
    caller whose `parse` prints only some of them.
    """
    try:
        st = os.stat(path)
    except OSError:
        return SourceEntry(parse(path, Messages()), None, False)
    key_path = os.path.normcase(os.path.abspath(path))
    key = (key_path, st.st_size, st.st_mtime_ns, tag)

    hit = _memory_get(key)
    if hit is not None:
        frame, tokens, messages = hit
        _replay(messages, show)
        return SourceEntry(frame.copy(deep=True), tokens, True)

    conn = connect()
    if conn is not None:
        try:
            row = conn.execute("SELECT size, mtime_ns, tag, frame, tokens, messages FROM sources WHERE path=?",
                               (key_path,)).fetchone()
            if row is not None and (row[0], row[1], row[2]) == (st.st_size, st.st_mtime_ns, tag) and row[3] is not None:
                frame = decode_frame(row[3])
                tokens = json.loads(row[4]) if row[4] is not None else None
                messages = json.loads(row[5])
                _memory_put(key, frame, tokens, messages)
                _replay(messages, show)
                return SourceEntry(frame.copy(deep=True), tokens, True)
            known_uncacheable = row is not None and (row[0], row[1], row[2]) == (st.st_size, st.st_mtime_ns, tag)
        except Exception as error:
            _warn_once(("read", key_path), f"Warning: catalog database read failed, reading "
                                           f"'{os.path.basename(path)}' directly: {error}")
            conn = None
            known_uncacheable = False
    else:
        known_uncacheable = False

    messages = Messages()
    df = parse(path, messages)
    if conn is None or known_uncacheable or not messages.cacheable:
        return SourceEntry(df, None, False)

    try:
        after = os.stat(path)
    except OSError:
        return SourceEntry(df, None, False)
    if (after.st_size, after.st_mtime_ns) != (st.st_size, st.st_mtime_ns):
        # Rewritten while it was being read: don't remember a frame that may be half of each
        return SourceEntry(df, None, False)

    blob = encode_frame(df)
    tokens = build_tokens(df, tokenize) if blob is not None else None
    try:
        conn.execute("INSERT OR REPLACE INTO sources (path, size, mtime_ns, tag, rows, frame, tokens, messages, "
                     "imported_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (key_path, st.st_size, st.st_mtime_ns, tag, len(df), blob,
                      json.dumps(tokens, ensure_ascii=False).encode("utf-8") if tokens is not None else None,
                      json.dumps(messages, ensure_ascii=False), time.time()))
        conn.commit()
    except Exception as error:
        _warn_once(("write", key_path), f"Warning: could not save '{os.path.basename(path)}' to the catalog "
                                        f"database: {error}")
        try:
            conn.rollback()
        except Exception:
            pass
    if blob is not None:
        _memory_put(key, df.copy(deep=True), tokens, messages)
    return SourceEntry(df, tokens, False)


# ---------------------------------------------------------------------------------------------
# Image index state


def index_state_get(folder):
    conn = connect()
    if conn is None:
        return None
    try:
        row = conn.execute("SELECT file_count, dir_mtime_ns, vector_count FROM index_state WHERE folder=?",
                           (os.path.normcase(os.path.abspath(folder)),)).fetchone()
    except Exception:
        return None
    return tuple(row) if row else None


def index_state_set(folder, file_count, dir_mtime_ns, vector_count):
    conn = connect()
    if conn is None:
        return
    try:
        conn.execute("INSERT OR REPLACE INTO index_state (folder, file_count, dir_mtime_ns, vector_count, indexed_at) "
                     "VALUES (?, ?, ?, ?, ?)",
                     (os.path.normcase(os.path.abspath(folder)), file_count, dir_mtime_ns, vector_count, time.time()))
        conn.commit()
    except Exception as error:
        print(f"Warning: could not save the image index state: {error}")


def index_state_clear(folder):
    conn = connect()
    if conn is None:
        return
    try:
        conn.execute("DELETE FROM index_state WHERE folder=?", (os.path.normcase(os.path.abspath(folder)),))
        conn.commit()
    except Exception:
        pass
