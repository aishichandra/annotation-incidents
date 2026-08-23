// Shared state and vocabulary.
// SCHEMA, curDoc, the ROLES table and its colors, role-option lookups,
// grouped-option rendering, and the 'armed' highlight target.
//
// Loaded as a classic script: everything here shares one global scope with the
// other static/js files. See templates/index.html for the load order.

const COLORS = ['#fde68a','#a5d6b0','#bfdbfe','#f3b7ac','#ddd6fe','#f9c9e0','#a7f3d0',
                '#fed7aa','#c7d2fe','#fecdd3','#bbf7d0','#e9d5ff'];
let SCHEMA = [];           // [{key,label,type,options}]
const color = {};          // field key -> highlight color
let curDoc = null;         // {index,title,url,markdown,ann:{fields,quotes,claims},_plain}
let saveTimer = null;

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
const ROLES = [
  { role: 'system',       label: 'System',       color: '#c4b5fd' },  // violet
  { role: 'developer',    label: 'Developer',    color: '#fdba74' },  // orange
  { role: 'actor',        label: 'Actor',        color: '#fde047' },  // yellow
  { role: 'factor',       label: 'Factor',       color: '#86efac' },  // green
  { role: 'harm',         label: 'Harm',         color: '#fca5a5' },  // red
  { role: 'harmed_party', label: 'Harmed party', color: '#7dd3fc' },  // blue
];
const ROLE = Object.fromEntries(ROLES.map(r => [r.role, r]));

// Every characteristic is droppable into a claim, so this is just ROLE.
const CLAIM_ROLE = ROLE;
// The two clauses a claim reads as complete without.
const OPTIONAL_CLAIM_ROLES = [
  { role: 'system',    lead: ' using ',        placeholder: 'system' },
  { role: 'developer', lead: ' developed by ', placeholder: 'developer' },
];
// Which slot a dragged value belongs to. The actor context is shared by every
// claim in a group, so it lives on the group header; the rest describe a single
// claim and are dropped onto the claim row itself.
const GROUP_ROLES = ['actor', 'system', 'developer'];
const CLAIM_ROLES_DROP = ['harm', 'harmed_party', 'factor'];
// Roles a claim holds as a list, and the key each is stored under. Anything not
// listed here is a single value that a drop replaces. `harm` is deliberately
// absent: one harm per claim is what keeps a claim one countable proposition.
const CLAIM_LIST_KEYS = { harmed_party: 'harmed_parties', factor: 'factors' };
let SCHEMA_ROLES = [];      // [{role,label,options,groups?}] from schema.claim_roles
// The coding rules, served by /api/schema from config.py so they are defined in
// one place rather than restated here. `required_roles` is what a completion
// sign-off demands; edit REQUIRED_CLAIM_ROLES in config.py and this follows.
// The fallback only matters if the schema fetch failed.
let RULES = { required_roles: ['actor', 'factor', 'harm', 'harmed_party'],
              optional_roles: ['system', 'developer'] };
function roleOptions(role) {
  const r = SCHEMA_ROLES.find(x => x.role === role);
  return (r && r.options) || [];
}
function setRoleOptions(role, opts) {
  const r = SCHEMA_ROLES.find(x => x.role === role);
  if (r) r.options = opts;
}
// Optional presentation grouping from vocab.json ("<list>_groups"), e.g. harm and
// factor. [{label, options}] or undefined.
function roleGroups(role) {
  const r = SCHEMA_ROLES.find(x => x.role === role);
  return (r && r.groups) || null;
}
// The codebook, from vocab.json ("<list>_definitions"): {option: text} for the
// options that have been defined. Undefined options are simply absent.
function roleDefinitions(role) {
  const r = SCHEMA_ROLES.find(x => x.role === role);
  return (r && r.definitions) || null;
}

// Arrange a flat option list into labelled sections for a menu. Sections follow
// the vocab's group order; anything ungrouped (including options a coder added
// themselves) falls into a trailing "Other" so nothing can be hidden by a group
// that forgot it. With no groups defined it's one unlabelled section, i.e. the
// plain flat list this app had before.
function groupedOptions(options, groups) {
  if (!groups || !groups.length) return [{ label: '', options }];
  const placed = new Set(), out = [];
  groups.forEach(g => {
    const opts = (g.options || []).filter(o => options.includes(o));
    opts.forEach(o => placed.add(o));
    if (opts.length) out.push({ label: g.label, options: opts });
  });
  const rest = options.filter(o => !placed.has(o));
  if (rest.length) out.push({ label: 'Other', options: rest });
  return out;
}

// A collapsible group heading, shared by the multiselect and the highlight value
// picker. `expanded` is the caller's Set of open labels; clicking toggles this
// group and asks the caller to rebuild. `nSel` (optional) is how many of the
// group's options are already chosen — shown as a badge, since a collapsed group
// would otherwise hide that its contents are in use.
function groupHeader(section, expanded, rebuild, nSel) {
  const open = expanded.has(section.label);
  const head = document.createElement('button');
  head.type = 'button';
  head.className = 'menu-group' + (open ? ' open' : '');
  head.dataset.group = section.label;
  head.innerHTML =
    `<span class="mg-caret">${open ? '▾' : '▸'}</span>` +
    `<span class="mg-name">${escapeHtml(section.label)}</span>` +
    (nSel ? `<span class="mg-sel">${nSel}</span>` : '') +
    `<span class="mg-n">${section.options.length}</span>`;
  head.onclick = (e) => {
    e.preventDefault(); e.stopPropagation();   // don't close the menu we're in
    if (open) expanded.delete(section.label); else expanded.add(section.label);
    rebuild();
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
let defTipEl = null;
let defTipTimer = null;

function hideDefTip() {
  clearTimeout(defTipTimer);
  if (defTipEl) { defTipEl.remove(); defTipEl = null; }
}

function showDefTip(anchor, name, text, accent) {
  hideDefTip();
  const tip = document.createElement('div');
  tip.className = 'deftip';
  // The name is repeated inside the tip because a long option wraps in the menu
  // and the tip may sit over it — you should always be able to see which code
  // the definition you're reading belongs to.
  tip.innerHTML = `<div class="deftip-name">${escapeHtml(name)}</div>`
                + `<div class="deftip-body">${escapeHtml(text)}</div>`;
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
function attachDefTip(el, name, text, accent) {
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
let armed = null;
// A just-completed text selection must not also trigger a highlighted mark's
// span menu (which would replace the tag menu). Set on selection, cleared next tick.
let skipSpanClick = false;
function sameArm(t) {
  if (!armed || armed.type !== t.type) return false;
  if (t.type === 'field') return armed.key === t.key && armed.value === t.value;
  return armed.role === t.role && armed.value === t.value;
}
function setArm(t) { armed = sameArm(t) ? null : t; afterArm(); }

