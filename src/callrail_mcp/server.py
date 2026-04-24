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

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


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
    """
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
    days: int | None, start_date: str | None, end_date: str | None
) -> tuple[bool, str]:
    """Cross-field validation for date windows used by listing tools."""
    if days is not None and days < 0:
        return False, f"days={days} is negative."
    ok, msg = _validate_date(start_date or "", "start_date")
    if not ok:
        return False, msg
    ok, msg = _validate_date(end_date or "", "end_date")
    if not ok:
        return False, msg
    if start_date and end_date and start_date > end_date:
        return False, f"end_date {end_date!r} is before start_date {start_date!r}."
    return True, ""


def _clamp_per_page(per_page: int) -> int:
    """Clamp per_page to [1, MAX_PER_PAGE]. Silently corrects nonsense input."""
    if per_page is None or per_page < 1:
        return 1
    return min(per_page, MAX_PER_PAGE)


def _ok(data: Any) -> str:
    return json.dumps(data, indent=2, default=str)


def _err(e: CallRailError) -> str:
    return json.dumps(
        {"error": True, "status": e.status, "message": str(e), "body": e.body},
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
_VALID_TRACKER_STATUSES: tuple[str, ...] = ("active", "disabled")
_AREA_CODE_RE = re.compile(r"^\d{3}$")
# Loose E.164-ish: optional + then 10-15 digits. Accepts +14125551234, 14125551234.
_PHONE_RE = re.compile(r"^\+?\d{10,15}$")
_TRACKER_ID_RE = re.compile(r"^TRK[A-Za-z0-9_-]+$")
_COMPANY_ID_RE = re.compile(r"^COM[A-Za-z0-9_-]+$")


def _require_non_empty(value: str | None, field_name: str) -> tuple[bool, str]:
    """True only if value is a non-empty, non-whitespace string."""
    if value is None or not str(value).strip():
        return False, f"{field_name} is required and cannot be empty."
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
# Discovered empirically by exhaustive testing — CallRail's docs do not enumerate.
# Any other source.type value returns 400 "Source Unknown tracking source type".
VALID_SOURCE_TYPES: tuple[str, ...] = (
    "all",
    "direct",
    "offline",
    "google_my_business",
    "google_ad_extension",  # Google Ads call extensions
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
    """
    ok, msg = _validate_window(days, start_date, end_date)
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
            duration_total += c.get("duration") or 0
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
    try:
        aid = client.resolve_account_id(account_id)
        return _ok(client.get(f"a/{aid}/calls/{call_id}/recording.json"))
    except CallRailError as e:
        return _err(e)


@mcp.tool()
def get_call_transcript(call_id: str, account_id: str | None = None) -> str:
    """Get the AI transcript for a call (requires CallRail Conversation Intelligence)."""
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
    ok, msg = _validate_window(days, None, None)
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

        matches: list[dict[str, Any]] = []
        for c in client.paginate(f"a/{aid}/calls.json", params, items_key="calls", max_pages=50):
            num = _digits_only(c.get("customer_phone_number") or "")
            if num.endswith(digits):
                matches.append(c)
        return _ok({"query": phone_number, "match_count": len(matches), "calls": matches})
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
    """
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
    """Strip whitespace, drop empties, dedupe in original order."""
    if not tags:
        return []
    seen: dict[str, None] = {}
    for t in tags:
        if not isinstance(t, str):
            continue
        s = t.strip()
        if s:
            seen.setdefault(s, None)
    return list(seen.keys())


@mcp.tool()
def add_call_tags(call_id: str, tags: list[str], account_id: str | None = None) -> str:
    """Append tags to a call without replacing existing ones.

    Empty/whitespace-only tag names are silently filtered out so that a
    request like `add_call_tags(['', 'lead'])` won't 400 — only `'lead'`
    is sent. Returns an error if no valid tags remain after cleaning.
    """
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
    """
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
        params: dict[str, Any] = {"per_page": min(per_page, MAX_PER_PAGE), "page": page}
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
    try:
        aid = client.resolve_account_id(account_id)
        client.delete(f"a/{aid}/tags/{tag_id}.json")
        return _ok({"deleted": True, "tag_id": tag_id})
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
