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

