# How the coding app is built — a Flask walkthrough

A guide to [`app.py`](../app.py) and [`templates/index.html`](../templates/index.html)
for someone who hasn't written a web app before. Read it next to the code; every
section points at the lines it's describing.

---

## 1. The mental model: two programs talking

A web app is **two programs**, running in different places, exchanging text over
HTTP:

| | Where it runs | Language | This project |
|---|---|---|---|
| **Server** ("backend") | Your laptop / Railway | Python | `app.py` |
| **Client** ("frontend") | Your browser | JavaScript | the `<script>` inside `templates/index.html` |

They never share variables. The *only* thing that crosses between them is an HTTP
**request** (browser → server) and an HTTP **response** (server → browser). Every
feature in this app is some version of:

```
browser: "POST /api/doc/3/annotations, here is JSON of what the coder typed"
server:  "200 OK, here is JSON saying I saved 4 quotes"
```

Flask is the Python library that handles the boring half of that — listening on a
port, parsing the request, matching the URL, turning your return value into a
response. You write the interesting half.

> **Why a server at all?** Because the coding has to be *saved* somewhere shared —
> files on disk and MongoDB. A browser can't write to your disk or hold a database
> password. That's the server's job. This is also why GitHub Pages can't host this
> app: Pages serves static files only, it can't run Python.

---

## 2. The smallest possible Flask app

```python
from flask import Flask
app = Flask(__name__)          # the application object

@app.route("/hello")           # "when a browser asks for /hello…"
def hello():                   # "…run this function"
    return "hi"                # "…and send what it returns back"

app.run(port=5001)             # start listening
```

Three ideas, and they're all you need to read `app.py`:

1. **`app`** — the application object. Created once at [app.py:129](../app.py#L129).
2. **`@app.route(...)`** — a *decorator* that registers the function below it as
   the handler for a URL. The function is called a **view function**.
3. **`app.run(...)`** — starts the development server. In this project it's at the
   very bottom, [app.py:834-838](../app.py#L834-L838), guarded by
   `if __name__ == "__main__":` so it only runs when you type `python app.py` — not
   when gunicorn imports the file in production.

**Nothing runs on a schedule.** A Flask app is passive: code runs only when a
request arrives, or once at import time. That's the single most useful thing to
internalise.

---

## 3. What runs when: import time vs request time

Everything at the top level of `app.py` runs **once**, when the file is imported:

```python
df = pd.read_csv(DATA_CSV)      # line 133 — the document list, read once
mongo_db = connect_mongo()      # line 159 — one database connection, reused
_seed_shared_files()            # line 832 — one-time file migration
```

That's a deliberate choice: reading a 55 KB CSV on *every* request would be waste.
The trade-off is that **editing `zotero_docs.csv` requires restarting the app** —
the `df` in memory is a snapshot.

Everything inside a view function runs **per request**. Annotations are re-read
from disk on every request ([`load_annotations`](../app.py#L211)) rather than cached,
because they change constantly and correctness matters more than speed at this size.

---

## 4. The two kinds of route in this app

### 4a. The page route — returns HTML, once

```python
@app.route("/")
def index():
    return render_template("index.html")
```
[app.py:513-515](../app.py#L513-L515)

`render_template` looks in the `templates/` folder (Flask's convention — the name
is not configurable by accident) and returns its contents as the response body.
This is the *only* HTML this app ever sends. It happens once, when you open the
tab.

### 4b. The API routes — return JSON, many times

Everything else returns data, not markup:

```python
@app.route("/api/docs")
def api_docs():
    ...
    return jsonify([...])       # Python list -> JSON text + the right Content-Type
```
[app.py:791-801](../app.py#L791-L801)

`jsonify` is Flask's "turn this Python object into a JSON response" helper. Return a
dict or a list; Flask does the rest.

**This split is the whole architecture.** One HTML page loads, and from then on the
JavaScript in that page fetches and posts JSON. The page never reloads while you
code. That's what people mean by a "single-page app" and a "JSON API".

---

## 5. Reading the request

A view function takes no arguments describing the request. Instead Flask gives you a
global-ish object called `request` that is magically scoped to the *current*
request. Three ways this app reads from it:

```python
request.args.get("coder")        # ?coder=alice        query string
request.headers.get("X-Coder")   # a request header
request.cookies.get("coder")     # a cookie
request.get_json(force=True)     # the POST body, parsed as JSON
```

All four appear in [`current_coder`](../app.py#L87-L100) and
[`api_save`](../app.py#L817-L830).

### URL variables

Part of a URL can be a parameter:

```python
@app.route("/api/doc/<int:i>")
def api_doc(i):                  # i is an int, extracted from the URL
```
[app.py:803](../app.py#L803)

`<int:i>` means "match digits here, convert to `int`, pass as `i`". If you request
`/api/doc/abc`, Flask returns 404 before your function ever runs — free validation.

There's one other converter in this file:

```python
@app.route("/api/incident/<path:inc_id>/groups", methods=["POST"])
```
[app.py:775](../app.py#L775)

`<path:...>` is like the default string converter but *also matches slashes*. It's
used because an incident ID is coder-entered text that might contain a `/`.

### GET vs POST

`methods=["POST"]` on a route means "only accept POST". The convention this app
follows:

- **GET** — read something, changes nothing, safe to repeat. (`/api/docs`,
  `/api/incidents`)
- **POST** — change something. (`/api/doc/3/annotations`, `/api/push`)

Browsers will happily re-issue a GET (refresh, prefetch, back button), so putting a
save behind GET is how you get mystery duplicate writes.

---

## 6. Follow one save end to end

This is the path worth memorising. A coder types in a comment box:

**1. The browser debounces and posts.** In `index.html`, edits schedule a save
rather than firing one per keystroke:

```js
const res = await fetch('/api/doc/' + curDoc.index + '/annotations', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(curDoc.ann),
});
```

`fetch` is the browser's built-in HTTP client. `JSON.stringify` turns the JS object
into the text that travels over the wire.

**2. Flask matches the URL** `/api/doc/3/annotations` to
[`api_save`](../app.py#L817), converting `3` to an int.

**3. The view function does the work:**

```python
coder = current_coder(strict=True)       # who is this? reject unknown names
store = load_annotations(coder)          # read that coder's whole JSON file
key = df["doc_key"].iloc[i]              # row 3 of the CSV -> its Zotero key
store[key] = request.get_json(force=True)
record_assignment(key, store[key].get("fields", {}))   # shared doc -> incident map
save_annotations(store, coder)           # write JSON + CSV mirror
sync_to_mongo(i, key, doc_ann(store, key), coder)      # mirror into Atlas
return jsonify({"ok": True, "coder": coder, "n": len(...)})
```

**4. The browser reads the reply** and updates the status line. Note the pattern:
read the whole store, mutate one key, write the whole store. At this scale (dozens
of documents) that's simpler and safer than partial updates, and it's why
`_atomic_write` matters — see §8.

---

## 7. How the frontend knows which coder it is

The server needs a coder name on every request; the browser has to supply it. Rather
than editing all ~14 `fetch(...)` call sites, `index.html` wraps `fetch` once:

```js
let CODER = localStorage.getItem('coder') || '';
const _fetch = window.fetch.bind(window);
window.fetch = (input, init) => {
  if (CODER && typeof input === 'string' && input.startsWith('/api/')) {
    input += (input.includes('?') ? '&' : '?') + 'coder=' + encodeURIComponent(CODER);
  }
  return _fetch(input, init);
};
```

Every later `fetch('/api/docs')` silently becomes `fetch('/api/docs?coder=coder1')`.
`localStorage` is a small per-browser key/value store that survives reloads, which
is why each coder picks their name once.

This is a **monkey patch** — replacing a built-in function at runtime. It's the
right call here (one place to reason about, no call site can forget) but it's the
kind of thing to comment loudly, because someone reading `fetch('/api/docs')` later
would otherwise never guess the URL changes.

---

## 8. Writing files without corrupting them

```python
def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)
```
[app.py:341-346](../app.py#L341-L346)

Write to a temp file, then **rename** it over the target. Renaming within a
filesystem is atomic: any reader sees either the entire old file or the entire new
one, never a half-written mix. Without this, a crash mid-write (or two writers
overlapping) leaves you with truncated JSON and a day's coding gone.

This is also why the `Procfile` says `--workers 1`. Two gunicorn workers are two
independent Python processes with no lock between them; both would happily
read-modify-write the same JSON file and one would silently lose. **One worker is a
correctness requirement here, not a performance setting.**

---

## 9. Failing softly vs failing loudly

Mongo sync is wrapped in `try/except` and only prints on failure
([app.py:439-440](../app.py#L439-L440)):

```python
except Exception as e:
    print(f"[mongo] sync failed for {key} ({e.__class__.__name__}: {e})")
```

Deliberate: Atlas being unreachable must not stop you coding. The local JSON file is
the source of truth; Mongo is a mirror you can re-populate later with **Push to
Mongo**. The same philosophy is in [`connect_mongo`](../app.py#L137-L157) — no
`MONGO_URI`, no crash, just a printed notice and offline mode.

The opposite choice appears in `current_coder(strict=True)`, which calls
`abort(400, ...)` on an unknown coder name. Here failing loudly *is* the safe
behaviour: silently filing one person's work under another's name would quietly
corrupt the research data. **Fail soft when the fallback is harmless; fail loud when
it isn't.**

---

## 10. Configuration and secrets

```python
def _load_dotenv(path: Path = HERE / ".env") -> None:
```
[app.py:54-64](../app.py#L54-L64)

A hand-rolled 10-line `.env` reader (avoids a dependency): it parses `KEY=value`
lines into `os.environ` if the file exists. `.env` is git-ignored; on Railway you set
the same variables in the dashboard and there's no file at all. That's why the
function returns quietly when the path is missing.

`os.environ.setdefault` is used rather than assignment so a real environment
variable always wins over the file.

Values read this way: `MONGO_URI`, `MONGO_DB`, `CODERS`, `PORT`, `FLASK_DEBUG`.

---

## 11. Worked example: add a coded field

Say you want to record **"Was a correction issued?"**. Because the schema is data,
not code, this is mostly a JSON edit:

1. **Add it to the scheme** — `schema.json`, in `fields`:
   ```json
   { "key": "correction_issued", "label": "Correction issued?", "type": "multi",
     "options": ["Yes", "No", "Unclear"] }
   ```
   (`"type": "text"` for a free-text box instead.)
2. **Restart the app.** `/api/schema` now serves the new field, and the frontend
   renders it automatically — the UI is generated from the schema, so there is no
   HTML to write.
3. **It saves itself.** Answers live in `fields.correction_issued` inside each
   coder's annotations file, and get a column in `data_annotated.<coder>.csv` and a
   spot in Mongo, because every one of those is built by looping over
   `schema["fields"]`.
4. **Only if it should be a controlled vocabulary** (shared with the DB validator)
   do you touch Python: add it to `FIELD_VOCAB` in
   [`incidents_vocab.py`](../incidents_vocab.py#L13).

Adding a whole new *route* is the other common change:

```python
@app.route("/api/stats")
def api_stats():
    store = load_annotations(current_coder())
    return jsonify({"documents_coded": sum(1 for r in store.values() if has_coding(r))})
```

Restart, then visit `http://127.0.0.1:5001/api/stats` — a new API endpoint is
genuinely that small.

---

## 12. Running and debugging

```bash
PY=~/.pyenv/versions/3.10.3/bin/python
$PY app.py                       # http://127.0.0.1:5001
FLASK_DEBUG=1 $PY app.py         # auto-reload on save + error pages in the browser
```

- **`print()` goes to the terminal**, not the browser. The `[mongo]` and `[coders]`
  lines you see at startup are exactly this.
- **Test the API without the UI** — this is the fastest way to isolate a bug to the
  backend or the frontend:
  ```bash
  curl "http://127.0.0.1:5001/api/coders"
  curl "http://127.0.0.1:5001/api/incidents?coder=coder1" | python -m json.tool | head -40
  ```
- **Browser DevTools → Network tab** shows every `fetch` the page makes, its URL
  (including the `?coder=` the shim appended), the JSON sent and the reply. If a
  save "didn't work", look here first: a red row is a server problem, a green row
  with the wrong data is a frontend problem.
- **`FLASK_DEBUG=1` is for local only.** It exposes an interactive Python console in
  the error page to anyone who can reach the port.

---

## 13. Deployment, in one paragraph

`app.run()` is a development server — single-threaded, not hardened. In production
something else imports your `app` object and serves it. That's the `Procfile`:

```
web: gunicorn app:app --workers 1 --timeout 120
```

`app:app` means "in the module `app`, use the object named `app`". Gunicorn never
runs your `if __name__ == "__main__":` block, which is why nothing important lives
inside it.

The catch on Railway/Render: the **filesystem is ephemeral**. Every deploy or
restart wipes the container's disk, so `annotations.<coder>.json` does not survive
there — MongoDB is the durable store. Locally, the files are the source of truth.
Making the hosted app durable means reading from Mongo rather than the JSON files at
request time; that's a genuine follow-up, not a config flag.

---

## Glossary

| Term | Meaning here |
|---|---|
| **route** | A URL pattern plus the function that answers it |
| **view function** | The Python function under `@app.route` |
| **decorator** | `@something` above a `def` — wraps/registers the function |
| **request / response** | The two halves of one browser↔server exchange |
| **GET / POST** | "read something" / "change something" |
| **endpoint** | One route's URL, e.g. `/api/push` |
| **JSON** | Text format for nested data; `jsonify` (Py) / `JSON.stringify` (JS) produce it |
| **template** | An HTML file Flask serves from `templates/` |
| **static / single-page** | This app sends HTML once, then exchanges only JSON |
| **debounce** | Waiting for typing to pause before saving, instead of saving per keystroke |
| **atomic write** | Write to temp file, rename over target — never a half-written file |
| **WSGI / gunicorn** | The production way to run a Python web app |
| **worker** | One process serving requests; this app must use exactly one |

## Where to read next, in order

1. [app.py:513-521](../app.py#L513-L521) — the two smallest routes, to see the shape.
2. [app.py:803-830](../app.py#L803-L830) — `api_doc` and `api_save`: one document out, one back in.
3. [app.py:626-703](../app.py#L626-L703) — `aggregate_incidents`: pure data-shaping, no Flask.
4. [app.py:378-441](../app.py#L378-L441) — `sync_to_mongo`: the hardest function in the file; the Mongo notebook explains it.
