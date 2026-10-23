import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from build_relic_db import (  # noqa: E402
    preserve_marketless_requiem_records,
    source_relic_key,
)


class BuildRelicDbIdentityTests(unittest.TestCase):
    def test_requiem_relics_without_market_info_keep_canonical_keys(self):
        expected = {
            "Requiem Eterna Relic": "requiem_eterna_relic",
            "Requiem I": "requiem_i_relic",
            "Requiem II": "requiem_ii_relic",
            "Requiem III": "requiem_iii_relic",
            "Requiem IV": "requiem_iv_relic",
        }
        for name, key in expected.items():
            with self.subTest(name=name):
                self.assertEqual(source_relic_key(name, ""), key)

    def test_existing_market_identity_takes_precedence(self):
        self.assertEqual(source_relic_key("Axi C13", "axi_c13_relic"), "axi_c13_relic")

    def test_unknown_or_non_requiem_without_market_identity_is_not_guessed(self):
        self.assertIsNone(source_relic_key("Requiem V", ""))
        self.assertIsNone(source_relic_key("Axi C13", ""))

    def test_marketless_requiem_keeps_previous_status_and_rewards_unchanged(self):
        previous = {
            "requiem_i_relic": {
                "name": "Requiem I", "tier": "Requiem", "vaulted": True,
                "rewards": [{"name": "existing reward", "urlName": "existing_reward"}],
            }
        }
        candidate = {}

        preserve_marketless_requiem_records(candidate, previous, {"Requiem I"})

        self.assertEqual(candidate, previous)
        self.assertIsNot(candidate["requiem_i_relic"], previous["requiem_i_relic"])

    def test_marketless_requiem_requires_known_identity_and_previous_record(self):
        with self.assertRaisesRegex(RuntimeError, "Unknown marketless Requiem identity"):
            preserve_marketless_requiem_records({}, {}, {"Requiem V"})
        with self.assertRaisesRegex(RuntimeError, "no previous canonical record"):
            preserve_marketless_requiem_records({}, {}, {"Requiem I"})


if __name__ == "__main__":
    unittest.main()
