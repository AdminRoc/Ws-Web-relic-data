#!/usr/bin/env python3
"""Build Database A: relic drop table from warframe-items Relics.json.

Fetches Relics.json from WFCD/warframe-items via jsDelivr CDN,
groups relics by base name (merging 4 refinement levels),
extracts unique reward items with their warframe.market urlNames.

Outputs:
  data/relics.json       — { relic_url_name: { name, tier, vaulted, rewards[] } }
  data/reward-items.json — [ { name, urlName, rarity } ] unique reward items
"""

import json
import os
import re
import sys
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(REPO_ROOT, "data")

RELIC_SOURCE_URLS = [
    "https://raw.githubusercontent.com/WFCD/warframe-items/master/data/json/Relics.json",
    "https://cdn.jsdelivr.net/gh/WFCD/warframe-items@master/data/json/Relics.json",
]
NAMES_SOURCE_URLS = [
    "https://raw.githubusercontent.com/AdminRoc/Ws-Web-assets/main/data/item/wm-items.json",
    "https://cdn.jsdelivr.net/gh/AdminRoc/Ws-Web-assets@main/data/item/wm-items.json",
]
DROPS_SOURCE_URLS = [
    "https://raw.githubusercontent.com/AdminRoc/Ws-Web-assets/main/data/item/drops-index.json",
    "https://cdn.jsdelivr.net/gh/AdminRoc/Ws-Web-assets@main/data/item/drops-index.json",
]
# Local fallback for development
LOCAL_RELIC_PATH = os.path.join(
    os.path.dirname(REPO_ROOT), "How-To-Design-The-UI", "warframe-items", "data", "json", "Relics.json"
)


def fetch_json(url=None):
    if url is None:
        # Try local file first
        if os.path.exists(LOCAL_RELIC_PATH):
            print(f"  Using local file: {LOCAL_RELIC_PATH}")
            with open(LOCAL_RELIC_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        # Try URLs
        for u in RELIC_SOURCE_URLS:
            try:
                print(f"  Fetching: {u}")
                req = urllib.request.Request(u, headers={"User-Agent": "Ws-Web-relic/1.0"})
                with urllib.request.urlopen(req, timeout=60) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except Exception as e:
                print(f"  Failed: {e}")
        raise RuntimeError("Could not load Relics.json from any source")
    req = urllib.request.Request(url, headers={"User-Agent": "Ws-Web-relic/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def base_relic_name(name):
    """Extract base relic name, e.g. 'Axi A1 Intact' -> 'Axi A1'"""
    for suffix in (" Intact", " Exceptional", " Flawless", " Radiant"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def relic_tier(name):
    """Extract relic tier: Lith, Meso, Neo, Axi, Requiem"""
    for tier in ("Lith", "Meso", "Neo", "Axi", "Requiem", "Vanguard"):
        if name.startswith(tier):
            return tier
    return "Other"


def fetch_asset(urls, item_type, minimum):
    """Require a complete shared artifact before replacing published relic data."""
    for url in urls:
        try:
            data = fetch_json(url)
            if isinstance(data, dict) and isinstance(data.get("items"), item_type) and len(data["items"]) >= minimum:
                return data["items"]
            print(f"  Incomplete artifact: {url}")
        except Exception as exc:
            print(f"  Failed: {url}: {exc}")
    raise RuntimeError("Could not load complete shared item artifacts")


def add_missing_official_relics(relics, reward_set, drops, market_items):
    """Fill the lag between official drop-table updates and WFCD Relics.json."""
    market_slugs = {
        item["en"].casefold(): item["slug"]
        for item in market_items if item.get("en") and item.get("slug")
    }
    known_names = {relic["name"] for relic in relics.values()}
    missing = {}
    source_pattern = re.compile(
        r"^((?:Lith|Meso|Neo|Axi|Requiem|Vanguard) [A-Za-z0-9]+) Relic "
        r"\((Intact|Exceptional|Flawless|Radiant)\)$"
    )
    for item_name, item in drops.items():
        for source in item.get("sources", []):
            if source.get("section") != "Relics":
                continue
            match = source_pattern.match(source.get("source", ""))
            if not match or match.group(1) in known_names:
                continue
            base, refinement = match.groups()
            slug = market_slugs.get((base + " Relic").casefold())
            if not slug:
                slug = base.lower().replace(" ", "_") + "_relic"
            if slug in relics:
                continue
            info = missing.setdefault(slug, {
                "name": base, "tier": relic_tier(base), "vaulted": False, "rewards": {}
            })
            reward = info["rewards"].setdefault(item_name, {
                "name": item_name,
                "urlName": market_slugs.get(item_name.casefold(), ""),
                "rarity": "",
                "chances": {},
            })
            reward["chances"][refinement] = source.get("chance", 0)
            if refinement == "Intact":
                chance = source.get("chance", 0)
                reward["rarity"] = "Rare" if chance <= 5 else "Uncommon" if chance <= 15 else "Common"

    for slug, info in missing.items():
        rewards = list(info.pop("rewards").values())
        if len(rewards) < 4 or any("Intact" not in reward["chances"] for reward in rewards):
            raise RuntimeError(f"Incomplete official reward table for {info['name']}: {len(rewards)} rewards")
        info["rewards"] = rewards
        relics[slug] = info
        known_names.add(info["name"])
        for reward in rewards:
            item_slug = reward["urlName"]
            if not item_slug:
                continue
            previous = reward_set.get(item_slug)
            if previous is None:
                reward_set[item_slug] = {
                    "name": reward["name"], "urlName": item_slug,
                    "highestRarity": reward["rarity"],
                }
            elif reward["rarity"] == "Rare":
                previous["highestRarity"] = "Rare"
    print(f"  Added {len(missing)} relics from the official drop-table index")


def build():
    os.makedirs(DATA_DIR, exist_ok=True)

    print("Loading Relics.json...")
    all_relics = fetch_json()
    print(f"  Loaded {len(all_relics)} relic entries (including refinement variants)")

    # Group by base relic name
    relics = {}
    reward_set = {}  # urlName -> { name, rarity, relic_count }

    for entry in all_relics:
        base = base_relic_name(entry["name"])
        tier = relic_tier(base)
        url_name = entry.get("marketInfo", {}).get("urlName", "")
        if not url_name:
            continue

        if url_name not in relics:
            relics[url_name] = {
                "name": base,
                "tier": tier,
                "vaulted": entry.get("vaulted", False),
                "rewards": [],
            }

        # Merge rewards (use Intact chances as reference)
        refinement = entry["name"].split()[-1] if " " in entry["name"] else ""
        for reward in entry.get("rewards", []):
            item = reward.get("item", {})
            item_name = item.get("name", "")
            wm = item.get("warframeMarket") or {}
            item_url = wm.get("urlName", "")
            rarity = reward.get("rarity", "")
            chance = reward.get("chance", 0)

            if not item_name:
                continue

            # Track unique reward items
            if item_url and item_url not in reward_set:
                reward_set[item_url] = {
                    "name": item_name,
                    "urlName": item_url,
                    "highestRarity": rarity,
                }
            elif item_url and reward_set[item_url]["highestRarity"] == "Uncommon" and rarity == "Rare":
                reward_set[item_url]["highestRarity"] = "Rare"

            # Check if reward already in this relic
            existing = next(
                (r for r in relics[url_name]["rewards"] if r.get("urlName") == item_url),
                None,
            )
            if existing:
                existing["chances"][refinement] = chance
            else:
                relics[url_name]["rewards"].append({
                    "name": item_name,
                    "urlName": item_url,
                    "rarity": rarity,
                    "chances": {refinement: chance} if refinement else {},
                })

    print("Loading shared market names and official drop-table index...")
    market_items = fetch_asset(NAMES_SOURCE_URLS, list, 1500)
    drops = fetch_asset(DROPS_SOURCE_URLS, dict, 2000)
    add_missing_official_relics(relics, reward_set, drops, market_items)
    print(f"  Grouped into {len(relics)} unique relics ({len(reward_set)} unique reward items)")

    relics_path = os.path.join(DATA_DIR, "relics.json")
    if os.path.exists(relics_path):
        with open(relics_path, "r", encoding="utf-8") as f:
            previous_relics = json.load(f)
        if len(relics) < len(previous_relics):
            raise RuntimeError("Relic count regressed; keeping the previously published data")

    # Write relics.json
    with open(relics_path, "w", encoding="utf-8") as f:
        json.dump(relics, f, ensure_ascii=False, indent=2)
    print(f"  Wrote {relics_path}")

    # Write reward-items.json
    reward_items = sorted(reward_set.values(), key=lambda x: x["name"])
    reward_items_path = os.path.join(DATA_DIR, "reward-items.json")
    with open(reward_items_path, "w", encoding="utf-8") as f:
        json.dump(reward_items, f, ensure_ascii=False, indent=2)
    print(f"  Wrote {reward_items_path}")

    # Write item-names-zh.json (EN -> ZH mapping from Ws-Web-assets wm-items.json)
    build_names_zh(reward_set, market_items)

    # Summary
    tier_counts = {}
    for r in relics.values():
        tier_counts[r["tier"]] = tier_counts.get(r["tier"], 0) + 1
    print(f"  Tier distribution: {tier_counts}")
    print("Done.")


def build_names_zh(reward_set, market_items):
    """Fetch Ws-Web-assets's wm-items.json and extract zh/en names for reward slugs.

    Writes data/item-names-zh.json: { urlName: { zh, en } }
    """
    names = {}
    all_map = {}
    for it in market_items:
        slug = it.get("slug")
        if slug and (it.get("zh") or it.get("en")):
            all_map[slug] = {
                "zh": it.get("zh") or "",
                "en": it.get("en") or "",
            }
    for r in reward_set.values():
        u = r.get("urlName")
        if u and u in all_map:
            names[u] = all_map[u]
    print(f"  Matched {len(names)}/{len(reward_set)} names")

    out = os.path.join(DATA_DIR, "item-names-zh.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(names, f, ensure_ascii=False, indent=2)
    print(f"  Wrote {out}")


if __name__ == "__main__":
    build()
