"""
CallRail MCP server.

Exposes CallRail REST API v3 as MCP tools usable from Claude Code / Desktop
and any other MCP-compatible client.

Environment:
    CALLRAIL_API_KEY         API key (required; see also CALLRAIL_API_KEY_FILE)
    CALLRAIL_API_KEY_FILE    Path to a file containing the API key (optional)
    CALLRAIL_BASE_URL        Override API base URL (default: v3 prod)
    CALLRAIL_LOG_LEVEL       Logger level (default: WARNING)

Run standalone for stdio transport:
    python -m callrail_mcp
"""
from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any

from mcp.server.fastmcp import FastMCP

from .client import MAX_PER_PAGE, VALID_TAG_COLORS, CallRailClient, CallRailError

# Library hygiene: do NOT call logging.basicConfig here — that mutates the
# host application's global logging config. Just request a logger; users
# configure handlers/levels themselves. CALLRAIL_LOG_LEVEL is honored only
# when this module's __main__ entry point runs (see main()).
logger = logging.getLogger(__name__)


_client: CallRailClient | None = None


def get_client() -> CallRailClient:
    """Lazy-init the singleton client so module import doesn't require an API key.

    Test code can override by assigning to `callrail_mcp.server._client`.
    """
    global _client
    if _client is None:
        base_url = os.environ.get("CALLRAIL_BASE_URL")
        _client = CallRailClient(base_url=base_url) if base_url else CallRailClient()
    return _client


# Backwards-compatibility shim — older code may reference `server.client`.
class _ClientProxy:
    """Forwards attribute access to the lazy-built client."""

    def __getattr__(self, name: str) -> Any:
        return getattr(get_client(), name)


client = _ClientProxy()
mcp = FastMCP("callrail-mcp")


# ---- Shared helpers ----

_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


def _validate_date(value: str, field_name: str) -> tuple[bool, str]:
    """Return (is_valid, error_message). Empty string is treated as not provided."""
    if not value:
        return True, ""
    if not _DATE_RE.match(value):
        return False, f"{field_name}={value!r} is not a valid YYYY-MM-DD date."
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as e:
        return False, f"{field_name}={value!r} is not a valid date: {e}"
    return True, ""


def _date_window(days: int | None, start_date: str | None, end_date: str | None) -> dict[str, str]:
    """Produce start_date/end_date query params (YYYY-MM-DD).

    Behavior:
    - Explicit dates always win over `days`.
    - `days <= 0` is treated as "no window" only if the caller passes None or 0
      explicitly; the calling tool is responsible for validating positive values.

    Defensively coerces string `days` (e.g. `"7"` from MCP clients sending
    loose JSON) to int. `_validate_window` does the same coercion but only
    returns a (ok, msg) tuple — without this, the original string would
    flow into `days > 0` and raise TypeError.
    """
    if isinstance(days, str):
        try:
            days = int(days)
        except (TypeError, ValueError):
            days = None
    elif isinstance(days, float):
        days = int(days) if days.is_integer() else None
    out: dict[str, str] = {}
    if start_date:
        out["start_date"] = start_date
    if end_date:
        out["end_date"] = end_date
    if days and days > 0 and "start_date" not in out:
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=days)
        out["start_date"] = start.isoformat()
        out["end_date"] = end.isoformat()
    return out


def _validate_window(
    days: int | None,
    start_date: str | None,
    end_date: str | None,
    *,
    require_window: bool = False,
) -> tuple[bool, str]:
    """Cross-field validation for date windows used by listing tools.

    Args:
        require_window: If True, reject `days=0` AND no start_date — without
            this guard, _date_window returns {} and CallRail returns ALL-TIME
            history, which silently blows up aggregating tools (cost
            estimates, summaries). Default False to preserve existing
            list_calls semantics where a single page of all-time data is
            an acceptable fallback.
    """
    # Reject `bool` early: in Python `isinstance(True, int)` is True,
    # which means `days=True` would silently be treated as `days=1`.
    # Almost certainly a caller mistake (probably wanted `False` to skip
    # the window or wrote `days=True` meaning "use default").
    if isinstance(days, bool):
        return False, (
            f"days={days!r} is a bool, not an integer. "
            f"Pass an integer number of days (or omit for default)."
        )
    # Coerce string-typed days from MCP clients that send loose JSON.
    # Reject non-integer floats explicitly — `int(1.5)` silently truncates,
    # which would surprise a user who wrote `days=1.5` expecting ~36h.
    if isinstance(days, float):
        if not days.is_integer():
            return False, f"days={days} is not a whole number; pass an integer."
        days = int(days)
    elif days is not None and not isinstance(days, int):
        try:
            days = int(days)
        except (TypeError, ValueError):
            return False, f"days={days!r} is not a valid integer."
    if days is not None and days < 0:
        return False, f"days={days} is negative."
    # Sanity cap. Without this, an exotic input like `days=10**18` would
    # propagate to `timedelta(days=10**18)` and raise an uncaught
    # OverflowError, crashing the MCP tool reply (the tool body's
    # `try/except CallRailError` doesn't catch OverflowError). 36500
    # days = 100 years; CallRail's data retention is far smaller and any
    # request beyond it is almost certainly a typo.
    if days is not None and days > 36500:
        return False, (
            f"days={days} exceeds maximum lookback of 36500 (~100 years)."
        )
    ok, msg = _validate_date(start_date or "", "start_date")
    if not ok:
        return False, msg
    ok, msg = _validate_date(end_date or "", "end_date")
    if not ok:
        return False, msg
    if start_date and end_date and start_date > end_date:
        return False, f"end_date {end_date!r} is before start_date {start_date!r}."
    if require_window and (days is None or days <= 0) and not start_date:
        return False, (
            "Either days>=1 or an explicit start_date is required "
            "(otherwise we'd aggregate all-time history)."
        )
    return True, ""


def _clamp_per_page(per_page: int) -> int:
    """Clamp per_page to [1, MAX_PER_PAGE]. Silently corrects nonsense input."""
    if per_page is None or per_page < 1:
        return 1
    return min(per_page, MAX_PER_PAGE)


def _ok(data: Any) -> str:
    return json.dumps(data, indent=2, default=str)


def _err(e: CallRailError) -> str:
    # Truncate the body to ~500 chars to prevent CallRail responses from
    # leaking large amounts of echoed data (PII, request payloads) into
    # MCP responses / logs. The full body is already capped at 2000 in
    # client.py; this is a second-line defense for MCP consumers.
    body: Any = e.body
    # Defensively decode bytes (CallRailError docs say str|None but a
    # future contributor might wire bytes through, and json.dumps would
    # raise TypeError on a bytes value).
    if isinstance(body, (bytes, bytearray)):
        body = body.decode("utf-8", errors="replace")
    if isinstance(body, str) and len(body) > 500:
        body = body[:500] + f"... [truncated, {len(e.body) - 500} more chars]"  # type: ignore[arg-type]
    return json.dumps(
        {"error": True, "status": e.status, "message": str(e), "body": body},
        indent=2,
    )


def _err_msg(message: str) -> str:
    return json.dumps({"error": True, "status": None, "message": message}, indent=2)


def _digits_only(s: str) -> str:
    return "".join(ch for ch in s if ch.isdigit())


# ---- Tracker validation helpers (used by create/update_tracker, list_trackers) ----

# Per CallRail TTS limits (empirically observed; docs do not specify exact numeric caps).
# Generous-but-not-crazy ceilings so we fail fast on absurd inputs instead of
# burning a network round-trip and an API error.
_MAX_TRACKER_NAME_LEN = 255
_MAX_TTS_MESSAGE_LEN = 500  # whisper_message and greeting_text use TTS
# Free-text fields on calls/forms. CallRail doesn't document explicit limits
# but multi-MB bodies are clearly a DoS vector (and unlikely intentional).
_MAX_NOTE_LEN = 4000
_MAX_TAGS_PER_REQUEST = 100
_MAX_CUSTOMER_NAME_LEN = 200
_VALID_TRACKER_STATUSES: tuple[str, ...] = ("active", "disabled")
# Loose E.164-ish: optional + then 10-15 ASCII digits. Accepts +14125551234,
# 14125551234. ASCII-only — rejects e.g. Devanagari digits ('\u0966' etc.)
# that Python's `\d` would otherwise match.
_PHONE_RE = re.compile(r"^\+?[0-9]{10,15}$")
_AREA_CODE_RE = re.compile(r"^[0-9]{3}$")  # was ^\d{3}$ — Unicode-digit safe
_TRACKER_ID_RE = re.compile(r"^TRK[A-Za-z0-9_-]+$")
_COMPANY_ID_RE = re.compile(r"^COM[A-Za-z0-9_-]+$")
_TAG_ID_RE = re.compile(r"^[0-9]+$")  # Tag IDs are numeric in CallRail.

# Unicode categories used to reject "invisible" / control / format characters
# in IDs. Cf=format (RTL/LTR override, ZWJ, etc.), Cc=control,
# Cs=surrogate halves, Mn=non-spacing combining marks.
_BANNED_UNICODE_CATEGORIES: frozenset[str] = frozenset({"Cf", "Cc", "Cs", "Mn"})


def _require_non_empty(value: str | None, field_name: str) -> tuple[bool, str]:
    """True only if value is a non-empty, non-whitespace string."""
    if value is None or not str(value).strip():
        return False, f"{field_name} is required and cannot be empty."
    return True, ""


def _validate_id_shape(
    value: str,
    field_name: str,
    prefix: str | None = None,
) -> tuple[bool, str]:
    """Validate a CallRail ID looks like a single URL-safe segment.

    - Must not contain '/' (multi-segment paths would reach different
      endpoints — CallRail 404s, but we shouldn't send the request).
    - Must not be just dots (those slip past `_safe_path`'s exact-match
      check when concatenated with a file extension like '.json').
    - Must not contain bidi/zero-width/combining characters that would
      flow through `_safe_path` (which only blocks ord<0x20 + 0x7f) and
      end up percent-encoded into URLs / log lines / error envelopes.
      Examples: U+202E RTL override, U+200B zero-width space, combining
      diacritics. These can hide spoofed IDs in display contexts.
    - Optional: must start with the given prefix ('TRK', 'COM', etc.).
    """
    if "/" in value:
        return False, (
            f"{field_name}={value!r} may not contain '/'. "
            f"IDs must be single URL path segments."
        )
    # Catch values that are only dots — they collide with .json suffix in
    # URL construction and produce bogus paths (e.g. tracker_id='..' →
    # 'trackers/...json', which isn't traversal but wastes an API call).
    if set(value.strip()) <= {"."}:
        return False, f"{field_name}={value!r} cannot consist only of dots."
    # Reject bidi controls / zero-width / combining marks. These pass
    # `_safe_path`'s control-char filter (which only blocks ord<0x20|0x7f)
    # but cause display ambiguity — an ID like 'TRK\u202eABC' renders as
    # 'TRKCBA' in many UIs, masking spoofed values in logs.
    bad = [c for c in value if unicodedata.category(c) in _BANNED_UNICODE_CATEGORIES]
    if bad:
        return False, (
            f"{field_name}={value!r} contains disallowed Unicode characters "
            f"(category {unicodedata.category(bad[0])}: bidi/zero-width/combining)."
        )
    if prefix and not value.startswith(prefix):
        return False, (
            f"{field_name}={value!r} must start with {prefix!r} "
            f"(CallRail IDs are prefixed: TRK for trackers, COM for companies, etc.)."
        )
    return True, ""


def _validate_phone(value: str, field_name: str) -> tuple[bool, str]:
    """Loose E.164-ish phone check. Avoids burning an API call on obvious garbage."""
    if not _PHONE_RE.match(value.strip()):
        return False, (
            f"{field_name}={value!r} doesn't look like a phone number "
            f"(expected E.164 format like '+14125551234' or '14125551234')."
        )
    return True, ""


def _validate_area_code(value: str) -> tuple[bool, str]:
    if not _AREA_CODE_RE.match(value):
        return False, f"area_code={value!r} must be exactly 3 digits (e.g. '412')."
    return True, ""


def _validate_pool_size(value: int) -> tuple[bool, str]:
    """Pool size sanity. CallRail prices each pool number, so cap aggressively
    to prevent accidental 5-figure provisioning bills."""
    if value < 1:
        return False, f"pool_size={value} must be >= 1."
    if value > 50:
        return False, (
            f"pool_size={value} exceeds safety cap of 50. "
            f"If you really need this many, edit the cap in server.py."
        )
    return True, ""


def _validate_length(value: str, field_name: str, max_len: int) -> tuple[bool, str]:
    if len(value) > max_len:
        return False, f"{field_name} length {len(value)} exceeds max {max_len}."
    return True, ""


def _validate_tracker_status(value: str | None) -> tuple[bool, str]:
    """list_trackers' status filter. None = no filter."""
    if value is None or value == "":
        return True, ""
    if value not in _VALID_TRACKER_STATUSES:
        return False, (
            f"status={value!r} must be one of {_VALID_TRACKER_STATUSES} or None."
        )
    return True, ""


# ---- Tools ----

@mcp.tool()
def list_accounts() -> str:
    """List CallRail accounts accessible to this API key.

    Most users have one account per agency. The returned `id` is used as
    `account_id` in all other tools (auto-resolved if omitted).
    """
    try:
        return _ok(client.get("a.json"))
    except CallRailError as e:
        return _err(e)


@mcp.tool()
def list_companies(
    account_id: str | None = None,
    per_page: int = 250,
    status: str | None = None,
) -> str:
    """List companies (client businesses) under a CallRail account.

    Args:
        account_id: CallRail account ID. Auto-resolves if omitted.
        per_page: Page size (max 250).
        status: Filter by status. Defaults to None (returns all). Common values:
            'active' (excludes disabled/soft-deleted), 'disabled'.
    """
    try:
        aid = client.resolve_account_id(account_id)
        params: dict[str, Any] = {"per_page": _clamp_per_page(per_page)}
        if status:
            params["status"] = status
        return _ok(client.get(f"a/{aid}/companies.json", params))
    except CallRailError as e:
        return _err(e)


VALID_TRACKER_TYPES: tuple[str, ...] = ("source", "session")
# Discovered empirically by exhaustive testing + live production trackers
# observed in the wild — CallRail's docs do not enumerate these. Any other
# source.type value returns 400 "Source Unknown tracking source type".
#
# If you encounter a 400 when using a source type that's clearly valid in
# the CallRail UI, add it here and open an issue/PR.
VALID_SOURCE_TYPES: tuple[str, ...] = (
    "all",
    "direct",
    "offline",
    "google_my_business",
    "google_ad_extension",  # Google Ads call extensions
    "facebook_all",          # Facebook/Meta ads (observed in production)
    "bing_all",              # Bing/Microsoft Ads (observed in production)
)


@mcp.tool()
def list_trackers(
    account_id: str | None = None,
    company_id: str | None = None,
    per_page: int = 250,
    page: int = 1,
    status: str | None = None,
) -> str:
    """List tracking phone numbers (trackers). Each tracker maps a pool of
    phone numbers to a traffic source (Google Ads, Organic, Direct, etc.).

    Args:
        account_id: Auto-resolves if omitted.
        company_id: Filter to one company.
        per_page: Page size (max 250).
        page: 1-indexed.
        status: Filter by status. Defaults to None (returns all, including
            soft-deleted/disabled). Common values: 'active', 'disabled'.
    """
    ok, msg = _validate_tracker_status(status)
    if not ok:
        return _err_msg(msg)
    try:
        aid = client.resolve_account_id(account_id)
        params: dict[str, Any] = {"per_page": _clamp_per_page(per_page), "page": max(1, page)}
        if company_id:
            params["company_id"] = company_id
        if status:
            params["status"] = status
        return _ok(client.get(f"a/{aid}/trackers.json", params))
    except CallRailError as e:
        return _err(e)


@mcp.tool()
def get_tracker(tracker_id: str, account_id: str | None = None) -> str:
    """Get full detail for a specific tracker.

    Args:
        tracker_id: 'TRK...' id.
        account_id: Auto-resolves if omitted.
    """
    ok, msg = _require_non_empty(tracker_id, "tracker_id")
    if not ok:
        return _err_msg(msg)
    ok, msg = _validate_id_shape(tracker_id, "tracker_id")
    if not ok:
        return _err_msg(msg)
    try:
        aid = client.resolve_account_id(account_id)
        return _ok(client.get(f"a/{aid}/trackers/{tracker_id}.json"))
    except CallRailError as e:
        return _err(e)


@mcp.tool()
def create_tracker(
    name: str,
    company_id: str,
    destination_number: str,
    confirm_billing: bool = False,
    type: str = "source",
    source_type: str = "all",
    area_code: str | None = None,
    toll_free: bool = False,
    pool_size: int | None = None,
    whisper_message: str | None = None,
    recording_enabled: bool = True,
    greeting_text: str | None = None,
    sms_enabled: bool = True,
    account_id: str | None = None,
) -> str:
    """⚠️  Create a new tracking phone number (tracker). **THIS COSTS MONEY.**

    CallRail charges per provisioned number — typical pricing as of 2026:
      - Local numbers: ~$3/month each
      - Toll-free (8XX): ~$3-5/month each
      - Session pools: charged per number × pool_size (so pool_size=8 = 8x)
      - Plus per-minute usage (~$0.05/min on answered calls)

    Most plans bundle 5–10 numbers; provisioning beyond your bundle adds
    overage charges. Some plans prorate partial-month usage, so creating
    and immediately deleting can still produce a small charge depending
    on your contract.

    **You must pass `confirm_billing=True` to actually create.** This guards
    against accidental provisioning when an AI is exploring tools.

    Args:
        name: Display name for the tracker (e.g. "Google Ads Call Extension").
        company_id: 'COM...' id of the company this tracker belongs to.
        destination_number: Where calls forward to, e.g. "+14129548337".
        confirm_billing: REQUIRED — set True to acknowledge the per-number
            cost. Returns an error envelope if False (default).
        type: 'source' (single number tied to one traffic source) or 'session'
            (DNI pool that swaps numbers per visitor). Default 'source'.
        source_type: For type='source', which traffic source. Must be one of:
            'all', 'direct', 'offline', 'google_my_business',
            'google_ad_extension' (this is what Google Ads call-extension uses).
            Ignored for type='session' (use 'all').
        area_code: 3-digit area code to provision the local number from
            (e.g. '412'). Ignored if `toll_free=True`.
        toll_free: If True, provision an 8XX toll-free number instead.
        pool_size: For type='session' only — how many numbers in the DNI pool
            (CallRail's "pool_size" required field). Typical 4-10. Each pool
            number is billed separately.
        whisper_message: Spoken to the agent answering the call so they know
            which marketing source it came from.
        recording_enabled: Record the call audio. Default True.
        greeting_text: Optional automated greeting text-to-speech.
        sms_enabled: Allow this number to receive/send SMS. Default True.
        account_id: Auto-resolves if omitted.

    Returns the created tracker including its newly-provisioned tracking_numbers.
    """
    # ---- Validate inputs BEFORE any network call. ----
    # Required fields, non-empty.
    for value, field in ((name, "name"), (company_id, "company_id"), (destination_number, "destination_number")):
        ok, msg = _require_non_empty(value, field)
        if not ok:
            return _err_msg(msg)
    # Length caps so we fast-fail instead of provisioning a number with
    # absurd metadata or running a 5-minute TTS greeting.
    ok, msg = _validate_length(name, "name", _MAX_TRACKER_NAME_LEN)
    if not ok:
        return _err_msg(msg)
    if whisper_message is not None:
        ok, msg = _validate_length(whisper_message, "whisper_message", _MAX_TTS_MESSAGE_LEN)
        if not ok:
            return _err_msg(msg)
    if greeting_text is not None:
        ok, msg = _validate_length(greeting_text, "greeting_text", _MAX_TTS_MESSAGE_LEN)
        if not ok:
            return _err_msg(msg)
    # destination_number format.
    ok, msg = _validate_phone(destination_number, "destination_number")
    if not ok:
        return _err_msg(msg)
    # Enum-valued fields.
    if type not in VALID_TRACKER_TYPES:
        return _err_msg(f"type must be one of {VALID_TRACKER_TYPES}, got {type!r}")
    if type == "source" and source_type not in VALID_SOURCE_TYPES:
        return _err_msg(
            f"source_type must be one of {VALID_SOURCE_TYPES}, got {source_type!r}. "
            f"Note: 'google_ad_extension' is what Google Ads call extensions use; "
            f"for general Google Ads / Bing Ads / Facebook DNI use type='session'."
        )
    # Tracking-number provisioning rules.
    if toll_free and area_code:
        return _err_msg(
            "Cannot specify both toll_free=True and area_code. "
            "Toll-free numbers don't have an area code; choose one."
        )
    if not toll_free and not area_code and type == "source":
        return _err_msg("Provide area_code (e.g. '412') or set toll_free=True.")
    if area_code is not None:
        ok, msg = _validate_area_code(area_code)
        if not ok:
            return _err_msg(msg)
    if type == "session":
        if pool_size is None:
            return _err_msg("type='session' requires pool_size (typical: 4-10).")
        ok, msg = _validate_pool_size(pool_size)
        if not ok:
            return _err_msg(msg)
    # Billing confirmation last so the user sees real validation errors first.
    if not confirm_billing:
        return _err_msg(
            "create_tracker requires confirm_billing=True. CallRail charges "
            "per provisioned number (~$3/mo local, ~$3-5/mo toll-free; "
            "session pools = pool_size × per-number cost). Pass "
            "confirm_billing=True if you intend to incur this charge."
        )

    body: dict[str, Any] = {
        "name": name,
        "company_id": company_id,
        "type": type,
        "destination_number": destination_number,
        "call_flow": {
            "type": "basic",
            "destination_number": destination_number,
            "recording_enabled": recording_enabled,
        },
    }
    if greeting_text is not None:
        body["call_flow"]["greeting_text"] = greeting_text
    if type == "source":
        body["source"] = {"type": source_type}
    tn: dict[str, Any] = {}
    if toll_free:
        tn["toll_free"] = True
    elif area_code:
        tn["area_code"] = area_code
    if type == "session" and pool_size is not None:
        tn["pool_size"] = pool_size
    body["tracking_number"] = tn
    if whisper_message is not None:
        body["whisper_message"] = whisper_message
    body["sms_enabled"] = sms_enabled

    try:
        aid = client.resolve_account_id(account_id)
        return _ok(client.post(f"a/{aid}/trackers.json", body))
    except CallRailError as e:
        return _err(e)


@mcp.tool()
def update_tracker(
    tracker_id: str,
    account_id: str | None = None,
    name: str | None = None,
    destination_number: str | None = None,
    whisper_message: str | None = None,
    greeting_text: str | None = None,
    sms_enabled: bool | None = None,
) -> str:
    """Update a tracker's mutable settings: name, destination, whisper, greeting, SMS.

    Args:
        tracker_id: 'TRK...' id.
        account_id: Auto-resolves if omitted.
        name: New display name.
        destination_number: Where calls forward (e.g. "+14129548337"). Updates
            the call_flow's destination too.
        whisper_message: New whisper text.
        greeting_text: New automated greeting. **If supplied, you must also
            supply destination_number** — CallRail's PUT /trackers replaces the
            entire call_flow object, so updating only greeting_text would
            silently zero out the destination, breaking the tracker.
        sms_enabled: Toggle SMS on/off.

    Field-level rules:
        - `name`, `destination_number`, `whisper_message`, `greeting_text`
          must be non-empty strings if provided. Pass `None` (the default)
          to leave a field unchanged.

    NOTE: Setting `status` via this PUT is silently ignored by CallRail.
    To disable a tracker, use `delete_tracker(tracker_id)` (soft-delete /
    disabled, keeps history). To permanently remove, contact CallRail support.
    """
    # ---- Pre-network validation. ----
    ok, msg = _require_non_empty(tracker_id, "tracker_id")
    if not ok:
        return _err_msg(msg)
    ok, msg = _validate_id_shape(tracker_id, "tracker_id")
    if not ok:
        return _err_msg(msg)
    # Reject explicit empty strings on optional fields. (Callers should pass
    # None to leave a field unchanged; "" is almost certainly a mistake.)
    for value, field in (
        (name, "name"),
        (destination_number, "destination_number"),
        (whisper_message, "whisper_message"),
        (greeting_text, "greeting_text"),
    ):
        if value is not None:
            ok, msg = _require_non_empty(value, field)
            if not ok:
                return _err_msg(msg)
    # Length caps for TTS / display fields.
    if name is not None:
        ok, msg = _validate_length(name, "name", _MAX_TRACKER_NAME_LEN)
        if not ok:
            return _err_msg(msg)
    if whisper_message is not None:
        ok, msg = _validate_length(whisper_message, "whisper_message", _MAX_TTS_MESSAGE_LEN)
        if not ok:
            return _err_msg(msg)
    if greeting_text is not None:
        ok, msg = _validate_length(greeting_text, "greeting_text", _MAX_TTS_MESSAGE_LEN)
        if not ok:
            return _err_msg(msg)
    # destination_number format if provided.
    if destination_number is not None:
        ok, msg = _validate_phone(destination_number, "destination_number")
        if not ok:
            return _err_msg(msg)
    # CRITICAL: greeting_text alone would replace call_flow with an object
    # that's missing destination_number, breaking the tracker. CallRail's
    # PUT /trackers does NOT do partial-merge inside call_flow, only at
    # the top level. Force the caller to be explicit.
    if greeting_text is not None and destination_number is None:
        return _err_msg(
            "Updating greeting_text requires also passing destination_number "
            "(CallRail's PUT replaces the entire call_flow object — supplying "
            "only greeting_text would zero out the destination). Pass both, "
            "or call get_tracker first to read the existing destination_number."
        )

    body: dict[str, Any] = {}
    if name is not None:
        body["name"] = name
    if destination_number is not None:
        body["destination_number"] = destination_number
        body["call_flow"] = {"type": "basic", "destination_number": destination_number}
    if whisper_message is not None:
        body["whisper_message"] = whisper_message
    if greeting_text is not None:
        # destination_number guaranteed present by validation above.
        body["call_flow"]["greeting_text"] = greeting_text
    if sms_enabled is not None:
        body["sms_enabled"] = sms_enabled
    if not body:
        return _err_msg("No fields supplied to update.")
    try:
        aid = client.resolve_account_id(account_id)
        return _ok(client.put(f"a/{aid}/trackers/{tracker_id}.json", body))
    except CallRailError as e:
        return _err(e)


@mcp.tool()
def delete_tracker(tracker_id: str, account_id: str | None = None) -> str:
    """Delete (disable) a tracker. Soft-removes it from active trackers; the
    tracker keeps its call history but stops receiving new calls. The
    underlying phone number is released.

    Args:
        tracker_id: 'TRK...' id.
        account_id: Auto-resolves if omitted.

    Returns: An object with `deleted: True`, `tracker_id`, and `response`
    (CallRail's body, which on success contains the disabled tracker record
    including `disabled_at` timestamp). Empty object if CallRail returned 204.
    """
    ok, msg = _require_non_empty(tracker_id, "tracker_id")
    if not ok:
        return _err_msg(msg)
    ok, msg = _validate_id_shape(tracker_id, "tracker_id")
    if not ok:
        return _err_msg(msg)
    try:
        aid = client.resolve_account_id(account_id)
        result = client.delete(f"a/{aid}/trackers/{tracker_id}.json")
        return _ok({"deleted": True, "tracker_id": tracker_id, "response": result})
    except CallRailError as e:
        return _err(e)


@mcp.tool()
def list_calls(
    account_id: str | None = None,
    company_id: str | None = None,
    days: int = 7,
    start_date: str | None = None,
    end_date: str | None = None,
    source: str | None = None,
    answered: str | None = None,
    per_page: int = 100,
    page: int = 1,
    fields: str | None = None,
) -> str:
    """List calls. Paginated. Filterable by company, date window, source,
    answered status.

    Args:
        account_id: Auto-resolves if omitted.
        company_id: Filter to one company. Omit for all companies.
        days: Lookback in days (default 7). Ignored if `start_date` provided.
        start_date: 'YYYY-MM-DD'.
        end_date: 'YYYY-MM-DD' (defaults to today).
        source: Filter (e.g. 'google_paid', 'google_organic', 'direct', 'bing_paid').
        answered: 'true' or 'false'.
        per_page: Max 250.
        page: 1-indexed.
        fields: Comma-separated additional fields to include, e.g.
            'company_name,source_name,keywords,landing_page_url,device,
            first_call,value,tags,note,gclid,fbclid,utm_source,utm_medium,
            utm_campaign,utm_content,utm_term,referrer_domain'.
    """
    ok, msg = _validate_window(days, start_date, end_date)
    if not ok:
        return _err_msg(msg)
    try:
        aid = client.resolve_account_id(account_id)
        params: dict[str, Any] = {"per_page": _clamp_per_page(per_page), "page": max(1, page)}
        params.update(_date_window(days, start_date, end_date))
        if company_id:
            params["company_id"] = company_id
        if source:
            params["source"] = source
        if answered is not None:
            params["answered"] = answered
        if fields:
            params["fields"] = fields
        return _ok(client.get(f"a/{aid}/calls.json", params))
    except CallRailError as e:
        return _err(e)


@mcp.tool()
def get_call(call_id: str, account_id: str | None = None, fields: str | None = None) -> str:
    """Get full detail for a specific call.

    Args:
        call_id: CallRail call id (prefix 'CAL...').
        account_id: Auto-resolves if omitted.
        fields: Comma-separated extra fields (see list_calls for common names).
    """
    ok, msg = _require_non_empty(call_id, "call_id")
    if not ok:
        return _err_msg(msg)
    ok, msg = _validate_id_shape(call_id, "call_id", prefix="CAL")
    if not ok:
        return _err_msg(msg)
    try:
        aid = client.resolve_account_id(account_id)
        params: dict[str, Any] = {}
        if fields:
            params["fields"] = fields
        return _ok(client.get(f"a/{aid}/calls/{call_id}.json", params))
    except CallRailError as e:
        return _err(e)


@mcp.tool()
def call_summary(
    account_id: str | None = None,
    company_id: str | None = None,
    days: int = 7,
    start_date: str | None = None,
    end_date: str | None = None,
) -> str:
    """Summarize calls over a date window.

    Returns counts: total, answered/missed, first-time/repeat callers, total
    duration, and breakdowns by `source` and `source_name`. Useful for
    weekly/monthly rollups without pulling every call into context.

    Note: requires `days>=1` or an explicit `start_date` — without a window
    this would paginate the entire account history (potentially 50+ pages
    of 250 calls each), which is rarely what callers want.
    """
    ok, msg = _validate_window(days, start_date, end_date, require_window=True)
    if not ok:
        return _err_msg(msg)
    try:
        aid = client.resolve_account_id(account_id)
        params: dict[str, Any] = {
            "per_page": MAX_PER_PAGE,
            "fields": "source,source_name,answered,first_call,duration",
        }
        params.update(_date_window(days, start_date, end_date))
        if company_id:
            params["company_id"] = company_id

        total = answered = missed = first_time = repeat = duration_total = 0
        by_source: dict[str, int] = {}
        by_source_name: dict[str, int] = {}

        for c in client.paginate(f"a/{aid}/calls.json", params, items_key="calls", max_pages=50):
            total += 1
            if c.get("answered"):
                answered += 1
            else:
                missed += 1
            if c.get("first_call"):
                first_time += 1
            else:
                repeat += 1
            # Robust int coercion — match usage_summary's defense (CallRail
            # currently returns int but shape changes shouldn't crash mid-loop).
            raw_duration = c.get("duration") or 0
            try:
                duration_total += int(float(raw_duration))
            except (TypeError, ValueError):
                logger.warning(
                    "call_summary: skipping call with malformed "
                    "duration=%r in call %s", raw_duration, c.get("id", "?"),
                )
            src = c.get("source") or "(none)"
            by_source[src] = by_source.get(src, 0) + 1
            sname = c.get("source_name") or "(none)"
            by_source_name[sname] = by_source_name.get(sname, 0) + 1

        return _ok(
            {
                "window": _date_window(days, start_date, end_date),
                "company_id": company_id,
                "total_calls": total,
                "answered": answered,
                "missed": missed,
                "first_time_callers": first_time,
                "repeat_callers": repeat,
                "total_duration_seconds": duration_total,
                "by_source": dict(sorted(by_source.items(), key=lambda x: -x[1])),
                "by_source_name": dict(sorted(by_source_name.items(), key=lambda x: -x[1])[:25]),
            }
        )
    except CallRailError as e:
        return _err(e)


@mcp.tool()
def list_form_submissions(
    account_id: str | None = None,
    company_id: str | None = None,
    days: int = 7,
    start_date: str | None = None,
    end_date: str | None = None,
    per_page: int = 100,
    page: int = 1,
    fields: str | None = None,
) -> str:
    """List form submissions captured by CallRail's Form Tracking."""
    ok, msg = _validate_window(days, start_date, end_date)
    if not ok:
        return _err_msg(msg)
    try:
        aid = client.resolve_account_id(account_id)
        params: dict[str, Any] = {"per_page": _clamp_per_page(per_page), "page": max(1, page)}
        params.update(_date_window(days, start_date, end_date))
        if company_id:
            params["company_id"] = company_id
        if fields:
            params["fields"] = fields
        return _ok(client.get(f"a/{aid}/form_submissions.json", params))
    except CallRailError as e:
        return _err(e)


@mcp.tool()
def list_text_messages(
    account_id: str | None = None,
    company_id: str | None = None,
    days: int = 7,
    start_date: str | None = None,
    end_date: str | None = None,
    per_page: int = 100,
    page: int = 1,
) -> str:
    """List SMS/text message conversations."""
    ok, msg = _validate_window(days, start_date, end_date)
    if not ok:
        return _err_msg(msg)
    try:
        aid = client.resolve_account_id(account_id)
        params: dict[str, Any] = {"per_page": _clamp_per_page(per_page), "page": max(1, page)}
        params.update(_date_window(days, start_date, end_date))
        if company_id:
            params["company_id"] = company_id
        return _ok(client.get(f"a/{aid}/text-messages.json", params))
    except CallRailError as e:
        return _err(e)


@mcp.tool()
def list_users(account_id: str | None = None) -> str:
    """List users on the account."""
    try:
        aid = client.resolve_account_id(account_id)
        return _ok(client.get(f"a/{aid}/users.json", {"per_page": MAX_PER_PAGE}))
    except CallRailError as e:
        return _err(e)


@mcp.tool()
def get_call_recording(call_id: str, account_id: str | None = None) -> str:
    """Get the recording URL for a call (if recording is enabled on the company)."""
    ok, msg = _require_non_empty(call_id, "call_id")
    if not ok:
        return _err_msg(msg)
    ok, msg = _validate_id_shape(call_id, "call_id", prefix="CAL")
    if not ok:
        return _err_msg(msg)
    try:
        aid = client.resolve_account_id(account_id)
        return _ok(client.get(f"a/{aid}/calls/{call_id}/recording.json"))
    except CallRailError as e:
        return _err(e)


@mcp.tool()
def get_call_transcript(call_id: str, account_id: str | None = None) -> str:
    """Get the AI transcript for a call (requires CallRail Conversation Intelligence)."""
    ok, msg = _require_non_empty(call_id, "call_id")
    if not ok:
        return _err_msg(msg)
    ok, msg = _validate_id_shape(call_id, "call_id", prefix="CAL")
    if not ok:
        return _err_msg(msg)
    try:
        aid = client.resolve_account_id(account_id)
        return _ok(client.get(f"a/{aid}/calls/{call_id}/transcription.json"))
    except CallRailError as e:
        return _err(e)


@mcp.tool()
def search_calls_by_number(
    phone_number: str,
    account_id: str | None = None,
    company_id: str | None = None,
    days: int = 90,
) -> str:
    """Find calls from/to a specific phone number. Matches on the last 10 digits
    of the stored `customer_phone_number` so any format works.

    Args:
        phone_number: Any format — will be normalized to digits-only.
            Must contain at least 7 digits to avoid false positives.
        account_id: Auto-resolves.
        company_id: Optional company filter.
        days: Lookback window (default 90).
    """
    digits = _digits_only(phone_number or "")
    if len(digits) < 7:
        return _err_msg(
            f"phone_number must contain at least 7 digits after stripping non-digits "
            f"(got {len(digits)} digit{'s' if len(digits) != 1 else ''} from {phone_number!r})."
        )
    if len(digits) > 10:
        digits = digits[-10:]
    # require_window=True — without a window we'd paginate all-time
    # call history just to filter for a phone match, which is hugely
    # wasteful (the user almost certainly wants recent calls).
    ok, msg = _validate_window(days, None, None, require_window=True)
    if not ok:
        return _err_msg(msg)
    try:
        aid = client.resolve_account_id(account_id)
        params: dict[str, Any] = {
            "per_page": MAX_PER_PAGE,
            "fields": "source,source_name,customer_phone_number,customer_name,answered,duration,first_call",
        }
        params.update(_date_window(days, None, None))
        if company_id:
            params["company_id"] = company_id

        # Cap matches at 500 to prevent MCP-frame-exceeding payloads on
        # popular numbers (a 365-day window on a hot hotline could hit
        # thousands of matches × ~500 bytes each = MBs of JSON).
        SEARCH_MATCH_CAP = 500
        matches: list[dict[str, Any]] = []
        truncated = False
        for c in client.paginate(f"a/{aid}/calls.json", params, items_key="calls", max_pages=50):
            num = _digits_only(c.get("customer_phone_number") or "")
            if num.endswith(digits):
                if len(matches) >= SEARCH_MATCH_CAP:
                    truncated = True
                    break
                matches.append(c)
        return _ok({
            "query": phone_number,
            "match_count": len(matches),
            "truncated": truncated,
            "match_cap": SEARCH_MATCH_CAP if truncated else None,
            "calls": matches,
        })
    except CallRailError as e:
        return _err(e)


# ---- Write tools (v0.2) ----

@mcp.tool()
def update_call(
    call_id: str,
    account_id: str | None = None,
    note: str | None = None,
    tags: list[str] | None = None,
    spam: bool | None = None,
    customer_name: str | None = None,
    lead_status: str | None = None,
) -> str:
    """Update an existing call: notes, tags, spam flag, customer name, lead status.

    Args:
        call_id: 'CAL...' id of the call to update.
        account_id: Auto-resolves if omitted.
        note: Replace the call's note text.
        tags: REPLACE the call's tag list with this set of tag names.
              (Use `add_call_tags`/`remove_call_tags` for additive changes.)
        spam: True to mark as spam, False to unmark. Note: spam-flagged calls
              are HIDDEN from default GET endpoints — re-reads will 404. Tag
              the call BEFORE flagging spam if you need both.
        customer_name: Override the auto-detected caller name.
        lead_status: e.g. 'good_lead', 'not_a_lead', 'unknown'.

    Note: `value` is intentionally NOT exposed here. CallRail's API returns
    a 500 server error when `value` is included in the PUT body to /calls
    (verified via live testing 2026-04-24). It IS supported on form
    submissions — see `update_form_submission`.

    Empty-string fields (e.g. `note=""`) are rejected because CallRail
    interprets them as "clear this field" — almost always a mistake.
    To intentionally clear a field, set it to None and use a separate UI
    operation, or contact CallRail support.

    Length caps (rejected pre-network):
        - `note`: 4000 chars
        - `customer_name`: 200 chars
        - `tags`: 100 entries max
    """
    ok, msg = _require_non_empty(call_id, "call_id")
    if not ok:
        return _err_msg(msg)
    ok, msg = _validate_id_shape(call_id, "call_id", prefix="CAL")
    if not ok:
        return _err_msg(msg)
    # Reject empty strings on free-text optional fields.
    for value, field in ((note, "note"), (customer_name, "customer_name"), (lead_status, "lead_status")):
        if value is not None:
            ok, msg = _require_non_empty(value, field)
            if not ok:
                return _err_msg(msg)
    # Length caps: prevent multi-MB request bodies.
    if note is not None:
        ok, msg = _validate_length(note, "note", _MAX_NOTE_LEN)
        if not ok:
            return _err_msg(msg)
    if customer_name is not None:
        ok, msg = _validate_length(customer_name, "customer_name", _MAX_CUSTOMER_NAME_LEN)
        if not ok:
            return _err_msg(msg)
    if tags is not None and len(tags) > _MAX_TAGS_PER_REQUEST:
        return _err_msg(
            f"tags list length {len(tags)} exceeds max {_MAX_TAGS_PER_REQUEST}."
        )
    body: dict[str, Any] = {}
    if note is not None:
        body["note"] = note
    if tags is not None:
        body["tags"] = tags
    if spam is not None:
        body["spam"] = spam
    if customer_name is not None:
        body["customer_name"] = customer_name
    if lead_status is not None:
        body["lead_status"] = lead_status
    if not body:
        return _err_msg("No fields supplied to update.")
    try:
        aid = client.resolve_account_id(account_id)
        return _ok(client.put(f"a/{aid}/calls/{call_id}.json", body))
    except CallRailError as e:
        return _err(e)


def _clean_tag_list(tags: list[str] | None) -> list[str]:
    """Strip whitespace, drop empties, dedupe in original order.

    Logs a warning when non-string entries are dropped (helps debug
    callers that pass mixed types like `[42, 'hot']` and wonder why
    only one tag was added).
    """
    if not tags:
        return []
    seen: dict[str, None] = {}
    dropped_non_strings = 0
    for t in tags:
        if not isinstance(t, str):
            dropped_non_strings += 1
            continue
        s = t.strip()
        if s:
            seen.setdefault(s, None)
    if dropped_non_strings:
        logger.warning(
            "_clean_tag_list dropped %d non-string entries; "
            "tags must be strings.", dropped_non_strings,
        )
    return list(seen.keys())


@mcp.tool()
def add_call_tags(call_id: str, tags: list[str], account_id: str | None = None) -> str:
    """Append tags to a call without replacing existing ones.

    Empty/whitespace-only tag names are silently filtered out so that a
    request like `add_call_tags(['', 'lead'])` won't 400 — only `'lead'`
    is sent. Returns an error if no valid tags remain after cleaning.
    """
    ok, msg = _require_non_empty(call_id, "call_id")
    if not ok:
        return _err_msg(msg)
    ok, msg = _validate_id_shape(call_id, "call_id", prefix="CAL")
    if not ok:
        return _err_msg(msg)
    cleaned = _clean_tag_list(tags)
    if not cleaned:
        return _err_msg("tags is empty (or only contained empty/whitespace strings).")
    try:
        aid = client.resolve_account_id(account_id)
        existing = client.get(f"a/{aid}/calls/{call_id}.json", {"fields": "tags"}).get("tags") or []
        existing_names = [t.get("name", t) if isinstance(t, dict) else t for t in existing]
        merged = list(dict.fromkeys(existing_names + cleaned))
        return _ok(client.put(f"a/{aid}/calls/{call_id}.json", {"tags": merged}))
    except CallRailError as e:
        return _err(e)


@mcp.tool()
def remove_call_tags(call_id: str, tags: list[str], account_id: str | None = None) -> str:
    """Remove specific tags from a call (case-sensitive on tag name).

    Idempotent — removing a tag that isn't attached succeeds silently.
    Empty/whitespace-only entries in the input list are ignored.
    """
    ok, msg = _require_non_empty(call_id, "call_id")
    if not ok:
        return _err_msg(msg)
    ok, msg = _validate_id_shape(call_id, "call_id", prefix="CAL")
    if not ok:
        return _err_msg(msg)
    cleaned = _clean_tag_list(tags)
    if not cleaned:
        return _err_msg("tags is empty (or only contained empty/whitespace strings).")
    try:
        aid = client.resolve_account_id(account_id)
        existing = client.get(f"a/{aid}/calls/{call_id}.json", {"fields": "tags"}).get("tags") or []
        existing_names = [t.get("name", t) if isinstance(t, dict) else t for t in existing]
        to_remove = set(cleaned)
        kept = [t for t in existing_names if t not in to_remove]
        return _ok(client.put(f"a/{aid}/calls/{call_id}.json", {"tags": kept}))
    except CallRailError as e:
        return _err(e)


@mcp.tool()
def update_form_submission(
    submission_id: str,
    account_id: str | None = None,
    note: str | None = None,
    tags: list[str] | None = None,
    value: float | None = None,
    spam: bool | None = None,
    lead_status: str | None = None,
) -> str:
    """Update an existing form submission: notes, tags, value, spam, lead status.

    Args:
        submission_id: CallRail form-submission id (prefix 'FOR...').
        account_id: Auto-resolves if omitted.
        note, tags, value, spam, lead_status: same semantics as `update_call`.

    Empty-string fields (e.g. `note=""`) are rejected to prevent accidental
    field-clearing — see `update_call` docstring.

    Length caps (rejected pre-network):
        - `note`: 4000 chars
        - `tags`: 100 entries max
    """
    ok, msg = _require_non_empty(submission_id, "submission_id")
    if not ok:
        return _err_msg(msg)
    ok, msg = _validate_id_shape(submission_id, "submission_id")
    if not ok:
        return _err_msg(msg)
    for value_, field in ((note, "note"), (lead_status, "lead_status")):
        if value_ is not None:
            ok, msg = _require_non_empty(value_, field)
            if not ok:
                return _err_msg(msg)
    if note is not None:
        ok, msg = _validate_length(note, "note", _MAX_NOTE_LEN)
        if not ok:
            return _err_msg(msg)
    if tags is not None and len(tags) > _MAX_TAGS_PER_REQUEST:
        return _err_msg(
            f"tags list length {len(tags)} exceeds max {_MAX_TAGS_PER_REQUEST}."
        )
    body: dict[str, Any] = {}
    if note is not None:
        body["note"] = note
    if tags is not None:
        body["tags"] = tags
    if value is not None:
        body["value"] = value
    if spam is not None:
        body["spam"] = spam
    if lead_status is not None:
        body["lead_status"] = lead_status
    if not body:
        return _err_msg("No fields supplied to update.")
    try:
        aid = client.resolve_account_id(account_id)
        return _ok(client.put(f"a/{aid}/form_submissions/{submission_id}.json", body))
    except CallRailError as e:
        return _err(e)


@mcp.tool()
def list_tags(
    account_id: str | None = None,
    company_id: str | None = None,
    per_page: int = 250,
    page: int = 1,
) -> str:
    """List all tags in the account, or filtered to one company."""
    try:
        aid = client.resolve_account_id(account_id)
        params: dict[str, Any] = {
            "per_page": _clamp_per_page(per_page),
            "page": max(1, page),
        }
        if company_id:
            params["company_id"] = company_id
        return _ok(client.get(f"a/{aid}/tags.json", params))
    except CallRailError as e:
        return _err(e)


@mcp.tool()
def create_tag(
    name: str,
    company_id: str,
    account_id: str | None = None,
    color: str | None = None,
) -> str:
    """Create a new tag scoped to one company.

    Args:
        name: Tag display name.
        company_id: Required — tags are per-company in CallRail.
        account_id: Auto-resolves if omitted.
        color: One of the 10 CallRail-supported colors:
            'red1', 'red2', 'orange1', 'yellow1', 'green1',
            'blue1', 'purple1', 'pink1', 'gray1', 'gray2'.
            If omitted, CallRail defaults to 'gray1'.
    """
    if color is not None and color not in VALID_TAG_COLORS:
        return _err_msg(
            f"Invalid color {color!r}. Must be one of: {', '.join(VALID_TAG_COLORS)}"
        )
    try:
        aid = client.resolve_account_id(account_id)
        body: dict[str, Any] = {"name": name, "company_id": company_id}
        if color:
            body["color"] = color
        return _ok(client.post(f"a/{aid}/tags.json", body))
    except CallRailError as e:
        return _err(e)


@mcp.tool()
def update_tag(
    tag_id: str,
    account_id: str | None = None,
    name: str | None = None,
    color: str | None = None,
) -> str:
    """Rename or recolor a tag.

    Args:
        tag_id: Numeric tag id.
        account_id: Auto-resolves if omitted.
        name: New display name.
        color: One of: 'red1', 'red2', 'orange1', 'yellow1', 'green1',
            'blue1', 'purple1', 'pink1', 'gray1', 'gray2'.
    """
    ok, msg = _require_non_empty(tag_id, "tag_id")
    if not ok:
        return _err_msg(msg)
    ok, msg = _validate_id_shape(tag_id, "tag_id")
    if not ok:
        return _err_msg(msg)
    # CallRail tag IDs are numeric — fail-fast on alphabetic / mixed inputs.
    if not _TAG_ID_RE.match(tag_id):
        return _err_msg(f"tag_id={tag_id!r} must be numeric (CallRail tag IDs are integers).")
    if name is not None:
        ok, msg = _require_non_empty(name, "name")
        if not ok:
            return _err_msg(msg)
    if color is not None and color not in VALID_TAG_COLORS:
        return _err_msg(
            f"Invalid color {color!r}. Must be one of: {', '.join(VALID_TAG_COLORS)}"
        )
    body: dict[str, Any] = {}
    if name is not None:
        body["name"] = name
    if color is not None:
        body["color"] = color
    if not body:
        return _err_msg("Supply name or color to update.")
    try:
        aid = client.resolve_account_id(account_id)
        return _ok(client.put(f"a/{aid}/tags/{tag_id}.json", body))
    except CallRailError as e:
        return _err(e)


@mcp.tool()
def delete_tag(tag_id: str, account_id: str | None = None) -> str:
    """Delete a tag. Removes it from any calls/form submissions it was on."""
    ok, msg = _require_non_empty(tag_id, "tag_id")
    if not ok:
        return _err_msg(msg)
    ok, msg = _validate_id_shape(tag_id, "tag_id")
    if not ok:
        return _err_msg(msg)
    if not _TAG_ID_RE.match(tag_id):
        return _err_msg(f"tag_id={tag_id!r} must be numeric (CallRail tag IDs are integers).")
    try:
        aid = client.resolve_account_id(account_id)
        client.delete(f"a/{aid}/tags/{tag_id}.json")
        return _ok({"deleted": True, "tag_id": tag_id})
    except CallRailError as e:
        return _err(e)


# ============================================================
# Aggregation / agency-billing tools (no CallRail-specific endpoint;
# we compose data from list_companies + list_trackers + list_calls).
# ============================================================

# CallRail Call Tracking Starter pricing (verified 2026-04-24 against the
# user's own billing dashboard at /settings/.../account/billing). Update
# these constants if you switch plans — they're public knowledge and not
# CallRail-side configurable for the integration.
PRICING_BASE_MONTHLY = 50.00
PRICING_BUNDLED_NUMBERS = 5
PRICING_BUNDLED_MINUTES = 250
PRICING_BUNDLED_TEXTS = 25
PRICING_PER_LOCAL_NUMBER = 3.00
PRICING_PER_TOLLFREE_NUMBER = 5.00
PRICING_PER_LOCAL_MINUTE = 0.05
# Note: toll-free minute pricing ($0.08/min) is real, but `usage_summary`
# doesn't yet differentiate per-call (would require looking up which tracker
# each call came from and whether that tracker has toll-free numbers).
# Currently all minutes are priced at PRICING_PER_LOCAL_MINUTE. Negligible
# error for accounts without toll-free numbers; documented in tool output.
PRICING_PER_TEXT = 0.05  # estimated; CallRail doesn't enumerate this


def _is_toll_free(number: str | None) -> bool:
    """True for North American toll-free prefixes (NANP only).

    Returns False for non-NANP numbers (international, shortcodes, etc.)
    rather than mis-classifying them as local — the cost model in
    `usage_summary` doesn't price non-NANP numbers correctly anyway.

    Extracts ASCII digits and looks at the first 11 starting with '1'.
    Handles common variations:
      - Bare E.164: '+18005551234' → toll-free
      - With extension: '+18005551234x77' → toll-free (trailing digits
        treated as extension, not number)
      - Human-formatted: '+1,800,555,1234' / '1-800-555-1234' →
        toll-free (separators stripped, first 11 digits used)
    """
    if not number:
        return False
    # Extract ASCII digits only (rejects Devanagari etc. by design).
    digits = "".join(c for c in number if c in "0123456789")
    # NANP toll-free: 11 digits, leading '1', specific area-code prefix.
    # If we have MORE than 11 digits, treat the trailing digits as an
    # extension and look at the first 11.
    if len(digits) >= 11 and digits[0] == "1":
        return digits[1:4] in {"800", "888", "877", "866", "855", "844", "833"}
    return False


@mcp.tool()
def usage_summary(
    account_id: str | None = None,
    days: int = 30,
    start_date: str | None = None,
    end_date: str | None = None,
) -> str:
    """Per-company cost-attribution summary for the current cycle.

    Aggregates active trackers + per-company call minutes and projects what
    each client is contributing to the agency's CallRail bill. Useful for:
      - Deciding which client to renegotiate / upsell / drop
      - Sanity-checking the upcoming invoice
      - Quarterly reviews

    Pricing assumes Call Tracking Starter ($50 base + 5 numbers + 250 mins
    bundled; $3/local number, $5/toll-free number, $0.05/local minute,
    $0.08/toll-free minute over bundle). Edit PRICING_* constants in
    server.py if you're on a different plan.

    Args:
        account_id: Auto-resolves if omitted.
        days: Lookback window in days (default 30 = roughly one cycle).
            Ignored if `start_date` provided.
        start_date: 'YYYY-MM-DD'.
        end_date: 'YYYY-MM-DD' (defaults to today).

    Returns:
        - `agency`: plan + totals + bundle utilization + cycle estimate
        - `by_company[]`: each company's minutes, active numbers, cost share
          (sorted by cost-share descending)
        - `biggest_cost_driver`: name of top company
        - `partial_failures[]`: per-company API errors. Each entry carries
          `partial_calls_before_failure`, `partial_minutes_before_failure`,
          `partial_local_numbers`, `partial_tollfree_numbers` so an under-
          reporting agency_total is observable, not silent.
        - `notes`: caveats about the cost model (toll-free minute pricing
          not yet differentiated; SMS not included).

    Cost shares sum exactly to `agency.estimated_cycle_total` via largest-
    remainder rounding.
    """
    # require_window=True rejects days<=0 with no start_date; without it
    # _date_window returns {} and we'd aggregate all-time call history,
    # blowing up the minute total and over-estimating cost wildly.
    ok, msg = _validate_window(days, start_date, end_date, require_window=True)
    if not ok:
        return _err_msg(msg)
    try:
        aid = client.resolve_account_id(account_id)
        # 1. Pull all companies (paginated). Most agencies have well under
        # 250 clients but MSPs can exceed it; previously truncated silently.
        companies = list(
            client.paginate(
                f"a/{aid}/companies.json",
                {"per_page": 250},
                items_key="companies",
            )
        )
        active_companies = [c for c in companies if c.get("status") == "active"]

        # 2. Pull active trackers per company. Numbers/pool_size aggregate
        # the count of provisioned numbers (CallRail bills per number, not
        # per tracker — a session pool of 4 = 4 numbers).
        # 3. Pull calls per company in the window for minute aggregation.
        # Both use paginate() — a busy client easily exceeds 250 calls in
        # a 30-day window (Malick at ~800 minutes was definitely truncated
        # before we wired up pagination).
        date_params = _date_window(days, start_date, end_date)
        per_company: list[dict[str, Any]] = []
        partial_failures: list[dict[str, Any]] = []

        for c in active_companies:
            cid = c.get("id")
            if not cid:
                continue
            # Initialize accumulators OUTSIDE the try so that a mid-loop
            # paginate failure can still surface the partial counts in
            # partial_failures (otherwise the user has no idea this
            # company contributed 800 minutes that vanished from the
            # agency_total).
            local_numbers = 0
            toll_free_numbers = 0
            total_seconds = 0
            call_count = 0
            try:
                # Active trackers + their tracking numbers (paginated).
                trackers = list(
                    client.paginate(
                        f"a/{aid}/trackers.json",
                        {"company_id": cid, "status": "active", "per_page": 250},
                        items_key="trackers",
                    )
                )
                for t in trackers:
                    for num in t.get("tracking_numbers") or []:
                        if _is_toll_free(num):
                            toll_free_numbers += 1
                        else:
                            local_numbers += 1
                # Calls + minutes in window (paginated). Critical: without
                # this, big clients silently truncate at 250 calls.
                call_params: dict[str, Any] = {"company_id": cid, "per_page": 250, **date_params}
                for call in client.paginate(
                    f"a/{aid}/calls.json", call_params, items_key="calls"
                ):
                    call_count += 1
                    # Robust int coercion — CallRail returns int but defend
                    # against future changes that ship strings/floats.
                    raw_duration = call.get("duration") or 0
                    try:
                        total_seconds += int(float(raw_duration))
                    except (TypeError, ValueError):
                        # Log + skip — surfaces malformed data without
                        # crashing the report.
                        logger.warning(
                            "usage_summary: skipping call with malformed "
                            "duration=%r in company %s", raw_duration, cid,
                        )
                minutes = round(total_seconds / 60.0, 1)
                per_company.append({
                    "company_id": cid,
                    "name": c.get("name", "(unnamed)"),
                    "active_local_numbers": local_numbers,
                    "active_tollfree_numbers": toll_free_numbers,
                    "active_total_numbers": local_numbers + toll_free_numbers,
                    "minutes_in_window": minutes,
                    "calls_in_window": call_count,
                })
            except CallRailError as e:
                # Per-company partial-success: don't let one company's
                # transient failure poison the whole agency report. Surface
                # whatever we accumulated before the failure so the user
                # can see the under-count was real and how big it was.
                partial_failures.append({
                    "company_id": cid,
                    "company_name": c.get("name", "(unnamed)"),
                    "error": str(e),
                    "status": e.status,
                    "partial_calls_before_failure": call_count,
                    "partial_minutes_before_failure": round(total_seconds / 60.0, 1),
                    "partial_local_numbers": local_numbers,
                    "partial_tollfree_numbers": toll_free_numbers,
                })
                continue

        # 4. Compute cost shares. We attribute the bundle (5 numbers, 250
        # minutes) proportionally to each company's contribution to the
        # agency total — biggest users absorb more of the "free tier" but
        # also more of the overage.
        total_local = sum(c["active_local_numbers"] for c in per_company)
        total_tollfree = sum(c["active_tollfree_numbers"] for c in per_company)
        total_minutes = sum(c["minutes_in_window"] for c in per_company)
        # Number-overage cost (charge only for numbers beyond bundle).
        local_overage_count = max(0, total_local - PRICING_BUNDLED_NUMBERS)
        # Toll-free numbers don't share the local-number bundle in
        # CallRail's pricing — every TF is overage.
        local_overage_cost = local_overage_count * PRICING_PER_LOCAL_NUMBER
        tollfree_overage_cost = total_tollfree * PRICING_PER_TOLLFREE_NUMBER
        # Minute-overage cost.
        minute_overage_count = max(0.0, total_minutes - PRICING_BUNDLED_MINUTES)
        minute_overage_cost = round(minute_overage_count * PRICING_PER_LOCAL_MINUTE, 2)
        agency_total = round(
            PRICING_BASE_MONTHLY + local_overage_cost + tollfree_overage_cost + minute_overage_cost,
            2,
        )
        # Per-company attribution: split the bill proportionally by
        # (numbers + minutes) contribution. Pure proportionality — not a
        # perfect cost model (the bundle "rebates" larger users more) but
        # it's a reasonable starting point. ALWAYS attribute base cost so
        # `sum(per-company costs) == agency_total` even when minutes==0.
        for company_row in per_company:
            # Per-bucket overage shares.
            nums_share = (
                company_row["active_local_numbers"] / total_local
                if total_local > 0 else 0
            )
            tf_share = (
                company_row["active_tollfree_numbers"] / total_tollfree
                if total_tollfree > 0 else 0
            )
            mins_share = (
                company_row["minutes_in_window"] / total_minutes
                if total_minutes > 0 else 0
            )
            cost = (
                nums_share * local_overage_cost
                + tf_share * tollfree_overage_cost
                + mins_share * minute_overage_cost
            )
            # Base attribution: prefer (numbers+minutes) blended share.
            # If neither numbers nor minutes exist on any company, fall
            # back to even split — the base is owed regardless.
            denom = total_local + total_tollfree + total_minutes
            if denom > 0:
                resource_share = (
                    (company_row["active_total_numbers"] + company_row["minutes_in_window"])
                    / denom
                )
            elif per_company:
                resource_share = 1.0 / len(per_company)
            else:
                resource_share = 0.0
            cost += resource_share * PRICING_BASE_MONTHLY
            # Store unrounded for largest-remainder reconciliation pass below.
            company_row["_cost_unrounded"] = cost
        # Largest-remainder rounding: round each share to cents, then
        # distribute the rounding residual (typically ±$0.01–0.05) to the
        # row with the largest fractional remainder. Ensures
        # sum(per-company costs) == agency_total exactly, matching what
        # a CallRail invoice would show.
        if per_company:
            target_cents = round(agency_total * 100)
            rounded_cents = [round(r["_cost_unrounded"] * 100) for r in per_company]
            residual = target_cents - sum(rounded_cents)
            if residual:
                # Sort by largest fractional remainder; adjust one cent at
                # a time until residual is zero. Positive residual = bump
                # up; negative = bump down. Cycle through `remainders` if
                # residual exceeds row count (defensive — current pricing
                # math bounds residual to ~N cents, but float drift on
                # huge accounts could exceed it).
                remainders = sorted(
                    range(len(per_company)),
                    key=lambda i: abs(per_company[i]["_cost_unrounded"] * 100 - rounded_cents[i]),
                    reverse=True,
                )
                step = 1 if residual > 0 else -1
                n = len(remainders)
                for i in range(abs(residual)):
                    rounded_cents[remainders[i % n]] += step
            for r, cents in zip(per_company, rounded_cents, strict=True):
                r["estimated_cost_share"] = round(cents / 100.0, 2)
                del r["_cost_unrounded"]
        per_company.sort(key=lambda r: r.get("estimated_cost_share", 0), reverse=True)

        return _ok({
            "window": date_params or {"days": days},
            "agency": {
                "plan": "Call Tracking Starter",
                "base_monthly": PRICING_BASE_MONTHLY,
                "active_local_numbers": total_local,
                "active_tollfree_numbers": total_tollfree,
                "active_total_numbers": total_local + total_tollfree,
                "bundled_numbers": PRICING_BUNDLED_NUMBERS,
                "minutes_used": round(total_minutes, 1),
                "bundled_minutes": PRICING_BUNDLED_MINUTES,
                "local_overage_cost": round(local_overage_cost, 2),
                "tollfree_overage_cost": round(tollfree_overage_cost, 2),
                "minute_overage_cost": round(minute_overage_cost, 2),
                "estimated_cycle_total": agency_total,
            },
            "by_company": per_company,
            "biggest_cost_driver": (
                per_company[0]["name"] if per_company else None
            ),
            "partial_failures": partial_failures,
            "notes": [
                "Number counts are CURRENT active counts (snapshot), not historical.",
                "Minutes are aggregated over the requested window via paginated "
                "calls (no truncation).",
                "Per-company attribution splits the bill proportionally to "
                "(numbers + minutes) contribution; a perfectly fair model would "
                "credit larger users for absorbing more of the bundle.",
                "Toll-free minute pricing ($0.08 vs $0.05 local) is NOT yet "
                "differentiated — all minutes priced at local rate. Negligible "
                "for accounts without toll-free numbers.",
                "SMS overage is not included in the cost estimate.",
                "Partial failures (per-company API errors) appear in "
                "`partial_failures`; companies in that list are excluded from "
                "totals, so the agency_total may under-estimate by the failed "
                "companies' share.",
                "Toll-free / local-number / minute thresholds and prices are "
                "Starter plan rates as of 2026-04. Edit PRICING_* in server.py "
                "if you switch plans.",
            ],
        })
    except CallRailError as e:
        return _err(e)


# CallRail's Google Ads integration default minimum call duration to
# upload a call as a conversion. Verified empirically by the user's own
# Google Ads conversion-action settings (60 seconds across all phone-call
# conversion actions in the Alan Construction account, 2026-04-24).
GOOGLE_ADS_DEFAULT_MIN_CALL_DURATION_SECONDS = 60


@mcp.tool()
def call_eligibility_check(
    call_id: str,
    google_ads_min_duration_seconds: int = GOOGLE_ADS_DEFAULT_MIN_CALL_DURATION_SECONDS,
    account_id: str | None = None,
) -> str:
    """Audit whether a specific call is/was eligible to count as a Google Ads
    conversion. Useful for "where did my conversion go" debugging.

    Checks:
      1. Did the call have a `gclid`? (Required for CallRail to upload to
         Google Ads as a UPLOAD_CLICKS Phone Call conversion.)
      2. Was the call answered? (Most integrations skip unanswered.)
      3. Did duration meet Google Ads' minimum? (Default 60s; configurable
         per conversion action in Google Ads UI.)
      4. Is the call from a Google source? Detection uses CallRail's
         internal `source` slug (e.g. `google_paid`, `google_my_business`)
         + presence of gclid — NOT the user-editable `source_name` display
         string (which can mislead, e.g. "Bing Ads (Google legacy import)"
         would substring-match as Google but is clearly Bing).

    Args:
        call_id: 'CAL...' id.
        google_ads_min_duration_seconds: Threshold to check duration against.
            Defaults to 60 (Google's UI default). Override if you've lowered
            it on a specific conversion action.
        account_id: Auto-resolves if omitted.

    Returns: Verdict + each criterion's pass/fail + suggested remediation
    when eligibility fails.
    """
    ok, msg = _require_non_empty(call_id, "call_id")
    if not ok:
        return _err_msg(msg)
    ok, msg = _validate_id_shape(call_id, "call_id", prefix="CAL")
    if not ok:
        return _err_msg(msg)
    if google_ads_min_duration_seconds < 0:
        return _err_msg(
            f"google_ads_min_duration_seconds={google_ads_min_duration_seconds} "
            f"must be non-negative."
        )
    try:
        aid = client.resolve_account_id(account_id)
        call_data = client.get(
            f"a/{aid}/calls/{call_id}.json",
            {
                "fields": (
                    # `source` is the CallRail-internal slug (e.g. 'google_paid',
                    # 'bing_paid'). More reliable than `source_name` for source
                    # detection — user-facing tracker names can mislead
                    # (e.g. "Bing Ads (migrated from Google)" would substring-
                    # match as Google on source_name but is clearly Bing).
                    "gclid,utm_source,utm_medium,duration,answered,"
                    "source,source_name,first_call,landing_page_url"
                )
            },
        )
        if not isinstance(call_data, dict):
            return _err_msg(f"Unexpected response shape from CallRail: {type(call_data).__name__}")

        gclid = call_data.get("gclid")
        utm_source = (call_data.get("utm_source") or "").lower()
        source_slug = (call_data.get("source") or "").lower()
        source_name = (call_data.get("source_name") or "").lower()
        # Robust int coercion — CallRail returns int but defend against
        # future schema changes (string/float).
        raw_duration = call_data.get("duration") or 0
        try:
            duration = int(float(raw_duration))
        except (TypeError, ValueError):
            duration = 0
        # `answered` may arrive as bool, "true"/"false" string, or int.
        raw_answered = call_data.get("answered")
        if isinstance(raw_answered, str):
            answered = raw_answered.strip().lower() in ("true", "yes", "1")
        else:
            answered = bool(raw_answered)

        # Heuristic: Google source = utm_source=google (GMB + paid) OR
        # CallRail internal `source` slug starts with 'google_' (e.g.
        # 'google_paid', 'google_organic', 'google_my_business') OR has
        # gclid. The gclid signal is honest: "gclid" stands for Google
        # Click ID — it can only be minted by Google Ads. So presence
        # proves Google origin even when CallRail's source_name is
        # generic (e.g. "Website Pool" for a DNI session that happens
        # to have served a Google Ads visitor).
        #
        # We deliberately use `source` (CallRail's internal slug) NOT
        # `source_name` (user-editable display string) — a tracker named
        # "Bing Ads (Google legacy import)" would false-positive on
        # source_name substring match but is clearly Bing.
        is_google = (
            utm_source == "google"
            or source_slug == "google"
            or source_slug.startswith("google_")
            or bool(gclid)
        )

        checks = {
            "has_gclid": bool(gclid),
            "answered": answered,
            "duration_meets_threshold": duration >= google_ads_min_duration_seconds,
            "is_google_source": is_google,
        }
        eligible = all(checks.values())

        # Targeted remediation suggestions per failed check.
        reasons: list[str] = []
        if not checks["has_gclid"]:
            reasons.append(
                "No gclid captured — call cannot be uploaded as Google Ads "
                "conversion. Likely from SERP call-extension (Google tracks "
                "those natively as 'Calls from ads') or from a non-Google "
                "source (GMB, organic, Bing)."
            )
        if not checks["answered"]:
            reasons.append(
                "Call was not answered. CallRail typically only uploads "
                "answered calls."
            )
        if not checks["duration_meets_threshold"]:
            reasons.append(
                f"Call duration {duration}s is below the Google Ads "
                f"threshold of {google_ads_min_duration_seconds}s. Either "
                f"shorten the threshold in Google Ads conversion-action "
                f"settings, or accept that quick calls don't count as leads."
            )
        if not checks["is_google_source"]:
            reasons.append(
                f"Call source ({source_name!r}, utm={utm_source!r}) doesn't "
                f"appear to be Google. Bing calls go to Microsoft Ads, "
                f"organic / GMB don't generate Google Ads conversions."
            )

        return _ok({
            "call_id": call_id,
            "google_ads_eligible": eligible,
            "checks": checks,
            "rejection_reasons": reasons,
            "call_facts": {
                "duration_seconds": duration,
                "answered": answered,
                "gclid": gclid,
                "utm_source": call_data.get("utm_source"),
                "source": call_data.get("source"),
                "source_name": call_data.get("source_name"),
                "landing_page_url": call_data.get("landing_page_url"),
                "first_call": call_data.get("first_call"),
            },
            "threshold_used": google_ads_min_duration_seconds,
            "notes": [
                "This tool checks LIKELY eligibility based on CallRail's "
                "default integration behavior + Google Ads' default minimum "
                "duration. Your actual configuration may differ — check "
                "CallRail Integrations > Google Ads > Integration Filters.",
                "SERP call-extension calls (where the user taps the phone "
                "icon directly in a Google ad) are tracked by Google natively "
                "as AD_CALL conversions, NOT through CallRail upload — so "
                "lacking a gclid here doesn't mean Google didn't count them.",
            ],
        })
    except CallRailError as e:
        return _err(e)


def main() -> None:
    """CLI entry point for stdio transport.

    Honors CALLRAIL_LOG_LEVEL (default: WARNING). Library callers who
    `import callrail_mcp.server` are unaffected by this — only the
    standalone server configures logging.
    """
    logging.basicConfig(
        level=os.environ.get("CALLRAIL_LOG_LEVEL", "WARNING").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    mcp.run()


if __name__ == "__main__":
    main()
