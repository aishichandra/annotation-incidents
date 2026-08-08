# Incident coding pipeline

Code journalism-AI incidents from Zotero snapshots, and sync the structured
result to MongoDB Atlas. Three steps:

```
Zotero  ──▶  zotero_import.py  ──▶  zotero_docs.csv ──▶ mongo_register_docs.py
                                         │                        │ (new docs only)
                                         ▼                        │
                              app.py  (Flask coding UI)  ──▶  annotations.<coder>.json
                                         │  ▲                  data_annotated.<coder>.csv
                                         ▼  │ fills in coding the local files lack
                              MongoDB Atlas  (incidents collection)
                                         │
                                         ▼
                              mongo_connect.ipynb  (read it back)
```

The article **text** only ever flows from `zotero_docs.csv`; Mongo stores coding,
not articles. The app re-reads that CSV whenever it changes and fills missing coding
in from Mongo on read, so both sides stay current without a restart.

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

Items in Zotero's **trash** are skipped, and articles saved twice are de-duplicated
by URL keeping the oldest copy — so re-adding an article (which leaves the old copy
trashed under a *different* item key) doesn't import it twice. Because coding is
keyed by Zotero item key, re-adding an already-coded article gives it a new key; the
existing coding then has to be re-pointed at that key in `annotations.<coder>.json`,
`incident_assignments.json`, and Mongo.

### Adding new articles to an existing project

```
$PY zotero_import.py                    # Zotero → zotero_docs.csv
$PY mongo_register_docs.py              # dry run: what's new
$PY mongo_register_docs.py --apply      # register the new docs in Mongo
git add -A && git commit -m "…" && git push    # → the deployed app picks them up
```

[`mongo_register_docs.py`](mongo_register_docs.py) only ever *adds* documents Mongo
has never seen. It never writes or deletes coding, so unlike the app's **Push**
button it cannot erase a coder's Atlas work. The `git push` is what matters for a
deployed app: the article text lives in the CSV, not in Mongo.

## 2. Code the documents → the app

```
cp .env.example .env      # then edit .env with your MONGO_URI (the file is git-ignored)
$PY app.py
# open http://127.0.0.1:5001
```

`app.py` loads `.env` automatically on startup. On a host you set the same
variables directly (see Deploy) instead of using a file.

- The document list comes entirely from `zotero_docs.csv`. Re-run step 1 and the
  running app picks the new articles up on the next request — no restart needed.
- Every edit **autosaves** — there is no save button. Each save writes three
  places: `annotations.<coder>.json` (source of truth), `data_annotated.<coder>.csv`
  (flat mirror), and, **if `MONGO_URI` is set**, the Atlas `incidents` collection.
- Without `MONGO_URI` the app still works fully offline (JSON/CSV only) — it just
  prints `[mongo] MONGO_URI not set` and skips the sync.
- The coding scheme lives in `schema.json`; controlled vocabularies (systems,
  developers, actors, factors, harms, harmed-parties) live in `vocab.json` and
  drive both the UI options and — via `incidents_vocab.py` — the DB collection.

**Run only one `app.py` at a time.** Two instances writing the same annotations
file concurrently can corrupt it. (Two *coders* through one instance is fine —
that's what the next section is about.)

## 2b. Coding with more than one coder

For intercoder reliability, several people code the **same** articles grouped into
the **same** incidents, but form their judgements **independently**. Set the coders
in `.env`:

```
CODERS=alice,bob
```

Each coder picks their name from the **Coding as** dropdown in the toolbar (stored
in the browser, so each person sets it once). What that changes:

| Shared by all coders | Private to each coder |
|---|---|
| The document list (`zotero_docs.csv`) | Field answers and comments |
| The coding scheme (`schema.json`, `vocab.json`) | Highlighted quotes |
| **Which document belongs to which incident** (`incident_assignments.json`) | Characteristics (actor / harm / factor / harmed party) |
| | Claim groupings dragged in the card view |

So if alice files an article under `INC-004`, bob opens that article and already
sees `INC-004` filled in — nobody codes a different set of incidents — but bob sees
none of alice's characteristics, quotes or comments. Incident membership is
deliberately *not* a private judgement: whoever sets it moves the article for
everyone.

Each incident card lists its source documents with a small badge showing **which
coders have coded it** — progress only, never their codes, so coders stay blind to
each other's judgements while coding.

Push / Pull act on the current coder alone: pushing as alice never touches bob's
work in Atlas, and pulling as bob never rewrites alice's local file.

**Migrating an existing project:** on first start the old `annotations.json` and
`incident_groups.json` become the *first* coder's files, and
`incident_assignments.json` is seeded from the incidents already coded. Existing
Atlas documents are read back as the first coder's work — no migration script and
no re-coding needed.

## 3. Read the data in Mongo

Open [`mongo_connect.ipynb`](mongo_connect.ipynb). Run the connect cell once,
then re-run the Overview / Detail cells any time to watch incidents land as you
code in the app.

New to MongoDB? Start with [`docs/mongo_guide.ipynb`](docs/mongo_guide.ipynb)
instead — a read-only guided tour of what's stored, why it's shaped that way, and
how to query it, ending in a tidy per-coder table for agreement analysis.

## Learning the codebase

| Guide | For |
|---|---|
| [`docs/mongo_guide.ipynb`](docs/mongo_guide.ipynb) | MongoDB: the data model, queries, the validator, safety |
| [`docs/flask_guide.md`](docs/flask_guide.md) | Flask: how `app.py` and the UI are built, and how to extend them |

## Data model (Atlas `incidents` collection)

One document per incident, keyed by `incident_id`. Several source documents can
share an incident, and several coders can code each source document — so coding
is nested two levels: `by_document.<doc_key>.by_coder.<coder>`. The collection's
validator and indexes are provisioned automatically by
`incidents_vocab.ensure_collection`, which `app.py` calls on startup.

```
incidents {
  _id,
  incident_id,                     # unique; the grouping key (shared by all coders)
  incident_title,
  by_document: {                   # one entry per source document
    <doc_key>: {
      by_coder: {                  # one reading per coder — never overwrite each other
        <coder>: {
          fields:  { <field_key>: { answer, comments }, ... },
          quotes:  [ { text, start, end, category?, value?, role? }, ... ],
          roles:   { actor[], factor[], harm[], harmed_party[] },
          updated_at
        }, ...
      }
    }, ...
  },
  documents: [ { doc_id, url, title }, ... ],   # source URLs in this incident (shared)
  groups_by_coder: {                            # drag-to-group claims, per coder
    <coder>: [ { id, members: [ { role, value }, ... ] }, ... ]
  },
  created_at, updated_at
}
```

Pooled characteristic / field lists are deliberately **not** stored — they're fully
derivable from `by_document`, so the incident document stays lean.

Documents written before multi-coder support kept one coding flat on
`by_document.<doc_key>` and a single `groups` array; both shapes still validate and
are read back as the first coder's work.

Indexes: unique on `incident_id`, plus `documents.url` and `documents.doc_id`.

## Files

Everything in the repo is one of five things: **code**, **config**, **data**,
**docs**, or **deploy**. Nothing else belongs at the root.

| File | Role |
|------|------|
| **Code** | |
| `zotero_import.py` | Step 1 — Zotero → `zotero_docs.csv` |
| `app.py` | Step 2 — Flask coding UI + Mongo sync |
| `templates/index.html` | The coding UI (HTML + all the frontend JS) |
| `incidents_vocab.py` | vocab.json → UI options + Mongo validator/indexes |
| **Config** | |
| `schema.json` | Coding scheme (fields + claim roles) |
| `vocab.json` | Controlled vocabularies |
| `.env` | Secrets + `CODERS` (git-ignored; copy from `.env.example`) |
| **Docs** | |
| `docs/mongo_guide.ipynb` | Learn MongoDB through this project's data |
| `docs/flask_guide.md` | Learn how the Flask app is built |
| `mongo_connect.ipynb` | Scratch notebook for quick looks at Atlas |
| **Data** | |
| `zotero_docs.csv` | Import output / app input |
| `annotations.<coder>.json` | App source of truth — one coder's per-document coding |
| `incident_groups.<coder>.json` | One coder's drag-to-group claim links |
| `incident_assignments.json` | **Shared** doc → incident mapping (all coders) |
| `data_annotated.<coder>.csv` | Flat CSV mirror of one coder's annotations |
| **Deploy** | |
| `Procfile`, `requirements.txt`, `runtime.txt`, `mise.toml` | How the host builds and runs it |

Generated at runtime and git-ignored: `server.log`, `__pycache__/`, `*.tmp`,
`backup-incidents-*.json`. Safe to delete at any time.

## Deploy (Railway / Render)

GitHub Pages can't run this — it's a Flask backend. Deploy to a host that runs
Python web services. The repo is already set up for it:

- `requirements.txt` — dependencies
- `Procfile` — `gunicorn app:app --workers 1 …` (**one worker**: the app persists
  to local JSON files, so concurrent writers would corrupt them)
- `runtime.txt` — Python version

Steps (Railway):

1. New Project → Deploy from GitHub repo → pick `annotation-incidents`.
2. Add environment variables **`MONGO_URI`**, **`MONGO_DB`** and **`CODERS`** (same
   values as your `.env`). Do **not** commit `.env`.
3. Deploy. Railway builds from `requirements.txt` and runs the `Procfile`.

**Caveat — ephemeral filesystem:** hosts wipe the local disk on each
deploy/restart, so `annotations.<coder>.json` and `data_annotated.<coder>.csv` don't
persist there. MongoDB is the durable store, and reads now fall back to it: on any
document the local file has no coding for, `load_annotations` fills in whatever
Mongo holds for that coder, so a redeploy no longer looks like lost work. A document
the local file *does* have coding for is left alone, so a save whose Mongo sync
failed can't be overwritten by Mongo's older copy — use **Pull** for the deliberate
"Mongo wins outright" direction.

The document list is the exception: it comes from `zotero_docs.csv`, which is
committed to the repo, because Mongo stores no article text. New articles reach a
deployed app by being committed and pushed (step 3 below), not by being written to
Mongo.

## Security note

The Atlas password now lives only in `.env` (git-ignored) and `mongo_connect.ipynb`
reads it from there. **Rotate the credential in Atlas** — it was previously
hard-coded, so treat the old value as compromised. Never commit `.env`; set the
vars directly on your host instead.
