import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class RelicKvWorkflowTest(unittest.TestCase):
    def test_normal_and_deep_date_publishers_read_back_each_key_before_markers(self):
        normal = (ROOT / ".github/workflows/update-data.yml").read_text(encoding="utf-8")
        deep = (ROOT / ".github/workflows/monitor-relic-dates.yml").read_text(encoding="utf-8")
        for text in (normal, deep):
            self.assertIn("https://relic.wfspeed.run/api/update-kv", text)
            self.assertIn("https://relic.wfspeed.run/api/kv?key=", text)
            self.assertIn("readback hash mismatch for", text)
        self.assertLess(normal.index("readback hash mismatch for"), normal.index("publish prices-summary.json prices_summary_json"))
        self.assertLess(normal.index("publish prices-summary.json prices_summary_json"), normal.index("publish relic-deep-date-summary.json relic_deep_date_summary_json"))


if __name__ == "__main__":
    unittest.main()
