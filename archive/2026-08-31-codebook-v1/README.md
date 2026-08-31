# Archive — 2026-08-31, codebook v1

The first round of incident coding, set aside because the **codebook changed**:
the categories in `schema.json` / `vocab.json` were redefined, so answers coded
under the old definitions are not comparable with what comes next. Everything
here was live in the app up to commit `d0b0092`.

Nothing in this folder is read by the app. It is a frozen record.

## Read the coding against the codebook it was made with

`codebook/schema.json` and `codebook/vocab.json` are snapshots of the definitions
**as they stood when this coding was done**. The live files at the repo root have
since moved on. Any option label below means what `codebook/` says it meant, not
what the current vocab says.

## What's here

| File | Contents |
|---|---|
| `zotero_docs.csv` | the 41 documents (article text from Zotero) |
| `annotations.coder1.json` | doc-level coding, 36 documents |
| `annotations.coder2.json` | doc-level coding, 17 documents |
| `incident_coding.coder1.json` | incident-level coding as it existed **on disk**, 10 incidents |
| `incident_coding.coder2.json` | empty on disk — the real work was only in Atlas, see below |
| `incident_assignments.json` | doc → incident map, 41 documents across INC-001…INC-043 |
| `data_annotated.coder1.csv` | derived flat mirror (git-ignored at root; kept here for completeness) |
| `codebook/` | `schema.json` + `vocab.json` as of this coding round |
| `mongo-dump.json` | raw dump of the whole Atlas `incidents` database |
| `incident_coding.from-atlas.coder1.json` | **19 incidents** — the real coder1 incident coding |
| `incident_coding.from-atlas.coder2.json` | **17 incidents** — the real coder2 incident coding |
| `incident_document_quotes.from-atlas.json` | 663 highlighted quotes across 31 incidents |

## Important: the local files alone were NOT the full record

The app filled missing coding in from Atlas on read, so most incident-level work
never landed on disk. On disk there were 10 incidents for coder1 and none for
coder2; Atlas held 19 and 17. **The `from-atlas` files above are the only copy of
that work** outside Atlas itself.

The two sides also drifted in shape: on disk a claim group names one `system` and
one `developer`, while Atlas uses `systems` / `developers` arrays (commit
`22d8c0e`, "A group can name several systems and several developers"). The
`from-atlas` files keep the Atlas plural form — it is the newer and truer record.

## Restoring

To put a round back into the app, copy the file back to the repo root under the
name the app expects, e.g.

    cp incident_coding.from-atlas.coder1.json ../../incident_coding.coder1.json
    cp zotero_docs.csv annotations.*.json incident_assignments.json ../../

Restoring the coding only makes sense together with `codebook/` — under the
current vocab, options that were renamed or removed will not match.

The raw Atlas state can be reloaded from `mongo-dump.json` with
`bson.json_util.loads`; each top-level key is a collection name.

## Warning added 2026-08-31, after a re-import

`zotero_import.py` was re-run after this archive was made. It brought the corpus
from 41 to 51 documents, and **re-extracting changed the text of 28 of the 41
documents archived here** — mostly by a character or two, but `73XWH6XB`
("AI Search Has a Citation Problem") moved by 389 characters.

Quote highlights are stored as character offsets into that text. The 663 quotes
in `incident_document_quotes.from-atlas.json` are therefore keyed to the copy of
`zotero_docs.csv` **in this folder**, not to the one at the repo root. Restoring
those quotes against the current CSV will land some of them on the wrong span.

If you ever restore the quotes, restore this folder's `zotero_docs.csv` with them.
