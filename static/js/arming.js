// Armed-target styling and the roles panel.
// Which field is armed to receive the next highlight, the hint text,
// scroll-to helpers, and the flat actor/harm/factor role cards.

import { buildSelect, buildText, buildValueEvidence, subLabel } from './form.js';
import { escapeHtml, persistSoon } from './persist.js';
import { field, removeQuote, renderArticle } from './reader.js';
import {
  GEO,
  ROLE,
  ROLES,
  armed,
  color,
  curDoc,
  roleDefinitions,
  roleEntry,
  roleGroups,
  roleOptions,
  sameArm,
  setArm,
  setRoleOptions,
} from './state.js';

export function armField(key, value) { setArm({ type: 'field', key, value }); }
export function afterArm() { updateArmHint(); refreshArmedStyles(); }

// Toggle armed styling on text-field cards and per-selection evidence rows.
export function refreshArmedStyles() {
  document.querySelectorAll('.card').forEach(c => {
    const arm = c.querySelector(':scope > .head > .arm');
    if (!arm) return;   // multi fields justify per selection, not per field
    const on = sameArm({ type: 'field', key: c.dataset.key, value: undefined });
    c.classList.toggle('armed', on);
    arm.textContent = on ? 'highlighting' : 'highlight';
  });
  document.querySelectorAll('.ev-row[data-arm]').forEach(row => {
    const on = sameArm(JSON.parse(row.dataset.arm));
    row.classList.toggle('armed', on);
    const b = row.querySelector('.ev-arm');
    if (b) b.textContent = on ? 'highlighting' : 'highlight';
  });
}

export function updateArmHint() {
  const h = document.getElementById('armHint');
  if (!armed) { h.textContent = ''; return; }
  const val = armed.value ? ' · ' + armed.value : '';
  h.textContent = armed.type === 'field'
    ? `Highlighting → ${(field(armed.key) || {}).label}${val}`
    : `Highlighting → ${(ROLE[armed.role] || {}).label}${val}`;
}
export function flashHint() {
  const h = document.getElementById('armHint');
  h.style.color = '#dc2626'; h.textContent = 'Arm a field or a characteristic first ↗';
  setTimeout(() => { h.style.color = ''; updateArmHint(); }, 1500);
}

export function scrollToCard(key) {
  const c = document.querySelector(`.card[data-key="${key}"]`);
  if (c) c.scrollIntoView({ behavior: 'smooth', block: 'center' });
}
export function scrollToMark(gi) {
  const m = document.querySelector(`mark[data-idxs~="${gi}"]`);
  if (m) {
    m.scrollIntoView({ behavior: 'smooth', block: 'center' });
    m.classList.add('active'); setTimeout(() => m.classList.remove('active'), 1200);
  }
}

// ---------- characteristics: flat actor / harm / factor / harmed party ----------
// No linking here — each role is just a multiselect of values, each value
// justified by highlights. Grouping into claims happens in the card view.
export function renderRoles() {
  const old = document.querySelector('.roles-section');
  if (old) old.replaceWith(buildRolesPanel());
}

export function buildRolesPanel() {
  const section = document.createElement('div');
  section.className = 'roles-section';
  const head = document.createElement('div');
  head.className = 'claims-head';
  // head.innerHTML = `<span class="label">Characteristics</span>`;
  section.appendChild(head);
  ROLES.forEach(r => section.appendChild(buildRoleCard(r)));
  section.appendChild(buildGeoCard());
  return section;
}

// ---------- geography: the places highlighted in this document ----------
// Not a multiselect like the characteristics above: a place is picked from the
// highlight menu, so this card only lists what has been said — each place and
// the passages behind it — and takes it back one passage at a time.
export function buildGeoCard() {
  const card = document.createElement('div');
  card.className = 'card'; card.dataset.role = GEO.role;

  const head = document.createElement('div');
  head.className = 'head'; head.style.cursor = 'default';
  head.innerHTML =
    `<span class="sq" style="background:${GEO.color}"></span>` +
    `<span class="label">${GEO.docLabel}</span>`;
  card.appendChild(head);

  const body = document.createElement('div');
  body.className = 'body';
  const mine = curDoc.ann.quotes.map((q, gi) => ({ q, gi })).filter(x => x.q.role === GEO.role);
  const wrap = document.createElement('div');
  wrap.className = 'value-ev';
  if (!mine.length) {
    const none = document.createElement('div');
    none.className = 'ev-none';
    none.textContent = 'Highlight a country in the article and choose Geography.';
    wrap.appendChild(none);
  }
  // One row per place; the same country highlighted twice is one statement
  // with two passages behind it.
  const groups = new Map();
  mine.forEach(x => {
    const k = x.q.value;
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(x);
  });
  groups.forEach(items => {
    const first = items[0].q;
    const row = document.createElement('div');
    row.className = 'ev-row';
    const rowHead = document.createElement('div');
    rowHead.className = 'ev-head';
    rowHead.innerHTML =
      `<span class="ev-dot" style="background:${GEO.color}"></span>` +
      `<span class="ev-val">${escapeHtml(first.value)}</span>` +
      `<span class="ev-count">${items.length} quote${items.length === 1 ? '' : 's'}</span>`;
    row.appendChild(rowHead);
    const quotes = document.createElement('div');
    quotes.className = 'quotes';
    items.forEach(({ q, gi }) => {
      const el = document.createElement('div');
      el.className = 'quote';
      el.style.borderLeftColor = GEO.color;
      el.innerHTML = `“${escapeHtml(q.text.slice(0, 180))}”<button class="x" title="Remove">×</button>`;
      el.onclick = (e) => { if (e.target.classList.contains('x')) return; scrollToMark(gi); };
      el.querySelector('.x').onclick = (e) => { e.stopPropagation(); removeQuote(gi); };
      quotes.appendChild(el);
    });
    row.appendChild(quotes);
    wrap.appendChild(row);
  });
  body.appendChild(wrap);
  card.appendChild(body);
  return card;
}

export function buildRoleCard(r) {
  if (!Array.isArray(curDoc.ann.roles[r.role])) curDoc.ann.roles[r.role] = [];
  const arr = curDoc.ann.roles[r.role];

  const card = document.createElement('div');
  card.className = 'card'; card.dataset.role = r.role;

  const head = document.createElement('div');
  head.className = 'head'; head.style.cursor = 'default';
  head.innerHTML =
    `<span class="sq" style="background:${r.color}"></span>` +
    `<span class="label">${r.label}</span>`;
  card.appendChild(head);

  const body = document.createElement('div');
  body.className = 'body';

  const evBox = document.createElement('div');
  const refreshEv = () => {
    evBox.innerHTML = '';
    evBox.appendChild(buildValueEvidence(arr, {
      color: r.color,
      armTarget: (v) => ({ type: 'role', role: r.role, value: v }),
      getQuotes: (v) => curDoc.ann.quotes.map((q, gi) => ({ q, gi }))
        .filter(x => x.q.role === r.role && x.q.value === v),
    }));
  };
  const select = buildSelect({
    options: roleOptions(r.role),
    groups: roleGroups(r.role),
    definitions: roleDefinitions(r.role),
    accent: r.color,
    selected: arr,
    onChange: () => { persistSoon(); refreshEv(); },
    onRemoveValue: (v) => {
      curDoc.ann.quotes = curDoc.ann.quotes.filter(q => !(q.role === r.role && q.value === v));
      renderArticle(true);
    },
    onAdd: async (val) => {
      const res = await fetch('/api/schema/role_option', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ role: r.role, option: val }),
      });
      const updated = (await res.json()).options;
      setRoleOptions(r.role, updated);
      return updated;
    },
  });
  body.appendChild(select);

  // A role may carry one piece of free text — the inciting actor's name. It sits
  // with its characteristic rather than in a field of its own, because it says
  // *which* actor, and is meaningless apart from the actor codes above it.
  const noteLabel = (roleEntry(r.role) || {}).note_label;
  // Set the same way Incident title is: type it, press Enter, and it settles
  // into plain text you click to edit again — so a typed name reads as *entered*
  // rather than as something still sitting in a box.
  if (noteLabel) {
    curDoc.ann.notes = curDoc.ann.notes || {};
    body.appendChild(subLabel(noteLabel));
    body.appendChild(buildText(curDoc.ann.notes, r.role, false,
                               'Organisation name(s) — not individuals'));
  }

  body.appendChild(subLabel('Justification (highlight each selection)'));
  body.appendChild(evBox);
  refreshEv();

  card.appendChild(body);
  return card;
}
