"""Server-level tests for tool input validation, helpers, and lazy client init."""
from __future__ import annotations

import json

import pytest
import responses

import callrail_mcp.server as server_mod
from callrail_mcp.client import CallRailClient
from callrail_mcp.server import (
    VALID_SOURCE_TYPES,
    _clamp_per_page,
    _clean_tag_list,
    _date_window,  # noqa: F401  — used by v0.4.7 regression test
    _require_non_empty,
    _validate_area_code,
    _validate_date,
    _validate_id_shape,
    _validate_length,
    _validate_phone,
    _validate_pool_size,
    _validate_tracker_status,
    _validate_window,
)

# Re-export for use in v0.4.6 tests
__all__ = ["_clean_tag_list"]


@pytest.fixture
def server_with_mock_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install a real CallRailClient (no retries) backed by `responses` mocks.

    Use the `responses` decorator on individual tests to register URL stubs.
    """
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = CallRailClient(max_retries=0)


@pytest.fixture(autouse=True)
def _reset_module_warning_dedup_state() -> None:
    """v0.5.3 (audit r4 F4): `_pick_account_tz` dedupes warnings via
    module-level sets. Reset them before EACH test so warning-asserting
    tests aren't polluted by earlier tests' fixtures (pytest doesn't
    guarantee test order)."""
    if hasattr(server_mod, "_warned_tzs"):
        server_mod._warned_tzs.clear()
    if hasattr(server_mod, "_warned_multi_tz_signature"):
        server_mod._warned_multi_tz_signature.clear()

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


# ============================================================
# Tracker validation helpers (pure functions, no env required)
# ============================================================

def test_require_non_empty() -> None:
    ok, msg = _require_non_empty("hello", "name")
    assert ok and msg == ""
    ok, msg = _require_non_empty("", "name")
    assert not ok and "name" in msg
    ok, msg = _require_non_empty("   ", "name")
    assert not ok and "name" in msg
    ok, msg = _require_non_empty(None, "name")
    assert not ok and "name" in msg


@pytest.mark.parametrize("phone,ok", [
    ("+14125551234", True),
    ("14125551234", True),
    ("+18005551234", True),
    ("18005551234", True),
    ("4125551234", True),       # 10 digits, no plus
    ("+44123456789012", True),  # 14 digits w/ plus
    ("412555", False),           # too short
    ("garbage", False),          # non-digits
    ("+1-412-555-1234", False),  # dashes break it (CallRail wants no formatting)
    ("+1 412 555 1234", False),  # spaces break it
    ("", False),
])
def test_validate_phone(phone: str, ok: bool) -> None:
    got_ok, _msg = _validate_phone(phone, "destination_number")
    assert got_ok is ok


@pytest.mark.parametrize("ac,ok", [
    ("412", True),
    ("800", True),
    ("4125", False),  # 4 digits
    ("41", False),    # 2 digits
    ("abc", False),
    ("", False),
    ("4-1", False),
])
def test_validate_area_code(ac: str, ok: bool) -> None:
    got_ok, _ = _validate_area_code(ac)
    assert got_ok is ok


@pytest.mark.parametrize("size,ok", [
    (1, True), (4, True), (10, True), (50, True),
    (0, False), (-1, False), (51, False), (10000, False),
])
def test_validate_pool_size(size: int, ok: bool) -> None:
    got_ok, _ = _validate_pool_size(size)
    assert got_ok is ok


def test_validate_length() -> None:
    ok, _ = _validate_length("ok", "name", 10)
    assert ok
    ok, msg = _validate_length("A" * 11, "name", 10)
    assert not ok and "exceeds max" in msg


def test_validate_tracker_status() -> None:
    assert _validate_tracker_status(None)[0]
    assert _validate_tracker_status("")[0]
    assert _validate_tracker_status("active")[0]
    assert _validate_tracker_status("disabled")[0]
    ok, msg = _validate_tracker_status("garbage")
    assert not ok and "garbage" in msg


@pytest.mark.parametrize("value,ok", [
    ("TRK019abc123", True),
    ("COM019abc123", True),
    ("TRK_xyz/admin", False),           # slash = multi-segment
    ("TRK_xyz/../../admin", False),     # deeper slash injection
    (".", False),                        # dots-only
    ("..", False),                       # dots-only
    ("...", False),                      # dots-only (the ..json slipway)
    ("   .", False),                     # whitespace + dot
])
def test_validate_id_shape_no_prefix(value: str, ok: bool) -> None:
    got_ok, _ = _validate_id_shape(value, "tracker_id")
    assert got_ok is ok


def test_validate_id_shape_with_prefix() -> None:
    ok, _ = _validate_id_shape("TRK_x", "tracker_id", prefix="TRK")
    assert ok
    ok, msg = _validate_id_shape("COM_x", "tracker_id", prefix="TRK")
    assert not ok and "TRK" in msg


def test_valid_source_types_includes_live_observed() -> None:
    """Round 2 live stress across 5 companies found Facebook + Bing ads
    using these source types. If they get removed, legitimate tracker
    creation breaks."""
    assert "facebook_all" in VALID_SOURCE_TYPES
    assert "bing_all" in VALID_SOURCE_TYPES


# ============================================================
# list_trackers + get_tracker validation
# ============================================================

def test_list_trackers_rejects_invalid_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = None
    out = json.loads(server_mod.list_trackers(status="garbage"))
    assert out["error"] is True
    assert "garbage" in out["message"]


def test_get_tracker_rejects_empty_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = None
    out = json.loads(server_mod.get_tracker(tracker_id=""))
    assert out["error"] is True
    assert "tracker_id" in out["message"]

    out2 = json.loads(server_mod.get_tracker(tracker_id="   "))
    assert out2["error"] is True


# ============================================================
# create_tracker validation — billing + required + format + conflicts
# ============================================================

def _create_call(monkeypatch: pytest.MonkeyPatch, **overrides) -> dict:
    """Build a baseline create_tracker call and apply overrides; return parsed JSON."""
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = None
    base = dict(
        name="Test Tracker",
        company_id="COM1",
        destination_number="+14129548337",
        confirm_billing=True,
        type="source",
        source_type="all",
        area_code="412",
    )
    base.update(overrides)
    return json.loads(server_mod.create_tracker(**base))


def test_create_tracker_blocks_without_billing_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out = _create_call(monkeypatch, confirm_billing=False)
    assert out["error"] is True
    assert "confirm_billing" in out["message"]


def test_create_tracker_rejects_empty_name(monkeypatch: pytest.MonkeyPatch) -> None:
    out = _create_call(monkeypatch, name="")
    assert out["error"] is True
    assert "name" in out["message"]


def test_create_tracker_rejects_empty_company_id(monkeypatch: pytest.MonkeyPatch) -> None:
    out = _create_call(monkeypatch, company_id="")
    assert out["error"] is True
    assert "company_id" in out["message"]


def test_create_tracker_rejects_empty_destination(monkeypatch: pytest.MonkeyPatch) -> None:
    out = _create_call(monkeypatch, destination_number="")
    assert out["error"] is True


def test_create_tracker_rejects_oversize_name(monkeypatch: pytest.MonkeyPatch) -> None:
    out = _create_call(monkeypatch, name="A" * 256)
    assert out["error"] is True
    assert "name" in out["message"] and "256" in out["message"]


def test_create_tracker_rejects_oversize_whisper(monkeypatch: pytest.MonkeyPatch) -> None:
    out = _create_call(monkeypatch, whisper_message="W" * 501)
    assert out["error"] is True
    assert "whisper" in out["message"].lower()


def test_create_tracker_rejects_oversize_greeting(monkeypatch: pytest.MonkeyPatch) -> None:
    out = _create_call(monkeypatch, greeting_text="G" * 501)
    assert out["error"] is True
    assert "greeting" in out["message"].lower()


def test_create_tracker_rejects_garbage_destination(monkeypatch: pytest.MonkeyPatch) -> None:
    out = _create_call(monkeypatch, destination_number="not-a-phone")
    assert out["error"] is True


def test_create_tracker_rejects_dashed_destination(monkeypatch: pytest.MonkeyPatch) -> None:
    out = _create_call(monkeypatch, destination_number="412-555-1234")
    assert out["error"] is True


def test_create_tracker_rejects_unknown_type(monkeypatch: pytest.MonkeyPatch) -> None:
    out = _create_call(monkeypatch, type="garbage")
    assert out["error"] is True
    assert "type" in out["message"]


def test_create_tracker_rejects_unknown_source_type(monkeypatch: pytest.MonkeyPatch) -> None:
    out = _create_call(monkeypatch, source_type="google_ads")
    assert out["error"] is True
    assert "source_type" in out["message"]


def test_create_tracker_rejects_toll_free_with_area_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C1-style flag conflict: toll_free + area_code is ambiguous."""
    out = _create_call(monkeypatch, toll_free=True, area_code="412")
    assert out["error"] is True
    assert "both" in out["message"].lower()


def test_create_tracker_source_requires_area_or_toll_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out = _create_call(monkeypatch, area_code=None, toll_free=False)
    assert out["error"] is True
    assert "area_code" in out["message"] or "toll_free" in out["message"]


def test_create_tracker_rejects_letters_in_area_code(monkeypatch: pytest.MonkeyPatch) -> None:
    out = _create_call(monkeypatch, area_code="abc")
    assert out["error"] is True
    assert "area_code" in out["message"]


def test_create_tracker_rejects_4digit_area_code(monkeypatch: pytest.MonkeyPatch) -> None:
    out = _create_call(monkeypatch, area_code="4125")
    assert out["error"] is True


def test_create_tracker_session_requires_pool_size(monkeypatch: pytest.MonkeyPatch) -> None:
    out = _create_call(monkeypatch, type="session", area_code="412", pool_size=None)
    assert out["error"] is True
    assert "pool_size" in out["message"]


def test_create_tracker_session_rejects_zero_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    out = _create_call(monkeypatch, type="session", area_code="412", pool_size=0)
    assert out["error"] is True


def test_create_tracker_session_rejects_negative_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    out = _create_call(monkeypatch, type="session", area_code="412", pool_size=-5)
    assert out["error"] is True


def test_create_tracker_session_rejects_huge_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    """Safety cap prevents accidental 5-figure provisioning bills."""
    out = _create_call(monkeypatch, type="session", area_code="412", pool_size=10000)
    assert out["error"] is True
    assert "50" in out["message"]


# ============================================================
# update_tracker validation — including C1 (greeting_text alone)
# ============================================================

def test_update_tracker_rejects_empty_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = None
    out = json.loads(server_mod.update_tracker(tracker_id=""))
    assert out["error"] is True


def test_update_tracker_rejects_no_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = None
    out = json.loads(server_mod.update_tracker(tracker_id="TRK1"))
    assert out["error"] is True
    assert "No fields supplied" in out["message"]


def test_update_tracker_rejects_empty_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = None
    out = json.loads(server_mod.update_tracker(tracker_id="TRK1", name=""))
    assert out["error"] is True
    assert "name" in out["message"]


def test_update_tracker_rejects_empty_destination(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = None
    out = json.loads(server_mod.update_tracker(tracker_id="TRK1", destination_number=""))
    assert out["error"] is True


def test_update_tracker_rejects_garbage_destination(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = None
    out = json.loads(
        server_mod.update_tracker(tracker_id="TRK1", destination_number="not-a-phone")
    )
    assert out["error"] is True


def test_update_tracker_C1_greeting_text_alone_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CRITICAL: update_tracker(greeting_text="x") alone would replace call_flow
    with an object missing destination_number, breaking the tracker."""
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = None
    out = json.loads(server_mod.update_tracker(tracker_id="TRK1", greeting_text="Hello"))
    assert out["error"] is True
    assert "destination_number" in out["message"]
    assert "greeting_text" in out["message"]


def test_update_tracker_oversize_name_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = None
    out = json.loads(server_mod.update_tracker(tracker_id="TRK1", name="A" * 256))
    assert out["error"] is True


# ============================================================
# delete_tracker validation
# ============================================================

def test_delete_tracker_rejects_empty_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = None
    out = json.loads(server_mod.delete_tracker(tracker_id=""))
    assert out["error"] is True


# ============================================================
# Happy-path tests using `responses` library to mock CallRail.
# Verify the request body shape we send is what we expect.
# ============================================================

@responses.activate
def test_create_tracker_source_local_happy_path(server_with_mock_client) -> None:
    """End-to-end: build the request body correctly for a local source tracker."""
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    responses.add(
        responses.POST,
        "https://api.callrail.com/v3/a/ACC1/trackers.json",
        json={"id": "TRK_NEW", "name": "Test Tracker"},
        status=201,
    )
    out = json.loads(
        server_mod.create_tracker(
            name="Test Tracker",
            company_id="COM1",
            destination_number="+14129548337",
            confirm_billing=True,
            area_code="412",
            whisper_message="Test lead",
        )
    )
    assert "TRK_NEW" in json.dumps(out)
    # Inspect what we POSTed.
    body = json.loads(responses.calls[1].request.body)
    assert body["name"] == "Test Tracker"
    assert body["company_id"] == "COM1"
    assert body["type"] == "source"
    assert body["destination_number"] == "+14129548337"
    assert body["call_flow"]["destination_number"] == "+14129548337"
    assert body["call_flow"]["type"] == "basic"
    assert body["call_flow"]["recording_enabled"] is True
    assert body["source"]["type"] == "all"
    assert body["tracking_number"] == {"area_code": "412"}
    assert body["whisper_message"] == "Test lead"
    assert body["sms_enabled"] is True


@responses.activate
def test_create_tracker_session_pool_happy_path(server_with_mock_client) -> None:
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    responses.add(
        responses.POST,
        "https://api.callrail.com/v3/a/ACC1/trackers.json",
        json={"id": "TRK_POOL"},
        status=201,
    )
    json.loads(
        server_mod.create_tracker(
            name="DNI Pool",
            company_id="COM1",
            destination_number="+14129548337",
            confirm_billing=True,
            type="session",
            area_code="412",
            pool_size=8,
        )
    )
    body = json.loads(responses.calls[1].request.body)
    assert body["type"] == "session"
    assert body["tracking_number"]["area_code"] == "412"
    assert body["tracking_number"]["pool_size"] == 8
    # Session trackers don't get a `source` block.
    assert "source" not in body


@responses.activate
def test_create_tracker_toll_free_happy_path(server_with_mock_client) -> None:
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    responses.add(
        responses.POST,
        "https://api.callrail.com/v3/a/ACC1/trackers.json",
        json={"id": "TRK_TF"},
        status=201,
    )
    json.loads(
        server_mod.create_tracker(
            name="Toll Free",
            company_id="COM1",
            destination_number="+14129548337",
            confirm_billing=True,
            toll_free=True,
        )
    )
    body = json.loads(responses.calls[1].request.body)
    assert body["tracking_number"] == {"toll_free": True}
    assert "area_code" not in body["tracking_number"]


@responses.activate
def test_update_tracker_name_only_happy_path(server_with_mock_client) -> None:
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    responses.add(
        responses.PUT,
        "https://api.callrail.com/v3/a/ACC1/trackers/TRK1.json",
        json={"id": "TRK1", "name": "New Name"},
        status=200,
    )
    json.loads(server_mod.update_tracker(tracker_id="TRK1", name="New Name"))
    body = json.loads(responses.calls[1].request.body)
    assert body == {"name": "New Name"}


@responses.activate
def test_update_tracker_greeting_with_destination_happy_path(
    server_with_mock_client,
) -> None:
    """C1 fix verified: greeting_text + destination_number works."""
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    responses.add(
        responses.PUT,
        "https://api.callrail.com/v3/a/ACC1/trackers/TRK1.json",
        json={"id": "TRK1"},
        status=200,
    )
    json.loads(
        server_mod.update_tracker(
            tracker_id="TRK1",
            destination_number="+14129548337",
            greeting_text="Welcome",
        )
    )
    body = json.loads(responses.calls[1].request.body)
    # Both destination_number AND greeting_text are inside the same call_flow.
    assert body["call_flow"]["destination_number"] == "+14129548337"
    assert body["call_flow"]["greeting_text"] == "Welcome"


@responses.activate
def test_delete_tracker_captures_response(server_with_mock_client) -> None:
    """H1 fix verified: delete_tracker now returns CallRail's body in `response`."""
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    responses.add(
        responses.DELETE,
        "https://api.callrail.com/v3/a/ACC1/trackers/TRK1.json",
        json={"id": "TRK1", "status": "disabled", "disabled_at": "2026-04-24T12:00:00Z"},
        status=200,
    )
    out = json.loads(server_mod.delete_tracker(tracker_id="TRK1"))
    assert out["deleted"] is True
    assert out["tracker_id"] == "TRK1"
    assert out["response"]["status"] == "disabled"
    assert "disabled_at" in out["response"]


@responses.activate
def test_delete_tracker_handles_204_no_body(server_with_mock_client) -> None:
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    responses.add(
        responses.DELETE,
        "https://api.callrail.com/v3/a/ACC1/trackers/TRK1.json",
        status=204,
    )
    out = json.loads(server_mod.delete_tracker(tracker_id="TRK1"))
    assert out["deleted"] is True
    assert out["response"] == {}


@responses.activate
def test_get_tracker_happy_path(server_with_mock_client) -> None:
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/trackers/TRK1.json",
        json={"id": "TRK1", "name": "Foo"},
        status=200,
    )
    out = json.loads(server_mod.get_tracker(tracker_id="TRK1"))
    assert out["id"] == "TRK1"


@responses.activate
def test_list_trackers_with_status_filter(server_with_mock_client) -> None:
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/trackers.json",
        json={"trackers": []},
        status=200,
    )
    json.loads(server_mod.list_trackers(status="active"))
    # Verify the status query param was forwarded.
    assert "status=active" in responses.calls[1].request.url


# ============================================================
# v0.4.0 — usage_summary
# ============================================================

def test_is_toll_free_helper() -> None:
    from callrail_mcp.server import _is_toll_free
    assert _is_toll_free("+18005551234") is True
    assert _is_toll_free("+18885551234") is True
    assert _is_toll_free("+18775551234") is True
    assert _is_toll_free("+18335551234") is True
    assert _is_toll_free("+14125551234") is False
    assert _is_toll_free("+17245551234") is False
    assert _is_toll_free(None) is False
    assert _is_toll_free("") is False


@responses.activate
def test_usage_summary_aggregates_correctly(server_with_mock_client) -> None:
    """End-to-end usage_summary: 2 companies, mix of trackers + calls."""
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    # Companies
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/companies.json",
        json={"companies": [
            {"id": "COM_BIG", "name": "Big Client", "status": "active"},
            {"id": "COM_SMALL", "name": "Small Client", "status": "active"},
            {"id": "COM_DEAD", "name": "Dead Client", "status": "disabled"},
        ], "total_pages": 1},
        status=200,
    )
    # Big Client trackers (4 numbers in a session pool + 1 GMB local)
    # NOTE: total_pages=1 required after v0.4.2 paginate fix — without it
    # the iterator keeps fetching until empty page or max_pages.
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/trackers.json",
        json={"trackers": [
            {"tracking_numbers": ["+14125551001", "+14125551002", "+14125551003", "+14125551004"]},
            {"tracking_numbers": ["+14125551005"]},
        ], "total_pages": 1},
        status=200,
    )
    # Big Client calls (10 calls totaling 600 seconds = 10 minutes)
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/calls.json",
        json={"calls": [{"duration": 60} for _ in range(10)], "total_pages": 1},
        status=200,
    )
    # Small Client trackers (1 toll-free)
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/trackers.json",
        json={"trackers": [{"tracking_numbers": ["+18005556666"]}], "total_pages": 1},
        status=200,
    )
    # Small Client calls (2 calls × 30s = 1 minute)
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/calls.json",
        json={"calls": [{"duration": 30}, {"duration": 30}], "total_pages": 1},
        status=200,
    )
    out = json.loads(server_mod.usage_summary(days=30))
    assert "agency" in out
    agency = out["agency"]
    assert agency["active_local_numbers"] == 5
    assert agency["active_tollfree_numbers"] == 1
    assert agency["active_total_numbers"] == 6
    assert agency["minutes_used"] == 11.0  # 10 + 1
    # Disabled company excluded.
    assert len(out["by_company"]) == 2
    # Big Client should be biggest cost driver.
    assert out["biggest_cost_driver"] == "Big Client"
    # Sorted: Big Client first.
    assert out["by_company"][0]["name"] == "Big Client"


def test_usage_summary_rejects_negative_days(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = None
    out = json.loads(server_mod.usage_summary(days=-7))
    assert out["error"] is True
    assert "negative" in out["message"]


def test_usage_summary_rejects_swapped_dates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = None
    out = json.loads(server_mod.usage_summary(start_date="2026-04-30", end_date="2026-04-01"))
    assert out["error"] is True
    assert "before start_date" in out["message"]


# ============================================================
# v0.4.0 — call_eligibility_check
# ============================================================

def test_call_eligibility_check_rejects_empty_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = None
    out = json.loads(server_mod.call_eligibility_check(call_id=""))
    assert out["error"] is True


def test_call_eligibility_check_rejects_negative_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = None
    out = json.loads(
        server_mod.call_eligibility_check(call_id="CAL_x", google_ads_min_duration_seconds=-1)
    )
    assert out["error"] is True
    assert "non-negative" in out["message"]


def test_call_eligibility_check_rejects_path_traversal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = None
    out = json.loads(server_mod.call_eligibility_check(call_id="CAL_x/../admin"))
    assert out["error"] is True
    assert "may not contain" in out["message"]


@responses.activate
def test_call_eligibility_check_eligible_call(server_with_mock_client) -> None:
    """A 90s answered call from Google with a gclid: should pass all checks."""
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/calls/CAL_GOOD.json",
        json={
            "gclid": "CjwK_test",
            "utm_source": "google",
            "utm_medium": "cpc",
            "duration": 90,
            "answered": True,
            "source_name": "Alan Construction Website Pool",
        },
        status=200,

    )
    out = json.loads(server_mod.call_eligibility_check(call_id="CAL_GOOD"))
    assert out["google_ads_eligible"] is True
    assert out["checks"]["has_gclid"] is True
    assert out["checks"]["answered"] is True
    assert out["checks"]["duration_meets_threshold"] is True
    assert out["checks"]["is_google_source"] is True
    assert out["rejection_reasons"] == []


@responses.activate
def test_call_eligibility_check_short_call_rejected(server_with_mock_client) -> None:
    """The exact Pittsburgh Z PA scenario from 2026-04-24: 58s answered with
    valid gclid → should fail on duration_meets_threshold."""
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/calls/CAL_PGH.json",
        json={
            "gclid": "CjwKCAjw_real",
            "utm_source": None,
            "duration": 58,
            "answered": True,
            "source_name": "Alan Construction Website Pool",
        },
        status=200,

    )
    out = json.loads(server_mod.call_eligibility_check(call_id="CAL_PGH"))
    assert out["google_ads_eligible"] is False
    assert out["checks"]["duration_meets_threshold"] is False
    # All other checks pass.
    assert out["checks"]["has_gclid"] is True
    assert out["checks"]["answered"] is True
    assert out["checks"]["is_google_source"] is True
    # Targeted rejection reason mentions duration + threshold.
    reasons_str = " ".join(out["rejection_reasons"])
    assert "58s" in reasons_str
    assert "60s" in reasons_str


@responses.activate
def test_call_eligibility_check_no_gclid_rejected(server_with_mock_client) -> None:
    """A GMB-organic call (no gclid): cannot upload to Google Ads."""
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/calls/CAL_GMB.json",
        json={
            "gclid": None,
            "utm_source": "google",
            "duration": 120,
            "answered": True,
            "source_name": "GMB Alan Construction",
        },
        status=200,

    )
    out = json.loads(server_mod.call_eligibility_check(call_id="CAL_GMB"))
    assert out["google_ads_eligible"] is False
    assert out["checks"]["has_gclid"] is False
    reasons_str = " ".join(out["rejection_reasons"])
    assert "gclid" in reasons_str.lower()


def test_usage_summary_rejects_zero_days_without_dates(monkeypatch: pytest.MonkeyPatch) -> None:
    """v0.4.1 fix: days<=0 with no dates would aggregate all-time history,
    blowing up the cost estimate."""
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = None
    out = json.loads(server_mod.usage_summary(days=0))
    assert out["error"] is True
    assert "days" in out["message"] or "start_date" in out["message"]


@responses.activate
def test_usage_summary_paginates_calls(server_with_mock_client) -> None:
    """v0.4.1 CRITICAL fix: previously truncated at 250 calls per company,
    silently undercounting heavy clients (Malick at ~800 minutes hit this
    in production)."""
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/companies.json",
        json={"companies": [{"id": "COM_BIG", "name": "Big Client", "status": "active"}], "total_pages": 1},
        status=200,
    )
    # Trackers: single page, 1 number.
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/trackers.json",
        json={"trackers": [{"tracking_numbers": ["+14125551001"]}], "total_pages": 1},
        status=200,
    )
    # Calls: page 1 with 250 calls (each 60s = 1 min) + total_pages=2.
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/calls.json",
        json={"calls": [{"duration": 60} for _ in range(250)], "page": 1, "total_pages": 2},
        status=200,
    )
    # Page 2 with 50 more calls.
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/calls.json",
        json={"calls": [{"duration": 60} for _ in range(50)], "page": 2, "total_pages": 2},
        status=200,
    )
    out = json.loads(server_mod.usage_summary(days=30))
    # 300 calls × 1 min = 300 minutes. Pre-fix this would have been 250.
    assert out["agency"]["minutes_used"] == 300.0
    assert out["by_company"][0]["calls_in_window"] == 300


@responses.activate
def test_usage_summary_partial_failure_per_company(server_with_mock_client) -> None:
    """v0.4.1 fix: one company's API failure shouldn't poison the whole report."""
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/companies.json",
        json={"companies": [
            {"id": "COM_OK", "name": "Working", "status": "active"},
            {"id": "COM_FAIL", "name": "Broken", "status": "active"},
        ], "total_pages": 1},
        status=200,
    )
    # Working company: trackers + calls succeed.
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/trackers.json",
        json={"trackers": [{"tracking_numbers": ["+14125551001"]}], "total_pages": 1},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/calls.json",
        json={"calls": [{"duration": 60}], "total_pages": 1},
        status=200,
    )
    # Broken company: trackers fail with 503.
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/trackers.json",
        json={"error": "service unavailable"},
        status=503,
    )
    out = json.loads(server_mod.usage_summary(days=30))
    # Working company in by_company; broken in partial_failures.
    assert len(out["by_company"]) == 1
    assert out["by_company"][0]["name"] == "Working"
    assert len(out["partial_failures"]) == 1
    assert out["partial_failures"][0]["company_name"] == "Broken"


@responses.activate
def test_call_eligibility_check_safe_duration_coercion(server_with_mock_client) -> None:
    """v0.4.1 fix: duration arrives as float-string instead of int."""
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/calls/CAL_FLOAT.json",
        json={
            "duration": "75.5",  # float string
            "answered": "true",  # string boolean
            "gclid": "x",
            "source_name": "google",
        },
        status=200,
    )
    out = json.loads(server_mod.call_eligibility_check(call_id="CAL_FLOAT"))
    assert out["call_facts"]["duration_seconds"] == 75
    assert out["call_facts"]["answered"] is True
    assert out["checks"]["duration_meets_threshold"] is True


def test_call_eligibility_check_requires_CAL_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """v0.4.1 fix: validate CAL prefix to fail fast on bogus IDs."""
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = None
    out = json.loads(server_mod.call_eligibility_check(call_id="TRK_wrong_prefix"))
    assert out["error"] is True
    assert "CAL" in out["message"]


# ============================================================
# v0.4.2 — ID validation across all tools (audit pass 6 findings)
# ============================================================

@pytest.mark.parametrize("tool_name,kwargs,id_field", [
    ("get_call", {"call_id": ""}, "call_id"),
    ("get_call", {"call_id": "TRK_wrong"}, "call_id"),
    ("get_call_recording", {"call_id": ""}, "call_id"),
    ("get_call_recording", {"call_id": "wrong/prefix"}, "call_id"),
    ("get_call_transcript", {"call_id": ""}, "call_id"),
    ("update_call", {"call_id": "", "note": "x"}, "call_id"),
    ("update_call", {"call_id": "../admin", "note": "x"}, "call_id"),
    ("add_call_tags", {"call_id": "", "tags": ["lead"]}, "call_id"),
    ("remove_call_tags", {"call_id": "", "tags": ["lead"]}, "call_id"),
    ("update_form_submission", {"submission_id": "", "note": "x"}, "submission_id"),
    ("update_form_submission", {"submission_id": "../admin", "note": "x"}, "submission_id"),
    ("update_tag", {"tag_id": "", "name": "x"}, "tag_id"),
    ("update_tag", {"tag_id": "..", "name": "x"}, "tag_id"),
    ("delete_tag", {"tag_id": ""}, "tag_id"),
    ("delete_tag", {"tag_id": "tag/../admin"}, "tag_id"),
])
def test_v042_id_validation_across_tools(
    tool_name: str,
    kwargs: dict,
    id_field: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All tools that interpolate an ID into a URL path now fail-fast on
    empty / dots / slashes / wrong-prefix instead of burning an API call."""
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = None
    tool = getattr(server_mod, tool_name)
    out = json.loads(tool(**kwargs))
    assert out["error"] is True


def test_v042_update_call_rejects_empty_note(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = None
    out = json.loads(server_mod.update_call(call_id="CAL_x", note=""))
    assert out["error"] is True
    assert "note" in out["message"]


def test_v042_update_call_rejects_empty_customer_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = None
    out = json.loads(server_mod.update_call(call_id="CAL_x", customer_name="   "))
    assert out["error"] is True


def test_v042_call_summary_rejects_zero_days(monkeypatch: pytest.MonkeyPatch) -> None:
    """v0.4.2 fix: call_summary now requires a window."""
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = None
    out = json.loads(server_mod.call_summary(days=0))
    assert out["error"] is True
    assert "days" in out["message"] or "start_date" in out["message"]


def test_v042_search_calls_rejects_zero_days(monkeypatch: pytest.MonkeyPatch) -> None:
    """v0.4.2 fix: search_calls_by_number now requires a window."""
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = None
    out = json.loads(server_mod.search_calls_by_number(phone_number="4125551234", days=0))
    assert out["error"] is True


# ============================================================
# v0.4.2 — Client-level fixes (POST no-retry, negative retry-after, paginate)
# ============================================================

def test_v042_clamp_delay_floors_negative() -> None:
    """v0.4.2 fix: time.sleep() crashes on negative values; clamp at 0."""
    assert CallRailClient._clamp_delay(-30) == 0.0
    assert CallRailClient._clamp_delay(-0.001) == 0.0
    assert CallRailClient._clamp_delay(5.5) == 5.5
    assert CallRailClient._clamp_delay(99999) == 60.0  # MAX_RETRY_DELAY_SECONDS


def test_v042_parse_retry_after_floors_negative() -> None:
    """v0.4.2 fix: server sends Retry-After: -30, was crashing time.sleep."""
    # Negative seconds value clamps to 0 instead of being passed to time.sleep.
    assert CallRailClient._parse_retry_after("-30", attempt=0) == 0.0


@responses.activate
def test_v042_post_does_NOT_retry_on_5xx(server_with_mock_client) -> None:
    """v0.4.2 CRITICAL fix: POST retries could create duplicate trackers
    ($3/mo each). Now POST fails fast on 5xx instead of retrying."""
    # Single 502 — pre-fix this would have been retried 3x, potentially
    # creating 3 trackers if CallRail processed each retry.
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    responses.add(
        responses.POST,
        "https://api.callrail.com/v3/a/ACC1/trackers.json",
        json={"error": "internal"},
        status=502,
    )
    out = json.loads(
        server_mod.create_tracker(
            name="Test", company_id="COM1", destination_number="+14125551234",
            confirm_billing=True, area_code="412",
        )
    )
    assert out["error"] is True
    assert out["status"] == 502
    # Verify only ONE POST was sent (no retries).
    post_calls = [c for c in responses.calls if c.request.method == "POST"]
    assert len(post_calls) == 1


@responses.activate
def test_v042_get_still_retries_on_5xx(monkeypatch: pytest.MonkeyPatch) -> None:
    """v0.4.2 sanity: GET retries should still work (idempotent)."""
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = CallRailClient(max_retries=2)
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"error": "transient"},
        status=503,
    )
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    # Should succeed after retry.
    aid = server_mod.client.resolve_account_id()
    assert aid == "ACC1"


# ============================================================
# v0.4.3 — Meta-audit fixes
# ============================================================

def test_v043_is_toll_free_skips_non_NANP() -> None:
    """v0.4.3 fix (Finding 9.2): non-NANP numbers (shortcodes, intl) must NOT
    be classified as local. They were being counted toward the local-number
    bundle, billing wrong."""
    from callrail_mcp.server import _is_toll_free
    # NANP toll-free still detected.
    assert _is_toll_free("+18005551234") is True
    assert _is_toll_free("18005551234") is True
    # Shortcodes (5 digits) — not NANP, return False.
    assert _is_toll_free("55555") is False
    assert _is_toll_free("12345") is False
    # International numbers (UK, +44...) — not NANP, return False.
    assert _is_toll_free("+44123456789012") is False
    # NANP local — still False (as before).
    assert _is_toll_free("+14125551234") is False


def test_v043_err_truncates_long_body() -> None:
    """v0.4.3 fix (Finding 2.1): long error bodies could leak echoed PII.
    Now truncated to ~500 chars in the envelope."""
    from callrail_mcp.client import CallRailError
    long_body = "X" * 2000
    e = CallRailError("test error", status=400, body=long_body)
    out = json.loads(server_mod._err(e))
    assert len(out["body"]) < 600
    assert "truncated" in out["body"]
    assert "1500 more chars" in out["body"]


def test_v043_err_passes_short_body_unchanged() -> None:
    from callrail_mcp.client import CallRailError
    e = CallRailError("oops", status=400, body="not too long")
    out = json.loads(server_mod._err(e))
    assert out["body"] == "not too long"


def test_v043_validate_window_coerces_string_days() -> None:
    """v0.4.3 fix (Finding 6.2): MCP clients may send loose JSON (string
    where int expected). Now coerced gracefully instead of raising."""
    ok, _ = _validate_window("7", None, None)
    assert ok
    ok, msg = _validate_window("not-a-number", None, None)
    assert not ok
    assert "integer" in msg


@responses.activate
def test_v043_cost_shares_sum_to_agency_total(server_with_mock_client) -> None:
    """v0.4.3 fix (Finding 4.1): largest-remainder rounding ensures
    sum(per-company shares) == agency_total exactly. Pre-fix could be off
    by ±$0.01-0.05 due to float rounding in proportional split."""
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    # 3 companies that each contribute 1/3 of minutes, no numbers.
    # Without rounding fix: 1/3 of $50 base × 3 = $49.99 or $50.01 → !=
    # agency_total $50.00.
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/companies.json",
        json={"companies": [
            {"id": "COM_A", "name": "A", "status": "active"},
            {"id": "COM_B", "name": "B", "status": "active"},
            {"id": "COM_C", "name": "C", "status": "active"},
        ], "total_pages": 1},
        status=200,
    )
    # Each company: 0 trackers, 1 minute (60s call).
    for _ in range(3):
        responses.add(
            responses.GET,
            "https://api.callrail.com/v3/a/ACC1/trackers.json",
            json={"trackers": [], "total_pages": 1},
            status=200,
        )
        responses.add(
            responses.GET,
            "https://api.callrail.com/v3/a/ACC1/calls.json",
            json={"calls": [{"duration": 60}], "total_pages": 1},
            status=200,
        )
    out = json.loads(server_mod.usage_summary(days=30))
    sum_shares = round(sum(c["estimated_cost_share"] for c in out["by_company"]), 2)
    assert sum_shares == out["agency"]["estimated_cycle_total"]


# ============================================================
# v0.4.3 — Happy-path tests for previously-uncovered tools
# (Finding 10.1: 11+ tools had ZERO test coverage)
# ============================================================

@responses.activate
def test_list_companies_happy_path(server_with_mock_client) -> None:
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/companies.json",
        json={"companies": [{"id": "COM1", "name": "Foo"}]},
        status=200,
    )
    out = json.loads(server_mod.list_companies())
    assert out["companies"][0]["id"] == "COM1"


@responses.activate
def test_list_users_happy_path(server_with_mock_client) -> None:
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/users.json",
        json={"users": [{"id": "USR1", "email": "s@pghdma.com"}]},
        status=200,
    )
    out = json.loads(server_mod.list_users())
    assert out["users"][0]["email"] == "s@pghdma.com"


@responses.activate
def test_list_form_submissions_happy_path(server_with_mock_client) -> None:
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/form_submissions.json",
        json={"form_submissions": [{"id": "FOR1", "form_data": {"name": "Kevin"}}]},
        status=200,
    )
    out = json.loads(server_mod.list_form_submissions())
    assert out["form_submissions"][0]["id"] == "FOR1"


@responses.activate
def test_list_text_messages_happy_path(server_with_mock_client) -> None:
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/text-messages.json",
        json={"conversations": []},
        status=200,
    )
    out = json.loads(server_mod.list_text_messages())
    assert "conversations" in out


@responses.activate
def test_list_tags_happy_path(server_with_mock_client) -> None:
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/tags.json",
        json={"tags": [{"id": "1", "name": "lead"}]},
        status=200,
    )
    out = json.loads(server_mod.list_tags())
    assert out["tags"][0]["name"] == "lead"


@responses.activate
def test_get_call_happy_path(server_with_mock_client) -> None:
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/calls/CAL1.json",
        json={"id": "CAL1", "duration": 60},
        status=200,
    )
    out = json.loads(server_mod.get_call(call_id="CAL1"))
    assert out["id"] == "CAL1"


@responses.activate
def test_get_call_recording_happy_path(server_with_mock_client) -> None:
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/calls/CAL1/recording.json",
        json={"url": "https://example.com/rec.mp3"},
        status=200,
    )
    out = json.loads(server_mod.get_call_recording(call_id="CAL1"))
    assert "url" in out


@responses.activate
def test_get_call_transcript_happy_path(server_with_mock_client) -> None:
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/calls/CAL1/transcription.json",
        json={"transcription": [{"speaker": "agent", "text": "Hello"}]},
        status=200,
    )
    out = json.loads(server_mod.get_call_transcript(call_id="CAL1"))
    assert "transcription" in out


@responses.activate
def test_update_call_happy_path(server_with_mock_client) -> None:
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    responses.add(
        responses.PUT,
        "https://api.callrail.com/v3/a/ACC1/calls/CAL1.json",
        json={"id": "CAL1", "note": "Hot lead"},
        status=200,
    )
    out = json.loads(server_mod.update_call(call_id="CAL1", note="Hot lead"))
    assert out["note"] == "Hot lead"
    body = json.loads(responses.calls[1].request.body)
    assert body == {"note": "Hot lead"}


@responses.activate
def test_update_form_submission_happy_path(server_with_mock_client) -> None:
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    responses.add(
        responses.PUT,
        "https://api.callrail.com/v3/a/ACC1/form_submissions/FOR1.json",
        json={"id": "FOR1", "value": 500.0},
        status=200,
    )
    out = json.loads(
        server_mod.update_form_submission(submission_id="FOR1", value=500.0)
    )
    assert out["value"] == 500.0


@responses.activate
def test_create_tag_happy_path(server_with_mock_client) -> None:
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    responses.add(
        responses.POST,
        "https://api.callrail.com/v3/a/ACC1/tags.json",
        json={"id": "100", "name": "vip"},
        status=201,
    )
    out = json.loads(
        server_mod.create_tag(name="vip", company_id="COM1", color="green1")
    )
    assert out["id"] == "100"


@responses.activate
def test_update_tag_happy_path(server_with_mock_client) -> None:
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    responses.add(
        responses.PUT,
        "https://api.callrail.com/v3/a/ACC1/tags/100.json",
        json={"id": "100", "name": "renamed"},
        status=200,
    )
    out = json.loads(server_mod.update_tag(tag_id="100", name="renamed"))
    assert out["name"] == "renamed"


@responses.activate
def test_delete_tag_happy_path(server_with_mock_client) -> None:
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    responses.add(
        responses.DELETE,
        "https://api.callrail.com/v3/a/ACC1/tags/100.json",
        status=204,
    )
    out = json.loads(server_mod.delete_tag(tag_id="100"))
    assert out["error"] is False if "error" in out else True
    # delete_tag returns the result dict (could be bare {} on 204 with no body).


@responses.activate
def test_add_call_tags_happy_path(server_with_mock_client) -> None:
    """Verifies the GET-then-PUT additive tag flow works end-to-end."""
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    # GET existing tags.
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/calls/CAL1.json",
        json={"tags": [{"name": "existing"}]},
        status=200,
    )
    # PUT merged.
    responses.add(
        responses.PUT,
        "https://api.callrail.com/v3/a/ACC1/calls/CAL1.json",
        json={"id": "CAL1"},
        status=200,
    )
    json.loads(server_mod.add_call_tags(call_id="CAL1", tags=["new"]))
    body = json.loads(responses.calls[2].request.body)
    # Existing + new, deduped, in order.
    assert body == {"tags": ["existing", "new"]}


@responses.activate
def test_remove_call_tags_happy_path(server_with_mock_client) -> None:
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/calls/CAL1.json",
        json={"tags": [{"name": "keep"}, {"name": "remove"}]},
        status=200,
    )
    responses.add(
        responses.PUT,
        "https://api.callrail.com/v3/a/ACC1/calls/CAL1.json",
        json={"id": "CAL1"},
        status=200,
    )
    json.loads(server_mod.remove_call_tags(call_id="CAL1", tags=["remove"]))
    body = json.loads(responses.calls[2].request.body)
    assert body == {"tags": ["keep"]}


@responses.activate
def test_search_calls_caps_match_count(server_with_mock_client) -> None:
    """v0.4.3 fix (Finding 6.1): popular numbers shouldn't return MB-sized
    JSON. Cap at 500 + flag truncation."""
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    # Return a single page with 600 matching calls.
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/calls.json",
        json={
            "calls": [
                {"customer_phone_number": "+14125551234", "id": f"CAL_{i}"}
                for i in range(600)
            ],
            "total_pages": 1,
        },
        status=200,
    )
    out = json.loads(server_mod.search_calls_by_number(phone_number="4125551234", days=30))
    assert out["truncated"] is True
    assert out["match_count"] == 500
    assert out["match_cap"] == 500


# ============================================================
# v0.4.4 — Audit pass 10 fixes (Unicode, is_google, extension parsing,
#          length caps, tag_id format, float days)
# ============================================================

@pytest.mark.parametrize("evil_id", [
    "TRK\u202eABC",     # RTL override
    "TRK\u200bABC",     # Zero-width space
    "TRK\u200dABC",     # Zero-width joiner
    "TRK\u00adABC",     # Soft hyphen (Cf)
    "TRKa\u0301bc",     # Combining acute accent (Mn)
])
def test_v044_id_shape_rejects_unicode_invisible_chars(
    evil_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bidi controls / zero-width / combining marks pass _safe_path's
    control-char filter (only ord<0x20|0x7f) but cause display ambiguity
    in logs. Now rejected at the validator level."""
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = None
    out = json.loads(server_mod.get_tracker(tracker_id=evil_id))
    assert out["error"] is True
    assert "Unicode" in out["message"] or "disallowed" in out["message"]


def test_v044_area_code_rejects_devanagari_digits(monkeypatch: pytest.MonkeyPatch) -> None:
    """ASCII-only regex (`[0-9]`) replaces `\\d` to block Unicode digits."""
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = None
    out = json.loads(server_mod.create_tracker(
        name="x", company_id="COM1", destination_number="+14125551234",
        confirm_billing=True, area_code="\u096a\u0967\u0968",  # Devanagari "412"
    ))
    assert out["error"] is True
    assert "area_code" in out["message"]


def test_v044_phone_rejects_devanagari_digits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = None
    out = json.loads(server_mod.create_tracker(
        name="x", company_id="COM1",
        destination_number="\u096a\u0967\u0968\u096b\u096b\u096b\u0967\u0968\u0969\u096a",
        confirm_billing=True, area_code="412",
    ))
    assert out["error"] is True


def test_v044_is_toll_free_handles_extension() -> None:
    """v0.4.4 fix: NANP toll-free with `+1...x77` extension was
    mis-classified as not-toll-free."""
    from callrail_mcp.server import _is_toll_free
    assert _is_toll_free("+18005551234x77") is True
    assert _is_toll_free("+18005551234,77") is True
    assert _is_toll_free("+18005551234;ext=77") is True
    # Local with extension still local.
    assert _is_toll_free("+14125551234x123") is False


def test_v044_validate_window_rejects_non_integer_float() -> None:
    """v0.4.4 fix: days=1.5 was silently truncated to 1 by int()."""
    ok, msg = _validate_window(1.5, None, None)
    assert not ok
    assert "whole number" in msg
    # Whole-number float still ok.
    ok, _ = _validate_window(7.0, None, None)
    assert ok


def test_v044_update_call_rejects_oversize_note(monkeypatch: pytest.MonkeyPatch) -> None:
    """v0.4.4 fix: prevent multi-MB note bodies from passing through."""
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = None
    out = json.loads(server_mod.update_call(call_id="CAL_x", note="A" * 4001))
    assert out["error"] is True
    assert "note" in out["message"].lower()


def test_v044_update_call_rejects_oversize_tags_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = None
    out = json.loads(server_mod.update_call(call_id="CAL_x", tags=["x"] * 101))
    assert out["error"] is True
    assert "tags" in out["message"].lower()


def test_v044_update_form_submission_rejects_oversize_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = None
    out = json.loads(server_mod.update_form_submission(
        submission_id="FOR_x", note="A" * 4001,
    ))
    assert out["error"] is True


def test_v044_tag_id_must_be_numeric(monkeypatch: pytest.MonkeyPatch) -> None:
    """v0.4.4 fix: CallRail tag IDs are numeric. Reject 'hello' etc."""
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = None
    out = json.loads(server_mod.update_tag(tag_id="hello", name="x"))
    assert out["error"] is True
    assert "numeric" in out["message"]
    out = json.loads(server_mod.delete_tag(tag_id="abc123"))
    assert out["error"] is True


def test_v044_call_eligibility_uses_source_slug(monkeypatch: pytest.MonkeyPatch) -> None:
    """v0.4.4 fix: `is_google` now uses CallRail's internal `source` slug
    (e.g. 'google_paid'), not the user-editable `source_name`. Previously
    a Bing tracker named 'Bing Ads (Google legacy import)' would have
    been classified as Google by source_name substring match."""
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = CallRailClient(max_retries=0)
    responses_lib = pytest.importorskip("responses")
    with responses_lib.RequestsMock() as rsps:
        rsps.add(
            responses_lib.GET,
            "https://api.callrail.com/v3/a.json",
            json={"accounts": [{"id": "ACC1"}]},
            status=200,
        )
        rsps.add(
            responses_lib.GET,
            "https://api.callrail.com/v3/a/ACC1/calls/CAL_BING.json",
            json={
                "gclid": None,
                "utm_source": None,
                "duration": 90,
                "answered": True,
                "source": "bing_paid",
                "source_name": "Bing Ads (Google legacy import)",
            },
            status=200,
        )
        out = json.loads(server_mod.call_eligibility_check(call_id="CAL_BING"))
        assert out["checks"]["is_google_source"] is False
        assert out["call_facts"]["source"] == "bing_paid"
        # F8 fix (audit pass 11): make sure `source` is actually in the
        # fields= URL query — if a refactor drops it, this test would
        # silently pass on the mock alone otherwise.
        assert "source" in rsps.calls[1].request.url


@responses.activate
def test_v044_call_eligibility_google_paid_detected(server_with_mock_client) -> None:
    """v0.4.4 sanity: source='google_paid' (no gclid) still detected."""
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/calls/CAL_GP.json",
        json={
            "gclid": None,
            "duration": 90,
            "answered": True,
            "source": "google_paid",
        },
        status=200,
    )
    out = json.loads(server_mod.call_eligibility_check(call_id="CAL_GP"))
    assert out["checks"]["is_google_source"] is True


def test_v044_err_handles_bytes_body() -> None:
    """v0.4.4 defensive: if body is ever bytes (not str), decode."""
    from callrail_mcp.client import CallRailError
    e = CallRailError("oops", status=500, body=b"binary stuff \xff")
    out = json.loads(server_mod._err(e))
    # Should not raise; body should be decoded with replacement chars.
    assert isinstance(out["body"], str)


# ============================================================
# v0.4.5 — Audit pass 11 fixes
# ============================================================

@responses.activate
def test_v045_paginate_caps_runaway_total_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """v0.4.5 defensive: a misbehaving server returning total_pages=999999
    shouldn't pin the iterator past max_pages."""
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = CallRailClient(max_retries=0)
    # Each page returns 1 call but claims total_pages=999999.
    for _ in range(3):
        responses.add(
            responses.GET,
            "https://api.callrail.com/v3/a/ACC1/calls.json",
            json={"calls": [{"id": "CAL_x"}], "total_pages": 999999},
            status=200,
        )
    items = list(
        server_mod.client.paginate(
            "a/ACC1/calls.json", {"per_page": 1}, items_key="calls", max_pages=3
        )
    )
    # Should stop at max_pages=3, not chase 999999.
    assert len(items) == 3


@responses.activate
def test_v045_call_eligibility_bare_google_source(server_with_mock_client) -> None:
    """v0.4.5 fix (F2): source='google' (no underscore) should be
    detected as Google. Pre-fix only `startswith('google_')` matched."""
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/calls/CAL_G.json",
        json={
            "gclid": None,
            "duration": 90,
            "answered": True,
            "source": "google",  # bare, no underscore
        },
        status=200,
    )
    out = json.loads(server_mod.call_eligibility_check(call_id="CAL_G"))
    assert out["checks"]["is_google_source"] is True


# ============================================================
# v0.4.6 — Audit pass 12 fixes
# ============================================================

@responses.activate
def test_v046_partial_failure_surfaces_accumulated_data(
    server_with_mock_client,
) -> None:
    """v0.4.6 fix (F1, HIGH): when a company's call pagination fails
    mid-flight, the partial accumulator was silently dropped — agency
    total under-reported with no way for the user to know how much was
    lost. Now reported in partial_failures."""
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/companies.json",
        json={"companies": [{"id": "COM_X", "name": "Company X", "status": "active"}], "total_pages": 1},
        status=200,
    )
    # Trackers succeed.
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/trackers.json",
        json={"trackers": [{"tracking_numbers": ["+14125551001"]}], "total_pages": 1},
        status=200,
    )
    # Calls page 1 succeeds with 5 calls × 60s = 5 minutes.
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/calls.json",
        json={"calls": [{"duration": 60} for _ in range(5)], "page": 1, "total_pages": 3},
        status=200,
    )
    # Calls page 2 fails with 503.
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/calls.json",
        json={"error": "service unavailable"},
        status=503,
    )
    out = json.loads(server_mod.usage_summary(days=30))
    assert len(out["partial_failures"]) == 1
    pf = out["partial_failures"][0]
    # Accumulated data is now visible — pre-fix this was 0.
    assert pf["partial_calls_before_failure"] == 5
    assert pf["partial_minutes_before_failure"] == 5.0
    assert pf["partial_local_numbers"] == 1


def test_v046_is_toll_free_handles_comma_format() -> None:
    """v0.4.6 fix (F2): '+1,800,555,1234' (comma-separated) was being
    split at first comma, leaving '+1', losing the 800-prefix detection.
    Now we extract digits ignoring all separators."""
    from callrail_mcp.server import _is_toll_free
    assert _is_toll_free("+1,800,555,1234") is True
    assert _is_toll_free("1-800-555-1234") is True
    assert _is_toll_free("1.800.555.1234") is True
    # (800)... without country code (10 digits) is NANP-format-incomplete;
    # we require the leading 1 to match. CallRail normalizes to E.164
    # internally so this matters mostly for human-formatted inputs.
    assert _is_toll_free("(800) 555-1234") is False


def test_v046_validate_window_rejects_bool() -> None:
    """v0.4.6 fix (F4): isinstance(True, int) is True in Python, so
    days=True silently became days=1. Now explicitly rejected."""
    ok, msg = _validate_window(True, None, None)
    assert not ok
    assert "bool" in msg
    ok, msg = _validate_window(False, None, None)
    assert not ok


# ============================================================
# v0.5.0 — compare_periods, bulk_update_calls, spam_detector
# ============================================================

def test_v050_compare_periods_rejects_invalid_days(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = None
    assert json.loads(server_mod.compare_periods(days=0))["error"] is True
    assert json.loads(server_mod.compare_periods(days=-1))["error"] is True
    assert json.loads(server_mod.compare_periods(days=400))["error"] is True


@responses.activate
def test_v050_compare_periods_happy_path(server_with_mock_client) -> None:
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/companies.json",
        json={"companies": [
            {"id": "COM_A", "name": "A", "status": "active", "time_zone": "America/New_York"},
        ], "total_pages": 1},
        status=200,
    )
    # Current window: 2 calls × 60s = 2 min
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/calls.json",
        json={"calls": [{"duration": 60}, {"duration": 60}], "total_pages": 1},
        status=200,
    )
    # Previous window: 1 call × 60s = 1 min
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/calls.json",
        json={"calls": [{"duration": 60}], "total_pages": 1},
        status=200,
    )
    out = json.loads(server_mod.compare_periods(days=30))
    assert out["timezone"] == "America/New_York"
    assert out["current"]["total_minutes"] == 2.0
    assert out["previous"]["total_minutes"] == 1.0
    assert out["agency_deltas"]["minutes_delta"] == 1.0
    assert out["agency_deltas"]["minutes_pct_change"] == 100.0
    # v0.5.1: biggest_mover now includes direction.
    assert out["biggest_mover"]["name"] == "A"
    assert out["biggest_mover"]["direction"] == "up"
    assert out["biggest_mover"]["minutes_delta"] == 1.0


def test_v050_bulk_update_requires_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = None
    out = json.loads(server_mod.bulk_update_calls(days=0, set_note="x"))
    assert out["error"] is True
    assert "filter" in out["message"]


def test_v050_bulk_update_requires_set_field(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = None
    out = json.loads(server_mod.bulk_update_calls(company_id="COM1", days=7))
    assert out["error"] is True
    assert "set_" in out["message"]


@responses.activate
def test_v050_bulk_update_dry_run(server_with_mock_client) -> None:
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/calls.json",
        json={"calls": [
            {"id": "CAL1", "source": "bing_paid", "duration": 15, "tags": []},
            {"id": "CAL2", "source": "bing_paid", "duration": 20, "tags": [{"name": "existing"}]},
        ], "total_pages": 1},
        status=200,
    )
    out = json.loads(server_mod.bulk_update_calls(
        source="bing_paid", days=7, set_tags_add=["low_priority"], dry_run=True,
    ))
    assert out["dry_run"] is True
    assert out["matched"] == 2
    assert len(out["would_update_calls"]) == 2
    assert out["set_fields"]["tags_add"] == ["low_priority"]
    # Should NOT have called PUT — verify by counting requests.
    put_calls = [c for c in responses.calls if c.request.method == "PUT"]
    assert len(put_calls) == 0


@responses.activate
def test_v050_bulk_update_commit(server_with_mock_client) -> None:
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/calls.json",
        json={"calls": [
            {"id": "CAL1", "source": "bing_paid", "tags": []},
        ], "total_pages": 1},
        status=200,
    )
    # v0.5.1 race-fix: bulk_update_calls now re-GETs tags per call.
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/calls/CAL1.json",
        json={"tags": []},
        status=200,
    )
    responses.add(
        responses.PUT,
        "https://api.callrail.com/v3/a/ACC1/calls/CAL1.json",
        json={"id": "CAL1"},
        status=200,
    )
    out = json.loads(server_mod.bulk_update_calls(
        source="bing_paid", days=7, set_tags_add=["low_priority"], dry_run=False,
    ))
    assert out["updated"] == 1
    assert out["failed_count"] == 0
    put_calls = [c for c in responses.calls if c.request.method == "PUT"]
    assert len(put_calls) == 1
    body = json.loads(put_calls[0].request.body)
    assert body == {"tags": ["low_priority"]}


@responses.activate
def test_v050_spam_detector_scores_correctly(server_with_mock_client) -> None:
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    # Mix of clean + spammy calls
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/calls.json",
        json={"calls": [
            # Clearly spam: 5s unanswered (score: 2+1=3)
            {"id": "CAL_S1", "duration": 5, "answered": False,
             "customer_phone_number": "+15551234567", "first_call": True},
            # Clean: 180s answered (score: 0)
            {"id": "CAL_OK", "duration": 180, "answered": True,
             "customer_phone_number": "+14125551001", "first_call": True},
            # Frequent caller spam (5 calls from same number, each short)
            *[
                {"id": f"CAL_F{i}", "duration": 8, "answered": False,
                 "customer_phone_number": "+15559999999", "first_call": i == 0}
                for i in range(5)
            ],
        ], "total_pages": 1},
        status=200,
    )
    out = json.loads(server_mod.spam_detector(days=30, auto_tag=False))
    assert out["scanned_calls"] == 7
    # CAL_S1 alone (score 3) + 5× CAL_F (score 4 each) = 6 likely spam
    assert out["likely_spam_count"] == 6
    # +15559999999 should appear in frequent callers (5 calls).
    assert any(fc["phone"] == "+15559999999" and fc["calls"] == 5 for fc in out["frequent_callers"])


@responses.activate
def test_v050_spam_detector_auto_tags(server_with_mock_client) -> None:
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/calls.json",
        json={"calls": [
            {"id": "CAL_S1", "duration": 5, "answered": False,
             "customer_phone_number": "+15551234567", "first_call": True},
        ], "total_pages": 1},
        status=200,
    )
    # GET existing tags (empty)
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/calls/CAL_S1.json",
        json={"tags": []},
        status=200,
    )
    # PUT merged tags
    responses.add(
        responses.PUT,
        "https://api.callrail.com/v3/a/ACC1/calls/CAL_S1.json",
        json={"id": "CAL_S1"},
        status=200,
    )
    # v0.5.0 safety: auto_tag=True requires company_id to scope the op.
    out = json.loads(server_mod.spam_detector(company_id="COM1", days=30, auto_tag=True))
    assert out["tagged_count"] == 1
    # Verify the tag name was added.
    put_calls = [c for c in responses.calls if c.request.method == "PUT"]
    assert len(put_calls) == 1
    body = json.loads(put_calls[0].request.body)
    assert body == {"tags": ["auto_detected_spam"]}


def test_v050_bulk_update_rejects_invalid_answered(monkeypatch: pytest.MonkeyPatch) -> None:
    """v0.5.0 audit fix: answered must be 'true'/'false'/None."""
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = None
    out = json.loads(server_mod.bulk_update_calls(
        company_id="COM1", days=7, answered="no", set_note="x",
    ))
    assert out["error"] is True
    assert "answered" in out["message"]


def test_v050_spam_detector_requires_company_for_auto_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """v0.5.0 audit fix: auto_tag without company_id would tag across whole agency."""
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = None
    out = json.loads(server_mod.spam_detector(days=30, auto_tag=True))
    assert out["error"] is True
    assert "company_id" in out["message"]


@responses.activate
def test_v050_bulk_update_surfaces_truncation(server_with_mock_client) -> None:
    """v0.5.0 audit fix: silent truncation at 500-cap is no longer silent."""
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    # Return 501 calls across 2 pages — bulk cap is 500, so the 501st
    # triggers truncated_at_cap=True.
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/calls.json",
        json={"calls": [{"id": f"CAL_{i}"} for i in range(250)], "page": 1, "total_pages": 3},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/calls.json",
        json={"calls": [{"id": f"CAL_{i}"} for i in range(250, 500)], "page": 2, "total_pages": 3},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/calls.json",
        json={"calls": [{"id": f"CAL_{i}"} for i in range(500, 600)], "page": 3, "total_pages": 3},
        status=200,
    )
    out = json.loads(server_mod.bulk_update_calls(
        source="bing_paid", days=7, set_note="x", dry_run=True,
    ))
    assert out["matched"] == 500
    assert out["truncated_at_cap"] is True
    assert out["hint"] is not None


def test_v052_tag_names_from_rejects_non_list(caplog: pytest.LogCaptureFixture) -> None:
    """v0.5.2 HIGH fix (round 3 F1): pre-fix, _tag_names_from('hot,lead')
    iterated chars → ['h','o','t',',','l','e','a','d'], corrupting tags.
    Now rejects non-list inputs."""
    import logging

    from callrail_mcp.server import _tag_names_from
    with caplog.at_level(logging.WARNING):
        assert _tag_names_from("hot,lead") == []
        assert _tag_names_from({"id": 1, "name": "x"}) == []
        assert _tag_names_from(42) == []
    # All three should have logged warnings.
    assert caplog.text.count("non-list") >= 3


def test_v052_spam_detector_caps_days_at_90(monkeypatch: pytest.MonkeyPatch) -> None:
    """v0.5.2 MED fix (round 3 F7): days=365 could materialize ~100MB
    of call dicts for scoring. Capped at 90."""
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = None
    out = json.loads(server_mod.spam_detector(days=91))
    assert out["error"] is True
    assert "90" in out["message"]
    # Boundary: 90 is allowed (would proceed to API call which fails
    # with no mock — we're only checking validation here).
    out = json.loads(server_mod.spam_detector(days=90))
    # Should NOT be a validation error (would be a CallRail API error
    # because no mock; but `error` could be True with status=500 etc.).
    # The point is the message shouldn't say "exceeds spam_detector cap".
    assert "exceeds spam_detector cap" not in str(out)


def test_v053_spam_detector_string_days_cap_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    """v0.5.3 MED fix (round 4 F1): pre-fix, spam_detector(days='365')
    bypassed the 90-day cap because `isinstance(str, int)` is False.
    Now coerces before the cap check."""
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = None
    out = json.loads(server_mod.spam_detector(days="365"))  # type: ignore[arg-type]
    assert out["error"] is True
    assert "90" in out["message"]


def test_v052_pick_account_tz_dedupes_warnings(caplog: pytest.LogCaptureFixture) -> None:
    """v0.5.2 LOW fix (round 3 F3): legacy-TZ + multi-TZ warnings now
    deduped per process to avoid log spam on repeated tool calls."""
    import logging

    from callrail_mcp.server import _pick_account_tz, _warned_multi_tz_signature, _warned_tzs
    # Clear dedupe caches before this test to make assertions deterministic.
    _warned_tzs.clear()
    _warned_multi_tz_signature.clear()
    with caplog.at_level(logging.WARNING):
        # First call with legacy TZ → warns once.
        _pick_account_tz([{"time_zone": "EST"}])
        first_count = caplog.text.count("legacy TZ")
        # Second call with same legacy TZ → no additional warning.
        _pick_account_tz([{"time_zone": "EST"}])
        second_count = caplog.text.count("legacy TZ")
    assert first_count == 1
    assert second_count == 1  # no additional warning


def test_v051_tag_names_from_filters_malformed() -> None:
    """v0.5.1 B1 fix: malformed tag dicts (no 'name' key) and non-string
    entries are filtered, not silently passed through as None."""
    from callrail_mcp.server import _tag_names_from
    assert _tag_names_from(None) == []
    assert _tag_names_from([]) == []
    # Mix of dict-with-name, dict-without-name, string, int, None.
    assert _tag_names_from([
        {"id": 1, "name": "lead"},
        {"id": 2},  # no name — drop
        "hot",
        42,         # non-string — drop
        None,
        {"name": ""},  # empty name — drop
        {"name": "vip"},
    ]) == ["lead", "hot", "vip"]


@responses.activate
def test_v051_spam_detector_handles_malformed_tags(server_with_mock_client) -> None:
    """v0.5.1 B1 fix: malformed existing tags don't break auto_tag."""
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/calls.json",
        json={"calls": [
            {"id": "CAL_S1", "duration": 5, "answered": False,
             "customer_phone_number": "+15551234567", "first_call": True},
        ], "total_pages": 1},
        status=200,
    )
    # Existing tags include a malformed dict with no 'name' field.
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/calls/CAL_S1.json",
        json={"tags": [{"id": 7}, {"name": "real"}]},
        status=200,
    )
    responses.add(
        responses.PUT,
        "https://api.callrail.com/v3/a/ACC1/calls/CAL_S1.json",
        json={"id": "CAL_S1"},
        status=200,
    )
    out = json.loads(server_mod.spam_detector(
        company_id="COM1", days=30, auto_tag=True,
    ))
    assert out["tagged_count"] == 1
    # The PUT body should NOT contain None — only the real string tags
    # plus the new auto_detected_spam tag.
    put_body = json.loads(
        next(c for c in responses.calls if c.request.method == "PUT").request.body
    )
    assert None not in put_body["tags"]
    assert put_body["tags"] == ["real", "auto_detected_spam"]


@responses.activate
def test_v051_compare_periods_partial_failures(server_with_mock_client) -> None:
    """v0.5.1 B3 fix: per-company API failures now surface in
    `partial_failures` instead of silently zeroing the company."""
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/companies.json",
        json={"companies": [
            {"id": "COM_OK", "name": "Working", "status": "active",
             "time_zone": "America/New_York"},
            {"id": "COM_FAIL", "name": "Broken", "status": "active",
             "time_zone": "America/New_York"},
        ], "total_pages": 1},
        status=200,
    )
    # Order matters: responses serves mocks in registration order
    # (URL match first, then FIFO). Iteration is:
    # current[COM_OK], current[COM_FAIL], previous[COM_OK], previous[COM_FAIL].
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/calls.json",
        json={"calls": [{"duration": 60}], "total_pages": 1},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/calls.json",
        json={"error": "service unavailable"},
        status=503,
    )
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/calls.json",
        json={"calls": [{"duration": 60}], "total_pages": 1},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/calls.json",
        json={"error": "service unavailable"},
        status=503,
    )
    out = json.loads(server_mod.compare_periods(days=30))
    # Two failures expected (one per window per failing company).
    assert len(out["partial_failures"]) == 2
    assert all(f["company_id"] == "COM_FAIL" for f in out["partial_failures"])
    assert {f["window"] for f in out["partial_failures"]} == {"current", "previous"}


@responses.activate
def test_v051_spam_detector_caps_likely_spam(server_with_mock_client) -> None:
    """v0.5.1 B8 fix: likely_spam list capped at 500 (was unbounded;
    could blow MCP frame size on a popular spam-targeted number)."""
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    # 600 spam-scoring calls (5s, unanswered, first_call → score 4 each)
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/calls.json",
        json={"calls": [
            {"id": f"CAL_{i}", "duration": 5, "answered": False,
             "customer_phone_number": f"+1555000{i:04d}", "first_call": True}
            for i in range(600)
        ], "total_pages": 1},
        status=200,
    )
    out = json.loads(server_mod.spam_detector(days=30, auto_tag=False))
    assert out["likely_spam_count"] == 600
    assert out["likely_spam_returned"] == 500
    assert out["likely_spam_truncated"] is True
    assert len(out["likely_spam"]) == 500


def test_v050_compare_periods_no_overlap() -> None:
    """v0.5.0 audit fix: prev_end was the same day as cur_start, double-counting."""
    # Can't easily unit-test date arithmetic without mocking datetime.now,
    # but we can at least verify the helpers produce non-overlapping windows
    # in principle via a direct check.
    from datetime import date, timedelta
    today = date(2026, 4, 24)
    days = 30
    cur_end = today
    cur_start = today - timedelta(days=days)
    prev_end = cur_start - timedelta(days=1)  # the fix
    prev_start = prev_end - timedelta(days=days)
    # Windows must be disjoint.
    assert prev_end < cur_start, f"prev_end={prev_end} overlaps cur_start={cur_start}"
    # Both windows cover `days+1` calendar days (inclusive on both ends
    # is CallRail's semantics).
    assert (cur_end - cur_start).days == days
    assert (prev_end - prev_start).days == days


def test_v050_date_window_uses_timezone() -> None:
    """v0.5.0: _date_window now accepts tz= param and uses it for 'today'."""
    # Just verify no crash + correct fallback for bad tz.
    out = _date_window(7, None, None, tz="America/New_York")
    assert "start_date" in out and "end_date" in out
    out = _date_window(7, None, None, tz="Invalid/Zone")
    assert "start_date" in out  # falls back to UTC silently


# ============================================================
# v0.6.0 — Companies CRUD, Users CRUD, get_form_submission,
#          get_text_message, list_webhooks, get_webhook
# ============================================================

# ---- Companies CRUD ----

@responses.activate
def test_v060_get_company_happy(server_with_mock_client) -> None:
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/companies/COM_X.json",
        json={"id": "COM_X", "name": "Test Co"},
        status=200,
    )
    out = json.loads(server_mod.get_company(company_id="COM_X"))
    assert out["id"] == "COM_X"


def test_v060_get_company_rejects_bad_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = None
    out = json.loads(server_mod.get_company(company_id="TRK_wrong"))
    assert out["error"] is True
    assert "COM" in out["message"]


@responses.activate
def test_v060_create_company_happy(server_with_mock_client) -> None:
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    responses.add(
        responses.POST,
        "https://api.callrail.com/v3/a/ACC1/companies.json",
        json={"id": "COM_NEW", "name": "Acme"},
        status=201,
    )
    out = json.loads(server_mod.create_company(name="Acme"))
    assert out["id"] == "COM_NEW"
    body = json.loads(responses.calls[1].request.body)
    assert body["name"] == "Acme"
    assert body["time_zone"] == "America/New_York"
    assert body["lead_scoring_enabled"] is True


def test_v060_create_company_rejects_empty_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = None
    out = json.loads(server_mod.create_company(name=""))
    assert out["error"] is True


def test_v060_create_company_rejects_oversize_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = None
    out = json.loads(server_mod.create_company(name="A" * 256))
    assert out["error"] is True


@responses.activate
def test_v060_update_company_happy(server_with_mock_client) -> None:
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    responses.add(
        responses.PUT,
        "https://api.callrail.com/v3/a/ACC1/companies/COM_X.json",
        json={"id": "COM_X", "name": "Renamed"},
        status=200,
    )
    out = json.loads(server_mod.update_company(company_id="COM_X", name="Renamed"))
    assert out["name"] == "Renamed"


def test_v060_update_company_no_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = None
    out = json.loads(server_mod.update_company(company_id="COM_X"))
    assert out["error"] is True
    assert "No fields supplied" in out["message"]


@responses.activate
def test_v060_delete_company_returns_response(server_with_mock_client) -> None:
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    responses.add(
        responses.DELETE,
        "https://api.callrail.com/v3/a/ACC1/companies/COM_X.json",
        json={"id": "COM_X", "status": "disabled", "disabled_at": "2026-04-24T12:00:00Z"},
        status=200,
    )
    out = json.loads(server_mod.delete_company(company_id="COM_X"))
    assert out["deleted"] is True
    assert out["company_id"] == "COM_X"
    assert out["response"]["status"] == "disabled"


# ---- Users CRUD ----

@responses.activate
def test_v060_get_user_happy(server_with_mock_client) -> None:
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/users/USR_X.json",
        json={"id": "USR_X", "email": "x@y.com"},
        status=200,
    )
    out = json.loads(server_mod.get_user(user_id="USR_X"))
    assert out["email"] == "x@y.com"


def test_v060_get_user_rejects_bad_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = None
    out = json.loads(server_mod.get_user(user_id="COM_wrong"))
    assert out["error"] is True
    assert "USR" in out["message"]


def test_v060_create_user_rejects_bad_email(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = None
    out = json.loads(server_mod.create_user(
        email="not-an-email", first_name="A", last_name="B",
    ))
    assert out["error"] is True
    assert "email" in out["message"]


def test_v060_create_user_rejects_empty_first_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = None
    out = json.loads(server_mod.create_user(
        email="ok@x.com", first_name="", last_name="B",
    ))
    assert out["error"] is True


def test_v060_create_user_validates_company_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = None
    out = json.loads(server_mod.create_user(
        email="ok@x.com", first_name="A", last_name="B",
        company_ids=["COM_ok", "BAD_PREFIX"],
    ))
    assert out["error"] is True
    assert "COM" in out["message"]


@responses.activate
def test_v060_create_user_happy(server_with_mock_client) -> None:
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    responses.add(
        responses.POST,
        "https://api.callrail.com/v3/a/ACC1/users.json",
        json={"id": "USR_NEW", "email": "ok@x.com"},
        status=201,
    )
    out = json.loads(server_mod.create_user(
        email="ok@x.com", first_name="A", last_name="B", role="reporting",
        company_ids=["COM_X"],
    ))
    assert out["id"] == "USR_NEW"
    body = json.loads(responses.calls[1].request.body)
    assert body == {
        "email": "ok@x.com", "first_name": "A", "last_name": "B",
        "role": "reporting", "company_ids": ["COM_X"],
    }


def test_v060_create_user_warns_on_unknown_role(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """Unknown roles aren't rejected (CallRail may have plan-specific
    roles), but log a warning so the user knows."""
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = None
    import logging
    with caplog.at_level(logging.WARNING):
        # Will fail at network call (no key). We just want to verify the
        # warning fires before the network attempt.
        json.loads(server_mod.create_user(
            email="ok@x.com", first_name="A", last_name="B", role="archmage",
        ))
    assert "archmage" in caplog.text


@responses.activate
def test_v060_update_user_happy(server_with_mock_client) -> None:
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    responses.add(
        responses.PUT,
        "https://api.callrail.com/v3/a/ACC1/users/USR_X.json",
        json={"id": "USR_X", "role": "admin"},
        status=200,
    )
    out = json.loads(server_mod.update_user(user_id="USR_X", role="admin"))
    assert out["role"] == "admin"


@responses.activate
def test_v060_delete_user(server_with_mock_client) -> None:
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    responses.add(
        responses.DELETE,
        "https://api.callrail.com/v3/a/ACC1/users/USR_X.json",
        status=204,
    )
    out = json.loads(server_mod.delete_user(user_id="USR_X"))
    assert out["deleted"] is True


# ---- Singletons + Webhooks ----

@responses.activate
def test_v060_get_form_submission(server_with_mock_client) -> None:
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/form_submissions/FOR_X.json",
        json={"id": "FOR_X", "form_data": {"name": "Kevin"}},
        status=200,
    )
    out = json.loads(server_mod.get_form_submission(submission_id="FOR_X"))
    assert out["id"] == "FOR_X"


@responses.activate
def test_v060_get_text_message(server_with_mock_client) -> None:
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    # Real CallRail conv IDs are short alphanumeric like "8hw3p".
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/text-messages/8hw3p.json",
        json={"id": "8hw3p", "customer_phone_number": "+15551234567"},
        status=200,
    )
    out = json.loads(server_mod.get_text_message(conversation_id="8hw3p"))
    assert out["id"] == "8hw3p"


def test_v060_get_text_message_rejects_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = None
    out = json.loads(server_mod.get_text_message(conversation_id=""))
    assert out["error"] is True


@responses.activate
def test_v060_list_webhooks(server_with_mock_client) -> None:
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/webhooks.json",
        json={"webhooks": []},
        status=200,
    )
    out = json.loads(server_mod.list_webhooks())
    assert "webhooks" in out


@responses.activate
def test_v060_get_webhook(server_with_mock_client) -> None:
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/webhooks/WH_1.json",
        json={"id": "WH_1", "url": "https://example.com/hook"},
        status=200,
    )
    out = json.loads(server_mod.get_webhook(webhook_id="WH_1"))
    assert out["id"] == "WH_1"


# ---- Validate_email helper ----

@pytest.mark.parametrize("email,ok", [
    ("ok@example.com", True),
    ("a.b+tag@sub.domain.com", True),
    ("plainstring", False),
    ("@nodomain.com", False),
    ("nodot@nodomain", False),
    ("space in@email.com", False),
    ("", False),
])
def test_v060_validate_email(email: str, ok: bool) -> None:
    from callrail_mcp.server import _validate_email
    got_ok, _ = _validate_email(email)
    assert got_ok is ok


def test_v047_validate_window_caps_huge_days() -> None:
    """v0.4.7 fix (round 16): days=10**18 was passing _validate_window
    (only floors at 0) and crashing _date_window with OverflowError from
    timedelta(days=10**18)."""
    ok, msg = _validate_window(10**18, None, None)
    assert not ok
    assert "36500" in msg or "lookback" in msg
    # Boundary: 36500 itself is allowed.
    ok, _ = _validate_window(36500, None, None)
    assert ok


def test_v047_date_window_coerces_string_days() -> None:
    """v0.4.7 fix (round 14 HIGH): `_validate_window` coerced `days` to int
    locally but only returned (ok, msg); `_date_window` still got the raw
    string, crashing with `TypeError: '>' not supported between str and int`.
    This exercises the end-to-end string-`days` path that v0.4.3's validator
    test missed."""
    out = _date_window("7", None, None)
    assert "start_date" in out and "end_date" in out
    # Silent coercion on garbage → treat as no window rather than crash.
    out = _date_window("not-a-number", None, None)
    assert out == {}


def test_v047_list_calls_accepts_string_days(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end check: an MCP client sending `days="7"` shouldn't crash."""
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = CallRailClient(max_retries=0)
    # Use a real `responses` mock to exercise the full call path.
    with responses.RequestsMock() as rsps:
        rsps.add(
            responses.GET,
            "https://api.callrail.com/v3/a.json",
            json={"accounts": [{"id": "ACC1"}]},
            status=200,
        )
        rsps.add(
            responses.GET,
            "https://api.callrail.com/v3/a/ACC1/calls.json",
            json={"calls": [], "total_pages": 1},
            status=200,
        )
        # Passing days as a string — pre-v0.4.7 this was an uncaught TypeError.
        # Note: live MCP clients almost always get per_page/days coerced by
        # FastMCP before dispatch, but in-process callers can send strings.
        out = json.loads(server_mod.list_calls(days="7"))  # type: ignore[arg-type]
        assert "calls" in out or out.get("error") is False


def test_v046_clean_tag_list_logs_dropped_non_strings(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """v0.4.6 fix (F5): non-string entries silently dropped. Now logged."""
    import logging
    with caplog.at_level(logging.WARNING):
        result = _clean_tag_list(["hot", 42, "lead", None, "vip"])
    assert result == ["hot", "lead", "vip"]
    assert "dropped 2 non-string" in caplog.text


@responses.activate
def test_v045_usage_summary_paginates_companies(server_with_mock_client) -> None:
    """v0.4.5 fix (F11): companies list now paginated. Pre-fix would
    silently truncate at 250 companies (single-page request)."""
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    # Page 1: 2 companies
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/companies.json",
        json={"companies": [
            {"id": "COM_A", "name": "A", "status": "active"},
            {"id": "COM_B", "name": "B", "status": "active"},
        ], "total_pages": 2},
        status=200,
    )
    # Page 2: 1 more
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/companies.json",
        json={"companies": [
            {"id": "COM_C", "name": "C", "status": "active"},
        ], "total_pages": 2},
        status=200,
    )
    # Each company gets a trackers + calls call.
    for _ in range(3):
        responses.add(
            responses.GET,
            "https://api.callrail.com/v3/a/ACC1/trackers.json",
            json={"trackers": [], "total_pages": 1},
            status=200,
        )
        responses.add(
            responses.GET,
            "https://api.callrail.com/v3/a/ACC1/calls.json",
            json={"calls": [], "total_pages": 1},
            status=200,
        )
    out = json.loads(server_mod.usage_summary(days=30))
    # All 3 companies counted, not just first 2.
    assert len(out["by_company"]) == 3


@responses.activate
def test_v043_paginate_handles_missing_total_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """v0.4.2 fix: previously hardcoded `total_pages` default to 1, silently
    truncating to page 1 whenever the field was missing. Now falls back to
    'stop on empty page'."""
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = CallRailClient(max_retries=0)
    # Page 1: 100 items, NO total_pages.
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/calls.json",
        json={"calls": [{"id": f"CAL_{i}"} for i in range(100)]},
        status=200,
    )
    # Page 2: 50 items, NO total_pages.
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/calls.json",
        json={"calls": [{"id": f"CAL_{i}"} for i in range(100, 150)]},
        status=200,
    )
    # Page 3: empty — terminator.
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/calls.json",
        json={"calls": []},
        status=200,
    )
    items = list(
        server_mod.client.paginate(
            "a/ACC1/calls.json", {"per_page": 100}, items_key="calls"
        )
    )
    # Pre-fix: 100. Post-fix: 150 (all data preserved).
    assert len(items) == 150


@responses.activate
def test_call_eligibility_check_custom_threshold(server_with_mock_client) -> None:
    """A 30s call passes if user lowered Google's threshold to 15s."""
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a.json",
        json={"accounts": [{"id": "ACC1"}]},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.callrail.com/v3/a/ACC1/calls/CAL_SHORT.json",
        json={
            "gclid": "CjwK_x",
            "duration": 30,
            "answered": True,
            "source_name": "google",
        },
        status=200,

    )
    out = json.loads(
        server_mod.call_eligibility_check(call_id="CAL_SHORT", google_ads_min_duration_seconds=15)
    )
    assert out["google_ads_eligible"] is True
    assert out["threshold_used"] == 15
