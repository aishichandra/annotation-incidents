// Document reader.
// Renders the markdown, paints highlight underlines in lanes, and handles
// text selection: snapping spans, the tag menu, and the value picker.
//
// Loaded as a classic script: everything here shares one global scope with the
// other static/js files. See templates/index.html for the load order.

async function loadDoc(i) {
  const d = await (await fetch('/api/doc/' + i)).json();
  curDoc = { ...d, ann: d.annotation };
  // Characteristics are selected flat now (no claim linking in the doc view);
  // grouping into claims happens in the incident card view.
  if (!curDoc.ann.roles || typeof curDoc.ann.roles !== 'object') curDoc.ann.roles = {};
  ROLES.forEach(r => { if (!Array.isArray(curDoc.ann.roles[r.role])) curDoc.ann.roles[r.role] = []; });
  delete curDoc.ann.claims;
  // Auto-populate a fresh incident ID for a not-yet-coded article (the coder can
  // still pick an existing one to connect it to another article).
  await refreshIncidentIds();
  const idAnn = fieldAnn('incident_id');
  if (!(idAnn.answer && String(idAnn.answer).trim()) && INCIDENT_NEXT) {
    idAnn.answer = INCIDENT_NEXT;
    persist();
  }
  // If this article's incident ID matches an existing incident, adopt its title.
  if (idAnn.answer && fillTitleForIncident(idAnn.answer, false)) persist();
  armed = null;
  document.getElementById('docTitle').textContent = curDoc.title;
  const u = document.getElementById('docUrl');
  const hasUrl = curDoc.url && curDoc.url !== 'nan';
  u.textContent = hasUrl ? curDoc.url : ''; u.href = hasUrl ? curDoc.url : '#';
  u.style.display = hasUrl ? 'block' : 'none';
  updateArmHint();
  renderArticle();
  renderForm();
  window.scrollTo(0, 0);
}

function field(key) { return SCHEMA.find(f => f.key === key); }
function fieldAnn(key) {
  if (!curDoc.ann.fields[key]) curDoc.ann.fields[key] = { answer: null, comments: '' };
  return curDoc.ann.fields[key];
}
function quotesFor(key) { return curDoc.ann.quotes.filter(q => q.category === key); }

// ---------- article + highlights ----------
function getTextNodes() {
  const root = document.getElementById('text');
  const out = []; const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let n; while ((n = w.nextNode())) out.push(n);
  return out;
}

function renderArticle(keepScroll) {
  const y = window.scrollY;
  const el = document.getElementById('text');
  el.innerHTML = window.marked ? marked.parse(curDoc.markdown) : escapeHtml(curDoc.markdown);
  curDoc._plain = el.textContent;
  paintUnderlines();
  if (keepScroll) requestAnimationFrame(() => window.scrollTo(0, y));
}

function quoteColor(q) {
  return q.role ? ((ROLE[q.role] || {}).color || '#eee') : (color[q.category] || '#fde68a');
}
function quoteLabel(q) {
  const base = q.role ? ((ROLE[q.role] || {}).label || q.role)
                      : ((field(q.category) || {}).label || q.category);
  return base + (q.value ? ' · ' + q.value : '');
}

// Draw each quote's underline at its own fixed lane, so every category is one
// continuous parallel line at a stable height (lane 0 just under the text,
// higher lanes stacked below). All marks reserve the same padding so lines from
// different stretches align. `lines` = [{color, lane}]; totalLanes fixed per doc.
const UL_THICK = 2, UL_GAP = 2, UL_STEP = UL_THICK + UL_GAP;
function applyUnderlines(m, lines, totalLanes) {
  m.style.backgroundImage = lines.map(l => `linear-gradient(${l.color}, ${l.color})`).join(', ');
  m.style.backgroundSize = lines.map(() => `100% ${UL_THICK}px`).join(', ');
  m.style.backgroundPosition = lines.map(l => `0 calc(100% - ${(totalLanes - l.lane) * UL_STEP}px)`).join(', ');
  m.style.backgroundRepeat = 'no-repeat';
  m.style.paddingBottom = ((totalLanes + 1) * UL_STEP) + 'px';
}

// Assign a stable lane to each quote (greedy interval colouring): overlapping
// quotes get different lanes; non-overlapping ones reuse the lowest free lane.
function assignLanes(quotes) {
  const laneOf = {}, laneEnds = [];
  quotes.map((q, idx) => ({ q, idx }))
    .sort((a, b) => a.q.start - b.q.start || a.q.end - b.q.end)
    .forEach(({ q, idx }) => {
      let l = 0;
      while (l < laneEnds.length && laneEnds[l] > q.start) l++;
      laneOf[idx] = l;
      laneEnds[l] = q.end;
    });
  return { laneOf, totalLanes: laneEnds.length };
}

// Paint underlines per character-interval: split the text at every quote
// boundary, and under each stretch draw one line for EVERY quote covering it.
// So overlapping/adjacent selections keep their own lines and stack where they
// overlap — no highlight is ever hidden by another.
function paintUnderlines() {
  const quotes = curDoc.ann.quotes;
  if (!quotes.length) return;
  const { laneOf, totalLanes } = assignLanes(quotes);
  const cuts = [...new Set(quotes.flatMap(q => [q.start, q.end]))].sort((a, b) => a - b);
  const intervals = [];
  for (let k = 0; k < cuts.length - 1; k++) {
    const a = cuts[k], b = cuts[k + 1];
    const items = [];
    quotes.forEach((q, idx) => { if (q.start <= a && q.end >= b) items.push({ q, idx }); });
    if (items.length) intervals.push({ a, b, items });
  }
  intervals.forEach((iv, ii) => wrapInterval(iv, ii, laneOf, totalLanes));
}

function wrapInterval(iv, ii, laneOf, totalLanes) {
  const lines = iv.items.map(it => ({ color: quoteColor(it.q), lane: laneOf[it.idx] }));
  const idxs = iv.items.map(it => it.idx);
  const title = iv.items.map(it => quoteLabel(it.q)).join('  |  ');

  const segs = []; let acc = 0;
  for (const node of getTextNodes()) {
    const ns = acc; acc += node.textContent.length;
    if (node.parentElement && node.parentElement.closest('mark')) continue;
    const s = Math.max(iv.a, ns), e = Math.min(iv.b, acc);
    if (s < e) segs.push({ node, s: s - ns, e: e - ns });
  }
  segs.forEach(({ node, s, e }, si) => {
    const r = document.createRange(); r.setStart(node, s); r.setEnd(node, e);
    const m = document.createElement('mark');
    applyUnderlines(m, lines, totalLanes);
    m.dataset.idxs = idxs.join(' ');   // which quotes this stretch belongs to
    m.title = title;
    m.onclick = () => {
      if (skipSpanClick) return;   // a fresh selection just happened — let it tag
      showSpanMenu(iv.a, iv.b, m.getBoundingClientRect());
    };
    try { r.surroundContents(m); } catch (err) {}
  });
}

function textOffset(node, offset) {
  let total = 0;
  const w = document.createTreeWalker(document.getElementById('text'), NodeFilter.SHOW_TEXT);
  let n; while ((n = w.nextNode())) { if (n === node) return total + offset; total += n.textContent.length; }
  return total;
}

// If this selection is the same text as an already-highlighted quote at the same
// place, reuse that quote's exact span so a second category stacks its underline
// (rather than landing on a near-miss span that renders nothing).
function snapSpan(start, end, text) {
  const t = (text || '').trim();
  if (!t) return { start, end, text };
  const hit = curDoc.ann.quotes.find(q =>
    (q.text || '').trim() === t && q.start < end && start < q.end);   // overlap + same text
  return hit ? { start: hit.start, end: hit.end, text: hit.text } : { start, end, text };
}

document.getElementById('text').addEventListener('mouseup', () => {
  const sel = window.getSelection();
  if (!curDoc || sel.isCollapsed || !sel.toString().trim()) return;
  // Block the mark's click (which fires right after) from opening the span menu.
  skipSpanClick = true;
  setTimeout(() => { skipSpanClick = false; }, 0);
  const r = sel.getRangeAt(0);
  const rect = r.getBoundingClientRect();
  let start = textOffset(r.startContainer, r.startOffset);
  let end = textOffset(r.endContainer, r.endOffset);
  if (start > end) [start, end] = [end, start];
  let text = curDoc._plain.slice(start, end);
  ({ start, end, text } = snapSpan(start, end, text));
  sel.removeAllRanges();

  if (armed) {
    // arm-then-highlight: attach this highlight to the armed field/value
    const q = { text, start, end };
    if (armed.type === 'field') { q.category = armed.key; if (armed.value !== undefined) q.value = armed.value; }
    else { q.role = armed.role; q.value = armed.value; }
    curDoc.ann.quotes.push(q);
    persist(); renderArticle(true);
    if (armed.type === 'field') renderCard(armed.key); else renderRoles();
  } else {
    // highlight-first: choose which category this highlight belongs to
    showCategoryMenu({ start, end, text }, rect);
  }
});

// ---------- highlight-first: file a highlight under a category ----------
let catMenuEl = null;
// Groups the coder has opened in the value picker. Module-level, so it survives
// the menu being torn down and rebuilt after each assignment — otherwise every
// value would need its group re-opened. Starts empty: collapsed by default.
const valueGroupsOpen = new Set();
function closeCategoryMenu() {
  if (catMenuEl) {
    if (catMenuEl._onDown) document.removeEventListener('mousedown', catMenuEl._onDown);
    catMenuEl.remove();
    catMenuEl = null;
  }
}

// File the highlight under a category. `value` defaults to the highlighted text,
// but can be an existing option chosen from the value picker.
function assignHighlight(pending, target, value) {
  value = (value !== undefined ? value : pending.text).trim();
  if (!value) return;
  if (target.type === 'role') {
    if (!Array.isArray(curDoc.ann.roles[target.role])) curDoc.ann.roles[target.role] = [];
    if (!curDoc.ann.roles[target.role].includes(value)) curDoc.ann.roles[target.role].push(value);
    curDoc.ann.quotes.push({ role: target.role, value,
      text: pending.text, start: pending.start, end: pending.end });
  } else if ((field(target.key) || {}).type === 'multi') {
    const fa = fieldAnn(target.key);
    if (!Array.isArray(fa.answer)) fa.answer = fa.answer ? [fa.answer] : [];
    if (!fa.answer.includes(value)) fa.answer.push(value);
    curDoc.ann.quotes.push({ category: target.key, value,
      text: pending.text, start: pending.start, end: pending.end });
  } else {
    // Free-text field (e.g. aftermath): the highlight is justification only —
    // no multi-select value, so the field stays free text.
    curDoc.ann.quotes.push({ category: target.key,
      text: pending.text, start: pending.start, end: pending.end });
  }
  persist();
  renderArticle(true);
  renderForm();
}

// Does a quote sit on this span under this exact category/role?
function quoteMatchesTarget(q, target) {
  if (target.type === 'role') return q.role === target.role;
  return !q.role && q.category === target.key;
}
function spanTargetIdx(pending, target) {
  return curDoc.ann.quotes.findIndex(q =>
    q.start === pending.start && q.end === pending.end && quoteMatchesTarget(q, target));
}
// Toggle a category for a span: add it (text as value) if absent, else remove it.
function toggleTarget(pending, target) {
  const i = spanTargetIdx(pending, target);
  if (i >= 0) removeQuote(i);
  else assignHighlight(pending, target, pending.text);
}

function showCategoryMenu(pending, rect) {
  closeCategoryMenu();
  const menu = document.createElement('div');
  menu.className = 'cat-menu';
  catMenuEl = menu;

  const title = document.createElement('div');
  title.className = 'cat-title';
  const snip = pending.text.length > 42 ? pending.text.slice(0, 42) + '…' : pending.text;
  title.textContent = `Tag “${snip}” (pick one or more):`;
  menu.appendChild(title);

  const groupOf = (label, buttons) => {
    const grp = document.createElement('div'); grp.className = 'cat-group';
    const lbl = document.createElement('div'); lbl.className = 'cat-group-label'; lbl.textContent = label;
    const btns = document.createElement('div'); btns.className = 'cat-btns';
    buttons.forEach(b => btns.appendChild(b));
    grp.appendChild(lbl); grp.appendChild(btns);
    return grp;
  };
  // A category chip: click the label to use the highlighted text as the value,
  // click the ▾ caret to pick an existing value from the dropdown instead.
  const catChip = (target, label, dot) => {
    const on = spanTargetIdx(pending, target) >= 0;
    const wrap = document.createElement('span'); wrap.className = 'cat-btn-wrap';
    const b = document.createElement('button'); b.className = 'cat-btn' + (on ? ' on' : '');
    b.innerHTML = `<span class="cat-check">${on ? '✓' : ''}</span>` +
      (dot ? `<span class="cat-dot" style="background:${dot}"></span>` : '') + label;
    // Toggle this category, then reopen the menu so more can be added to the same text.
    b.onclick = () => { toggleTarget(pending, target); showCategoryMenu(pending, rect); };
    wrap.appendChild(b);
    // Value picker only for multi-value tags (roles + multi fields). Free-text
    // fields like aftermath are justification-only — no value dropdown.
    const isTextField = target.type === 'field' && (field(target.key) || {}).type !== 'multi';
    if (!isTextField) {
      const caret = document.createElement('button');
      caret.className = 'cat-caret'; caret.textContent = '▾'; caret.title = 'Pick an existing value';
      caret.onclick = () => showValuePicker(pending, target, rect);
      wrap.appendChild(caret);
    }
    return wrap;
  };

  // Tags offered on highlight, in a fixed order mixing fields and roles — no
  // group titles, just one flat list.
  const TAG_ORDER = [
    { type: 'role', role: 'system' },
    { type: 'role', role: 'developer' },
    { type: 'role', role: 'actor' },
    { type: 'role', role: 'factor' },
    { type: 'role', role: 'harm' },
    { type: 'role', role: 'harmed_party' },
    { type: 'field', key: 'incident_aftermath' },
  ];
  const btns = document.createElement('div'); btns.className = 'cat-btns';
  TAG_ORDER.forEach(t => {
    if (t.type === 'role') {
      const r = ROLE[t.role];
      if (r) btns.appendChild(catChip({ type: 'role', role: t.role }, r.label, r.color));
    } else {
      const f = field(t.key);
      if (f) btns.appendChild(catChip({ type: 'field', key: t.key }, f.label, color[t.key]));
    }
  });
  menu.appendChild(btns);

  const done = document.createElement('button');
  done.className = 'cat-done'; done.textContent = 'Done';
  done.onclick = () => closeCategoryMenu();
  menu.appendChild(done);

  document.body.appendChild(menu);
  positionMenu(menu, rect);
  addOutsideClose(menu);
}

function positionMenu(menu, rect) {
  const mw = menu.offsetWidth, mh = menu.offsetHeight;
  let left = rect.left, top = rect.bottom + 6;
  if (left + mw > window.innerWidth - 8) left = window.innerWidth - mw - 8;
  if (top + mh > window.innerHeight - 8) top = rect.top - mh - 6;
  menu.style.left = Math.max(8, left) + 'px';
  menu.style.top = Math.max(8, top) + 'px';
}
function addOutsideClose(menu) {
  setTimeout(() => {
    const onDown = (e) => { if (!e.target.closest('.cat-menu')) closeCategoryMenu(); };
    document.addEventListener('mousedown', onDown);
    menu._onDown = onDown;
  }, 0);
}

// A category's existing values: its schema options plus any already-selected values.
function valueOptions(target) {
  let opts = [], selected = [];
  if (target.type === 'role') {
    opts = roleOptions(target.role).slice();
    if (Array.isArray(curDoc.ann.roles[target.role])) selected = curDoc.ann.roles[target.role];
  } else {
    const f = field(target.key);
    opts = ((f && f.options) || []).slice();
    const fa = curDoc.ann.fields[target.key];
    if (fa && Array.isArray(fa.answer)) selected = fa.answer;
  }
  const seen = new Set(), out = [];
  [...opts, ...selected].forEach(v => { if (v && !seen.has(v)) { seen.add(v); out.push(v); } });
  return out;
}

// Second step of the highlight menu: pick which value the highlight pertains to.
function showValuePicker(pending, target, rect) {
  closeCategoryMenu();
  const menu = document.createElement('div');
  menu.className = 'cat-menu';
  catMenuEl = menu;

  const label = target.type === 'role'
    ? `${(ROLE[target.role] || {}).label}`
    : `${(field(target.key) || {}).label}`;
  const title = document.createElement('div');
  title.className = 'cat-title'; title.textContent = `${label} — which value?`;
  menu.appendChild(title);

  const list = document.createElement('div'); list.className = 'cat-vlist';
  const snip = pending.text.length > 30 ? pending.text.slice(0, 30) + '…' : pending.text;
  const useText = document.createElement('button');
  useText.className = 'cat-vopt cat-vopt-text';
  useText.textContent = `＋ Use “${snip}”`;
  useText.onclick = () => { assignHighlight(pending, target, pending.text); showCategoryMenu(pending, rect); };

  // Same grouping (and same collapsed-by-default behaviour) as the multiselect,
  // so a value sits in the same place whichever route you reach it by.
  const groups = target.type === 'role'
    ? roleGroups(target.role) : ((field(target.key) || {}).groups || null);
  const fillList = () => {
    list.innerHTML = '';
    list.appendChild(useText);
    groupedOptions(valueOptions(target), groups).forEach(section => {
      if (section.label) {
        list.appendChild(groupHeader(section, valueGroupsOpen, fillList, 0));
        if (!valueGroupsOpen.has(section.label)) return;
      }
      section.options.forEach(o => {
        const b = document.createElement('button');
        b.className = 'cat-vopt' + (section.label ? ' in-group' : '');
        b.textContent = o;
        b.onclick = () => { assignHighlight(pending, target, o); showCategoryMenu(pending, rect); };
        list.appendChild(b);
      });
    });
  };
  fillList();
  menu.appendChild(list);

  const back = document.createElement('button');
  back.className = 'cat-back'; back.textContent = '← Back';
  back.onclick = () => showCategoryMenu(pending, rect);
  menu.appendChild(back);

  document.body.appendChild(menu);
  positionMenu(menu, rect);
  addOutsideClose(menu);
}

// Click a highlight to see every category covering that stretch of text; remove
// them one at a time, add more, or clear them all. Takes the clicked interval
// [a,b) and recomputes covering quotes fresh (robust to index shifts on remove).
function showSpanMenu(a, b, rect) {
  closeCategoryMenu();
  const items = curDoc.ann.quotes
    .map((q, idx) => ({ q, idx }))
    .filter(it => it.q.start <= a && it.q.end >= b);
  if (!items.length) return;
  const menu = document.createElement('div');
  menu.className = 'cat-menu';
  catMenuEl = menu;

  const text = items[0].q.text || '';
  const title = document.createElement('div');
  title.className = 'cat-title';
  const snip = text.length > 34 ? text.slice(0, 34) + '…' : text;
  title.textContent = `Categories on “${snip}”`;
  menu.appendChild(title);

  items.forEach(it => {
    const row = document.createElement('div'); row.className = 'span-row';
    const dot = document.createElement('span'); dot.className = 'cat-dot';
    dot.style.background = quoteColor(it.q);
    const name = document.createElement('span'); name.className = 'span-name';
    name.textContent = quoteLabel(it.q);
    const x = document.createElement('button'); x.className = 'span-x';
    x.textContent = '✕'; x.title = 'Remove this category';
    x.onclick = () => { removeQuote(it.idx); showSpanMenu(a, b, rect); };  // reopen with the rest
    row.appendChild(dot); row.appendChild(name); row.appendChild(x);
    menu.appendChild(row);
  });

  const add = document.createElement('button');
  add.className = 'cat-goto'; add.textContent = '＋ Add another category';
  add.onclick = () => showCategoryMenu({ start: items[0].q.start, end: items[0].q.end, text }, rect);
  menu.appendChild(add);

  const del = document.createElement('button');
  del.className = 'cat-del'; del.textContent = '✕ Remove all here';
  del.onclick = () => {
    curDoc.ann.quotes.map((q, idx) => ({ q, idx }))
      .filter(it => it.q.start <= a && it.q.end >= b)
      .map(it => it.idx).sort((x, y) => y - x)   // remove high indices first
      .forEach(idx => removeQuote(idx));
    closeCategoryMenu();
  };
  menu.appendChild(del);

  document.body.appendChild(menu);
  positionMenu(menu, rect);
  addOutsideClose(menu);
}
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeCategoryMenu(); });

// Drop a selected value from its field answer / claim role.
function removeValue(q) {
  if (q.role) {
    const arr = curDoc.ann.roles[q.role];
    if (Array.isArray(arr)) {
      const i = arr.indexOf(q.value);
      if (i >= 0) arr.splice(i, 1);
    }
  } else {
    const fa = curDoc.ann.fields[q.category];
    if (fa && Array.isArray(fa.answer)) {
      const i = fa.answer.indexOf(q.value);
      if (i >= 0) fa.answer.splice(i, 1);
    }
  }
}

function removeQuote(globalIdx) {
  const q = curDoc.ann.quotes[globalIdx];
  curDoc.ann.quotes.splice(globalIdx, 1);
  // If this was the last highlight justifying its selected value, drop the value too.
  if (q && q.value !== undefined) {
    const stillJustified = q.role
      ? curDoc.ann.quotes.some(x => x.role === q.role && x.value === q.value)
      : curDoc.ann.quotes.some(x => x.category === q.category && x.value === q.value);
    if (!stillJustified) {
      removeValue(q);
      if (armed && sameArm(q.role
        ? { type: 'role', role: q.role, value: q.value }
        : { type: 'field', key: q.category, value: q.value })) {
        armed = null;
        updateArmHint();
      }
    }
  }
  persist();
  renderArticle(true);
  if (q.role) renderRoles(); else renderCard(q.category);
}

