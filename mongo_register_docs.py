"""Register newly imported Zotero documents in MongoDB.

Run after zotero_import.py picks up articles added to the Zotero collection.

A newly registered document gets a temporary Mongo incident whose `_id` is the
Zotero item key. This is intentional: app.py uses the document key as the
incident fallback until the document is assigned a real incident ID such as
INC-006.

Once a document is assigned to a real incident, app.py uses the real incident
ID as Mongo `_id` and keeps the Zotero key as documents[].doc_id.

This script is ADD-ONLY. It never modifies or deletes an existing incident,
coding, assignment, or document.

Usage:

    python mongo_register_docs.py
        Dry run — lists documents that would be registered.

    python mongo_register_docs.py --apply
        Actually writes the new placeholder incidents to MongoDB.
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
    """Load KEY=VALUE pairs from .env.

    A real environment variable always wins over the .env value.
    """
    if not path.exists():
        return

    for line in path.read_text().splitlines():
        line = line.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)

        os.environ.setdefault(
            key.strip(),
            value.strip().strip('"').strip("'"),
        )


def mongo_collection():
    """Connect to the incidents collection."""

    load_dotenv()

    uri = os.environ.get("MONGO_URI")

    if not uri:
        print("MONGO_URI not set — nothing to do.")
        return None

    db_name = os.environ.get("MONGO_DB", "incidents")

    try:
        client = MongoClient(
            uri,
            serverSelectionTimeoutMS=8000,
        )

        # Force an actual connection check.
        client.admin.command("ping")

        return client[db_name].incidents

    except Exception as exc:
        print(
            f"MongoDB connection failed "
            f"({exc.__class__.__name__}: {exc})"
        )
        return None


def load_docs() -> pd.DataFrame | None:
    """Load zotero_docs.csv and validate the required columns."""

    if not DOCS_CSV.exists():
        print(
            f"{DOCS_CSV.name} missing — "
            "run zotero_import.py first."
        )
        return None

    try:
        docs = pd.read_csv(DOCS_CSV)
    except Exception as exc:
        print(
            f"Could not read {DOCS_CSV.name}: "
            f"{exc.__class__.__name__}: {exc}"
        )
        return None

    required = {"zotero_key", "title", "url"}
    missing = required - set(docs.columns)

    if missing:
        print(
            f"{DOCS_CSV.name} is missing required columns: "
            f"{', '.join(sorted(missing))}"
        )
        return None

    # Normalize the key because this is our stable document identifier.
    docs["zotero_key"] = (
        docs["zotero_key"]
        .astype(str)
        .str.strip()
    )

    # Remove blank keys.
    docs = docs[docs["zotero_key"] != ""]

    # A Zotero key should be unique.
    duplicates = docs[
        docs["zotero_key"].duplicated(keep=False)
    ]

    if not duplicates.empty:
        keys = sorted(
            set(duplicates["zotero_key"].tolist())
        )

        print(
            "ERROR: duplicate zotero_key values found in CSV:"
        )

        for key in keys:
            print(f"  {key}")

        print(
            "\nFix the duplicate Zotero keys before registering documents."
        )

        return None

    return docs.set_index("zotero_key", drop=False)


def get_registered_document_keys(coll) -> set[str]:
    """Return every Zotero document key Mongo already knows about.

    Current app.py schema:
        documents[].doc_id

    Older registration schema:
        documents[].doc_id
        by_document.<key>

    We recognize both so this migration script does not accidentally duplicate
    documents that were registered by the previous version.
    """

    known: set[str] = set()

    projection = {
        "_id": 1,
        "documents.doc_id": 1,
        "by_document": 1,
    }

    for incident in coll.find({}, projection):

        # Current / expected schema.
        for document in incident.get("documents") or []:
            if not isinstance(document, dict):
                continue

            doc_id = document.get("doc_id")

            if doc_id:
                known.add(str(doc_id))

        # Legacy schema.
        for doc_id in (incident.get("by_document") or {}).keys():
            if doc_id:
                known.add(str(doc_id))

        # If an old placeholder used the Zotero key as incident_id and did not
        # put it anywhere else, recognize that too.
        #
        # Current app.py uses _id for this purpose, so this is only a
        # compatibility check for records created by the old script.
        incident_id = incident.get("incident_id")

        if incident_id:
            known.add(str(incident_id))

    return known


def register_document(
    coll,
    key: str,
    title: str,
    url: str,
) -> None:
    """Create one new placeholder incident.

    IMPORTANT:
    The Mongo `_id` is the Zotero key.

    This matches app.py's incident_of():

        assigned incident_id
            OR
        zotero document key

    No coder-specific coding is created here because nobody has coded the
    document yet.
    """

    now = datetime.now(timezone.utc)

    document_entry = {
        "doc_id": key,
        "url": url,
        "title": title,
    }

    coll.update_one(
        {"_id": key},
        {
            "$setOnInsert": {
                "created_at": now,
                "documents": [document_entry],
            },
            "$set": {
                "updated_at": now,
            },
        },
        upsert=True,
    )


def main():

    docs = load_docs()

    if docs is None:
        return

    coll = mongo_collection()

    if coll is None:
        return

    known = get_registered_document_keys(coll)

    new_keys = [
        key
        for key in docs.index
        if key not in known
    ]

    print(
        f"{'APPLY' if APPLY else 'DRY RUN'} — "
        f"{len(docs)} docs in CSV, "
        f"{len(known)} document keys already known to Mongo, "
        f"{len(new_keys)} to register"
    )

    if not new_keys:
        print("Nothing to add.")
        return

    print()

    for key in new_keys:

        row = docs.loc[key]

        title = str(row.get("title") or "").strip()
        url = str(row.get("url") or "").strip()

        print(
            f"  + {key}  "
            f"{title[:80]}"
        )

        if not APPLY:
            continue

        try:
            register_document(
                coll=coll,
                key=key,
                title=title,
                url=url,
            )

        except Exception as exc:
            print(
                f"    ERROR registering {key}: "
                f"{exc.__class__.__name__}: {exc}"
            )

    if APPLY:
        print(
            f"\nRegistered {len(new_keys)} document(s)."
        )
    else:
        print(
            "\n(dry run — nothing written)"
        )


if __name__ == "__main__":
    main()

