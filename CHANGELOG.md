# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
