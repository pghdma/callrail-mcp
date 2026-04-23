"""Unit tests for the CallRail HTTP client."""
from __future__ import annotations

import pytest
import responses

from callrail_mcp.client import CallRailClient, CallRailError


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
