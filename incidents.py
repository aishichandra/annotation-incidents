"""Rolling per-document coding up into per-incident views.

aggregate_incidents() is what the card view reads: it walks every document,
buckets them by incident, and merges each coder's evidence, field answers and
claim groups into one object per incident.
"""
import re
from datetime import datetime

from config import (
    CODERS, IDENTITY_FIELDS, OPTIONAL_CLAIM_ROLES, REQUIRED_CLAIM_ROLES, load_schema,
)
from doc_source import cell
import doc_source
import mongo_sync
import storage


def _next_incident_id(ids):
    """Next INC-### id above the highest existing one (INC-001 if none)."""
    nums = [int(m.group(1)) for m in (re.match(r"INC-(\d+)$", x) for x in ids) if m]
    return f"INC-{(max(nums) + 1) if nums else 1:03d}"


def incident_sort_key(inc_id: str):
    """Order incidents by their number, with anything unnumbered last.

    A document nobody has filed yet stands in as its own incident under its
    Zotero key (see storage.incident_of). Those keys start with a digit or a
    capital, so a plain string sort filed every one of them ahead of INC-001 —
    which put the ungrouped documents at the top of the list instead of the end.
    Sorting on the number also keeps INC-9 before INC-10 whatever the padding."""
    m = re.match(r"INC-(\d+)$", inc_id or "")
    return (0, int(m.group(1)), "") if m else (1, 0, inc_id or "")


def coded_by(key, stores) -> list:
    """Which coders have coded this document. Drives the "coded by" badge."""
    return [coder for coder, store in stores.items() if storage.has_coding(store.get(key))]



def claim_is_complete(cl: dict) -> bool:
    """A claim reads as finished when it says who was harmed, how, and why —
    harm, at least one harmed party, at least one factor. The optional
    `using … developed by …` clauses are not part of the sentence's core."""
    return bool(cl.get("harm")
                and (cl.get("harmed_parties") or [])
                and (cl.get("factors") or []))


def incident_completeness(inc: dict) -> dict:
    """Whether one coder's reading of an incident is finished enough to sign off.

    Two bars, and the second is the one that matters. Every required role must
    have at least one value, which says the coder has read the documents and
    picked out the characteristics. And at least one claim group must name an
    actor and carry a complete claim, which says they have gone the further step
    of asserting who did what to whom — the judgement the flat lists alone never
    state. An incident can have a full palette and still assert nothing.

    Returns `{"ok": bool, "missing": [...]}`; `missing` names what is absent so
    the card can say why the button is disabled rather than just disabling it."""
    missing = [role for role in REQUIRED_CLAIM_ROLES
               if not (inc.get("role_values") or {}).get(role)]
    if not any(g.get("actor") and any(claim_is_complete(c) for c in (g.get("claims") or []))
               for g in (inc.get("groups") or [])):
        missing.append("complete_claim")
    return {"ok": not missing, "missing": missing}


def _blank_incident(inc_id: str, role_defs: list) -> dict:
    """The shape of one incident before anything is read into it.

    Every key is present from the start, empty. A card renders "No data" for an
    absent answer, so a missing key and an unanswered one have to look the same
    to it — and the aggregate below fills these in from several passes that each
    know about only some of them."""
    return {
        "incident_id": inc_id, "title": "", "documents": [],
        "field_values": {}, "field_comments": {},
        "role_values": {r["role"]: [] for r in role_defs}, "groups": [],
        "role_notes": {}, "value_quotes": {}, "comment": "", "flagged": False,
    }


def _document_entry(i: int, key: str, rec: dict, all_stores: dict) -> dict:
    """One member document as a card lists it.

    `date` and `domain` are read off the document rather than coded — the date
    from Zotero via zotero_docs.csv, the domain from the URL — so neither is
    anybody's to enter. `coded_by` is progress across the team, never anyone's
    codes: coders stay blind to each other's judgements while coding."""
    return {
        "index": i, "doc_key": key, "title": cell(i, "title"),
        "url": cell(i, "url"), "quotes": len(rec["quotes"]),
        "date": cell(i, "date"), "domain": doc_source.domain(cell(i, "url")),
        "coded_by": coded_by(key, all_stores),
    }


def _read_incident_answers(g: dict, entry: dict, field_defs: list) -> None:
    """This coder's answers about the incident itself onto `g`.

    Read once per incident rather than pooled across its documents: the
    aftermath, the two card answers, the note naming an actor, the comment, and
    whether the coder has flagged their own reading as one they are unsure of.
    Identity fields are skipped — the incident owns its id and title, so a copy
    inside a coder's answers could only drift from them."""
    answers = entry.get("fields") or {}
    for f in field_defs:
        fk = f["key"]
        if fk in IDENTITY_FIELDS:
            continue
        fa = answers.get(fk, {})
        ans = fa.get("answer")
        vals = ans if isinstance(ans, list) else ([ans] if ans else [])
        g["field_values"][fk] = [str(v).strip() for v in vals if str(v).strip()]
        cmt = str(fa.get("comments") or "").strip()
        if cmt:
            g["field_comments"][fk] = [cmt]
    for role, note in (entry.get("notes") or {}).items():
        if str(note or "").strip():
            g["role_notes"][role] = str(note).strip()
    g["comment"] = str(entry.get("comment") or "")
    g["flagged"] = bool(entry.get("flagged"))


def _pool_role_values(g: dict, rec: dict, role_defs: list) -> None:
    """Add one document's characteristics to the incident's palette.

    The palette is the union across member documents: a code applied to any of
    them is a code the incident's claims can be built from. Order is first-seen
    and duplicates are dropped, so the same code on three documents is one chip."""
    for r in role_defs:
        bucket = g["role_values"][r["role"]]
        for v in rec["roles"].get(r["role"], []):
            v = str(v).strip()
            if v and v not in bucket:
                bucket.append(v)


def _collect_value_quotes(g: dict, rec: dict, key: str, i: int) -> None:
    """The passages justifying each pooled value, keyed by role then value.

    This is what lets a card show the evidence behind a characteristic without
    leaving for the document view. A quote with no value tags nothing in
    particular and is skipped; free-text fields keep tagging by category. The
    same passage can be highlighted once per document, so identical text from
    one document is one piece of evidence rather than several."""
    for q in rec["quotes"]:
        if not isinstance(q, dict):
            continue
        value = str(q.get("value") or "").strip()
        text = str(q.get("text") or "").strip()
        kind = q.get("role") or q.get("category")
        if not (value and text and kind):
            continue
        bucket = g["value_quotes"].setdefault(str(kind), {}).setdefault(value, [])
        if not any(b["text"] == text and b["doc_key"] == key for b in bucket):
            bucket.append({"text": text, "doc_key": key, "title": cell(i, "title")})


# ---------------------------------------------------------------- claim groups
# A saved grouping names its codes as strings, so a code no longer coded on any
# member document would leave a claim pointing at nothing. Pruning happens on
# every read rather than on edit: the coding it depends on lives in another file
# that this one never gets to see change.


def _still_coded(g: dict, role: str, value) -> bool:
    return bool(value) and value in (g["role_values"].get(role) or [])


def _keep(g: dict, role: str, value):
    """The value if it is still coded, else None — a scalar slot empties out
    rather than dangling."""
    return value if _still_coded(g, role, value) else None


def _keep_list(g: dict, role: str, values, legacy=None) -> list:
    """The still-coded values of a list slot, in order.

    `legacy` is the pre-plural single value the slot used to hold: groups saved
    before systems and developers went plural carry one there, and it is folded
    in so an old grouping renders as a one-item list rather than an empty clause."""
    out = [v for v in (values or []) if _still_coded(g, role, v)]
    if legacy and legacy not in out and _still_coded(g, role, legacy):
        out.append(legacy)
    return out


def _prune_claim(g: dict, cl: dict):
    """One claim with every dangling value dropped, or None if nothing survives.

    Harm stays single-valued and the parties and factors are lists, for the
    reason build_validator gives: one harm reaching several parties is a
    conjunction anyone can read back, whereas plural harms alongside plural
    parties would leave "which harm hit which party?" unanswerable."""
    harm = _keep(g, "harm", cl.get("harm"))
    parties = [p for p in (cl.get("harmed_parties") or [])
               if _still_coded(g, "harmed_party", p)]
    factors = [f for f in (cl.get("factors") or []) if _still_coded(g, "factor", f)]
    if not (harm or parties or factors):
        return None
    return {"id": cl.get("id"), "harm": harm, "harmed_parties": parties,
            "factors": factors}


def _prune_group(g: dict, grp: dict):
    """One actor context with every dangling value dropped, or None if it is
    left holding nothing at all.

    A group that still names an actor is kept even with no claims, since it is
    the header a coder is about to hang claims off, and a group holding only an
    omission still holds a decision. Groups written before the actor-grouped
    structure carried a flat `members` list; they aren't convertible without a
    coder deciding how to split them, so they are skipped rather than
    half-rendered."""
    if "claims" not in grp:
        return None
    claims = [c for c in (_prune_claim(g, cl) for cl in grp.get("claims") or []) if c]
    actor = _keep(g, "actor", grp.get("actor"))
    # Plural for the same reason factors are: one actor context can run on
    # several systems, and a system can have more than one party behind it. The
    # actor itself stays single — a second actor is a second context, which is a
    # second group.
    systems = _keep_list(g, "system", grp.get("systems"), grp.get("system"))
    developers = _keep_list(g, "developer", grp.get("developers"), grp.get("developer"))
    # The optional clauses this group has taken out of its sentence: "inapplicable
    # here" rather than "not answered yet", which is a judgement and so survives a
    # reload like any other.
    omit = [r for r in (grp.get("omit") or []) if r in OPTIONAL_CLAIM_ROLES]
    if not (actor or systems or developers or claims or omit):
        return None
    return {"id": grp.get("id"), "actor": actor, "systems": systems,
            "developers": developers, "claims": claims, "omit": omit}


def _derive_from_documents(g: dict) -> None:
    """The incident's dates and domains: its documents', de-duplicated.

    `undated` is carried rather than inferred from the two lengths, so a card can
    say "and two we have no date for" instead of quietly showing a range that
    covers fewer articles than the incident holds."""
    g["dates"] = sorted({d["date"] for d in g["documents"] if d["date"]})
    g["domains"] = sorted({d["domain"] for d in g["documents"] if d["domain"]})
    g["undated"] = sum(1 for d in g["documents"] if not d["date"])


def aggregate_incidents(coder: str):
    """Build one coder's per-incident view, shared by the cards and the Mongo push.

    An incident is the set of documents sharing an `incident_id` in the shared
    assignment map (a document nobody has filed yet falls back to its own key), so
    the incidents and their documents are identical for every coder. Everything
    inside a card, though, is this coder's own reading.

    Two passes. The first walks every document once, filing it under its incident
    and folding in what that document contributes: the palette of characteristics,
    the passages justifying them, and — the first time an incident is seen — the
    answers the coder gave the incident itself. The second walks the incidents,
    which is where anything that needs the whole incident belongs: pruning the
    claim groups against the pooled palette, reading the dates and domains off the
    members, and checking completeness against the groups as pruned.

    Returns (incidents_dict, field_defs, role_defs)."""
    store = storage.load_annotations(coder)
    all_stores = {c: storage.load_annotations(c) for c in CODERS}
    assignments = storage.load_assignments()
    schema = load_schema()
    field_defs = schema["fields"]
    role_defs = schema.get("claim_roles", [])
    inc_store = storage.load_incident_coding(coder)

    incidents = {}
    for i in range(len(doc_source.df)):
        key = doc_source.df["doc_key"].iloc[i]
        rec = storage.doc_ann(store, key)
        inc_id = storage.incident_of(key, assignments)
        g = incidents.setdefault(inc_id, _blank_incident(inc_id, role_defs))
        g["documents"].append(_document_entry(i, key, rec, all_stores))
        if not g["title"]:
            g["title"] = storage.incident_title_for(inc_id, assignments)
        if not g["field_values"]:          # the first document to name it
            _read_incident_answers(g, inc_store.get(inc_id) or {}, field_defs)
        _pool_role_values(g, rec, role_defs)
        _collect_value_quotes(g, rec, key, i)

    for inc_id, g in incidents.items():
        entry = inc_store.get(inc_id) or {}
        saved = entry.get("groups") or []
        g["groups"] = [grp for grp in (_prune_group(g, x) for x in saved) if grp]
        _derive_from_documents(g)
        # Computed after pruning, so the check sees the same groups the card
        # does. The stored sign-off rides alongside it: `completeness` is what the
        # coding currently supports, `status` is what the coder has actually
        # attested to, and the two can disagree — signing off then editing is what
        # clears the flag (see clear_signoff).
        g["completeness"] = incident_completeness(g)
        g["status"] = entry.get("status") or ""
        g["completed_at"] = entry.get("completed_at") or ""

    return incidents, field_defs, role_defs


def _jsonable(v):
    """BSON/datetime -> something json.dumps can render, for the raw JSON view."""
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)          # ObjectId and anything else exotic


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
    store = storage.load_incident_coding(coder)
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
    storage.save_incident_coding(store, coder)
    mongo_sync.sync_incident_coding_to_mongo(inc_id, coder, entry)
