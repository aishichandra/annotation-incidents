"""Inter-coder agreement: Cohen's kappa for every category in the scheme.

The unit is the incident, and every code is its own yes/no question — "did this
coder apply this code to this incident?" — so a category that lets a coder pick
several values (a multiselect) is measured the only way kappa can measure it: one
2x2 table per code, plus one pooled over all of a category's codes for the
category as a whole.

The pooled figure is over the codes either coder used on those incidents.

Each incident also gets its own kappa. Within one incident the items are the
codes themselves: every code in the vocabulary is one yes/no that each coder
answered (applied it, or not), and kappa is taken over those. The universe is the
full vocabulary — not just the codes somebody used — because with one incident
there is nothing else to define "neither"; it is the same list for every
incident, so the figures are comparable. The cost is that a long vocabulary makes
most codes "neither", which raises chance agreement, so these read lower than raw
agreement suggests, and an incident where the coders applied nothing in common
can go negative.

Where the vocabulary organises a category into subcategories (harm and factor
have groups), each subcategory is measured too: a coder "applied" it when they
applied any of its codes. That is more forgiving than per-code kappa, since a
near-miss inside one subcategory counts as agreement, so both are reported and
neither replaces the other. Codes outside every subcategory sit under
"Other" (as the codebook does) and have no subcategory figure of their own.

A category is a characteristic (system, developer, actor, factor, harm, harmed
party — read the same way the Codebook counts them, from documents and claims
together). Geography and translated are deliberately not measured: a field left
blank reads as "didn't apply", which would score an unanswered field as
disagreement.

Which incidents count is the part that decides what the number means, so it is
explicit. Two coders who never touched an incident agree on every code in it
("neither used it"), and counting those would inflate kappa with every incident
nobody has coded yet. So only incidents BOTH coders have coded are measured, and
an incident either of them set aside as "not an incident" is out of scope for
both. `scope="complete"` tightens that to incidents both signed off.
"""
from collections import Counter

from config import ROLE_KEYS, load_schema
import storage

SCOPES = ("coded", "complete")


def kappa_table(a: int, b: int, c: int, d: int) -> dict:
    """Cohen's kappa for one binary 2x2 table.

    a = both coders applied it, b = only the first, c = only the second,
    d = neither. `kappa` is None when it is undefined: no incidents, or both
    coders answered identically for every incident AND always the same way
    (expected agreement of 1), where there is no variation to agree about."""
    n = a + b + c + d
    out = {"both": a, "only_a": b, "only_b": c, "neither": d, "n": n,
           "observed": None, "kappa": None}
    if not n:
        return out
    po = (a + d) / n
    pe = ((a + b) * (a + c) + (c + d) * (b + d)) / (n * n)
    out["observed"] = po
    if pe < 1:
        out["kappa"] = (po - pe) / (1 - pe)
    return out


def _cells() -> dict:
    """The three cells of a table worth listing — what both coders marked, and
    what only one did. "Neither" is every unmarked code, so it isn't listed."""
    return {"both": [], "only_a": [], "only_b": []}


def _file(items: dict, x: bool, y: bool, inc: str, label: str = "") -> None:
    """Record one yes/no pair under the cell it falls in."""
    cell = "both" if x and y else "only_a" if x else "only_b" if y else None
    if cell:
        items[cell].append({"inc": inc, "label": label})


def _coder_view(coder: str) -> dict:
    """What one coder said, per incident: {inc_id: {category: {values}}} for the
    incidents they have coded, plus each incident's status."""
    store = storage.load_annotations(coder)
    inc_store = storage.load_incident_coding(coder)
    assignments = storage.load_assignments()
    cats = {}                                   # category -> {inc_id: set(values)}
    for role in ROLE_KEYS:
        cats[role] = storage._role_uses_by_incident(store, inc_store, role, assignments)
    touched = {inc for per in cats.values() for inc, vals in per.items() if vals}
    status = {inc: (e or {}).get("status") or "" for inc, e in inc_store.items()}
    return {"cats": cats, "touched": touched, "status": status}


def _subcategories(key, values, group_of, order, A, B, incidents) -> list:
    """The category's codes arranged under their subcategories, each with the
    kappa of "applied any code in this subcategory". Subcategories nobody used
    are left out; codes in none sit last under "Other" with no figure."""
    by_group = {}
    for v in values:
        by_group.setdefault(group_of.get(v["value"], ""), []).append(v)
    out = []
    for label in order + [""]:
        members = by_group.get(label)
        if not members:
            continue
        sub = {"label": label or "Other", "values": members,
               "ungrouped": not label, "table": None, **kappa_table(0, 0, 0, 0)}
        if label:
            codes = {m["value"] for m in members}
            t, items = [0, 0, 0, 0], _cells()
            for inc in sorted(incidents):
                x = bool(A.get(inc, set()) & codes)
                y = bool(B.get(inc, set()) & codes)
                _file(items, x, y, inc)
                t[0] += x and y
                t[1] += x and not y
                t[2] += y and not x
                t[3] += not x and not y
            sub.update(kappa_table(*t))
            sub["table"], sub["items"] = t, items
        out.append(sub)
    return out


def compute(coder_a: str, coder_b: str, scope: str = "coded") -> dict:
    """Kappa for every category and every code between two coders."""
    if scope not in SCOPES:
        scope = "coded"
    va, vb = _coder_view(coder_a), _coder_view(coder_b)

    pool = va["touched"] & vb["touched"]
    out_of_scope = {inc for inc in pool
                    if "not_an_incident" in (va["status"].get(inc), vb["status"].get(inc))}
    incidents = pool - out_of_scope
    if scope == "complete":
        incidents = {inc for inc in incidents
                     if va["status"].get(inc) == "complete"
                     and vb["status"].get(inc) == "complete"}

    schema = load_schema()
    labels = {r["role"]: r.get("label", r["role"]) for r in schema.get("claim_roles", [])}
    vocab_order = {r["role"]: r.get("options") or [] for r in schema.get("claim_roles", [])}

    group_of = {}                               # category -> {code: subcategory}
    group_order = {}                            # category -> [subcategory, ...]
    for r in schema.get("claim_roles", []):
        group_of[r["role"]] = {o: g["label"] for g in r.get("groups") or []
                               for o in g["options"]}
        group_order[r["role"]] = [g["label"] for g in r.get("groups") or []]

    assignments = storage.load_assignments()
    universe = {key: list(dict.fromkeys(vocab_order.get(key, []))) for key in ROLE_KEYS}
    per_incident = []
    for inc in sorted(incidents):
        total, cats = [0, 0, 0, 0], {}
        total_items = _cells()
        for key in ROLE_KEYS:
            x_set, y_set = va["cats"][key].get(inc, set()), vb["cats"][key].get(inc, set())
            # The vocabulary, plus any code used that it no longer lists.
            codes = universe[key] + [c for c in (x_set | y_set) if c not in universe[key]]
            t = [0, 0, 0, 0]
            items = _cells()
            for code in codes:
                x, y = code in x_set, code in y_set
                _file(items, x, y, inc, code)
                _file(total_items, x, y, inc, f"{labels.get(key, key)} · {code}")
                t[0] += x and y
                t[1] += x and not y
                t[2] += y and not x
                t[3] += not x and not y
            cats[key] = {**kappa_table(*t), "items": items,
                         "applied_a": len(x_set), "applied_b": len(y_set)}
            for i in range(4):
                total[i] += t[i]
        per_incident.append({"id": inc, "title": storage.incident_title_for(inc, assignments),
                             "status_a": va["status"].get(inc, ""),
                             "status_b": vb["status"].get(inc, ""),
                             "overall": {**kappa_table(*total), "items": total_items},
                             "categories": cats})

    categories = []
    for key in ROLE_KEYS:
        A, B = va["cats"][key], vb["cats"][key]
        used = Counter()
        for inc in incidents:
            used.update(A.get(inc, set()) | B.get(inc, set()))
        order = {v: i for i, v in enumerate(vocab_order.get(key, []))}
        values, pa, pooled_items = [], [0, 0, 0, 0], _cells()
        for value in sorted(used, key=lambda v: (-used[v], order.get(v, 1 << 30), v)):
            t, items = [0, 0, 0, 0], _cells()
            for inc in sorted(incidents):
                x, y = value in A.get(inc, ()), value in B.get(inc, ())
                _file(items, x, y, inc)
                _file(pooled_items, x, y, inc, value)
                t[0] += x and y
                t[1] += x and not y
                t[2] += y and not x
                t[3] += not x and not y
            for i in range(4):
                pa[i] += t[i]
            values.append({"value": value, **kappa_table(*t), "items": items})
        # The pool covers only codes somebody used in scope. Padding it with the
        # whole vocabulary's never-used codes would make a category's kappa depend
        # on how long its list happens to be (geography has hundreds).
        cat = {"key": key, "label": labels.get(key, key), "kind": "role",
               "pooled": {**kappa_table(*pa), "items": pooled_items},
               "values": values, "subcategories": None}
        if group_order.get(key):
            cat["subcategories"] = _subcategories(
                key, values, group_of[key], group_order[key], A, B, incidents)
            sub_items = _cells()
            for s in cat["subcategories"]:
                if s["table"]:
                    for cell, lst in s["items"].items():
                        sub_items[cell] += [{"inc": it["inc"], "label": s["label"]}
                                            for it in lst]
            cat["pooled_by_subcategory"] = {**kappa_table(*[
                sum(s["table"][i] for s in cat["subcategories"] if s["table"])
                for i in range(4)]), "items": sub_items}
        categories.append(cat)
    return {
        "a": coder_a, "b": coder_b, "scope": scope,
        "n_incidents": len(incidents),
        "coded_by_a": len(va["touched"]), "coded_by_b": len(vb["touched"]),
        "coded_by_both": len(pool), "set_aside": len(out_of_scope),
        "categories": categories,
        "incidents": per_incident,
    }
