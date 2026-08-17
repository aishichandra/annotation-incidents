"""Rebuild the flat `codings` collection from `incidents` — the analysis view.

The `incidents` collection is shaped for *writing*: one document per incident,
each coder's work in its own subtree so a save can never reach another's. That
shape is nested under dynamic keys (coder names, document keys), which no index
can reach and no aggregation reads comfortably.

Intercoder reliability wants the opposite: one row per judgement. This derives it.

    {incident_id, doc_id, coder, kind, role, value, n_quotes, quotes:[{text,start,end}]}

`kind` is "characteristic" for the four claim roles or "field" for an incident
field like incident_system. `role` is the tag itself (harm, incident_system, …).
A value a coder selected without highlighting anything still gets a row, with
n_quotes 0 — that gap is exactly what an agreement measure should see.

Derived and disposable: drop it and re-run any time.

    $PY build_codings.py            # dry run — counts only
    $PY build_codings.py --apply
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
APPLY = "--apply" in sys.argv
# Read from the schema rather than hardcoded, so a role added there is counted
# here without anyone remembering to update this file. Only a missing or
# unreadable schema falls back — anything else should surface, not be swallowed.
try:
    _schema = json.loads((HERE / "schema.json").read_text())
    ROLE_KEYS = tuple(r["role"] for r in _schema.get("claim_roles", []))
except (OSError, ValueError):
    ROLE_KEYS = ("system", "developer", "deployer",
                 "actor", "factor", "harm", "harmed_party")


def load_dotenv(path: Path = HERE / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def rows_for(inc: dict):
    """One row per (document, coder, role, value) judgement in one incident."""
    inc_id = str(inc["_id"])
    for coder, slot in (inc.get("by_coder") or {}).items():
        # Field answers are the incident's, so they aren't tied to one document;
        # their evidence is, and is attached below where it exists.
        field_quotes = {}
        for doc_id, ev in ((slot or {}).get("documents") or {}).items():
            for q in (ev or {}).get("quotes") or []:
                kind = q.get("role") or q.get("category")
                value = str(q.get("value") or "").strip()
                if not (kind and value):
                    continue
                field_quotes.setdefault((kind, value), []).append(
                    {"doc_id": doc_id, "text": q.get("text", ""),
                     "start": q.get("start"), "end": q.get("end")})

        for doc_id, ev in ((slot or {}).get("documents") or {}).items():
            quotes_by = {}
            for q in (ev or {}).get("quotes") or []:
                if q.get("role") and q.get("value"):
                    quotes_by.setdefault((q["role"], q["value"]), []).append(
                        {"text": q.get("text", ""), "start": q.get("start"), "end": q.get("end")})
            for role in ROLE_KEYS:
                for value in ((ev or {}).get("roles") or {}).get(role) or []:
                    qs = quotes_by.get((role, value), [])
                    yield {"incident_id": inc_id, "doc_id": doc_id, "coder": coder,
                           "kind": "characteristic", "role": role, "value": value,
                           "n_quotes": len(qs), "quotes": qs}

        for fk, fa in ((slot or {}).get("fields") or {}).items():
            ans = (fa or {}).get("answer")
            values = ans if isinstance(ans, list) else ([ans] if ans else [])
            for value in values:
                value = str(value).strip()
                if not value:
                    continue
                qs = field_quotes.get((fk, value), [])
                yield {"incident_id": inc_id, "doc_id": None, "coder": coder,
                       "kind": "field", "role": fk, "value": value,
                       "n_quotes": len(qs), "quotes": qs}


def main():
    load_dotenv()
    uri = os.environ.get("MONGO_URI")
    if not uri:
        print("MONGO_URI not set — nothing to do.")
        return
    from pymongo import MongoClient
    db = MongoClient(uri, serverSelectionTimeoutMS=8000)[os.environ.get("MONGO_DB", "incidents")]

    rows = [r for inc in db.incidents.find() for r in rows_for(inc)]
    by_kind, by_coder = {}, {}
    unsupported = 0
    for r in rows:
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
        by_coder[r["coder"]] = by_coder.get(r["coder"], 0) + 1
        if not r["n_quotes"]:
            unsupported += 1
    print(f"{'APPLY' if APPLY else 'DRY RUN'}")
    print(f"  {len(rows)} judgement rows from {db.incidents.count_documents({})} incidents")
    print(f"  by kind: {by_kind}")
    print(f"  by coder: {by_coder}")
    print(f"  rows with no supporting quote: {unsupported}")
    if not APPLY:
        print("\n(dry run — nothing written)")
        return
    now = datetime.now(timezone.utc)
    for r in rows:
        r["built_at"] = now
    db.codings.drop()
    if rows:
        db.codings.insert_many(rows)
    db.codings.create_index([("incident_id", 1), ("coder", 1)])
    db.codings.create_index([("role", 1), ("value", 1)])
    db.codings.create_index("doc_id")
    print(f"\n  wrote {db.codings.count_documents({})} rows to `codings`")


if __name__ == "__main__":
    main()
