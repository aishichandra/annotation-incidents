// Coding form.
// The right-hand sidebar: one card per schema field, selects, text inputs,
// incident id lookup, and the evidence/quote lists under each value.

import { armField, buildRolesPanel, scrollToMark } from './arming.js';
import { escapeHtml, persist, persistSoon } from './persist.js';
import { field, fieldAnn, removeQuote, renderArticle } from './reader.js';
import {
  SCHEMA,
  attachDefTip,
  color,
  curDoc,
  groupHeader,
  groupedOptions,
  hideDefTip,
  sameArm,
  setArm,
} from './state.js';
import { INCIDENT_IDS, fillTitleForIncident, refreshIncidentIds } from './sync.js';

// ---------- coding form ----------
export function renderForm() {
  const root = document.getElementById('form');
  root.innerHTML = '';
  // Characteristics panel (flat actor/harm/factor/harmed-party) sits just above
  // the Incident aftermath card.
  //
  // `card_only` fields are skipped: Geography and Translated are answered once
  // for the incident, on its card. This sidebar codes a document, and offering
  // them here would ask the same question of every article in an incident.
  SCHEMA.filter(f => !f.card_only).forEach(f => {
    if (f.key === 'incident_aftermath') root.appendChild(buildRolesPanel());
    root.appendChild(buildCard(f));
  });
  if (!SCHEMA.some(f => f.key === 'incident_aftermath')) root.appendChild(buildRolesPanel());
}

// rebuild a single card in place (keeps the rest of the form untouched)
export function renderCard(key) {
  const old = document.querySelector(`.card[data-key="${key}"]`);
  if (old) old.replaceWith(buildCard(field(key)));
}

export function subLabel(text) {
  const d = document.createElement('div'); d.className = 'sub'; d.textContent = text; return d;
}

export function buildCard(f) {
  const isMulti = f.type === 'multi';
  const canJustify = f.justify !== false;
  const fieldArmed = !isMulti && canJustify && sameArm({ type: 'field', key: f.key, value: undefined });

  const card = document.createElement('div');
  card.className = 'card' + (fieldArmed ? ' armed' : '');
  card.dataset.key = f.key;

  const head = document.createElement('div');
  head.className = 'head';
  head.innerHTML = canJustify
    ? `<span class="sq" style="background:${color[f.key]}"></span>` +
      `<span class="label">${f.label}</span>` +
      // multiselects justify per selection, so no whole-field arm here
      (isMulti ? '' : `<span class="arm">${fieldArmed ? 'highlighting' : 'highlight'}</span>`)
    : `<span class="label">${f.label}</span>`;
  if (canJustify && !isMulti) head.onclick = () => armField(f.key, undefined);
  else head.style.cursor = 'default';
  card.appendChild(head);

  const body = document.createElement('div');
  body.className = 'body';
  const fa = fieldAnn(f.key);

  if (f.key === 'incident_id') {
    body.appendChild(buildIncidentId(fa));
  } else if (!isMulti) {
    body.appendChild(buildText(fa, 'answer', false));
    if (canJustify) {
      body.appendChild(subLabel('Justification (highlighted quotes)'));
      body.appendChild(buildQuotes(f.key));
    }
  } else {
    // multiselect + per-selection justification
    if (!Array.isArray(fa.answer)) fa.answer = fa.answer ? [fa.answer] : [];
    const evBox = document.createElement('div');
    const refreshEv = () => {
      evBox.innerHTML = '';
      evBox.appendChild(buildValueEvidence(fa.answer, {
        color: color[f.key],
        armTarget: (v) => ({ type: 'field', key: f.key, value: v }),
        getQuotes: (v) => curDoc.ann.quotes.map((q, gi) => ({ q, gi }))
          .filter(x => x.q.category === f.key && x.q.value === v),
      }));
    };
    const select = buildSelect({
      options: f.options || [],
      groups: f.groups || null,
      definitions: f.definitions || null,
      accent: color[f.key],
      selected: fa.answer,
      onChange: () => { persistSoon(); refreshEv(); },
      onRemoveValue: (v) => {
        curDoc.ann.quotes = curDoc.ann.quotes.filter(q => !(q.category === f.key && q.value === v));
        renderArticle(true);
      },
      onAdd: async (val) => {
        const res = await fetch('/api/schema/option', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ field: f.key, option: val }),
        });
        field(f.key).options = (await res.json()).options;
        return field(f.key).options;
      },
    });
    body.appendChild(select);
    body.appendChild(subLabel('Justification (highlight each selection)'));
    body.appendChild(evBox);
    refreshEv();
  }

  if (f.comments !== false) {
    body.appendChild(subLabel('Additional comments'));
    body.appendChild(buildText(fa, 'comments', true));
  }

  card.appendChild(body);
  return card;
}

// Per-selection justification: each selected value gets its own arm + highlights,
// and is flagged "needs evidence" until it has at least one highlight.
export function buildValueEvidence(values, opts) {
  const wrap = document.createElement('div');
  wrap.className = 'value-ev';
  if (!values.length) {
    const none = document.createElement('div');
    none.className = 'ev-none'; none.textContent = 'No selections yet.';
    wrap.appendChild(none);
    return wrap;
  }
  values.forEach(v => {
    const t = opts.armTarget(v);
    const on = sameArm(t);
    const qs = opts.getQuotes(v);
    const row = document.createElement('div');
    row.className = 'ev-row' + (on ? ' armed' : '');
    row.dataset.arm = JSON.stringify(t);

    const head = document.createElement('div');
    head.className = 'ev-head';
    const flag = qs.length
      ? `<span class="ev-count">${qs.length} quote${qs.length === 1 ? '' : 's'}</span>`
      : `<span class="ev-warn">⚠ needs evidence</span>`;
    head.innerHTML =
      `<span class="ev-dot" style="background:${opts.color}"></span>` +
      `<span class="ev-val">${escapeHtml(v)}</span>` + flag +
      `<span class="ev-arm">${on ? 'highlighting' : 'highlight'}</span>`;
    head.querySelector('.ev-arm').onclick = () => setArm(t);
    row.appendChild(head);

    if (qs.length) {
      const q = document.createElement('div');
      q.className = 'quotes';
      qs.forEach(({ q: qq, gi }) => {
        const el = document.createElement('div');
        el.className = 'quote';
        el.style.borderLeftColor = opts.color;
        el.innerHTML = `“${escapeHtml(qq.text.slice(0, 180))}”<button class="x" title="Remove">×</button>`;
        el.onclick = (e) => { if (e.target.classList.contains('x')) return; scrollToMark(gi); };
        el.querySelector('.x').onclick = (e) => { e.stopPropagation(); removeQuote(gi); };
        q.appendChild(el);
      });
      row.appendChild(q);
    }
    wrap.appendChild(row);
  });
  return wrap;
}

// settable free text: type + Enter sets it to plain, settled text;
// click the text to edit, × to clear. For multiline, Shift+Enter adds a newline.
export function buildText(fa, prop, multiline, placeholder) {
  const wrap = document.createElement('div');

  function render(focus) {
    wrap.innerHTML = '';
    const val = typeof fa[prop] === 'string' ? fa[prop].trim() : '';
    const editing = wrap.dataset.editing === '1';

    if (val && !editing) {
      const box = document.createElement('div');
      box.className = 'text-answer';
      box.innerHTML = `<span class="val">${escapeHtml(val)}</span><button class="x" title="Clear">×</button>`;
      box.querySelector('.x').onclick = (e) => {
        e.stopPropagation();
        fa[prop] = ''; wrap.dataset.editing = ''; persist(); render(true);
      };
      box.onclick = (e) => {
        if (e.target.classList.contains('x')) return;
        wrap.dataset.editing = '1'; render(true);
      };
      wrap.appendChild(box);
    } else {
      const inp = document.createElement(multiline ? 'textarea' : 'input');
      if (multiline) { inp.className = 'comments'; inp.rows = 2; } else { inp.type = 'text'; }
      if (placeholder) inp.placeholder = placeholder;
      inp.value = fa[prop] || '';
      inp.oninput = () => { fa[prop] = inp.value; persistSoon(); };
      inp.onkeydown = (e) => {
        if (e.key === 'Enter' && !(multiline && e.shiftKey)) {
          e.preventDefault(); wrap.dataset.editing = ''; render(false); persist();
        }
      };
      inp.onblur = () => {
        if (!document.body.contains(inp)) return;   // ignore blur from our own re-render
        if ((inp.value || '').trim()) { wrap.dataset.editing = ''; render(false); persistSoon(); }
      };
      wrap.appendChild(inp);
      if (focus) inp.focus();
    }
  }

  render(false);
  return wrap;
}

// Incident ID: an editable text box (auto-populated with a fresh ID), plus a
// dropdown to connect this article to an existing incident, and a "New ID" button.
export function buildIncidentId(fa) {
  const wrap = document.createElement('div');
  wrap.className = 'incident-id';

  const inp = document.createElement('input');
  inp.type = 'text'; inp.className = 'inc-input'; inp.value = fa.answer || '';
  inp.oninput = () => { fa.answer = inp.value; persistSoon(); };
  inp.onblur = () => {
    fa.answer = (inp.value || '').trim(); inp.value = fa.answer;
    if (fillTitleForIncident(fa.answer, false)) renderCard('incident_title');
    persistSoon();
  };
  wrap.appendChild(inp);

  const row = document.createElement('div'); row.className = 'inc-row';

  const sel = document.createElement('select'); sel.className = 'inc-select';
  const ph = document.createElement('option');
  ph.value = ''; ph.textContent = 'Connect to existing incident…';
  sel.appendChild(ph);
  INCIDENT_IDS.forEach(id => {
    const o = document.createElement('option'); o.value = id; o.textContent = id;
    if (id === fa.answer) o.selected = true;
    sel.appendChild(o);
  });
  sel.onchange = () => {
    if (!sel.value) return;
    fa.answer = sel.value; inp.value = sel.value;
    // Connecting to an existing incident adopts its title.
    if (fillTitleForIncident(sel.value, true)) renderCard('incident_title');
    persist();
  };
  row.appendChild(sel);

  const nb = document.createElement('button');
  nb.type = 'button'; nb.className = 'inc-new'; nb.textContent = 'New ID';
  nb.title = 'Assign a fresh incident ID';
  nb.onclick = async () => {
    await refreshIncidentIds();
    if (INCIDENT_NEXT) { fa.answer = INCIDENT_NEXT; inp.value = INCIDENT_NEXT; persist(); }
    renderCard('incident_id');   // refresh the picker with the new value selected
  };
  row.appendChild(nb);

  wrap.appendChild(row);
  return wrap;
}

// Generic multiselect dropdown with tags + "add option". `selected` is an array
// mutated in place; onChange fires after any change; onAdd (optional) persists a
// new option and returns the updated options list. `definitions` (optional) is
// the codebook, {option: text}, shown on hover in `accent`'s colour.
export function buildSelect(cfg) {
  const selected = cfg.selected;
  let options = cfg.options || [];
  const sel = document.createElement('div');
  sel.className = 'select';

  const control = document.createElement('div');
  control.className = 'select-control';
  control.onclick = (e) => {
    if (e.target.closest('.select-tag button')) return;
    const willOpen = !sel.classList.contains('open');
    document.querySelectorAll('.select.open').forEach(s => s.classList.remove('open'));
    sel.classList.toggle('open', willOpen);
  };
  sel.appendChild(control);

  const menu = document.createElement('div');
  menu.className = 'select-menu';
  sel.appendChild(menu);

  function renderControl() {
    control.innerHTML = '';
    if (!selected.length) {
      const ph = document.createElement('span');
      ph.className = 'select-ph'; ph.textContent = 'Select…';
      control.appendChild(ph);
    } else {
      selected.forEach(o => {
        const tag = document.createElement('span');
        tag.className = 'select-tag';
        tag.innerHTML = `${escapeHtml(o)}<button title="Remove">×</button>`;
        tag.querySelector('button').onclick = (e) => {
          e.stopPropagation();
          const i = selected.indexOf(o); if (i >= 0) selected.splice(i, 1);
          if (cfg.onRemoveValue) cfg.onRemoveValue(o);
          renderControl(); buildMenu(); cfg.onChange();
        };
        control.appendChild(tag);
      });
    }
    const caret = document.createElement('span');
    caret.className = 'caret'; caret.textContent = '▾';
    control.appendChild(caret);
  }

  // Which groups are expanded. Starts empty — every group is collapsed, so a long
  // vocabulary (21 harms) opens as a short list of headings. Kept outside
  // buildMenu so a rebuild (adding an option, removing a tag) doesn't re-collapse
  // what the coder just opened.
  const expanded = new Set();

  // Keep the headings' "n selected" badges current after a tick, without
  // rebuilding the menu — a rebuild would jump the scroll position mid-click.
  function refreshCounts() {
    const sections = groupedOptions(options, cfg.groups);
    menu.querySelectorAll('.menu-group').forEach(head => {
      const sec = sections.find(s => s.label === head.dataset.group);
      if (!sec) return;
      const n = sec.options.filter(o => selected.includes(o)).length;
      let badge = head.querySelector('.mg-sel');
      if (!badge) {
        badge = document.createElement('span');
        badge.className = 'mg-sel';
        head.insertBefore(badge, head.querySelector('.mg-n'));
      }
      badge.textContent = n;
      badge.style.display = n ? '' : 'none';
    });
  }

  function buildMenu() {
    menu.innerHTML = '';
    hideDefTip();          // the row a tip was explaining is about to be replaced
    // Grouped vocabularies (harm, factor) show a collapsible heading before their
    // options; ungrouped ones render as a plain flat list, always visible.
    groupedOptions(options, cfg.groups).forEach(section => {
      if (section.label) {
        menu.appendChild(groupHeader(section, expanded, () => buildMenu(),
                                     section.options.filter(o => selected.includes(o)).length));
        if (!expanded.has(section.label)) return;    // collapsed: skip its options
      }
      section.options.forEach(o => {
        const on = selected.includes(o);
        const row = document.createElement('label');
        row.className = 'menu-opt' + (on ? ' sel' : '') + (section.label ? ' in-group' : '');
        row.dataset.opt = o;
        row.innerHTML = `<input type="checkbox" ${on ? 'checked' : ''}><span>${escapeHtml(o)}</span>`;
        attachDefTip(row, o, (cfg.definitions || {})[o], cfg.accent);
        row.querySelector('input').onchange = (e) => {
          if (e.target.checked) { if (!selected.includes(o)) selected.push(o); }
          else {
            const i = selected.indexOf(o); if (i >= 0) selected.splice(i, 1);
            if (cfg.onRemoveValue) cfg.onRemoveValue(o);
          }
          row.classList.toggle('sel', e.target.checked);
          renderControl(); refreshCounts(); cfg.onChange();
        };
        menu.appendChild(row);
      });
    });
    if (cfg.onAdd) {
      const add = document.createElement('div');
      add.className = 'menu-add';
      add.innerHTML = `<input type="text" placeholder="add option…"><button>Add</button>`;
      const inp = add.querySelector('input');
      const go = async () => {
        const val = inp.value.trim();
        if (!val) return;
        const updated = await cfg.onAdd(val);
        if (Array.isArray(updated)) options = updated;
        if (!selected.includes(val)) selected.push(val);
        cfg.onChange();
        buildMenu(); renderControl();
      };
      add.querySelector('button').onclick = (e) => { e.stopPropagation(); go(); };
      inp.onclick = (e) => e.stopPropagation();
      inp.onkeydown = (e) => { if (e.key === 'Enter') { e.stopPropagation(); go(); } };
      menu.appendChild(add);
    }
  }

  buildMenu();
  renderControl();
  return sel;
}

export function buildQuotes(key) {
  const wrap = document.createElement('div');
  wrap.className = 'quotes';
  const mine = curDoc.ann.quotes
    .map((q, gi) => ({ q, gi }))
    .filter(x => x.q.category === key);
  if (!mine.length) return wrap;
  mine.forEach(({ q, gi }) => {
    const el = document.createElement('div');
    el.className = 'quote';
    el.style.borderLeftColor = color[key] || '#ccc';
    el.innerHTML = `“${escapeHtml(q.text.slice(0, 220))}”<button class="x" title="Remove">×</button>`;
    el.onclick = (e) => { if (e.target.classList.contains('x')) return; scrollToMark(gi); };
    el.querySelector('.x').onclick = (e) => { e.stopPropagation(); removeQuote(gi); };
    wrap.appendChild(el);
  });
  return wrap;
}
