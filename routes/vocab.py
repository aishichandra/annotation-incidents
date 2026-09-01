"""The Codebook tab: editing the coding scheme itself.

The vocabulary is shared, not per coder — one scheme is the whole point of two
people coding the same incidents — so every edit here lands in vocab.json and
every coder sees it.

Renaming and deleting are the dangerous pair, because coding already on disk
names its codes as strings. A rename therefore migrates that coding in the same
request, and a delete is refused while anything still uses the code.

Two kinds of vocabulary live here and are edited identically: a claim role,
coded per document, and a controlled field, answered once on an incident card.
_vocab_key resolves either, so no route below has to know which it was handed;
only counting and renaming differ, and _vocab_usage hides that too.
"""
from flask import Blueprint, jsonify, request

from config import CODERS, load_schema
from incidents_vocab import (
    FIELD_VOCAB, ROLE_VOCAB, add_option, delete_option, rename_option,
    reorder_options, set_definition,
)
from storage import (
    field_value_incidents, field_value_usage, incident_title_for,
    load_assignments, rename_field_value, rename_role_value,
    role_value_incidents, role_value_usage,
)
import mongo_sync


bp = Blueprint("vocab", __name__)


# ---------------------------------------------------------------- codebook
# The Codebook tab edits the controlled vocabulary itself: what the codes are and
# what each one means. It is shared, not per coder — one scheme is the whole
# point of coding the same incidents twice — so every route here writes
# vocab.json and every coder sees the result.
#
# Renaming and deleting are the dangerous pair, because coding already on disk
# names its codes as strings. A rename therefore migrates that coding in the same
# request, and a delete is refused while anything still uses the code.


def _vocab_key(name: str) -> str:
    """The vocab.json list behind a claim role or a controlled field, or None.

    The Codebook tab addresses both by name and both are edited the same way, so
    every endpoint below reaches its vocabulary through here without having to
    know which of the two kinds it was handed."""
    return ROLE_VOCAB.get(name) or FIELD_VOCAB.get(name)


def _vocab_usage(name: str) -> dict:
    """{value: {coder: n}} for one codebook section. The two kinds are counted
    in different places — a role across documents, quotes and claims; a field in
    the incident's own answers — but they are counted in the same unit, the
    incident, so a use means the same thing either way."""
    return (role_value_usage([name]) if name in ROLE_VOCAB
            else field_value_usage([name])).get(name, {})


def _codebook_section(name, label, entry, used):
    """One section of the codebook: a vocabulary's codes in display order, each
    with its group, its definition and how many incidents already use it, plus
    `unknown` — anything the coding names that the vocabulary no longer offers.

    `entry` is the schema entry the vocab overlay has already been applied to,
    which is the same shape for a claim role and for a controlled field."""
    group_of = {}
    for g in entry.get("groups") or []:
        for o in g["options"]:
            group_of[o] = g["label"]
    defs = entry.get("definitions") or {}
    # In display order — groups in their own order, then anything ungrouped.
    # The codebook used to read the flat list, which for a grouped vocabulary
    # is only the record of what exists: `harm`'s flat order diverges from its
    # groups', so the same group was listed one way here and another way in
    # the menu a coder picks from.
    flat = list(entry.get("options") or [])
    seen, ordered = set(), []
    for g in entry.get("groups") or []:
        for o in g["options"]:
            if o in flat and o not in seen:
                seen.add(o)
                ordered.append(o)
    ordered += [o for o in flat if o not in seen]
    options = [{"name": o, "definition": defs.get(o, ""), "group": group_of.get(o, ""),
                "uses": used.get(o, {}), "total": sum((used.get(o) or {}).values())}
               for o in ordered]
    known = {o["name"] for o in options}
    unknown = [{"name": v, "uses": by, "total": sum(by.values())}
               for v, by in used.items() if v not in known]
    return {"role": name, "label": label,
            "groups": [g["label"] for g in entry.get("groups") or []],
            "options": options,
            "unknown": sorted(unknown, key=lambda u: -u["total"])}


@bp.route("/api/vocab")
def api_vocab():
    """The whole codebook as the editor needs it: every controlled vocabulary,
    its options in order with their group and definition, and how many times each
    is already used — an editor should never rename or delete blind.

    Both kinds of vocabulary are here. The characteristics come first, in scheme
    order; the incident's own controlled fields (geography, translated) follow.
    They are answered once per incident rather than coded per document, but the
    codebook is the scheme, and a code you can pick is a code that has to be
    defined somewhere."""
    schema = load_schema()          # already carries the vocab overlay
    role_usage = role_value_usage(list(ROLE_VOCAB))
    field_usage = field_value_usage(list(FIELD_VOCAB))
    sections = [
        _codebook_section(r["role"], r.get("label", r["role"]), r,
                          role_usage.get(r["role"], {}))
        for r in schema.get("claim_roles", []) if _vocab_key(r["role"])
    ] + [
        _codebook_section(f["key"], f.get("label", f["key"]), f,
                          field_usage.get(f["key"], {}))
        for f in schema.get("fields", []) if _vocab_key(f["key"])
    ]
    return jsonify({"roles": sections, "coders": CODERS})


@bp.route("/api/vocab/uses")
def api_vocab_uses():
    """Which incidents one code is used in, newest id last, with each coder's
    count. What the Codebook shows when you click a code's use count — a number
    on its own doesn't tell you whether a rename is safe, the incidents behind it
    do."""
    role, option = request.args.get("role", ""), request.args.get("option", "")
    if not _vocab_key(role):
        return jsonify({"error": "unknown role"}), 404
    assignments = load_assignments()
    by_inc = (role_value_incidents(role, option) if role in ROLE_VOCAB
              else field_value_incidents(role, option))
    incidents = [{"incident_id": inc_id,
                  "title": incident_title_for(inc_id, assignments) if inc_id else "",
                  "uses": by, "total": sum(by.values())}
                 for inc_id, by in by_inc.items()]
    incidents.sort(key=lambda i: (i["incident_id"] == "", i["incident_id"]))
    return jsonify({"role": role, "option": option, "incidents": incidents,
                    "total": sum(i["total"] for i in incidents)})


@bp.route("/api/vocab/definition", methods=["POST"])
def api_vocab_definition():
    """Write one code's definition — the text the coding UI shows on hover."""
    body = request.get_json(force=True)
    vkey = _vocab_key(body.get("role"))
    if not vkey:
        return jsonify({"error": "unknown role"}), 404
    if not set_definition(vkey, body.get("option", ""), body.get("definition", "")):
        return jsonify({"error": "unknown option"}), 404
    return jsonify({"ok": True})


@bp.route("/api/vocab/option", methods=["POST"])
def api_vocab_add():
    """Add a code, optionally into one of the role's existing groups."""
    body = request.get_json(force=True)
    role = body.get("role")
    vkey = _vocab_key(role)
    if not vkey:
        return jsonify({"error": "unknown role"}), 404
    if not add_option(vkey, body.get("option", ""), body.get("group", ""),
                      body.get("definition", "")):
        return jsonify({"error": "blank or duplicate option"}), 400
    mongo_sync.resync_validator()
    return jsonify({"ok": True})


@bp.route("/api/vocab/reorder", methods=["POST"])
def api_vocab_reorder():
    """Reorder one section of a role's codes: a named group, or the flat list.

    Order is presentation, not meaning — no coding names a position — so unlike
    rename this touches vocab.json alone and never migrates anything."""
    body = request.get_json(force=True)
    role = body.get("role")
    vkey = _vocab_key(role)
    if not vkey:
        return jsonify({"error": "unknown role"}), 404
    if not reorder_options(vkey, body.get("group", "") or "", body.get("order") or []):
        return jsonify({"error": "order must list exactly the codes already in "
                                 "that section"}), 400
    return jsonify({"ok": True})


@bp.route("/api/vocab/rename", methods=["POST"])
def api_vocab_rename():
    """Rename a code and rewrite every quote and claim that names it.

    Both halves or neither: the vocabulary is written first because a failure
    there leaves the coding untouched, whereas coding migrated against a
    vocabulary that never changed would name a code nobody offers."""
    body = request.get_json(force=True)
    role = body.get("role")
    vkey = _vocab_key(role)
    if not vkey:
        return jsonify({"error": "unknown role"}), 404
    old, new = body.get("old", ""), (body.get("new") or "").strip()
    if not rename_option(vkey, old, new):
        return jsonify({"error": "unknown option, or that name is taken"}), 400
    migrated = (sum(rename_role_value(role, old, new).values()) if role in ROLE_VOCAB
                else rename_field_value(role, old, new))
    mongo_sync.resync_validator()
    mongo_sync.invalidate_mongo_cache()
    # `slots` is what the rewrite physically touched, `total` how many uses that
    # is — a document counts once however many quotes back it. The Codebook shows
    # the use count before a rename, so the report afterwards has to match it or
    # the same rename appears to have grown on the way through.
    uses = _vocab_usage(role).get(new, {})
    return jsonify({"ok": True, "migrated": uses, "total": sum(uses.values()),
                    "slots": migrated})


@bp.route("/api/vocab/delete", methods=["POST"])
def api_vocab_delete():
    """Remove a code — refused while any coder still uses it, since deleting
    would leave their quotes naming something the scheme no longer has. Rename it
    into another code first, or clear it from the coding."""
    body = request.get_json(force=True)
    role = body.get("role")
    vkey = _vocab_key(role)
    if not vkey:
        return jsonify({"error": "unknown role"}), 404
    option = body.get("option", "")
    uses = _vocab_usage(role).get(option, {})
    if uses:
        return jsonify({"error": "in use", "uses": uses,
                        "total": sum(uses.values())}), 409
    if not delete_option(vkey, option):
        return jsonify({"error": "unknown option"}), 404
    mongo_sync.resync_validator()
    return jsonify({"ok": True})
