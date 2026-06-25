"""Tests for issue #815: issues-mirror sync follows Link pagination (runs against UAT)"""
import os
import pytest
import httpx
from unittest.mock import Mock, patch, MagicMock


# Resolved from UAT .env at runtime; see tester skill Step 0.
BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "8001")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


# --- Acceptance Criteria Tests ---

def test_issues_mirror_pagination__follows_link_pagination():
    """AC1: _fetch_issues_conditional follows Link: rel="next" pagination and fetches all pages."""
    # Import the function and helper
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'apps', 'dashboard'))
    from github_events_sync import _fetch_issues_conditional, _parse_next_link

    # Mock httpx.get to simulate paginated responses
    with patch('github_events_sync.httpx.get') as mock_get:
        # First page response with Link header pointing to next page
        mock_first_page = Mock()
        mock_first_page.status_code = 200
        mock_first_page.headers = {
            'ETag': '"abc123"',
            'X-RateLimit-Remaining': '4999',
            'X-RateLimit-Reset': '1234567890',
            'Link': '<https://api.github.com/repos/owner/repo/issues?page=2&per_page=100>; rel="next"'
        }
        mock_first_page.json.return_value = [
            {'number': 1, 'title': 'Issue 1'},
            {'number': 2, 'title': 'Issue 2'},
        ]

        # Second page response with Link header pointing to third page
        mock_second_page = Mock()
        mock_second_page.status_code = 200
        mock_second_page.headers = {
            'X-RateLimit-Remaining': '4998',
            'X-RateLimit-Reset': '1234567890',
            'Link': '<https://api.github.com/repos/owner/repo/issues?page=3&per_page=100>; rel="next"'
        }
        mock_second_page.json.return_value = [
            {'number': 3, 'title': 'Issue 3'},
            {'number': 4, 'title': 'Issue 4'},
        ]

        # Third page response with no Link header (last page)
        mock_third_page = Mock()
        mock_third_page.status_code = 200
        mock_third_page.headers = {
            'X-RateLimit-Remaining': '4997',
            'X-RateLimit-Reset': '1234567890',
        }
        mock_third_page.json.return_value = [
            {'number': 5, 'title': 'Issue 5'},
        ]

        # Configure mock to return different responses for each call
        mock_get.side_effect = [mock_first_page, mock_second_page, mock_third_page]

        # Call the function
        status, items, etag, rate_info = _fetch_issues_conditional('owner', 'repo')

        # Assert all 5 issues were collected
        assert status == 200
        assert len(items) == 5
        assert items[0]['number'] == 1
        assert items[-1]['number'] == 5
        # Verify ETag is from first page
        assert etag == '"abc123"'


def test_issues_mirror_pagination__etag_first_page_only():
    """AC2: Conditional request (ETag) is used on first page; subsequent pages are unconditional."""
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'apps', 'dashboard'))
    from github_events_sync import _fetch_issues_conditional

    with patch('github_events_sync.httpx.get') as mock_get:
        # First page with Link header
        mock_first = Mock()
        mock_first.status_code = 200
        mock_first.headers = {
            'ETag': '"old_etag"',
            'X-RateLimit-Remaining': '4999',
            'X-RateLimit-Reset': '1234567890',
            'Link': '<https://api.github.com/repos/o/r/issues?page=2>; rel="next"'
        }
        mock_first.json.return_value = [{'number': 1}]

        # Second page (no Link header)
        mock_second = Mock()
        mock_second.status_code = 200
        mock_second.headers = {
            'X-RateLimit-Remaining': '4998',
            'X-RateLimit-Reset': '1234567890',
        }
        mock_second.json.return_value = [{'number': 2}]

        mock_get.side_effect = [mock_first, mock_second]

        # Call with an ETag
        _fetch_issues_conditional('owner', 'repo', etag='"old_etag"')

        # Verify first call has If-None-Match header
        first_call = mock_get.call_args_list[0]
        assert first_call[1]['headers']['If-None-Match'] == '"old_etag"'

        # Verify second call does NOT have If-None-Match header
        second_call = mock_get.call_args_list[1]
        assert 'If-None-Match' not in second_call[1]['headers']


def test_issues_mirror_pagination__304_fast_path_exits_early():
    """AC5: On 304 response, function exits early without fetching additional pages."""
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'apps', 'dashboard'))
    from github_events_sync import _fetch_issues_conditional

    with patch('github_events_sync.httpx.get') as mock_get:
        # First page returns 304 (not modified)
        mock_304 = Mock()
        mock_304.status_code = 304
        mock_304.headers = {
            'ETag': '"current_etag"',
            'X-RateLimit-Remaining': '5000',
            'X-RateLimit-Reset': '1234567890',
        }

        mock_get.return_value = mock_304

        # Call with an ETag
        status, items, etag, rate_info = _fetch_issues_conditional('owner', 'repo', etag='"current_etag"')

        # Assert 304 response and no items
        assert status == 304
        assert items is None
        # Assert only one call was made (no pagination)
        assert mock_get.call_count == 1


def test_issues_mirror_pagination__per_page_retained():
    """AC6: per_page=100 is retained per request (default parameter used)."""
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'apps', 'dashboard'))
    from github_events_sync import _fetch_issues_conditional

    with patch('github_events_sync.httpx.get') as mock_get:
        # Response with Link header
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.headers = {
            'ETag': '"etag"',
            'X-RateLimit-Remaining': '4999',
            'X-RateLimit-Reset': '1234567890',
            'Link': '<https://api.github.com/repos/o/r/issues?page=2&per_page=100>; rel="next"'
        }
        mock_resp.json.return_value = [{'number': 1}]

        # Second page
        mock_resp2 = Mock()
        mock_resp2.status_code = 200
        mock_resp2.headers = {
            'X-RateLimit-Remaining': '4998',
            'X-RateLimit-Reset': '1234567890',
        }
        mock_resp2.json.return_value = [{'number': 2}]

        mock_get.side_effect = [mock_resp, mock_resp2]

        _fetch_issues_conditional('owner', 'repo')

        # Verify first page request has per_page=100
        first_call = mock_get.call_args_list[0]
        assert first_call[1]['params']['per_page'] == 100


def test_issues_mirror_pagination__parse_next_link_extracts_url():
    """Helper function _parse_next_link correctly extracts rel="next" URL."""
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'apps', 'dashboard'))
    from github_events_sync import _parse_next_link

    # Standard GitHub Link header format
    link_header = '<https://api.github.com/repos/o/r/issues?page=2>; rel="next", <https://api.github.com/repos/o/r/issues?page=5>; rel="last"'
    result = _parse_next_link(link_header)

    assert result == 'https://api.github.com/repos/o/r/issues?page=2'


def test_issues_mirror_pagination__parse_next_link_returns_none_when_absent():
    """_parse_next_link returns None when rel="next" is not present."""
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'apps', 'dashboard'))
    from github_events_sync import _parse_next_link

    # Link header with only last, no next
    link_header = '<https://api.github.com/repos/o/r/issues?page=1>; rel="last"'
    result = _parse_next_link(link_header)

    assert result is None


def test_issues_mirror_pagination__excludes_pull_requests():
    """Fetched items exclude pull requests (verified by "pull_request" field filtering)."""
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'apps', 'dashboard'))
    from github_events_sync import _fetch_issues_conditional

    with patch('github_events_sync.httpx.get') as mock_get:
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.headers = {
            'ETag': '"etag"',
            'X-RateLimit-Remaining': '4999',
            'X-RateLimit-Reset': '1234567890',
        }
        # Response contains both issues and PRs (PRs have pull_request field)
        mock_resp.json.return_value = [
            {'number': 1, 'title': 'Issue 1'},
            {'number': 2, 'title': 'PR 1', 'pull_request': {'url': '...'}},
            {'number': 3, 'title': 'Issue 2'},
        ]

        mock_get.return_value = mock_resp

        status, items, etag, rate_info = _fetch_issues_conditional('owner', 'repo')

        # Assert only issues (not PRs) are returned
        assert status == 200
        assert len(items) == 2
        assert all('pull_request' not in item for item in items)
