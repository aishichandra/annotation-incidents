// One incident as it is stored, on the card that shows it.
//
// Read from Mongo when Mongo has it and from the local files when it does not,
// so what you are looking at is the record rather than the render of it.

import { CODER } from '../coder.js';
import { escapeHtml } from '../persist.js';

// ---------- raw JSON for one incident ----------
// Fetched rather than printed from INCIDENTS, because the card's own object is
// the *aggregated view* (pooled values, pruned claims) while what's usually
// wanted here is the record as stored. The response says which one it is.
export async function toggleJson(incId) {
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
export function jsonTree(value) {
  const root = document.createElement('div');
  root.className = 'json-tree';
  root.appendChild(jsonNode(null, value, true));
  return root;
}

export function jsonLeaf(value) {
  if (value === null) return '<span class="jn-null">null</span>';
  const t = typeof value;
  if (t === 'string') return `<span class="jn-str">${escapeHtml(JSON.stringify(value))}</span>`;
  if (t === 'number') return `<span class="jn-num">${value}</span>`;
  if (t === 'boolean') return `<span class="jn-bool">${value}</span>`;
  return escapeHtml(String(value));
}

export function jsonNode(key, value, last) {
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
