# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.3] - 2026-04-24

### Fixed (live-verification findings — round 2 of v0.3.2)

Post-0.3.2 live testing against real CallRail trackers across all 5 active
agency client companies surfaced 3 additional issues. All fixed.

- **`VALID_SOURCE_TYPES` was too strict.** Previous list of 5 types missed
  `facebook_all` and `bing_all`, both of which are in production use
  (observed on Malick Brothers' Facebook Ads + Bing Ads trackers). Anyone
  trying to `create_tracker(source_type="facebook_all", ...)` would have
  been rejected client-side despite being valid on CallRail's side. List
  now includes 7 values; comment invites PRs to add more as discovered.
- **Tracker IDs containing `/` slipped past validation.** e.g.
  `tracker_id="TRK_xyz/companies/COM_admin"` was split into multiple
  URL segments by `_safe_path`, each segment encoded, and forwarded to
  CallRail (which 404'd, so not exploitable — but wasted an API call).
  New `_validate_id_shape` rejects any ID containing a slash.
- **Dots-only tracker IDs slipped past `_safe_path`.** e.g.
  `tracker_id=".."` got concatenated with the `.json` extension to
  produce `...json`, which passed the exact-match check for `"."` /
  `".."`. Same no-exploit-but-wastes-API-call story. Now rejected
  client-side.

### Added

- `_validate_id_shape(value, field_name, prefix=None)` helper — wired
  into `get_tracker`, `update_tracker`, `delete_tracker`. Supports an
  optional prefix check for future tightening.
- 10 new tests covering the new validation (8 parametrized on
  `_validate_id_shape` + 2 on the source-types list).

Tests: 133 → 143. All green.

## [0.3.2] - 2026-04-24

### Fixed (tracker CRUD audit pass — bug-hunt round 5)

A targeted audit of the v0.3.0 tracker CRUD code surfaced 1 critical, 4 high,
and 7 medium bugs. All fixed in this release. **No breaking changes** — every
fix tightens validation or improves return-value fidelity.

#### CRITICAL
- **`update_tracker(greeting_text="x")` alone would break the tracker.** PUT
  /trackers replaces the whole `call_flow` object — supplying greeting_text
  without destination_number would silently zero out the destination number.
  Now rejected with a clear error directing the caller to pass both fields
  together (or call `get_tracker` first to read the current destination).

#### HIGH
- **`delete_tracker` was discarding CallRail's response body.** The disabled
  record (with `disabled_at` timestamp, etc.) is now captured in
  `{"deleted": True, "tracker_id": ..., "response": <body>}`. Empty object
  on 204.
- **No format validation on `tracker_id` / `company_id` / `destination_number`.**
  `get_tracker(tracker_id="")`, `update_tracker(tracker_id="   ")`,
  `create_tracker(company_id="")` etc. previously burned an account-resolve
  API call before failing. All now fail-fast pre-network with clear errors.
- **`update_tracker` ran no input validation before resolving the account.**
  Now mirrors `create_tracker`: every input checked before any network I/O.

#### MEDIUM
- **`toll_free=True` + `area_code="412"` silently dropped the area_code.**
  Now rejected with `"Cannot specify both… choose one."`.
- **No format check on `area_code` / `pool_size` / `destination_number`.**
  - `area_code` must match `^\d{3}$`.
  - `pool_size` must be in `[1, 50]` — the upper cap is a safety guard
    against accidental 5-figure provisioning bills.
  - `destination_number` must look like an E.164-ish phone (`^\+?\d{10,15}$`).
- **No length caps on `name` / `whisper_message` / `greeting_text`.**
  - `name`: 255 char cap.
  - `whisper_message` / `greeting_text`: 500 char cap (CallRail TTS limits).
  Prevents 5-minute TTS greetings billing the user.
- **`list_trackers(status="garbage")` was forwarded to the API.** Now
  validated against `("active", "disabled", None)` before any network call.
- **Dead `if sms_enabled is not None` branch removed.** The parameter type
  was `bool = True`, never None — branch always evaluated True. Now
  unconditionally sets `sms_enabled` in the request body.

#### Validation order normalization
- All validation now runs **before** `confirm_billing` check in
  `create_tracker`, so users see real input errors first instead of
  having to fix billing-confirm before learning about other problems.

### Added — testing infrastructure

- **67 new mock-based unit tests** for tracker CRUD covering every
  validation gate, every flag conflict, every format check, every
  length cap. Total: 60 → 127.
- **6 new property-based fuzz tests** using Hypothesis (~500 random
  inputs each). Invariant: tracker tools must NEVER raise an uncaught
  exception, only return parseable JSON envelopes. Total: 127 → 133.
- **`hypothesis>=6.100`** added to `[project.optional-dependencies].dev`.

### Notes

This release contains no live API behavior changes — every existing
caller continues to work. Validation tightens may now reject some
inputs that previously made it to CallRail (and got 400-ed by them
instead). Net result: faster + clearer failures for bad inputs.

## [0.3.1] - 2026-04-24

### Added

- `list_companies` and `list_trackers` accept a new optional `status`
  parameter (server-side filter via CallRail's `?status=` query). Pass
  `status="active"` to exclude soft-deleted/disabled records — useful
  for cleaning up dashboards after running `delete_tracker` or
  deleting a company, since CallRail's DELETE is a soft-delete that
  preserves history but leaves entries in the default list response.
- Verified live: in a test account with 31 trackers (12 active),
  `list_trackers(status="active")` correctly returns 12.

### Notes

CallRail's DELETE on companies and trackers is documented (now) to be
a soft-delete: status flips to "disabled", `disabled_at` timestamp set,
underlying phone number released back to CallRail's pool, billing for
that number stops. The record is retained for audit. This was
previously surfaced as confusion ("DELETE returned 200 but record still
appears") — the new `status` filter makes the intended workflow clearer.

## [0.3.0] - 2026-04-24

### Added — tracker CRUD

Provision, configure, and disable CallRail tracking phone numbers
programmatically. Useful for new-client onboarding (replaces ~20 minutes
of clicking through the CallRail UI per client) and for automated source
attribution setup.

- **`get_tracker(tracker_id)`** — full detail for one tracker.
- **`create_tracker(name, company_id, destination_number, …)`** — provision
  a new tracking number. Supports both `type='source'` (single number tied
  to one traffic source) and `type='session'` (DNI pool that swaps numbers
  per visitor). Local (via `area_code`) or toll-free (`toll_free=True`).
  Configures whisper message, recording, greeting text, SMS in one call.
- **`update_tracker(tracker_id, …)`** — change name, destination,
  whisper, greeting, SMS toggle. Notes that CallRail silently ignores
  status changes via PUT (use `delete_tracker` to disable).
- **`delete_tracker(tracker_id)`** — soft-delete: tracker stops receiving
  new calls, history retained, phone number released back to CallRail's
  pool.

### Discovered (and now exposed) constants

- `VALID_TRACKER_TYPES = ('source', 'session')`
- `VALID_SOURCE_TYPES = ('all', 'direct', 'offline', 'google_my_business',
  'google_ad_extension')` — discovered by exhaustive testing; CallRail's
  REST docs do not enumerate this. Anything else returns
  400 *"Unknown tracking source type"*.

### Notes

The 5 valid `source_type` values may surprise users expecting
`google_ads` / `bing_ads` / `facebook_ads` etc. Those traffic sources are
typically tracked via `type='session'` DNI pools (which swap numbers per
visitor based on the referring URL / utm params), not via single-number
source trackers. `google_ad_extension` specifically maps to the SERP
call-extension number on Google Ads.

## [0.2.4] - 2026-04-24

### Removed (BREAKING for `update_call`)

- **`value` parameter removed from `update_call`.** Discovered through
  round-trip determinism testing: CallRail's API returns **HTTP 500 server
  error** when `value` is included in a PUT to `/calls/{id}` (verified live
  multiple times). Each rejected request also burned 3 retry attempts due
  to our 5xx-retry policy. The field is undocumented for calls in CallRail's
  REST docs and may be a future feature, but it's broken today.

  `value` remains supported on `update_form_submission` (different endpoint,
  different behavior) where it is documented and works.

### Added

- `update_call` docstring now warns about CallRail's "spam-flagged calls
  vanish from default GET endpoints" behavior — tag the call BEFORE marking
  it spam if you need to do both.

### Notes

This release contains no fixes from a fresh bug hunt; rather, it removes a
field that direct testing proved CallRail itself does not support.

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
