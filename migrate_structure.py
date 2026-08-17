"""Migrate the incidents collection and the local files to the coder-owned shape.

What changes, and why:

- `_id` becomes the incident id. There is no second identity field; the old
  ObjectId and the duplicate `incident_id` both go.
- `by_document.<key>.by_coder.<coder>` becomes `by_coder.<coder>.documents.<key>`,
  so everything one coder judged sits in one subtree.
- `groups_by_coder.<coder>` folds into `by_coder.<coder>.groups`.
- Field answers stop being stored per document. `incident_system`,
  `incident_developer`, `incident_deployer`, `incident_deployer_name` and
  `incident_aftermath` describe the incident, so they're answered once per
  (incident, coder) — merged across that coder's documents, which is safe because
  every disagreement in the data is a value versus a blank.
- `incident_id` / `incident_title` are dropped from field answers entirely; the
  incident owns them.
- Empty answers ("" / null / []) are not stored at all.

Quotes and their offsets are copied verbatim — this migration must not lose one.

    $PY migrate_structure.py            # dry run
    $PY migrate_structure.py --apply
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
APPLY = "--apply" in sys.argv
IDENTITY = ("incident_id", "incident_title")


def load_dotenv(path: Path = HERE / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def is_empty(v) -> bool:
    return v is None or v == "" or v == [] or v == {}


def clean_fields(fields: dict) -> dict:
    out = {}
    for fk, fa in (fields or {}).items():
        if fk in IDENTITY or not isinstance(fa, dict):
            continue
        entry = {}
        if not is_empty(fa.get("answer")):
            entry["answer"] = fa["answer"]
        cmt = str(fa.get("comments") or "").strip()
        if cmt:
            entry["comments"] = cmt
        if entry:
            out[fk] = entry
    return out


def merge_fields(into: dict, extra: dict) -> dict:
    """Fold one document's field answers into the incident's. Values win over
    blanks (every conflict in the data is exactly that); list answers union while
    keeping order, so nothing a coder picked is dropped."""
    for fk, fa in extra.items():
        cur = into.get(fk)
        if cur is None:
            into[fk] = dict(fa)
            continue
        a, b = cur.get("answer"), fa.get("answer")
        if isinstance(a, list) and isinstance(b, list):
            cur["answer"] = a + [v for v in b if v not in a]
        elif is_empty(a) and not is_empty(b):
            cur["answer"] = b
        cmts = [c for c in (cur.get("comments"), fa.get("comments")) if c]
        if cmts:
            cur["comments"] = " | ".join(dict.fromkeys(cmts))
    return into


def migrate_incident(old: dict) -> dict:
    inc_id = str(old.get("incident_id") or old.get("_id"))
    by_coder = {}
    for doc_key, entry in (old.get("by_document") or {}).items():
        nested = (entry or {}).get("by_coder")
        # pre-multi-coder documents kept one flat coding on the entry
        readings = nested if isinstance(nested, dict) else (
            {"coder1": entry} if (entry or {}).get("quotes") else {})
        for coder, coding in (readings or {}).items():
            slot = by_coder.setdefault(coder, {"fields": {}, "groups": [], "documents": {}})
            quotes = (coding or {}).get("quotes") or []
            roles = (coding or {}).get("roles") or {}
            if quotes or any(roles.values()):
                slot["documents"][str(doc_key)] = {"quotes": quotes, "roles": roles}
            merge_fields(slot["fields"], clean_fields((coding or {}).get("fields") or {}))
    for coder, groups in (old.get("groups_by_coder") or {}).items():
        by_coder.setdefault(coder, {"fields": {}, "groups": [], "documents": {}})
        by_coder[coder]["groups"] = groups or []
    by_coder = {c: v for c, v in by_coder.items()
                if v["fields"] or v["groups"] or v["documents"]}
    return {
        "_id": inc_id,
        "title": old.get("incident_title") or "",
        "documents": old.get("documents") or [],
        "by_coder": by_coder,
        "created_at": old.get("created_at") or datetime.now(timezone.utc),
        "updated_at": old.get("updated_at") or datetime.now(timezone.utc),
    }


def count_quotes(by_coder: dict) -> int:
    return sum(len((d or {}).get("quotes") or [])
               for c in by_coder.values() for d in (c.get("documents") or {}).values())


def main():
    load_dotenv()
    uri = os.environ.get("MONGO_URI")
    coders = [c.strip() for c in os.environ.get("CODERS", "").split(",") if c.strip()] \
        or ["coder1", "coder2"]

    print(f"{'APPLY' if APPLY else 'DRY RUN'}\n")
    migrated = []
    if uri:
        from pymongo import MongoClient
        db = MongoClient(uri, serverSelectionTimeoutMS=8000)[os.environ.get("MONGO_DB", "incidents")]
        old_docs = list(db.incidents.find())
        before = sum(len((c or {}).get("quotes") or [])
                     for d in old_docs
                     for e in (d.get("by_document") or {}).values()
                     for c in ((e or {}).get("by_coder") or {}).values())
        migrated = [migrate_incident(d) for d in old_docs]
        after = sum(count_quotes(m["by_coder"]) for m in migrated)
        print(f"  incidents: {len(old_docs)}")
        print(f"  quotes before: {before}   after: {after}   "
              f"{'OK' if before == after else '*** MISMATCH ***'}")
        if before != after:
            print("  refusing to apply: quotes would be lost")
            return
        for m in migrated[:3]:
            fields = {c: sorted(v["fields"]) for c, v in m["by_coder"].items()}
            print(f"    {m['_id']}: coders={sorted(m['by_coder'])} fields={fields}")
        if APPLY:
            from incidents_vocab import ensure_collection
            db.incidents.drop()                 # _id changes type, so rewrite wholesale
            ensure_collection(db, "incidents")
            if migrated:
                db.incidents.insert_many(migrated)
            print(f"\n  rewrote {len(migrated)} incident(s); quotes now "
                  f"{sum(count_quotes(d.get('by_coder') or {}) for d in db.incidents.find())}")
    else:
        print("  MONGO_URI not set — local files only")

    # ---- local files ----
    # Built from the migrated collection, not from the old local file: Mongo is
    # the fuller copy (a coder's local file can be stale, or reset by a deploy),
    # so this leaves local and Atlas agreeing. Anything local that Mongo never
    # saw is kept, so un-synced work isn't dropped.
    by_inc = {m["_id"]: m for m in migrated}
    for coder in coders:
        ann_path = HERE / f"annotations.{coder}.json"
        old_ann = json.loads(ann_path.read_text()) if ann_path.exists() else {}
        new_ann = {}
        for m in migrated:
            for doc_key, ev in ((m["by_coder"].get(coder) or {}).get("documents") or {}).items():
                new_ann[doc_key] = ev
        kept_local = 0
        for k, v in old_ann.items():
            if k in new_ann or not isinstance(v, dict):
                continue
            quotes, roles = v.get("quotes") or [], v.get("roles") or {}
            if quotes or any(roles.values()):
                new_ann[k] = {"quotes": quotes, "roles": roles}
                kept_local += 1
        inc_coding = {}
        for inc_id, m in by_inc.items():
            slot = (m["by_coder"] or {}).get(coder)
            if slot and (slot["fields"] or slot["groups"]):
                inc_coding[inc_id] = {"fields": slot["fields"], "groups": slot["groups"]}
        print(f"\n  {coder}: {len(new_ann)} document(s) with evidence "
              f"({kept_local} local-only kept), "
              f"{len(inc_coding)} incident(s) with answers/claims")
        if APPLY:
            ann_path.write_text(json.dumps(new_ann, indent=2, ensure_ascii=False))
            (HERE / f"incident_coding.{coder}.json").write_text(
                json.dumps(inc_coding, indent=2, ensure_ascii=False))
            old_groups = HERE / f"incident_groups.{coder}.json"
            if old_groups.exists():
                old_groups.unlink()
                print(f"    removed {old_groups.name} (folded into incident_coding.{coder}.json)")

    print("\n(dry run — nothing written)" if not APPLY else "\nDone.")


if __name__ == "__main__":
    main()
