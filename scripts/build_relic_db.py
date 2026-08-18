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

    print(f"  Grouped into {len(relics)} unique relics ({len(reward_set)} unique reward items)")

    # Write relics.json
    relics_path = os.path.join(DATA_DIR, "relics.json")
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
    build_names_zh(reward_set)

    # Summary
    tier_counts = {}
    for r in relics.values():
        tier_counts[r["tier"]] = tier_counts.get(r["tier"], 0) + 1
    print(f"  Tier distribution: {tier_counts}")
    print("Done.")


def build_names_zh(reward_set):
    """Fetch Ws-Web-assets's wm-items.json and extract zh/en names for reward slugs.

    Writes data/item-names-zh.json: { urlName: { zh, en } }
    """
    names = {}
    try:
        print("Fetching wm-items.json (EN->ZH names)...")
        last_err = None
        for url in NAMES_SOURCE_URLS:
            try:
                data = fetch_json(url)
                last_err = None
                break
            except Exception as e:
                last_err = e
                print(f"  Failed: {e}")
        if last_err:
            raise last_err

        all_map = {}
        for it in data.get("items", []):
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
    except Exception as e:
        print(f"  WARN: name fetch failed ({e}); writing empty name map")

    out = os.path.join(DATA_DIR, "item-names-zh.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(names, f, ensure_ascii=False, indent=2)
    print(f"  Wrote {out}")


if __name__ == "__main__":
    build()
