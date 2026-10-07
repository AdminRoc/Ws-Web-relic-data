"""Fail-closed identity and cross-artifact checks for the relic data release.

The single approved key correction below is specific to WFCD's old Axi Y2
marketInfo.urlName. It is not a rule that different relics or reward items are
interchangeable.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
from pathlib import Path
from typing import Any, Callable


REFINEMENTS = {"Intact", "Exceptional", "Flawless", "Radiant"}
OFFICIAL_CHANCE_RARITY = {
    "Intact": {25.33: "Common", 11.0: "Uncommon", 2.0: "Rare"},
    "Exceptional": {23.33: "Common", 13.0: "Uncommon", 4.0: "Rare"},
    "Flawless": {20.0: "Common", 17.0: "Uncommon", 6.0: "Rare"},
    "Radiant": {16.67: "Common", 20.0: "Uncommon", 10.0: "Rare"},
}
EXPECTED_RARITIES = {"Common": 3, "Uncommon": 2, "Rare": 1}
APPROVED_KEY_RENAMES = {
    "axi_o7_relic": {
        "target": "axi_y2_relic",
        "name": "Axi Y2",
        "tier": "Axi",
    },
}


def _require_object(value: Any, label: str) -> dict:
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be a JSON object")
    return value


def _relic_reward_signature(record: dict, label: str) -> tuple:
    rewards = record.get("rewards")
    if not isinstance(rewards, list) or len(rewards) != 6:
        raise RuntimeError(f"{label} must contain exactly six rewards")
    signature = []
    slugs = set()
    for reward in rewards:
        if not isinstance(reward, dict):
            raise RuntimeError(f"{label} contains a malformed reward")
        name = reward.get("name")
        slug = reward.get("urlName")
        rarity = reward.get("rarity")
        chances = reward.get("chances")
        if not all(isinstance(value, str) and value for value in (name, slug, rarity)):
            raise RuntimeError(f"{label} contains an incomplete reward identity")
        if slug in slugs:
            raise RuntimeError(f"{label} contains duplicate reward slug {slug}")
        slugs.add(slug)
        if rarity not in {"Common", "Uncommon", "Rare"}:
            raise RuntimeError(f"{label} contains an invalid rarity for {slug}")
        if not isinstance(chances, dict) or set(chances) != REFINEMENTS:
            raise RuntimeError(f"{label} has incomplete refinement chances for {slug}")
        normalized_chances = []
        for refinement in sorted(REFINEMENTS):
            chance = chances[refinement]
            if isinstance(chance, bool) or not isinstance(chance, (int, float)) or not math.isfinite(chance):
                raise RuntimeError(f"{label} has an invalid chance for {slug} {refinement}")
            normalized_chances.append((refinement, float(chance)))
        signature.append((slug, name, rarity, tuple(normalized_chances)))
    return tuple(sorted(signature))


def validate_relic_key_continuity(previous: dict, current: dict) -> dict[str, str]:
    """Reject key loss except for one exact, semantically identical correction."""
    previous = _require_object(previous, "previous relic database")
    current = _require_object(current, "candidate relic database")

    for old_key, rule in APPROVED_KEY_RENAMES.items():
        new_key = rule["target"]
        if old_key in current:
            raise RuntimeError(
                f"Known stale relic key is still present: {old_key}; "
                f"expected canonical key {new_key}"
            )
        if new_key in current:
            canonical = current[new_key]
            if canonical.get("name") != rule["name"] or canonical.get("tier") != rule["tier"]:
                raise RuntimeError(f"Canonical relic identity mismatch for {new_key}")

    missing = set(previous) - set(current)
    added = set(current) - set(previous)
    if not missing:
        return {}

    for old_key, rule in APPROVED_KEY_RENAMES.items():
        new_key = rule["target"]
        if missing == {old_key} and added == {new_key}:
            old_record = _require_object(previous[old_key], f"previous {old_key}")
            new_record = _require_object(current[new_key], f"candidate {new_key}")
            for label, record in ((old_key, old_record), (new_key, new_record)):
                if record.get("name") != rule["name"] or record.get("tier") != rule["tier"]:
                    raise RuntimeError(f"Refusing key correction: {label} is not {rule['name']} ({rule['tier']})")
                if not isinstance(record.get("vaulted"), bool):
                    raise RuntimeError(f"Refusing key correction: {label} has no boolean vaulted state")
            if old_record["vaulted"] != new_record["vaulted"]:
                raise RuntimeError("Refusing key correction: vaulted state changed")
            if _relic_reward_signature(old_record, old_key) != _relic_reward_signature(new_record, new_key):
                raise RuntimeError("Refusing key correction: reward identities, rarities, or official chances changed")
            return {old_key: new_key}

    raise RuntimeError(
        "Relic key regression is not the exact approved one-key correction; "
        f"missing={sorted(missing)}, added={sorted(added)}"
    )


def resolve_local_relic_key(
    name: str,
    tier: str,
    local_relics: dict,
    key_builder: Callable[[str, str], str],
) -> str | None:
    """Resolve only a canonical key; refuse fuzzy same-name key substitutions."""
    canonical = key_builder(name, tier)
    record = local_relics.get(canonical)
    if record is not None:
        if record.get("name") != name or record.get("tier") != tier:
            raise RuntimeError(
                f"Canonical key collision for {name} ({tier}) at {canonical}: "
                f"stored as {record.get('name')} ({record.get('tier')})"
            )
        return canonical

    same_name = [
        (key, value) for key, value in local_relics.items()
        if isinstance(value, dict) and value.get("name") == name
    ]
    if same_name:
        detail = ", ".join(f"{key} ({value.get('tier')})" for key, value in same_name)
        raise RuntimeError(
            f"Non-canonical relic key for {name} ({tier}); refusing name-only match: {detail}"
        )
    return None


def _lifecycle_reward_signature(record: dict, label: str) -> tuple:
    rewards = record.get("rewards")
    if not isinstance(rewards, list):
        raise RuntimeError(f"{label} lifecycle rewards must be a list")
    signature = []
    for reward in rewards:
        if not isinstance(reward, dict):
            raise RuntimeError(f"{label} has a malformed lifecycle reward")
        name = reward.get("name")
        rarity = reward.get("rarity")
        slug = reward.get("urlName", "")
        if not isinstance(name, str) or not isinstance(rarity, str) or not isinstance(slug, str):
            raise RuntimeError(f"{label} has an incomplete lifecycle reward")
        signature.append((name, rarity, slug))
    return tuple(sorted(signature))


def _validate_official_rewards(record: dict, label: str) -> tuple:
    """Validate the exact six-row reward shape before syncing lifecycle snapshots."""
    rewards = record.get("rewards")
    if not isinstance(rewards, list) or len(rewards) != 6:
        raise RuntimeError(f"{label} must contain exactly six official rewards")

    signature = []
    identities = set()
    rarity_counts = {rarity: 0 for rarity in EXPECTED_RARITIES}
    totals = {refinement: 0.0 for refinement in REFINEMENTS}
    for reward in rewards:
        if not isinstance(reward, dict):
            raise RuntimeError(f"{label} contains a malformed official reward")
        name = reward.get("name")
        slug = reward.get("urlName")
        rarity = reward.get("rarity")
        chances = reward.get("chances")
        if not isinstance(name, str) or not name.strip() \
           or not isinstance(slug, str) \
           or rarity not in EXPECTED_RARITIES:
            raise RuntimeError(f"{label} contains an incomplete official reward identity")
        identity = (name, rarity, slug)
        if identity in identities:
            raise RuntimeError(f"{label} contains a duplicate official reward identity")
        identities.add(identity)
        rarity_counts[rarity] += 1

        if not isinstance(chances, dict) or set(chances) != REFINEMENTS:
            raise RuntimeError(f"{label} has incomplete official refinement chances")
        for refinement in REFINEMENTS:
            chance = chances[refinement]
            if isinstance(chance, bool) or not isinstance(chance, (int, float)) \
               or not math.isfinite(chance):
                raise RuntimeError(f"{label} has an invalid official chance for {name} {refinement}")
            normalized = round(float(chance), 2)
            if OFFICIAL_CHANCE_RARITY[refinement].get(normalized) != rarity:
                raise RuntimeError(
                    f"{label} official relic chance/rarity mismatch: {name} {refinement} {normalized}"
                )
            totals[refinement] += float(chance)
        signature.append(identity)

    if rarity_counts != EXPECTED_RARITIES:
        raise RuntimeError(f"{label} must contain three Common, two Uncommon, and one Rare reward")
    for refinement, total in totals.items():
        if abs(total - 100.0) > 0.05:
            raise RuntimeError(f"{label} official {refinement} chances total {total}, expected 100")
    return tuple(sorted(signature))


def refresh_official_reward_snapshots(relics: dict, full: dict) -> list[str]:
    """Refresh only stale full-history reward snapshots from validated official rows."""
    relics = _require_object(relics, "relics")
    full = _require_object(full, "deep-date")
    full_rows = _require_object(full.get("relics"), "deep-date.relics")
    expected = {key for key, value in relics.items() if value.get("tier") != "Requiem"}
    if set(full_rows) != expected:
        missing = sorted(expected - set(full_rows))
        extra = sorted(set(full_rows) - expected)
        raise RuntimeError(
            "Cannot refresh official rewards with incomplete lifecycle keys; "
            f"missing={missing}, extra={extra}"
        )

    updated = []
    for key in sorted(expected):
        canonical = _require_object(relics[key], f"relics.{key}")
        row = _require_object(full_rows[key], f"deep-date.relics.{key}")
        if row.get("name") != canonical.get("name") or row.get("tier") != canonical.get("tier"):
            raise RuntimeError(f"Lifecycle identity mismatch during official reward refresh: {key}")

        official_signature = _validate_official_rewards(canonical, key)
        previous_rewards = row.get("rewards")
        previous_signature = _lifecycle_reward_signature(row, key)
        if len(previous_rewards) != 6:
            raise RuntimeError(f"{key} lifecycle snapshot must contain exactly six rewards")
        previous_by_identity = {}
        previous_rarity_counts = {rarity: 0 for rarity in EXPECTED_RARITIES}
        for reward in previous_rewards:
            if not isinstance(reward, dict):
                raise RuntimeError(f"{key} has a malformed lifecycle reward")
            if reward["rarity"] not in EXPECTED_RARITIES or not reward["name"].strip():
                raise RuntimeError(f"{key} has an invalid lifecycle reward identity")
            zh = reward.get("zh")
            if zh is not None and not isinstance(zh, str):
                raise RuntimeError(f"{key} has an invalid lifecycle reward translation")
            identity = (reward["name"], reward["rarity"], reward.get("urlName", ""))
            if identity in previous_by_identity:
                raise RuntimeError(f"{key} contains a duplicate lifecycle reward identity")
            previous_by_identity[identity] = zh
            previous_rarity_counts[reward["rarity"]] += 1
        if previous_rarity_counts != EXPECTED_RARITIES:
            raise RuntimeError(f"{key} lifecycle snapshot has an invalid 3/2/1 rarity distribution")

        if previous_signature == official_signature:
            continue

        row["rewards"] = [
            {
                "name": reward["name"],
                "rarity": reward["rarity"],
                "urlName": reward["urlName"],
                "zh": previous_by_identity.get(
                    (reward["name"], reward["rarity"], reward["urlName"])
                ),
            }
            for reward in canonical["rewards"]
        ]
        updated.append(key)

    return updated


def _rekey_mapping(container: dict, old_key: str, new_key: str, label: str, rule: dict) -> bool:
    old_present = old_key in container
    new_present = new_key in container
    if old_present == new_present:
        state = "both" if old_present else "neither"
        raise RuntimeError(f"{label} must contain exactly one of {old_key}/{new_key}; found {state}")
    key = old_key if old_present else new_key
    record = _require_object(container[key], f"{label}.{key}")
    if record.get("name") != rule["name"] or record.get("tier") != rule["tier"]:
        raise RuntimeError(f"{label}.{key} is not the approved {rule['name']} ({rule['tier']}) record")
    if not old_present:
        return False
    updated = {new_key if key == old_key else key: value for key, value in container.items()}
    container.clear()
    container.update(updated)
    return True


def _rekey_varzia_list(document: dict, old_key: str, new_key: str, label: str) -> bool:
    values = document.get("varziaRelics")
    if not isinstance(values, list) or len(values) != len(set(values)):
        raise RuntimeError(f"{label}.varziaRelics must be a unique list")
    if old_key in values and new_key in values:
        raise RuntimeError(f"{label}.varziaRelics contains both old and new identity keys")
    if old_key not in values:
        return False
    document["varziaRelics"] = [new_key if key == old_key else key for key in values]
    return True


def migrate_approved_lifecycle_keys(
    relics: dict,
    full: dict,
    summary: dict,
    refresh_official_rewards: bool = False,
) -> bool:
    """Apply the approved key correction and optional official reward snapshot refresh."""
    changed = False
    full_rows = _require_object(full.get("relics"), "deep-date.relics")
    summary_rows = _require_object(summary.get("items"), "deep-date-summary.items")

    for old_key, rule in APPROVED_KEY_RENAMES.items():
        new_key = rule["target"]
        if old_key in relics or new_key not in relics:
            raise RuntimeError(f"Relic definitions are not at the canonical key state for {old_key} -> {new_key}")
        canonical = _require_object(relics[new_key], f"relics.{new_key}")
        if canonical.get("name") != rule["name"] or canonical.get("tier") != rule["tier"]:
            raise RuntimeError(f"Canonical relic identity mismatch for {new_key}")

        changed |= _rekey_mapping(full_rows, old_key, new_key, "deep-date.relics", rule)
        changed |= _rekey_mapping(summary_rows, old_key, new_key, "deep-date-summary.items", rule)
        changed |= _rekey_varzia_list(full, old_key, new_key, "deep-date")
        changed |= _rekey_varzia_list(summary, old_key, new_key, "deep-date-summary")

        if not refresh_official_rewards:
            lifecycle_signature = _lifecycle_reward_signature(full_rows[new_key], new_key)
            canonical_signature = tuple(
                sorted((reward["name"], reward["rarity"], reward.get("urlName", ""))
                       for reward in canonical["rewards"])
            )
            if lifecycle_signature != canonical_signature:
                raise RuntimeError(f"Lifecycle reward identity mismatch after re-keying {new_key}")

    if refresh_official_rewards:
        updated = refresh_official_reward_snapshots(relics, full)
        if updated:
            changed = True
            print(
                "Refreshed official reward snapshots while preserving lifecycle history: "
                + ", ".join(updated)
            )
    validate_lifecycle_release(relics, full, summary)
    return changed


def validate_lifecycle_release(relics: dict, full: dict, summary: dict) -> int:
    """Validate complete non-Requiem lifecycle coverage and reward consistency."""
    relics = _require_object(relics, "relics")
    full = _require_object(full, "deep-date")
    summary = _require_object(summary, "deep-date-summary")
    full_rows = _require_object(full.get("relics"), "deep-date.relics")
    summary_rows = _require_object(summary.get("items"), "deep-date-summary.items")
    expected = {key for key, value in relics.items() if value.get("tier") != "Requiem"}
    if set(full_rows) != expected or set(summary_rows) != expected:
        raise RuntimeError(
            "Lifecycle keys do not match non-Requiem relics; "
            f"full_missing={sorted(expected - set(full_rows))}, "
            f"full_extra={sorted(set(full_rows) - expected)}, "
            f"summary_missing={sorted(expected - set(summary_rows))}, "
            f"summary_extra={sorted(set(summary_rows) - expected)}"
        )
    if full.get("generated") != summary.get("generated"):
        raise RuntimeError("Lifecycle full/summary generations differ")

    full_varzia = full.get("varziaRelics")
    summary_varzia = summary.get("varziaRelics")
    if not isinstance(full_varzia, list) or not isinstance(summary_varzia, list):
        raise RuntimeError("Lifecycle varziaRelics fields must be lists")
    if len(full_varzia) != len(set(full_varzia)) or len(summary_varzia) != len(set(summary_varzia)):
        raise RuntimeError("Lifecycle varziaRelics contains duplicate keys")
    if set(full_varzia) != set(summary_varzia) or not set(full_varzia).issubset(expected):
        raise RuntimeError("Lifecycle full/summary Varzia relic identities differ")
    varzia_keys = set(full_varzia)

    for key in expected:
        canonical = _require_object(relics[key], f"relics.{key}")
        row = _require_object(full_rows[key], f"deep-date.relics.{key}")
        item = _require_object(summary_rows[key], f"deep-date-summary.items.{key}")
        for label, record in (("deep-date", row), ("deep-date-summary", item)):
            if record.get("name") != canonical.get("name") or record.get("tier") != canonical.get("tier"):
                raise RuntimeError(f"Lifecycle identity mismatch: {label}.{key}")
        if _lifecycle_reward_signature(row, key) != tuple(
            sorted((reward["name"], reward["rarity"], reward.get("urlName", ""))
                   for reward in canonical.get("rewards", []))
        ):
            raise RuntimeError(f"Lifecycle canonical reward mismatch: {key}")

        events = row.get("events")
        if not isinstance(events, list):
            raise RuntimeError(f"Lifecycle events must be a list: {key}")
        for event in events:
            if not isinstance(event, dict):
                raise RuntimeError(f"Malformed lifecycle event: {key}")
            timestamp = event.get("ts")
            if timestamp:
                from datetime import datetime, timedelta
                utc = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                beijing = (utc + timedelta(hours=8)).date().isoformat()
                if event.get("gameDateTZ") != "UTC+8" or event.get("gameDate") != beijing:
                    raise RuntimeError(f"Lifecycle timestamp/date mismatch: {key}")

        released = next((event for event in events if event.get("type") == "released"), None)
        vaults = [event for event in events if event.get("type") == "vaulted"]
        last = events[-1] if events else None
        summary_expected = {
            "status": row.get("status"),
            "released": released.get("gameDate") if released else None,
            "releasedTZ": released.get("gameDateTZ") if released else None,
            "vaultedAt": vaults[-1].get("gameDate") if vaults else None,
            "vaultedTZ": vaults[-1].get("gameDateTZ") if vaults else None,
            "lastChange": last.get("gameDate") if last else None,
            "varzia": row.get("varzia"),
            "varziaSet": row.get("varziaSet"),
        }
        for field, value in summary_expected.items():
            if item.get(field) != value:
                raise RuntimeError(f"Lifecycle summary mismatch: {key}.{field}")
        if bool(row.get("varzia")) != (key in varzia_keys):
            raise RuntimeError(f"Lifecycle Varzia flag mismatch: {key}")

    return len(expected)


def _atomic_write_json(path: Path, value: dict, indent: int | None) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=indent)
    os.replace(temporary, path)


def validate_data_dir(
    data_dir: Path,
    migrate: bool = False,
    refresh_official_rewards: bool = False,
) -> int:
    relics_path = data_dir / "relics.json"
    full_path = data_dir / "relic-deep-date.json"
    summary_path = data_dir / "relic-deep-date-summary.json"
    relics = json.loads(relics_path.read_text(encoding="utf-8"))
    full = json.loads(full_path.read_text(encoding="utf-8"))
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    original_full = copy.deepcopy(full)
    original_summary = copy.deepcopy(summary)
    if migrate:
        migrate_approved_lifecycle_keys(
            relics,
            full,
            summary,
            refresh_official_rewards=refresh_official_rewards,
        )
    elif refresh_official_rewards:
        updated = refresh_official_reward_snapshots(relics, full)
        if updated:
            print(
                "Refreshed official reward snapshots while preserving lifecycle history: "
                + ", ".join(updated)
            )
    count = validate_lifecycle_release(relics, full, summary)
    if full != original_full:
        _atomic_write_json(full_path, full, indent=1)
    if summary != original_summary:
        _atomic_write_json(summary_path, summary, indent=None)
    print(f"Validated {count} non-Requiem relic identities and lifecycle records.")
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--migrate-approved-key-renames",
        action="store_true",
        help="apply the exact approved relic lifecycle key correction, then validate",
    )
    parser.add_argument(
        "--refresh-official-reward-snapshots",
        action="store_true",
        help="refresh only reward snapshots from validated official relic tables, preserving lifecycle history",
    )
    args = parser.parse_args()
    data_dir = Path(__file__).resolve().parent.parent / "data"
    validate_data_dir(
        data_dir,
        migrate=args.migrate_approved_key_renames,
        refresh_official_rewards=args.refresh_official_reward_snapshots,
    )


if __name__ == "__main__":
    main()
