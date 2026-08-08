"""Register newly imported Zotero documents in MongoDB — step 3 of the pipeline.

Run after `zotero_import.py` picks up articles you added to the Zotero collection.
Every document in zotero_docs.csv that Mongo has never seen is inserted the same
way an uncoded document enters via app.sync_to_mongo: a placeholder incident keyed
by the Zotero item key, which the app replaces with the real incident once you
assign an incident_id.

This script only ever *adds* documents. It never writes, moves, or deletes coding,
so it cannot damage what is already in Atlas — unlike the app's Push button, which
writes the active coder's whole local store and will erase that coder's coding in
Mongo for any document their local file is missing.

    $PY mongo_register_docs.py            # dry run — lists what would be added
    $PY mongo_register_docs.py --apply    # write
"""
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from pymongo import MongoClient

HERE = Path(__file__).resolve().parent
DOCS_CSV = HERE / "zotero_docs.csv"
APPLY = "--apply" in sys.argv


def load_dotenv(path: Path = HERE / ".env") -> None:
    """Same precedence as app.py: a real env var wins over the file."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main():
    load_dotenv()
    uri = os.environ.get("MONGO_URI")
    if not uri:
        print("MONGO_URI not set — nothing to do.")
        return
    if not DOCS_CSV.exists():
        print(f"{DOCS_CSV.name} missing — run zotero_import.py first.")
        return

    coll = MongoClient(uri, serverSelectionTimeoutMS=8000)[
        os.environ.get("MONGO_DB", "incidents")].incidents
    docs = pd.read_csv(DOCS_CSV).set_index("zotero_key")

    # Everything Mongo already tracks, under any incident — both the coding
    # subtrees and the shared documents[] entries, since a document can appear
    # in one without the other.
    known = set()
    for inc in coll.find({}, {"by_document": 1, "documents": 1}):
        known |= set(inc.get("by_document") or {})
        known |= {d.get("doc_id") for d in (inc.get("documents") or [])}

    new_docs = [k for k in docs.index if k not in known]
    print(f"{'APPLY' if APPLY else 'DRY RUN'} — {len(docs)} docs in csv, "
          f"{len(known)} already in Mongo, {len(new_docs)} to add")
    if not new_docs:
        return

    now = datetime.now(timezone.utc)
    for key in new_docs:
        title, url = str(docs.loc[key, "title"]), str(docs.loc[key, "url"])
        print(f"  + {key}  {title[:60]}")
        if not APPLY:
            continue
        coll.update_one({"incident_id": key}, {"$pull": {"documents": {"doc_id": key}}})
        coll.update_one(
            {"incident_id": key},
            {"$setOnInsert": {"incident_id": key, "created_at": now},
             "$set": {"incident_title": title, f"by_document.{key}": {"by_coder": {}},
                      "updated_at": now},
             "$push": {"documents": {"doc_id": key, "url": url, "title": title}}},
            upsert=True)

    print(f"\nAdded {len(new_docs)} document(s)." if APPLY else "\n(dry run — nothing written)")


if __name__ == "__main__":
    main()
