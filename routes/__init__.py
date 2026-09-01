"""The HTTP layer, one module per area of the app.

Each module owns a Blueprint and nothing else: the routes for one part of the
UI, the request parsing they need, and the JSON they return. The work itself
lives below them — storage for the coding on disk, mongo_sync for the mirror,
incidents for rolling documents up into incidents — so a route reads as what it
decides, not as how it is stored.

    pages      the page, the coder list, the health of this process
    schema     the coding scheme the UI builds its menus from
    vocab      the Codebook tab: the codes themselves and what they mean
    docs       the documents, and one coder's evidence on them
    incidents  the cards, and every judgement recorded on one
    sync       Pull and Push
"""
from . import docs, incidents, pages, schema, sync, vocab

BLUEPRINTS = (pages.bp, schema.bp, vocab.bp, docs.bp, incidents.bp, sync.bp)


def register(app) -> None:
    """Attach every blueprint. Registration order is presentation only — the URL
    map is keyed by rule, not by the order rules arrive in."""
    for bp in BLUEPRINTS:
        app.register_blueprint(bp)
