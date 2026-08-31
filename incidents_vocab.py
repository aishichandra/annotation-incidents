"""Single source of truth for the incident coding vocabulary.

`vocab.json` holds the controlled category lists. Both the Flask app (frontend
options) and the MongoDB validator (allowed values) are derived from it here, so
the two can never drift apart.
"""
import json
from pathlib import Path

VOCAB_JSON = Path(__file__).parent / "vocab.json"

# Which frontend field / claim-role maps to which vocab list.
#
# Every controlled-vocabulary selection is a characteristic, so all of them are
# roles. System and developer used to sit apart as "fields" with an
# {answer, comments} wrapper, which made them a second kind of thing to code, to
# store, to tag a quote with and to drag into a claim — for no difference anyone
# could point at. `fields` now holds only what is genuinely not a characteristic:
# free text.
FIELD_VOCAB = {}
ROLE_VOCAB = {"system": "systems", "developer": "developers",
              "actor": "actor", "factor": "factor",
              "harm": "harm", "harmed_party": "harmed_party"}

# A vocab list may optionally be organised into named groups under
# "<list>_groups", e.g. "harm_groups": {"Labor/economic harms": [...], ...}.
# Grouping is presentation only — the flat list stays the authoritative set of
# allowed values, so an ungrouped option is still perfectly valid (the UI shows
# it under "Other"). Add a "<list>_groups" object to vocab.json and any list can
# be grouped; nothing here needs changing.
GROUP_SUFFIX = "_groups"

# A vocab list may also carry the codebook definition of each of its options
# under "<list>_definitions", e.g.
#   "harm_definitions": {"Plagiarism": "Passing off another's work as ..."}
# The UI shows a definition as a tooltip wherever that option can be chosen, so
# the rule a coder is applying is legible at the moment they apply it rather than
# in a document beside the app. Like grouping this is presentation only: an
# option with no definition simply gets no tooltip, and a definition for a value
# that is not in the flat list is ignored.
DEFS_SUFFIX = "_definitions"


def load_vocab() -> dict:
    """Read vocab.json (empty dict if it's missing)."""
    if VOCAB_JSON.exists():
        return json.loads(VOCAB_JSON.read_text())
    return {}


def save_vocab(vocab: dict) -> None:
    VOCAB_JSON.write_text(json.dumps(vocab, indent=2, ensure_ascii=False))


def _overlay(entry: dict, vkey: str, vocab: dict) -> None:
    """Put one vocab list (its groups and definitions, if any) onto a schema entry.

    `options` is the flat list of allowed values — the authority. `groups`, when
    present, is how the UI arranges them: [{label, options}] in vocab order. A
    grouped value missing from the flat list is added to it (so a group can't
    offer something unselectable), and a value in no group simply doesn't appear
    in `groups` — the UI puts those under "Other". `definitions`, when present,
    is {option: text} for the tooltip shown where an option is chosen."""
    if vkey not in vocab:
        return
    options = list(vocab[vkey])
    entry["options"] = options
    _overlay_groups(entry, vkey, vocab, options)
    _overlay_definitions(entry, vkey, vocab, options)


def _overlay_groups(entry: dict, vkey: str, vocab: dict, options: list) -> None:
    """Arrange one vocab list into `groups` ([{label, options}]), appending any
    grouped value the flat list forgot so a group can't offer something
    unselectable."""
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
    else:
        entry.pop("groups", None)


def _overlay_definitions(entry: dict, vkey: str, vocab: dict, options: list) -> None:
    """Put one vocab list's option definitions onto a schema entry as
    `definitions` ({option: text}), dropping blanks and anything not actually an
    option — the flat list stays the authority on what exists."""
    raw = vocab.get(vkey + DEFS_SUFFIX)
    if not isinstance(raw, dict):
        entry.pop("definitions", None)
        return
    defs = {o: str(raw[o]).strip() for o in options
            if isinstance(raw.get(o), str) and str(raw[o]).strip()}
    if defs:
        entry["definitions"] = defs
    else:
        entry.pop("definitions", None)


# ---------------------------------------------------------------- editing
# Editing the vocabulary from the Codebook tab. Every one of these rewrites
# vocab.json as a whole — the flat list, its groups and its definitions are three
# views of the same option, so they are kept in step here rather than leaving a
# caller to remember all three.
#
# None of them touch coding already on disk. Renaming an option would strand
# every quote and claim that names it, so a rename is paired with
# storage.rename_role_value() by the route that calls it; deleting is refused
# outright while an option is still in use.


def option_index(vocab: dict, vkey: str, option: str) -> int:
    """Where `option` sits in its flat list, or -1."""
    try:
        return (vocab.get(vkey) or []).index(option)
    except ValueError:
        return -1


def set_definition(vkey: str, option: str, text: str) -> bool:
    """Write one option's definition. Blank text removes it (the option then has
    no tooltip). False if the option doesn't exist."""
    vocab = load_vocab()
    if option_index(vocab, vkey, option) < 0:
        return False
    defs = vocab.setdefault(vkey + DEFS_SUFFIX, {})
    text = (text or "").strip()
    if text:
        defs[option] = text
    else:
        defs.pop(option, None)
    save_vocab(vocab)
    return True


def add_option(vkey: str, option: str, group: str = "", definition: str = "") -> bool:
    """Add an option, optionally into an existing group and with a definition.
    False if it is already there."""
    option = (option or "").strip()
    vocab = load_vocab()
    if not option or option_index(vocab, vkey, option) >= 0:
        return False
    vocab.setdefault(vkey, []).append(option)
    groups = vocab.get(vkey + GROUP_SUFFIX)
    if group and isinstance(groups, dict) and group in groups:
        groups[group].append(option)
    definition = (definition or "").strip()
    if definition:
        vocab.setdefault(vkey + DEFS_SUFFIX, {})[option] = definition
    save_vocab(vocab)
    return True


def reorder_options(vkey: str, group: str, order: list) -> bool:
    """Put one section of a vocabulary into `order`.

    `group` names a group in "<vkey>_groups" to reorder inside; "" means the flat
    list — the whole of it for an ungrouped vocabulary, and the ungrouped tail
    (what the UI shows as "Other") for a grouped one. `order` has to be a
    permutation of what that section already holds: a reorder may never add or
    drop a code, which is what add_option/delete_option are for, and refusing
    here keeps a half-finished drag from quietly losing one.

    Only the list that actually decides display order is rewritten — a group's
    own list, or the flat list — so reordering one group leaves the rest of
    vocab.json byte-for-byte alone.
    """
    order = [str(o) for o in (order or [])]
    vocab = load_vocab()
    options = list(vocab.get(vkey) or [])
    if not options:
        return False
    groups = vocab.get(vkey + GROUP_SUFFIX)
    grouped = isinstance(groups, dict)

    if group:
        if not grouped or group not in groups:
            return False
        if sorted(order) != sorted([v for v in (groups[group] or []) if v]):
            return False
        groups[group] = order
    else:
        placed = {v for vals in groups.values() for v in (vals or [])} if grouped else set()
        current = [o for o in options if o not in placed]
        if sorted(order) != sorted(current):
            return False
        # Splice the reordered tail back in, leaving grouped entries where they
        # are: for a grouped vocabulary the flat list is the record of what
        # exists, and only its ungrouped remainder is on screen.
        it = iter(order)
        vocab[vkey] = [o if o in placed else next(it) for o in options]

    save_vocab(vocab)
    return True


def rename_option(vkey: str, old: str, new: str) -> bool:
    """Rename an option in place — same position in the flat list, same group,
    same definition. False if `old` is unknown or `new` is already taken."""
    new = (new or "").strip()
    vocab = load_vocab()
    i = option_index(vocab, vkey, old)
    if i < 0 or not new or option_index(vocab, vkey, new) >= 0:
        return False
    vocab[vkey][i] = new
    groups = vocab.get(vkey + GROUP_SUFFIX)
    if isinstance(groups, dict):
        for label, values in groups.items():
            groups[label] = [new if v == old else v for v in (values or [])]
    defs = vocab.get(vkey + DEFS_SUFFIX)
    if isinstance(defs, dict) and old in defs:
        # rebuilt rather than reassigned so the definition keeps its place in the
        # file next to the options around it
        vocab[vkey + DEFS_SUFFIX] = {(new if k == old else k): v for k, v in defs.items()}
    save_vocab(vocab)
    return True


def delete_option(vkey: str, option: str) -> bool:
    """Remove an option, its group membership and its definition. False if it
    isn't there. Callers must check it is unused first."""
    vocab = load_vocab()
    if option_index(vocab, vkey, option) < 0:
        return False
    vocab[vkey] = [o for o in vocab[vkey] if o != option]
    groups = vocab.get(vkey + GROUP_SUFFIX)
    if isinstance(groups, dict):
        for label, values in groups.items():
            groups[label] = [v for v in (values or []) if v != option]
    defs = vocab.get(vkey + DEFS_SUFFIX)
    if isinstance(defs, dict):
        defs.pop(option, None)
    save_vocab(vocab)
    return True


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
    # One element per actor context: who did it, optionally with what systems and
    # whose models, and the claims made about that context. actor and harm are
    # single values; `systems`, `developers`, `harmed_parties` and `factors` are
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
            "systems": {"bsonType": "array"},
            "developers": {"bsonType": "array"},
            # pre-plural single values, still permitted so older groups validate
            "system": {"bsonType": ["string", "null"]},
            "developer": {"bsonType": ["string", "null"]},
            "claims": {"bsonType": "array", "items": claim_obj},
            # optional clauses ("using …", "developed by …") this group has taken
            # out of its sentence as inapplicable
            "omit": {"bsonType": "array"},
            # flat links written before the actor-grouped structure; still
            # permitted so any pre-restructure document keeps validating
            "members": {"bsonType": "array"}}},
    }
    # Everything one coder judges about this incident, in one subtree: the
    # incident's own field answers, their claim groups, their comment on the
    # incident as a whole, and their evidence per source document. Keeping it under a single path is what lets a save be one
    # $set that cannot reach another coder's work.
    coder_entry = {
        "bsonType": "object",
        "properties": {
            "fields": {"bsonType": ["object", "null"]},
            # free text belonging to one characteristic (the inciting actor's
            # name), keyed by role
            "notes": {"bsonType": ["object", "null"]},
            "groups": groups_array,
            # this coder's remark about the incident as a whole
            "comment": {"bsonType": ["string", "null"]},
            # this coder's judgement about the incident: "" (still working),
            # "complete" (signed off), or "not_an_incident" (set aside), and
            # when it was set
            "status": {"bsonType": ["string", "null"]},
            "completed_at": {"bsonType": ["string", "null"]},
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
