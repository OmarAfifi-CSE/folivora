"""Build the deterministic 20-case golden fixture used by summary_quality.py."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from browserbase_service import PRESET_TAB_PILES

CASES_PATH = ROOT / "eval" / "summary_cases.json"

HOF_INDICES = [0, 3, 8, 13, 18, 24, 31, 38, 44, 51]
POSTGRES_INDICES = [0, 2, 4, 7, 10]
EDGE_INDICES = [0, 2, 4, 6, 9]

EXPECTED_FACTS = {
    "hof-00": ["DEV Community", "beginner guide", "Education Tracks"],
    "hof-03": ["jobseekers", "motivation", "asking for help"],
    "hof-08": ["open source", "indie hacking", "7 users"],
    "hof-13": ["CTE", "window functions", "COALESCE"],
    "hof-18": ["metadata", "search", "social platforms", "browser"],
    "hof-24": ["Gemini", "phone", "Android"],
    "hof-31": ["Laravel", "batch process", "CSV", "email"],
    "hof-38": ["local-first", "32 messaging platforms", "13 LLM providers"],
    "hof-44": ["tool use", "production systems", "observability"],
    "hof-51": ["generative AI", "gacha", "retries"],
    "pg-00": ["Japan", "Tokyo", "Kyoto", "Shinkansen"],
    "pg-02": ["caffeine", "deep sleep", "circadian"],
    "pg-04": ["Flutter", "networking", "open source"],
    "pg-07": ["PyTorch", "CPU threading", "OMP_NUM_THREADS", "containers"],
    "pg-10": ["software complexity", "corporate culture"],
    "edge-00": ["Gemma", "WebGPU", "Web Worker", "4-bit"],
    "edge-02": ["Sentry", "OpenTelemetry", "token usage", "latency"],
    "edge-04": ["Browserbase", "CDP", "concurrency slots"],
    "edge-06": ["mechanical keyboard", "switch lubing", "guide"],
    "edge-09": ["minimalist", "home office", "desk"],
}

STOPWORDS = {
    "about", "after", "again", "against", "also", "because", "before", "being",
    "between", "building", "could", "every", "first", "from", "guide", "have",
    "into", "just", "more", "most", "other", "over", "should", "some", "than",
    "that", "their", "there", "these", "they", "this", "through", "using",
    "what", "when", "where", "which", "while", "with", "would", "your",
}


def _heading(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(value or "")).strip()
    if " — " in cleaned:
        return cleaned.split(" — ", 1)[0].strip()
    return ""


def _facts(case_id: str, tab: dict) -> list[str]:
    if case_id in EXPECTED_FACTS:
        return EXPECTED_FACTS[case_id]
    candidates: list[str] = []
    for value in list(tab.get("topics") or []) + [tab.get("title", "")]:
        for token in re.findall(r"[A-Za-z][A-Za-z0-9+#.-]{4,}", str(value)):
            token = token.strip(".-").lower()
            if len(token) >= 4 and token not in STOPWORDS and token not in candidates:
                candidates.append(token)
    return candidates[:4] or ["engineering"]


def _case(case_id: str, tab: dict, *, rated: bool, intent: str) -> dict:
    headings: list[str] = []
    for item in tab.get("takeaways") or []:
        heading = _heading(item)
        if heading and heading not in headings:
            headings.append(heading)
    headings = headings[:4]
    case_tab = {
        "id": tab.get("id"),
        "title": tab.get("title"),
        "url": tab.get("url"),
        "domain": tab.get("domain"),
        "overview": tab.get("overview") or "",
        "takeaways": tab.get("takeaways") or [],
        "topics": tab.get("topics") or [],
        "full_text": str(tab.get("full_text") or "")[:12000],
        "code_blocks": tab.get("code_blocks") or [],
        "word_count": tab.get("word_count") or 1000,
        "code_block_count": tab.get("code_block_count") or 0,
        "heading_count": tab.get("heading_count") or len(headings),
    }
    if rated:
        focused = [item for item in (tab.get("takeaways") or []) if _heading(item)][:2]
        case_tab["interest_section"] = {
            "active": True,
            "headline": f'Focused on "{intent}"',
            "why_relevant": (
                f"The page connects {intent} to concrete implementation choices, the tradeoffs "
                "the team accepted, and verification steps that make the result credible."
            ),
            "tailored_points": focused,
            "mention_count": 6,
        }
    return {
        "case_id": case_id,
        "rated": rated,
        "intent": intent if rated else "",
        "expected_facts": _facts(case_id, tab),
        "expected_sections": headings,
        "forbidden_claims": [
            "guaranteed production result",
            "works for every codebase",
            "no tradeoffs",
        ],
        "tab": case_tab,
    }


def main() -> None:
    hof = PRESET_TAB_PILES["dev_hall_of_fame_pile"]["tabs"]
    postgres = PRESET_TAB_PILES["postgres_docker_pile"]["tabs"]
    edge = PRESET_TAB_PILES["edge_ai_quantization_pile"]["tabs"]
    selected: list[tuple[str, dict]] = []
    for index in HOF_INDICES:
        selected.append((f"hof-{index:02d}", hof[index]))
    for index in POSTGRES_INDICES:
        selected.append((f"pg-{index:02d}", postgres[index]))
    for index in EDGE_INDICES:
        selected.append((f"edge-{index:02d}", edge[index]))

    cases = []
    for order, (case_id, tab) in enumerate(selected):
        rated = order % 2 == 1
        intent = ""
        if rated:
            topics = tab.get("topics") or []
            intent = str(topics[0]) if topics else "software engineering"
        cases.append(_case(case_id, tab, rated=rated, intent=intent))

    CASES_PATH.write_text(json.dumps(cases, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(cases)} cases to {CASES_PATH}")


if __name__ == "__main__":
    main()
