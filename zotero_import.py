
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


def main():
    # read-only, even if Zotero is open
    con = sqlite3.connect(f"file:{DB}?immutable=1", uri=True)
    cur = con.cursor()

    # snapshot attachments (linkMode 1 = imported_url) inside the collection,
    # matched via the attachment's parent item being in the collection.
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
        """,
        (COLLECTION,),
    ).fetchall()

    if not rows:
        print(f"No snapshots found in collection {COLLECTION!r}.")
        return

    records = []
    for att_key, att_path, parent_id, parent_key in rows:
        if not att_path or not att_path.startswith("storage:"):
            continue
        fpath = STORAGE / att_key / att_path[len("storage:"):]
        if not fpath.exists():
            print(f"  missing file: {fpath}")
            continue

        title = field_value(cur, parent_id, "title") or fpath.stem
        url = field_value(cur, parent_id, "url") or ""

        html = fpath.read_text(encoding="utf-8", errors="ignore")
        markdown = trafilatura.extract(
            html, output_format="markdown", include_links=True,
            include_formatting=True, favor_recall=True, url=url or None,
        ) or ""

        records.append({
            "zotero_key": parent_key,
            "title": title,
            "url": url,
            "markdown": markdown,
            "snapshot": str(fpath),
        })
        print(f"  ok  {len(markdown):6d} chars  {title[:60]}")

    con.close()
    pd.DataFrame(records).to_csv(OUT, index=False)
    print(f"\nWrote {OUT} ({len(records)} docs)")


if __name__ == "__main__":
    main()
