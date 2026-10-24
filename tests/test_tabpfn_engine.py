"""
test_tabpfn_engine.py - Unit tests for Prior Labs TabPFN quantile engine
"""

import sys
import unittest
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from browserbase_service import PRESET_TAB_PILES, compute_intent_match
from tabpfn_engine import TabPFNQuantileEngine


class TestTabPFNEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = TabPFNQuantileEngine(context_rows=160)
        cls.engine.warm_up()

    def test_quantile_monotonicity_and_verdicts(self):
        tabs = [dict(t) for t in PRESET_TAB_PILES["postgres_docker_pile"]["tabs"]]
        for t in tabs:
            t["intent_match_score"] = compute_intent_match(t, "flutter")

        res = self.engine.evaluate_tab_pile(tabs, rated=True)
        self.assertEqual(res["total_tabs"], 14)
        self.assertEqual(res["kept_count"] + res["closed_count"], 14)
        self.assertGreater(res["kept_count"], 0)
        self.assertGreater(res["closed_count"], 0)
        self.assertGreater(res["minutes_saved"], 50.0)

        for item in res["all_tabs"]:
            self.assertLessEqual(item["p10_score"], item["p50_score"])
            self.assertLessEqual(item["p50_score"], item["p90_score"])
            self.assertIn(item["verdict"], ("KEEP_EXTRACT", "SAFE_CLOSE"))

    def test_unrated_summarize_all_mode_keeps_all_valid_tabs(self):
        tabs = [dict(t) for t in PRESET_TAB_PILES["postgres_docker_pile"]["tabs"]]
        for t in tabs:
            t["intent_match_score"] = compute_intent_match(t, "")
        res = self.engine.evaluate_tab_pile(tabs, rated=False)
        self.assertFalse(res["rated"])
        self.assertEqual(res["mode"], "summarize_all")
        self.assertEqual(res["kept_count"], 14)
        self.assertEqual(res["closed_count"], 0)

    def test_code_reference_beats_old_essay(self):
        pair = [
            {
                "id": "code-tab",
                "title": "pgvector HNSW Index",
                "url": "https://github.com/pgvector/pgvector",
                "domain": "github.com",
                "age_days": 3.0,
                "word_count": 1100,
                "code_block_count": 10,
                "table_count": 2,
                "heading_count": 12,
                "link_density": 2.5,
                "intent_match_score": 0.92,
                "snippet": "CREATE INDEX ON items USING hnsw",
            },
            {
                "id": "essay-tab",
                "title": "5,000-Word Essay With Zero Code",
                "url": "https://blog.example.com/essay",
                "domain": "blog.example.com",
                "age_days": 22.0,
                "word_count": 5200,
                "code_block_count": 0,
                "table_count": 0,
                "heading_count": 4,
                "link_density": 2.0,
                "intent_match_score": 0.08,
                "snippet": "Long essay.",
            },
        ]
        res = self.engine.evaluate_tab_pile(pair)
        by_id = {t["id"]: t for t in res["all_tabs"]}
        self.assertGreater(by_id["code-tab"]["p50_score"], by_id["essay-tab"]["p50_score"] + 25.0)
        self.assertEqual(by_id["code-tab"]["verdict"], "KEEP_EXTRACT")
        self.assertEqual(by_id["essay-tab"]["verdict"], "SAFE_CLOSE")

    def test_handles_nan_and_inf_gracefully(self):
        noisy_tab = [
            {
                "id": "nan-tab",
                "title": "Noisy Telemetry Tab",
                "url": "https://example.com",
                "domain": "example.com",
                "age_days": float("nan"),
                "word_count": float("inf"),
                "code_block_count": None,
                "table_count": -1,
                "heading_count": "bad",
                "link_density": None,
                "intent_match_score": float("nan"),
                "snippet": "test",
            }
        ]
        res = self.engine.evaluate_tab_pile(noisy_tab)
        self.assertEqual(res["total_tabs"], 1)
        self.assertTrue(0.0 <= res["all_tabs"][0]["p50_score"] <= 100.0)

    def test_empty_tabs_raises_value_error(self):
        with self.assertRaises(ValueError):
            self.engine.evaluate_tab_pile([])


if __name__ == "__main__":
    unittest.main()
