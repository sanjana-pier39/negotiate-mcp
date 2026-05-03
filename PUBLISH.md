# Publishing `negotiate-mcp`

Three milestones: PyPI, GitHub, and the MCP servers list. Do them in that order — the MCP listing assumes PyPI install works.

---

## Part 1 — Publish to PyPI

### One-time account setup

1. Create a PyPI account at [pypi.org/account/register](https://pypi.org/account/register/). Verify your email.
2. Enable 2FA at [pypi.org/manage/account/](https://pypi.org/manage/account/) (TOTP via Authy / 1Password).
3. Generate an API token: [pypi.org/manage/account/token/](https://pypi.org/manage/account/token/) — scope it to "Entire account" for the first publish, then narrow to `negotiate-mcp` afterward.
4. Save the token as `~/.pypirc`:

   ```ini
   [pypi]
     username = __token__
     password = pypi-AgEIcHlwaS5vcmc...      # your full token, including the prefix
   ```

   Or set `TWINE_USERNAME=__token__` and `TWINE_PASSWORD=pypi-AgE...` as environment variables.

### Test on TestPyPI first (recommended)

TestPyPI is a separate index for trial uploads — won't pollute the real one if something's off.

```bash
# Same flow but at test.pypi.org
# Create a separate token at https://test.pypi.org/manage/account/token/

cd mcp-connector
pip install --upgrade build twine

# Build wheel + sdist
rm -rf dist/
python -m build

# Upload to TestPyPI
twine upload --repository-url https://test.pypi.org/legacy/ dist/*

# Verify install from TestPyPI
pip install --index-url https://test.pypi.org/simple/ \
            --extra-index-url https://pypi.org/simple/ \
            negotiate-mcp

# Smoke test the install
negotiate-mcp --help     # or just `negotiate-mcp` (it'll wait on stdio for MCP requests)
```

### Real publish

```bash
cd mcp-connector
rm -rf dist/
python -m build
twine upload dist/*
```

That's it. Within a couple of minutes the package is live at [pypi.org/project/negotiate-mcp/](https://pypi.org/project/negotiate-mcp/) and `pip install negotiate-mcp` / `uvx negotiate-mcp` works for the world.

### Bump versions later

Edit `pyproject.toml`'s `version` field, commit, then re-run the build + upload. PyPI doesn't allow re-uploading the same version, so always bump first.

---

## Part 2 — Push to GitHub

Conventionally MCP servers live in their own repo so they can have a clean issue tracker and release tags.

```bash
cd mcp-connector

# Create the repo on GitHub:
gh repo create sanjana-pier39/negotiate-mcp --public --source . --remote origin --description "MCP server for the negotiate.v1 protocol"
# (or use the GitHub web UI if you don't have gh CLI)

git init
git add .
git commit -m "Initial publish — negotiate-mcp v0.1.0"
git branch -M main
git push -u origin main

# Tag the release so anyone can find this exact version
git tag v0.1.0
git push origin v0.1.0
```

Then on GitHub:

1. **Create a release** off the tag at `https://github.com/sanjana-pier39/negotiate-mcp/releases/new` — paste a one-paragraph "what is this" + a link to the PyPI page.
2. **Add topics** in the repo settings: `mcp`, `claude`, `model-context-protocol`, `agent-skills`, `negotiation`. These help GitHub search surface your server.
3. **Pin the repo** to your profile if it's a flagship.

---

## Part 3 — List on the official MCP servers index

The community-maintained list of MCP servers lives in the official MCP repo: [github.com/modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers). Listing yours there gets you discovered by anyone browsing for MCP integrations.

```bash
# Fork the repo
gh repo fork modelcontextprotocol/servers --clone --remote
cd servers
git checkout -b add-negotiate-mcp
```

Open `README.md` in the fork. There's a section `### 🌎 Community Servers` (or similar — the exact heading evolves). Add a single bullet alphabetically:

```markdown
- **[Negotiate](https://github.com/sanjana-pier39/negotiate-mcp)** - Negotiate prices at any
  [negotiate.v1](https://github.com/sanjana-pier39/pier39-skills/blob/main/PROTOCOL.md)-compliant
  storefront. Five tools: `discover_store`, `list_products`, `start_negotiation`, `send_message`,
  `read_history`.
```

Commit + push + open a PR:

```bash
git add README.md
git commit -m "Add negotiate-mcp server"
git push -u origin add-negotiate-mcp
gh pr create --title "Add negotiate-mcp" \
             --body "MCP server for the negotiate.v1 protocol — lets agents negotiate at any compliant storefront. PyPI: https://pypi.org/project/negotiate-mcp/"
```

Maintainers usually merge community-server PRs within a few days.

---

## Optional — submit to other MCP catalogs

There are emerging directories beyond the official list:

- **[mcpservers.org](https://mcpservers.org)** — community catalog with search/filter
- **[Smithery.ai](https://smithery.ai)** — installer-flavored catalog
- **[mcp.so](https://mcp.so)** — directory + tutorials
- **Anthropic's marketplace** (when launched)

Each has its own submission flow. Most just want a GitHub URL + 1-2 sentence description.

---

## Pre-publish checklist

Before the first PyPI upload, double-check:

- [ ] `pyproject.toml` `version` is `0.1.0` and unique on PyPI (run `pip search` or check the page)
- [ ] `LICENSE` file present
- [ ] `README.md` is well-formatted (it becomes the PyPI page)
- [ ] `python -m build` produces both `negotiate_mcp-0.1.0-py3-none-any.whl` and `negotiate_mcp-0.1.0.tar.gz` in `dist/`
- [ ] `unzip -l dist/*.whl` shows `negotiate_mcp/__init__.py`, `__main__.py`, `server.py`, plus the `.dist-info/` metadata
- [ ] You can `pip install dist/*.whl` into a fresh virtualenv and `negotiate-mcp` runs without ImportError
