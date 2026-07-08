"""nash_telemetry.py — fire-and-forget product-usage events from the connector.

The connector can't write to the checkout server's DB directly, so we POST
events to its /api/events endpoint. Everything here is best-effort:

  * emit() NEVER blocks the caller (the POST runs in a daemon thread) and NEVER
    raises — a broken analytics pipeline must not slow or break a shopper's tool
    call. This is load-bearing: it runs inside the hot path of every tool.
  * We also write one structured `NASH_EVENT {json}` line to stderr per event so
    a Fly log drain can capture usage even if the ingest key isn't set.

Per-request context (session id / client IP / country) is stashed in a
contextvar by the ASGI middleware and picked up here.
"""
from __future__ import annotations

import contextvars
import json
import os
import sys
import threading
import urllib.request

_CHECKOUT = os.environ.get("NASH_CHECKOUT_URL", "https://nash-checkout.pier39.ai").rstrip("/")
_KEY = (os.environ.get("NASH_INTERNAL_API_KEY") or "").strip()

# Set per-request by the middleware; read by emit().
_ctx: "contextvars.ContextVar[dict | None]" = contextvars.ContextVar("nash_req_ctx", default=None)


def set_context(*, session_id=None, ip=None, country=None, client_kind=None,
                user_agent=None) -> None:
    try:
        _ctx.set({"session_id": session_id, "ip": ip, "country": country,
                  "client_kind": client_kind, "user_agent": user_agent})
    except Exception:
        pass


# Our own e2e smoke test hits prod daily and self-identifies via User-Agent.
# Tag it 'synthetic' so it's excluded from human metrics downstream.
_SYNTHETIC_UA_MARKERS = tuple(
    m.strip().lower() for m in
    os.environ.get("NASH_SYNTHETIC_UA", "nash-e2e-smoke,nash-smoke").split(",")
    if m.strip()
)


def _is_synthetic_ua(user_agent) -> bool:
    ua = (user_agent or "").lower()
    return bool(ua) and any(m in ua for m in _SYNTHETIC_UA_MARKERS)


def _client_kind(ip: str | None, user_agent=None) -> str:
    if _is_synthetic_ua(user_agent):
        return "synthetic"
    ip = (ip or "")
    # Anthropic's hosted infra (Claude web/mobile) egresses from 160.79.10x.x.
    if ip.startswith("160.79.106.") or ip.startswith("160.79.104."):
        return "web"
    return "desktop" if ip else "unknown"


def emit(event_type: str, **fields) -> None:
    """Record a product event. Fire-and-forget: never blocks, never raises."""
    try:
        ctx = _ctx.get() or {}
        _ua = ctx.get("user_agent")
        # Synthetic (smoke-test) traffic overrides any prior client_kind.
        _ck = "synthetic" if _is_synthetic_ua(_ua) else (
            ctx.get("client_kind") or _client_kind(ctx.get("ip"), _ua))
        payload = {
            "event_type": event_type,
            "source": "mcp",
            "session_id": ctx.get("session_id"),
            "ip": ctx.get("ip"),
            "country": ctx.get("country"),
            "client_kind": _ck,
            "user_agent": _ua,
        }
        for k, v in fields.items():
            if v is not None:
                payload[k] = v
        # Structured line for the Fly log drain (works even with no ingest key).
        try:
            sys.stderr.write("NASH_EVENT " + json.dumps(payload, default=str)[:900] + "\n")
        except Exception:
            pass
        if not _KEY:
            return
        threading.Thread(target=_post, args=(payload,), daemon=True).start()
    except Exception:
        pass  # analytics must never break a tool call


def _post(payload: dict) -> None:
    try:
        data = json.dumps(payload, default=str).encode("utf-8")
        req = urllib.request.Request(
            _CHECKOUT + "/api/events", data=data, method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {_KEY}"})
        with urllib.request.urlopen(req, timeout=3) as r:
            r.read()
    except Exception:
        pass
