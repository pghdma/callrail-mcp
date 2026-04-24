"""Server-level tests for tool input validation, helpers, and lazy client init."""
from __future__ import annotations

import json

import pytest
import responses

import callrail_mcp.server as server_mod
from callrail_mcp.client import CallRailClient
from callrail_mcp.server import (
    _clamp_per_page,
    _clean_tag_list,
    _date_window,
    _require_non_empty,
    _validate_area_code,
    _validate_date,
    _validate_length,
    _validate_phone,
    _validate_pool_size,
    _validate_tracker_status,
    _validate_window,
)


@pytest.fixture
def server_with_mock_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install a real CallRailClient (no retries) backed by `responses` mocks.

    Use the `responses` decorator on individual tests to register URL stubs.
    """
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = CallRailClient(max_retries=0)

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
