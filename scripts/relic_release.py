"""Fail-closed identity and cross-artifact checks for the relic data release.

The single approved key correction below is specific to WFCD's old Axi Y2
marketInfo.urlName. It is not a rule that different relics or reward items are
interchangeable.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
from typing import Any, Callable


REFINEMENTS = {"Intact", "Exceptional", "Flawless", "Radiant"}
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


def migrate_approved_lifecycle_keys(relics: dict, full: dict, summary: dict) -> bool:
    """Re-key only the approved lifecycle row, preserving all row contents."""
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

        if _lifecycle_reward_signature(full_rows[new_key], new_key) != tuple(
            sorted((reward["name"], reward["rarity"], reward.get("urlName", ""))
                   for reward in canonical["rewards"])
        ):
            raise RuntimeError(f"Lifecycle reward identity mismatch after re-keying {new_key}")

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


def validate_data_dir(data_dir: Path, migrate: bool = False) -> int:
    relics_path = data_dir / "relics.json"
    full_path = data_dir / "relic-deep-date.json"
    summary_path = data_dir / "relic-deep-date-summary.json"
    relics = json.loads(relics_path.read_text(encoding="utf-8"))
    full = json.loads(full_path.read_text(encoding="utf-8"))
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    changed = migrate_approved_lifecycle_keys(relics, full, summary) if migrate else False
    count = validate_lifecycle_release(relics, full, summary)
    if changed:
        _atomic_write_json(full_path, full, indent=1)
        _atomic_write_json(summary_path, summary, indent=None)
        print("Applied the single approved Axi Y2 lifecycle key correction; history contents were preserved.")
    print(f"Validated {count} non-Requiem relic identities and lifecycle records.")
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--migrate-approved-key-renames",
        action="store_true",
        help="apply only the exact approved relic lifecycle key correction, then validate",
    )
    args = parser.parse_args()
    data_dir = Path(__file__).resolve().parent.parent / "data"
    validate_data_dir(data_dir, migrate=args.migrate_approved_key_renames)


if __name__ == "__main__":
    main()
