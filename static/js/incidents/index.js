// The incidents view: the index of tiles, the four tabs, and opening one.
//
// What is on screen and what the server holds are kept in step here and nowhere
// else: INCIDENTS is this coder's incidents as last fetched, DIRTY_INCIDENTS is
// the ones a save has invalidated, and a card is built only when its tile is
// opened.

import { setView } from '../boot.js';
import { escapeHtml } from '../persist.js';
import { loadDoc } from '../reader.js';
import { incidentCard } from './card.js';
import { buildGroupsUI } from './claims.js';
import { buildIncidentComment, flushIncidentComments } from './comment.js';
import { buildCardFields } from './fields.js';
import { toggleJson } from './json.js';
import { buildPalette } from './palette.js';
import { completenessOf, wireComplete } from './signoff.js';

// Incidents whose data changed since they were rendered. A document save marks
// the incident it belongs to; nothing else needs a redraw, so switching tabs
// with none of these is free.
export const DIRTY_INCIDENTS = new Set();

export let INCIDENTS_RENDERED = false;

// Say that what is on screen no longer matches the coding. Called from the
// codebook, where a rename changes the chips a card shows.
export function markIncidentsStale() { INCIDENTS_RENDERED = false; }

// The display fields from /api/incidents. Held here because a card is now built
// on demand, long after the response that carried them has been consumed.
export let FIELDS = [];

// The one incident expanded right now, or null. Only one is ever open: the point
// of the collapsed index is that the page stays short enough to scan.
export let OPEN_INCIDENT = null;

export function markIncidentDirty(incId) {
  if (incId) DIRTY_INCIDENTS.add(incId);
}

// ---------- the four sections ----------
// A coder works top-down: what still needs coding, what has been started, then
// what has been settled — signed off, or ruled out. Settled work stays listed
// and countable rather than hidden, since "what have I finished" and "what did
// I rule out" are both part of the record.
export const isDone = (inc) => inc.status === 'complete';

export const isOut  = (inc) => inc.status === 'not_an_incident';

// Any highlighted evidence on a member document, regardless of whether it adds
// up to a complete claim yet — the same ground-truth `quotes` count a card's
// document row shows, not the pooled roles a claim reads (those can lag a
// quote by a save).
export const isStarted = (inc) => (inc.documents || []).some((d) => d.quotes > 0);

export const SECTIONS = [
  { id: 'todo', label: 'To code',         match: (inc) => !isDone(inc) && !isOut(inc) && !isStarted(inc) },
  { id: 'wip',  label: 'In progress',     match: (inc) => !isDone(inc) && !isOut(inc) && isStarted(inc) },
  { id: 'done', label: 'Complete',        match: isDone },
  { id: 'out',  label: 'Not an incident', match: isOut },
];

export const sectionOf = (inc) => (SECTIONS.find(s => s.match(inc)) || SECTIONS[0]).id;

export const sectionLabel = (id) => (SECTIONS.find(s => s.id === id) || SECTIONS[0]).label;

export const tileEl = (incId) =>
  document.querySelector(`.inc-tile[data-inc="${CSS.escape(incId)}"]`);

// ---------- "I'm working on this" ----------
// A coder's own reminder of which "to code" tiles they have their eye on —
// purely local, so it lives in this browser only and never reaches the other
// coder, the server, or Mongo. Keyed by coder so two people sharing a machine
// don't see each other's marks. Read straight from localStorage rather than
// importing CODER from coder.js: coder.js -> boot.js -> this module -> coder.js
// is already a cycle, and this module loads as part of resolving *that* import,
// before coder.js's own body — the line setting CODER — has run.
const CLAIM_KEY = 'claimed:' + (localStorage.getItem('coder') || '');

function loadClaims() {
  try { return new Set(JSON.parse(localStorage.getItem(CLAIM_KEY) || '[]')); }
  catch (e) { return new Set(); }
}

const CLAIMED = loadClaims();

function saveClaims() {
  try { localStorage.setItem(CLAIM_KEY, JSON.stringify(Array.from(CLAIMED))); }
  catch (e) { /* private browsing, storage full — the mark just won't persist */ }
}

export const isClaimed = (incId) => CLAIMED.has(incId);

// Only meaningful in "To code": once real evidence lands the tile moves to "In
// progress" on its own, where the dot already means something else.
export function toggleClaim(incId) {
  if (CLAIMED.has(incId)) CLAIMED.delete(incId); else CLAIMED.add(incId);
  saveClaims();
  const inc = INCIDENTS[incId];
  if (inc) refreshTile(inc);
}

// Bring the incidents view up to date with the least disturbance: nothing on the
// first visit but a full render, and after that only the tiles whose data moved.
export async function refreshIncidents() {
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
export function wireIncidentCard(root) {
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
export function tileTitle(inc) {
  if (inc.title) return escapeHtml(inc.title);
  const d = inc.documents[0];
  return `<span class="t-untitled" title="This incident has no title of its own \u2014`
       + ` showing its first document instead">${escapeHtml(d ? (d.title || '(untitled document)')
                                                             : '(no documents)')}</span>`;
}

// The status dot's classes and tooltip for one tile. Shared between building a
// tile fresh and repainting one in place, so the two can never drift apart.
function stateDot(inc, sec) {
  const ready = sec === 'wip' && completenessOf(inc).ok;
  const claimed = sec === 'todo' && isClaimed(inc.incident_id);
  const cls = 't-state ' + sec + (ready ? ' ready' : '') + (claimed ? ' claimed' : '');
  const title = sec === 'todo'
    ? (claimed ? 'You are working on this \u2014 click to clear'
               : 'Click to mark that you are working on this')
    : '';
  return { cls, title };
}

export function incidentTile(inc) {
  const encId = escapeHtml(inc.incident_id);
  const sec = sectionOf(inc);
  const dot = stateDot(inc, sec);
  return `<div class="inc-tile" data-inc="${encId}" data-sec="${sec}">
    <div class="tile-head" role="button" tabindex="0" aria-expanded="false">
      <div class="t-top"><span class="t-id">${encId}</span>
        <span class="t-flag"${inc.flagged ? '' : ' hidden'} title="You are not sure about this one">\u2691</span>
        <span class="${dot.cls}" data-claim="${encId}" title="${escapeHtml(dot.title)}"></span></div>
      <div class="t-title">${tileTitle(inc)}</div>
    </div>
    <div class="tile-card" hidden></div>
  </div>`;
}

// Repaint a collapsed tile from its incident. Cheap enough to call on every edit,
// so the index's "needs …" hint tracks the card a coder is working in.
export function refreshTile(inc) {
  const tile = tileEl(inc.incident_id);
  if (!tile) return;
  const sec = sectionOf(inc);
  tile.dataset.sec = sec;
  const t = tile.querySelector('.t-title');
  if (t) t.innerHTML = tileTitle(inc);
  const s = tile.querySelector('.t-state');
  if (s) {
    const dot = stateDot(inc, sec);
    s.className = dot.cls;
    s.title = dot.title;
  }
  const fl = tile.querySelector('.t-flag');
  if (fl) fl.hidden = !inc.flagged;
}

// The four sections are tabs, not one long scroll: only the selected one is on
// the page, so "Complete" and "Not an incident" cost nothing to carry around and
// the index stays a single screen of tiles.
export let ACTIVE_TAB = 'todo';

export function tocHtml(groups) {
  const tabs = SECTIONS.map(s =>
    `<button class="toc-link sec-${s.id}${s.id === ACTIVE_TAB ? ' active' : ''}"`
    + ` data-tab="${s.id}" role="tab" aria-selected="${s.id === ACTIVE_TAB}">`
    + `${escapeHtml(s.label)}<span class="toc-n">${groups[s.id].length}</span></button>`).join('');
  return `<div class="inc-toc">
    <div class="toc-row"><nav class="toc-links" role="tablist">${tabs}</nav></div>
  </div>`;
}

const SEC_EMPTY = { todo: 'left to code', wip: 'in progress' };

export function sectionHtml(sec, list) {
  const body = list.length
    ? `<div class="inc-grid">${list.map(incidentTile).join('')}</div>`
    : `<div class="sec-empty">Nothing ${SEC_EMPTY[sec.id] || 'here'}.</div>`;
  return `<section class="inc-section" id="sec-${sec.id}" data-sec="${sec.id}"`
    + `${sec.id === ACTIVE_TAB ? '' : ' hidden'}>${body}</section>`;
}

// Switch tabs. Collapsing first keeps an open card from being stranded on a
// section nobody is looking at.
export function showTab(tabId) {
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
export async function loadIncidents(opts = {}) {
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

export function wireIndex(root) {
  root.querySelectorAll('.inc-tile > .tile-head').forEach(head => {
    const incId = head.parentElement.dataset.inc;
    head.onclick = () => toggleIncident(incId);
    head.onkeydown = (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggleIncident(incId); }
    };
  });
  root.querySelectorAll('[data-tab]').forEach(b => b.onclick = () => showTab(b.dataset.tab));
  // The dot only means something in "to code" — elsewhere the click would set
  // a claim nothing ever shows, so it's only wired up there.
  root.querySelectorAll('.inc-tile[data-sec="todo"] .t-state[data-claim]').forEach(dot => {
    dot.onclick = (e) => { e.stopPropagation(); toggleClaim(dot.dataset.claim); };
  });
}

// The top bar is fixed and the table of contents sticks beneath it, so a plain
// scrollIntoView would park the heading underneath them both.
export function stickyOffset() {
  const bar = document.querySelector('.bar');
  const toc = document.querySelector('.inc-toc');
  return (bar ? bar.offsetHeight : 0) + (toc ? toc.offsetHeight : 0) + 14;
}

// ---------- opening one incident ----------

export function toggleIncident(incId) {
  if (OPEN_INCIDENT === incId) closeIncident();
  else openIncident(incId);
}

// Build the full card only when it is actually looked at. With fifty-odd
// incidents, rendering every palette and claim board up front was most of both
// the cost of this view and its height.
export function openIncident(incId, opts = {}) {
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

export function closeIncident() {
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

// The incident that follows `incId` within its own section — "To code" or "In
// progress" — or null at the end of it. Used to carry a coder on after a
// sign-off moves the tile they were in; read before that move, so `incId`'s
// tile is still where it was when the coder clicked.
export function nextInSectionAfter(incId) {
  const tile = tileEl(incId);
  const sec = tile ? tile.dataset.sec : 'todo';
  const list = Array.from(document.querySelectorAll(`.inc-tile[data-sec="${sec}"]`))
    .map(t => t.dataset.inc);
  const i = list.indexOf(incId);
  return (i >= 0 && i + 1 < list.length) ? list[i + 1] : null;
}

export const NODATA = '<span class="tow-nodata">No data</span>';

export let INCIDENTS = {};   // incident_id -> incident object, for post-render handlers
