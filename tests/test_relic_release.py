import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from relic_release import (  # noqa: E402
    migrate_approved_lifecycle_keys,
    resolve_local_relic_key,
    validate_lifecycle_release,
    validate_relic_key_continuity,
)


CHANCES = {
    "Common": {"Intact": 25.33, "Exceptional": 23.33, "Flawless": 20, "Radiant": 16.67},
    "Uncommon": {"Intact": 11, "Exceptional": 13, "Flawless": 17, "Radiant": 20},
    "Rare": {"Intact": 2, "Exceptional": 4, "Flawless": 6, "Radiant": 10},
}
AXI_Y2_REWARDS = [
    ("Fang Prime Blueprint", "fang_prime_blueprint", "Common"),
    ("Orthos Prime Handle", "orthos_prime_handle", "Common"),
    ("Epitaph Prime Barrel", "epitaph_prime_barrel", "Uncommon"),
    ("Yareli Prime Neuroptics Blueprint", "yareli_prime_neuroptics_blueprint", "Rare"),
    ("Akarius Prime Link", "akarius_prime_link", "Uncommon"),
    ("Kompressa Prime Blueprint", "kompressa_prime_blueprint", "Common"),
]


def make_axi_y2_relic():
    return {
        "name": "Axi Y2",
        "tier": "Axi",
        "vaulted": True,
        "rewards": [
            {"name": name, "urlName": slug, "rarity": rarity, "chances": dict(CHANCES[rarity])}
            for name, slug, rarity in AXI_Y2_REWARDS
        ],
    }


def make_lifecycle_record(relic, status="vaulted"):
    events = [
        {"type": "released", "gameDate": "2018-02-22", "gameDateTZ": "US-ET", "ts": None},
        {"type": "vaulted", "gameDate": "2023-08-30", "gameDateTZ": "US-ET", "ts": None},
    ]
    rewards = [
        {"name": reward["name"], "rarity": reward["rarity"], "urlName": reward["urlName"], "zh": None}
        for reward in relic["rewards"]
    ]
    full_record = {
        "name": relic["name"],
        "tier": relic["tier"],
        "isBaro": False,
        "status": status,
        "events": events,
        "rewards": rewards,
        "varzia": False,
        "varziaSet": None,
    }
    summary_record = {
        "name": relic["name"],
        "tier": relic["tier"],
        "status": status,
        "released": events[0]["gameDate"],
        "releasedTZ": events[0]["gameDateTZ"],
        "vaultedAt": events[1]["gameDate"],
        "vaultedTZ": events[1]["gameDateTZ"],
        "lastChange": events[-1]["gameDate"],
        "varzia": False,
        "varziaSet": None,
    }
    return full_record, summary_record


def canonical_relic_key(name, tier):
    import re
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") + "_relic"


class RelicReleaseTests(unittest.TestCase):
    def test_exact_axi_y2_key_correction_is_accepted(self):
        relic = make_axi_y2_relic()
        mapping = validate_relic_key_continuity(
            {"axi_o7_relic": copy.deepcopy(relic)},
            {"axi_y2_relic": copy.deepcopy(relic)},
        )
        self.assertEqual(mapping, {"axi_o7_relic": "axi_y2_relic"})

    def test_corrected_record_matches_the_confirmed_official_reward_table(self):
        relic = make_axi_y2_relic()
        for old_slug, new_slug in (("axi_o7_relic", "axi_y2_relic"),):
            with self.subTest(old_slug=old_slug, new_slug=new_slug):
                mapping = validate_relic_key_continuity(
                    {old_slug: copy.deepcopy(relic)},
                    {new_slug: copy.deepcopy(relic)},
                )
                self.assertEqual(mapping[old_slug], new_slug)
        self.assertEqual(len(relic["rewards"]), 6)

    def test_any_semantic_change_blocks_the_key_correction(self):
        mutations = [
            lambda r: r.update(name="Axi O7"),
            lambda r: r.update(tier="Neo"),
            lambda r: r.update(vaulted=False),
            lambda r: r["rewards"][0].update(urlName="different_prime_blueprint"),
            lambda r: r["rewards"][0].update(rarity="Uncommon"),
            lambda r: r["rewards"][0]["chances"].update(Radiant=16),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                old = make_axi_y2_relic()
                new = make_axi_y2_relic()
                mutate(new)
                with self.assertRaises(RuntimeError):
                    validate_relic_key_continuity(
                        {"axi_o7_relic": old}, {"axi_y2_relic": new}
                    )

    def test_rejects_unrelated_missing_key_or_extra_key_delta(self):
        with self.assertRaises(RuntimeError):
            validate_relic_key_continuity({"lith_a1_relic": {}}, {})
        with self.assertRaises(RuntimeError):
            validate_relic_key_continuity(
                {"axi_o7_relic": make_axi_y2_relic()},
                {"axi_y2_relic": make_axi_y2_relic(), "neo_b1_relic": {}},
            )

    def test_rejects_candidate_that_still_uses_the_known_stale_key(self):
        relic = make_axi_y2_relic()
        with self.assertRaisesRegex(RuntimeError, "Known stale relic key"):
            validate_relic_key_continuity({"axi_o7_relic": relic}, {"axi_o7_relic": relic})

    def test_new_non_conflicting_relic_addition_remains_allowed(self):
        self.assertEqual(
            validate_relic_key_continuity({"lith_a1_relic": {}}, {"lith_a1_relic": {}, "neo_b1_relic": {}}),
            {},
        )

    def test_lifecycle_rekey_preserves_history_and_is_idempotent(self):
        relic = make_axi_y2_relic()
        full_record, summary_record = make_lifecycle_record(relic)
        full = {"generated": "test-generation", "relics": {"axi_o7_relic": full_record}, "varziaRelics": []}
        summary = {"generated": "test-generation", "items": {"axi_o7_relic": summary_record}, "varziaRelics": []}
        original_full = copy.deepcopy(full_record)
        original_summary = copy.deepcopy(summary_record)

        changed = migrate_approved_lifecycle_keys({"axi_y2_relic": relic}, full, summary)
        self.assertTrue(changed)
        self.assertNotIn("axi_o7_relic", full["relics"])
        self.assertNotIn("axi_o7_relic", summary["items"])
        self.assertEqual(full["relics"]["axi_y2_relic"], original_full)
        self.assertEqual(summary["items"]["axi_y2_relic"], original_summary)
        self.assertEqual(validate_lifecycle_release({"axi_y2_relic": relic}, full, summary), 1)
        self.assertFalse(migrate_approved_lifecycle_keys({"axi_y2_relic": relic}, full, summary))

    def test_lifecycle_rekey_rejects_both_keys_or_reward_disagreement(self):
        relic = make_axi_y2_relic()
        full_record, summary_record = make_lifecycle_record(relic)
        full = {"generated": "g", "relics": {"axi_o7_relic": full_record, "axi_y2_relic": copy.deepcopy(full_record)}, "varziaRelics": []}
        summary = {"generated": "g", "items": {"axi_o7_relic": summary_record}, "varziaRelics": []}
        with self.assertRaisesRegex(RuntimeError, "exactly one"):
            migrate_approved_lifecycle_keys({"axi_y2_relic": relic}, full, summary)

        full = {"generated": "g", "relics": {"axi_o7_relic": full_record}, "varziaRelics": []}
        full["relics"]["axi_o7_relic"]["rewards"][0]["urlName"] = "wrong_reward_identity"
        summary = {"generated": "g", "items": {"axi_o7_relic": summary_record}, "varziaRelics": []}
        with self.assertRaisesRegex(RuntimeError, "reward identity mismatch"):
            migrate_approved_lifecycle_keys({"axi_y2_relic": relic}, full, summary)

    def test_deep_date_resolver_uses_exact_canonical_identity_only(self):
        relic = make_axi_y2_relic()
        self.assertEqual(
            resolve_local_relic_key("Axi Y2", "Axi", {"axi_y2_relic": relic}, canonical_relic_key),
            "axi_y2_relic",
        )
        with self.assertRaisesRegex(RuntimeError, "name-only match"):
            resolve_local_relic_key("Axi Y2", "Axi", {"axi_o7_relic": relic}, canonical_relic_key)
        mismatched_tier = copy.deepcopy(relic)
        mismatched_tier["tier"] = "Neo"
        with self.assertRaisesRegex(RuntimeError, "Canonical key collision"):
            resolve_local_relic_key("Axi Y2", "Axi", {"axi_y2_relic": mismatched_tier}, canonical_relic_key)
        self.assertIsNone(resolve_local_relic_key("Axi Z9", "Axi", {}, canonical_relic_key))


if __name__ == "__main__":
    unittest.main()
