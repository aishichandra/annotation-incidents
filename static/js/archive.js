// Archive tab: look back at rounds of coding that were set aside.
//
// Read-only. A round is a folder under archive/ (see routes/archive.py); this
// file lists the rounds, and for the chosen one shows every archived incident
// with each coder's reading of it side by side — their claims, answers, comment
// and the highlighted evidence — so you can check what was coded and by whom.

import { escapeHtml } from './persist.js';
import { ROLE } from './state.js';

let CHOSEN = '';
let ROUND = '';

export async function loadArchive() {
  const root = document.getElementById('archive');
  root.innerHTML = '<div class="ar-empty">Loading the archive…</div>';
  let list;
  try { list = (await (await fetch('/api/archives')).json()).rounds; }
  catch (e) { root.innerHTML = '<div class="ar-empty">Could not load the archive.</div>'; return; }
  if (!list.length) {
    root.innerHTML = '<div class="ar-empty">Nothing has been archived yet.</div>';
    return;
  }
  if (!list.some(r => r.name === CHOSEN)) CHOSEN = list[0].name;

  root.innerHTML = `
    <div class="ar-head">
      <h1>Archive</h1>
      <label>Round <select id="arRound">${list.map(r =>
        `<option value="${escapeHtml(r.name)}"${r.name === CHOSEN ? ' selected' : ''}>${escapeHtml(r.title)} — ${r.incidents} incident${r.incidents === 1 ? '' : 's'}</option>`).join('')}
      </select></label>
      <p>Read-only. These incidents were set aside when the live coding was reset; each shows
        what every coder coded.</p>
    </div>
    <div id="arBody"><div class="ar-empty">Loading…</div></div>`;
  root.querySelector('#arRound').onchange = (e) => { CHOSEN = e.target.value; showRound(); };
  await showRound();
}

async function showRound() {
  const body = document.getElementById('arBody');
  let d;
  try { d = await (await fetch('/api/archives/' + encodeURIComponent(CHOSEN))).json(); }
  catch (e) { body.innerHTML = '<div class="ar-empty">Could not load this round.</div>'; return; }
  body.innerHTML = '';
  ROUND = d.name;
  d.incidents.forEach(inc => body.appendChild(incidentBlock(inc, d.coders)));
}

const chip = (role, v) => `<span class="ar-chip" style="border-color:${(ROLE[role] || {}).color || '#d4d4d8'};` +
  `background:${((ROLE[role] || {}).color || '#d4d4d8')}33">${escapeHtml(v)}</span>`;
const chips = (role, vals) => (vals || []).map(v => chip(role, v)).join(' ');

// One claim, read as the sentence it was built as.
function claimLine(cl) {
  const list = (plural, single) => (cl[plural] && cl[plural].length) ? cl[plural] : (cl[single] ? [cl[single]] : []);
  const part = (role, vals, text) => vals.length ? `${text}${chips(role, vals)}` : '';
  return [
    chips('harm', list('harms', 'harm')) || '<i>[no harm]</i>',
    ' allegedly impacted ',
    chips('harmed_party', list('harmed_parties', 'harmed_party')) || '<i>[no harmed party]</i>',
    ' in part as a result of actions taken by ',
    cl.actor ? chip('actor', cl.actor) : '<i>[no actor]</i>',
    ' using ',
    chips('factor', cl.factors) || '<i>[no factor]</i>',
    part('developer', list('developers', 'developer'), ' developed by '),
    part('system', list('systems', 'system'), ' because of '),
    '.',
  ].join('');
}

// A highlight is either evidence for a code (role + value) or, in the document view,
// the stretch that describes the incident's aftermath (category, no role).
const quoteLabel = (q) => q.role ? (q.value || q.role)
  : (q.category || 'highlight').replace(/^incident_/, '');

function statusText(c) {
  const s = c.status === 'complete' ? 'signed off as complete'
    : c.status === 'not_an_incident' ? 'set aside as not an incident' : 'in progress';
  return c.completed_at ? `${s} · ${c.completed_at.slice(0, 10)}` : s;
}

function coderColumn(name, c) {
  const fields = Object.entries(c.fields || {}).map(([k, f]) => {
    const a = Array.isArray(f.answer) ? f.answer.join(', ') : (f.answer || '');
    return `<div><span class="ar-k">${escapeHtml(k.replace(/^incident_/, ''))}</span> ${escapeHtml(a)}
      ${f.comments ? `<em>${escapeHtml(f.comments)}</em>` : ''}</div>`;
  }).join('');
  return `<div class="ar-coder">
    <div class="ar-coder-h"><b>${escapeHtml(name)}</b><span>${escapeHtml(statusText(c))}</span></div>
    ${(c.claims || []).length ? `<div class="ar-sec">Claims</div>${c.claims.map(cl =>
      `<div class="ar-claim">${claimLine(cl)}</div>`).join('')}` : ''}
    ${fields ? `<div class="ar-sec">Answers</div>${fields}` : ''}
    ${c.comment ? `<div class="ar-sec">Comment</div><div class="ar-comment">${escapeHtml(c.comment)}</div>` : ''}
  </div>`;
}

function incidentBlock(inc, coders) {
  const det = document.createElement('details');
  det.className = 'ar-inc';
  const who = Object.keys(inc.coders);
  det.innerHTML = `
    <summary><b>${escapeHtml(inc.incident_id)}</b>
      <span class="ar-inc-title">${escapeHtml(inc.title || '')}</span>
      <span class="ar-by">${who.map(w => `${escapeHtml(w)}${inc.coders[w].status === 'complete' ? ' ✓' : ''}`).join(' · ')}</span>
    </summary>
    <div class="ar-docs"></div>
    <div class="ar-cols">${who.map(w => coderColumn(w, inc.coders[w])).join('')}</div>`;
  const docs = det.querySelector('.ar-docs');
  inc.documents.forEach(d => docs.appendChild(documentBlock(inc, d)));
  return det;
}

// One of the incident's documents, opened to read its text with a coder's
// highlights on it. Fetched on first open — the texts are large.
function documentBlock(inc, d) {
  const el = document.createElement('details');
  el.className = 'ar-docblock';
  const coders = Object.keys(inc.coders).filter(c =>
    (inc.coders[c].evidence || []).some(e => e.doc === d.key && e.quotes.length));
  el.innerHTML = `<summary><span class="ar-date">${escapeHtml(d.date || 'no date')}</span>${escapeHtml(d.title)}
    <span class="ar-by">${coders.map(c => `${escapeHtml(c)} ${inc.coders[c].evidence
      .filter(e => e.doc === d.key).reduce((n, e) => n + e.quotes.length, 0)} highlights`).join(' · ')}</span></summary>
    <div class="ar-read"></div>`;
  let loaded = false;
  el.addEventListener('toggle', async () => {
    if (!el.open || loaded) return;
    loaded = true;
    const box = el.querySelector('.ar-read');
    box.textContent = 'Loading…';
    let doc;
    try {
      doc = await (await fetch(`/api/archives/${encodeURIComponent(ROUND)}/doc/${encodeURIComponent(d.key)}`)).json();
    } catch (e) { box.textContent = 'Could not load this document.'; return; }
    const quotesOf = (c) => ((inc.coders[c] || {}).evidence || [])
      .filter(e => e.doc === d.key).flatMap(e => e.quotes);
    box.innerHTML = `
      ${doc.url ? `<a class="docurl" href="${escapeHtml(doc.url)}" target="_blank" rel="noopener">${escapeHtml(doc.url)}</a>` : ''}
      ${coders.length ? `<div class="ar-pick">Highlights by ${coders.map((c, i) =>
        `<label><input type="radio" name="hl-${escapeHtml(inc.incident_id + d.key)}" value="${escapeHtml(c)}"${i ? '' : ' checked'}> ${escapeHtml(c)}</label>`).join(' ')}</div>` : '<div class="ar-pick">No highlights on this document.</div>'}
      <div class="ar-text"></div>`;
    const text = box.querySelector('.ar-text');
    const paint = (coder) => {
      text.innerHTML = window.marked ? marked.parse(doc.markdown) : escapeHtml(doc.markdown);
      if (coder) highlight(text, quotesOf(coder));
    };
    paint(coders[0] || '');
    box.querySelectorAll('input[type=radio]').forEach(r => { r.onchange = () => paint(r.value); });
  });
  return el;
}

// Hover a highlight to see what it was tagged with: the category (Harm, Factor,
// Actor…) and the value picked. A highlight tagged more than once lists each.
let TIP = null;
document.addEventListener('mouseover', (e) => {
  const m = e.target.closest && e.target.closest('.ar-text mark[data-tip]');
  if (!m) { if (TIP) { TIP.remove(); TIP = null; } return; }
  if (TIP && TIP._m === m) return;
  if (TIP) TIP.remove();
  let tags; try { tags = JSON.parse(m.dataset.tip); } catch (_) { return; }
  TIP = document.createElement('div');
  TIP.className = 'ar-tip';
  TIP._m = m;
  TIP.innerHTML = tags.map(([cat, val, role]) => `<div><span class="ar-tip-cat"
    style="background:${((ROLE[role] || {}).color || '#e5e7eb')}">${escapeHtml(cat)}</span>${val ? ' ' + escapeHtml(val) : ''}</div>`).join('');
  document.body.appendChild(TIP);
  const r = m.getBoundingClientRect();
  TIP.style.top = (window.scrollY + r.top - TIP.offsetHeight - 6) + 'px';
  TIP.style.left = Math.max(8, Math.min(window.scrollX + r.left, window.innerWidth - TIP.offsetWidth - 12)) + 'px';
});

// Paint highlights over rendered text. Offsets count characters of the rendered
// text, as the reader recorded them; overlapping highlights are split at every
// edge so each stretch shows all it was tagged with.
function highlight(root, quotes) {
  const nodes = [];
  const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let pos = 0, n;
  while ((n = w.nextNode())) { nodes.push({ node: n, start: pos }); pos += n.nodeValue.length; }
  const cuts = [...new Set(quotes.flatMap(q => [q.start, q.end]))].sort((a, b) => a - b);
  for (const { node, start } of nodes.reverse()) {
    const end = start + node.nodeValue.length;
    const edges = [start, ...cuts.filter(c => c > start && c < end), end];
    const frag = document.createDocumentFragment();
    for (let i = 0; i + 1 < edges.length; i++) {
      const a = edges[i], b = edges[i + 1];
      const piece = node.nodeValue.slice(a - start, b - start);
      const hit = quotes.filter(q => q.start <= a && q.end >= b);
      if (!hit.length) { frag.appendChild(document.createTextNode(piece)); continue; }
      const m = document.createElement('mark');
      const role = hit[0].role || 'aftermath';
      m.style.background = ((ROLE[role] || {}).color || '#e5e7eb') + '88';
      m.dataset.tip = JSON.stringify(hit.map(q => [(ROLE[q.role] || {}).label || 'Aftermath',
        q.role ? quoteLabel(q) : '', q.role || 'aftermath']));
      m.textContent = piece;
      frag.appendChild(m);
    }
    node.replaceWith(frag);
  }
}
