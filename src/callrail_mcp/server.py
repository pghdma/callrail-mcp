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
from datetime import datetime, timedelta, timezone
from typing import Any

from mcp.server.fastmcp import FastMCP

from .client import MAX_PER_PAGE, CallRailClient, CallRailError

logging.basicConfig(
    level=os.environ.get("CALLRAIL_LOG_LEVEL", "WARNING").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


def _build_client() -> CallRailClient:
    base_url = os.environ.get("CALLRAIL_BASE_URL")
    if base_url:
        return CallRailClient(base_url=base_url)
    return CallRailClient()


client = _build_client()
mcp = FastMCP("callrail-mcp")


# ---- Shared helpers ----

def _date_window(days: int | None, start_date: str | None, end_date: str | None) -> dict[str, str]:
    """Produce start_date/end_date query params (YYYY-MM-DD)."""
    out: dict[str, str] = {}
    if start_date:
        out["start_date"] = start_date
    if end_date:
        out["end_date"] = end_date
    if days and "start_date" not in out:
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=days)
        out["start_date"] = start.isoformat()
        out["end_date"] = end.isoformat()
    return out


def _ok(data: Any) -> str:
    return json.dumps(data, indent=2, default=str)


def _err(e: CallRailError) -> str:
    return json.dumps(
        {"error": True, "status": e.status, "message": str(e), "body": e.body},
        indent=2,
    )


def _digits_only(s: str) -> str:
    return "".join(ch for ch in s if ch.isdigit())


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
def list_companies(account_id: str | None = None, per_page: int = 250) -> str:
    """List companies (client businesses) under a CallRail account.

    Args:
        account_id: CallRail account ID. Auto-resolves if omitted.
        per_page: Page size (max 250).
    """
    try:
        aid = client.resolve_account_id(account_id)
        return _ok(client.get(f"a/{aid}/companies.json", {"per_page": min(per_page, MAX_PER_PAGE)}))
    except CallRailError as e:
        return _err(e)


@mcp.tool()
def list_trackers(
    account_id: str | None = None,
    company_id: str | None = None,
    per_page: int = 250,
    page: int = 1,
) -> str:
    """List tracking phone numbers (trackers). Each tracker maps a pool of
    phone numbers to a traffic source (Google Ads, Organic, Direct, etc.).
    """
    try:
        aid = client.resolve_account_id(account_id)
        params: dict[str, Any] = {"per_page": min(per_page, MAX_PER_PAGE), "page": page}
        if company_id:
            params["company_id"] = company_id
        return _ok(client.get(f"a/{aid}/trackers.json", params))
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
    try:
        aid = client.resolve_account_id(account_id)
        params: dict[str, Any] = {"per_page": min(per_page, MAX_PER_PAGE), "page": page}
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
    try:
        aid = client.resolve_account_id(account_id)
        params: dict[str, Any] = {"per_page": min(per_page, MAX_PER_PAGE), "page": page}
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
    try:
        aid = client.resolve_account_id(account_id)
        params: dict[str, Any] = {"per_page": min(per_page, MAX_PER_PAGE), "page": page}
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
        phone_number: Any format — will be normalized.
        account_id: Auto-resolves.
        company_id: Optional company filter.
        days: Lookback window (default 90).
    """
    try:
        aid = client.resolve_account_id(account_id)
        digits = _digits_only(phone_number)
        if len(digits) > 10:
            digits = digits[-10:]
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
    value: float | None = None,
    spam: bool | None = None,
    customer_name: str | None = None,
    lead_status: str | None = None,
) -> str:
    """Update an existing call: notes, tags, value, spam flag, customer name, lead status.

    Args:
        call_id: 'CAL...' id of the call to update.
        account_id: Auto-resolves if omitted.
        note: Replace the call's note text.
        tags: REPLACE the call's tag list with this set of tag names.
              (Use `add_call_tags`/`remove_call_tags` for additive changes.)
        value: Set the call's monetary value.
        spam: True to mark as spam, False to unmark.
        customer_name: Override the auto-detected caller name.
        lead_status: e.g. 'good_lead', 'not_a_lead', 'unknown'.
    """
    try:
        aid = client.resolve_account_id(account_id)
        body: dict[str, Any] = {}
        if note is not None:
            body["note"] = note
        if tags is not None:
            body["tags"] = tags
        if value is not None:
            body["value"] = value
        if spam is not None:
            body["spam"] = spam
        if customer_name is not None:
            body["customer_name"] = customer_name
        if lead_status is not None:
            body["lead_status"] = lead_status
        if not body:
            return _ok({"error": True, "message": "No fields supplied to update."})
        return _ok(client.put(f"a/{aid}/calls/{call_id}.json", body))
    except CallRailError as e:
        return _err(e)


@mcp.tool()
def add_call_tags(call_id: str, tags: list[str], account_id: str | None = None) -> str:
    """Append tags to a call without replacing existing ones."""
    try:
        aid = client.resolve_account_id(account_id)
        existing = client.get(f"a/{aid}/calls/{call_id}.json", {"fields": "tags"}).get("tags") or []
        existing_names = [t.get("name", t) if isinstance(t, dict) else t for t in existing]
        merged = list(dict.fromkeys(existing_names + tags))
        return _ok(client.put(f"a/{aid}/calls/{call_id}.json", {"tags": merged}))
    except CallRailError as e:
        return _err(e)


@mcp.tool()
def remove_call_tags(call_id: str, tags: list[str], account_id: str | None = None) -> str:
    """Remove specific tags from a call (case-sensitive on tag name)."""
    try:
        aid = client.resolve_account_id(account_id)
        existing = client.get(f"a/{aid}/calls/{call_id}.json", {"fields": "tags"}).get("tags") or []
        existing_names = [t.get("name", t) if isinstance(t, dict) else t for t in existing]
        kept = [t for t in existing_names if t not in tags]
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
    try:
        aid = client.resolve_account_id(account_id)
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
            return _ok({"error": True, "message": "No fields supplied to update."})
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
        color: Optional CallRail color name
            (e.g. 'gray1','red','orange','green','blue','purple','pink',
            'brown','dark blue','dark green').
    """
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
    """Rename or recolor a tag."""
    try:
        aid = client.resolve_account_id(account_id)
        body: dict[str, Any] = {}
        if name is not None:
            body["name"] = name
        if color is not None:
            body["color"] = color
        if not body:
            return _ok({"error": True, "message": "Supply name or color to update."})
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
    """CLI entry point for stdio transport."""
    mcp.run()


if __name__ == "__main__":
    main()
