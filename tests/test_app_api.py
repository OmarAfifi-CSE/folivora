"""
test_app_api.py - End-to-End FastAPI Route & Integration Tests for Folivora
"""

import sys
import unittest
from pathlib import Path
from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import app as app_module
from app import _RATE_LIMITS, app, rate_limiter, summary_job_store


class _FakePolishService:
    available = True
    max_tabs = 2

    def __init__(self):
        self.calls = 0

    def polish_many(
        self, tabs, *, rated, intent, shared_cache_allowed=False, batch_budget_s=None, trace_collector=None
    ):
        self.calls += 1
        results = {}
        for tab_id, tab in tabs:
            results[tab_id] = {
                "overview": "A concise grounded overview that is returned by the test double.",
                "takeaways": ["Queue Design — A grounded takeaway supplied by the test double."],
                "interest_section": tab.get("interest_section"),
                "summary_read_sec": 20,
                "summary_source": "gemma_verified",
                "polish_status": "polished",
                "summary_validation": {"status": "passed", "attempts": 1},
            }
        return results


class TestAppAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def setUp(self):
        rate_limiter.reset()
        summary_job_store.reset()

    def _use_fake_polish(self):
        fake = _FakePolishService()
        original = app_module.summary_service
        app_module.summary_service = fake
        self.addCleanup(setattr, app_module, "summary_service", original)
        return fake

    def _create_minimal_job(self):
        job_id = summary_job_store.create(
            [
                {
                    "id": "user-tab-1",
                    "title": "Queue Reliability",
                    "full_read_min": 4.0,
                    "summary_read_sec": 40,
                    "overview": "Deterministic overview.",
                    "takeaways": ["Queue Design — Deterministic takeaway."],
                    "interest_section": None,
                    "full_text": "PRIVATE FULL ARTICLE TEXT MUST NEVER LEAVE THE SERVER.",
                    "all_paragraphs": ["PRIVATE PARAGRAPH."],
                }
            ],
            rated=False,
            effective_intent="",
            preset_key="sample",
            shared_cache_allowed=True,
            total_tabs=1,
            kept_count=1,
            closed_count=0,
            closed_read_min=0.0,
            total_words=500,
            closed_words=0,
        )
        return job_id

    def test_health_endpoint_reports_all_integrations(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "healthy")
        self.assertIn("integrations", data)
        self.assertIn("browserbase", data["integrations"])
        self.assertIn("tabpfn_token", data["integrations"])

    def test_presets_endpoint(self):
        resp = self.client.get("/presets")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("postgres_docker_pile", data)
        self.assertIn("edge_ai_quantization_pile", data)

    def test_benchmarks_endpoint_returns_3600_row_study(self):
        resp = self.client.get("/benchmarks")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data.get("dataset_total_rows"), 3600)
        self.assertEqual(data.get("unseen_test_rows"), 720)
        self.assertGreaterEqual(len(data.get("benchmarks", [])), 5)
        safety = data.get("decision_safety_analysis") or []
        self.assertEqual(len(safety), 5)
        shipped = next(item for item in safety if "Shipped" in item["policy"])
        self.assertLess(shipped["false_close_rate_pct"], 1.0)

    def test_triage_fast_lane_postgres_pile(self):
        # Mode A: No user_intent -> Summarize All Mode (rated=False, all 14 tabs kept & summarized)
        resp = self.client.post(
            "/triage",
            json={
                "preset_key": "postgres_docker_pile",
                "user_intent": "",
                "verify_live_browserbase": False,
                "include_gemma": False,
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["mode"], "summarize_all")
        self.assertFalse(data["rated"])
        self.assertEqual(data["total_tabs"], 14)
        self.assertEqual(data["kept_count"], 14)
        self.assertEqual(data["closed_count"], 0)
        self.assertGreater(data["minutes_saved"], 50.0)

    def test_triage_everyday_life_interest_japan_travel(self):
        # Mode B: Everyday non-coding topic ("japan travel") -> rated=True, separates travel from unrelated tabs
        resp = self.client.post(
            "/triage",
            json={
                "preset_key": "postgres_docker_pile",
                "user_intent": "japan travel",
                "verify_live_browserbase": False,
                "include_gemma": False,
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["mode"], "interest_ranked")
        self.assertTrue(data["rated"])
        self.assertEqual(data["kept_count"], 1)
        self.assertEqual(data["closed_count"], 13)
        self.assertIn("Japan", data["kept_tabs"][0]["title"])

    def test_triage_edge_ai_pile(self):
        resp = self.client.post(
            "/triage",
            json={
                "preset_key": "edge_ai_quantization_pile",
                "verify_live_browserbase": False,
                "include_gemma": False,
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total_tabs"], 10)

    def test_digest_upgrade_endpoint_enforces_digit_gate(self):
        resp = self.client.post(
            "/digest",
            json={
                "total_tabs": 14,
                "kept_count": 5,
                "closed_count": 9,
                "minutes_saved": 152.9,
                "top_tab_title": "pgvector Documentation",
                "top_tab_p50": 84.2,
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("9", data["spoken_digest"])
        self.assertIn("5", data["spoken_digest"])
        self.assertIn("152.9", data["spoken_digest"])

    def test_index_and_favicon_routes(self):
        idx = self.client.get("/")
        self.assertEqual(idx.status_code, 200)
        self.assertEqual(idx.headers.get("cache-control"), "no-store")
        fav = self.client.get("/favicon.ico")
        self.assertEqual(fav.status_code, 200)
        ext = self.client.get("/download-extension.zip")
        self.assertEqual(ext.status_code, 200)
        self.assertEqual(ext.headers.get("content-type"), "application/zip")
        pitch = self.client.get("/pitch")
        self.assertEqual(pitch.status_code, 200)
        self.assertIn("Folivora", pitch.text)
        pitch_pdf = self.client.get("/pitch.pdf")
        self.assertEqual(pitch_pdf.status_code, 200)
        self.assertEqual(pitch_pdf.headers.get("content-type"), "application/pdf")

    def test_synthesize_audio_rejects_empty_and_overlong_text(self):
        r_empty = self.client.post("/synthesize-audio", json={"text": "   "})
        self.assertEqual(r_empty.status_code, 400)
        r_long = self.client.post("/synthesize-audio", json={"text": "a" * 1050})
        self.assertEqual(r_long.status_code, 400)

    def test_rate_limiter_blocks_bursts_with_retry_after(self):
        rate_limiter.reset()
        headers = {"X-Forwarded-For": "198.51.100.77"}
        limit = _RATE_LIMITS["/synthesize-audio"][0]
        try:
            for _ in range(limit):
                resp = self.client.options("/synthesize-audio", headers=headers)
                self.assertEqual(resp.status_code, 405)
            blocked = self.client.options("/synthesize-audio", headers=headers)
            self.assertEqual(blocked.status_code, 429)
            self.assertGreaterEqual(int(blocked.headers["Retry-After"]), 1)
            self.assertEqual(blocked.headers.get("cache-control"), "no-store")
            self.assertEqual(blocked.headers.get("x-content-type-options"), "nosniff")
        finally:
            rate_limiter.reset()

    def test_polish_endpoint_rate_limiter_blocks_bursts(self):
        rate_limiter.reset()
        headers = {"X-Forwarded-For": "198.51.100.88"}
        limit = _RATE_LIMITS["/polish-tabs"][0]
        try:
            for _ in range(limit):
                resp = self.client.options("/polish-tabs", headers=headers)
                self.assertEqual(resp.status_code, 405)
            blocked = self.client.options("/polish-tabs", headers=headers)
            self.assertEqual(blocked.status_code, 429)
            self.assertGreaterEqual(int(blocked.headers["Retry-After"]), 1)
            self.assertEqual(blocked.headers.get("cache-control"), "no-store")
        finally:
            rate_limiter.reset()

    def test_transcribe_voice_rejects_empty_file(self):
        r = self.client.post(
            "/transcribe-voice",
            files={"file": ("empty.webm", b"", "audio/webm")},
        )
        self.assertEqual(r.status_code, 400)

    def test_triage_with_custom_user_intent(self):
        resp = self.client.post(
            "/triage",
            json={
                "preset_key": "postgres_docker_pile",
                "user_intent": "only keep sentry input_tokens regex scrubber and cloudflare 525 ssl",
                "verify_live_browserbase": False,
                "include_gemma": False,
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("sentry", data["effective_intent"].lower())
        self.assertGreater(data["kept_count"], 0)

    def test_triage_inline_gemma_option(self):
        resp = self.client.post(
            "/triage",
            json={
                "preset_key": "edge_ai_quantization_pile",
                "verify_live_browserbase": False,
                "include_gemma": True,
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(len(data["spoken_digest"]) > 20)
        self.assertIn(data["digest_source"], ("gemma", "gemma-retry", "verified-template"))

    def test_triage_custom_urls_strictly_isolates_user_links(self):
        resp = self.client.post(
            "/triage",
            json={
                "custom_urls": [
                    "https://example.com",
                    "https://github.com/pgvector/pgvector",
                ],
                "verify_live_browserbase": False,
                "include_gemma": False,
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        # Must strictly equal the 2 user URLs and NEVER mix in the 14 preset tabs!
        self.assertEqual(data["total_tabs"], 2)
        self.assertEqual(data["preset_key"], "custom_urls")
        returned_urls = {t["url"] for t in data["all_tabs"]}
        self.assertEqual(returned_urls, {"https://example.com", "https://github.com/pgvector/pgvector"})

    def test_triage_rejects_unknown_preset_and_unsafe_urls(self):
        unknown = self.client.post(
            "/triage",
            json={"preset_key": "not_a_real_pile", "custom_urls": [], "user_intent": ""},
        )
        self.assertEqual(unknown.status_code, 400)

        private = self.client.post(
            "/triage",
            json={
                "preset_key": "postgres_docker_pile",
                "custom_urls": ["http://127.0.0.1:8000/health"],
                "user_intent": "",
            },
        )
        self.assertEqual(private.status_code, 422)

        credentials = self.client.post(
            "/triage",
            json={
                "preset_key": "postgres_docker_pile",
                "custom_urls": ["https://user:pass@example.com/private"],
                "user_intent": "",
            },
        )
        self.assertEqual(credentials.status_code, 422)

        too_many = self.client.post(
            "/triage",
            json={
                "preset_key": "postgres_docker_pile",
                "custom_urls": [f"https://example.com/{i}" for i in range(66)],
                "user_intent": "",
            },
        )
        self.assertEqual(too_many.status_code, 422)

        long_intent = self.client.post(
            "/triage",
            json={
                "preset_key": "postgres_docker_pile",
                "custom_urls": [],
                "user_intent": "x" * 241,
            },
        )
        self.assertEqual(long_intent.status_code, 422)

    def test_triage_compacts_large_hof_payload_and_caches_privately(self):
        resp = self.client.post(
            "/triage",
            headers={"Accept-Encoding": "identity"},
            json={
                "preset_key": "dev_hall_of_fame_pile",
                "user_intent": "Gemini",
                "verify_live_browserbase": False,
                "include_gemma": False,
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get("cache-control"), "no-store")
        self.assertLess(len(resp.content), 500_000)
        data = resp.json()
        self.assertEqual(data["total_tabs"], 65)
        self.assertTrue(data["kept_tabs"][0]["interest_section"])
        for bucket in ("kept_tabs", "closed_tabs", "all_tabs"):
            for tab in data[bucket]:
                self.assertNotIn("full_text", tab)
                self.assertNotIn("all_paragraphs", tab)
                self.assertNotIn("synthesized_points", tab)

    def test_security_headers_and_gzip(self):
        index = self.client.get("/")
        self.assertEqual(index.status_code, 200)
        self.assertTrue(index.headers.get("content-security-policy"))
        self.assertEqual(index.headers.get("x-content-type-options"), "nosniff")
        self.assertEqual(index.headers.get("x-frame-options"), "DENY")
        self.assertEqual(index.headers.get("referrer-policy"), "strict-origin-when-cross-origin")

        gzipped = self.client.post(
            "/triage",
            headers={"Accept-Encoding": "gzip"},
            json={
                "preset_key": "dev_hall_of_fame_pile",
                "user_intent": "Gemini",
                "verify_live_browserbase": False,
                "include_gemma": False,
            },
        )
        self.assertEqual(gzipped.status_code, 200)
        self.assertEqual(gzipped.headers.get("content-encoding"), "gzip")
        self.assertEqual(gzipped.headers.get("cache-control"), "no-store")

    def test_triage_returns_private_polish_job_and_polish_endpoint_updates_it(self):
        fake = self._use_fake_polish()
        triage = self.client.post(
            "/triage",
            json={
                "preset_key": "postgres_docker_pile",
                "user_intent": "",
                "verify_live_browserbase": False,
                "include_gemma": False,
            },
        )
        self.assertEqual(triage.status_code, 200)
        data = triage.json()
        self.assertTrue(data["polish_available"])
        self.assertTrue(data["job_id"])
        self.assertEqual(len(data["polish_targets"]), 2)
        job = summary_job_store.get(data["job_id"])
        self.assertIsNotNone(job)
        self.assertNotIn("full_text", triage.text)
        self.assertEqual(data["kept_tabs"][0]["summary_source"], "deterministic")
        self.assertEqual(data["kept_tabs"][0]["polish_status"], "pending")

        first_id = data["polish_targets"][0]
        polished = self.client.post(
            "/polish-tabs",
            json={"job_id": data["job_id"], "tab_ids": [first_id]},
        )
        self.assertEqual(polished.status_code, 200)
        self.assertEqual(polished.headers.get("cache-control"), "no-store")
        polished_data = polished.json()
        self.assertEqual(polished_data["results"][first_id]["polish_status"], "polished")
        self.assertIn("minutes_saved", polished_data)

        cached = self.client.post(
            "/polish-tabs",
            json={"job_id": data["job_id"], "tab_ids": [first_id]},
        )
        self.assertEqual(cached.status_code, 200)
        self.assertEqual(cached.json()["results"][first_id]["polish_status"], "polished")
        self.assertEqual(fake.calls, 1)

    def test_polish_endpoint_rejects_unknown_expired_and_foreign_tabs(self):
        self._use_fake_polish()
        missing = self.client.post(
            "/polish-tabs",
            json={"job_id": "missing-job-id-1234567890", "tab_ids": ["user-tab-1"]},
        )
        self.assertEqual(missing.status_code, 404)

        job_id = self._create_minimal_job()
        foreign = self.client.post(
            "/polish-tabs",
            json={"job_id": job_id, "tab_ids": ["user-tab-999"]},
        )
        self.assertEqual(foreign.status_code, 422)

        summary_job_store.get(job_id)["expires_at"] = 0
        expired = self.client.post(
            "/polish-tabs",
            json={"job_id": job_id, "tab_ids": ["user-tab-1"]},
        )
        self.assertEqual(expired.status_code, 410)

    def test_polish_endpoint_returns_503_when_model_unavailable(self):
        fake = self._use_fake_polish()
        job_id = self._create_minimal_job()
        fake.available = False
        resp = self.client.post(
            "/polish-tabs",
            json={"job_id": job_id, "tab_ids": ["user-tab-1"]},
        )
        self.assertEqual(resp.status_code, 503)

    def test_polish_pending_response_never_serializes_private_tab_content(self):
        self._use_fake_polish()
        job_id = self._create_minimal_job()
        self.assertTrue(summary_job_store.begin_polish(job_id, "user-tab-1"))
        resp = self.client.post(
            "/polish-tabs",
            json={"job_id": job_id, "tab_ids": ["user-tab-1"]},
        )
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["pending_count"], 1)
        self.assertNotIn("PRIVATE FULL ARTICLE TEXT", resp.text)
        self.assertNotIn("PRIVATE PARAGRAPH", resp.text)
        self.assertNotIn("full_text", payload["results"]["user-tab-1"])
        self.assertNotIn("all_paragraphs", payload["results"]["user-tab-1"])
        self.assertEqual(payload["results"]["user-tab-1"]["polish_status"], "pending")


if __name__ == "__main__":
    unittest.main()
