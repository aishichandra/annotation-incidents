"""Reading and writing the coding itself - the source of truth on disk.

One file per coder, plus the shared doc -> incident map. Each loader overlays
anything Mongo holds that the local file lacks, so work done on another machine
appears without a Pull, while a local edit is never overwritten by a stale
remote copy.

  shape       has_coding(), doc_ann(), answer_text()
  evidence    load_annotations(), save_annotations()  (+ the flat CSV mirror)
  incident    load_incident_coding(), save_incident_coding()
  shared map  load_assignments(), record_assignment(), incident_of()
  vocabulary  role_value_usage(), role_value_incidents(), rename_role_value()
"""
import json

from config import (
    ASSIGNMENTS_JSON, CODERS, LEGACY_ANNOTATIONS_JSON, LEGACY_CODER, ROLE_KEYS,
    _atomic_write, _read_json, annotated_csv_path, annotations_path,
    incident_coding_path, load_schema,
)
import doc_source
import mongo_sync


def has_coding(rec) -> bool:
    """Has this coder actually put something on the document — a highlight or a
    characteristic? Field answers can't count: they belong to the incident, so
    they'd mark every coder as having coded every document in it."""
    if not isinstance(rec, dict):
        return False
    return bool(rec.get("quotes")) or any((rec.get("roles") or {}).values())


def doc_ann(store, key):
    """One document's evidence for one coder: {"quotes": [], "roles": {}}.

    `roles` holds the flat per-document selections: {actor:[], harm:[], factor:[],
    harmed_party:[]}. Their highlighted evidence lives in quotes tagged with the
    role, and the two are reconciled here — a value justified by a role-tagged
    highlight counts as selected even if the stored roles object missed it. Each
    quote's stale `claim` reference is dropped; linking lives in the card view.

    Field answers are deliberately absent: they belong to the incident, so the
    document view fetches them with `incident_fields`."""
    a = store.get(key)
    if not isinstance(a, dict):
        a = {}
    quotes = a.get("quotes", []) or []
    roles = a.get("roles")
    roles = ({rk: list(roles.get(rk) or []) for rk in ROLE_KEYS}
             if isinstance(roles, dict) else {rk: [] for rk in ROLE_KEYS})
    for q in quotes:
        if not isinstance(q, dict):
            continue
        q.pop("claim", None)
        r, v = q.get("role"), q.get("value")
        if r in roles and v and v not in roles[r]:
            roles[r].append(v)
    return {"quotes": quotes, "roles": roles}


def answer_text(field_ann):
    """Flatten a field's answer to a string for the CSV."""
    ans = field_ann.get("answer")
    if isinstance(ans, list):
        return " | ".join(ans)
    return ans or ""


def load_annotations(coder: str) -> dict:
    """One coder's evidence per document: {doc_key: {"quotes": [], "roles": {}}}.

    Field answers are not here — they belong to the incident, not to one of its
    documents (see `load_incident_coding`). Quotes are, because their offsets
    only mean anything against a particular document's text.

    The local file, plus anything Mongo holds for this coder that the local file
    has no coding for. That fill-in is what makes the app reflect Mongo without
    anyone pressing Pull: work saved from another machine — or from a deploy
    whose filesystem has since been rebuilt, as on Railway — shows up on its own.

    A document the local file already has coding for is left alone, so a save
    whose Mongo sync failed can never be silently overwritten by Mongo's older
    copy. Use /api/pull for the deliberate "Mongo wins outright" direction."""
    store = _read_json(annotations_path(coder))
    if mongo_sync.mongo_db is None:
        return store
    remote = mongo_sync._mongo_snapshot(f"ann:{coder}",
                                        lambda: mongo_sync.store_from_mongo(coder))
    for key, coding in remote.items():
        if not has_coding(store.get(key)):
            store[key] = coding
    return store


def save_annotations_only(store: dict, coder: str) -> None:
    _atomic_write(annotations_path(coder), json.dumps(store, indent=2, ensure_ascii=False))


def save_annotations(store: dict, coder: str) -> None:
    """Write one coder's per-document evidence + their flattened CSV mirror.

    The mirror is one row per document, so the incident-level answers a document
    inherits are joined back on for it — they're what the columns mean, even
    though they're no longer stored per document."""
    save_annotations_only(store, coder)
    schema = load_schema()
    assignments = load_assignments()
    inc_store = load_incident_coding(coder)
    out = doc_source.df.copy()
    anns = {k: doc_ann(store, k) for k in doc_source.df["doc_key"]}
    fields_for = {k: incident_fields(coder, incident_of(k, assignments), assignments, inc_store)
                  for k in doc_source.df["doc_key"]}
    for f in schema["fields"]:
        out[f["key"]] = [answer_text(fields_for[k].get(f["key"], {}))
                         for k in doc_source.df["doc_key"]]
    out["coder"] = coder
    out["annotations_json"] = [json.dumps(anns[k], ensure_ascii=False)
                               for k in doc_source.df["doc_key"]]
    out.to_csv(annotated_csv_path(coder), index=False)


def blank_incident_coding() -> dict:
    """Every part of one coder's incident-level coding. `status` is their
    judgement about the incident as a whole (see INCIDENT_STATUSES) and
    `completed_at` when they last set it. Both belong
    here for the reason in load_incident_coding: a part left out of this dict is
    read from Mongo and then dropped on the next write."""
    return {"fields": {}, "notes": {}, "groups": [], "comment": "",
            "status": "", "completed_at": ""}


def load_incident_coding(coder: str) -> dict:
    """One coder's incident-level coding, keyed by incident id:
    {inc_id: {"fields": {...}, "notes": {...}, "groups": [...], "comment": "..."}}.

    `fields` are the incident's own free-text answers — aftermath — answered once
    for the incident rather than repeated on each of its documents. `notes` is the
    free text belonging to a characteristic (the inciting actor's name), keyed by
    role. `groups` are the claim groups built in the card view. `comment` is the
    coder's own remark about the incident as a whole — uncertainty, a question for
    the team, anything that belongs to no single field or characteristic.

    Filled in from Mongo per part, the same way `load_annotations` works: a part
    the local file already has is left alone, so a local edit can't be undone by
    a stale remote copy, while work done elsewhere still appears without a Pull.
    Every part must be listed here — one left out is fetched from Mongo and then
    dropped, and the next Push writes that emptiness back over the good copy."""
    store = {k: {**blank_incident_coding(), **(v or {})}
             for k, v in _read_json(incident_coding_path(coder)).items()}
    if mongo_sync.mongo_db is None:
        return store
    remote = mongo_sync._mongo_snapshot(
        f"inc:{coder}", lambda: mongo_sync.incident_coding_from_mongo(coder))
    for inc_id, entry in remote.items():
        local = store.setdefault(inc_id, blank_incident_coding())
        for part in blank_incident_coding():
            if not local.get(part) and entry.get(part):
                local[part] = entry[part]
    return store


def save_incident_coding(store: dict, coder: str) -> None:
    # Incidents a coder has neither answered nor linked anything on aren't worth
    # a line in the file.
    lean = {k: v for k, v in store.items()
            if v.get("fields") or v.get("groups") or v.get("notes")
            or v.get("comment") or v.get("status")}
    _atomic_write(incident_coding_path(coder), json.dumps(lean, indent=2, ensure_ascii=False))


def load_assignments() -> dict:
    """The shared doc -> incident mapping every coder codes against.
    Shape: {doc_key: {"incident_id": str, "incident_title": str}}.

    Documents Mongo has grouped but the local file hasn't heard about are filled
    in, so a grouping made elsewhere reaches this app on its own. A local entry
    always wins — regrouping here isn't undone by a stale remote copy."""
    store = _read_json(ASSIGNMENTS_JSON)
    if mongo_sync.mongo_db is None:
        return store
    remote = mongo_sync._mongo_snapshot("assignments",
                                        mongo_sync.assignments_from_mongo)
    for key, entry in remote.items():
        if entry.get("incident_id"):
            store.setdefault(key, entry)
    return store


def save_assignments(store: dict) -> None:
    _atomic_write(ASSIGNMENTS_JSON, json.dumps(store, indent=2, ensure_ascii=False))


def record_assignment(key, fields) -> None:
    """Publish a save's incident_id / incident_title to the shared mapping.

    Incident membership is deliberately *not* private to a coder: whoever files a
    document under an incident moves it for everyone, so all coders keep coding
    the same incidents. A blank id leaves the current assignment alone."""
    inc_id = (answer_text(fields.get("incident_id", {})) or "").strip()
    if not inc_id:
        return
    title = (answer_text(fields.get("incident_title", {})) or "").strip()
    store = load_assignments()
    prev = store.get(key) or {}
    entry = {"incident_id": inc_id, "incident_title": title or prev.get("incident_title", "")}
    if entry != prev:
        store[key] = entry
        save_assignments(store)


def incident_of(key, assignments=None) -> str:
    """Which incident a document belongs to. Falls back to the document's own key
    for one nobody has filed yet, matching how the card view buckets them."""
    assigned = (load_assignments() if assignments is None else assignments).get(key) or {}
    return (assigned.get("incident_id") or "").strip() or str(key)


def incident_title_for(inc_id: str, assignments=None) -> str:
    """The incident's shared title, from the assignment map."""
    for entry in (load_assignments() if assignments is None else assignments).values():
        if entry.get("incident_id") == inc_id and entry.get("incident_title"):
            return entry["incident_title"]
    return ""


def incident_fields(coder, inc_id, assignments=None, inc_store=None) -> dict:
    """The field answers the document view shows: this coder's answers for the
    incident, plus its shared identity overlaid on top so every coder sees the
    same id and title even if they never typed them."""
    store = load_incident_coding(coder) if inc_store is None else inc_store
    fields = dict((store.get(inc_id) or {}).get("fields") or {})
    fields["incident_id"] = {"answer": inc_id}
    title = ""
    for entry in (load_assignments() if assignments is None else assignments).values():
        if entry.get("incident_id") == inc_id and entry.get("incident_title"):
            title = entry["incident_title"]
            break
    if title:
        fields["incident_title"] = {"answer": title}
    return fields


# ------------------------------------------------- vocabulary already in use
# Where a chosen value physically sits, so renaming a code in the Codebook can
# find every one and counting can tell an editor what a change would touch.
#
# A document record holds a code twice over: once in `roles` ({role: [values]}),
# which is what the sidebar's multiselect shows, and again on every quote that
# justifies it ({role, value}). Both have to move together or a rename would
# leave a document selected for a code its quotes no longer name.
#
# Incident coding spreads them out instead: actor, system and developer describe
# the actor context and live on the group; harm is one value on the claim; harmed
# parties and factors are lists on the claim. `harmed_party` singular is the
# pre-plural shape, still read (see build_validator) so old codings migrate too.
_GROUP_VALUE_ROLES = ("actor", "system", "developer")
_CLAIM_LIST_ROLES = {"harmed_party": "harmed_parties", "factor": "factors"}
_CLAIM_VALUE_ROLES = {"harm": "harm", "harmed_party": "harmed_party"}


def _walk_role_values(store, inc_store, role: str, fn) -> int:
    """Pass every stored value of `role` through `fn`, replacing it with what fn
    returns. Mutates both stores; returns how many values actually changed."""
    changed = 0
    for rec in (store or {}).values():
        for q in (rec or {}).get("quotes") or []:
            if q.get("role") == role and q.get("value"):
                new = fn(q["value"])
                if new != q["value"]:
                    q["value"] = new
                    changed += 1
        selected = ((rec or {}).get("roles") or {}).get(role)
        if isinstance(selected, list):
            for i, v in enumerate(selected):
                new = fn(v)
                if new != v:
                    selected[i] = new
                    changed += 1
    for entry in (inc_store or {}).values():
        for grp in (entry or {}).get("groups") or []:
            if role in _GROUP_VALUE_ROLES and grp.get(role):
                new = fn(grp[role])
                if new != grp[role]:
                    grp[role] = new
                    changed += 1
            for claim in grp.get("claims") or []:
                ckey = _CLAIM_VALUE_ROLES.get(role)
                if ckey and claim.get(ckey):
                    new = fn(claim[ckey])
                    if new != claim[ckey]:
                        claim[ckey] = new
                        changed += 1
                lkey = _CLAIM_LIST_ROLES.get(role)
                if lkey and isinstance(claim.get(lkey), list):
                    for i, v in enumerate(claim[lkey]):
                        new = fn(v)
                        if new != v:
                            claim[lkey][i] = new
                            changed += 1
    return changed


def role_value_usage(roles) -> dict:
    """How often each code is actually used, per coder: {role: {value: {coder: n}}}.

    What the Codebook needs before it lets anyone rename or delete something. One
    pass over each coder's two files rather than one per option, and it counts
    values as they are on disk — including any that are no longer in the
    vocabulary at all, which is exactly what an editor needs to see."""
    out = {r: {} for r in roles}
    for coder in CODERS:
        store = _read_json(annotations_path(coder))
        inc_store = _read_json(incident_coding_path(coder))
        for role in roles:
            counts = out[role]

            def tally(v, _counts=counts, _coder=coder):
                _counts.setdefault(v, {})
                _counts[v][_coder] = _counts[v].get(_coder, 0) + 1
                return v

            _walk_role_values(store, inc_store, role, tally)
    return out


def role_value_incidents(role: str, value: str) -> dict:
    """Which incidents one code is actually used in: {inc_id: {coder: n}}.

    Evidence is filed per document, so a quote's incident is the one its document
    was assigned to; incident-level coding is already keyed by incident. A
    document that belongs to no incident yet is counted under "" — it is still a
    use of the code, and hiding it would make the total not add up."""
    assignments = load_assignments()
    out = {}

    def bump(inc_id, coder, n):
        if n:
            out.setdefault(inc_id, {})
            out[inc_id][coder] = out[inc_id].get(coder, 0) + n

    for coder in CODERS:
        for key, rec in (_read_json(annotations_path(coder)) or {}).items():
            n = sum(1 for q in (rec or {}).get("quotes") or []
                    if q.get("role") == role and q.get("value") == value)
            selected = ((rec or {}).get("roles") or {}).get(role)
            if isinstance(selected, list):
                n += sum(1 for v in selected if v == value)
            bump(incident_of(key, assignments), coder, n)
        for inc_id, entry in (_read_json(incident_coding_path(coder)) or {}).items():
            hits = 0

            def tally(v, _value=value):
                nonlocal hits
                if v == _value:
                    hits += 1
                return v

            _walk_role_values({}, {inc_id: entry}, role, tally)
            bump(inc_id, coder, hits)
    return out


def rename_role_value(role: str, old: str, new: str) -> dict:
    """Rewrite every use of one code, for every coder, to its new name.

    Works on the local files only — the ones this app treats as the source of
    truth — rather than through load_annotations(), whose Mongo overlay would
    quietly materialise remote documents locally as a side effect of a rename.
    Mongo catches up on the next Push."""
    out = {}
    for coder in CODERS:
        store = _read_json(annotations_path(coder))
        inc_store = _read_json(incident_coding_path(coder))
        n = _walk_role_values(store, inc_store, role,
                              lambda v, _o=old, _n=new: _n if v == _o else v)
        if n:
            save_annotations(store, coder)
            _atomic_write(incident_coding_path(coder),
                          json.dumps(inc_store, indent=2, ensure_ascii=False))
            out[coder] = n
    return out


def _seed_shared_files() -> None:
    """One-time migration off the single-coder layout: the old annotations.json
    becomes the first coder's file. Which document sits in which incident lives
    in incident_assignments.json and is the shared record; nothing here derives
    it, since field answers no longer carry an incident id."""
    if LEGACY_ANNOTATIONS_JSON.exists() and not annotations_path(LEGACY_CODER).exists():
        annotations_path(LEGACY_CODER).write_text(LEGACY_ANNOTATIONS_JSON.read_text())
        print(f"[coders] migrated {LEGACY_ANNOTATIONS_JSON.name} -> "
              f"{annotations_path(LEGACY_CODER).name}")
