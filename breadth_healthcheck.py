#!/usr/bin/env python3
"""Breadth health check for Nash `list_products`.

Flags stores that would silently fall back to the NARROW curated nash.v1
catalog (PATH 2) instead of a WIDE live source, so you can catch coverage
regressions before shoppers do.

For each sampled store it reproduces the real `list_products` resolution:

  shopify  — live /products.json resolves and returns products         (WIDE)
  serpapi  — not Shopify, but the live SerpApi search will fire         (WIDE)
  narrow   — neither: falls back to the curated catalog or no_catalog   (⚠ NARROW)

Always samples ALL catalog_backed brands (the ones that MUST be wide via
SerpApi) plus a random sample of the rest.

Usage:
  python3 breadth_healthcheck.py                 # default sample of 40 + all catalog_backed
  python3 breadth_healthcheck.py --sample 100
  python3 breadth_healthcheck.py --probe-serpapi # actually call SerpApi to confirm results
  python3 breadth_healthcheck.py --json          # machine-readable output

Exit code is non-zero when:
  * SERPAPI_KEY is unset (collapses EVERY non-Shopify store to narrow), or
  * the narrow rate in the sample exceeds --max-narrow-pct (default 10%).
Handy as a scheduled task — a non-zero exit is your alert.
"""
import argparse
import json
import random
import sys

import negotiate_mcp.server as ns


def classify(store: dict, probe_serpapi: bool = False) -> tuple[str, str]:
    """Return (category, human_detail) for one store."""
    domain = store.get("domain") or ""

    # 1. Live Shopify probe — free and authoritative for the ~36K Shopify
    #    brands. Resolves source_domain / <slug>.com the same way list_products
    #    PATH 1 does.
    try:
        res = ns._fetch_shopify_live(domain, query="", limit=5, offset=0)
    except Exception as e:  # noqa: BLE001
        res = None
    if res is not None and res[0]:
        return "shopify", f"{res[1]} products live via /products.json"

    # 2. SerpApi universal fallback — would PATH 1.5 fire for this store?
    brand, live_ok = ns._live_search_target(domain)
    if live_ok:
        if probe_serpapi:
            try:
                got = ns._fetch_serpapi_products(brand, "", limit=10, country="US")
            except Exception:  # noqa: BLE001
                got = None
            if got:
                return "serpapi", f"{len(got)} live SerpApi results for '{brand}'"
            return "narrow", f"SerpApi returned nothing for '{brand}'"
        return "serpapi", f"live search enabled (brand='{brand}')"

    # 3. Neither → falls to the curated catalog (PATH 2) or no_catalog (PATH 3).
    return "narrow", "no live Shopify + no SerpApi target"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sample", type=int, default=40,
                    help="how many non-catalog_backed stores to sample (default 40)")
    ap.add_argument("--probe-serpapi", action="store_true",
                    help="actually call SerpApi to confirm results (costs API calls)")
    ap.add_argument("--max-narrow-pct", type=float, default=10.0,
                    help="fail if narrow%% exceeds this (default 10)")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of text")
    ap.add_argument("--seed", type=int, default=None, help="RNG seed for a repeatable sample")
    args = ap.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    problems: list[str] = []
    key_set = bool(ns._serpapi_key())
    if not key_set:
        problems.append(
            "SERPAPI_KEY is UNSET — every non-Shopify store falls back to the "
            "narrow curated catalog. Set it on the connector app."
        )

    try:
        stores = ns._get_directory().get("stores", [])
    except Exception as e:  # noqa: BLE001
        print(f"FATAL: could not load registry: {e}", file=sys.stderr)
        return 2

    catbacked = [s for s in stores if s.get("catalog_backed") or s.get("live_search")]
    others = [s for s in stores if not (s.get("catalog_backed") or s.get("live_search"))]
    sample = catbacked + random.sample(others, min(args.sample, len(others)))

    # Probe stores concurrently — each classify() does a live HTTP fetch, so
    # serial would take minutes on a large sample.
    from concurrent.futures import ThreadPoolExecutor

    def _one(s):
        try:
            cat, detail = classify(s, args.probe_serpapi)
        except Exception as e:  # noqa: BLE001
            cat, detail = "narrow", f"probe error: {type(e).__name__}"
        return {
            "name": s.get("name"),
            "domain": s.get("domain"),
            "catalog_backed": bool(s.get("catalog_backed") or s.get("live_search")),
            "result": cat,
            "detail": detail,
        }

    counts = {"shopify": 0, "serpapi": 0, "narrow": 0}
    rows = []
    narrow_rows = []
    with ThreadPoolExecutor(max_workers=16) as ex:
        for row in ex.map(_one, sample):
            counts[row["result"]] += 1
            rows.append(row)
            if row["result"] == "narrow":
                narrow_rows.append(row)

    total = len(sample)
    narrow_pct = 100.0 * counts["narrow"] / max(1, total)

    # A catalog_backed brand that resolves narrow is a hard failure — those are
    # exactly the brands that are supposed to be wide via SerpApi.
    catbacked_narrow = [r for r in narrow_rows if r["catalog_backed"]]
    if catbacked_narrow:
        problems.append(
            f"{len(catbacked_narrow)} catalog_backed brand(s) resolved NARROW: "
            + ", ".join(r["name"] or r["domain"] for r in catbacked_narrow)
        )

    fail = bool(problems) or narrow_pct > args.max_narrow_pct

    if args.json:
        print(json.dumps({
            "serpapi_key_set": key_set,
            "sampled": total,
            "counts": counts,
            "narrow_pct": round(narrow_pct, 1),
            "problems": problems,
            "narrow": narrow_rows,
            "rows": rows,
        }, indent=2))
        return 1 if fail else 0

    print("Nash breadth health check")
    print("=" * 40)
    print(f"SERPAPI_KEY set : {key_set}")
    print(f"Sampled         : {total} stores "
          f"({len(catbacked)} catalog_backed + {total - len(catbacked)} sampled)")
    print(f"  WIDE  shopify : {counts['shopify']}")
    print(f"  WIDE  serpapi : {counts['serpapi']}")
    print(f"  NARROW        : {counts['narrow']}  ({narrow_pct:.1f}%)")
    if narrow_rows:
        print("\nStores falling back to the narrow catalog:")
        for r in narrow_rows[:50]:
            tag = " [catalog_backed!]" if r["catalog_backed"] else ""
            print(f"  - {r['name'] or r['domain']}{tag}: {r['detail']}")
    if problems:
        print("\nPROBLEMS:")
        for p in problems:
            print(f"  ⚠ {p}")
    print("\nRESULT:", "FAIL" if fail else "OK")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
