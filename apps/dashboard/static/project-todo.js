/* Per-project to-do panel (issue #844).
 *
 * Renders on the home page (right of each project block) and as a docked panel
 * in the project view. Full-screen mode (Messenger-style) and screenshot
 * attachments share one module + API (/api/projects/<project>/todos).
 */
(function () {
  'use strict';

  var _states = {};
  var _fsOpen = false;
  var HOME_LIST_LIMIT = 5;

  var TODO_CSS = [
    '.todo{padding:13px 16px;display:flex;flex-direction:column;min-height:100%}',
    '.todo.compact{max-height:min(52vh,480px);overflow:hidden}',
    '.todo.todo-dock-inner{padding-top:10px}',
    '.todo.compact .todo-scroll{overflow:auto;flex:1;display:flex;flex-direction:column;min-height:0}',
    '.todo-head{display:flex;align-items:center;gap:7px;margin-bottom:10px;flex-shrink:0}',
    ".todo-title{font-size:12px;font-weight:700;letter-spacing:.03em;text-transform:uppercase;color:var(--text-sub);display:flex;align-items:center;gap:6px}",
    '.todo-count{font-family:var(--mono);font-size:11px;color:var(--text-sub)}',
    '.todo-saved{margin-left:auto;font-family:var(--mono);font-size:10px;color:var(--green);opacity:0;transition:opacity .2s;display:inline-flex;align-items:center;gap:3px}',
    '.todo-saved.show{opacity:1}',
    '.todo-fsbtn{margin-left:6px;background:none;border:none;color:var(--text-sub);cursor:pointer;padding:4px 6px;border-radius:5px;display:inline-flex;align-items:center;font-size:15px}',
    '.todo-fsbtn:hover{background:var(--surface-hover);color:var(--blue)}',
    '.todo-add{display:flex;gap:6px;margin-bottom:10px;flex-shrink:0}',
    '.todo-input{flex:1;border:1px solid var(--border);background:var(--surface);color:var(--text);border-radius:7px;padding:8px 11px;font-size:13px;font-family:inherit}',
    '.todo-input:focus{outline:none;border-color:var(--blue);box-shadow:0 0 0 3px var(--blue-bg)}',
    '.todo-input::placeholder{color:var(--text-muted)}',
    '.todo-addbtn{border:1px solid var(--border);background:var(--text);color:var(--surface);border-radius:7px;width:34px;display:flex;align-items:center;justify-content:center;cursor:pointer;font-size:16px;flex-shrink:0}',
    '.todo-addbtn:hover{opacity:.88}',
    '.todo-list{display:flex;flex-direction:column;gap:2px;flex:1}',
    '.todo-item{display:flex;flex-direction:column;gap:6px;padding:7px 8px;border-radius:7px;transition:background .12s;position:relative}',
    '.todo-item:hover,.todo-item.focused{background:var(--surface-hover)}',
    '.todo-item.dragging{opacity:.5}',
    '.todo-item.drop-target{box-shadow:inset 0 2px 0 var(--blue)}',
    '.todo-row{display:flex;align-items:flex-start;gap:9px}',
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
    '.todo-item:hover .todo-actions,.todo-item.focused .todo-actions{opacity:1}',
    '.todo-grip{color:var(--text-sub);cursor:grab;display:flex;align-items:center;font-size:14px;padding:3px 1px;touch-action:none}',
    '.todo-grip:active{cursor:grabbing}',
    '.todo-iconbtn{background:none;border:none;color:var(--text-sub);cursor:pointer;padding:3px;border-radius:4px;display:flex;align-items:center;font-size:14px}',
    '.todo-iconbtn:hover{background:var(--surface-2);color:var(--text)}',
    '.todo-iconbtn.edit:hover{color:var(--blue)}',
    '.todo-iconbtn.attach:hover{color:var(--purple)}',
    '.todo-iconbtn.del:hover{color:var(--red)}',
    '.todo-attaches{display:flex;flex-wrap:wrap;gap:6px;padding-left:26px}',
    '.todo-thumb{position:relative;width:72px;height:54px;border-radius:6px;overflow:hidden;border:1px solid var(--border);background:var(--surface-2);display:block}',
    '.todo-thumb img{width:100%;height:100%;object-fit:cover;display:block}',
    '.todo-thumb-del{position:absolute;top:2px;right:2px;width:18px;height:18px;border-radius:4px;border:none;background:rgba(0,0,0,.55);color:#fff;font-size:11px;cursor:pointer;display:none;align-items:center;justify-content:center;padding:0}',
    '.todo-thumb:hover .todo-thumb-del{display:flex}',
    '.todo-empty{font-size:12px;color:var(--text-sub);font-style:italic;padding:10px 8px;text-align:center}',
    '.todo-done-bar{display:flex;align-items:center;gap:6px;margin-top:8px;flex-shrink:0}',
    '.todo-done-toggle{flex:1;font-family:var(--mono);font-size:11px;color:var(--text-sub);cursor:pointer;display:flex;align-items:center;gap:5px;padding:5px 8px;border-radius:6px;border:none;background:none;text-align:left}',
    '.todo-done-toggle:hover{background:var(--surface-hover);color:var(--text-muted)}',
    '.todo-done-toggle .ti{transition:transform .15s}',
    '.todo-done-clear{font-family:var(--mono);font-size:10px;color:var(--text-sub);cursor:pointer;padding:5px 8px;border-radius:6px;border:none;background:none;flex-shrink:0}',
    '.todo-done-clear:hover{background:var(--surface-hover);color:var(--red)}',
    '.todo.done-collapsed .todo-done-toggle .ti{transform:rotate(-90deg)}',
    '.todo.done-collapsed .todo-done-list{display:none}',
    '.todo-foot{margin-top:6px;border-top:1px dashed var(--border);padding-top:8px;flex-shrink:0}',
    '.todo-hint{font-size:10.5px;color:var(--text-sub);display:flex;align-items:center;gap:5px;font-family:var(--mono)}',
    '.todo-more{font-size:11px;color:var(--text-sub);padding:6px 8px;font-family:var(--mono)}',
    '.todo-dock{position:fixed;right:20px;bottom:20px;width:320px;max-width:calc(100vw - 40px);background:var(--surface);border:1px solid var(--border);border-radius:12px;box-shadow:0 10px 30px rgba(0,0,0,.18);z-index:60;display:flex;flex-direction:column;overflow:hidden}',
    '.todo-dock.collapsed .todo-dock-body{display:none}',
    '.todo-dock-bar{display:flex;align-items:center;gap:8px;padding:10px 13px;cursor:pointer;font-size:13px;font-weight:600;color:var(--text);border-bottom:1px solid var(--border)}',
    '.todo-dock.collapsed .todo-dock-bar{border-bottom:none}',
    '.todo-dock-expand{margin-left:auto;background:none;border:none;color:var(--text-sub);cursor:pointer;padding:4px 6px;border-radius:5px;font-size:14px;display:inline-flex;align-items:center}',
    '.todo-dock-expand:hover{color:var(--blue);background:var(--surface-hover)}',
    '.todo-dock-bar .ti-chevron-down{margin-left:4px;transition:transform .15s;flex-shrink:0}',
    '.todo-dock.collapsed .todo-dock-bar .ti-chevron-down{transform:rotate(180deg)}',
    '.todo-dock-body{max-height:min(60vh,520px);overflow:auto}',
    '@media(max-width:560px){.todo-dock{right:12px;left:12px;width:auto}}',
    '.todo-fs{position:fixed;inset:0;z-index:400;background:var(--bg);display:flex;flex-direction:column}',
    '.todo-fs.hidden{display:none!important}',
    '.todo-fs-bar{display:flex;align-items:center;gap:10px;padding:12px 16px;border-bottom:1px solid var(--border);background:var(--surface);flex-shrink:0}',
    '.todo-fs-back{background:none;border:1px solid var(--border);border-radius:8px;width:36px;height:36px;display:flex;align-items:center;justify-content:center;cursor:pointer;color:var(--text);font-size:18px}',
    '.todo-fs-back:hover{background:var(--surface-hover)}',
    '.todo-fs-title{font-size:15px;font-weight:700;color:var(--text)}',
    '.todo-fs-sub{font-size:11px;color:var(--text-sub);font-family:var(--mono)}',
    '.todo-fs-body{flex:1;overflow:hidden;display:flex;flex-direction:column}',
    '.todo-fs-body .todo{max-width:720px;margin:0 auto;width:100%;height:100%;padding:16px 20px 24px}',
    '.todo-fs-body .todo-scroll{overflow:auto;flex:1}',
    '.todo-lightbox{position:fixed;inset:0;z-index:500;background:rgba(0,0,0,.82);display:flex;align-items:center;justify-content:center;padding:24px}',
    '.todo-lightbox.hidden{display:none!important}',
    '.todo-lightbox img{max-width:100%;max-height:100%;border-radius:8px;box-shadow:0 8px 40px rgba(0,0,0,.4)}',
    '.todo-lightbox-close{position:absolute;top:16px;right:16px;background:rgba(255,255,255,.15);border:none;color:#fff;width:40px;height:40px;border-radius:8px;cursor:pointer;font-size:22px}',
    '.todo-toast{position:fixed;left:50%;bottom:26px;transform:translateX(-50%) translateY(8px);background:var(--text);color:var(--surface);font-size:12.5px;padding:9px 15px;border-radius:8px;box-shadow:0 6px 20px rgba(0,0,0,.22);z-index:120;opacity:0;transition:opacity .18s,transform .18s;pointer-events:none;display:flex;align-items:center;gap:7px}',
    '.todo-toast.show{opacity:1;transform:translateX(-50%) translateY(0)}',
    '@media (prefers-reduced-motion: reduce){.todo *,.todo-saved,.todo-toast,.todo-dock-bar .ti,.todo-fs{transition:none!important;animation:none!important}}'
  ].join('');

  function injectStyles() {
    if (document.getElementById('commander-todo-styles')) return;
    var s = document.createElement('style');
    s.id = 'commander-todo-styles';
    s.textContent = TODO_CSS;
    document.head.appendChild(s);
  }

  function ensureShells() {
    if (!document.getElementById('todo-fs-root')) {
      var fs = document.createElement('div');
      fs.id = 'todo-fs-root';
      fs.className = 'todo-fs hidden';
      fs.innerHTML =
        '<div class="todo-fs-bar">' +
          '<button type="button" class="todo-fs-back" aria-label="Close full screen"><i class="ti ti-arrow-left"></i></button>' +
          '<div><div class="todo-fs-title" id="todo-fs-title">To-do</div><div class="todo-fs-sub" id="todo-fs-sub"></div></div>' +
        '</div>' +
        '<div class="todo-fs-body"><div class="todo" id="todo-fs-panel"></div></div>';
      document.body.appendChild(fs);
      fs.querySelector('.todo-fs-back').onclick = closeFullscreen;
    }
    if (!document.getElementById('todo-lightbox')) {
      var lb = document.createElement('div');
      lb.id = 'todo-lightbox';
      lb.className = 'todo-lightbox hidden';
      lb.innerHTML =
        '<button type="button" class="todo-lightbox-close" aria-label="Close">&times;</button>' +
        '<img id="todo-lightbox-img" alt="Attachment preview" />';
      document.body.appendChild(lb);
      lb.querySelector('.todo-lightbox-close').onclick = function () {
        lb.classList.add('hidden');
      };
      lb.onclick = function (e) {
        if (e.target === lb) lb.classList.add('hidden');
      };
    }
  }

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function apiBase(project) {
    return '/api/projects/' + encodeURIComponent(project) + '/todos';
  }

  function getState(project) {
    if (!_states[project]) {
      _states[project] = {
        items: [],
        project: project,
        editingId: null,
        dragId: null,
        focusId: null,
        panels: [],
      };
    }
    return _states[project];
  }

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

  function apiClearDone(project) {
    return fetch(apiBase(project) + '/done', { method: 'DELETE' })
      .then(function (r) { return r.ok ? r.json() : null; });
  }

  function apiUploadAttachment(project, id, file) {
    var fd = new FormData();
    fd.append('file', file, file.name || 'screenshot.png');
    return fetch(apiBase(project) + '/' + id + '/attachments', {
      method: 'POST',
      body: fd,
    }).then(function (r) { return r.ok ? r.json() : null; });
  }

  function apiDeleteAttachment(project, id, name) {
    return fetch(
      apiBase(project) + '/' + id + '/attachments/' + encodeURIComponent(name),
      { method: 'DELETE' }
    ).then(function (r) { return r.ok; });
  }

  function flashSaved(panel) {
    var el = panel.querySelector('.todo-saved');
    if (!el) return;
    el.classList.add('show');
    clearTimeout(el._t);
    el._t = setTimeout(function () { el.classList.remove('show'); }, 1100);
  }

  function showToast(msg) {
    var t = document.createElement('div');
    t.className = 'todo-toast';
    t.textContent = msg;
    document.body.appendChild(t);
    requestAnimationFrame(function () { t.classList.add('show'); });
    setTimeout(function () {
      t.classList.remove('show');
      setTimeout(function () { if (t.parentNode) t.parentNode.removeChild(t); }, 250);
    }, 1900);
  }

  function showComingSoon() {
    showToast('Promote to a ticket is coming soon');
  }

  function promote() { showComingSoon(); }

  function attachHtml(item) {
    var atts = item.attachments || [];
    if (!atts.length) return '';
    return '<div class="todo-attaches">' + atts.map(function (a) {
      return '<span class="todo-thumb" data-url="' + esc(a.url) + '">' +
        '<img src="' + esc(a.url) + '" alt="' + esc(a.name) + '" loading="lazy" />' +
        '<button type="button" class="todo-thumb-del" data-name="' + esc(a.name) + '" title="Remove attachment">&times;</button>' +
      '</span>';
    }).join('') + '</div>';
  }

  function row(panel, item) {
    var st = getState(panel._todoProject);
    var doneCls = item.done ? ' done' : '';
    var focused = st.focusId === item.id ? ' focused' : '';
    var editing = st.editingId === item.id;
    var textCell = editing
      ? '<input class="todo-edit" value="' + esc(item.text) + '" />'
      : '<span class="todo-text" title="Double-click to edit">' + esc(item.text) + '</span>';
    return '' +
      '<div class="todo-item' + doneCls + focused + '" data-id="' + item.id + '"' +
           (editing ? '' : ' draggable="true"') + '>' +
        '<div class="todo-row">' +
          '<span class="todo-check" role="checkbox" aria-checked="' + (item.done ? 'true' : 'false') +
            '" tabindex="0" title="Toggle done"><i class="ti ti-check"></i></span>' +
          textCell +
          '<span class="todo-actions">' +
            '<span class="todo-grip" title="Drag to reorder" aria-hidden="true"><i class="ti ti-grip-vertical"></i></span>' +
            '<button class="todo-iconbtn attach" type="button" title="Attach screenshot" aria-label="Attach screenshot"><i class="ti ti-paperclip"></i></button>' +
            '<button class="todo-iconbtn edit" type="button" title="Edit" aria-label="Edit"><i class="ti ti-pencil"></i></button>' +
            '<button class="todo-iconbtn del" type="button" title="Delete" aria-label="Delete"><i class="ti ti-x"></i></button>' +
          '</span>' +
        '</div>' +
        attachHtml(item) +
        '<input type="file" class="todo-file-input" accept="image/png,image/jpeg,image/gif,image/webp" hidden />' +
      '</div>';
  }

  function renderPanel(panel) {
    var st = getState(panel._todoProject);
    var isFs = panel.id === 'todo-fs-panel';
    var isDock = panel.id === 'todo-dock-panel';
    var isCompact = !isFs;
    var isHomeEmbed = isCompact && !isDock && panel.closest && panel.closest('.pb-cell-todo');

    var items = st.items.slice().sort(function (a, b) {
      if (a.position !== b.position) return a.position - b.position;
      return a.id - b.id;
    });
    var active = items.filter(function (i) { return !i.done; });
    var done = items.filter(function (i) { return i.done; });

    var activeShown = isHomeEmbed ? active.slice(0, HOME_LIST_LIMIT) : active;
    var activeHidden = isHomeEmbed ? Math.max(0, active.length - HOME_LIST_LIMIT) : 0;

    var listHtml = activeShown.length
      ? activeShown.map(function (i) { return row(panel, i); }).join('') +
        (activeHidden
          ? '<div class="todo-more">+' + activeHidden + ' more — open full screen</div>'
          : '')
      : '<div class="todo-empty">No to-dos yet — capture an idea above.</div>';

    var doneHtml = (!isHomeEmbed && done.length)
      ? '<div class="todo-done-bar">' +
          '<button class="todo-done-toggle" type="button">' +
            '<i class="ti ti-chevron-down" style="font-size:13px"></i> Done (' + done.length + ')' +
          '</button>' +
          '<button class="todo-done-clear" type="button" title="Remove all completed items">Clear done</button>' +
        '</div>' +
        '<div class="todo-done-list">' +
          done.map(function (i) { return row(panel, i); }).join('') +
        '</div>'
      : '';

    var fsBtn = (isCompact && !isDock)
      ? '<button type="button" class="todo-fsbtn" title="Open full screen"><i class="ti ti-arrows-maximize"></i></button>'
      : '';

    var headHtml = isDock ? '' : (
      '<div class="todo-head">' +
        '<span class="todo-title"><i class="ti ti-checklist" style="font-size:14px"></i> To-do</span>' +
        '<span class="todo-count">' + active.length + '</span>' +
        '<span class="todo-saved"><i class="ti ti-check" style="font-size:11px"></i> saved</span>' +
        fsBtn +
      '</div>'
    );

    var scrollWrapOpen = isCompact ? '<div class="todo-scroll">' : '';
    var scrollWrapClose = isCompact ? '</div>' : '';

    panel.innerHTML = scrollWrapOpen +
      headHtml +
      '<div class="todo-add">' +
        '<input class="todo-input" placeholder="Jot an idea or task…" aria-label="Add a to-do" />' +
        '<button class="todo-addbtn" type="button" title="Add" aria-label="Add"><i class="ti ti-plus"></i></button>' +
      '</div>' +
      '<div class="todo-list">' + listHtml + '</div>' +
      doneHtml +
      '<div class="todo-foot"><span class="todo-hint">' +
        '<i class="ti ti-paperclip" style="font-size:11px"></i> Paste a screenshot to add a new to-do' +
      '</span></div>' +
      scrollWrapClose;

    wire(panel);
  }

  function renderAll(project) {
    getState(project).panels.forEach(renderPanel);
  }

  function wire(panel) {
    var st = getState(panel._todoProject);
    var input = panel.querySelector('.todo-input');

    var fsBtn = panel.querySelector('.todo-fsbtn');
    if (fsBtn) {
      fsBtn.onclick = function (e) {
        e.stopPropagation();
        openFullscreen(st.project);
      };
    }

    var addBtn = panel.querySelector('.todo-addbtn');
    if (addBtn) addBtn.onclick = function () { add(panel, input.value); };
    if (input) {
      input.onkeydown = function (e) { if (e.key === 'Enter') add(panel, input.value); };
    }

    var doneToggle = panel.querySelector('.todo-done-toggle');
    if (doneToggle) {
      doneToggle.onclick = function () { panel.classList.toggle('done-collapsed'); };
    }

    var clearDoneBtn = panel.querySelector('.todo-done-clear');
    if (clearDoneBtn) {
      clearDoneBtn.onclick = function (e) {
        e.stopPropagation();
        clearDone(st.project);
      };
    }

    panel.onpaste = function (e) {
      var items = e.clipboardData && e.clipboardData.items;
      if (!items) return;
      for (var i = 0; i < items.length; i++) {
        if (items[i].type && items[i].type.indexOf('image') === 0) {
          e.preventDefault();
          var blob = items[i].getAsFile();
          if (blob) addFromScreenshot(st.project, blob);
          break;
        }
      }
    };

    panel.querySelectorAll('.todo-item').forEach(function (el) {
      var id = +el.dataset.id;

      el.addEventListener('mouseenter', function () { st.focusId = id; });
      el.addEventListener('click', function () { st.focusId = id; renderAll(st.project); });

      var check = el.querySelector('.todo-check');
      if (check) {
        check.onclick = function (e) { e.stopPropagation(); toggle(st.project, id); };
        check.onkeydown = function (e) {
          if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(st.project, id); }
        };
      }

      var textEl = el.querySelector('.todo-text');
      if (textEl) textEl.ondblclick = function () { beginEdit(st.project, id); };

      var editEl = el.querySelector('.todo-edit');
      if (editEl) {
        var commit = function () { commitEdit(st.project, id, editEl.value); };
        editEl.onkeydown = function (e) {
          if (e.key === 'Enter') { e.preventDefault(); commit(); }
          else if (e.key === 'Escape') { st.editingId = null; renderAll(st.project); }
        };
        editEl.onblur = commit;
        editEl.focus();
        editEl.select();
      }

      var attachBtn = el.querySelector('.attach');
      var fileInput = el.querySelector('.todo-file-input');
      if (attachBtn && fileInput) {
        attachBtn.onclick = function (e) {
          e.stopPropagation();
          st.focusId = id;
          fileInput.click();
        };
        fileInput.onchange = function () {
          if (fileInput.files && fileInput.files[0]) {
            uploadAttachment(st.project, id, fileInput.files[0]);
            fileInput.value = '';
          }
        };
      }

      var editBtn = el.querySelector('.edit');
      if (editBtn) editBtn.onclick = function (e) { e.stopPropagation(); beginEdit(st.project, id); };

      var del = el.querySelector('.del');
      if (del) del.onclick = function (e) { e.stopPropagation(); removeItem(st.project, id); };

      el.querySelectorAll('.todo-thumb').forEach(function (thumb) {
        var img = thumb.querySelector('img');
        if (img) {
          img.onclick = function (e) {
            e.stopPropagation();
            openLightbox(img.src);
          };
        }
        var rm = thumb.querySelector('.todo-thumb-del');
        if (rm) {
          rm.onclick = function (e) {
            e.stopPropagation();
            removeAttachment(st.project, id, rm.dataset.name);
          };
        }
      });

      el.addEventListener('dragstart', function (e) {
        st.dragId = id;
        el.classList.add('dragging');
        if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move';
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
        if (st.dragId != null && st.dragId !== id) reorder(st.project, st.dragId, id);
        st.dragId = null;
      });
    });
  }

  function openLightbox(src) {
    var lb = document.getElementById('todo-lightbox');
    var img = document.getElementById('todo-lightbox-img');
    if (!lb || !img) return;
    img.src = src;
    lb.classList.remove('hidden');
  }

  function openFullscreen(project, label) {
    ensureShells();
    var st = getState(project);
    var root = document.getElementById('todo-fs-root');
    var panel = document.getElementById('todo-fs-panel');
    if (!root || !panel) return;

    document.getElementById('todo-fs-title').textContent = (label || project) + ' — To-do';
    document.getElementById('todo-fs-sub').textContent = 'Paste a screenshot to add a new to-do';

    if (st.panels.indexOf(panel) < 0) {
      panel.classList.add('todo');
      panel._todoProject = project;
      st.panels.push(panel);
    }

    root.classList.remove('hidden');
    _fsOpen = true;
    document.body.style.overflow = 'hidden';
    renderAll(project);

    var dock = document.getElementById('todo-dock');
    if (dock) dock.classList.add('collapsed');

    fetchList(project).then(function (items) {
      st.items = items || [];
      renderAll(project);
      var input = panel.querySelector('.todo-input');
      if (input) input.focus();
    });
  }

  function closeFullscreen() {
    var root = document.getElementById('todo-fs-root');
    var panel = document.getElementById('todo-fs-panel');
    if (root) root.classList.add('hidden');
    _fsOpen = false;
    document.body.style.overflow = '';
    if (panel) {
      var st = getState(panel._todoProject);
      var idx = st.panels.indexOf(panel);
      if (idx >= 0) st.panels.splice(idx, 1);
    }
  }

  function refresh(project) {
    return fetchList(project).then(function (items) {
      getState(project).items = items || [];
      renderAll(project);
    });
  }

  function add(panel, text) {
    var project = panel._todoProject;
    text = (text || '').trim();
    if (!text) return;
    apiCreate(project, text).then(function (created) {
      if (created) getState(project).items.push(created);
      renderAll(project);
      flashSaved(panel);
      var input = panel.querySelector('.todo-input');
      if (input) input.focus();
    });
  }

  function addFromScreenshot(project, file) {
    apiCreate(project, 'Screenshot').then(function (created) {
      if (!created) {
        showToast('Failed to create to-do from screenshot');
        return;
      }
      var st = getState(project);
      st.items.push(created);
      st.focusId = created.id;
      renderAll(project);
      apiUploadAttachment(project, created.id, file).then(function (att) {
        if (!att) {
          showToast('To-do created but screenshot attach failed');
          return refresh(project);
        }
        return refresh(project).then(function () {
          st.focusId = created.id;
          st.panels.forEach(flashSaved);
        });
      });
    });
  }

  function toggle(project, id) {
    var item = getState(project).items.filter(function (i) { return i.id === id; })[0];
    if (!item) return;
    var next = !item.done;
    apiUpdate(project, id, { done: next }).then(function (upd) {
      if (upd) { item.done = upd.done; item.position = upd.position; item.attachments = upd.attachments || item.attachments; }
      else { item.done = next; }
      renderAll(project);
      getState(project).panels.forEach(flashSaved);
    });
  }

  function beginEdit(project, id) {
    getState(project).editingId = id;
    renderAll(project);
  }

  function commitEdit(project, id, text) {
    var st = getState(project);
    if (st.editingId !== id) return;
    st.editingId = null;
    text = (text || '').trim();
    var item = st.items.filter(function (i) { return i.id === id; })[0];
    if (!item) { renderAll(project); return; }
    if (!text || text === item.text) { renderAll(project); return; }
    apiUpdate(project, id, { text: text }).then(function (upd) {
      item.text = upd ? upd.text : text;
      renderAll(project);
      st.panels.forEach(flashSaved);
    });
  }

  function removeItem(project, id) {
    apiDelete(project, id).then(function (ok) {
      if (ok) getState(project).items = getState(project).items.filter(function (i) { return i.id !== id; });
      renderAll(project);
      getState(project).panels.forEach(flashSaved);
    });
  }

  function clearDone(project) {
    apiClearDone(project).then(function (body) {
      if (!body) {
        getState(project).items = getState(project).items.filter(function (i) { return !i.done; });
        renderAll(project);
        return refresh(project);
      }
      return refresh(project).then(function () {
        getState(project).panels.forEach(flashSaved);
      });
    });
  }

  function uploadAttachment(project, id, file) {
    apiUploadAttachment(project, id, file).then(function (att) {
      if (!att) {
        showToast('Failed to attach image');
        return;
      }
      return refresh(project).then(function () {
        getState(project).panels.forEach(flashSaved);
      });
    });
  }

  function removeAttachment(project, id, name) {
    apiDeleteAttachment(project, id, name).then(function (ok) {
      if (!ok) return;
      refresh(project);
    });
  }

  function reorder(project, dragId, targetId) {
    var st = getState(project);
    var ordered = st.items.slice().sort(function (a, b) {
      if (a.position !== b.position) return a.position - b.position;
      return a.id - b.id;
    });
    var fromIdx = ordered.findIndex(function (i) { return i.id === dragId; });
    var toIdx = ordered.findIndex(function (i) { return i.id === targetId; });
    if (fromIdx < 0 || toIdx < 0 || fromIdx === toIdx) return;

    var moved = ordered.splice(fromIdx, 1)[0];
    ordered.splice(toIdx, 0, moved);

    var changed = [];
    ordered.forEach(function (item, idx) {
      if (item.position !== idx) { item.position = idx; changed.push(item); }
    });
    renderAll(project);
    if (!changed.length) return;
    Promise.all(changed.map(function (item) {
      return apiUpdate(project, item.id, { position: item.position });
    })).then(function () { st.panels.forEach(flashSaved); });
  }

  function mount(container, project, opts) {
    if (!container) return;
    injectStyles();
    ensureShells();
    opts = opts || {};
    container.classList.add('todo');
    if (container.id === 'todo-dock-panel') container.classList.add('todo-dock-inner');
    if (opts.compact !== false && container.id !== 'todo-fs-panel') {
      container.classList.add('compact');
    }
    container._todoProject = project;
    var st = getState(project);
    if (st.panels.indexOf(container) < 0) st.panels.push(container);

    fetchList(project).then(function (items) {
      st.items = items || [];
      renderAll(project);
    });
  }

  window.CommanderTodo = {
    mount: mount,
    promote: promote,
    openFullscreen: openFullscreen,
    closeFullscreen: closeFullscreen,
  };
})();
