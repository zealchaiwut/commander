/* Shell tab routing (issue #797 extraction).
 *
 * switchTab, dropdown helpers, keyboard nav, and popstate deep-link handler.
 * Cross-module globals live on window from project.html inline scripts.
 */
/* eslint-disable no-unused-vars */
/* global _slug, _activeTab, _cachedFullRepo, _ticketsLoaded, _sprintMgmtLoaded,
          loadSprintMgmt, loadTickets, _smgmtArStopTicker, _smgmtArInit, _smgmtArStartTicker,
          _smgmtLivePollId, _smgmtLogPollId, _statusRefreshId, logsDestroy, deployTabDestroy,
          deployTabInit, ganttInit, compareInit, metricsInit, evaInit, calibInit, notesInit,
          roadmapInit, advInit, projSettingsInit, settingsInitValues, settingsPopulateRepos,
          globalSettingsLoad, _bcInitTab, _lpRenderBc, logsInit, _deepLinkSprintSubView,
          _applyDeepLinkSubView, _smgmtSavedSubView, _smgmtShowSubView, _histLoadLedger,
          _globalSettingsLinkActive, _ticketsRepo, _deepLinkView, _deepLinkFilter,
          _evlState, parseUrl, _arTickerId, _arInterval */
/* eslint-enable no-unused-vars */

export function switchTab(tab, pushHistory) {
  let _statusDeepLink = false;
  if (tab === 'status') {
    tab = 'metrics';
    _statusDeepLink = true;
  }

  if (_activeTab === 'sprint-mgmt' && tab !== 'sprint-mgmt') {
    if (_smgmtLivePollId !== null) {
      clearInterval(_smgmtLivePollId);
      _smgmtLivePollId = null;
    }
    if (_smgmtLogPollId !== null) {
      clearInterval(_smgmtLogPollId);
      _smgmtLogPollId = null;
    }
  }

  if (_activeTab === 'logs' && tab !== 'logs') {
    logsDestroy();
  }

  if (_activeTab === 'metrics' && tab !== 'metrics') {
    if (_statusRefreshId !== null) {
      clearInterval(_statusRefreshId);
      _statusRefreshId = null;
    }
  }

  if (_activeTab === 'deploy' && tab !== 'deploy') {
    deployTabDestroy();
  }

  _activeTab = tab;

  const onGlobalSettings = tab === 'global-settings';
  _globalSettingsLinkActive(onGlobalSettings);
  const projHeader = document.getElementById('proj-header');
  if (projHeader) projHeader.classList.toggle('hidden', onGlobalSettings);
  const subTabsRow = document.querySelector('.sub-tabs-row');
  if (subTabsRow) subTabsRow.classList.toggle('hidden', onGlobalSettings);

  const _topLevelTabs = ['sprint-mgmt', 'tickets', 'manage', 'planning', 'settings'];
  ['sprint-mgmt', 'tickets', 'logs', 'deploy', 'bulk-create', 'timeline', 'compare', 'metrics', 'est-vs-actual', 'calibration', 'notes', 'roadmap', 'advisor', 'settings'].forEach(t => {
    const btn = document.getElementById('stab-' + t);
    if (!btn) return;
    const isActive = !onGlobalSettings && t === tab;
    btn.classList.toggle('active', isActive);
    btn.setAttribute('aria-selected', String(isActive));
  });
  // Roving tabindex: active top-level tab gets 0; others get -1 so Tab exits the group
  _topLevelTabs.forEach(t => {
    const suffix = t === 'manage' ? 'manage-trigger' : t === 'planning' ? 'planning-trigger' : t;
    const btn = document.getElementById('stab-' + suffix);
    if (!btn) return;
    const isTopActive = !onGlobalSettings && (t === tab || btn.classList.contains('active'));
    btn.tabIndex = isTopActive ? 0 : -1;
  });
  closeAllStabDropdowns();
  ['analytics', 'more', 'planning', 'manage'].forEach(groupName => {
    const group = document.getElementById('stab-group-' + groupName);
    if (!group) return;
    const trigger = group.querySelector('.stab-trigger');
    if (trigger) trigger.classList.toggle('active', !!group.querySelector('.stab.active'));
  });

  ['sprint-mgmt', 'tickets', 'logs', 'deploy', 'bulk-create', 'timeline', 'compare', 'metrics', 'est-vs-actual', 'calibration', 'notes', 'roadmap', 'advisor', 'settings', 'global-settings'].forEach(t => {
    const pane = document.getElementById('pane-' + t);
    if (pane) pane.classList.toggle('active', t === tab);
  });

  const newUrl = '/project/' + encodeURIComponent(_slug) + '/' + tab;
  if (pushHistory !== false) {
    window.history.pushState({ slug: _slug, tab }, '', newUrl);
  }

  if (tab === 'tickets' && !_ticketsLoaded) {
    _ticketsLoaded = true;
    loadTickets();
  }

  if (tab === 'sprint-mgmt') {
    if (_deepLinkSprintSubView()) _applyDeepLinkSubView();
    else _smgmtShowSubView(_smgmtSavedSubView() || 'board');
  }

  if (tab === 'sprint-mgmt' && !_sprintMgmtLoaded && _cachedFullRepo[_slug]) {
    _sprintMgmtLoaded = true;
    loadSprintMgmt().then(() => _smgmtArInit());
    _histLoadLedger(_cachedFullRepo[_slug]);
  } else if (tab === 'sprint-mgmt' && _sprintMgmtLoaded) {
    if (_arTickerId === null && _arInterval > 0) _smgmtArStartTicker();
  }

  if (tab === 'bulk-create') {
    _bcInitTab();
    _lpRenderBc();
  }
  if (tab === 'logs') logsInit();
  if (tab === 'deploy') deployTabInit();
  if (tab === 'timeline') ganttInit();
  if (tab === 'compare') compareInit();
  if (tab === 'metrics') {
    metricsInit();
    if (_statusDeepLink && typeof window.anlShowTab === 'function') {
      window.anlShowTab('status');
    }
  }
  if (tab === 'est-vs-actual') evaInit();
  if (tab === 'calibration') calibInit();
  if (tab === 'notes') notesInit();
  if (tab === 'roadmap') roadmapInit();
  if (tab === 'advisor') advInit();
  if (tab === 'settings') projSettingsInit();
  if (tab === 'global-settings') {
    settingsInitValues();
    settingsPopulateRepos();
    globalSettingsLoad();
  }

  if (typeof window._smgmtUpdateSelectionUI === 'function') window._smgmtUpdateSelectionUI();
  if (typeof window._bulkUpdateActionBar === 'function') window._bulkUpdateActionBar();
  if (typeof window._smgmtUpdateToolbarTop === 'function') window._smgmtUpdateToolbarTop();
}

export function toggleStabDropdown(name, e) {
  e.stopPropagation();
  const group = document.getElementById('stab-group-' + name);
  const isOpen = group.classList.contains('open');
  closeAllStabDropdowns();
  if (!isOpen) group.classList.add('open');
}

export function closeAllStabDropdowns() {
  document.querySelectorAll('.stab-group.open').forEach(g => g.classList.remove('open'));
}

document.addEventListener('click', closeAllStabDropdowns);

const _subTabsEl = document.getElementById('sub-tabs');
if (_subTabsEl) {
  _subTabsEl.addEventListener('keydown', function(e) {
    const enabledTabs = ['sprint-mgmt', 'tickets', 'manage', 'logs', 'deploy', 'metrics', 'planning', 'roadmap', 'advisor', 'settings'];
    const focused = document.activeElement;
    const currentId = focused ? focused.id.replace('stab-', '') : null;
    const currentIdx = enabledTabs.indexOf(currentId);
    if (currentIdx < 0) return;

    if (e.key === 'ArrowRight') {
      e.preventDefault();
      const next = enabledTabs[(currentIdx + 1) % enabledTabs.length];
      document.getElementById('stab-' + next).focus();
    } else if (e.key === 'ArrowLeft') {
      e.preventDefault();
      const prev = enabledTabs[(currentIdx - 1 + enabledTabs.length) % enabledTabs.length];
      document.getElementById('stab-' + prev).focus();
    } else if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      if (currentId) switchTab(currentId);
    }
  });
}

window.addEventListener('popstate', function(e) {
  const { slug, tab, view, filter } = parseUrl();
  const effSlug = slug || (e.state && e.state.slug);
  const effTab = (slug ? tab : (e.state && e.state.tab)) || 'sprint-mgmt';
  if (!effSlug) return;
  if (effSlug !== _slug) {
    _ticketsRepo = null;
    _ticketsLoaded = false;
  }
  _slug = effSlug;
  _deepLinkView = view;
  _deepLinkFilter = filter;
  _evlState.errorsOnly = (filter === 'errors');
  switchTab(effTab, false);
});
