# Changelog

All notable changes to `negotiate-mcp`. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

(Track upcoming work here. Move into the next versioned section at release time.)

---

## 0.2.0 — *unreleased*

> Note: this entry covers the audit-driven engineering pass. Set the date when `twine upload` lands and update this line.

### Added
- **Tool annotations** on every tool (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`). Required for the Anthropic Connectors Directory and any other MCP catalog that consumes annotations.
  - `find_stores`, `discover_store`, `list_products`, `read_history` — `readOnlyHint=True`, `idempotentHint=True`, `openWorldHint=True`.
  - `start_negotiation` — `readOnlyHint=False`, `destructiveHint=False`, `idempotentHint=False`, `openWorldHint=True` (creates a session record at the merchant; additive, not destructive; not idempotent because each call spawns a new session).
  - `send_message` — same as `start_negotiation`. Note: structurally non-destructive at the MCP layer, but a shopper turn can functionally commit ("I accept that offer") because the merchant agent interprets natural language.
- **`_validate_outbound_url()` SSRF guard** on `send_message` and `read_history`. Rejects non-HTTPS schemes (`file://`, `ftp://`, etc.), RFC1918 hosts (`10.*`, `192.168.*`, `172.16-31.*`), loopback (`127.*`, `localhost`), link-local (`169.254.*` — covers AWS/GCP metadata), and IPv6 loopback / link-local / ULA. Blocks pivot attempts via crafted `next_url` / `history_url` arguments.
- **Per-IP rate limiter** (`_rate_limit_middleware`) for the hosted streamable-HTTP transport. Token bucket: 60 req/min sustained, 10-token burst. Returns `429` with `Retry-After`, `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset` headers. Successful responses also carry `X-RateLimit-*` headers for client self-throttling. Tunable via `RATE_LIMIT_PER_MINUTE`, `RATE_LIMIT_BURST`, `RATE_LIMIT_DISABLED` env vars. Stdio transport is unaffected.
- **Defensive integer parsing** in `list_products`. A non-integer `limit` or `offset` now returns a friendly `RuntimeError` instead of crashing the JSON-RPC transport.
- **CI test suite** at `tests/test_annotations.py` — fails the build if any tool ships without annotations or if the annotation matrix drifts from the audit ground-truth. Run `pytest tests/`.
- **`CHANGELOG.md`** (this file).

### Changed
- **Transport security is properly configured** via `TransportSecuritySettings(allowed_hosts=..., allowed_origins=...)` on FastMCP construction. Defaults: `mcp.pier39.ai`, `localhost`, `127.0.0.1` for hosts; `claude.ai`, `*.claude.com`, `*.anthropic.com` for origins. Override via `ALLOWED_HOSTS` and `ALLOWED_ORIGINS` env vars.
- **Documentation overhauled.** README now includes **Example flows** (two end-to-end traces showing real tool composition), a complete **Tool reference** (per-tool signatures, inputs/outputs, errors, annotations, examples), **FAQ**, **Limits**, **Privacy & telemetry**, **Authentication**, and **Support** sections. ~330 lines of new content.
- **PRIVACY.md expanded** to cover connector telemetry explicitly: what's sent (`tool`, `brand_slug`, `client`), what's NOT sent (no message content, no URLs, no PII), retention (30 days), and how to disable (`TELEMETRY_DISABLED=1`). New "Connector telemetry (negotiate-mcp)" section under "What we collect".
- **`__init__.py` `__version__` synced** to match `pyproject.toml`. (Was `0.1.2` while `pyproject.toml` said `0.1.4` — drift caught by the audit.)
- **README intro and tool table** updated to reflect the current six-tool surface (was claiming five).

### Removed
- `_disable_mcp_host_validation()` — reached into `mcp.server.transport_security.TransportSecurityMiddleware` and replaced `_validate_host` and `_validate_origin` with no-ops. Anthropic security reviewers flag this kind of monkey-patching.
- `_wrap_with_host_rewriter()` — ASGI middleware that lied about the Host header (rewrote every incoming `Host:` to `localhost`). Replaced by proper `allowed_hosts` configuration.

### Migration notes for self-hosted operators

If you run a fork of `negotiate-mcp` at your own hostname, **read this before deploying 0.2.0**:

1. **Set `ALLOWED_HOSTS` and `ALLOWED_ORIGINS` env vars** before deploying. The previous default of `*` is gone; the new defaults cover only `mcp.pier39.ai`, `localhost`, and the Anthropic surfaces. If your hosted instance lives at a different hostname, add it explicitly:
   ```bash
   fly secrets set \
     ALLOWED_HOSTS=mcp.your-domain.example,localhost,127.0.0.1 \
     ALLOWED_ORIGINS=https://claude.ai,https://*.claude.com,https://*.anthropic.com \
     -a your-app
   ```
   Without this, FastMCP rejects every request with a 421.
2. **Pin `mcp>=1.0`** in your environment if you weren't already. Older FastMCP versions don't accept `transport_security`.
3. **Rate limiting is now on by default** at the hosted endpoint. Default cap is 60 req/min/client with a 10-token burst. Override with `RATE_LIMIT_PER_MINUTE` and `RATE_LIMIT_BURST` if your traffic profile requires it. Set `RATE_LIMIT_DISABLED=1` to turn it off entirely.
4. **The `next_url` / `history_url` SSRF guard** rejects private IP ranges. If you intentionally point those at a non-public test endpoint (e.g. `127.0.0.1` during local development), use stdio transport instead, or set up a public-facing hostname for tests.

For the hosted reference instance at `mcp.pier39.ai`, all of this is already configured by the maintainers.

---

## 0.1.4 and earlier

See `git log` and the [PyPI release history](https://pypi.org/project/negotiate-mcp/#history). Pre-0.2.0 releases predate the Anthropic Connectors Directory submission process and don't carry tool annotations.
