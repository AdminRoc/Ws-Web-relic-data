import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from scripts import relic_schedule_guard
from scripts.relic_schedule_guard import (
    _parse_run_started_at,
    _workflow_runs_url,
    should_skip_fallback,
)


NOW = datetime(2026, 9, 29, 21, 5, tzinfo=timezone.utc)


def run(created_at, *, status="completed", event="workflow_dispatch", branch="main"):
    return {
        "id": "peer",
        "created_at": created_at,
        "status": status,
        "event": event,
        "head_branch": branch,
        "conclusion": "success" if status == "completed" else None,
    }


class RelicScheduleGuardTests(unittest.TestCase):
    def _invoke_guard(self, *, event, runs, started=NOW, extra_env=None):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "github-output.txt"
            env = {
                "GITHUB_EVENT_NAME": event,
                "GITHUB_EVENT_INPUTS_FORCE_REFRESH": "false",
                "GITHUB_EVENT_INPUTS_PUBLISH_ONLY": "false",
                "GH_TOKEN": "test-only-placeholder",
                "GITHUB_REPOSITORY": "AdminRoc/Ws-Web-relic-data",
                "GITHUB_RUN_ID": "current",
                "GITHUB_OUTPUT": str(output_path),
            }
            env.update(extra_env or {})
            with patch.dict(os.environ, env, clear=False):
                with patch.object(
                    relic_schedule_guard, "_list_workflow_runs", return_value=runs
                ):
                    with patch.object(relic_schedule_guard, "_get_run_started_at", return_value=started):
                        self.assertEqual(relic_schedule_guard.main(), 0)
            return output_path.read_text(encoding="utf-8")

    def test_workflow_runs_query_fetches_latest_mixed_events_for_local_filter(self):
        url = _workflow_runs_url("AdminRoc/Ws-Web-relic-data")
        parsed = urlparse(url)

        self.assertEqual(
            parsed.path,
            "/repos/AdminRoc/Ws-Web-relic-data/actions/workflows/update-data.yml/runs",
        )
        self.assertEqual(parse_qs(parsed.query), {"per_page": ["100"]})

    def test_previous_hour_success_does_not_suppress_fallback(self):
        self.assertFalse(should_skip_fallback([run("2026-09-29T20:00:46Z")], NOW))

    def test_current_hour_dispatch_suppresses_fallback(self):
        self.assertTrue(should_skip_fallback([run("2026-09-29T21:01:01Z")], NOW))

    def test_late_fallback_dispatch_still_suppresses_same_hour(self):
        late_hour = datetime(2026, 9, 29, 22, 55, tzinfo=timezone.utc)
        self.assertTrue(
            should_skip_fallback([run("2026-09-29T22:00:49Z")], late_hour)
        )

    def test_current_run_timestamp_parser_uses_github_run_started_at(self):
        started = _parse_run_started_at({"run_started_at": "2026-09-29T22:55:33Z"})
        self.assertEqual(started, datetime(2026, 9, 29, 22, 55, 33, tzinfo=timezone.utc))

    def test_current_run_timestamp_parser_falls_back_to_created_at(self):
        started = _parse_run_started_at({"created_at": "2026-09-29T22:55:33Z"})
        self.assertEqual(started, datetime(2026, 9, 29, 22, 55, 33, tzinfo=timezone.utc))

    def test_current_hour_failed_dispatch_still_suppresses_duplicate_sample(self):
        failed = run("2026-09-29T21:02:00Z", status="completed")
        failed["conclusion"] = "failure"
        self.assertTrue(should_skip_fallback([failed], NOW))

    def test_in_progress_main_dispatch_suppresses_fallback(self):
        active = run("2026-09-29T20:00:46Z", status="in_progress")
        self.assertTrue(should_skip_fallback([active], NOW))

    def test_old_orphaned_queued_dispatch_does_not_suppress_fallback(self):
        orphan = run("2026-08-19T12:00:00Z", status="queued")
        self.assertFalse(should_skip_fallback([orphan], NOW))

    def test_dispatch_on_another_branch_suppresses_fallback_because_producer_checks_out_main(self):
        other_branch = run("2026-09-29T21:01:01Z", branch="work-in-progress")
        self.assertTrue(should_skip_fallback([other_branch], NOW))

    def test_non_dispatch_run_does_not_suppress_fallback(self):
        scheduled = run("2026-09-29T21:01:01Z", event="schedule")
        self.assertFalse(should_skip_fallback([scheduled], NOW))

    def test_late_dispatch_skips_when_same_hour_schedule_already_succeeded(self):
        scheduled = run("2026-09-29T21:00:00Z", event="schedule")
        output = self._invoke_guard(event="workflow_dispatch", runs=[scheduled])
        self.assertIn("should_run=false", output)

    def test_dispatch_runs_after_same_hour_schedule_failure(self):
        scheduled = run("2026-09-29T21:00:00Z", event="schedule")
        scheduled["conclusion"] = "failure"
        output = self._invoke_guard(event="workflow_dispatch", runs=[scheduled])
        self.assertIn("should_run=true", output)

    def test_dispatch_from_previous_hour_does_not_suppress_fallback(self):
        scheduled = run("2026-09-29T20:59:59Z", event="schedule")
        output = self._invoke_guard(event="workflow_dispatch", runs=[scheduled])
        self.assertIn("should_run=true", output)

    def test_forced_manual_refresh_can_override_same_hour_schedule(self):
        scheduled = run("2026-09-29T21:00:00Z", event="schedule")
        output = self._invoke_guard(
            event="workflow_dispatch",
            runs=[scheduled],
            extra_env={"GITHUB_EVENT_INPUTS_FORCE_REFRESH": "true"},
        )
        self.assertIn("should_run=true", output)

    def test_publish_only_republishes_current_data_despite_same_hour_run(self):
        scheduled = run("2026-09-29T21:00:00Z", event="schedule")
        output = self._invoke_guard(
            event="workflow_dispatch",
            runs=[scheduled],
            extra_env={"GITHUB_EVENT_INPUTS_PUBLISH_ONLY": "true"},
        )
        self.assertIn("should_run=true", output)


if __name__ == "__main__":
    unittest.main()
