"""Tests for issue #1291: Extract per-ticket loop from run_sprint into helper"""
import ast
import subprocess


SOURCE_PATH = "services/sprint_manager/sprint_manager.py"


def _parse_source():
    with open(SOURCE_PATH) as f:
        return f.read()


def _find_function(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


# ---------------------------------------------------------------------------
# AC-1: run_sprint_loop() function exists
# ---------------------------------------------------------------------------

def test_run_sprint_loop_function_exists():
    """AC-1: run_sprint_loop() helper exists in sprint_manager.py"""
    source = _parse_source()
    tree = ast.parse(source)
    funcs = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "run_sprint_loop" in funcs, "run_sprint_loop function not found in sprint_manager.py"


def test_run_sprint_loop_has_expected_params():
    """AC-1: run_sprint_loop() accepts at minimum the per-ticket context (label, state, summary, cfg)"""
    source = _parse_source()
    tree = ast.parse(source)
    node = _find_function(tree, "run_sprint_loop")
    assert node is not None, "run_sprint_loop not found"
    all_args = [a.arg for a in node.args.args] + [a.arg for a in node.args.kwonlyargs]
    # Must accept label (used for structured_log) and key orchestration flags
    assert "label" in all_args, "run_sprint_loop missing 'label' param"
    assert "cfg" in all_args, "run_sprint_loop missing 'cfg' param"


# ---------------------------------------------------------------------------
# AC-2: run_sprint delegates to run_sprint_loop()
# ---------------------------------------------------------------------------

def test_run_sprint_calls_run_sprint_loop():
    """AC-2: run_sprint calls run_sprint_loop()"""
    source = _parse_source()
    tree = ast.parse(source)
    node = _find_function(tree, "run_sprint")
    assert node is not None, "run_sprint not found"
    body_src = ast.get_source_segment(source, node)
    assert "run_sprint_loop(" in body_src, "run_sprint does not call run_sprint_loop()"


def test_run_sprint_still_calls_preflight():
    """AC-2: run_sprint still calls run_sprint_preflight() as its first phase"""
    source = _parse_source()
    tree = ast.parse(source)
    node = _find_function(tree, "run_sprint")
    body_src = ast.get_source_segment(source, node)
    assert "run_sprint_preflight(" in body_src, "run_sprint no longer calls run_sprint_preflight()"


def test_run_sprint_no_inline_fix_loop():
    """AC-2: run_sprint body contains no inline per-ticket fix-loop (it delegates to run_sprint_loop)"""
    source = _parse_source()
    tree = ast.parse(source)
    node = _find_function(tree, "run_sprint")
    assert node is not None, "run_sprint not found"
    body_src = ast.get_source_segment(source, node)
    # The fix-loop sentinel "_fix_rounds" should NOT appear directly in run_sprint
    assert "_fix_rounds" not in body_src, (
        "run_sprint still contains inline fix-loop logic (_fix_rounds found); "
        "it should delegate to run_sprint_loop()"
    )


# ---------------------------------------------------------------------------
# AC-3: run_sprint body targets ~150 lines
# ---------------------------------------------------------------------------

def test_run_sprint_body_line_count():
    """AC-3: run_sprint body is roughly 150 lines (≤200 after extraction)"""
    with open(SOURCE_PATH) as f:
        lines = f.readlines()

    # Find def run_sprint( — it is after run_sprint_preflight so search from line 4000 onward
    start = None
    for i, line in enumerate(lines):
        if "def run_sprint(" in line and i > 4000:
            start = i
            break
    assert start is not None, "run_sprint def not found"

    # Find next top-level def after run_sprint (non-indented def)
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("def ") or lines[i].startswith("class "):
            end = i
            break

    body_lines = end - start
    assert body_lines <= 200, (
        f"run_sprint body is {body_lines} lines — expected ≤200 after extraction. "
        "Loop body should be in run_sprint_loop()."
    )


# ---------------------------------------------------------------------------
# AC-4: Pure refactor — logic lives in run_sprint_loop, not duplicated
# ---------------------------------------------------------------------------

def test_run_sprint_loop_contains_fix_loop():
    """AC-4: run_sprint_loop() contains the per-ticket fix-loop logic (_fix_rounds / _fix_attempt)"""
    source = _parse_source()
    tree = ast.parse(source)
    node = _find_function(tree, "run_sprint_loop")
    assert node is not None, "run_sprint_loop not found"
    body_src = ast.get_source_segment(source, node)
    assert "_fix_rounds" in body_src, "run_sprint_loop missing fix-loop logic (_fix_rounds)"
    assert "_fix_attempt" in body_src, "run_sprint_loop missing fix-loop logic (_fix_attempt)"


def test_run_sprint_loop_contains_coder_dispatch():
    """AC-4: run_sprint_loop() contains _dispatch_coder call"""
    source = _parse_source()
    tree = ast.parse(source)
    node = _find_function(tree, "run_sprint_loop")
    body_src = ast.get_source_segment(source, node)
    assert "_dispatch_coder(" in body_src, "run_sprint_loop missing _dispatch_coder call"


def test_run_sprint_loop_contains_tester_dispatch():
    """AC-4: run_sprint_loop() contains _dispatch_tester call"""
    source = _parse_source()
    tree = ast.parse(source)
    node = _find_function(tree, "run_sprint_loop")
    body_src = ast.get_source_segment(source, node)
    assert "_dispatch_tester(" in body_src, "run_sprint_loop missing _dispatch_tester call"


def test_run_sprint_loop_contains_handle_post_tester():
    """AC-4: run_sprint_loop() contains handle_post_tester call"""
    source = _parse_source()
    tree = ast.parse(source)
    node = _find_function(tree, "run_sprint_loop")
    body_src = ast.get_source_segment(source, node)
    assert "handle_post_tester(" in body_src, "run_sprint_loop missing handle_post_tester call"


# ---------------------------------------------------------------------------
# AC-5: All existing tests pass (module imports cleanly)
# ---------------------------------------------------------------------------

def test_sprint_manager_imports_cleanly():
    """AC-5: sprint_manager module can be imported without errors"""
    result = subprocess.run(
        ["python3", "-c", "import services.sprint_manager.sprint_manager"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"sprint_manager import failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_help_still_works():
    """AC-5: CLI --help still exits 0"""
    result = subprocess.run(
        ["python3", "services/sprint_manager/sprint_manager.py", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"--help exited {result.returncode}: {result.stderr}"
    assert "Sprint Manager" in result.stdout


def test_run_sprint_still_public():
    """AC-5: run_sprint remains a public function (not renamed or removed)"""
    source = _parse_source()
    tree = ast.parse(source)
    funcs = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "run_sprint" in funcs, "run_sprint was removed or renamed"


def test_return_type_unchanged():
    """AC-5: run_sprint still returns tuple[SprintSummary, SprintState]"""
    source = _parse_source()
    tree = ast.parse(source)
    node = _find_function(tree, "run_sprint")
    assert node is not None
    if node.returns:
        ret = ast.unparse(node.returns)
        assert "SprintSummary" in ret and "SprintState" in ret, (
            f"run_sprint return type changed: {ret}"
        )


# ---------------------------------------------------------------------------
# AC-6: Smoke — run_sprint_loop is importable and callable (empty-list guard)
# ---------------------------------------------------------------------------

def test_run_sprint_loop_is_importable():
    """AC-6: run_sprint_loop can be imported from sprint_manager"""
    result = subprocess.run(
        ["python3", "-c",
         "from services.sprint_manager.sprint_manager import run_sprint_loop; "
         "print('ok')"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"run_sprint_loop not importable:\n{result.stderr}"
    )
    assert "ok" in result.stdout
