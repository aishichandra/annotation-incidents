"""The Archive tab: read-only access to coding rounds set aside under archive/.

A round is a folder under archive/ holding `completed_incidents.json` beside
`incident_coding.<coder>.json`, `annotations.<coder>.json` and
`incident_assignments.json` — the same shapes the live app reads, frozen. Older
rounds without that manifest are listed nowhere: they predate it and are only
useful as files. Nothing here writes.
"""
import csv
import json
import re
import sys

from flask import Blueprint, abort, jsonify

from config import HERE
import doc_source


bp = Blueprint("archive", __name__)

ARCHIVE_DIR = HERE / "archive"
_NAME_RE = re.compile(r"[A-Za-z0-9._-]+")


def _round_dir(name: str):
    """A round's folder, or 404. The name is checked against a plain pattern and
    the listing, so it can't climb out of archive/."""
    d = ARCHIVE_DIR / name
    if not _NAME_RE.fullmatch(name) or not (d / "completed_incidents.json").is_file():
        abort(404, f"no archived round {name!r}")
    return d


def _read(path, default):
    try:
        return json.loads(path.read_text() or "null") or default
    except (OSError, ValueError):
        return default


def _documents(d) -> dict:
    """The round's own copy of the document text: {doc_key: {title, url, date,
    markdown}}. Highlights are character offsets into it, so this — not the live
    corpus, which has been re-extracted since — is the text they belong to. A round
    carries it as `documents.json` (just the documents of its incidents); one
    without falls back to its whole `zotero_docs.csv`."""
    path = d / "documents.json"
    if path.is_file():
        return _read(path, {})
    csv.field_size_limit(sys.maxsize)
    with (d / "zotero_docs.csv").open(newline="", encoding="utf-8") as f:
        return {row["zotero_key"]: row for row in csv.DictReader(f)}


def _title(d) -> str:
    """The first heading of the round's README, else its folder name."""
    readme = d / "README.md"
    if readme.is_file():
        for line in readme.read_text().splitlines():
            if line.startswith("# "):
                return line[2:].strip()
    return d.name


@bp.route("/api/archives")
def api_archives():
    """Every archived round, newest first."""
    rounds = []
    for d in sorted(ARCHIVE_DIR.iterdir(), reverse=True) if ARCHIVE_DIR.is_dir() else []:
        if (d / "completed_incidents.json").is_file():
            done = _read(d / "completed_incidents.json", {}).get("completed_incidents", [])
            rounds.append({"name": d.name, "title": _title(d), "incidents": len(done)})
    return jsonify({"rounds": rounds})


@bp.route("/api/archives/<name>/doc/<key>")
def api_archive_doc(name, key):
    """One archived document: its title, link and text as the highlights were made
    against it (a leading heading that only repeats the title is dropped, as in the
    reader)."""
    row = _documents(_round_dir(name)).get(key)
    if row is None:
        abort(404, f"no document {key!r} in this round")
    md, title = row.get("markdown") or "", row.get("title") or key
    lines = md.lstrip().splitlines()
    if lines and lines[0].startswith("# ") \
            and doc_source._norm(lines[0][2:]) == doc_source._norm(title):
        lines = lines[1:]
        while lines and not lines[0].strip():
            lines = lines[1:]
        md = "\n".join(lines)
    return jsonify({"key": key, "title": title, "url": row.get("url") or "",
                    "date": row.get("date") or "", "markdown": md})


@bp.route("/api/archives/<name>")
def api_archive(name):
    """One round: each archived incident with every coder's coding of it — status,
    claims, answers, comment, and the highlighted evidence on its documents."""
    d = _round_dir(name)
    done = _read(d / "completed_incidents.json", {}).get("completed_incidents", [])
    assign = _read(d / "incident_assignments.json", {})
    coders = sorted(p.name[len("incident_coding."):-len(".json")]
                    for p in d.glob("incident_coding.*.json"))
    inc_store = {c: _read(d / f"incident_coding.{c}.json", {}) for c in coders}
    ann_store = {c: _read(d / f"annotations.{c}.json", {}) for c in coders}
    titles = dict(zip(doc_source.df["doc_key"], doc_source.df["title"])) \
        if len(doc_source.df) else {}

    incidents = []
    for inc_id in done:
        keys = [k for k, a in assign.items() if (a or {}).get("incident_id") == inc_id]
        incident = {
            "incident_id": inc_id,
            "title": next((a.get("incident_title") for a in assign.values()
                           if (a or {}).get("incident_id") == inc_id
                           and a.get("incident_title")), ""),
            "documents": [{"key": k, "title": titles.get(k, k)} for k in keys],
            "coders": {},
        }
        for c in coders:
            entry = inc_store[c].get(inc_id)
            docs = {k: ann_store[c][k] for k in keys if ann_store[c].get(k)}
            if not entry and not docs:
                continue
            incident["coders"][c] = {
                **(entry or {}),
                "evidence": [{"doc": k, "title": titles.get(k, k),
                              "quotes": r.get("quotes") or []}
                             for k, r in docs.items()],
            }
        incidents.append(incident)
    return jsonify({"name": name, "title": _title(d), "coders": coders,
                    "incidents": incidents})
