"""Tests for #793: Document boundary map — routers, services, repos.

Each test is anchored to a specific acceptance criterion of issue #793. The
ticket is documentation-only: it produces two architecture docs,
``docs/architecture/boundaries.md`` and ``docs/architecture/frontend-map.md``.

The inventory test is the contract enforcer — it parses every route decorator
out of ``server.py`` and asserts each one is documented in exactly one cluster,
so the doc can never silently drift from the code (UAT steps 1, 5 and AC1/AC7).
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SERVER_PY = REPO_ROOT / "apps" / "dashboard" / "server.py"
PROJECT_HTML = REPO_ROOT / "apps" / "dashboard" / "static" / "project.html"
BOUNDARIES = REPO_ROOT / "docs" / "architecture" / "boundaries.md"
FRONTEND_MAP = REPO_ROOT / "docs" / "architecture" / "frontend-map.md"

# The exact eight clusters mandated by AC2.
REQUIRED_CLUSTERS = [
    "sprints",
    "tickets/issues",
    "projects",
    "settings",
    "analytics",
    "backup",
    "logs/activity",
    "system/health",
]

# Per-endpoint checklist line, e.g.
#   - [ ] `GET /api/sprints` (server.py:1571) — ...
ENDPOINT_RE = re.compile(
    r"^- \[[ x]\] `(GET|POST|PUT|DELETE|PATCH|WEBSOCKET) (\S+)` \(server\.py:(\d+)\)",
)
DECORATOR_RE = re.compile(
    r'^@app\.(get|post|put|delete|patch|websocket)\("([^"]*)"',
)


def server_decorators():
    """All route decorators in server.py as (line_no, METHOD, path) triples."""
    out = []
    for i, line in enumerate(SERVER_PY.read_text().splitlines(), 1):
        m = DECORATOR_RE.match(line)
        if m:
            out.append((i, m.group(1).upper(), m.group(2)))
    return out


def documented_endpoints():
    """All per-endpoint checklist entries in boundaries.md as triples."""
    out = []
    for line in BOUNDARIES.read_text().splitlines():
        m = ENDPOINT_RE.match(line.strip())
        if m:
            out.append((int(m.group(3)), m.group(1), m.group(2)))
    return out


# ── AC1: boundaries.md exists and inventories every endpoint in server.py ──────

def test_ac1_boundaries_file_exists():
    assert BOUNDARIES.is_file(), "docs/architecture/boundaries.md must exist"


def test_ac1_every_server_endpoint_is_inventoried():
    decorators = server_decorators()
    documented = documented_endpoints()
    missing = set(decorators) - set(documented)
    assert not missing, f"endpoints in server.py not inventoried: {sorted(missing)}"


# ── AC2: endpoints grouped into exactly the eight named clusters ───────────────

def test_ac2_all_eight_clusters_present():
    text = BOUNDARIES.read_text()
    for cluster in REQUIRED_CLUSTERS:
        assert f"Cluster: `{cluster}`" in text, f"missing cluster section: {cluster}"


def test_ac2_no_unexpected_clusters():
    headers = re.findall(r"Cluster: `([^`]+)`", BOUNDARIES.read_text())
    assert sorted(headers) == sorted(REQUIRED_CLUSTERS), (
        f"cluster set mismatch: {sorted(headers)}"
    )


# ── AC3: each cluster documents router module, service module, data layer ──────

def test_ac3_each_cluster_documents_three_layers():
    text = BOUNDARIES.read_text()
    # Split on cluster headers and check each block has all three fields.
    blocks = re.split(r"### Cluster: `[^`]+`", text)[1:]
    assert len(blocks) == len(REQUIRED_CLUSTERS)
    for cluster, block in zip(REQUIRED_CLUSTERS, blocks):
        assert "Target router module:" in block, f"{cluster} missing router module"
        assert "Service module:" in block, f"{cluster} missing service module"
        assert "Repo/data layer:" in block, f"{cluster} missing repo/data layer"


# ── AC4: layer rules stated explicitly (routers / services / repos) ────────────

def test_ac4_layer_rules_section_present():
    text = BOUNDARIES.read_text()
    assert "## Layer Rules" in text
    # All three rules, each unambiguous.
    assert re.search(r"\brouters\b.*HTTP only", text, re.IGNORECASE)
    assert re.search(r"\bservices\b.*no FastAPI", text, re.IGNORECASE)
    assert re.search(r"\brepos\b.*(SQL|GitHub|GH)", text, re.IGNORECASE)


# ── AC5: extraction order list published, lowest-risk first ────────────────────

def test_ac5_extraction_order_published():
    text = BOUNDARIES.read_text()
    assert "## Extraction Order" in text
    section = text.split("## Extraction Order", 1)[1]
    # An ordered list whose first item is flagged smallest/lowest-risk.
    first_item = re.search(r"^\s*1\.\s+(.*)$", section, re.MULTILINE)
    assert first_item, "extraction order must be a numbered list"
    assert re.search(
        r"lowest[- ]risk|smallest", first_item.group(1), re.IGNORECASE
    ), "first extraction-order entry must be labeled smallest/lowest-risk"


# ── AC6: per-endpoint checklist in every cluster ───────────────────────────────

def test_ac6_each_cluster_has_per_endpoint_checklist():
    text = BOUNDARIES.read_text()
    blocks = re.split(r"### Cluster: `[^`]+`", text)[1:]
    for cluster, block in zip(REQUIRED_CLUSTERS, blocks):
        entries = [l for l in block.splitlines() if ENDPOINT_RE.match(l.strip())]
        assert entries, f"cluster {cluster} has no per-endpoint checklist items"


# ── AC7: every endpoint appears in exactly one cluster — no orphans/dupes ───────

def test_ac7_no_orphans_no_duplicates():
    decorators = server_decorators()
    documented = documented_endpoints()
    # No duplicates in the doc.
    assert len(documented) == len(set(documented)), (
        "duplicate endpoint entries in boundaries.md"
    )
    # Exact set equality: every endpoint exactly once, nothing extra.
    assert set(decorators) == set(documented), (
        f"orphans: {sorted(set(decorators) - set(documented))}; "
        f"extras: {sorted(set(documented) - set(decorators))}"
    )


def test_ac7_uat5_counts_match_exactly():
    # UAT step 5: count in server.py == count across all clusters.
    assert len(server_decorators()) == len(documented_endpoints())


# ── AC8: frontend-map.md exists with a sitemap of project.html views ───────────

def test_ac8_frontend_map_exists_with_sitemap():
    assert FRONTEND_MAP.is_file(), "docs/architecture/frontend-map.md must exist"
    text = FRONTEND_MAP.read_text()
    assert "## Sitemap" in text
    # Every top-level tab switchTab('x') target must appear in the sitemap.
    tabs = set(re.findall(r"switchTab\('([^']+)'\)", PROJECT_HTML.read_text()))
    for tab in tabs:
        assert tab in text, f"frontend-map sitemap missing view: {tab}"


# ── AC9: page → API binding table covering every view and its API calls ────────

def test_ac9_binding_table_present_and_covers_views():
    text = FRONTEND_MAP.read_text()
    assert "## Page → API Binding" in text
    tabs = set(re.findall(r"switchTab\('([^']+)'\)", PROJECT_HTML.read_text()))
    # The binding section names every top-level view and binds at least one API.
    binding_section = text.split("## Page → API Binding", 1)[1]
    for tab in tabs:
        assert tab in binding_section, f"binding table missing view: {tab}"
    # Each view row references at least one /api/ endpoint.
    api_refs = re.findall(r"/api/[a-zA-Z0-9_./{}-]*", binding_section)
    assert len(api_refs) >= len(tabs), "binding table must list API calls per view"
