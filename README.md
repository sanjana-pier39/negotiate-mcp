# negotiate-mcp

[![PyPI](https://img.shields.io/pypi/v/negotiate-mcp.svg)](https://pypi.org/project/negotiate-mcp/)

Model Context Protocol (MCP) server for the **[negotiate.v1](https://github.com/sanjana-pier39/pier39-skills/blob/main/PROTOCOL.md)** protocol. Once installed in Claude Desktop, Cowork, Claude Code, or any other MCP-aware client, your Claude gains five native tools for negotiating with any negotiate.v1-compliant store.

## What you get

| Tool | Purpose |
| --- | --- |
| `discover_store(domain)` | Probe a domain to check if it's negotiable. Returns the protocol descriptor. |
| `list_products(domain)` | Enumerate negotiable products at the store. |
| `start_negotiation(domain, product_id)` | Open a chat session with the merchant agent. |
| `send_message(next_url, message)` | Send one shopper turn. |
| `read_history(history_url)` | Read the running history of a session. |

The agent uses these like a human would use a browser: discover the store, pick a product, start a chat, send turns until the deal closes.

mcp-name: io.github.sanjana-pier39/negotiate-mcp

## Install

The recommended path uses [uv](https://github.com/astral-sh/uv) — no virtualenv plumbing, picks the right Python automatically.

```bash
# install uv if you don't have it (macOS):
brew install uv

# then point Claude Desktop / Cowork / Claude Code at it (see below).
# uvx will install the package the first time it's invoked.
```

If you'd rather use plain pip:

```bash
pip install negotiate-mcp
```

## Wire it into Claude Desktop

1. Open your Claude Desktop config:
   - macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
   - Windows: `%APPDATA%\Claude\claude_desktop_config.json`

2. Add this entry under `mcpServers` (creating the file if it doesn't exist):

   ```json
   {
     "mcpServers": {
       "negotiate-agent": {
         "command": "uvx",
         "args": ["negotiate-mcp"]
       }
     }
   }
   ```

   See [`claude_desktop_config.example.json`](./claude_desktop_config.example.json).

3. Quit and re-open Claude Desktop. The five tools should appear in any new conversation.

If you installed with plain `pip` instead of `uv`, replace the command/args block with:

```json
"command": "negotiate-mcp",
"args": []
```

## Wire it into Cowork or Claude Code

Same `negotiate-mcp` command. Add it to the corresponding MCP config in those clients (consult their docs for exact paths). The tool surface is identical.

## Try it

Once installed, in a fresh chat:

> Negotiate for a Dyson HP07 at negotiate.pier39.ai. Try to get it under $500. Bonus points for the engraved gift box.

Claude will call `discover_store("negotiate.pier39.ai")`, find the HP07 in the product list, call `start_negotiation`, then drive the conversation through `send_message` until `closed: true`. No prompt acrobatics needed.

## Test standalone (no Claude required)

```bash
# Run the server on stdio:
uvx negotiate-mcp

# Or, if you've used pip:
python -m negotiate_mcp
```

Most useful when paired with the [`mcp` CLI](https://github.com/modelcontextprotocol/inspector) to inspect tool definitions and exercise them by hand.

## Adding more stores

The connector works against any negotiate.v1-compliant store, not just the Atlas reference (`negotiate.pier39.ai`). As stores adopt the protocol, just point your shopper agent at their domain — the same five tools work everywhere.

See [PROTOCOL.md](https://github.com/sanjana-pier39/pier39-skills/blob/main/PROTOCOL.md) for the full spec.

## Develop locally

```bash
git clone https://github.com/sanjana-pier39/negotiate-mcp
cd negotiate-mcp
pip install -e .
python -m negotiate_mcp     # runs on stdio
```

To publish a new version, see [`PUBLISH.md`](./PUBLISH.md).

## License

MIT.
