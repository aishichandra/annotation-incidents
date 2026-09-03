
import re
import sqlite3
from pathlib import Path

import fitz
import pandas as pd
import trafilatura

COLLECTION = "Incidents Dashboard Articles"
ZOTERO = Path.home() / "Zotero"
DB = ZOTERO / "zotero.sqlite"
STORAGE = ZOTERO / "storage"
OUT = Path(__file__).parent / "zotero_docs.csv"


def field_value(cur, item_id, field):
    """An item's value for a named Zotero field, or None."""
    row = cur.execute(
        """SELECT idv.value FROM itemData idt
           JOIN itemDataValues idv ON idv.valueID = idt.valueID
           JOIN fields f ON f.fieldID = idt.fieldID
           WHERE idt.itemID = ? AND f.fieldName = ?""",
        (item_id, field),
    ).fetchone()
    return row[0] if row else None


def article_date(cur, item_id):
    """The item's publication date as YYYY-MM-DD, or "" when Zotero has none.

    Zotero stores a date as its own normalised prefix followed by whatever was
    actually entered — "2025-12-11 2025-12-11T08:56:03+00:00" — so the first
    token is the part worth keeping. Anything that isn't a whole date (a bare
    year, a month with no day) is dropped rather than half-recorded: a column
    that sometimes holds a year is a column every reader has to parse."""
    head = (field_value(cur, item_id, "date") or "").strip().split(" ")[0]
    return head if re.fullmatch(r"\d{4}-\d{2}-\d{2}", head) else ""


def html_to_markdown(html, url):
    """Article markdown for a snapshot.

    Some pages (e.g. Substack-style sites) inline megabytes of CSS, which makes
    trafilatura give up and return nothing. Only in that case do we retry on a
    copy with <style> blocks and data: URIs stripped — the first pass is left
    untouched so already-coded documents keep byte-identical text, and with it
    the character offsets their highlights are stored against."""
    def extract(source):
        return trafilatura.extract(
            source, output_format="markdown", include_links=True,
            include_formatting=True, favor_recall=True, url=url or None,
        ) or ""

    markdown = extract(html)
    if markdown:
        return markdown, False
    stripped = re.sub(r"<style\b[^>]*>.*?</style>", "", html, flags=re.S | re.I)
    stripped = re.sub(r'\s(?:src|srcset|href)="data:[^"]*"', "", stripped, flags=re.I)
    return (extract(stripped), True) if len(stripped) < len(html) else ("", False)


def pdf_to_text(path):
    """Article text for a PDF attachment.

    PyMuPDF's plain per-page get_text() occasionally spaces words apart
    character by character — seen on a machine-translated save, where each
    glyph carries its own position instead of sitting in a real word run.
    Collapsing repeated spaces is the one cleanup that needs."""
    with fitz.open(path) as doc:
        text = "\n\n".join(page.get_text() for page in doc)
    return re.sub(r" {2,}", " ", text).strip()


def main():
    # read-only, even if Zotero is open
    con = sqlite3.connect(f"file:{DB}?immutable=1", uri=True)
    cur = con.cursor()

    # Snapshot (linkMode 1 = imported_url, text/html) and PDF (linkMode 0 =
    # imported_file, application/pdf) attachments inside the collection,
    # matched via the attachment's parent item being in the collection.
    # Items in Zotero's trash stay in collectionItems until the trash is emptied,
    # so they are excluded explicitly — otherwise deleting an article here, or
    # re-adding one (which leaves the old copy trashed under a different item
    # key), would import it twice. Oldest first, so the de-duplication below
    # keeps the key any existing coding is already filed under.
    rows = cur.execute(
        """
        SELECT ai.key AS att_key, ia.path AS att_path, ia.contentType AS att_type,
               pi.itemID AS parent_id, pi.key AS parent_key
        FROM collections c
        JOIN collectionItems cit ON cit.collectionID = c.collectionID
        JOIN items pi            ON pi.itemID = cit.itemID
        JOIN itemAttachments ia  ON ia.parentItemID = pi.itemID
        JOIN items ai            ON ai.itemID = ia.itemID
        WHERE c.collectionName = ?
          AND ((ia.contentType = 'text/html' AND ia.linkMode = 1)
               OR (ia.contentType = 'application/pdf' AND ia.linkMode = 0))
          AND pi.itemID NOT IN (SELECT itemID FROM deletedItems)
          AND ai.itemID NOT IN (SELECT itemID FROM deletedItems)
        ORDER BY pi.dateAdded
        """,
        (COLLECTION,),
    ).fetchall()

    if not rows:
        print(f"No snapshots or PDFs found in collection {COLLECTION!r}.")
        return

    # A parent item can carry both a snapshot and a PDF; the snapshot wins so
    # coding (keyed by parent item, not attachment) always points at the same
    # extraction. First attachment seen for a parent stands unless it's a PDF
    # and a snapshot for that same parent turns up later.
    by_parent = {}
    for att_key, att_path, att_type, parent_id, parent_key in rows:
        current = by_parent.get(parent_id)
        if current is None or (current[2] != "text/html" and att_type == "text/html"):
            by_parent[parent_id] = (att_key, att_path, att_type, parent_key)

    records = []
    seen_urls = {}
    for parent_id, (att_key, att_path, att_type, parent_key) in by_parent.items():
        if not att_path or not att_path.startswith("storage:"):
            continue
        fpath = STORAGE / att_key / att_path[len("storage:"):]
        if not fpath.exists():
            print(f"  missing file: {fpath}")
            continue

        title = field_value(cur, parent_id, "title") or fpath.stem
        url = field_value(cur, parent_id, "url") or ""
        date = article_date(cur, parent_id)

        # The same article saved twice is one document to code, not two. The
        # first (oldest) copy wins so its key — and any coding on it — survives.
        if url and url in seen_urls:
            print(f"  dup {parent_key} same URL as {seen_urls[url]}  {title[:50]}")
            continue

        if att_type == "text/html":
            html = fpath.read_text(encoding="utf-8", errors="ignore")
            text, retried = html_to_markdown(html, url)
            flag = " (retried)" if retried else ""
        else:
            text = pdf_to_text(fpath)
            flag = " (pdf)"
        if not text:
            kind = "snapshot" if att_type == "text/html" else "PDF"
            print(f"  EMPTY extraction — check {kind}: {title[:60]}")

        records.append({
            "zotero_key": parent_key,
            "title": title,
            "url": url,
            # When the article was published. Kept as a column of its own rather
            # than derived from the text: it is the one thing about a document
            # that Zotero knows and the article body often doesn't say.
            "date": date,
            "markdown": text,
            "source_file": str(fpath),
        })
        if url:
            seen_urls[url] = parent_key
        print(f"  ok  {len(text):6d} chars  {title[:60]}{flag}")

    con.close()
    pd.DataFrame(records).to_csv(OUT, index=False)
    print(f"\nWrote {OUT} ({len(records)} docs)")


if __name__ == "__main__":
    main()
