// Claim groups: who did what to whom, built by dragging chips into a sentence.
//
// An actor context holds the claims made about it. Dropping a chip is the only
// way to fill a slot, so the drop zones below are where a claim actually gets
// made — everything else is the shape it gets made in.

import { escapeHtml } from '../persist.js';
import {
  CLAIM_LIST_KEYS,
  CLAIM_ROLES_DROP,
  GROUP_LIST_KEYS,
  GROUP_ROLES,
  OPTIONAL_CLAIM_ROLES,
  color,
  groupValues,
} from '../state.js';
import { roleColor, roleInk } from './card.js';
import { refreshDraggables } from './palette.js';
import { refreshComplete } from './signoff.js';

export async function saveGroups(inc) {
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

// The claim groups (right column) — each a fill-in-the-blank sentence + drop zone.
// Rebuilds itself on every change and persists per incident.
export function buildGroupsUI(container, inc) {
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
export function nextId(list) {
  return String(list.reduce((mx, x) => Math.max(mx, parseInt(x.id, 10) || 0), 0) + 1);
}

export function newClaim(grp) {
  // harmed_parties is plural — a single harm can land on several parties. The
  // singular harmed_party is the pre-plural field and must not be seeded here,
  // or every new claim carries a dead null nobody reads.
  return { id: nextId(grp.claims || []), harm: null, harmed_parties: [], factors: [] };
}

export function newGroup(inc) {
  const g = { id: nextId(inc.groups || []), actor: null, system: null, developer: null,
              claims: [], omit: [] };
  g.claims.push(newClaim(g));
  return g;
}

// A group is one actor context — "<actor> using <system> developed by <developer>"
// — with its claims listed underneath. The header and each claim row are separate
// drop zones, so a dragged chip's destination is never ambiguous: actor / system /
// developer land in the header, harm / harmed party / factor land in the claim you
// drop them on.
export function buildGroupBox(inc, grp, container) {
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

// The slots shared by every claim in the group. The actor is single — a second
// actor is a second context, so it gets its own group — while the systems it
// used and who developed them are lists, joined by "&" the way a claim's factors
// are. Dropping onto the actor replaces; dropping onto the others adds.
export function actorHeader(inc, grp, container) {
  const h = document.createElement('div');
  h.className = 'grp-sentence grp-head';
  const rebuild = () => { saveGroups(inc); buildGroupsUI(container, inc); };
  const omitted = (role) => (grp.omit || []).includes(role);

  // An empty slot: the placeholder, plus — for an optional clause — an × that
  // takes the clause out of this group's sentence rather than a value out of it.
  const emptySlot = (role, placeholder, onOmit) => {
    const span = document.createElement('span');
    span.className = 'sent-slot';
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
  };

  // The actor: one value, and a drop replaces it.
  const scalarSlot = (role, placeholder) => {
    const v = grp[role];
    if (!v) return emptySlot(role, placeholder);
    const span = document.createElement('span');
    span.className = 'sent-slot';
    span.appendChild(valueChip(role, v, () => { grp[role] = null; rebuild(); }));
    return span;
  };

  // Systems and developers: every value dropped in, joined by "&", each with its
  // own × — the same shape a claim's harmed parties and factors take.
  const listSlot = (role, key, placeholder, onOmit) => {
    const vals = groupValues(grp, role);
    if (!vals.length) return emptySlot(role, placeholder, onOmit);
    const span = document.createElement('span');
    span.className = 'sent-slot';
    vals.forEach((v, i) => {
      if (i) span.appendChild(document.createTextNode(' & '));
      span.appendChild(valueChip(role, v, () => {
        grp[key] = groupValues(grp, role).filter(x => x !== v);
        grp[role] = null;              // the pre-plural single value is spent
        rebuild();
      }));
    });
    return span;
  };

  h.appendChild(scalarSlot('actor', 'Actor'));
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
    const filled = groupValues(grp, cfg.role).length;
    const available = ((inc.role_values || {})[cfg.role] || []).length;
    if (!filled && (omitted(cfg.role) || !available)) return;
    anyClause = true;
    h.appendChild(document.createTextNode(cfg.lead));
    const sp = listSlot(cfg.role, cfg.key, cfg.placeholder, () => {
      grp.omit = (grp.omit || []).concat([cfg.role]);
      rebuild();
    });
    if (!filled) sp.classList.add('opt');
    h.appendChild(sp);
  });
  if (anyClause) h.appendChild(document.createTextNode(','));

  // Bringing a dropped clause back. Only offered where there is something to put
  // in it, matching the rule for showing the clause in the first place.
  const restorable = OPTIONAL_CLAIM_ROLES.filter(cfg =>
    omitted(cfg.role) && !groupValues(grp, cfg.role).length
    && ((inc.role_values || {})[cfg.role] || []).length);
  restorable.forEach(cfg => {
    const b = document.createElement('button');
    b.className = 'sent-restore';
    b.textContent = '+ ' + cfg.placeholder;
    b.title = `Put "${cfg.lead.trim()} [${cfg.placeholder}]" back in this group's sentence`;
    b.onclick = () => { grp.omit = (grp.omit || []).filter(r => r !== cfg.role); rebuild(); };
    h.appendChild(b);
  });

  dropZone(h, GROUP_ROLES, (m) => {
    const key = GROUP_LIST_KEYS[m.role];
    if (key) {                      // list: a drop adds, duplicates are ignored
      const vals = groupValues(grp, m.role);
      if (vals.includes(m.value)) return;
      grp[key] = vals.concat([m.value]);
      grp[m.role] = null;           // folded into the list; don't count it twice
    } else {
      grp[m.role] = m.value;        // the actor: a drop replaces
    }
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
export function claimRow(inc, grp, cl, container) {
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
export function valueChip(role, value, onRemove) {
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
export function dropZone(el, roles, apply) {
  el.ondragover = (e) => { e.preventDefault(); el.classList.add('over'); };
  el.ondragleave = () => el.classList.remove('over');
  el.ondrop = (e) => {
    e.preventDefault(); el.classList.remove('over');
    let m; try { m = JSON.parse(e.dataTransfer.getData('text/plain')); } catch (_) { return; }
    if (!m || !m.role || !m.value || !roles.includes(m.role)) return;
    apply(m);
  };
}
