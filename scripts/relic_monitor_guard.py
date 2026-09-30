#!/usr/bin/env python3
"""Run the lifecycle monitor only after the parent relic producer really ran."""

from __future__ import annotations

import json
import os
import re
import sys
from urllib.parse import quote
from urllib.request import Request, urlopen


def should_run(event_name: str, jobs: object = None) -> bool:
    """Non-workflow_run triggers run; chained runs require one successful producer job."""
    if event_name != "workflow_run":
        return True
    if not isinstance(jobs, list):
        raise ValueError("GitHub Actions API returned an invalid job list")

    producer_jobs = [
        job for job in jobs
        if isinstance(job, dict) and job.get("name") == "update-data"
    ]
    if len(producer_jobs) != 1:
        raise ValueError("parent workflow does not contain exactly one update-data job")
    return producer_jobs[0].get("conclusion") == "success"


def _get_parent_jobs(repository: str, token: str, run_id: str) -> list[dict]:
    if not re.fullmatch(r"[0-9]+", run_id):
        raise ValueError("parent workflow run ID is invalid")
    url = (
        "https://api.github.com/repos/"
        f"{quote(repository, safe='/')}/actions/runs/{quote(run_id, safe='')}/jobs?per_page=100"
    )
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Ws-Web-relic-data-monitor-guard",
        },
    )
    try:
        with urlopen(request, timeout=20) as response:
            payload = json.load(response)
    except Exception:
        # Do not emit request details or authorization headers into Actions logs.
        raise RuntimeError("could not read parent workflow jobs from GitHub Actions API") from None

    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(jobs, list):
        raise ValueError("GitHub Actions API returned an invalid job list")
    return jobs


def _write_output(value: bool) -> None:
    output_path = os.environ["GITHUB_OUTPUT"]
    with open(output_path, "a", encoding="utf-8") as output:
        output.write(f"should_run={'true' if value else 'false'}\n")


def main() -> int:
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    try:
        if event_name == "workflow_run":
            jobs = _get_parent_jobs(
                os.environ["GITHUB_REPOSITORY"],
                os.environ["GH_TOKEN"],
                os.environ["PARENT_RUN_ID"],
            )
            run_monitor = should_run(event_name, jobs)
        else:
            run_monitor = should_run(event_name)

        _write_output(run_monitor)
        if run_monitor:
            print("Lifecycle monitor allowed: parent update-data job succeeded or this is a direct trigger.")
        else:
            print("Lifecycle monitor skipped: parent update-data job did not succeed.")
        return 0
    except Exception:
        # Fail closed: do not rebuild or publish lifecycle state if the parent
        # producer outcome cannot be established from the read-only API.
        print("::error::Could not verify the parent producer job; refusing lifecycle rebuild.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
