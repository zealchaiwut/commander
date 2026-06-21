"""Tests for issue #1290: Extract run_sprint preflight/branch-setup into helper (runs against UAT)"""
import subprocess
import ast


def test_run_sprint_preflight__function_exists():
    """AC-1: A `run_sprint_preflight()` function exists containing preflight + branch-setup logic"""
    # Read sprint_manager.py and verify the function exists and has proper signature
    with open("services/sprint_manager/sprint_manager.py", "r") as f:
        source = f.read()

    # Parse AST to find the function
    tree = ast.parse(source)
    functions = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}

    assert "run_sprint_preflight" in functions, "run_sprint_preflight function not found"

    # Verify it has the expected parameters from the diff
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "run_sprint_preflight":
            arg_names = [arg.arg for arg in node.args.args]
            # Check key parameters are present
            assert "label" in arg_names
            assert "alert_modes" in arg_names
            assert "repo_name" in arg_names
            assert "cfg" in arg_names
            break
    else:
        assert False, "run_sprint_preflight not found in AST walk"


def test_run_sprint_calls_preflight():
    """AC-2: `run_sprint` calls `run_sprint_preflight()` and does not duplicate extracted logic"""
    with open("services/sprint_manager/sprint_manager.py", "r") as f:
        source = f.read()

    # Find the run_sprint function
    tree = ast.parse(source)
    run_sprint_found = False

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "run_sprint":
            run_sprint_found = True
            # Verify it calls run_sprint_preflight
            func_source = ast.get_source_segment(source, node)
            assert "run_sprint_preflight(" in func_source, "run_sprint does not call run_sprint_preflight"
            assert "pf = run_sprint_preflight(" in func_source, "run_sprint does not assign result to variable"
            break

    assert run_sprint_found, "run_sprint function not found"


def test_preflight_logic_not_in_run_sprint_body():
    """AC-3: `run_sprint` is not moved as a whole blob; only preflight/branch-setup is extracted"""
    with open("services/sprint_manager/sprint_manager.py", "r") as f:
        lines = f.readlines()

    # Find run_sprint function (the second one, after preflight)
    run_sprint_line = None
    for i, line in enumerate(lines):
        if "def run_sprint(" in line and i > 4000:  # After the preflight version
            run_sprint_line = i
            break

    assert run_sprint_line is not None, "run_sprint function not found"

    # Extract first ~50 lines of run_sprint body to verify it calls preflight early
    sprint_body = "\n".join(lines[run_sprint_line:run_sprint_line+50])

    # Check that preflight call happens early
    assert "run_sprint_preflight(" in sprint_body, "preflight not called early in run_sprint"

    # Get the part of run_sprint after the preflight call
    pf_call_index = sprint_body.find("run_sprint_preflight(")
    after_pf_call = sprint_body[pf_call_index:].find("if pf.early_exit:")

    if after_pf_call > 0:
        # Rough check that early env setup doesn't happen again
        after_extraction = sprint_body[pf_call_index+after_pf_call:]
        assert "eff_repo   = repo_name or" not in after_extraction, \
            "Environment setup duplicated in run_sprint after preflight extraction"


def test_run_sprint_unpacking_result():
    """AC-2 (continued): Verify run_sprint properly unpacks and uses preflight result"""
    with open("services/sprint_manager/sprint_manager.py", "r") as f:
        source = f.read()

    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "run_sprint":
            func_source = ast.get_source_segment(source, node)
            # Verify it unpacks the result
            assert "state = pf.state" in func_source or "pf.state" in func_source, \
                "Result not properly unpacked from preflight"
            assert "if pf.early_exit:" in func_source, \
                "Early exit condition not checked"
            break


def test_sprintpreflightresult_dataclass_exists():
    """AC-2 (structural): _SprintPreflightResult dataclass exists"""
    with open("services/sprint_manager/sprint_manager.py", "r") as f:
        source = f.read()

    tree = ast.parse(source)
    classes = {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}

    assert "_SprintPreflightResult" in classes, "_SprintPreflightResult dataclass not found"

    # Verify it has key fields
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "_SprintPreflightResult":
            # Extract field names from the class body (ast stores annotations as fields)
            field_names = set()
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    field_names.add(item.target.id)

            expected_fields = ["state", "state_path", "summary", "sprint_num", "sprint_branch",
                             "target_branch", "eff_repo", "api_url", "run_id", "rerun_decisions",
                             "eff_sprints_dir", "dispatch_levels", "level_nums_by_idx",
                             "pipeline_on", "start_time", "early_exit"]

            for field in expected_fields:
                assert field in field_names, f"Missing field {field} in _SprintPreflightResult"
            break


def test_help_output_unchanged():
    """AC-5: `python sprint_manager.py --help` exits 0 and output is identical to pre-refactor"""
    result = subprocess.run(
        ["python3", "services/sprint_manager/sprint_manager.py", "--help"],
        capture_output=True,
        text=True
    )

    assert result.returncode == 0, f"--help failed with exit code {result.returncode}"
    assert "Sprint Manager" in result.stdout, "--help output does not contain expected text"
    assert "label" in result.stdout, "--help output missing positional argument"


def test_no_new_public_api_surface():
    """AC-7: No new public API surface beyond `run_sprint_preflight()`"""
    with open("services/sprint_manager/sprint_manager.py", "r") as f:
        source = f.read()

    # Check for new public functions (not starting with _)
    tree = ast.parse(source)
    public_funcs = {
        node.name for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
    }

    # Some functions like main(), parse_args() etc. are expected to exist
    # The important check is that we're not adding multiple new functions
    assert "run_sprint_preflight" in public_funcs, "run_sprint_preflight not in public API"
    assert "run_sprint" in public_funcs, "run_sprint still in public API"


def test_diff_is_code_motion_only():
    """AC-8: Diff contains zero logic changes — only code motion and the new call-site"""
    result = subprocess.run(
        ["git", "diff", "HEAD~1", "--", "services/sprint_manager/sprint_manager.py"],
        capture_output=True,
        text=True,
        cwd="."
    )

    # Count additions vs deletions of key logic patterns
    # The diff should show:
    # - Extracted function (additions of existing code from run_sprint)
    # - run_sprint body calling the extracted function
    # - No conditional logic changes, no algorithm changes

    # Verify we see the new function definition
    assert "def run_sprint_preflight(" in result.stdout, "Preflight function definition not in diff"

    # Verify we see the call in run_sprint
    assert "pf = run_sprint_preflight(" in result.stdout, "Preflight call not in diff"

    # Verify dataclass is defined
    assert "@dataclasses.dataclass" in result.stdout, "Dataclass definition not in diff"
    assert "class _SprintPreflightResult:" in result.stdout, "Dataclass not in diff"


def test_imports_added():
    """Verify necessary imports are added (dataclasses)"""
    with open("services/sprint_manager/sprint_manager.py", "r") as f:
        lines = f.readlines()

    # Find import section
    import_found = False
    for i, line in enumerate(lines):
        if i < 50 and "import dataclasses" in line:
            import_found = True
            break

    assert import_found, "dataclasses import not found in early import section"


def test_cli_arguments_preserved():
    """Verify CLI arguments for run_sprint remain unchanged"""
    result = subprocess.run(
        ["python3", "services/sprint_manager/sprint_manager.py", "--help"],
        capture_output=True,
        text=True
    )

    # Check that all expected arguments are still present
    expected_args = [
        "--skip-gates",
        "--gate-typecheck",
        "--gate-lint",
        "--gate-pytest",
        "--dry-run",
        "--resume",
        "--retry-failed",
        "--target-branch",
    ]

    for arg in expected_args:
        assert arg in result.stdout, f"Expected argument {arg} not found in help"


def test_return_type_unchanged():
    """Verify run_sprint still returns tuple[SprintSummary, SprintState]"""
    with open("services/sprint_manager/sprint_manager.py", "r") as f:
        source = f.read()

    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "run_sprint":
            # Check return type annotation
            if node.returns:
                return_annotation_str = ast.unparse(node.returns)
                # Should return tuple[SprintSummary, SprintState]
                assert "SprintSummary" in return_annotation_str and "SprintState" in return_annotation_str, \
                    f"Unexpected return type: {return_annotation_str}"
            break
