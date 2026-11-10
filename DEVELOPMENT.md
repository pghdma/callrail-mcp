# callrail-mcp developer notes

Project: Model Context Protocol server for CallRail REST API v3.
Author: Steve Japalucci / PGHDMA.
Repo: https://github.com/pghdma/callrail-mcp. MIT, public.

## Where things live

- **Source:** `src/callrail_mcp/` (`client.py`, `server.py`, `__init__.py`, `__main__.py`)
- **Tests:** `tests/`: 517 passing, uses `responses` + `hypothesis`. No real API hits in CI.
- **Local dev venv:** `.venv/` (created via `python -m venv .venv && pip install -e ".[dev]"`).
- **pipx install:** `~/.local/bin/callrail-mcp` → points to `~/.local/pipx/venvs/callrail-mcp/`.
- **API key:** `~/.config/callrail/api-key.txt` (mode 600). Also honored: `CALLRAIL_API_KEY` env, `CALLRAIL_API_KEY_FILE` env.
- **Account id:** `CALLRAIL_ACCOUNT_ID` env wins, then the per-client cache, then one `GET a.json`.

## Release flow

```bash
# 1. bump version in 4 files (server.json carries it twice; the registry rejects a re-used version)
sed -i '' 's/0.X.Y/0.X.Z/g' src/callrail_mcp/__init__.py pyproject.toml src/callrail_mcp/client.py server.json
# 2. edit CHANGELOG.md: move items from [Unreleased] to a new [0.X.Z] - YYYY-MM-DD section
# 3. run checks
.venv/bin/ruff check src tests && CALLRAIL_API_KEY=dummy .venv/bin/pytest -q
# 4. commit, push
git add -A && git commit -m "..." && git push
# 5. reinstall locally so new tools are picked up on next MCP client restart
pipx install . --force
# 6. publish: git tag vX.Y.Z && git push origin vX.Y.Z && gh release create vX.Y.Z --notes-file <notes.md>
#    publish.yml then does the rest with no manual steps: verifies every version field matches
#    the tag, uploads to PyPI (trusted publishing), waits for PyPI to serve it, and publishes
#    server.json to the MCP registry via GitHub OIDC. No device codes, no local mcp-publisher.
#    To prove the registry path without a release: Actions > Publish > Run workflow > staging.
```

Restart your MCP client after `pipx install --force` for new tools to appear in the session.

## Patterns to keep

- **Every tool returns a JSON string via `_ok()` or `_err()`.** Never raise from a tool body, catch `CallRailError` and format.
- **Pre-validate tool inputs before calling `client.resolve_account_id()`**: avoids burning an API call to fetch the account for a request that would fail validation anyway.
- **`_safe_path()` encodes EVERY path segment** used in the URL. Rejects dot-segments and control chars. Any new tool that interpolates a user-controllable id (call_id, tracker_id, tag_id, etc.) into a path must go through `client.get/post/put/delete` (which use `_safe_path` internally).
- **Enum tuples:** `VALID_TAG_COLORS` (client.py, 24 documented values as of 2026-07; cyan1 live-verified), `VALID_TRACKER_TYPES`, `VALID_SOURCE_TYPES` (server.py, 12 = 10 documented + facebook_all/bing_all which docs still omit). CallRail now documents these at apidocs.callrail.com, check docs first, probe live second.
- **No `logging.basicConfig()` at import time.** Library hygiene, only `main()` (CLI entry) configures logging.
- **Lazy client init.** `server.get_client()` is the accessor; `server.client` is a proxy for back-compat. Module import must not require a key.
- **Tool annotations.** Every `@mcp.tool` passes one of `_READ`, `_CREATE`, `_UPDATE`, `_DELETE`, `_BULK_WRITE`, `_DIAL`. `test_v130_every_tool_is_annotated` enumerates the write set, so a new write tool must be added there too.
- **No silent no-ops.** When CallRail ignores or rejects a field (read-only toggles, PUT fields that do not exist), reject it with the reason or report it under `api_warnings`. Check the request-body table in the docs, not the response fields.
- **Shared validators for filters:** `_validate_choice` (enums), `_validate_time_zone` (IANA via zoneinfo), `_validate_fields_list` (comma-separated `fields`), `_validate_filter_list` (`tags[]`-style lists).

## API coverage limits, endpoints we deliberately did NOT ship

Probed live 2026-04-24, re-probed 2026-07-03, 2026-10-05 and 2026-11-10. These
endpoints are documented but return **403 "You do not have permission"**
on this account. Re-attempt if account/plan is upgraded:

- **`POST /a/{aid}/text-messages.json`** (send SMS; MMS supported
  since 2026-05-05 via media_url/media_file), needs A2P/TCR SMS
  registration. CallRail enforces TCPA-compliance keywords
  (STOP/CANCEL/UNSUBSCRIBE) on outbound.
- **`POST /a/{aid}/integrations.json`** (create webhook integration),
  needs Integration-Admin permission.
- **Outbound Caller IDs** (`/caller_ids.json` CRUD), now DOCUMENTED
  (was UI-only in April) but still 403 on this account. Support ticket
  may unlock.
- **Message Flows** (`/message-flows.json` CRUD. SMS auto-reply
  flows), documented, 403.
- **Integration Filters** (`/integration_triggers.json` CRUD),
  documented, 403.

Shipped in v1.1.0 (were new since April): leads + timelines,
sms-threads (incl. PUT), calls/summary + calls/timeseries +
forms/summary, page_views. Still unshipped but AVAILABLE (probed 200,
low value): summary_emails CRUD, companies/bulk_update.json
(external_form_capture only), form_submissions/ignored_fields.json.

⚠️ CallRail breaking change 2026-05-21: transcripts (endpoint + fields)
return 404/null without Premium Conversation Intelligence.
get_call_transcript surfaces a hint on 404.

CallRail also now ships an official hosted OAuth MCP server (~30
tools, URL via account team), see README comparison section.

Endpoints we built but the user should know are permission-sensitive:
- `create_outbound_call` works on standard accounts but **places a
  real phone call**, requires `confirm_dialing=True` safety guard.
- `create_notification` works; `alert_type` is a closed enum (all,
  first_call, missed_and_vm, vm_only; CallRail rejects legacy values with
  "Alert type Invalid Call Type") and `notification_type` is push, email
  or desktop. `company_id` on GET /notifications is ignored server-side,
  so `list_notifications` filters client-side. `name` is auto-generated.
- Form submission endpoints return 403 while no company has Form Tracking
  enabled. Account state, not a permission; the tools surface a hint.

Endpoints CallRail does NOT expose via API (UI only on standard
plans):
- Outbound Caller IDs (verification flow)
- Numbers / porting / ownership moves
- Call Flows (IVR builder)
- Custom Fields CRUD (only readable as part of call/form responses)
- Do Not Call list management

## Known CallRail API quirks (don't re-learn these)

- DELETE on companies & trackers is a **soft-delete**. Records still appear in list responses unless `status="active"` filter used.
- Marking a call spam **hides it from default GETs**. Tag first, spam-flag last.
- `value` field on PUT /calls returns **HTTP 500** from CallRail. Do not expose on `update_call`. Works on form submissions.
- `Retry-After` can be seconds-int OR HTTP-date. `_parse_retry_after` handles both + caps at 60s.
- CallRail can return JSON arrays where docs suggest objects, `_parse` rejects non-object responses with CallRailError.
- Tag colors: 24 documented values (see VALID_TAG_COLORS). Anything else = 400. (Was 10 empirical values pre-2026-07; docs now enumerate.)
- Tracker source.type: 12 known-valid values (10 documented + facebook_all/bing_all). Anything else = 400. For multi-source DNI use `type='session'` pools.
- Tag create/`add_call_tags` with unknown names **auto-creates tags at the company level** as a side effect.
- `confirm_billing=True` is REQUIRED on `create_tracker`, guarding against accidental automated provisioning. Costs money (~$3/mo/number).
- Voicemail calls carry `answered=true` (the system answered). `_human_answered()` is the rule for "a person picked up"; `answer_status=missed` includes voicemail on CallRail's side.
- No date params means CallRail's `recent` range (previous 7 days incl. today), not all-time. Communication Records are retained 25 months; windows past that (including `date_range=all_time`) return an error.
- `time_zone` must be an IANA name (CallRail's 400 lists them). Company records can carry legacy abbreviations (EST, PDT); `_LEGACY_TZ_TO_IANA` maps them before use.
- `value` is a STRING on PUT /form_submissions, PUT /sms-threads and PUT /calls ("$1.00" or "1.00"). The 500 on update_call(value) was observed while sending a float; the string form is untested live, so `value` stays off update_call.
- PUT /trackers replaces the whole `call_flow` object and has no top-level destination_number. `update_tracker` GETs the tracker and merges; advanced (IVR) flows are refused.
- POST /companies accepts only `name` and `time_zone`; every toggle is a PUT field. `lead_scoring_enabled` is read-only; `swap_exclude_jquery` and `keyword_spotting_enabled` are deprecated no-ops.
- PUT /users has no `role`, and `email` needs the user's `current_password`. `update_user` rejects both. Names and email are editable only for the API key's own user.
- POST /form_submissions requires `form_url` + `form_data` and either `session_id` or the referrer trio. It takes no customer_*, note, tags, value or lead_status; the tool applies those with a follow-up PUT.
- Tags: `tag_level=account` cannot be combined with `company_id` on GET /tags; account-level creation needs an admin key and no company_id. `disabled` on PUT /tags is documented as a string ("true"/"false").
- `/calls/{id}/transcription.json` is undocumented and 404s. Transcript data comes from field selection on `/calls/{id}.json` (transcription, conversational_transcript, call_summary, call_highlights, keywords_spotted, sentiment, speaker_percent).
- Rate limits: 1,000/hour and 10,000/day for general requests (SMS send 150/hour, outbound call 100/hour). Not 60/minute.
- Recording URLs: non-HIPAA links are good for at least 24 hours (not guaranteed forever); HIPAA accounts get a temporary URL that expires in about 24 hours.
- `call_disposition` (field selection on calls; Sean McCrohan at CallRail pointed us at it, 2026-10) is becoming the explicit exclusive state: answered, missed, abandoned, voicemail, voicemail_abandoned, voice_assist. Live 2026-11-10 it mirrors `call_type` and returns "outbound" for outbound calls; the docs say that will become null. `_human_answered()` prefers it. `notice` is NOT a selectable field; it appears on its own when a transcript is plan-gated.
- `call_type` filter enum per the API's own 400: all, first_call, missed, abandoned, voicemails, inbound, outbound, voice_assist. `answer_status` also takes `all`. An unknown filter NAME (for example `disposition=x`) is silently ignored with a 200.
- Response headers carry `X-Rate-Limit-Hourly-Used/Allowed` and `X-Rate-Limit-Daily-Used/Allowed` (undocumented).
- `search` on GET /calls matches any fragment of customer_number as well as names, notes, source and the business number; `search_calls_by_number` uses it to narrow, then matches the suffix exactly.

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

## Current version: 1.3.2

See `CHANGELOG.md` for full history. Highlights:
- `0.1.0`: initial 12 read tools
- `0.2.0-0.2.4`: write tools (update_call, tag CRUD, form updates); 4 passes of bug fixing
- `0.3.0`: tracker CRUD + billing-confirmation safeguard
- `0.3.1`: `status` filter on `list_companies` / `list_trackers`
- `0.3.2`: tracker CRUD audit (12 bugs incl. CRITICAL: `update_tracker(greeting_text)` alone wiped destination)
- `0.3.3`: facebook_all/bing_all source types added; tracker_id slash bypass
- `0.4.0`: `usage_summary` + `call_eligibility_check` agency tools
- `0.4.1`: usage_summary CRITICAL pagination bug (was truncating at 250 calls/company)
- `0.4.2`: POST no-retry on 5xx, paginate total_pages handling, ID validation across older tools
- `0.4.3`: meta-audit: largest-remainder cost rounding, _err truncation, etc.
- `0.4.4`: Unicode-invisible char rejection, source-slug detection, extension stripping
- `0.4.5`: paginate companies, defensive total_pages cap
- `0.4.6`: partial-failure surfaces accumulated minutes (silent data loss fix), bool rejected as days, is_toll_free handles formatted numbers
- `0.4.7`: string-`days` no longer crashes `_date_window`; docstrings updated for length caps + source-slug semantics
- `0.4.8`: `days=10**18` no longer raises OverflowError from `timedelta` (capped at 36500/100yr)
- `0.5.0`: 3 new agency workflow tools: `compare_periods`, `bulk_update_calls`, `spam_detector` + TZ-aware `_date_window`
- `0.5.1`: Round 2 audit on v0.5.0: 11 bugs (Unicode tag filtering, partial_failures, TOCTOU race fix, broad exception catching, biggest_mover direction)
- `0.5.2`: Round 3 audit on v0.5.1: 8 bugs (HIGH: `_tag_names_from` non-list type-check; spam_detector days-cap at 90, dedup TZ warnings, auto_tag uses full filtered list)
- `0.5.3`: Round 4 audit on v0.5.2: 4 bugs (string-days bypass on spam_detector cap, auto_tag operation cap of 1000, docstring drift, test isolation for warning dedup)
- `0.5.4`: Round 5 cleanup (0 correctness bugs, 2 LOW style fixes, v0.5.x bug-hunt converged)
- `0.6.0`: **API parity push: 12 new tools** (Companies CRUD, Users CRUD, get_form_submission, get_text_message, list_webhooks, get_webhook). API surface coverage 50% → 75%
- `0.6.1`: Audit on v0.6.0: 9 bugs (2 HIGH: create_company always-sending bool toggles could DISABLE paid features; create_user(role='') slipped through)
- `0.7.0`: Final API parity push: 8 more tools (get_tag, list/get_integration, create_form_submission, create_outbound_call w/ confirm_dialing safety, list/create/update/delete_notification). API coverage 75% → ~85%
- `1.0.0`: **First stable release published to PyPI.** Locked feature surface; all remaining gaps are documented as out-of-scope (account-permission-gated or UI-only on standard CallRail plans).
- `1.1.0` to `1.2.2`: leads, SMS threads, server-side analytics, filter corrections, house style, automated PyPI + registry publishing.
- `1.3.2`: second sweep (2026-11-10): `call_disposition`, new call types, nine review fixes (429-then-404 delete, numeric ids at the schema, transcript `available`, non-IANA time zones).
- `1.3.0`: **Health sweep against the current docs and live API.** Voicemail counted separately from answered; 40+ documented filters and `fields` options wired through; request contracts corrected for companies, users, form submissions, trackers, tags and notifications; call-flow merge on `update_tracker`; tool annotations; `CALLRAIL_ACCOUNT_ID`.

**Tests: 517 passing. 57 tools total. mypy --strict + ruff + pytest -W error + bandit all clean.**

## Future work (deferred, see "API coverage limits" above)

Still out of scope as of v1.3.0; will work on if/when:
1. SMS send (when account gets A2P SMS API permission)
2. Webhook integration CRUD (when account gets Integration-Admin permission)
3. Outbound Caller IDs (if CallRail exposes via API)
4. Custom Fields CRUD (if CallRail exposes via API)
5. Do-not-call list management (if CallRail exposes via API)
6. Call flows / IVR builder (if CallRail exposes via API)
7. Toll-free minute pricing differentiation in `usage_summary`
8. `export_calls_to_csv` for client-deliverable reporting
9. `value` on `update_call` as a currency string (the documented type; needs one live write to confirm the old 500 was a type problem)
10. `append_tags` on PUT /calls (documented; the current GET + merge + PUT in `add_call_tags` works, so low priority)
11. `call_flow_uuid` on trackers to assign an existing advanced flow (documented as `{"type": "advanced", "uuid": ...}`)

Not yet started.
