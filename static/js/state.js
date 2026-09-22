// Shared state and vocabulary.
// SCHEMA, curDoc, the ROLES table and its colors, role-option lookups,
// grouped-option rendering, and the 'armed' highlight target.

import { afterArm } from './arming.js';
import { escapeHtml } from './persist.js';
import { field } from './reader.js';

export const COLORS = ['#fde68a','#a5d6b0','#bfdbfe','#f3b7ac','#ddd6fe','#f9c9e0','#a7f3d0',
                '#fed7aa','#c7d2fe','#fecdd3','#bbf7d0','#e9d5ff'];
export let SCHEMA = [];           // [{key,label,type,options}]
export const color = {};          // field key -> highlight color
export let curDoc = null;         // {index,title,url,markdown,ann:{fields,quotes,claims},_plain}
export let saveTimer = null;

// Every controlled-vocabulary characteristic, in the coding scheme's order.
// System and developer are ordinary roles: coded the same way, tagged the same
// way on a quote, dragged into a claim the same way. Their colours are the ones
// they carried when they were separate "fields", so a chip that was violet
// yesterday is violet today.
// A claim links these roles; each highlight is colored by ROLE and carries the
// claim's number so evidence for the same claim reads as connected. Maximally
// distinct hues, so two role highlights never read as the same color. The order
// here is the order they appear in the document sidebar, the highlight tag menu
// (TAG_ORDER) and the incident palette — factor before harm in all three.
export const ROLES = [
  { role: 'system',       label: 'System',       color: '#c4b5fd' },  // violet
  { role: 'developer',    label: 'Developer',    color: '#fdba74' },  // orange
  { role: 'actor',        label: 'Actor',        color: '#fde047' },  // yellow
  { role: 'factor',       label: 'Factor',       color: '#86efac' },  // green
  { role: 'harm',         label: 'Harm',         color: '#fca5a5' },  // red
  { role: 'harmed_party', label: 'Harmed party', color: '#7dd3fc' },  // blue
];
export const ROLE = Object.fromEntries(ROLES.map(r => [r.role, r]));

// Where an actor, system, developer or harmed party is — a place, dropped
// onto that value's own chip rather than assumed to be the same for every
// value in it. Labelled "Based in" rather than "Location" so it doesn't read
// as another way to answer the incident-level Geography/location field — the
// two ask different questions (where the *incident* is about, versus where
// one of its characteristics is) and happen to share a vocabulary, not a
// meaning. Deliberately outside ROLES/CLAIM_ROLE: it is never dragged into a
// claim's own drop zone, and its "characteristics" come from the geography
// vocabulary, not from role_values, so it has no business in anything driven by
// that list.
//
// In a document the same thing is called `docLabel` — there it is a tag on a
// highlighted country, one of the choices beside Actor and Harm, and the coder
// is picking a place, not asking where something is based. That highlight is a
// quote like any other, tagged `role: GEO.role`, `value` the place — it says
// where the *incident* is, on the reading that a document naming a place
// asserts nothing about which characteristic it's about. Which characteristic
// a place is about, if any, is said on the incident card instead (drag it onto
// a chip there — see role_geo in claims.js and storage.sync_doc_geo). It is
// still no characteristic itself — it never reaches a document's `roles`.
export const GEO = { role: 'geography', label: 'Based in', docLabel: 'Geography',
                     color: '#d1d5db' };  // slate

// Which characteristics can take a location — mirrors config.GEO_ROLES. Harm
// and factor are absent: neither is a thing that is anywhere.
export const GEO_ROLES = new Set(['actor', 'system', 'developer', 'harmed_party']);

// The role_geo key a place for a whole role sits under — every actor, say,
// rather than one value in it. Mirrors config.ROLE_GEO_ALL.
export const ROLE_GEO_ALL = '*';

// Every characteristic is droppable into a claim, so this is just ROLE.
// Geography and Translated are not here because they are not characteristics:
// they describe the incident and are answered once on its card (card_only
// fields in the schema), never highlighted and never dragged.
export const CLAIM_ROLE = ROLE;
// The two clauses a claim reads as complete without. Both are lists: one actor
// context can involve several systems, and a system can be built by more than
// one party, so "A & B" is an ordinary thing to need to say for either.
// The two clauses a claim reads as complete without.
export const OPTIONAL_CLAIM_ROLES = [
  { role: 'developer', key: 'developers', lead: ' developed by ', placeholder: 'developer' },
  { role: 'system',    key: 'systems',    lead: ' because of ',   placeholder: 'system' },
];
// Every claim is now flat and self-contained — no grouping tier, so every
// role is dropped straight onto the one claim it describes.
export const CLAIM_ROLES_DROP = ['harm', 'harmed_party', 'actor', 'factor', 'system', 'developer'];
// Roles a claim holds as a list, and the key each is stored under. Anything not
// listed here is a single value that a drop replaces. `harm` and `actor` are
// deliberately absent: harm is what makes a claim one assertion and actor is
// who makes it, so a second one of either is a second claim, not a second chip.
export const CLAIM_LIST_KEYS = { harmed_party: 'harmed_parties', factor: 'factors',
                                 system: 'systems', developer: 'developers' };

// A claim's values for one role, whichever shape they are stored in — harmed
// parties, systems and developers all went plural after claims had already
// been saved holding one value each, so the pre-plural singular is still
// read and folded in. Writers put the list first and blank the singular, so
// nothing is counted twice.
export function claimValues(cl, role) {
  const key = CLAIM_LIST_KEYS[role];
  if (!key) return cl[role] ? [cl[role]] : [];
  const vals = Array.isArray(cl[key]) ? cl[key].slice() : [];
  if (cl[role] && !vals.includes(cl[role])) vals.push(cl[role]);
  return vals;
}
export let SCHEMA_ROLES = [];      // [{role,label,options,groups?}] from schema.claim_roles
// The coding rules, served by /api/schema from config.py so they are defined in
// one place rather than restated here. `required_roles` is what a completion
// sign-off demands; edit REQUIRED_CLAIM_ROLES in config.py and this follows.
// The fallback only matters if the schema fetch failed.
export let RULES = { required_roles: ['actor', 'factor', 'harm', 'harmed_party'],
              optional_roles: ['system', 'developer'] };

// The scheme, as /api/schema serves it. Everything above is read all over the
// app but written only here — a module's binding belongs to the module that
// declares it — so both places that fetch the schema (startup, and a codebook
// edit) hand it to this instead of assigning the three pieces themselves.
// Colours are assigned here too, so a field added while the app is running gets
// one without a reload.
export function applySchema(schema) {
  SCHEMA = schema.fields || [];
  SCHEMA_ROLES = schema.claim_roles || [];
  if (schema.rules) RULES = schema.rules;
  SCHEMA.forEach((f, i) => { color[f.key] = COLORS[i % COLORS.length]; });
}

// The rest of the writable state, each with the one setter its other modules
// need. Reads stay direct: an import is a live binding, so `curDoc` elsewhere is
// always this `curDoc`.
export function setCurDoc(doc) { curDoc = doc; }
export function setArmed(target) { armed = target; }
export function setSkipSpanClick(on) { skipSpanClick = on; }
export function setSaveTimer(t) { saveTimer = t; }
// One claim role's schema entry — its options, its grouping, its definitions and
// its note label are all fields of this, so the lookup happens here rather than
// once per question asked about a role.
export function roleEntry(role) { return SCHEMA_ROLES.find(x => x.role === role); }
export function roleOptions(role) { return (roleEntry(role) || {}).options || []; }
export function setRoleOptions(role, opts) { const r = roleEntry(role); if (r) r.options = opts; }
// Optional presentation grouping from vocab.json ("<list>_groups"), e.g. harm and
// factor. [{label, options}] or undefined.
export function roleGroups(role) { return (roleEntry(role) || {}).groups || null; }
// The codebook, from vocab.json ("<list>_definitions"): {option: text} for the
// options that have been defined. Undefined options are simply absent.
export function roleDefinitions(role) { return (roleEntry(role) || {}).definitions || null; }

// Everything a menu needs to know about the characteristic it is offering,
// whichever of the two kinds it is: a claim role or a document field. The two
// live in different halves of the schema, and the difference was being spelled
// out again at each question a menu asks — its name, its grouping, its
// definitions, its colour — which is four chances for the answers to disagree.
// Both entries carry `groups` and `definitions` under those names, so only the
// lookup and the colour actually differ.
export function targetVocab(target) {
  const isRole = target.type === 'role';
  const src = (isRole ? roleEntry(target.role) : field(target.key)) || {};
  return {
    label: (isRole ? (ROLE[target.role] || {}) : src).label || '',
    groups: src.groups || null,
    definitions: src.definitions || {},
    accent: isRole ? (ROLE[target.role] || {}).color : color[target.key],
  };
}

// Arrange a flat option list into labelled sections for a menu. Sections follow
// the vocab's group order; anything ungrouped (including options a coder added
// themselves) falls into a trailing "Other" so nothing can be hidden by a group
// that forgot it. With no groups defined it's one unlabelled section, i.e. the
// plain flat list this app had before.
//
// A group whose own label is itself one of the flat options (geography's
// continents, each heading its own country list) counts as placed too, even
// though it never sits in the group's `options` — it's picked from the heading
// itself (see groupHeader's `pick`), not listed a second time under "Other".
export function groupedOptions(options, groups) {
  if (!groups || !groups.length) return [{ label: '', options }];
  const placed = new Set(), out = [];
  groups.forEach(g => {
    const opts = (g.options || []).filter(o => options.includes(o));
    opts.forEach(o => placed.add(o));
    if (g.label && options.includes(g.label)) placed.add(g.label);
    if (opts.length) out.push({ label: g.label, options: opts });
  });
  const rest = options.filter(o => !placed.has(o));
  if (rest.length) out.push({ label: 'Other', options: rest });
  return out;
}

// A collapsible group heading, shared by the multiselect and the highlight value
// picker. `expanded` is the caller's Set of open labels; clicking it toggles this
// group and asks the caller to rebuild. `nSel` (optional) is how many of the
// group's options are already chosen — shown as a badge, since a collapsed group
// would otherwise hide that its contents are in use.
//
// `pick` (optional) is passed when the heading itself is also a selectable
// value — geography's continents, each heading the list of its own countries —
// so a coder can answer "Africa" broadly without opening the group, or open it
// and pick specific countries instead, or both. {checked, onToggle(checked)}.
// A plain `<div>` rather than `<button>` here, since a `<button>` can't
// validly contain the checkbox `pick` adds.
export function groupHeader(section, expanded, rebuild, nSel, pick) {
  const open = expanded.has(section.label);
  const head = document.createElement('div');
  head.className = 'menu-group' + (open ? ' open' : '');
  head.dataset.group = section.label;
  head.tabIndex = 0;
  head.setAttribute('role', 'button');

  if (pick) {
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.className = 'mg-check';
    cb.title = `${section.label}, without picking a specific one below`;
    cb.checked = pick.checked;
    cb.onclick = (e) => { e.stopPropagation(); pick.onToggle(cb.checked); };
    head.appendChild(cb);
  }

  const caret = document.createElement('span');
  caret.className = 'mg-caret'; caret.textContent = open ? '▾' : '▸';
  head.appendChild(caret);
  const name = document.createElement('span');
  name.className = 'mg-name'; name.textContent = section.label;
  head.appendChild(name);
  if (nSel) {
    const sel = document.createElement('span');
    sel.className = 'mg-sel'; sel.textContent = String(nSel);
    head.appendChild(sel);
  }
  const n = document.createElement('span');
  n.className = 'mg-n'; n.textContent = String(section.options.length);
  head.appendChild(n);

  const toggle = () => {
    if (open) expanded.delete(section.label); else expanded.add(section.label);
    rebuild();
  };
  head.onclick = (e) => {
    e.preventDefault(); e.stopPropagation();   // don't close the menu we're in
    toggle();
  };
  head.onkeydown = (e) => {
    if (e.target === head && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); toggle(); }
  };
  return head;
}

// ---------------------------------------------------------------- definitions
// A category's definition, shown on hover wherever that category can be chosen,
// so the rule a coder is applying is legible at the moment they apply it rather
// than in a codebook beside the app.
//
// One element on <body> rather than a tip inside each row: both menus scroll
// inside their own box, which would clip anything positioned within them.
export let defTipEl = null;
export let defTipTimer = null;

export function hideDefTip() {
  clearTimeout(defTipTimer);
  if (defTipEl) { defTipEl.remove(); defTipEl = null; }
}

// A definition is plain text — it is typed into a textarea in the Codebook tab —
// laid out here as what it was written as: a line break starts a new paragraph,
// and starred text is emphasis, set in italic. Definitions now run past one
// sentence (the rule for the edge case usually follows the definition itself),
// and a paragraph break is the whole of what they need.
//
// Both `**this**` and `*this*` count: the codebook was written in the first and
// nobody should have to go back through it to keep an italic. A single star has
// to hug the text it marks — otherwise "5 * 3" and a real rule further down the
// line would pair off and italicise everything between them.
export function defHtml(text) {
  return String(text || '').trim().split(/\n+/)
    .map(p => p.trim()).filter(Boolean)
    .map(p => `<p>${escapeHtml(p).replace(/\*\*([^*]+)\*\*|\*(\S|\S[^*\n]*?\S)\*/g,
                                        (m, a, b) => `<em>${a || b}</em>`)}</p>`)
    .join('');
}

export function showDefTip(anchor, name, text, accent) {
  hideDefTip();
  const tip = document.createElement('div');
  tip.className = 'deftip';
  // The name is repeated inside the tip because a long option wraps in the menu
  // and the tip may sit over it — you should always be able to see which code
  // the definition you're reading belongs to.
  tip.innerHTML = `<div class="deftip-name">${escapeHtml(name)}</div>`
                + `<div class="deftip-body">${defHtml(text)}</div>`;
  // Bordered in the characteristic's own colour, so a definition is tied to the
  // same hue as its chips and highlights rather than introducing one of its own.
  if (accent) tip.style.setProperty('--tip-accent', accent);
  document.body.appendChild(tip);
  defTipEl = tip;
  // Beside the row it explains, flipped to the other side when that would run
  // off screen, and always kept fully on screen vertically.
  const r = anchor.getBoundingClientRect();
  const tw = tip.offsetWidth, th = tip.offsetHeight;
  let left = r.right + 12;
  if (left + tw > window.innerWidth - 10) left = r.left - tw - 12;
  const top = Math.min(Math.max(10, r.top + r.height / 2 - th / 2),
                       window.innerHeight - th - 10);
  tip.style.left = Math.max(10, left) + 'px';
  tip.style.top = Math.max(10, top) + 'px';
}

// Give one option row its definition tooltip. `text` missing (an option nobody
// has defined yet) leaves the row exactly as it was — no marker, no tip.
// The delay keeps running the cursor down a long list from strobing.
export function attachDefTip(el, name, text, accent) {
  if (!text) return el;
  el.classList.add('has-def');
  el.addEventListener('mouseenter', () => {
    clearTimeout(defTipTimer);
    defTipTimer = setTimeout(() => showDefTip(el, name, text, accent), 220);
  });
  el.addEventListener('mouseleave', hideDefTip);
  return el;
}

// Scrolling the menu (or clicking anywhere) would strand a tip beside a row that
// has moved on. Capture phase, so a scroll inside a menu counts too.
document.addEventListener('scroll', hideDefTip, true);
document.addEventListener('mousedown', hideDefTip, true);

// What's currently receiving highlights: a specific selected value in a field or
// claim-role multiselect (value is undefined for a whole text field).
// null | {type:'field', key, value} | {type:'role', claim, role, value}
export let armed = null;
// A just-completed text selection must not also trigger a highlighted mark's
// span menu (which would replace the tag menu). Set on selection, cleared next tick.
export let skipSpanClick = false;
export function sameArm(t) {
  if (!armed || armed.type !== t.type) return false;
  if (t.type === 'field') return armed.key === t.key && armed.value === t.value;
  return armed.role === t.role && armed.value === t.value;
}
export function setArm(t) { armed = sameArm(t) ? null : t; afterArm(); }
