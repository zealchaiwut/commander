/* Run Sprint controls + preflight modal (issue #448) — extracted from
 * project.html (issue #797). Covers the Run / Cancel actions and the full
 * preflight modal: DAG render, dependency order, conflict + mis-sizing flags,
 * and the confirm-to-dispatch flow. Page helpers and broadly-shared board
 * caches resolve through the page's global scope; preflight state (`_pf*`) is
 * seeded on `window` by ./state.js.
 */

/* global _smgmtRepo, _smgmtShowToast, escHtml, sprintLabelDisplay, loadSprintMgmt,
   _pfCurrentLabel:writable, _pfCurrentRepo:writable, _pfState:writable,
   _pfDagData:writable, _pfWarnings:writable, _pfCycle:writable,
   _pfFlags:writable, _pfSelectedIds:writable */

export function smgmtRunBlockedToast() {
  _smgmtShowToast('Another sprint is running — wait for it to finish or cancel it');
}

export function smgmtRunSprint(label) {
  _pfOpen(label);
}

export async function smgmtCancelSprint(label) {
  const repo = _smgmtRepo();
  if (!repo) return;
  if (!confirm(`Cancel sprint ${sprintLabelDisplay(label)}? The sprint will stop and tickets will not be modified.`)) return;
  try {
    const res = await fetch(`/api/sprints/run/${encodeURIComponent(label)}?project=${encodeURIComponent(repo)}`, { method: 'DELETE' });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      _smgmtShowToast(`Cancel failed: ${err.detail || res.status}`);
    } else {
      _smgmtShowToast(`Sprint ${sprintLabelDisplay(label)} cancel signal sent`);
      setTimeout(() => loadSprintMgmt(), 2000);
    }
  } catch (e) {
    _smgmtShowToast(`Cancel failed: ${e.message}`);
  }
}



export function _pfOpen(label) {
  const repo = _smgmtRepo();
  if (!repo) return;
  _pfCurrentLabel = label;
  _pfCurrentRepo  = repo;
  _pfReset();
  document.getElementById('pf-backdrop').classList.remove('hidden');
  document.getElementById('pf-modal').classList.remove('hidden');
  document.getElementById('pf-close-btn').focus();
  _pfFetch();
}

export function _pfReset() {
  document.getElementById('pf-loading').classList.remove('hidden');
  document.getElementById('pf-content').classList.add('hidden');
  document.getElementById('pf-error').classList.add('hidden');
  document.getElementById('pf-footer').classList.add('hidden');
  document.getElementById('pf-confirm-btn').disabled = true;
  document.getElementById('pf-confirm-btn').textContent = 'Run Sprint';
  _pfDagData = null;
  _pfWarnings = null;
  _pfCycle = null;
  _pfFlags = null;
  _pfSelectedIds = new Set();
}

export function _pfClose() {
  document.getElementById('pf-backdrop').classList.add('hidden');
  document.getElementById('pf-modal').classList.add('hidden');
  _pfCurrentLabel = null;
  _pfCurrentRepo  = null;
  _pfState        = 'idle';
  _pfDagData      = null;
  _pfWarnings     = null;
  _pfCycle        = null;
  _pfFlags        = null;
  _pfSelectedIds  = new Set();
}

export async function _pfFetch() {
  _pfState = 'loading';
  const label = _pfCurrentLabel;
  const repo  = _pfCurrentRepo;
  try {
    const res = await fetch(
      `/api/sprints/${encodeURIComponent(label)}/preflight?project=${encodeURIComponent(repo)}`
    );
    if (!res.ok) throw new Error(await res.text());
    if (_pfCurrentLabel !== label) return;
    const data = await res.json();
    _pfDagData  = data.dag              || null;
    _pfWarnings = data.warnings         || null;
    _pfCycle    = data.cycle            || null;
    _pfFlags    = data.mis_sizing_flags || null;
    if (_pfDagData) {
      for (const t of (_pfDagData.tickets || [])) _pfSelectedIds.add(t.id);
    }
    _pfState = 'success';
    _pfShowSuccess();
  } catch (e) {
    if (_pfCurrentLabel !== label) return;
    _pfState = 'error';
    _pfShowError(e.message || 'Preflight check failed.');
  }
}

export function _pfShowSuccess() {
  document.getElementById('pf-loading').classList.add('hidden');
  document.getElementById('pf-error').classList.add('hidden');
  const n = parseInt((_pfCurrentLabel || '').split('-')[1], 10);
  const dagHtml       = _pfDagData && (_pfDagData.tickets || []).length > 0
    ? _pfBuildDAGHtml(_pfDagData)
    : '';
  const warningsHtml  = _pfBuildWarningsHtml();
  const cycleHtml     = _pfBuildCycleHtml();
  const flagsHtml     = _pfBuildFlagsHtml();
  const conflictsHtml = _pfBuildConflictsHtml();
  const orderHtml     = _pfBuildOrderHtml();
  document.getElementById('pf-content').innerHTML =
    `<p style="font-size:13px;color:var(--text);margin:0;">Ready to run <strong>Sprint ${n}</strong>.</p>
     ${warningsHtml}
     ${cycleHtml}
     ${flagsHtml}
     ${dagHtml}
     <div class="pf-section">
       <div class="pf-section-label">Conflicts</div>
       <div class="pf-section-body" id="pf-conflicts">${conflictsHtml}</div>
     </div>
     <div class="pf-section">
       <div class="pf-section-label">Proposed Execution Order</div>
       <div class="pf-section-body" id="pf-order">${orderHtml}</div>
     </div>`;
  document.getElementById('pf-content').classList.remove('hidden');
  document.getElementById('pf-footer').classList.remove('hidden');
  _pfUpdateConfirmBtn();
  document.getElementById('pf-cancel-btn').focus();
  if (_pfDagData && (_pfDagData.edges || []).length > 0) {
    requestAnimationFrame(() => _pfDrawDAGArrows(_pfDagData.edges));
  }
}

export function _pfUpdateConfirmBtn() {
  const hasCycle = !!(_pfCycle && _pfCycle.length);
  const pendingFlags = (_pfFlags && (_pfFlags.flags || []).filter(f => f.status === 'pending')) || [];
  const hasPending = pendingFlags.length > 0;
  const confirmBtn = document.getElementById('pf-confirm-btn');
  if (!confirmBtn) return;
  confirmBtn.disabled = hasCycle || hasPending;
  if (hasCycle) {
    confirmBtn.title = 'Cannot run: dependency cycle detected. Resolve the cycle first.';
    confirmBtn.setAttribute('aria-label', 'Run Sprint — disabled: dependency cycle detected');
  } else if (hasPending) {
    confirmBtn.title = `Cannot run: ${pendingFlags.length} mis-sizing flag${pendingFlags.length > 1 ? 's' : ''} need review.`;
    confirmBtn.setAttribute('aria-label', 'Run Sprint — disabled: mis-sizing flags need review');
  } else {
    confirmBtn.title = '';
    confirmBtn.setAttribute('aria-label', 'Run Sprint');
  }
}

export function _pfBuildWarningsHtml() {
  if (!_pfWarnings) return '';
  const chips = [];
  const unestimated    = _pfWarnings.unestimated    || [];
  const staleEstimates = _pfWarnings.stale_estimates || [];
  const missingAc      = _pfWarnings.missing_ac      || [];
  if (unestimated.length) {
    chips.push(`<span class="pf-warning-chip">${unestimated.length} unestimated: ${escHtml(unestimated.join(', '))}</span>`);
  }
  if (staleEstimates.length) {
    chips.push(`<span class="pf-warning-chip">${staleEstimates.length} stale estimate${staleEstimates.length > 1 ? 's' : ''}: ${escHtml(staleEstimates.join(', '))}</span>`);
  }
  if (missingAc.length) {
    chips.push(`<span class="pf-warning-chip">${missingAc.length} missing AC: ${escHtml(missingAc.join(', '))}</span>`);
  }
  if (!chips.length) return '';
  return `<div class="pf-warnings-section">
    <div class="pf-warnings-label">Warnings</div>
    <div class="pf-warning-chips">${chips.join('')}</div>
  </div>`;
}

export function _pfBuildCycleHtml() {
  if (!_pfCycle || !_pfCycle.length) return '';
  return `<div class="pf-cycle-banner">
    <strong>Cycle detected:</strong> ${escHtml(_pfCycle.join(' → '))}
  </div>`;
}

// ── Mis-sizing flags section (issue #578) ───────────────────────────────────

export function _pfBuildFlagsHtml() {
  const flags = _pfFlags && (_pfFlags.flags || []);
  if (!flags || !flags.length) return '';

  const label = _pfCurrentLabel;
  const repo  = _pfCurrentRepo;

  const rows = flags.map(f => {
    const num = f.issue_number;
    const resolved = f.status !== 'pending';
    const itemClass = resolved ? 'pf-flag-item resolved' : 'pf-flag-item';

    const estLabel = f.current_estimate
      ? `${escHtml(f.current_estimate)} (${f.current_estimate_minutes ?? '?'} min)`
      : 'unknown';
    const avgLabel = f.historical_avg_actual_size
      ? `${escHtml(f.historical_avg_actual_size)} (${f.historical_avg_actual_minutes ?? '?'} min avg)`
      : 'unknown';
    const drivingLabels = (f.driving_labels || []).map(l => `<code>${escHtml(l)}</code>`).join(', ');
    const eventCount = f.mis_sizing_event_count || 0;

    let badgeHtml = '';
    let actionsHtml = '';
    if (resolved) {
      const actionText = { approved: 'Approved', reestimated: 'Re-estimated', dismissed: 'Dismissed' }[f.status] || f.status;
      badgeHtml = `<span class="pf-flag-badge pf-flag-badge-resolved">${escHtml(actionText)}</span>`;
      const noteText = f.action_note ? ` — ${escHtml(f.action_note)}` : '';
      const newSizeText = f.new_size ? ` New size: ${escHtml(f.new_size)}.` : '';
      actionsHtml = `<div class="pf-flag-resolved-note">${escHtml(actionText)}${newSizeText}${noteText}</div>`;
    } else {
      badgeHtml = `<span class="pf-flag-badge pf-flag-badge-pending">Review needed</span>`;
      actionsHtml = `
        <div class="pf-flag-actions" id="pf-flag-actions-${num}">
          <button class="pf-flag-action-btn approve" onclick="_pfFlagAction(${num}, 'approved')">Approve</button>
          <button class="pf-flag-action-btn" onclick="_pfFlagShowSizePicker(${num}, '${escHtml(f.current_estimate || 'S')}')">Re-estimate</button>
          <button class="pf-flag-action-btn dismiss" onclick="_pfFlagAction(${num}, 'dismissed')">Dismiss</button>
        </div>
        <div id="pf-flag-picker-${num}" style="display:none">
          <div class="pf-flag-size-picker">
            <span style="font-size:12px;color:var(--text-muted);">New size:</span>
            ${['S','M','L','XL'].map(s =>
              `<button class="pf-flag-size-btn" onclick="_pfFlagReestimate(${num}, '${s}')">${s}</button>`
            ).join('')}
            <button class="pf-flag-size-cancel" onclick="_pfFlagHidePicker(${num})">Cancel</button>
          </div>
        </div>`;
    }

    return `<div class="${itemClass}" id="pf-flag-item-${num}">
      <div class="pf-flag-header">
        <span class="pf-flag-id">#${num}</span>
        <span class="pf-flag-title" title="${escHtml(f.title)}">${escHtml(f.title)}</span>
        ${badgeHtml}
      </div>
      <div class="pf-flag-details">
        Estimate: <strong>${estLabel}</strong> ·
        Historical avg: <strong>${avgLabel}</strong> ·
        ${eventCount} mis-sizing event${eventCount !== 1 ? 's' : ''} on: ${drivingLabels}
      </div>
      ${actionsHtml}
    </div>`;
  });

  const pending = flags.filter(f => f.status === 'pending').length;
  const subtitle = pending > 0
    ? `${pending} ticket${pending > 1 ? 's' : ''} flagged for review`
    : 'All flags resolved';

  return `<div class="pf-flags-section" id="pf-flags-section">
    <div class="pf-flags-label">Mis-sizing review — ${subtitle}</div>
    ${rows.join('')}
  </div>`;
}

export function _pfFlagShowSizePicker(num, currentSize) {
  const actionsEl = document.getElementById(`pf-flag-actions-${num}`);
  const pickerEl  = document.getElementById(`pf-flag-picker-${num}`);
  if (actionsEl) actionsEl.style.display = 'none';
  if (pickerEl)  pickerEl.style.display  = 'block';
}

export function _pfFlagHidePicker(num) {
  const actionsEl = document.getElementById(`pf-flag-actions-${num}`);
  const pickerEl  = document.getElementById(`pf-flag-picker-${num}`);
  if (actionsEl) actionsEl.style.display = '';
  if (pickerEl)  pickerEl.style.display  = 'none';
}

export async function _pfFlagAction(num, action, newSize) {
  const label = _pfCurrentLabel;
  const repo  = _pfCurrentRepo;
  if (!label || !repo) return;

  // Disable buttons to prevent double-click
  const itemEl = document.getElementById(`pf-flag-item-${num}`);
  if (itemEl) itemEl.querySelectorAll('button').forEach(b => { b.disabled = true; });

  try {
    const body = { action };
    if (newSize) body.new_size = newSize;
    const res = await fetch(
      `/api/sprints/${encodeURIComponent(label)}/mis-sizing-flags/${num}/action?project=${encodeURIComponent(repo)}`,
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
    );
    if (!res.ok) {
      const err = await res.text();
      _smgmtShowToast(`Flag action failed: ${err}`, 'error');
      if (itemEl) itemEl.querySelectorAll('button').forEach(b => { b.disabled = false; });
      return;
    }
    const data = await res.json();
    // Update local state and re-render flags section
    _pfFlags = data;
    const flagsSection = document.getElementById('pf-flags-section');
    if (flagsSection) {
      const newHtml = _pfBuildFlagsHtml();
      flagsSection.outerHTML = newHtml || '<div id="pf-flags-section"></div>';
    }
    _pfUpdateConfirmBtn();
  } catch (e) {
    _smgmtShowToast('Flag action failed: ' + e.message, 'error');
    if (itemEl) itemEl.querySelectorAll('button').forEach(b => { b.disabled = false; });
  }
}

export function _pfFlagReestimate(num, newSize) {
  _pfFlagHidePicker(num);
  _pfFlagAction(num, 'reestimated', newSize);
}

// ────────────────────────────────────────────────────────────────────────────

export function _pfBuildDAGHtml(dag) {
  const ticketMap = {};
  for (const t of (dag.tickets || [])) ticketMap[t.id] = t;
  const layers = dag.layers || [];
  if (!layers.length) return '';

  let colsHtml = '';
  for (let i = 0; i < layers.length; i++) {
    const layer = layers[i];
    let cardsHtml = '';
    for (const id of layer) {
      const t = ticketMap[id] || { id, number: id.replace('#', ''), title: id, state: 'backlog', size: null, files_touched: [] };
      const stateClass = t.state || 'backlog';
      const stateBadge = `<span class="ticket-status-pill ${escHtml(stateClass)}">${escHtml(stateClass)}</span>`;
      const sizeBadge  = t.size ? `<span class="pf-dag-size-badge">${escHtml(t.size)}</span>` : '';
      const files      = (t.files_touched || []);
      const shown      = files.slice(0, 3).map(f => `<span>${escHtml(f.split('/').slice(-1)[0])}</span>`).join('');
      const more       = files.length > 3 ? `<span>+${files.length - 3} more</span>` : '';
      const filesHtml  = (shown || more) ? `<div class="pf-dag-card-files">${shown}${more}</div>` : '';
      cardsHtml += `<div class="pf-dag-card" id="pf-card-${escHtml(id)}" data-dag-id="${escHtml(id)}">
          <div class="pf-dag-card-header">
            <input type="checkbox" class="pf-dag-card-check" checked
              onchange="_pfToggleTicket('${escHtml(id)}')" aria-label="Include ticket ${escHtml(id)}">
            <span class="pf-dag-card-num">${escHtml(id)}</span>
          </div>
          <div class="pf-dag-card-title" title="${escHtml(t.title)}">${escHtml(t.title)}</div>
          <div class="pf-dag-card-meta">${stateBadge}${sizeBadge}</div>
          ${filesHtml}
        </div>`;
    }
    colsHtml += `<div class="pf-dag-col">
        <div class="pf-dag-col-label">Level ${i + 1}</div>
        ${cardsHtml}
      </div>`;
  }

  return `<div class="pf-dag-section">
    <div class="pf-dag-section-label">Execution Graph</div>
    <div class="pf-dag-wrap" id="pf-dag-wrap">
      <svg class="pf-dag-svg" id="pf-dag-svg" aria-hidden="true"></svg>
      <div class="pf-dag-levels" id="pf-dag-levels">${colsHtml}</div>
    </div>
  </div>`;
}

export function _pfDrawDAGArrows(edges) {
  if (!edges || !edges.length) return;
  const wrap   = document.getElementById('pf-dag-wrap');
  const svg    = document.getElementById('pf-dag-svg');
  const levels = document.getElementById('pf-dag-levels');
  if (!wrap || !svg || !levels) return;

  const wrapRect = wrap.getBoundingClientRect();
  const h = levels.getBoundingClientRect().height;
  svg.setAttribute('width',  String(wrapRect.width));
  svg.setAttribute('height', String(h));

  const defs   = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
  const marker = document.createElementNS('http://www.w3.org/2000/svg', 'marker');
  marker.setAttribute('id', 'pf-arrow');
  marker.setAttribute('markerWidth',  '7');
  marker.setAttribute('markerHeight', '7');
  marker.setAttribute('refX', '6');
  marker.setAttribute('refY', '3.5');
  marker.setAttribute('orient', 'auto');
  const arrowPath = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  arrowPath.setAttribute('d', 'M0,0 L0,7 L7,3.5 z');
  arrowPath.setAttribute('fill', 'var(--text-muted)');
  marker.appendChild(arrowPath);
  defs.appendChild(marker);
  svg.appendChild(defs);

  for (const [fromId, toId] of edges) {
    const fromEl = wrap.querySelector(`[data-dag-id="${fromId}"]`);
    const toEl   = wrap.querySelector(`[data-dag-id="${toId}"]`);
    if (!fromEl || !toEl) continue;

    const fr = fromEl.getBoundingClientRect();
    const tr = toEl.getBoundingClientRect();
    const x1 = fr.right  - wrapRect.left;
    const y1 = fr.top    + fr.height / 2 - wrapRect.top;
    const x2 = tr.left   - wrapRect.left - 7;
    const y2 = tr.top    + tr.height / 2 - wrapRect.top;
    const mx = (x1 + x2) / 2;

    const line = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    line.setAttribute('d', `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`);
    line.setAttribute('stroke', 'var(--text-muted)');
    line.setAttribute('stroke-width', '1.5');
    line.setAttribute('fill', 'none');
    line.setAttribute('marker-end', 'url(#pf-arrow)');
    svg.appendChild(line);
  }
}

export function _pfToggleTicket(id) {
  if (_pfSelectedIds.has(id)) {
    _pfSelectedIds.delete(id);
  } else {
    _pfSelectedIds.add(id);
  }
  const card = document.getElementById(`pf-card-${id}`);
  if (card) card.classList.toggle('pf-deselected', !_pfSelectedIds.has(id));
  _pfUpdateSections();
}

export function _pfGetSelectedTickets() {
  if (!_pfDagData) return [];
  return (_pfDagData.tickets || []).filter(t => _pfSelectedIds.has(t.id));
}

export function _pfComputeConflicts(tickets) {
  const conflicts = [];
  for (let i = 0; i < tickets.length; i++) {
    for (let j = i + 1; j < tickets.length; j++) {
      const filesA = tickets[i].files_touched || [];
      const filesB = tickets[j].files_touched || [];
      const shared = filesA.filter(f => filesB.includes(f));
      for (const file of shared) {
        conflicts.push({ a: tickets[i], b: tickets[j], file });
      }
    }
  }
  return conflicts;
}

export function _pfBuildConflictsHtml() {
  const selected = _pfGetSelectedTickets();
  const conflicts = _pfComputeConflicts(selected);
  if (!conflicts.length) {
    return '<p class="pf-no-conflict">No file conflicts detected.</p>';
  }
  return conflicts.map(c =>
    `<p class="pf-conflict-item">Tickets #${c.a.number} and #${c.b.number} both touch <code>${escHtml(c.file)}</code></p>`
  ).join('');
}

export function _pfBuildOrderHtml() {
  if (!_pfDagData) return '<p class="pf-no-conflict">No order data available.</p>';
  const layers = (_pfDagData.layers || [])
    .map(layer => layer.filter(id => _pfSelectedIds.has(id)))
    .filter(l => l.length > 0);
  if (!layers.length) return '<p class="pf-no-conflict">No tickets selected.</p>';

  let html = '<ol class="pf-order-list">';
  for (let i = 0; i < layers.length; i++) {
    const nums = layers[i].map(id => id);
    const descriptor = i === 0 ? 'parallel-eligible' : `runs after Level ${i}`;
    html += `<li class="pf-order-item">Level ${i + 1}: ${escHtml(nums.join(', '))} — ${escHtml(descriptor)}.</li>`;
  }
  html += '</ol>';
  return html;
}

export function _pfUpdateSections() {
  const conflictsEl = document.getElementById('pf-conflicts');
  const orderEl     = document.getElementById('pf-order');
  if (conflictsEl) conflictsEl.innerHTML = _pfBuildConflictsHtml();
  if (orderEl)     orderEl.innerHTML     = _pfBuildOrderHtml();
}

export function _pfShowError(msg) {
  document.getElementById('pf-loading').classList.add('hidden');
  document.getElementById('pf-content').classList.add('hidden');
  document.getElementById('pf-error-msg').textContent = msg;
  document.getElementById('pf-error').classList.remove('hidden');
  document.getElementById('pf-footer').classList.remove('hidden');
  document.getElementById('pf-confirm-btn').disabled = true;
  document.getElementById('pf-retry-btn').focus();
}

export function _pfRetry() {
  _pfReset();
  _pfFetch();
}

export async function _pfConfirm() {
  if (_pfState !== 'success') return;
  const label = _pfCurrentLabel;
  const repo  = _pfCurrentRepo;
  if (!label || !repo) return;
  const confirmBtn = document.getElementById('pf-confirm-btn');
  confirmBtn.disabled = true;
  confirmBtn.innerHTML = '<span class="pf-spinner" style="width:12px;height:12px;border-width:2px;"></span> Running…';
  try {
    const res = await fetch('/api/sprints/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project: repo, sprint_label: label }),
    });
    if (!res.ok) throw new Error(await res.text());
    _pfClose();
    const n = parseInt(label.split('-')[1], 10);
    _smgmtShowToast(`Sprint ${n} dispatched.`);
    await loadSprintMgmt();
  } catch (e) {
    _pfState = 'error';
    _pfShowError('Failed to run sprint: ' + e.message);
  }
}
