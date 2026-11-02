#!/usr/bin/env python3
"""
wfm_prices.py - Warframe Market price checker

For each item in a category it shows, from SELL orders:
    number of listings, min, median and max platinum
(max ignores extreme outliers, such as a 98,000 platinum troll listing)
plus ducat value (prime parts) and a suggested price, in one of two modes:
    Sell Fast                    = 1 platinum under the cheapest realistic sell listing (default, never above Best Price)
    Best Price (Price Clusters)  = median of the busiest price cluster among sell listings

Categories: prime parts, mods (veiled rivens included), arcanes, relics

Setup:   pip install requests
Usage:
    python wfm_prices.py                          # interactive menu
    python wfm_prices.py -c mods --search serration
    python wfm_prices.py -c prime --limit 25 --csv prime.csv
    python wfm_prices.py -c relics --online-only --platform pc
"""

import argparse
import csv
import json
import re
import statistics
import sys
import time
from difflib import SequenceMatcher

import requests

API_V2 = "https://api.warframe.market/v2"
MIN_DELAY = 0.4  # seconds between requests (the API allows about 3 per second)

CATEGORIES = {
    "prime": "Prime parts",
    "mods": "Mods",
    "arcanes": "Arcanes",
    "relics": "Relics",
}
MENU = ["prime", "mods", "arcanes", "relics"]


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
class Client:
    def __init__(self, platform="pc"):
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "Language": "en",
            "Platform": platform,
            "User-Agent": "wfm-price-checker/1.0",
        })
        self._last = 0.0

    def get(self, url, params=None):
        """GET with rate limiting and retries. Returns parsed JSON, or None on 404."""
        for attempt in range(5):
            wait = MIN_DELAY - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                r = self.session.get(url, params=params, timeout=20)
            except requests.RequestException as e:
                if attempt == 4:
                    raise SystemExit(f"Network error: {e}")
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(2 ** attempt + 1)
                continue
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
        raise SystemExit(f"Gave up after repeated failures: {url}")


# --------------------------------------------------------------------------
# Items and categories
# --------------------------------------------------------------------------
def item_name(item):
    en = (item.get("i18n") or {}).get("en") or {}
    return en.get("name") or item.get("name") or item.get("slug", "?")


def classify(item, include_sets=False):
    """Return 'prime', 'mods', 'arcanes', 'relics', or None."""
    tags = {str(t).lower() for t in (item.get("tags") or [])}
    slug = (item.get("slug") or "").lower()

    # Veiled (unrevealed) rivens are listed as normal items and count as mods.
    if "veiled" in slug or "veiled" in item_name(item).lower():
        return "mods"
    if "relic" in tags or slug.endswith("_relic"):
        return "relics"
    if "arcane_enhancement" in tags or "arcane" in tags or slug.startswith("arcane_"):
        return "arcanes"
    if "mod" in tags:
        return "mods"
    if "prime" in tags or "_prime_" in slug:
        is_set = "set" in tags or slug.endswith("_set")
        if is_set and not include_sets:
            return None
        return "prime"
    return None


def fuzzy_score(query, name):
    """0 = no match, higher = better. Exact text matches score highest; small typos still match."""
    q = query.lower().strip()
    n = name.lower()
    if not q:
        return 1.0
    if q in n:
        return 4.0 if n.startswith(q) else 3.0
    words = re.findall(r"[a-z0-9']+", n)
    tokens = q.split()
    if not words or not tokens:
        return 0.0
    total = 0.0
    for tok in tokens:
        if tok in n:
            total += 1.0
            continue
        if len(tok) < 3:
            return 0.0
        best = 0.0
        for w in words:
            best = max(best, SequenceMatcher(None, tok, w).ratio(),
                       SequenceMatcher(None, tok, w[:len(tok)]).ratio())  # half-typed words
        if best < (0.7 if len(tok) >= 5 else 0.78):
            return 0.0
        total += best * 0.9
    return total / len(tokens)


def search_items(items, query):
    """Items matching the search text (typo tolerant), best matches first. No text = all items."""
    if not query or not query.strip():
        return list(items)
    scored = []
    for it in items:
        s = fuzzy_score(query, item_name(it))
        if s > 0:
            scored.append((-s, item_name(it).lower(), it))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [t[2] for t in scored]


def load_items(client):
    j = client.get(f"{API_V2}/items")
    data = (j or {}).get("data")
    if not data:
        raise SystemExit("Could not read the item list (unexpected response). Try --debug.")
    return data


# --------------------------------------------------------------------------
# Orders
# --------------------------------------------------------------------------
def norm_orders(j):
    """Turn an orders response into a flat list of simple dicts."""
    if not j:
        return []
    raw = j.get("data")
    if raw is None:
        raw = (j.get("payload") or {}).get("orders", [])
    if isinstance(raw, dict):  # {"sell": [...], "buy": [...]}
        flat = []
        for kind in ("sell", "buy"):
            for o in raw.get(kind, []):
                o = dict(o)
                o.setdefault("type", kind)
                flat.append(o)
        raw = flat

    out = []
    for o in raw:
        try:
            plat = float(o["platinum"])
        except (KeyError, TypeError, ValueError):
            continue
        if o.get("visible") is False:
            continue
        user = o.get("user") or {}
        out.append({
            "type": str(o.get("type") or o.get("order_type") or "").lower(),
            "platinum": plat,
            "quantity": max(int(o.get("quantity") or 1), 1),
            "rank": o.get("rank", o.get("mod_rank")),
            "status": str(user.get("status") or "").lower(),
            "subtype": str(o.get("subtype") or "").lower(),
        })
    return out


def resolve_rank(rank, item, orders):
    """Turn a rank choice into a number. None = any rank, 0 = unranked, "max" = the item's max rank."""
    if rank != "max":
        return rank
    top = item.get("maxRank", item.get("max_rank"))
    if top is None:  # item data has no max rank: use the highest rank seen in the orders
        seen = [o["rank"] for o in orders if o["rank"] is not None]
        top = max(seen) if seen else None
    return top


def item_variants(item):
    """Variants an item comes in, lower case (mods with a cosmetic Atragraph copy: regular + atragraph)."""
    return [str(v).lower() for v in (item.get("subtypes") or [])]


def has_atragraph(item):
    return "atragraph" in item_variants(item) and "regular" in item_variants(item)


LIVE_STATUSES = ("ingame", "online")
SELLER_CHOICES = ("all", "online", "offline")


def seller_mode(value):
    """Normalise the seller filter: "all", "online" (online / in-game only) or "offline" (offline only).
    Old True / False values still work (True = online only)."""
    if value is True:
        return "online"
    if not value:
        return "all"
    value = str(value).lower()
    return value if value in SELLER_CHOICES else "all"


def apply_filters(orders, online_only=False, rank=None, variant=None):
    """online_only is the seller filter: "all" / "online" / "offline" (True / False also accepted)."""
    mode = seller_mode(online_only)
    out = []
    for o in orders:
        if variant == "atragraph" and o["subtype"] != "atragraph":
            continue
        if variant == "regular" and o["subtype"] not in ("regular", ""):
            continue
        live = o["status"] in LIVE_STATUSES
        if (mode == "online" and not live) or (mode == "offline" and live):
            continue
        if rank is not None and o["rank"] is not None and o["rank"] != rank:
            continue
        out.append(o)
    return out


# --------------------------------------------------------------------------
# Maths
# --------------------------------------------------------------------------
# A sell price counts as an outlier when it is above BOTH of these limits:
#   Q3 + 1.5 * IQR  (the usual statistics rule), and
#   OUTLIER_FACTOR * median  (so tightly packed prices do not flag normal listings)
OUTLIER_FACTOR = 3.0


def outlier_limit(prices):
    """Highest price still treated as normal, or None when there are too few prices to tell."""
    if len(prices) < 4:
        return None
    q1, _, q3 = statistics.quantiles(sorted(prices), n=4, method="inclusive")
    return max(q3 + 1.5 * (q3 - q1), OUTLIER_FACTOR * statistics.median(prices))


def stats(prices):
    if not prices:
        return {"count": 0, "min": None, "median": None, "max": None,
                "outliers": 0, "top_outlier": None}
    limit = outlier_limit(prices)
    normal = [p for p in prices if limit is None or p <= limit]
    outliers = [p for p in prices if limit is not None and p > limit]
    return {
        "count": len(prices),
        "min": min(prices),
        "median": statistics.median(prices),
        "max": max(normal),
        "outliers": len(outliers),
        "top_outlier": max(outliers) if outliers else None,
    }


# Two ways to suggest a price (the app lets you switch between them):
PRICE_MODES = {
    "fast": "Sell Fast",
    "clusters": "Best Price (Price Clusters)",
}
DEFAULT_PRICE_MODE = "fast"

UNDERCUT = 1.0          # Sell Fast: how many platinum under the cheapest realistic listing
LOWBALL_FACTOR = 0.5    # Sell Fast: a listing under this share of the median is a lowball, not a real competitor
CLUSTER_WIDTH = 0.15    # Price clusters: a cluster spans this share of its lowest price...
CLUSTER_MIN_WIDTH = 2.0 # ...but never less than this many platinum


def _whole(x):
    """Round half up to whole platinum (Python's round() would round 2.5 down to 2)."""
    return float(int(x + 0.5))


def sell_fast_price(orders):
    """Price to list at to sell quickly: 1 platinum under the cheapest realistic sell listing.
    It uses exactly the listings you selected with the Sellers setting (all / online and in-game /
    offline), so it always agrees with the Listings, Min, Median and Max columns. Lowball listings
    far under the median are ignored, and the result is never higher than the Best Price.
    None when there are no sell orders."""
    prices = sorted(o["platinum"] for o in orders if o["type"] == "sell")
    if not prices:
        return None
    med = statistics.median(prices)
    realistic = [p for p in prices if p >= LOWBALL_FACTOR * med] or prices
    fast = max(1.0, realistic[0] - UNDERCUT)
    best = cluster_price(orders)
    if best is not None and fast > best:
        fast = best
    return fast


def cluster_price(orders):
    """Price from the busiest price cluster: the sell prices are scanned for the narrow price band
    (about CLUSTER_WIDTH wide) holding the most listings - where the market really bunches up -
    and the suggestion is the median price inside it. Extreme high outliers are ignored.
    If no two listings bunch together it falls back to the median sell price.
    None when there are no sell orders."""
    prices = sorted(o["platinum"] for o in orders if o["type"] == "sell")
    if not prices:
        return None
    limit = outlier_limit(prices)
    if limit is not None:
        prices = [p for p in prices if p <= limit]
    best = []
    for i, p in enumerate(prices):
        hi = p + max(CLUSTER_MIN_WIDTH, CLUSTER_WIDTH * p)
        members = [q for q in prices[i:] if q <= hi]
        if len(members) > len(best):       # ties keep the cheaper cluster
            best = members
    if len(best) < 2:
        return _whole(statistics.median(prices))
    return _whole(statistics.median(best))


def suggested_price(orders, mode=DEFAULT_PRICE_MODE):
    return cluster_price(orders) if mode == "clusters" else sell_fast_price(orders)


ASSET_BASE = "https://warframe.market/static/assets/"


def image_url(item):
    """Full URL of the item's thumbnail (falls back to the icon), or None."""
    en = (item.get("i18n") or {}).get("en") or {}
    path = en.get("thumb") or en.get("icon") or item.get("thumb") or item.get("icon")
    if not path:
        return None
    return path if str(path).startswith("http") else ASSET_BASE + str(path).lstrip("/")


def image_urls(item):
    """Picture links to try, best first: thumbnail, then full icon."""
    en = (item.get("i18n") or {}).get("en") or {}
    out = []
    for path in (en.get("thumb"), en.get("icon"), item.get("thumb"), item.get("icon")):
        if path:
            url = path if str(path).startswith("http") else ASSET_BASE + str(path).lstrip("/")
            if url not in out:
                out.append(url)
    return out


STAT_KEYS = ("listings", "min", "median", "max", "outliers", "top_outlier", "price_fast", "price_cluster")


def compute_row(item, raw_orders, online_only=False, rank=None, variant=None):
    """One result row for an item, from the raw orders response.
    variant: None = all orders, or "regular" / "atragraph" to count only that version of a mod."""
    orders = norm_orders(raw_orders)
    orders = apply_filters(orders, online_only, resolve_rank(rank, item, orders), variant)
    st = stats([o["platinum"] for o in orders if o["type"] == "sell"])
    return {
        "name": item_name(item),
        "listings": st["count"],
        "min": st["min"],
        "median": st["median"],
        "max": st["max"],
        "outliers": st["outliers"],
        "top_outlier": st["top_outlier"],
        "ducats": item.get("ducats"),
        "price_fast": sell_fast_price(orders),
        "price_cluster": cluster_price(orders),
        "image_url": image_url(item),
        "image_urls": image_urls(item),
    }


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------
def fmt(v):
    if v is None:
        return "-"
    v = float(v)
    return f"{v:,.0f}" if v.is_integer() else f"{v:,.1f}"


def print_table(headers, rows):
    widths = [max([len(str(h))] + [len(str(r[i])) for r in rows]) for i, h in enumerate(headers)]

    def line(cells):
        parts = []
        for i, c in enumerate(cells):
            parts.append(str(c).ljust(widths[i]) if i == 0 else str(c).rjust(widths[i]))
        return "  ".join(parts)

    print(line(headers))
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print(line(r))


def sort_rows(rows, key):
    if key == "name":
        return sorted(rows, key=lambda r: r["name"].lower())
    return sorted(rows, key=lambda r: (r.get(key) is None, -(r.get(key) or 0)))


def write_csv(path, rows, fields):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"\nSaved {len(rows)} rows to {path}")


# --------------------------------------------------------------------------
# Runners
# --------------------------------------------------------------------------
def run_items(client, cat, args):
    items = load_items(client)
    if args.debug and items:
        print("DEBUG first item:", json.dumps(items[0], indent=2)[:1500], file=sys.stderr)

    picked = search_items([it for it in items if classify(it, args.include_sets) == cat], args.search)
    if args.limit:
        picked = picked[: args.limit]
    if not picked:
        print("No items matched. Try a different --search, or run with --debug.")
        return
    if args.debug:
        print("DEBUG first matching item:", json.dumps(picked[0], indent=2)[:2000], file=sys.stderr)
        print("DEBUG picture link built from it:", image_url(picked[0]), file=sys.stderr)

    rows = []
    total = len(picked)
    for n, it in enumerate(picked, 1):
        name = item_name(it)
        print(f"[{n}/{total}] {name}", file=sys.stderr)
        raw = client.get(f"{API_V2}/orders/item/{it.get('slug')}")
        if args.debug and n == 1:
            print("DEBUG raw orders:", json.dumps(raw, indent=2)[:1500], file=sys.stderr)
        rows.append(compute_row(it, raw, args.online_only, args.rank, args.variant))

    rows = sort_rows(rows, args.sort)
    show_ducats = cat == "prime"
    headers = ["Item", "Listings", "Min", "Median", "Max"]
    if show_ducats:
        headers.append("Ducats")
    headers.append(PRICE_MODES[args.price_mode])
    price_key = "price_cluster" if args.price_mode == "clusters" else "price_fast"

    table = []
    for r in rows:
        cells = [r["name"], r["listings"], fmt(r["min"]), fmt(r["median"]), fmt(r["max"])]
        if show_ducats:
            cells.append(fmt(r["ducats"]))
        cells.append(fmt(r[price_key]))
        table.append(cells)

    print(f"\n=== {CATEGORIES[cat]} - sell orders ({args.platform.upper()}"
          f"{', online/in-game only' if args.online_only == 'online' else (', offline only' if args.online_only == 'offline' else '')}) ===")
    print_table(headers, table)
    if args.price_mode == "clusters":
        print("\nBest Price = median price of the busiest price cluster among sell listings. "
              "'-' means no sell orders.")
    else:
        print("\nSell Fast = 1 platinum under the cheapest realistic sell listing "
              "'-' means no sell orders.")
    cut = [r for r in rows if r["outliers"]]
    if cut:
        print("Max ignores outlier listings: " + "; ".join(
            f"{r['name']} ({r['outliers']} above, highest {fmt(r['top_outlier'])})" for r in cut))

    if args.csv:
        fields = ["name", "listings", "min", "median", "max", "outliers", "top_outlier"]
        if show_ducats:
            fields.append("ducats")
        fields += ["price_fast", "price_cluster"]
        write_csv(args.csv, rows, fields)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def pick_category():
    print("What do you want to look at?")
    for i, k in enumerate(MENU, 1):
        print(f"  {i}) {CATEGORIES[k]}")
    while True:
        choice = input("Enter a number: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(MENU):
            return MENU[int(choice) - 1]
        print("Please enter a number from the list.")


def rank_arg(text):
    t = text.strip().lower()
    if t == "any":
        return None
    if t == "unranked":
        return 0
    if t == "max":
        return "max"
    if t.isdigit():
        return int(t)
    raise argparse.ArgumentTypeError("use any, unranked, max, or a number")


def build_parser():
    p = argparse.ArgumentParser(description="Warframe Market price checker (sell orders).")
    p.add_argument("-c", "--category", choices=MENU, help="prime, mods, arcanes or relics")
    p.add_argument("--search", help="only items matching this text (typos are tolerated)")
    p.add_argument("--limit", type=int, help="only check the first N matching items")
    p.add_argument("--sort", choices=["name", "min", "max", "median", "ducats"], default="name")
    p.add_argument("--csv", metavar="FILE", help="also save results to a CSV file")
    p.add_argument("--platform", choices=["pc", "xbox", "ps4", "switch"], default="pc")
    p.add_argument("--online-only", action="store_const", const="online", default="all", dest="online_only",
                   help="only count players who are online or in-game (off by default)")
    p.add_argument("--offline-only", action="store_const", const="offline", dest="online_only",
                   help="only count players who are offline")
    p.add_argument("--rank", type=rank_arg, default=None,
                   help='mods/arcanes only: "any" (default), "unranked" (rank 0), "max", or a number')
    p.add_argument("--variant", choices=["regular", "atragraph"], default=None,
                   help="mods only: count only the regular or only the Atragraph version (default: both together)")
    p.add_argument("--price-mode", choices=list(PRICE_MODES), default=DEFAULT_PRICE_MODE,
                   help="suggested price: fast = Sell Fast (default), clusters = Best Price (Price Clusters)")
    p.add_argument("--include-sets", action="store_true", help="include prime sets in the prime category")
    p.add_argument("--debug", action="store_true", help="print raw API samples to stderr")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    cat = args.category or pick_category()
    client = Client(args.platform)
    try:
        run_items(client, cat, args)
    except requests.HTTPError as e:
        raise SystemExit(f"The API refused the request: {e}")


if __name__ == "__main__":
    main()
