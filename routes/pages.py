"""The page, the coder list, and the health of this process.

Everything here answers "what am I looking at?" rather than "what is coded?" —
the single HTML page the whole UI lives in, the coders a request may claim to
be, and what the running worker is actually attached to.
"""
import os

from flask import Blueprint, jsonify, render_template, request

from config import CODERS, add_coder, added_coders, check_rename, current_coder, remove_coder, rename_coder
import doc_source
import mongo_sync
import storage


bp = Blueprint("pages", __name__)


# ---------------------------------------------------------------- routes


@bp.route("/")
def index():
    return render_template("index.html")


@bp.route("/api/coders")
def api_coders():
    """Who can code, and who this request is being served as. The UI's coder
    picker is built from this."""
    return jsonify({"coders": CODERS, "current": current_coder(),
                    "removable": added_coders()})


@bp.route("/api/coders", methods=["POST"])
def api_add_coder():
    """Add a coder by name. Body: {name}. Their files and Mongo subtree are
    created by their first save, except that every incident another coder has
    set aside as "not an incident" starts out set aside for them too."""
    try:
        name = add_coder((request.get_json(silent=True) or {}).get("name"))
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    seeded = storage.seed_excluded_incidents(name)
    return jsonify({"ok": True, "name": name, "excluded_seeded": seeded,
                    "coders": CODERS})


@bp.route("/api/coders/<name>", methods=["PATCH"])
def api_rename_coder(name):
    """Rename a coder added from the UI. Body: {name: <new name>}. Their files
    and their Mongo subtree move with the name, so their coding is kept."""
    try:
        new = check_rename(name, (request.get_json(silent=True) or {}).get("name"))
        if new != name:
            moved = mongo_sync.rename_coder(name, new)
            rename_coder(name, new)
        else:
            moved = 0
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, "name": new, "incidents_moved": moved, "coders": CODERS})


@bp.route("/api/coders/<name>", methods=["DELETE"])
def api_remove_coder(name):
    """Delete a coder added from the UI, along with all their coding — their
    files on disk and their subtree in Mongo. Permanent."""
    if name not in added_coders():
        try:
            remove_coder(name)          # raises the right error
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
    removed = mongo_sync.delete_coder(name)
    remove_coder(name)
    return jsonify({"ok": True, "incidents_cleared": removed, "coders": CODERS})


@bp.route("/api/health")
def api_health():
    """What this process is actually attached to.

    `connect_mongo()` runs once, at import, so the database name is fixed when the
    worker starts. Changing MONGO_DB on the host therefore does nothing until the
    process restarts — and on a host that keeps serving the old worker there is no
    way to tell from the UI, since coding read out of the wrong database looks
    exactly like coding read out of the right one.

    `stale` is that mismatch: the environment names one database, the live handle
    another. It means restart the service, not change the variable again. No
    credentials are returned — the URI is never part of this."""
    live = mongo_sync.mongo_db.name if mongo_sync.mongo_db is not None else None
    want = os.environ.get("MONGO_DB", "incidents")
    return jsonify({
        "mongo_connected": live is not None,
        "mongo_db_live": live,
        "mongo_db_env": want,
        "stale": live is not None and live != want,
        "documents": len(doc_source.df),
        "coders": CODERS,
    })
