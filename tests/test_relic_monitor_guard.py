import unittest
from unittest.mock import patch

from scripts.relic_monitor_guard import _get_parent_jobs, should_run


class RelicMonitorGuardTests(unittest.TestCase):
    def test_manual_and_schedule_triggers_run_without_parent_jobs(self):
        self.assertTrue(should_run("workflow_dispatch"))
        self.assertTrue(should_run("schedule"))

    def test_successful_parent_producer_runs_monitor(self):
        jobs = [{"name": "update-data", "conclusion": "success"}]
        self.assertTrue(should_run("workflow_run", jobs))

    def test_skipped_parent_producer_does_not_run_monitor(self):
        jobs = [{"name": "update-data", "conclusion": "skipped"}]
        self.assertFalse(should_run("workflow_run", jobs))

    def test_failed_parent_producer_does_not_run_monitor(self):
        jobs = [{"name": "update-data", "conclusion": "failure"}]
        self.assertFalse(should_run("workflow_run", jobs))

    def test_invalid_or_ambiguous_parent_job_list_fails_closed(self):
        for jobs in (None, [], [{"name": "other", "conclusion": "success"}], [
            {"name": "update-data", "conclusion": "success"},
            {"name": "update-data", "conclusion": "success"},
        ]):
            with self.subTest(jobs=jobs), self.assertRaises(ValueError):
                should_run("workflow_run", jobs)

    def test_actions_api_failure_is_sanitized(self):
        with patch("scripts.relic_monitor_guard.urlopen", side_effect=OSError("private request details")):
            with self.assertRaisesRegex(RuntimeError, "could not read parent workflow jobs") as error:
                _get_parent_jobs("AdminRoc/Ws-Web-relic-data", "not-logged", "123")
        self.assertNotIn("private request details", str(error.exception))


if __name__ == "__main__":
    unittest.main()
