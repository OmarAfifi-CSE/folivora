"""
test_gemma_validator.py - Unit tests for Gemma `check_output()` digit gate & 1-retry rescue loop
"""

import sys
import unittest
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from gemma_service import GemmaDigestService, check_output


class TestGemmaValidator(unittest.TestCase):
    def setUp(self):
        self.service = GemmaDigestService()

    def test_accepts_exact_verified_numbers(self):
        text = (
            "Closed 9 of your 14 tabs below TabPFN's P90 threshold, saving 152.9 minutes of reading. "
            "Extracted 5 reference tabs onto your one-page cheat sheet, led by pgvector Documentation."
        )
        ok, reasons = check_output(
            text,
            required_tokens=["9", "5", "152.9"],
            allowed_numbers={"9", "14", "5", "152.9", "90"},
        )
        self.assertTrue(ok)
        self.assertEqual(reasons, [])

    def test_rejects_rounded_or_invented_digits(self):
        # Model rounded 152.9 to 153 and invented "in 4 seconds"
        bad_text = (
            "Closed 9 of your 14 tabs in 4 seconds, saving about 153 minutes of reading. "
            "Extracted 5 reference tabs onto your cheat sheet."
        )
        ok, reasons = check_output(
            bad_text,
            required_tokens=["9", "5", "152.9"],
            allowed_numbers={"9", "14", "5", "152.9", "90"},
        )
        self.assertFalse(ok)
        self.assertIn("missing_required_fact:152.9", reasons)
        self.assertIn("unverified_or_rounded_number:4", reasons)
        self.assertIn("unverified_or_rounded_number:153", reasons)

    def test_rejects_markdown_and_ai_cliches(self):
        bad_text = (
            "Sure! Here is your summary: **Closed 9** of 14 tabs saving 152.9 minutes, "
            "and kept 5 game-changer tabs."
        )
        ok, reasons = check_output(
            bad_text,
            required_tokens=["9", "5", "152.9"],
            allowed_numbers={"9", "14", "5", "152.9", "90"},
        )
        self.assertFalse(ok)
        self.assertIn("contains_banned_phrase_or_markdown", reasons)

    def test_retry_loop_rescues_first_try_rejection(self):
        def mock_caller(_prompt: str, attempt: int) -> str:
            if attempt == 1:
                # Fails attempt 1 due to invented digit '3' ("in 3 seconds")
                return (
                    "Closed 9 of 14 tabs in 3 seconds, saving 152.9 minutes of reading. "
                    "Extracted 5 reference tabs led by pgvector."
                )
            # Succeeds on attempt 2 after receiving named rejection reason
            return (
                "Closed 9 of 14 tabs below the P90 threshold, saving 152.9 minutes of reading. "
                "Extracted 5 reference tabs onto your one-page cheat sheet, led by pgvector."
            )

        res = self.service.synthesize_digest(
            total_tabs=14,
            kept_count=5,
            closed_count=9,
            minutes_saved=152.9,
            top_tab_title="pgvector Documentation",
            top_tab_p50=84.2,
            custom_caller=mock_caller,
        )
        self.assertEqual(res["source"], "gemma-retry")
        self.assertEqual(res["attempts"], 2)
        self.assertFalse(res["passed_first_try"])
        self.assertTrue(res["passed_with_retry"])
        self.assertIn("unverified_or_rounded_number:3", res["rejections"])

    def test_falls_back_to_template_when_both_attempts_fail(self):
        res = self.service.synthesize_digest(
            total_tabs=14,
            kept_count=5,
            closed_count=9,
            minutes_saved=152.9,
            top_tab_title="pgvector Documentation",
            top_tab_p50=84.2,
            custom_caller=lambda _p, _att: "Closed 999 imaginary tabs in 500 minutes.",
        )
        self.assertEqual(res["source"], "verified-template")
        self.assertIn("152.9 minutes", res["spoken_digest"])

    def test_build_prompt_supports_all_four_versions_and_title_digits(self):
        for ver in ("v1", "v2", "v3", "v4"):
            prompt, req, allowed = self.service.build_prompt(
                total_tabs=14,
                kept_count=5,
                closed_count=9,
                minutes_saved=152.9,
                top_tab_title="Cloudflare Error 525 SSL Fix: Part 2",
                top_tab_p50=81.4,
                prompt_version=ver,
            )
            self.assertIn("14", prompt)
            self.assertIn("525", allowed)
            self.assertEqual(req, ["9", "5", "152.9"])

    def test_rejects_empty_and_overlong_candidates(self):
        ok_empty, reasons_empty = check_output("", ["9"], {"9"})
        self.assertFalse(ok_empty)
        self.assertIn("empty_output", reasons_empty)

        long_draft = " ".join(["word"] * 50) + " 9"
        ok_long, reasons_long = check_output(long_draft, ["9"], {"9"}, max_words=40)
        self.assertFalse(ok_long)
        self.assertTrue(any(r.startswith("too_long") for r in reasons_long))


if __name__ == "__main__":
    unittest.main()
