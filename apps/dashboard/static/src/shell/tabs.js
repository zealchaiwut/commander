/* Shell tab routing (issue #797 extraction).
 *
 * switchTab, dropdown helpers, keyboard nav, and popstate deep-link handler.
 * Cross-module globals live on window from project.html inline scripts.
 */
/* global _slug, _activeTab, _cachedFullRepo, _sprintMgmtLoaded,
          loadSprintMgmt, loadTickets, _smgmtArInit, _smgmtArStartTicker,
          _smgmtLivePollId, _smgmtLogPollId, deployTabDestroy,
          deployTabInit,
          projSettingsInit, settingsInitValues, settingsPopulateRepos,
          globalSettingsLoad, _bcInitTab, _lpRenderBc, _deepLinkSprintSubView,
          _applyDeepLinkSubView, _smgmtSavedSubView, _smgmtShowSubView, _histLoadLedger,
          _globalSettingsLinkActive, _evlState, parseUrl, _arTickerId, _arInterval,
          failuresInit, brainInit */

// Maps each dropdown group to its child tab ids so the roving tabindex
// can assign tabIndex=0 to the group trigger without relying on .active class
// state being set first (fixes issue #1175).
const _GROUP_CHILDREN = {
  manage: ["deploy", "bulk-create"],
};

/**
 * Compute roving tabindex assignments for the top-level tab strip.
 * Returns an object mapping each top-level key to 0 (Tab-reachable) or -1.
 * Exactly one key gets 0 unless onGlobalSettings is true (strip is hidden).
 */
export function computeRovingTabindex(tab, onGlobalSettings) {
  return Object.fromEntries(
    ["sprint-mgmt", "tickets", "failures", "brain", "manage", "settings"].map(
      (t) => {
        const ownsTab =
          !onGlobalSettings &&
          (t === tab ||
            (_GROUP_CHILDREN[t] && _GROUP_CHILDREN[t].includes(tab)));
        return [t, ownsTab ? 0 : -1];
      },
    ),
  );
}

export function switchTab(tab, pushHistory) {
  // Removed tabs (#2025): redirect to failures instead of blank pane.
  // console.warn makes stale internal callers visible in development (#2063).
  if (tab === "metrics" || tab === "logs" || tab === "status") {
    console.warn(
      '[tabs] switchTab("' +
        tab +
        '"): tab removed in #2025, redirecting to "failures"',
    );
    tab = "failures";
  }

  if (_activeTab === "sprint-mgmt" && tab !== "sprint-mgmt") {
    if (_smgmtLivePollId !== null) {
      clearInterval(_smgmtLivePollId);
      _smgmtLivePollId = null;
    }
    if (_smgmtLogPollId !== null) {
      clearInterval(_smgmtLogPollId);
      _smgmtLogPollId = null;
    }
  }

  if (_activeTab === "deploy" && tab !== "deploy") {
    deployTabDestroy();
  }

  _activeTab = tab;

  const onGlobalSettings = tab === "global-settings";
  _globalSettingsLinkActive(onGlobalSettings);
  const projHeader = document.getElementById("proj-header");
  if (projHeader) projHeader.classList.toggle("hidden", onGlobalSettings);
  const subTabsRow = document.querySelector(".sub-tabs-row");
  if (subTabsRow) subTabsRow.classList.toggle("hidden", onGlobalSettings);

  const _topLevelTabs = [
    "sprint-mgmt",
    "tickets",
    "failures",
    "brain",
    "manage",
    "settings",
  ];
  [
    "sprint-mgmt",
    "tickets",
    "deploy",
    "bulk-create",
    "failures",
    "brain",
    "settings",
  ].forEach((t) => {
    const btn = document.getElementById("stab-" + t);
    if (!btn) return;
    const isActive = !onGlobalSettings && t === tab;
    btn.classList.toggle("active", isActive);
    btn.setAttribute("aria-selected", String(isActive));
  });
  // Roving tabindex: exactly one top-level element gets tabIndex=0; group triggers
  // own the slot when a child tab is active. Uses explicit group membership so the
  // result is correct regardless of when .active classes are applied (issue #1175).
  const _rovingMap = computeRovingTabindex(tab, onGlobalSettings);
  _topLevelTabs.forEach((t) => {
    const suffix = t === "manage" ? "manage-trigger" : t;
    const btn = document.getElementById("stab-" + suffix);
    if (!btn) return;
    btn.tabIndex = _rovingMap[t];
  });
  closeAllStabDropdowns();
  ["manage"].forEach((groupName) => {
    const group = document.getElementById("stab-group-" + groupName);
    if (!group) return;
    const trigger = group.querySelector(".stab-trigger");
    if (trigger)
      trigger.classList.toggle("active", !!group.querySelector(".stab.active"));
  });

  [
    "sprint-mgmt",
    "tickets",
    "deploy",
    "bulk-create",
    "failures",
    "brain",
    "settings",
    "global-settings",
  ].forEach((t) => {
    const pane = document.getElementById("pane-" + t);
    if (pane) pane.classList.toggle("active", t === tab);
  });

  const newUrl = "/project/" + encodeURIComponent(_slug) + "/" + tab;
  if (pushHistory !== false) {
    window.history.pushState({ slug: _slug, tab }, "", newUrl);
  }

  if (tab === "tickets") {
    loadTickets();
  }

  if (tab === "sprint-mgmt") {
    if (_deepLinkSprintSubView()) _applyDeepLinkSubView();
    else _smgmtShowSubView(_smgmtSavedSubView() || "board");
  }

  if (tab === "sprint-mgmt" && !_sprintMgmtLoaded && _cachedFullRepo[_slug]) {
    _sprintMgmtLoaded = true;
    loadSprintMgmt().then(() => _smgmtArInit());
    _histLoadLedger(_cachedFullRepo[_slug]);
  } else if (tab === "sprint-mgmt" && _sprintMgmtLoaded) {
    if (_arTickerId === null && _arInterval > 0) _smgmtArStartTicker();
  }

  if (tab === "bulk-create") {
    _bcInitTab();
    _lpRenderBc();
  }
  if (tab === "deploy") deployTabInit();
  if (tab === "failures") failuresInit();
  if (tab === "brain") brainInit();
  if (tab === "settings") projSettingsInit();
  if (tab === "global-settings") {
    settingsInitValues();
    settingsPopulateRepos();
    globalSettingsLoad();
  }

  if (typeof window._smgmtUpdateSelectionUI === "function")
    window._smgmtUpdateSelectionUI();
  if (typeof window._bulkUpdateActionBar === "function")
    window._bulkUpdateActionBar();
  if (typeof window._smgmtUpdateToolbarTop === "function")
    window._smgmtUpdateToolbarTop();
}

export function toggleStabDropdown(name, e) {
  e.stopPropagation();
  const group = document.getElementById("stab-group-" + name);
  const isOpen = group.classList.contains("open");
  closeAllStabDropdowns();
  if (!isOpen) {
    group.classList.add("open");
    // At ≤430px, .sub-tabs { overflow-x: auto } forces overflow-y: auto (CSS
    // spec forbids overflow-x:auto + overflow-y:visible), which clips the
    // absolutely-positioned .stab-dropdown even though its containing block
    // (.sub-tabs-row) is above the scroll container. Fix: position the dropdown
    // as fixed using the trigger's viewport rect so it escapes the clip edge.
    if (window.innerWidth <= 430) {
      const trigger = group.querySelector(".stab-trigger");
      const dropdown = group.querySelector(".stab-dropdown");
      if (trigger && dropdown) {
        const rect = trigger.getBoundingClientRect();
        dropdown.style.setProperty("position", "fixed");
        dropdown.style.setProperty("top", rect.bottom + 2 + "px");
        dropdown.style.setProperty("left", rect.left + "px");
      }
    }
  }
}

export function closeAllStabDropdowns() {
  document.querySelectorAll(".stab-group.open").forEach((g) => {
    g.classList.remove("open");
    // Reset any inline position styles set by the mobile fixed-position fix.
    const dropdown = g.querySelector(".stab-dropdown");
    if (dropdown) {
      dropdown.style.removeProperty("position");
      dropdown.style.removeProperty("top");
      dropdown.style.removeProperty("left");
    }
  });
}

document.addEventListener("click", closeAllStabDropdowns);

const _subTabsEl = document.getElementById("sub-tabs");
if (_subTabsEl) {
  _subTabsEl.addEventListener("keydown", function (e) {
    const enabledTabs = [
      "sprint-mgmt",
      "tickets",
      "failures",
      "brain",
      "manage",
      "deploy",
      "settings",
    ];
    const focused = document.activeElement;
    const currentId = focused ? focused.id.replace("stab-", "") : null;
    const currentIdx = enabledTabs.indexOf(currentId);
    if (currentIdx < 0) return;

    if (e.key === "ArrowRight") {
      e.preventDefault();
      const next = enabledTabs[(currentIdx + 1) % enabledTabs.length];
      document.getElementById("stab-" + next).focus();
    } else if (e.key === "ArrowLeft") {
      e.preventDefault();
      const prev =
        enabledTabs[(currentIdx - 1 + enabledTabs.length) % enabledTabs.length];
      document.getElementById("stab-" + prev).focus();
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      if (currentId) switchTab(currentId);
    }
  });
}

window.addEventListener("popstate", function (e) {
  const { slug, tab, view, filter } = parseUrl();
  const effSlug = slug || (e.state && e.state.slug);
  const effTab = (slug ? tab : e.state && e.state.tab) || "sprint-mgmt";
  if (!effSlug) return;
  if (effSlug !== _slug) {
    _ticketsRepo = null;
  }
  _slug = effSlug;
  _deepLinkView = view;
  _deepLinkFilter = filter;
  _evlState.errorsOnly = filter === "errors";
  switchTab(effTab, false);
});
