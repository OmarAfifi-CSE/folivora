"""Golden-set evaluation for the grounded tab-summary pipeline.

Offline mode validates deterministic fixture drafts against the real validator.
Live mode calls the configured Gemma provider through TabSummaryService.
Manual readability scores live in eval/summary_review.json.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
except ImportError:
    pass

import summary_service as service


CASES_PATH = ROOT / "eval" / "summary_cases.json"
REVIEW_PATH = ROOT / "eval" / "summary_review.json"
GENERIC_HEADINGS = [
    "Implementation Choice",
    "Operational Tradeoff",
    "Evidence Check",
    "Delivery Constraint",
]
FILLERS = [
    "The example stays concrete.",
    "The context matters.",
    "The result is auditable.",
    "The tradeoff is explicit.",
]
BODY_TEMPLATES = [
    "The source explains {heading} through {fact}, the constraint that shaped it, and the verification step that made the decision defensible.",
    "A second angle is {heading}: the team weighed {fact} against delivery speed, then documented the boundary conditions behind the choice.",
    "The write-up treats {heading} as an engineering decision, pairing {fact} with the operational tradeoff and the evidence a reviewer should check.",
    "Finally, {heading} shows how {fact} affected reliability, with a concrete verification path instead of a broad recommendation.",
]
POINT_TEMPLATES = [
    "A reader focused on {intent} can use this page to connect {fact} to a concrete next step, while naming the constraint that limits the claim.",
    "The practical signal for {intent} is how {fact} changes the recommendation a reader should test before adopting the approach.",
]


def load_cases(path: Path = CASES_PATH, limit: int | None = None) -> list[dict[str, Any]]:
    cases = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not cases:
        raise ValueError(f"No cases found in {path}")
    if limit is not None:
        cases = cases[: max(1, limit)]
    return cases


def _fit(text: str, low: int, high: int) -> str:
    text = service._normalize_ws(text)
    words = text.split()
    if len(words) > high:
        text = " ".join(words[:high]).rstrip(" ,;:-—") + "."
        words = text.split()
    index = 0
    while len(words) < low and index < 8:
        filler = FILLERS[index % len(FILLERS)]
        if len(words) + len(filler.split()) > high:
            index += 1
            continue
        text = text.rstrip(".") + ". " + filler
        words = text.split()
        index += 1
    return text


def _heading(value: str, fallback: str) -> str:
    heading = service._strip_emoji(str(value or "")).strip(" -—:")
    if re.search(r"\b(?:i|we|my|our)\b", heading, flags=re.IGNORECASE):
        return fallback
    if not heading or not heading[0].isalnum() or len(heading.split()) > 8:
        return fallback
    return heading


def _anchor(text: str) -> str:
    words = service._normalize_ws(text).split()
    anchor = " ".join(words[:8])
    if len(anchor.split()) < 6:
        anchor = " ".join(words)
    return anchor


def _evidence(pack: dict[str, Any]) -> list[dict[str, Any]]:
    return list(pack.get("evidence") or []) + list(pack.get("code_blocks") or [])


def fixture_draft(case: dict[str, Any], pack: dict[str, Any]) -> dict[str, Any]:
    facts = list(case.get("expected_facts") or []) or ["the engineering decision"]
    evidence = _evidence(pack)
    if not evidence:
        raise ValueError(f"{case['case_id']} has no evidence")
    bullet_count = 3 if pack["sparse"] else 4

    overview = (
        f"This engineering write-up connects {facts[0]} and {facts[-1]} to concrete "
        "implementation choices, operational constraints, and verification steps that a "
        "reader can inspect before applying the ideas."
    )
    overview = _fit(overview, 40, 46)

    takeaways: list[dict[str, str]] = []
    for index in range(bullet_count):
        item = evidence[index % len(evidence)]
        heading = _heading(
            item.get("heading", ""), GENERIC_HEADINGS[index % len(GENERIC_HEADINGS)]
        )
        fact = facts[index % len(facts)]
        body = BODY_TEMPLATES[index % len(BODY_TEMPLATES)].format(
            heading=heading.lower(), fact=fact
        )
        text = _fit(f"{heading} — {body}", 36, 42)
        takeaways.append(
            {
                "text": text,
                "evidence_id": item["id"],
                "anchor": _anchor(item["text"]),
            }
        )

    draft: dict[str, Any] = {
        "overview": overview,
        "overview_evidence_ids": [evidence[0]["id"]],
        "takeaways": takeaways,
    }
    if pack.get("rated") and pack.get("has_interest"):
        why_fact = facts[1 % len(facts)]
        why = (
            f"For readers focused on {pack['intent']}, the useful signal is how {why_fact} "
            "shapes implementation choices, operational constraints, and the evidence a "
            "team should inspect before adopting the approach."
        )
        why = _fit(why, 28, 40)
        points: list[dict[str, str]] = []
        for index in range(2):
            item = evidence[(bullet_count + index) % len(evidence)]
            heading = ["Reader Takeaway", "Practical Next Step"][index]
            fact = facts[(index + 2) % len(facts)]
            body = POINT_TEMPLATES[index].format(intent=pack["intent"], fact=fact)
            points.append(
                {
                    "text": _fit(f"{heading} — {body}", 36, 42),
                    "evidence_id": item["id"],
                    "anchor": _anchor(item["text"]),
                }
            )
        draft["interest"] = {
            "why_relevant": why,
            "why_evidence_ids": [evidence[0]["id"]],
            "points": points,
        }
    return draft


def _fact_tokens(value: str) -> list[str]:
    tokens = service._normalize_key(value).split()
    return [token for token in tokens if token not in service._STOPWORDS]


def _combined(result: dict[str, Any]) -> str:
    parts = [str(result.get("overview") or "")]
    parts.extend(str(item) for item in result.get("takeaways") or [])
    interest = result.get("interest_section") or {}
    parts.append(str(interest.get("why_relevant") or ""))
    parts.extend(str(item) for item in interest.get("tailored_points") or [])
    return service._normalize_ws(" ".join(parts))


def _fact_coverage(case: dict[str, Any], result: dict[str, Any]) -> tuple[float, list[str]]:
    facts = list(case.get("expected_facts") or [])
    if not facts:
        return 1.0, []
    summary_text = service._normalize_key(_combined(result))
    matched: list[str] = []
    for fact in facts:
        tokens = _fact_tokens(fact)
        if tokens and all(token in summary_text for token in tokens):
            matched.append(fact)
    return len(matched) / len(facts), matched


def _unsupported_numbers(pack: dict[str, Any], result: dict[str, Any]) -> int:
    allowed = set(pack.get("allowed_numbers") or [])
    return sum(
        1 for number in service._NUMBER_RE.findall(_combined(result)) if number not in allowed
    )


def _quote_only(result: dict[str, Any]) -> int:
    texts = [str(result.get("overview") or "")]
    texts.extend(str(item) for item in result.get("takeaways") or [])
    interest = result.get("interest_section") or {}
    texts.append(str(interest.get("why_relevant") or ""))
    texts.extend(str(item) for item in interest.get("tailored_points") or [])
    return sum(1 for text in texts if text and service._is_quote_only(text))


def _verbatim_copy_sections(pack: dict[str, Any], result: dict[str, Any]) -> int:
    texts = [str(result.get("overview") or "")]
    texts.extend(str(item) for item in result.get("takeaways") or [])
    interest = result.get("interest_section") or {}
    texts.append(str(interest.get("why_relevant") or ""))
    texts.extend(str(item) for item in interest.get("tailored_points") or [])
    return sum(1 for text in texts if text and service._is_verbatim_copy(text, pack))


def _emoji_count(result: dict[str, Any]) -> int:
    return len(service._EMOJI_RE.findall(_combined(result)))


def _duplicate_rate(result: dict[str, Any]) -> float:
    takeaways = [str(item) for item in result.get("takeaways") or []]
    pairs = len(takeaways) * (len(takeaways) - 1) / 2
    if pairs <= 0:
        return 0.0
    tokens = [service._token_set(item) for item in takeaways]
    duplicates = 0
    for index in range(len(tokens)):
        for other in range(index + 1, len(tokens)):
            if service._jaccard(tokens[index], tokens[other]) >= 0.68:
                duplicates += 1
    return duplicates / pairs


def _section_coverage(case: dict[str, Any], result: dict[str, Any]) -> float:
    headings = [str(item) for item in case.get("expected_sections") or []]
    if not headings:
        return 1.0
    takeaways = " ".join(str(item) for item in result.get("takeaways") or [])
    normalized = service._normalize_key(takeaways)
    covered = 0
    for heading in headings:
        key = service._normalize_key(heading)
        if key and key in normalized:
            covered += 1
    return covered / len(headings)


def _forbidden_claims(case: dict[str, Any], result: dict[str, Any]) -> int:
    combined = service._normalize_key(_combined(result))
    return sum(
        1
        for claim in case.get("forbidden_claims") or []
        if service._normalize_key(claim) in combined
    )


def _evaluate_result(case: dict[str, Any], pack: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    overview_words = service._word_count(str(result.get("overview") or ""))
    takeaway_words = [service._word_count(str(item)) for item in result.get("takeaways") or []]
    total_words = overview_words + sum(takeaway_words)
    fact_coverage, matched_facts = _fact_coverage(case, result)
    return {
        "case_id": case["case_id"],
        "rated": bool(case.get("rated")),
        "summary_source": result.get("summary_source"),
        "polish_status": result.get("polish_status"),
        "overview_words": overview_words,
        "takeaway_words": takeaway_words,
        "total_words": total_words,
        "in_word_target": 140 <= total_words <= 220,
        "fact_coverage": round(fact_coverage, 4),
        "matched_facts": matched_facts,
        "expected_section_coverage": round(_section_coverage(case, result), 4),
        "duplicate_rate": round(_duplicate_rate(result), 4),
        "unsupported_numbers": _unsupported_numbers(pack, result),
        "quote_only_sections": _quote_only(result),
        "verbatim_copy_sections": _verbatim_copy_sections(pack, result),
        "emoji_count": _emoji_count(result),
        "forbidden_claims": _forbidden_claims(case, result),
        "read_sec": result.get("summary_read_sec"),
        "validation_reasons": list(
            (result.get("summary_validation") or {}).get("reasons") or []
        ),
    }


def _fallback(case: dict[str, Any], reasons: list[str], attempts: int) -> dict[str, Any]:
    return service._fallback_result(
        case["tab"], reasons=reasons or ["fixture_validation_failed"], attempts=attempts
    )


def _offline_case(case: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], float]:
    started = time.perf_counter()
    pack = service.build_evidence_pack(
        case["tab"], rated=bool(case["rated"]), intent=str(case.get("intent") or "")
    )
    draft = fixture_draft(case, pack)
    ok, reasons, normalized = service.validate_tab_summary(draft, pack)
    if ok and normalized is not None:
        normalized["polish_status"] = "polished"
        normalized["summary_source"] = "gemma_verified"
        normalized["summary_validation"] = {
            "status": "passed",
            "reasons": [],
            "attempts": 1,
            "model": "offline-fixture",
            "latency_ms": 0.0,
            "cache_hit": False,
            "prompt_version": service.PROMPT_VERSION,
        }
        result = normalized
    else:
        result = _fallback(case, reasons, attempts=1)
    return pack, result, (time.perf_counter() - started) * 1000.0


def _live_case(
    case: dict[str, Any], summary_service: service.TabSummaryService
) -> tuple[dict[str, Any], dict[str, Any], float]:
    pack = service.build_evidence_pack(
        case["tab"], rated=bool(case["rated"]), intent=str(case.get("intent") or "")
    )
    started = time.perf_counter()
    try:
        result = summary_service.polish_tab(
            case["tab"],
            rated=bool(case["rated"]),
            intent=str(case.get("intent") or ""),
            shared_cache_allowed=False,
        )
    except Exception as exc:
        result = _fallback(case, [f"harness_exception:{type(exc).__name__}"], attempts=0)
    return pack, result, (time.perf_counter() - started) * 1000.0


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return round(ordered[index], 1)


def _batch_latency(
    cases: list[dict[str, Any]],
    summary_service: service.TabSummaryService,
    count: int,
    repeats: int,
) -> float | None:
    tabs = [
        (str(case["case_id"]), case["tab"])
        for case in cases
        if not case.get("rated")
    ][:count]
    if len(tabs) < count:
        tabs = [(str(case["case_id"]), case["tab"]) for case in cases][:count]
    if not tabs:
        return None
    samples: list[float] = []
    for _ in range(max(1, repeats)):
        started = time.perf_counter()
        summary_service.polish_many(
            tabs,
            rated=False,
            intent="",
            shared_cache_allowed=False,
        )
        samples.append((time.perf_counter() - started) * 1000.0)
    return _p95(samples)


def _manual_review(path: Path, case_ids: list[str]) -> dict[str, Any]:
    if not path.exists():
        return {
            "present": False,
            "reviewed": 0,
            "coverage": 0.0,
            "mean": None,
            "min": None,
        }
    raw = json.loads(path.read_text(encoding="utf-8"))
    scores = []
    for case_id in case_ids:
        entry = raw.get(case_id) or {}
        score = entry.get("readability")
        if isinstance(score, (int, float)) and 1 <= float(score) <= 5:
            scores.append(float(score))
    return {
        "present": True,
        "reviewed": len(scores),
        "coverage": round(len(scores) / max(1, len(case_ids)), 4),
        "mean": round(sum(scores) / len(scores), 2) if scores else None,
        "min": min(scores) if scores else None,
    }


def _aggregate(case_rows: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(case_rows)
    fact_values = [row["fact_coverage"] for row in case_rows]
    duplicate_values = [row["duplicate_rate"] for row in case_rows]
    return {
        "cases": count,
        "polished": sum(1 for row in case_rows if row["polish_status"] == "polished"),
        "fallback": sum(1 for row in case_rows if row["polish_status"] != "polished"),
        "fallback_rate": round(
            sum(1 for row in case_rows if row["polish_status"] != "polished") / max(1, count),
            4,
        ),
        "fact_coverage": round(sum(fact_values) / max(1, count), 4),
        "unsupported_numbers": sum(row["unsupported_numbers"] for row in case_rows),
        "quote_only_sections": sum(row["quote_only_sections"] for row in case_rows),
        "verbatim_copy_sections": sum(row["verbatim_copy_sections"] for row in case_rows),
        "emoji_count": sum(row["emoji_count"] for row in case_rows),
        "duplicate_rate": round(sum(duplicate_values) / max(1, count), 4),
        "forbidden_claims": sum(row["forbidden_claims"] for row in case_rows),
        "word_target_passes": sum(1 for row in case_rows if row["in_word_target"]),
        "word_target_rate": round(
            sum(1 for row in case_rows if row["in_word_target"]) / max(1, count), 4
        ),
        "mean_section_coverage": round(
            sum(row["expected_section_coverage"] for row in case_rows) / max(1, count), 4
        ),
        "per_case_latency_p95_ms": _p95([row["latency_ms"] for row in case_rows]),
    }


def evaluate_gates(report: dict[str, Any]) -> dict[str, bool]:
    aggregate = report["aggregate"]
    manual = report["manual_review"]
    batch = report["batch_latency_ms"]
    gates = {
        "no_unsupported_numbers": aggregate["unsupported_numbers"] == 0,
        "no_quote_only_bullets": aggregate["quote_only_sections"] == 0,
        "no_verbatim_copy": aggregate["verbatim_copy_sections"] == 0,
        "no_emoji": aggregate["emoji_count"] == 0,
        "no_forbidden_claims": aggregate["forbidden_claims"] == 0,
        "duplicate_rate_below_5pct": aggregate["duplicate_rate"] < 0.05,
        "fact_coverage_at_least_85pct": aggregate["fact_coverage"] >= 0.85,
        "word_target_at_least_18_of_20": aggregate["word_target_passes"]
        >= math.ceil(aggregate["cases"] * 0.9),
        "manual_review_complete": manual["coverage"] == 1.0,
        "manual_readability_at_least_4": manual["min"] is not None
        and manual["min"] >= 4.0,
        "manual_readability_mean_at_least_4": manual["mean"] is not None
        and manual["mean"] >= 4.0,
    }
    if report["mode"] == "live-gemma":
        gates["fallback_below_10pct"] = aggregate["fallback_rate"] < 0.10
        gates["batch_5_tabs_under_14s"] = batch.get("5") is not None and batch["5"] <= 14000
        gates["batch_8_tabs_under_20s"] = batch.get("8") is not None and batch["8"] <= 20000
    return gates


def run_harness(
    *,
    live: bool = False,
    cases_path: Path = CASES_PATH,
    review_path: Path = REVIEW_PATH,
    limit: int | None = None,
    batch_repeats: int = 1,
) -> dict[str, Any]:
    cases = load_cases(cases_path, limit=limit)
    summary_service = None
    if live:
        summary_service = service.TabSummaryService()
        if not summary_service.available:
            raise RuntimeError(
                "Live Gemma mode requested but no summary provider is configured "
                "(OLLAMA_BASE_URL, OPENROUTER_API_KEY, GOOGLE_API_KEY, or GEMINI_API_KEY)."
            )

    rows: list[dict[str, Any]] = []
    for case in cases:
        if live:
            assert summary_service is not None
            pack, result, latency_ms = _live_case(case, summary_service)
        else:
            pack, result, latency_ms = _offline_case(case)
        row = _evaluate_result(case, pack, result)
        row["latency_ms"] = round(latency_ms, 1)
        rows.append(row)

    batch_latency: dict[str, float | None] = {"5": None, "8": None}
    if live and summary_service is not None:
        batch_latency["5"] = _batch_latency(cases, summary_service, 5, batch_repeats)
        batch_latency["8"] = _batch_latency(cases, summary_service, 8, batch_repeats)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "live-gemma" if live else "offline-fixture",
        "cases_path": str(cases_path),
        "manual_review": _manual_review(review_path, [str(case["case_id"]) for case in cases]),
        "batch_latency_ms": batch_latency,
        "aggregate": _aggregate(rows),
        "cases": rows,
    }
    report["gates"] = evaluate_gates(report)
    report["passed"] = all(report["gates"].values())
    return report


def format_summary(report: dict[str, Any]) -> str:
    lines = [
        f"mode: {report['mode']}",
        f"cases: {report['aggregate']['cases']}",
        f"polished: {report['aggregate']['polished']}",
        f"fallback_rate: {report['aggregate']['fallback_rate']:.1%}",
        f"fact_coverage: {report['aggregate']['fact_coverage']:.1%}",
        f"duplicate_rate: {report['aggregate']['duplicate_rate']:.1%}",
        f"unsupported_numbers: {report['aggregate']['unsupported_numbers']}",
        f"quote_only_sections: {report['aggregate']['quote_only_sections']}",
        f"verbatim_copy_sections: {report['aggregate']['verbatim_copy_sections']}",
        f"forbidden_claims: {report['aggregate']['forbidden_claims']}",
        f"word_target: {report['aggregate']['word_target_passes']}/{report['aggregate']['cases']}",
        f"manual_review: {report['manual_review']}",
        f"per_case_latency_p95_ms: {report['aggregate']['per_case_latency_p95_ms']}",
        f"batch_latency_ms: {report['batch_latency_ms']}",
        "gates:",
    ]
    lines.extend(f"  {'PASS' if ok else 'FAIL'} {name}" for name, ok in report["gates"].items())
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-gemma", action="store_true", help="Call the configured provider.")
    parser.add_argument("--gate", action="store_true", help="Exit nonzero when a gate fails.")
    parser.add_argument("--json-out", type=Path, help="Write the full report as JSON.")
    parser.add_argument("--cases", type=Path, default=CASES_PATH)
    parser.add_argument("--review", type=Path, default=REVIEW_PATH)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--batch-repeats", type=int, default=1)
    args = parser.parse_args(argv)

    try:
        report = run_harness(
            live=args.live_gemma,
            cases_path=args.cases,
            review_path=args.review,
            limit=args.limit,
            batch_repeats=max(1, args.batch_repeats),
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(format_summary(report))
    if args.gate and not report["passed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
