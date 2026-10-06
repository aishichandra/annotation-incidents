"""The Agreement tab: Cohen's kappa between two coders, per category and code."""
from flask import Blueprint, abort, jsonify, request

import agreement
from config import CODERS


bp = Blueprint("agreement", __name__)


@bp.route("/api/agreement")
def api_agreement():
    """Query: `a`, `b` (two different coders; default the first two) and `scope`
    ("coded" — every incident both have coded — or "complete" — both signed off).
    Reads both coders' coding including what Mongo holds for them, so it matches
    what each sees in the app."""
    a = request.args.get("a") or (CODERS[0] if CODERS else "")
    b = request.args.get("b") or next((c for c in CODERS if c != a), "")
    if a not in CODERS or b not in CODERS:
        abort(400, f"unknown coder (expected two of {', '.join(CODERS)})")
    if a == b:
        abort(400, "pick two different coders")
    return jsonify(agreement.compute(a, b, request.args.get("scope") or "coded"))
