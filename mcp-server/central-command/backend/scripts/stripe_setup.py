#!/usr/bin/env python3
"""Verify + wire the Stripe account for OsirisCare billing.

Runs locally with STRIPE_API_KEY in env. No Python dependencies — uses urllib
against the Stripe REST API directly so you don't need to pip-install `stripe`.

What it checks:

  1. The four products named "OsirisCare {Pilot|Essentials|Professional|Enterprise}"
     exist and have an active price.
  2. Each active price has the lookup_key the backend expects:
        pilot        -> osiris-pilot-onetime
        essentials   -> osiris-essentials-monthly
        professional -> osiris-professional-monthly
        enterprise   -> osiris-enterprise-monthly
  3. A webhook endpoint exists for https://www.osiriscare.net/api/billing/webhook
     and is subscribed to the five events the backend actually handles.

Run modes:

    python stripe_setup.py --verify           # read-only, prints a report
    python stripe_setup.py --fix              # idempotently corrects drift
    python stripe_setup.py --fix --webhook    # also creates webhook if missing
                                                (prints signing secret ONCE —
                                                 capture it for VPS env)

Safety rails:

  * Never prints the Stripe API key.
  * --verify never mutates anything.
  * Webhook SECRET is printed only once, at creation. If the webhook already
    exists, we cannot recover the secret via API — you'll need to pull it
    from the Dashboard (Developers → Webhooks → Click endpoint → Signing secret).

Usage:

    export STRIPE_API_KEY=sk_live_...          # or sk_test_...
    python mcp-server/central-command/backend/scripts/stripe_setup.py --verify
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

API_BASE = "https://api.stripe.com/v1"

# (product_name_suffix, expected_lookup_key, expected_recurring, description)
EXPECTED_PRODUCTS: List[Tuple[str, str, bool, str]] = [
    ("Pilot",        "osiris-pilot-onetime",       False, "$299 one-time 90-day pilot"),
    ("Essentials",   "osiris-essentials-monthly",  True,  "$499/mo"),
    ("Professional", "osiris-professional-monthly", True, "$799/mo"),
    ("Enterprise",   "osiris-enterprise-monthly",  True,  "$1,299/mo"),
]

WEBHOOK_URL = "https://www.osiriscare.net/api/billing/webhook"
WEBHOOK_EVENTS: List[str] = [
    "checkout.session.completed",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "invoice.paid",
    "invoice.payment_failed",
]


# ─── Tiny Stripe API client (urllib-based) ───────────────────────────

def _stripe_request(
    api_key: str,
    method: str,
    path: str,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    url = f"{API_BASE}{path}"
    data: Optional[bytes] = None
    if method == "GET" and params:
        url = f"{url}?{_encode_params(params)}"
    elif params:
        data = _encode_params(params).encode("utf-8")

    req = urllib.request.Request(url=url, data=data, method=method)
    auth = base64.b64encode(f"{api_key}:".encode()).decode()
    req.add_header("Authorization", f"Basic {auth}")
    req.add_header("Stripe-Version", "2024-06-20")
    if data is not None:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            body_json = json.loads(body)
        except json.JSONDecodeError:
            body_json = {"raw": body}
        raise SystemExit(
            f"Stripe API error {e.code} on {method} {path}: "
            f"{body_json.get('error', {}).get('message', body)}"
        )


def _encode_params(params: Dict[str, Any]) -> str:
    """Stripe form-encodes nested params as `key[0]=v0&key[1]=v1`. For the
    endpoints we use, flat + repeat-key-for-arrays is enough."""
    pairs: List[Tuple[str, str]] = []
    for k, v in params.items():
        if isinstance(v, list):
            for i, item in enumerate(v):
                pairs.append((f"{k}[{i}]", str(item)))
        elif isinstance(v, bool):
            pairs.append((k, "true" if v else "false"))
        else:
            pairs.append((k, str(v)))
    return urllib.parse.urlencode(pairs)


def stripe_list_all(api_key: str, path: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Paginate a /list endpoint until exhausted."""
    items: List[Dict[str, Any]] = []
    cursor: Optional[str] = None
    while True:
        p = dict(params or {})
        p["limit"] = 100
        if cursor:
            p["starting_after"] = cursor
        resp = _stripe_request(api_key, "GET", path, p)
        items.extend(resp.get("data", []))
        if not resp.get("has_more"):
            break
        cursor = resp["data"][-1]["id"]
    return items


# ─── Report helpers ──────────────────────────────────────────────────

OK = "\033[92m✓\033[0m"
BAD = "\033[91m✗\033[0m"
WARN = "\033[93m!\033[0m"
INFO = "\033[94m→\033[0m"


def _mask_key(api_key: str) -> str:
    if not api_key or len(api_key) < 12:
        return "(none)"
    return f"{api_key[:7]}…{api_key[-4:]}"


def _live_or_test(api_key: str) -> str:
    if api_key.startswith("sk_live_"):
        return "\033[91mLIVE\033[0m"
    if api_key.startswith("sk_test_"):
        return "\033[93mtest\033[0m"
    return "unknown"


# ─── Product + price checks ─────────────────────────────────────────

def find_product_by_name(products: List[Dict[str, Any]], suffix: str) -> Optional[Dict[str, Any]]:
    # Match "OsirisCare Pilot" exactly or any product whose name ends with the suffix.
    target = f"OsirisCare {suffix}".lower()
    for p in products:
        name = (p.get("name") or "").strip().lower()
        if name == target or name.endswith(suffix.lower()):
            return p
    return None


def audit_products(api_key: str, fix: bool) -> Tuple[int, int]:
    """Return (drift_count, fixed_count)."""
    print(f"\n{INFO} Product + price audit")
    products = stripe_list_all(api_key, "/products", {"active": True})
    drift = 0
    fixed = 0

    for suffix, expected_key, expect_recurring, blurb in EXPECTED_PRODUCTS:
        product = find_product_by_name(products, suffix)
        if not product:
            print(f"  {BAD} OsirisCare {suffix}: product NOT FOUND")
            drift += 1
            continue

        prices = stripe_list_all(
            api_key, "/prices",
            {"product": product["id"], "active": True},
        )
        if not prices:
            print(f"  {BAD} OsirisCare {suffix}: no active price (expected {blurb})")
            drift += 1
            continue

        # Prefer the price whose lookup_key already matches; otherwise the first.
        matching = [pr for pr in prices if pr.get("lookup_key") == expected_key]
        price = matching[0] if matching else prices[0]

        actual_key = price.get("lookup_key")
        is_recurring = price.get("recurring") is not None
        amount = price.get("unit_amount")
        currency = (price.get("currency") or "").upper()

        cadence = "recurring" if is_recurring else "one-time"
        want_cadence = "recurring" if expect_recurring else "one-time"
        amount_str = f"${amount/100:,.2f} {currency}" if amount is not None else "(no amount)"

        row = f"  OsirisCare {suffix:<13} {amount_str:>15} {cadence:>10}  lookup_key={actual_key or '(none)'}"

        if actual_key == expected_key and is_recurring == expect_recurring:
            print(f"  {OK}{row}")
            continue

        if is_recurring != expect_recurring:
            print(
                f"  {BAD}{row}\n"
                f"      expected {want_cadence} — fix manually in Stripe Dashboard "
                f"(you may need to create a new Price and archive the wrong one)"
            )
            drift += 1
            continue

        # Drift is just the lookup_key — fixable via API.
        print(f"  {WARN}{row}")
        print(f"      expected lookup_key={expected_key}")
        drift += 1

        if fix:
            _stripe_request(
                api_key, "POST", f"/prices/{price['id']}",
                {"lookup_key": expected_key, "transfer_lookup_key": "true"},
            )
            print(f"      {OK} updated price {price['id']}.lookup_key={expected_key}")
            fixed += 1

    return drift, fixed


# ─── Webhook checks ─────────────────────────────────────────────────

def audit_webhook(api_key: str, fix: bool, create_webhook: bool) -> Tuple[bool, Optional[str]]:
    """Return (ok, signing_secret_if_created)."""
    print(f"\n{INFO} Webhook endpoint audit")
    endpoints = stripe_list_all(api_key, "/webhook_endpoints", None)
    matching = [e for e in endpoints if e.get("url") == WEBHOOK_URL]

    if matching:
        ep = matching[0]
        enabled = set(ep.get("enabled_events") or [])
        missing = [e for e in WEBHOOK_EVENTS if e not in enabled and "*" not in enabled]
        status = ep.get("status")
        print(f"  {OK} endpoint exists: {ep['id']} (status={status})")
        if missing:
            print(f"  {WARN} missing events: {missing}")
            if fix:
                _stripe_request(
                    api_key, "POST", f"/webhook_endpoints/{ep['id']}",
                    {"enabled_events": sorted(set(WEBHOOK_EVENTS) | enabled)},
                )
                print(f"  {OK} updated endpoint to subscribe to all {len(WEBHOOK_EVENTS)} events")
            else:
                print(f"  {INFO} run with --fix to subscribe to missing events")
        else:
            print(f"  {OK} subscribed to all {len(WEBHOOK_EVENTS)} required events")
        print(
            f"  {INFO} Signing secret is NOT available via API for existing endpoints.\n"
            f"       Pull it from Stripe Dashboard → Developers → Webhooks → click this endpoint → Reveal."
        )
        return True, None

    # No matching webhook exists.
    print(f"  {BAD} no webhook for {WEBHOOK_URL}")
    if not (fix and create_webhook):
        print(f"  {INFO} run with --fix --webhook to create it and reveal signing secret")
        return False, None

    resp = _stripe_request(
        api_key, "POST", "/webhook_endpoints",
        {
            "url": WEBHOOK_URL,
            "enabled_events": WEBHOOK_EVENTS,
            "description": "OsirisCare billing webhook (partner + client)",
        },
    )
    secret = resp.get("secret")
    print(f"  {OK} created endpoint {resp['id']} (status={resp.get('status')})")
    return True, secret


# ─── Main ────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--verify", action="store_true", help="read-only audit (default)")
    parser.add_argument("--fix", action="store_true", help="apply idempotent corrections")
    parser.add_argument(
        "--webhook", action="store_true",
        help="with --fix: create the webhook endpoint if missing (prints signing secret ONCE)",
    )
    args = parser.parse_args()

    api_key = os.getenv("STRIPE_API_KEY", "").strip()
    if not api_key:
        print(
            "STRIPE_API_KEY not set. Set it and re-run:\n"
            "  export STRIPE_API_KEY=sk_live_...   # or sk_test_...\n",
            file=sys.stderr,
        )
        return 2

    if not (args.verify or args.fix):
        args.verify = True  # default to read-only

    print(f"Mode: {_live_or_test(api_key)} ({_mask_key(api_key)})")
    if args.fix:
        print(f"{WARN} running in --fix mode — will mutate products, prices, and/or webhooks")

    drift, fixed = audit_products(api_key, fix=args.fix)
    webhook_ok, webhook_secret = audit_webhook(api_key, fix=args.fix, create_webhook=args.webhook)

    print()
    if drift == 0 and webhook_ok and webhook_secret is None:
        print(f"{OK} Stripe account matches backend expectations. No changes needed.")
    else:
        print(
            f"Summary: {drift} drift(s) found, {fixed} fixed, webhook "
            f"{'OK' if webhook_ok else 'MISSING'}."
        )

    if webhook_secret:
        print()
        print(f"{WARN} Webhook signing secret (captured ONCE — copy into VPS env now):")
        print()
        print(f"    STRIPE_WEBHOOK_SECRET={webhook_secret}")
        print()
        print("Set it on the VPS:")
        print()
        print("  ssh root@178.156.162.116")
        print("  cd /opt/mcp-server")
        print(f"  # edit .env — STRIPE_WEBHOOK_SECRET={webhook_secret[:8]}…")
        print("  vi .env")
        print("  docker compose restart mcp-server")
        print()

    return 0 if (drift == 0 and webhook_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
