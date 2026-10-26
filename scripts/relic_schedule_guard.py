#!/usr/bin/env python3
"""Fail-closed deduplication guard for hourly relic-data runs."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("run timestamp has no timezone")
    return parsed.astimezone(timezone.utc)


def should_skip_fallback(runs: list[dict], now: datetime) -> bool:
    """Return true for a dispatch in this UTC hour or one still running."""
    if now.tzinfo is None:
        raise ValueError("current time has no timezone")

    now_utc = now.astimezone(timezone.utc)
    hour_start = now_utc.replace(minute=0, second=0, microsecond=0)

    for run in runs:
        if run.get("event") != "workflow_dispatch":
            continue

        created_at = _parse_utc(run["created_at"])
        status = run.get("status")

        # The producer explicitly checks out main, so a dispatch selected from
        # another branch can still publish main. Any active dispatch must not
        # overlap the scheduled fallback.
        if status == "in_progress":
            return True

        # Any dispatch created this UTC hour counts, including failed/cancelled
        # attempts: avoid a second price sample in the same hour.
        if created_at >= hour_start:
            return True

    return False


def should_skip_dispatch(
    runs: list[dict], now: datetime, current_run_id: str
) -> bool:
    """Avoid a duplicate dispatch after a same-hour schedule or dispatch run."""
    if now.tzinfo is None:
        raise ValueError("current time has no timezone")

    now_utc = now.astimezone(timezone.utc)
    hour_start = now_utc.replace(minute=0, second=0, microsecond=0)
    active_statuses = {"in_progress", "queued", "waiting", "requested"}

    for run in runs:
        if str(run.get("id", "")) == current_run_id:
            continue
        event = run.get("event")
        if event not in {"schedule", "workflow_dispatch"}:
            continue

        status = run.get("status")
        if status in active_statuses:
            return True

        created_at = _parse_utc(run["created_at"])
        if created_at < hour_start:
            continue

        if event == "workflow_dispatch":
            # Even a failed dispatch may have committed generated data before
            # publication failed; avoid another price sample in the same hour.
            return True
        if run.get("conclusion") == "success":
            # A successful same-hour schedule already produced and published data.
            return True

    return False


def _write_output(value: bool) -> None:
    output_path = os.environ["GITHUB_OUTPUT"]
    with open(output_path, "a", encoding="utf-8") as output:
        output.write(f"should_run={'true' if value else 'false'}\n")


def _workflow_runs_url(repository: str) -> str:
    # Fetch the newest mixed-event page, then filter schedule/dispatch runs
    # locally. A server-side event filter can omit a recent hourly peer run.
    query = urlencode({"per_page": "100"})
    return (
        "https://api.github.com/repos/"
        f"{quote(repository, safe='/')}/actions/workflows/update-data.yml/runs?{query}"
    )


def _list_workflow_runs(repository: str, token: str) -> list[dict]:
    url = _workflow_runs_url(repository)
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Ws-Web-relic-data-schedule-guard",
        },
    )
    with urlopen(request, timeout=20) as response:
        payload = json.load(response)
    runs = payload.get("workflow_runs") if isinstance(payload, dict) else None
    if not isinstance(runs, list):
        raise ValueError("GitHub Actions API returned an invalid workflow-run list")
    return runs


def _get_run_started_at(repository: str, token: str, run_id: str) -> datetime:
    url = (
        "https://api.github.com/repos/"
        f"{quote(repository, safe='/')}/actions/runs/{quote(run_id, safe='')}"
    )
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Ws-Web-relic-data-schedule-guard",
        },
    )
    with urlopen(request, timeout=20) as response:
        payload = json.load(response)
    return _parse_run_started_at(payload)


def _parse_run_started_at(payload: object) -> datetime:
    if not isinstance(payload, dict):
        raise ValueError("GitHub Actions API returned an invalid current run")
    timestamp = payload.get("run_started_at") or payload.get("created_at")
    if not isinstance(timestamp, str):
        raise ValueError("GitHub Actions API omitted the current run start time")
    return _parse_utc(timestamp)


def main() -> int:
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    if event not in {"schedule", "workflow_dispatch"}:
        _write_output(False)
        print(f"Unsupported trigger event {event!r}; skipping data publication.")
        return 0

    publish_only = (
        event == "workflow_dispatch"
        and os.environ.get("GITHUB_EVENT_INPUTS_PUBLISH_ONLY") == "true"
    )
    force_refresh = (
        event == "workflow_dispatch"
        and os.environ.get("GITHUB_EVENT_INPUTS_FORCE_REFRESH") == "true"
    )
    if publish_only:
        _write_output(True)
        print("Publish-only dispatch selected; validating and publishing current main data.")
        return 0
    if force_refresh:
        _write_output(True)
        print("Forced manual refresh selected; duplicate-run guard bypassed.")
        return 0

    try:
        repository = os.environ["GITHUB_REPOSITORY"]
        token = os.environ["GH_TOKEN"]
        run_id = os.environ["GITHUB_RUN_ID"]
        runs = _list_workflow_runs(repository, token)
        run_started_at = _get_run_started_at(repository, token, run_id)
        runner_clock = datetime.now(timezone.utc)
        hour_start = run_started_at.replace(minute=0, second=0, microsecond=0)
        peer_runs = [
            run
            for run in runs
            if str(run.get("id", "")) != run_id
            and run.get("event") in {"schedule", "workflow_dispatch"}
        ]
        same_hour_dispatches = [
            run
            for run in peer_runs
            if run.get("event") == "workflow_dispatch"
            and _parse_utc(run["created_at"]) >= hour_start
        ]
        same_hour_schedules = [
            run
            for run in peer_runs
            if run.get("event") == "schedule"
            and _parse_utc(run["created_at"]) >= hour_start
        ]
        active_dispatches = [
            run
            for run in peer_runs
            if run.get("event") == "workflow_dispatch"
            and run.get("status") in {"in_progress", "queued", "waiting", "requested"}
        ]
        newest_dispatch = max(
            (run for run in peer_runs if run.get("event") == "workflow_dispatch"),
            key=lambda run: _parse_utc(run["created_at"]),
            default=None,
        )
        newest_dispatch_at = (
            _parse_utc(newest_dispatch["created_at"]).isoformat()
            if newest_dispatch
            else "none"
        )
        skip = (
            should_skip_fallback(runs, run_started_at)
            if event == "schedule"
            else should_skip_dispatch(runs, run_started_at, run_id)
        )
        print(
            "Schedule guard evidence: "
            f"github_run_started_at={run_started_at.isoformat()} "
            f"runner_clock_utc={runner_clock.isoformat()} "
            f"peer_runs_seen={len(peer_runs)} "
            f"same_hour_dispatches={len(same_hour_dispatches)} "
            f"same_hour_schedules={len(same_hour_schedules)} "
            f"active_dispatches={len(active_dispatches)} "
            f"newest_dispatch_at={newest_dispatch_at}"
        )
        _write_output(not skip)
        if skip:
            if event == "schedule":
                print(
                    "Skipping scheduled fallback: a dispatch already exists "
                    "in the GitHub run-start UTC hour or is still in progress."
                )
            else:
                print(
                    "Skipping workflow_dispatch: a peer run already covers this "
                    "UTC hour or is still in progress."
                )
        else:
            print("No duplicate run found; data update will run.")
        return 0
    except Exception:
        # Do not log request details or headers. Missing/invalid API data must not
        # trigger a second price sample when dispatch status cannot be verified.
        try:
            _write_output(False)
        except Exception:
            pass
        print("Could not verify peer runs; data update skipped (fail-closed).", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
