# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.3] - 2026-04-24

### Security

- **Path traversal fixed.** Tool inputs like `call_id`, `submission_id`,
  `tag_id`, `account_id`, `company_id` were interpolated directly into the
  request URL via `urljoin`. A value like `../../../../etc/passwd` would
  divert the request away from the API path. Each path segment is now
  URL-encoded (`_safe_path`).
- **Redirects disabled.** The CallRail API never legitimately redirects;
  following one to an attacker-controlled host could leak the
  `Authorization: Token token=<key>` header. `Session.max_redirects = 0`
  + `allow_redirects=False` per request + explicit 3xx → CallRailError
  in `_parse`.
- **Retry-After cap.** Server-supplied `Retry-After` is now capped at
  60 seconds so a misbehaving (or hostile) endpoint can't pin the client
  for hours.

### Fixed

- **Network errors are now wrapped.** `requests.ConnectionError`, `Timeout`,
  and `ChunkedEncodingError` previously propagated raw out of `client.get()` /
  `.post()` / `.put()` / `.delete()`. They are now retried (same backoff as
  5xx) and, on exhaustion, raised as `CallRailError` for a consistent error
  contract.
- **`Retry-After` HTTP-date format** (`"Wed, 21 Oct 2026 07:28:00 GMT"`) no
  longer crashes the retry loop with `ValueError`. RFC 7231 second-form is
  tried first; date form is parsed via `email.utils.parsedate_to_datetime`;
  unparseable values fall back to exponential backoff.
- **Non-object JSON responses** (`["a","b"]`, `"plain string"`) are now
  rejected with a clear `CallRailError` instead of returning a value that
  later crashes downstream `.get()` calls. Mypy `no-any-return` is satisfied.
- **`logging.basicConfig` no longer runs at module import.** This was
  clobbering the host application's log configuration. The CLI entry
  point `main()` configures logging explicitly; library callers control
  their own handlers.
- **`paginate()` now clamps `per_page`** the same way listing tools do
  (was the only public client method that didn't), and **logs a warning**
  when the `max_pages` safety cap is hit (silent truncation before).
- **`list_companies` / `list_trackers` use `_clamp_per_page` consistently**
  with the other listing tools.

### Added

- `CallRailClient` is now a context manager: `with CallRailClient() as c: ...`
  releases the underlying HTTP `Session` on exit. Plain `.close()` also works.
- `MAX_RETRY_DELAY_SECONDS = 60.0` and `RETRYABLE_NETWORK_ERRORS` exported
  for transparency.
- Default `timeout` is now `(connect=5.0, read=20.0)` instead of a single
  value — a slow connect on a flaky network won't burn the full read budget.

## [0.2.2] - 2026-04-24

### Fixed
- **`search_calls_by_number`**: empty / non-digit / very-short input no longer
  returns the entire call history. Now requires ≥7 digits after stripping
  non-digits and returns a clear error envelope explaining why if not.
- **API key whitespace**: trailing newlines / leading spaces (a frequent
  copy-paste mistake) are now stripped in `CallRailClient.__init__`. Previously
  `requests` raised a cryptic *"Invalid leading whitespace in header value"*.
- **Module import no longer requires an API key.** The singleton `CallRailClient`
  is now lazy-built on first use via `get_client()`. `import callrail_mcp.server`
  works in clean environments — useful for test discovery, schema introspection,
  and `--help` flows.
- **`per_page` clamping** (`list_calls`, `list_form_submissions`, `list_text_messages`):
  values `≤ 0` now clamp to `1` instead of being passed through to the API.
- **`days=0`** no longer silently degrades to "no date filter" (returning the
  whole account history). Now ignored if non-positive.
- **`days=-N`** rejected with a clear error.
- **Date validation**: `start_date` / `end_date` validated against `YYYY-MM-DD`
  format before hitting the API. Malformed inputs return a client-side error
  instead of being silently dropped.
- **`end_date < start_date`** now rejected with a clear error (CallRail would
  otherwise return an unrelated, confusing result set).
- **`add_call_tags` / `remove_call_tags`**: empty/whitespace tag entries in the
  input list are silently filtered out via `_clean_tag_list()`. Avoids the API
  400 from `add_call_tags(['', 'lead'])` and the side-effect of partially
  applying changes.

### Changed
- `server.client` is now a transparent proxy over `get_client()` for
  backward compatibility — existing call sites work unchanged.
- Bumped User-Agent to `callrail-mcp/0.2.2`.

### Added
- 8 new unit tests covering: API-key whitespace stripping, lazy client init,
  `per_page` clamping, date-window validation (malformed, swapped, negative),
  `_clean_tag_list` behavior, and `search_calls_by_number` minimum-digit guard.

## [0.2.1] - 2026-04-24

### Fixed
- `create_tag` and `update_tag`: documented color values were wrong. CallRail's API
  rejects all the named colors I'd listed (`red`, `blue`, etc.) with `400 "Color is
  not included in the list"`. The actual valid set, discovered by exhaustive testing
  against the live API, is exposed as `client.VALID_TAG_COLORS`:
  `red1, red2, orange1, yellow1, green1, blue1, purple1, pink1, gray1, gray2`.
- Both tools now validate the color client-side before hitting the API and return a
  clean error envelope listing the valid options if you pass something else.
- Docstrings updated.

## [0.2.0] - 2026-04-24

### Added
- `CallRailClient` now supports `post()`, `put()`, and `delete()` (parallel to existing `get()` — same retry/backoff behavior, JSON body in/out, 204 handled).
- New write tools:
  - `update_call` — update note, tags, value, spam flag, customer name, lead status.
  - `add_call_tags` / `remove_call_tags` — additive/subtractive tag changes (preserves existing tags).
  - `update_form_submission` — same field surface as `update_call` for CallRail form-tracking entries.
  - `list_tags`, `create_tag`, `update_tag`, `delete_tag` — full CRUD on the per-company tag taxonomy.
- 5 new unit tests covering POST/PUT/DELETE happy paths, error envelopes, and 429 retry on POST.

## [0.1.0] - 2026-04-23

### Added
- Initial public release.
- `CallRailClient` — thin HTTP client with retry on 429/5xx, timeouts, transparent pagination helper.
- MCP server exposing the following tools: `list_accounts`, `list_companies`,
  `list_trackers`, `list_calls`, `get_call`, `call_summary`, `list_form_submissions`,
  `list_text_messages`, `list_users`, `get_call_recording`, `get_call_transcript`,
  `search_calls_by_number`.
- API key loading from `CALLRAIL_API_KEY`, `CALLRAIL_API_KEY_FILE`, or
  `~/.config/callrail/api-key.txt`.
- `callrail-mcp` CLI entry point for stdio transport.
- README with Claude Code / Claude Desktop configuration examples.
- Unit tests for client retry and pagination logic using `responses`.
