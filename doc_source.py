"""The documents being coded: zotero_docs.csv, loaded into a DataFrame.

`df` is reassigned by refresh_docs() whenever the CSV changes on disk, so other
modules must reach it as `doc_source.df` - never `from doc_source import df`,
which would freeze a stale copy.
"""
import pandas as pd

from config import DATA_CSV

# The document list to code comes entirely from zotero_docs.csv. If it's missing
# (import not run yet), start empty rather than crash — the UI just shows no docs.
COLUMNS = ["zotero_key", "title", "url", "markdown", "snapshot"]
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
    return str(df[col].iloc[i]) if col in df.columns else default


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
