/* Per-project to-do panel (issue #844).
 *
 * One framework-free module renders the to-do list on two surfaces — the right
 * side of each project block on the home page and a docked panel inside the
 * project view — so the markup and behaviour live in exactly one place and the
 * two surfaces never drift. Both read and write the per-project to-do API from
 * #843 (`/api/projects/<project>/todos`), keyed by the project slug, so they
 * show the identical list.
 *
 * No build step, no modules: this attaches a single global, `CommanderTodo`,
 * mirroring the existing `add-project-modal.js` / `log-colorize.js` pattern.
 * The panel's CSS is injected once and is driven entirely by the page's own
 * theme tokens (--surface, --border, --text, …), so it adapts to light/dark on
 * both pages without duplicating colours. Visual design matches the attached
 * mock, references/issue-844/project_todo_mock.html.
 */
(function () {
  'use strict';

  // ── styles (from project_todo_mock.html — single source of truth) ──────────
  var TODO_CSS = [
    '.todo{padding:13px 16px;display:flex;flex-direction:column;min-height:100%}',
    '.todo-head{display:flex;align-items:center;gap:7px;margin-bottom:10px}',
    ".todo-title{font-size:12px;font-weight:700;letter-spacing:.03em;text-transform:uppercase;color:var(--text-sub);display:flex;align-items:center;gap:6px}",
    '.todo-count{font-family:var(--mono);font-size:11px;color:var(--text-sub)}',
    '.todo-saved{margin-left:auto;font-family:var(--mono);font-size:10px;color:var(--green);opacity:0;transition:opacity .2s;display:inline-flex;align-items:center;gap:3px}',
    '.todo-saved.show{opacity:1}',
    '.todo-add{display:flex;gap:6px;margin-bottom:10px}',
    '.todo-input{flex:1;border:1px solid var(--border);background:var(--surface);color:var(--text);border-radius:7px;padding:8px 11px;font-size:13px;font-family:inherit}',
    '.todo-input:focus{outline:none;border-color:var(--blue);box-shadow:0 0 0 3px var(--blue-bg)}',
    '.todo-input::placeholder{color:var(--text-muted)}',
    '.todo-addbtn{border:1px solid var(--border);background:var(--text);color:var(--surface);border-radius:7px;width:34px;display:flex;align-items:center;justify-content:center;cursor:pointer;font-size:16px;flex-shrink:0}',
    '.todo-addbtn:hover{opacity:.88}',
    '.todo-list{display:flex;flex-direction:column;gap:2px;flex:1}',
    '.todo-item{display:flex;align-items:flex-start;gap:9px;padding:7px 8px;border-radius:7px;transition:background .12s;position:relative}',
    '.todo-item:hover{background:var(--surface-hover)}',
    '.todo-item.dragging{opacity:.5}',
    '.todo-item.drop-target{box-shadow:inset 0 2px 0 var(--blue)}',
    '.todo-check{width:17px;height:17px;border-radius:5px;border:1.6px solid var(--text-sub);flex-shrink:0;cursor:pointer;display:flex;align-items:center;justify-content:center;margin-top:1px;transition:all .12s;background:var(--surface)}',
    '.todo-check:hover{border-color:var(--green)}',
    '.todo-check .ti{font-size:12px;color:#fff;opacity:0}',
    '.todo-item.done .todo-check{background:var(--green);border-color:var(--green)}',
    '.todo-item.done .todo-check .ti{opacity:1}',
    '.todo-text{flex:1;font-size:13px;line-height:1.45;color:var(--text);word-break:break-word;cursor:text}',
    '.todo-item.done .todo-text{text-decoration:line-through;color:var(--text-sub)}',
    '.todo-edit{flex:1;font-size:13px;line-height:1.45;font-family:inherit;border:1px solid var(--blue);background:var(--surface);color:var(--text);border-radius:5px;padding:2px 6px}',
    '.todo-edit:focus{outline:none;box-shadow:0 0 0 3px var(--blue-bg)}',
    '.todo-actions{display:flex;gap:2px;opacity:0;transition:opacity .12s;flex-shrink:0}',
    '.todo-item:hover .todo-actions{opacity:1}',
    '.todo-grip{color:var(--text-sub);cursor:grab;display:flex;align-items:center;font-size:14px;padding:3px 1px;touch-action:none}',
    '.todo-grip:active{cursor:grabbing}',
    '.todo-iconbtn{background:none;border:none;color:var(--text-sub);cursor:pointer;padding:3px;border-radius:4px;display:flex;align-items:center;font-size:14px}',
    '.todo-iconbtn:hover{background:var(--surface-2);color:var(--text)}',
    '.todo-iconbtn.promote:hover{color:var(--blue)}',
    '.todo-iconbtn.del:hover{color:var(--red)}',
    '.todo-empty{font-size:12px;color:var(--text-sub);font-style:italic;padding:10px 8px;text-align:center}',
    '.todo-done-toggle{margin-top:8px;font-family:var(--mono);font-size:11px;color:var(--text-sub);cursor:pointer;display:flex;align-items:center;gap:5px;padding:5px 8px;border-radius:6px;border:none;background:none;width:100%;text-align:left}',
    '.todo-done-toggle:hover{background:var(--surface-hover);color:var(--text-muted)}',
    '.todo-done-toggle .ti{transition:transform .15s}',
    '.todo.done-collapsed .todo-done-toggle .ti{transform:rotate(-90deg)}',
    '.todo.done-collapsed .todo-done-list{display:none}',
    '.todo-foot{margin-top:6px;border-top:1px dashed var(--border);padding-top:8px}',
    '.todo-hint{font-size:10.5px;color:var(--text-sub);display:flex;align-items:center;gap:5px;font-family:var(--mono)}',
    // docked panel shell (project view)
    '.todo-dock{position:fixed;right:20px;bottom:20px;width:320px;max-width:calc(100vw - 40px);background:var(--surface);border:1px solid var(--border);border-radius:12px;box-shadow:0 10px 30px rgba(0,0,0,.18);z-index:60;display:flex;flex-direction:column;overflow:hidden}',
    '.todo-dock.collapsed .todo-dock-body{display:none}',
    '.todo-dock-bar{display:flex;align-items:center;gap:8px;padding:10px 13px;cursor:pointer;font-size:13px;font-weight:600;color:var(--text);border-bottom:1px solid var(--border)}',
    '.todo-dock.collapsed .todo-dock-bar{border-bottom:none}',
    '.todo-dock-bar .ti-chevron-down{margin-left:auto;transition:transform .15s}',
    '.todo-dock.collapsed .todo-dock-bar .ti-chevron-down{transform:rotate(180deg)}',
    '.todo-dock-body{max-height:min(60vh,520px);overflow:auto}',
    '@media(max-width:560px){.todo-dock{right:12px;left:12px;width:auto}}',
    // promote "coming soon" toast
    '.todo-toast{position:fixed;left:50%;bottom:26px;transform:translateX(-50%) translateY(8px);background:var(--text);color:var(--surface);font-size:12.5px;padding:9px 15px;border-radius:8px;box-shadow:0 6px 20px rgba(0,0,0,.22);z-index:120;opacity:0;transition:opacity .18s,transform .18s;pointer-events:none;display:flex;align-items:center;gap:7px}',
    '.todo-toast.show{opacity:1;transform:translateX(-50%) translateY(0)}',
    '@media (prefers-reduced-motion: reduce){.todo *,.todo-saved,.todo-toast,.todo-dock-bar .ti{transition:none!important;animation:none!important}}'
  ].join('');

  function injectStyles() {
    if (document.getElementById('commander-todo-styles')) return;
    var s = document.createElement('style');
    s.id = 'commander-todo-styles';
    s.textContent = TODO_CSS;
    document.head.appendChild(s);
  }

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function apiBase(project) {
    return '/api/projects/' + encodeURIComponent(project) + '/todos';
  }

  // ── network ────────────────────────────────────────────────────────────────
  function fetchList(project) {
    return fetch(apiBase(project))
      .then(function (r) { return r.ok ? r.json() : []; })
      .catch(function () { return []; });
  }

  function apiCreate(project, text) {
    return fetch(apiBase(project), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: text })
    }).then(function (r) { return r.ok ? r.json() : null; });
  }

  function apiUpdate(project, id, fields) {
    return fetch(apiBase(project) + '/' + id, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(fields)
    }).then(function (r) { return r.ok ? r.json() : null; });
  }

  function apiDelete(project, id) {
    return fetch(apiBase(project) + '/' + id, { method: 'DELETE' })
      .then(function (r) { return r.ok; });
  }

  // ── "Saved" indicator ───────────────────────────────────────────────────────
  function flashSaved(panel) {
    var el = panel.querySelector('.todo-saved');
    if (!el) return;
    el.classList.add('show');
    clearTimeout(el._t);
    el._t = setTimeout(function () { el.classList.remove('show'); }, 1100);
  }

  // ── "coming soon" affordance for promote (no other action) ──────────────────
  function showComingSoon() {
    var t = document.createElement('div');
    t.className = 'todo-toast';
    t.innerHTML = '<i class="ti ti-arrow-up-right"></i> Promote to a ticket is coming soon';
    document.body.appendChild(t);
    requestAnimationFrame(function () { t.classList.add('show'); });
    setTimeout(function () {
      t.classList.remove('show');
      setTimeout(function () { if (t.parentNode) t.parentNode.removeChild(t); }, 250);
    }, 1900);
  }

  function promote() {
    // Rendered + styled, but wiring to planning is a later ticket (#844 AC16):
    // show a coming-soon affordance and do nothing else — no fetch, no nav.
    showComingSoon();
  }

  // ── rendering ────────────────────────────────────────────────────────────────
  function row(panel, item) {
    var st = panel._todoState;
    var doneCls = item.done ? ' done' : '';
    var editing = st.editingId === item.id;
    var textCell = editing
      ? '<input class="todo-edit" value="' + esc(item.text) + '" />'
      : '<span class="todo-text" title="Double-click to edit">' + esc(item.text) + '</span>';
    return '' +
      '<div class="todo-item' + doneCls + '" data-id="' + item.id + '"' +
           (editing ? '' : ' draggable="true"') + '>' +
        '<span class="todo-check" role="checkbox" aria-checked="' + (item.done ? 'true' : 'false') +
          '" tabindex="0" title="Toggle done"><i class="ti ti-check"></i></span>' +
        textCell +
        '<span class="todo-actions">' +
          '<span class="todo-grip" title="Drag to reorder" aria-hidden="true"><i class="ti ti-grip-vertical"></i></span>' +
          '<button class="todo-iconbtn promote" title="Promote to ticket (coming soon)" aria-label="Promote (coming soon)"><i class="ti ti-arrow-up-right"></i></button>' +
          '<button class="todo-iconbtn del" title="Delete" aria-label="Delete"><i class="ti ti-x"></i></button>' +
        '</span>' +
      '</div>';
  }

  function render(panel) {
    var st = panel._todoState;
    var items = st.items.slice().sort(function (a, b) {
      if (a.position !== b.position) return a.position - b.position;
      return a.id - b.id;
    });
    var active = items.filter(function (i) { return !i.done; });
    var done = items.filter(function (i) { return i.done; });

    var listHtml = active.length
      ? active.map(function (i) { return row(panel, i); }).join('')
      : '<div class="todo-empty">No to-dos yet — capture an idea above.</div>';

    var doneHtml = done.length
      ? '<button class="todo-done-toggle" type="button">' +
          '<i class="ti ti-chevron-down" style="font-size:13px"></i> Done (' + done.length + ')' +
        '</button>' +
        '<div class="todo-done-list">' +
          done.map(function (i) { return row(panel, i); }).join('') +
        '</div>'
      : '';

    panel.innerHTML = '' +
      '<div class="todo-head">' +
        '<span class="todo-title"><i class="ti ti-checklist" style="font-size:14px"></i> To-do</span>' +
        '<span class="todo-count">' + active.length + '</span>' +
        '<span class="todo-saved"><i class="ti ti-check" style="font-size:11px"></i> saved</span>' +
      '</div>' +
      '<div class="todo-add">' +
        '<input class="todo-input" placeholder="Jot an idea or task…" aria-label="Add a to-do" />' +
        '<button class="todo-addbtn" type="button" title="Add" aria-label="Add"><i class="ti ti-plus"></i></button>' +
      '</div>' +
      '<div class="todo-list">' + listHtml + '</div>' +
      doneHtml +
      '<div class="todo-foot"><span class="todo-hint">' +
        '<i class="ti ti-arrow-up-right" style="font-size:11px"></i> ↗ promotes an item into planning (coming soon)' +
      '</span></div>';

    wire(panel);
  }

  // ── wiring ────────────────────────────────────────────────────────────────────
  function wire(panel) {
    var st = panel._todoState;
    var input = panel.querySelector('.todo-input');

    var addBtn = panel.querySelector('.todo-addbtn');
    if (addBtn) addBtn.onclick = function () { add(panel, input.value); };
    if (input) {
      input.onkeydown = function (e) { if (e.key === 'Enter') add(panel, input.value); };
    }

    var doneToggle = panel.querySelector('.todo-done-toggle');
    if (doneToggle) {
      doneToggle.onclick = function () { panel.classList.toggle('done-collapsed'); };
    }

    panel.querySelectorAll('.todo-item').forEach(function (el) {
      var id = +el.dataset.id;

      var check = el.querySelector('.todo-check');
      if (check) {
        check.onclick = function () { toggle(panel, id); };
        check.onkeydown = function (e) {
          if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(panel, id); }
        };
      }

      var textEl = el.querySelector('.todo-text');
      if (textEl) textEl.ondblclick = function () { beginEdit(panel, id); };

      var editEl = el.querySelector('.todo-edit');
      if (editEl) {
        var commit = function () { commitEdit(panel, id, editEl.value); };
        editEl.onkeydown = function (e) {
          if (e.key === 'Enter') { e.preventDefault(); commit(); }
          else if (e.key === 'Escape') { st.editingId = null; render(panel); }
        };
        editEl.onblur = commit;
        editEl.focus();
        editEl.select();
      }

      var prom = el.querySelector('.promote');
      if (prom) prom.onclick = function () { promote(); };

      var del = el.querySelector('.del');
      if (del) del.onclick = function () { removeItem(panel, id); };

      // drag-to-reorder
      el.addEventListener('dragstart', function (e) {
        st.dragId = id;
        el.classList.add('dragging');
        if (e.dataTransfer) { e.dataTransfer.effectAllowed = 'move'; }
      });
      el.addEventListener('dragend', function () {
        el.classList.remove('dragging');
        panel.querySelectorAll('.drop-target').forEach(function (n) { n.classList.remove('drop-target'); });
      });
      el.addEventListener('dragover', function (e) {
        e.preventDefault();
        if (e.dataTransfer) e.dataTransfer.dropEffect = 'move';
        el.classList.add('drop-target');
      });
      el.addEventListener('dragleave', function () { el.classList.remove('drop-target'); });
      el.addEventListener('drop', function (e) {
        e.preventDefault();
        el.classList.remove('drop-target');
        if (st.dragId != null && st.dragId !== id) reorder(panel, st.dragId, id);
        st.dragId = null;
      });
    });
  }

  // ── mutations (each persists, then flashes "Saved") ────────────────────────
  function add(panel, text) {
    var st = panel._todoState;
    text = (text || '').trim();
    if (!text) return;
    apiCreate(st.project, text).then(function (created) {
      if (created) st.items.push(created);
      render(panel);
      flashSaved(panel);
      var input = panel.querySelector('.todo-input');
      if (input) input.focus();
    });
  }

  function toggle(panel, id) {
    var st = panel._todoState;
    var item = st.items.filter(function (i) { return i.id === id; })[0];
    if (!item) return;
    var next = !item.done;
    apiUpdate(st.project, id, { done: next }).then(function (upd) {
      if (upd) { item.done = upd.done; item.position = upd.position; }
      else { item.done = next; }
      render(panel);
      flashSaved(panel);
    });
  }

  function beginEdit(panel, id) {
    panel._todoState.editingId = id;
    render(panel);
  }

  function commitEdit(panel, id, text) {
    var st = panel._todoState;
    if (st.editingId !== id) return; // already committed/cancelled
    st.editingId = null;
    text = (text || '').trim();
    var item = st.items.filter(function (i) { return i.id === id; })[0];
    if (!item) { render(panel); return; }
    if (!text || text === item.text) { render(panel); return; }
    apiUpdate(st.project, id, { text: text }).then(function (upd) {
      item.text = upd ? upd.text : text;
      render(panel);
      flashSaved(panel);
    });
  }

  function removeItem(panel, id) {
    var st = panel._todoState;
    apiDelete(st.project, id).then(function (ok) {
      if (ok) st.items = st.items.filter(function (i) { return i.id !== id; });
      render(panel);
      flashSaved(panel);
    });
  }

  function reorder(panel, dragId, targetId) {
    var st = panel._todoState;
    var ordered = st.items.slice().sort(function (a, b) {
      if (a.position !== b.position) return a.position - b.position;
      return a.id - b.id;
    });
    var fromIdx = ordered.findIndex(function (i) { return i.id === dragId; });
    var toIdx = ordered.findIndex(function (i) { return i.id === targetId; });
    if (fromIdx < 0 || toIdx < 0 || fromIdx === toIdx) return;

    var moved = ordered.splice(fromIdx, 1)[0];
    ordered.splice(toIdx, 0, moved);

    // Reassign sequential positions; persist only the rows that actually moved.
    var changed = [];
    ordered.forEach(function (item, idx) {
      if (item.position !== idx) { item.position = idx; changed.push(item); }
    });
    render(panel);
    if (!changed.length) return;
    Promise.all(changed.map(function (item) {
      return apiUpdate(st.project, item.id, { position: item.position });
    })).then(function () { flashSaved(panel); });
  }

  // ── public mount ────────────────────────────────────────────────────────────
  function mount(container, project) {
    if (!container) return;
    injectStyles();
    container.classList.add('todo');
    container.dataset.project = project;
    container._todoState = { items: [], project: project, editingId: null, dragId: null };
    fetchList(project).then(function (items) {
      container._todoState.items = items || [];
      render(container);
    });
  }

  window.CommanderTodo = { mount: mount, promote: promote };
})();
