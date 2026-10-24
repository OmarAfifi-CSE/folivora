import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "eval"
if str(EVAL) not in sys.path:
    sys.path.insert(0, str(EVAL))

import summary_quality


class SummaryQualityHarnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = summary_quality.run_harness()

    def test_offline_fixture_runs_all_cases(self):
        self.assertEqual(self.report["aggregate"]["cases"], 20)
        self.assertEqual(self.report["mode"], "offline-fixture")

    def test_offline_fixture_passes_deterministic_gates(self):
        aggregate = self.report["aggregate"]
        self.assertEqual(aggregate["fallback"], 0)
        self.assertEqual(aggregate["fact_coverage"], 1.0)
        self.assertEqual(aggregate["unsupported_numbers"], 0)
        self.assertEqual(aggregate["quote_only_sections"], 0)
        self.assertEqual(aggregate["verbatim_copy_sections"], 0)
        self.assertEqual(aggregate["emoji_count"], 0)
        self.assertEqual(aggregate["forbidden_claims"], 0)
        self.assertLess(aggregate["duplicate_rate"], 0.05)
        self.assertEqual(aggregate["word_target_rate"], 1.0)
        for gate in (
            "no_unsupported_numbers",
            "no_quote_only_bullets",
            "no_verbatim_copy",
            "no_emoji",
            "no_forbidden_claims",
            "duplicate_rate_below_5pct",
            "fact_coverage_at_least_85pct",
            "word_target_at_least_18_of_20",
            "manual_review_complete",
            "manual_readability_at_least_4",
            "manual_readability_mean_at_least_4",
        ):
            self.assertTrue(self.report["gates"][gate], gate)
        self.assertTrue(self.report["passed"])

    def test_manual_review_gate_stays_open_without_scores(self):
        unreviewed = summary_quality.run_harness(review_path=EVAL / "missing_review.json")
        self.assertFalse(unreviewed["gates"]["manual_review_complete"])
        self.assertFalse(unreviewed["passed"])

    def test_cases_include_rated_and_unrated_tabs(self):
        rated = [case for case in self.report["cases"] if case["rated"]]
        unrated = [case for case in self.report["cases"] if not case["rated"]]
        self.assertEqual(len(rated), 10)
        self.assertEqual(len(unrated), 10)


if __name__ == "__main__":
    unittest.main()
