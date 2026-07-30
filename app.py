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
- Saves to annotations.json (source of truth) and mirrors a flattened view into
  data_annotated.csv.
"""
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from flask import Flask, jsonify, render_template, request

from incidents_vocab import (
    FIELD_VOCAB, ROLE_VOCAB, apply_vocab_to_schema, ensure_collection,
    load_vocab, save_vocab,
)

HERE = Path(__file__).parent
DATA_CSV = HERE / "zotero_docs.csv"   # produced by zotero_import.py (single source)
ANNOTATIONS_JSON = HERE / "annotations.json"
DATA_ANNOTATED_CSV = HERE / "data_annotated.csv"
SCHEMA_JSON = HERE / "schema.json"
INCIDENT_GROUPS_JSON = HERE / "incident_groups.json"  # card-view links, per incident


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
    "claim_roles": [
        {"role": "actor", "label": "Actor", "options": []},
        {"role": "harm", "label": "Harm", "options": []},
        {"role": "factor", "label": "Factor", "options": []},
        {"role": "harmed_party", "label": "Harmed party", "options": []},
    ],
}

# The four characteristic roles, in order. Selected flat per document; grouped
# into claims only in the incident card view.
ROLE_KEYS = [r["role"] for r in DEFAULT_SCHEMA["claim_roles"]]

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


def load_annotations() -> dict:
    if ANNOTATIONS_JSON.exists():
        text = ANNOTATIONS_JSON.read_text().strip()
        if text:
            return json.loads(text)
    return {}


def load_groups() -> dict:
    """Incident-level claim groupings built by dragging in the card view.
    Shape: {incident_id: {"groups": [{"id", "members": [{"role", "value"}]}]}}."""
    if INCIDENT_GROUPS_JSON.exists():
        text = INCIDENT_GROUPS_JSON.read_text().strip()
        if text:
            return json.loads(text)
    return {}


def save_groups(store: dict) -> None:
    _atomic_write(INCIDENT_GROUPS_JSON, json.dumps(store, indent=2, ensure_ascii=False))


def doc_ann(store, key):
    """A document's annotation record, with defaults.

    `roles` holds the flat per-document selections: {actor:[], harm:[], factor:[],
    harmed_party:[]}. Their highlighted evidence lives in quotes tagged with the
    role. Documents saved under the old claim-linked model are migrated on read:
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
    return {"fields": a.get("fields", {}), "quotes": quotes, "roles": roles}


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


def save_annotations(store: dict) -> None:
    _atomic_write(ANNOTATIONS_JSON, json.dumps(store, indent=2, ensure_ascii=False))
    schema = load_schema()
    out = df.copy()
    for f in schema["fields"]:
        out[f["key"]] = [
            answer_text(doc_ann(store, k)["fields"].get(f["key"], {})) for k in df["doc_key"]
        ]
    out["annotations_json"] = [
        json.dumps(doc_ann(store, k), ensure_ascii=False) for k in df["doc_key"]
    ]
    out.to_csv(DATA_ANNOTATED_CSV, index=False)


def sync_to_mongo(i, key, record):
    """Upsert one document's coding into the `incidents` collection.

    The incident is keyed by the coder-entered `incident_id` (falling back to the
    Zotero item key when blank), so several documents can share one incident. Each
    source doc is tracked in `documents[]` and its coding under `by_document.<key>`,
    so grouping documents never overwrites another document's work.

    Changing a document's incident_id *moves* it: the doc is removed from every
    other incident (both its `by_document` coding and its `documents[]` entry), and
    any incident left with no documents, coding, or groups is deleted — so an edit
    can't leave a stale duplicate under the old incident. Non-fatal."""
    if mongo_db is None:
        return
    fields = record.get("fields", {})
    inc_id = (answer_text(fields.get("incident_id", {})) or "").strip() or key
    now = datetime.now(timezone.utc)
    doc_entry = {"doc_id": key, "url": cell(i, "url"), "title": cell(i, "title")}
    coding = {"fields": fields, "quotes": record.get("quotes", []),
              "roles": record.get("roles", {}), "updated_at": now}
    try:
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
                      f"by_document.{key}": coding, "updated_at": now},
             "$push": {"documents": doc_entry}},
            upsert=True,
        )
        # Clean up any incident now emptied by the move (no docs, coding, or groups).
        mongo_db.incidents.delete_many(
            {"documents": {"$size": 0},
             "$or": [{"by_document": {"$exists": False}}, {"by_document": {}}],
             "groups": {"$not": {"$elemMatch": {"members.0": {"$exists": True}}}}})
    except Exception as e:
        print(f"[mongo] sync failed for {key} ({e.__class__.__name__}: {e})")


def store_from_mongo() -> dict:
    """Rebuild the local {doc_key: {fields, quotes, roles}} store from Mongo.

    Inverse of `sync_to_mongo`: each incident's `by_document.<doc_key>` coding is
    keyed back by its document key, the same shape `annotations.json` uses. The
    per-doc `updated_at` (present only in Mongo) is dropped so the file keeps its
    original shape. Empty if Mongo isn't connected."""
    store = {}
    if mongo_db is None:
        return store
    for inc in mongo_db.incidents.find():
        for doc_key, coding in (inc.get("by_document") or {}).items():
            store[str(doc_key)] = {
                "fields": coding.get("fields", {}),
                "quotes": coding.get("quotes", []),
                "roles": coding.get("roles", {}),
            }
    return store


@app.route("/api/pull", methods=["POST"])
def api_pull():
    """Pull annotations from Mongo into the local file (Mongo wins).

    Manual bring-back for the write-only mirror: on any document present in both
    Mongo and the local file, Mongo's copy overwrites the local one. Documents
    that exist only locally (not yet synced anywhere) are kept, so a pull never
    loses un-synced work. Rewrites annotations.json + data_annotated.csv."""
    if mongo_db is None:
        return jsonify({"ok": False, "error": "MongoDB not connected"}), 503
    remote = store_from_mongo()
    store = load_annotations()
    store.update(remote)          # Mongo wins for shared keys; local-only kept
    save_annotations(store)
    return jsonify({"ok": True, "pulled": len(remote), "total": len(store)})


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
    (so a shared ID can auto-fill the title), plus a suggested new ID."""
    store = load_annotations()
    ids, titles = set(), {}
    for rec in store.values():
        if not isinstance(rec, dict):
            continue
        f = rec.get("fields", {})
        ans = f.get("incident_id", {}).get("answer")
        if isinstance(ans, str) and ans.strip():
            iid = ans.strip()
            ids.add(iid)
            title = f.get("incident_title", {}).get("answer")
            if isinstance(title, str) and title.strip() and iid not in titles:
                titles[iid] = title.strip()
    return jsonify({"ids": sorted(ids), "titles": titles, "next": _next_incident_id(sorted(ids))})


def aggregate_incidents():
    """Build the per-incident view shared by the cards and the Mongo push.

    An incident is the set of documents sharing a non-empty `incident_id` answer;
    a blank id falls back to the document's own key. Incident-level fields are
    aggregated across the member docs (multiselects/text collect distinct non-empty
    values; title is the first). The four characteristic roles are pooled into
    `role_values` — the palette the card view drags from. Saved claim groupings
    come from incident_groups.json, pruned to values that still exist so links
    can't dangle. Returns (incidents_dict, field_defs, role_defs)."""
    store = load_annotations()
    schema = load_schema()
    field_defs = schema["fields"]
    role_defs = schema.get("claim_roles", [])
    groups_store = load_groups()

    incidents = {}
    for i in range(len(df)):
        key = df["doc_key"].iloc[i]
        rec = doc_ann(store, key)
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
    for inc_id, g in incidents.items():
        saved = groups_store.get(inc_id, {}).get("groups", [])
        pruned = []
        for grp in saved:
            members = [m for m in grp.get("members", [])
                       if m.get("value") in g["role_values"].get(m.get("role"), [])]
            if members:
                pruned.append({"id": grp.get("id"), "members": members})
        g["groups"] = pruned

    return incidents, field_defs, role_defs


@app.route("/api/incidents")
def api_incidents():
    """One incident card each, with fields, pooled characteristics, and groups.
    Every field is returned even when empty so the card can render "No data"."""
    incidents, field_defs, role_defs = aggregate_incidents()
    display_fields = [{"key": f["key"], "label": f["label"]}
                      for f in field_defs if f["key"] not in ("incident_id", "incident_title")]
    roles_meta = [{"role": r["role"], "label": r["label"]} for r in role_defs]
    ordered = sorted(incidents.values(), key=lambda g: g["incident_id"])
    return jsonify({"incidents": ordered, "fields": display_fields, "roles": roles_meta})


@app.route("/api/push", methods=["POST"])
def api_push():
    """Push everything local up to Mongo (the inverse of /api/pull).

    Per document: the coding (fields, quotes, roles) is upserted into
    `by_document.<key>` via the same path a save uses. Per incident: only the claim
    `groups` are written onto the incident document — the pooled characteristic and
    field lists are intentionally *not* stored, since they are fully derivable from
    `by_document` (see aggregate_incidents). Any such rollups left by an older push
    are unset so the incident doc stays lean. Non-fatal per item."""
    if mongo_db is None:
        return jsonify({"ok": False, "error": "MongoDB not connected"}), 503
    store = load_annotations()
    docs_pushed = 0
    for i in range(len(df)):
        key = df["doc_key"].iloc[i]
        try:
            sync_to_mongo(i, key, doc_ann(store, key))
            docs_pushed += 1
        except Exception as e:
            print(f"[mongo] push failed for {key} ({e.__class__.__name__}: {e})")

    incidents, _, _ = aggregate_incidents()
    now = datetime.now(timezone.utc)
    incidents_pushed, groups_pushed = 0, 0
    for inc_id, g in incidents.items():
        try:
            mongo_db.incidents.update_one(
                {"incident_id": inc_id},
                {"$setOnInsert": {"incident_id": inc_id, "created_at": now},
                 "$set": {"groups": g["groups"], "updated_at": now},
                 "$unset": {"role_values": "", "field_values": ""}},  # drop old rollups
                upsert=True,
            )
            incidents_pushed += 1
            groups_pushed += len(g["groups"])
        except Exception as e:
            print(f"[mongo] incident push failed for {inc_id} ({e.__class__.__name__}: {e})")
    return jsonify({"ok": True, "documents": docs_pushed,
                    "incidents": incidents_pushed, "groups": groups_pushed})


@app.route("/api/incident/<path:inc_id>/groups", methods=["POST"])
def api_save_groups(inc_id):
    """Persist the card-view claim groupings for one incident. Body: {groups:[…]}.
    Each group is {id, members:[{role, value}]}. This is the single home for links
    now that the document view codes characteristics flat."""
    body = request.get_json(force=True)
    groups = body.get("groups", [])
    store = load_groups()
    store[inc_id] = {"groups": groups,
                     "updated_at": datetime.now(timezone.utc).isoformat()}
    save_groups(store)
    return jsonify({"ok": True, "groups": len(groups)})


@app.route("/api/docs")
def api_docs():
    store = load_annotations()
    return jsonify([
        {"index": i, "title": cell(i, "title"),
         "n": len(doc_ann(store, df["doc_key"].iloc[i])["quotes"])}
        for i in range(len(df))
    ])


@app.route("/api/doc/<int:i>")
def api_doc(i):
    store = load_annotations()
    return jsonify({
        "index": i,
        "title": cell(i, "title"),
        "url": cell(i, "url"),
        "markdown": markdown_no_title(i),
        "annotation": doc_ann(store, df["doc_key"].iloc[i]),
    })


@app.route("/api/doc/<int:i>/annotations", methods=["POST"])
def api_save(i):
    store = load_annotations()
    key = df["doc_key"].iloc[i]
    store[key] = request.get_json(force=True)
    save_annotations(store)
    sync_to_mongo(i, key, store[key])
    return jsonify({"ok": True, "n": len(store[key].get("quotes", []))})


if __name__ == "__main__":
    # Local dev entrypoint. In production, gunicorn imports `app:app` (see Procfile)
    # and this block is skipped. PORT is provided by the host; default to 5001 local.
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=bool(os.environ.get("FLASK_DEBUG")))
