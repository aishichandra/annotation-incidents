// Armed-target styling and the roles panel.
// Which field is armed to receive the next highlight, the hint text,
// scroll-to helpers, and the flat actor/harm/factor role cards.
//
// Loaded as a classic script: everything here shares one global scope with the
// other static/js files. See templates/index.html for the load order.


function armField(key, value) { setArm({ type: 'field', key, value }); }
function afterArm() { updateArmHint(); refreshArmedStyles(); }

// Toggle armed styling on text-field cards and per-selection evidence rows.
function refreshArmedStyles() {
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

function updateArmHint() {
  const h = document.getElementById('armHint');
  if (!armed) { h.textContent = ''; return; }
  const val = armed.value ? ' · ' + armed.value : '';
  h.textContent = armed.type === 'field'
    ? `Highlighting → ${(field(armed.key) || {}).label}${val}`
    : `Highlighting → ${(ROLE[armed.role] || {}).label}${val}`;
}
function flashHint() {
  const h = document.getElementById('armHint');
  h.style.color = '#dc2626'; h.textContent = 'Arm a field or a characteristic first ↗';
  setTimeout(() => { h.style.color = ''; updateArmHint(); }, 1500);
}

function scrollToCard(key) {
  const c = document.querySelector(`.card[data-key="${key}"]`);
  if (c) c.scrollIntoView({ behavior: 'smooth', block: 'center' });
}
function scrollToMark(gi) {
  const m = document.querySelector(`mark[data-q="${gi}"]`);
  if (m) {
    m.scrollIntoView({ behavior: 'smooth', block: 'center' });
    m.classList.add('active'); setTimeout(() => m.classList.remove('active'), 1200);
  }
}

// ---------- characteristics: flat actor / harm / factor / harmed party ----------
// No linking here — each role is just a multiselect of values, each value
// justified by highlights. Grouping into claims happens in the card view.
function renderRoles() {
  const old = document.querySelector('.roles-section');
  if (old) old.replaceWith(buildRolesPanel());
}

function buildRolesPanel() {
  const section = document.createElement('div');
  section.className = 'roles-section';
  const head = document.createElement('div');
  head.className = 'claims-head';
  // head.innerHTML = `<span class="label">Characteristics</span>`;
  section.appendChild(head);
  ROLES.forEach(r => section.appendChild(buildRoleCard(r)));
  return section;
}

function buildRoleCard(r) {
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
  const noteLabel = (SCHEMA_ROLES.find(x => x.role === r.role) || {}).note_label;
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

