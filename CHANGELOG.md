# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.1] - 2026-04-24

### Changed

- Added `<!-- mcp-name: io.github.pghdma/callrail-mcp -->` ownership marker
  to README so the package can be claimed in the official MCP Registry
  (`registry.modelcontextprotocol.io`). No code changes — README + version
  bump only so the marker shows up on PyPI.

## [1.0.0] - 2026-04-24

### First stable release — published to PyPI

`pip install callrail-mcp` now works.

This release locks the feature surface at **49 tools, ~85% of the
CallRail REST API v3**. The remaining 15% is documented as out-of-scope
in the README and CLAUDE.md (either CallRail-account-permission-gated
or UI-only on standard plans). No code changes vs v0.7.0 — version
bump only, plus README cleanup for the PyPI launch.

### Out of scope (will work on when feasible)

**Blocked by CallRail account permissions** (returns 403 for standard
accounts; verified live 2026-04-24):
- Send SMS (`POST /text-messages.json`) — needs A2P SMS registration
- Webhook integration CRUD (`POST /integrations.json` with `type=Webhook`)
  — needs Integration-Admin permission

**Not exposed by CallRail's REST API** (UI-only on standard plans):
- Outbound Caller IDs verification
- Numbers / porting / ownership
- Call Flows (IVR builder)
- Custom Fields CRUD
- Do Not Call list management

PRs welcome from anyone whose CallRail account has the gated
permissions or whose CallRail plan exposes the UI-only endpoints.

### Audit-and-test discipline behind v1.0

Across 18 minor releases (v0.3.1 → v1.0.0) over the development
period:

- ~115 bugs caught and fixed (1 CRITICAL silent-data-loss in
  `usage_summary` pagination, several HIGH including
  `update_tracker(greeting_text)` that wiped destination, POST retry
  double-write risk, `create_company` toggles disabling paid
  features, plus dozens of MED/LOW correctness + UX fixes)
- 60 → 297 tests, 84% coverage
- All 5 check tools clean: `pytest -W error`, `mypy --strict`,
  `ruff`, `bandit`, `pyright`
- Total external API spend across development: $0

## [0.7.0] - 2026-04-24

### Added — 8 new tools (final API parity push)

API surface coverage now ~85% (up from 75% in v0.6.x). The remaining
~15% is either deliberately not exposed by CallRail (UI-only) or
gated behind account permissions our standard API key doesn't have
(see CLAUDE.md "API coverage limits").

#### Tools shipped
- **`get_tag(tag_id)`** — single tag detail. Completes tag CRUD.
- **`list_integrations(company_id)`** — discover GMB / Google Ads /
  Facebook / Slack / Webhook integrations attached to a company.
  Account-level listing isn't supported (CallRail returns 400).
- **`get_integration(integration_id)`** — single-record detail.
- **`create_form_submission(company_id, referrer, referring_url,
  landing_page_url, ...)`** — manually create a form submission for
  backfilling offline leads (walk-ins, paper forms, etc.). All 3 of
  referrer/referring_url/landing_page_url required by CallRail.
- **`create_outbound_call(from_number, to_number, confirm_dialing=False)`**
  — place an outbound call. **Mirrors `create_tracker`'s safety
  pattern**: requires `confirm_dialing=True` because it actually places
  a real phone call (legal implications + minute cost).
- **`list_notifications`** / **`create_notification`** /
  **`update_notification`** / **`delete_notification`** — full CRUD on
  per-user alert rules (who gets pinged on which call/text/form event).

### Discovered + documented (NOT shipped)

Probed live and confirmed permission-gated:
- **`POST /text-messages.json`** (send SMS) returns 403 on standard
  CallRail accounts — needs A2P SMS registration / dedicated SMS API
  permission. CallRail enforces TCPA-compliance keywords on outbound.
- **`POST /integrations.json`** (create webhook integration) returns
  403 — needs Integration-Admin permission.

CallRail does NOT expose the following via API (UI-only):
- Outbound Caller IDs verification flow
- Numbers / porting / ownership moves
- Call Flows (IVR builder)
- Custom Fields CRUD (only readable as part of call/form responses)
- Do Not Call list management

CLAUDE.md now has an "API coverage limits" section documenting all
of the above so future contributors don't waste time re-discovering.

### Added — tests
- 13 new tests (284 → 297 total).

### Verified clean
- `mypy --strict`, `ruff`, `pytest -W error`, `bandit`, `pyright` —
  all 5 check tools clean.

## [0.6.1] - 2026-04-24

### Fixed (audit on v0.6.0 — 9 findings, 2 HIGH)

#### HIGH
- **`create_company` was force-sending all 6 optional boolean toggles
  on every create**, with `False` defaults. On accounts where any of
  those features (CallScore, lead scoring, Call Intelligence, keyword
  spotting, form capture) is enabled at the account level, this would
  have **DISABLED them on the new company**. Toggles now default to
  `None`; only included in the request body when the caller explicitly
  sets them.
- **`create_user(role="")` slipped past validation**, logged a
  misleading "unknown role" warning, and POSTed empty role to CallRail.
  Now `_require_non_empty(role)` runs before the unknown-role check.

#### MEDIUM
- **`update_user(email="   ")` returned the wrong error message**
  ("doesn't look like a valid email" instead of "required"). Now
  `_require_non_empty(email)` runs before `_validate_email`.
- **`first_name` / `last_name` had no length caps** in `create_user` /
  `update_user`. `_MAX_USER_NAME_LEN = 100` now enforced.

#### LOW
- **`company_ids` not type-checked** in `create_user` / `update_user`.
  Pass a string by mistake → iterates chars → confusing per-char errors.
  Now early-rejects with `"company_ids must be a list..."`.
- **Singleton ID lengths** (`webhook_id`, `conversation_id`,
  `submission_id`) capped at 256 chars to prevent absurd URL paths.
- **`get_company` docstring** now documents that disabled-record
  responses (status="disabled") are returned NOT 404'd.

### Added — tests

- 4 new tests (280 → 284):
  - `create_user(role="")` rejected
  - `create_user(first_name="A"*101)` rejected
  - `create_user(company_ids="COM_X")` (string not list) rejected
  - `update_user(email="   ")` says "required" not "invalid format"

## [0.6.0] - 2026-04-24

### Added — 12 new tools (API-parity push)

Increases CallRail v3 API surface coverage from ~50% to ~75%. Fills
the biggest gaps an agency owner would hit during day-to-day use.

#### Companies CRUD
- **`get_company(company_id)`** — single-record fetch.
- **`create_company(name, time_zone, ...)`** — new client onboarding.
  Free (CallRail bills per number, not per company). Defaults match
  observed live shapes (TZ "America/New_York", lead_scoring_enabled
  True).
- **`update_company(company_id, ...)`** — change name, TZ, scoring
  features. Empty-string fields rejected.
- **`delete_company(company_id)`** — soft-delete (status flips to
  "disabled", data retained). Mirrors `delete_tracker` semantics.

#### Users CRUD
- **`get_user(user_id)`** — single-record fetch.
- **`create_user(email, first_name, last_name, role, company_ids)`** —
  invites a new user (CallRail emails them). Validates email format,
  warns on unknown roles. Common roles: admin / manager / reporting /
  analyst.
- **`update_user(user_id, ...)`** — mutate email/name/role/companies.
- **`delete_user(user_id)`** — hard-removes user from account
  (different from companies/trackers which soft-delete).

#### Singletons (filling missing get-one endpoints)
- **`get_form_submission(submission_id)`** — was list+update only.
- **`get_text_message(conversation_id)`** — was list-only. Conv IDs
  are short alphanumeric strings (e.g. `"8hw3p"`), no prefix.

#### Webhooks (read-only for v0.6.0)
- **`list_webhooks(company_id?)`** — discover existing webhooks.
- **`get_webhook(webhook_id)`** — single-record detail.
- (CRUD deferred to v0.6.1 — request body shapes need live API
  verification before shipping create/update/delete.)

### Added — tests
- 29 new tests (251 → 280). Coverage maintained at 84%.

### Deferred to v0.6.1
Need live API verification of request body shapes:
- SMS send / reply (requires compliance-keyword handling per docs)
- Webhook create / update / delete
- Form submission manual create
- Outbound call placement

## [0.5.4] - 2026-04-24

### Changed (round 5 cleanup, 0 correctness bugs)
- Hoisted `_SPAM_AUTO_TAG_CAP` to module level (consistency with
  `_BULK_UPDATE_CAP`).
- Extracted `_coerce_days_int()` helper to dedupe coercion logic
  between `_validate_window` and `spam_detector`.

Round 5 audit on v0.5.3 found ZERO correctness bugs. We're at the
v0.5.x bug floor.

## [0.5.3] - 2026-04-24

### Fixed (round 4 audit on v0.5.2 — 4 findings, converging)

#### MEDIUM
- **`spam_detector` 90-day cap was bypassable via string input.**
  `spam_detector(days="365")` (loose JSON from MCP clients) hit
  `isinstance("365", int)` → False → cap-check skipped → `_date_window`
  re-coerced and a 365-day request reached CallRail. Now the cap-check
  coerces explicitly first.
- **`spam_detector(auto_tag=True)` had no operation cap.** A high-volume
  client with 5000+ flagged calls would trigger 5000 sequential
  GET+PUT pairs (~17 minutes), often killed by MCP transport timeouts
  mid-loop. Now capped at 1000 operations with `tag_truncated_at_cap`,
  `tag_cap`, `tag_total_eligible` fields surfacing the truncation.

#### LOW
- **`spam_detector` docstring** said "1-90 typical" but 91+ now
  hard-errors. Updated to "1-90; 90 is hard-capped".
- **Test isolation hardened** for the v0.5.2 TZ-warning dedup state.
  Module-level `_warned_tzs` / `_warned_multi_tz_signature` sets are
  now reset before each test via an `autouse` fixture, so
  warning-asserting tests can't be flaked by earlier-run tests
  pre-populating the dedup state.

### Trend
Rounds 2→3→4 found 11→8→4 bugs. Converging toward the floor.

### Added — tests
- 1 new test (250 → 251):
  - `spam_detector(days="365")` now correctly rejected by the 90 cap

## [0.5.2] - 2026-04-24

### Fixed (round 3 audit on v0.5.1 — 8 findings)

#### HIGH
- **`_tag_names_from` accepted non-list iterables**, silently corrupting
  tags. `_tag_names_from("hot,lead")` iterated chars and returned
  `['h','o','t',',','l','e','a','d']`. `_tag_names_from({"id":1})`
  returned dict keys. `_tag_names_from(42)` raised uncaught TypeError.
  Combined with `bulk_update_calls` / `spam_detector` callers, malformed
  CallRail responses could have triggered tag corruption AND auto-creation
  of garbage tags (CallRail auto-creates unknown tag names at the company
  level). Now type-checks the input is a list and warns + returns `[]`
  on anything else.

#### MEDIUM
- **`spam_detector` no longer accepts `days > 90`**. Scoring + auto-tag
  materialize the full call list in memory; `days=365` on a high-volume
  client could be ~100MB of dicts. The docstring already advised "1-90
  typical" but it wasn't enforced.
- **`spam_detector(auto_tag=True)` was tagging only the cap-truncated
  preview (500), not the full filtered set**. If 600 spam calls existed,
  only 500 got tagged silently. Now iterates the full `all_likely_spam`
  list and reports `tag_attempted_count` separately from `tagged_count`.

#### LOW
- **`_pick_account_tz` warnings deduped per-process**. Legacy-TZ /
  multi-TZ warnings used to fire on every aggregation tool call,
  spamming logs in `/loop` or repeated audits.
- **Legacy-TZ list extended** to cover `EDT`, `CDT`, `MDT`, `PDT`,
  `AKDT`, `HDT`, `AST`, `ADT`, `GMT`, `Z`, `UTC-5`, `UTC-8` (was
  missing several).
- **`compare_periods.partial_failures[]`** entries now include
  `timezone` so a copy-pasted bug report retains TZ context.
- **`bulk_update_calls` docstring** now documents the per-call extra
  GET when `set_tags_add` is used (~2× latency vs other set_* fields).

### Verified clean (round 3 explicit checks)
- Exception handler ordering is correct (`CallRailError` before
  bare `Exception` in commit loops).
- `compare_periods` partial_failures iteration is deterministic
  (list iteration order, not dict).
- `bulk_update_calls` per-call GET 404 handling is loud (not silent).

### Added — tests

- 3 new unit tests (247 → 250 total):
  - `_tag_names_from` rejects non-list inputs (str, dict, int) with warnings
  - `spam_detector` days-cap at 90
  - `_pick_account_tz` dedupes warnings per-process

## [0.5.1] - 2026-04-24

### Fixed (v0.5.0 round 2 audit — 11 findings)

#### MEDIUM
- **`spam_detector` was sending `[None, "tag"]`** when an existing call
  tag dict was malformed (no `name` field). New shared helper
  `_tag_names_from()` filters non-string entries consistently across
  `add_call_tags`, `remove_call_tags`, `bulk_update_calls`,
  `spam_detector`.
- **`compare_periods` per-company API failures were silently logged**
  (no `partial_failures[]` like `usage_summary` has). Now surfaces
  per-company / per-window errors with `partial_calls_before_failure`
  and `partial_minutes_before_failure` so under-counting is observable.
- **`bulk_update_calls` had a TOCTOU tag race**. The commit phase
  trusted the (potentially minutes-old) tags from the matched list
  result — a concurrent caller's tag write between list-time and
  commit-time would be silently overwritten. Now re-GETs fresh tags
  per call before merging (mirrors `spam_detector` / `add_call_tags`
  pattern).
- **`spam_detector` could return MB-sized `likely_spam` arrays** on
  popular numbers. Capped at 500 with `likely_spam_total`,
  `likely_spam_returned`, `likely_spam_truncated` fields so callers
  know when more exists.
- **`bulk_update_calls` and `spam_detector` commit loops only caught
  `CallRailError`** — an unexpected exception (e.g. from a future
  client refactor) would abort the loop with no audit trail of what
  was already committed. Now also catch broad `Exception` per call,
  recording each in `failures[]`.

#### LOW
- **`bulk_update_calls` dry-run preview showed `null` in `current_tags`**
  when CallRail returned nameless tag dicts (cosmetic, but inconsistent
  with the now-fixed commit-path filter).
- **`_pick_account_tz` warns on legacy TZ names** like `"EST"`/`"PST"`
  (which `zoneinfo.ZoneInfo` accepts but treats as fixed offsets, no
  DST — day boundaries drift 1 hour for half the year).
- **`_pick_account_tz` warns when an agency has multiple TZs** across
  active companies (multi-region MSPs); previously picked first
  non-deterministically.
- **`compare_periods` `biggest_mover`** was just a name — couldn't
  tell from the response whether the mover went up or down. Now an
  object with `name`, `direction` (`up`/`down`/`flat`),
  `minutes_delta`, `minutes_pct_change`.

### Added — tests

- 4 new unit tests (243 → 247 total):
  - `_tag_names_from` filters dicts-without-name + non-strings
  - `spam_detector` handles malformed existing tags (no None in PUT body)
  - `compare_periods` surfaces partial failures per window
  - `spam_detector` caps likely_spam at 500
- 2 existing tests updated for new contract:
  - `test_v050_bulk_update_commit` adds the per-call re-GET expectation
  - `test_v050_compare_periods_happy_path` asserts the new
    `biggest_mover` dict shape

## [0.5.0] - 2026-04-24

### Added — 3 new agency workflow tools

- **`compare_periods(days=30)`** — Month-over-month (or any-window-over-
  previous-equivalent-window) delta analysis. Returns per-company minute
  and call deltas, percentage changes, and biggest mover. Built to catch
  traffic trends before they become invoice surprises. Cap: 365 days.
- **`bulk_update_calls(filter=..., set=..., dry_run=True)`** — Apply the
  same update (tag, note, lead_status, spam flag) to every call matching
  a filter in one tool call. Replaces dozens of sequential `update_call`
  calls. **`dry_run=True` by default**: returns a preview of which calls
  would be updated. Hard cap of 500 calls per invocation; silent
  truncation is surfaced via `truncated_at_cap: true` flag.
- **`spam_detector(days=30, auto_tag=False)`** — Heuristic spam scoring
  (duration, answered, first-call, repeat-caller). Flags calls with
  score ≥ 3. Optional `auto_tag=True` (requires `company_id` for safety)
  adds `auto_detected_spam` tag to flagged calls. Deliberately does NOT
  mark `spam=True` — CallRail hides spam-flagged calls from default
  GETs, so tagging is reviewable; user can spam-flag manually after.

### Changed — `_date_window` is now timezone-aware

- `_date_window(tz="America/New_York")` uses the account's IANA timezone
  for the "today" boundary instead of UTC. `usage_summary` and
  `compare_periods` auto-pick the TZ from the first active company.
- New `_pick_account_tz()` helper reuses already-fetched companies
  instead of issuing a separate request.

Previously, a user in ET asking for `days=1` at 5 PM ET (= 10 PM UTC)
would get 1 UTC day — which could misalign with their actual business
day at month boundaries. Now the windows match the account's wall
clock.

### Fixed (v0.5.0 own-audit pass)

Running the audit-and-fix loop on v0.5.0's own code surfaced 8 issues:

- **HIGH**: `bulk_update_calls` silent truncation at 500-cap now surfaced
  via `truncated_at_cap: true` + human hint string.
- **HIGH**: `bulk_update_calls` now validates `answered` is exactly
  `"true"`/`"false"`/None. Previously `answered="no"` was forwarded to
  CallRail, which silently ignored it and returned ALL calls, which
  then got bulk-updated.
- **MED**: `bulk_update_calls` now validates `set_lead_status` is non-empty.
- **MED**: `compare_periods` window boundaries were overlapping by one
  day (CallRail's start_date/end_date are inclusive on both ends).
  Fixed: `prev_end = cur_start - 1 day`.
- **MED**: `spam_detector(auto_tag=True)` now REQUIRES `company_id` to
  prevent accidentally tagging spam across every company in the agency.
- **LOW**: `compare_periods` now uses the shared `_validate_window`
  helper for consistency with other windowed tools.
- **LOW**: `spam_detector` now uses `_validate_window(require_window=True)`
  for consistency (previously had duplicated ad-hoc days check).
- **LOW**: Removed dead `_get_account_timezone` helper in favor of
  `_pick_account_tz` which reuses already-fetched companies.

### Added — tests

- 13 new tests (230 → 243 total):
  - `compare_periods`: happy path, invalid-days rejection, window
    non-overlap regression.
  - `bulk_update_calls`: filter required, set_* required, dry-run
    doesn't PUT, commit succeeds, invalid `answered` rejected,
    truncation surfaced.
  - `spam_detector`: scoring logic, auto-tag flow, company_id required
    for auto_tag.
  - `_date_window`: tz parameter honored, bad tz falls back to UTC.

## [0.4.8] - 2026-04-24

### Fixed (audit pass 16)
- **`days=10**18` (or any huge integer) raised uncaught
  `OverflowError`** from `timedelta(days=10**18)`, crashing the MCP
  tool reply with a trace instead of a clean error envelope. Affected
  every windowed tool. `_validate_window` now caps `days` at 36500
  (~100 years), comfortably above any legit lookback and far below the
  C-int boundary.

### Tests
- 1 regression test for the days-cap (229 → 230 total).
- Round 15's cosmetic test polish: `test_v047_list_calls_accepts_string_days`
  now actually passes `days="7"` (was `days=7`, with a fake type-ignore).

## [0.4.7] - 2026-04-24

### Fixed (audit pass 13 + 14 — approaching the bug floor)

Pass 13 ran bandit (security scanner), pyright (alternate type-checker),
and a docstring-accuracy agent — all three reported ZERO code defects.
Only documentation drift. Pass 14 ran one more round with a different
lens and caught one HIGH-severity defect.

#### HIGH — discovered in round 14 (final lens)
- **`_date_window` crashed on string `days`**. `_validate_window` coerced
  string `days` to int locally (added v0.4.3) but only returned `(ok, msg)`
  — the coerced value was thrown away. `_date_window` then received the
  original string and raised `TypeError: '>' not supported between str and
  int`. Reachable via every tool accepting `days` (list_calls, call_summary,
  usage_summary, list_form_submissions, list_text_messages,
  search_calls_by_number) when an MCP client sent loose JSON. Tests caught
  the validator behavior but never exercised the full path. Now
  `_date_window` defensively coerces too, and an end-to-end regression
  test asserts `list_calls(days="7")` doesn't crash.

### Documentation drift (rounds 13 + 14 findings)
- CLAUDE.md stale by 5 versions; now accurate through v0.4.7 and lists
  ~20 validation guards added across versions.
- `usage_summary` docstring now documents the `partial_failures[]` schema
  including the v0.4.6 `partial_calls_before_failure` / `partial_minutes_
  before_failure` / etc. fields.
- `call_eligibility_check` docstring explains why source-slug detection
  uses `source` not `source_name` (misleading user-editable display).
- `update_call` / `update_form_submission` docstrings now list length caps
  (`note` 4000, `customer_name` 200, `tags` 100-entry max).
- README `update_form_submission` row clarified: "same field surface as
  update_call PLUS `value` (not supported on update_call — returns 500)".

### Clean across 4 independent check tools
- `pytest -W error` (warnings as errors): clean
- `mypy --strict`: no issues in 4 source files
- `ruff` lint: all checks passed
- `bandit`: 1 LOW (intentional `assert` for type narrowing)
- `pyright`: only missing-source-stub warnings for third-party deps

### Added — tests
- 2 new regression tests (227 → 229):
  - `_date_window` coerces string `days` + garbage-falls-back-to-no-window
  - `list_calls(days="7")` end-to-end doesn't crash

## [0.4.6] - 2026-04-24

### Fixed (audit pass 12 — 5 bugs incl. 1 HIGH)

#### HIGH — silent data loss on partial pagination failure
- **`usage_summary` partial-failure path was dropping accumulated
  call data**. If a company's call pagination succeeded for pages 1-2
  (250 calls each = 500 calls = ~$25 of overage) but then failed on
  page 3 with a 503, the agency total under-reported by those 500
  calls' worth and the user had no way to know how much was lost.
  Now `partial_failures[]` includes `partial_calls_before_failure`,
  `partial_minutes_before_failure`, `partial_local_numbers`,
  `partial_tollfree_numbers` for each company that errored mid-loop.

#### LOW
- **`_is_toll_free` was splitting on commas, breaking
  `+1,800,555,1234`-style human-formatted toll-free numbers**.
  Splitting at the first comma left `+1`, leading to misclassification.
  Now extracts ASCII digits ignoring all separators.
- **`_validate_window` accepted `bool` as `days`** because Python's
  `isinstance(True, int)` is True. `days=True` silently became
  `days=1`. Now rejected with a clear error.
- **`_clean_tag_list` silently dropped non-string entries.** `add_call_tags(['hot', 42, 'lead'])` would silently drop the `42` and add only 2 tags. Now logs a warning identifying how many entries were dropped.
- **`call_summary` swallowed malformed durations silently.** Now logs
  a WARNING with the offending call ID + raw value (matches
  `usage_summary` behavior added in v0.4.6 too).

### Verified clean
- `pytest -W error` passes (no Python warnings in test runs)
- `mypy --strict` passes
- ruff lint clean

### Added — tests

- 4 new unit tests (223 → 227 total):
  - Partial-failure surfaces accumulated data
  - Toll-free comma/dash/dot format detection
  - Bool-as-days rejection
  - `_clean_tag_list` warning on non-strings

## [0.4.5] - 2026-04-24

### Fixed (audit pass 11 — diminishing returns territory, but still 6 bugs)

- **`call_eligibility_check` source detection had a dead clause + missed
  bare `source="google"`** (F2). The redundant `source_slug == "google_my_business"`
  was already covered by the `startswith("google_")` check. And bare `"google"`
  (no underscore — rare but valid CallRail slug) was missed entirely.
- **`usage_summary` companies list was unpaginated** (F11) → silently
  truncated agencies with >250 active companies. Now uses `client.paginate`.
- **`paginate()` blindly trusted server-reported `total_pages`** (F3).
  A misbehaving server returning `total_pages: 999999` wouldn't be capped
  by `max_pages`. Now `total_pages` is clamped at `max_pages`.
- **`PRICING_PER_TOLLFREE_MINUTE` constant was defined but never used**
  (F12). Replaced with a comment documenting the limitation (we don't
  yet differentiate per-call pricing by tracker number type).

### Added — tests

- 3 new unit tests (220 → 223 total):
  - `paginate()` defensive cap on runaway `total_pages`
  - `is_google` accepts bare `source="google"` slug
  - `usage_summary` companies list paginates correctly
- Strengthened `test_v044_call_eligibility_uses_source_slug` to assert
  `source` is in the `fields=` URL (catches future refactor regressions).

## [0.4.4] - 2026-04-24

### Fixed (audit pass 10 — adversarial fuzzing + cross-tool consistency)

3 parallel audit angles (own-code review, adversarial input fuzzing,
cross-tool consistency) surfaced 20+ findings. Fixed the 13
highest-impact.

#### HIGH
- **Unicode bypass in IDs**: bidi/zero-width/combining chars (RTL
  override `\u202e`, ZWS `\u200b`, combining marks) passed
  `_validate_id_shape` and `_safe_path` (which only blocks
  `ord<0x20|0x7f`). They flowed through to URL paths + log lines +
  error envelopes where they mask spoofed IDs in display contexts.
  Now rejected at the validator with category-based check
  (`unicodedata.category()`).
- **`call_eligibility_check` was reading the wrong field for source
  detection**. Used `source_name` (user-editable display string,
  e.g. "Bing Ads (Google legacy import)") for substring match —
  would falsely classify Bing calls as Google. Now uses CallRail's
  internal `source` slug (e.g. `google_paid`, `bing_paid`).
- **`_is_toll_free` mis-classified NANP toll-free with extensions**.
  `+18005551234x77` was being counted as not-toll-free because the
  `x77` made the digit count != 11. Now strips RFC 3966 extensions
  (`x`, `,`, `;ext=`) before classification.

#### MEDIUM
- **Devanagari (and other Unicode) digits accepted in numeric
  validators**. `^\d{3}$` matches `\u096a\u0967\u0968` (Devanagari
  "412"). Replaced all `\d` with `[0-9]` for ASCII-only enforcement
  in date / phone / area-code regexes.
- **Tag IDs accepted any string** despite being numeric in CallRail.
  `update_tag(tag_id="hello world")` now fails fast.
- **Free-text fields had no length caps**. `update_call(note="X"*1MB)`
  would have sent multi-MB request bodies. Added `_MAX_NOTE_LEN=4000`,
  `_MAX_TAGS_PER_REQUEST=100`, `_MAX_CUSTOMER_NAME_LEN=200`.
- **`_validate_window` silently truncated float `days`**. `days=1.5`
  → `int(1.5)` = 1 (user expected ~36h). Now rejects non-integer
  floats explicitly.
- **`call_summary` didn't coerce duration**. Would `TypeError` if
  CallRail ever shipped string durations. Now matches `usage_summary`
  defense (`int(float(x))` with `contextlib.suppress`).

#### DEFENSIVE
- `_err()` now decodes bytes bodies (was: would crash if a future
  contributor wired bytes through CallRailError).
- `resolve_account_id()` now type-checks `accounts[0]` is a dict
  before `.get()` (was: AttributeError if CallRail returned a list
  of strings).
- API key file permission warning skipped on Windows (NTFS doesn't
  have POSIX mode bits — warning fired every load).
- Largest-remainder rounding loop cycles through indices when
  `abs(residual) > len(per_company)` (defensive against future
  pricing arithmetic that might exceed N cents drift).

### Added — tests

- 16 new unit tests (204 → 220 total):
  - 5-row parametrized matrix on Unicode-invisible-char rejection in IDs
  - Devanagari digit rejection in area_code + phone
  - Toll-free with extension classification
  - Float-`days` non-integer rejection
  - Oversize note / tags / customer_name caps for `update_call` +
    `update_form_submission`
  - Tag ID numeric validation
  - `is_google` source-slug detection (Bing-named-Google rejected;
    `google_paid`-no-gclid accepted)
  - `_err` bytes-body handling

## [0.4.3] - 2026-04-24

### Fixed (audit pass 9 — meta-audit on what passes 1-8 missed)

A meta-audit looking specifically at categories prior passes likely
skipped (race conditions, logging hygiene, timezone bugs, float
arithmetic, MCP protocol layer, config edge cases, weird API
behaviors, test coverage gaps) surfaced 14 findings. Fixed the 9
highest-impact + closed major coverage gaps.

#### CORRECTNESS
- **Cost shares now sum to `agency_total` exactly** (Finding 4.1).
  Pre-fix: float rounding could cause sum(per-company shares) to
  differ from agency_total by ±$0.01-0.05, breaking invoice
  reconciliation. Now uses largest-remainder rounding to distribute
  the residual to the company with the largest fractional share.
- **`_is_toll_free` no longer mis-classifies non-NANP numbers as
  local** (Finding 9.2). Shortcodes (5-digit), international numbers,
  etc. now return `False` instead of being counted as $3/mo local
  numbers in `usage_summary`.

#### PRIVACY / SECURITY
- **`_err()` truncates body to ~500 chars** (Finding 2.1) to prevent
  CallRail's echoed response data (potential PII, request payloads)
  from leaking unbounded into MCP responses and logs. Full body still
  capped at 2000 in `client.py` for the second-line defense.
- **API key file permission warning** (Finding 8.2). `_load_api_key`
  now warns (without erroring) if the file is group/world-readable.
  Recommended mode is 600.

#### CONFIG / UX
- **`CALLRAIL_API_KEY_FILE` now expands `$VAR` references** (Finding
  8.1). Paths like `$HOME/secrets/key.txt` previously resolved to the
  literal string and failed; now goes through `os.path.expandvars()`
  before `expanduser()`.
- **`_validate_window` coerces string-typed `days`** (Finding 6.2).
  MCP clients that send loose JSON (e.g. `days="30"`) now get the
  expected behavior instead of an uncaught `TypeError`.
- **`search_calls_by_number` caps matches at 500** (Finding 6.1) to
  prevent MCP-frame-exceeding payloads. Returns `truncated: true` and
  `match_cap: 500` when the cap is hit.

#### TYPING
- **mypy now passes cleanly** on the codebase. Fixed `Returning Any`
  warning in `resolve_account_id` by explicitly checking the type of
  `accounts[0]["id"]` before returning.

### Added — tests

- 21 new unit tests (183 → 204 total):
  - 13 happy-path tests for previously-uncovered tools:
    `list_companies`, `list_users`, `list_form_submissions`,
    `list_text_messages`, `list_tags`, `get_call`,
    `get_call_recording`, `get_call_transcript`, `update_call`,
    `update_form_submission`, `create_tag`, `update_tag`,
    `delete_tag`, `add_call_tags`, `remove_call_tags`
  - 1 cost-share invariant test
    (`sum(per-company costs) == agency_total`)
  - 1 search_calls truncation test (`match_cap` triggers at 500)
  - 1 `_is_toll_free` non-NANP rejection test
  - 2 `_err` body truncation tests (long + short)
  - 1 `_validate_window` string-coercion test

Coverage: 72% → 84% overall, server.py 67% → 82%.

### Considered + deferred (not fixed in this release)
- `add_call_tags` / `remove_call_tags` GET-then-PUT race condition
  (Finding 1.1). Documented limitation: not safe for concurrent
  use on the same call. CallRail has no atomic add-tag endpoint.
- UTC vs account-timezone mismatch in `_date_window` (Finding 3.1).
  Documenting in next release; full fix requires fetching
  `account.time_zone` and using it for the window boundaries.

## [0.4.2] - 2026-04-24

### Fixed (audit pass 6 — sweep of previously-untouched code)

A focused audit pass on the older tools (call CRUD, tag CRUD, form
CRUD, read tools) and the HTTP client layer surfaced 30+ findings.
Highest-impact 12 fixed in this release.

#### CRITICAL
- **POST retries on 5xx could create duplicate trackers.** A 502 on
  `create_tracker` would trigger up to 3 retries — if CallRail had
  actually processed the original request and just lost the response,
  the retries would produce 2-4 trackers ($3/mo each, charged forever).
  Fix: 5xx-retry policy now restricted to **idempotent methods** (GET,
  PUT, DELETE, HEAD, OPTIONS). POST returns the 5xx as an error
  envelope on first failure. 429 still retries all methods (server
  hasn't accepted the request yet).
- **`paginate()` silently truncated to page 1** when `total_pages` was
  missing from the response. Previously hardcoded a default of `1`,
  causing immediate stop. Now falls back to "stop on empty page",
  preserving all data.

#### HIGH
- **Missing ID validation on 9 tools**: `get_call`,
  `get_call_recording`, `get_call_transcript`, `update_call`,
  `add_call_tags`, `remove_call_tags`, `update_form_submission`,
  `update_tag`, `delete_tag` all accepted empty / dots-only / slash-
  containing IDs and forwarded them to CallRail (404). Now fail-fast
  with `_require_non_empty` + `_validate_id_shape` (with appropriate
  prefix where applicable).
- **`update_call` / `update_form_submission` accepted empty-string
  optional fields** (`note=""`, `customer_name="   "`), which
  CallRail interprets as "clear this field" — almost always a mistake.
  Now rejected with a clear error.

#### MEDIUM
- **`call_summary` / `search_calls_by_number` accepted `days=0` with
  no `start_date`** → `_date_window` returned `{}` → CallRail returned
  ALL-TIME call history → up to 12,500 calls aggregated. Same root
  cause as the `usage_summary` bug from v0.4.1. Now `_validate_window`
  has a `require_window=True` flag used by all three.
- **`_parse_retry_after` accepted negative seconds.** A server sending
  `Retry-After: -30` would have crashed `time.sleep()` with
  `ValueError`. New `_clamp_delay()` helper floors at 0 and caps at
  `MAX_RETRY_DELAY_SECONDS`.
- **`list_tags` used `min(per_page, MAX_PER_PAGE)` instead of
  `_clamp_per_page`** (didn't floor at 1) and didn't clamp `page≥1`.
  Now consistent with sibling listing tools.

### Added — tests

- 24 new unit tests (159 → 183 total):
  - 15-row parametrized matrix covering ID validation across every
    fixed tool
  - Empty-string field rejection on update_call
  - `days=0` rejection on call_summary + search_calls_by_number
  - `_clamp_delay` boundary tests
  - `_parse_retry_after` with negative seconds
  - **POST does NOT retry on 5xx** (CRITICAL fix verification)
  - GET still retries on 5xx (sanity)
  - `paginate()` continues past page 1 when `total_pages` is missing

### Considered + deferred
- Silent pagination truncation in `list_companies`, `list_users`,
  `list_form_submissions`, `list_text_messages`, `list_tags`
  (one-page only, no auto-paginate). Default behavior preserved to
  avoid surprising callers with very large response payloads. Will
  add an opt-in `all_pages=True` flag in a future release.
- Length caps on `note`/`customer_name`/tag-name (CallRail's actual
  limits aren't documented).
- Enum validation for `lead_status` (could break for accounts using
  custom lead-status values).
- `resolve_account_id()` validation of caller-supplied IDs (would
  add a HEAD request to every call — not worth the latency).

## [0.4.1] - 2026-04-24

### Fixed (3-round audit pass on v0.4.0 tools)

A focused 3-round audit on `usage_summary` and `call_eligibility_check`
surfaced 1 CRITICAL + 1 HIGH + 4 MEDIUM bugs. All fixed.

#### CRITICAL — confirmed in production data
- **`usage_summary` was silently truncating call counts at 250 per
  company.** Used a single `client.get(... per_page=250)` instead of
  paginating. Live evidence: Malick + Stewart both showed exactly 250
  calls in v0.4.0 output (the page-1 ceiling). The agency total
  underestimate was ~$44 ($132 vs the real ~$176 from the billing
  dashboard). Now uses `client.paginate()` for both calls AND trackers
  loops — no truncation regardless of cycle volume.

#### HIGH
- **Cost attribution missed the base subscription when no minutes were
  used.** When `total_minutes == 0`, the entire attribution block was
  skipped, leaving every company's `estimated_cost_share` unset and the
  $50 base unattributed. Now always attributes base; falls back to
  even-N-way split when no resource signal is available.

#### MEDIUM
- **Per-company API failures poisoned the whole report.** One company
  hitting a 503 → entire `usage_summary` errors out. Now per-company
  try/except; failures collected in a `partial_failures: [...]` field
  in the response so the rest of the agency report still ships.
- **`days=0` (or negative) with no explicit dates** would have made
  `_date_window` return empty params → CallRail returns ALL-TIME call
  history → cost estimate based on years of minutes. Now rejected
  with a clear error.
- **Duration parsing was fragile.** `int(call_data.get("duration"))`
  would crash on float-strings like `"60.5"`. Same for `bool(answered)`
  on string `"true"`. Both now safely coerced.
- **`call_eligibility_check` didn't enforce `CAL` prefix on
  `call_id`.** Now uses `_validate_id_shape(prefix="CAL")` to fail-fast
  on bogus IDs.

### Considered + rejected
- The audit suggested removing `bool(gclid)` from the `is_google` source
  heuristic, claiming it tautologically tracks `has_gclid`. Rejected:
  gclid stands for "Google Click ID" and is only minted by Google Ads
  — its presence is honest signal that the call originated from Google,
  even when CallRail's `source_name` is generic ("Website Pool" for
  DNI sessions). Kept as-is with extended comment explaining why.

### Added — tests

- 5 new tests (154 → 159 total):
  - `test_usage_summary_paginates_calls` — proves >250 calls now counted
  - `test_usage_summary_partial_failure_per_company` — proves one bad
    company doesn't poison the report
  - `test_usage_summary_rejects_zero_days_without_dates`
  - `test_call_eligibility_check_safe_duration_coercion` — float-string
    + string-boolean inputs handled
  - `test_call_eligibility_check_requires_CAL_prefix`

## [0.4.0] - 2026-04-24

### Added — agency aggregation tools

Two new tools that are pure reads (zero write cost, zero provisioning) but
add real agency-level utility.

- **`usage_summary(days=30)`** — per-company cost attribution for the
  current CallRail cycle. Aggregates active trackers + per-company
  call-minute totals + projects estimated cost share under Call Tracking
  Starter pricing ($50 base + 5 numbers + 250 mins bundled; $3/local,
  $5/toll-free, $0.05/min over bundle). Returns a sorted breakdown
  showing who's driving the bill. Useful for: "which client is burning
  my CallRail budget", quarterly reviews, upsell / renegotiation
  signals. Pricing constants are editable in `server.py` for other plans.

- **`call_eligibility_check(call_id, google_ads_min_duration_seconds=60)`**
  — audits whether a specific call is/was eligible to count as a
  Google Ads conversion. Checks: `gclid` presence, answered status,
  duration vs. Google's threshold, source (Google vs Bing/GMB/organic).
  Returns verdict + per-check pass/fail + targeted remediation text
  when eligibility fails. Built specifically to short-circuit
  conversion-debug sessions like "this 58-second answered call with a
  gclid doesn't show in Google Ads, why?".

### Added — tests

- 11 new unit tests (143 → 154 total):
  - 1 for `_is_toll_free` helper (number-type detection)
  - 3 for `usage_summary` (aggregation correctness, negative-days
    rejection, swapped-dates rejection)
  - 7 for `call_eligibility_check` (all happy + rejection paths,
    including the exact 58-second Pittsburgh Z PA scenario from
    2026-04-24 that motivated building the tool)

### Changed

- README updated to reflect all 26 tools across 4 categories (read,
  write, tracker provisioning, agency aggregation). Previous README was
  stale since v0.1.0 and listed only 12 tools.
- Added agency-specific example prompts to README: cost attribution +
  conversion debugging + provisioning.

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
