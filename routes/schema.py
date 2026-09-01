"""The coding scheme as the UI needs it, and growing it from the UI.

/api/schema is what the front end builds every menu from: the fields, the claim
roles, their options after the vocab overlay, and the rules a sign-off is
measured against. The two option routes are the "add your own" box at the foot
of a menu — a code typed while coding, which lands in vocab.json like any other.
"""
from flask import Blueprint, jsonify, request

from config import (
    OPTIONAL_CLAIM_ROLES, REQUIRED_CLAIM_ROLES, load_schema, save_schema,
)
from incidents_vocab import FIELD_VOCAB, ROLE_VOCAB, load_vocab, save_vocab
import mongo_sync


bp = Blueprint("schema", __name__)


@bp.route("/api/schema")
def api_schema():
    """The coding scheme the UI builds itself from: fields, claim roles and their
    controlled vocabularies.

    `rules` carries the constants the frontend would otherwise have to hardcode.
    They live in config.py; sending them means the coding scheme is edited in one
    place and the UI follows, instead of the same rule being written once in
    Python and once in JavaScript and quietly drifting apart."""
    schema = load_schema()
    schema["rules"] = {"required_roles": list(REQUIRED_CLAIM_ROLES),
                       "optional_roles": list(OPTIONAL_CLAIM_ROLES)}
    return jsonify(schema)


def add_vocab_option(vkey, option):
    """Append an option to a vocab list (vocab.json) and resync the DB validator."""
    vocab = load_vocab()
    opts = vocab.setdefault(vkey, [])
    if option and option not in opts:
        opts.append(option)
        save_vocab(vocab)
        mongo_sync.resync_validator()


@bp.route("/api/schema/option", methods=["POST"])
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


@bp.route("/api/schema/role_option", methods=["POST"])
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
