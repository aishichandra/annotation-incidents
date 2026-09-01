// The comment box on a card: this coder's remark about the incident as a whole.
//
// Autosaves as you type, and flushes on the way out of the page, so a comment
// half-written when a tab closes is not lost.

import { escapeHtml } from '../persist.js';
import { INCIDENTS } from './index.js';

// Comments in flight, incident id -> pending debounce timer. Typing shouldn't
// cost a request per keystroke, but nothing may be lost either, so a pending
// comment is flushed before anything that could redraw its card.
export const COMMENT_TIMERS = new Map();

export function flushIncidentComments() {
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
export async function saveComment(incId) {
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
export function buildIncidentComment(wrap, incId) {
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
