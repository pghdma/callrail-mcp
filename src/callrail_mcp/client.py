"""
CallRail API v3 HTTP client.

Thin wrapper around the REST API with:
- Token auth via header
- Automatic retry on 429 / 5xx with exponential backoff
- Request timeouts
- Transparent pagination helper
- Consistent JSON error envelope

API docs: https://apidocs.callrail.com/
"""
from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from requests import Response

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.callrail.com/v3/"
DEFAULT_TIMEOUT = 20.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_PER_PAGE = 100
MAX_PER_PAGE = 250

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
        timeout: float = DEFAULT_TIMEOUT,
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
        self.session.headers.update(
            {
                "Authorization": f"Token token={self.api_key}",
                "Accept": "application/json",
                "User-Agent": "callrail-mcp/0.2.2 (+https://github.com/pghdma/callrail-mcp)",
            }
        )

    # ---- low-level ----

    def _request(self, method: str, path: str, **kwargs: Any) -> Response:
        """Do one HTTP request with retry/backoff on 429 and 5xx."""
        url = urljoin(self.base_url, path.lstrip("/"))
        kwargs.setdefault("timeout", self.timeout)

        for attempt in range(self.max_retries + 1):
            resp = self.session.request(method, url, **kwargs)
            # CallRail responds 429 with Retry-After on rate limit (60 req/min/account)
            if resp.status_code == 429 and attempt < self.max_retries:
                delay = float(resp.headers.get("Retry-After", str(2 ** attempt)))
                logger.warning("CallRail 429; sleeping %.1fs (attempt %d)", delay, attempt + 1)
                time.sleep(delay)
                continue
            if 500 <= resp.status_code < 600 and attempt < self.max_retries:
                delay = 2 ** attempt
                logger.warning("CallRail %d; retrying in %ds (attempt %d)", resp.status_code, delay, attempt + 1)
                time.sleep(delay)
                continue
            return resp

        return resp  # last response even if it failed

    def _parse(self, resp: Response, method: str, path: str) -> dict[str, Any]:
        """Validate response status and parse JSON. Raises CallRailError on non-2xx."""
        if resp.status_code >= 400:
            raise CallRailError(
                f"CallRail API returned {resp.status_code} for {method} {path}",
                status=resp.status_code,
                body=resp.text[:2000],
            )
        if resp.status_code == 204 or not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError as e:
            raise CallRailError(f"Non-JSON response from {method} {path}: {e}", body=resp.text[:500]) from e

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
        params.setdefault("per_page", DEFAULT_PER_PAGE)
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
