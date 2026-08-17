"""Fold the vocabulary fields into the characteristics.

`incident_system` and `incident_developer` were stored apart from the other
characteristics — an `{answer, comments}` wrapper at incident level, while actor,
factor, harm and harmed_party were flat lists per document. Nothing justified the
split: both are picked from a controlled vocabulary, both are evidenced by quotes
in a particular document, and both are dragged into a claim. It cost a second
storage shape, a second quote tag (`category` vs `role`) and a bridge table.

They become roles named `system` and `developer`, living beside the rest.

Where each value lands is decided by its own evidence: a value goes onto the
documents whose quotes justify it. A value with no quote anywhere in the incident
goes onto that coder's first coded document, so a selection made without a
highlight is not silently dropped.

Quotes carrying `category: incident_system|incident_developer` are retagged as
`role: system|developer`. `incident_aftermath` stays a field, and its quotes keep
their category — it is free text, not a characteristic.

The 6 comments left on those fields have no equivalent on a role, so they move to
`by_coder.<coder>.notes.<role>` rather than being thrown away.

    $PY migrate_roles.py            # dry run
    $PY migrate_roles.py --apply
"""
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
APPLY = "--apply" in sys.argv
FIELD_TO_ROLE = {"incident_system": "system", "incident_developer": "developer",
                 "incident_deployer": "deployer"}
ROLE_KEYS = ("system", "developer", "deployer",
             "actor", "factor", "harm", "harmed_party")


def load_dotenv(path: Path = HERE / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def migrate_slot(slot: dict, stats: dict) -> dict:
    """One coder's subtree of one incident."""
    docs = slot.get("documents") or {}
    for ev in docs.values():
        roles = (ev or {}).setdefault("roles", {})
        for rk in ROLE_KEYS:
            roles.setdefault(rk, [])
        # retag the evidence first, so placement can read it
        for q in (ev or {}).get("quotes") or []:
            role = FIELD_TO_ROLE.get(q.get("category"))
            if role:
                q.pop("category", None)
                q["role"] = role
                stats["quotes_retagged"] += 1

    # where does each field value have evidence?
    for fk, role in FIELD_TO_ROLE.items():
        fa = (slot.get("fields") or {}).get(fk) or {}
        ans = fa.get("answer")
        values = ans if isinstance(ans, list) else ([ans] if ans else [])
        values = [str(v).strip() for v in values if str(v).strip()]
        for value in values:
            homes = [dk for dk, ev in docs.items()
                     if any(q.get("role") == role and q.get("value") == value
                            for q in (ev or {}).get("quotes") or [])]
            if not homes:
                homes = sorted(docs)[:1]      # keep the selection somewhere
                stats["placed_without_quote"] += 1 if homes else 0
                if not homes:
                    stats["dropped_no_document"] += 1
            for dk in homes:
                bucket = docs[dk]["roles"].setdefault(role, [])
                if value not in bucket:
                    bucket.append(value)
                    stats["values_moved"] += 1
        cmt = str(fa.get("comments") or "").strip()
        if cmt:
            slot.setdefault("notes", {})[role] = cmt
            stats["comments_moved"] += 1
        (slot.get("fields") or {}).pop(fk, None)
    if not slot.get("fields"):
        slot.pop("fields", None)
    return slot


def main():
    load_dotenv()
    stats = dict(values_moved=0, comments_moved=0, quotes_retagged=0,
                 placed_without_quote=0, dropped_no_document=0)
    coders = [c.strip() for c in os.environ.get("CODERS", "").split(",") if c.strip()] \
        or ["coder1", "coder2"]
    print(f"{'APPLY' if APPLY else 'DRY RUN'}\n")

    docs = []
    db = None
    if os.environ.get("MONGO_URI"):
        from pymongo import MongoClient
        db = MongoClient(os.environ["MONGO_URI"], serverSelectionTimeoutMS=8000)[
            os.environ.get("MONGO_DB", "incidents")]
        docs = list(db.incidents.find())
        before = sum(len((d or {}).get("quotes") or []) for i in docs
                     for c in (i.get("by_coder") or {}).values()
                     for d in (c.get("documents") or {}).values())
        for inc in docs:
            for slot in (inc.get("by_coder") or {}).values():
                migrate_slot(slot or {}, stats)
        after = sum(len((d or {}).get("quotes") or []) for i in docs
                    for c in (i.get("by_coder") or {}).values()
                    for d in (c.get("documents") or {}).values())
        print(f"  quotes before {before}, after {after} "
              f"{'OK' if before == after else '*** MISMATCH ***'}")
        if before != after:
            print("  refusing to apply")
            return
        for k, v in stats.items():
            print(f"  {k.replace('_', ' '):24s} {v}")
        if APPLY:
            for inc in docs:
                db.incidents.replace_one({"_id": inc["_id"]}, inc)
            print(f"\n  rewrote {len(docs)} incident(s) in Atlas")

    # local files mirror the same move
    by_inc = {d["_id"]: d for d in docs}
    for coder in coders:
        ann_p = HERE / f"annotations.{coder}.json"
        inc_p = HERE / f"incident_coding.{coder}.json"
        ann = json.loads(ann_p.read_text()) if ann_p.exists() else {}
        inc = json.loads(inc_p.read_text()) if inc_p.exists() else {}
        for inc_id, entry in inc.items():
            slot = (by_inc.get(inc_id, {}).get("by_coder") or {}).get(coder)
            if slot is None:
                continue
            entry.pop("fields", None) if not slot.get("fields") else None
            entry["fields"] = slot.get("fields") or {}
            if slot.get("notes"):
                entry["notes"] = slot["notes"]
            for dk, ev in (slot.get("documents") or {}).items():
                ann[dk] = {"quotes": ev.get("quotes") or [], "roles": ev.get("roles") or {}}
        print(f"  {coder}: {len(ann)} document(s), "
              f"{sum(1 for e in inc.values() if e.get('notes'))} with notes")
        if APPLY:
            ann_p.write_text(json.dumps(ann, indent=2, ensure_ascii=False))
            inc_p.write_text(json.dumps(inc, indent=2, ensure_ascii=False))

    print("\n(dry run — nothing written)" if not APPLY else "\nDone.")


if __name__ == "__main__":
    main()
