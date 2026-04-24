"""Unit tests for the CallRail HTTP client."""
from __future__ import annotations

from unittest.mock import patch

import pytest
import requests
import responses

from callrail_mcp.client import (
    MAX_RETRY_DELAY_SECONDS,
    VALID_TAG_COLORS,
    CallRailClient,
    CallRailError,
    _safe_path,
)


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


# ---- API-key whitespace stripping (v0.2.2) ----

def test_api_key_whitespace_is_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Trailing newlines / leading spaces are common copy-paste mistakes;
    they must be stripped before going into the Authorization header."""
    monkeypatch.delenv("CALLRAIL_API_KEY", raising=False)
    c = CallRailClient(api_key="  abc123\n")
    assert c.api_key == "abc123"
    assert "abc123" in c.session.headers["Authorization"]
    # No leading/trailing whitespace in the header value.
    assert c.session.headers["Authorization"] == c.session.headers["Authorization"].strip()


def test_api_key_strips_embedded_newlines(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CALLRAIL_API_KEY", raising=False)
    c = CallRailClient(api_key="abc\n123")
    assert c.api_key == "abc123"


def test_api_key_empty_after_stripping_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CALLRAIL_API_KEY", raising=False)
    with pytest.raises(CallRailError, match="empty after stripping"):
        CallRailClient(api_key="   \n  ")


# ---- v0.2.3: path traversal / URL-encoding ----

def test_safe_path_passes_clean_input() -> None:
    assert _safe_path("a/ACC1/calls/CAL_normal.json") == "a/ACC1/calls/CAL_normal.json"


def test_safe_path_blocks_dotdot_traversal() -> None:
    """`..` segments would let urljoin escape the base path. Must be rejected."""
    with pytest.raises(CallRailError, match="not allowed"):
        _safe_path("a/ACC1/calls/../../../etc/passwd.json")


def test_safe_path_blocks_dot_segment() -> None:
    with pytest.raises(CallRailError, match="not allowed"):
        _safe_path("a/./b")


def test_safe_path_blocks_empty_segment() -> None:
    """Double-slash creates an empty segment that some parsers treat oddly."""
    with pytest.raises(CallRailError, match="not allowed"):
        _safe_path("a//b")


def test_safe_path_blocks_control_chars() -> None:
    with pytest.raises(CallRailError, match="control character"):
        _safe_path("a/ACC1/tags/foo\x00bar")
    with pytest.raises(CallRailError, match="control character"):
        _safe_path("a/ACC1/calls/CAL\nbad")


def test_safe_path_encodes_special_chars() -> None:
    out = _safe_path("a/ACC1/tags/tag with spaces & ?")
    assert "%20" in out
    assert "%26" in out
    assert "%3F" in out


def test_safe_path_handles_empty() -> None:
    assert _safe_path("") == ""
    assert _safe_path("/") == ""


def test_get_with_traversal_id_raises_callrail_error(client: CallRailClient) -> None:
    """End-to-end: a malicious call_id surfaces as CallRailError, never an HTTP request."""
    with pytest.raises(CallRailError, match="not allowed"):
        client.get("a/ACC1/calls/../../etc/passwd.json")


# ---- v0.2.3: network error wrapping ----

def test_connection_error_is_wrapped(client: CallRailClient) -> None:
    """ConnectionError should be retried then surfaced as CallRailError, never bare."""
    with (
        patch.object(client.session, "request",
                     side_effect=requests.exceptions.ConnectionError("DNS fail")),
        pytest.raises(CallRailError, match="Network error"),
    ):
        client.get("a.json")


def test_timeout_is_wrapped(client: CallRailClient) -> None:
    with (
        patch.object(client.session, "request",
                     side_effect=requests.exceptions.Timeout("slow")),
        pytest.raises(CallRailError, match="Network error"),
    ):
        client.get("a.json")


def test_connection_error_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Transient ConnectionError on first attempt is retried."""
    monkeypatch.setenv("CALLRAIL_API_KEY", "test")
    c = CallRailClient(max_retries=2)
    monkeypatch.setattr("time.sleep", lambda _: None)  # make tests fast

    real_response = requests.Response()
    real_response.status_code = 200
    real_response._content = b'{"ok": true}'

    call_count = {"n": 0}
    def flaky(*a, **kw):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise requests.exceptions.ConnectionError("blip")
        return real_response

    with patch.object(c.session, "request", side_effect=flaky):
        data = c.get("a.json")
    assert data == {"ok": True}
    assert call_count["n"] == 2  # retried once, succeeded second time


# ---- v0.2.3: Retry-After parsing ----

def test_parse_retry_after_seconds() -> None:
    assert CallRailClient._parse_retry_after("5", 0) == 5.0
    assert CallRailClient._parse_retry_after("30", 1) == 30.0


def test_parse_retry_after_caps_at_max() -> None:
    assert CallRailClient._parse_retry_after("999999", 0) == MAX_RETRY_DELAY_SECONDS


def test_parse_retry_after_http_date_does_not_crash() -> None:
    """RFC 7231 also allows HTTP-date format. Must not crash with ValueError."""
    out = CallRailClient._parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT", 0)
    # Either parsed to a positive number of seconds, or fell back to default.
    assert isinstance(out, float)
    assert 0 <= out <= MAX_RETRY_DELAY_SECONDS


def test_parse_retry_after_garbage_falls_back() -> None:
    out = CallRailClient._parse_retry_after("not-a-thing", 1)
    assert isinstance(out, float)
    assert out <= MAX_RETRY_DELAY_SECONDS


def test_parse_retry_after_empty_uses_backoff() -> None:
    assert CallRailClient._parse_retry_after("", 2) == 4.0
    assert CallRailClient._parse_retry_after(None, 3) == 8.0


# ---- v0.2.3: response validation ----

@responses.activate
def test_rejects_json_array_response(client: CallRailClient) -> None:
    """If CallRail returned a JSON array we'd return it, then downstream
    .get() calls would AttributeError. Now we reject upfront."""
    responses.add(responses.GET, "https://api.callrail.com/v3/a.json",
                  body=b'["a","b","c"]', status=200,
                  headers={"content-type": "application/json"})
    with pytest.raises(CallRailError, match="Expected JSON object"):
        client.get("a.json")


@responses.activate
def test_rejects_redirect(client: CallRailClient) -> None:
    """Following a 3xx could leak the Authorization header to attacker-controlled host."""
    responses.add(responses.GET, "https://api.callrail.com/v3/a.json",
                  status=302, headers={"Location": "https://evil.com/exfil"})
    with pytest.raises(CallRailError, match="redirect"):
        client.get("a.json")


# ---- v0.2.3: context manager ----

def test_client_close_releases_session(client: CallRailClient) -> None:
    client.close()
    # Calling close again should be safe.
    client.close()


def test_client_as_context_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALLRAIL_API_KEY", "test")
    with CallRailClient() as c:
        assert c.api_key == "test"
    # After exit, .close() has run; subsequent close() must not raise.
    c.close()


# ---- v0.2.3: redirects disabled at session level ----

def test_session_max_redirects_zero(client: CallRailClient) -> None:
    assert client.session.max_redirects == 0


# ---- v0.2.3: pagination clamps per_page ----

@responses.activate
def test_paginate_clamps_negative_per_page(client: CallRailClient) -> None:
    responses.add(responses.GET, "https://api.callrail.com/v3/a/ACC1/calls.json",
                  json={"calls": [{"id": "a"}], "total_pages": 1}, status=200)
    list(client.paginate("a/ACC1/calls.json", params={"per_page": -5}, items_key="calls"))
    sent_pp = responses.calls[0].request.params["per_page"]
    assert int(sent_pp) >= 1


@responses.activate
def test_paginate_clamps_huge_per_page(client: CallRailClient) -> None:
    responses.add(responses.GET, "https://api.callrail.com/v3/a/ACC1/calls.json",
                  json={"calls": [{"id": "a"}], "total_pages": 1}, status=200)
    list(client.paginate("a/ACC1/calls.json", params={"per_page": 99999}, items_key="calls"))
    sent_pp = int(responses.calls[0].request.params["per_page"])
    assert sent_pp <= 250
