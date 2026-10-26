"""Decide whether a publish-only KV recovery needs a write."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path


def decide(http_status: str, current_path: str, expected_path: str) -> str:
    """Skip matching values, publish missing/mismatched values, fail closed on unknown status."""
    status = str(http_status).strip()
    if status == "404":
        return "publish"
    if status != "200":
        raise ValueError(f"cannot verify current KV value (HTTP {status or '000'})")

    current = Path(current_path).read_bytes()
    expected = Path(expected_path).read_bytes()
    if hashlib.sha256(current).digest() == hashlib.sha256(expected).digest():
        return "skip"
    return "publish"


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 3:
        print("usage: relic_kv_preflight.py HTTP_STATUS CURRENT_FILE EXPECTED_FILE", file=sys.stderr)
        return 2
    try:
        print(decide(*args))
    except (OSError, ValueError) as error:
        print(f"KV preflight failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
