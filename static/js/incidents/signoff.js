// What a coder says about an incident as a whole: complete, not an incident,
// or flagged as one they are unsure of.
//
// Completeness is recomputed here from the coding on screen so the button can
// say what is missing, and again on the server before a sign-off is accepted —
// the card can be stale, the server cannot.

import { escapeHtml } from '../persist.js';
import { RULES } from '../state.js';
import { roleLabel } from './card.js';
import { INCIDENTS, loadIncidents, nextInSectionAfter, refreshTile } from './index.js';

// Persist an incident's groups (debounced-ish: fire immediately, it's small).
// ---------- completion sign-off ----------
// A mirror of incident_completeness() in incidents.py, so the control can react
// to a drag without a round trip. The server re-checks before recording a
// sign-off and answers 409 if it disagrees, so the two drifting apart costs a
// confusing button, never a wrong record.
export const MISSING_LABEL = { complete_claim: 'a linked claim' };

export function claimIsComplete(cl) {
  return !!(cl.harm && (cl.harmed_parties || []).length && (cl.factors || []).length);
}

export function completenessOf(inc) {
  const missing = (RULES.required_roles || [])
    .filter(r => !(((inc.role_values || {})[r]) || []).length);
  if (!(inc.groups || []).some(g => g.actor && (g.claims || []).some(claimIsComplete))) {
    missing.push('complete_claim');
  }
  return { ok: !missing.length, missing };
}

export function missingText(missing) {
  return missing.map(m => MISSING_LABEL[m] || roleLabel(m).toLowerCase()).join(', ');
}

// "I'm not sure about this" — offered whatever state the incident is in, because
// a coder can doubt a reading they have already signed off, and doubt about one
// they have set aside is worth just as much. Raising it changes nothing else: it
// is a request for a second look, not a status.
export function flagControl(inc) {
  const encId = escapeHtml(inc.incident_id);
  return inc.flagged
    ? `<button class="inc-flag on" data-inc="${encId}" `
      + `title="You flagged this as uncertain \u2014 press to clear it">`
      + `\u2691 Unsure</button>`
    : `<button class="inc-flag" data-inc="${encId}" `
      + `title="Flag this as one you are not sure about \u2014 say what in the comment box">`
      + `\u2691 Not sure</button>`;
}

export function completeControl(inc) {
  const encId = escapeHtml(inc.incident_id);
  const when = (inc.completed_at || '').slice(0, 10);
  if (inc.status === 'not_an_incident') {
    return `<span class="inc-out" title="Set aside by you${when ? ' on ' + when : ''}">`
         + `Not an incident</span>` + flagControl(inc)
         + `<button class="inc-restore" data-inc="${encId}" `
         + `title="Put this back with the incidents you are coding">Restore</button>`;
  }
  if (inc.status === 'complete') {
    return `<span class="inc-done" title="Signed off ${escapeHtml(inc.completed_at || '')}">`
         + `\u2713 Complete${when ? ' \u00b7 ' + escapeHtml(when) : ''}</span>` + flagControl(inc)
         + `<button class="inc-undo" data-inc="${encId}" title="Withdraw this sign-off">Undo</button>`;
  }
  // Excluding is always available: it is a judgement about the material, so it
  // never waits on the coding being finished.
  const drop = flagControl(inc)
             + `<button class="inc-drop" data-inc="${encId}" `
             + `title="This isn't an incident \u2014 set it aside">Not an incident</button>`;
  const st = completenessOf(inc);
  if (!st.ok) {
    return `<span class="inc-needs" title="Fill these in to sign this incident off">`
         + `Needs ${escapeHtml(missingText(st.missing))}</span>` + drop;
  }
  return drop + `<button class="inc-mark" data-inc="${encId}">Mark complete</button>`;
}

// Re-render the control wherever this incident is on screen. Called after any
// edit the completeness check reads, so the button tracks the coding.
export function refreshComplete(inc) {
  document.querySelectorAll('.inc-complete').forEach(el => {
    if (el.dataset.inc !== inc.incident_id) return;
    el.innerHTML = completeControl(inc);
    wireComplete(el);
  });
  // The tile carries the same judgement in one line, so it moves with the card.
  refreshTile(inc);
}

export function wireComplete(el) {
  const mark = el.querySelector('.inc-mark');
  if (mark) mark.onclick = () => setStatus(mark.dataset.inc, 'complete');
  const undo = el.querySelector('.inc-undo');
  if (undo) undo.onclick = () => setStatus(undo.dataset.inc, '');
  const restore = el.querySelector('.inc-restore');
  if (restore) restore.onclick = () => setStatus(restore.dataset.inc, '');
  const drop = el.querySelector('.inc-drop');
  if (drop) drop.onclick = () => setStatus(drop.dataset.inc, 'not_an_incident');
  const flag = el.querySelector('.inc-flag');
  if (flag) flag.onclick = () => setFlag(flag.dataset.inc, !INCIDENTS[flag.dataset.inc].flagged);
}

// Raised and cleared with the same button. Nothing else moves — the incident
// stays in the section its status puts it in — so the only thing to redraw is
// the control itself and the marker on its tile.
export async function setFlag(incId, flagged) {
  const inc = INCIDENTS[incId];
  if (!inc) return;
  const btn = document.querySelector(`.inc-flag[data-inc="${CSS.escape(incId)}"]`);
  if (btn) btn.disabled = true;
  try {
    const res = await fetch('/api/incident/' + encodeURIComponent(incId) + '/flag', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ flagged }), keepalive: true,
    });
    let j = null;
    try { j = await res.json(); } catch (_) { /* an error page, not JSON */ }
    if (!res.ok || !j || !j.ok) {
      // The same 404 the status control explains: app.py runs without the
      // reloader unless FLASK_DEBUG is set, so a new route needs a restart.
      document.getElementById('status').textContent = res.status === 404
        ? 'Route not found — restart the app to pick up server changes'
        : `Could not save that flag — HTTP ${res.status}`;
      if (btn) btn.disabled = false;
      return;
    }
    inc.flagged = j.flagged;
    document.getElementById('status').textContent =
      j.flagged ? (j.synced ? 'Flagged as unsure ✓' : 'Flagged as unsure (saved locally)')
                : 'Flag cleared';
  } catch (e) {
    document.getElementById('status').textContent = 'Could not save that flag';
    if (btn) btn.disabled = false;
    return;
  }
  refreshComplete(inc);
}

export async function setStatus(incId, status) {
  const inc = INCIDENTS[incId];
  if (!inc) return;
  const was = inc.status;
  const el = document.querySelector(`.inc-complete[data-inc="${CSS.escape(incId)}"]`);
  el && el.querySelectorAll('button').forEach(b => b.disabled = true);
  try {
    const res = await fetch('/api/incident/' + encodeURIComponent(incId) + '/status', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status }),
    });
    // An error page is HTML, not JSON, so parsing has to be allowed to fail —
    // otherwise every non-JSON response arrives here as a bare parse error and
    // the real status code is lost.
    let j = null;
    try { j = await res.json(); } catch (_) { /* not JSON: an error page */ }
    if (!res.ok || !j || !j.ok) {
      // 409 is the completeness refusal, and it names what is missing — the one
      // failure that is about the coding rather than the plumbing.
      const msg = (res.status === 409 && j)
        ? `Needs ${escapeHtml(missingText(j.missing || []))}`
        : (res.status === 404
            // This page asked for a route the server doesn't have, which almost
            // always means the server predates it: app.py runs without the
            // reloader unless FLASK_DEBUG is set, so a .py change needs a restart.
            ? 'Route not found \u2014 restart the app to pick up server changes'
            : `Save failed \u2014 HTTP ${res.status}`);
      if (el) el.innerHTML = `<span class="inc-needs">${msg}</span>`;
      return;
    }
    inc.status = j.status;
    inc.completed_at = j.completed_at;
    if (status === 'complete') {
      const s = document.getElementById('status');
      if (s) s.textContent = j.synced
        ? `${incId}: signed off, ${j.documents} document(s) pushed to Mongo \u2713`
        : (j.mongo
            ? `${incId}: signed off locally \u2014 Mongo sync failed, use Push to Mongo`
            : `${incId}: signed off \u2014 saved locally (Mongo not connected)`);
    }
  } catch (e) {
    // fetch itself threw: nothing answered at all.
    if (el) el.innerHTML = '<span class="inc-needs">No response \u2014 is the app running?</span>';
    return;
  }
  // Every status change moves the tile between sections, which is a change to the
  // index rather than to a single card — so the whole thing is redrawn. Settling
  // an incident carries you on to the next one still to code; withdrawing a
  // sign-off leaves you on the incident you just reopened.
  if (status !== was) {
    const settled = status === 'complete' || status === 'not_an_incident';
    return loadIncidents({ open: settled ? nextInSectionAfter(incId) : incId });
  }
  refreshComplete(inc);
}
