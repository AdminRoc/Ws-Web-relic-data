import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("validate_relic_release.py")
PUBLISHED = [
    "relics.json", "reward-items.json", "item-categories.json", "item-names-zh.json",
    "relic-deep-date.json", "prices.json", "prices-summary.json",
    "relic-deep-date-summary.json", "update-versions.json",
]


class RelicReleaseValidatorTest(unittest.TestCase):
    def test_rejects_encrypted_wrapper(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            for name in PUBLISHED:
                value = {"items": {}, "generated": "2026-01-01T00:00:00Z"}
                if name == "prices.json":
                    value = {"ct": "encrypted"}
                (data / name).write_text(json.dumps(value), encoding="utf-8")
            result = subprocess.run([sys.executable, str(SCRIPT), "--data-dir", str(data)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn("encrypted wrapper prices.json", result.stdout)


if __name__ == "__main__":
    unittest.main()
