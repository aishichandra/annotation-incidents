"""Incidents: the cards, and the judgements a coder records on one.

An incident is the set of documents sharing an incident_id, so which incidents
exist is shared by everyone. Everything a coder says *about* one — its field
answers, its claim groups, its comment, whether it is signed off, whether they
are unsure of it — is theirs alone, written to their own files and to
by_coder.<coder> in Mongo, where one $set can never reach another coder's work.
"""
from datetime import datetime, timezone

from flask import Blueprint, abort, jsonify, request

from config import CODERS, INCIDENT_STATUSES, current_coder, load_schema
from incidents import (
    _jsonable, _next_incident_id, aggregate_incidents, clear_signoff,
    incident_completeness, incident_sort_key,
)
from incidents_vocab import FIELD_VOCAB, load_vocab
from storage import (
    blank_incident_coding, doc_ann, has_coding, load_annotations,
    load_assignments, load_incident_coding, save_incident_coding,
)
import mongo_sync


bp = Blueprint("incidents", __name__)


@bp.route("/api/incidents")
def api_incidents():
    """One incident card each, with fields, pooled characteristics, and groups —
    all as coded by the active coder. Every field is returned even when empty so
    the card can render "No data"."""
    coder = current_coder()
    incidents, field_defs, role_defs = aggregate_incidents(coder)

    def display_field(f):
        """What a card needs for one field. Normally just its name — the answers
        come with the incident. A card-only field is *answered* on the card, so
        it also carries the vocabulary its menu offers, the same overlay the
        document sidebar gets for a characteristic."""
        out = {"key": f["key"], "label": f["label"]}
        if f.get("card_only"):
            out.update({"card_only": True, "type": f.get("type", "multi"),
                        "control": f.get("control", "menu"),
                        "options": f.get("options") or [],
                        "groups": f.get("groups") or None,
                        "definitions": f.get("definitions") or None})
        return out

    display_fields = [display_field(f) for f in field_defs
                      if f["key"] not in ("incident_id", "incident_title")]
    roles_meta = [{"role": r["role"], "label": r["label"]} for r in role_defs]
    ordered = sorted(incidents.values(),
                     key=lambda g: incident_sort_key(g["incident_id"]))
    return jsonify({"incidents": ordered, "fields": display_fields,
                    "roles": roles_meta, "coder": coder, "coders": CODERS})


@bp.route("/api/incident_ids")
def api_incident_ids():
    """Existing incident IDs (to connect an article to another), a title for each
    (so a shared ID can auto-fill the title), plus a suggested new ID.

    Read from the shared assignment map, so every coder is offered the same
    incidents no matter who first filed a document under one."""
    ids, titles = set(), {}
    for entry in load_assignments().values():
        iid = str(entry.get("incident_id") or "").strip()
        if not iid:
            continue
        ids.add(iid)
        title = str(entry.get("incident_title") or "").strip()
        if title and iid not in titles:
            titles[iid] = title
    return jsonify({"ids": sorted(ids), "titles": titles, "next": _next_incident_id(sorted(ids))})


@bp.route("/api/incident/<path:inc_id>/json")
def api_incident_json(inc_id):
    """The incident as it is stored, for the card's raw-JSON view.

    Narrowed to the active coder's own subtree — the card shows one coder's
    reading, and coders stay blind to each other's judgements while coding.

    Only the quote *text* is dropped, replaced by a count. It is the bulk of the
    record (one incident runs to ~24k characters of it) and its offsets only mean
    something against the document view, where it stays readable. Everything that
    describes the coding itself is kept: the incident's field answers, the claim
    groups, and each document's selected characteristics — which live in the same
    subtree as the quotes, so dropping that subtree wholesale would take the
    characteristics with it.

    Mongo's document is the answer when it's there, since that's the record the
    analysis reads. Without it — Mongo not configured, or an incident nobody has
    synced yet — fall back to the same structure assembled from the local files,
    and say which one is being shown so the two are never confused."""
    coder = current_coder()

    def strip_quotes(documents):
        out = {}
        for key, ev in (documents or {}).items():
            ev = dict(ev or {})
            ev["n_quotes"] = len(ev.pop("quotes", None) or [])
            out[key] = ev
        return out

    def narrow(doc):
        slot = dict((doc.get("by_coder") or {}).get(coder) or {})
        slot["documents"] = strip_quotes(slot.get("documents"))
        return {**{k: v for k, v in doc.items() if k != "by_coder"},
                "by_coder": {coder: slot}}

    if mongo_sync.mongo_db is not None:
        try:
            doc = mongo_sync.mongo_db.incidents.find_one({"_id": inc_id})
            if doc:
                return jsonify({"source": f"mongodb — {coder} only, quote text replaced by n_quotes",
                                "incident": _jsonable(narrow(doc))})
        except Exception as e:
            print(f"[mongo] json read failed for {inc_id} ({e.__class__.__name__}: {e})")
    incidents, _, _ = aggregate_incidents(coder)
    g = incidents.get(inc_id)
    if g is None:
        abort(404, f"no incident {inc_id!r}")
    inc = load_incident_coding(coder).get(inc_id) or {}
    ann = load_annotations(coder)
    local = {"_id": inc_id, "title": g.get("title", ""),
             "documents": [{"doc_id": d["doc_key"], "url": d["url"], "title": d["title"],
                            "date": d["date"]}
                           for d in g.get("documents", [])],
             "by_coder": {coder: {
                 "fields": inc.get("fields") or {},
                 "groups": inc.get("groups") or [],
                 "comment": inc.get("comment") or "",
                 "documents": strip_quotes(
                     {d["doc_key"]: doc_ann(ann, d["doc_key"])
                      for d in g.get("documents", []) if has_coding(ann.get(d["doc_key"]))})}}}
    return jsonify({"source": f"local files (not in MongoDB yet) — {coder} only, "
                              "quote text replaced by n_quotes",
                    "incident": _jsonable(local)})


@bp.route("/api/incident/<path:inc_id>/groups", methods=["POST"])
def api_save_groups(inc_id):
    """Persist the active coder's card-view claim groupings for one incident.
    Body: {groups:[…]}. A group is one actor context — {id, actor, systems:[],
    developers:[], claims:[{id, harm, harmed_parties:[], factors:[]}]} — where
    actor and harm are single values, and systems, developers, harmed_parties
    and factors are lists. This is the single
    home for links now that the document view codes characteristics flat; each coder
    links their own claims, so the groupings are per coder.

    Like a document save, this writes both places: the coder's own JSON file and —
    when Mongo is connected — `groups_by_coder.<coder>` on the incident. Push stays
    available for a bulk re-send, but is no longer what claims depend on."""
    coder = current_coder(strict=True)
    body = request.get_json(force=True)
    groups = body.get("groups", [])
    store = load_incident_coding(coder)
    entry = store.setdefault(inc_id, blank_incident_coding())
    entry["groups"] = groups
    save_incident_coding(store, coder)
    synced = mongo_sync.sync_incident_coding_to_mongo(inc_id, coder, entry)
    clear_signoff(coder, inc_id)
    return jsonify({"ok": True, "coder": coder, "groups": len(groups),
                    "synced": synced})


@bp.route("/api/incident/<path:inc_id>/status", methods=["POST"])
def api_set_status(inc_id):
    """Record this coder's judgement about an incident as a whole.
    Body: {status: "" | "complete" | "not_an_incident"}.

    One route for every judgement, because they are the same kind of thing —
    a coder saying where this incident stands for them — and differ only in what
    has to be true first:

      "complete"         gated. Recomputed here from the stored coding rather
                         than taken from the request, so a card rendered before
                         the coding changed can't sign off work that no longer
                         qualifies. A refusal is 409, naming what is missing.
      "not_an_incident"  ungated. Deciding the material isn't an incident is a
                         finding in its own right, and is usually reached long
                         before the coding could ever be complete. Why it went
                         belongs in the card's comment, like any other remark
                         about the incident as a whole.
      ""                 back to work in progress.

    Per coder, like every other judgement. One coder excluding an incident
    leaves the other's coding of it untouched — and that disagreement is data,
    not a conflict to resolve here."""
    coder = current_coder(strict=True)
    body = request.get_json(force=True) or {}
    status = str(body.get("status") or "")
    if status not in INCIDENT_STATUSES:
        abort(400, f"unknown status {status!r} (expected one of {INCIDENT_STATUSES})")

    incidents, _, _ = aggregate_incidents(coder)
    inc = incidents.get(inc_id)
    if inc is None:
        abort(404, f"unknown incident {inc_id!r}")

    if status == "complete":
        state = incident_completeness(inc)
        if not state["ok"]:
            return jsonify({"ok": False, "error": "incomplete",
                            "missing": state["missing"]}), 409

    store = load_incident_coding(coder)
    entry = store.setdefault(inc_id, blank_incident_coding())
    entry["status"] = status
    # When the judgement was last set — a sign-off date, or when it was excluded.
    entry["completed_at"] = (datetime.now(timezone.utc).isoformat(timespec="seconds")
                             if status else "")
    save_incident_coding(store, coder)
    synced = mongo_sync.sync_incident_coding_to_mongo(inc_id, coder, entry)

    # A sign-off says this reading is final, so make sure Mongo holds all of it
    # rather than only the flag. Every save already syncs, but a save whose sync
    # failed — Atlas briefly unreachable, a validator rejection — would leave the
    # analysis copy short of the very evidence the sign-off is attesting to. This
    # is one upsert per member document, and it is the moment worth spending them:
    # it makes "complete" mean complete in Mongo too, and `synced` says whether it
    # actually is.
    if status == "complete":
        ann = load_annotations(coder)
        for d in inc.get("documents") or []:
            if not mongo_sync.sync_to_mongo(d["index"], d["doc_key"],
                                            doc_ann(ann, d["doc_key"]), coder, inc_id):
                synced = False
        mongo_sync.invalidate_mongo_cache()

    return jsonify({"ok": True, "coder": coder, "incident_id": inc_id,
                    "status": status, "completed_at": entry["completed_at"],
                    "documents": len(inc.get("documents") or []) if status == "complete" else 0,
                    # `synced` is whether the write landed; `mongo` whether there
                    # was anywhere to write to. Without both, a coder running
                    # offline on purpose is told a sync "failed".
                    "mongo": mongo_sync.mongo_db is not None,
                    "synced": synced})


@bp.route("/api/incident/<path:inc_id>/comment", methods=["POST"])
def api_save_comment(inc_id):
    """Persist the active coder's free-text comment on one incident as a whole.
    Body: {comment: "…"}.

    This is the place for what belongs to the incident but to none of its parts —
    why a call was a close one, a question for the team, what a coder would want a
    reader of this coding to know. Field comments justify one answer and role notes
    name one characteristic; neither has room for a remark about the whole reading.

    Per coder, like every other judgement: each coder comments on their own copy,
    so a comment can't leak one coder's reading into another's while they code.
    Written to the coder's JSON file and, when Mongo is connected, to
    `by_coder.<coder>.comment` on the incident."""
    coder = current_coder(strict=True)
    body = request.get_json(force=True) or {}
    comment = str(body.get("comment") or "").strip()
    store = load_incident_coding(coder)
    entry = store.setdefault(inc_id, blank_incident_coding())
    entry["comment"] = comment
    save_incident_coding(store, coder)
    synced = mongo_sync.sync_incident_coding_to_mongo(inc_id, coder, entry)
    return jsonify({"ok": True, "coder": coder, "comment": comment,
                    "synced": synced})


@bp.route("/api/incident/<path:inc_id>/flag", methods=["POST"])
def api_set_flag(inc_id):
    """Flag this coder's own reading of an incident as one they are unsure of.
    Body: {flagged: true|false}.

    Deliberately not a fourth `status`. A status is exclusive — an incident is
    either being worked on, signed off, or set aside — but uncertainty cuts
    across all three: the readings most worth a second pair of eyes are often the
    ones a coder has finished and still doubts. So it is its own flag, it can be
    raised and cleared at any point, and it gates nothing.

    What is uncertain goes in the card's comment box, which is already the place
    for a remark about the incident as a whole — a flag says "look at this", the
    comment says why, and splitting prose across two boxes would only make the
    reason harder to find.

    Per coder, like every other judgement: one coder's doubt is not the other's,
    and a disagreement about how solid a reading is is itself worth recording."""
    coder = current_coder(strict=True)
    body = request.get_json(force=True) or {}
    flagged = bool(body.get("flagged"))
    store = load_incident_coding(coder)
    entry = store.setdefault(inc_id, blank_incident_coding())
    entry["flagged"] = flagged
    save_incident_coding(store, coder)
    synced = mongo_sync.sync_incident_coding_to_mongo(inc_id, coder, entry)
    return jsonify({"ok": True, "coder": coder, "flagged": flagged, "synced": synced})


@bp.route("/api/incident/<path:inc_id>/field", methods=["POST"])
def api_save_incident_field(inc_id):
    """Answer one of the incident's own controlled fields from its card.
    Body: {key, answer: [...]}.

    Geography and Translated describe the incident, not any one of its
    documents: there is no passage to highlight for "this happened in Kenya" and
    no claim to drag it into, so they are answered once, here, rather than coded
    per document and pooled. Stored in this coder's `fields` map like any other
    incident-level answer, and synced to `by_coder.<coder>.fields`.

    Values are checked against the vocabulary rather than trusted. The card is
    the only place they are set, so a menu built before a code was renamed in the
    Codebook is the one way an answer could name something the scheme no longer
    offers. An empty answer removes the field rather than storing a blank — the
    same rule `clean_fields` applies everywhere else."""
    coder = current_coder(strict=True)
    body = request.get_json(force=True) or {}
    key = body.get("key")
    vkey = FIELD_VOCAB.get(key)
    if not vkey:
        return jsonify({"error": "unknown field"}), 404
    # A menu sends a list, a toggle sends the one value it was switched to (or
    # nothing, switched off). Both arrive here as a list and are stored as what
    # the field is: a single-valued field keeps a string, so nothing downstream
    # has to unwrap a list of one to ask what an incident was answered.
    raw = body.get("answer")
    raw = raw if isinstance(raw, list) else ([raw] if raw else [])
    answer, seen = [], set()
    for v in raw:
        v = str(v).strip()
        if v and v not in seen:
            seen.add(v)
            answer.append(v)
    allowed = set(load_vocab().get(vkey) or [])
    stale = [v for v in answer if v not in allowed]
    if stale:
        return jsonify({"error": "unknown value", "values": stale}), 400
    single = next((f.get("type") == "single" for f in load_schema()["fields"]
                   if f["key"] == key), False)
    if single:
        answer = answer[:1]
    store = load_incident_coding(coder)
    entry = store.setdefault(inc_id, blank_incident_coding())
    fields = entry.setdefault("fields", {})
    if answer:
        fields.setdefault(key, {})["answer"] = answer[0] if single else answer
    else:
        fields.pop(key, None)
    save_incident_coding(store, coder)
    synced = mongo_sync.sync_incident_coding_to_mongo(inc_id, coder, entry)
    return jsonify({"ok": True, "coder": coder, "key": key, "answer": answer,
                    "synced": synced})
