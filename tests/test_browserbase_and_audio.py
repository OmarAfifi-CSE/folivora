"""
test_browserbase_and_audio.py - Unit & live integration tests for Browserbase & ElevenLabs services
"""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

load_dotenv(ROOT_DIR / ".env")

from audio_service import AudioService
from browserbase_service import (
    BrowserbaseService,
    CDPError,
    _CDPConnection,
    _cdp_extract_url,
    compute_intent_match,
    extract_clean_urls_from_list,
    extract_focus_tokens,
    fast_http_extract_url,
)


class TestBrowserbaseAndAudio(unittest.TestCase):
    def test_compute_intent_match_boosts_matching_code_tabs(self):
        code_tab = {
            "title": "pgvector HNSW Index Parameters",
            "snippet": "CREATE INDEX ON items USING hnsw",
            "domain": "github.com",
            "code_block_count": 9,
        }
        essay_tab = {
            "title": "Why Software Complexity Is Hard",
            "snippet": "A long philosophical essay",
            "domain": "blog.example.org",
            "code_block_count": 0,
        }
        intent = "keep pgvector hnsw index command"
        s_code = compute_intent_match(code_tab, intent)
        s_essay = compute_intent_match(essay_tab, intent)
        self.assertGreater(s_code, 0.70)
        self.assertLess(s_essay, 0.30)

    def test_browserbase_live_session_create_and_release(self):
        bb = BrowserbaseService()
        if not bb.is_configured:
            self.skipTest("Browserbase credentials not configured")
        res = bb.inspect_tabs(
            preset_key="postgres_docker_pile",
            verify_live_browserbase=True,
        )
        self.assertTrue(res["browserbase"]["live_session_created"])
        self.assertIsNotNone(res["browserbase"]["session_id"])
        self.assertTrue(res["browserbase"]["session_released"])

    @patch("audio_service.requests.delete")
    @patch("audio_service.requests.post")
    def test_scribe_transcribe_and_immediate_privacy_delete(self, mock_post, mock_delete):
        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 200
        mock_post_resp.json.return_value = {
            "text": "Keep only the pgvector HNSW index tabs and close the essays.",
            "transcription_id": "tr_987654321",
        }
        mock_post.return_value = mock_post_resp

        mock_del_resp = MagicMock()
        mock_del_resp.status_code = 200
        mock_delete.return_value = mock_del_resp

        svc = AudioService(api_key="test_key")
        out = svc.transcribe_and_forget(b"fake_webm_audio_bytes")
        self.assertEqual(out["text"], "Keep only the pgvector HNSW index tabs and close the essays.")
        self.assertTrue(out["remote_deleted"])
        mock_delete.assert_called_once()
        self.assertIn("tr_987654321", mock_delete.call_args[0][0])

    def test_audio_service_rejects_empty_text_or_missing_key(self):
        svc_no_key = AudioService(api_key="")
        with self.assertRaises(RuntimeError):
            svc_no_key.synthesize_speech("Hello world")
        svc_with_key = AudioService(api_key="dummy")
        with self.assertRaises(RuntimeError):
            svc_with_key.synthesize_speech("   ")

    def test_live_elevenlabs_flash_tts_returns_mp3_bytes(self):
        svc = AudioService()
        if not svc.is_configured:
            self.skipTest("ELEVENLABS_API_KEY not configured")
        mp3_bytes = svc.synthesize_speech(
            "Closed 9 of 14 tabs below the P90 threshold, saving 152.9 minutes."
        )
        self.assertIsInstance(mp3_bytes, bytes)
        self.assertGreater(len(mp3_bytes), 2000)

    def test_browserbase_graceful_fallback_when_unconfigured(self):
        bb = BrowserbaseService(api_key="", project_id="")
        self.assertFalse(bb.is_configured)
        res = bb.inspect_tabs(preset_key="postgres_docker_pile", verify_live_browserbase=True)
        self.assertFalse(res["browserbase"]["live_session_created"])
        self.assertEqual(len(res["tabs"]), 14)

    def test_extract_clean_urls_splits_glued_urls(self):
        glued = [
            "https://dev.to/gramli/fine-tuning-qwen3-8b-48mhhttps://dev.to/omarafifi/reviving-daphq-5cd8"
        ]
        out = extract_clean_urls_from_list(glued)
        self.assertEqual(
            out,
            [
                "https://dev.to/gramli/fine-tuning-qwen3-8b-48mh",
                "https://dev.to/omarafifi/reviving-daphq-5cd8",
            ],
        )

    @patch("browserbase_service.requests.get")
    def test_fast_http_extract_preserves_multiline_pre_and_extracts_takeaways(self, mock_get):
        long_dart_code = "\n".join([f"final line{i} = socket.add(chunk{i}); // streaming chunk" for i in range(10)])
        sample_html = f"""
        <html>
          <head><title>Reviving Daphq: Bare-Metal Engine - DEV Community</title></head>
          <body>
            <main>
              <p>Posted on May 27</p>
              <p>Daphq went from an unpolished repository artifact to a production-grade open-source engine capable of hitting 80-90 MB/s over local Wi-Fi.</p>
              <p>Memory Safety and Disk I/O Flow Control: Before v1.0.0 there was high risk of Out-Of-Memory crashes when streaming multi-gigabyte files.</p>
              <p>Inline token <code>SocketOption.tcpNoDelay</code> should never replace a real pre block.</p>
              <pre><code>{long_dart_code}</code></pre>
            </main>
          </body>
        </html>
        """
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = sample_html
        mock_get.return_value = mock_resp

        tab = fast_http_extract_url("https://dev.to/omarafifi/reviving-daphq", idx=0)
        self.assertEqual(tab["title"], "Reviving Daphq: Bare-Metal Engine")
        self.assertGreaterEqual(len(tab["takeaways"]), 2)
        self.assertTrue(any("80-90 MB/s" in tk for tk in tab["takeaways"]))

    def test_flutter_focus_strictly_separates_flutter_tab_from_unrelated_llm_tab(self):
        from tabpfn_engine import TabPFNQuantileEngine

        flutter_tab = {
            "id": "daphq",
            "title": "Reviving Daphq: Bare-Metal File Transfer Engine",
            "url": "https://dev.to/omarafifi/reviving-daphq",
            "domain": "dev.to",
            "age_days": 1.2,
            "word_count": 1292,
            "code_block_count": 5,
            "table_count": 2,
            "heading_count": 12,
            "link_density": 2.4,
            "overview": "Built with Flutter and Dart using raw TCP sockets.",
            "takeaways": ["Reaches 80-90 MB/s in Flutter.", "Maintains 60 FPS UI in Dart."],
            "topics": ["Flutter", "Dart", "TCP / Networking"],
            "full_text": "Flutter Dart cross-platform file transfer engine widget state management.",
        }
        qwen_tab = {
            "id": "qwen",
            "title": "Fine-Tuning Qwen3-8B with Tinker: From 50% to 76% Accuracy",
            "url": "https://dev.to/gramli/fine-tuning-qwen3-8b",
            "domain": "dev.to",
            "age_days": 2.0,
            "word_count": 5867,
            "code_block_count": 8,
            "table_count": 3,
            "heading_count": 22,
            "link_density": 2.8,
            "overview": "Fine-tuning Qwen3-8B in Python on Tinker.",
            "takeaways": ["Achieved 76% accuracy on 2,500 examples."],
            "topics": ["LLM / AI", "Fine-Tuning", "Tinker", "Qwen"],
            "full_text": "Fine-tuning Qwen3-8B LLM in Python with Thinking Machines Tinker.",
        }

        flutter_tab["intent_match_score"] = compute_intent_match(flutter_tab, "flutter", is_custom_mode=True)
        qwen_tab["intent_match_score"] = compute_intent_match(qwen_tab, "flutter", is_custom_mode=True)

        self.assertGreater(flutter_tab["intent_match_score"], 0.70)
        self.assertLessEqual(qwen_tab["intent_match_score"], 0.05)

        eng = TabPFNQuantileEngine(context_rows=160)
        res = eng.evaluate_tab_pile([flutter_tab, qwen_tab])
        by_id = {t["id"]: t for t in res["all_tabs"]}

        self.assertGreater(by_id["daphq"]["p50_score"], 80.0)
        self.assertEqual(by_id["daphq"]["verdict"], "KEEP_EXTRACT")
        self.assertLess(by_id["qwen"]["p50_score"], 20.0)
        self.assertEqual(by_id["qwen"]["verdict"], "SAFE_CLOSE")
        self.assertIn("0 mentions of 'flutter'", by_id["qwen"]["verdict_reason"])

    def test_natural_language_sentence_i_am_interested_in_flutter_development(self):
        # Regression test for "I am interested in Flutter development":
        # Must extract ONLY ['flutter'] and never match 'am', 'in', or 'development'!
        tokens = extract_focus_tokens("I am interested in Flutter development")
        self.assertEqual(tokens, ["flutter"])

        qwen_tab = {
            "id": "qwen",
            "title": "Fine-Tuning Qwen3-8B with Tinker: From 50% to 76% Accuracy",
            "url": "https://dev.to/gramli/fine-tuning-qwen3-8b",
            "domain": "dev.to",
            "age_days": 2.0,
            "word_count": 2604,
            "code_block_count": 8,
            "table_count": 3,
            "heading_count": 21,
            "link_density": 2.8,
            "overview": "I am in a software development challenge fine-tuning Qwen3-8B in Python.",
            "takeaways": ["Achieved 76% accuracy in development."],
            "topics": ["AI & Fine-Tuning", "Machine Learning"],
            "full_text": "I am in software development fine-tuning Qwen3-8B LLM in Python with Tinker.",
        }
        score = compute_intent_match(qwen_tab, "I am interested in Flutter development", is_custom_mode=True)
        self.assertLessEqual(score, 0.05)
        self.assertEqual(qwen_tab["topic_mentions"], 0)
        self.assertIsNone(qwen_tab.get("interest_section"))
        self.assertNotIn("am, in", qwen_tab["match_explanation"])

    def test_two_section_architecture_and_passing_mention_rejection(self):
        daphq_tab = {
            "id": "daphq",
            "title": "Reviving Daphq: Bare-Metal File Transfer Engine",
            "url": "https://dev.to/omarafifi/reviving-daphq",
            "domain": "dev.to",
            "age_days": 1.2,
            "word_count": 1198,
            "code_block_count": 5,
            "table_count": 2,
            "heading_count": 11,
            "link_density": 2.4,
            "overview": "Daphq is a cross-platform Flutter and Dart file transfer application.",
            "takeaways": [
                "Built with Flutter and Dart to stream chunks over raw TCP sockets.",
                "Saves physical RAM and transfers files to my laptop.",
            ],
            "topics": ["Networking & P2P", "Flutter & Dart"],
            "full_text": "Flutter Dart cross-platform file transfer engine saves physical RAM and transfers files to my laptop.",
        }

        # 1. Searching "laptop buying" should NOT match a Flutter networking post that only says "laptop" once in passing!
        score_laptop = compute_intent_match(daphq_tab, "laptop buying", is_custom_mode=True)
        self.assertLessEqual(score_laptop, 0.05)
        self.assertIsNone(daphq_tab.get("interest_section"))

        # 2. Searching "Flutter" on the same tab SHOULD match and populate Section 2 (`interest_section`)!
        score_flutter = compute_intent_match(daphq_tab, "I am interested in Flutter development", is_custom_mode=True)
        self.assertGreater(score_flutter, 0.70)
        self.assertIsNotNone(daphq_tab.get("interest_section"))
        self.assertTrue(daphq_tab["interest_section"]["active"])
        self.assertGreater(len(daphq_tab["interest_section"]["tailored_points"]), 0)

    def test_dev_hall_of_fame_65_masterpieces_and_section_anchored_synthesis(self):
        bb = BrowserbaseService()
        # 1. Verify all 65 DEV Hall of Fame exhibits load and filter cleanly
        hof_res = bb.inspect_tabs(
            preset_key="dev_hall_of_fame_pile",
            user_intent="Gemini",
            verify_live_browserbase=False,
        )
        self.assertEqual(len(hof_res["tabs"]), 65)
        phone_tab = next(
            t for t in hof_res["tabs"]
            if "my-phone-broke-and-gemini-fixed-it" in t["url"]
        )
        self.assertGreater(phone_tab["intent_match_score"], 0.85)
        self.assertEqual(phone_tab["wing"], "The Engine Room")
        self.assertIsNotNone(phone_tab["interest_section"])
        joined_phone_takeaways = " ".join(phone_tab["takeaways"])
        self.assertNotIn("Hey, you ran some ADB commands", joined_phone_takeaways)
        self.assertNotIn("\U0001f916", joined_phone_takeaways)

        # 2. Verify Reviving Daphq + Conference Speaker extraction & section-anchored synthesis
        custom_res = bb.inspect_tabs(
            custom_urls=[
                "https://dev.to/omarafifi/reviving-daphq-how-i-built-an-open-source-bare-metal-file-transfer-engine-solo-with-github-copilot-5cd8",
                "https://dev.to/sylwia-lask/how-to-become-a-tech-conference-speaker-and-why-you-might-regret-it-2ink",
                "https://dev.to/georgekobaidze/promise-delivered-welcome-to-the-dev-hall-of-fame-65-authors-65-masterpieces-15fa",
            ],
            user_intent="Flutter",
            verify_live_browserbase=False,
        )
        daphq, speaker, roundup = custom_res["tabs"][0], custom_res["tabs"][1], custom_res["tabs"][2]

        # Daphq must have section-anchored tailored points with 'ValueKeys' (never 'ValueKey s')
        self.assertIsNotNone(daphq["interest_section"])
        joined_pts = " ".join(daphq["interest_section"]["tailored_points"])
        self.assertIn("ValueKeys", joined_pts)
        self.assertNotIn("ValueKey s", joined_pts)
        for pt in daphq["interest_section"]["tailored_points"]:
            self.assertIn(" — ", pt)

        # Speaker article must have real topics ('Career & Growth', 'Public Speaking', 'Tech Conferences'), never 'Become'
        self.assertNotIn("Become", speaker["topics"])
        self.assertIn("Career & Growth", speaker["topics"])
        self.assertIsNone(speaker["interest_section"])

        # Hall of Fame roundup URL must be flagged as a 65-item collection roundup
        self.assertTrue(roundup.get("is_collection_roundup"))
        self.assertEqual(roundup.get("collection_count"), 65)

    @staticmethod
    def _sample_tab(url="https://example.com"):
        return {
            "id": "user-tab-1",
            "title": "Example Domain",
            "url": url,
            "domain": "example.com",
            "age_days": 1.0,
            "word_count": 133,
            "code_block_count": 0,
            "table_count": 0,
            "heading_count": 1,
            "link_density": 1.0,
            "overview": "A small example page.",
            "takeaways": ["Example Domain — A reserved documentation hostname."],
            "synthesized_points": [],
            "all_paragraphs": [],
            "topics": ["example"],
            "full_text": "Example Domain",
            "snippet": "A small example page.",
            "fact_numbers": ["1"],
            "is_broken_404": False,
        }

    def test_cdp_connection_sends_flattened_session_messages(self):
        fake_ws = MagicMock()
        fake_ws.recv.return_value = json.dumps({"id": 1, "result": {"ok": True}})
        conn = _CDPConnection.__new__(_CDPConnection)
        conn._ws = fake_ws
        conn._next_id = 0

        result = conn.call("Runtime.evaluate", {"expression": "document.readyState"}, session_id="session-123")
        self.assertTrue(result["ok"])
        payload = json.loads(fake_ws.send.call_args[0][0])
        self.assertEqual(payload["id"], 1)
        self.assertEqual(payload["sessionId"], "session-123")
        self.assertEqual(payload["method"], "Runtime.evaluate")

    def test_cdp_connection_surfaces_protocol_errors(self):
        fake_ws = MagicMock()
        fake_ws.recv.return_value = json.dumps({"id": 1, "error": {"message": "Target detached"}})
        conn = _CDPConnection.__new__(_CDPConnection)
        conn._ws = fake_ws
        conn._next_id = 0
        with self.assertRaises(CDPError):
            conn.call("Page.navigate", {"url": "https://example.com"})

    @patch("browserbase_service._cdp_fetch_rendered_html")
    def test_cdp_rendered_html_reuses_section_aware_extractor(self, mock_fetch):
        mock_fetch.return_value = """
        <html><head><title>Streaming Engine Deep Dive - DEV Community</title></head>
        <body><main>
          <p>This streaming engine moves multi-gigabyte files over local Wi-Fi with bounded memory.</p>
          <p>Backpressure and socket flow control keep the transfer stable under load.</p>
          <h2>Memory Safety</h2>
          <p>Bounded queues prevent Out-Of-Memory failures when the receiver falls behind.</p>
        </main></body></html>
        """
        tab = _cdp_extract_url("https://example.com/post", 0, "wss://example.invalid")
        self.assertEqual(tab["title"], "Streaming Engine Deep Dive")
        self.assertGreater(tab["word_count"], 20)
        self.assertTrue(tab["takeaways"])

    @patch.object(BrowserbaseService, "release_session", return_value=True)
    @patch.object(BrowserbaseService, "create_session")
    @patch("browserbase_service._cdp_extract_url")
    def test_custom_live_urls_use_cdp_and_release_session(self, mock_cdp, mock_create, mock_release):
        mock_create.return_value = {
            "session_id": "sess-123",
            "ws_url": "wss://connect.browserbase.com/debug/sess-123",
            "connect_url": "",
            "debugger_url": "https://www.browserbase.com/devtools/sess-123",
            "replay_url": "https://www.browserbase.com/sessions/sess-123",
        }
        mock_cdp.return_value = self._sample_tab()
        service = BrowserbaseService(api_key="key", project_id="project")
        result = service.inspect_tabs(
            custom_urls=["https://example.com"],
            verify_live_browserbase=True,
        )
        self.assertEqual(result["browserbase"]["cdp_pages_inspected"], 1)
        self.assertEqual(result["browserbase"]["session_id"], "sess-123")
        self.assertTrue(result["browserbase"]["session_released"])
        self.assertEqual(len(result["tabs"]), 1)
        mock_cdp.assert_called_once()
        mock_release.assert_called_once_with("sess-123")

    @patch.object(BrowserbaseService, "release_session", return_value=True)
    @patch.object(BrowserbaseService, "create_session")
    @patch("browserbase_service.fast_http_extract_url")
    @patch("browserbase_service._cdp_extract_url", side_effect=CDPError("socket exploded"))
    def test_custom_live_failure_falls_back_to_http_without_leaking_session(
        self, mock_cdp, mock_fast, mock_create, mock_release
    ):
        mock_create.return_value = {
            "session_id": "sess-456",
            "ws_url": "wss://connect.browserbase.com/debug/sess-456",
            "connect_url": "",
            "debugger_url": "https://www.browserbase.com/devtools/sess-456",
            "replay_url": "https://www.browserbase.com/sessions/sess-456",
        }
        mock_fast.return_value = self._sample_tab()
        service = BrowserbaseService(api_key="key", project_id="project")
        result = service.inspect_tabs(
            custom_urls=["https://example.com"],
            verify_live_browserbase=True,
        )
        self.assertEqual(result["browserbase"]["cdp_pages_inspected"], 0)
        self.assertIn("CDP extraction failed", result["browserbase"]["fallback_reason"])
        self.assertTrue(result["browserbase"]["session_released"])
        mock_cdp.assert_called_once()
        mock_fast.assert_called_once()
        mock_release.assert_called_once_with("sess-456")

    def test_facebook_error_shell_is_labeled_login_walled_not_summarized(self):
        fb_html = (
            "<html><head><title>Error</title></head><body>"
            "<p>You must log in to continue.</p>"
            "<p>We’ll get this fixed as soon as we can.</p>"
            "</body></html>"
        )
        tab = fast_http_extract_url(
            "https://www.facebook.com/watch",
            1,
            prefetched_html=fb_html,
        )
        self.assertTrue(tab["is_login_walled"])
        self.assertEqual(tab["topics"], ["Login Wall"])
        self.assertNotIn("is_broken_404", {k for k in tab if tab[k] is True})
        # The error-shell prose must never leak into the takeaways.
        joined = " ".join(tab["takeaways"]).lower()
        self.assertNotIn("get this fixed", joined)

    def test_normal_content_page_is_never_flagged_as_login_walled(self):
        body_words = " ".join(f"word{i}" for i in range(400))
        ok_html = (
            "<html><head><title>A Genuine Long Article</title></head><body>"
            f"<p>{body_words}</p></body></html>"
        )
        tab = fast_http_extract_url(
            "https://blog.example.com/genuine",
            1,
            prefetched_html=ok_html,
        )
        self.assertFalse(tab["is_login_walled"])
        self.assertGreater(tab["word_count"], 300)

    def test_cloudflare_bot_check_shell_is_labeled_login_walled(self):
        cf_html = (
            "<html><head><title>Just a moment...</title></head><body>"
            "<p>Checking your browser before accessing the site.</p>"
            "</body></html>"
        )
        tab = fast_http_extract_url(
            "https://protected.example.com/docs",
            1,
            prefetched_html=cf_html,
        )
        self.assertTrue(tab["is_login_walled"])
        self.assertEqual(tab["wall_label"], "bot check")

    def test_youtube_consent_wall_is_labeled_not_summarized(self):
        # Cookie-less fetches of youtube.com receive Google's "Before you
        # continue to YouTube" consent shell; it must never be summarized
        # as if it were the video's page.
        yt_html = (
            "<html><head><title>Before you continue to YouTube</title></head><body>"
            "<p>Our systems have detected unusual traffic from your computer network.</p>"
            "</body></html>"
        )
        tab = fast_http_extract_url(
            "https://www.youtube.com/watch",
            1,
            prefetched_html=yt_html,
        )
        self.assertTrue(tab["is_login_walled"])
        self.assertIn(tab["wall_label"], ("consent wall", "bot check"))

    def test_javascript_shell_page_is_labeled_unreadable(self):
        # YouTube video pages served to a cookie-less, script-less fetcher can
        # arrive as a bare JS shell with no wall phrases at all — raw text under
        # the content floor must still be caught instead of "summarized".
        shell_html = (
            "<html><head><title>YouTube</title></head><body>"
            "<div id=app></div><noscript>Enable JavaScript</noscript>"
            "</body></html>"
        )
        tab = fast_http_extract_url(
            "https://www.youtube.com/watch",
            1,
            prefetched_html=shell_html,
        )
        self.assertTrue(tab["is_login_walled"])
        self.assertEqual(tab["wall_label"], "empty shell")
        self.assertTrue(tab["title"].startswith("Unreadable page:"))


if __name__ == "__main__":
    unittest.main()
