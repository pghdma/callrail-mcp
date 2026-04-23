# Contributing to callrail-mcp

Thanks for your interest in improving this project! Contributions of all kinds are welcome — bug reports, feature ideas, documentation, and code.

## Ground rules

- Be respectful. This project follows the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md).
- Discuss non-trivial changes in an issue first so we can agree on scope before you invest time.
- Keep pull requests focused. Small, single-purpose PRs are easier to review and land.

## Dev setup

Requires Python 3.10+.

```bash
git clone https://github.com/pghdma/callrail-mcp
cd callrail-mcp
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Running tests

```bash
pytest
```

All tests use mocked HTTP via the `responses` library — no real CallRail API key is required for the suite.

## Linting + type checking

```bash
ruff check src tests
ruff format src tests
mypy src
```

CI runs `ruff check` and `pytest` on every push and PR against Python 3.10, 3.11, 3.12, and 3.13. Mypy is run non-blocking (for now).

## Commit style

- Use imperative mood: *"Add retry-after handling"*, not *"Added retry-after handling"*.
- Reference issues: *"Fix pagination off-by-one (#42)"*.
- Keep the first line under 72 characters. Body optional but encouraged for non-trivial changes.

## Adding a new CallRail endpoint

1. Open `src/callrail_mcp/server.py`.
2. Add a `@mcp.tool()`-decorated function. Follow the docstring + typing conventions in existing tools.
3. Reuse `client.get()` / `client.paginate()` — don't hit `requests` directly.
4. Add tests in `tests/test_client.py` (if the change touches the client) or a new `tests/test_tools.py` (for server-level behavior).
5. Update `README.md`'s tool table.
6. Update `CHANGELOG.md` under `## [Unreleased]`.

## Running the MCP locally

With a real API key in `~/.config/callrail/api-key.txt` (mode 600) or `CALLRAIL_API_KEY`:

```bash
python -m callrail_mcp
```

It will wait for MCP stdio messages. To smoke-test the API without running the server:

```bash
python -c "from callrail_mcp.client import CallRailClient; print(CallRailClient().get('a.json'))"
```

## Releases

Maintainers cut releases via GitHub. The flow:

1. Bump the version in `pyproject.toml` and `src/callrail_mcp/__init__.py`.
2. Update `CHANGELOG.md` — move items from `## [Unreleased]` under a new `## [x.y.z] - YYYY-MM-DD` heading.
3. Commit: `chore: release x.y.z`.
4. Tag: `git tag vX.Y.Z && git push --tags`.
5. Create a GitHub Release from the tag — the `publish.yml` workflow will build and publish to PyPI via trusted publishing.

## Reporting bugs

Please use the [bug report template](.github/ISSUE_TEMPLATE/bug_report.md). Include:

- Python version
- `callrail-mcp` version
- MCP client (Claude Code / Desktop / Cursor / etc.) and version
- Minimal steps to reproduce
- Redacted logs or error output — never paste your API key.

## Requesting features

Use the [feature request template](.github/ISSUE_TEMPLATE/feature_request.md). Describe the use case in plain terms — what are you trying to ask Claude (or another MCP client) to do?

## Questions

Open a [Discussion](https://github.com/pghdma/callrail-mcp/discussions) or email [s@pghdma.com](mailto:s@pghdma.com).
