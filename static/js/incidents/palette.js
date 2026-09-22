// The palette of pooled characteristics, and the evidence behind each one.
//
// Every code any member document carries, as a chip you can drag into a claim,
// with the passages that justify it one click away.

import { CLAIM_LIST_KEYS, GEO, ROLES } from '../state.js';
import { roleColor, roleLabel } from './card.js';
import { saveCardField } from './fields.js';
import { NODATA } from './index.js';

// Where a characteristic has been used. A value can appear in any number of
// claims — dragging copies rather than moves — so this returns every claim id
// that holds it, and the palette shows them on the chip.
export function usedInClaims(inc, role, value) {
  const marks = [];
  (inc.claims || []).forEach(cl => {
    const key = CLAIM_LIST_KEYS[role];
    const hit = key ? (cl[key] || []).includes(value) : cl[role] === value;
    if (hit) marks.push(String(cl.id));
  });
  return marks;
}

// Re-render everything draggable on an incident's card — the characteristics
// palette and the System/Developer field chips — so their claim marks match the
// current claims. Called after every claim change; these live in a sibling
// column, so they're found by incident id rather than passed around.
export function refreshDraggables(inc) {
  document.querySelectorAll('.tow-palette').forEach(el => {
    if (el.dataset.inc === inc.incident_id) buildPalette(el, inc);
  });
}

// The pooled characteristics palette (left column) — draggable chips grouped by
// role, each marked with the claims it's already used in.
export function buildPalette(container, inc) {
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
  palette.appendChild(geoPaletteRow(inc));
  container.appendChild(palette);
}

// The geography vocabulary, as chips to drag onto an already-placed actor,
// system, developer or harmed-party chip — not a role, so it has no evidence,
// no claim badges and no pooling from documents: just the controlled list
// (shared with the incident-level Geography/location field) plus a way to grow
// it, matching the "add your own" a menu offers.
// Only ever the places this incident's own Geography/location already names —
// not the global vocabulary every incident could ever draw from. Choosing a
// country for the incident is what makes it a candidate for Based in; nothing
// else offers a place a coder hasn't already said applies here.
function geoPaletteRow(inc) {
  const row = document.createElement('div'); row.className = 'pal-row';
  const lbl = document.createElement('span');
  lbl.className = 'pal-role'; lbl.style.color = GEO.color; lbl.textContent = GEO.label;
  const chips = document.createElement('div'); chips.className = 'pal-chips';
  const places = (inc.field_values || {}).incident_geography || [];
  places.forEach(v => chips.appendChild(makeGeoChip(v, inc)));
  row.appendChild(lbl); row.appendChild(chips);
  return row;
}

// A draggable location chip. Unlike makeChip this carries no claim badges —
// the same location can sit on any number of values at once and nothing here
// tracks which. Its × doesn't delete a vocabulary entry (there's no "the
// Codebook's Israel" versus "this incident's Israel" — it's one shared list);
// it un-names this place for *this* incident, through the same save the
// card's own Geography/location control uses, so the two stay one control
// with two ways in. That also drops it from Based in, and prunes any role_geo
// tag naming it (see incidents._prune_role_geo) — a place a coder just said
// doesn't apply here can't still be marking where something is.
export function makeGeoChip(value, inc) {
  const chip = document.createElement('span');
  chip.className = 'drag-chip geo-chip';
  chip.style.background = GEO.color + '44';
  chip.style.borderColor = GEO.color;
  chip.draggable = true;
  chip.title = 'Drag onto an actor, system, developer or harmed party to say where it is based';
  chip.appendChild(document.createTextNode(value));
  const del = document.createElement('button');
  del.className = 'chip-x'; del.textContent = '×';
  del.title = `"${value}" doesn't apply to this incident`;
  del.onclick = async (e) => {
    e.stopPropagation();
    const places = (inc.field_values || {}).incident_geography || [];
    await saveCardField(inc, 'incident_geography', places.filter((p) => p !== value), null);
  };
  chip.appendChild(del);
  chip.ondragstart = (e) => {
    e.dataTransfer.setData('text/plain', JSON.stringify({ role: GEO.role, value }));
    e.dataTransfer.effectAllowed = 'copy';
  };
  return chip;
}

// A draggable palette chip (characteristics palette only). `claims` is the list
// of claim ids this value is already in — a used chip stays fully draggable,
// since the same characteristic is expected to appear in several claims; it just
// carries the claim numbers so you can see what's still unplaced.
export function makeChip(role, value, claims, inc, kind) {
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

// ---------- evidence behind a characteristic ----------
// Which quotes justify one pooled value. `kind` is the tag the quote carries —
// the characteristic's role ('harm').
export function evidenceFor(inc, kind, value) {
  return ((inc.value_quotes || {})[kind] || {})[value] || [];
}

export function quotesKey(kind, value) { return kind + ' ' + value; }

// Which evidence panels are open, keyed by incident id. Held here rather than on
// the incident object because a refreshed card is handed a *new* object from the
// server — state hanging off the old one would shut every panel on the card you
// had just been reading. Survives both the palette rebuild after a claim change
// and a card re-render after a save.
export const OPEN_QUOTES = {};

export function openSet(inc) {
  return OPEN_QUOTES[inc.incident_id] || (OPEN_QUOTES[inc.incident_id] = new Set());
}

export function isQuotesOpen(inc, kind, value) {
  return openSet(inc).has(quotesKey(kind, value));
}

export function toggleQuotes(inc, kind, value) {
  const open = openSet(inc);
  const k = quotesKey(kind, value);
  if (!open.delete(k)) open.add(k);
}

// The panel itself: every passage this coder highlighted for the value, with the
// document it came from (incidents can pool several).
export function quotePanel(inc, kind, value) {
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
