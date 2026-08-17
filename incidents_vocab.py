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

# A vocab list may optionally be organised into named groups under
# "<list>_groups", e.g. "harm_groups": {"Labor/economic harms": [...], ...}.
# Grouping is presentation only — the flat list stays the authoritative set of
# allowed values, so an ungrouped option is still perfectly valid (the UI shows
# it under "Other"). Add a "<list>_groups" object to vocab.json and any list can
# be grouped; nothing here needs changing.
GROUP_SUFFIX = "_groups"


def load_vocab() -> dict:
    """Read vocab.json (empty dict if it's missing)."""
    if VOCAB_JSON.exists():
        return json.loads(VOCAB_JSON.read_text())
    return {}


def save_vocab(vocab: dict) -> None:
    VOCAB_JSON.write_text(json.dumps(vocab, indent=2, ensure_ascii=False))


def _overlay(entry: dict, vkey: str, vocab: dict) -> None:
    """Put one vocab list (and its groups, if any) onto a schema entry.

    `options` is the flat list of allowed values — the authority. `groups`, when
    present, is how the UI arranges them: [{label, options}] in vocab order. A
    grouped value missing from the flat list is added to it (so a group can't
    offer something unselectable), and a value in no group simply doesn't appear
    in `groups` — the UI puts those under "Other"."""
    if vkey not in vocab:
        return
    options = list(vocab[vkey])
    entry["options"] = options
    raw = vocab.get(vkey + GROUP_SUFFIX)
    if not isinstance(raw, dict):
        entry.pop("groups", None)
        return
    groups = []
    for label, values in raw.items():
        vals = [v for v in (values or []) if v]
        for v in vals:
            if v not in options:
                options.append(v)
        if vals:
            groups.append({"label": label, "options": vals})
    if groups:
        entry["groups"] = groups


def apply_vocab_to_schema(schema: dict, vocab: dict | None = None) -> dict:
    """Overlay the controlled vocab onto a frontend schema so the UI options
    always match the DB. Mutates and returns `schema`."""
    vocab = load_vocab() if vocab is None else vocab
    for f in schema.get("fields", []):
        key = FIELD_VOCAB.get(f.get("key"))
        if key:
            _overlay(f, key, vocab)
    for r in schema.get("claim_roles", []):
        key = ROLE_VOCAB.get(r.get("role"))
        if key:
            _overlay(r, key, vocab)
    return schema


def build_validator(vocab: dict | None = None) -> dict:
    """MongoDB `$jsonSchema` validator matching what app.py actually writes.

    One document per incident, keyed by `incident_id`. The `documents` array tracks
    the source URLs grouped into the incident — shared by every coder, since which
    document belongs to which incident is a shared decision. Each coder's own
    reading of a source document lives under
    `by_document.<doc_key>.by_coder.<coder>`, so coders never overwrite each other.
    Controlled vocab is enforced in the UI (from vocab.json /
    apply_vocab_to_schema), so this validator only checks structure, not the deep
    enum values nested under dynamic by_document / coder keys.

    Pre-multi-coder documents stored one coding flat on `by_document.<doc_key>`;
    those keys stay permitted here (and are read back as the first coder's work),
    so existing data keeps validating without a migration script.

    `vocab` is accepted for call-site compatibility but no longer needed here.
    """
    evidence = {   # one coder's evidence for one source document
        "bsonType": "object",
        "properties": {
            "quotes": {"bsonType": "array"},
            "roles": {"bsonType": ["object", "null"]},
            "updated_at": {"bsonType": ["date", "null"]},
        },
    }
    # One element per actor context: who did it, optionally with what system and
    # whose model, and the claims made about that context. actor, system,
    # developer and harm are single values; `harmed_parties` and `factors` are
    # lists. The asymmetry is deliberate — one harm reaching several parties, or
    # arising from several factors, is a conjunction anyone can read back, whereas
    # plural harms alongside plural parties would leave "which harm hit which
    # party?" unanswerable. Holding harm to one value is what keeps a claim a
    # single countable proposition.
    claim_obj = {
        "bsonType": "object",
        "properties": {
            "id": {"bsonType": ["string", "null"]},
            "harm": {"bsonType": ["string", "null"]},
            "harmed_parties": {"bsonType": "array"},
            # pre-plural single value, still permitted so older claims validate
            "harmed_party": {"bsonType": ["string", "null"]},
            "factors": {"bsonType": "array"},
        },
    }
    groups_array = {
        "bsonType": "array",
        "items": {"bsonType": "object", "properties": {
            "id": {"bsonType": ["string", "null"]},
            "actor": {"bsonType": ["string", "null"]},
            "system": {"bsonType": ["string", "null"]},
            "developer": {"bsonType": ["string", "null"]},
            "claims": {"bsonType": "array", "items": claim_obj},
            # flat links written before the actor-grouped structure; still
            # permitted so any pre-restructure document keeps validating
            "members": {"bsonType": "array"}}},
    }
    # Everything one coder judges about this incident, in one subtree: the
    # incident's own field answers, their claim groups, and their evidence per
    # source document. Keeping it under a single path is what lets a save be one
    # $set that cannot reach another coder's work.
    coder_entry = {
        "bsonType": "object",
        "properties": {
            "fields": {"bsonType": ["object", "null"]},
            "groups": groups_array,
            "documents": {"bsonType": "object", "additionalProperties": evidence},
            "updated_at": {"bsonType": ["date", "null"]},
        },
    }
    schema = {
        "bsonType": "object",
        "properties": {
            # _id is the incident id — there is no second identity field.
            "_id": {"bsonType": "string"},
            "title": {"bsonType": ["string", "null"]},
            "by_coder": {"bsonType": "object", "additionalProperties": coder_entry},
            "documents": {
                "bsonType": "array",
                "items": {"bsonType": "object", "required": ["doc_id", "url"], "properties": {
                    "doc_id": {"bsonType": "string"},
                    "url": {"bsonType": ["string", "null"]},
                    "title": {"bsonType": ["string", "null"]}}},
            },
            # Pooled characteristic / field lists are NOT stored — they're
            # derived from by_coder when a card is rendered, so the document
            # holds only what was actually judged.
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
    coll.create_index("documents.url")               # "which incident cites this URL?"
    coll.create_index("documents.doc_id")
    return coll
