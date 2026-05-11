# negotiate.v1 — Protocol Spec

A small HTTP protocol that lets any AI shopper agent negotiate with any store's seller-side AI agent, with no prior coordination. Stores advertise capability via a single discovery file. Agents drive the negotiation through plain GETs.

This document is the canonical spec. If you're building a **store** that wants to be negotiable, follow Section 2. If you're building a **shopper agent** that wants to negotiate against any negotiate.v1 store, follow Section 3.

---

## 1. Overview

The protocol has three surfaces:

| Surface | Purpose | Format |
| --- | --- | --- |
| Discovery | "Is this store negotiable?" | One JSON file at a known URL |
| Catalog | "What can I negotiate over here?" | JSON product list |
| Chat | "Negotiate one product, turn by turn." | Two GET endpoints + one for history |

Everything is GET-based JSON. No POST, no WebSocket, no SSE, no auth. AI shoppers using GET-only fetch tools (Claude.ai web browsing, simple curl-based agents) can drive the full negotiation. Browser widgets MAY use POST equivalents — see §5.

---

## 2. Discovery — `/negotiate.json`

Every negotiate.v1-compliant store MUST serve a JSON descriptor at:

```
GET /negotiate.json
```

Stores SHOULD also mirror it at the IETF well-known URI:

```
GET /.well-known/negotiate.json
```

Both URLs MUST return identical content with `Content-Type: application/json; charset=utf-8` and CORS header `Access-Control-Allow-Origin: *`.

### Schema

```jsonc
{
  "negotiate_protocol": "negotiate.v1",        // required, exact string
  "store": {                                    // required
    "name":     "Atlas Premium Appliance",
    "city":     "Atlanta, GA",                  // optional
    "rep_name": "Chonkers",                     // the chat agent's display name
    "tagline":  "Premium home tech, outlet pricing.",
    "policy":   "Free expedited shipping. 30-day return. ..."
  },
  "merchant_skill": {                           // optional but recommended
    "name":        "pier39-merchant",
    "repo":        "https://github.com/...",
    "description": "..."
  },
  "endpoints": {                                // required
    "start_chat":   { "method": "GET", "url_template": "<base>/api/store/chat/start?product_id={product_id}", ... },
    "send_message": { "method": "GET", "url_template": "<base>/api/store/chat/{session_id}/say?message={url_encoded_message}", ... },
    "read_history": { "method": "GET", "url_template": "<base>/api/store/chat/{session_id}", ... },
    "catalog":      { "method": "GET", "url":          "<base>/api/store/catalog", ... }
  },
  "products": [                                 // required, may be empty
    {
      "id":             "hp07-hot-cool",
      "name":           "Dyson Pure Hot+Cool HP07",
      "subtitle":       "...",
      "list_price":     579,
      "currency":       "USD",
      "kind":           "purifier",             // optional, free-form category
      "page_url":       "https://.../store/p/hp07-hot-cool",
      "start_chat_url": "https://.../api/store/chat/start?product_id=hp07-hot-cool"
    }
  ],
  "limits": {                                   // required
    "max_chat_starts_per_hour_per_ip": 8,
    "max_messages_per_chat":           30,
    "session_idle_ttl_seconds":        3600,
    "max_message_length_chars":        2000,
    "currency":                        "USD"
  },
  "human_docs": {                               // optional
    "agent_guide_html": "...", "agent_guide_json": "...", "llms_txt": "..."
  },
  "negotiation_guidance": [...]                 // optional, free-form tips
}
```

### Required URL templates

The `endpoints` block uses [RFC 6570](https://www.rfc-editor.org/rfc/rfc6570)-style placeholders. A shopper agent substitutes them at request time. The four required endpoints:

| Key | Template variables |
| --- | --- |
| `start_chat`   | `{product_id}` |
| `send_message` | `{session_id}`, `{url_encoded_message}` |
| `read_history` | `{session_id}` |
| `catalog`      | none |

---

## 3. Chat flow

### 3.1 Start a session

```
GET <start_chat URL with product_id substituted>
```

Response (HTTP 201):

```json
{
  "session_id": "abc123...",
  "greeting":   "Hi! I'm Chonkers from Atlas...",
  "next":       "<send_message URL with session_id substituted, {url_encoded_message} unfilled>"
}
```

The `next` field is a hypermedia hint: take it, replace the `{url_encoded_message}` placeholder with your URL-encoded shopper turn, fetch the result. Following `next` is the recommended way to drive the conversation.

### 3.2 Send a turn

```
GET <send_message URL with session_id and url_encoded_message substituted>
```

Response (HTTP 200):

```json
{
  "message": "I can do $585 with the extra battery and free shipping...",
  "closed":  false,
  "next":    "<URL for the next turn>"  // null when closed=true
}
```

Repeat until `closed: true`. Both sides converge on closed=true when:
- The shopper accepts a deal explicitly, OR
- The shopper walks away explicitly, OR
- The merchant agent decides further negotiation is unproductive

After closed=true, further calls to `send_message` MUST return HTTP 400 with `{"error": "this chat is closed"}`.

### 3.3 Read history (optional)

```
GET <read_history URL with session_id substituted>
```

Returns:

```json
{
  "session_id": "abc123...",
  "history": [
    { "speaker": "merchant", "message": "Hi! I'm Chonkers..." },
    { "speaker": "shopper",  "message": "Could you do $499..." },
    { "speaker": "merchant", "message": "..." }
  ]
}
```

Useful for resumption or for shoppers who lost their connection mid-negotiation.

---

## 4. For shopper agents

The complete flow against any negotiate.v1 store, given just the store's domain `<DOMAIN>`:

```
1. GET https://<DOMAIN>/negotiate.json
   → If "negotiate_protocol":"negotiate.v1", proceed. Otherwise, the store doesn't speak the protocol.

2. Find the product you want in `products[]`. You can also GET the live `endpoints.catalog.url` for the freshest list.

3. GET the product's `start_chat_url` (or substitute `endpoints.start_chat.url_template` with `{product_id}`).
   → Save the returned session_id.
   → Read the merchant's greeting.

4. Decide your shopper turn. URL-encode it. Substitute into the `next` URL from the previous response and GET it.
   → Read the merchant's reply.
   → If `closed:true`, you're done. Otherwise repeat with the new `next` URL.

5. Respect the limits in `limits` — back off on 429 responses.
```

A shopper that follows this loop works against any negotiate.v1-compliant store, regardless of who built it.

---

## 5. Optional POST endpoints

Stores MAY expose POST equivalents for browser widget use. If they do:

```
POST <base>/api/store/chat/start         body: {"product_id": "..."}
POST <base>/api/store/chat/{sid}/message body: {"message": "..."}
```

Both MUST return `Access-Control-Allow-Origin: *` and accept `Content-Type: application/json`. POST endpoints are advisory; shopper agents SHOULD NOT depend on them — GET is the required protocol surface.

---

## 6. Errors

All errors are JSON: `{"error": "<message>"}`. Standard status codes:

| Status | Meaning |
| --- | --- |
| 400 | Malformed request (missing param, bad product_id, message too long, chat closed) |
| 404 | Unknown product_id, unknown session_id |
| 429 | Rate limit hit (per-IP or per-session) |
| 500 | Server error (LLM upstream failed, etc.) |

---

## 7. Hidden merchant state

Stores MUST NOT include the following in any GET response (only in the merchant agent's private system prompt):

- `floor` — the merchant's walk-away price
- `levers` — concession ladder
- `inventory_note` — internal margin/aging notes
- `merchant_persona` — system-prompt-only flavor

Public catalog endpoints MUST strip these before returning. The reference implementation does this in `_public_catalog_view()`.

---

## 8. Versioning

The protocol version is a string in `negotiate_protocol`. Today: `"negotiate.v1"`.

Breaking changes increment the major version (`negotiate.v2`). Stores MAY serve multiple versions side by side at different paths (`/negotiate.v1.json`, `/negotiate.v2.json`) and MAY add a `versions` array to `/negotiate.json` when supporting more than one.

Shopper agents MUST refuse to negotiate against a store whose `negotiate_protocol` they don't understand.

---

## 9. Reference implementation

The Atlas Premium Appliance demo at `https://negotiate.pier39.ai` is a complete reference implementation:

- Catalog: [`store/catalog.json`](./store/catalog.json) — products with hidden merchant state
- Server: [`server.py`](./server.py) — all endpoints + discovery files + rate limits
- Discovery: live at `https://negotiate.pier39.ai/negotiate.json`

The merchant agent is a Claude model with the [`pier39-merchant`](https://github.com/sanjana-pier39/pier39-skills) skill loaded as its system prompt. Stores can replace the skill, the model, the catalog, or the entire backend — as long as the protocol surface in §2–§7 stays compliant, any shopper agent will work against them.

---

## License

This protocol spec is MIT-licensed — copy, fork, embed in your own product. Skills are Apache 2.0.
