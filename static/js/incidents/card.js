// One incident card: everything it shows that nobody edits in place.
//
// The interactive parts are placeholders filled in after render — the palette,
// the claim groups, the two answered-here fields, the comment box — so this
// stays the shape of a card rather than its behaviour.

import { CODER } from '../coder.js';
import { escapeHtml } from '../persist.js';
import { CLAIM_ROLE } from '../state.js';
import { NODATA } from './index.js';
import { completeControl } from './signoff.js';

// A Tow/CJR-styled card carrying every detail the JSON holds for one incident.
// Anything un-coded renders as "No data". The characteristics palette + claim
// groups are an interactive placeholder filled in by buildGroupsUI after render.
export function incidentCard(inc, fields) {
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
  // The two characteristics nobody codes: when the incident's articles were
  // published, and where. Both are read off the documents — the date from
  // Zotero, the domain from the URL — so they are plain text rather than chips:
  // there is no judgement here to drag into a claim or to justify with a quote.
  const derivedBlock = (label, html) =>
    `<div class="tow-field"><div class="tow-label">${label}</div>`
    + `<div class="tow-value tow-derived">${html}</div></div>`;
  const dates = inc.dates || [], domains = inc.domains || [];
  // A range covering fewer articles than the incident holds would read as the
  // whole story, so the documents Zotero has no date for are counted out loud.
  const undated = inc.undated
    ? `<span class="tow-undated">${inc.undated} undated</span>` : '';
  const publishedBlock = derivedBlock('Published date', !dates.length ? NODATA
    : escapeHtml(dates.length > 1 ? `${dates[0]} – ${dates[dates.length - 1]}` : dates[0])
      + (undated ? ' ' + undated : ''));
  const domainBlock = derivedBlock(domains.length > 1 ? 'Domains' : 'Domain',
    !domains.length ? NODATA : escapeHtml(domains.join(', ')));

  // Aftermath renders last of all, under the claim groups; the rest sit up left.
  // Card-only fields are answered here rather than read here, so they are left
  // to buildCardFields below instead of rendering as chips.
  const leftFieldBlocks = fields
    .filter(f => f.key !== 'incident_aftermath' && !f.card_only)
    .map(fieldBlock).join('');
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
      <button class="card-close" title="Collapse this incident (Esc)">\u00d7</button>
    </div>
    <div class="tow-body">
      <div class="tow-col c1">
        ${leftFieldBlocks}
        ${publishedBlock}
        ${domainBlock}
        <div class="tow-cardfields" data-inc="${encId}"></div>
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

export const roleColor = (role) => (CLAIM_ROLE[role] && CLAIM_ROLE[role].color) || '#e5e7eb';

export const roleLabel = (role) => (CLAIM_ROLE[role] && CLAIM_ROLE[role].label) || role;

// The palette in ROLES is chip *background* colour — pale by design, and too light
// to read as text on white (yellow worst of all). Darken it toward black for the
// sentence placeholders, so an empty slot still shows which role it wants without
// a second colour list that could drift out of step with the chips.
export function roleInk(role, amount = 0.45) {
  const hex = roleColor(role);
  const n = parseInt(hex.slice(1), 16);
  const dim = (c) => Math.round(c * (1 - amount));
  return `rgb(${dim((n >> 16) & 255)}, ${dim((n >> 8) & 255)}, ${dim(n & 255)})`;
}
