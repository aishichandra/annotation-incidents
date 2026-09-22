// Claims: who did what to whom, built by dragging chips into a sentence.
//
// Dropping a chip is the only way to fill a slot, so the drop zone on each
// claim is where the claim actually gets made — everything else is the shape
// it gets made in. Every claim is flat and self-contained: no shared context
// between claims, so a second harm, actor, or anything else is simply a
// second claim.

import { escapeHtml } from '../persist.js';
import {
  CLAIM_LIST_KEYS,
  CLAIM_ROLES_DROP,
  GEO,
  GEO_ROLES,
  OPTIONAL_CLAIM_ROLES,
  ROLE,
  ROLE_GEO_ALL,
  claimValues,
} from '../state.js';
import { roleColor, roleInk } from './card.js';
import { refreshDraggables } from './palette.js';
import { refreshComplete } from './signoff.js';

export async function saveClaims(inc) {
  try {
    await fetch('/api/incident/' + encodeURIComponent(inc.incident_id) + '/claims', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ claims: inc.claims }),
    });
  } catch (e) { /* non-fatal: local drag state stays until reload */ }
  // Editing the claims invalidates any sign-off — the server does this in
  // clear_signoff(); reflect it here so the card can't keep claiming complete.
  if (inc.status === 'complete') { inc.status = ''; inc.completed_at = ''; }
  refreshComplete(inc);
}

// The claims (right column) — each a fill-in-the-blank sentence + drop zone.
// Rebuilds itself on every change and persists per incident.
export function buildClaimsUI(container, inc) {
  if (!inc) return;
  // Always start with one empty claim. Empty claims aren't saved server-side,
  // so this just seeds the template each load.
  if (!inc.claims.length) inc.claims.push(newClaim(inc));
  container.innerHTML = '';

  const wrap = document.createElement('div');
  wrap.className = 'tow-field';
  wrap.innerHTML = `<div class="tow-label">Claims</div>`;
  inc.claims.forEach(cl => wrap.appendChild(claimBox(inc, cl, container)));

  const add = document.createElement('button');
  add.className = 'grp-add'; add.textContent = '+ New claim';
  add.onclick = () => {
    inc.claims.push(newClaim(inc));
    saveClaims(inc);
    buildClaimsUI(container, inc);
  };
  wrap.appendChild(add);
  container.appendChild(wrap);
  refreshDraggables(inc);   // keep the chips' claim marks in step with the claims
}

// Ids are per-incident counters.
export function nextId(list) {
  return String(list.reduce((mx, x) => Math.max(mx, parseInt(x.id, 10) || 0), 0) + 1);
}

export function newClaim(inc) {
  // harmed_parties, systems, developers and factors are plural — several
  // parties can share one harm, and one actor can run on several systems
  // built by more than one developer. Seeded as empty lists directly, so a
  // fresh claim carries no dead null nobody reads.
  return { id: nextId(inc.claims || []), harm: null, harmed_parties: [], actor: null,
           systems: [], developers: [], factors: [], omit: [] };
}

// One claim's box: its own delete button, and the sentence beneath it.
export function claimBox(inc, cl, container) {
  const box = document.createElement('div');
  box.className = 'grp-box';

  const top = document.createElement('div');
  top.className = 'grp-top';
  top.innerHTML = `<span class="grp-name">Claim ${escapeHtml(cl.id)}</span>`;
  const del = document.createElement('button');
  del.className = 'grp-del'; del.textContent = '×';
  del.title = 'Delete this claim';
  del.onclick = () => {
    inc.claims = inc.claims.filter(c => c !== cl);
    saveClaims(inc);
    buildClaimsUI(container, inc);
  };
  top.appendChild(del);
  box.appendChild(top);

  box.appendChild(claimRow(inc, cl, container));
  return box;
}

// One claim, read as a single sentence: "<harm> allegedly impacted <harmed
// parties> in part as a result of actions taken by <actor> using <factors>"
// plus whichever of "developed by <developer>" / "because of <system>" this
// claim has something to say — both optional. harm and actor are
// single-valued; harmed parties, factors, systems and developers are lists,
// since several contributing causes, or several systems/developers, read
// unambiguously as a conjunction.
export function claimRow(inc, cl, container) {
  const row = document.createElement('div');
  row.className = 'grp-sentence';

  const rebuild = () => { saveClaims(inc); buildClaimsUI(container, inc); };
  const omitted = (role) => (cl.omit || []).includes(role);

  // An empty slot: the placeholder, plus — for an optional clause — an × that
  // takes the clause out of this claim's sentence rather than a value out of it.
  const emptySlot = (role, placeholder, onOmit) => {
    const span = document.createElement('span');
    span.className = 'sent-slot';
    const ph = document.createElement('span');
    ph.className = 'sent-ph'; ph.style.color = roleInk(role);
    ph.textContent = `[${placeholder}]`;
    span.appendChild(ph);
    if (onOmit) {
      const x = document.createElement('button');
      x.className = 'sent-x sent-omit'; x.textContent = '×';
      x.title = `Drop "${placeholder}" from this claim`;
      x.onclick = onOmit;
      span.appendChild(x);
    }
    return span;
  };

  // A single-valued slot (harm, actor). A drop replaces whatever is there;
  // there is no omit, since each is what keeps a claim one countable
  // proposition naming one thing.
  const scalarSlot = (role, placeholder) => {
    const v = cl[role];
    if (!v) return emptySlot(role, placeholder);
    const span = document.createElement('span');
    span.className = 'sent-slot';
    const geo = GEO_ROLES.has(role) ? valueGeo(inc, role, v, rebuild) : null;
    span.appendChild(valueChip(role, v, () => { cl[role] = null; rebuild(); }, geo));
    return span;
  };

  // A list-valued slot — every value dropped in, joined by "&", each with its
  // own ×. `onOmit`, when given, makes an empty slot optional rather than an
  // unanswered question (harmed_party and factor are always required, so
  // never pass one for them; system and developer always do).
  const listSlot = (role, placeholder, onOmit) => {
    const vals = claimValues(cl, role);
    if (!vals.length) {
      const sp = emptySlot(role, placeholder, onOmit);
      if (onOmit) sp.classList.add('opt');
      return sp;
    }
    const span = document.createElement('span');
    span.className = 'sent-slot';
    vals.forEach((v, i) => {
      if (i) span.appendChild(document.createTextNode(' & '));
      const geo = GEO_ROLES.has(role) ? valueGeo(inc, role, v, rebuild) : null;
      span.appendChild(valueChip(role, v, () => {
        cl[CLAIM_LIST_KEYS[role]] = claimValues(cl, role).filter(x => x !== v);
        cl[role] = null;              // the pre-plural single value is spent
        rebuild();
      }, geo));
    });
    return span;
  };

  row.appendChild(scalarSlot('harm', 'harm'));
  row.appendChild(document.createTextNode(' allegedly impacted '));
  row.appendChild(listSlot('harmed_party', 'harmed party/ies'));
  row.appendChild(document.createTextNode(' in part as a result of actions taken by '));
  row.appendChild(scalarSlot('actor', 'Actor'));
  row.appendChild(document.createTextNode(' using '));
  row.appendChild(listSlot('factor', 'factor(s)'));

  // "developed by …" / "because of …" appear once the incident has something
  // to drop there, or once they're filled; otherwise the sentence reads as
  // complete without them. A claim that doesn't need one can also drop it
  // outright — not every claim is about a named system, and an empty clause
  // left standing reads as an unanswered question rather than an
  // inapplicable one.
  OPTIONAL_CLAIM_ROLES.forEach(cfg => {
    const filled = claimValues(cl, cfg.role).length;
    const available = ((inc.role_values || {})[cfg.role] || []).length;
    if (!filled && (omitted(cfg.role) || !available)) return;
    row.appendChild(document.createTextNode(cfg.lead));
    row.appendChild(listSlot(cfg.role, cfg.placeholder, () => {
      cl.omit = (cl.omit || []).concat([cfg.role]);
      rebuild();
    }));
  });
  row.appendChild(document.createTextNode('.'));

  // Bringing a dropped clause back. Only offered where there is something to
  // put in it, matching the rule for showing the clause in the first place.
  const restorable = OPTIONAL_CLAIM_ROLES.filter(cfg =>
    omitted(cfg.role) && !claimValues(cl, cfg.role).length
    && ((inc.role_values || {})[cfg.role] || []).length);
  restorable.forEach(cfg => {
    const b = document.createElement('button');
    b.className = 'sent-restore';
    b.textContent = '+ ' + cfg.placeholder;
    b.title = `Put "${cfg.lead.trim()} [${cfg.placeholder}]" back`;
    b.onclick = () => { cl.omit = (cl.omit || []).filter(r => r !== cfg.role); rebuild(); };
    row.appendChild(b);
  });

  dropZone(row, CLAIM_ROLES_DROP, (m) => {
    const key = CLAIM_LIST_KEYS[m.role];
    if (key) {                      // list: a drop adds, duplicates are ignored
      const vals = claimValues(cl, m.role);
      if (vals.includes(m.value)) return;
      cl[key] = vals.concat([m.value]);
      cl[m.role] = null;            // folded into the list; don't count it twice
      // Dropping into a clause the claim had dropped is the coder saying they
      // want it after all, so the drop is never refused for having been put away.
      cl.omit = (cl.omit || []).filter(r => r !== m.role);
    } else {
      cl[m.role] = m.value;         // scalar (harm, actor): a drop replaces
    }
    rebuild();
  });
  return row;
}

// A characteristic's geography hooks:
// {locations, inherited, wide, onAdd, onRemove, onRemoveWide}.
// Read and written on `inc.role_geo[role][value]` — pooled once for the whole
// incident, not per claim — so dropping a location on any appearance of a
// value tags the value itself: the same "Journalists" cited in two different
// claims reads with the same location on both. `rebuild` re-renders every
// claim, which is what carries the change to every other appearance of the
// same value.
//
// A place can also apply to a whole role rather than one value in it — every
// actor, every harmed party — which is how a document says it: you highlight a
// country and choose what it applies to. Those sit under ROLE_GEO_ALL, and read
// on every chip of that role alongside the value's own. `wide` is the ones among
// `locations` that came that way, since taking one off means taking it off the
// whole role, and the × says so.
//
// A value nobody has dropped a place onto isn't "nowhere" — it defaults to
// this incident's own Geography/location answer, on the reading that an
// incident's characteristics are where the incident is unless a coder says
// otherwise for one of them specifically. `locations` is only ever the
// explicit tags; `inherited` is true exactly when there aren't any and the
// incident's own answer is standing in for them.
function valueGeo(inc, role, value, rebuild) {
  const held = (key) => (((inc.role_geo || {})[role] || {})[key]) || [];
  const incidentGeo = () => (inc.field_values || {}).incident_geography || [];
  // `key` is the value the places are filed under, or ROLE_GEO_ALL for the role.
  const persist = (key, locations) => {
    inc.role_geo = inc.role_geo || {};
    inc.role_geo[role] = inc.role_geo[role] || {};
    if (locations.length) inc.role_geo[role][key] = locations;
    else delete inc.role_geo[role][key];
    fetch('/api/incident/' + encodeURIComponent(inc.incident_id) + '/geo', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ role, value: key, locations }),
    }).catch(() => { /* non-fatal: local state stays until reload */ });
    rebuild();
  };
  const own = held(value);
  const wide = held(ROLE_GEO_ALL).filter((l) => !own.includes(l));
  const tagged = own.concat(wide);
  return {
    locations: tagged.length ? tagged : incidentGeo(),
    inherited: !tagged.length,
    wide,
    onAdd: (loc) => {
      const cur = held(value);
      if (!cur.includes(loc)) persist(value, cur.concat([loc]));
    },
    onRemove: (loc) => persist(value, held(value).filter((l) => l !== loc)),
    onRemoveWide: (loc) => persist(ROLE_GEO_ALL, held(ROLE_GEO_ALL).filter((l) => l !== loc)),
  };
}

// A filled slot: the value, an optional × per attached location, and a × that
// clears the value itself. `geo` is only passed for actor / system / developer
// / harmed party — harm and factor don't take one, so their chips render
// exactly as before and accept no drop.
export function valueChip(role, value, onRemove, geo) {
  const roleName = ((ROLE[role] || {}).label || role).toLowerCase();
  const chip = document.createElement('span');
  chip.className = 'sent-v';
  chip.style.background = roleColor(role) + '33';
  chip.style.borderColor = roleColor(role);
  chip.appendChild(document.createTextNode(value));
  if (geo) {
    geo.locations.forEach((loc) => {
      const g = document.createElement('span');
      g.className = 'sent-geo' + (geo.inherited ? ' sent-geo-inherited' : '');
      g.style.borderColor = GEO.color;
      g.appendChild(document.createTextNode(loc));
      if (geo.inherited) {
        // Nothing to remove — there's no tag on this value to take off, just
        // this incident's own Geography/location standing in for one. Dropping
        // a place here replaces the inheritance with a real tag, same as usual.
        g.title = 'From this incident’s Geography/location — drop a place '
                + 'here to say this one is different';
      } else {
        // A place that applies to the whole role reads the same on every chip of
        // it, so its × comes off all of them — said here rather than discovered.
        const wide = geo.wide.includes(loc);
        if (wide) g.title = `Applies to every ${roleName}`;
        const gx = document.createElement('button');
        gx.className = 'sent-x sent-geo-x'; gx.textContent = '×';
        gx.title = wide ? `Remove ${loc} from every ${roleName}` : `Remove ${loc}`;
        gx.onclick = (e) => {
          e.stopPropagation();
          if (wide) geo.onRemoveWide(loc); else geo.onRemove(loc);
        };
        g.appendChild(gx);
      }
      chip.appendChild(g);
    });
    // Its own drop target rather than dropZone() — a geography drop adds to
    // *this value's* locations, which the claim-level drop zone (a different
    // set of roles entirely) has no way to express, and stopPropagation keeps
    // it from also reaching that outer zone.
    chip.ondragover = (e) => { e.preventDefault(); e.stopPropagation(); chip.classList.add('geo-over'); };
    chip.ondragleave = () => chip.classList.remove('geo-over');
    chip.ondrop = (e) => {
      e.preventDefault(); e.stopPropagation(); chip.classList.remove('geo-over');
      let m; try { m = JSON.parse(e.dataTransfer.getData('text/plain')); } catch (_) { return; }
      if (!m || m.role !== GEO.role || !m.value) return;
      geo.onAdd(m.value);
    };
  }
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
