"""The app itself: what it is made of, and how a request finds its way in.

Run:  python app.py     (http://127.0.0.1:5001; FLASK_DEBUG=1 to reload on edit)
In production gunicorn imports `app:app` — see the Procfile.

Multiple coders (intercoder reliability)
---------------------------------------
Several coders code the *same* documents and the *same* incidents independently:

- Shared by everyone: the document list (zotero_docs.csv), the coding scheme
  (schema.json / vocab.json) and — crucially — which document belongs to which
  incident. That grouping lives in incident_assignments.json (doc_key ->
  incident_id + title) so every coder sees an identical set of incidents.
- Private per coder: every interpretive judgement. Each coder writes their own
  annotations.<coder>.json (evidence per document), incident_coding.<coder>.json
  (the incident's field answers, card answers and claim groups) and
  data_annotated.<coder>.csv. In MongoDB it all sits under `by_coder.<coder>` on
  the incident, so one $set can never reach another coder's work.

Where a judgement lives follows what it is about. A quote's offsets only mean
something against one document, so evidence — the quotes and the characteristics
they justify — is per document. What is true of the incident as a whole (its
aftermath, where it happened, whether the article was translated, the inciting
actor's name) is answered once against the incident.

Every controlled-vocabulary selection coded on a document is a characteristic,
including system and developer. They are all roles, coded the same way, tagged
the same way on a quote, and dragged into a claim the same way. Geography and
Translated are the exception that proves it: nothing in a document is
highlighted to justify them, so they are fields answered on the card.

The active coder comes from `?coder=`, the `X-Coder` header, or the `coder`
cookie, and must be one of CODERS (set the CODERS env var, comma-separated).

Layout
------
Modules import in one direction, bottom-up, and the routes sit on top:

    config.py       paths, coders, schema, file helpers   (imports nothing local)
    doc_source.py   zotero_docs.csv -> the `df` of documents
    storage.py      read/write the coding on disk         <-+ these two import
    mongo_sync.py   the optional MongoDB mirror           <-+ each other
    incidents.py    roll documents up into incident views
    routes/         the HTTP layer, one blueprint per area of the UI
    app.py          this file: builds the app out of them

Two module-level values are rebound after import and must always be reached
through their module — `doc_source.df` and `mongo_sync.mongo_db` — never bound
by name, which would capture a stale copy.
"""
import os

from flask import Flask

import routes
from doc_source import refresh_docs
from storage import _seed_shared_files


def create_app() -> Flask:
    """Build the app: the blueprints, and the one thing that must happen before
    every request."""
    flask_app = Flask(__name__)

    @flask_app.before_request
    def _refresh_before_request() -> None:
        # Re-reads zotero_docs.csv only when it has changed on disk, so a fresh
        # import shows up in a running app for one stat() per request.
        refresh_docs()

    routes.register(flask_app)
    return flask_app


refresh_docs()
_seed_shared_files()

# Module-level, because gunicorn imports it by name: `gunicorn app:app`.
app = create_app()


if __name__ == "__main__":
    # Local dev entrypoint; gunicorn skips this block. PORT is provided by the
    # host, and defaults to 5001 because macOS listens on 5000 (AirPlay).
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=bool(os.environ.get("FLASK_DEBUG")))
