import importlib.util
import json
import tempfile
import time
import urllib.error
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_price_db.py"
SPEC = importlib.util.spec_from_file_location("build_price_db", SCRIPT)
build_price_db = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build_price_db)


class BuildPriceDbApiTests(unittest.TestCase):
    def test_waits_between_wm_manifest_and_first_order_request(self):
        starts = []

        def fake_fetch_json(url):
            starts.append(time.monotonic())
            if url == build_price_db.WM_ITEMS_URL:
                return {"data": []}
            return {"data": {"sell": []}}

        with tempfile.TemporaryDirectory() as temp_dir:
            build_price_db.DATA_DIR = temp_dir
            Path(temp_dir, "reward-items.json").write_text(
                json.dumps([{"urlName": "sample_item", "name": "Sample Item"}]),
                encoding="utf-8",
            )
            with patch.object(build_price_db, "fetch_json", side_effect=fake_fetch_json):
                build_price_db.build()

        self.assertEqual(len(starts), 2)
        self.assertGreaterEqual(starts[1] - starts[0], build_price_db.REQUEST_DELAY - 0.02)

    def test_retries_rate_limit_using_retry_after(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"ok": true}'

        throttled = urllib.error.HTTPError(
            "https://api.warframe.market/v2/items", 429, "rate limited",
            {"Retry-After": "2"}, None,
        )
        with (
            patch.object(build_price_db.urllib.request, "urlopen", side_effect=[throttled, FakeResponse()]) as urlopen,
            patch.object(build_price_db.time, "sleep") as sleep,
        ):
            self.assertEqual(build_price_db.fetch_json("https://api.warframe.market/v2/items"), {"ok": True})

        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once_with(2.0)

    def test_does_not_retry_permanent_not_found(self):
        missing = urllib.error.HTTPError(
            "https://api.warframe.market/v2/items/missing", 404, "not found", {}, None,
        )
        with (
            patch.object(build_price_db.urllib.request, "urlopen", side_effect=missing) as urlopen,
            patch.object(build_price_db.time, "sleep") as sleep,
        ):
            with self.assertRaises(urllib.error.HTTPError):
                build_price_db.fetch_json("https://api.warframe.market/v2/items/missing")

        urlopen.assert_called_once()
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
