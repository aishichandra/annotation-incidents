"""Single source of truth for the incident coding vocabulary.

`vocab.json` holds the controlled category lists. Both the Flask app (frontend
options) and the MongoDB validator (allowed values) are derived from it here, so
the two can never drift apart.
"""
import json
from pathlib import Path

VOCAB_JSON = Path(__file__).parent / "vocab.json"

# Which frontend field / claim-role maps to which vocab list.
FIELD_VOCAB = {"incident_system": "systems", "incident_developer": "developers"}
ROLE_VOCAB = {"actor": "actor", "factor": "factor",
              "harm": "harm", "harmed_party": "harmed_party"}


def load_vocab() -> dict:
    """Read vocab.json (empty dict if it's missing)."""
    if VOCAB_JSON.exists():
        return json.loads(VOCAB_JSON.read_text())
    return {}


def save_vocab(vocab: dict) -> None:
    VOCAB_JSON.write_text(json.dumps(vocab, indent=2, ensure_ascii=False))


def apply_vocab_to_schema(schema: dict, vocab: dict | None = None) -> dict:
    """Overlay the controlled vocab onto a frontend schema so the UI options
    always match the DB. Mutates and returns `schema`."""
    vocab = load_vocab() if vocab is None else vocab
    for f in schema.get("fields", []):
        key = FIELD_VOCAB.get(f.get("key"))
        if key and key in vocab:
            f["options"] = list(vocab[key])
    for r in schema.get("claim_roles", []):
        key = ROLE_VOCAB.get(r.get("role"))
        if key and key in vocab:
            r["options"] = list(vocab[key])
    return schema


def build_validator(vocab: dict | None = None) -> dict:
    """MongoDB `$jsonSchema` validator matching what app.py actually writes.

    One document per incident, keyed by `incident_id`. Each source document's
    coding lives under `by_document.<doc_key>`; the `documents` array tracks the
    source URLs grouped into the incident. Controlled vocab is enforced in the UI
    (from vocab.json / apply_vocab_to_schema), so this validator only checks
    structure, not the deep enum values nested under dynamic by_document keys.

    `vocab` is accepted for call-site compatibility but no longer needed here.
    """
    coding = {   # by_document.<doc_key> — one source doc's coding
        "bsonType": "object",
        "properties": {
            "fields": {"bsonType": ["object", "null"]},
            "quotes": {"bsonType": "array"},
            "claims": {"bsonType": "array"},
            "updated_at": {"bsonType": ["date", "null"]},
        },
    }
    schema = {
        "bsonType": "object",
        "required": ["incident_id"],
        "properties": {
            "incident_id": {"bsonType": "string"},
            "incident_title": {"bsonType": ["string", "null"]},
            "by_document": {"bsonType": "object", "additionalProperties": coding},
            "documents": {
                "bsonType": "array",
                "items": {"bsonType": "object", "required": ["doc_id", "url"], "properties": {
                    "doc_id": {"bsonType": "string"},
                    "url": {"bsonType": ["string", "null"]},
                    "title": {"bsonType": ["string", "null"]}}},
            },
            "created_at": {"bsonType": ["date", "null"]},
            "updated_at": {"bsonType": ["date", "null"]},
        },
    }
    return {"$jsonSchema": schema}


def ensure_collection(db, name: str = "incidents", vocab: dict | None = None):
    """Create the collection (with validator + indexes) or update the validator
    if it already exists. Idempotent — safe to call on every app startup. Returns
    the collection."""
    validator = build_validator(vocab)
    if name in db.list_collection_names():
        db.command("collMod", name, validator=validator, validationLevel="moderate")
    else:
        db.create_collection(name, validator=validator, validationLevel="moderate")
    coll = db[name]
    coll.create_index("incident_id", unique=True)   # one document per incident
    coll.create_index("documents.url")               # "which incident cites this URL?"
    coll.create_index("documents.doc_id")
    return coll
