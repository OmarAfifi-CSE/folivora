"""
test_summary_service.py - Grounding, validation, fallback, cache, and job-store tests.
"""

import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from job_store import SummaryCache, SummaryJobStore
from summary_service import (
    TabSummaryService,
    build_evidence_pack,
    build_tab_prompt,
    validate_tab_summary,
)

E = "\u2014"
EVIDENCE_ITEMS = [
    f"Queue Design {E} The team split producers from consumers, added an explicit acknowledgement step, kept retry policy visible to every service owner, and documented the handoff contract so downstream teams could reason about duplicate delivery without reading the implementation.",
    f"Failure Recovery {E} Durable checkpoints let workers resume after crashes, while dead-letter queues isolated malformed payloads for later inspection, and a replay tool let operators reprocess selected messages without rebuilding the entire pipeline.",
    f"Latency Budget {E} Profiling moved the p95 wait from 800 ms to 240 ms by batching writes, removing a redundant serialization pass from the hot path, and caching the schema lookup that previously ran on every single message.",
    f"Rollout Plan {E} A shadow consumer ran beside production for two weeks, comparing message counts, flagging mismatches before the final cutover, and giving the team a rollback switch that could restore the old path within minutes.",
    f"Observability {E} Dashboard panels tracked queue depth, consumer lag, acknowledgement failures, and replay volume, while alert thresholds distinguished a slow drain from a genuinely stalled worker pool during the two-week rollout period.",
    f"Handoff Contract {E} Runbooks named the owner of every alert, documented safe replay limits, and recorded the exact commands for pausing a consumer without dropping an in-flight message before anyone rotates on call.",
]
VALID_OVERVIEW = (
    "This engineering case study explains how the team rebuilt a message queue, separated producers "
    "from consumers, added durable checkpoints, cut median processing delay, and validated the migration "
    "with a shadow consumer before the final cutover."
)


def sample_tab() -> dict:
    return {
        "id": "user-tab-1",
        "title": "Rebuilding a Queue Without Losing Messages",
        "url": "https://example.com/queue",
        "domain": "example.com",
        "overview": (
            "A practical account of rebuilding an ingestion queue while keeping message loss at zero "
            "and documenting the operational tradeoffs."
        ),
        "takeaways": EVIDENCE_ITEMS,
        "synthesized_points": EVIDENCE_ITEMS,
        "all_paragraphs": EVIDENCE_ITEMS,
        "full_text": " ".join(EVIDENCE_ITEMS),
        "code_blocks": ["def consume(message): ack(message); return message"],
        "interest_section": {
            "active": True,
            "headline": 'Focused on "queue reliability"',
            "why_relevant": (
                "The page treats queue reliability as a working concern rather than a passing mention, "
                "with concrete design, recovery, and rollout evidence."
            ),
            "tailored_points": EVIDENCE_ITEMS[:3],
            "mention_count": 14,
        },
    }


GENERAL_BODIES = [
    "The article explains how {heading} works in practice, naming the constraint the team faced, the decision they made, and the check that kept the rollout defensible for reviewers.",
    "A second lesson from {heading} is that delivery speed depends on explicit boundaries, observable failure modes, and a rollback path the team can rehearse before traffic moves.",
    "The write-up connects {heading} to the implementation tradeoff, showing which measurement changed the decision and which assumption the team tested before scaling it. That evidence keeps the recommendation specific.",
    "Finally, {heading} shows how the team balanced reliability with operational cost, using a concrete verification step instead of a broad claim about best practice. The result becomes a repeatable operating habit.",
]
POINT_BODIES = [
    "For a queue reliability priority, {heading} becomes a practical check: define the ownership boundary, observe the failure mode, and verify the rollback before traffic moves. The goal is a decision the team can defend.",
    "The focused takeaway from {heading} is to test the assumption against production evidence, then document the constraint so the next on-call engineer can act without guessing.",
]


def rewritten_text(item: dict, index: int) -> str:
    body = GENERAL_BODIES[index % len(GENERAL_BODIES)].format(heading=item["heading"])
    return f"{item['heading']} \u2014 {body}"


def make_draft(pack: dict, *, rated: bool = False) -> dict:
    by_heading = {item["heading"]: item for item in pack["evidence"]}
    general_headings = ["Queue Design", "Failure Recovery", "Latency Budget", "Rollout Plan"]
    general = [by_heading[heading] for heading in general_headings if heading in by_heading]
    if len(general) < 4:
        general = pack["evidence"][:4]
    takeaways = [
        {
            "text": rewritten_text(item, index),
            "evidence_id": item["id"],
            "anchor": " ".join(item["text"].split()[:8]),
        }
        for index, item in enumerate(general)
    ]
    draft = {
        "overview": VALID_OVERVIEW,
        "overview_evidence_ids": [general[0]["id"]],
        "takeaways": takeaways,
        "interest": None,
    }
    if rated:
        focus_items = [
            by_heading[heading]
            for heading in ("Observability", "Handoff Contract")
            if heading in by_heading
        ]
        draft["interest"] = {
            "why_relevant": (
                "The article connects queue reliability to concrete producer and consumer boundaries, "
                "durable recovery behavior after worker crashes, and a rollout plan that protects live "
                "traffic throughout the migration."
            ),
            "why_evidence_ids": [focus_items[0]["id"]],
            "points": [
                {
                    "text": (
                        f"{item['heading']} \u2014 "
                        + POINT_BODIES[index % len(POINT_BODIES)].format(
                            heading=item["heading"]
                        )
                    ),
                    "evidence_id": item["id"],
                    "anchor": " ".join(item["text"].split()[:8]),
                }
                for index, item in enumerate(focus_items[:2])
            ],
        }
    return draft


class FakeGenerator:
    def __init__(self, responses, model="fake/gemma"):
        self.responses = list(responses)
        self.model = model
        self.calls = 0

    def __call__(self, prompt, *, max_tokens, temperature, timeout_s):
        self.calls += 1
        response = self.responses.pop(0) if self.responses else None
        if response is None:
            return None, "fake/no-provider", 0, 0
        return json.dumps(response), self.model, 100, 80


class TestEvidenceAndValidation(unittest.TestCase):
    def setUp(self):
        self.tab = sample_tab()
        self.pack = build_evidence_pack(self.tab, rated=False, intent="")

    def test_evidence_pack_is_grounded_and_bounded(self):
        self.assertGreaterEqual(len(self.pack["evidence"]), 4)
        self.assertEqual(self.pack["evidence"][0]["id"], "E1")
        self.assertIn("Queue Design", self.pack["section_headings"])
        self.assertIn("800", self.pack["allowed_numbers"])
        self.assertIn(["800", "ms"], self.pack["allowed_number_units"])
        self.assertTrue(self.pack["code_blocks"])
        self.assertEqual(len(self.pack["content_hash"]), 64)

    def test_validator_accepts_grounded_unrated_draft(self):
        ok, reasons, result = validate_tab_summary(make_draft(self.pack), self.pack)
        self.assertTrue(ok, reasons)
        self.assertIsNotNone(result)
        self.assertEqual(result["summary_source"], "gemma_verified")
        self.assertFalse(result.get("interest_section"))

    def test_validator_rejects_unsupported_number_and_unit(self):
        draft = make_draft(self.pack)
        draft["takeaways"][0]["text"] += " The queue handled 47 GB overnight."
        ok, reasons, _ = validate_tab_summary(draft, self.pack)
        self.assertFalse(ok)
        self.assertTrue(any("unsupported_number:47" in reason for reason in reasons))
        self.assertTrue(any("unsupported_unit:47GB" in reason for reason in reasons))

    def test_validator_rejects_quote_only_and_duplicate_points(self):
        draft = make_draft(self.pack)
        draft["takeaways"][0]["text"] = (
            'Queue Design \u2014 "The team split producers from consumers and added an acknowledgement step '
            "that kept every retry policy visible to service owners during the migration.\""
        )
        ok, reasons, _ = validate_tab_summary(draft, self.pack)
        self.assertFalse(ok)
        self.assertTrue(any("quote_only" in reason for reason in reasons))

        draft = make_draft(self.pack)
        draft["takeaways"][1]["text"] = draft["takeaways"][0]["text"]
        ok, reasons, _ = validate_tab_summary(draft, self.pack)
        self.assertFalse(ok)
        self.assertTrue(any("duplicate_general_takeaways" in reason for reason in reasons))

    def test_validator_rejects_verbatim_evidence_reuse(self):
        draft = make_draft(self.pack)
        draft["takeaways"][0]["text"] = self.pack["evidence"][0]["text"]
        ok, reasons, _ = validate_tab_summary(draft, self.pack)
        self.assertFalse(ok)
        self.assertIn("takeaway_1_verbatim_copy", reasons)

    def test_validator_requires_exact_anchor(self):
        draft = make_draft(self.pack)
        draft["takeaways"][0]["anchor"] = "this phrase is not in the source evidence"
        ok, reasons, _ = validate_tab_summary(draft, self.pack)
        self.assertFalse(ok)
        self.assertIn("takeaway_1_anchor_not_exact", reasons)

    def test_validator_accepts_rated_draft_with_focus(self):
        tab = sample_tab()
        pack = build_evidence_pack(tab, rated=True, intent="queue reliability")
        ok, reasons, result = validate_tab_summary(make_draft(pack, rated=True), pack)
        self.assertTrue(ok, reasons)
        self.assertTrue(result["interest_section"]["active"])
        self.assertEqual(result["interest_section"]["mention_count"], 14)


class TestTabSummaryService(unittest.TestCase):
    def test_service_retries_then_returns_verified_summary(self):
        pack = build_evidence_pack(sample_tab(), rated=False, intent="")
        generator = FakeGenerator([{}, make_draft(pack)])
        service = TabSummaryService(
            available=True,
            text_generator=generator,
            cache=SummaryCache(ttl_s=60, max_entries=4),
        )
        result = service.polish_tab(sample_tab(), rated=False, intent="", shared_cache_allowed=False)
        self.assertEqual(result["summary_source"], "gemma_verified")
        self.assertEqual(result["summary_validation"]["attempts"], 2)
        self.assertEqual(generator.calls, 2)
        self.assertNotIn("full_text", result)

    def test_service_repairs_anchor_and_labels_fast_provider(self):
        pack = build_evidence_pack(sample_tab(), rated=False, intent="")
        draft = make_draft(pack)
        draft["takeaways"][0]["anchor"] = "not a grounded anchor at all"
        generator = FakeGenerator([draft], model="google/gemini-3.1-flash-lite")
        service = TabSummaryService(
            available=True,
            text_generator=generator,
            cache=SummaryCache(ttl_s=60, max_entries=4),
        )
        result = service.polish_tab(sample_tab(), rated=False, intent="")
        self.assertEqual(result["summary_source"], "gemini_verified")
        self.assertEqual(result["polish_status"], "polished")
        self.assertEqual(result["summary_validation"]["model"], "google/gemini-3.1-flash-lite")

    def test_service_drops_invented_interest_for_unrated_page(self):
        pack = build_evidence_pack(sample_tab(), rated=False, intent="")
        draft = make_draft(pack, rated=True)
        service = TabSummaryService(
            available=True,
            text_generator=FakeGenerator([draft]),
            cache=SummaryCache(ttl_s=60, max_entries=4),
        )
        result = service.polish_tab(sample_tab(), rated=False, intent="")
        self.assertEqual(result["polish_status"], "polished")
        self.assertNotIn("interest_section", result)

    def test_service_repairs_a_first_person_bullet(self):
        pack = build_evidence_pack(sample_tab(), rated=False, intent="")
        draft = make_draft(pack)
        valid_text = draft["takeaways"][0]["text"]
        draft["takeaways"][0]["text"] = (
            'Queue Design \u2014 "The producer boundary, explicit acknowledgement step, '
            "visible retry policy, and documented handoff contract let every downstream "
            'owner reason about duplicate deliveries during the migration."'
        )
        generator = FakeGenerator(
            [
                draft,
                {"texts": [{"label": "takeaway_1", "text": valid_text}]},
            ]
        )
        service = TabSummaryService(
            available=True,
            text_generator=generator,
            cache=SummaryCache(ttl_s=60, max_entries=4),
        )
        result = service.polish_tab(sample_tab(), rated=False, intent="")
        self.assertEqual(result["polish_status"], "polished")
        self.assertEqual(result["summary_validation"]["attempts"], 2)
        self.assertEqual(generator.calls, 2)

    def test_prompt_v2_requires_explicit_word_counts(self):
        pack = build_evidence_pack(sample_tab(), rated=False, intent="")
        prompt = build_tab_prompt(pack, version="tab-summary-v2")
        self.assertIn("32-42 words", prompt)
        self.assertIn("38-46 words", prompt)

    def test_service_uses_shared_cache(self):
        pack = build_evidence_pack(sample_tab(), rated=False, intent="")
        generator = FakeGenerator([make_draft(pack)])
        service = TabSummaryService(
            available=True,
            text_generator=generator,
            cache=SummaryCache(ttl_s=60, max_entries=4),
        )
        first = service.polish_tab(
            sample_tab(), rated=False, intent="", shared_cache_allowed=True
        )
        second = service.polish_tab(
            sample_tab(), rated=False, intent="", shared_cache_allowed=True
        )
        self.assertEqual(generator.calls, 1)
        self.assertFalse(first["summary_validation"]["cache_hit"])
        self.assertTrue(second["summary_validation"]["cache_hit"])

    def test_service_falls_back_to_deterministic_summary(self):
        service = TabSummaryService(
            available=True,
            text_generator=FakeGenerator([None]),
            cache=SummaryCache(ttl_s=60, max_entries=4),
        )
        result = service.polish_tab(sample_tab(), rated=False, intent="")
        self.assertEqual(result["summary_source"], "deterministic_fallback")
        self.assertEqual(result["polish_status"], "fallback")
        self.assertIn("provider_unavailable", result["summary_validation"]["reasons"])
        self.assertTrue(result["overview"])
        self.assertNotIn("full_text", result)

    def test_unavailable_service_does_not_call_provider(self):
        generator = FakeGenerator([make_draft(build_evidence_pack(sample_tab(), rated=False, intent=""))])
        service = TabSummaryService(
            available=False,
            text_generator=generator,
            cache=SummaryCache(ttl_s=60, max_entries=4),
        )
        result = service.polish_tab(sample_tab(), rated=False, intent="")
        self.assertEqual(result["summary_source"], "deterministic_fallback")
        self.assertEqual(generator.calls, 0)


class TestSummaryConfigAndTracing(unittest.TestCase):
    def test_canonical_polish_env_overrides_legacy_aliases(self):
        env = {
            "SUMMARY_POLISH_ENABLED": "true",
            "SUMMARY_POLISH_LIMIT": "3",
            "SUMMARY_POLISH_MAX_TABS": "7",
            "SUMMARY_POLISH_BATCH_BUDGET_S": "9",
            "SUMMARY_POLISH_BUDGET_S": "11",
            "SUMMARY_POLISH_CONCURRENCY": "2",
            "SUMMARY_POLISH_TIMEOUT_S": "4",
            "SUMMARY_CACHE_TTL_S": "120",
        }
        with patch.dict(os.environ, env, clear=True):
            service = TabSummaryService(available=True, text_generator=FakeGenerator([]))
        self.assertTrue(service.available)
        self.assertEqual(service.max_tabs, 3)
        self.assertEqual(service.batch_budget_s, 9)
        self.assertEqual(service.concurrency, 2)
        self.assertEqual(service.per_tab_timeout_s, 4)
        self.assertEqual(service.cache.ttl_s, 120)

    def test_legacy_polish_aliases_remain_supported(self):
        env = {
            "SUMMARY_POLISH_MAX_TABS": "5",
            "SUMMARY_POLISH_BUDGET_S": "7",
        }
        with patch.dict(os.environ, env, clear=True):
            service = TabSummaryService(available=True, text_generator=FakeGenerator([]))
        self.assertEqual(service.max_tabs, 5)
        self.assertEqual(service.batch_budget_s, 7)

    def test_disabled_polish_marks_service_unavailable(self):
        with patch.dict(
            os.environ,
            {"SUMMARY_POLISH_ENABLED": "false"},
            clear=True,
        ):
            service = TabSummaryService(available=True, text_generator=FakeGenerator([]))
        self.assertFalse(service.enabled)
        self.assertFalse(service.available)

    def test_trace_collector_records_safe_polish_spans(self):
        pack = build_evidence_pack(sample_tab(), rated=False, intent="")
        service = TabSummaryService(
            available=True,
            text_generator=FakeGenerator([make_draft(pack)]),
            cache=SummaryCache(ttl_s=60, max_entries=4),
        )
        collector: list[dict] = []
        results = service.polish_many(
            [("user-tab-1", sample_tab())],
            rated=False,
            intent="",
            trace_collector=collector,
        )
        self.assertEqual(results["user-tab-1"]["summary_source"], "gemma_verified")
        names = {span["span_name"] for span in collector}
        self.assertIn("summary.polish_batch", names)
        self.assertIn("summary.build_evidence_pack", names)
        self.assertIn("gemma.summarize_tab", names)
        self.assertIn("summary.validate_tab", names)
        payload = json.dumps(collector)
        self.assertNotIn("Rebuilding a Queue Without Losing Messages", payload)
        self.assertNotIn("The team split producers", payload)
        self.assertNotIn("full_text", payload)
        batch = next(span for span in collector if span["span_name"] == "summary.polish_batch")
        self.assertEqual(batch["summary.tab_count"], 1)
        self.assertEqual(batch["summary.polished_count"], 1)


class TestJobStore(unittest.TestCase):
    def test_job_lookup_reports_expiry_and_never_leaks_full_text(self):
        store = SummaryJobStore(ttl_s=900, max_jobs=4)
        job_id = store.create(
            [sample_tab()],
            rated=False,
            effective_intent="",
            preset_key="sample",
            shared_cache_allowed=True,
            total_tabs=1,
            kept_count=1,
            closed_count=0,
            closed_read_min=0.0,
            total_words=100,
            closed_words=0,
        )
        job, state = store.lookup(job_id)
        self.assertEqual(state, "active")
        self.assertIn("user-tab-1", job["tabs"])
        store._jobs[job_id]["expires_at"] = time.monotonic() - 1
        expired, state = store.lookup(job_id)
        self.assertIsNone(expired)
        self.assertEqual(state, "expired")

    def test_job_store_lru_and_updated_minutes(self):
        store = SummaryJobStore(ttl_s=900, max_jobs=2)
        ids = []
        for index in range(3):
            tab = sample_tab()
            tab["id"] = f"user-tab-{index}"
            ids.append(
                store.create(
                    [tab],
                    rated=False,
                    effective_intent="",
                    preset_key="sample",
                    shared_cache_allowed=True,
                    total_tabs=1,
                    kept_count=1,
                    closed_count=0,
                    closed_read_min=0.0,
                    total_words=100,
                    closed_words=0,
                )
            )
        self.assertEqual(len(store), 2)
        self.assertIsNone(store.get(ids[0]))
        self.assertIsNotNone(store.get(ids[2]))

        job = store.get(ids[2])
        store.save_polished(
            ids[2],
            "user-tab-2",
            {
                "summary_read_sec": 30,
                "full_read_min": 2.0,
                "overview": "Short overview.",
                "takeaways": ["One grounded point about the queue."],
            },
        )
        self.assertAlmostEqual(store.updated_minutes_saved(job), 1.5, places=1)

    def test_begin_polish_prevents_duplicate_inflight_work(self):
        store = SummaryJobStore(ttl_s=900, max_jobs=2)
        job_id = store.create(
            [sample_tab()],
            rated=False,
            effective_intent="",
            preset_key="sample",
            shared_cache_allowed=True,
            total_tabs=1,
            kept_count=1,
            closed_count=0,
            closed_read_min=0.0,
            total_words=100,
            closed_words=0,
        )
        self.assertTrue(store.begin_polish(job_id, "user-tab-1"))
        self.assertFalse(store.begin_polish(job_id, "user-tab-1"))
        store.end_polish(job_id, "user-tab-1")
        self.assertTrue(store.begin_polish(job_id, "user-tab-1"))


if __name__ == "__main__":
    unittest.main()
