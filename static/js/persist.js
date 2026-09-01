// Persistence and startup.
// Debounced save to /api/doc/<i>/annotations, escapeHtml, the
// click-outside dropdown close, and the init() call that starts the app.

import { markIncidentDirty } from './incidents/index.js';
import { curDoc, saveTimer, setSaveTimer } from './state.js';

// ---------- persistence ----------
export function persistSoon() { clearTimeout(saveTimer); setSaveTimer(setTimeout(persist, 500)); }
export async function persist() {
  clearTimeout(saveTimer);
  const res = await fetch('/api/doc/' + curDoc.index + '/annotations', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(curDoc.ann),
  });
  // This is the only thing that can make an incident card stale, so it's the
  // only thing that asks for one to be redrawn. Switching tabs no longer does.
  markIncidentDirty((curDoc.ann.fields.incident_id || {}).answer);
  const j = await res.json();
  document.getElementById('status').textContent =
    `Saved ✓ ${j.n} quote${j.n === 1 ? '' : 's'} → data_annotated.csv`;
}

export function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
}

// close any open dropdown when clicking outside of it
document.addEventListener('click', (e) => {
  if (!e.target.closest('.select')) {
    document.querySelectorAll('.select.open').forEach(s => s.classList.remove('open'));
  }
});
