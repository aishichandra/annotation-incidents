"""
Structured incident-coding app with write-back to the dataframe.

Run:  ~/.pyenv/versions/3.10.3/bin/python app.py
Then open http://127.0.0.1:5001

- Reads zotero_docs.csv (from zotero_import.py), re-reading it whenever the file
  changes, so a fresh import shows up without restarting the app.
- The coding scheme (fields + options) lives in schema.json, which is created
  from DEFAULT_SCHEMA on first run and can be edited by hand or grown from the UI.
- Per article we store: each field's answer + comments, and a list of quotes
  (highlighted passages) tagged with the field they justify. Annotations are
  keyed by the stable Zotero item key.

Multiple coders (intercoder reliability)
---------------------------------------
Several coders code the *same* documents and the *same* incidents independently:

- Shared by everyone: the document list (zotero_docs.csv), the coding scheme
  (schema.json / vocab.json) and — crucially — which document belongs to which
  incident. That grouping lives in incident_assignments.json (doc_key ->
  incident_id + title) so every coder sees an identical set of incidents.
- Private per coder: every interpretive judgement. Each coder writes their own
  annotations.<coder>.json (evidence per document), incident_coding.<coder>.json
  (the incident's field answers + claim groups) and data_annotated.<coder>.csv.
  In MongoDB it all sits under `by_coder.<coder>` on the incident, so one $set
  can never reach another coder's work.

Where a judgement lives follows what it is about. A quote's offsets only mean
something against one document, so evidence — the quotes and the characteristics
they justify — is per document. Free text about the incident as a whole (its
aftermath, the inciting actor's name) is answered once against the incident.

Every controlled-vocabulary selection is a characteristic, including system and
developer. They are all roles, coded the same way, tagged the same way on a
quote, and dragged into a claim the same way. The inciting actor is the `actor`
role — only its *name* is a field, because a name is free text, not a code.

The active coder comes from `?coder=`, the `X-Coder` header, or the `coder`
cookie, and must be one of CODERS (set the CODERS env var, comma-separated).
"""
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from flask import Flask, abort, jsonify, render_template, request

from incidents_vocab import (
    FIELD_VOCAB, ROLE_VOCAB, apply_vocab_to_schema, ensure_collection,
    load_vocab, save_vocab,
)

HERE = Path(__file__).parent
DATA_CSV = HERE / "zotero_docs.csv"   # produced by zotero_import.py (single source)
SCHEMA_JSON = HERE / "schema.json"
# Shared across coders: which document sits in which incident.
ASSIGNMENTS_JSON = HERE / "incident_assignments.json"
# Pre-multi-coder files; migrated into the first coder's files on startup.
LEGACY_ANNOTATIONS_JSON = HERE / "annotations.json"
LEGACY_GROUPS_JSON = HERE / "incident_groups.json"


def _load_dotenv(path: Path = HERE / ".env") -> None:
    """Load KEY=VALUE lines from a local .env into os.environ (no dependency).
    On a host (Railway/Render) there's no .env — the env vars are set directly."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

# Who may code. Override with e.g. CODERS="aisvarya,priya" (env or .env). The
# first name is also where pre-multi-coder files and Mongo records are filed.
CODERS = [c.strip() for c in os.environ.get("CODERS", "").split(",") if c.strip()] \
    or ["coder1", "coder2"]
LEGACY_CODER = CODERS[0]


def annotations_path(coder: str) -> Path:
    return HERE / f"annotations.{coder}.json"


def incident_coding_path(coder: str) -> Path:
    """One coder's incident-level coding: the incident's field answers and its
    claim groups, keyed by incident id."""
    return HERE / f"incident_coding.{coder}.json"


def groups_path(coder: str) -> Path:
    """Pre-restructure claims file. Read once by the migration, never written."""
    return HERE / f"incident_groups.{coder}.json"


def annotated_csv_path(coder: str) -> Path:
    return HERE / f"data_annotated.{coder}.csv"


def current_coder(strict: bool = False) -> str:
    """The coder this request belongs to: `?coder=`, `X-Coder`, or the cookie.

    Unknown names fall back to the first coder so a bare URL still works; on
    writes (`strict`) an explicit unknown name is rejected instead, so a typo
    can't silently file one coder's work under another's name."""
    raw = (request.args.get("coder") or request.headers.get("X-Coder")
           or request.cookies.get("coder") or "").strip()
    if raw in CODERS:
        return raw
    if strict and raw:
        abort(400, f"unknown coder {raw!r} (expected one of {', '.join(CODERS)})")
    return CODERS[0]


# type: "text" = free text; "multi" = pick several from `options` (+ add your own)
DEFAULT_SCHEMA = {
    "fields": [
        {"key": "incident_id", "label": "Incident ID", "type": "text",
         "justify": False, "comments": False},
        {"key": "incident_title", "label": "Incident title", "type": "text",
         "justify": False, "comments": False},
        # Only free text about the incident as a whole lives here. Anything
        # picked from a controlled vocabulary is a characteristic (claim_roles
        # below), and free text belonging to one of those — the inciting actor's
        # name — is a note on that role, not a field.
        {"key": "incident_aftermath", "label": "Incident aftermath", "type": "text"},
    ],
    # Characteristics coded per document as flat multiselects (no linking here).
    # Linking values into claims happens in the incident card view instead.
    # Display order, matching the UI and the coding scheme's own order.
    "claim_roles": [
        {"role": "system", "label": "System", "options": []},
        {"role": "developer", "label": "Developer", "options": []},
        {"role": "actor", "label": "Actor", "options": [],
         "note_label": "Inciting actor(s) name"},
        {"role": "factor", "label": "Factor", "options": []},
        {"role": "harm", "label": "Harm", "options": []},
        {"role": "harmed_party", "label": "Harmed party", "options": []},
    ],
}

# The four characteristic roles, in order. Selected flat per document; grouped
# into claims only in the incident card view.
ROLE_KEYS = [r["role"] for r in DEFAULT_SCHEMA["claim_roles"]]

# The incident's identity. Answered once for the incident and owned by it, so
# these are never stored inside a coder's field answers — they'd be a copy that
# can drift from the incident they're filed under.
IDENTITY_FIELDS = ("incident_id", "incident_title")


def is_empty(v) -> bool:
    """Nothing was answered. One rule, so "" / None / [] can't mean the same
    thing three different ways — an unanswered field simply isn't stored."""
    return v is None or v == "" or v == [] or v == {}


def clean_fields(fields: dict) -> dict:
    """A coder's field answers, ready to store: identity dropped (the incident
    owns it) and every empty answer or comment omitted rather than recorded as
    one of three flavours of blank."""
    out = {}
    for fk, fa in (fields or {}).items():
        if fk in IDENTITY_FIELDS or not isinstance(fa, dict):
            continue
        entry = {}
        if not is_empty(fa.get("answer")):
            entry["answer"] = fa["answer"]
        cmt = str(fa.get("comments") or "").strip()
        if cmt:
            entry["comments"] = cmt
        if entry:
            out[fk] = entry
    return out

# The characteristics a claim's optional "using … developed by …" clauses draw
# from. They are ordinary roles like any other; a claim simply reads as complete
# without them.
OPTIONAL_CLAIM_ROLES = ("system", "developer")

app = Flask(__name__)
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


@app.before_request
def _refresh_before_request() -> None:
    refresh_docs()


refresh_docs()


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


def load_schema() -> dict:
    if not SCHEMA_JSON.exists():
        SCHEMA_JSON.write_text(json.dumps(DEFAULT_SCHEMA, indent=2, ensure_ascii=False))
    schema = json.loads(SCHEMA_JSON.read_text())
    # Overlay controlled vocab so the UI options always match the DB.
    return apply_vocab_to_schema(schema)


def save_schema(schema: dict) -> None:
    SCHEMA_JSON.write_text(json.dumps(schema, indent=2, ensure_ascii=False))


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


def _read_json(path: Path) -> dict:
    if path.exists():
        text = path.read_text().strip()
        if text:
            return json.loads(text)
    return {}


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


def load_annotations(coder: str) -> dict:
    """One coder's evidence per document: {doc_key: {"quotes": [], "roles": {}}}.

    Field answers are not here — they belong to the incident, not to one of its
    documents (see `load_incident_coding`). Quotes are, because their offsets
    only mean anything against a particular document's text.

    The local file, plus anything Mongo holds for this coder that the local file
    has no coding for. That fill-in is what makes the app reflect Mongo without
    anyone pressing Pull: work saved from another machine — or from a deploy
    whose filesystem has since been rebuilt, as on Railway — shows up on its own.

    A document the local file already has coding for is left alone, so a save
    whose Mongo sync failed can never be silently overwritten by Mongo's older
    copy. Use /api/pull for the deliberate "Mongo wins outright" direction."""
    store = _read_json(annotations_path(coder))
    if mongo_db is None:
        return store
    for key, coding in _mongo_snapshot(f"ann:{coder}", lambda: store_from_mongo(coder)).items():
        if not has_coding(store.get(key)):
            store[key] = coding
    return store


def save_annotations_only(store: dict, coder: str) -> None:
    _atomic_write(annotations_path(coder), json.dumps(store, indent=2, ensure_ascii=False))


def blank_incident_coding() -> dict:
    return {"fields": {}, "notes": {}, "groups": []}


def load_incident_coding(coder: str) -> dict:
    """One coder's incident-level coding, keyed by incident id:
    {inc_id: {"fields": {...}, "groups": [...]}}.

    `fields` are the incident's own answers — system, developer, aftermath —
    answered once for the incident rather than repeated on each of its documents.
    `groups` are the claim groups built in the card view.

    Filled in from Mongo per part, the same way `load_annotations` works: a part
    the local file already has is left alone, so a local edit can't be undone by
    a stale remote copy, while work done elsewhere still appears without a Pull."""
    store = {k: {**blank_incident_coding(), **(v or {})}
             for k, v in _read_json(incident_coding_path(coder)).items()}
    if mongo_db is None:
        return store
    remote = _mongo_snapshot(f"inc:{coder}", lambda: incident_coding_from_mongo(coder))
    for inc_id, entry in remote.items():
        local = store.setdefault(inc_id, blank_incident_coding())
        if not local["fields"] and entry.get("fields"):
            local["fields"] = entry["fields"]
        if not local["groups"] and entry.get("groups"):
            local["groups"] = entry["groups"]
    return store


def save_incident_coding(store: dict, coder: str) -> None:
    # Incidents a coder has neither answered nor linked anything on aren't worth
    # a line in the file.
    lean = {k: v for k, v in store.items()
            if v.get("fields") or v.get("groups") or v.get("notes")}
    _atomic_write(incident_coding_path(coder), json.dumps(lean, indent=2, ensure_ascii=False))


def load_assignments() -> dict:
    """The shared doc -> incident mapping every coder codes against.
    Shape: {doc_key: {"incident_id": str, "incident_title": str}}.

    Documents Mongo has grouped but the local file hasn't heard about are filled
    in, so a grouping made elsewhere reaches this app on its own. A local entry
    always wins — regrouping here isn't undone by a stale remote copy."""
    store = _read_json(ASSIGNMENTS_JSON)
    if mongo_db is None:
        return store
    for key, entry in _mongo_snapshot("assignments", assignments_from_mongo).items():
        if entry.get("incident_id"):
            store.setdefault(key, entry)
    return store


def save_assignments(store: dict) -> None:
    _atomic_write(ASSIGNMENTS_JSON, json.dumps(store, indent=2, ensure_ascii=False))


def record_assignment(key, fields) -> None:
    """Publish a save's incident_id / incident_title to the shared mapping.

    Incident membership is deliberately *not* private to a coder: whoever files a
    document under an incident moves it for everyone, so all coders keep coding
    the same incidents. A blank id leaves the current assignment alone."""
    inc_id = (answer_text(fields.get("incident_id", {})) or "").strip()
    if not inc_id:
        return
    title = (answer_text(fields.get("incident_title", {})) or "").strip()
    store = load_assignments()
    prev = store.get(key) or {}
    entry = {"incident_id": inc_id, "incident_title": title or prev.get("incident_title", "")}
    if entry != prev:
        store[key] = entry
        save_assignments(store)


def _seed_shared_files() -> None:
    """One-time migration off the single-coder layout: the old annotations.json
    becomes the first coder's file. Which document sits in which incident lives
    in incident_assignments.json and is the shared record; nothing here derives
    it, since field answers no longer carry an incident id."""
    if LEGACY_ANNOTATIONS_JSON.exists() and not annotations_path(LEGACY_CODER).exists():
        annotations_path(LEGACY_CODER).write_text(LEGACY_ANNOTATIONS_JSON.read_text())
        print(f"[coders] migrated {LEGACY_ANNOTATIONS_JSON.name} -> "
              f"{annotations_path(LEGACY_CODER).name}")


def doc_ann(store, key):
    """One document's evidence for one coder: {"quotes": [], "roles": {}}.

    `roles` holds the flat per-document selections: {actor:[], harm:[], factor:[],
    harmed_party:[]}. Their highlighted evidence lives in quotes tagged with the
    role, and the two are reconciled here — a value justified by a role-tagged
    highlight counts as selected even if the stored roles object missed it. Each
    quote's stale `claim` reference is dropped; linking lives in the card view.

    Field answers are deliberately absent: they belong to the incident, so the
    document view fetches them with `incident_fields`."""
    a = store.get(key)
    if not isinstance(a, dict):
        a = {}
    quotes = a.get("quotes", []) or []
    roles = a.get("roles")
    roles = ({rk: list(roles.get(rk) or []) for rk in ROLE_KEYS}
             if isinstance(roles, dict) else {rk: [] for rk in ROLE_KEYS})
    for q in quotes:
        if not isinstance(q, dict):
            continue
        q.pop("claim", None)
        r, v = q.get("role"), q.get("value")
        if r in roles and v and v not in roles[r]:
            roles[r].append(v)
    return {"quotes": quotes, "roles": roles}


def incident_of(key, assignments=None) -> str:
    """Which incident a document belongs to. Falls back to the document's own key
    for one nobody has filed yet, matching how the card view buckets them."""
    assigned = (load_assignments() if assignments is None else assignments).get(key) or {}
    return (assigned.get("incident_id") or "").strip() or str(key)


def incident_fields(coder, inc_id, assignments=None, inc_store=None) -> dict:
    """The field answers the document view shows: this coder's answers for the
    incident, plus its shared identity overlaid on top so every coder sees the
    same id and title even if they never typed them."""
    store = load_incident_coding(coder) if inc_store is None else inc_store
    fields = dict((store.get(inc_id) or {}).get("fields") or {})
    fields["incident_id"] = {"answer": inc_id}
    title = ""
    for entry in (load_assignments() if assignments is None else assignments).values():
        if entry.get("incident_id") == inc_id and entry.get("incident_title"):
            title = entry["incident_title"]
            break
    if title:
        fields["incident_title"] = {"answer": title}
    return fields


def answer_text(field_ann):
    """Flatten a field's answer to a string for the CSV."""
    ans = field_ann.get("answer")
    if isinstance(ans, list):
        return " | ".join(ans)
    return ans or ""


def _atomic_write(path: Path, text: str) -> None:
    """Write via a temp file + rename so a crash/overlap can't leave a
    half-written (corrupt) file behind."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def save_annotations(store: dict, coder: str) -> None:
    """Write one coder's per-document evidence + their flattened CSV mirror.

    The mirror is one row per document, so the incident-level answers a document
    inherits are joined back on for it — they're what the columns mean, even
    though they're no longer stored per document."""
    save_annotations_only(store, coder)
    schema = load_schema()
    assignments = load_assignments()
    inc_store = load_incident_coding(coder)
    out = df.copy()
    anns = {k: doc_ann(store, k) for k in df["doc_key"]}
    fields_for = {k: incident_fields(coder, incident_of(k, assignments), assignments, inc_store)
                  for k in df["doc_key"]}
    for f in schema["fields"]:
        out[f["key"]] = [answer_text(fields_for[k].get(f["key"], {})) for k in df["doc_key"]]
    out["coder"] = coder
    out["annotations_json"] = [json.dumps(anns[k], ensure_ascii=False) for k in df["doc_key"]]
    out.to_csv(annotated_csv_path(coder), index=False)


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


def sync_to_mongo(i, key, record, coder, inc_id):
    """Upsert one coder's evidence for one document into the `incidents` collection.

    The incident is `_id`, so several documents can share one. Each source doc is
    tracked in `documents[]` and each coder's evidence for it under
    `by_coder.<coder>.documents.<key>` — a single path, so a write can never reach
    another coder's subtree or another document's.

    Moving a document to a different incident detaches it from every other one,
    carrying *every* coder's evidence across so nobody loses work when someone
    else regroups. Any incident left empty by the move is deleted. Non-fatal."""
    if mongo_db is None:
        return
    now = datetime.now(timezone.utc)
    doc_entry = {"doc_id": key, "url": cell(i, "url"), "title": cell(i, "title")}
    try:
        # Every coder's evidence for this document, wherever it currently sits, so
        # a move carries all of it rather than just this coder's.
        carried = {}
        for inc in mongo_db.incidents.find({}, {"by_coder": 1}):
            for c, sub in (inc.get("by_coder") or {}).items():
                got = ((sub or {}).get("documents") or {}).get(key)
                if got:
                    carried[c] = got
        # An empty coding is an absence, not a reading: a coder who hasn't touched
        # this document (or has cleared it) leaves no entry, so "who coded what"
        # stays answerable straight from the collection.
        if has_coding(record):
            carried[coder] = {"quotes": record.get("quotes", []),
                              "roles": record.get("roles", {}), "updated_at": now}
        else:
            carried.pop(coder, None)
        # Detach from any OTHER incident it was previously filed under.
        for other in mongo_db.incidents.find({"_id": {"$ne": inc_id}}, {"by_coder": 1}):
            unset = {f"by_coder.{c}.documents.{key}": ""
                     for c, sub in (other.get("by_coder") or {}).items()
                     if ((sub or {}).get("documents") or {}).get(key)}
            mongo_db.incidents.update_one(
                {"_id": other["_id"]},
                {"$pull": {"documents": {"doc_id": key}}, **({"$unset": unset} if unset else {})})
        # Replace just this document's entry + evidence under its (new) incident.
        mongo_db.incidents.update_one({"_id": inc_id}, {"$pull": {"documents": {"doc_id": key}}})
        sets = {f"by_coder.{c}.documents.{key}": v for c, v in carried.items()}
        mongo_db.incidents.update_one(
            {"_id": inc_id},
            {"$setOnInsert": {"created_at": now},
             "$set": {"title": incident_title_for(inc_id) or cell(i, "title"),
                      "updated_at": now, **sets},
             "$unset": {f"by_coder.{coder}.documents.{key}": ""} if coder not in carried else {},
             "$push": {"documents": doc_entry}},
            upsert=True,
        )
        # This write is now the freshest state; don't serve a pre-write snapshot.
        invalidate_mongo_cache()
        prune_empty_incidents()
    except Exception as e:
        print(f"[mongo] sync failed for {key} ({e.__class__.__name__}: {e})")


def incident_title_for(inc_id: str, assignments=None) -> str:
    """The incident's shared title, from the assignment map."""
    for entry in (load_assignments() if assignments is None else assignments).values():
        if entry.get("incident_id") == inc_id and entry.get("incident_title"):
            return entry["incident_title"]
    return ""


def sync_incident_coding_to_mongo(inc_id: str, coder: str, entry: dict) -> None:
    """Mirror one coder's incident-level coding — field answers and claim groups —
    onto the incident. Only this coder's slot is written."""
    if mongo_db is None:
        return
    now = datetime.now(timezone.utc)
    try:
        mongo_db.incidents.update_one(
            {"_id": inc_id},
            {"$setOnInsert": {"created_at": now},
             "$set": {f"by_coder.{coder}.fields": entry.get("fields") or {},
                      f"by_coder.{coder}.notes": entry.get("notes") or {},
                      f"by_coder.{coder}.groups": entry.get("groups") or [],
                      f"by_coder.{coder}.updated_at": now,
                      "title": incident_title_for(inc_id), "updated_at": now}},
            upsert=True)
        invalidate_mongo_cache()
    except Exception as e:
        print(f"[mongo] incident coding sync failed for {inc_id} ({e.__class__.__name__}: {e})")


def store_from_mongo(coder: str) -> dict:
    """Rebuild one coder's local {doc_key: {quotes, roles}} store from Mongo.

    Inverse of `sync_to_mongo`: every incident's `by_coder.<coder>.documents` is
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
    {inc_id: {"fields": {...}, "groups": [...]}}. Empty if Mongo isn't connected."""
    out = {}
    if mongo_db is None:
        return out
    for inc in mongo_db.incidents.find({}, {"by_coder": 1}):
        sub = (inc.get("by_coder") or {}).get(coder) or {}
        if sub.get("fields") or sub.get("groups") or sub.get("notes"):
            out[str(inc["_id"])] = {"fields": sub.get("fields") or {},
                                    "notes": sub.get("notes") or {},
                                    "groups": sub.get("groups") or []}
    return out


def assignments_from_mongo() -> dict:
    """The shared doc -> incident map as Mongo currently has it (incident_id +
    title per document listed in `documents[]`).

    A document that nobody has grouped yet is stored under an incident_id equal to
    its own document key — that's `sync_to_mongo`'s `or key` fallback, and it is
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


@app.route("/api/pull", methods=["POST"])
def api_pull():
    """Pull the active coder's coding from Mongo into their local files.

    Manual bring-back for the write-only mirror: wherever Mongo and the local file
    both hold something, Mongo's copy of *this coder's* coding wins; other coders'
    files are untouched. Anything that exists only locally (not yet synced) is
    kept, so a pull never loses un-synced work. Per-document evidence, incident
    field answers, claim groups and the shared assignments all come back."""
    if mongo_db is None:
        return jsonify({"ok": False, "error": "MongoDB not connected"}), 503
    coder = current_coder(strict=True)
    invalidate_mongo_cache()      # an explicit pull must not read a stale snapshot
    remote = store_from_mongo(coder)
    store = load_annotations(coder)
    store.update(remote)          # Mongo wins for shared keys; local-only kept
    assignments = load_assignments()
    assignments.update(assignments_from_mongo())
    save_assignments(assignments)
    save_annotations(store, coder)

    inc_store = load_incident_coding(coder)
    for inc_id, entry in incident_coding_from_mongo(coder).items():
        inc_store[inc_id] = {**blank_incident_coding(), **entry}
    save_incident_coding(inc_store, coder)
    return jsonify({"ok": True, "coder": coder, "pulled": len(remote), "total": len(store),
                    "incidents": len(inc_store)})


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/schema")
def api_schema():
    return jsonify(load_schema())


def add_vocab_option(vkey, option):
    """Append an option to a vocab list (vocab.json) and resync the DB validator."""
    vocab = load_vocab()
    opts = vocab.setdefault(vkey, [])
    if option and option not in opts:
        opts.append(option)
        save_vocab(vocab)
        resync_validator()


@app.route("/api/schema/option", methods=["POST"])
def api_add_option():
    """Append a new option to a field's option list and persist it.

    Controlled fields (system/developer) are stored in vocab.json so the UI and
    the DB validator stay in sync; other fields fall back to schema.json."""
    body = request.get_json(force=True)
    field_key, option = body["field"], body["option"].strip()
    if field_key in FIELD_VOCAB:
        add_vocab_option(FIELD_VOCAB[field_key], option)
        for f in load_schema()["fields"]:
            if f["key"] == field_key:
                return jsonify(f)
    schema = load_schema()
    for f in schema["fields"]:
        if f["key"] == field_key and f.get("type") == "multi":
            if option and option not in f.get("options", []):
                f.setdefault("options", []).append(option)
                save_schema(schema)
            return jsonify(f)
    return jsonify({"error": "field not found"}), 404


@app.route("/api/schema/role_option", methods=["POST"])
def api_add_role_option():
    """Append a new option to a claim role's option list and persist it.

    Claim roles are controlled vocab, so additions go to vocab.json and resync
    the DB validator."""
    body = request.get_json(force=True)
    role, option = body["role"], body["option"].strip()
    if role in ROLE_VOCAB:
        add_vocab_option(ROLE_VOCAB[role], option)
        for r in load_schema().get("claim_roles", []):
            if r["role"] == role:
                return jsonify(r)
    schema = load_schema()
    for r in schema.get("claim_roles", []):
        if r["role"] == role:
            if option and option not in r.get("options", []):
                r.setdefault("options", []).append(option)
                save_schema(schema)
            return jsonify(r)
    return jsonify({"error": "role not found"}), 404


def _next_incident_id(ids):
    """Next INC-### id above the highest existing one (INC-001 if none)."""
    nums = [int(m.group(1)) for m in (re.match(r"INC-(\d+)$", x) for x in ids) if m]
    return f"INC-{(max(nums) + 1) if nums else 1:03d}"


@app.route("/api/incident_ids")
def api_incident_ids():
    """Existing incident IDs (to connect an article to another), a title for each
    (so a shared ID can auto-fill the title), plus a suggested new ID.

    Read from the shared assignment map, so every coder is offered the same
    incidents no matter who first filed a document under one."""
    ids, titles = set(), {}
    for entry in load_assignments().values():
        iid = str(entry.get("incident_id") or "").strip()
        if not iid:
            continue
        ids.add(iid)
        title = str(entry.get("incident_title") or "").strip()
        if title and iid not in titles:
            titles[iid] = title
    return jsonify({"ids": sorted(ids), "titles": titles, "next": _next_incident_id(sorted(ids))})


def has_coding(rec) -> bool:
    """Has this coder actually put something on the document — a highlight or a
    characteristic? Field answers can't count: they belong to the incident, so
    they'd mark every coder as having coded every document in it."""
    if not isinstance(rec, dict):
        return False
    return bool(rec.get("quotes")) or any((rec.get("roles") or {}).values())


def coded_by(key, stores) -> list:
    """Which coders have coded this document. Drives the "coded by" badge."""
    return [coder for coder, store in stores.items() if has_coding(store.get(key))]


def aggregate_incidents(coder: str):
    """Build one coder's per-incident view, shared by the cards and the Mongo push.

    An incident is the set of documents sharing an `incident_id` in the shared
    assignment map (a document nobody has filed yet falls back to its own key), so
    the incidents and their documents are identical for every coder. Everything
    inside a card, though, is this coder's own reading: incident-level fields are
    aggregated across the member docs (multiselects/text collect distinct non-empty
    values; title is the first), the four characteristic roles are pooled into
    `role_values` — the palette the card view drags from — and saved claim
    groupings come from incident_groups.<coder>.json, pruned to values that still
    exist so links can't dangle. Each document also carries `coded_by`, the coders
    who have touched it, so progress is visible across the team.
    Returns (incidents_dict, field_defs, role_defs)."""
    store = load_annotations(coder)
    all_stores = {c: load_annotations(c) for c in CODERS}
    assignments = load_assignments()
    schema = load_schema()
    field_defs = schema["fields"]
    role_defs = schema.get("claim_roles", [])
    inc_store = load_incident_coding(coder)

    incidents = {}
    for i in range(len(df)):
        key = df["doc_key"].iloc[i]
        rec = doc_ann(store, key)
        inc_id = incident_of(key, assignments)
        g = incidents.setdefault(inc_id, {
            "incident_id": inc_id, "title": "", "documents": [],
            "field_values": {}, "field_comments": {},
            "role_values": {r["role"]: [] for r in role_defs}, "groups": [],
            "role_notes": {}, "value_quotes": {},
        })
        g["documents"].append({
            "index": i, "doc_key": key, "title": cell(i, "title"),
            "url": cell(i, "url"), "quotes": len(rec["quotes"]),
            "coded_by": coded_by(key, all_stores),
        })
        if not g["title"]:
            g["title"] = incident_title_for(inc_id, assignments)
        # The incident's own answers, read once per incident rather than pooled
        # across its documents — there is only one answer to pool now.
        if not g["field_values"]:
            answers = (inc_store.get(inc_id) or {}).get("fields") or {}
            for f in field_defs:
                fk = f["key"]
                if fk in IDENTITY_FIELDS:
                    continue
                fa = answers.get(fk, {})
                ans = fa.get("answer")
                vals = ans if isinstance(ans, list) else ([ans] if ans else [])
                g["field_values"][fk] = [str(v).strip() for v in vals if str(v).strip()]
                cmt = str(fa.get("comments") or "").strip()
                if cmt:
                    g["field_comments"][fk] = [cmt]
            for role, note in ((inc_store.get(inc_id) or {}).get("notes") or {}).items():
                if str(note or "").strip():
                    g["role_notes"][role] = str(note).strip()
        for r in role_defs:
            bucket = g["role_values"][r["role"]]
            for v in rec["roles"].get(r["role"], []):
                v = str(v).strip()
                if v and v not in bucket:
                    bucket.append(v)
        # The passages justifying each pooled value, so the card can show the
        # evidence behind a characteristic without leaving for the document view.
        # Keyed by the role the quote carries. A quote with no value tags nothing
        # in particular and is skipped; free-text fields keep tagging by category.
        for q in rec["quotes"]:
            if not isinstance(q, dict):
                continue
            value = str(q.get("value") or "").strip()
            text = str(q.get("text") or "").strip()
            kind = q.get("role") or q.get("category")
            if not (value and text and kind):
                continue
            bucket = g["value_quotes"].setdefault(str(kind), {}).setdefault(value, [])
            # The same passage can be highlighted once per document; identical
            # text from the same document is one piece of evidence, not several.
            if not any(b["text"] == text and b["doc_key"] == key for b in bucket):
                bucket.append({"text": text, "doc_key": key, "title": cell(i, "title")})

    # Attach saved groupings, dropping any value that is no longer coded. A value
    # is either one of the four characteristic roles (checked against the pooled
    # role_values) or an optional system/developer (checked against that
    # incident-level field's values).
    def still_coded(g, role, value):
        return bool(value) and value in (g["role_values"].get(role) or [])

    def keep(g, role, value):
        """The value if it's still coded, else None — scalar slots empty out
        rather than dangle."""
        return value if still_coded(g, role, value) else None

    for inc_id, g in incidents.items():
        saved = (inc_store.get(inc_id) or {}).get("groups") or []
        pruned = []
        for grp in saved:
            # Groups written before the actor-grouped structure carried a flat
            # `members` list. They aren't convertible without a coder deciding how
            # to split them, so they're skipped rather than half-rendered.
            if "claims" not in grp:
                continue
            claims = []
            for cl in grp.get("claims") or []:
                harm = keep(g, "harm", cl.get("harm"))
                # Plural, like factors: one harm landing on several parties reads
                # as a conjunction. It's plural harms *times* plural parties that
                # would leave "which harm hit which party?" unanswerable, and harm
                # stays single-valued for exactly that reason.
                parties = [p for p in (cl.get("harmed_parties") or [])
                           if still_coded(g, "harmed_party", p)]
                factors = [f for f in (cl.get("factors") or [])
                           if still_coded(g, "factor", f)]
                if harm or parties or factors:
                    claims.append({"id": cl.get("id"), "harm": harm,
                                   "harmed_parties": parties, "factors": factors})
            actor = keep(g, "actor", grp.get("actor"))
            system = keep(g, "system", grp.get("system"))
            developer = keep(g, "developer", grp.get("developer"))
            # An actor context with nothing left in it at all is dropped; one that
            # still names an actor is kept even with no claims, since it's the
            # header a coder is about to hang claims off.
            if actor or system or developer or claims:
                pruned.append({"id": grp.get("id"), "actor": actor, "system": system,
                               "developer": developer, "claims": claims})
        g["groups"] = pruned

    return incidents, field_defs, role_defs


@app.route("/api/coders")
def api_coders():
    """Who can code, and who this request is being served as. The UI's coder
    picker is built from this."""
    return jsonify({"coders": CODERS, "current": current_coder()})


@app.route("/api/incidents")
def api_incidents():
    """One incident card each, with fields, pooled characteristics, and groups —
    all as coded by the active coder. Every field is returned even when empty so
    the card can render "No data"."""
    coder = current_coder()
    incidents, field_defs, role_defs = aggregate_incidents(coder)
    display_fields = [{"key": f["key"], "label": f["label"]}
                      for f in field_defs if f["key"] not in ("incident_id", "incident_title")]
    roles_meta = [{"role": r["role"], "label": r["label"]} for r in role_defs]
    ordered = sorted(incidents.values(), key=lambda g: g["incident_id"])
    return jsonify({"incidents": ordered, "fields": display_fields,
                    "roles": roles_meta, "coder": coder, "coders": CODERS})


def _jsonable(v):
    """BSON/datetime -> something json.dumps can render, for the raw JSON view."""
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)          # ObjectId and anything else exotic


@app.route("/api/incident/<path:inc_id>/json")
def api_incident_json(inc_id):
    """The incident as it is stored, for the card's raw-JSON view.

    Narrowed to the active coder's own subtree — the card shows one coder's
    reading, and coders stay blind to each other's judgements while coding.

    Only the quote *text* is dropped, replaced by a count. It is the bulk of the
    record (one incident runs to ~24k characters of it) and its offsets only mean
    something against the document view, where it stays readable. Everything that
    describes the coding itself is kept: the incident's field answers, the claim
    groups, and each document's selected characteristics — which live in the same
    subtree as the quotes, so dropping that subtree wholesale would take the
    characteristics with it.

    Mongo's document is the answer when it's there, since that's the record the
    analysis reads. Without it — Mongo not configured, or an incident nobody has
    synced yet — fall back to the same structure assembled from the local files,
    and say which one is being shown so the two are never confused."""
    coder = current_coder()

    def strip_quotes(documents):
        out = {}
        for key, ev in (documents or {}).items():
            ev = dict(ev or {})
            ev["n_quotes"] = len(ev.pop("quotes", None) or [])
            out[key] = ev
        return out

    def narrow(doc):
        slot = dict((doc.get("by_coder") or {}).get(coder) or {})
        slot["documents"] = strip_quotes(slot.get("documents"))
        return {**{k: v for k, v in doc.items() if k != "by_coder"},
                "by_coder": {coder: slot}}

    if mongo_db is not None:
        try:
            doc = mongo_db.incidents.find_one({"_id": inc_id})
            if doc:
                return jsonify({"source": f"mongodb — {coder} only, quote text replaced by n_quotes",
                                "incident": _jsonable(narrow(doc))})
        except Exception as e:
            print(f"[mongo] json read failed for {inc_id} ({e.__class__.__name__}: {e})")
    incidents, _, _ = aggregate_incidents(coder)
    g = incidents.get(inc_id)
    if g is None:
        abort(404, f"no incident {inc_id!r}")
    inc = load_incident_coding(coder).get(inc_id) or {}
    ann = load_annotations(coder)
    local = {"_id": inc_id, "title": g.get("title", ""),
             "documents": [{"doc_id": d["doc_key"], "url": d["url"], "title": d["title"]}
                           for d in g.get("documents", [])],
             "by_coder": {coder: {
                 "fields": inc.get("fields") or {},
                 "groups": inc.get("groups") or [],
                 "documents": strip_quotes(
                     {d["doc_key"]: doc_ann(ann, d["doc_key"])
                      for d in g.get("documents", []) if has_coding(ann.get(d["doc_key"]))})}}}
    return jsonify({"source": f"local files (not in MongoDB yet) — {coder} only, "
                              "quote text replaced by n_quotes",
                    "incident": _jsonable(local)})


@app.route("/api/push", methods=["POST"])
def api_push():
    """Push the active coder's local work up to Mongo (the inverse of /api/pull).

    Per document: this coder's evidence is upserted into
    `by_coder.<coder>.documents.<key>` via the same path a save uses, leaving the
    other coders' readings in place. Per incident: this coder's field answers and
    claim groups go to `by_coder.<coder>`. The pooled characteristic and field
    lists a card shows are intentionally *not* stored — they're derived (see
    aggregate_incidents). Non-fatal per item."""
    if mongo_db is None:
        return jsonify({"ok": False, "error": "MongoDB not connected"}), 503
    coder = current_coder(strict=True)
    store = load_annotations(coder)
    assignments = load_assignments()
    docs_pushed = 0
    for i in range(len(df)):
        key = df["doc_key"].iloc[i]
        try:
            sync_to_mongo(i, key, doc_ann(store, key), coder, incident_of(key, assignments))
            docs_pushed += 1
        except Exception as e:
            print(f"[mongo] push failed for {key} ({e.__class__.__name__}: {e})")

    incidents, _, _ = aggregate_incidents(coder)
    inc_store = load_incident_coding(coder)
    incidents_pushed, groups_pushed = 0, 0
    for inc_id, g in incidents.items():
        try:
            entry = inc_store.get(inc_id) or blank_incident_coding()
            sync_incident_coding_to_mongo(inc_id, coder, {**entry, "groups": g["groups"]})
            incidents_pushed += 1
            groups_pushed += len(g["groups"])
        except Exception as e:
            print(f"[mongo] incident push failed for {inc_id} ({e.__class__.__name__}: {e})")
    invalidate_mongo_cache()
    return jsonify({"ok": True, "coder": coder, "documents": docs_pushed,
                    "incidents": incidents_pushed, "groups": groups_pushed})


@app.route("/api/incident/<path:inc_id>/groups", methods=["POST"])
def api_save_groups(inc_id):
    """Persist the active coder's card-view claim groupings for one incident.
    Body: {groups:[…]}. A group is one actor context — {id, actor, system,
    developer, claims:[{id, harm, harmed_parties:[], factors:[]}]} — where
    actor, system, developer and harm are single values, and harmed_parties
    and factors are lists. This is the single
    home for links now that the document view codes characteristics flat; each coder
    links their own claims, so the groupings are per coder.

    Like a document save, this writes both places: the coder's own JSON file and —
    when Mongo is connected — `groups_by_coder.<coder>` on the incident. Push stays
    available for a bulk re-send, but is no longer what claims depend on."""
    coder = current_coder(strict=True)
    body = request.get_json(force=True)
    groups = body.get("groups", [])
    store = load_incident_coding(coder)
    entry = store.setdefault(inc_id, blank_incident_coding())
    entry["groups"] = groups
    save_incident_coding(store, coder)
    sync_incident_coding_to_mongo(inc_id, coder, entry)
    return jsonify({"ok": True, "coder": coder, "groups": len(groups),
                    "synced": mongo_db is not None})


@app.route("/api/docs")
def api_docs():
    """The shared document list; the quote count is the active coder's own."""
    store = load_annotations(current_coder())
    return jsonify([
        {"index": i, "title": cell(i, "title"),
         "n": len(doc_ann(store, df["doc_key"].iloc[i])["quotes"])}
        for i in range(len(df))
    ])


@app.route("/api/doc/<int:i>")
def api_doc(i):
    """One document to code: its text, this coder's evidence for it, and the
    field answers it inherits from the incident it belongs to."""
    coder = current_coder()
    key = df["doc_key"].iloc[i]
    assignments = load_assignments()
    rec = doc_ann(load_annotations(coder), key)
    return jsonify({
        "index": i,
        "title": cell(i, "title"),
        "url": cell(i, "url"),
        "markdown": markdown_no_title(i),
        "coder": coder,
        "annotation": {**rec,
                       "fields": incident_fields(coder, incident_of(key, assignments), assignments),
                       # free text belonging to a characteristic (the inciting
                       # actor's name); incident-level, edited beside its role
                       "notes": (load_incident_coding(coder).get(incident_of(key, assignments))
                                 or {}).get("notes") or {}},
    })


@app.route("/api/doc/<int:i>/annotations", methods=["POST"])
def api_save(i):
    """Save one document as the active coder.

    The payload is what the document view holds, and each part goes to the home
    it belongs to: quotes and characteristics are evidence for *this document*,
    while the field answers describe the *incident* and are stored once against
    it. The incident id and title go to the shared assignment map, so every coder
    codes the same incidents."""
    coder = current_coder(strict=True)
    key = df["doc_key"].iloc[i]
    body = request.get_json(force=True) or {}
    posted_fields = body.get("fields") or {}

    record_assignment(key, posted_fields)
    assignments = load_assignments()
    inc_id = incident_of(key, assignments)

    store = load_annotations(coder)
    store[key] = {"quotes": body.get("quotes", []), "roles": body.get("roles", {})}
    rec = doc_ann(store, key)
    store[key] = rec
    save_annotations(store, coder)

    inc_store = load_incident_coding(coder)
    entry = inc_store.setdefault(inc_id, blank_incident_coding())
    entry["fields"] = clean_fields(posted_fields)
    entry["notes"] = {r: str(t).strip() for r, t in (body.get("notes") or {}).items()
                      if r in ROLE_KEYS and str(t or "").strip()}
    save_incident_coding(inc_store, coder)

    sync_to_mongo(i, key, rec, coder, inc_id)
    sync_incident_coding_to_mongo(inc_id, coder, entry)
    return jsonify({"ok": True, "coder": coder, "n": len(rec["quotes"])})


_seed_shared_files()

if __name__ == "__main__":
    # Local dev entrypoint. In production, gunicorn imports `app:app` (see Procfile)
    # and this block is skipped. PORT is provided by the host; default to 5001 local.
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=bool(os.environ.get("FLASK_DEBUG")))
