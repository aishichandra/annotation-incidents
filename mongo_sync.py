"""Optional MongoDB mirror of the coding.

Everything here degrades to a no-op when MONGO_URI is unset: `mongo_db` is then
None and each function returns early, so coding works offline on JSON/CSV alone.
Reach the handle as `mongo_sync.mongo_db` - `storage` imports this module and
this module imports `storage`, so binding names across that cycle at import time
would see a half-built module.

  connection    connect_mongo(), mongo_db, resync_validator()
  read cache    _mongo_snapshot() - one query per kind per TTL
  write         push_documents(), sync_incident_coding_to_mongo()
  read          store_from_mongo(), incident_coding_from_mongo(), ...
"""
import os
import time
from datetime import datetime, timezone

from doc_source import cell
from incidents_vocab import ensure_collection
import storage


def connect_mongo():
    """Optional MongoDB sync. Set MONGO_URI (and optionally MONGO_DB) to enable.
    Returns a Database or None; failure is non-fatal so coding still works offline."""
    uri = os.environ.get("MONGO_URI")
    if not uri:
        print("[mongo] MONGO_URI not set — skipping MongoDB sync (JSON/CSV only)")
        return None
    try:
        from pymongo import MongoClient
        client = MongoClient(uri, serverSelectionTimeoutMS=2000)
        client.admin.command("ping")
        db = client[os.environ.get("MONGO_DB", "incidents")]
        # Keep the DB validator in step with vocab.json so the collection accepts
        # exactly the values the UI offers.
        ensure_collection(db, "incidents")
        print(f"[mongo] connected — syncing saves to '{db.name}.incidents'")
        return db
    except Exception as e:
        print(f"[mongo] not connected ({e.__class__.__name__}); saves stay JSON/CSV only")
        return None


mongo_db = connect_mongo()


def resync_validator():
    """Push the current vocab.json into the DB validator (after option edits)."""
    if mongo_db is not None:
        try:
            ensure_collection(mongo_db, "incidents")
        except Exception as e:
            print(f"[mongo] validator resync failed ({e.__class__.__name__}: {e})")


_MONGO_READ_TTL = 5.0          # seconds a Mongo read is reused for
_mongo_cache: dict = {}


def _mongo_snapshot(kind: str, build):
    """A Mongo read, reused for a few seconds.

    Reflecting remote state on every read would otherwise mean a query per
    request; this caps it at one per TTL per kind. Writes call
    `invalidate_mongo_cache()` so a save is visible immediately."""
    now = time.monotonic()
    hit = _mongo_cache.get(kind)
    if hit and now - hit[0] < _MONGO_READ_TTL:
        return hit[1]
    data = build()
    _mongo_cache[kind] = (now, data)
    return data


def invalidate_mongo_cache() -> None:
    _mongo_cache.clear()


def prune_empty_incidents() -> None:
    """Delete any incident left with no documents and nothing coded on it."""
    if mongo_db is None:
        return
    for inc in mongo_db.incidents.find({}, {"documents": 1, "by_coder": 1}):
        if inc.get("documents"):
            continue
        if any((c or {}).get("fields") or (c or {}).get("groups") or (c or {}).get("documents")
               for c in (inc.get("by_coder") or {}).values()):
            continue
        mongo_db.incidents.delete_one({"_id": inc["_id"]})


def push_documents(items) -> int:
    """Upsert many coders'-worth of one-document-each evidence in a single round trip.

    `items` is an iterable of (i, key, record, coder, inc_id) — a save, a push, or
    a sign-off all just hand this a list of however many documents they're writing.
    Each source doc is tracked in `documents[]` and each coder's evidence for it
    under `by_coder.<coder>.documents.<key>` — a single path, so a write can never
    reach another coder's subtree or another document's.

    Moving a document to a different incident detaches it from every other one,
    carrying *every* coder's evidence across so nobody loses work when someone
    else regroups. Working out what to carry, and what to detach it from, used to
    mean scanning the whole `incidents` collection twice *per document* — fine for
    a single save, but a full Push (every document a coder has) turned into
    documents x incidents round trips to Atlas and could take minutes, long enough
    for a deployed host's proxy to kill the request and hand the frontend an HTML
    error page instead of JSON. One document's key only ever appears in its own
    `by_coder.*.documents.<key>` paths, so no other document's write in this same
    batch can change what its carried evidence or stale incidents look like —
    which makes reading the collection once up front, before any writes, exactly
    equivalent to re-querying per document. Any incident left empty afterward is
    deleted. Failures are per document, not per batch: one document hitting the
    validator doesn't lose the rest. Returns how many documents were fully
    written."""
    items = list(items)
    if mongo_db is None or not items:
        return 0
    from pymongo import UpdateOne
    from pymongo.errors import BulkWriteError

    now = datetime.now(timezone.utc)
    snapshot = list(mongo_db.incidents.find({}, {"documents": 1, "by_coder": 1}))

    ops = []
    op_ranges = []  # (start, end) into `ops`, parallel to `items`
    for i, key, record, coder, inc_id in items:
        start = len(ops)
        doc_entry = {"doc_id": key, "url": cell(i, "url"), "title": cell(i, "title"),
                     "date": cell(i, "date")}
        # Every coder's evidence for this document, wherever it currently sits, so
        # a move carries all of it rather than just this coder's.
        carried = {}
        for inc in snapshot:
            for c, sub in (inc.get("by_coder") or {}).items():
                got = ((sub or {}).get("documents") or {}).get(key)
                if got:
                    carried[c] = got
        # An empty coding is an absence, not a reading: a coder who hasn't touched
        # this document (or has cleared it) leaves no entry, so "who coded what"
        # stays answerable straight from the collection.
        if storage.has_coding(record):
            carried[coder] = {"quotes": record.get("quotes", []),
                              "roles": record.get("roles", {}), "updated_at": now}
        else:
            carried.pop(coder, None)
        # Detach from any OTHER incident it was previously filed under.
        for other in snapshot:
            if other["_id"] == inc_id:
                continue
            unset = {f"by_coder.{c}.documents.{key}": ""
                     for c, sub in (other.get("by_coder") or {}).items()
                     if ((sub or {}).get("documents") or {}).get(key)}
            has_doc = any(d.get("doc_id") == key for d in (other.get("documents") or []))
            if unset or has_doc:
                ops.append(UpdateOne(
                    {"_id": other["_id"]},
                    {"$pull": {"documents": {"doc_id": key}}, **({"$unset": unset} if unset else {})}))
        # Replace just this document's entry + evidence under its (new) incident.
        ops.append(UpdateOne({"_id": inc_id}, {"$pull": {"documents": {"doc_id": key}}}))
        sets = {f"by_coder.{c}.documents.{key}": v for c, v in carried.items()}
        ops.append(UpdateOne(
            {"_id": inc_id},
            {"$setOnInsert": {"created_at": now},
             "$set": {"title": storage.incident_title_for(inc_id) or cell(i, "title"),
                      "updated_at": now, **sets},
             "$unset": {f"by_coder.{coder}.documents.{key}": ""} if coder not in carried else {},
             "$push": {"documents": doc_entry}},
            upsert=True,
        ))
        op_ranges.append((start, len(ops)))

    failed_ops = set()
    try:
        # Unordered: one document's validator rejection shouldn't stop the rest
        # from landing, mirroring the old per-document try/except.
        mongo_db.incidents.bulk_write(ops, ordered=False)
    except BulkWriteError as e:
        failed_ops = {err["index"] for err in e.details.get("writeErrors", [])}
        print(f"[mongo] push: {len(failed_ops)} of {len(ops)} write(s) failed")
    except Exception as e:
        print(f"[mongo] push failed ({e.__class__.__name__}: {e})")
        return 0

    # This write is now the freshest state; don't serve a pre-write snapshot.
    invalidate_mongo_cache()
    prune_empty_incidents()
    return sum(1 for start, end in op_ranges
               if not any(idx in failed_ops for idx in range(start, end)))


def sync_incident_coding_to_mongo(inc_id: str, coder: str, entry: dict) -> bool:
    """Mirror one coder's incident-level coding — field answers, claim groups, the
    sign-off and the coder's comment — onto the incident. Only this coder's slot
    is written.

    Returns whether the write actually landed. A failure here isn't fatal — the
    local file is the source of truth and a later Push resends — but it must not
    be reported to the coder as a success, or they are told their judgement is in
    Mongo when the collection validator rejected it."""
    if mongo_db is None:
        return False
    now = datetime.now(timezone.utc)
    try:
        mongo_db.incidents.update_one(
            {"_id": inc_id},
            {"$setOnInsert": {"created_at": now},
             "$set": {f"by_coder.{coder}.fields": entry.get("fields") or {},
                      f"by_coder.{coder}.notes": entry.get("notes") or {},
                      f"by_coder.{coder}.groups": entry.get("groups") or [],
                      f"by_coder.{coder}.comment": entry.get("comment") or "",
                      f"by_coder.{coder}.status": entry.get("status") or "",
                      f"by_coder.{coder}.completed_at": entry.get("completed_at") or "",
                      # "I am not sure about this coding" — a request for a second
                      # look, separate from the sign-off because a coder can be
                      # unsure of a reading they have finished.
                      f"by_coder.{coder}.flagged": bool(entry.get("flagged")),
                      f"by_coder.{coder}.updated_at": now,
                      "title": storage.incident_title_for(inc_id), "updated_at": now}},
            upsert=True)
        invalidate_mongo_cache()
        return True
    except Exception as e:
        print(f"[mongo] incident coding sync failed for {inc_id} ({e.__class__.__name__}: {e})")
        return False


def store_from_mongo(coder: str) -> dict:
    """Rebuild one coder's local {doc_key: {quotes, roles}} store from Mongo.

    Inverse of `push_documents`: every incident's `by_coder.<coder>.documents` is
    keyed back by document key, the shape annotations.<coder>.json uses. Other
    coders' readings are skipped, and the per-document `updated_at` (Mongo only)
    is dropped so the file keeps its own shape. Empty if Mongo isn't connected."""
    store = {}
    if mongo_db is None:
        return store
    for inc in mongo_db.incidents.find({}, {"by_coder": 1}):
        sub = (inc.get("by_coder") or {}).get(coder) or {}
        for doc_key, coding in (sub.get("documents") or {}).items():
            store[str(doc_key)] = {"quotes": (coding or {}).get("quotes", []),
                                   "roles": (coding or {}).get("roles", {})}
    return store


def incident_coding_from_mongo(coder: str) -> dict:
    """One coder's incident-level coding as Mongo has it:
    {inc_id: {"fields": {...}, "groups": [...], "comment": "..."}}. Empty if Mongo
    isn't connected."""
    out = {}
    if mongo_db is None:
        return out
    for inc in mongo_db.incidents.find({}, {"by_coder": 1}):
        sub = (inc.get("by_coder") or {}).get(coder) or {}
        if (sub.get("fields") or sub.get("groups") or sub.get("notes")
                or sub.get("comment") or sub.get("status") or sub.get("flagged")):
            out[str(inc["_id"])] = {"fields": sub.get("fields") or {},
                                    "notes": sub.get("notes") or {},
                                    "groups": sub.get("groups") or [],
                                    "comment": sub.get("comment") or "",
                                    "status": sub.get("status") or "",
                                    "completed_at": sub.get("completed_at") or "",
                                    "flagged": bool(sub.get("flagged"))}
    return out


def assignments_from_mongo() -> dict:
    """The shared doc -> incident map as Mongo currently has it (incident_id +
    title per document listed in `documents[]`).

    A document that nobody has grouped yet is stored under an incident_id equal to
    its own document key — that's `storage.incident_of`'s `or key` fallback, and it is
    how a registered-but-uncoded document sits in the collection. Those are not
    real groupings, so they're skipped: reading one back as an assignment would
    pre-fill the incident ID box with the Zotero key on every new article."""
    out = {}
    if mongo_db is None:
        return out
    for inc in mongo_db.incidents.find({}, {"title": 1, "documents": 1}):
        inc_id = str(inc["_id"])
        for d in (inc.get("documents") or []):
            doc_id = str(d.get("doc_id") or "")
            if doc_id and inc_id != doc_id:
                out[doc_id] = {"incident_id": inc_id,
                               "incident_title": inc.get("title") or ""}
    return out
