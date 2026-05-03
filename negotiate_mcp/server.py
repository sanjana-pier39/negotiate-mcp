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
except ImportError:
    sys.exit(
        "Missing dependency: pip install mcp\n"
        "(The official Anthropic MCP SDK; ships FastMCP in mcp.server.fastmcp.)"
    )

mcp = FastMCP("negotiate-agent")

USER_AGENT = "negotiate-mcp/0.1 (+https://github.com/sanjana-pier39/pier39-skills)"
DEFAULT_TIMEOUT = 30


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
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
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


@mcp.tool()
def list_products(domain: str) -> list[dict]:
    """List products available for negotiation at a store.

    Fetches the store's negotiate.json and returns the products array. Each
    product has id, name, list_price, page_url, and start_chat_url.

    Args:
        domain: Site to query. Same accepted forms as discover_store.

    Returns:
        List of product dicts. Empty list if the store has no listed products.
    """
    desc = discover_store(domain)
    return desc.get("products", [])


@mcp.tool()
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
    desc = discover_store(domain)
    template = desc["endpoints"]["start_chat"]["url_template"]
    url = template.replace("{product_id}", urllib.parse.quote(product_id))
    return _http_get_json(url)


@mcp.tool()
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


@mcp.tool()
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
    return _http_get_json(history_url)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Entry point for the `negotiate-mcp` console script and `python -m negotiate_mcp`."""
    mcp.run()


if __name__ == "__main__":
    main()
