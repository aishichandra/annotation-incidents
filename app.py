"""
Structured incident-coding app with write-back to the dataframe.

Run:  ~/.pyenv/versions/3.10.3/bin/python app.py
Then open http://127.0.0.1:5001

- Reads zotero_docs.csv (from zotero_import.py), re-reading it whenever the file
  changes, so a fresh import shows up without restarting the app.
- The coding scheme (fields + options) lives in schema.json, which is created
  from DEFAULT_SCHEMA on first run and can be edited by hand or grown from the UI.
- Per article we store: each field's answer + comments, and a list of quotes
  (highlighted passages) tagged with the field they justify. Annotations are
  keyed by the stable Zotero item key.

Multiple coders (intercoder reliability)
---------------------------------------
Several coders code the *same* documents and the *same* incidents independently:

- Shared by everyone: the document list (zotero_docs.csv), the coding scheme
  (schema.json / vocab.json) and — crucially — which document belongs to which
  incident. That grouping lives in incident_assignments.json (doc_key ->
  incident_id + title) so every coder sees an identical set of incidents.
- Private per coder: every interpretive judgement. Each coder writes their own
  annotations.<coder>.json (evidence per document), incident_coding.<coder>.json
  (the incident's field answers + claim groups) and data_annotated.<coder>.csv.
  In MongoDB it all sits under `by_coder.<coder>` on the incident, so one $set
  can never reach another coder's work.

Where a judgement lives follows what it is about. A quote's offsets only mean
something against one document, so evidence — the quotes and the characteristics
they justify — is per document. Free text about the incident as a whole (its
aftermath, the inciting actor's name) is answered once against the incident.

Every controlled-vocabulary selection is a characteristic, including system and
developer. They are all roles, coded the same way, tagged the same way on a
quote, and dragged into a claim the same way. The inciting actor is the `actor`
role — only its *name* is a field, because a name is free text, not a code.

The active coder comes from `?coder=`, the `X-Coder` header, or the `coder`
cookie, and must be one of CODERS (set the CODERS env var, comma-separated).

Layout
------
This file is the HTTP layer only: the Flask app and its routes. The work they
call sits in modules that import in one direction, bottom-up:

    config.py       paths, coders, schema, file helpers   (imports nothing local)
    doc_source.py   zotero_docs.csv -> the `df` of documents
    storage.py      read/write the coding on disk         <-+ these two import
    mongo_sync.py   the optional MongoDB mirror           <-+ each other
    incidents.py    roll documents up into incident views
    app.py          routes (this file)

Two module-level values are rebound after import and must always be reached
through their module - `doc_source.df` and `mongo_sync.mongo_db` - never bound
by name, which would capture a stale copy.
"""
import os
from datetime import datetime, timezone

from flask import Flask, abort, jsonify, render_template, request

from config import (
    CODERS, INCIDENT_STATUSES, OPTIONAL_CLAIM_ROLES, REQUIRED_CLAIM_ROLES,
    ROLE_KEYS, clean_fields, current_coder, load_schema, save_schema,
)
from incidents import (
    _jsonable, _next_incident_id, aggregate_incidents, incident_completeness,
)
from incidents_vocab import (
    FIELD_VOCAB, ROLE_VOCAB, load_vocab, save_vocab,
)
from storage import (
    blank_incident_coding, doc_ann, has_coding, incident_fields, incident_of,
    load_annotations, load_assignments, load_incident_coding, record_assignment,
    save_annotations, save_assignments, save_incident_coding, _seed_shared_files,
)
import doc_source
import mongo_sync
from doc_source import cell, markdown_no_title, refresh_docs


app = Flask(__name__)


@app.before_request
def _refresh_before_request() -> None:
    refresh_docs()


refresh_docs()

# ---------------------------------------------------------------- routes


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/schema")
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


@app.route("/api/schema/option", methods=["POST"])
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


@app.route("/api/schema/role_option", methods=["POST"])
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


@app.route("/api/coders")
def api_coders():
    """Who can code, and who this request is being served as. The UI's coder
    picker is built from this."""
    return jsonify({"coders": CODERS, "current": current_coder()})


@app.route("/api/incidents")
def api_incidents():
    """One incident card each, with fields, pooled characteristics, and groups —
    all as coded by the active coder. Every field is returned even when empty so
    the card can render "No data"."""
    coder = current_coder()
    incidents, field_defs, role_defs = aggregate_incidents(coder)
    display_fields = [{"key": f["key"], "label": f["label"]}
                      for f in field_defs if f["key"] not in ("incident_id", "incident_title")]
    roles_meta = [{"role": r["role"], "label": r["label"]} for r in role_defs]
    ordered = sorted(incidents.values(), key=lambda g: g["incident_id"])
    return jsonify({"incidents": ordered, "fields": display_fields,
                    "roles": roles_meta, "coder": coder, "coders": CODERS})


@app.route("/api/docs")
def api_docs():
    """The shared document list; the quote count is the active coder's own."""
    store = load_annotations(current_coder())
    return jsonify([
        {"index": i, "title": cell(i, "title"),
         "n": len(doc_ann(store, doc_source.df["doc_key"].iloc[i])["quotes"])}
        for i in range(len(doc_source.df))
    ])


@app.route("/api/doc/<int:i>")
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


@app.route("/api/doc/<int:i>/annotations", methods=["POST"])
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


@app.route("/api/incident_ids")
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


@app.route("/api/incident/<path:inc_id>/json")
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
             "documents": [{"doc_id": d["doc_key"], "url": d["url"], "title": d["title"]}
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


@app.route("/api/incident/<path:inc_id>/groups", methods=["POST"])
def api_save_groups(inc_id):
    """Persist the active coder's card-view claim groupings for one incident.
    Body: {groups:[…]}. A group is one actor context — {id, actor, system,
    developer, claims:[{id, harm, harmed_parties:[], factors:[]}]} — where
    actor, system, developer and harm are single values, and harmed_parties
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
    mongo_sync.sync_incident_coding_to_mongo(inc_id, coder, entry)
    clear_signoff(coder, inc_id)
    return jsonify({"ok": True, "coder": coder, "groups": len(groups),
                    "synced": mongo_sync.mongo_db is not None})



def clear_signoff(coder: str, inc_id: str) -> None:
    """Withdraw a sign-off when the coding no longer supports it.

    A sign-off says "my reading of this incident is finished". An edit that
    leaves the incident complete doesn't contradict that, so the flag stands —
    withdrawing on *any* edit would mean every autosave while coding a member
    document silently un-signed the incident, including edits the check never
    reads, like aftermath text. An edit that breaks completeness does contradict
    it, so that one withdraws and the coder has to look again.

    The `status` check comes first because it makes the common case free: with no
    sign-off to protect there is nothing to do, and the aggregate below — which
    walks every document — never runs."""
    store = load_incident_coding(coder)
    entry = store.get(inc_id)
    # Only a sign-off is a claim about the coding being finished, so only it can
    # be falsified by an edit. "not_an_incident" is a judgement about the
    # material — coding more of it doesn't make the thing an incident.
    if not entry or entry.get("status") != "complete":
        return
    incidents, _, _ = aggregate_incidents(coder)
    inc = incidents.get(inc_id)
    if inc is not None and incident_completeness(inc)["ok"]:
        return
    entry["status"] = ""
    entry["completed_at"] = ""
    save_incident_coding(store, coder)
    mongo_sync.sync_incident_coding_to_mongo(inc_id, coder, entry)


@app.route("/api/incident/<path:inc_id>/status", methods=["POST"])
def api_set_status(inc_id):
    """Record this coder's judgement about an incident as a whole.
    Body: {status: "" | "complete" | "not_an_incident", reason?: "…"}.

    One route for every judgement, because they are the same kind of thing —
    a coder saying where this incident stands for them — and differ only in what
    has to be true first:

      "complete"         gated. Recomputed here from the stored coding rather
                         than taken from the request, so a card rendered before
                         the coding changed can't sign off work that no longer
                         qualifies. A refusal is 409, naming what is missing.
      "not_an_incident"  ungated. Deciding the material isn't an incident is a
                         finding in its own right, and is usually reached long
                         before the coding could ever be complete. `reason` is
                         kept alongside it.
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
    entry["excluded_reason"] = (str(body.get("reason") or "").strip()
                                if status == "not_an_incident" else "")
    save_incident_coding(store, coder)
    mongo_sync.sync_incident_coding_to_mongo(inc_id, coder, entry)
    return jsonify({"ok": True, "coder": coder, "incident_id": inc_id,
                    "status": status, "completed_at": entry["completed_at"],
                    "excluded_reason": entry["excluded_reason"],
                    "synced": mongo_sync.mongo_db is not None})


@app.route("/api/incident/<path:inc_id>/comment", methods=["POST"])
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
    mongo_sync.sync_incident_coding_to_mongo(inc_id, coder, entry)
    return jsonify({"ok": True, "coder": coder, "comment": comment,
                    "synced": mongo_sync.mongo_db is not None})


@app.route("/api/pull", methods=["POST"])
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


@app.route("/api/push", methods=["POST"])
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


_seed_shared_files()

if __name__ == "__main__":
    # Local dev entrypoint. In production, gunicorn imports `app:app` (see Procfile)
    # and this block is skipped. PORT is provided by the host; default to 5001 local.
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=bool(os.environ.get("FLASK_DEBUG")))
