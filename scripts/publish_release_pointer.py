#!/usr/bin/env python3
"""Verify pinned raw relic artifacts, then update only relic_release_meta."""
import base64
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import socket
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "AdminRoc/Ws-Web-relic-data"
ENDPOINT = "https://relic.wfspeed.run"
MANIFEST_PATH = "release-manifest.json"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(url, timeout=45, attempts=4, delay=2):
    request = urllib.request.Request(url, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            if error.code < 500 or attempt + 1 == attempts:
                raise
        except (urllib.error.URLError, TimeoutError, socket.timeout):
            if attempt + 1 == attempts:
                raise
        time.sleep(delay * (attempt + 1))
    raise RuntimeError("unreachable GET retry state")


def current_pointer():
    url = ENDPOINT + "/api/kv?key=relic_release_meta&v=" + str(time.time_ns())
    try:
        return json.loads(read(url, 15).decode("utf-8"))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise


def verify_pointer_after_write(expected, attempts=5, delay=2):
    for attempt in range(attempts):
        try:
            actual = current_pointer()
        except urllib.error.HTTPError as error:
            if error.code < 500 or attempt + 1 == attempts:
                raise
        except (urllib.error.URLError, TimeoutError, socket.timeout):
            if attempt + 1 == attempts:
                raise
        else:
            if actual == expected:
                return
            if actual and actual.get("snapshot"):
                snapshot = dict(actual["snapshot"])
                snapshot.pop("switched_at", None)
                if dict(actual, snapshot=snapshot) == expected:
                    return
        if attempt + 1 < attempts:
            time.sleep(delay * (attempt + 1))
    raise SystemExit("Relic release pointer readback mismatch after bounded verification")


CHUNK_BYTES = 512 * 1024
SLOT_CHUNKS = 64


def snapshot_url(commit, slot, start, count):
    return ENDPOINT + "/api/release-artifact?" + urllib.parse.urlencode(
        {"revision": commit, "slot": slot, "start": start, "count": count}
    )


def stage_snapshot(commit, previous, payloads, token):
    active = (previous or {}).get("snapshot") or {}
    slot = "b" if active.get("slot") == "a" else "a"
    files = {}
    chunks = []
    for path, raw in payloads.items():
        parts = [raw[offset:offset + CHUNK_BYTES] for offset in range(0, len(raw), CHUNK_BYTES)]
        if not 1 <= len(parts) <= 16:
            raise SystemExit("relic snapshot file exceeds its supported size: " + path)
        files[path] = {"start": len(chunks), "count": len(parts), "sha256": sha(raw)}
        chunks.extend(parts)
    if len(chunks) > SLOT_CHUNKS:
        raise SystemExit("relic snapshot exceeds the fixed two-slot KV budget")
    def stage_chunk(index, chunk):
        url = snapshot_url(commit, slot, index, 1)
        try:
            if read(url + "&v=" + str(time.time_ns()), attempts=4) == chunk:
                return
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
        request = urllib.request.Request(url, data=base64.b64encode(chunk), method="POST",
            headers={"Authorization": "Bearer " + token, "Content-Type": "text/plain"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    response.read()
                break
            except (urllib.error.URLError, TimeoutError) as error:
                code = getattr(error, "code", None)
                if code in (400, 401, 403, 409, 413):
                    raise
                print(f"Chunk {index} write response inconclusive ({code}); verifying before any retry", file=sys.stderr)
                time.sleep(5)
                try:
                    if read(url + "&v=" + str(time.time_ns()), attempts=4) == chunk:
                        break
                except urllib.error.HTTPError as read_error:
                    if read_error.code != 404:
                        raise
                if attempt == 2 or current_pointer() != previous:
                    raise SystemExit("KV chunk readback mismatch") from error
    for index, chunk in enumerate(chunks):
        stage_chunk(index, chunk)
    for path, raw in payloads.items():
        file = files[path]
        url = snapshot_url(commit, slot, file["start"], file["count"])
        for attempt in range(7):
            try:
                actual = read(url + "&v=" + str(time.time_ns()), attempts=1)
                if len(actual) == len(raw) and sha(actual) == file["sha256"]:
                    break
            except (urllib.error.URLError, TimeoutError):
                pass
            if attempt == 6:
                raise SystemExit("KV snapshot readback mismatch: " + path)
            time.sleep(11)
            if attempt in (2, 4):
                if current_pointer() != previous:
                    raise SystemExit("Active relic pointer changed during repair")
                for index in range(file["start"], file["start"] + file["count"]):
                    stage_chunk(index, chunks[index])
    return {"slot": slot, "files": files}


def main():
    token = os.environ.get("KV_WRITE_TOKEN", "")
    if not token:
        raise SystemExit("Missing KV_WRITE_TOKEN")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    remote = subprocess.check_output(["git", "ls-remote", "origin", "refs/heads/main"], cwd=ROOT, text=True).split()
    if len(commit) != 40 or not remote or remote[0].lower() != commit.lower():
        raise SystemExit("Relic candidate is not the current remote main")
    manifest_raw = (ROOT / "data" / MANIFEST_PATH).read_bytes()
    manifest = json.loads(manifest_raw.decode("utf-8"))
    if manifest.get("schema_version") != 1 or manifest.get("repository") != REPOSITORY or not manifest.get("files"):
        raise SystemExit("Invalid relic release manifest")
    base = f"https://raw.githubusercontent.com/{REPOSITORY}/{commit}/data/"
    if sha(read(base + MANIFEST_PATH)) != sha(manifest_raw):
        raise SystemExit("Pinned relic manifest differs from the pushed release")
    payloads = {MANIFEST_PATH: manifest_raw}
    for path, entry in manifest["files"].items():
        if not isinstance(path, str) or path.startswith("/") or ".." in path.split("/"):
            raise SystemExit("Unsafe path in relic release manifest")
        raw = read(base + urllib.parse.quote(path, safe="/._-"))
        if len(raw) != entry.get("bytes") or sha(raw) != entry.get("sha256"):
            raise SystemExit("Pinned relic raw hash mismatch: " + path)
        payloads[path] = raw

    generation = os.environ.get("GITHUB_RUN_ID", "")
    if not generation.isdigit():
        raise SystemExit("Missing numeric GitHub run id")
    pointer = {
        "schema_version": 1,
        "repository": REPOSITORY,
        "commit_sha": commit,
        "manifest_path": MANIFEST_PATH,
        "manifest_sha256": sha(manifest_raw),
        "manifest_bytes": len(manifest_raw),
        "data_revision": manifest["release_id"],
        "release_id": manifest["release_id"],
        "generation": generation,
        "published_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    old = current_pointer()
    if old:
        if old.get("repository") != REPOSITORY:
            raise SystemExit("Existing relic pointer belongs to another repository")
        old_generation = str(old.get("generation", ""))
        if old_generation.isdigit() and int(old_generation) > int(generation):
            raise SystemExit("Refusing to move the relic pointer backwards")
        pointer["previous_commit_sha"] = old.get("commit_sha")
    else:
        pointer["previous_commit_sha"] = None

    previous = old
    if previous and previous.get("snapshot") and previous.get("manifest_sha256") == pointer["manifest_sha256"]:
        print("Verified snapshot manifest is already current")
        return
    if previous and previous.get("snapshot"):
        elapsed = time.time() - previous["snapshot"]["switched_at"] / 1000
        if elapsed < 180:
            time.sleep(min(185, 182 - elapsed))
    if current_pointer() != previous:
        raise SystemExit("Active pointer changed before staging")
    pointer["snapshot"] = stage_snapshot(commit, previous, payloads, token)
    if current_pointer() != previous:
        raise SystemExit("Active pointer changed during staging")
    remote = subprocess.check_output(["git", "ls-remote", "origin", "refs/heads/main"], cwd=ROOT, text=True).split()
    if not remote or remote[0].lower() != commit.lower():
        raise SystemExit("Candidate is no longer the remote main")
    value = json.dumps(pointer, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    body = json.dumps({"key": "relic_release_meta", "value": value}, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(ENDPOINT + "/api/update-kv", data=body,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            response.read()
    except Exception as error:
        print("Pointer write response inconclusive; verify without replay: " + type(error).__name__, file=sys.stderr)
    verify_pointer_after_write(pointer)
    print(f"RELIC RELEASE POINTER OK commit={commit} files_checked={len(manifest['files'])}")


if __name__ == "__main__":
    main()
