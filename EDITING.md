# Editing this app

A map from *the thing you want to change* to *the file you open*. Line numbers
drift; the function and constant names are the durable part — search for those.

Run it with `python app.py` → <http://127.0.0.1:5001>. There is no build step.
Change a Python file and the dev server reloads itself; change a JS or CSS file
and a browser refresh is enough.

---

## The one rule worth knowing

**Data and rules are defined once, on the Python side, and the UI follows.**

- The **coding scheme** — which fields exist, which claim roles exist, and every
  controlled vocabulary — lives in `schema.json` and `vocab.json`. The UI builds
  itself from them at startup. Adding a harm type or renaming a field is a JSON
  edit, not a code edit.
- The **coding rules** — which roles are required, which are optional — live in
  `config.py` and are sent to the browser by `/api/schema` as `rules`.

So most changes are one edit in one place. Where that is *not* true, it is
listed under [Two-file changes](#two-file-changes) below. Nothing else needs a
matching edit in a second file.

---

## I want to change…

### The coding scheme

| Change | Edit |
|---|---|
| Add/remove a harm, factor, actor type, … | `vocab.json` — the app also offers an "add your own" box in the UI, which writes here for you |
| Group vocabulary options under headings | `vocab.json`, the `<list>_groups` keys |
| Add/rename/remove a **field** | `schema.json` → `fields` |
| Add/remove a **claim role** | `schema.json` → `claim_roles` |
| Which roles a *finished* incident needs | `REQUIRED_CLAIM_ROLES`, `config.py` — derived as "every role that isn't optional", so a new role is required by default |
| Which roles a claim may leave out | `OPTIONAL_CLAIM_ROLES`, `config.py` |
| What makes one *claim* complete | `claim_is_complete()`, `incidents.py` |
| What makes one *incident* complete | `incident_completeness()`, `incidents.py` |
| When a sign-off is withdrawn | `clear_signoff()`, `app.py` |
| The judgements a coder can record | `INCIDENT_STATUSES`, `config.py` |
| What recording a judgement does | `api_set_status()`, `app.py` |

Changing `REQUIRED_CLAIM_ROLES` in `config.py` is enough on its own — the browser
reads it from `/api/schema` at startup. Restart the app and reload the page.

### Who codes

| Change | Edit |
|---|---|
| Add/remove a coder | `CODERS` in `.env` (comma-separated), *not* the code |
| How the active coder is resolved | `current_coder()`, `config.py` |

Each coder gets their own `annotations.<coder>.json` and
`incident_coding.<coder>.json` automatically — no code change needed.

### The incidents view (the card list)

| Change | Edit |
|---|---|
| Card layout / what a card shows | `incidentCard()`, `static/js/30-incidents.js` |
| The characteristic palette down the left | `buildPalette()`, same file |
| Claim groups down the right | `buildGroupsUI()`, `claimRow()`, same file |
| The completion / not-an-incident control | `completeControl()`, same file |
| Splitting live vs set-aside incidents | `loadIncidents()`, same file |
| What the button *decides* | `completenessOf()` — mirrors the Python; see below |
| Saving a claim edit | `saveGroups()`, same file |

### The document view (reading + highlighting)

| Change | Edit |
|---|---|
| How the article renders | `renderArticle()`, `static/js/50-reader.js` |
| Highlight underlines, overlapping lanes | `paintUnderlines()`, `assignLanes()`, same file |
| Where a selection snaps to (word edges) | `snapSpan()`, same file |
| The tag menu after selecting text | `showCategoryMenu()`, same file |
| The right-hand coding form | `renderForm()`, `buildCard()`, `static/js/60-form.js` |

### Look and feel

| Change | Edit |
|---|---|
| Any styling at all | `static/app.css` — one file, no preprocessor |
| Highlight colours per field | `COLORS`, `static/js/10-state.js` |
| Role colours and their order in menus | `ROLES`, `static/js/10-state.js` |

### Behaviour

| Change | Edit |
|---|---|
| Autosave delay (currently 500 ms) | `persistSoon()`, `static/js/80-persist.js` |
| How long a Mongo read is cached (5 s) | `_MONGO_READ_TTL`, `mongo_sync.py` |
| Port (default 5001) | `PORT` env var, or the bottom of `app.py` |

### The server

| Change | Edit |
|---|---|
| Add or change an API route | `app.py` — routes only, nothing else lives there |
| How coding is read/written on disk | `storage.py` |
| Anything MongoDB | `mongo_sync.py` |
| How documents roll up into incidents | `aggregate_incidents()`, `incidents.py` |
| The document list itself | `doc_source.py` (reads `zotero_docs.csv`) |

---

## How the pieces fit

Imports run one way, bottom-up. Nothing below imports anything above it.

```
config.py       paths, coders, schema, the coding rules   (imports nothing local)
doc_source.py   zotero_docs.csv -> the `df` of documents
storage.py      reads/writes the coding on disk        <-+  these two import
mongo_sync.py   the optional MongoDB mirror            <-+  each other
incidents.py    rolls documents up into incident views
app.py          the routes
```

The frontend is nine plain scripts in `static/js/`, loaded in order and sharing
one global scope — no modules, no bundler. The numeric prefixes *are* the load
order; `templates/index.html` lists them with a line each on what they hold.

**Two globals are rebound after startup and must be reached through their
module** — `doc_source.df` and `mongo_sync.mongo_db`. Writing
`from doc_source import df` captures a stale copy. This is the one Python gotcha
in the codebase.

---

## Two-file changes

These are the only places where one change means editing two files. Each is
flagged with a comment in both.

| Change | Both of |
|---|---|
| The completeness check itself | `incident_completeness()` in `incidents.py` **and** `completenessOf()` in `static/js/30-incidents.js` |

The JS copy exists so the button reacts to a drag without a round trip. It is a
**convenience only**: the server recomputes the check before recording any
sign-off and refuses with `409` if it disagrees. So if the two drift, the cost is
a button that looks wrong, never a wrong record — but keep them in step anyway.

Note the *rule* (`REQUIRED_CLAIM_ROLES`) is **not** duplicated — only the shape of
the check is. Changing which roles are required is a one-file edit.

---

## Checking you didn't break it

There is no test suite. What there is:

```bash
python -m pyflakes *.py      # undefined names, unused imports — catches most typos
python app.py                # then click through the view you changed
```

For a frontend change, open the browser console. The app runs with **zero
console errors**; anything there is new and yours.

Two things worth knowing when you change storage:

- `blank_incident_coding()` in `storage.py` lists **every part** of a coder's
  incident-level record. A part left out of that dict is read from Mongo and
  then dropped on the next write — see the comment on `load_incident_coding()`.
- Saves are atomic (`_atomic_write`), and local files win over Mongo on read, so
  a failed sync never loses work. Keep both properties if you touch that path.

---

## Data files: what's safe to delete

| File | Safe to delete? |
|---|---|
| `annotations.<coder>.json` | **No** — the per-document coding |
| `incident_coding.<coder>.json` | **No** — incident answers, claim groups, sign-offs |
| `incident_assignments.json` | **No** — which document is in which incident; without it every document becomes its own incident |
| `zotero_docs.csv` | **No** — the documents being coded |
| `schema.json`, `vocab.json` | **No** — the coding scheme |
| `data_annotated.<coder>.csv` | Yes — derived, rewritten on every save, nothing reads it |
| `server.log`, `__pycache__/`, `*.tmp` | Yes — regenerated |

Everything above is also in git, so a mistaken delete is `git checkout` away.
