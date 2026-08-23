// Codebook tab: edit the coding scheme itself — what the codes are and what each
// one means — instead of hand-editing vocab.json beside a running app.
//
// The vocabulary is shared, not per coder: one scheme is the whole point of two
// people coding the same incidents, so every edit here lands in vocab.json and
// every coder sees it. That is also why renaming is careful. Coding on disk
// names its codes as strings, so a rename rewrites every quote and claim that
// names the old one (the server does both halves in one request), and deleting a
// code that is still in use is refused rather than quietly orphaning it.
//
// Loaded as a classic script: everything here shares one global scope with the
// other static/js files. See templates/index.html for the load order.

let CODEBOOK = null;

async function loadCodebook() {
  const root = document.getElementById('codebook');
  if (!CODEBOOK) root.innerHTML = '<div class="cb-empty">Loading the codebook…</div>';
  try {
    CODEBOOK = await (await fetch('/api/vocab')).json();
  } catch (e) {
    root.innerHTML = '<div class="cb-empty">Could not load the codebook.</div>';
    return;
  }
  renderCodebook();
}

function renderCodebook() {
  const root = document.getElementById('codebook');
  root.innerHTML = '';

  const head = document.createElement('div');
  head.className = 'cb-head';
  head.innerHTML = `<h1>AI Incidents Coding Scheme</h1>`;
  root.appendChild(head);

  (CODEBOOK.roles || []).forEach(r => root.appendChild(cbRoleSection(r)));
}

// One characteristic and its codes, in the role's own colour so a section is
// recognisable as the same thing it is in the sidebar and on the cards.
function cbRoleSection(r) {
  const sec = document.createElement('section');
  sec.className = 'cb-role';
  sec.style.setProperty('--cb-accent', (ROLE[r.role] || {}).color || '#d4d4d8');

  const head = document.createElement('div');
  head.className = 'cb-role-head';
  const defined = r.options.filter(o => o.definition).length;
  head.innerHTML =
    `<span class="cb-dot"></span>` +
    `<span class="cb-role-name">${escapeHtml(r.label)}</span>` +
    `<span class="cb-role-n">${r.options.length} codes</span>` +
    `<span class="cb-role-def${defined === r.options.length ? ' all' : ''}">` +
    `${defined}/${r.options.length} defined</span>`;
  sec.appendChild(head);

  // Grouped vocabularies (harm, factor) keep their headings here too, so the
  // codebook reads in the same order as the menus a coder actually sees.
  const sections = r.groups.length
    ? r.groups.map(g => ({ label: g, options: r.options.filter(o => o.group === g) }))
        .concat([{ label: 'Other', options: r.options.filter(o => !o.group) }])
        .filter(s => s.options.length)
    : [{ label: '', options: r.options }];

  sections.forEach(s => {
    if (s.label) {
      const h = document.createElement('div');
      h.className = 'cb-group';
      h.textContent = s.label;
      sec.appendChild(h);
    }
    s.options.forEach(o => sec.appendChild(cbOptionRow(r, o)));
  });

  // Anything the coding names that the vocabulary no longer offers. Normally
  // empty; it appears when a code was removed from vocab.json by hand while
  // quotes still pointed at it, and it is the one thing here you cannot fix by
  // editing text — the coding has to be renamed onto a code that exists.
  if (r.unknown && r.unknown.length) {
    const h = document.createElement('div');
    h.className = 'cb-group cb-group-warn';
    h.textContent = 'Used in coding but not in the scheme';
    sec.appendChild(h);
    r.unknown.forEach(u => {
      const row = document.createElement('div');
      row.className = 'cb-row cb-row-orphan';
      row.innerHTML = `<div class="cb-main"><div class="cb-name">${escapeHtml(u.name)}</div>` +
        `<div class="cb-def-view empty">Not offered by the scheme — add it below, ` +
        `or rename the code it should have been.</div></div>` +
        `<div class="cb-uses">${cbUsesText(u)}</div>`;
      sec.appendChild(row);
    });
  }

  sec.appendChild(cbAddRow(r));
  return sec;
}

function cbUsesText(o) {
  if (!o.total) return '<span class="cb-unused">unused</span>';
  const by = Object.entries(o.uses).map(([c, n]) => `${c}: ${n}`).join(', ');
  return `<span class="cb-used" title="${escapeHtml(by)}">${o.total} use${o.total === 1 ? '' : 's'}</span>`;
}

// The incidents behind a use count. A number alone doesn't tell you whether a
// code is being applied the way you meant it to be — the incidents do — so the
// count opens the list, and each incident there takes you to its card.
async function cbToggleUses(role, option, row, btn) {
  const open = row.nextElementSibling && row.nextElementSibling.classList.contains('cb-uses-panel');
  if (open) {
    row.nextElementSibling.remove();
    btn.classList.remove('open');
    return;
  }
  document.querySelectorAll('.cb-uses-panel').forEach(el => el.remove());
  document.querySelectorAll('.cb-uses-btn.open').forEach(el => el.classList.remove('open'));
  btn.classList.add('open');

  const panel = document.createElement('div');
  panel.className = 'cb-uses-panel';
  panel.innerHTML = '<div class="cb-uses-loading">Looking…</div>';
  row.after(panel);

  let d;
  try {
    d = await (await fetch('/api/vocab/uses?role=' + encodeURIComponent(role)
                           + '&option=' + encodeURIComponent(option))).json();
  } catch (e) {
    panel.innerHTML = '<div class="cb-uses-loading">Could not load that.</div>';
    return;
  }
  panel.innerHTML = '';
  const head = document.createElement('div');
  head.className = 'cb-uses-head';
  head.textContent = `“${option}” in ${d.incidents.length} incident`
                   + `${d.incidents.length === 1 ? '' : 's'} — ${d.total} use`
                   + `${d.total === 1 ? '' : 's'} in all`;
  panel.appendChild(head);

  d.incidents.forEach(inc => {
    const b = document.createElement('button');
    b.className = 'cb-uses-inc';
    const by = Object.entries(inc.uses).map(([c, n]) => `${c} ${n}`).join(' · ');
    b.innerHTML =
      `<span class="cb-uses-id">${escapeHtml(inc.incident_id || 'Unassigned')}</span>` +
      `<span class="cb-uses-title">${escapeHtml(inc.title || (inc.incident_id
        ? '' : 'documents not yet grouped into an incident'))}</span>` +
      `<span class="cb-uses-by">${escapeHtml(by)}</span>`;
    if (inc.incident_id) b.onclick = () => cbGoToIncident(inc.incident_id);
    else b.classList.add('cb-uses-inc-none');
    panel.appendChild(b);
  });
}

// Leave the scheme and land on the incident itself, highlighted so it is obvious
// which of the cards you were sent to.
async function cbGoToIncident(incId) {
  setView('incidents');
  await refreshIncidents();
  const card = document.querySelector(`.tow-card[data-card="${CSS.escape(incId)}"]`);
  if (!card) return;
  // setView puts this view back where you left it on the next frame, so park the
  // card's position there and land after that restore rather than fighting it.
  const top = Math.max(0, card.getBoundingClientRect().top + window.scrollY
                          - Math.max(0, (window.innerHeight - card.offsetHeight) / 2));
  VIEW_SCROLL.incidents = top;
  requestAnimationFrame(() => requestAnimationFrame(() => window.scrollTo(0, top)));
  card.classList.add('cb-target');
  setTimeout(() => card.classList.remove('cb-target'), 1600);
}

// One code: its name, its definition, how much coding already depends on it, and
// a delete the server refuses while that number is above zero.
function cbOptionRow(r, o) {
  const row = document.createElement('div');
  row.className = 'cb-row';

  const main = document.createElement('div');
  main.className = 'cb-main';

  main.appendChild(cbEditable({
    value: o.name,
    cls: 'cb-name',
    placeholder: 'Name this code',
    save: async (next) => {
      if (o.total && !confirm(
          `Rename “${o.name}” to “${next}”?\n\n` +
          `${o.total} existing use${o.total === 1 ? '' : 's'} ` +
          `(${Object.entries(o.uses).map(([c, n]) => `${c}: ${n}`).join(', ')}) ` +
          `will be rewritten to the new name. Push to Mongo afterwards to carry ` +
          `the change up.`)) return false;
      const d = await cbPost('/api/vocab/rename', { role: r.role, old: o.name, new: next });
      if (!d.ok) return cbFail(d, 'Could not rename that code');
      cbSay(d.total ? `Renamed — ${d.total} use${d.total === 1 ? '' : 's'} rewritten`
                    : 'Renamed');
      await cbReload();
      return true;
    },
  }));

  main.appendChild(cbEditable({
    value: o.definition,
    cls: 'cb-def',
    multiline: true,
    placeholder: 'Add a definition…',
    save: async (next) => {
      const d = await cbPost('/api/vocab/definition',
                             { role: r.role, option: o.name, definition: next });
      if (!d.ok) return cbFail(d, 'Could not save that definition');
      o.definition = next;
      cbSay('Definition saved');
      await cbApplyToCodingViews();
      return true;
    },
  }));
  row.appendChild(main);

  const side = document.createElement('div');
  side.className = 'cb-side';
  if (o.total) {
    const uses = document.createElement('button');
    uses.className = 'cb-uses cb-uses-btn';
    uses.innerHTML = cbUsesText(o);
    uses.title = 'Show the incidents this code is used in';
    uses.onclick = () => cbToggleUses(r.role, o.name, row, uses);
    side.appendChild(uses);
  } else {
    side.innerHTML = `<div class="cb-uses">${cbUsesText(o)}</div>`;
  }
  const del = document.createElement('button');
  del.className = 'cb-del';
  del.textContent = '×';
  del.title = o.total ? `In use ${o.total} time${o.total === 1 ? '' : 's'} — rename it instead`
                      : 'Remove this code';
  del.onclick = async () => {
    if (!confirm(`Remove “${o.name}” from the ${r.label.toLowerCase()} codes?`)) return;
    const d = await cbPost('/api/vocab/delete', { role: r.role, option: o.name });
    if (!d.ok) return cbFail(d, 'Could not remove that code');
    cbSay('Removed');
    await cbReload();
  };
  side.appendChild(del);
  row.appendChild(side);
  return row;
}

// Add a code to this characteristic, into one of its groups where it has them.
function cbAddRow(r) {
  const wrap = document.createElement('div');
  wrap.className = 'cb-add';

  const name = document.createElement('input');
  name.className = 'cb-add-name';
  name.placeholder = `Add a ${r.label.toLowerCase()} code…`;

  const group = document.createElement('select');
  group.className = 'cb-add-group';
  if (r.groups.length) {
    group.innerHTML = `<option value="">No group</option>` +
      r.groups.map(g => `<option value="${escapeHtml(g)}">${escapeHtml(g)}</option>`).join('');
  } else {
    group.style.display = 'none';
  }

  const go = document.createElement('button');
  go.className = 'cb-add-go';
  go.textContent = 'Add';
  go.onclick = async () => {
    const val = name.value.trim();
    if (!val) return;
    const d = await cbPost('/api/vocab/option',
                           { role: r.role, option: val, group: group.value });
    if (!d.ok) return cbFail(d, 'Could not add that code');
    name.value = '';
    cbSay('Added');
    await cbReload();
  };
  name.onkeydown = (e) => { if (e.key === 'Enter') go.click(); };

  wrap.append(name, group, go);
  return wrap;
}

// One click-to-edit cell. Reads as plain text until you click it, the way the
// incident title does, so a page of definitions reads as a codebook rather than
// as a wall of form fields. Enter commits (⌘/Ctrl+Enter in a definition, where
// Enter is a line break); Escape puts the old text back.
function cbEditable({ value, placeholder, multiline, cls, save }) {
  const wrap = document.createElement('div');
  wrap.className = 'cb-edit';

  const show = () => {
    wrap.innerHTML = '';
    const view = document.createElement('div');
    view.className = `${cls}-view${value ? '' : ' empty'}`;
    view.textContent = value || placeholder;
    view.title = 'Click to edit';
    view.onclick = edit;
    wrap.appendChild(view);
  };

  const edit = () => {
    wrap.innerHTML = '';
    const f = document.createElement(multiline ? 'textarea' : 'input');
    f.className = `${cls}-field`;
    f.value = value;
    if (multiline) f.rows = Math.min(6, Math.max(2, Math.ceil((value.length || 40) / 60)));
    let settled = false;
    const commit = async () => {
      if (settled) return;
      settled = true;
      const next = f.value.trim();
      if (next && next !== value) {
        const ok = await save(next);
        if (ok !== false) value = next;
      }
      show();
    };
    f.onblur = commit;
    f.onkeydown = (e) => {
      if (e.key === 'Escape') { settled = true; show(); }
      else if (e.key === 'Enter' && (!multiline || e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        f.blur();
      }
    };
    wrap.appendChild(f);
    f.focus();
    if (f.setSelectionRange) f.setSelectionRange(f.value.length, f.value.length);
  };

  show();
  return wrap;
}

async function cbPost(path, body) {
  const res = await fetch(path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  let data = {};
  try { data = await res.json(); } catch (e) { /* empty body */ }
  return { ok: res.ok, ...data };
}

function cbFail(d, fallback) {
  const detail = d.error === 'in use'
    ? `it is used ${d.total} time${d.total === 1 ? '' : 's'} — rename it instead`
    : d.error;
  cbSay(`${fallback}${detail ? ' — ' + detail : ''}`);
  return false;
}

function cbSay(msg) {
  const el = document.getElementById('status');
  el.textContent = msg;
  clearTimeout(cbSay._t);
  cbSay._t = setTimeout(() => { if (el.textContent === msg) el.textContent = ''; }, 4000);
}

// Re-read the scheme and redraw the tab. Used after anything that can change the
// option list, since usage counts and grouping move with it.
async function cbReload() {
  await loadCodebook();
  await cbApplyToCodingViews();
}

// The coding views build their menus once from SCHEMA_ROLES, so an edit here has
// to be pushed back into them — otherwise the tab you just left would still
// offer yesterday's wording until a reload.
async function cbApplyToCodingViews() {
  let schema;
  try { schema = await (await fetch('/api/schema')).json(); }
  catch (e) { return; }
  SCHEMA = schema.fields;
  SCHEMA_ROLES = schema.claim_roles || [];
  if (schema.rules) RULES = schema.rules;
  if (curDoc) renderForm();
  // Incident cards show chips of coded values, so a rename changes them too.
  INCIDENTS_RENDERED = false;
}
