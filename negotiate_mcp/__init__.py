"""negotiate-mcp — MCP server for the negotiate.v1 protocol.

Once installed in any MCP-aware client (Claude Desktop, Cowork, Claude Code,
or any other), exposes 5 tools that let the agent natively negotiate at any
negotiate.v1-compliant storefront:

    discover_store(domain)
    list_products(domain)
    start_negotiation(domain, product_id)
    send_message(next_url, message)
    read_history(history_url)

Run with:
    uvx negotiate-mcp
or
    python -m negotiate_mcp
"""
from negotiate_mcp.server import mcp, main

__all__ = ["mcp", "main"]
__version__ = "0.1.0"
