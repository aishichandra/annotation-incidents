// Bootstrap and view switching.
// init() loads schema + docs; setView() flips Incidents/Documents and
// restores each view's scroll position.

import { loadCodebook } from './codebook.js';
import { initCoders } from './coder.js';
import { refreshIncidents } from './incidents/index.js';
import { loadDoc } from './reader.js';
import { applySchema } from './state.js';
import { pullFromMongo, pushToMongo } from './sync.js';

export async function init() {
  await initCoders();   // before any /api/ call, so each one is attributed
  applySchema(await (await fetch('/api/schema')).json());
  const docs = await (await fetch('/api/docs')).json();
  const sel = document.getElementById('docSelect');
  sel.innerHTML = docs.map(d =>
    `<option value="${d.index}">${d.title.slice(0,80)}${d.n ? '  ('+d.n+')' : ''}</option>`).join('');
  sel.onchange = () => loadDoc(+sel.value);
  document.getElementById('pullBtn').onclick = pullFromMongo;
  document.getElementById('pushBtn').onclick = pushToMongo;
  document.getElementById('tabDocs').onclick = () => setView('docs');
  document.getElementById('tabIncidents').onclick = () => setView('incidents');
  document.getElementById('tabCodebook').onclick = () => setView('codebook');
  loadDoc(docs.length ? docs[0].index : 0);
  // Land where you left off; Incidents on a first visit.
  let start = 'incidents';
  try { start = localStorage.getItem('towView') || 'incidents'; } catch (_) {}
  setView(VIEWS.includes(start) ? start : 'incidents');
}

// ---------- view switching ----------
// Three surfaces: the per-document coder ('docs'), the incident overview
// ('incidents') and the scheme itself ('codebook'). CSS keyed off body.view-*
// shows and hides each one.
// Switching views is navigation, nothing more. It used to also refetch and
// rebuild every incident card, which is why coming back from a document landed
// you at the top with every panel shut — the list you were scrolled into no
// longer existed. Data now refreshes when data changes (see markIncidentDirty),
// so a tab click leaves the DOM alone and your place in it survives.
//
// The two views share the window scrollbar, so hiding a tall one shrinks the
// page and the browser clamps scrollY. Each view's offset is therefore parked on
// the way out and put back on the way in, the same way loadDoc(keepScroll) does.
export let CURRENT_VIEW = null;
export const VIEWS = ['incidents', 'docs', 'codebook'];
export const TAB_OF = { incidents: 'tabIncidents', docs: 'tabDocs', codebook: 'tabCodebook' };
export const VIEW_SCROLL = { docs: 0, incidents: 0, codebook: 0 };

export function setView(v) {
  if (CURRENT_VIEW) VIEW_SCROLL[CURRENT_VIEW] = window.scrollY;
  CURRENT_VIEW = v;
  try { localStorage.setItem('towView', v); } catch (_) {}
  VIEWS.forEach(name => {
    document.body.classList.toggle('view-' + name, v === name);
    document.getElementById(TAB_OF[name]).classList.toggle('active', v === name);
  });
  const done = () => requestAnimationFrame(() => window.scrollTo(0, VIEW_SCROLL[v] || 0));
  if (v === 'incidents') refreshIncidents().then(done);
  // The scheme is shared, so it can have moved under you while you were coding —
  // always re-read it rather than trusting what this tab drew last time.
  else if (v === 'codebook') loadCodebook().then(done);
  else done();
}
