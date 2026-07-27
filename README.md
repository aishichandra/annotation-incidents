# Incident coding pipeline

Code journalism-AI incidents from Zotero snapshots, and sync the structured
result to MongoDB Atlas. Three steps:

```
Zotero  ──▶  zotero_import.py  ──▶  zotero_docs.csv
                                         │
                                         ▼
                              app.py  (Flask coding UI)  ──▶  annotations.json
                                         │                     data_annotated.csv
                                         ▼
                              MongoDB Atlas  (incidents collection)
                                         │
                                         ▼
                              mongo_connect.ipynb  (read it back)
```

All commands use the project's interpreter (has flask / pymongo / trafilatura /
pandas installed):

```
PY=~/.pyenv/versions/3.10.3/bin/python
```

## 1. Import from Zotero → `zotero_docs.csv`

Reads the Zotero SQLite DB (read-only) for HTML snapshots in the collection
`Incidents Dashboard Articles`, extracts article markdown with trafilatura, and
writes one row per document to `zotero_docs.csv`.

```
$PY zotero_import.py
```

To change the source collection, edit `COLLECTION` at the top of
[`zotero_import.py`](zotero_import.py).

## 2. Code the documents → the app

```
cp .env.example .env      # then edit .env with your MONGO_URI (the file is git-ignored)
$PY app.py
# open http://127.0.0.1:5001
```

`app.py` loads `.env` automatically on startup. On a host you set the same
variables directly (see Deploy) instead of using a file.

- The document list comes entirely from `zotero_docs.csv` (re-run step 1 and
  restart the app to refresh it).
- Every edit **autosaves** — there is no save button. Each save writes three
  places: `annotations.json` (source of truth), `data_annotated.csv` (flat
  mirror), and, **if `MONGO_URI` is set**, the Atlas `incidents` collection.
- Without `MONGO_URI` the app still works fully offline (JSON/CSV only) — it just
  prints `[mongo] MONGO_URI not set` and skips the sync.
- The coding scheme lives in `schema.json`; controlled vocabularies (systems,
  developers, actors, factors, harms, harmed-parties) live in `vocab.json` and
  drive both the UI options and — via `incidents_vocab.py` — the DB collection.

**Run only one `app.py` at a time.** Two instances writing `annotations.json`
concurrently can corrupt it.

## 3. Read the data in Mongo

Open [`mongo_connect.ipynb`](mongo_connect.ipynb). Run the connect cell once,
then re-run the Overview / Detail cells any time to watch incidents land as you
code in the app.

## Data model (Atlas `incidents` collection)

One document per incident, keyed by `incident_id`. Several source documents can
share an incident; each document's coding is stored under `by_document.<doc_key>`.
The collection's validator and indexes are provisioned automatically by
`incidents_vocab.ensure_collection`, which `app.py` calls on startup.

```
incidents {
  _id,
  incident_id,                     # unique; the grouping key
  incident_title,
  by_document: {                   # one entry per source document
    <doc_key>: {
      fields:  { <field_key>: { answer, comments }, ... },
      quotes:  [ { text, start, end, category?, value?, claim?, role? }, ... ],
      claims:  [ { id, actor[], factor[], harm[], harmed_party[] }, ... ],
      updated_at
    }, ...
  },
  documents: [ { doc_id, url, title }, ... ],   # source URLs in this incident
  created_at, updated_at
}
```

Indexes: unique on `incident_id`, plus `documents.url` and `documents.doc_id`.

## Files

| File | Role |
|------|------|
| `zotero_import.py` | Step 1 — Zotero → `zotero_docs.csv` |
| `app.py` | Step 2 — Flask coding UI + Mongo sync |
| `templates/index.html` | The coding UI |
| `incidents_vocab.py` | vocab.json → UI options + Mongo validator/indexes |
| `schema.json` | Coding scheme (fields + claim roles) |
| `vocab.json` | Controlled vocabularies |
| `mongo_connect.ipynb` | Step 3 — read incidents from Atlas |
| `zotero_docs.csv` | Import output / app input |
| `annotations.json` | App source of truth (per-document coding) |
| `data_annotated.csv` | Flat CSV mirror of annotations |

## Deploy (Railway / Render)

GitHub Pages can't run this — it's a Flask backend. Deploy to a host that runs
Python web services. The repo is already set up for it:

- `requirements.txt` — dependencies
- `Procfile` — `gunicorn app:app --workers 1 …` (**one worker**: the app persists
  to a local `annotations.json`, so concurrent writers would corrupt it)
- `runtime.txt` — Python version

Steps (Railway):

1. New Project → Deploy from GitHub repo → pick `annotation-incidents`.
2. Add environment variables **`MONGO_URI`** and **`MONGO_DB`** (same values as
   your `.env`). Do **not** commit `.env`.
3. Deploy. Railway builds from `requirements.txt` and runs the `Procfile`.

**Caveat — ephemeral filesystem:** hosts wipe the local disk on each
deploy/restart, so `annotations.json` and `data_annotated.csv` don't persist
there. MongoDB is the durable store. To run the coding app durably online you'd
switch its source-of-truth reads from the JSON file to Mongo — a follow-up, not
done here.

## Security note

The Atlas password now lives only in `.env` (git-ignored) and `mongo_connect.ipynb`
reads it from there. **Rotate the credential in Atlas** — it was previously
hard-coded, so treat the old value as compromised. Never commit `.env`; set the
vars directly on your host instead.
