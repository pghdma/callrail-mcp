"""Property-based invariant tests (v1.1.2 deep audit, round 3).

These pin invariants that example-based tests can't exhaustively cover:

1. usage_summary's money invariant: per-company cost shares sum to the
   agency total EXACTLY (cent-level), for arbitrary fleets. This is the
   largest-remainder rounding guarantee that invoice reconciliation
   depends on.
2. _date_window is a total function over garbage inputs and always
   honors an explicit end_date.
3. _safe_path can never emit a dot-segment or an unencoded slash
   (path-traversal safety), no matter the input.
4. _parse_retry_after always lands in [0, MAX_RETRY_DELAY_SECONDS].
5. _is_toll_free never raises on arbitrary text.
"""
from __future__ import annotations

import json
import math
from typing import Any
from unittest.mock import MagicMock

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import callrail_mcp.server as server_mod
from callrail_mcp.client import (
    MAX_RETRY_DELAY_SECONDS,
    CallRailClient,
    CallRailError,
)
from callrail_mcp.server import _date_window, _is_toll_free

# ---- 1. Money invariant ----

company_st = st.fixed_dictionaries({
    "durations": st.lists(st.integers(min_value=0, max_value=10**7), max_size=6),
    "numbers": st.lists(st.sampled_from([
        "+14125551234", "+18005551234", "+18665550000", "+15555550001",
    ]), max_size=4),
})


@settings(max_examples=120, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(fleet=st.lists(company_st, min_size=1, max_size=6))
def test_usage_summary_cost_shares_sum_exactly(fleet: list[dict[str, Any]]) -> None:
    companies = [
        {"id": f"COM{i}", "name": f"Co{i}", "status": "active",
         "time_zone": "America/New_York"}
        for i in range(len(fleet))
    ]

    def fake_paginate(path: str, params: dict[str, Any] | None = None, **kw: Any):
        params = params or {}
        if path.endswith("companies.json"):
            return iter(companies)
        idx = int(str(params.get("company_id", "COM0"))[3:])
        if path.endswith("trackers.json"):
            return iter([{"tracking_numbers": fleet[idx]["numbers"]}])
        return iter([{"duration": d} for d in fleet[idx]["durations"]])

    m = MagicMock()
    m.resolve_account_id.return_value = "ACC1"
    m.paginate.side_effect = fake_paginate
    server_mod._client = m

    out = json.loads(server_mod.usage_summary(days=30))
    assert "error" not in out, out
    total = out["agency"]["estimated_cycle_total"]
    share_cents = [round(r["estimated_cost_share"] * 100) for r in out["by_company"]]
    assert sum(share_cents) == round(total * 100), (
        f"shares {share_cents} must sum to total {total} exactly"
    )
    assert out["partial_failures"] == []


# ---- 2. _date_window totality + explicit-date honoring ----

garbage_days = st.one_of(
    st.integers(min_value=-10**6, max_value=10**6),
    st.floats(allow_nan=True, allow_infinity=True),
    st.text(max_size=8),
    st.booleans(),
    st.none(),
)


@settings(max_examples=300, deadline=None)
@given(days=garbage_days, tz=st.text(max_size=12))
def test_date_window_total_function(days: Any, tz: str) -> None:
    out = _date_window(days, None, None, tz=tz)  # must never raise
    assert isinstance(out, dict)
    if out:
        assert set(out) == {"start_date", "end_date"}
        assert out["start_date"] <= out["end_date"]


@settings(max_examples=100, deadline=None)
@given(days=st.integers(min_value=1, max_value=36500))
def test_date_window_honors_explicit_end_date(days: int) -> None:
    out = _date_window(days, None, "2026-06-01")
    assert out["end_date"] == "2026-06-01"
    assert out["start_date"] < out["end_date"]


# ---- 3. _safe_path traversal safety ----

from callrail_mcp.client import _safe_path  # noqa: E402


@settings(max_examples=500, deadline=None)
@given(path=st.text(max_size=40))
def test_safe_path_never_emits_traversal(path: str) -> None:
    try:
        out = _safe_path(path)
    except CallRailError:
        return  # rejecting is always safe
    for segment in out.split("/") if out else []:
        assert segment not in ("", ".", "..")
        # Every non-alphanumeric-unreserved char must be percent-encoded:
        # no raw spaces, control chars, or reserved delimiters survive.
        assert all(c.isalnum() or c in "-._~%" for c in segment), segment


# ---- 4. Retry-After clamp range ----


@settings(max_examples=300, deadline=None)
@given(value=st.one_of(st.none(), st.text(max_size=30)),
       attempt=st.integers(min_value=0, max_value=10))
def test_parse_retry_after_always_in_range(value: str | None, attempt: int) -> None:
    delay = CallRailClient._parse_retry_after(value, attempt)
    assert 0.0 <= delay <= MAX_RETRY_DELAY_SECONDS
    assert math.isfinite(delay)


# ---- 5. _is_toll_free totality ----


@settings(max_examples=300, deadline=None)
@given(number=st.one_of(st.none(), st.text(max_size=30)))
def test_is_toll_free_never_raises(number: str | None) -> None:
    assert _is_toll_free(number) in (True, False)
