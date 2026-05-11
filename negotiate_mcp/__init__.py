"""negotiate-mcp — MCP server for the negotiate.v1 protocol.

Once installed in any MCP-aware client (Claude Desktop, Cowork, Claude Code,
or any other), exposes 6 tools that let the agent natively discover and
negotiate at any negotiate.v1-compliant storefront:

    find_stores(query, category)              ← NEW: search the public directory
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
# Define __version__ BEFORE importing from server. server.py reads it back
# during _build_mcp() to set serverInfo.version, so it needs to exist on the
# package namespace before that import runs.
__version__ = "0.2.1"

from negotiate_mcp.server import mcp, main

__all__ = ["mcp", "main", "__version__"]
