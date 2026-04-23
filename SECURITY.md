# Security Policy

## Supported versions

Security fixes are applied to the latest minor release. Older releases are not maintained.

| Version | Supported |
|---|---|
| 0.1.x | ✅ |

## Reporting a vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Instead, please email [s@pghdma.com](mailto:s@pghdma.com) with:

- A description of the vulnerability
- Steps to reproduce (minimal proof-of-concept preferred)
- The affected version(s)
- Your suggested severity rating
- Optional: whether you'd like credit in the release notes

You should receive an initial response within **72 hours**. If the issue is confirmed, we'll work on a fix and coordinate disclosure timing with you.

## Handling your CallRail API key

This project never logs, stores, or transmits your CallRail API key anywhere other than:

- In memory during server runtime
- In the `Authorization` header sent to `api.callrail.com` over HTTPS

Storage locations you control:

- `CALLRAIL_API_KEY` environment variable (ephemeral)
- `~/.config/callrail/api-key.txt` file (recommended: `chmod 600`)
- `CALLRAIL_API_KEY_FILE` pointing to an alternate file

Never commit your key to a repository. The included `.gitignore` blocks common filename patterns, but review your commits before pushing.

If your key is ever exposed, **revoke it immediately** at <https://app.callrail.com/settings/api-keys> and rotate.

## Dependency updates

This project has two runtime dependencies: `mcp` and `requests`. CI covers Python 3.10–3.13 on every push. Dependabot is configured to propose version bumps.
