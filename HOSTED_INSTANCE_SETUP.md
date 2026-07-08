# Dedicated authorized-only instance for the ChatGPT app

A second Fly app running the **same connector image** with
`NASH_AUTHORIZED_SOURCES_ONLY=1`. Every request to it is authorized-only
(merchants' own catalogs), so the OpenAI reviewer can only ever see authorized
data — no reliance on per-request client detection. Point the ChatGPT app here.

**Submit this MCP URL to OpenAI:** `https://negotiate-mcp-hosted.fly.dev/mcp`
(or your custom domain + `/mcp`).

---

## One-time setup

**1. Recover the main app's real config and reconcile.** The original `fly.toml`
was never committed, so pull it and copy the service/health/VM/region settings
into `fly.hosted.toml` (only `app` and the `NASH_AUTHORIZED_SOURCES_ONLY` env
should differ):

```bash
cd mcp-connector
fly config save --app negotiate-mcp        # writes fly.toml from the live app
# open both files; make fly.hosted.toml match except app name + the env flag
```

**2. Create the app:**

```bash
fly apps create negotiate-mcp-hosted
```

**3. Replicate the secrets.** The hosted instance needs the SAME secrets as the
main connector (SerpApi for the internal price guard, the order/proxy key, the
OAuth vars for ChatGPT, the Apps domain-verification token, session secret,
telemetry key). List what the main app has, then set each on the new app:

```bash
fly secrets list --app negotiate-mcp          # see the names you must replicate
fly secrets set --app negotiate-mcp-hosted \
  SERPAPI_KEY=... \
  NASH_INTERNAL_API_KEY=... \
  WORKOS_AUTHKIT_DOMAIN=... \
  OPENAI_APPS_VERIFICATION_TOKEN=... \
  SESSION_SECRET=... \
  # ...plus any others `fly secrets list` shows on the main app
```

**4. Deploy:**

```bash
fly deploy --config fly.hosted.toml --app negotiate-mcp-hosted
```

**5. Verify it's warm and authorized-only:**

```bash
fly status --app negotiate-mcp-hosted                     # >=1 machine started
time curl -s https://negotiate-mcp-hosted.fly.dev/mcp     # responds fast, no cold start
```

Then a functional check: from the ChatGPT app (or any MCP client hitting this
URL), search a **curated brand** like "Nike" — it must NOT appear as a
`pier39.fly.dev/nike` store, and browsing it must return no Nash-compiled
catalog. A Shopify store (its own `/products.json`) should still return products.
That difference confirms the authorized-only gate is live.

---

## Custom domain (optional, nicer for submission)

```bash
fly certs add mcp-hosted.pier39.ai --app negotiate-mcp-hosted
# add the shown DNS records, then set PUBLIC_HOST=mcp-hosted.pier39.ai in
# fly.hosted.toml and redeploy. Submit https://mcp-hosted.pier39.ai/mcp
```

`PUBLIC_HOST` MUST equal the hostname the ChatGPT app connects to — the OAuth
resource-metadata is built from it; a mismatch breaks the ChatGPT auth handshake.

---

## Notes
- The main `negotiate-mcp` app is unchanged (full breadth for the Claude
  channel). This is purely additive.
- Same git repo / image, so future connector changes deploy to both apps — just
  run the `fly deploy` for each.
- SerpApi is still needed here (secret set) but only for the internal price
  guard; it is never shown to shoppers in this instance.
