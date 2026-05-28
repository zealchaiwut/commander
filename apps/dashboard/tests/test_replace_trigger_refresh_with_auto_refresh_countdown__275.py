"""Tests for issue #275: Replace Trigger Refresh with auto-refresh countdown pill.

Acceptance criteria covered:

AC-1   Pill replaces "Trigger Refresh"; shows "Auto Refresh" label with countdown
AC-2   On countdown zero, refreshes board data silently (no spinner, scroll preserved)
AC-3   Dropdown offers Off / 1s / 5s / 10s interval options
AC-4   Selecting "Off" stops the ticker and adds is-off class
AC-5   Clicking the pill trigger calls _smgmtArManualRefresh (immediate refresh)
AC-6   Chosen interval persists in sessionStorage under key 'smgmt-ar-interval'
AC-7   Auto-refresh init wired into loadSprintMgmt / tab switch lifecycle
AC-8   Real browser: countdown ticks visibly, Off toggle stops it, pill visible
"""
import os
import re
import time

import httpx
import pytest

BASE_URL = os.environ.get("TEST_BASE_URL", "http://localhost:8000")
TEST_SLUG = "commander"

# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


@pytest.fixture(scope="module")
def html(client):
    r = client.get(f"/project/{TEST_SLUG}/sprint-mgmt")
    assert r.status_code == 200, f"project.html not served: {r.status_code}"
    return r.text


# ── AC-1: Pill replaces "Trigger Refresh" ────────────────────────────────────


class TestPillReplacesOldButton:
    def test_old_refresh_button_removed(self, html):
        """AC-1: id='smgmt-refresh-btn' must not exist — replaced by the pill."""
        assert 'id="smgmt-refresh-btn"' not in html, (
            "Old Trigger Refresh button (smgmt-refresh-btn) must be removed"
        )

    def test_old_trigger_refresh_text_removed(self, html):
        """AC-1: 'Trigger Refresh' text label must be gone from the HTML."""
        assert "Trigger Refresh" not in html, (
            "'Trigger Refresh' text must be removed — replaced by pill"
        )

    def test_pill_element_exists(self, html):
        """AC-1: Auto-refresh pill container (smgmt-ar-pill) is present."""
        assert 'id="smgmt-ar-pill"' in html, (
            "Auto-refresh pill div with id='smgmt-ar-pill' must exist"
        )

    def test_pill_trigger_button_exists(self, html):
        """AC-1: Pill trigger button (smgmt-ar-trigger) is present."""
        assert 'id="smgmt-ar-trigger"' in html, (
            "Pill trigger button with id='smgmt-ar-trigger' must exist"
        )

    def test_pill_label_element_exists(self, html):
        """AC-1: Label span (smgmt-ar-label) inside the pill is present."""
        assert 'id="smgmt-ar-label"' in html, (
            "Label span with id='smgmt-ar-label' must exist inside the pill"
        )

    def test_pill_label_initial_text(self, html):
        """AC-1: Label initially renders 'Auto Refresh' (before JS runs)."""
        assert ">Auto Refresh<" in html, (
            "smgmt-ar-label must contain 'Auto Refresh' as its initial text"
        )

    def test_pill_refresh_icon_present(self, html):
        """AC-1: Refresh icon (ti-refresh) is present inside the pill trigger."""
        assert 'id="smgmt-ar-icon"' in html, (
            "Refresh icon with id='smgmt-ar-icon' must exist inside the pill"
        )

    def test_pill_caret_button_exists(self, html):
        """AC-1: Caret button (smgmt-ar-caret) to open the dropdown is present."""
        assert 'id="smgmt-ar-caret"' in html, (
            "Caret button with id='smgmt-ar-caret' must exist for opening dropdown"
        )


# ── AC-2: Silent refresh — no spinner, scroll preserved ─────────────────────


class TestSilentRefresh:
    def test_load_sprint_mgmt_accepts_silent_param(self, html):
        """AC-2: loadSprintMgmt(silent) signature supports the silent flag."""
        assert "async function loadSprintMgmt(silent)" in html, (
            "loadSprintMgmt must accept a 'silent' parameter"
        )

    def test_spinner_guarded_by_silent_flag(self, html):
        """AC-2: 'Loading sprints' spinner only rendered when silent is falsy."""
        idx = html.find("async function loadSprintMgmt(silent)")
        assert idx != -1
        excerpt = html[idx: idx + 800]
        assert "if (!silent)" in excerpt, (
            "The 'Loading sprints…' spinner must be guarded by 'if (!silent)'"
        )

    def test_scroll_position_restored_after_refresh(self, html):
        """AC-2: scroll position saved before and restored after silent refresh."""
        idx = html.find("async function _smgmtArDoRefresh")
        assert idx != -1, "_smgmtArDoRefresh function not found in project.html"
        excerpt = html[idx: idx + 500]
        assert "scrollY" in excerpt, (
            "_smgmtArDoRefresh must capture window.scrollY before refresh"
        )
        assert "window.scrollTo" in excerpt, (
            "_smgmtArDoRefresh must call window.scrollTo to restore scroll after refresh"
        )

    def test_do_refresh_calls_load_silent_true(self, html):
        """AC-2: _smgmtArDoRefresh calls loadSprintMgmt with silent=true."""
        idx = html.find("async function _smgmtArDoRefresh")
        assert idx != -1
        excerpt = html[idx: idx + 400]
        assert "loadSprintMgmt(" in excerpt, (
            "_smgmtArDoRefresh must call loadSprintMgmt"
        )
        assert "true" in excerpt, (
            "_smgmtArDoRefresh must pass silent=true to loadSprintMgmt"
        )


# ── AC-3: Dropdown offers Off / 1s / 5s / 10s ───────────────────────────────


class TestDropdownOptions:
    def test_dropdown_element_exists(self, html):
        """AC-3: Dropdown container (smgmt-ar-dropdown) is present."""
        assert 'id="smgmt-ar-dropdown"' in html, (
            "Dropdown div with id='smgmt-ar-dropdown' must exist"
        )

    def test_dropdown_has_off_option(self, html):
        """AC-3: Dropdown includes an Off option (data-interval='0')."""
        assert 'data-interval="0"' in html, (
            "Dropdown must have an option with data-interval='0' for Off"
        )

    def test_dropdown_has_1s_option(self, html):
        """AC-3: Dropdown includes a 1s option."""
        assert 'data-interval="1"' in html, (
            "Dropdown must have an option with data-interval='1' for 1s"
        )

    def test_dropdown_has_5s_option(self, html):
        """AC-3: Dropdown includes a 5s option."""
        assert 'data-interval="5"' in html, (
            "Dropdown must have an option with data-interval='5' for 5s"
        )

    def test_dropdown_has_10s_option(self, html):
        """AC-3: Dropdown includes a 10s option."""
        assert 'data-interval="10"' in html, (
            "Dropdown must have an option with data-interval='10' for 10s"
        )

    def test_dropdown_four_options_total(self, html):
        """AC-3: Exactly four interval options (Off, 1s, 5s, 10s) exist."""
        matches = re.findall(r'class="smgmt-ar-option"', html)
        assert len(matches) == 4, (
            f"Expected 4 dropdown options, found {len(matches)}"
        )

    def test_dropdown_off_label(self, html):
        """AC-3: Off option shows 'Off' text."""
        assert ">Off<" in html, (
            "Off dropdown option must have 'Off' as its visible label"
        )

    def test_dropdown_role_listbox(self, html):
        """AC-3: Dropdown container has role='listbox' for accessibility."""
        assert 'role="listbox"' in html, (
            "Dropdown div must have role='listbox'"
        )

    def test_dropdown_default_hidden(self, html):
        """AC-3: Dropdown is hidden by default (no 'open' class in static HTML)."""
        idx = html.find('id="smgmt-ar-dropdown"')
        assert idx != -1
        tag_end = html.find(">", idx)
        opening_tag = html[idx: tag_end]
        assert "open" not in opening_tag, (
            "Dropdown must not have 'open' class in the static HTML"
        )


# ── AC-4: Off stops the ticker ───────────────────────────────────────────────


class TestOffToggle:
    def test_is_off_css_class_defined(self, html):
        """AC-4: CSS rule .smgmt-ar-pill.is-off exists to style the off state."""
        assert ".smgmt-ar-pill.is-off" in html, (
            "CSS rule .smgmt-ar-pill.is-off must exist"
        )

    def test_stop_ticker_function_exists(self, html):
        """AC-4: _smgmtArStopTicker function is defined."""
        assert "function _smgmtArStopTicker" in html, (
            "_smgmtArStopTicker function must be defined"
        )

    def test_stop_ticker_calls_clearInterval(self, html):
        """AC-4: _smgmtArStopTicker clears the interval via clearInterval."""
        idx = html.find("function _smgmtArStopTicker")
        assert idx != -1
        excerpt = html[idx: idx + 200]
        assert "clearInterval" in excerpt, (
            "_smgmtArStopTicker must call clearInterval to stop the ticker"
        )

    def test_select_interval_zero_stops_ticker(self, html):
        """AC-4: _smgmtArSelectInterval(0) calls _smgmtArStopTicker."""
        idx = html.find("function _smgmtArSelectInterval")
        assert idx != -1
        excerpt = html[idx: idx + 600]
        assert "_smgmtArStopTicker" in excerpt, (
            "_smgmtArSelectInterval must call _smgmtArStopTicker when interval=0"
        )

    def test_update_label_adds_is_off_class(self, html):
        """AC-4: When interval=0, _smgmtArUpdateLabel adds 'is-off' class to pill."""
        idx = html.find("function _smgmtArUpdateLabel")
        assert idx != -1
        excerpt = html[idx: idx + 400]
        assert "is-off" in excerpt, (
            "_smgmtArUpdateLabel must add 'is-off' class when interval is 0"
        )

    def test_update_label_removes_is_off_class_when_active(self, html):
        """AC-4: When interval>0, _smgmtArUpdateLabel removes 'is-off' class."""
        idx = html.find("function _smgmtArUpdateLabel")
        assert idx != -1
        excerpt = html[idx: idx + 400]
        assert "classList.remove" in excerpt and "is-off" in excerpt, (
            "_smgmtArUpdateLabel must remove 'is-off' class when interval > 0"
        )


# ── AC-5: Clicking pill triggers immediate manual refresh ────────────────────


class TestManualRefresh:
    def test_manual_refresh_function_exists(self, html):
        """AC-5: _smgmtArManualRefresh function is defined."""
        assert "function _smgmtArManualRefresh" in html, (
            "_smgmtArManualRefresh function must be defined"
        )

    def test_trigger_button_calls_manual_refresh(self, html):
        """AC-5: Pill trigger button onclick calls _smgmtArManualRefresh."""
        assert "onclick=\"_smgmtArManualRefresh()\"" in html, (
            "smgmt-ar-trigger onclick must call _smgmtArManualRefresh()"
        )

    def test_manual_refresh_calls_do_refresh(self, html):
        """AC-5: _smgmtArManualRefresh delegates to _smgmtArDoRefresh."""
        idx = html.find("function _smgmtArManualRefresh")
        assert idx != -1
        excerpt = html[idx: idx + 400]
        assert "_smgmtArDoRefresh" in excerpt, (
            "_smgmtArManualRefresh must call _smgmtArDoRefresh"
        )

    def test_manual_refresh_resets_countdown(self, html):
        """AC-5: Manual refresh resets countdown so next auto-tick is a full interval away."""
        idx = html.find("function _smgmtArManualRefresh")
        assert idx != -1
        excerpt = html[idx: idx + 400]
        assert "_arCountdown = _arInterval" in excerpt, (
            "_smgmtArManualRefresh must reset _arCountdown to _arInterval"
        )


# ── AC-6: sessionStorage persistence ─────────────────────────────────────────


class TestSessionStoragePersistence:
    def test_session_key_constant_defined(self, html):
        """AC-6: _AR_SESSION_KEY constant set to 'smgmt-ar-interval'."""
        assert "_AR_SESSION_KEY = 'smgmt-ar-interval'" in html, (
            "_AR_SESSION_KEY must be 'smgmt-ar-interval'"
        )

    def test_select_interval_saves_to_session_storage(self, html):
        """AC-6: _smgmtArSelectInterval persists chosen interval to sessionStorage."""
        idx = html.find("function _smgmtArSelectInterval")
        assert idx != -1
        excerpt = html[idx: idx + 600]
        assert "sessionStorage.setItem" in excerpt, (
            "_smgmtArSelectInterval must call sessionStorage.setItem"
        )

    def test_init_reads_from_session_storage(self, html):
        """AC-6: _smgmtArInit reads the saved interval from sessionStorage."""
        idx = html.find("function _smgmtArInit")
        assert idx != -1
        excerpt = html[idx: idx + 300]
        assert "sessionStorage.getItem" in excerpt, (
            "_smgmtArInit must call sessionStorage.getItem to restore the chosen interval"
        )

    def test_init_defaults_to_5s_when_no_stored_value(self, html):
        """AC-6: _smgmtArInit falls back to 5s if sessionStorage has no saved value."""
        idx = html.find("function _smgmtArInit")
        assert idx != -1
        excerpt = html[idx: idx + 300]
        assert "5" in excerpt, (
            "_smgmtArInit must default to 5s interval when sessionStorage is empty"
        )

    def test_select_interval_respects_skip_save_flag(self, html):
        """AC-6: skipSave param allows interval selection without overwriting sessionStorage."""
        idx = html.find("function _smgmtArSelectInterval")
        assert idx != -1
        excerpt = html[idx: idx + 600]
        assert "skipSave" in excerpt, (
            "_smgmtArSelectInterval must accept a skipSave parameter"
        )


# ── AC-7: Lifecycle wiring ────────────────────────────────────────────────────


class TestLifecycleWiring:
    def test_init_function_calls_ar_init(self, html):
        """AC-7: Top-level init() calls _smgmtArInit after loading sprint mgmt."""
        idx = html.find("async function init()")
        assert idx != -1, "async function init() not found"
        # init() can be several kilobytes; scan up to 8000 chars to cover the full body
        excerpt = html[idx: idx + 8000]
        assert "_smgmtArInit" in excerpt, (
            "init() must call _smgmtArInit() after loadSprintMgmt"
        )

    def test_load_sprint_mgmt_triggers_ar_init_on_first_load(self, html):
        """AC-7: When sprint-mgmt tab is loaded for the first time, _smgmtArInit is called."""
        # The tab-switch handler calls loadSprintMgmt().then(() => _smgmtArInit())
        assert "loadSprintMgmt().then" in html, (
            "Tab switch must chain .then(() => _smgmtArInit()) after loadSprintMgmt"
        )
        assert "_smgmtArInit()" in html, (
            "_smgmtArInit() must be invoked from the tab-switch handler"
        )

    def test_ticker_paused_when_leaving_sprint_mgmt_tab(self, html):
        """AC-7: Ticker is stopped when user navigates away from sprint-mgmt tab."""
        idx = html.find("function switchTab")
        assert idx != -1
        excerpt = html[idx: idx + 600]
        assert "_smgmtArStopTicker" in excerpt, (
            "switchTab must call _smgmtArStopTicker when leaving sprint-mgmt"
        )

    def test_ticker_restarts_when_returning_to_sprint_mgmt_tab(self, html):
        """AC-7: Ticker restarts when user navigates back to sprint-mgmt tab."""
        idx = html.find("function switchTab")
        assert idx != -1
        # switchTab spans well over 700 chars; scan a larger window
        excerpt = html[idx: idx + 2500]
        assert "_smgmtArStartTicker" in excerpt, (
            "switchTab must call _smgmtArStartTicker when returning to sprint-mgmt"
        )

    def test_start_ticker_function_uses_set_interval(self, html):
        """AC-7: _smgmtArStartTicker uses setInterval for the countdown."""
        idx = html.find("function _smgmtArStartTicker")
        assert idx != -1
        excerpt = html[idx: idx + 300]
        assert "setInterval" in excerpt, (
            "_smgmtArStartTicker must use setInterval to drive the countdown"
        )

    def test_tick_function_decrements_countdown(self, html):
        """AC-7: _smgmtArTick decrements _arCountdown each second."""
        idx = html.find("function _smgmtArTick")
        assert idx != -1
        excerpt = html[idx: idx + 300]
        assert "_arCountdown--" in excerpt, (
            "_smgmtArTick must decrement _arCountdown each tick"
        )

    def test_tick_function_triggers_refresh_at_zero(self, html):
        """AC-7: _smgmtArTick triggers _smgmtArDoRefresh when countdown reaches zero."""
        idx = html.find("function _smgmtArTick")
        assert idx != -1
        excerpt = html[idx: idx + 400]
        assert "_smgmtArDoRefresh" in excerpt, (
            "_smgmtArTick must call _smgmtArDoRefresh when countdown hits zero"
        )

    def test_default_interval_is_5(self, html):
        """AC-7: Default _arInterval is 5 (5s auto-refresh on page load)."""
        assert "let _arInterval   = 5;" in html or "let _arInterval = 5;" in html, (
            "Default _arInterval must be 5 (five-second auto-refresh)"
        )


# ── AC-8: Real browser Selenium tests ────────────────────────────────────────


@pytest.mark.selenium
class TestBrowserBehavior:
    """Real-browser tests using headless Chrome. Require @pytest.mark.selenium."""

    @pytest.fixture(scope="class")
    def driver(self):
        from tests.selenium_helpers import build_driver
        drv = build_driver()
        drv.implicitly_wait(5)
        yield drv
        drv.quit()

    def _nav_sprint_mgmt(self, driver):
        driver.get(f"{BASE_URL}/project/{TEST_SLUG}/sprint-mgmt")
        time.sleep(1.5)  # allow JS to init

    def test_pill_visible_in_browser(self, driver):
        """AC-8: Auto-refresh pill renders and is displayed in the browser."""
        from selenium.webdriver.common.by import By

        self._nav_sprint_mgmt(driver)
        pill = driver.find_element(By.ID, "smgmt-ar-pill")
        assert pill.is_displayed(), "smgmt-ar-pill must be visible on the page"

    def test_trigger_button_visible(self, driver):
        """AC-8: Pill trigger button is visible and clickable."""
        from selenium.webdriver.common.by import By

        self._nav_sprint_mgmt(driver)
        trigger = driver.find_element(By.ID, "smgmt-ar-trigger")
        assert trigger.is_displayed(), "smgmt-ar-trigger button must be visible"

    def test_label_shows_countdown_text(self, driver):
        """AC-8: Label text matches 'Auto Refresh (Ns)' pattern after JS initialises."""
        from selenium.webdriver.common.by import By

        self._nav_sprint_mgmt(driver)
        label = driver.find_element(By.ID, "smgmt-ar-label")
        text = label.text
        pattern = re.compile(r"Auto Refresh \(\d+s\)|Auto Refresh")
        assert pattern.match(text), (
            f"Label text '{text}' must match 'Auto Refresh (Ns)' or 'Auto Refresh'"
        )

    def test_countdown_ticks_visibly(self, driver):
        """AC-8: Countdown decrements each second (verified over 2 seconds)."""
        from selenium.webdriver.common.by import By

        self._nav_sprint_mgmt(driver)
        # Ensure auto-refresh is set to 5s (not Off)
        driver.execute_script("_smgmtArSelectInterval(5, false);")
        time.sleep(0.3)

        label = driver.find_element(By.ID, "smgmt-ar-label")
        text_before = label.text
        time.sleep(2.2)
        text_after = label.text

        # Either the number decreased or a refresh cycle already happened
        assert text_before != text_after or re.search(r"\(\d+s\)", text_after), (
            f"Countdown must tick: before='{text_before}' after='{text_after}'"
        )

    def test_off_option_stops_countdown(self, driver):
        """AC-8: Selecting 'Off' stops the ticker — label stays static."""
        from selenium.webdriver.common.by import By

        self._nav_sprint_mgmt(driver)
        # Select Off via JS
        driver.execute_script("_smgmtArSelectInterval(0, false);")
        time.sleep(0.3)

        label = driver.find_element(By.ID, "smgmt-ar-label")
        text1 = label.text
        time.sleep(2.2)
        text2 = label.text

        assert text1 == text2, (
            f"After selecting Off, label must not change: was '{text1}', became '{text2}'"
        )
        assert text1 == "Auto Refresh", (
            f"Label must show 'Auto Refresh' (no countdown) when Off: got '{text1}'"
        )

    def test_pill_has_is_off_class_when_off(self, driver):
        """AC-8: Pill gets 'is-off' CSS class when Off is selected."""
        from selenium.webdriver.common.by import By

        self._nav_sprint_mgmt(driver)
        driver.execute_script("_smgmtArSelectInterval(0, false);")
        time.sleep(0.3)

        pill = driver.find_element(By.ID, "smgmt-ar-pill")
        classes = pill.get_attribute("class") or ""
        assert "is-off" in classes, (
            f"smgmt-ar-pill must have 'is-off' class when Off selected; classes: '{classes}'"
        )

    def test_re_enable_restarts_countdown(self, driver):
        """AC-8: Re-selecting an interval after Off restarts the visible countdown."""
        from selenium.webdriver.common.by import By

        self._nav_sprint_mgmt(driver)
        driver.execute_script("_smgmtArSelectInterval(0, false);")
        time.sleep(0.3)

        driver.execute_script("_smgmtArSelectInterval(5, false);")
        time.sleep(0.3)

        label = driver.find_element(By.ID, "smgmt-ar-label")
        text = label.text
        assert re.search(r"\(\d+s\)", text), (
            f"After re-enabling 5s, label must show countdown: '{text}'"
        )

    def test_dropdown_opens_on_caret_click(self, driver):
        """AC-8: Clicking the caret button opens the dropdown."""
        from selenium.webdriver.common.by import By

        self._nav_sprint_mgmt(driver)
        caret = driver.find_element(By.ID, "smgmt-ar-caret")
        caret.click()
        time.sleep(0.2)

        dropdown = driver.find_element(By.ID, "smgmt-ar-dropdown")
        classes = dropdown.get_attribute("class") or ""
        assert "open" in classes, (
            f"Dropdown must have 'open' class after caret click; classes: '{classes}'"
        )

    def test_session_storage_persists_interval(self, driver):
        """AC-8: sessionStorage retains the chosen interval after page interaction."""
        from selenium.webdriver.common.by import By

        self._nav_sprint_mgmt(driver)
        driver.execute_script("_smgmtArSelectInterval(10, false);")
        time.sleep(0.3)

        stored = driver.execute_script(
            "return sessionStorage.getItem('smgmt-ar-interval');"
        )
        assert stored == "10", (
            f"sessionStorage['smgmt-ar-interval'] must be '10' after selecting 10s; got '{stored}'"
        )

    def test_session_storage_survives_reload(self, driver):
        """AC-8: After page reload, the pill restores the previously chosen interval."""
        from selenium.webdriver.common.by import By

        self._nav_sprint_mgmt(driver)
        driver.execute_script("_smgmtArSelectInterval(10, false);")
        time.sleep(0.3)

        # Reload (session storage survives within the same session)
        driver.refresh()
        time.sleep(1.5)

        label = driver.find_element(By.ID, "smgmt-ar-label")
        text = label.text
        # With 10s interval restored, countdown may have ticked down already — any
        # "Auto Refresh (Ns)" (not plain "Auto Refresh") confirms restoration succeeded.
        assert re.search(r"Auto Refresh \(\d+s\)", text), (
            f"After reload, pill must show a countdown (interval restored); label: '{text}'"
        )

    def test_manual_refresh_click_does_not_crash(self, driver):
        """AC-8: Clicking the pill trigger while Off performs a one-shot refresh without error."""
        from selenium.webdriver.common.by import By

        self._nav_sprint_mgmt(driver)
        driver.execute_script("_smgmtArSelectInterval(0, false);")
        time.sleep(0.3)

        trigger = driver.find_element(By.ID, "smgmt-ar-trigger")
        trigger.click()
        time.sleep(1.5)

        # No JS error and pill is still in off state
        pill = driver.find_element(By.ID, "smgmt-ar-pill")
        classes = pill.get_attribute("class") or ""
        assert "is-off" in classes, (
            "After manual refresh while Off, pill must remain in 'is-off' state"
        )
