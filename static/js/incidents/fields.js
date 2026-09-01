// The answers given on the card itself: Geography/location and Translated.
//
// They describe the incident rather than any of its documents, so there is no
// passage to highlight for them and no claim to drag them into — they are
// answered here, from the codebook, and nowhere else.

import { buildSelect } from '../form.js';
import { escapeHtml } from '../persist.js';
import { field } from '../reader.js';
import { attachDefTip, color } from '../state.js';
import { FIELDS, NODATA, refreshIncidents, refreshTile } from './index.js';

// The incident's own controlled answers — Geography/location, Translated —
// picked here and nowhere else. They describe the incident rather than any one
// of its documents, so there is no passage to highlight for them and no claim to
// drag them into: the card is where they are answered, and the same multiselect
// the document sidebar uses is what answers them, hover definitions and all.
//
// Saved on change to this coder's incident coding, like the comment box. The
// list held here is the control's own, so a failed save leaves the card showing
// what the server actually holds rather than what the click implied.
export function buildCardFields(container, inc) {
  if (!inc) return;
  container.innerHTML = '';
  FIELDS.filter(f => f.card_only).forEach(f => {
    const block = document.createElement('div');
    block.className = 'tow-field';

    const head = document.createElement('div');
    head.className = 'tow-label tow-field-head';
    head.innerHTML = `<span>${escapeHtml(f.label)}</span>`
                   + `<span class="inc-note-state"></span>`;
    block.appendChild(head);
    const state = head.querySelector('.inc-note-state');

    // The control owns this list and mutates it in place, so it is a copy of
    // what the incident holds rather than the incident's own array: a save that
    // fails must not leave the card showing a value the server rejected.
    const selected = (inc.field_values[f.key] || []).slice();
    if (f.control === 'toggle') {
      block.appendChild(buildFieldToggle(f, inc, selected[0] || '', state));
      container.appendChild(block);
      return;
    }
    block.appendChild(buildSelect({
      options: f.options || [],
      groups: f.groups || null,
      definitions: f.definitions || null,
      accent: color[f.key],
      selected,
      onChange: () => saveCardField(inc, f.key, selected, state),
      // Adding a code here writes it to the vocabulary, exactly as adding one
      // from the document sidebar does — same list, same codebook.
      onAdd: async (val) => {
        const res = await fetch('/api/schema/option', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ field: f.key, option: val }),
        });
        const updated = await res.json().catch(() => null);
        if (updated && updated.options) {
          f.options = updated.options;
          f.groups = updated.groups || f.groups;
          f.definitions = updated.definitions || f.definitions;
        }
        return f.options || [];
      },
    }));
    container.appendChild(block);
  });
}

// A field with two states and no third — Translated — as a switch rather than a
// menu: one control, flipped between its two values, reading as the answer it is
// without being opened.
//
// The catch a switch has to earn its way past is that a field has three states,
// not two: on, off, and never answered. An unanswered switch that already looks
// like "no" would put an answer on every incident nobody has looked at. So it
// sits visibly unset until it is touched — grey, knob centred, "Not answered" —
// and the × puts it back there, because a stray click must not be permanent.
export function buildFieldToggle(f, inc, current, state) {
  const [onValue, offValue] = f.options || [];
  const wrap = document.createElement('div');
  wrap.className = 'tow-switch';
  if (!onValue || !offValue) {
    wrap.innerHTML = NODATA;      // a switch needs both its states to exist
    return wrap;
  }

  const sw = document.createElement('button');
  sw.className = 'sw';
  sw.setAttribute('role', 'switch');
  sw.innerHTML = '<span class="sw-knob"></span>';
  const name = document.createElement('span');
  name.className = 'sw-name';
  const clear = document.createElement('button');
  clear.className = 'sw-clear';
  clear.textContent = '\u00d7';
  clear.title = 'Clear — back to unanswered';

  const paint = () => {
    sw.classList.toggle('on', current === onValue);
    sw.setAttribute('aria-checked', current === onValue ? 'true'
                                  : current === offValue ? 'false' : 'mixed');
    sw.classList.toggle('unset', !current);
    sw.title = current ? `${current} — press to flip` : `Press to set “${onValue}”`;
    name.textContent = current || 'Not answered';
    name.classList.toggle('unset', !current);
    clear.hidden = !current;
    attachDefTip(name, current, (f.definitions || {})[current], color[f.key]);
  };

  const set = async (next) => {
    const ok = await saveCardField(inc, f.key, next ? [next] : [], state);
    if (ok === false) return;
    current = next;
    paint();
  };
  // Unanswered flips to the first value; after that it just swaps.
  sw.onclick = () => set(current === onValue ? offValue : onValue);
  clear.onclick = () => set('');

  wrap.append(sw, name, clear);
  paint();
  return wrap;
}

// Saved the moment the menu changes, and reported beside the label the way the
// comment box reports — this is the only control on a card that writes an answer
// rather than a link, so "did that land?" has to be answerable without opening
// the JSON panel.
export async function saveCardField(inc, key, answer, state) {
  const say = (msg, ok) => {
    if (!state) return;
    clearTimeout(state._clear);
    state.textContent = msg;
    state.classList.toggle('on', !!ok);
    if (ok) state._clear = setTimeout(() => {
      state.textContent = ''; state.classList.remove('on');
    }, 2000);
  };
  say('Saving…', false);
  let res, d = {};
  try {
    res = await fetch(`/api/incident/${encodeURIComponent(inc.incident_id)}/field`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key, answer }), keepalive: true,
    });
    d = await res.json().catch(() => ({}));
  } catch (e) {
    say('Not saved — no connection', false);
    return false;
  }
  if (!res.ok) {
    // A code renamed or removed in the Codebook since this menu was built is the
    // one thing this can fail on. Say which, and redraw from the server rather
    // than leaving a chip that exists only on screen.
    say(d.error === 'unknown value'
      ? `Not saved — “${(d.values || []).join('”, “')}” is no longer in the codebook`
      : 'Not saved', false);
    refreshIncidents();
    return false;
  }
  inc.field_values[key] = Array.isArray(d.answer) ? d.answer
                        : (d.answer ? [d.answer] : []);
  say(d.synced ? 'Saved ✓' : 'Saved locally ✓', true);
  refreshTile(inc);
  return true;
}
