// Who is coding.
// Coder picker + the fetch wrapper that stamps every /api/ call with ?coder=.

import { init } from './boot.js';

// ---------- who is coding ----------
// Several coders code the same documents and incidents independently, so every
// API call has to say who it is for. Rather than thread a coder argument through
// the ~12 call sites below, wrap fetch once: same-origin /api/ requests get the
// active coder appended. Chosen in the toolbar and remembered in localStorage.
export let CODER = localStorage.getItem('coder') || '';
export const _fetch = window.fetch.bind(window);
window.fetch = (input, init) => {
  if (CODER && typeof input === 'string' && input.startsWith('/api/')) {
    input += (input.includes('?') ? '&' : '?') + 'coder=' + encodeURIComponent(CODER);
  }
  return _fetch(input, init);
};

// Fill the toolbar picker from the server's coder list. Switching coder reloads
// the page so nothing from the previous coder's session lingers on screen.
export async function initCoders() {
  const sel = document.getElementById('coderSelect');
  let d;
  try { d = await _fetch('/api/coders').then(r => r.json()); }
  catch (e) { sel.style.display = 'none'; return; }
  const coders = d.coders || [];
  const removable = d.removable || [];
  if (!coders.includes(CODER)) { CODER = d.current || coders[0] || ''; }
  localStorage.setItem('coder', CODER);
  sel.innerHTML = coders.map(c =>
    `<option value="${c}"${c === CODER ? ' selected' : ''}>${c}</option>`).join('')
    + '<option value="__add__">+ Add coder…</option>'
    + (removable.length ? '<option value="__rename__">✎ Rename coder…</option>'
                      + '<option value="__remove__">− Delete coder…</option>' : '');
  sel.onchange = async () => {
    if (sel.value === '__rename__') {
      sel.value = CODER;
      const old = (prompt('Rename which coder? ' + removable.join(', ')) || '').trim();
      if (!old) return;
      const name = (prompt(`New name for ${old} (letters, digits, - or _):`, old) || '').trim();
      if (!name || name === old) return;
      const r = await _fetch('/api/coders/' + encodeURIComponent(old), {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      });
      const d = await r.json();
      if (!d.ok) { alert(d.error || 'Could not rename coder'); return; }
      if (old === CODER) localStorage.setItem('coder', d.name);
    } else if (sel.value === '__remove__') {
      sel.value = CODER;
      const name = (prompt('Delete which coder? ' + removable.join(', ')) || '').trim();
      if (!name) return;
      const typed = (prompt(`This permanently deletes ${name} AND all their coding `
        + '(local files and MongoDB). It cannot be undone.\n\n'
        + `Type ${name} to confirm:`) || '').trim();
      if (typed !== name) return;
      const r = await _fetch('/api/coders/' + encodeURIComponent(name), { method: 'DELETE' });
      const d = await r.json();
      if (!d.ok) { alert(d.error || 'Could not remove coder'); return; }
      if (name === CODER) localStorage.removeItem('coder');
    } else if (sel.value === '__add__') {
      const name = (prompt('Name for the new coder (letters, digits, - or _):') || '').trim();
      sel.value = CODER;
      if (!name) return;
      const r = await _fetch('/api/coders', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      });
      const d = await r.json();
      if (!d.ok) { alert(d.error || 'Could not add coder'); return; }
      localStorage.setItem('coder', d.name);
    } else {
      localStorage.setItem('coder', sel.value);
    }
    location.reload();
  };
}
