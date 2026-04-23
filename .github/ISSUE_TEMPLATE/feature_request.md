---
name: Feature request
about: Suggest a new tool or capability
title: "[Feature] "
labels: enhancement
assignees: ''
---

## Use case

What are you trying to do with your MCP client (Claude, Cursor, etc.)? Describe the real workflow — e.g. *"I want to ask Claude to list all missed calls from Google Ads that lasted under 10 seconds so we can flag ad-copy misfires."*

## Proposed tool / API surface

If you have a specific tool signature in mind, sketch it:

```python
@mcp.tool()
def new_tool(arg: str, ...) -> str:
    """What it does."""
```

Or describe the CallRail API endpoint you'd like wrapped (see <https://apidocs.callrail.com/>).

## Alternatives considered

What workarounds exist today? What's wrong with them?

## Additional context

Links, screenshots, related issues.
