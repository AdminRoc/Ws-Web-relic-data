#!/usr/bin/env python3
"""Validate local relic release artifacts without contacting KV or external APIs."""
import hashlib
import json
import argparse
from pathlib import Path


PUBLISHED = [
    "relics.json", "reward-items.json", "item-categories.json", "item-names-zh.json",
    "relic-deep-date.json", "prices.json", "prices-summary.json",
    "relic-deep-date-summary.json", "update-versions.json",
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default=Path(__file__).parents[1] / "data", type=Path)
    args = parser.parse_args()
    errors, manifest = [], []
    documents = {}
    for name in PUBLISHED:
        path = args.data_dir / name
        if not path.is_file():
            errors.append(f"missing {name}")
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            errors.append(f"invalid {name}: {error}")
            continue
        if isinstance(value, dict) and value.get("ct"):
            errors.append(f"encrypted wrapper {name}")
        documents[name] = value
        manifest.append({"file": name, "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    for marker in ("prices-summary.json", "relic-deep-date-summary.json"):
        value = documents.get(marker)
        if not isinstance(value, dict) or not value.get("generated") or not isinstance(value.get("items"), dict):
            errors.append(f"invalid ready marker {marker}")
    print(json.dumps({"artifacts": manifest, "errors": errors}, ensure_ascii=False, indent=2))
    raise SystemExit(bool(errors))


if __name__ == "__main__":
    main()
