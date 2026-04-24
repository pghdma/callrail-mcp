"""Server-level tests for tool input validation, helpers, and lazy client init."""
from __future__ import annotations

import json

import pytest

import callrail_mcp.server as server_mod
from callrail_mcp.server import (
    _clamp_per_page,
    _clean_tag_list,
    _date_window,
    _validate_date,
    _validate_window,
)

# ---- helpers ----

def test_clamp_per_page_floor_and_ceiling() -> None:
    assert _clamp_per_page(0) == 1
    assert _clamp_per_page(-5) == 1
    assert _clamp_per_page(1) == 1
    assert _clamp_per_page(100) == 100
    assert _clamp_per_page(250) == 250
    assert _clamp_per_page(300) == 250
    assert _clamp_per_page(99999) == 250


def test_clean_tag_list_filters_and_dedupes() -> None:
    assert _clean_tag_list(["a", "", "b", "  ", "a", " c "]) == ["a", "b", "c"]
    assert _clean_tag_list([]) == []
    assert _clean_tag_list(None) == []
    assert _clean_tag_list(["", "  ", "\t"]) == []


def test_validate_date_accepts_iso() -> None:
    ok, msg = _validate_date("2026-04-23", "start_date")
    assert ok and msg == ""


def test_validate_date_rejects_garbage() -> None:
    ok, msg = _validate_date("not-a-date", "start_date")
    assert not ok
    assert "start_date" in msg

    ok, msg = _validate_date("2026/04/23", "start_date")  # wrong separator
    assert not ok

    ok, msg = _validate_date("2026-13-99", "start_date")  # impossible date
    assert not ok


def test_validate_date_treats_empty_as_not_provided() -> None:
    ok, _ = _validate_date("", "start_date")
    assert ok


def test_validate_window_rejects_swapped_dates() -> None:
    ok, msg = _validate_window(None, "2026-04-23", "2026-04-01")
    assert not ok
    assert "before start_date" in msg


def test_validate_window_rejects_negative_days() -> None:
    ok, msg = _validate_window(-7, None, None)
    assert not ok
    assert "negative" in msg


def test_date_window_ignores_zero_or_negative_days() -> None:
    """days=0 should NOT silently include the entire history."""
    assert _date_window(0, None, None) == {}
    assert _date_window(-5, None, None) == {}
    # Positive value still works.
    out = _date_window(7, None, None)
    assert "start_date" in out and "end_date" in out


# ---- lazy client init ----

def test_module_imports_without_api_key(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Critical: importing the server module must NOT require an API key.

    This exercise simulates a clean environment and ensures we don't regress
    the lazy-init behavior.
    """
    monkeypatch.delenv("CALLRAIL_API_KEY", raising=False)
    monkeypatch.setenv("CALLRAIL_API_KEY_FILE", str(tmp_path / "nonexistent"))

    # Reset the singleton so get_client would build fresh.
    server_mod._client = None

    # Module attributes are accessible without instantiating the client.
    assert hasattr(server_mod, "list_calls")
    assert hasattr(server_mod, "get_client")
    # Calling get_client() WOULD raise (no key available); but importing
    # / referencing the proxy does not.
    assert server_mod.client is not None


# ---- search_calls_by_number guard ----

def test_search_phone_rejects_empty_input(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = None  # force lazy rebuild
    out = json.loads(server_mod.search_calls_by_number(phone_number=""))
    assert out["error"] is True
    assert "at least 7 digits" in out["message"]


def test_search_phone_rejects_letters_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = None
    out = json.loads(server_mod.search_calls_by_number(phone_number="abcdef"))
    assert out["error"] is True
    assert "at least 7 digits" in out["message"]


def test_search_phone_rejects_too_few_digits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = None
    out = json.loads(server_mod.search_calls_by_number(phone_number="123-45"))
    assert out["error"] is True
    assert "at least 7 digits" in out["message"]


# ---- empty tag list guard ----

def test_add_call_tags_rejects_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = None
    out = json.loads(server_mod.add_call_tags(call_id="CAL_x", tags=[]))
    assert out["error"] is True

    out2 = json.loads(server_mod.add_call_tags(call_id="CAL_x", tags=["", "  "]))
    assert out2["error"] is True


# ---- update_call no-op guard ----

def test_update_call_with_no_fields_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = None
    out = json.loads(server_mod.update_call(call_id="CAL_x"))
    assert out["error"] is True
    assert "No fields supplied" in out["message"]
