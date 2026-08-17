"""Bring the vocabulary in line with the Tow coding scheme.

Applies the change everywhere a value can be stored — vocab.json and its group
maps, the local coder files, and Atlas — so no coded row is left pointing at a
label that no longer exists.

Three of the renames are **re-definitions, not relabels**: the scheme splits
"transparency" into audience-facing versus internal, and splits citation problems
into fabricated sources versus distorted representation of real ones. This script
applies the closest mapping so nothing is left dangling, then prints every
affected row with its quote so a coder can flip the ones that landed wrong.
Nothing else can decide those — the distinction is in the evidence, not the label.

    $PY migrate_vocab.py            # dry run
    $PY migrate_vocab.py --apply
"""
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
APPLY = "--apply" in sys.argv

# --- straightforward relabels: same concept, scheme's wording -----------------
RENAMES = {
    "systems": {"Chatbot": "Chatbot (general use)",
                "Writing assistance": "Text generation"},
    "actor": {"Disinformation actor": "Mis/disinformation actor"},
    "factor": {
        "AI-based impersonation of journalists/outlets":
            "AI-based hoaxing/counterfeiting of journalists/outlets",
        "AI-based mimicry of journalists' likeness/style":
            "AI-based mimicry of established journalists/outlets",
        "Deployment of AI with intention to harm or mislead":
            "Deployment of AI with intention to harm, mislead, or troll",
    },
    "harm": {},
    "developers": {},
    "harmed_party": {},
}

# --- re-definitions: mapped provisionally, every row printed for review -------
NEEDS_REVIEW = {
    "factor": {"Lack of transparency/disclosure": "Lack of audience disclosure"},
    "harm": {"Citation issues": "Citation fabrication",
             "Inaccurate summarization": "Distorted source representation"},
}

# --- two codes collapse into one ---------------------------------------------
MERGES = {"actor": {"Third-party aggregator/content farm": "Content farm/SEO-manipulation actor",
                    "SEO/platform-manipulation actor": "Content farm/SEO-manipulation actor"}}

# --- codes the scheme has and the vocab lacks; value -> group to file it under -
ADDITIONS = {
    "developers": [("Hybrid/in-house tool on vendor model", None)],
    "actor": [("Government/state actor", None)],
    "harm": [("Legal burden", "Labor/economic harms")],
}

DROPS = {"actor": ["other"]}          # never used, not in the scheme

# vocab list name -> the role/field tag values are stored under
TAGGED_AS = {"systems": "incident_system", "developers": "incident_developer",
             "actor": "actor", "factor": "factor",
             "harm": "harm", "harmed_party": "harmed_party"}


def all_maps():
    """value-mapping per vocab list, merging every kind of change."""
    out = {}
    for src in (RENAMES, NEEDS_REVIEW, MERGES):
        for vk, m in src.items():
            out.setdefault(vk, {}).update(m)
    return out


def load_dotenv(path: Path = HERE / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def remap_list(values, mapping):
    """Rename in place, dropping duplicates a merge creates but keeping order."""
    out = []
    for v in values or []:
        nv = mapping.get(v, v)
        if nv not in out:
            out.append(nv)
    return out


def migrate_vocab(vocab, maps):
    for vk, mapping in maps.items():
        if vk in vocab:
            vocab[vk] = remap_list(vocab[vk], mapping)
        gk = vk + "_groups"
        if isinstance(vocab.get(gk), dict):
            vocab[gk] = {label: remap_list(vals, mapping)
                         for label, vals in vocab[gk].items()}
    for vk, adds in ADDITIONS.items():
        for value, group in adds:
            if value not in vocab.get(vk, []):
                vocab.setdefault(vk, []).append(value)
            gk = vk + "_groups"
            if group and isinstance(vocab.get(gk), dict) and value not in vocab[gk].get(group, []):
                vocab[gk].setdefault(group, []).append(value)
    for vk, drops in DROPS.items():
        vocab[vk] = [v for v in vocab.get(vk, []) if v not in drops]
    return vocab


def remap_coding(coding, by_tag):
    """Rewrite one document's evidence: role lists and the value on each quote."""
    n = 0
    roles = coding.get("roles") or {}
    for role, vals in roles.items():
        m = by_tag.get(role)
        if m:
            new = remap_list(vals, m)
            n += sum(1 for a, b in zip(vals, new) if a != b) + max(0, len(vals) - len(new))
            roles[role] = new
    for q in coding.get("quotes") or []:
        tag = q.get("role") or q.get("category")
        m = by_tag.get(tag)
        if m and q.get("value") in m:
            q["value"] = m[q["value"]]
            n += 1
    return n


def remap_fields(fields, by_tag):
    n = 0
    for fk, fa in (fields or {}).items():
        m = by_tag.get(fk)
        if not m:
            continue
        ans = fa.get("answer")
        if isinstance(ans, list):
            new = remap_list(ans, m)
            if new != ans:
                fa["answer"] = new
                n += 1
        elif ans in m:
            fa["answer"] = m[ans]
            n += 1
    return n


def remap_groups(groups, by_tag):
    """Claim groups hold values in their own slots."""
    n = 0
    for g in groups or []:
        for slot, tag in (("actor", "actor"), ("system", "incident_system"),
                          ("developer", "incident_developer")):
            m = by_tag.get(tag)
            if m and g.get(slot) in m:
                g[slot] = m[g[slot]]
                n += 1
        for cl in g.get("claims") or []:
            if by_tag.get("harm") and cl.get("harm") in by_tag["harm"]:
                cl["harm"] = by_tag["harm"][cl["harm"]]
                n += 1
            for key, tag in (("harmed_parties", "harmed_party"), ("factors", "factor")):
                if by_tag.get(tag):
                    cl[key] = remap_list(cl.get(key), by_tag[tag])
    return n


def main():
    load_dotenv()
    maps = all_maps()
    by_tag = {TAGGED_AS[vk]: m for vk, m in maps.items() if vk in TAGGED_AS and m}
    print(f"{'APPLY' if APPLY else 'DRY RUN'}\n")

    for vk, m in maps.items():
        for a, b in m.items():
            print(f"  {TAGGED_AS.get(vk, vk):18s} {a!r}\n  {'':18s}   -> {b!r}")
    for vk, adds in ADDITIONS.items():
        for v, g in adds:
            print(f"  {TAGGED_AS.get(vk, vk):18s} + {v!r}" + (f"  (group: {g})" if g else ""))
    for vk, drops in DROPS.items():
        for v in drops:
            print(f"  {TAGGED_AS.get(vk, vk):18s} - {v!r}")

    vocab = json.loads((HERE / "vocab.json").read_text())
    new_vocab = migrate_vocab(json.loads(json.dumps(vocab)), maps)
    print(f"\n  vocab.json: " + ", ".join(
        f"{k} {len(vocab.get(k, []))}->{len(new_vocab.get(k, []))}"
        for k in ("systems", "developers", "actor", "factor", "harm", "harmed_party")))

    # ---- local files ----
    changed = 0
    coders = [c.strip() for c in os.environ.get("CODERS", "").split(",") if c.strip()] or ["coder1", "coder2"]
    local = {}
    for coder in coders:
        ann_p, inc_p = HERE / f"annotations.{coder}.json", HERE / f"incident_coding.{coder}.json"
        ann = json.loads(ann_p.read_text()) if ann_p.exists() else {}
        inc = json.loads(inc_p.read_text()) if inc_p.exists() else {}
        n = sum(remap_coding(v, by_tag) for v in ann.values())
        for e in inc.values():
            n += remap_fields(e.get("fields"), by_tag) + remap_groups(e.get("groups"), by_tag)
        local[coder] = (ann_p, ann, inc_p, inc)
        changed += n
        print(f"  {coder}: {n} value(s) rewritten locally")

    # ---- Atlas ----
    review = []
    db = None
    if os.environ.get("MONGO_URI"):
        from pymongo import MongoClient
        db = MongoClient(os.environ["MONGO_URI"], serverSelectionTimeoutMS=8000)[
            os.environ.get("MONGO_DB", "incidents")]
        n = 0
        docs = list(db.incidents.find())
        for inc in docs:
            for coder, slot in (inc.get("by_coder") or {}).items():
                n += remap_fields((slot or {}).get("fields"), by_tag)
                n += remap_groups((slot or {}).get("groups"), by_tag)
                for doc_id, ev in ((slot or {}).get("documents") or {}).items():
                    n += remap_coding(ev, by_tag)
                    # collect the rows a human has to re-check
                    for q in ev.get("quotes") or []:
                        tag = q.get("role") or q.get("category")
                        for vk, m in NEEDS_REVIEW.items():
                            if TAGGED_AS[vk] == tag and q.get("value") in m.values():
                                review.append((inc["_id"], coder, doc_id, tag,
                                               q["value"], (q.get("text") or "")[:150]))
        print(f"  atlas: {n} value(s) rewritten across {len(docs)} incident(s)")

    if not APPLY:
        print("\n(dry run — nothing written)")
        return

    (HERE / "vocab.json").write_text(json.dumps(new_vocab, indent=2, ensure_ascii=False))
    for coder, (ann_p, ann, inc_p, inc) in local.items():
        ann_p.write_text(json.dumps(ann, indent=2, ensure_ascii=False))
        inc_p.write_text(json.dumps(inc, indent=2, ensure_ascii=False))
    if db is not None:
        for inc in docs:
            db.incidents.replace_one({"_id": inc["_id"]}, inc)
    print("\n  written.")

    if review:
        out = HERE / "vocab_review.md"
        lines = ["# Rows to re-check after the vocabulary re-definitions", "",
                 "The scheme splits two codes along a line only the evidence can settle.",
                 "Each row below was mapped to the closest new code; flip any that are wrong.", "",
                 "- **Lack of audience disclosure** — was the missing disclosure *to the audience*?",
                 "  If it was staff not being told, it belongs under",
                 "  *Lack of internal transparency about AI-related management decisions*.",
                 "- **Citation fabrication** — source or quote does not exist / claim not in it.",
                 "- **Distorted source representation** — source is real and correctly cited, but",
                 "  its substance was mischaracterised.", "",
                 "| incident | coder | document | tag | mapped to | quote |",
                 "|---|---|---|---|---|---|"]
        for r in sorted(review):
            q = r[5].replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} | {r[4]} | {q} |")
        out.write_text("\n".join(lines) + "\n")
        print(f"  {len(review)} row(s) need review -> {out.name}")


if __name__ == "__main__":
    main()
