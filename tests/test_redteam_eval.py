"""
test_redteam_eval.py - Automated tests for the 24-case Adversarial Red-Team & Canary Suite.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "eval"
if str(EVAL) not in sys.path:
    sys.path.insert(0, str(EVAL))

import redteam_eval


class RedTeamEvalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = redteam_eval.run_redteam_suite()

    def test_redteam_runs_all_24_cases_across_8_threat_families(self):
        self.assertEqual(self.report["total_adversarial_cases"], 24)
        self.assertEqual(self.report["threat_families"], 8)

    def test_schema_only_json_fails_semantic_grounding_attacks(self):
        schema_mode = self.report["modes"]["schema_only_json"]
        self.assertEqual(schema_mode["hijacked"], 21)
        self.assertEqual(schema_mode["asr_pct"], 87.5)

    def test_folivora_hardened_blocks_all_24_attacks_and_canary_leaks(self):
        hardened = self.report["modes"]["folivora_hardened"]
        self.assertEqual(hardened["hijacked"], 0)
        self.assertEqual(hardened["asr_pct"], 0.0)
        self.assertEqual(hardened["canary_leaks"], 0)
        self.assertTrue(self.report["passed"])


if __name__ == "__main__":
    unittest.main()
