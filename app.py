"""
Structured incident-coding app with write-back to the dataframe.

Run:  ~/.pyenv/versions/3.10.3/bin/python app.py
Then open http://127.0.0.1:5001

- Reads zotero_docs.csv (from zotero_import.py) if present, else data.csv.
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
  annotations.<coder>.json, incident_groups.<coder>.json and
  data_annotated.<coder>.csv, and in MongoDB their coding is nested under
  `by_document.<doc_key>.by_coder.<coder>` / `groups_by_coder.<coder>`.

The active coder comes from `?coder=`, the `X-Coder` header, or the `coder`
cookie, and must be one of CODERS (set the CODERS env var, comma-separated).
"""
import json
import os
import re
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


def groups_path(coder: str) -> Path:
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
        {"key": "incident_system", "label": "Incident system", "type": "multi", "options": []},
        {"key": "incident_developer", "label": "Incident developer", "type": "multi", "options": []},
        {"key": "incident_deployer", "label": "Incident deployer", "type": "multi", "options": []},
        {"key": "incident_deployer_name", "label": "Incident deployer name", "type": "text"},
        {"key": "incident_aftermath", "label": "Incident aftermath", "type": "text"},
    ],
    # Characteristics coded per document as flat multiselects (no linking here).
    # Linking values into claims happens in the incident card view instead.
    # Display order, matching the UI: factor before harm.
    "claim_roles": [
        {"role": "actor", "label": "Actor", "options": []},
        {"role": "factor", "label": "Factor", "options": []},
        {"role": "harm", "label": "Harm", "options": []},
        {"role": "harmed_party", "label": "Harmed party", "options": []},
    ],
}

# The four characteristic roles, in order. Selected flat per document; grouped
# into claims only in the incident card view.
ROLE_KEYS = [r["role"] for r in DEFAULT_SCHEMA["claim_roles"]]

# Incident-level fields that can also be dragged into a claim, as the optional
# "using <system> developed by <developer>" clauses. The key is the role name
# stored inside a group member; the value is the field its options come from.
# Unlike the four characteristic roles these are optional — a claim is complete
# without them.
CLAIM_FIELD_ROLES = {"system": "incident_system", "developer": "incident_developer"}

app = Flask(__name__)
# The document list to code comes entirely from zotero_docs.csv. If it's missing
# (import not run yet), start empty rather than crash — the UI just shows no docs.
COLUMNS = ["zotero_key", "title", "url", "markdown", "snapshot"]
df = pd.read_csv(DATA_CSV) if DATA_CSV.exists() else pd.DataFrame(columns=COLUMNS)
df["doc_key"] = df["zotero_key"].astype(str)


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


def load_annotations(coder: str) -> dict:
    """One coder's private per-document coding, keyed by doc_key."""
    return _read_json(annotations_path(coder))


def load_groups(coder: str) -> dict:
    """One coder's incident-level claim groupings, built by dragging in the card
    view. Shape: {incident_id: {"groups": [{"id", "members": [{"role", "value"}]}]}}."""
    return _read_json(groups_path(coder))


def save_groups(store: dict, coder: str) -> None:
    _atomic_write(groups_path(coder), json.dumps(store, indent=2, ensure_ascii=False))


def load_assignments() -> dict:
    """The shared doc -> incident mapping every coder codes against.
    Shape: {doc_key: {"incident_id": str, "incident_title": str}}."""
    return _read_json(ASSIGNMENTS_JSON)


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
    """One-time migration off the single-coder layout.

    The old annotations.json / incident_groups.json become the first coder's
    files, and the shared incident assignment map is seeded from whatever coding
    already exists (first coder to have filed a document wins)."""
    for legacy, path in ((LEGACY_ANNOTATIONS_JSON, annotations_path(LEGACY_CODER)),
                         (LEGACY_GROUPS_JSON, groups_path(LEGACY_CODER))):
        if legacy.exists() and not path.exists():
            path.write_text(legacy.read_text())
            print(f"[coders] migrated {legacy.name} -> {path.name}")
    if ASSIGNMENTS_JSON.exists():
        return
    store = {}
    for coder in CODERS:
        for key, rec in load_annotations(coder).items():
            if not isinstance(rec, dict) or key in store:
                continue
            fields = rec.get("fields", {})
            inc_id = (answer_text(fields.get("incident_id", {})) or "").strip()
            if inc_id:
                store[key] = {"incident_id": inc_id,
                              "incident_title": (answer_text(fields.get("incident_title", {})) or "").strip()}
    if store:
        save_assignments(store)
        print(f"[coders] seeded {ASSIGNMENTS_JSON.name} with {len(store)} document(s)")


def doc_ann(store, key, assignments=None):
    """A document's annotation record, with defaults.

    `roles` holds the flat per-document selections: {actor:[], harm:[], factor:[],
    harmed_party:[]}. Their highlighted evidence lives in quotes tagged with the
    role. `assignments` (the shared doc -> incident map) is overlaid on top of the
    coder's own incident_id / incident_title answers, so every coder sees the same
    incident membership even if they never typed the id themselves.

    Documents saved under the old claim-linked model are migrated on read:
    every claim's role values are unioned into the flat lists. The roles object is
    always reconciled with the quotes — any value justified by a role-tagged
    highlight is added as a selected characteristic, even if the stored roles
    object missed it (legacy, hybrid, or Mongo-synced data). Each quote's stale
    `claim` reference is dropped; linking now lives in the card view."""
    a = store.get(key)
    if not isinstance(a, dict):
        a = {}
    quotes = a.get("quotes", []) or []
    roles = a.get("roles")
    if not isinstance(roles, dict):
        roles = {rk: [] for rk in ROLE_KEYS}
        for c in (a.get("claims") or []):
            if not isinstance(c, dict):
                continue
            for rk in ROLE_KEYS:
                for v in (c.get(rk) or []):
                    if v and v not in roles[rk]:
                        roles[rk].append(v)
    else:
        roles = {rk: list(roles.get(rk) or []) for rk in ROLE_KEYS}
    # Reconcile with the highlights: a role-tagged quote's value is a selected
    # characteristic. Also drop the legacy per-quote claim reference.
    for q in quotes:
        if not isinstance(q, dict):
            continue
        q.pop("claim", None)
        r = q.get("role")
        v = q.get("value")
        if r in roles and v and v not in roles[r]:
            roles[r].append(v)
    fields = dict(a.get("fields", {}))
    assigned = (load_assignments() if assignments is None else assignments).get(key)
    if assigned:
        for fk, val in (("incident_id", assigned.get("incident_id")),
                        ("incident_title", assigned.get("incident_title"))):
            if val:
                fields[fk] = {**fields.get(fk, {}), "answer": val}
    return {"fields": fields, "quotes": quotes, "roles": roles}


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
    """Write one coder's annotations + their own flattened CSV mirror."""
    _atomic_write(annotations_path(coder), json.dumps(store, indent=2, ensure_ascii=False))
    schema = load_schema()
    assignments = load_assignments()
    out = df.copy()
    anns = {k: doc_ann(store, k, assignments) for k in df["doc_key"]}
    for f in schema["fields"]:
        out[f["key"]] = [answer_text(anns[k]["fields"].get(f["key"], {})) for k in df["doc_key"]]
    out["coder"] = coder
    out["annotations_json"] = [json.dumps(anns[k], ensure_ascii=False) for k in df["doc_key"]]
    out.to_csv(annotated_csv_path(coder), index=False)


def _by_coder(entry: dict) -> dict:
    """The {coder: coding} map inside a stored `by_document.<key>` entry.
    Coding written before multi-coder support sat flat on the entry; it's read
    back as belonging to the first coder."""
    if not isinstance(entry, dict):
        return {}
    nested = entry.get("by_coder")
    if isinstance(nested, dict):
        return dict(nested)
    if "fields" in entry or "quotes" in entry or "roles" in entry:
        return {LEGACY_CODER: {k: entry[k] for k in ("fields", "quotes", "roles", "updated_at")
                               if k in entry}}
    return {}


def sync_to_mongo(i, key, record, coder):
    """Upsert one coder's coding of one document into the `incidents` collection.

    The incident is keyed by the shared `incident_id` (falling back to the Zotero
    item key when blank), so several documents can share one incident. Each source
    doc is tracked in `documents[]` and each coder's reading of it under
    `by_document.<key>.by_coder.<coder>` — so saving never overwrites another
    coder's work, or another document's.

    Changing a document's incident_id *moves* it: the doc is removed from every
    other incident (both its `by_document` coding and its `documents[]` entry) and
    every coder's coding of it is carried across to the new incident, so no one
    loses work when someone else regroups. Any incident left with no documents,
    coding, or groups is deleted. Non-fatal."""
    if mongo_db is None:
        return
    fields = record.get("fields", {})
    inc_id = (answer_text(fields.get("incident_id", {})) or "").strip() or key
    now = datetime.now(timezone.utc)
    doc_entry = {"doc_id": key, "url": cell(i, "url"), "title": cell(i, "title")}
    coding = {"fields": fields, "quotes": record.get("quotes", []),
              "roles": record.get("roles", {}), "updated_at": now}
    try:
        # Everything already stored for this doc, wherever it currently sits, so a
        # move keeps the other coders' readings of it.
        merged = {}
        for inc in mongo_db.incidents.find({f"by_document.{key}": {"$exists": True}},
                                           {f"by_document.{key}": 1}):
            merged.update(_by_coder((inc.get("by_document") or {}).get(key)))
        # An empty coding is an absence, not a reading: a coder who hasn't touched
        # this document (or has cleared it) leaves no subtree, so "who coded what"
        # stays answerable straight from the collection.
        if has_coding(record):
            merged[coder] = coding
        else:
            merged.pop(coder, None)
        # Detach this doc from any OTHER incident it was previously filed under.
        mongo_db.incidents.update_many(
            {"incident_id": {"$ne": inc_id},
             "$or": [{f"by_document.{key}": {"$exists": True}}, {"documents.doc_id": key}]},
            {"$unset": {f"by_document.{key}": ""},
             "$pull": {"documents": {"doc_id": key}}})
        # Replace just this document's entry + coding under its (new) incident.
        mongo_db.incidents.update_one(
            {"incident_id": inc_id}, {"$pull": {"documents": {"doc_id": key}}})
        mongo_db.incidents.update_one(
            {"incident_id": inc_id},
            {"$setOnInsert": {"incident_id": inc_id, "created_at": now},
             "$set": {"incident_title": answer_text(fields.get("incident_title", {})) or cell(i, "title"),
                      f"by_document.{key}": {"by_coder": merged}, "updated_at": now},
             "$push": {"documents": doc_entry}},
            upsert=True,
        )
        # Clean up any incident now emptied by the move (no docs, coding, or groups).
        mongo_db.incidents.delete_many({
            "documents": {"$size": 0},
            "groups": {"$not": {"$elemMatch": {"members.0": {"$exists": True}}}},
            "$and": [
                {"$or": [{"by_document": {"$exists": False}}, {"by_document": {}}]},
                {"$or": [{"groups_by_coder": {"$exists": False}}, {"groups_by_coder": {}}]},
            ]})
    except Exception as e:
        print(f"[mongo] sync failed for {key} ({e.__class__.__name__}: {e})")


def store_from_mongo(coder: str) -> dict:
    """Rebuild one coder's local {doc_key: {fields, quotes, roles}} store from Mongo.

    Inverse of `sync_to_mongo`: each incident's `by_document.<doc_key>.by_coder.<coder>`
    coding is keyed back by its document key, the same shape annotations.<coder>.json
    uses. Other coders' readings of the same document are skipped. The per-doc
    `updated_at` (present only in Mongo) is dropped so the file keeps its original
    shape. Empty if Mongo isn't connected."""
    store = {}
    if mongo_db is None:
        return store
    for inc in mongo_db.incidents.find():
        for doc_key, entry in (inc.get("by_document") or {}).items():
            coding = _by_coder(entry).get(coder)
            if coding is None:
                continue
            store[str(doc_key)] = {
                "fields": coding.get("fields", {}),
                "quotes": coding.get("quotes", []),
                "roles": coding.get("roles", {}),
            }
    return store


def assignments_from_mongo() -> dict:
    """The shared doc -> incident map as Mongo currently has it (incident_id +
    title per document listed in `documents[]`)."""
    out = {}
    if mongo_db is None:
        return out
    for inc in mongo_db.incidents.find({}, {"incident_id": 1, "incident_title": 1, "documents": 1}):
        for d in (inc.get("documents") or []):
            doc_id = str(d.get("doc_id") or "")
            if doc_id:
                out[doc_id] = {"incident_id": inc.get("incident_id", ""),
                               "incident_title": inc.get("incident_title") or ""}
    return out


@app.route("/api/pull", methods=["POST"])
def api_pull():
    """Pull the active coder's annotations from Mongo into their local file.

    Manual bring-back for the write-only mirror: on any document present in both
    Mongo and the local file, Mongo's copy of *this coder's* coding overwrites the
    local one; other coders' files are untouched. Documents that exist only locally
    (not yet synced anywhere) are kept, so a pull never loses un-synced work. The
    shared incident assignments and the per-coder claim groups come back too."""
    if mongo_db is None:
        return jsonify({"ok": False, "error": "MongoDB not connected"}), 503
    coder = current_coder(strict=True)
    remote = store_from_mongo(coder)
    store = load_annotations(coder)
    store.update(remote)          # Mongo wins for shared keys; local-only kept
    assignments = load_assignments()
    assignments.update(assignments_from_mongo())
    save_assignments(assignments)
    save_annotations(store, coder)

    groups = load_groups(coder)
    for inc in mongo_db.incidents.find({}, {"incident_id": 1, "groups_by_coder": 1, "groups": 1}):
        by_coder = inc.get("groups_by_coder") or (
            {LEGACY_CODER: inc.get("groups")} if inc.get("groups") else {})
        if coder in by_coder:
            groups[inc["incident_id"]] = {"groups": by_coder[coder],
                                          "updated_at": datetime.now(timezone.utc).isoformat()}
    save_groups(groups, coder)
    return jsonify({"ok": True, "coder": coder, "pulled": len(remote), "total": len(store)})


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
    """Has this coder actually put something on the document — an answer, a
    highlight or a characteristic?

    The shared incident_id / incident_title don't count: they're published by
    whoever grouped the document and overlaid on everyone, so treating them as
    coding would mark every coder as having coded every grouped document."""
    if not isinstance(rec, dict):
        return False
    for fk, fa in (rec.get("fields") or {}).items():
        if fk in ("incident_id", "incident_title") or not isinstance(fa, dict):
            continue
        if fa.get("answer") or str(fa.get("comments") or "").strip():
            return True
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
    groups_store = load_groups(coder)

    incidents = {}
    for i in range(len(df)):
        key = df["doc_key"].iloc[i]
        rec = doc_ann(store, key, assignments)
        fields = rec["fields"]
        inc_id = (answer_text(fields.get("incident_id", {})) or "").strip() or key
        g = incidents.setdefault(inc_id, {
            "incident_id": inc_id, "title": "", "documents": [],
            "field_values": {}, "field_comments": {},
            "role_values": {r["role"]: [] for r in role_defs}, "groups": [],
        })
        g["documents"].append({
            "index": i, "doc_key": key, "title": cell(i, "title"),
            "url": cell(i, "url"), "quotes": len(rec["quotes"]),
            "coded_by": coded_by(key, all_stores),
        })
        title = answer_text(fields.get("incident_title", {})).strip()
        if title and not g["title"]:
            g["title"] = title
        for f in field_defs:
            fk = f["key"]
            if fk in ("incident_id", "incident_title"):
                continue
            fa = fields.get(fk, {})
            ans = fa.get("answer")
            vals = ans if isinstance(ans, list) else ([ans] if ans else [])
            bucket = g["field_values"].setdefault(fk, [])
            for v in vals:
                v = str(v).strip()
                if v and v not in bucket:
                    bucket.append(v)
            cmt = str(fa.get("comments") or "").strip()
            if cmt:
                cbucket = g["field_comments"].setdefault(fk, [])
                if cmt not in cbucket:
                    cbucket.append(cmt)
        for r in role_defs:
            bucket = g["role_values"][r["role"]]
            for v in rec["roles"].get(r["role"], []):
                v = str(v).strip()
                if v and v not in bucket:
                    bucket.append(v)

    # Attach saved groupings, dropping any member whose value is no longer coded.
    # A member is either one of the four characteristic roles (checked against the
    # pooled role_values) or an optional system/developer (checked against that
    # incident-level field's values).
    def still_coded(g, m):
        role, value = m.get("role"), m.get("value")
        if role in g["role_values"]:
            return value in g["role_values"][role]
        fk = CLAIM_FIELD_ROLES.get(role)
        return bool(fk) and value in g["field_values"].get(fk, [])

    for inc_id, g in incidents.items():
        saved = groups_store.get(inc_id, {}).get("groups", [])
        pruned = []
        for grp in saved:
            members = [m for m in grp.get("members", []) if still_coded(g, m)]
            if members:
                pruned.append({"id": grp.get("id"), "members": members})
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


@app.route("/api/push", methods=["POST"])
def api_push():
    """Push the active coder's local work up to Mongo (the inverse of /api/pull).

    Per document: this coder's coding (fields, quotes, roles) is upserted into
    `by_document.<key>.by_coder.<coder>` via the same path a save uses, leaving the
    other coders' readings in place. Per incident: only this coder's claim groups
    are written, under `groups_by_coder.<coder>` — the pooled characteristic and
    field lists are intentionally *not* stored, since they are fully derivable from
    `by_document` (see aggregate_incidents). Rollups and the single-coder `groups`
    array left by an older push are unset so the incident doc stays lean. Non-fatal
    per item."""
    if mongo_db is None:
        return jsonify({"ok": False, "error": "MongoDB not connected"}), 503
    coder = current_coder(strict=True)
    store = load_annotations(coder)
    assignments = load_assignments()
    docs_pushed = 0
    for i in range(len(df)):
        key = df["doc_key"].iloc[i]
        try:
            sync_to_mongo(i, key, doc_ann(store, key, assignments), coder)
            docs_pushed += 1
        except Exception as e:
            print(f"[mongo] push failed for {key} ({e.__class__.__name__}: {e})")

    incidents, _, _ = aggregate_incidents(coder)
    now = datetime.now(timezone.utc)
    incidents_pushed, groups_pushed = 0, 0
    for inc_id, g in incidents.items():
        try:
            mongo_db.incidents.update_one(
                {"incident_id": inc_id},
                {"$setOnInsert": {"incident_id": inc_id, "created_at": now},
                 "$set": {f"groups_by_coder.{coder}": g["groups"], "updated_at": now},
                 # drop old rollups + the pre-multi-coder single `groups` array
                 "$unset": {"role_values": "", "field_values": "",
                            **({"groups": ""} if coder == LEGACY_CODER else {})}},
                upsert=True,
            )
            incidents_pushed += 1
            groups_pushed += len(g["groups"])
        except Exception as e:
            print(f"[mongo] incident push failed for {inc_id} ({e.__class__.__name__}: {e})")
    return jsonify({"ok": True, "coder": coder, "documents": docs_pushed,
                    "incidents": incidents_pushed, "groups": groups_pushed})


@app.route("/api/incident/<path:inc_id>/groups", methods=["POST"])
def api_save_groups(inc_id):
    """Persist the active coder's card-view claim groupings for one incident.
    Body: {groups:[…]}, each group {id, members:[{role, value}]}. This is the single
    home for links now that the document view codes characteristics flat; each coder
    links their own claims, so the groupings are per coder."""
    coder = current_coder(strict=True)
    body = request.get_json(force=True)
    groups = body.get("groups", [])
    store = load_groups(coder)
    store[inc_id] = {"groups": groups,
                     "updated_at": datetime.now(timezone.utc).isoformat()}
    save_groups(store, coder)
    return jsonify({"ok": True, "coder": coder, "groups": len(groups)})


@app.route("/api/docs")
def api_docs():
    """The shared document list; the quote count is the active coder's own."""
    store = load_annotations(current_coder())
    assignments = load_assignments()
    return jsonify([
        {"index": i, "title": cell(i, "title"),
         "n": len(doc_ann(store, df["doc_key"].iloc[i], assignments)["quotes"])}
        for i in range(len(df))
    ])


@app.route("/api/doc/<int:i>")
def api_doc(i):
    coder = current_coder()
    store = load_annotations(coder)
    return jsonify({
        "index": i,
        "title": cell(i, "title"),
        "url": cell(i, "url"),
        "markdown": markdown_no_title(i),
        "coder": coder,
        "annotation": doc_ann(store, df["doc_key"].iloc[i], load_assignments()),
    })


@app.route("/api/doc/<int:i>/annotations", methods=["POST"])
def api_save(i):
    """Save one document as the active coder. The interpretive coding lands in
    that coder's own file; the incident this document belongs to is published to
    the shared assignment map so the other coders code the same incidents."""
    coder = current_coder(strict=True)
    store = load_annotations(coder)
    key = df["doc_key"].iloc[i]
    store[key] = request.get_json(force=True)
    record_assignment(key, store[key].get("fields", {}))
    save_annotations(store, coder)
    sync_to_mongo(i, key, doc_ann(store, key), coder)
    return jsonify({"ok": True, "coder": coder, "n": len(store[key].get("quotes", []))})


_seed_shared_files()

if __name__ == "__main__":
    # Local dev entrypoint. In production, gunicorn imports `app:app` (see Procfile)
    # and this block is skipped. PORT is provided by the host; default to 5001 local.
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=bool(os.environ.get("FLASK_DEBUG")))
