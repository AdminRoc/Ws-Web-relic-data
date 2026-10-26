#!/usr/bin/env python3
"""Create or verify the small manifest for one SHA-pinned relic data release."""
import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
MANIFEST = DATA / "release-manifest.json"


def expected_outputs():
    files = {}
    for path in sorted(DATA.rglob("*.json")):
        if path == MANIFEST:
            continue
        raw = path.read_bytes()
        json.loads(raw.decode("utf-8"))
        files[path.relative_to(DATA).as_posix()] = {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
        }
    if not files:
        raise ValueError("no relic data JSON files found")
    identity = json.dumps(files, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    release_id = hashlib.sha256(identity).hexdigest()
    doc = {
        "schema_version": 1,
        "repository": "AdminRoc/Ws-Web-relic-data",
        "release_id": release_id,
        "files": files,
    }
    payload = (json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    return doc, payload


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    doc, payload = expected_outputs()
    if args.verify:
        if not MANIFEST.is_file() or MANIFEST.read_bytes() != payload:
            raise SystemExit("RELIC RELEASE MANIFEST MISMATCH")
        print(f"RELIC RELEASE MANIFEST OK release_id={doc['release_id']} files={len(doc['files'])}")
        return
    MANIFEST.write_bytes(payload)
    print(f"Built relic release manifest release_id={doc['release_id']} files={len(doc['files'])}")


if __name__ == "__main__":
    main()
