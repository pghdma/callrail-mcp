# Minimal image for the callrail-mcp stdio server.
# Glama uses this for introspection / health checks.
FROM python:3.12-slim

WORKDIR /app

# Install the package from PyPI (already published as v1.0.0).
RUN pip install --no-cache-dir callrail-mcp

# CallRail API key must be supplied at runtime via env var or mounted file:
#   docker run -e CALLRAIL_API_KEY=... ghcr.io/pghdma/callrail-mcp
# or
#   docker run -v ~/.config/callrail:/root/.config/callrail ghcr.io/pghdma/callrail-mcp
ENTRYPOINT ["callrail-mcp"]
