// Incidents view.
// One card per incident: field blocks, the role palette, claim groups,
// drag-and-drop chips, per-incident comments, quote panels, JSON inspector.
//
// Loaded as a classic script: everything here shares one global scope with the
// other static/js files. See templates/index.html for the load order.

// Incidents whose data changed since they were rendered. A document save marks
// the incident it belongs to; nothing else needs a redraw, so switching tabs
// with none of these is free.
const DIRTY_INCIDENTS = new Set();
let INCIDENTS_RENDERED = false;
// The display fields from /api/incidents. Held here because a card is now built
// on demand, long after the response that carried them has been consumed.
let FIELDS = [];
// The one incident expanded right now, or null. Only one is ever open: the point
// of the collapsed index is that the page stays short enough to scan.
let OPEN_INCIDENT = null;

function markIncidentDirty(incId) {
  if (incId) DIRTY_INCIDENTS.add(incId);
}

// ---------- the three sections ----------
// A coder works top-down: what still needs coding, then what has been settled —
// signed off, or ruled out. Settled work stays listed and countable rather than
// hidden, since "what have I finished" and "what did I rule out" are both part
// of the record.
const isDone = (inc) => inc.status === 'complete';
const isOut  = (inc) => inc.status === 'not_an_incident';
const SECTIONS = [
  { id: 'todo', label: 'To code',         match: (inc) => !isDone(inc) && !isOut(inc) },
  { id: 'done', label: 'Complete',        match: isDone },
  { id: 'out',  label: 'Not an incident', match: isOut },
];
const sectionOf = (inc) => (SECTIONS.find(s => s.match(inc)) || SECTIONS[0]).id;
const sectionLabel = (id) => (SECTIONS.find(s => s.id === id) || SECTIONS[0]).label;
const tileEl = (incId) =>
  document.querySelector(`.inc-tile[data-inc="${CSS.escape(incId)}"]`);

// Bring the incidents view up to date with the least disturbance: nothing on the
// first visit but a full render, and after that only the tiles whose data moved.
async function refreshIncidents() {
  if (!INCIDENTS_RENDERED) return loadIncidents();
  // A redraw replaces the textarea, so anything still on the debounce goes out
  // first — otherwise re-reading the server's copy would undo it.
  await flushIncidentComments();
  if (!DIRTY_INCIDENTS.size) return;
  let data;
  try { data = await (await fetch('/api/incidents')).json(); }
  catch (e) { return; }
  const fresh = {};
  data.incidents.forEach(inc => { fresh[inc.incident_id] = inc; });
  FIELDS = data.fields;
  // An incident that appeared or disappeared changes the list itself, not just a
  // tile, so fall back to a full render for that.
  const sameSet = Object.keys(fresh).length === Object.keys(INCIDENTS).length
    && Object.keys(fresh).every(k => k in INCIDENTS);
  // So does one whose status moved it into a different section — the tile has to
  // change places, which the index owns rather than the tile.
  const moved = !sameSet || Array.from(DIRTY_INCIDENTS).some(incId => {
    const tile = tileEl(incId);
    return fresh[incId] && tile && sectionOf(fresh[incId]) !== tile.dataset.sec;
  });
  if (moved) {
    Object.assign(INCIDENTS, fresh);
    DIRTY_INCIDENTS.clear();
    return loadIncidents();
  }
  DIRTY_INCIDENTS.forEach(incId => {
    const inc = fresh[incId];
    if (!inc || !tileEl(incId)) return;
    INCIDENTS[incId] = inc;
    refreshTile(inc);
    // Rebuild the open card in place; a collapsed one is rebuilt when it opens.
    if (OPEN_INCIDENT === incId) openIncident(incId, { scroll: false });
  });
  DIRTY_INCIDENTS.clear();
}

// Post-render wiring for one card: opening its documents, its draggable chips,
// its claims and its JSON panel. Shared so a single re-rendered card comes back
// as live as one from a full render.
function wireIncidentCard(root) {
  root.querySelectorAll('.tow-doc').forEach(el => {
    el.onclick = (e) => {
      if (e.target.closest('.durl')) return;   // let the external link work
      const i = +el.dataset.index;
      setView('docs');
      document.getElementById('docSelect').value = i;
      loadDoc(i);
    };
  });
  root.querySelectorAll('.tow-cardfields').forEach(el => buildCardFields(el, INCIDENTS[el.dataset.inc]));
  root.querySelectorAll('.tow-palette').forEach(el => buildPalette(el, INCIDENTS[el.dataset.inc]));
  root.querySelectorAll('.tow-groups').forEach(el => buildGroupsUI(el, INCIDENTS[el.dataset.inc]));
  root.querySelectorAll('.inc-note-body').forEach(el =>
    buildIncidentComment(el, el.closest('.inc-note').dataset.inc));
  root.querySelectorAll('.inc-complete').forEach(el => wireComplete(el));
  root.querySelectorAll('.json-btn').forEach(btn => btn.onclick = () => toggleJson(btn.dataset.inc));
  root.querySelectorAll('.card-close').forEach(btn => btn.onclick = () => closeIncident());
}

// ---------- the collapsed index ----------

// An incident with no title of its own is shown by its first document, so a tile
// is never just a bare id — twenty-odd incidents here were grouped but never
// named, and "INC-031" alone tells a coder nothing about what they are opening.
function tileTitle(inc) {
  if (inc.title) return escapeHtml(inc.title);
  const d = inc.documents[0];
  return `<span class="t-untitled" title="This incident has no title of its own \u2014`
       + ` showing its first document instead">${escapeHtml(d ? (d.title || '(untitled document)')
                                                             : '(no documents)')}</span>`;
}

function incidentTile(inc) {
  const encId = escapeHtml(inc.incident_id);
  const sec = sectionOf(inc);
  const ready = sec === 'todo' && completenessOf(inc).ok ? ' ready' : '';
  return `<div class="inc-tile" data-inc="${encId}" data-sec="${sec}">
    <div class="tile-head" role="button" tabindex="0" aria-expanded="false">
      <div class="t-top"><span class="t-id">${encId}</span>
        <span class="t-flag"${inc.flagged ? '' : ' hidden'} title="You are not sure about this one">\u2691</span>
        <span class="t-state ${sec}${ready}"></span></div>
      <div class="t-title">${tileTitle(inc)}</div>
    </div>
    <div class="tile-card" hidden></div>
  </div>`;
}

// Repaint a collapsed tile from its incident. Cheap enough to call on every edit,
// so the index's "needs …" hint tracks the card a coder is working in.
function refreshTile(inc) {
  const tile = tileEl(inc.incident_id);
  if (!tile) return;
  const sec = sectionOf(inc);
  tile.dataset.sec = sec;
  const t = tile.querySelector('.t-title');
  if (t) t.innerHTML = tileTitle(inc);
  const s = tile.querySelector('.t-state');
  if (s) s.className = 't-state ' + sec
    + (sec === 'todo' && completenessOf(inc).ok ? ' ready' : '');
  const fl = tile.querySelector('.t-flag');
  if (fl) fl.hidden = !inc.flagged;
}

// The three sections are tabs, not one long scroll: only the selected one is on
// the page, so "Complete" and "Not an incident" cost nothing to carry around and
// the index stays a single screen of tiles.
let ACTIVE_TAB = 'todo';

function tocHtml(groups) {
  const tabs = SECTIONS.map(s =>
    `<button class="toc-link sec-${s.id}${s.id === ACTIVE_TAB ? ' active' : ''}"`
    + ` data-tab="${s.id}" role="tab" aria-selected="${s.id === ACTIVE_TAB}">`
    + `${escapeHtml(s.label)}<span class="toc-n">${groups[s.id].length}</span></button>`).join('');
  return `<div class="inc-toc">
    <div class="toc-row"><nav class="toc-links" role="tablist">${tabs}</nav></div>
  </div>`;
}

function sectionHtml(sec, list) {
  const body = list.length
    ? `<div class="inc-grid">${list.map(incidentTile).join('')}</div>`
    : `<div class="sec-empty">Nothing ${sec.id === 'todo' ? 'left to code' : 'here'}.</div>`;
  return `<section class="inc-section" id="sec-${sec.id}" data-sec="${sec.id}"`
    + `${sec.id === ACTIVE_TAB ? '' : ' hidden'}>${body}</section>`;
}

// Switch tabs. Collapsing first keeps an open card from being stranded on a
// section nobody is looking at.
function showTab(tabId) {
  if (!SECTIONS.some(s => s.id === tabId)) return;
  if (OPEN_INCIDENT) {
    const t = tileEl(OPEN_INCIDENT);
    if (t && t.dataset.sec !== tabId) closeIncident();
  }
  ACTIVE_TAB = tabId;
  document.querySelectorAll('.inc-section').forEach(el => {
    el.hidden = el.dataset.sec !== tabId;
  });
  document.querySelectorAll('.toc-link').forEach(el => {
    const on = el.dataset.tab === tabId;
    el.classList.toggle('active', on);
    el.setAttribute('aria-selected', String(on));
  });
}

// Render the whole index. `open` names the incident to expand afterwards —
// by default whichever was open before, so a redraw doesn't shut the card a
// coder is working in.
async function loadIncidents(opts = {}) {
  const reopen = ('open' in opts) ? opts.open : OPEN_INCIDENT;
  const wrap = document.getElementById('incidents');
  wrap.innerHTML = '<div class="inc-wrap"><div class="iempty">Loading…</div></div>';
  let data;
  try { data = await (await fetch('/api/incidents')).json(); }
  catch (e) {
    wrap.innerHTML = '<div class="inc-wrap"><div class="iempty">Failed to load incidents.</div></div>';
    return;
  }
  INCIDENTS = {};
  data.incidents.forEach(inc => { INCIDENTS[inc.incident_id] = inc; });
  FIELDS = data.fields;
  OPEN_INCIDENT = null;                 // the DOM the old id pointed at is gone

  if (!data.incidents.length) {
    wrap.innerHTML = '<div class="inc-wrap"><div class="iempty">No incidents coded yet.</div></div>';
    INCIDENTS_RENDERED = true;
    DIRTY_INCIDENTS.clear();
    return;
  }
  const groups = {};
  SECTIONS.forEach(s => { groups[s.id] = data.incidents.filter(s.match); });
  const secs = SECTIONS.map(s => sectionHtml(s, groups[s.id])).join('');
  wrap.innerHTML = `<div class="inc-wrap">${tocHtml(groups)}${secs}</div>`;
  wireIndex(wrap);
  INCIDENTS_RENDERED = true;
  DIRTY_INCIDENTS.clear();
  if (reopen && INCIDENTS[reopen]) openIncident(reopen, { scroll: false });
}

function wireIndex(root) {
  root.querySelectorAll('.inc-tile > .tile-head').forEach(head => {
    const incId = head.parentElement.dataset.inc;
    head.onclick = () => toggleIncident(incId);
    head.onkeydown = (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggleIncident(incId); }
    };
  });
  root.querySelectorAll('[data-tab]').forEach(b => b.onclick = () => showTab(b.dataset.tab));
}

// The top bar is fixed and the table of contents sticks beneath it, so a plain
// scrollIntoView would park the heading underneath them both.
function stickyOffset() {
  const bar = document.querySelector('.bar');
  const toc = document.querySelector('.inc-toc');
  return (bar ? bar.offsetHeight : 0) + (toc ? toc.offsetHeight : 0) + 14;
}

// ---------- opening one incident ----------

function toggleIncident(incId) {
  if (OPEN_INCIDENT === incId) closeIncident();
  else openIncident(incId);
}

// Build the full card only when it is actually looked at. With fifty-odd
// incidents, rendering every palette and claim board up front was most of both
// the cost of this view and its height.
function openIncident(incId, opts = {}) {
  const inc = INCIDENTS[incId];
  const tile = tileEl(incId);
  if (!inc || !tile) return;
  // Reached from the codebook or a sign-off, the incident may sit on a tab that
  // is not showing; bring that tab up rather than opening it out of sight.
  if (tile.dataset.sec !== ACTIVE_TAB) showTab(tile.dataset.sec);
  if (OPEN_INCIDENT && OPEN_INCIDENT !== incId) closeIncident();
  OPEN_INCIDENT = incId;
  tile.classList.add('open');
  const head = tile.querySelector('.tile-head');
  if (head) head.setAttribute('aria-expanded', 'true');
  const holder = tile.querySelector('.tile-card');
  holder.innerHTML = incidentCard(inc, FIELDS);
  holder.hidden = false;
  wireIncidentCard(holder);
  if (opts.scroll !== false) {
    requestAnimationFrame(() => {
      const top = tile.getBoundingClientRect().top + window.scrollY - stickyOffset();
      window.scrollTo({ top: Math.max(0, top), behavior: 'smooth' });
    });
  }
}

function closeIncident() {
  const tile = OPEN_INCIDENT && tileEl(OPEN_INCIDENT);
  const inc = OPEN_INCIDENT && INCIDENTS[OPEN_INCIDENT];
  // Throwing the card away takes the comment box with it; saveComment reads the
  // in-memory incident rather than the textarea, so flushing first loses nothing.
  flushIncidentComments();
  OPEN_INCIDENT = null;
  if (!tile) return;
  tile.classList.remove('open');
  const head = tile.querySelector('.tile-head');
  if (head) head.setAttribute('aria-expanded', 'false');
  const holder = tile.querySelector('.tile-card');
  holder.hidden = true;
  holder.innerHTML = '';
  if (inc) refreshTile(inc);            // the summary may have moved on while open
}

// Escape collapses the open incident, but not while a field inside it has focus —
// there Escape belongs to the box being edited.
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape' || !OPEN_INCIDENT) return;
  if (!document.body.classList.contains('view-incidents')) return;
  const t = e.target;
  if (t && t.closest && t.closest('input, textarea, select, .cb-row')) return;
  closeIncident();
});

// The incident that follows `incId` in "To code", or null at the end of it.
// Used to carry a coder on after a sign-off moves the tile they were in.
function nextTodoAfter(incId) {
  const todo = Array.from(document.querySelectorAll('.inc-tile[data-sec="todo"]'))
    .map(t => t.dataset.inc);
  const i = todo.indexOf(incId);
  return (i >= 0 && i + 1 < todo.length) ? todo[i + 1] : null;
}

const NODATA = '<span class="tow-nodata">No data</span>';
let INCIDENTS = {};   // incident_id -> incident object, for post-render handlers

// A Tow/CJR-styled card carrying every detail the JSON holds for one incident.
// Anything un-coded renders as "No data". The characteristics palette + claim
// groups are an interactive placeholder filled in by buildGroupsUI after render.
function incidentCard(inc, fields) {
  const chips = arr => arr.map(v => `<span class="tow-chip">${escapeHtml(v)}</span>`).join('');

  // One field block — free text about the incident, plus any coder comments.
  // Everything picked from a vocabulary is a characteristic and lives in the
  // palette instead, so nothing here is draggable.
  const fieldBlock = (f) => {
    const vals = inc.field_values[f.key] || [];
    const cmts = (inc.field_comments && inc.field_comments[f.key]) || [];
    const valHtml = !vals.length ? NODATA : chips(vals);
    const cmtHtml = cmts.map(c => `<div class="tow-comment">“${escapeHtml(c)}”</div>`).join('');
    return `<div class="tow-field"><div class="tow-label">${escapeHtml(f.label)}</div>`
      + `<div class="tow-value">${valHtml}${cmtHtml}</div></div>`;
  };
  // The two characteristics nobody codes: when the incident's articles were
  // published, and where. Both are read off the documents — the date from
  // Zotero, the domain from the URL — so they are plain text rather than chips:
  // there is no judgement here to drag into a claim or to justify with a quote.
  const derivedBlock = (label, html) =>
    `<div class="tow-field"><div class="tow-label">${label}</div>`
    + `<div class="tow-value tow-derived">${html}</div></div>`;
  const dates = inc.dates || [], domains = inc.domains || [];
  // A range covering fewer articles than the incident holds would read as the
  // whole story, so the documents Zotero has no date for are counted out loud.
  const undated = inc.undated
    ? `<span class="tow-undated">${inc.undated} undated</span>` : '';
  const publishedBlock = derivedBlock('Published date', !dates.length ? NODATA
    : escapeHtml(dates.length > 1 ? `${dates[0]} – ${dates[dates.length - 1]}` : dates[0])
      + (undated ? ' ' + undated : ''));
  const domainBlock = derivedBlock(domains.length > 1 ? 'Domains' : 'Domain',
    !domains.length ? NODATA : escapeHtml(domains.join(', ')));

  // Aftermath renders last of all, under the claim groups; the rest sit up left.
  // Card-only fields are answered here rather than read here, so they are left
  // to buildCardFields below instead of rendering as chips.
  const leftFieldBlocks = fields
    .filter(f => f.key !== 'incident_aftermath' && !f.card_only)
    .map(fieldBlock).join('');
  const aftermath = fields.find(f => f.key === 'incident_aftermath');
  const aftermathBlock = aftermath ? fieldBlock(aftermath) : '';

  const docsHtml = inc.documents.map(d => {
    const url = d.url
      ? `<a class="durl" href="${escapeHtml(d.url)}" target="_blank" rel="noopener">source ↗</a>` : '';
    // Who has coded this document — progress only, never their codes, so coders
    // stay blind to each other's judgements while coding.
    const by = (d.coded_by || []);
    const badge = by.length
      ? `<span class="dcoders" title="Coded by ${escapeHtml(by.join(', '))}">${
          by.map(c => `<span class="dcoder${c === CODER ? ' me' : ''}">${escapeHtml(c)}</span>`).join('')}</span>`
      : '<span class="dcoders"><span class="dcoder none">uncoded</span></span>';
    return `<div class="tow-doc" data-index="${d.index}">`
      + `<span class="dtitle">${escapeHtml(d.title || '(untitled document)')}</span>${badge}${url}</div>`;
  }).join('');

  const encId = escapeHtml(inc.incident_id);
  return `<div class="tow-card" data-card="${encId}">
    <div class="tow-head">
      <span class="tow-id">${encId}</span>
      <div class="tow-headdocs">${docsHtml}</div>
      <button class="card-close" title="Collapse this incident (Esc)">\u00d7</button>
    </div>
    <div class="tow-body">
      <div class="tow-col c1">
        ${leftFieldBlocks}
        ${publishedBlock}
        ${domainBlock}
        <div class="tow-cardfields" data-inc="${encId}"></div>
        <div class="tow-palette" data-inc="${encId}"></div>
      </div>
      <div class="tow-col c2">
        <div class="tow-groups" data-inc="${encId}"></div>
        ${aftermathBlock}
      </div>
    </div>
    <div class="inc-note" data-inc="${encId}">
      <div class="inc-note-head">
        <div class="tow-label">Comments${CODER ? '(' + escapeHtml(CODER) + ')' : ''}</div>
        <span class="inc-note-state"></span>
      </div>
      <div class="inc-note-body"></div>
    </div>
    <div class="tow-foot">
      <button class="json-btn" data-inc="${encId}" title="Show this incident as it is stored">{ } JSON</button>
      <div class="inc-complete" data-inc="${encId}">${completeControl(inc)}</div>
    </div>
    <div class="json-panel" data-inc="${encId}" hidden></div>
  </div>`;
}

const roleColor = (role) => (CLAIM_ROLE[role] && CLAIM_ROLE[role].color) || '#e5e7eb';
const roleLabel = (role) => (CLAIM_ROLE[role] && CLAIM_ROLE[role].label) || role;
// The palette in ROLES is chip *background* colour — pale by design, and too light
// to read as text on white (yellow worst of all). Darken it toward black for the
// sentence placeholders, so an empty slot still shows which role it wants without
// a second colour list that could drift out of step with the chips.
function roleInk(role, amount = 0.45) {
  const hex = roleColor(role);
  const n = parseInt(hex.slice(1), 16);
  const dim = (c) => Math.round(c * (1 - amount));
  return `rgb(${dim((n >> 16) & 255)}, ${dim((n >> 8) & 255)}, ${dim(n & 255)})`;
}

// Persist an incident's groups (debounced-ish: fire immediately, it's small).
// ---------- completion sign-off ----------
// A mirror of incident_completeness() in incidents.py, so the control can react
// to a drag without a round trip. The server re-checks before recording a
// sign-off and answers 409 if it disagrees, so the two drifting apart costs a
// confusing button, never a wrong record.
const MISSING_LABEL = { complete_claim: 'a linked claim' };

function claimIsComplete(cl) {
  return !!(cl.harm && (cl.harmed_parties || []).length && (cl.factors || []).length);
}

function completenessOf(inc) {
  const missing = (RULES.required_roles || [])
    .filter(r => !(((inc.role_values || {})[r]) || []).length);
  if (!(inc.groups || []).some(g => g.actor && (g.claims || []).some(claimIsComplete))) {
    missing.push('complete_claim');
  }
  return { ok: !missing.length, missing };
}

function missingText(missing) {
  return missing.map(m => MISSING_LABEL[m] || roleLabel(m).toLowerCase()).join(', ');
}

// "I'm not sure about this" — offered whatever state the incident is in, because
// a coder can doubt a reading they have already signed off, and doubt about one
// they have set aside is worth just as much. Raising it changes nothing else: it
// is a request for a second look, not a status.
function flagControl(inc) {
  const encId = escapeHtml(inc.incident_id);
  return inc.flagged
    ? `<button class="inc-flag on" data-inc="${encId}" `
      + `title="You flagged this as uncertain \u2014 press to clear it">`
      + `\u2691 Unsure</button>`
    : `<button class="inc-flag" data-inc="${encId}" `
      + `title="Flag this as one you are not sure about \u2014 say what in the comment box">`
      + `\u2691 Not sure</button>`;
}

function completeControl(inc) {
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
function refreshComplete(inc) {
  document.querySelectorAll('.inc-complete').forEach(el => {
    if (el.dataset.inc !== inc.incident_id) return;
    el.innerHTML = completeControl(inc);
    wireComplete(el);
  });
  // The tile carries the same judgement in one line, so it moves with the card.
  refreshTile(inc);
}

function wireComplete(el) {
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
async function setFlag(incId, flagged) {
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

async function setStatus(incId, status) {
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
    return loadIncidents({ open: settled ? nextTodoAfter(incId) : incId });
  }
  refreshComplete(inc);
}


async function saveGroups(inc) {
  try {
    await fetch('/api/incident/' + encodeURIComponent(inc.incident_id) + '/groups', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ groups: inc.groups }),
    });
  } catch (e) { /* non-fatal: local drag state stays until reload */ }
  // Editing the claims invalidates any sign-off — the server does this in
  // clear_signoff(); reflect it here so the card can't keep claiming complete.
  if (inc.status === 'complete') { inc.status = ''; inc.completed_at = ''; }
  refreshComplete(inc);
}

// Comments in flight, incident id -> pending debounce timer. Typing shouldn't
// cost a request per keystroke, but nothing may be lost either, so a pending
// comment is flushed before anything that could redraw its card.
const COMMENT_TIMERS = new Map();

function flushIncidentComments() {
  return Promise.all(Array.from(COMMENT_TIMERS.keys()).map(incId => {
    clearTimeout(COMMENT_TIMERS.get(incId));
    COMMENT_TIMERS.delete(incId);
    return saveComment(incId);
  }));
}

// Typed and then reloaded, before the debounce ran. `keepalive` on the request is
// what lets it outlive the page.
window.addEventListener('pagehide', flushIncidentComments);

// Persist one incident's comment. The text comes from the in-memory incident, so
// this is safe to call after the textarea has gone (a re-render, a view switch).
async function saveComment(incId) {
  const inc = INCIDENTS[incId];
  if (!inc) return;
  const box = document.querySelector(`.inc-note[data-inc="${CSS.escape(incId)}"]`);
  const state = box && box.querySelector('.inc-note-state');
  if (state) { clearTimeout(state._clear); state.textContent = 'Saving…'; state.classList.remove('on'); }
  try {
    await fetch('/api/incident/' + encodeURIComponent(incId) + '/comment', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ comment: inc.comment || '' }), keepalive: true,
    });
    if (state) {
      state.textContent = 'Saved ✓'; state.classList.add('on');
      clearTimeout(state._clear);
      state._clear = setTimeout(() => {
        state.textContent = ''; state.classList.remove('on');
      }, 2000);
    }
  } catch (e) {
    if (state) { state.textContent = 'Not saved — check your connection'; state.classList.remove('on'); }
  }
}

// One card's comment box. Set the same way the actor's name and the incident
// title are: type it, press Enter, and it settles into plain text you click to
// edit again — so a written comment reads as *entered* rather than as something
// still sitting in a box. Shift+Enter is a new line, since a comment is prose.
// Typing still autosaves on a debounce, so nothing is lost on the way to setting.
function buildIncidentComment(wrap, incId) {
  const inc = INCIDENTS[incId];
  if (!inc) return;

  // Leave edit mode and write it. `editing` only ever forces the box open over a
  // comment that already exists, so it is not a "was the coder typing?" flag —
  // a first comment is typed with it unset, and guarding on it here left Enter
  // doing nothing at all until the second edit.
  const settle = () => {
    wrap.dataset.editing = '';
    render(false);
    saveNow();
  };

  const saveNow = () => {
    clearTimeout(COMMENT_TIMERS.get(incId));
    COMMENT_TIMERS.delete(incId);
    saveComment(incId);
  };

  function render(focus) {
    wrap.innerHTML = '';
    const val = (inc.comment || '').trim();
    if (val && wrap.dataset.editing !== '1') {
      const box = document.createElement('div');
      box.className = 'text-answer inc-note-set';
      box.innerHTML = `<span class="val">${escapeHtml(val)}</span><button class="x" title="Clear">×</button>`;
      box.querySelector('.x').onclick = (e) => {
        e.stopPropagation();
        inc.comment = ''; wrap.dataset.editing = ''; saveNow(); render(true);
      };
      box.onclick = (e) => {
        if (e.target.classList.contains('x')) return;
        wrap.dataset.editing = '1'; render(true);
      };
      wrap.appendChild(box);
      return;
    }
    const ta = document.createElement('textarea');
    ta.rows = 2;
    ta.value = inc.comment || '';
    ta.oninput = () => {
      inc.comment = ta.value;
      clearTimeout(COMMENT_TIMERS.get(incId));
      COMMENT_TIMERS.set(incId, setTimeout(() => {
        COMMENT_TIMERS.delete(incId);
        saveComment(incId);
      }, 700));
    };
    ta.onkeydown = (e) => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); settle(); }
    };
    ta.onblur = () => {
      if (!document.body.contains(ta)) return;   // ignore blur from our own re-render
      if ((ta.value || '').trim()) settle();
      else if (COMMENT_TIMERS.has(incId)) saveNow();   // cleared, and not yet written
    };
    wrap.appendChild(ta);
    if (focus) ta.focus();
  }

  wrap.dataset.editing = '';
  render(false);
}

// Where a characteristic has been used. A value can appear in any number of
// places — dragging copies rather than moves — so this returns every mark that
// holds it, and the palette shows them on the chip.
//
// Actor / system / developer belong to the group, so their mark is the group id
// ("2"). Harm / harmed party / factor belong to one claim inside a group, so
// theirs is "group.claim" ("2.1").
function usedInClaims(inc, role, value) {
  const marks = [];
  (inc.groups || []).forEach(g => {
    if (GROUP_ROLES.includes(role)) {
      if (groupValues(g, role).includes(value)) marks.push(String(g.id));
      return;
    }
    (g.claims || []).forEach(cl => {
      const key = CLAIM_LIST_KEYS[role];
      const hit = key ? (cl[key] || []).includes(value) : cl[role] === value;
      if (hit) marks.push(g.id + '.' + cl.id);
    });
  });
  return marks;
}

// Re-render everything draggable on an incident's card — the characteristics
// palette and the System/Developer field chips — so their claim marks match the
// current claims. Called after every claim change; these live in a sibling
// column, so they're found by incident id rather than passed around.
function refreshDraggables(inc) {
  document.querySelectorAll('.tow-palette').forEach(el => {
    if (el.dataset.inc === inc.incident_id) buildPalette(el, inc);
  });
}

// The incident's own controlled answers — Geography/location, Translated —
// picked here and nowhere else. They describe the incident rather than any one
// of its documents, so there is no passage to highlight for them and no claim to
// drag them into: the card is where they are answered, and the same multiselect
// the document sidebar uses is what answers them, hover definitions and all.
//
// Saved on change to this coder's incident coding, like the comment box. The
// list held here is the control's own, so a failed save leaves the card showing
// what the server actually holds rather than what the click implied.
function buildCardFields(container, inc) {
  if (!inc) return;
  container.innerHTML = '';
  FIELDS.filter(f => f.card_only).forEach(f => {
    const block = document.createElement('div');
    block.className = 'tow-field';

    const head = document.createElement('div');
    head.className = 'tow-label tow-field-head';
    head.innerHTML = `<span>${escapeHtml(f.label)}</span>`
                   + `<span class="inc-note-state"></span>`;
    block.appendChild(head);
    const state = head.querySelector('.inc-note-state');

    // The control owns this list and mutates it in place, so it is a copy of
    // what the incident holds rather than the incident's own array: a save that
    // fails must not leave the card showing a value the server rejected.
    const selected = (inc.field_values[f.key] || []).slice();
    if (f.control === 'toggle') {
      block.appendChild(buildFieldToggle(f, inc, selected[0] || '', state));
      container.appendChild(block);
      return;
    }
    block.appendChild(buildSelect({
      options: f.options || [],
      groups: f.groups || null,
      definitions: f.definitions || null,
      accent: color[f.key],
      selected,
      onChange: () => saveCardField(inc, f.key, selected, state),
      // Adding a code here writes it to the vocabulary, exactly as adding one
      // from the document sidebar does — same list, same codebook.
      onAdd: async (val) => {
        const res = await fetch('/api/schema/option', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ field: f.key, option: val }),
        });
        const updated = await res.json().catch(() => null);
        if (updated && updated.options) {
          f.options = updated.options;
          f.groups = updated.groups || f.groups;
          f.definitions = updated.definitions || f.definitions;
        }
        return f.options || [];
      },
    }));
    container.appendChild(block);
  });
}

// A field with two states and no third — Translated — as a switch rather than a
// menu: one control, flipped between its two values, reading as the answer it is
// without being opened.
//
// The catch a switch has to earn its way past is that a field has three states,
// not two: on, off, and never answered. An unanswered switch that already looks
// like "no" would put an answer on every incident nobody has looked at. So it
// sits visibly unset until it is touched — grey, knob centred, "Not answered" —
// and the × puts it back there, because a stray click must not be permanent.
function buildFieldToggle(f, inc, current, state) {
  const [onValue, offValue] = f.options || [];
  const wrap = document.createElement('div');
  wrap.className = 'tow-switch';
  if (!onValue || !offValue) {
    wrap.innerHTML = NODATA;      // a switch needs both its states to exist
    return wrap;
  }

  const sw = document.createElement('button');
  sw.className = 'sw';
  sw.setAttribute('role', 'switch');
  sw.innerHTML = '<span class="sw-knob"></span>';
  const name = document.createElement('span');
  name.className = 'sw-name';
  const clear = document.createElement('button');
  clear.className = 'sw-clear';
  clear.textContent = '\u00d7';
  clear.title = 'Clear — back to unanswered';

  const paint = () => {
    sw.classList.toggle('on', current === onValue);
    sw.setAttribute('aria-checked', current === onValue ? 'true'
                                  : current === offValue ? 'false' : 'mixed');
    sw.classList.toggle('unset', !current);
    sw.title = current ? `${current} — press to flip` : `Press to set “${onValue}”`;
    name.textContent = current || 'Not answered';
    name.classList.toggle('unset', !current);
    clear.hidden = !current;
    attachDefTip(name, current, (f.definitions || {})[current], color[f.key]);
  };

  const set = async (next) => {
    const ok = await saveCardField(inc, f.key, next ? [next] : [], state);
    if (ok === false) return;
    current = next;
    paint();
  };
  // Unanswered flips to the first value; after that it just swaps.
  sw.onclick = () => set(current === onValue ? offValue : onValue);
  clear.onclick = () => set('');

  wrap.append(sw, name, clear);
  paint();
  return wrap;
}

// Saved the moment the menu changes, and reported beside the label the way the
// comment box reports — this is the only control on a card that writes an answer
// rather than a link, so "did that land?" has to be answerable without opening
// the JSON panel.
async function saveCardField(inc, key, answer, state) {
  const say = (msg, ok) => {
    if (!state) return;
    clearTimeout(state._clear);
    state.textContent = msg;
    state.classList.toggle('on', !!ok);
    if (ok) state._clear = setTimeout(() => {
      state.textContent = ''; state.classList.remove('on');
    }, 2000);
  };
  say('Saving…', false);
  let res, d = {};
  try {
    res = await fetch(`/api/incident/${encodeURIComponent(inc.incident_id)}/field`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key, answer }), keepalive: true,
    });
    d = await res.json().catch(() => ({}));
  } catch (e) {
    say('Not saved — no connection', false);
    return false;
  }
  if (!res.ok) {
    // A code renamed or removed in the Codebook since this menu was built is the
    // one thing this can fail on. Say which, and redraw from the server rather
    // than leaving a chip that exists only on screen.
    say(d.error === 'unknown value'
      ? `Not saved — “${(d.values || []).join('”, “')}” is no longer in the codebook`
      : 'Not saved', false);
    refreshIncidents();
    return false;
  }
  inc.field_values[key] = Array.isArray(d.answer) ? d.answer
                        : (d.answer ? [d.answer] : []);
  say(d.synced ? 'Saved ✓' : 'Saved locally ✓', true);
  refreshTile(inc);
  return true;
}

// The pooled characteristics palette (left column) — draggable chips grouped by
// role, each marked with the claims it's already used in.
function buildPalette(container, inc) {
  if (!inc) return;
  container.innerHTML = '';
  const palette = document.createElement('div');
  palette.className = 'tow-field';
  // Named again: Published and Domain now sit directly above this in the column,
  // and two labelled blocks over an unlabelled list made the characteristics
  // read as part of them.
  palette.innerHTML = `<div class="tow-label">Characteristics</div>`;
  // Every characteristic, System and Developer included — one list, one way to
  // code, one way to drag into a claim.
  const anyValues = ROLES.some(r => (inc.role_values[r.role] || []).length)
    || Object.keys(inc.role_notes || {}).length;
  if (!anyValues) {
    const nd = document.createElement('div'); nd.innerHTML = NODATA;
    palette.appendChild(nd);
  } else {
    ROLES.forEach(r => {
      const vals = inc.role_values[r.role] || [];
      if (!vals.length && !(inc.role_notes || {})[r.role]) return;
      const row = document.createElement('div'); row.className = 'pal-row';
      const lbl = document.createElement('span');
      lbl.className = 'pal-role'; lbl.style.color = roleColor(r.role); lbl.textContent = r.label;
      const chips = document.createElement('div'); chips.className = 'pal-chips';
      const note = (inc.role_notes || {})[r.role];
      if (note) {
        const n = document.createElement('div');
        n.className = 'pal-note';
        n.textContent = note;
        chips.appendChild(n);
      }
      vals.forEach(v => {
        chips.appendChild(makeChip(r.role, v, usedInClaims(inc, r.role, v), inc, r.role));
        // The open panel follows its own chip, breaking the flex row so it reads
        // as belonging to that value rather than to the role as a whole.
        if (isQuotesOpen(inc, r.role, v)) chips.appendChild(quotePanel(inc, r.role, v));
      });
      row.appendChild(lbl); row.appendChild(chips);
      palette.appendChild(row);
    });
  }
  container.appendChild(palette);
}


// The claim groups (right column) — each a fill-in-the-blank sentence + drop zone.
// Rebuilds itself on every change and persists per incident.
function buildGroupsUI(container, inc) {
  if (!inc) return;
  // Always start with one actor context holding one empty claim. Empty groups
  // aren't saved server-side, so this just seeds the template each load.
  if (!inc.groups.length) inc.groups.push(newGroup(inc));
  container.innerHTML = '';

  const groupsWrap = document.createElement('div');
  groupsWrap.className = 'tow-field';
  groupsWrap.innerHTML = `<div class="tow-label">Groups (linked claims)</div>`;
  inc.groups.forEach(grp => groupsWrap.appendChild(buildGroupBox(inc, grp, container)));

  const add = document.createElement('button');
  add.className = 'grp-add'; add.textContent = '+ New actor group';
  add.onclick = () => {
    inc.groups.push(newGroup(inc));
    saveGroups(inc);
    buildGroupsUI(container, inc);
  };
  groupsWrap.appendChild(add);
  container.appendChild(groupsWrap);
  refreshDraggables(inc);   // keep the chips' claim marks in step with the claims
}

// Ids are per-incident counters, unique only within their own scope: group ids
// across the incident, claim ids within their group.
function nextId(list) {
  return String(list.reduce((mx, x) => Math.max(mx, parseInt(x.id, 10) || 0), 0) + 1);
}

function newClaim(grp) {
  // harmed_parties is plural — a single harm can land on several parties. The
  // singular harmed_party is the pre-plural field and must not be seeded here,
  // or every new claim carries a dead null nobody reads.
  return { id: nextId(grp.claims || []), harm: null, harmed_parties: [], factors: [] };
}

function newGroup(inc) {
  const g = { id: nextId(inc.groups || []), actor: null, system: null, developer: null,
              claims: [], omit: [] };
  g.claims.push(newClaim(g));
  return g;
}

// A draggable palette chip (characteristics palette only). `claims` is the list
// of claim ids this value is already in — a used chip stays fully draggable,
// since the same characteristic is expected to appear in several claims; it just
// carries the claim numbers so you can see what's still unplaced.
function makeChip(role, value, claims, inc, kind) {
  claims = claims || [];
  const chip = document.createElement('span');
  const open = inc && isQuotesOpen(inc, kind, value);
  chip.className = 'drag-chip' + (claims.length ? ' used' : '') + (open ? ' open' : '');
  chip.style.background = roleColor(role) + (claims.length ? '22' : '44');
  chip.style.borderColor = roleColor(role);
  chip.draggable = true;
  const n = inc ? evidenceFor(inc, kind, value).length : 0;
  chip.title = (claims.length
    ? `${roleLabel(role)} — used in claim ${claims.join(', ')}. Drag again to add it to another claim.`
    : `${roleLabel(role)} — not yet used. Drag into a claim.`)
    + `\nClick to ${open ? 'hide' : 'show'} the ${n} quote(s) behind it.`;
  chip.appendChild(document.createTextNode(value));
  // How much evidence sits behind this value, so an unsupported one is visible
  // without opening it. Distinct from the claim badges, which are counts of use.
  if (inc) {
    const qn = document.createElement('span');
    qn.className = 'chip-qn';
    qn.textContent = n ? '❝' + n : '❝0';
    chip.appendChild(qn);
  }
  // One badge per claim, so each claim reads as its own mark rather than as a
  // run-together number. Capped at three so a much-reused value can't stretch
  // the chip; the overflow badge says how many more.
  claims.slice(0, 3).forEach(id => {
    const badge = document.createElement('span');
    badge.className = 'chip-used';
    badge.textContent = id;
    chip.appendChild(badge);
  });
  if (claims.length > 3) {
    const more = document.createElement('span');
    more.className = 'chip-used chip-more';
    more.textContent = '+' + (claims.length - 3);
    chip.appendChild(more);
  }
  chip.ondragstart = (e) => {
    chip._dragged = true;
    e.dataTransfer.setData('text/plain', JSON.stringify({ role, value }));
    e.dataTransfer.effectAllowed = 'copy';
  };
  // Click reveals the evidence. Guarded so the click that ends a drag doesn't
  // also toggle the panel.
  if (inc) {
    chip.onclick = () => {
      if (chip._dragged) { chip._dragged = false; return; }
      toggleQuotes(inc, kind, value);
      refreshDraggables(inc);
    };
  }
  return chip;
}

// ---------- raw JSON for one incident ----------
// Fetched rather than printed from INCIDENTS, because the card's own object is
// the *aggregated view* (pooled values, pruned claims) while what's usually
// wanted here is the record as stored. The response says which one it is.
async function toggleJson(incId) {
  const panel = document.querySelector(`.json-panel[data-inc="${CSS.escape(incId)}"]`);
  const btn = document.querySelector(`.json-btn[data-inc="${CSS.escape(incId)}"]`);
  if (!panel) return;
  if (!panel.hidden) {                       // open -> close
    panel.hidden = true;
    if (btn) btn.classList.remove('on');
    return;
  }
  panel.hidden = false;
  if (btn) btn.classList.add('on');
  panel.textContent = 'Loading…';
  try {
    const res = await fetch('/api/incident/' + encodeURIComponent(incId) + '/json'
                            + (CODER ? '?coder=' + encodeURIComponent(CODER) : ''));
    const j = await res.json();
    if (!res.ok) throw new Error(j.error || res.status);
    panel.innerHTML = '';
    const bar = document.createElement('div');
    bar.className = 'json-bar';
    const src = document.createElement('div');
    src.className = 'json-src';
    src.textContent = 'source: ' + j.source;
    const tree = jsonTree(j.incident);
    const act = (label, fn) => {
      const b = document.createElement('button');
      b.className = 'json-act'; b.textContent = label; b.onclick = fn;
      return b;
    };
    bar.appendChild(src);
    bar.appendChild(act('Collapse all', () =>
      tree.querySelectorAll('.jn').forEach(n => n.querySelector('.jn-body') && n.classList.add('collapsed'))));
    bar.appendChild(act('Expand all', () =>
      tree.querySelectorAll('.jn').forEach(n => n.classList.remove('collapsed'))));
    const copy = act('Copy', async () => {
      try {
        await navigator.clipboard.writeText(JSON.stringify(j.incident, null, 2));
        copy.textContent = 'Copied ✓';
        setTimeout(() => { copy.textContent = 'Copy'; }, 1200);
      } catch (_) { copy.textContent = 'Copy failed'; }
    });
    bar.appendChild(copy);
    panel.appendChild(bar);
    panel.appendChild(tree);
  } catch (e) {
    panel.textContent = 'Could not load JSON: ' + e.message;
  }
}

// A collapsible, colour-coded rendering of a JSON value. Plain JSON.stringify
// output is a wall of monospace where the only cue to structure is how far a
// line is indented — fine for ten lines, not for an incident. Here keys are
// distinguished from values by colour, every object and array folds away behind
// its own header, and an indent guide runs down each level so a nested value can
// be traced back to what contains it.
function jsonTree(value) {
  const root = document.createElement('div');
  root.className = 'json-tree';
  root.appendChild(jsonNode(null, value, true));
  return root;
}

function jsonLeaf(value) {
  if (value === null) return '<span class="jn-null">null</span>';
  const t = typeof value;
  if (t === 'string') return `<span class="jn-str">${escapeHtml(JSON.stringify(value))}</span>`;
  if (t === 'number') return `<span class="jn-num">${value}</span>`;
  if (t === 'boolean') return `<span class="jn-bool">${value}</span>`;
  return escapeHtml(String(value));
}

function jsonNode(key, value, last) {
  const wrap = document.createElement('div');
  wrap.className = 'jn';
  // Array items have no key of their own; their position is the indent.
  const keyHtml = key === null ? ''
    : `<span class="jn-key">${escapeHtml(JSON.stringify(key))}</span><span class="jn-p">: </span>`;
  const comma = last ? '' : '<span class="jn-p">,</span>';

  if (value === null || typeof value !== 'object') {
    wrap.innerHTML = `<span class="jn-caret"></span>${keyHtml}${jsonLeaf(value)}${comma}`;
    return wrap;
  }
  const isArr = Array.isArray(value);
  const entries = isArr ? value.map((v, i) => [null, v]) : Object.entries(value);
  const [open, close] = isArr ? ['[', ']'] : ['{', '}'];
  if (!entries.length) {   // nothing to fold away
    wrap.innerHTML = `<span class="jn-caret"></span>${keyHtml}`
      + `<span class="jn-empty">${open}${close}</span>${comma}`;
    return wrap;
  }

  const head = document.createElement('div');
  head.className = 'jn-head';
  head.innerHTML = `<span class="jn-caret">▾</span>${keyHtml}<span class="jn-p">${open}</span>`
    + `<span class="jn-sum"> … <span class="jn-p">${close}</span>${comma}</span>`
    + `<span class="jn-count">${entries.length} ${isArr
        ? (entries.length === 1 ? 'item' : 'items')
        : (entries.length === 1 ? 'key' : 'keys')}</span>`;
  head.onclick = (e) => { e.stopPropagation(); wrap.classList.toggle('collapsed'); };

  const body = document.createElement('div');
  body.className = 'jn-body';
  entries.forEach(([k, v], i) => body.appendChild(jsonNode(k, v, i === entries.length - 1)));

  const tail = document.createElement('div');
  tail.className = 'jn-tail';
  tail.innerHTML = `<span class="jn-p">${close}</span>${comma}`;

  wrap.appendChild(head); wrap.appendChild(body); wrap.appendChild(tail);
  return wrap;
}

// ---------- evidence behind a characteristic ----------
// Which quotes justify one pooled value. `kind` is the tag the quote carries —
// the characteristic's role ('harm').
function evidenceFor(inc, kind, value) {
  return ((inc.value_quotes || {})[kind] || {})[value] || [];
}

function quotesKey(kind, value) { return kind + ' ' + value; }

// Which evidence panels are open, keyed by incident id. Held here rather than on
// the incident object because a refreshed card is handed a *new* object from the
// server — state hanging off the old one would shut every panel on the card you
// had just been reading. Survives both the palette rebuild after a claim change
// and a card re-render after a save.
const OPEN_QUOTES = {};

function openSet(inc) {
  return OPEN_QUOTES[inc.incident_id] || (OPEN_QUOTES[inc.incident_id] = new Set());
}

function isQuotesOpen(inc, kind, value) {
  return openSet(inc).has(quotesKey(kind, value));
}

function toggleQuotes(inc, kind, value) {
  const open = openSet(inc);
  const k = quotesKey(kind, value);
  if (!open.delete(k)) open.add(k);
}

// The panel itself: every passage this coder highlighted for the value, with the
// document it came from (incidents can pool several).
function quotePanel(inc, kind, value) {
  const panel = document.createElement('div');
  panel.className = 'qt-panel';
  const quotes = evidenceFor(inc, kind, value);
  const head = document.createElement('div');
  head.className = 'qt-head';
  head.textContent = quotes.length
    ? `${quotes.length} quote(s) for “${value}”`
    : `“${value}”`;
  panel.appendChild(head);
  if (!quotes.length) {
    const none = document.createElement('div');
    none.className = 'qt-none';
    none.textContent = 'Selected without a highlighted passage.';
    panel.appendChild(none);
    return panel;
  }
  const multiDoc = new Set(quotes.map(q => q.doc_key)).size > 1;
  quotes.forEach(q => {
    const item = document.createElement('div');
    item.className = 'qt-item';
    const t = document.createElement('span');
    t.className = 'qt-text';
    t.textContent = '“' + q.text + '”';
    item.appendChild(t);
    // Name the source only when the incident pools more than one document —
    // otherwise it's the same title repeated under every quote.
    if (multiDoc) {
      const src = document.createElement('span');
      src.className = 'qt-src';
      src.textContent = '— ' + q.title;
      item.appendChild(src);
    }
    panel.appendChild(item);
  });
  return panel;
}

// A group is one actor context — "<actor> using <system> developed by <developer>"
// — with its claims listed underneath. The header and each claim row are separate
// drop zones, so a dragged chip's destination is never ambiguous: actor / system /
// developer land in the header, harm / harmed party / factor land in the claim you
// drop them on.
function buildGroupBox(inc, grp, container) {
  const box = document.createElement('div');
  box.className = 'grp-box';

  const top = document.createElement('div');
  top.className = 'grp-top';
  top.innerHTML = `<span class="grp-name">Group ${escapeHtml(grp.id)}</span>`;
  const del = document.createElement('button');
  del.className = 'grp-del'; del.textContent = '×';
  del.title = 'Delete this actor group and all its claims';
  del.onclick = () => {
    inc.groups = inc.groups.filter(g => g !== grp);
    saveGroups(inc);
    buildGroupsUI(container, inc);
  };
  top.appendChild(del);
  box.appendChild(top);

  box.appendChild(actorHeader(inc, grp, container));

  const claims = document.createElement('div');
  claims.className = 'grp-claims';
  (grp.claims || []).forEach(cl => claims.appendChild(claimRow(inc, grp, cl, container)));
  box.appendChild(claims);

  const add = document.createElement('button');
  add.className = 'grp-add grp-add-claim'; add.textContent = '+ Claim';
  add.onclick = () => {
    grp.claims = grp.claims || [];
    grp.claims.push(newClaim(grp));
    saveGroups(inc);
    buildGroupsUI(container, inc);
  };
  box.appendChild(add);
  return box;
}

// The slots shared by every claim in the group. The actor is single — a second
// actor is a second context, so it gets its own group — while the systems it
// used and who developed them are lists, joined by "&" the way a claim's factors
// are. Dropping onto the actor replaces; dropping onto the others adds.
function actorHeader(inc, grp, container) {
  const h = document.createElement('div');
  h.className = 'grp-sentence grp-head';
  const rebuild = () => { saveGroups(inc); buildGroupsUI(container, inc); };
  const omitted = (role) => (grp.omit || []).includes(role);

  // An empty slot: the placeholder, plus — for an optional clause — an × that
  // takes the clause out of this group's sentence rather than a value out of it.
  const emptySlot = (role, placeholder, onOmit) => {
    const span = document.createElement('span');
    span.className = 'sent-slot';
    const ph = document.createElement('span');
    ph.className = 'sent-ph';
    ph.style.color = roleInk(role);
    ph.textContent = `[${placeholder}]`;
    span.appendChild(ph);
    if (onOmit) {
      const x = document.createElement('button');
      x.className = 'sent-x sent-omit'; x.textContent = '×';
      x.title = `Drop "${placeholder}" from this group's sentence`;
      x.onclick = onOmit;
      span.appendChild(x);
    }
    return span;
  };

  // The actor: one value, and a drop replaces it.
  const scalarSlot = (role, placeholder) => {
    const v = grp[role];
    if (!v) return emptySlot(role, placeholder);
    const span = document.createElement('span');
    span.className = 'sent-slot';
    span.appendChild(valueChip(role, v, () => { grp[role] = null; rebuild(); }));
    return span;
  };

  // Systems and developers: every value dropped in, joined by "&", each with its
  // own × — the same shape a claim's harmed parties and factors take.
  const listSlot = (role, key, placeholder, onOmit) => {
    const vals = groupValues(grp, role);
    if (!vals.length) return emptySlot(role, placeholder, onOmit);
    const span = document.createElement('span');
    span.className = 'sent-slot';
    vals.forEach((v, i) => {
      if (i) span.appendChild(document.createTextNode(' & '));
      span.appendChild(valueChip(role, v, () => {
        grp[key] = groupValues(grp, role).filter(x => x !== v);
        grp[role] = null;              // the pre-plural single value is spent
        rebuild();
      }));
    });
    return span;
  };

  h.appendChild(scalarSlot('actor', 'Actor'));
  // The whole thing reads as one sentence across two blocks: this header, then
  // each numbered claim under it. The comma after the actor is always there;
  // the one closing the clauses only if a clause actually rendered, so a group
  // that drops both reads "[Actor]," and not a stranded pair of commas.
  h.appendChild(document.createTextNode(','));
  // "using …" / "developed by …" appear once the incident has something to drop
  // there, or once they're filled; otherwise the header reads as complete. A
  // group that doesn't need one can also drop it outright — not every actor
  // context is about a named system, and an empty clause left standing reads as
  // an unanswered question rather than an inapplicable one.
  let anyClause = false;
  OPTIONAL_CLAIM_ROLES.forEach(cfg => {
    const filled = groupValues(grp, cfg.role).length;
    const available = ((inc.role_values || {})[cfg.role] || []).length;
    if (!filled && (omitted(cfg.role) || !available)) return;
    anyClause = true;
    h.appendChild(document.createTextNode(cfg.lead));
    const sp = listSlot(cfg.role, cfg.key, cfg.placeholder, () => {
      grp.omit = (grp.omit || []).concat([cfg.role]);
      rebuild();
    });
    if (!filled) sp.classList.add('opt');
    h.appendChild(sp);
  });
  if (anyClause) h.appendChild(document.createTextNode(','));

  // Bringing a dropped clause back. Only offered where there is something to put
  // in it, matching the rule for showing the clause in the first place.
  const restorable = OPTIONAL_CLAIM_ROLES.filter(cfg =>
    omitted(cfg.role) && !groupValues(grp, cfg.role).length
    && ((inc.role_values || {})[cfg.role] || []).length);
  restorable.forEach(cfg => {
    const b = document.createElement('button');
    b.className = 'sent-restore';
    b.textContent = '+ ' + cfg.placeholder;
    b.title = `Put "${cfg.lead.trim()} [${cfg.placeholder}]" back in this group's sentence`;
    b.onclick = () => { grp.omit = (grp.omit || []).filter(r => r !== cfg.role); rebuild(); };
    h.appendChild(b);
  });

  dropZone(h, GROUP_ROLES, (m) => {
    const key = GROUP_LIST_KEYS[m.role];
    if (key) {                      // list: a drop adds, duplicates are ignored
      const vals = groupValues(grp, m.role);
      if (vals.includes(m.value)) return;
      grp[key] = vals.concat([m.value]);
      grp[m.role] = null;           // folded into the list; don't count it twice
    } else {
      grp[m.role] = m.value;        // the actor: a drop replaces
    }
    // Dropping into a clause the group had dropped is the coder saying they want
    // it after all, so the drop is never refused for having been put away.
    grp.omit = (grp.omit || []).filter(r => r !== m.role);
    rebuild();
  });
  return h;
}

// One claim: "allegedly contributed to <harm> affecting <party> because of
// <factors>." harm and
// party are single-valued; factors is a list, since several contributing causes
// for one harm read unambiguously.
function claimRow(inc, grp, cl, container) {
  const row = document.createElement('div');
  row.className = 'grp-sentence grp-claim';

  const num = document.createElement('span');
  num.className = 'claim-num'; num.textContent = grp.id + '.' + cl.id;
  row.appendChild(num);

  const rebuild = () => { saveGroups(inc); buildGroupsUI(container, inc); };

  // The one single-valued slot: a drop replaces whatever is there.
  const scalarSlot = (role, placeholder) => {
    const span = document.createElement('span');
    span.className = 'sent-slot';
    if (!cl[role]) {
      span.innerHTML = `<span class="sent-ph" style="color:${roleInk(role)}">`
                      + `[${escapeHtml(placeholder)}]</span>`;
      return span;
    }
    span.appendChild(valueChip(role, cl[role], () => { cl[role] = null; rebuild(); }));
    return span;
  };

  // A multi-valued slot: every value dropped in, joined by "&". Harmed parties
  // and factors both read as conjunctions, so they share this.
  const listSlot = (role, key, placeholder) => {
    const span = document.createElement('span');
    span.className = 'sent-slot';
    const vals = cl[key] || [];
    if (!vals.length) {
      span.innerHTML = `<span class="sent-ph" style="color:${roleInk(role)}">`
                      + `[${escapeHtml(placeholder)}]</span>`;
      return span;
    }
    vals.forEach((v, i) => {
      if (i) span.appendChild(document.createTextNode(' & '));
      span.appendChild(valueChip(role, v, () => {
        cl[key] = cl[key].filter(x => x !== v);
        rebuild();
      }));
    });
    return span;
  };

  row.appendChild(document.createTextNode('allegedly contributed to '));
  row.appendChild(scalarSlot('harm', 'harm'));
  row.appendChild(document.createTextNode(' affecting '));
  row.appendChild(listSlot('harmed_party', 'harmed_parties', 'harmed party/ies'));
  row.appendChild(document.createTextNode(' because of '));
  row.appendChild(listSlot('factor', 'factors', 'factor(s)'));
  row.appendChild(document.createTextNode('.'));

  const del = document.createElement('button');
  del.className = 'grp-del claim-del'; del.textContent = '×'; del.title = 'Delete claim';
  del.onclick = () => {
    grp.claims = grp.claims.filter(c => c !== cl);
    saveGroups(inc);
    buildGroupsUI(container, inc);
  };
  row.appendChild(del);

  dropZone(row, CLAIM_ROLES_DROP, (m) => {
    const key = CLAIM_LIST_KEYS[m.role];
    if (key) {                      // list: a drop adds, duplicates are ignored
      cl[key] = cl[key] || [];
      if (cl[key].includes(m.value)) return;
      cl[key].push(m.value);
    } else {
      cl[m.role] = m.value;         // scalar: a drop replaces
    }
    rebuild();
  });
  return row;
}

// A filled slot: the value plus a × that clears it.
function valueChip(role, value, onRemove) {
  const chip = document.createElement('span');
  chip.className = 'sent-v';
  chip.style.background = roleColor(role) + '33';
  chip.style.borderColor = roleColor(role);
  chip.appendChild(document.createTextNode(value));
  const x = document.createElement('button');
  x.className = 'sent-x'; x.textContent = '×'; x.title = 'Remove';
  x.onclick = onRemove;
  chip.appendChild(x);
  return chip;
}

// Wire an element as a drop target for a given set of roles. A chip of the wrong
// kind is refused rather than silently dropped somewhere it doesn't belong.
function dropZone(el, roles, apply) {
  el.ondragover = (e) => { e.preventDefault(); el.classList.add('over'); };
  el.ondragleave = () => el.classList.remove('over');
  el.ondrop = (e) => {
    e.preventDefault(); el.classList.remove('over');
    let m; try { m = JSON.parse(e.dataTransfer.getData('text/plain')); } catch (_) { return; }
    if (!m || !m.role || !m.value || !roles.includes(m.role)) return;
    apply(m);
  };
}

