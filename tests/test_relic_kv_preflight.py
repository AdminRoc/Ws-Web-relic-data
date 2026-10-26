import tempfile
import unittest
from pathlib import Path

from scripts.relic_kv_preflight import decide


class RelicKvPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.current = Path(self.temp.name) / "current.json"
        self.expected = Path(self.temp.name) / "expected.json"

    def tearDown(self):
        self.temp.cleanup()

    def test_matching_production_value_skips_write(self):
        self.current.write_bytes(b'{"items":[]}')
        self.expected.write_bytes(b'{"items":[]}')
        self.assertEqual(decide("200", str(self.current), str(self.expected)), "skip")

    def test_different_production_value_is_republished(self):
        self.current.write_bytes(b'{"items":[1]}')
        self.expected.write_bytes(b'{"items":[2]}')
        self.assertEqual(decide("200", str(self.current), str(self.expected)), "publish")

    def test_missing_production_key_is_published(self):
        self.expected.write_bytes(b'{"items":[]}')
        self.assertEqual(decide("404", str(self.current), str(self.expected)), "publish")

    def test_unavailable_preflight_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "HTTP 503"):
            decide("503", str(self.current), str(self.expected))


if __name__ == "__main__":
    unittest.main()
