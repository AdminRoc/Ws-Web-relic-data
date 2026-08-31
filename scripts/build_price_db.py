#!/usr/bin/env python3
"""Build Database B: rolling 72h price database for relic reward items.

For each tradable reward item, fetches WM /top sell orders,
computes a price snapshot (remove lowest, average next 3),
appends to rolling history, purges entries >72h old.

Computes:
  - recent_avg: most recent price snapshot
  - three_day_avg: average of all snapshots within 72h

Outputs:
  data/prices.json       — price history with computed averages
  data/item-categories.json — item urlName -> category mapping
"""

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(REPO_ROOT, "data")

WM_API = "https://api.warframe.market"
MAX_HISTORY_HOURS = 72
REQUEST_DELAY = 0.35  # seconds between API calls (~2.8 req/s under the 3/s limit)
MAX_ITEMS_PER_RUN = 0  # 0 = all items

# Item name -> category classification patterns
# Based on known Warframe item naming and WM tags
CATEGORY_KEYWORDS = {
    "warframe": [
        "Chassis Blueprint", "Neuroptics Blueprint", "Systems Blueprint",
        "Harness Blueprint", "Wings Blueprint",
    ],
    "primary": [
        "Upper Limb", "Lower Limb", "String", "Grip",
    ],
    "secondary": [
        "Link",
    ],
    "melee": [
        "Blade", "Handle", "Hilt", "Guard", "Gauntlet", "Disc",
        "Chain", "Head", "Boot", "Ornament",
    ],
}

# WM API tag -> our category (tags field of /v2/items)
TAG_CATEGORY = {
    "warframe": "warframe",
    "primary": "primary",
    "secondary": "secondary",
    "melee": "melee",
}

# 部件名后缀（用于名称推导：去掉后缀得到武器基名，查蓝图/套装 tags）
PART_SUFFIXES = [
    'Blueprint', 'Receivers', 'Receiver', 'Barrels', 'Barrel', 'Stocks', 'Stock',
    'Blades', 'Blade', 'Handles', 'Handle', 'Links', 'Link', 'Strings', 'String',
    'Grips', 'Grip', 'Upper Limb', 'Lower Limb', 'Limbs', 'Heads', 'Head',
    'Guards', 'Guard', 'Gauntlets', 'Gauntlet', 'Discs', 'Disc', 'Chains', 'Chain',
    'Boots', 'Boot', 'Hilts', 'Hilt', 'Ornaments', 'Ornament', 'Pouches', 'Pouch',
    'Stars', 'Motor', 'Carapace', 'Cerebrum', 'Systems', 'Chassis', 'Neuroptics',
    'Harness', 'Wings', 'Collar', 'Band', 'Buckle',
]
SIDE_WORDS = (' Right', ' Left')

WM_ITEMS_URL = "https://api.warframe.market/v2/items"


def fetch_json(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Ws-Web-relic/1.0",
        "Platform": "pc",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _slugify(name):
    import re
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _base_name(part_name):
    """去掉部件后缀，返回武器基名；失败返回 None。"""
    for sfx in PART_SUFFIXES:
        if part_name.endswith(" " + sfx):
            base = part_name[: -(len(sfx) + 1)]
            for side in SIDE_WORDS:
                if base.endswith(side):
                    base = base[: -len(side)]
            return base
    return None


def _cat_from_tags(tags):
    for t in tags or []:
        if t in TAG_CATEGORY:
            return TAG_CATEGORY[t]
    return None


def build_wm_classifier():
    """Fetch WM /v2/items, build a classifier based on WM tags.

    Strategy:
      1. Item's own tags: warframe/primary/secondary/melee -> direct match
      2. Weapon components only carry 'weapon' tag -> strip the part
         suffix from the name and look up the weapon blueprint/set tags
         (e.g. 'Redeemer Prime Blade' -> redeemer_prime_blueprint -> melee)
      3. Fallback: keyword heuristic on the item name
    Returns a function classify(url_name) -> category string.
    """
    data = fetch_json(WM_ITEMS_URL)
    items = data.get("data", [])
    by_slug = {}
    for it in items:
        by_slug[it.get("slug")] = it

    def classify(url_name):
        it = by_slug.get(url_name)
        if it:
            c = _cat_from_tags(it.get("tags"))
            if c:
                return c
            if "weapon" in (it.get("tags") or []):
                en_name = (it.get("i18n") or {}).get("en", {}).get("name") or ""
                base = _base_name(en_name)
                if base:
                    root_slug = _slugify(base)
                    root = by_slug.get(root_slug + "_blueprint") or by_slug.get(root_slug + "_set")
                    if root:
                        c = _cat_from_tags(root.get("tags"))
                        if c:
                            return c
        return classify_item(url_name) if url_name else "other"

    return classify


def classify_item(item_name):
    """Fallback keyword heuristic: classify item into warframe/primary/secondary/melee/other"""
    name_lower = (item_name or "").lower()

    for kw in CATEGORY_KEYWORDS["warframe"]:
        if kw.lower() in name_lower:
            return "warframe"
    for kw in CATEGORY_KEYWORDS["melee"]:
        if kw.lower() in name_lower:
            return "melee"
    for kw in CATEGORY_KEYWORDS["secondary"]:
        if kw.lower() in name_lower:
            return "secondary"
    for kw in CATEGORY_KEYWORDS["primary"]:
        if kw.lower() in name_lower:
            return "primary"

    if "blueprint" in name_lower:
        weapon_parts = ["barrel", "receiver", "stock", "blade", "handle", "link",
                        "string", "grip", "limb", "guard", "gauntlet", "disc",
                        "chain", "head", "boot", "hilt", "ornament"]
        if not any(p in name_lower for p in weapon_parts):
            return "warframe"

    return "other"


def compute_price_snapshot(sell_orders):
    """去掉最低价后，取紧随其后的 2 个最低价做平均。返回 float 或 None。"""
    if not sell_orders:
        return None
    prices = sorted([o["platinum"] for o in sell_orders if o.get("platinum")])
    if len(prices) < 3:
        return None
    # 去掉最低价
    prices = prices[1:]
    # 取紧随其后的 2 个最低价做平均
    top2 = prices[:2]
    if not top2:
        return None
    return round(sum(top2) / len(top2), 2)


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_expired(ts_str):
    """Check if a timestamp is older than MAX_HISTORY_HOURS"""
    try:
        ts = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - ts
        return age > timedelta(hours=MAX_HISTORY_HOURS)
    except Exception:
        return True


def build():
    os.makedirs(DATA_DIR, exist_ok=True)

    # Load reward items
    reward_path = os.path.join(DATA_DIR, "reward-items.json")
    if not os.path.exists(reward_path):
        print("ERROR: reward-items.json not found. Run build_relic_db.py first.")
        sys.exit(1)

    with open(reward_path, "r", encoding="utf-8-sig") as f:
        reward_items = json.load(f)
    print(f"Loaded {len(reward_items)} reward items")

    # Load existing prices（兼容加密：透传解密）
    prices_path = os.path.join(DATA_DIR, "prices.json")
    prices_data = {}
    if os.path.exists(prices_path):
        try:
            if os.environ.get("PRICE_DATA_SECRET"):
                import crypto_price as _cp
                v = _cp.load_json(prices_path)
                if v is not None:
                    prices_data = v
                    print(f"Loaded existing prices(解密) for {len(prices_data.get('items', {}))} items")
                else:
                    raise ValueError("decrypt load fail")
            else:
                with open(prices_path, "r", encoding="utf-8-sig") as f:
                    prices_data = json.load(f)
                print(f"Loaded existing prices for {len(prices_data.get('items', {}))} items")
        except Exception as e:
            # 回退明文
            try:
                with open(prices_path, "r", encoding="utf-8-sig") as f:
                    prices_data = json.load(f)
                print(f"Loaded existing prices(fallback) for {len(prices_data.get('items', {}))} items ({e})")
            except Exception:
                prices_data = {"generated": now_iso(), "items": {}}
    else:
        prices_data = {"generated": now_iso(), "items": {}}

    # Filter to items with valid urlName (tradable)
    tradable_items = [it for it in reward_items if it.get("urlName")]
    print(f"  {len(tradable_items)} tradable items to fetch prices for")

    if MAX_ITEMS_PER_RUN > 0:
        tradable_items = tradable_items[:MAX_ITEMS_PER_RUN]
        print(f"  Limited to {MAX_ITEMS_PER_RUN} items for this run")

    # Purge expired entries first
    items_data = prices_data.get("items", {})
    purged = 0
    for url_name, item_data in list(items_data.items()):
        history = item_data.get("history", [])
        valid_history = [h for h in history if not is_expired(h.get("time", ""))]
        if len(valid_history) != len(history):
            purged += len(history) - len(valid_history)
            item_data["history"] = valid_history
            if not valid_history:
                del items_data[url_name]
    if purged:
        print(f"  Purged {purged} expired price entries")

    # Fetch prices for each item
    new_snapshots = 0
    errors = 0
    skipped = 0
    categories = {}

    # Build WM-tags based classifier (fetch /v2/items once)
    print("Fetching WM items manifest for classification...")
    try:
        classify = build_wm_classifier()
        print("  Classifier ready (WM tags + set inheritance)")
    except Exception as e:
        print(f"  WARN: classifier fetch failed ({e}); falling back to keyword heuristic")
        classify = classify_item

    for i, item in enumerate(tradable_items):
        url_name = item["urlName"]
        item_name = item["name"]

        # Classify
        category = classify(url_name)
        categories[url_name] = category

        # Fetch top orders
        try:
            api_url = f"{WM_API}/v2/orders/item/{url_name}/top"
            data = fetch_json(api_url)
            sell_orders = data.get("data", {}).get("sell", []) if "data" in data else data.get("sell", [])
        except Exception as e:
            print(f"  [{i+1}/{len(tradable_items)}] ERROR {url_name}: {e}")
            errors += 1
            time.sleep(REQUEST_DELAY)
            continue

        price = compute_price_snapshot(sell_orders)
        if price is None:
            skipped += 1
            time.sleep(REQUEST_DELAY)
            continue

        # Store in history
        snapshot = {"price": price, "time": now_iso()}
        if url_name not in items_data:
            items_data[url_name] = {"name": item_name, "history": []}
        items_data[url_name]["name"] = item_name
        items_data[url_name]["history"].append(snapshot)
        new_snapshots += 1

        if (i + 1) % 20 == 0:
            print(f"  [{i+1}/{len(tradable_items)}] Fetched {new_snapshots} prices, {errors} errors, {skipped} skipped")

        time.sleep(REQUEST_DELAY)

    print(f"  Done: {new_snapshots} new price snapshots, {errors} errors, {skipped} skipped")

    # Compute averages
    for url_name, item_data in items_data.items():
        history = item_data.get("history", [])
        # Purge expired
        valid_history = [h for h in history if not is_expired(h.get("time", ""))]
        item_data["history"] = valid_history

        if valid_history:
            item_data["recent_avg"] = valid_history[-1]["price"]
            item_data["three_day_avg"] = round(
                sum(h["price"] for h in valid_history) / len(valid_history), 2
            )
            item_data["sample_count"] = len(valid_history)
        else:
            item_data.pop("recent_avg", None)
            item_data.pop("three_day_avg", None)
            item_data.pop("sample_count", None)

    # Remove items with no valid history
    items_data = {k: v for k, v in items_data.items() if v.get("history")}

    prices_data["generated"] = now_iso()
    prices_data["items"] = items_data

    # 写盘：若有 SECRET 则加密包装（仅公库暴露内容）
    if os.environ.get("PRICE_DATA_SECRET"):
        try:
            import crypto_price as _cp
            _cp.save_json_encrypt(prices_path, prices_data)
            print(f"  Wrote(加密) {prices_path} ({len(items_data)} items)")
        except Exception as e:
            print(f"  WARN 加密失败回退明文: {e}")
            with open(prices_path, "w", encoding="utf-8") as f:
                json.dump(prices_data, f, ensure_ascii=False, indent=2)
            print(f"  Wrote {prices_path} ({len(items_data)} items)")
    else:
        with open(prices_path, "w", encoding="utf-8") as f:
            json.dump(prices_data, f, ensure_ascii=False, indent=2)
        print(f"  Wrote {prices_path} ({len(items_data)} items)")

    # 摘要版（供 EdgeOne KV 存储，KV 单请求体有限制）：只保留页面实际用到的字段
    summary_items = {}
    for url_name, item_data in items_data.items():
        s = {"name": item_data.get("name") or url_name}
        if "recent_avg" in item_data:
            s["recent_avg"] = item_data["recent_avg"]
        if "three_day_avg" in item_data:
            s["three_day_avg"] = item_data["three_day_avg"]
        summary_items[url_name] = s
    summary = {"generated": prices_data["generated"], "items": summary_items}
    summary_path = os.path.join(DATA_DIR, "prices-summary.json")
    if os.environ.get("PRICE_DATA_SECRET"):
        try:
            import crypto_price as _cp
            _cp.save_json_encrypt(summary_path, summary)
            print(f"  Wrote(加密) {summary_path} ({len(summary_items)} items)")
        except Exception as e:
            print(f"  WARN 加密失败回退明文: {e}")
            with open(summary_path, "w", encoding="utf-8") as f:
                json.dump(summary, f, ensure_ascii=False, indent=2)
            print(f"  Wrote {summary_path} ({len(summary_items)} items)")
    else:
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        print(f"  Wrote {summary_path} ({len(summary_items)} items)")

    # Write categories（文字库扩面，同 SECRET）
    cat_path = os.path.join(DATA_DIR, "item-categories.json")
    if os.environ.get("PRICE_DATA_SECRET"):
        try:
            import crypto_price as _cp
            _cp.save_json_encrypt(cat_path, categories)
            print(f"  Wrote(加密) {cat_path}")
        except Exception as e:
            print(f"  WARN 加密失败回退明文: {e}")
            with open(cat_path, "w", encoding="utf-8") as f:
                json.dump(categories, f, ensure_ascii=False, indent=2)
            print(f"  Wrote {cat_path}")
    else:
        with open(cat_path, "w", encoding="utf-8") as f:
            json.dump(categories, f, ensure_ascii=False, indent=2)
        print(f"  Wrote {cat_path}")

    print("Done.")


if __name__ == "__main__":
    build()
