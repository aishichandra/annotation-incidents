"""The documents being coded: zotero_docs.csv, loaded into a DataFrame.

`df` is reassigned by refresh_docs() whenever the CSV changes on disk, so other
modules must reach it as `doc_source.df` - never `from doc_source import df`,
which would freeze a stale copy.
"""
from urllib.parse import urlparse

import pandas as pd

from config import DATA_CSV

# The document list to code comes entirely from zotero_docs.csv. If it's missing
# (import not run yet), start empty rather than crash — the UI just shows no docs.
COLUMNS = ["zotero_key", "title", "url", "date", "markdown", "snapshot"]
df = pd.DataFrame(columns=COLUMNS + ["doc_key"])
_docs_mtime = False            # False = never loaded; None is a valid "no file yet"


def refresh_docs() -> None:
    """Re-read zotero_docs.csv if it changed on disk.

    Runs before every request, so re-importing from Zotero shows up in a running
    app — the list used to be read once at import, which left a newly added
    article invisible until the process restarted. Comparing mtime keeps the
    steady state to one stat() per request."""
    global df, _docs_mtime
    mtime = DATA_CSV.stat().st_mtime if DATA_CSV.exists() else None
    if mtime == _docs_mtime:
        return
    _docs_mtime = mtime
    fresh = pd.read_csv(DATA_CSV) if mtime else pd.DataFrame(columns=COLUMNS)
    fresh["doc_key"] = fresh["zotero_key"].astype(str) if len(fresh) else []
    df = fresh
    print(f"[docs] loaded {len(df)} document(s) from {DATA_CSV.name}")


def cell(i, col, default=""):
    """One document's value for a column, as a string.

    An empty cell reads back from the CSV as NaN, which `str()` would turn into
    the word "nan" — visible in the UI as an article dated "nan". A missing
    value and a missing column mean the same thing here, so both give the
    default."""
    if col not in df.columns:
        return default
    v = df[col].iloc[i]
    return default if pd.isna(v) else str(v)


def domain(url):
    """The site a URL belongs to — "nytimes.com" — or "" if it has no host.

    Derived at read time rather than stored: it is already contained in the URL,
    and a stored second copy could only ever disagree with it. `www.` is dropped
    because it distinguishes a host, not a publisher."""
    host = urlparse(str(url or "").strip()).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _norm(s):
    return "".join(ch.lower() for ch in s if ch.isalnum())


def markdown_no_title(i):
    """Drop a leading H1 that just repeats the doc title (shown separately)."""
    md, title = cell(i, "markdown"), cell(i, "title")
    lines = md.lstrip().splitlines()
    if lines and lines[0].startswith("# ") and _norm(lines[0][2:]) == _norm(title):
        lines = lines[1:]
        while lines and not lines[0].strip():
            lines = lines[1:]
        return "\n".join(lines)
    return md
