# Incident coding pipeline

Code journalism-AI incidents from Zotero snapshots and PDFs, and sync the structured
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
PyMuPDF / pandas installed):

```
PY=~/.pyenv/versions/3.10.3/bin/python
```

## 1. Import from Zotero → `zotero_docs.csv`

Reads the Zotero SQLite DB (read-only) for HTML snapshots and PDF attachments in
the collection `Incidents Dashboard Articles`, extracts article text — markdown
via trafilatura for a snapshot, plain text via PyMuPDF for a PDF — and writes one
row per document to `zotero_docs.csv`: `zotero_key, title, url, date, markdown,
source_file`. If an item carries both a snapshot and a PDF, the snapshot is used.
`date` is the item's publication date, normalised to `YYYY-MM-DD` and left empty
when Zotero has none — Zotero stores it as its own normalised prefix followed by
whatever was typed, so only the first token is kept, and a partial date (a bare
year) is dropped rather than half-recorded.

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

Two of a card's characteristics are read off the documents rather than coded:
**Published** (the date, or the range across the incident's articles, from
`zotero_docs.csv`) and **Domain** (from each article's URL). Nobody types them, so
they cannot drift from the documents they describe; an incident whose articles
Zotero has no date for says so — "2025-11-25 · 1 undated" — instead of showing a
range narrower than the incident.

**Geography/location** and **Translated** are answered on the card itself, from
the codebook, and nowhere else. They describe the incident rather than any one of
its documents — there is no passage to highlight for "this happened in Kenya" and
no claim to drag it into — so they are controlled *fields* (`card_only` in the
schema), not characteristics: absent from the document sidebar and the highlight
tag menu, present in the Codebook tab like every other vocabulary, and saved per
coder to `by_coder.<coder>.fields` as you pick them.

Push / Pull act on the current coder alone: pushing as alice never touches bob's
work in Atlas, and pulling as bob never rewrites alice's local file.

Each incident card ends in a **comment box** for the incident as a whole — a close
call, a question for the team, anything that belongs to the reading but to no one
field or characteristic. It autosaves as you type and is per coder like every other
judgement, so commenting can't leak one coder's reading into another's.

Claim links autosave to Mongo like everything else — dragging a characteristic into
a claim writes `incident_coding.<coder>.json` *and* `by_coder.<coder>.groups` on the
incident, so Push is only ever a bulk re-send. A group's sentence carries two
optional clauses — *using [system]*, *developed by [developer]* — and a group that
isn't about a named system can drop either with the small × beside it (`omit`),
which reads as "inapplicable here" rather than "not answered yet"; `+ system` puts
it back, and so does dragging a value in. Note that a claim stores role/value
pairs, not references: `aggregate_incidents` drops any value no longer coded on a
member document, and a claim left with nothing disappears. An incident whose
documents have all moved away is deleted once nothing is coded on it.

Any incident can also be **flagged as one you are not sure about** — the `⚑ Not
sure` button on the card, and a `⚑` on its tile in the index so flagged readings
are findable without opening them. It is deliberately not a fourth status:
uncertainty cuts across all three, and the readings most worth a second pair of
eyes are often the ones a coder has finished and still doubts. It gates nothing,
can be raised and cleared at any point, and is per coder like every other
judgement. *What* is uncertain goes in the card's comment box — the flag says
"look at this", the comment says why.

## 2c. Judging an incident: complete, or not an incident

Every save already reaches Mongo on its own, so nothing needs uploading by hand.
What the card view adds is a **judgement**: the coder saying their reading of this
incident is finished. Each incident card carries one control, per coder:

| Card shows | Meaning |
|---|---|
| `Needs harm, a linked claim` | Not signed off, and what is missing |
| `Mark complete` | Everything required is coded — sign-off available |
| `✓ Complete · 2026-08-19` + `Undo` | Signed off, with the date |

An incident qualifies when **every required characteristic has at least one
value** — actor, factor, harm and harmed party, with system and developer staying
optional (`REQUIRED_CLAIM_ROLES` in `config.py`, derived from the scheme so a new
role is required unless it is listed as optional) — **and at least one claim group
names an actor and holds a complete claim** (harm + harmed party + factor).
Geography and Translated are fields rather than characteristics, so they never
enter this: they describe the incident and assert nothing.

The second bar is the one that matters: an incident can have a full palette and
still assert nothing. Linking is what turns a pile of characteristics into a claim
about who did what to whom.

The check runs on the server (`incident_completeness`) whenever coding is read,
and again before a sign-off is recorded, so a card rendered before the coding
changed cannot sign off work that no longer qualifies — it gets a 409 naming what
is missing.

A sign-off is **withdrawn only when an edit actually breaks completeness**
(`clear_signoff`). Editing coding that leaves the incident complete keeps it —
otherwise every autosave while coding a member document would silently un-sign
the incident, including edits the check never reads, such as aftermath text.

Sign-offs are per coder and land at `by_coder.<coder>.status` / `.completed_at`
on the incident, so analysis can separate finished coding from work in progress.

### Not an incident

Some documents turn out not to describe an incident at all. **Not an incident**
on any card sets it aside — one click, no confirmation:

- **Nothing is deleted.** The documents stay assigned and the coding stays put.
  An incident is not a record you can remove — it exists because documents point
  at it through the *shared* `incident_assignments.json`, so deleting one would
  unassign its documents and turn each into its own incident again.
- **It is ungated.** Unlike a sign-off, nothing has to be coded first. Judging
  that the material isn't an incident is a finding in its own right, and is
  usually reached long before the coding could ever be complete.
- **It is per coder**, like every other judgement. One coder setting an incident
  aside leaves the other's coding of it untouched — and that disagreement is
  data, not a conflict for the app to resolve.
- **It survives editing.** A sign-off is withdrawn when an edit costs the
  incident its completeness; an exclusion is not, because coding more of
  something does not make it an incident.

Set-aside incidents drop into a collapsed **Not an incident (n)** list at the
foot of the page, where **Restore** puts one back. In Mongo they are
`by_coder.<coder>.status = "not_an_incident"`.

Why it went, when it matters, belongs in the card's **Comments** box — already
the place for a remark about the incident as a whole — so the button doesn't ask
and nothing separate is stored.

All three judgements go through one route, `POST /api/incident/<id>/status`,
with `status` one of `""`, `"complete"`, `"not_an_incident"`
(`INCIDENT_STATUSES` in `config.py`).

## 3. Read the data in Mongo

Open [`mongo_connect.ipynb`](mongo_connect.ipynb). Run the connect cell once,
then re-run the Overview / Detail cells any time to watch incidents land as you
code in the app.

New to MongoDB? Start with [`docs/mongo_guide.ipynb`](docs/mongo_guide.ipynb)
instead — a read-only guided tour of what's stored, why it's shaped that way, and
how to query it, ending in a tidy per-coder table for agreement analysis.

## Changing the code

[`EDITING.md`](EDITING.md) is the map: a table from *the thing you want to
change* to *the file you open*, which changes are a one-line edit to `config.py`
or `vocab.json` rather than code at all, and the single place where one change
means editing two files.

## Learning the codebase

| Guide | For |
|---|---|
| [`docs/mongo_guide.ipynb`](docs/mongo_guide.ipynb) | MongoDB: the data model, queries, the validator, safety |
| [`docs/flask_guide.md`](docs/flask_guide.md) | Flask: how `app.py` and the UI are built, and how to extend them |

## Data model (Atlas `incidents` collection)

One document per incident, keyed by `_id` — the incident id *is* the key, so there
is no second identity field. Everything one coder judges lives in a single subtree,
`by_coder.<coder>`, which is what lets a save be one `$set` that cannot reach
another coder's work. The validator and indexes are provisioned by
`incidents_vocab.ensure_collection`, which `app.py` calls on startup.

```
incidents {
  _id,                                    # "INC-001" — the incident id
  title,
  documents: [ { doc_id, url, title, date }, ... ],  # source articles (shared by all coders)
  by_coder: {
    <coder>: {
      fields:    { <field_key>: { answer, comments? }, ... },   # the INCIDENT's answers
                                          # incident_geography / incident_translated
                                          # hold a list picked from the codebook
      notes:     { <role>: "…" },         # free text naming one characteristic
      groups:    [ { id, actor, system, developer, omit[],
                     claims: [ { id, harm, harmed_parties[], factors[] } ] }, ... ],
      comment:   "…",                     # this coder's remark on the whole incident
      documents: {                        # evidence, per source document
        <doc_key>: {
          quotes: [ { text, start, end, role? | category?, value? }, ... ],
          roles:  { actor[], factor[], harm[], harmed_party[] },
          updated_at
        }, ...
      },
      updated_at
    }, ...
  },
  created_at, updated_at
}
```

**Where a judgement lives follows what it is about.** A quote's offsets only mean
something against one document's text, so evidence is per document. `incident_system`,
`incident_developer`, `incident_deployer`, `incident_deployer_name` and
`incident_aftermath` describe the *incident*, so they're answered once against it
rather than repeated on each of its documents with all but one left blank.

Three rules keep it honest:

- **One identity.** `incident_id` / `incident_title` are never copied into a coder's
  field answers — the incident owns them, so they can't drift.
- **Empty means absent.** An unanswered field isn't stored, so there is no `""` vs
  `null` vs `[]` to disambiguate later.
- **Nothing derived is stored.** The pooled characteristic and field lists a card
  shows are rebuilt by `aggregate_incidents` on read.

Indexes: `documents.url` and `documents.doc_id` (`_id` is indexed by MongoDB).

## Analysis view (`codings` collection)

`incidents` is shaped for writing, and its nesting uses dynamic keys (coder names,
document keys) that no index reaches. Intercoder reliability wants the opposite —
one row per judgement — so that view is **derived**, never authored:

```
$PY build_codings.py            # dry run
$PY build_codings.py --apply
```

```
codings { incident_id, doc_id, coder, kind, role, value, n_quotes, quotes[] }
```

`kind` is `characteristic` (one of the four claim roles) or `field`. A value picked
without any highlight still gets a row with `n_quotes: 0` — the gap an agreement
measure should see. Drop and rebuild it whenever; nothing reads back from it.

## Files

Everything in the repo is one of five things: **code**, **config**, **data**,
**docs**, or **deploy**. Nothing else belongs at the root.

| File | Role |
|------|------|
| **Code** | |
| `zotero_import.py` | Step 1 — Zotero → `zotero_docs.csv` |
| `app.py` | Step 2 — builds the app; the routes live in `routes/` |
| `routes/` | The HTTP layer, one blueprint per area: pages, schema, vocab, docs, incidents, sync |
| `config.py` | Paths, coders, schema, file helpers — imports nothing local |
| `doc_source.py` | `zotero_docs.csv` → the `df` of documents to code |
| `storage.py` | Reads/writes the coding on disk (one file per coder) |
| `mongo_sync.py` | The optional MongoDB mirror; a no-op without `MONGO_URI` |
| `incidents.py` | Rolls per-document coding up into per-incident views |
| `incidents_vocab.py` | vocab.json → UI options + Mongo validator/indexes |
| `templates/index.html` | Page markup; loads the CSS and JS below |
| `static/app.css` | All the styling |
| `static/js/*.js` | The frontend, nine files loaded in order (see index.html) |
| **Config** | |
| `schema.json` | Coding scheme (fields + claim roles) |
| `vocab.json` | Controlled vocabularies |
| `.env` | Secrets + `CODERS` (git-ignored; copy from `.env.example`) |
| **Docs** | |
| `docs/mongo_guide.ipynb` | Learn MongoDB through this project's data |
| `EDITING.md` | Where to change what — start here to edit the code |
| `docs/flask_guide.md` | Learn how the Flask app is built |
| `mongo_connect.ipynb` | Scratch notebook for quick looks at Atlas |
| **Data** | |
| `zotero_docs.csv` | Import output / app input |
| `annotations.<coder>.json` | One coder's evidence per document (quotes + characteristics) |
| `incident_coding.<coder>.json` | One coder's incident-level answers, claim groups + comment |
| `incident_assignments.json` | **Shared** doc → incident mapping (all coders) |
| `data_annotated.<coder>.csv` | Flat CSV mirror of one coder's annotations — rewritten on every save, git-ignored (nothing reads it back) |
| **Deploy** | |
| `Procfile`, `requirements.txt`, `runtime.txt`, `mise.toml` | How the host builds and runs it |

Generated at runtime and git-ignored: `server.log`, `__pycache__/`, `*.tmp`,
`backup-incidents-*.json`, `data_annotated.<coder>.csv`. Safe to delete at any
time — the CSV comes back the next time that coder saves.

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
