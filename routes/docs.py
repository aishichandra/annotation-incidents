"""The documents being coded, and one coder's evidence on them.

A quote's offsets only mean something against one document's text, so evidence —
the highlighted passages and the characteristics they justify — is stored per
document, per coder. The incident-level answers a document inherits are joined
back on when it is read, and are not stored here.
"""
from flask import Blueprint, abort, jsonify, request

from config import ROLE_KEYS, clean_fields, current_coder
from doc_source import cell, markdown_no_title
from incidents import clear_signoff
from storage import (
    blank_incident_coding, doc_ann, geo_pairs, incident_fields, incident_of,
    load_annotations, load_assignments, load_incident_coding, record_assignment,
    save_annotations, save_incident_coding, sync_doc_geo,
)
import doc_dates
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


@bp.route("/api/docdate/<key>", methods=["POST"])
def api_set_doc_date(key):
    """Fill in (or correct, or clear) the date of a document Zotero has none for.
    Body: {date: "YYYY-MM-DD" | ""}. Shared by every coder, since when an article
    was published is a fact about it. A date Zotero supplied is refused (409): it
    would be overwritten by the next import."""
    rows = doc_source.df[doc_source.df["doc_key"] == key]
    if rows.empty:
        abort(404, f"unknown document {key!r}")
    if str(rows.iloc[0].get("date") or "").strip() not in ("", "nan"):
        return jsonify({"ok": False, "error": "this document's date comes from Zotero"}), 409
    date = str((request.get_json(force=True) or {}).get("date") or "").strip()
    if not doc_dates.valid(date):
        return jsonify({"ok": False, "error": "use a real date as YYYY-MM-DD"}), 400
    doc_dates.save(key, date)
    synced = mongo_sync.set_doc_date(key, date)
    return jsonify({"ok": True, "key": key, "date": date, "synced": synced})


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

    # Read before the assignment below can move the document to another incident:
    # its Geography highlights have to leave the one and arrive at the other.
    old_inc = incident_of(key)
    record_assignment(key, posted_fields)
    assignments = load_assignments()
    inc_id = incident_of(key, assignments)

    store = load_annotations(coder)
    geo_before = geo_pairs(store.get(key))
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
    # Where a highlighted place applies is said in the document but held on the
    # incident, beside the places dropped onto a characteristic from its card —
    # see storage.sync_doc_geo. It can touch an incident other than this one, when
    # the document has just been moved out of it.
    geo_touched = sync_doc_geo(inc_store, store, assignments, key, old_inc, inc_id,
                               geo_before, geo_pairs(rec))
    save_incident_coding(inc_store, coder)

    mongo_sync.push_documents([(i, key, rec, coder, inc_id)])
    for touched_id in geo_touched | {inc_id}:
        mongo_sync.sync_incident_coding_to_mongo(touched_id, coder, inc_store[touched_id])
    clear_signoff(coder, inc_id)
    return jsonify({"ok": True, "coder": coder, "n": len(rec["quotes"])})
