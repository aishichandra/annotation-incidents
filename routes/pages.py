"""The page, the coder list, and the health of this process.

Everything here answers "what am I looking at?" rather than "what is coded?" —
the single HTML page the whole UI lives in, the coders a request may claim to
be, and what the running worker is actually attached to.
"""
import os

from flask import Blueprint, jsonify, render_template

from config import CODERS, current_coder
import doc_source
import mongo_sync


bp = Blueprint("pages", __name__)


# ---------------------------------------------------------------- routes


@bp.route("/")
def index():
    return render_template("index.html")


@bp.route("/api/coders")
def api_coders():
    """Who can code, and who this request is being served as. The UI's coder
    picker is built from this."""
    return jsonify({"coders": CODERS, "current": current_coder()})


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
