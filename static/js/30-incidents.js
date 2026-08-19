// Incidents view.
// One card per incident: field blocks, the role palette, claim groups,
// drag-and-drop chips, per-incident comments, quote panels, JSON inspector.
//
// Loaded as a classic script: everything here shares one global scope with the
// other static/js files. See templates/index.html for the load order.

// Incidents whose data changed since they were rendered. A document save marks
// the incident it belongs to; nothing else needs a redraw, so switching tabs
// with none of these is free.
const DIRTY_INCIDENTS = new Set();
let INCIDENTS_RENDERED = false;

function markIncidentDirty(incId) {
  if (incId) DIRTY_INCIDENTS.add(incId);
}

// Bring the incidents view up to date with the least disturbance: nothing on the
// first visit but a full render, and after that only the cards whose data moved.
async function refreshIncidents() {
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
  // An incident that appeared or disappeared changes the list itself, not just a
  // card, so fall back to a full render for that.
  const sameSet = Object.keys(fresh).length === Object.keys(INCIDENTS).length
    && Object.keys(fresh).every(k => k in INCIDENTS);
  if (!sameSet) {
    DIRTY_INCIDENTS.clear();
    return loadIncidents();
  }
  DIRTY_INCIDENTS.forEach(incId => {
    const inc = fresh[incId];
    const card = document.querySelector(`.tow-card[data-card="${CSS.escape(incId)}"]`);
    if (!inc || !card) return;
    INCIDENTS[incId] = inc;
    const holder = document.createElement('div');
    holder.innerHTML = incidentCard(inc, data.fields);
    const replacement = holder.firstElementChild;
    card.replaceWith(replacement);
    wireIncidentCard(replacement);
  });
  DIRTY_INCIDENTS.clear();
}

// Post-render wiring for one card: opening its documents, its draggable chips,
// its claims and its JSON panel. Shared so a single re-rendered card comes back
// as live as one from a full render.
function wireIncidentCard(root) {
  root.querySelectorAll('.tow-doc').forEach(el => {
    el.onclick = (e) => {
      if (e.target.closest('.durl')) return;   // let the external link work
      const i = +el.dataset.index;
      setView('docs');
      document.getElementById('docSelect').value = i;
      loadDoc(i);
    };
  });
  root.querySelectorAll('.tow-palette').forEach(el => buildPalette(el, INCIDENTS[el.dataset.inc]));
  root.querySelectorAll('.tow-groups').forEach(el => buildGroupsUI(el, INCIDENTS[el.dataset.inc]));
  root.querySelectorAll('.inc-note-body').forEach(el =>
    buildIncidentComment(el, el.closest('.inc-note').dataset.inc));
  root.querySelectorAll('.inc-complete').forEach(el => wireComplete(el));
  root.querySelectorAll('.json-btn').forEach(btn => btn.onclick = () => toggleJson(btn.dataset.inc));
}

async function loadIncidents() {
  const wrap = document.getElementById('incidents');
  wrap.innerHTML = '<div class="inc-wrap"><div class="iempty">Loading…</div></div>';
  let data;
  try { data = await (await fetch('/api/incidents')).json(); }
  catch (e) {
    wrap.innerHTML = '<div class="inc-wrap"><div class="iempty">Failed to load incidents.</div></div>'; return;
  }
  INCIDENTS = {};
  data.incidents.forEach(inc => { INCIDENTS[inc.incident_id] = inc; });
  // Incidents this coder has set aside drop to their own collapsed list at the
  // foot of the page: out of the way of the ones still being coded, but visible
  // and countable, since "what did I rule out" is part of the record.
  const live = data.incidents.filter(inc => inc.status !== 'not_an_incident');
  const out = data.incidents.filter(inc => inc.status === 'not_an_incident');
  const body = live.length
    ? live.map(inc => incidentCard(inc, data.fields)).join('')
    : `<div class="iempty">${out.length ? 'Every incident has been set aside.'
                                        : 'No incidents coded yet.'}</div>`;
  const excluded = out.length
    ? `<details class="inc-out-list">
         <summary>Not an incident (${out.length})</summary>
         ${out.map(inc => incidentCard(inc, data.fields)).join('')}
       </details>`
    : '';
  wrap.innerHTML = `<div class="inc-wrap">${body}${excluded}</div>`;
  wireIncidentCard(wrap);
  INCIDENTS_RENDERED = true;
  DIRTY_INCIDENTS.clear();
}

const NODATA = '<span class="tow-nodata">No data</span>';
let INCIDENTS = {};   // incident_id -> incident object, for post-render handlers

// A Tow/CJR-styled card carrying every detail the JSON holds for one incident.
// Anything un-coded renders as "No data". The characteristics palette + claim
// groups are an interactive placeholder filled in by buildGroupsUI after render.
function incidentCard(inc, fields) {
  const chips = arr => arr.map(v => `<span class="tow-chip">${escapeHtml(v)}</span>`).join('');

  // One field block — free text about the incident, plus any coder comments.
  // Everything picked from a vocabulary is a characteristic and lives in the
  // palette instead, so nothing here is draggable.
  const fieldBlock = (f) => {
    const vals = inc.field_values[f.key] || [];
    const cmts = (inc.field_comments && inc.field_comments[f.key]) || [];
    const valHtml = !vals.length ? NODATA : chips(vals);
    const cmtHtml = cmts.map(c => `<div class="tow-comment">“${escapeHtml(c)}”</div>`).join('');
    return `<div class="tow-field"><div class="tow-label">${escapeHtml(f.label)}</div>`
      + `<div class="tow-value">${valHtml}${cmtHtml}</div></div>`;
  };
  // Aftermath renders last of all, under the claim groups; the rest sit up left.
  const leftFieldBlocks = fields.filter(f => f.key !== 'incident_aftermath').map(fieldBlock).join('');
  const aftermath = fields.find(f => f.key === 'incident_aftermath');
  const aftermathBlock = aftermath ? fieldBlock(aftermath) : '';

  const docsHtml = inc.documents.map(d => {
    const url = d.url
      ? `<a class="durl" href="${escapeHtml(d.url)}" target="_blank" rel="noopener">source ↗</a>` : '';
    // Who has coded this document — progress only, never their codes, so coders
    // stay blind to each other's judgements while coding.
    const by = (d.coded_by || []);
    const badge = by.length
      ? `<span class="dcoders" title="Coded by ${escapeHtml(by.join(', '))}">${
          by.map(c => `<span class="dcoder${c === CODER ? ' me' : ''}">${escapeHtml(c)}</span>`).join('')}</span>`
      : '<span class="dcoders"><span class="dcoder none">uncoded</span></span>';
    return `<div class="tow-doc" data-index="${d.index}">`
      + `<span class="dtitle">${escapeHtml(d.title || '(untitled document)')}</span>${badge}${url}</div>`;
  }).join('');

  const encId = escapeHtml(inc.incident_id);
  return `<div class="tow-card" data-card="${encId}">
    <div class="tow-head">
      <span class="tow-id">${encId}</span>
      <div class="tow-headdocs">${docsHtml}</div>
    </div>
    <div class="tow-body">
      <div class="tow-col c1">
        ${leftFieldBlocks}
        <div class="tow-palette" data-inc="${encId}"></div>
      </div>
      <div class="tow-col c2">
        <div class="tow-groups" data-inc="${encId}"></div>
        ${aftermathBlock}
      </div>
    </div>
    <div class="inc-note" data-inc="${encId}">
      <div class="inc-note-head">
        <div class="tow-label">Comments${CODER ? '(' + escapeHtml(CODER) + ')' : ''}</div>
        <span class="inc-note-state"></span>
      </div>
      <div class="inc-note-body"></div>
    </div>
    <div class="tow-foot">
      <button class="json-btn" data-inc="${encId}" title="Show this incident as it is stored">{ } JSON</button>
      <div class="inc-complete" data-inc="${encId}">${completeControl(inc)}</div>
    </div>
    <div class="json-panel" data-inc="${encId}" hidden></div>
  </div>`;
}

const roleColor = (role) => (CLAIM_ROLE[role] && CLAIM_ROLE[role].color) || '#e5e7eb';
const roleLabel = (role) => (CLAIM_ROLE[role] && CLAIM_ROLE[role].label) || role;
// The palette in ROLES is chip *background* colour — pale by design, and too light
// to read as text on white (yellow worst of all). Darken it toward black for the
// sentence placeholders, so an empty slot still shows which role it wants without
// a second colour list that could drift out of step with the chips.
function roleInk(role, amount = 0.45) {
  const hex = roleColor(role);
  const n = parseInt(hex.slice(1), 16);
  const dim = (c) => Math.round(c * (1 - amount));
  return `rgb(${dim((n >> 16) & 255)}, ${dim((n >> 8) & 255)}, ${dim(n & 255)})`;
}

// Persist an incident's groups (debounced-ish: fire immediately, it's small).
// ---------- completion sign-off ----------
// A mirror of incident_completeness() in incidents.py, so the control can react
// to a drag without a round trip. The server re-checks before recording a
// sign-off and answers 409 if it disagrees, so the two drifting apart costs a
// confusing button, never a wrong record.
const MISSING_LABEL = { complete_claim: 'a linked claim' };

function claimIsComplete(cl) {
  return !!(cl.harm && (cl.harmed_parties || []).length && (cl.factors || []).length);
}

function completenessOf(inc) {
  const missing = (RULES.required_roles || [])
    .filter(r => !(((inc.role_values || {})[r]) || []).length);
  if (!(inc.groups || []).some(g => g.actor && (g.claims || []).some(claimIsComplete))) {
    missing.push('complete_claim');
  }
  return { ok: !missing.length, missing };
}

function missingText(missing) {
  return missing.map(m => MISSING_LABEL[m] || roleLabel(m).toLowerCase()).join(', ');
}

function completeControl(inc) {
  const encId = escapeHtml(inc.incident_id);
  const when = (inc.completed_at || '').slice(0, 10);
  if (inc.status === 'not_an_incident') {
    const why = inc.excluded_reason
      ? ` \u00b7 ${escapeHtml(inc.excluded_reason)}` : '';
    return `<span class="inc-out" title="Set aside by you${when ? ' on ' + when : ''}">`
         + `Not an incident${why}</span>`
         + `<button class="inc-restore" data-inc="${encId}" `
         + `title="Put this back with the incidents you are coding">Restore</button>`;
  }
  if (inc.status === 'complete') {
    return `<span class="inc-done" title="Signed off ${escapeHtml(inc.completed_at || '')}">`
         + `\u2713 Complete${when ? ' \u00b7 ' + escapeHtml(when) : ''}</span>`
         + `<button class="inc-undo" data-inc="${encId}" title="Withdraw this sign-off">Undo</button>`;
  }
  // Excluding is always available: it is a judgement about the material, so it
  // never waits on the coding being finished.
  const drop = `<button class="inc-drop" data-inc="${encId}" `
             + `title="This isn't an incident \u2014 set it aside">Not an incident</button>`;
  const st = completenessOf(inc);
  if (!st.ok) {
    return `<span class="inc-needs" title="Fill these in to sign this incident off">`
         + `Needs ${escapeHtml(missingText(st.missing))}</span>` + drop;
  }
  return drop + `<button class="inc-mark" data-inc="${encId}">Mark complete</button>`;
}

// Re-render the control wherever this incident is on screen. Called after any
// edit the completeness check reads, so the button tracks the coding.
function refreshComplete(inc) {
  document.querySelectorAll('.inc-complete').forEach(el => {
    if (el.dataset.inc !== inc.incident_id) return;
    el.innerHTML = completeControl(inc);
    wireComplete(el);
  });
}

function wireComplete(el) {
  const mark = el.querySelector('.inc-mark');
  if (mark) mark.onclick = () => setStatus(mark.dataset.inc, 'complete');
  const undo = el.querySelector('.inc-undo');
  if (undo) undo.onclick = () => setStatus(undo.dataset.inc, '');
  const restore = el.querySelector('.inc-restore');
  if (restore) restore.onclick = () => setStatus(restore.dataset.inc, '');
  const drop = el.querySelector('.inc-drop');
  if (drop) drop.onclick = () => setStatus(drop.dataset.inc, 'not_an_incident');
}

async function setStatus(incId, status, reason) {
  const inc = INCIDENTS[incId];
  if (!inc) return;
  const was = inc.status;
  const el = document.querySelector(`.inc-complete[data-inc="${CSS.escape(incId)}"]`);
  el && el.querySelectorAll('button').forEach(b => b.disabled = true);
  try {
    const res = await fetch('/api/incident/' + encodeURIComponent(incId) + '/status', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status, reason: reason || '' }),
    });
    // An error page is HTML, not JSON, so parsing has to be allowed to fail —
    // otherwise every non-JSON response arrives here as a bare parse error and
    // the real status code is lost.
    let j = null;
    try { j = await res.json(); } catch (_) { /* not JSON: an error page */ }
    if (!res.ok || !j || !j.ok) {
      // 409 is the completeness refusal, and it names what is missing — the one
      // failure that is about the coding rather than the plumbing.
      const msg = (res.status === 409 && j)
        ? `Needs ${escapeHtml(missingText(j.missing || []))}`
        : (res.status === 404
            // This page asked for a route the server doesn't have, which almost
            // always means the server predates it: app.py runs without the
            // reloader unless FLASK_DEBUG is set, so a .py change needs a restart.
            ? 'Route not found \u2014 restart the app to pick up server changes'
            : `Save failed \u2014 HTTP ${res.status}`);
      if (el) el.innerHTML = `<span class="inc-needs">${msg}</span>`;
      return;
    }
    inc.status = j.status;
    inc.completed_at = j.completed_at;
    inc.excluded_reason = j.excluded_reason || '';
  } catch (e) {
    // fetch itself threw: nothing answered at all.
    if (el) el.innerHTML = '<span class="inc-needs">No response \u2014 is the app running?</span>';
    return;
  }
  // Crossing into or out of "not an incident" moves the card between the live
  // list and the set-aside one — a change to the list, not to one card, so the
  // whole thing is redrawn. Every other transition just restyles the control.
  if (status === 'not_an_incident' || was === 'not_an_incident') return loadIncidents();
  refreshComplete(inc);
}


async function saveGroups(inc) {
  try {
    await fetch('/api/incident/' + encodeURIComponent(inc.incident_id) + '/groups', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ groups: inc.groups }),
    });
  } catch (e) { /* non-fatal: local drag state stays until reload */ }
  // Editing the claims invalidates any sign-off — the server does this in
  // clear_signoff(); reflect it here so the card can't keep claiming complete.
  if (inc.status === 'complete') { inc.status = ''; inc.completed_at = ''; }
  refreshComplete(inc);
}

// Comments in flight, incident id -> pending debounce timer. Typing shouldn't
// cost a request per keystroke, but nothing may be lost either, so a pending
// comment is flushed before anything that could redraw its card.
const COMMENT_TIMERS = new Map();

function flushIncidentComments() {
  return Promise.all(Array.from(COMMENT_TIMERS.keys()).map(incId => {
    clearTimeout(COMMENT_TIMERS.get(incId));
    COMMENT_TIMERS.delete(incId);
    return saveComment(incId);
  }));
}

// Typed and then reloaded, before the debounce ran. `keepalive` on the request is
// what lets it outlive the page.
window.addEventListener('pagehide', flushIncidentComments);

// Persist one incident's comment. The text comes from the in-memory incident, so
// this is safe to call after the textarea has gone (a re-render, a view switch).
async function saveComment(incId) {
  const inc = INCIDENTS[incId];
  if (!inc) return;
  const box = document.querySelector(`.inc-note[data-inc="${CSS.escape(incId)}"]`);
  const state = box && box.querySelector('.inc-note-state');
  if (state) { clearTimeout(state._clear); state.textContent = 'Saving…'; state.classList.remove('on'); }
  try {
    await fetch('/api/incident/' + encodeURIComponent(incId) + '/comment', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ comment: inc.comment || '' }), keepalive: true,
    });
    if (state) {
      state.textContent = 'Saved ✓'; state.classList.add('on');
      clearTimeout(state._clear);
      state._clear = setTimeout(() => {
        state.textContent = ''; state.classList.remove('on');
      }, 2000);
    }
  } catch (e) {
    if (state) { state.textContent = 'Not saved — check your connection'; state.classList.remove('on'); }
  }
}

// One card's comment box. Set the same way the actor's name and the incident
// title are: type it, press Enter, and it settles into plain text you click to
// edit again — so a written comment reads as *entered* rather than as something
// still sitting in a box. Shift+Enter is a new line, since a comment is prose.
// Typing still autosaves on a debounce, so nothing is lost on the way to setting.
function buildIncidentComment(wrap, incId) {
  const inc = INCIDENTS[incId];
  if (!inc) return;

  // Leave edit mode and write it. `editing` only ever forces the box open over a
  // comment that already exists, so it is not a "was the coder typing?" flag —
  // a first comment is typed with it unset, and guarding on it here left Enter
  // doing nothing at all until the second edit.
  const settle = () => {
    wrap.dataset.editing = '';
    render(false);
    saveNow();
  };

  const saveNow = () => {
    clearTimeout(COMMENT_TIMERS.get(incId));
    COMMENT_TIMERS.delete(incId);
    saveComment(incId);
  };

  function render(focus) {
    wrap.innerHTML = '';
    const val = (inc.comment || '').trim();
    if (val && wrap.dataset.editing !== '1') {
      const box = document.createElement('div');
      box.className = 'text-answer inc-note-set';
      box.innerHTML = `<span class="val">${escapeHtml(val)}</span><button class="x" title="Clear">×</button>`;
      box.querySelector('.x').onclick = (e) => {
        e.stopPropagation();
        inc.comment = ''; wrap.dataset.editing = ''; saveNow(); render(true);
      };
      box.onclick = (e) => {
        if (e.target.classList.contains('x')) return;
        wrap.dataset.editing = '1'; render(true);
      };
      wrap.appendChild(box);
      return;
    }
    const ta = document.createElement('textarea');
    ta.rows = 2;
    ta.value = inc.comment || '';
    ta.oninput = () => {
      inc.comment = ta.value;
      clearTimeout(COMMENT_TIMERS.get(incId));
      COMMENT_TIMERS.set(incId, setTimeout(() => {
        COMMENT_TIMERS.delete(incId);
        saveComment(incId);
      }, 700));
    };
    ta.onkeydown = (e) => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); settle(); }
    };
    ta.onblur = () => {
      if (!document.body.contains(ta)) return;   // ignore blur from our own re-render
      if ((ta.value || '').trim()) settle();
      else if (COMMENT_TIMERS.has(incId)) saveNow();   // cleared, and not yet written
    };
    wrap.appendChild(ta);
    if (focus) ta.focus();
  }

  wrap.dataset.editing = '';
  render(false);
}

// Where a characteristic has been used. A value can appear in any number of
// places — dragging copies rather than moves — so this returns every mark that
// holds it, and the palette shows them on the chip.
//
// Actor / system / developer belong to the group, so their mark is the group id
// ("2"). Harm / harmed party / factor belong to one claim inside a group, so
// theirs is "group.claim" ("2.1").
function usedInClaims(inc, role, value) {
  const marks = [];
  (inc.groups || []).forEach(g => {
    if (GROUP_ROLES.includes(role)) {
      if (g[role] === value) marks.push(String(g.id));
      return;
    }
    (g.claims || []).forEach(cl => {
      const key = CLAIM_LIST_KEYS[role];
      const hit = key ? (cl[key] || []).includes(value) : cl[role] === value;
      if (hit) marks.push(g.id + '.' + cl.id);
    });
  });
  return marks;
}

// Re-render everything draggable on an incident's card — the characteristics
// palette and the System/Developer field chips — so their claim marks match the
// current claims. Called after every claim change; these live in a sibling
// column, so they're found by incident id rather than passed around.
function refreshDraggables(inc) {
  document.querySelectorAll('.tow-palette').forEach(el => {
    if (el.dataset.inc === inc.incident_id) buildPalette(el, inc);
  });
}

// The pooled characteristics palette (left column) — draggable chips grouped by
// role, each marked with the claims it's already used in.
function buildPalette(container, inc) {
  if (!inc) return;
  container.innerHTML = '';
  const palette = document.createElement('div');
  palette.className = 'tow-field';
  // palette.innerHTML = `<div class="tow-label">Characteristics</div>`;
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
  container.appendChild(palette);
}


// The claim groups (right column) — each a fill-in-the-blank sentence + drop zone.
// Rebuilds itself on every change and persists per incident.
function buildGroupsUI(container, inc) {
  if (!inc) return;
  // Always start with one actor context holding one empty claim. Empty groups
  // aren't saved server-side, so this just seeds the template each load.
  if (!inc.groups.length) inc.groups.push(newGroup(inc));
  container.innerHTML = '';

  const groupsWrap = document.createElement('div');
  groupsWrap.className = 'tow-field';
  groupsWrap.innerHTML = `<div class="tow-label">Groups (linked claims)</div>`;
  inc.groups.forEach(grp => groupsWrap.appendChild(buildGroupBox(inc, grp, container)));

  const add = document.createElement('button');
  add.className = 'grp-add'; add.textContent = '+ New actor group';
  add.onclick = () => {
    inc.groups.push(newGroup(inc));
    saveGroups(inc);
    buildGroupsUI(container, inc);
  };
  groupsWrap.appendChild(add);
  container.appendChild(groupsWrap);
  refreshDraggables(inc);   // keep the chips' claim marks in step with the claims
}

// Ids are per-incident counters, unique only within their own scope: group ids
// across the incident, claim ids within their group.
function nextId(list) {
  return String(list.reduce((mx, x) => Math.max(mx, parseInt(x.id, 10) || 0), 0) + 1);
}

function newClaim(grp) {
  // harmed_parties is plural — a single harm can land on several parties. The
  // singular harmed_party is the pre-plural field and must not be seeded here,
  // or every new claim carries a dead null nobody reads.
  return { id: nextId(grp.claims || []), harm: null, harmed_parties: [], factors: [] };
}

function newGroup(inc) {
  const g = { id: nextId(inc.groups || []), actor: null, system: null, developer: null,
              claims: [], omit: [] };
  g.claims.push(newClaim(g));
  return g;
}

// A draggable palette chip (characteristics palette only). `claims` is the list
// of claim ids this value is already in — a used chip stays fully draggable,
// since the same characteristic is expected to appear in several claims; it just
// carries the claim numbers so you can see what's still unplaced.
function makeChip(role, value, claims, inc, kind) {
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

// ---------- raw JSON for one incident ----------
// Fetched rather than printed from INCIDENTS, because the card's own object is
// the *aggregated view* (pooled values, pruned claims) while what's usually
// wanted here is the record as stored. The response says which one it is.
async function toggleJson(incId) {
  const panel = document.querySelector(`.json-panel[data-inc="${CSS.escape(incId)}"]`);
  const btn = document.querySelector(`.json-btn[data-inc="${CSS.escape(incId)}"]`);
  if (!panel) return;
  if (!panel.hidden) {                       // open -> close
    panel.hidden = true;
    if (btn) btn.classList.remove('on');
    return;
  }
  panel.hidden = false;
  if (btn) btn.classList.add('on');
  panel.textContent = 'Loading…';
  try {
    const res = await fetch('/api/incident/' + encodeURIComponent(incId) + '/json'
                            + (CODER ? '?coder=' + encodeURIComponent(CODER) : ''));
    const j = await res.json();
    if (!res.ok) throw new Error(j.error || res.status);
    panel.innerHTML = '';
    const bar = document.createElement('div');
    bar.className = 'json-bar';
    const src = document.createElement('div');
    src.className = 'json-src';
    src.textContent = 'source: ' + j.source;
    const tree = jsonTree(j.incident);
    const act = (label, fn) => {
      const b = document.createElement('button');
      b.className = 'json-act'; b.textContent = label; b.onclick = fn;
      return b;
    };
    bar.appendChild(src);
    bar.appendChild(act('Collapse all', () =>
      tree.querySelectorAll('.jn').forEach(n => n.querySelector('.jn-body') && n.classList.add('collapsed'))));
    bar.appendChild(act('Expand all', () =>
      tree.querySelectorAll('.jn').forEach(n => n.classList.remove('collapsed'))));
    const copy = act('Copy', async () => {
      try {
        await navigator.clipboard.writeText(JSON.stringify(j.incident, null, 2));
        copy.textContent = 'Copied ✓';
        setTimeout(() => { copy.textContent = 'Copy'; }, 1200);
      } catch (_) { copy.textContent = 'Copy failed'; }
    });
    bar.appendChild(copy);
    panel.appendChild(bar);
    panel.appendChild(tree);
  } catch (e) {
    panel.textContent = 'Could not load JSON: ' + e.message;
  }
}

// A collapsible, colour-coded rendering of a JSON value. Plain JSON.stringify
// output is a wall of monospace where the only cue to structure is how far a
// line is indented — fine for ten lines, not for an incident. Here keys are
// distinguished from values by colour, every object and array folds away behind
// its own header, and an indent guide runs down each level so a nested value can
// be traced back to what contains it.
function jsonTree(value) {
  const root = document.createElement('div');
  root.className = 'json-tree';
  root.appendChild(jsonNode(null, value, true));
  return root;
}

function jsonLeaf(value) {
  if (value === null) return '<span class="jn-null">null</span>';
  const t = typeof value;
  if (t === 'string') return `<span class="jn-str">${escapeHtml(JSON.stringify(value))}</span>`;
  if (t === 'number') return `<span class="jn-num">${value}</span>`;
  if (t === 'boolean') return `<span class="jn-bool">${value}</span>`;
  return escapeHtml(String(value));
}

function jsonNode(key, value, last) {
  const wrap = document.createElement('div');
  wrap.className = 'jn';
  // Array items have no key of their own; their position is the indent.
  const keyHtml = key === null ? ''
    : `<span class="jn-key">${escapeHtml(JSON.stringify(key))}</span><span class="jn-p">: </span>`;
  const comma = last ? '' : '<span class="jn-p">,</span>';

  if (value === null || typeof value !== 'object') {
    wrap.innerHTML = `<span class="jn-caret"></span>${keyHtml}${jsonLeaf(value)}${comma}`;
    return wrap;
  }
  const isArr = Array.isArray(value);
  const entries = isArr ? value.map((v, i) => [null, v]) : Object.entries(value);
  const [open, close] = isArr ? ['[', ']'] : ['{', '}'];
  if (!entries.length) {   // nothing to fold away
    wrap.innerHTML = `<span class="jn-caret"></span>${keyHtml}`
      + `<span class="jn-empty">${open}${close}</span>${comma}`;
    return wrap;
  }

  const head = document.createElement('div');
  head.className = 'jn-head';
  head.innerHTML = `<span class="jn-caret">▾</span>${keyHtml}<span class="jn-p">${open}</span>`
    + `<span class="jn-sum"> … <span class="jn-p">${close}</span>${comma}</span>`
    + `<span class="jn-count">${entries.length} ${isArr
        ? (entries.length === 1 ? 'item' : 'items')
        : (entries.length === 1 ? 'key' : 'keys')}</span>`;
  head.onclick = (e) => { e.stopPropagation(); wrap.classList.toggle('collapsed'); };

  const body = document.createElement('div');
  body.className = 'jn-body';
  entries.forEach(([k, v], i) => body.appendChild(jsonNode(k, v, i === entries.length - 1)));

  const tail = document.createElement('div');
  tail.className = 'jn-tail';
  tail.innerHTML = `<span class="jn-p">${close}</span>${comma}`;

  wrap.appendChild(head); wrap.appendChild(body); wrap.appendChild(tail);
  return wrap;
}

// ---------- evidence behind a characteristic ----------
// Which quotes justify one pooled value. `kind` is the tag the quote carries —
// the characteristic's role ('harm').
function evidenceFor(inc, kind, value) {
  return ((inc.value_quotes || {})[kind] || {})[value] || [];
}

function quotesKey(kind, value) { return kind + ' ' + value; }

// Which evidence panels are open, keyed by incident id. Held here rather than on
// the incident object because a refreshed card is handed a *new* object from the
// server — state hanging off the old one would shut every panel on the card you
// had just been reading. Survives both the palette rebuild after a claim change
// and a card re-render after a save.
const OPEN_QUOTES = {};

function openSet(inc) {
  return OPEN_QUOTES[inc.incident_id] || (OPEN_QUOTES[inc.incident_id] = new Set());
}

function isQuotesOpen(inc, kind, value) {
  return openSet(inc).has(quotesKey(kind, value));
}

function toggleQuotes(inc, kind, value) {
  const open = openSet(inc);
  const k = quotesKey(kind, value);
  if (!open.delete(k)) open.add(k);
}

// The panel itself: every passage this coder highlighted for the value, with the
// document it came from (incidents can pool several).
function quotePanel(inc, kind, value) {
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

// A group is one actor context — "<actor> using <system> developed by <developer>"
// — with its claims listed underneath. The header and each claim row are separate
// drop zones, so a dragged chip's destination is never ambiguous: actor / system /
// developer land in the header, harm / harmed party / factor land in the claim you
// drop them on.
function buildGroupBox(inc, grp, container) {
  const box = document.createElement('div');
  box.className = 'grp-box';

  const top = document.createElement('div');
  top.className = 'grp-top';
  top.innerHTML = `<span class="grp-name">Group ${escapeHtml(grp.id)}</span>`;
  const del = document.createElement('button');
  del.className = 'grp-del'; del.textContent = '×';
  del.title = 'Delete this actor group and all its claims';
  del.onclick = () => {
    inc.groups = inc.groups.filter(g => g !== grp);
    saveGroups(inc);
    buildGroupsUI(container, inc);
  };
  top.appendChild(del);
  box.appendChild(top);

  box.appendChild(actorHeader(inc, grp, container));

  const claims = document.createElement('div');
  claims.className = 'grp-claims';
  (grp.claims || []).forEach(cl => claims.appendChild(claimRow(inc, grp, cl, container)));
  box.appendChild(claims);

  const add = document.createElement('button');
  add.className = 'grp-add grp-add-claim'; add.textContent = '+ Claim';
  add.onclick = () => {
    grp.claims = grp.claims || [];
    grp.claims.push(newClaim(grp));
    saveGroups(inc);
    buildGroupsUI(container, inc);
  };
  box.appendChild(add);
  return box;
}

// The three single-valued slots shared by every claim in the group. Dropping a
// value replaces whatever is there — these are scalars, so there is never more
// than one actor, one system or one developer.
function actorHeader(inc, grp, container) {
  const h = document.createElement('div');
  h.className = 'grp-sentence grp-head';
  const rebuild = () => { saveGroups(inc); buildGroupsUI(container, inc); };
  const omitted = (role) => (grp.omit || []).includes(role);

  // `onOmit` is what an *empty* optional clause offers: an × that takes the
  // clause out of this group's sentence, not a value out of a slot.
  const slot = (role, placeholder, onOmit) => {
    const span = document.createElement('span');
    span.className = 'sent-slot';
    const v = grp[role];
    if (!v) {
      const ph = document.createElement('span');
      ph.className = 'sent-ph';
      ph.style.color = roleInk(role);
      ph.textContent = `[${placeholder}]`;
      span.appendChild(ph);
      if (onOmit) {
        const x = document.createElement('button');
        x.className = 'sent-x sent-omit'; x.textContent = '×';
        x.title = `Drop "${placeholder}" from this group's sentence`;
        x.onclick = onOmit;
        span.appendChild(x);
      }
      return span;
    }
    span.appendChild(valueChip(role, v, () => {
      grp[role] = null;
      rebuild();
    }));
    return span;
  };

  h.appendChild(slot('actor', 'Actor'));
  // The whole thing reads as one sentence across two blocks: this header, then
  // each numbered claim under it. The comma after the actor is always there;
  // the one closing the clauses only if a clause actually rendered, so a group
  // that drops both reads "[Actor]," and not a stranded pair of commas.
  h.appendChild(document.createTextNode(','));
  // "using …" / "developed by …" appear once the incident has something to drop
  // there, or once they're filled; otherwise the header reads as complete. A
  // group that doesn't need one can also drop it outright — not every actor
  // context is about a named system, and an empty clause left standing reads as
  // an unanswered question rather than an inapplicable one.
  let anyClause = false;
  OPTIONAL_CLAIM_ROLES.forEach(cfg => {
    const available = ((inc.role_values || {})[cfg.role] || []).length;
    if (!grp[cfg.role] && (omitted(cfg.role) || !available)) return;
    anyClause = true;
    h.appendChild(document.createTextNode(cfg.lead));
    const sp = slot(cfg.role, cfg.placeholder, () => {
      grp.omit = (grp.omit || []).concat([cfg.role]);
      rebuild();
    });
    if (!grp[cfg.role]) sp.classList.add('opt');
    h.appendChild(sp);
  });
  if (anyClause) h.appendChild(document.createTextNode(','));

  // Bringing a dropped clause back. Only offered where there is something to put
  // in it, matching the rule for showing the clause in the first place.
  const restorable = OPTIONAL_CLAIM_ROLES.filter(cfg =>
    omitted(cfg.role) && !grp[cfg.role] && ((inc.role_values || {})[cfg.role] || []).length);
  restorable.forEach(cfg => {
    const b = document.createElement('button');
    b.className = 'sent-restore';
    b.textContent = '+ ' + cfg.placeholder;
    b.title = `Put "${cfg.lead.trim()} [${cfg.placeholder}]" back in this group's sentence`;
    b.onclick = () => { grp.omit = (grp.omit || []).filter(r => r !== cfg.role); rebuild(); };
    h.appendChild(b);
  });

  dropZone(h, GROUP_ROLES, (m) => {
    grp[m.role] = m.value;          // scalar: a drop replaces
    // Dropping into a clause the group had dropped is the coder saying they want
    // it after all, so the drop is never refused for having been put away.
    grp.omit = (grp.omit || []).filter(r => r !== m.role);
    rebuild();
  });
  return h;
}

// One claim: "allegedly contributed to <harm> affecting <party> because of
// <factors>." harm and
// party are single-valued; factors is a list, since several contributing causes
// for one harm read unambiguously.
function claimRow(inc, grp, cl, container) {
  const row = document.createElement('div');
  row.className = 'grp-sentence grp-claim';

  const num = document.createElement('span');
  num.className = 'claim-num'; num.textContent = grp.id + '.' + cl.id;
  row.appendChild(num);

  const rebuild = () => { saveGroups(inc); buildGroupsUI(container, inc); };

  // The one single-valued slot: a drop replaces whatever is there.
  const scalarSlot = (role, placeholder) => {
    const span = document.createElement('span');
    span.className = 'sent-slot';
    if (!cl[role]) {
      span.innerHTML = `<span class="sent-ph" style="color:${roleInk(role)}">`
                      + `[${escapeHtml(placeholder)}]</span>`;
      return span;
    }
    span.appendChild(valueChip(role, cl[role], () => { cl[role] = null; rebuild(); }));
    return span;
  };

  // A multi-valued slot: every value dropped in, joined by "&". Harmed parties
  // and factors both read as conjunctions, so they share this.
  const listSlot = (role, key, placeholder) => {
    const span = document.createElement('span');
    span.className = 'sent-slot';
    const vals = cl[key] || [];
    if (!vals.length) {
      span.innerHTML = `<span class="sent-ph" style="color:${roleInk(role)}">`
                      + `[${escapeHtml(placeholder)}]</span>`;
      return span;
    }
    vals.forEach((v, i) => {
      if (i) span.appendChild(document.createTextNode(' & '));
      span.appendChild(valueChip(role, v, () => {
        cl[key] = cl[key].filter(x => x !== v);
        rebuild();
      }));
    });
    return span;
  };

  row.appendChild(document.createTextNode('allegedly contributed to '));
  row.appendChild(scalarSlot('harm', 'harm'));
  row.appendChild(document.createTextNode(' affecting '));
  row.appendChild(listSlot('harmed_party', 'harmed_parties', 'harmed party/ies'));
  row.appendChild(document.createTextNode(' because of '));
  row.appendChild(listSlot('factor', 'factors', 'factor(s)'));
  row.appendChild(document.createTextNode('.'));

  const del = document.createElement('button');
  del.className = 'grp-del claim-del'; del.textContent = '×'; del.title = 'Delete claim';
  del.onclick = () => {
    grp.claims = grp.claims.filter(c => c !== cl);
    saveGroups(inc);
    buildGroupsUI(container, inc);
  };
  row.appendChild(del);

  dropZone(row, CLAIM_ROLES_DROP, (m) => {
    const key = CLAIM_LIST_KEYS[m.role];
    if (key) {                      // list: a drop adds, duplicates are ignored
      cl[key] = cl[key] || [];
      if (cl[key].includes(m.value)) return;
      cl[key].push(m.value);
    } else {
      cl[m.role] = m.value;         // scalar: a drop replaces
    }
    rebuild();
  });
  return row;
}

// A filled slot: the value plus a × that clears it.
function valueChip(role, value, onRemove) {
  const chip = document.createElement('span');
  chip.className = 'sent-v';
  chip.style.background = roleColor(role) + '33';
  chip.style.borderColor = roleColor(role);
  chip.appendChild(document.createTextNode(value));
  const x = document.createElement('button');
  x.className = 'sent-x'; x.textContent = '×'; x.title = 'Remove';
  x.onclick = onRemove;
  chip.appendChild(x);
  return chip;
}

// Wire an element as a drop target for a given set of roles. A chip of the wrong
// kind is refused rather than silently dropped somewhere it doesn't belong.
function dropZone(el, roles, apply) {
  el.ondragover = (e) => { e.preventDefault(); el.classList.add('over'); };
  el.ondragleave = () => el.classList.remove('over');
  el.ondrop = (e) => {
    e.preventDefault(); el.classList.remove('over');
    let m; try { m = JSON.parse(e.dataTransfer.getData('text/plain')); } catch (_) { return; }
    if (!m || !m.role || !m.value || !roles.includes(m.role)) return;
    apply(m);
  };
}

