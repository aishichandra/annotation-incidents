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


def aggregate_incidents(coder: str):
    """Build one coder's per-incident view, shared by the cards and the Mongo push.

    An incident is the set of documents sharing an `incident_id` in the shared
    assignment map (a document nobody has filed yet falls back to its own key), so
    the incidents and their documents are identical for every coder. Everything
    inside a card, though, is this coder's own reading: incident-level fields are
    aggregated across the member docs (multiselects/text collect distinct non-empty
    values; title is the first), the four characteristic roles are pooled into
    `role_values` — the palette the card view drags from — and saved claim
    groupings come from incident_groups.<coder>.json, pruned to values that still
    exist so links can't dangle. The coder's comment on the incident as a whole
    rides along too. Each document also carries `coded_by`, the coders who have
    touched it, so progress is visible across the team.
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
        g = incidents.setdefault(inc_id, {
            "incident_id": inc_id, "title": "", "documents": [],
            "field_values": {}, "field_comments": {},
            "role_values": {r["role"]: [] for r in role_defs}, "groups": [],
            "role_notes": {}, "value_quotes": {}, "comment": "", "flagged": False,
        })
        g["documents"].append({
            "index": i, "doc_key": key, "title": cell(i, "title"),
            "url": cell(i, "url"), "quotes": len(rec["quotes"]),
            # Read off the document rather than coded: when it was published
            # (from Zotero, via zotero_docs.csv) and who published it (from the
            # URL). Neither is a judgement, so neither is anybody's to enter.
            "date": cell(i, "date"), "domain": doc_source.domain(cell(i, "url")),
            "coded_by": coded_by(key, all_stores),
        })
        if not g["title"]:
            g["title"] = storage.incident_title_for(inc_id, assignments)
        # The incident's own answers, read once per incident rather than pooled
        # across its documents — there is only one answer to pool now.
        if not g["field_values"]:
            answers = (inc_store.get(inc_id) or {}).get("fields") or {}
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
            for role, note in ((inc_store.get(inc_id) or {}).get("notes") or {}).items():
                if str(note or "").strip():
                    g["role_notes"][role] = str(note).strip()
            g["comment"] = str((inc_store.get(inc_id) or {}).get("comment") or "")
            # This coder asking for a second look at their own coding. Read here
            # with the rest of their incident-level judgement; see api_set_flag.
            g["flagged"] = bool((inc_store.get(inc_id) or {}).get("flagged"))
        for r in role_defs:
            bucket = g["role_values"][r["role"]]
            for v in rec["roles"].get(r["role"], []):
                v = str(v).strip()
                if v and v not in bucket:
                    bucket.append(v)
        # The passages justifying each pooled value, so the card can show the
        # evidence behind a characteristic without leaving for the document view.
        # Keyed by the role the quote carries. A quote with no value tags nothing
        # in particular and is skipped; free-text fields keep tagging by category.
        for q in rec["quotes"]:
            if not isinstance(q, dict):
                continue
            value = str(q.get("value") or "").strip()
            text = str(q.get("text") or "").strip()
            kind = q.get("role") or q.get("category")
            if not (value and text and kind):
                continue
            bucket = g["value_quotes"].setdefault(str(kind), {}).setdefault(value, [])
            # The same passage can be highlighted once per document; identical
            # text from the same document is one piece of evidence, not several.
            if not any(b["text"] == text and b["doc_key"] == key for b in bucket):
                bucket.append({"text": text, "doc_key": key, "title": cell(i, "title")})

    # Attach saved groupings, dropping any value that is no longer coded. A value
    # is either one of the four characteristic roles (checked against the pooled
    # role_values) or an optional system/developer (checked against that
    # incident-level field's values).
    def still_coded(g, role, value):
        return bool(value) and value in (g["role_values"].get(role) or [])

    def keep(g, role, value):
        """The value if it's still coded, else None — scalar slots empty out
        rather than dangle."""
        return value if still_coded(g, role, value) else None

    def keep_list(g, role, values, legacy=None):
        """The still-coded values of a group's list slot, in order.

        `legacy` is the pre-plural single value the slot used to hold: groups
        saved before systems and developers went plural carry one there, and it
        is folded in so an old grouping renders as a one-item list rather than
        as an empty clause."""
        out = [v for v in (values or []) if still_coded(g, role, v)]
        if legacy and legacy not in out and still_coded(g, role, legacy):
            out.append(legacy)
        return out

    for inc_id, g in incidents.items():
        saved = (inc_store.get(inc_id) or {}).get("groups") or []
        pruned = []
        for grp in saved:
            # Groups written before the actor-grouped structure carried a flat
            # `members` list. They aren't convertible without a coder deciding how
            # to split them, so they're skipped rather than half-rendered.
            if "claims" not in grp:
                continue
            claims = []
            for cl in grp.get("claims") or []:
                harm = keep(g, "harm", cl.get("harm"))
                # Plural, like factors: one harm landing on several parties reads
                # as a conjunction. It's plural harms *times* plural parties that
                # would leave "which harm hit which party?" unanswerable, and harm
                # stays single-valued for exactly that reason.
                parties = [p for p in (cl.get("harmed_parties") or [])
                           if still_coded(g, "harmed_party", p)]
                factors = [f for f in (cl.get("factors") or [])
                           if still_coded(g, "factor", f)]
                if harm or parties or factors:
                    claims.append({"id": cl.get("id"), "harm": harm,
                                   "harmed_parties": parties, "factors": factors})
            actor = keep(g, "actor", grp.get("actor"))
            # Plural for the same reason factors are: one actor context can run
            # on several systems, and a system can have more than one party
            # behind it. The actor itself stays single — a second actor is a
            # second context, which is a second group.
            systems = keep_list(g, "system", grp.get("systems"), grp.get("system"))
            developers = keep_list(g, "developer", grp.get("developers"),
                                   grp.get("developer"))
            # The optional clauses this group has taken out of its sentence. Not
            # every actor context is about a named system, and saying so is a
            # judgement — "inapplicable here" rather than "not answered yet" — so
            # it survives a reload like any other. Filtered to the roles that have
            # an optional clause; nothing else is omittable.
            omit = [r for r in (grp.get("omit") or []) if r in OPTIONAL_CLAIM_ROLES]
            # An actor context with nothing left in it at all is dropped; one that
            # still names an actor is kept even with no claims, since it's the
            # header a coder is about to hang claims off. A group that holds only
            # an omission still holds a decision, so that counts as content too.
            if actor or systems or developers or claims or omit:
                pruned.append({"id": grp.get("id"), "actor": actor,
                               "systems": systems, "developers": developers,
                               "claims": claims, "omit": omit})
        g["groups"] = pruned
        # Computed after pruning, so the check sees the same groups the card
        # does. The stored sign-off rides alongside it: `completeness` is what
        # the coding currently supports, `status` is what the coder has actually
        # attested to, and the two can disagree — signing off then editing is
        # what clears the flag (see api_set_complete).
        # The incident's own dates and domains: its documents', de-duplicated.
        # `undated` is carried rather than inferred from the two lengths, so a
        # card can say "and two we have no date for" instead of quietly showing
        # a range that covers fewer articles than the incident holds.
        g["dates"] = sorted({d["date"] for d in g["documents"] if d["date"]})
        g["domains"] = sorted({d["domain"] for d in g["documents"] if d["domain"]})
        g["undated"] = sum(1 for d in g["documents"] if not d["date"])
        entry = inc_store.get(inc_id) or {}
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
