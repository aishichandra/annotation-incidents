# Archive — 2026-10-06, completed and not-completed incidents

Two groups of incidents, set aside when the live coding was reset. `completed_incidents.json` lists both:

- **Completed** (`completed_incidents`): signed off as complete — INC-044, INC-045, INC-046,
  INC-047, INC-050, INC-176, INC-197 (all signed off by Klaudia; Emma had signed off none).
- **Not completed** (`in_progress_incidents`): 49 incidents a coder had coded something on but
  nobody had signed off. Built from the Atlas dump below plus the local files as committed just
  before the reset (a coder's local copy wins, Atlas fills what it lacks — the way the app reads them).

Incidents marked "not an incident" were not archived; they stayed in the live app.

Nothing in this folder is read by the app. It is a frozen record.

| File | Contents |
|---|---|
| `incident_coding.<coder>.json` | each coder's incident-level coding (fields, claims, notes, comment, status) for those incidents — Emma's reading of them included, even though she had not signed off |
| `annotations.<coder>.json` | each coder's highlighted evidence on the documents belonging to those incidents |
| `incident_assignments.json` | the doc → incident map at the time |
| `documents.json` | the text of those incidents' documents — what the highlight offsets point into. One article (BKUHYT6T) is no longer in the live corpus, so its text was recovered from git history (commit `ec29196`) |
| `zotero_docs.csv` | the whole corpus as it stood |
| `codebook/` | `schema.json` + `vocab.json` as they stood — read the codes against these |
| `mongo-dump.json` | raw dump of every collection in Atlas, taken before any reset: the safety copy of everything, completed or not |

The `incident_coding.*` and `annotations.*` files have the same shape as the live
files, so a round can be restored by copying them back under the repo root.
