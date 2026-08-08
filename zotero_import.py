
import re
import sqlite3
from pathlib import Path

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


def to_markdown(html, url):
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


def main():
    # read-only, even if Zotero is open
    con = sqlite3.connect(f"file:{DB}?immutable=1", uri=True)
    cur = con.cursor()

    # Snapshot attachments (linkMode 1 = imported_url) inside the collection,
    # matched via the attachment's parent item being in the collection.
    # Items in Zotero's trash stay in collectionItems until the trash is emptied,
    # so they are excluded explicitly — otherwise deleting an article here, or
    # re-adding one (which leaves the old copy trashed under a different item
    # key), would import it twice. Oldest first, so the de-duplication below
    # keeps the key any existing coding is already filed under.
    rows = cur.execute(
        """
        SELECT ai.key AS att_key, ia.path AS att_path,
               pi.itemID AS parent_id, pi.key AS parent_key
        FROM collections c
        JOIN collectionItems cit ON cit.collectionID = c.collectionID
        JOIN items pi            ON pi.itemID = cit.itemID
        JOIN itemAttachments ia  ON ia.parentItemID = pi.itemID
        JOIN items ai            ON ai.itemID = ia.itemID
        WHERE c.collectionName = ?
          AND ia.contentType = 'text/html' AND ia.linkMode = 1
          AND pi.itemID NOT IN (SELECT itemID FROM deletedItems)
          AND ai.itemID NOT IN (SELECT itemID FROM deletedItems)
        ORDER BY pi.dateAdded
        """,
        (COLLECTION,),
    ).fetchall()

    if not rows:
        print(f"No snapshots found in collection {COLLECTION!r}.")
        return

    records = []
    seen_urls = {}
    for att_key, att_path, parent_id, parent_key in rows:
        if not att_path or not att_path.startswith("storage:"):
            continue
        fpath = STORAGE / att_key / att_path[len("storage:"):]
        if not fpath.exists():
            print(f"  missing file: {fpath}")
            continue

        title = field_value(cur, parent_id, "title") or fpath.stem
        url = field_value(cur, parent_id, "url") or ""

        # The same article saved twice is one document to code, not two. The
        # first (oldest) copy wins so its key — and any coding on it — survives.
        if url and url in seen_urls:
            print(f"  dup {parent_key} same URL as {seen_urls[url]}  {title[:50]}")
            continue

        html = fpath.read_text(encoding="utf-8", errors="ignore")
        markdown, retried = to_markdown(html, url)
        if not markdown:
            print(f"  EMPTY extraction — check snapshot: {title[:60]}")

        records.append({
            "zotero_key": parent_key,
            "title": title,
            "url": url,
            "markdown": markdown,
            "snapshot": str(fpath),
        })
        if url:
            seen_urls[url] = parent_key
        flag = " (retried)" if retried else ""
        print(f"  ok  {len(markdown):6d} chars  {title[:60]}{flag}")

    con.close()
    pd.DataFrame(records).to_csv(OUT, index=False)
    print(f"\nWrote {OUT} ({len(records)} docs)")


if __name__ == "__main__":
    main()
