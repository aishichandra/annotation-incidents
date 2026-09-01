"""The documents being coded, and one coder's evidence on them.

A quote's offsets only mean something against one document's text, so evidence —
the highlighted passages and the characteristics they justify — is stored per
document, per coder. The incident-level answers a document inherits are joined
back on when it is read, and are not stored here.
"""
from flask import Blueprint, jsonify, request

from config import ROLE_KEYS, clean_fields, current_coder
from doc_source import cell, markdown_no_title
from incidents import clear_signoff
from storage import (
    blank_incident_coding, doc_ann, incident_fields, incident_of,
    load_annotations, load_assignments, load_incident_coding, record_assignment,
    save_annotations, save_incident_coding,
)
import doc_source
import mongo_sync


bp = Blueprint("docs", __name__)


@bp.route("/api/docs")
def api_docs():
    """The shared document list; the quote count is the active coder's own."""
    store = load_annotations(current_coder())
    return jsonify([
        {"index": i, "title": cell(i, "title"),
         "n": len(doc_ann(store, doc_source.df["doc_key"].iloc[i])["quotes"])}
        for i in range(len(doc_source.df))
    ])


@bp.route("/api/doc/<int:i>")
def api_doc(i):
    """One document to code: its text, this coder's evidence for it, and the
    field answers it inherits from the incident it belongs to."""
    coder = current_coder()
    key = doc_source.df["doc_key"].iloc[i]
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


@bp.route("/api/doc/<int:i>/annotations", methods=["POST"])
def api_save(i):
    """Save one document as the active coder.

    The payload is what the document view holds, and each part goes to the home
    it belongs to: quotes and characteristics are evidence for *this document*,
    while the field answers describe the *incident* and are stored once against
    it. The incident id and title go to the shared assignment map, so every coder
    codes the same incidents."""
    coder = current_coder(strict=True)
    key = doc_source.df["doc_key"].iloc[i]
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
    # Notes belong to the incident but are edited from a document, and an incident
    # usually has several. So this merges rather than replaces: a role the payload
    # doesn't mention keeps what it had. Replacing meant any save from a sibling
    # document — which posts the notes *it* loaded, often none — silently wiped a
    # name typed on another. A role that *is* mentioned, with empty text, is the
    # coder actually clearing it.
    notes = dict(entry.get("notes") or {})
    for r, t in (body.get("notes") or {}).items():
        if r not in ROLE_KEYS:
            continue
        t = str(t or "").strip()
        if t:
            notes[r] = t
        else:
            notes.pop(r, None)
    entry["notes"] = notes
    save_incident_coding(inc_store, coder)

    mongo_sync.sync_to_mongo(i, key, rec, coder, inc_id)
    mongo_sync.sync_incident_coding_to_mongo(inc_id, coder, entry)
    clear_signoff(coder, inc_id)
    return jsonify({"ok": True, "coder": coder, "n": len(rec["quotes"])})
