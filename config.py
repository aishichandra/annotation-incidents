"""Paths, coders, schema and the small file helpers everything else builds on.

This module is the bottom of the import graph: it imports nothing from the rest
of the app, so anything here is safe to use from any other module.

  paths + .env       HERE, DATA_CSV, SCHEMA_JSON, annotations_path(), ...
  coders             CODERS, current_coder() - who a request belongs to
  schema             DEFAULT_SCHEMA, load_schema(), clean_fields()
  file helpers       _read_json(), _atomic_write()
"""
import json
import os
from pathlib import Path

from flask import abort, request

from incidents_vocab import apply_vocab_to_schema


HERE = Path(__file__).parent
DATA_CSV = HERE / "zotero_docs.csv"   # produced by zotero_import.py (single source)
SCHEMA_JSON = HERE / "schema.json"
# Shared across coders: which document sits in which incident.
ASSIGNMENTS_JSON = HERE / "incident_assignments.json"
# Pre-multi-coder file; migrated into the first coder's file on startup.
LEGACY_ANNOTATIONS_JSON = HERE / "annotations.json"


def _load_dotenv(path: Path = HERE / ".env") -> None:
    """Load KEY=VALUE lines from a local .env into os.environ (no dependency).
    On a host (Railway/Render) there's no .env — the env vars are set directly."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

# Who may code. Override with e.g. CODERS="aisvarya,priya" (env or .env). The
# first name is also where pre-multi-coder files and Mongo records are filed.
CODERS = [c.strip() for c in os.environ.get("CODERS", "").split(",") if c.strip()] \
    or ["coder1", "coder2"]
LEGACY_CODER = CODERS[0]


def annotations_path(coder: str) -> Path:
    return HERE / f"annotations.{coder}.json"


def incident_coding_path(coder: str) -> Path:
    """One coder's incident-level coding: the incident's field answers and its
    claim groups, keyed by incident id."""
    return HERE / f"incident_coding.{coder}.json"


def annotated_csv_path(coder: str) -> Path:
    return HERE / f"data_annotated.{coder}.csv"


def current_coder(strict: bool = False) -> str:
    """The coder this request belongs to: `?coder=`, `X-Coder`, or the cookie.

    Unknown names fall back to the first coder so a bare URL still works; on
    writes (`strict`) an explicit unknown name is rejected instead, so a typo
    can't silently file one coder's work under another's name."""
    raw = (request.args.get("coder") or request.headers.get("X-Coder")
           or request.cookies.get("coder") or "").strip()
    if raw in CODERS:
        return raw
    if strict and raw:
        abort(400, f"unknown coder {raw!r} (expected one of {', '.join(CODERS)})")
    return CODERS[0]


# type: "text" = free text; "multi" = pick several from `options` (+ add your own)
DEFAULT_SCHEMA = {
    "fields": [
        {"key": "incident_id", "label": "Incident ID", "type": "text",
         "justify": False, "comments": False},
        {"key": "incident_title", "label": "Incident title", "type": "text",
         "justify": False, "comments": False},
        # Only free text about the incident as a whole lives here. Anything
        # picked from a controlled vocabulary is a characteristic (claim_roles
        # below), and free text belonging to one of those — the inciting actor's
        # name — is a note on that role, not a field.
        {"key": "incident_aftermath", "label": "Incident aftermath", "type": "text"},
    ],
    # Characteristics coded per document as flat multiselects (no linking here).
    # Linking values into claims happens in the incident card view instead.
    # Display order, matching the UI and the coding scheme's own order.
    "claim_roles": [
        {"role": "system", "label": "System", "options": []},
        {"role": "developer", "label": "Developer", "options": []},
        {"role": "actor", "label": "Actor", "options": [],
         "note_label": "Inciting actor(s) name"},
        {"role": "factor", "label": "Factor", "options": []},
        {"role": "harm", "label": "Harm", "options": []},
        {"role": "harmed_party", "label": "Harmed party", "options": []},
    ],
}

# The four characteristic roles, in order. Selected flat per document; grouped
# into claims only in the incident card view.
ROLE_KEYS = [r["role"] for r in DEFAULT_SCHEMA["claim_roles"]]

# The incident's identity. Answered once for the incident and owned by it, so
# these are never stored inside a coder's field answers — they'd be a copy that
# can drift from the incident they're filed under.
IDENTITY_FIELDS = ("incident_id", "incident_title")


def is_empty(v) -> bool:
    """Nothing was answered. One rule, so "" / None / [] can't mean the same
    thing three different ways — an unanswered field simply isn't stored."""
    return v is None or v == "" or v == [] or v == {}


def clean_fields(fields: dict) -> dict:
    """A coder's field answers, ready to store: identity dropped (the incident
    owns it) and every empty answer or comment omitted rather than recorded as
    one of three flavours of blank."""
    out = {}
    for fk, fa in (fields or {}).items():
        if fk in IDENTITY_FIELDS or not isinstance(fa, dict):
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

# The characteristics a claim's optional "using … developed by …" clauses draw
# from. They are ordinary roles like any other; a claim simply reads as complete
# without them.
OPTIONAL_CLAIM_ROLES = ("system", "developer")


def load_schema() -> dict:
    if not SCHEMA_JSON.exists():
        SCHEMA_JSON.write_text(json.dumps(DEFAULT_SCHEMA, indent=2, ensure_ascii=False))
    schema = json.loads(SCHEMA_JSON.read_text())
    # Overlay controlled vocab so the UI options always match the DB.
    return apply_vocab_to_schema(schema)


def save_schema(schema: dict) -> None:
    SCHEMA_JSON.write_text(json.dumps(schema, indent=2, ensure_ascii=False))


def _read_json(path: Path) -> dict:
    if path.exists():
        text = path.read_text().strip()
        if text:
            return json.loads(text)
    return {}


def _atomic_write(path: Path, text: str) -> None:
    """Write via a temp file + rename so a crash/overlap can't leave a
    half-written (corrupt) file behind."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)
