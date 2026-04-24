"""Unit tests for the CallRail HTTP client."""
from __future__ import annotations

import pytest
import responses

from callrail_mcp.client import VALID_TAG_COLORS, CallRailClient, CallRailError


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> CallRailClient:
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    return CallRailClient(max_retries=2)


# ---- auth ----

def test_missing_key_raises(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.delenv("CALLRAIL_API_KEY", raising=False)
    monkeypatch.setenv("CALLRAIL_API_KEY_FILE", str(tmp_path / "nonexistent.txt"))
    with pytest.raises(CallRailError, match="No CallRail API key"):
        CallRailClient()


def test_key_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALLRAIL_API_KEY", "abc123")
    c = CallRailClient()
    assert c.api_key == "abc123"
    assert c.session.headers["Authorization"] == "Token token=abc123"


def test_key_from_file(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.delenv("CALLRAIL_API_KEY", raising=False)
    key_file = tmp_path / "key.txt"
    key_file.write_text("file-key\n")
    monkeypatch.setenv("CALLRAIL_API_KEY_FILE", str(key_file))
    c = CallRailClient()
    assert c.api_key == "file-key"


# ---- get ----

@responses.activate
def test_get_happy_path(client: CallRailClient) -> None:
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    data = client.get("a.json")
    assert data == {"accounts": [{"id": "ACC1"}]}


@responses.activate
def test_get_error_raises(client: CallRailClient) -> None:
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"error": "invalid token"},
        status=401,
    )
    with pytest.raises(CallRailError) as exc:
        client.get("a.json")
    assert exc.value.status == 401


# ---- retry / backoff ----

@responses.activate
def test_retries_on_429(client: CallRailClient) -> None:
    responses.add(responses.GET, "https://api.callrail.com/v3/a.json",
                  json={"error": "rate_limited"}, status=429,
                  headers={"Retry-After": "0"})
    responses.add(responses.GET, "https://api.callrail.com/v3/a.json",
                  json={"accounts": [{"id": "ACC1"}]}, status=200)
    data = client.get("a.json")
    assert data["accounts"][0]["id"] == "ACC1"
    assert len(responses.calls) == 2


@responses.activate
def test_retries_on_500(client: CallRailClient) -> None:
    responses.add(responses.GET, "https://api.callrail.com/v3/a.json",
                  json={"error": "internal"}, status=500)
    responses.add(responses.GET, "https://api.callrail.com/v3/a.json",
                  json={"ok": True}, status=200)
    data = client.get("a.json")
    assert data == {"ok": True}
    assert len(responses.calls) == 2


@responses.activate
def test_retry_exhaustion_raises(client: CallRailClient) -> None:
    for _ in range(5):
        responses.add(responses.GET, "https://api.callrail.com/v3/a.json",
                      json={"error": "gone"}, status=503)
    with pytest.raises(CallRailError) as exc:
        client.get("a.json")
    assert exc.value.status == 503


# ---- pagination ----

@responses.activate
def test_paginate_yields_all_items(client: CallRailClient) -> None:
    responses.add(responses.GET, "https://api.callrail.com/v3/a/ACC1/calls.json",
                  json={"calls": [{"id": "a"}, {"id": "b"}], "total_pages": 2, "page": 1}, status=200)
    responses.add(responses.GET, "https://api.callrail.com/v3/a/ACC1/calls.json",
                  json={"calls": [{"id": "c"}], "total_pages": 2, "page": 2}, status=200)
    items = list(client.paginate("a/ACC1/calls.json", items_key="calls"))
    assert [i["id"] for i in items] == ["a", "b", "c"]


@responses.activate
def test_paginate_max_pages_cap(client: CallRailClient) -> None:
    # Server claims infinite pages; our cap should stop at max_pages.
    for _ in range(10):
        responses.add(responses.GET, "https://api.callrail.com/v3/a/ACC1/calls.json",
                      json={"calls": [{"id": "x"}], "total_pages": 99}, status=200)
    items = list(client.paginate("a/ACC1/calls.json", items_key="calls", max_pages=3))
    assert len(items) == 3


# ---- account resolution ----

@responses.activate
def test_resolve_account_id_returns_given(client: CallRailClient) -> None:
    aid = client.resolve_account_id("ACC_EXPLICIT")
    assert aid == "ACC_EXPLICIT"


@responses.activate
def test_resolve_account_id_fetches(client: CallRailClient) -> None:
    responses.add(responses.GET, "https://api.callrail.com/v3/a.json",
                  json={"accounts": [{"id": "ACC_FIRST"}]}, status=200)
    aid = client.resolve_account_id()
    assert aid == "ACC_FIRST"


@responses.activate
def test_resolve_account_id_empty_raises(client: CallRailClient) -> None:
    responses.add(responses.GET, "https://api.callrail.com/v3/a.json",
                  json={"accounts": []}, status=200)
    with pytest.raises(CallRailError, match="No CallRail accounts"):
        client.resolve_account_id()


# ---- write methods (v0.2) ----

@responses.activate
def test_post_returns_parsed_json(client: CallRailClient) -> None:
    responses.add(responses.POST, "https://api.callrail.com/v3/a/ACC1/tags.json",
                  json={"id": "TAG1", "name": "spam"}, status=201)
    data = client.post("a/ACC1/tags.json", {"name": "spam", "company_id": "COM1"})
    assert data["id"] == "TAG1"
    assert data["name"] == "spam"
    # body sent
    sent = responses.calls[0].request.body
    assert b"spam" in sent
    assert b"COM1" in sent


@responses.activate
def test_put_partial_update(client: CallRailClient) -> None:
    responses.add(responses.PUT, "https://api.callrail.com/v3/a/ACC1/calls/CAL1.json",
                  json={"id": "CAL1", "spam": True, "tags": ["junk"]}, status=200)
    data = client.put("a/ACC1/calls/CAL1.json", {"spam": True, "tags": ["junk"]})
    assert data["spam"] is True
    assert data["tags"] == ["junk"]


@responses.activate
def test_delete_returns_empty_on_204(client: CallRailClient) -> None:
    responses.add(responses.DELETE, "https://api.callrail.com/v3/a/ACC1/tags/TAG1.json",
                  status=204)
    data = client.delete("a/ACC1/tags/TAG1.json")
    assert data == {}


@responses.activate
def test_put_error_raises(client: CallRailClient) -> None:
    responses.add(responses.PUT, "https://api.callrail.com/v3/a/ACC1/calls/CAL_NOPE.json",
                  json={"error": "not found"}, status=404)
    with pytest.raises(CallRailError) as exc:
        client.put("a/ACC1/calls/CAL_NOPE.json", {"spam": True})
    assert exc.value.status == 404


@responses.activate
def test_post_retries_on_429(client: CallRailClient) -> None:
    responses.add(responses.POST, "https://api.callrail.com/v3/a/ACC1/tags.json",
                  status=429, headers={"Retry-After": "0"})
    responses.add(responses.POST, "https://api.callrail.com/v3/a/ACC1/tags.json",
                  json={"id": "TAG_OK"}, status=201)
    data = client.post("a/ACC1/tags.json", {"name": "x"})
    assert data["id"] == "TAG_OK"
    assert len(responses.calls) == 2


# ---- tag color enum ----

def test_valid_tag_colors_contains_known_values() -> None:
    """The set discovered by exhaustive API testing — guards against accidental
    edits that would remove a real value or accept an unsupported one."""
    expected = {"red1", "red2", "orange1", "yellow1", "green1",
                "blue1", "purple1", "pink1", "gray1", "gray2"}
    assert set(VALID_TAG_COLORS) == expected


def test_valid_tag_colors_excludes_common_invalid_values() -> None:
    """Plain color names without numbers are NOT valid in CallRail's API."""
    for invalid in ("red", "blue", "green", "gray", "black", "white", "#FF0000", "brown1"):
        assert invalid not in VALID_TAG_COLORS
