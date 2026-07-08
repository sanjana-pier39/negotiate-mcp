# Dockerfile for the hosted `negotiate-mcp` connector (Fly app: negotiate-mcp,
# served at mcp.pier39.ai/mcp). The connector is a plain Python package; in
# production it runs the streamable-HTTP transport under uvicorn.
#
# NOTE: this file was reconstructed after the original (untracked, never
# committed) was lost in a folder reorg. Pair it with the fly.toml restored via
# `fly config save --app negotiate-mcp` so the service port / health check /
# machine-warmth settings match the live app.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HOST=0.0.0.0 \
    PORT=8000 \
    MCP_TRANSPORT=streamable-http \
    PYTHONPATH=/app

WORKDIR /app

# Install DEPS only (mcp pulls pydantic/starlette/httpx; uvicorn for the HTTP
# transport). We deliberately do NOT rely on the installed package copy for the
# app code — PYTHONPATH=/app makes `import negotiate_mcp` resolve to the COPYd
# source below, so a stale/cached pip layer can never serve old code.
COPY . /app
RUN pip install -e . uvicorn

EXPOSE 8000

# Console script from pyproject ([project.scripts] negotiate-mcp). With
# MCP_TRANSPORT=streamable-http it binds $HOST:$PORT and serves /mcp.
CMD ["negotiate-mcp"]
