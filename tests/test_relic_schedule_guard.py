import unittest
from datetime import datetime, timezone

from scripts.relic_schedule_guard import should_skip_fallback


NOW = datetime(2026, 9, 29, 21, 5, tzinfo=timezone.utc)


def run(created_at, *, status="completed", event="workflow_dispatch", branch="main"):
    return {
        "created_at": created_at,
        "status": status,
        "event": event,
        "head_branch": branch,
    }


class RelicScheduleGuardTests(unittest.TestCase):
    def test_previous_hour_success_does_not_suppress_fallback(self):
        self.assertFalse(should_skip_fallback([run("2026-09-29T20:00:46Z")], NOW))

    def test_current_hour_dispatch_suppresses_fallback(self):
        self.assertTrue(should_skip_fallback([run("2026-09-29T21:01:01Z")], NOW))

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


if __name__ == "__main__":
    unittest.main()
