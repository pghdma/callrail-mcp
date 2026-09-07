"""All-tool adversarial input fuzz (v1.1.1 audit).

Invariant: EVERY MCP tool, called with garbage in ANY single parameter,
must return a JSON-parseable string envelope, never raise. This is the
project's core error contract (tool bodies catch CallRailError and
validation returns _err_msg envelopes; a raw exception crashes the MCP
reply frame).

The 2026-07 deep-audit fuzz found 1,051 violations of this invariant
(non-string types in string fields crashed shared validators with raw
TypeError/AttributeError across all 59 tools). This test pins the fix:
the full matrix (~5k calls) runs in a few seconds against a
contract-respecting mock client.
"""
from __future__ import annotations

import inspect
import json
import logging
from typing import Any
from unittest.mock import MagicMock

import pytest

import callrail_mcp.server as server_mod

# Values chosen to hit every crash class found in the audit: wrong
# types (int/list/dict/bytes/bool/float), empty/whitespace, Unicode,
# path-metachar strings, huge ints, NaN.
GARBAGE: tuple[Any, ...] = (
    None, 0, -1, True, "", "   ", "🚀", "..", "a/b",
    [], {}, ["x"], 10**20, 1.5, float("nan"), "999999999999", b"bytes",
)


def _contract_mock() -> MagicMock:
    """Mock client honoring the CallRailClient contract: HTTP verbs
    return dicts (client._parse guarantees dict-or-CallRailError),
    paginate yields from a list, resolve_account_id returns str."""
    m = MagicMock()
    m.get.return_value = {}
    m.post.return_value = {}
    m.put.return_value = {}
    m.delete.return_value = {}
    m.paginate.return_value = iter([])
    m.resolve_account_id.return_value = "ACC1"
    return m


def _plausible(param: str) -> str:
    """Fill required params with plausible-shaped values so only the
    fuzzed parameter is garbage."""
    if "company" in param:
        return "COM1"
    if "call" in param:
        return "CAL1"
    if "user" in param:
        return "USR1"
    if "tracker" in param:
        return "TRK1"
    if "email" in param:
        return "x@y.co"
    if "number" in param:
        return "+14125551234"
    if "tag_id" in param:
        return "1"
    return "X1"


def _tool_functions() -> list[tuple[str, Any]]:
    tool_names = {t.name for t in server_mod.mcp._tool_manager.list_tools()}
    tools = [
        (n, f) for n, f in vars(server_mod).items()
        if inspect.isfunction(f) and n in tool_names
    ]
    assert len(tools) == len(tool_names), "tool registry / module drift"
    return tools


@pytest.mark.parametrize("tool_name,fn", _tool_functions())
def test_tool_never_raises_on_garbage_input(
    tool_name: str, fn: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(logging.Logger, "warning", lambda *a, **k: None)
    sig = inspect.signature(fn)
    base = {
        p: _plausible(p)
        for p, prm in sig.parameters.items()
        if prm.default is inspect.Parameter.empty
    }
    # Exercise the session-tracker branch too (pool_size validation
    # is unreachable with the default type="source").
    variants = [dict(base)]
    if tool_name == "create_tracker":
        variants.append({**base, "type": "session"})
    for variant in variants:
        for param in sig.parameters:
            for garbage in GARBAGE:
                server_mod._client = _contract_mock()
                kwargs = dict(variant)
                kwargs[param] = garbage
                try:
                    out = fn(**kwargs)
                except Exception as e:  # noqa: BLE001 (the point of the test)
                    pytest.fail(
                        f"{tool_name}({param}={garbage!r}) raised "
                        f"{type(e).__name__}: {e}; tools must return "
                        f"error envelopes, never raise."
                    )
                assert isinstance(out, str), (
                    f"{tool_name}({param}={garbage!r}) returned "
                    f"{type(out).__name__}, expected str"
                )
                json.loads(out)  # must be valid JSON
