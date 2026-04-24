"""
CallRail API v3 HTTP client.

Thin wrapper around the REST API with:
- Token auth via header
- Automatic retry on 429 / 5xx / network errors with exponential backoff
- Request timeouts
- Transparent pagination helper
- URL-encoded path segments (resists ID-based path traversal)
- Redirects disabled (no exfil to attacker-controlled URLs)
- Context-manager friendly (call .close() or `with`)

API docs: https://apidocs.callrail.com/
"""
from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterator
from pathlib import Path
from types import TracebackType
from typing import Any
from urllib.parse import quote, urljoin

import requests
from requests import Response
from requests.exceptions import (
    ChunkedEncodingError,
    RequestException,
    Timeout,
)
from requests.exceptions import (
    ConnectionError as RequestsConnectionError,
)

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.callrail.com/v3/"
DEFAULT_TIMEOUT: tuple[float, float] = (5.0, 20.0)  # (connect, read)
DEFAULT_MAX_RETRIES = 3
DEFAULT_PER_PAGE = 100
MAX_PER_PAGE = 250
MAX_RETRY_DELAY_SECONDS = 60.0
RETRYABLE_NETWORK_ERRORS: tuple[type[BaseException], ...] = (
    RequestsConnectionError,
    Timeout,
    ChunkedEncodingError,
)

# Discovered empirically by exhaustive testing against the v3 tags endpoint
# (the docs don't enumerate this). Any other value returns
# 400 "Color is not included in the list".
VALID_TAG_COLORS: tuple[str, ...] = (
    "red1", "red2",
    "orange1",
    "yellow1",
    "green1",
    "blue1",
    "purple1",
    "pink1",
    "gray1", "gray2",
)


class CallRailError(RuntimeError):
    """Raised when the CallRail API returns an error we cannot retry past."""

    def __init__(self, message: str, status: int | None = None, body: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.body = body


def _safe_path(path: str) -> str:
    """URL-encode each path segment so an untrusted ID containing slashes,
    encoded slashes, control chars, or whitespace cannot escape the API
    base path. Dot-segments (`.` and `..`) are explicitly rejected because
    `quote()` leaves them alone (dots are unreserved in RFC 3986) and
    `urljoin` would then resolve them, defeating the whole purpose.

    >>> _safe_path("a/ACC1/calls/CAL_normal.json")
    'a/ACC1/calls/CAL_normal.json'
    >>> _safe_path("a/ACC1/calls/../../etc/passwd.json")
    Traceback (most recent call last):
    ...
    callrail_mcp.client.CallRailError: Path segment '..' is not allowed (would escape the API base path).
    """
    if not path:
        return ""
    # Strip leading slashes so urljoin keeps the base_url path component.
    stripped = path.lstrip("/")
    if not stripped:
        return ""
    out: list[str] = []
    for segment in stripped.split("/"):
        if segment in ("", ".", ".."):
            # An empty segment ('//' in input) or dot-segment would let
            # urljoin walk out of the API path. Refuse.
            raise CallRailError(
                f"Path segment {segment!r} is not allowed (would escape the API base path)."
            )
        # Reject control characters / NULs that some servers / proxies handle weirdly.
        if any(ord(c) < 0x20 or ord(c) == 0x7f for c in segment):
            raise CallRailError(
                f"Path segment {segment!r} contains a control character."
            )
        out.append(quote(segment, safe=""))
    return "/".join(out)


def _load_api_key() -> str:
    """Load API key from env var or ~/.config/callrail/api-key.txt."""
    key = os.environ.get("CALLRAIL_API_KEY", "").strip()
    if key:
        return key
    key_path = Path(os.environ.get("CALLRAIL_API_KEY_FILE", "")).expanduser() if os.environ.get("CALLRAIL_API_KEY_FILE") else (
        Path.home() / ".config" / "callrail" / "api-key.txt"
    )
    if key_path.exists():
        return key_path.read_text().strip()
    raise CallRailError(
        "No CallRail API key found. Set CALLRAIL_API_KEY env var or place the "
        f"key in {key_path} (mode 600). Get a key at: "
        "https://app.callrail.com/settings/api-keys"
    )


class CallRailClient:
    """HTTP client for the CallRail REST API v3.

    Args:
        api_key: CallRail API key. Falls back to `CALLRAIL_API_KEY` env var
            or ~/.config/callrail/api-key.txt.
        base_url: Override the API base URL (useful for testing).
        timeout: Per-request timeout in seconds.
        max_retries: Max retries on 429 / 5xx before raising.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float | tuple[float, float] = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        # Strip leading/trailing whitespace and any embedded newlines that
        # commonly come from copy-paste mistakes. Without this, requests
        # raises a cryptic "Invalid leading whitespace ... in header value".
        raw = api_key if api_key is not None else _load_api_key()
        self.api_key = raw.strip().replace("\n", "").replace("\r", "")
        if not self.api_key:
            raise CallRailError("CallRail API key is empty after stripping whitespace.")
        self.base_url = base_url if base_url.endswith("/") else base_url + "/"
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()
        # Disable redirects: the CallRail API never legitimately redirects, and
        # following one could exfiltrate the Authorization header to whatever
        # host the redirect points to.
        self.session.max_redirects = 0
        self.session.headers.update(
            {
                "Authorization": f"Token token={self.api_key}",
                "Accept": "application/json",
                "User-Agent": "callrail-mcp/0.2.4 (+https://github.com/pghdma/callrail-mcp)",
            }
        )

    # ---- context-manager / cleanup ----

    def close(self) -> None:
        """Close the underlying HTTP session and release sockets."""
        self.session.close()

    def __enter__(self) -> CallRailClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # ---- low-level ----

    @staticmethod
    def _parse_retry_after(value: str | None, attempt: int) -> float:
        """RFC 7231: Retry-After can be seconds-int OR HTTP-date. Fall back
        to exponential backoff if neither parses. Cap at MAX_RETRY_DELAY_SECONDS
        so a misbehaving server can't pin us for hours."""
        default = float(2 ** attempt)
        if not value:
            return min(default, MAX_RETRY_DELAY_SECONDS)
        try:
            return min(float(value), MAX_RETRY_DELAY_SECONDS)
        except (TypeError, ValueError):
            pass
        try:
            from datetime import datetime, timezone
            from email.utils import parsedate_to_datetime
            target = parsedate_to_datetime(value)
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            secs = max(0.0, (target - datetime.now(timezone.utc)).total_seconds())
            return min(secs, MAX_RETRY_DELAY_SECONDS)
        except (TypeError, ValueError, IndexError):
            return min(default, MAX_RETRY_DELAY_SECONDS)

    def _request(self, method: str, path: str, **kwargs: Any) -> Response:
        """Do one HTTP request with retry/backoff on 429, 5xx, and transient
        network errors. Path is URL-encoded segment-by-segment to resist
        path traversal via untrusted IDs."""
        url = urljoin(self.base_url, _safe_path(path))
        kwargs.setdefault("timeout", self.timeout)
        # Defense in depth — the session already disables redirects.
        kwargs.setdefault("allow_redirects", False)

        last_exc: BaseException | None = None
        resp: Response | None = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self.session.request(method, url, **kwargs)
            except RETRYABLE_NETWORK_ERRORS as e:
                last_exc = e
                if attempt < self.max_retries:
                    delay = min(float(2 ** attempt), MAX_RETRY_DELAY_SECONDS)
                    logger.warning(
                        "CallRail %s for %s; retrying in %.1fs (attempt %d/%d)",
                        type(e).__name__, url, delay, attempt + 1, self.max_retries + 1,
                    )
                    time.sleep(delay)
                    continue
                raise CallRailError(
                    f"Network error talking to CallRail after {attempt + 1} attempts: "
                    f"{type(e).__name__}: {e}"
                ) from e
            except RequestException as e:
                # Non-retryable requests-level error (e.g. SSL, invalid URL)
                raise CallRailError(
                    f"Request to CallRail failed: {type(e).__name__}: {e}"
                ) from e

            # CallRail responds 429 with Retry-After on rate limit (60 req/min/account)
            if resp.status_code == 429 and attempt < self.max_retries:
                delay = self._parse_retry_after(resp.headers.get("Retry-After"), attempt)
                logger.warning("CallRail 429; sleeping %.1fs (attempt %d)", delay, attempt + 1)
                time.sleep(delay)
                continue
            if 500 <= resp.status_code < 600 and attempt < self.max_retries:
                delay = min(float(2 ** attempt), MAX_RETRY_DELAY_SECONDS)
                logger.warning("CallRail %d; retrying in %.1fs (attempt %d)", resp.status_code, delay, attempt + 1)
                time.sleep(delay)
                continue
            return resp

        # Should be unreachable; keeps type checker happy.
        if resp is not None:
            return resp
        assert last_exc is not None
        raise CallRailError(f"Exhausted retries: {last_exc}") from last_exc

    def _parse(self, resp: Response, method: str, path: str) -> dict[str, Any]:
        """Validate response status and parse JSON. Raises CallRailError on non-2xx,
        non-JSON, non-object payloads, or unexpected redirects."""
        if 300 <= resp.status_code < 400:
            raise CallRailError(
                f"Unexpected redirect from CallRail ({resp.status_code} for "
                f"{method} {path}). Redirects are disabled to prevent token "
                f"leakage to attacker-controlled hosts.",
                status=resp.status_code,
                body=(resp.headers.get("Location", "") or resp.text[:500]),
            )
        if resp.status_code >= 400:
            raise CallRailError(
                f"CallRail API returned {resp.status_code} for {method} {path}",
                status=resp.status_code,
                body=resp.text[:2000],
            )
        if resp.status_code == 204 or not resp.content:
            return {}
        try:
            data: Any = resp.json()
        except ValueError as e:
            raise CallRailError(
                f"Non-JSON response from {method} {path}: {e}",
                status=resp.status_code,
                body=resp.text[:500],
            ) from e
        if not isinstance(data, dict):
            raise CallRailError(
                f"Expected JSON object from {method} {path}, got "
                f"{type(data).__name__}.",
                status=resp.status_code,
                body=str(data)[:500],
            )
        return data

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET `path` and return parsed JSON. Raises CallRailError on non-2xx."""
        resp = self._request("GET", path, params=params or {})
        return self._parse(resp, "GET", path)

    def post(self, path: str, body: dict[str, Any] | None = None, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """POST `path` with JSON body. Returns parsed JSON."""
        resp = self._request("POST", path, json=body or {}, params=params or {})
        return self._parse(resp, "POST", path)

    def put(self, path: str, body: dict[str, Any] | None = None, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """PUT `path` with JSON body. Used for partial updates per CallRail's REST conventions."""
        resp = self._request("PUT", path, json=body or {}, params=params or {})
        return self._parse(resp, "PUT", path)

    def delete(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """DELETE `path`. CallRail returns 204 on success."""
        resp = self._request("DELETE", path, params=params or {})
        return self._parse(resp, "DELETE", path)

    # ---- mid-level helpers ----

    def resolve_account_id(self, account_id: str | None = None) -> str:
        """Return `account_id` if given, else fetch the first accessible account."""
        if account_id:
            return account_id
        data = self.get("a.json")
        accounts = data.get("accounts") or data.get("agencies") or []
        if not accounts:
            raise CallRailError("No CallRail accounts accessible with this API key")
        return accounts[0]["id"]

    def paginate(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        items_key: str | None = None,
        max_pages: int = 50,
    ) -> Iterator[dict[str, Any]]:
        """Yield items across pages. Stops at `max_pages` to avoid runaways.

        Args:
            path: API path (e.g. `a/{id}/calls.json`).
            params: Query params. Auto-fills page and per_page.
            items_key: Which top-level array to yield from. If None, auto-detects.
            max_pages: Safety cap.
        """
        params = dict(params or {})
        # Clamp per_page same as the listing tools so paginate() is safe to
        # call directly with caller-supplied values.
        raw_pp = params.get("per_page", DEFAULT_PER_PAGE)
        try:
            pp = int(raw_pp)
        except (TypeError, ValueError):
            pp = DEFAULT_PER_PAGE
        params["per_page"] = max(1, min(pp, MAX_PER_PAGE))
        page = 1
        while page <= max_pages:
            params["page"] = page
            data = self.get(path, params)
            key = items_key
            if key is None:
                # Heuristic: find the first list-valued key
                for k, v in data.items():
                    if isinstance(v, list):
                        key = k
                        break
            if not key or not data.get(key):
                break
            yield from data[key]
            total_pages = data.get("total_pages", 1)
            if page >= total_pages:
                break
            page += 1
        else:
            # Loop exited normally — i.e. we hit the max_pages cap before
            # finishing. Warn the caller they may be missing data.
            logger.warning(
                "paginate(%s) hit max_pages cap of %d; remaining pages not fetched.",
                path, max_pages,
            )
