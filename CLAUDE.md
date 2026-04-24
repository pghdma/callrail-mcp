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
- Tracker source.type: only `all direct offline google_my_business google_ad_extension`. Anything else = 400. For Google/Bing/FB ads, use `type='session'` DNI pools instead of source trackers.
- Tag create/`add_call_tags` with unknown names **auto-creates tags at the company level** as a side effect.
- `confirm_billing=True` is REQUIRED on `create_tracker` — defensive against AI exploration. Costs money (~$3/mo/number).

## Current version: 0.3.1

See `CHANGELOG.md` for full history. Highlights:
- `0.1.0` — initial 12 read tools
- `0.2.0-0.2.4` — write tools (update_call, tag CRUD, form updates); 4 passes of bug fixing
- `0.3.0` — tracker CRUD (`create_tracker`/`update_tracker`/`delete_tracker`/`get_tracker`) + billing-confirmation safeguard
- `0.3.1` — `status` filter on `list_companies` and `list_trackers`

## Candidate features (ranked by agency utility)

1. `usage_summary` tool — per-client cost attribution (minutes × $0.05 + active-tracker count × $3)
2. Webhook subscribe/unsubscribe
3. Notifications config CRUD (who gets pinged on which call)
4. Custom field CRUD
5. Outbound call placement
6. Do-not-call list management

Not yet started. Ranked by the 2026-04-24 conversation with Steve.
