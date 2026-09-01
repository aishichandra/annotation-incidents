"""Pull and Push — the two deliberate directions between disk and Atlas.

Every save already syncs on its own, so neither of these is how work normally
reaches Mongo. They exist for the moments when one copy has to win outright:
Pull rebuilds this coder's local files from Atlas, Push resends everything they
have. Both act on the active coder alone.
"""
from flask import Blueprint, jsonify

from config import current_coder
from incidents import aggregate_incidents
from storage import (
    blank_incident_coding, doc_ann, incident_of, load_annotations,
    load_assignments, load_incident_coding, save_annotations, save_assignments,
    save_incident_coding,
)
import doc_source
import mongo_sync


bp = Blueprint("sync", __name__)


@bp.route("/api/pull", methods=["POST"])
def api_pull():
    """Pull the active coder's coding from Mongo into their local files.

    Manual bring-back for the write-only mirror: wherever Mongo and the local file
    both hold something, Mongo's copy of *this coder's* coding wins; other coders'
    files are untouched. Anything that exists only locally (not yet synced) is
    kept, so a pull never loses un-synced work. Per-document evidence, incident
    field answers, claim groups and the shared assignments all come back."""
    if mongo_sync.mongo_db is None:
        return jsonify({"ok": False, "error": "MongoDB not connected"}), 503
    coder = current_coder(strict=True)
    mongo_sync.invalidate_mongo_cache()      # an explicit pull must not read a stale snapshot
    remote = mongo_sync.store_from_mongo(coder)
    store = load_annotations(coder)
    store.update(remote)          # Mongo wins for shared keys; local-only kept
    assignments = load_assignments()
    assignments.update(mongo_sync.assignments_from_mongo())
    save_assignments(assignments)
    save_annotations(store, coder)

    inc_store = load_incident_coding(coder)
    for inc_id, entry in mongo_sync.incident_coding_from_mongo(coder).items():
        inc_store[inc_id] = {**blank_incident_coding(), **entry}
    save_incident_coding(inc_store, coder)
    return jsonify({"ok": True, "coder": coder, "pulled": len(remote), "total": len(store),
                    "incidents": len(inc_store)})


@bp.route("/api/push", methods=["POST"])
def api_push():
    """Push the active coder's local work up to Mongo (the inverse of /api/pull).

    Per document: this coder's evidence is upserted into
    `by_coder.<coder>.documents.<key>` via the same path a save uses, leaving the
    other coders' readings in place. Per incident: this coder's field answers,
    claim groups and comment go to `by_coder.<coder>`. The pooled characteristic
    and field lists a card shows are intentionally *not* stored — they're derived
    (see aggregate_incidents). Non-fatal per item."""
    if mongo_sync.mongo_db is None:
        return jsonify({"ok": False, "error": "MongoDB not connected"}), 503
    coder = current_coder(strict=True)
    store = load_annotations(coder)
    assignments = load_assignments()
    docs_pushed = 0
    for i in range(len(doc_source.df)):
        key = doc_source.df["doc_key"].iloc[i]
        try:
            mongo_sync.sync_to_mongo(i, key, doc_ann(store, key), coder,
                                     incident_of(key, assignments))
            docs_pushed += 1
        except Exception as e:
            print(f"[mongo] push failed for {key} ({e.__class__.__name__}: {e})")

    incidents, _, _ = aggregate_incidents(coder)
    inc_store = load_incident_coding(coder)
    incidents_pushed, groups_pushed = 0, 0
    for inc_id, g in incidents.items():
        try:
            entry = inc_store.get(inc_id) or blank_incident_coding()
            mongo_sync.sync_incident_coding_to_mongo(inc_id, coder, {**entry, "groups": g["groups"]})
            incidents_pushed += 1
            groups_pushed += len(g["groups"])
        except Exception as e:
            print(f"[mongo] incident push failed for {inc_id} ({e.__class__.__name__}: {e})")
    mongo_sync.invalidate_mongo_cache()
    return jsonify({"ok": True, "coder": coder, "documents": docs_pushed,
                    "incidents": incidents_pushed, "groups": groups_pushed})
