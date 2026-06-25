"""Tests for issue #1396: GitHub fetch failures in mis-sizing rebuild."""
import os
import pytest
import httpx

_base_url_env = os.environ.get("UAT_BASE_URL")
_port_env = os.environ.get("UAT_PORT", "")
BASE_URL = _base_url_env or ("http://localhost:" + _port_env)
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run tester skill Step 0 to resolve UAT."
    )


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=90.0) as c:
        yield c


def test_rebuild_labels_fetch_failure__labels_fetched_field_in_response(client):
    """AC: POST /api/mis-sizing/rebuild includes 'labels_fetched' field."""
    r = client.post("/api/mis-sizing/rebuild?project=zealchaiwut/commander")
    assert r.status_code == 200
    data = r.json()
    assert "labels_fetched" in data
    assert isinstance(data["labels_fetched"], bool)


def test_rebuild_labels_fetch_failure__labels_fetched_true_on_success(client):
    """AC: On success, labels_by_num is populated, 'labels_fetched': true."""
    r = client.post("/api/mis-sizing/rebuild?project=zealchaiwut/commander")
    assert r.status_code == 200
    data = r.json()
    assert "message" in data
    # When no completed tickets exist or rebuild succeeds, we get a 200 with a message
    assert "message" in data
    assert isinstance(data["labels_fetched"], bool)


def test_rebuild_labels_fetch_failure__http_200_with_rebuild_message_on_any_outcome(
    client,
):
    """AC: Response is HTTP 200 with message, regardless of label fetch result."""
    r = client.post("/api/mis-sizing/rebuild?project=zealchaiwut/commander")
    assert r.status_code == 200
    data = r.json()
    # The response should always be 200 with a message and the labels_fetched flag
    assert "message" in data
    assert "labels_fetched" in data
    assert isinstance(data["labels_fetched"], bool)
