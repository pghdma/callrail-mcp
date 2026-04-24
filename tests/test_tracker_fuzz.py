"""Property-based fuzz tests for tracker tools.

Invariant: tracker tools must NEVER raise an uncaught exception. Whatever
random garbage we throw at them, they must return parseable JSON — either
a success body or an error envelope (`{"error": True, ...}`).

This guards against:
- Unicode pathologies in name / whisper / greeting
- Pathological integer inputs (huge, negative, zero) for pool_size / per_page / page
- Empty / whitespace strings on required fields
- Non-string / non-int types where we expect them (defensive against MCP
  clients that send loose JSON).
"""
from __future__ import annotations

import json

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import callrail_mcp.server as server_mod


@pytest.fixture(autouse=True)
def _api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALLRAIL_API_KEY", "test-key")
    server_mod._client = None  # force lazy rebuild


def _is_envelope(s: str) -> bool:
    """Tool output must be JSON-parseable."""
    try:
        parsed = json.loads(s)
    except (ValueError, TypeError):
        return False
    return isinstance(parsed, (dict, list))


# Strategies
ascii_strs = st.text(alphabet=st.characters(min_codepoint=32, max_codepoint=126), max_size=300)
nasty_strs = st.text(max_size=600)  # any unicode incl. control chars
phones = st.from_regex(r"^\+?\d{0,20}$", fullmatch=True)
area_codes = st.text(alphabet="0123456789abcdef-", min_size=0, max_size=6)
maybe_int = st.one_of(st.integers(min_value=-10000, max_value=100000), st.none())


# ---- create_tracker fuzz ----

@settings(deadline=None, max_examples=120, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    name=nasty_strs,
    company_id=ascii_strs,
    destination_number=phones,
    type_=st.sampled_from(["source", "session", "garbage", "", "SOURCE"]),
    source_type=st.sampled_from(["all", "direct", "google_ads", "garbage", ""]),
    area_code=area_codes,
    toll_free=st.booleans(),
    pool_size=maybe_int,
    whisper_message=st.one_of(st.none(), nasty_strs),
    greeting_text=st.one_of(st.none(), nasty_strs),
    confirm_billing=st.booleans(),
)
def test_create_tracker_never_raises(
    name: str,
    company_id: str,
    destination_number: str,
    type_: str,
    source_type: str,
    area_code: str,
    toll_free: bool,
    pool_size: int | None,
    whisper_message: str | None,
    greeting_text: str | None,
    confirm_billing: bool,
) -> None:
    out = server_mod.create_tracker(
        name=name,
        company_id=company_id,
        destination_number=destination_number,
        type=type_,
        source_type=source_type,
        area_code=area_code or None,
        toll_free=toll_free,
        pool_size=pool_size,
        whisper_message=whisper_message,
        greeting_text=greeting_text,
        confirm_billing=confirm_billing,
    )
    assert _is_envelope(out)
    parsed = json.loads(out)
    # Validation should reject everything pre-network — we have a fake API key
    # so any actual network call would error out as well, just differently.
    # Either way, the output is parseable JSON.
    assert isinstance(parsed, dict)


# ---- update_tracker fuzz ----

@settings(deadline=None, max_examples=120, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    tracker_id=ascii_strs,
    name=st.one_of(st.none(), nasty_strs),
    destination_number=st.one_of(st.none(), phones),
    whisper_message=st.one_of(st.none(), nasty_strs),
    greeting_text=st.one_of(st.none(), nasty_strs),
    sms_enabled=st.one_of(st.none(), st.booleans()),
)
def test_update_tracker_never_raises(
    tracker_id: str,
    name: str | None,
    destination_number: str | None,
    whisper_message: str | None,
    greeting_text: str | None,
    sms_enabled: bool | None,
) -> None:
    out = server_mod.update_tracker(
        tracker_id=tracker_id,
        name=name,
        destination_number=destination_number,
        whisper_message=whisper_message,
        greeting_text=greeting_text,
        sms_enabled=sms_enabled,
    )
    assert _is_envelope(out)


# ---- get_tracker fuzz ----

@settings(deadline=None, max_examples=80, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(tracker_id=ascii_strs)
def test_get_tracker_never_raises(tracker_id: str) -> None:
    out = server_mod.get_tracker(tracker_id=tracker_id)
    assert _is_envelope(out)


# ---- delete_tracker fuzz ----

@settings(deadline=None, max_examples=80, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(tracker_id=ascii_strs)
def test_delete_tracker_never_raises(tracker_id: str) -> None:
    out = server_mod.delete_tracker(tracker_id=tracker_id)
    assert _is_envelope(out)


# ---- list_trackers fuzz ----

@settings(deadline=None, max_examples=80, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    status=st.one_of(st.none(), ascii_strs),
    per_page=st.integers(min_value=-1000, max_value=10000),
    page=st.integers(min_value=-1000, max_value=10000),
)
def test_list_trackers_never_raises(status: str | None, per_page: int, page: int) -> None:
    out = server_mod.list_trackers(status=status, per_page=per_page, page=page)
    assert _is_envelope(out)


# ---- C1 specific: greeting_text alone is ALWAYS rejected ----

@settings(deadline=None, max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    tracker_id=st.text(min_size=1, max_size=40, alphabet=st.characters(min_codepoint=33, max_codepoint=126)).filter(
        lambda s: s.strip() and "/" not in s and "." not in s
    ),
    greeting_text=nasty_strs.filter(lambda s: s.strip()),
)
def test_C1_greeting_text_alone_always_rejected(tracker_id: str, greeting_text: str) -> None:
    """greeting_text without destination_number must always produce error envelope."""
    out = server_mod.update_tracker(tracker_id=tracker_id, greeting_text=greeting_text)
    parsed = json.loads(out)
    assert parsed.get("error") is True
    assert "destination_number" in parsed["message"]
