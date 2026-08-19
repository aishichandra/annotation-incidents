// Bootstrap and view switching.
// init() loads schema + docs; setView() flips Incidents/Documents and
// restores each view's scroll position.
//
// Loaded as a classic script: everything here shares one global scope with the
// other static/js files. See templates/index.html for the load order.

async function init() {
  await initCoders();   // before any /api/ call, so each one is attributed
  const schema = await (await fetch('/api/schema')).json();
  SCHEMA = schema.fields;
  SCHEMA_ROLES = schema.claim_roles || [];
  if (schema.rules) RULES = schema.rules;
  SCHEMA.forEach((f, i) => color[f.key] = COLORS[i % COLORS.length]);
  const docs = await (await fetch('/api/docs')).json();
  const sel = document.getElementById('docSelect');
  sel.innerHTML = docs.map(d =>
    `<option value="${d.index}">${d.title.slice(0,80)}${d.n ? '  ('+d.n+')' : ''}</option>`).join('');
  sel.onchange = () => loadDoc(+sel.value);
  document.getElementById('pullBtn').onclick = pullFromMongo;
  document.getElementById('pushBtn').onclick = pushToMongo;
  document.getElementById('tabDocs').onclick = () => setView('docs');
  document.getElementById('tabIncidents').onclick = () => setView('incidents');
  loadDoc(docs.length ? docs[0].index : 0);
  // Land where you left off; Incidents on a first visit.
  let start = 'incidents';
  try { start = localStorage.getItem('towView') || 'incidents'; } catch (_) {}
  setView(start === 'docs' ? 'docs' : 'incidents');
}

// ---------- by-incident view ----------
// Toggle between the per-document coder ('docs') and the incident overview
// ('incidents'). CSS keyed off body.view-incidents shows/hides each surface.
// Switching views is navigation, nothing more. It used to also refetch and
// rebuild every incident card, which is why coming back from a document landed
// you at the top with every panel shut — the list you were scrolled into no
// longer existed. Data now refreshes when data changes (see markIncidentDirty),
// so a tab click leaves the DOM alone and your place in it survives.
//
// The two views share the window scrollbar, so hiding a tall one shrinks the
// page and the browser clamps scrollY. Each view's offset is therefore parked on
// the way out and put back on the way in, the same way loadDoc(keepScroll) does.
let CURRENT_VIEW = null;
const VIEW_SCROLL = { docs: 0, incidents: 0 };

function setView(v) {
  if (CURRENT_VIEW) VIEW_SCROLL[CURRENT_VIEW] = window.scrollY;
  CURRENT_VIEW = v;
  try { localStorage.setItem('towView', v); } catch (_) {}
  document.body.classList.toggle('view-incidents', v === 'incidents');
  document.getElementById('tabDocs').classList.toggle('active', v === 'docs');
  document.getElementById('tabIncidents').classList.toggle('active', v === 'incidents');
  const done = () => requestAnimationFrame(() => window.scrollTo(0, VIEW_SCROLL[v] || 0));
  if (v === 'incidents') refreshIncidents().then(done);
  else done();
}

