#!/usr/bin/env python3
"""
negotiate-mcp — MCP server for the negotiate.v1 protocol.

Once installed in Claude Desktop / Cowork / any MCP-aware client, this
exposes 5 native tools that drive the full negotiation flow:

  * discover_store(domain)            — probe and validate a store's protocol descriptor
  * list_products(domain)             — enumerate negotiable products at a store
  * start_negotiation(domain, product_id) — open a chat session with the merchant
  * send_message(next_url, message)   — send one shopper turn
  * read_history(history_url)         — read the running history of a session

The shopper agent uses these the way a human would use a browser: discover
the store, pick a product, start a chat, send turns until the deal closes.

Install:
    pip install mcp

Add to your Claude Desktop config (claude_desktop_config.json):
    See claude_desktop_config.example.json in this folder.

Run standalone for testing:
    python negotiate_mcp.py
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request

try:
    from mcp.server.fastmcp import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings
except ImportError:
    sys.exit(
        "Missing dependency: pip install 'mcp>=1.0'\n"
        "(The official Anthropic MCP SDK; ships FastMCP in mcp.server.fastmcp.)"
    )

def _build_mcp() -> FastMCP:
    """Construct FastMCP with proper transport security via env vars.

    FastMCP's TransportSecurityMiddleware does Host- and Origin-header
    validation on every request. The directory submission requires this to
    remain enabled, so we configure it explicitly rather than disabling it.

    Configure in production via env vars (e.g. `fly secrets set ...`):
        ALLOWED_HOSTS=mcp.pier39.ai
        ALLOWED_ORIGINS=https://claude.ai,https://*.claude.com,https://*.anthropic.com

    Defaults below cover localhost development plus the production hostname
    and the Anthropic surfaces that connect to us.
    """
    import os

    allowed_hosts = [
        h.strip() for h in os.environ.get(
            "ALLOWED_HOSTS",
            "mcp.pier39.ai,localhost,127.0.0.1",
        ).split(",") if h.strip()
    ]
    allowed_origins = [
        o.strip() for o in os.environ.get(
            "ALLOWED_ORIGINS",
            "https://claude.ai,https://*.claude.com,https://*.anthropic.com",
        ).split(",") if o.strip()
    ]

    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )
    return FastMCP("negotiate-agent", transport_security=security)


mcp = _build_mcp()

USER_AGENT = "negotiate-mcp/0.1 (+https://github.com/sanjana-pier39/pier39-skills)"
DEFAULT_TIMEOUT = 30


# ---------------------------------------------------------------------------
# Telemetry (fire-and-forget POST to pier39 backend)
# ---------------------------------------------------------------------------

import os as _os                              # noqa: E402
import threading as _threading                # noqa: E402

_TELEMETRY_URL = _os.environ.get(
    "TELEMETRY_URL", "https://pier39.fly.dev/api/telemetry"
).strip()
_TELEMETRY_DISABLED = _os.environ.get("TELEMETRY_DISABLED", "").lower() in (
    "1", "true", "yes",
)
_CLIENT_HINT = _os.environ.get("MCP_CLIENT", "").strip()  # e.g. "claude.ai"


def _domain_to_slug(domain: str) -> str | None:
    """Best-effort: pull the brand slug from a pier39.fly.dev/<slug> URL."""
    if not domain:
        return None
    d = domain.strip().lower().replace("http://", "").replace("https://", "").rstrip("/")
    if d.startswith("pier39.fly.dev/"):
        rest = d[len("pier39.fly.dev/"):].split("/", 1)[0]
        return rest or None
    return None


def _log_call(tool_name: str, brand_slug: str | None = None) -> None:
    """Fire-and-forget telemetry ping. Never blocks; errors are swallowed."""
    if _TELEMETRY_DISABLED or not _TELEMETRY_URL:
        return
    payload = json.dumps({
        "tool": tool_name,
        "brand_slug": brand_slug,
        "client": _CLIENT_HINT,
    }).encode("utf-8")

    def _send():
        try:
            req = urllib.request.Request(
                _TELEMETRY_URL,
                data=payload,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": USER_AGENT,
                },
            )
            urllib.request.urlopen(req, timeout=4).read()
        except Exception:
            pass  # silent — telemetry must never break a tool call

    _threading.Thread(target=_send, daemon=True).start()


def _http_get_json(url: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """GET a URL, parse JSON, raise on protocol/transport errors."""
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", errors="replace") if e.fp else str(e)
        raise RuntimeError(f"HTTP {e.code} from {url}: {msg[:300]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Could not reach {url}: {e.reason}")
    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"{url} did not return JSON: {e}\n{body[:300]}")


def _normalize_domain(domain: str) -> str:
    """Accept 'example.com', 'https://example.com', 'https://example.com/' — normalize to 'https://example.com'."""
    domain = domain.strip().rstrip("/")
    if not domain.startswith(("http://", "https://")):
        domain = "https://" + domain
    return domain


# ---------------------------------------------------------------------------
# SSRF guard for tools that take URL arguments from the model
# ---------------------------------------------------------------------------

# Block link-local, loopback, and RFC1918 ranges in URLs that come from
# external (model-supplied or merchant-supplied) input. Without this guard,
# a malicious next_url / history_url could pivot into cloud metadata
# services or internal RFC1918 networks.
_PRIVATE_HOST_PREFIXES = (
    "10.", "127.", "169.254.", "172.16.", "172.17.", "172.18.", "172.19.",
    "172.20.", "172.21.", "172.22.", "172.23.", "172.24.", "172.25.",
    "172.26.", "172.27.", "172.28.", "172.29.", "172.30.", "172.31.",
    "192.168.", "0.0.0.0",
)
_BLOCKED_HOSTS = {
    "localhost", "metadata.google.internal", "metadata", "instance-data",
}


def _validate_outbound_url(url: str) -> str:
    """
    Reject URLs that would let a crafted tool input pivot into the host
    network or cloud metadata services.

    Returns the URL unchanged on success; raises ValueError otherwise.
    """
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception as e:
        raise ValueError(f"Invalid URL: {e}")

    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"Refusing non-HTTP(S) URL scheme: {parsed.scheme!r}. "
            f"Negotiate only follows http(s) endpoints."
        )

    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError(f"URL has no host: {url!r}")

    if host in _BLOCKED_HOSTS:
        raise ValueError(f"Refusing internal hostname: {host!r}")

    if any(host.startswith(p) for p in _PRIVATE_HOST_PREFIXES):
        raise ValueError(
            f"Refusing private/loopback IP: {host!r}. "
            f"Negotiate only follows public-internet URLs."
        )

    if host == "::1" or host.startswith("fe80:") or host.startswith("fc") or host.startswith("fd"):
        raise ValueError(f"Refusing IPv6 loopback/link-local/ULA: {host!r}")

    return url


# ---------------------------------------------------------------------------
# Per-IP rate limiting for the hosted streamable-HTTP transport
# ---------------------------------------------------------------------------
#
# Token-bucket rate limiter, applied as ASGI middleware in front of the
# FastMCP app. Per-client (per-IP) limits, in-memory state per process.
#
# Tunable via env vars:
#   RATE_LIMIT_PER_MINUTE   (default 60)  — sustained rate, tokens/minute
#   RATE_LIMIT_BURST        (default 10)  — extra tokens above sustained rate
#                                           (allows short bursts)
#   RATE_LIMIT_DISABLED     (default 0)   — set to 1 to disable entirely
#
# For multi-instance deployments where you want a global limit across
# machines, replace the in-memory `_buckets` dict with a Redis-backed
# implementation. See _audit/RATE_LIMITING.md for the runbook.
#
# Stdio transport skips this middleware entirely (no remote callers).

import threading as _rl_threading                 # noqa: E402
import time as _rl_time                           # noqa: E402

_RATE_LIMIT_PER_MINUTE = int(_os.environ.get("RATE_LIMIT_PER_MINUTE", "60"))
_RATE_LIMIT_BURST = int(_os.environ.get("RATE_LIMIT_BURST", "10"))
_RATE_LIMIT_DISABLED = _os.environ.get("RATE_LIMIT_DISABLED", "").lower() in (
    "1", "true", "yes",
)
_RATE_LIMIT_CAPACITY = _RATE_LIMIT_PER_MINUTE + _RATE_LIMIT_BURST
_RATE_LIMIT_REFILL_PER_SEC = _RATE_LIMIT_PER_MINUTE / 60.0

_rl_lock = _rl_threading.Lock()
_rl_buckets: dict = {}  # ip -> {"tokens": float, "last_refill": float (monotonic seconds)}
_rl_last_cleanup = 0.0
_RL_CLEANUP_INTERVAL = 60.0       # seconds between cleanup sweeps
_RL_BUCKET_TTL = 600.0            # drop buckets idle this long
_RL_MAX_BUCKETS_BEFORE_CLEANUP = 1000


def _rl_client_ip(scope: dict) -> str:
    """Extract real client IP from the ASGI scope.

    Honors X-Forwarded-For (Fly, Cloudflare, any reverse proxy) when set.
    The first IP in the chain is the originating client; the rest are the
    proxy hops we've passed through.
    """
    for name, value in scope.get("headers") or []:
        if name.lower() == b"x-forwarded-for":
            return value.decode("latin-1").split(",")[0].strip()
    client = scope.get("client")
    return client[0] if client else "unknown"


def _rl_check(ip: str) -> tuple[bool, int, int]:
    """Token-bucket admission check.

    Returns (allowed, retry_after_seconds, tokens_remaining_int).
    """
    global _rl_last_cleanup
    now = _rl_time.monotonic()

    with _rl_lock:
        # Lazy cleanup of stale buckets, bounded so it doesn't run on every
        # request once the table is small.
        if (
            len(_rl_buckets) > _RL_MAX_BUCKETS_BEFORE_CLEANUP
            and (now - _rl_last_cleanup) > _RL_CLEANUP_INTERVAL
        ):
            _rl_last_cleanup = now
            stale = [
                k for k, b in _rl_buckets.items()
                if (now - b["last_refill"]) > _RL_BUCKET_TTL
            ]
            for k in stale:
                _rl_buckets.pop(k, None)

        bucket = _rl_buckets.get(ip)
        if bucket is None:
            bucket = {"tokens": float(_RATE_LIMIT_CAPACITY), "last_refill": now}
            _rl_buckets[ip] = bucket

        elapsed = now - bucket["last_refill"]
        bucket["tokens"] = min(
            float(_RATE_LIMIT_CAPACITY),
            bucket["tokens"] + elapsed * _RATE_LIMIT_REFILL_PER_SEC,
        )
        bucket["last_refill"] = now

        if bucket["tokens"] >= 1.0:
            bucket["tokens"] -= 1.0
            return True, 0, int(bucket["tokens"])

        # Out of tokens: compute time until next token is available.
        deficit = 1.0 - bucket["tokens"]
        retry_after = max(1, int(deficit / _RATE_LIMIT_REFILL_PER_SEC) + 1)
        return False, retry_after, 0


def _rate_limit_middleware(asgi_app):
    """ASGI middleware that token-bucket-rate-limits per client IP.

    On admission, decorates the response with X-RateLimit-* headers.
    On rejection, sends an immediate 429 with Retry-After set, without
    invoking the inner app.
    """
    async def wrapped(scope, receive, send):
        if scope.get("type") != "http" or _RATE_LIMIT_DISABLED:
            await asgi_app(scope, receive, send)
            return

        ip = _rl_client_ip(scope)
        allowed, retry_after, remaining = _rl_check(ip)
        reset_unix = int(_rl_time.time()) + (retry_after if not allowed else 60)

        if not allowed:
            body = json.dumps({
                "error": {
                    "code": "rate_limited",
                    "message": (
                        f"Rate limit exceeded: {_RATE_LIMIT_PER_MINUTE} "
                        f"requests/minute per client. Retry in {retry_after}s."
                    ),
                }
            }).encode("utf-8")
            await send({
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"retry-after", str(retry_after).encode()),
                    (b"x-ratelimit-limit", str(_RATE_LIMIT_PER_MINUTE).encode()),
                    (b"x-ratelimit-remaining", b"0"),
                    (b"x-ratelimit-reset", str(reset_unix).encode()),
                    (b"content-length", str(len(body)).encode()),
                ],
            })
            await send({"type": "http.response.body", "body": body})
            return

        # Inject rate-limit headers into the successful response.
        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-ratelimit-limit", str(_RATE_LIMIT_PER_MINUTE).encode()))
                headers.append((b"x-ratelimit-remaining", str(remaining).encode()))
                headers.append((b"x-ratelimit-reset", str(reset_unix).encode()))
                message["headers"] = headers
            await send(message)

        await asgi_app(scope, receive, send_with_headers)

    return wrapped


# ---------------------------------------------------------------------------
# Static route middleware — handles two endpoints that the MCP layer doesn't:
#   /favicon.ico      → 302 redirect to the canonical Pier39 brand icon, so
#                       the directory listing and Google's favicon API show
#                       the right logo for tool calls.
#   /health           → 200 OK JSON, for Fly's HTTP health check (configured
#                       in fly.toml). Without this, Fly's prober gets 404,
#                       marks the machine unhealthy, and auto_stop_machines
#                       takes the app offline. Same response on /api/health
#                       to be friendly to other monitoring conventions.
#
# Both bypass rate limiter and transport security so external probes always
# succeed without bumping into any guards.
# ---------------------------------------------------------------------------

_FAVICON_TARGET = _os.environ.get(
    "FAVICON_URL",
    "https://pier39.ai/icon.png?20c219485b49c924",
)

_HEALTH_BODY = b'{"ok":true,"server":"negotiate-mcp"}'


def _favicon_middleware(asgi_app):
    """Handle /favicon.ico (302) and /health, /api/health (200 OK)."""
    async def wrapped(scope, receive, send):
        if scope.get("type") == "http":
            path = scope.get("path")

            if path == "/favicon.ico":
                await send({
                    "type": "http.response.start",
                    "status": 302,
                    "headers": [
                        (b"location", _FAVICON_TARGET.encode("latin-1")),
                        (b"cache-control", b"public, max-age=86400"),
                        (b"content-length", b"0"),
                    ],
                })
                await send({"type": "http.response.body", "body": b""})
                return

            if path in ("/health", "/api/health"):
                await send({
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(_HEALTH_BODY)).encode()),
                        (b"cache-control", b"no-store"),
                    ],
                })
                await send({"type": "http.response.body", "body": _HEALTH_BODY})
                return

        await asgi_app(scope, receive, send)
    return wrapped


# ---------------------------------------------------------------------------
# Public store directory (for find_stores tool)
# ---------------------------------------------------------------------------

DEFAULT_DIRECTORY_URL = "https://raw.githubusercontent.com/sanjana-pier39/negotiate-directory/main/registry.json"
_DIRECTORY_CACHE = {"data": None, "fetched_at": 0.0, "url": ""}
_DIRECTORY_CACHE_TTL = 300  # 5 minutes


def _get_directory() -> dict:
    """Fetch + cache the public negotiate.v1 store directory.

    The directory URL can be overridden with the DIRECTORY_URL env var so
    private/fork directories work too.
    """
    import os
    import time
    url = os.environ.get("DIRECTORY_URL", DEFAULT_DIRECTORY_URL).strip()
    now = time.time()
    if (
        _DIRECTORY_CACHE["data"] is not None
        and _DIRECTORY_CACHE["url"] == url
        and (now - _DIRECTORY_CACHE["fetched_at"]) < _DIRECTORY_CACHE_TTL
    ):
        return _DIRECTORY_CACHE["data"]
    try:
        data = _http_get_json(url)
    except RuntimeError as e:
        # Don't crash if the directory is temporarily unreachable
        if _DIRECTORY_CACHE["data"] is not None:
            return _DIRECTORY_CACHE["data"]  # serve stale
        raise RuntimeError(f"Could not fetch store directory at {url}: {e}")
    _DIRECTORY_CACHE.update({"data": data, "fetched_at": now, "url": url})
    return data


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool(
    annotations={
        "title": "Find Stores",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
def find_stores(query: str = "", category: str = "") -> list[dict]:
    """Find negotiate.v1-compliant stores matching a search query or category.

    Use this when the shopper asks to negotiate for something but hasn't
    specified a particular store. Search by free-text product name, category,
    or both. Returns a ranked list of matching stores; pick one and pass its
    domain to start_negotiation.

    Args:
        query: Free-text search across store name, tagline, categories, and
               product names. Empty string matches all stores.
        category: Filter by category tag (e.g., "appliances", "books",
                  "fitness", "office", "fashion"). Empty string skips filter.

    Returns:
        List of matching store dicts. Each entry has:
          - name: human-readable store name
          - domain: the domain to pass to discover_store / start_negotiation
          - tagline: short marketing line
          - categories: list of category tags
          - products_count: how many products are listed
          - sample_products: a few example product names

        Empty list if nothing matches. Raises RuntimeError if the directory
        is unreachable on first call.
    """
    _log_call("find_stores")
    directory = _get_directory()
    stores = directory.get("stores", [])
    q = query.strip().lower()
    c = category.strip().lower()

    matches = []
    for store in stores:
        # Build a searchable text blob from name, tagline, categories, sample products
        searchable = " ".join([
            store.get("name", ""),
            store.get("tagline", ""),
            " ".join(store.get("categories", [])),
            " ".join(store.get("sample_products", [])),
        ]).lower()

        if q and q not in searchable:
            continue
        if c and c not in [cat.lower() for cat in store.get("categories", [])]:
            continue
        matches.append(store)

    return matches


@mcp.tool(
    annotations={
        "title": "Discover Store",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
def discover_store(domain: str) -> dict:
    """Probe a domain to discover whether it speaks the negotiate.v1 protocol.

    Fetches /negotiate.json (with /.well-known/negotiate.json fallback) and
    validates the schema. Returns the full protocol descriptor on success.

    Args:
        domain: Site to probe. Accepts 'example.com', 'https://example.com',
                or with trailing slash.

    Returns:
        The negotiate.v1 descriptor: store info, endpoints, products, limits.

    Raises:
        RuntimeError if the domain doesn't speak negotiate.v1 or can't be reached.
    """
    _log_call("discover_store", _domain_to_slug(domain))
    base = _normalize_domain(domain)
    last_error = None
    for path in ("/negotiate.json", "/.well-known/negotiate.json"):
        try:
            data = _http_get_json(base + path)
            break
        except RuntimeError as e:
            last_error = e
            continue
    else:
        raise RuntimeError(f"No negotiate.v1 descriptor found at {base}: {last_error}")

    proto = data.get("negotiate_protocol")
    if proto != "negotiate.v1":
        raise RuntimeError(
            f"{base} returned a descriptor but the protocol is {proto!r}, not 'negotiate.v1'. "
            f"This shopper agent only speaks negotiate.v1."
        )
    return data


@mcp.tool(
    annotations={
        "title": "List Products",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
def list_products(domain: str, query: str = "", limit: int = 50, offset: int = 0) -> dict:
    """List products available for negotiation at a store.

    Fetches the store's negotiate.json and returns a paginated, optionally
    filtered slice of the products array. Each product has id, name, kind,
    list_price, page_url, and start_chat_url.

    Args:
        domain: Site to query. Same accepted forms as discover_store.
        query: Optional case-insensitive substring filter against product
               name and id. Empty string matches all.
        limit: Max products to return (default 50, max 100). Stores can
               have thousands of SKUs — keep this small to avoid hitting
               the MCP 1MB result-size cap.
        offset: Skip this many matches before returning (default 0). Use
               with limit to paginate through large catalogs.

    Returns:
        {
          "total_in_store": int,        # total products at this store
          "matched": int,               # how many matched the query
          "returned": int,              # how many returned in this page
          "offset": int,
          "limit": int,
          "products": [ ... ],          # the page of results
          "more_available": bool,       # True if matched > offset+returned
          "next_offset": int|None,      # pass to next call, or None if done
        }
    """
    _log_call("list_products", _domain_to_slug(domain))
    try:
        limit = max(1, min(int(limit or 50), 100))
        offset = max(0, int(offset or 0))
    except (TypeError, ValueError):
        raise RuntimeError(
            "list_products: 'limit' and 'offset' must be non-negative integers."
        )
    q = (query or "").strip().lower()

    desc = discover_store(domain)
    all_products = desc.get("products", [])
    total = len(all_products)

    # Apply text filter
    if q:
        filtered = [
            p for p in all_products
            if q in (p.get("name", "") + " " + p.get("id", "")).lower()
        ]
    else:
        filtered = all_products
    matched = len(filtered)

    # Paginate
    page = filtered[offset : offset + limit]

    # Strip heavy fields each product doesn't need at the listing level
    SLIM_KEEP = {"id", "name", "kind", "list_price", "page_url", "start_chat_url"}
    slim_page = [{k: v for k, v in p.items() if k in SLIM_KEEP} for p in page]

    end = offset + len(slim_page)
    return {
        "total_in_store": total,
        "matched": matched,
        "returned": len(slim_page),
        "offset": offset,
        "limit": limit,
        "products": slim_page,
        "more_available": end < matched,
        "next_offset": end if end < matched else None,
    }


@mcp.tool(
    annotations={
        "title": "Start Negotiation",
        # Creates a new session record at the merchant — additive write,
        # not destructive. Each call spawns a fresh session so it's not
        # idempotent. Talks to an external merchant endpoint.
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
def start_negotiation(domain: str, product_id: str) -> dict:
    """Open a new negotiation session for a specific product.

    Looks up the start_chat URL template from the store's descriptor,
    substitutes the product_id, fetches the result. The merchant agent's
    opening greeting is in the response.

    Args:
        domain: Site to negotiate at.
        product_id: Must be one of products[].id from list_products().

    Returns:
        Dict with session_id, greeting (merchant's opener), and next URL
        for the next turn (with {url_encoded_message} placeholder).
    """
    _log_call("start_negotiation", _domain_to_slug(domain))
    desc = discover_store(domain)
    template = desc["endpoints"]["start_chat"]["url_template"]
    url = template.replace("{product_id}", urllib.parse.quote(product_id))
    return _http_get_json(url)


@mcp.tool(
    annotations={
        "title": "Send Negotiation Message",
        # Appends a turn to an active negotiation. Structurally non-
        # destructive at the MCP layer. NOTE: a shopper message can
        # functionally commit (e.g. "I accept that offer") because the
        # merchant agent on the other side interprets natural language.
        # Treat each send_message as potentially binding within the
        # context of the running negotiation.
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
def send_message(next_url: str, message: str) -> dict:
    """Send one shopper turn in an active negotiation.

    Take the 'next' URL from the previous response (returned by
    start_negotiation or the previous send_message call), substitute
    your URL-encoded shopper message, and fetch.

    Args:
        next_url: The 'next' URL from the previous response. Should
                  contain a {url_encoded_message} placeholder.
        message: Your shopper turn, plain text. Will be URL-encoded.

    Returns:
        Dict with the merchant's reply, a 'closed' flag, and the next
        URL for the following turn (or null if the negotiation is closed).
    """
    _log_call("send_message")
    _validate_outbound_url(next_url)
    if "{url_encoded_message}" not in next_url:
        # Fallback: assume the URL ends with message= and just append
        sep = "&" if "?" in next_url else "?"
        if "message=" in next_url:
            url = next_url.split("message=")[0] + "message=" + urllib.parse.quote(message)
        else:
            url = f"{next_url}{sep}message={urllib.parse.quote(message)}"
    else:
        url = next_url.replace("{url_encoded_message}", urllib.parse.quote(message))
    return _http_get_json(url)


@mcp.tool(
    annotations={
        "title": "Read Negotiation History",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
def read_history(history_url: str) -> dict:
    """Read the running history of a chat session.

    Useful for resumption or for double-checking what's been said. The
    history_url comes from the read_history endpoint in the store's
    descriptor — typically <base>/api/store/chat/<session_id>.

    Args:
        history_url: Full URL to the history endpoint with session_id substituted.

    Returns:
        Dict with session_id and history (list of {speaker, message}).
    """
    _log_call("read_history")
    _validate_outbound_url(history_url)
    return _http_get_json(history_url)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Entry point for the `negotiate-mcp` console script and `python -m negotiate_mcp`.

    Default transport: stdio (for Claude Desktop's local install via config file).

    Set MCP_TRANSPORT=streamable-http to run as a remote/hosted MCP server.
    """
    import os
    transport = os.environ.get("MCP_TRANSPORT", "stdio").strip().lower()
    if transport == "stdio":
        mcp.run()
        return
    if transport in ("streamable-http", "http", "sse"):
        host = os.environ.get("HOST", "0.0.0.0")
        port = int(os.environ.get("PORT", "8000"))
        # Build the ASGI app. FastMCP's TransportSecurity layer validates
        # Host and Origin headers against the allowed_* lists configured in
        # _build_mcp(). Set ALLOWED_HOSTS / ALLOWED_ORIGINS env vars to
        # whitelist your production hostname and client origins.
        if transport == "sse":
            app = mcp.sse_app()
        else:
            app = mcp.streamable_http_app()

        # Per-IP token-bucket rate limit (default 60 req/min per client).
        # Tune via RATE_LIMIT_PER_MINUTE / RATE_LIMIT_BURST env vars; set
        # RATE_LIMIT_DISABLED=1 to bypass entirely. See _audit/RATE_LIMITING.md
        # for multi-instance / Redis-backed scaling and Cloudflare edge rules.
        app = _rate_limit_middleware(app)

        # Outermost: 302 /favicon.ico -> pier39.ai/icon.png so the directory
        # listing and Google favicon API show the brand icon. Bypasses rate
        # limit and transport security; safe because it returns no data.
        app = _favicon_middleware(app)

        try:
            import uvicorn
        except ImportError:
            sys.exit("Missing dependency for HTTP transport: pip install uvicorn")
        uvicorn.run(
            app,
            host=host,
            port=port,
            log_level="info",
            forwarded_allow_ips="*",  # trust X-Forwarded-* from Fly/Cloudflare proxies
            proxy_headers=True,
        )
        return
    sys.exit(f"Unknown MCP_TRANSPORT={transport!r}; expected stdio, streamable-http, or sse.")


if __name__ == "__main__":
    main()
