import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from build_deep_date_db import fetch_varzia_relics


class VarziaRelicMappingTests(unittest.TestCase):
    def test_uses_market_slug_when_wfcd_url_name_is_missing(self):
        rows = [
            ("T1VoidProjectionProteaIvaraVaultABronze", "Lith B12"),
            ("T2VoidProjectionProteaIvaraVaultABronze", "Meso I3"),
            ("T2VoidProjectionProteaIvaraVaultBBronze", "Meso V16"),
            ("T3VoidProjectionProteaIvaraVaultABronze", "Neo P11"),
            ("T3VoidProjectionProteaIvaraVaultBBronze", "Neo O4"),
            ("T4VoidProjectionProteaIvaraVaultABronze", "Axi A23"),
        ]
        inventory = [
            {
                "uniqueName": f"/Lotus/Types/Game/Projections/{unique_name}",
                "credits": 1,
                "ducats": None,
            }
            for unique_name, _ in rows
        ]
        relic_items = [
            {
                "uniqueName": f"/Lotus/Types/Game/Projections/{unique_name}",
                "name": f"{display_name} Intact",
                "marketInfo": {"urlName": None},
            }
            for unique_name, display_name in rows
        ]
        with patch(
            "build_deep_date_db.fetch_json",
            side_effect=[{"vaultTrader": {"inventory": inventory}}, relic_items],
        ):
            result = fetch_varzia_relics()

        self.assertEqual(
            result,
            [
                (f"{display_name.lower().replace(' ', '_')}_relic", unique_name)
                for unique_name, display_name in rows
            ],
        )

    def test_keeps_explicit_market_url_name(self):
        unique_name = "/Lotus/Types/Game/Projections/T4VoidProjectionTestBronze"
        inventory = [{"uniqueName": unique_name, "credits": 1, "ducats": None}]
        relic_items = [{
            "uniqueName": unique_name,
            "name": "Unrecognized Test Relic",
            "marketInfo": {"urlName": "axi_test_relic"},
        }]
        with patch(
            "build_deep_date_db.fetch_json",
            side_effect=[{"vaultTrader": {"inventory": inventory}}, relic_items],
        ):
            result = fetch_varzia_relics()
        self.assertEqual(result, [("axi_test_relic", "T4VoidProjectionTestBronze")])

    def test_does_not_guess_nonstandard_display_names(self):
        unique_name = "/Lotus/Types/Game/Projections/T4VoidProjectionTestBronze"
        inventory = [{"uniqueName": unique_name, "credits": 1, "ducats": None}]
        relic_items = [{
            "uniqueName": unique_name,
            "name": "Meso I3 Flawless",
            "marketInfo": {"urlName": None},
        }]
        with patch(
            "build_deep_date_db.fetch_json",
            side_effect=[{"vaultTrader": {"inventory": inventory}}, relic_items],
        ):
            result = fetch_varzia_relics()
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
