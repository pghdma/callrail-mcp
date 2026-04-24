# callrail-mcp

[![PyPI version](https://img.shields.io/pypi/v/callrail-mcp.svg)](https://pypi.org/project/callrail-mcp/)
[![Python versions](https://img.shields.io/pypi/pyversions/callrail-mcp.svg)](https://pypi.org/project/callrail-mcp/)
[![CI](https://github.com/pghdma/callrail-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/pghdma/callrail-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![MCP](https://img.shields.io/badge/MCP-compatible-blue)](https://modelcontextprotocol.io/)

A [Model Context Protocol](https://modelcontextprotocol.io/) server that exposes the [CallRail REST API v3](https://apidocs.callrail.com/) to any MCP-compatible client (Claude Code, Claude Desktop, Cursor, etc.).

Created by **[Steve Japalucci](https://github.com/pghdma)** — Founder of [Pittsburgh Digital Marketing Agency (PGHDMA)](https://pghdma.com).

## What you can ask Claude to do

Once installed, any MCP-aware assistant can answer things like:

**Reporting**
- *"Pull last week's calls for Alan Construction, grouped by source"*
- *"Show me every missed call from Google Ads this month"*
- *"Find any calls from 412-555-1234 across all clients in the last 90 days"*
- *"Get the transcript for call CAL019abc..."*

**Agency cost attribution** *(new in v0.4)*
- *"Why is my CallRail bill $174? Break it down by client"*
- *"Which client is the biggest minute user this cycle?"*

**Conversion debugging** *(new in v0.4)*
- *"Why didn't this call convert in Google Ads? CAL019..."*
- *"Is this 58-second call eligible to count as a Google Ads conversion?"*

**Tag + tracker management**
- *"Tag this call as 'lead' and add a note"*
- *"Provision a new Google Ads call-extension tracker for Renaissance in area code 412"* *(requires `confirm_billing=True` — costs ~$3/mo)*

## Installation

> **Heads-up:** this package is not yet on PyPI. Install directly from GitHub
> until the first release lands. Drop the `git+` URL for the PyPI commands once
> it's published.

```bash
# from GitHub (current — works today)
pip install git+https://github.com/pghdma/callrail-mcp.git

# or with pipx for isolation (recommended for the MCP CLI)
pipx install git+https://github.com/pghdma/callrail-mcp.git
```

Once published to PyPI, you'll be able to use the shorter form:

```bash
pip install callrail-mcp
# or
pipx install callrail-mcp
```

## Auth

Get an API key at **Settings → API Keys** in your CallRail account. You need Account Admin permission to create one.

Provide it one of two ways:

### Option 1: environment variable (recommended for most setups)

```bash
export CALLRAIL_API_KEY="your_key_here"
```

### Option 2: key file

```bash
mkdir -p ~/.config/callrail
echo "your_key_here" > ~/.config/callrail/api-key.txt
chmod 600 ~/.config/callrail/api-key.txt
```

Or override with `CALLRAIL_API_KEY_FILE=/path/to/key.txt`.

## Configure your MCP client

### Claude Code / Claude Desktop (`~/.claude.json` or `claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "callrail": {
      "command": "callrail-mcp",
      "env": {
        "CALLRAIL_API_KEY": "your_key_here"
      }
    }
  }
}
```

If you installed via pipx, `callrail-mcp` will be on your PATH automatically. Otherwise, point `command` at the full path to the executable.

### Cursor / other clients

The server speaks standard MCP stdio. Any client that supports stdio MCP servers will work — just run `callrail-mcp` as the command.

## Available tools

**49 tools total** — ~85% of CallRail's REST API v3 surface. Read tools, write tools, tracker provisioning, agency aggregation, account management (Companies/Users CRUD), notifications, integrations discovery, outbound calls, and offline-lead backfill via `create_form_submission`.

### Read tools

| Tool | Purpose |
|---|---|
| `list_accounts` | List accessible CallRail accounts |
| `list_companies` | List companies (clients) under an account. Optional `status="active"` filter |
| `list_trackers` | List tracking phone numbers + their source mapping. Optional `status="active"` filter |
| `get_tracker` | Full detail for one tracker |
| `list_calls` | Paginated call list — filter by company / date / source / answered |
| `get_call` | Full detail for a specific call |
| `call_summary` | Aggregate stats (total, answered, by source, duration) for a window |
| `list_form_submissions` | CallRail Form Tracking submissions |
| `list_text_messages` | SMS conversations |
| `list_users` | Account users |
| `get_call_recording` | Recording URL (if recording enabled) |
| `get_call_transcript` | Conversation Intelligence transcript |
| `search_calls_by_number` | Find calls by phone number across a window |
| `list_tags` | List tags in account or filtered to one company |

### Write tools *(v0.2+)*

| Tool | Purpose |
|---|---|
| `update_call` | Update note, tags, spam flag, customer name, lead status |
| `add_call_tags` / `remove_call_tags` | Additive/subtractive tag changes (preserves existing) |
| `update_form_submission` | Same field surface as `update_call`, **plus** `value` (numeric, supported on form submissions but NOT on calls — CallRail returns 500) |
| `create_tag` / `update_tag` / `delete_tag` | Full CRUD on the per-company tag taxonomy |

### Tracker provisioning *(v0.3+)*

| Tool | Purpose |
|---|---|
| `create_tracker` | Provision a new tracking number. **Requires `confirm_billing=True`** as a safety guard against accidental AI provisioning |
| `update_tracker` | Update mutable settings: name, destination, whisper, greeting, SMS |
| `delete_tracker` | Soft-delete a tracker (releases the phone number, preserves history) |

### Account management *(v0.6+)*

| Tool | Purpose |
|---|---|
| `get_company` / `create_company` / `update_company` / `delete_company` | Full company (client) CRUD. Free — CallRail bills per number, not per company. Soft-delete semantics |
| `get_user` / `create_user` / `update_user` / `delete_user` | Full user CRUD. `create_user` invites by email; common roles: admin / manager / reporting / analyst |
| `get_form_submission` | Single form-submission detail (was list+update only) |
| `get_text_message` | Single SMS conversation detail with all messages |
| `list_webhooks` / `get_webhook` | Discover existing webhook subscriptions (CRUD coming in v0.6.1) |

Validation is strict: phone-number format, area code (`^\d{3}$`), `pool_size` ∈ [1, 50] (safety cap to prevent accidental 5-figure provisioning bills), name/whisper/greeting length caps, source-type enum (`all`, `direct`, `offline`, `google_my_business`, `google_ad_extension`, `facebook_all`, `bing_all`).

### Agency aggregation *(v0.4+)*

| Tool | Purpose |
|---|---|
| `usage_summary` | Per-company cost-attribution breakdown for the cycle. Returns minutes used, active numbers, estimated $ cost share — sorted by biggest cost driver. Useful for "which client is burning my CallRail budget" |
| `call_eligibility_check` | Audit whether a specific call qualifies as a Google Ads conversion. Checks `gclid` presence, answered-status, duration vs. Google's threshold (default 60s), and source. Useful for "where did my conversion go" debugging |
| `compare_periods` *(v0.5)* | Compare current N-day window vs previous N-day window. Per-company minute/call deltas + biggest mover. Catches traffic trends before they hit the invoice |
| `bulk_update_calls` *(v0.5)* | Apply a single update (tag / note / lead_status / spam) to every call matching a filter. `dry_run=True` by default; surfaces truncation at 500-cap. Replaces dozens of sequential `update_call` invocations |
| `spam_detector` *(v0.5)* | Heuristically flag likely-spam calls (short duration, unanswered, repeat-caller patterns). Optional `auto_tag=True` adds `auto_detected_spam` tag. Deliberately does NOT set `spam=True` (that would hide the call from default GETs) |

All tools accept `account_id` optionally — if omitted, the first accessible account is auto-resolved. Most accept `company_id` to filter to a single client.

### Rich field selection

The CallRail API returns a lean default payload. Ask for more fields on `list_calls` / `get_call` / `list_form_submissions` via the `fields` parameter:

```
fields=company_name,source_name,keywords,landing_page_url,device,first_call,value,tags,note,gclid,fbclid,utm_source,utm_medium,utm_campaign,utm_content,utm_term,referrer_domain
```

See the [CallRail API docs](https://apidocs.callrail.com/) for the full field catalog per resource.

## Examples

### Claude Code

```
> List companies under our CallRail account.

(Claude calls list_companies → returns clients with IDs and primary numbers)

> Pull today's calls for company COM019ab... — include source and keyword.

(Claude calls list_calls with company_id, days=1, fields="source,keywords,landing_page_url")

> Why is my CallRail bill $174 this month? Break it down by client.

(Claude calls usage_summary → returns per-company cost share, sorted by biggest user)

> Why didn't this call show up as a conversion in Google Ads? CAL019dbf79...

(Claude calls call_eligibility_check → returns gclid/duration/answered checks
 + targeted reason like "duration 58s under Google Ads minimum (60s)")

> Provision a new Google-Ads-call-extension tracker for Alan Construction in 412.

(Claude calls create_tracker — refuses unless you also pass confirm_billing=True
 since it incurs a ~$3/mo charge)
```

### Direct Python usage

The `CallRailClient` is also usable as a library:

```python
from callrail_mcp.client import CallRailClient

cr = CallRailClient()  # picks up CALLRAIL_API_KEY
aid = cr.resolve_account_id()
for call in cr.paginate(f"a/{aid}/calls.json", {"per_page": 250}, items_key="calls"):
    print(call["id"], call.get("source"), call.get("customer_name"))
```

## Running the server directly

For debugging or to verify your key works:

```bash
python -m callrail_mcp
```

The server speaks MCP stdio. It will wait for JSON-RPC messages on stdin. Ctrl-C to exit.

To smoke-test the API key without running the MCP loop:

```python
python -c "from callrail_mcp.client import CallRailClient; c=CallRailClient(); print(c.get('a.json'))"
```

## Rate limits

CallRail allows 60 requests/minute per API key. The client retries 429 responses using the `Retry-After` header, and 5xx responses with exponential backoff (max 3 retries by default). For heavy pagination, prefer the built-in `paginate()` helper which uses `per_page=100` by default.

## Development

```bash
git clone https://github.com/pghdma/callrail-mcp
cd callrail-mcp
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## Contributing

Contributions welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) for dev setup, test conventions, and release flow. Please file issues via [GitHub Issues](https://github.com/pghdma/callrail-mcp/issues) and follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Security

If you discover a security vulnerability, please report it privately per [SECURITY.md](SECURITY.md) instead of opening a public issue.

## Author

**[Steve Japalucci](https://github.com/pghdma)** — Founder of [Pittsburgh Digital Marketing Agency](https://pghdma.com). Reach out at [s@pghdma.com](mailto:s@pghdma.com).

## License

MIT — see [LICENSE](LICENSE). Copyright © 2026 Steve Japalucci / Pittsburgh Digital Marketing Agency.

## Disclaimer

This project is an independent open-source integration and is **not affiliated with, endorsed by, or officially supported by CallRail**. "CallRail" is a trademark of CallRail, Inc. All product names, logos, and brands are property of their respective owners.
