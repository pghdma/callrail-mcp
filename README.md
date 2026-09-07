<!-- mcp-name: io.github.pghdma/callrail-mcp -->

# callrail-mcp

[![PyPI version](https://img.shields.io/pypi/v/callrail-mcp.svg)](https://pypi.org/project/callrail-mcp/)
[![Python versions](https://img.shields.io/pypi/pyversions/callrail-mcp.svg)](https://pypi.org/project/callrail-mcp/)
[![CI](https://github.com/pghdma/callrail-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/pghdma/callrail-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![MCP](https://img.shields.io/badge/MCP-compatible-blue)](https://modelcontextprotocol.io/)
[![Available on CodeGuilds](https://img.shields.io/badge/Available_on-CodeGuilds-6366f1)](https://codeguilds.dev/packages/callrail-mcp)

A [Model Context Protocol](https://modelcontextprotocol.io/) server that exposes the [CallRail REST API v3](https://apidocs.callrail.com/) to any MCP-compatible client (Claude Code, Claude Desktop, Cursor, and others).

Created by **[Steve Japalucci](https://github.com/pghdma)**, founder of [Pittsburgh Digital Marketing Agency (PGHDMA)](https://pghdma.com).

Works with both major versions of the MCP Python SDK (1.x and 2.x).

## What you can ask your assistant to do

Once installed, any MCP-aware assistant can answer things like:

**Reporting**

- *"Pull last week's calls for Alan Construction, grouped by source"*
- *"Show me every missed call this month"*
- *"Find any calls from 412-555-1234 across all clients in the last 90 days"*
- *"Get the transcript for call CAL019abc..."*

**Agency cost attribution**

- *"Why is my CallRail bill $174? Break it down by client"*
- *"Which client is the biggest minute user this cycle?"*

**Conversion debugging**

- *"Why didn't this call convert in Google Ads? CAL019..."*
- *"Is this 58-second call eligible to count as a Google Ads conversion?"*

**Lead management**

- *"Show me everything this person has ever done: calls, forms, texts"*
- *"Tag this SMS thread as a qualified lead and add a note"*

**Tag and tracker management**

- *"Tag this call as 'lead' and add a note"*
- *"Provision a new Google Ads call-extension tracker for Renaissance in area code 412"* (requires `confirm_billing=True`, costs about $3/mo)

## Installation

```bash
# Recommended: pipx for an isolated CLI install
pipx install callrail-mcp

# Or with pip
pip install callrail-mcp
```

To install from source (latest unreleased):

```bash
pipx install git+https://github.com/pghdma/callrail-mcp.git
```

## Auth

Get an API key at **Settings > API Keys** in your CallRail account. You need Account Admin permission to create one.

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

### Cursor and other clients

The server speaks standard MCP stdio. Any client that supports stdio MCP servers will work; just run `callrail-mcp` as the command.

## Available tools

**57 tools total**, covering roughly 95% of CallRail's REST API v3 surface: read tools, write tools, tracker provisioning, agency aggregation, account management, notifications, integrations discovery, outbound calls, offline-lead backfill, leads and cross-channel timelines, SMS-thread lead management, and server-side analytics.

### Read tools

| Tool | Purpose |
|---|---|
| `list_accounts` | List accessible CallRail accounts |
| `list_companies` | List companies (clients) under an account. Optional `status="active"` filter and `page` |
| `list_trackers` | List tracking phone numbers and their source mapping. Optional `status="active"` filter |
| `get_tracker` | Full detail for one tracker |
| `list_calls` | Paginated call list. Filter by company, date, and `answer_status` |
| `get_call` | Full detail for a specific call |
| `call_summary` | Aggregate stats (total, answered, by source, duration) for a window |
| `list_form_submissions` | CallRail Form Tracking submissions |
| `list_text_messages` | SMS conversations |
| `list_users` | Account users |
| `get_call_recording` | Recording URL (if recording enabled) |
| `get_call_transcript` | Conversation Intelligence transcript (requires Premium CI since 2026-05-21) |
| `search_calls_by_number` | Find calls by phone number across a window |
| `list_tags` | List tags in an account or filtered to one company |

### Write tools

| Tool | Purpose |
|---|---|
| `update_call` | Update note, tags, spam flag, customer name, lead status |
| `add_call_tags` / `remove_call_tags` | Additive and subtractive tag changes (preserves existing) |
| `update_form_submission` | Same field surface as `update_call`, plus `value` (numeric, supported on form submissions but not on calls, where CallRail returns 500) |
| `create_tag` / `update_tag` / `delete_tag` | Full CRUD on the per-company tag taxonomy |

### Tracker provisioning

| Tool | Purpose |
|---|---|
| `create_tracker` | Provision a new tracking number. **Requires `confirm_billing=True`** as a safety guard against accidental provisioning. Supports source trackers and session (DNI) pools of 4 to 50 numbers |
| `update_tracker` | Update mutable settings: name, destination, whisper, greeting, SMS |
| `delete_tracker` | Soft-delete a tracker (releases the phone number, preserves history) |

### Account management

| Tool | Purpose |
|---|---|
| `get_company` / `create_company` / `update_company` / `delete_company` | Full company (client) CRUD. Free, since CallRail bills per number rather than per company. Soft-delete semantics |
| `get_user` / `create_user` / `update_user` / `delete_user` | Full user CRUD. `create_user` invites by email; roles: admin, manager, reporting |
| `get_tag` | Single tag detail |
| `get_form_submission` | Single form-submission detail |
| `get_text_message` | Single SMS conversation with all messages |
| `create_form_submission` | Manually create a form submission (backfill walk-in, paper-form, or offline leads) |

### Notifications and integrations

| Tool | Purpose |
|---|---|
| `list_notifications` / `create_notification` / `update_notification` / `delete_notification` | Full per-user alert-rule CRUD (who gets pinged on which call, text, or form event) |
| `list_integrations(company_id)` / `get_integration` | Discover GMB, Google Ads, Facebook, Slack, and Webhook integrations attached to a company. CallRail models webhooks as an integration type, so this is also how you inspect webhooks |

### Outbound calling

| Tool | Purpose |
|---|---|
| `create_outbound_call` | Place an outbound call. CallRail dials `business_phone_number` first, then bridges to `customer_phone_number`, showing `caller_id`. **Requires `confirm_dialing=True`** as a safety guard, since it dials real phones, costs minutes, and carries legal implications. US and Canada only |

### Leads and server-side analytics

| Tool | Purpose |
|---|---|
| `list_leads` / `get_lead_timeline` | CallRail's deduplicated person records, plus full cross-channel history (calls, forms, texts) per lead with first and last touch attribution |
| `list_sms_threads` / `get_sms_thread` / `update_sms_thread` | SMS-thread lead management: tag, note, and qualify texting leads the way you would calls |
| `call_stats` | Server-side call aggregation via `/calls/summary.json`. Group by company, company_id, source, keywords, campaign, referrer, landing_page, or last_requested_page in one request instead of paginating every call |
| `call_timeseries` | Call-volume trend line. Supports `interval` (hour, day, week, month, year) and guards CallRail's 200-data-point limit before sending |
| `form_stats` | Server-side form-submission totals |
| `get_call_page_views` | The visitor's page-view journey behind a call. Pairs with `call_eligibility_check` for conversion debugging |

### Agency aggregation

| Tool | Purpose |
|---|---|
| `usage_summary` | Per-company cost-attribution breakdown for the cycle: minutes used, active numbers, estimated cost share, sorted by biggest cost driver. Answers "which client is burning my CallRail budget" |
| `call_eligibility_check` | Audit whether a specific call qualifies as a Google Ads conversion. Checks `gclid` presence, answered status, duration against Google's threshold (default 60s), and source |
| `compare_periods` | Compare the current N-day window against the previous one. Per-company minute and call deltas plus biggest mover |
| `bulk_update_calls` | Apply one update (tag, note, lead_status, spam) to every call matching a filter. `dry_run=True` by default and capped at 500 calls |
| `spam_detector` | Heuristically flag likely-spam calls (short duration, unanswered, repeat-caller patterns). Optional `auto_tag=True` adds an `auto_detected_spam` tag. Deliberately does not set `spam=True`, which would hide the call from default GETs |

All tools accept `account_id` optionally; if omitted, the first accessible account is auto-resolved. Most accept `company_id` to filter to a single client.

### A note on filtering calls

CallRail's `GET /calls.json` accepts `answer_status` (`answered`, `missed`, `voicemail`) but has **no** `answered` or `source` parameter. Earlier versions of this server forwarded both and CallRail silently ignored them, so results looked filtered but were not. Since v1.2.0:

- `answer_status` is the documented filter and is applied server-side.
- `answered` remains as a deprecated alias that translates to `answer_status`.
- `source` is applied client-side, and the response includes a `source_filter` block stating exactly what was matched. For source breakdowns, prefer `call_stats(group_by="source")`.

Validation is strict throughout: phone-number format, area code (`^\d{3}$`), session pool size (CallRail's 4 to 50 range), name, whisper, and greeting length caps, and a 12-value source-type enum.

### Rich field selection

The CallRail API returns a lean default payload. Ask for more fields on `list_calls`, `get_call`, or `list_form_submissions` via the `fields` parameter:

```
fields=company_name,source_name,keywords,landing_page_url,device_type,first_call,value,tags,note,gclid,fbclid,utm_source,utm_medium,utm_campaign,utm_content,utm_term,referrer_domain
```

See the [CallRail API docs](https://apidocs.callrail.com/) for the full field catalog per resource.

## How this compares to CallRail's official MCP server

CallRail offers an official hosted MCP server (documented at [apidocs.callrail.com](https://apidocs.callrail.com/#mcp)) using OAuth 2.0, with roughly 30 tools and a server URL provided by your CallRail account team. It is a good option if you want a fully managed remote server.

This project is different on purpose:

| | callrail-mcp (this project) | Official CallRail MCP |
|---|---|---|
| Install | `pip install callrail-mcp`, running in two minutes | URL provisioned by your CallRail account team |
| Hosting | Local stdio, so your API key never leaves your machine | Hosted remote (OAuth) |
| Tools | 57 | ~30 |
| Agency tooling | `usage_summary` cost attribution, `compare_periods`, `spam_detector`, `bulk_update_calls`, `call_eligibility_check` | Not offered |
| Safety guards | `confirm_billing`, `confirm_dialing`, `dry_run` defaults, strict input validation | Not documented |
| Source | MIT, open, auditable | Closed |

Both speak the same underlying REST API v3. If you run an agency across multiple client accounts and want cost attribution and bulk workflows, this project is built for exactly that.

## Out of scope (deliberately not implemented)

The following CallRail capabilities are **not in this MCP**, by design. PRs are welcome if you have an account that supports them, or open an issue and we will prioritize.

### Blocked by CallRail account permissions (returns 403)

These endpoints exist but require account upgrades or additional permissions that a standard CallRail account does not have. Re-probed live 2026-09-07:

- **Send SMS/MMS** (`POST /text-messages.json`) needs A2P/TCR SMS registration. CallRail enforces TCPA-compliance keywords (STOP, CANCEL, UNSUBSCRIBE) on outbound text messages. MMS support was added by CallRail on 2026-05-05.
- **Webhook integration create, update, delete** (`POST /integrations.json` with `type=Webhook`) needs Integration-Admin permission.
- **Outbound Caller IDs** (`/caller_ids.json` CRUD) is documented by CallRail but returns 403 on a standard account.
- **Message Flows** (`/message-flows.json` CRUD, SMS auto-reply flows) is documented but returns 403.
- **Integration Filters** (`/integration_triggers.json` CRUD) is documented but returns 403.

### Not exposed by CallRail's REST API

These have no API equivalent and are managed exclusively via the CallRail web UI:

- **Numbers**: account-level number ownership, porting, and transfers.
- **Call Flows**: the IVR builder and call routing tree configuration.
- **Custom Fields CRUD**: custom data columns are readable as part of call and form responses, but the schema management endpoint is not exposed.
- **Do Not Call list**: DNC number management.

### Available but not yet shipped

Readable on a standard account, judged low value so far: `summary_emails` CRUD, `companies/bulk_update.json` (external form capture only), and `form_submissions/ignored_fields.json`.

## Examples

```
> List companies under our CallRail account.

(calls list_companies, returns clients with IDs and primary numbers)

> Pull today's calls for company COM019ab..., include source and keyword.

(calls list_calls with company_id, days=1, fields="source,keywords,landing_page_url")

> Why is my CallRail bill $174 this month? Break it down by client.

(calls usage_summary, returns per-company cost share sorted by biggest user)

> Why didn't this call show up as a conversion in Google Ads? CAL019dbf79...

(calls call_eligibility_check, returns gclid, duration, and answered checks
 plus a targeted reason such as "duration 58s under Google Ads minimum (60s)")

> Show me everything this lead has done with us.

(calls list_leads then get_lead_timeline, returns calls, forms, and texts in one timeline)

> Provision a new Google-Ads-call-extension tracker for Alan Construction in 412.

(calls create_tracker, refuses unless you also pass confirm_billing=True
 since it incurs a monthly charge)
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

`paginate()` accepts an optional `stats` dict that reports `pages_fetched`, `items_yielded`, `total_records`, and `truncated`, so you can tell a capped result from a complete one.

## Running the server directly

For debugging, or to verify your key works:

```bash
python -m callrail_mcp
```

The server speaks MCP stdio. It will wait for JSON-RPC messages on stdin. Press Ctrl-C to exit.

To smoke-test the API key without running the MCP loop:

```bash
python -c "from callrail_mcp.client import CallRailClient; c=CallRailClient(); print(c.get('a.json'))"
```

## Rate limits

CallRail allows 60 requests per minute per API key. The client retries 429 responses using the `Retry-After` header, and retries 5xx responses with exponential backoff for idempotent methods only (GET, PUT, DELETE, HEAD, OPTIONS). POST is never retried on 5xx, so a lost response cannot create duplicate trackers. For heavy pagination, prefer the built-in `paginate()` helper.

## Development

```bash
git clone https://github.com/pghdma/callrail-mcp
cd callrail-mcp
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

See [DEVELOPMENT.md](DEVELOPMENT.md) for release flow, API quirks worth knowing, and the conventions this codebase follows.

## Contributing

Contributions welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for dev setup, test conventions, and release flow. Please file issues via [GitHub Issues](https://github.com/pghdma/callrail-mcp/issues) and follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Security

If you discover a security vulnerability, please report it privately per [SECURITY.md](SECURITY.md) instead of opening a public issue.

## Author

**[Steve Japalucci](https://github.com/pghdma)**, founder of [Pittsburgh Digital Marketing Agency](https://pghdma.com). Reach out at [s@pghdma.com](mailto:s@pghdma.com).

## License

MIT. See [LICENSE](LICENSE). Copyright (c) 2026 Steve Japalucci / Pittsburgh Digital Marketing Agency.

## Disclaimer

This project is an independent open-source integration and is **not affiliated with, endorsed by, or officially supported by CallRail**. "CallRail" is a trademark of CallRail, Inc. All product names, logos, and brands are property of their respective owners.
