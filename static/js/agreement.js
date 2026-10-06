// Agreement tab: Cohen's kappa between two coders, for every category and code.
//
// Computed on the server (agreement.py) from both coders' coding; this file only
// asks and draws. Each category is one block — its pooled kappa up top, each
// code beneath it — and a kappa the data can't define (nobody varied) reads "—"
// rather than a made-up number.

import { cbGoToIncident } from './codebook.js';
import { escapeHtml } from './persist.js';
import { ROLE } from './state.js';

let CHOICE = { a: '', b: '', scope: 'coded' };
let DATA = null;

// Landis & Koch's bands — the conventional reading of a kappa.
export function kappaBand(k) {
  if (k === null || k === undefined) return { label: 'undefined', cls: 'k-na' };
  if (k < 0) return { label: 'worse than chance', cls: 'k-0' };
  if (k <= 0.2) return { label: 'slight', cls: 'k-1' };
  if (k <= 0.4) return { label: 'fair', cls: 'k-2' };
  if (k <= 0.6) return { label: 'moderate', cls: 'k-3' };
  if (k <= 0.8) return { label: 'substantial', cls: 'k-4' };
  return { label: 'almost perfect', cls: 'k-5' };
}

const fmt = (k) => (k === null || k === undefined ? '—' : k.toFixed(2));
const pct = (x) => (x === null || x === undefined ? '—' : Math.round(x * 100) + '%');

export async function loadAgreement() {
  const root = document.getElementById('agreement');
  if (!DATA) root.innerHTML = '<div class="ag-empty">Computing agreement…</div>';
  const q = new URLSearchParams({ scope: CHOICE.scope });
  if (CHOICE.a) q.set('a', CHOICE.a);
  if (CHOICE.b) q.set('b', CHOICE.b);
  try {
    const r = await fetch('/api/agreement?' + q);
    if (!r.ok) throw new Error(await r.text());
    DATA = await r.json();
  } catch (e) {
    root.innerHTML = '<div class="ag-empty">Could not compute agreement — it needs two coders.</div>';
    return;
  }
  CHOICE.a = DATA.a; CHOICE.b = DATA.b;
  render();
}

async function coderList() {
  try { return (await (await fetch('/api/coders')).json()).coders || []; }
  catch (_) { return [DATA.a, DATA.b]; }
}

async function render() {
  const root = document.getElementById('agreement');
  const coders = await coderList();
  const opts = (sel) => coders.map(c =>
    `<option value="${escapeHtml(c)}"${c === sel ? ' selected' : ''}>${escapeHtml(c)}</option>`).join('');

  root.innerHTML = '';
  const head = document.createElement('div');
  head.className = 'ag-head';
  head.innerHTML = `
    <h1>Inter-coder agreement</h1>
    <div class="ag-controls">
      <label>Coder <select id="agA">${opts(DATA.a)}</select></label>
      <label>vs <select id="agB">${opts(DATA.b)}</select></label>
      <label>Incidents
        <select id="agScope">
          <option value="coded"${DATA.scope === 'coded' ? ' selected' : ''}>both have coded</option>
          <option value="complete"${DATA.scope === 'complete' ? ' selected' : ''}>both signed off</option>
        </select></label>
    </div>
    <p class="ag-what">Cohen’s kappa (κ) measures how much two coders agree <i>beyond what luck alone would produce</i>: 1 is perfect agreement, 0 is no better than chance. Click any κ to see how it was worked out.</p>
    <p>Over <b>${DATA.n_incidents}</b> incident${DATA.n_incidents === 1 ? '' : 's'}
      (${escapeHtml(DATA.a)} has coded ${DATA.coded_by_a}, ${escapeHtml(DATA.b)} ${DATA.coded_by_b},
      ${DATA.coded_by_both} by both${DATA.set_aside ? `, ${DATA.set_aside} set aside as not an incident` : ''}).
      Each code is a yes/no per incident — did the coder apply it? — and a category’s figure pools
      the codes either coder used. Harm and factor also have a subcategory κ, where a coder
      “applied” a subcategory by applying any code in it. A “—” means κ is undefined: the coders never varied.</p>`;
  root.appendChild(head);
  const redo = () => {
    CHOICE = { a: head.querySelector('#agA').value, b: head.querySelector('#agB').value,
               scope: head.querySelector('#agScope').value };
    loadAgreement();
  };
  head.querySelectorAll('select').forEach(s => { s.onchange = redo; });

  root.appendChild(incidentTable());
  DATA.categories.forEach(c => root.appendChild(categoryBlock(c)));
}

// One row per incident: its own κ over every code in the vocabulary (each code a
// yes/no that both coders answered), overall and within each category.
// Click a column heading to sort by it; click again to reverse. κ columns start
// lowest-first, since the incidents to look at are the ones the coders disagree on,
// and an incident with no κ (undefined) always sorts last.
let SORT = { col: 'id', dir: 1 };
const sortValue = (i, col) => (col === 'id' ? i.id
  : col === 'overall' ? i.overall.kappa : i.categories[col].kappa);

function incidentTable() {
  const sec = document.createElement('section');
  sec.className = 'ag-cat';
  const cats = DATA.categories;
  const list = (DATA.incidents || []).slice().sort((x, y) => {
    const a = sortValue(x, SORT.col), b = sortValue(y, SORT.col);
    if (a === b) return x.id.localeCompare(y.id, undefined, { numeric: true });
    if (a === null) return 1;
    if (b === null) return -1;
    return (typeof a === 'string' ? a.localeCompare(b, undefined, { numeric: true })
                                  : a - b) * SORT.dir;
  });
  const th = (col, label, title) => `<th class="ag-sortable${SORT.col === col ? ' sorted' : ''}"
    data-col="${escapeHtml(col)}" title="${escapeHtml(title || 'Sort by ' + label)}">${escapeHtml(label)}${
    SORT.col === col ? (SORT.dir === 1 ? ' ▲' : ' ▼') : ''}</th>`;
  const status = (s) => (s === 'complete' ? '✓' : s === 'not_an_incident' ? '✗' : '·');
  const rows = list.map(i => `
    <tr>
      <td class="ag-val"><b>${escapeHtml(i.id)}</b>
        <span class="ag-inc-title">${escapeHtml(i.title || '')}</span></td>
      <td>${kappaCell(i.overall, i.id + ' · all codes, all categories', 'codes')}</td>
      ${cats.map(c => `<td>${kappaCell(i.categories[c.key], i.id + ' · ' + c.label, 'codes')}</td>`).join('')}
      <td class="ag-st" title="${escapeHtml(DATA.a)}: ${escapeHtml(i.status_a || 'in progress')} · ${escapeHtml(DATA.b)}: ${escapeHtml(i.status_b || 'in progress')}">${status(i.status_a)} ${status(i.status_b)}</td>
    </tr>`).join('');
  sec.innerHTML = `
    <div class="ag-cat-head"><span class="ag-cat-name">By incident</span>
      <span class="ag-band" style="margin-left:auto">each incident’s κ over every code in the vocabulary</span></div>
    ${rows ? `<table class="ag-table ag-inc">
      <thead><tr>${th('id', 'Incident')}${th('overall', 'Overall κ')}
        ${cats.map(c => th(c.key, c.label)).join('')}
        <th title="Sign-off: ${escapeHtml(DATA.a)} then ${escapeHtml(DATA.b)} (✓ complete, ✗ not an incident, · in progress)">Status</th></tr></thead>
      <tbody>${rows}</tbody></table>` : '<div class="ag-none">No incidents in scope.</div>'}`;
  sec.querySelectorAll('th.ag-sortable').forEach(h => {
    h.onclick = () => {
      const col = h.dataset.col;
      SORT = col === SORT.col ? { col, dir: -SORT.dir } : { col, dir: 1 };
      sec.replaceWith(incidentTable());
    };
  });
  return sec;
}

// A κ chip. Click it for the worked formula with that cell's own numbers.
// `t` is any table carrying kappa + both / only_a / only_b / neither; `what` names
// what is being measured, for the heading of the worked calculation.
function kappaCell(t, what, unit) {
  const band = kappaBand(t.kappa);
  const data = escapeHtml(JSON.stringify(
    [t.both, t.only_a, t.only_b, t.neither, what || '', t.items || null, unit || 'items']));
  return `<span class="ag-k ${band.cls}" data-t="${data}" tabindex="0" role="button" ` +
         `title="${band.label} — click for the formula">${fmt(t.kappa)}</span>`;
}

// The worked calculation, in a popover under the chip that was clicked.

// What a κ is made of: this cell's counts, and who marked what.
function workedFormula(t) {
  const [a, b, c, d, what, items, unit] = t;
  const A = escapeHtml(DATA.a), B = escapeHtml(DATA.b);
  const n = a + b + c + d;
  return `
    ${what ? `<div class="ag-fx-title">${escapeHtml(what)}</div>` : ''}
    <p class="ag-fx-p">Out of <b>${n}</b> ${escapeHtml(unit)}, each coder either marked it or didn’t:</p>
    <table class="ag-fx-grid">
      <tr><th></th><th>${B} marked it</th><th>${B} didn’t</th></tr>
      <tr><th>${A} marked it</th><td>${a}<small>both</small></td><td>${b}<small>only ${A}</small></td></tr>
      <tr><th>${A} didn’t</th><td>${c}<small>only ${B}</small></td><td>${d}<small>neither</small></td></tr>
    </table>

    ${items ? markedList(items) : ''}`;
}

// Who marked what: the incidents (and codes) behind a, b and c, each incident a
// link to its card. The disagreements come first, since a long "both" list would
// otherwise push them out of sight. The "neither" cell is every code nobody marked, so isn't listed.
function markedList(items) {
  const group = (title, list) => !list.length ? '' : `
    <div class="ag-fx-grp"><div class="ag-fx-h">${title} <span>${list.length}</span></div>
      ${list.map(it => `<div class="ag-fx-it">
        <a href="#" class="ag-go" data-inc="${escapeHtml(it.inc)}">${escapeHtml(it.inc)}</a>
        ${it.label ? `<span>${escapeHtml(it.label)}</span>` : ''}</div>`).join('')}
    </div>`;
  const a = escapeHtml(DATA.a), b = escapeHtml(DATA.b);
  return `<div class="ag-fx-marked">
    ${group(`Only ${a} marked`, items.only_a)}
    ${group(`Only ${b} marked`, items.only_b)}
    ${group(`Both ${a} and ${b} marked`, items.both)}</div>`;
}

let FX = null;
function closeFormula() { if (FX) { FX.remove(); FX = null; } }
document.addEventListener('click', (e) => {
  const go = e.target.closest && e.target.closest('.ag-go');
  if (go) { e.preventDefault(); closeFormula(); cbGoToIncident(go.dataset.inc); return; }
  const chip = e.target.closest && e.target.closest('.ag-k[data-t]');
  if (FX && FX.contains(e.target)) return;
  const same = FX && FX._chip === chip;
  closeFormula();
  if (!chip || same) return;
  let t; try { t = JSON.parse(chip.dataset.t); } catch (_) { return; }
  FX = document.createElement('div');
  FX.className = 'ag-fx';
  FX._chip = chip;
  FX.innerHTML = workedFormula(t);
  document.body.appendChild(FX);
  const r = chip.getBoundingClientRect();
  FX.style.top = (window.scrollY + r.bottom + 6) + 'px';
  FX.style.left = Math.max(8, Math.min(window.scrollX + r.left, window.innerWidth - FX.offsetWidth - 12)) + 'px';
});
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeFormula(); });

function categoryBlock(c) {
  const sec = document.createElement('section');
  sec.className = 'ag-cat';
  sec.style.setProperty('--cb-accent', (ROLE[c.key] || {}).color || '#d4d4d8');
  const p = c.pooled;
  const label = c.label;
  const codeRow = (v, nested) => `
    <tr>
      <td class="ag-val${nested ? ' ag-nested' : ''}">${escapeHtml(v.value)}</td>
      <td>${kappaCell(v, label + ' · ' + v.value, 'incidents')}</td>
      <td>${pct(v.observed)}</td>
      <td>${v.both}</td><td>${v.only_a}</td><td>${v.only_b}</td>
    </tr>`;
  // A category with subcategories lists its codes under each one, the
  // subcategory's own kappa ("applied any code in it") on its heading row.
  const rows = c.subcategories
    ? c.subcategories.map(s => `
    <tr class="ag-sub">
      <td class="ag-val">${escapeHtml(s.label)}
        <span class="ag-sub-note">${s.ungrouped ? 'no subcategory' : 'subcategory'}</span></td>
      <td>${s.ungrouped ? '' : kappaCell(s, label + ' · subcategory: ' + s.label, 'incidents')}</td>
      <td>${s.ungrouped ? '' : pct(s.observed)}</td>
      <td>${s.ungrouped ? '' : s.both}</td><td>${s.ungrouped ? '' : s.only_a}</td>
      <td>${s.ungrouped ? '' : s.only_b}</td>
    </tr>${s.values.map(v => codeRow(v, true)).join('')}`).join('')
    : c.values.map(v => codeRow(v, false)).join('');
  const bySub = c.pooled_by_subcategory;
  sec.innerHTML = `
    <div class="ag-cat-head">
      <span class="cb-dot"></span>
      <span class="ag-cat-name">${escapeHtml(c.label)}</span>
      <span class="ag-cat-k">
        ${bySub ? `<span class="ag-lvl">by subcategory</span>${kappaCell(bySub, label + ' · pooled over its subcategories', 'yes/no decisions')}` : ''}
        <span class="ag-lvl">${bySub ? 'by code' : ''}</span>${kappaCell(p, label + ' · pooled over the codes used', 'yes/no decisions')}
        <span class="ag-band">${kappaBand(p.kappa).label}</span></span>
    </div>
    ${c.values.length ? `
    <table class="ag-table">
      <thead><tr><th>${c.subcategories ? 'Subcategory / code' : 'Code'}</th><th>κ</th><th>Agree</th>
        <th title="Both coders applied it">Both</th>
        <th title="Only ${escapeHtml(DATA.a)}">Only ${escapeHtml(DATA.a)}</th>
        <th title="Only ${escapeHtml(DATA.b)}">Only ${escapeHtml(DATA.b)}</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>` : '<div class="ag-none">No codes used on these incidents.</div>'}`;
  return sec;
}
