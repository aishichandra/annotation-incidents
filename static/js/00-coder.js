// Who is coding.
// Coder picker + the fetch wrapper that stamps every /api/ call with ?coder=.

import { init } from './20-init.js';

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
  if (!coders.includes(CODER)) { CODER = d.current || coders[0] || ''; }
  localStorage.setItem('coder', CODER);
  sel.innerHTML = coders.map(c =>
    `<option value="${c}"${c === CODER ? ' selected' : ''}>${c}</option>`).join('');
  sel.onchange = () => {
    localStorage.setItem('coder', sel.value);
    location.reload();
  };
}
