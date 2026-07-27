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
    # Claim roles are multiselects; a claim links selections across these roles.
    "claim_roles": [
        {"role": "actor", "label": "Actor", "options": []},
        {"role": "harm", "label": "Harm", "options": []},
        {"role": "factor", "label": "Factor", "options": []},
        {"role": "harmed_party", "label": "Harmed party", "options": []},
    ],
}

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


def doc_ann(store, key):
    """A document's annotation record, with defaults."""
    a = store.get(key)
    if not isinstance(a, dict):
        a = {}
    # claims: [{id, actor, harm, factor, harmed_party}] — the actor↔harm↔factor↔
    # harmed-party pairings; their highlighted evidence lives in quotes (role+claim).
    return {"fields": a.get("fields", {}), "quotes": a.get("quotes", []),
            "claims": a.get("claims", [])}


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
    so grouping documents never overwrites another document's work. Non-fatal."""
    if mongo_db is None:
        return
    fields = record.get("fields", {})
    inc_id = (answer_text(fields.get("incident_id", {})) or "").strip() or key
    now = datetime.now(timezone.utc)
    doc_entry = {"doc_id": key, "url": cell(i, "url"), "title": cell(i, "title")}
    coding = {"fields": fields, "quotes": record.get("quotes", []),
              "claims": record.get("claims", []), "updated_at": now}
    try:
        # Replace just this document's entry + coding (idempotent on re-save).
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
    except Exception as e:
        print(f"[mongo] sync failed for {key} ({e.__class__.__name__}: {e})")


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
