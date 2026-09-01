// Mongo sync.
// Push local coding up / pull remote coding down, plus incident id and
// title lookups used when coding a document.

import { CODER } from './coder.js';
import { fieldAnn } from './reader.js';

// Push everything local (per-document coding + incident groups + pooled lists)
// up to Mongo. Non-destructive to local data.
export async function pushToMongo() {
  const btn = document.getElementById('pushBtn');
  const status = document.getElementById('status');
  if (!confirm(`Push ${CODER}'s coding and claim groups up to MongoDB? Other coders' work is left as it is.`)) return;
  btn.disabled = true;
  status.textContent = 'Pushing to Mongo…';
  try {
    const res = await fetch('/api/push', { method: 'POST' });
    const j = await res.json();
    if (!res.ok || !j.ok) throw new Error(j.error || 'push failed');
    status.textContent = `Pushed ${j.documents} document(s), ${j.incidents} incident(s), ${j.groups} group(s) as ${j.coder} ✓`;
  } catch (e) {
    status.textContent = 'Push failed: ' + e.message;
  } finally {
    btn.disabled = false;
  }
}

// Manual bring-back: overwrite local annotations with Mongo's copy (Mongo wins),
// then reload so the fetched coding shows. Guard against a double-click.
export async function pullFromMongo() {
  const btn = document.getElementById('pullBtn');
  const status = document.getElementById('status');
  if (!confirm(`Pull ${CODER}'s annotations from MongoDB? Mongo's copy overwrites ${CODER}'s local edits for any document that exists in both; other coders' files are untouched.`)) return;
  btn.disabled = true;
  status.textContent = 'Pulling from Mongo…';
  try {
    const res = await fetch('/api/pull', { method: 'POST' });
    const j = await res.json();
    if (!res.ok || !j.ok) throw new Error(j.error || 'pull failed');
    status.textContent = `Pulled ${j.pulled} document(s) from Mongo as ${j.coder} ✓`;
    location.reload();
  } catch (e) {
    status.textContent = 'Pull failed: ' + e.message;
    btn.disabled = false;
  }
}

// Existing incident IDs (for the "connect to another article" picker) + the next
// suggested new ID. Refreshed from the server whenever a document loads.
export let INCIDENT_IDS = [], INCIDENT_NEXT = '', INCIDENT_TITLES = {};
export async function refreshIncidentIds() {
  try {
    const d = await (await fetch('/api/incident_ids')).json();
    INCIDENT_IDS = d.ids || []; INCIDENT_NEXT = d.next || ''; INCIDENT_TITLES = d.titles || {};
  } catch (e) { INCIDENT_IDS = []; INCIDENT_NEXT = ''; INCIDENT_TITLES = {}; }
}

// If an incident ID belongs to an existing incident, copy that incident's title
// in. `overwrite` replaces an existing title (used when explicitly connecting);
// otherwise it only fills a blank title. Returns true if it changed the title.
export function fillTitleForIncident(id, overwrite) {
  const t = INCIDENT_TITLES[id];
  if (!t) return false;
  const ta = fieldAnn('incident_title');
  if (!overwrite && ta.answer && String(ta.answer).trim()) return false;
  if (ta.answer === t) return false;
  ta.answer = t;
  return true;
}
