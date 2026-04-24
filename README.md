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

- *"Pull last week's calls for Alan Construction, grouped by source"*
- *"Show me every missed call from Google Ads this month"*
- *"Find any calls from 412-555-1234 across all clients in the last 90 days"*
- *"What's the first-time-caller rate for Malick Brothers vs. Renaissance Electric?"*
- *"Get the transcript for call CAL019abc..."*
- *"List form submissions from today with gclid/utm data"*

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

**Read tools**

| Tool | Purpose |
|---|---|
| `list_accounts` | List accessible CallRail accounts |
| `list_companies` | List companies (clients) under an account |
| `list_trackers` | List tracking phone numbers + their source mapping |
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

**Write tools** *(new in v0.2)*

| Tool | Purpose |
|---|---|
| `update_call` | Update note, tags, value, spam flag, customer name, lead status |
| `add_call_tags` / `remove_call_tags` | Additive/subtractive tag changes (preserves existing) |
| `update_form_submission` | Same field surface as `update_call` for form entries |
| `create_tag` / `update_tag` / `delete_tag` | Full CRUD on the per-company tag taxonomy |

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

(Claude calls list_companies → returns 19 clients with IDs and primary numbers)

> Pull today's calls for company COM019ab... — include source and keyword.

(Claude calls list_calls with company_id, days=1, fields="source,keywords,landing_page_url")
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
