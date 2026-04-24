# callrail-mcp — Claude Code session notes

Project: Model Context Protocol server for CallRail REST API v3.
Author: Steve Japalucci / PGHDMA.
Repo: https://github.com/pghdma/callrail-mcp — MIT, public.

## Where things live

- **Source:** `src/callrail_mcp/` (`client.py`, `server.py`, `__init__.py`, `__main__.py`)
- **Tests:** `tests/` — 60 passing, uses `responses` + `hypothesis`. No real API hits in CI.
- **Local dev venv:** `.venv/` (created via `python -m venv .venv && pip install -e ".[dev]"`).
- **pipx install:** `/Users/s/.local/bin/callrail-mcp` → points to `~/.local/pipx/venvs/callrail-mcp/`.
- **API key:** `~/.config/callrail/api-key.txt` (mode 600). Also honored: `CALLRAIL_API_KEY` env, `CALLRAIL_API_KEY_FILE` env.

## Release flow

```bash
# 1. bump version in 3 places
sed -i '' 's/0.X.Y/0.X.Z/g' src/callrail_mcp/__init__.py pyproject.toml src/callrail_mcp/client.py
# 2. edit CHANGELOG.md — move items from [Unreleased] to new [0.X.Z] - YYYY-MM-DD
# 3. run checks
.venv/bin/ruff check src tests && CALLRAIL_API_KEY=dummy .venv/bin/pytest -q
# 4. commit, push
git add -A && git commit -m "..." && git push
# 5. reinstall locally so new tools are picked up in Claude Code next restart
pipx install . --force
# 6. (optional) publish to PyPI: git tag v0.X.Z && git push --tags && create GitHub Release
#    — publish.yml handles the build+upload via trusted publishing (one-time setup at pypi.org)
```

Claude Code restart required after `pipx install --force` for new tools to show up in the MCP session.

## Patterns to keep

- **Every tool returns a JSON string via `_ok()` or `_err()`.** Never raise from a tool body — catch `CallRailError` and format.
- **Pre-validate tool inputs before calling `client.resolve_account_id()`** — avoids burning an API call to fetch the account for a request that would fail validation anyway.
- **`_safe_path()` encodes EVERY path segment** used in the URL. Rejects dot-segments and control chars. Any new tool that interpolates a user-controllable id (call_id, tracker_id, tag_id, etc.) into a path must go through `client.get/post/put/delete` (which use `_safe_path` internally).
- **Discovered enums are tuples in `client.py`:** `VALID_TAG_COLORS`, `VALID_TRACKER_TYPES`, `VALID_SOURCE_TYPES`. Update these as new valid values are discovered. Client-side validation uses them to fail fast with helpful error messages.
- **No `logging.basicConfig()` at import time.** Library hygiene — only `main()` (CLI entry) configures logging.
- **Lazy client init.** `server.get_client()` is the accessor; `server.client` is a proxy for back-compat. Module import must not require a key.

## Known CallRail API quirks (don't re-learn these)

- DELETE on companies & trackers is a **soft-delete**. Records still appear in list responses unless `status="active"` filter used.
- Marking a call spam **hides it from default GETs**. Tag first, spam-flag last.
- `value` field on PUT /calls returns **HTTP 500** from CallRail. Do not expose on `update_call`. Works on form submissions.
- `Retry-After` can be seconds-int OR HTTP-date. `_parse_retry_after` handles both + caps at 60s.
- CallRail can return JSON arrays where docs suggest objects — `_parse` rejects non-object responses with CallRailError.
- Tag colors: only `red1 red2 orange1 yellow1 green1 blue1 purple1 pink1 gray1 gray2`. Anything else = 400.
- Tracker source.type: 7 known-valid values: `all direct offline google_my_business google_ad_extension facebook_all bing_all`. Anything else = 400. For multi-source DNI use `type='session'` pools.
- Tag create/`add_call_tags` with unknown names **auto-creates tags at the company level** as a side effect.
- `confirm_billing=True` is REQUIRED on `create_tracker` — defensive against AI exploration. Costs money (~$3/mo/number).

## Validation guards added across versions (don't re-discover these)

- ID validation everywhere: `_require_non_empty` + `_validate_id_shape` + Unicode-category rejection (no RTL/ZWS/combining chars). Wired into get/update/delete tools for tracker/call/form/tag IDs. Optional `prefix=` arg enforces "TRK"/"COM"/"CAL". Tag IDs additionally must match `^[0-9]+$`.
- `_validate_window` rejects: bool, non-integer floats, negative days, swapped start/end, malformed YYYY-MM-DD. Optional `require_window=True` rejects `days<=0` w/o explicit dates (used by aggregating tools to prevent all-time scans).
- `_safe_path` rejects empty/dot/control segments + percent-encodes per segment. Tracker IDs with `/` rejected at the validator before reaching `_safe_path`.
- POST does NOT retry on 5xx (avoids duplicate trackers). GET/PUT/DELETE retry as before. 429 retries all methods.
- Pagination: `paginate()` clamps `total_pages` at `max_pages`, falls back to "stop on empty page" when total_pages missing.
- Cost rounding in `usage_summary` uses largest-remainder so per-company shares sum exactly to agency_total.
- Length caps: tracker name 255, whisper/greeting 500, note 4000, customer_name 200, tags-per-request 100.
- `_err()` truncates body to 500 chars + decodes bytes defensively.
- API key file: `$VAR` expansion, mode-600 warning (skipped on Windows).

## Current version: 0.5.4

See `CHANGELOG.md` for full history. Highlights:
- `0.1.0` — initial 12 read tools
- `0.2.0-0.2.4` — write tools (update_call, tag CRUD, form updates); 4 passes of bug fixing
- `0.3.0` — tracker CRUD + billing-confirmation safeguard
- `0.3.1` — `status` filter on `list_companies` / `list_trackers`
- `0.3.2` — tracker CRUD audit (12 bugs incl. CRITICAL: `update_tracker(greeting_text)` alone wiped destination)
- `0.3.3` — facebook_all/bing_all source types added; tracker_id slash bypass
- `0.4.0` — `usage_summary` + `call_eligibility_check` agency tools
- `0.4.1` — usage_summary CRITICAL pagination bug (was truncating at 250 calls/company)
- `0.4.2` — POST no-retry on 5xx, paginate total_pages handling, ID validation across older tools
- `0.4.3` — meta-audit: largest-remainder cost rounding, _err truncation, etc.
- `0.4.4` — Unicode-invisible char rejection, source-slug detection, extension stripping
- `0.4.5` — paginate companies, defensive total_pages cap
- `0.4.6` — partial-failure surfaces accumulated minutes (silent data loss fix), bool rejected as days, is_toll_free handles formatted numbers
- `0.4.7` — string-`days` no longer crashes `_date_window`; docstrings updated for length caps + source-slug semantics
- `0.4.8` — `days=10**18` no longer raises OverflowError from `timedelta` (capped at 36500/100yr)
- `0.5.0` — 3 new agency workflow tools: `compare_periods`, `bulk_update_calls`, `spam_detector` + TZ-aware `_date_window`
- `0.5.1` — Round 2 audit on v0.5.0: 11 bugs (Unicode tag filtering, partial_failures, TOCTOU race fix, broad exception catching, biggest_mover direction)
- `0.5.2` — Round 3 audit on v0.5.1: 8 bugs (HIGH: `_tag_names_from` non-list type-check; spam_detector days-cap at 90, dedup TZ warnings, auto_tag uses full filtered list)
- `0.5.4` — Round 4 audit on v0.5.2: 4 bugs (string-days bypass on spam_detector cap, auto_tag operation cap of 1000, docstring drift, test isolation for warning dedup)

**Tests: 251 passing. Coverage: 84%. mypy --strict + ruff + pytest -W error + bandit + pyright all clean.**

## Candidate features (ranked by agency utility)

1. Webhook subscribe/unsubscribe
2. Notifications config CRUD (who gets pinged on which call)
3. Custom field CRUD
4. Outbound call placement
5. Do-not-call list management
6. Toll-free minute pricing differentiation in `usage_summary`
7. `export_calls_to_csv` for client-deliverable reporting

Not yet started.
