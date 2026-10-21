"""
test_telemetry_scrubber.py - Unit tests for Sentry OpenTelemetry GenAI spans & secret scrubber
"""

import sys
import unittest
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from telemetry import before_send_scrubber, scrub_dict_secrets, trace_genai_span


class TestTelemetryScrubber(unittest.TestCase):
    def test_redacts_secrets_while_preserving_input_and_output_tokens(self):
        payload = {
            "api_key": "sk_live_12345",
            "access_token": "tok_abc",
            "tabpfn_token": "tabpfn_sk_999",
            "gen_ai.usage.input_tokens": 430,
            "gen_ai.usage.output_tokens": 28,
            "gen_ai.performance.tokens_per_second": 31.4,
            "ws_url": "wss://connect.usw2.browserbase.com/?signingKey=eyJhbGciOiJBMjU2",
        }
        cleaned = scrub_dict_secrets(payload)
        self.assertEqual(cleaned["api_key"], "[Redacted]")
        self.assertEqual(cleaned["access_token"], "[Redacted]")
        self.assertEqual(cleaned["tabpfn_token"], "[Redacted]")
        self.assertIn("signingKey=[Redacted]", cleaned["ws_url"])
        # Crucial invariant: plural input_tokens / output_tokens must NEVER be scrubbed
        self.assertEqual(cleaned["gen_ai.usage.input_tokens"], 430)
        self.assertEqual(cleaned["gen_ai.usage.output_tokens"], 28)
        self.assertEqual(cleaned["gen_ai.performance.tokens_per_second"], 31.4)

    def test_before_send_scrubber_handles_nested_spans(self):
        event = {
            "extra": {"ELEVENLABS_API_KEY": "secret_1", "safe_metric": 42},
            "spans": [
                {
                    "op": "gen_ai.request",
                    "data": {
                        "gen_ai.usage.input_tokens": 120,
                        "gen_ai.usage.output_tokens": 34,
                        "bot_token": "secret_bot",
                    },
                }
            ],
        }
        scrubbed = before_send_scrubber(event)
        self.assertEqual(scrubbed["extra"]["ELEVENLABS_API_KEY"], "[Redacted]")
        self.assertEqual(scrubbed["extra"]["safe_metric"], 42)
        span_data = scrubbed["spans"][0]["data"]
        self.assertEqual(span_data["bot_token"], "[Redacted]")
        self.assertEqual(span_data["gen_ai.usage.input_tokens"], 120)
        self.assertEqual(span_data["gen_ai.usage.output_tokens"], 34)

    def test_trace_genai_span_collects_metrics(self):
        collector = []
        with trace_genai_span("gemma.test", "google/gemma-3-27b-it", trace_collector=collector) as meta:
            meta["gen_ai.usage.input_tokens"] = 100
            meta["gen_ai.usage.output_tokens"] = 25
        self.assertEqual(len(collector), 1)
        self.assertEqual(collector[0]["gen_ai.usage.input_tokens"], 100)
        self.assertEqual(collector[0]["gen_ai.usage.output_tokens"], 25)
        self.assertGreater(collector[0]["latency_ms"], 0.0)
        self.assertGreater(collector[0]["gen_ai.performance.tokens_per_second"], 0.0)


if __name__ == "__main__":
    unittest.main()
