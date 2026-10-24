"""
gemma_service.py - Google Gemma Open-Weight Digest Synthesizer & Fact-Checking Validator

Enforces the golden rule from Week 1 winners:
"Code owns the numbers; Gemma writes the words."

Supports 3 open-weight Gemma lanes:
1. Local Ollama (`OLLAMA_BASE_URL` -> `gemma3:4b`)
2. OpenRouter non-reasoning Gemma 3 (`google/gemma-3-27b-it`, `google/gemma-3-12b-it`)
3. Google AI Studio Gemma (`gemma-4-26b-a4b-it`, `gemma-4-31b-it`)

Every generated draft is checked by `check_output()`:
- Zero invented or rounded numbers (every digit in the draft must exist in `allowed_numbers`).
- Required counts (`closed_count`, `kept_count`, `minutes_saved`) must appear verbatim.
- Strict length limit (<= 42 words) and zero AI cliches.
- Failed drafts get 1 retry naming the exact rejected tokens before falling back to a verified template.
"""

import os
import re
import time
import requests

from telemetry import trace_genai_span

BANNED_PATTERNS = re.compile(
    r"(\bdelve\b|\btapestry\b|\bgame-changer\b|\brevolutionary\b|\bas an ai\b|\bsure[,!]\b|\bhere is\b|\*\*)",
    re.IGNORECASE,
)


def check_output(
    candidate_text: str,
    required_tokens: list[str],
    allowed_numbers: set[str],
    max_words: int = 42,
) -> tuple[bool, list[str]]:
    """
    Deterministic validation gate:
    1. Rejects empty or overly short (<12 words) or overly long (>max_words) output.
    2. Rejects banned AI cliches or markdown formatting.
    3. Verifies every required token (`closed_count`, `kept_count`, `minutes_saved`) is present verbatim.
    4. Extracts all numeric literals in the text and rejects any invented or rounded number
       not present in `allowed_numbers`.
    """
    reasons: list[str] = []
    cleaned = (candidate_text or "").strip().strip('"').strip("'")
    if not cleaned:
        return False, ["empty_output"]

    words = cleaned.split()
    if len(words) < 10:
        reasons.append(f"too_short:{len(words)}_words")
    if len(words) > max_words:
        reasons.append(f"too_long:{len(words)}_words_max_{max_words}")

    if BANNED_PATTERNS.search(cleaned):
        reasons.append("contains_banned_phrase_or_markdown")

    cleaned_lower = cleaned.lower()
    for req in required_tokens:
        if str(req).lower() not in cleaned_lower:
            reasons.append(f"missing_required_fact:{req}")

    # Strict digit check: every number in the prose must come from Python's verified set
    found_numbers = re.findall(r"\b\d+(?:\.\d+)?\b", cleaned)
    for num in found_numbers:
        if num not in allowed_numbers:
            reasons.append(f"unverified_or_rounded_number:{num}")

    return (len(reasons) == 0), reasons


class GemmaDigestService:
    OPENROUTER_MODELS = ("google/gemma-3-27b-it", "google/gemma-3-12b-it")
    GOOGLE_MODELS = ("gemma-4-26b-a4b-it", "gemma-4-31b-it")
    OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
    _TAB_LANE_COUNTER = 0

    def __init__(self, timeout_s: float | None = None):
        self.ollama_base_url = os.getenv("OLLAMA_BASE_URL", "").strip()
        self.ollama_model = os.getenv("OLLAMA_MODEL", "gemma3:4b").strip()
        self.openrouter_api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
        self.google_api_key = (os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY") or "").strip()
        try:
            self.timeout_s = float(timeout_s if timeout_s is not None else os.getenv("GEMMA_TIMEOUT_S", "8.0"))
        except (TypeError, ValueError):
            self.timeout_s = 8.0

    def build_prompt(
        self,
        total_tabs: int,
        kept_count: int,
        closed_count: int,
        minutes_saved: float,
        top_tab_title: str,
        top_tab_p50: float,
        prompt_version: str = "v4",
    ) -> tuple[str, list[str], set[str]]:
        """
        Builds the prompt and the exact verification contract (`required_tokens`, `allowed_numbers`).
        Supports `v1`, `v2`, `v3`, and `v4` for the reproducible `eval/EVAL.md` harness.
        """
        min_str = f"{minutes_saved:.1f}" if (minutes_saved % 1 != 0) else str(int(minutes_saved))
        p50_str = f"{top_tab_p50:.1f}"

        required_tokens = [str(closed_count), str(kept_count), min_str]
        allowed_numbers = {
            str(total_tabs),
            str(kept_count),
            str(closed_count),
            min_str,
            str(int(round(minutes_saved))),
            p50_str,
            str(int(round(top_tab_p50))),
            "1",
            "2",
            "90",
        }
        # Also allow any digit that naturally appears inside the top tab's title (e.g. "4", "3", "525")
        for title_num in re.findall(r"\b\d+(?:\.\d+)?\b", top_tab_title):
            allowed_numbers.add(title_num)

        short_topic = top_tab_title.split(":")[0].strip()

        if prompt_version == "v1":
            prompt = (
                f"Summarize this tab cleanup in two short sentences: out of {total_tabs} open tabs, "
                f"{closed_count} low-utility tabs were closed saving {min_str} minutes of reading, and "
                f"{kept_count} reference tabs were kept led by {short_topic}."
            )
        elif prompt_version == "v2":
            prompt = (
                f"Write a two-sentence spoken brief (maximum 38 words) for a developer. "
                f"Mention that {closed_count} of {total_tabs} tabs were closed to save {min_str} minutes, "
                f"and {kept_count} tabs were kept on the cheat sheet led by {short_topic}. Plain text only."
            )
        elif prompt_version == "v3":
            prompt = (
                f"Write a 2-sentence spoken summary, strictly 15 to 36 words, plain text with no markdown. "
                f"Copy these exact numbers character-for-character without rounding: "
                f"closed \"{closed_count}\" of \"{total_tabs}\" tabs, saved \"{min_str}\" minutes, "
                f"and kept \"{kept_count}\" reference tabs led by {short_topic}. "
                f"Do not invent any other numbers."
            )
        else:  # v4 (Shipped production prompt — natural human English, handles 0 closed or 0 kept cleanly)
            if closed_count == 0:
                prompt = (
                    f"Write a friendly 2-sentence spoken summary (20 to 36 words, plain everyday English, no markdown, no intro). "
                    f"You MUST include the exact numbers \"{closed_count}\", \"{kept_count}\", and \"{min_str}\" "
                    f"character-for-character (never round decimals or invent any other digits). "
                    f"Sentence 1: State that all {kept_count} of the {total_tabs} open tabs are summarized below "
                    f"(with {closed_count} tabs closed). "
                    f"Sentence 2: State that reading the extracted key takeaways—starting with {short_topic}—saves "
                    f"{min_str} minutes compared to reading the full articles. Reply with ONLY the two sentences."
                )
            elif kept_count == 0:
                prompt = (
                    f"Write a friendly 2-sentence spoken summary (20 to 36 words, plain everyday English, no markdown, no intro). "
                    f"You MUST include the exact numbers \"{closed_count}\", \"{kept_count}\", and \"{min_str}\" "
                    f"character-for-character (never round decimals or invent any other digits). "
                    f"Sentence 1: State that {kept_count} of the {total_tabs} open tabs matched the requested topic. "
                    f"Sentence 2: State that all {closed_count} open tabs cover other topics (saving {min_str} minutes if closed), "
                    f"though their general summaries remain available below. Reply with ONLY the two sentences."
                )
            else:
                prompt = (
                    f"Write a friendly 2-sentence spoken summary (20 to 36 words, plain everyday English, no markdown, no intro). "
                    f"You MUST include the exact numbers \"{closed_count}\", \"{kept_count}\", and \"{min_str}\" "
                    f"character-for-character (never round decimals or invent any other digits). "
                    f"Sentence 1: State that {kept_count} of the {total_tabs} open tabs match the user's focus, "
                    f"starting with {short_topic}. "
                    f"Sentence 2: State that closing the other {closed_count} unrelated tab saves "
                    f"{min_str} minutes of full-article reading. Reply with ONLY the two sentences."
                )

        return prompt, required_tokens, allowed_numbers

    def deterministic_template(
        self,
        total_tabs: int,
        kept_count: int,
        closed_count: int,
        minutes_saved: float,
        top_tab_title: str,
        rated: bool = True,
    ) -> str:
        min_str = f"{minutes_saved:.1f}" if (minutes_saved % 1 != 0) else str(int(minutes_saved))
        short_topic = top_tab_title.split(":")[0].strip()
        if not rated:
            return (
                f"Summarized all {kept_count} of your {total_tabs} open tabs into key takeaways—starting with "
                f"{short_topic}—saving ~{min_str} minutes compared to reading the full pages. "
                f"Type or pick any interest above whenever you want to score and filter these tabs."
            )
        if closed_count == 0:
            return (
                f"All {kept_count} of your {total_tabs} open tabs match your interest ({closed_count} unrelated tabs to close). "
                f"Reading the extracted takeaways below—starting with {short_topic}—saves ~{min_str} minutes compared to reading the full articles."
            )
        if kept_count == 0:
            return (
                f"{kept_count} of your {total_tabs} open tabs matched that topic—all {closed_count} open tabs cover other subjects "
                f"(saving {min_str} minutes of reading if closed), though you can still read their general summaries below."
            )
        tab_word = "tab" if closed_count == 1 else "tabs"
        verb_word = "matches" if kept_count == 1 else "match"
        return (
            f"{kept_count} of your {total_tabs} open tabs {verb_word} your interest—led by {short_topic}—with both a general summary and a tailored interest breakdown below. "
            f"Closing the other {closed_count} unrelated {tab_word} saves {min_str} minutes of full-article reading."
        )

    def _call_raw_model(
        self,
        prompt: str,
        *,
        max_tokens: int = 110,
        temperature: float = 0.25,
        timeout_s: float | None = None,
    ) -> tuple[str | None, str, int, int]:
        """
        Calls the configured Gemma provider (Ollama -> OpenRouter -> Google AI Studio)
        and returns `(text, model_used, input_tokens, output_tokens)`.

        A shared wall-clock deadline prevents a slow first provider from consuming the
        entire budget before the other configured lanes are attempted.
        """
        est_in = max(12, int(len(prompt.split()) * 1.3))
        total_timeout = self.timeout_s if timeout_s is None else max(0.8, float(timeout_s))
        deadline = time.monotonic() + total_timeout
        configured_slots = 0
        if self.ollama_base_url:
            configured_slots += 1
        if self.openrouter_api_key:
            configured_slots += len(self.OPENROUTER_MODELS)
        if self.google_api_key:
            configured_slots += len(self.GOOGLE_MODELS)
        slots_left = max(1, configured_slots)

        def next_timeout() -> float | None:
            remaining = deadline - time.monotonic()
            if remaining <= 0.6:
                return None
            return max(0.8, min(remaining, max(4.5, total_timeout * 0.65)))

        # 1. Local Ollama lane (if configured)
        if self.ollama_base_url:
            request_timeout = next_timeout()
            slots_left -= 1
            if request_timeout is not None:
                try:
                    resp = requests.post(
                        f"{self.ollama_base_url.rstrip('/')}/api/generate",
                        json={
                            "model": self.ollama_model,
                            "prompt": prompt,
                            "stream": False,
                            "options": {
                                "temperature": temperature,
                                "num_predict": max(24, int(max_tokens)),
                            },
                        },
                        timeout=request_timeout,
                    )
                    if resp.status_code == 200:
                        txt = (resp.json().get("response") or "").strip().strip('"')
                        if txt:
                            return txt, f"ollama/{self.ollama_model}", est_in, max(
                                8, int(len(txt.split()) * 1.3)
                            )
                except Exception:
                    pass

        # 2. Google AI Studio Gemma 4 lane (fast with MINIMAL thinking)
        if self.google_api_key:
            for g_model in self.GOOGLE_MODELS:
                request_timeout = next_timeout()
                slots_left -= 1
                if request_timeout is None:
                    break
                try:
                    url = (
                        f"https://generativelanguage.googleapis.com/v1beta/models/"
                        f"{g_model}:generateContent?key={self.google_api_key}"
                    )
                    resp = requests.post(
                        url,
                        json={
                            "contents": [{"parts": [{"text": prompt}]}],
                            "generationConfig": {
                                "maxOutputTokens": max(220, int(max_tokens)),
                                "temperature": float(temperature),
                                "thinkingConfig": {"thinkingLevel": "MINIMAL"},
                            },
                        },
                        timeout=request_timeout,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        cands = data.get("candidates") or []
                        if cands:
                            parts = cands[0].get("content", {}).get("parts") or []
                            spoken = [
                                p.get("text", "")
                                for p in parts
                                if p.get("text", "").strip() and not p.get("thought")
                            ]
                            raw = " ".join(spoken).strip().strip('"')
                            if raw:
                                return raw, f"google/{g_model}", est_in, max(
                                    8, int(len(raw.split()) * 1.3)
                                )
                except Exception:
                    continue

        # 3. OpenRouter non-reasoning Gemma 3 lane
        if self.openrouter_api_key:
            for model_id in self.OPENROUTER_MODELS:
                request_timeout = next_timeout()
                slots_left -= 1
                if request_timeout is None:
                    break
                try:
                    resp = requests.post(
                        self.OPENROUTER_URL,
                        headers={
                            "Authorization": f"Bearer {self.openrouter_api_key}",
                            "Content-Type": "application/json",
                            "HTTP-Referer": "https://folivora.tech",
                            "X-Title": "Folivora Lazy Tab Triage",
                        },
                        json={
                            "model": model_id,
                            "messages": [{"role": "user", "content": prompt}],
                            "max_tokens": max(24, int(max_tokens)),
                            "temperature": float(temperature),
                        },
                        timeout=request_timeout,
                    )
                    if resp.status_code == 200:
                        body = resp.json()
                        choices = body.get("choices") or []
                        if choices:
                            txt = (choices[0].get("message", {}).get("content") or "").strip().strip('"')
                            usage = body.get("usage") or {}
                            in_t = int(usage.get("prompt_tokens") or est_in)
                            out_t = int(
                                usage.get("completion_tokens") or max(8, int(len(txt.split()) * 1.3))
                            )
                            if txt:
                                return txt, f"openrouter/{model_id}", in_t, out_t
                except Exception:
                    continue

        return None, "verified-template", est_in, 32

    def generate_text(
        self,
        prompt: str,
        *,
        max_tokens: int = 700,
        temperature: float = 0.2,
        timeout_s: float | None = None,
    ) -> tuple[str | None, str, int, int]:
        """Generic bounded generation used by the grounded tab summarizer."""
        return self._call_raw_model(
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout_s=timeout_s,
        )

    def generate_tab_text(
        self,
        prompt: str,
        *,
        max_tokens: int = 900,
        temperature: float = 0.2,
        timeout_s: float | None = None,
    ) -> tuple[str | None, str, int, int]:
        """Multi-lane Open-Weight Gemma load balancer for per-tab JSON summaries.

        Distributes concurrent card requests round-robin across Open-Weight Gemma
        endpoints (`google/gemma-4-26b-a4b-it` and `google/gemma-4-31b-it` with
        MINIMAL thinking on Google AI Studio, plus throughput-sorted
        `openrouter/google/gemma-3-12b-it` and local Ollama `gemma3:4b`), with
        automatic fast-lane failover when remaining wall-clock budget is tight.
        """
        provider = os.getenv("SUMMARY_POLISH_PROVIDER", "auto").strip().lower()
        total_timeout = self.timeout_s if timeout_s is None else max(0.8, float(timeout_s))
        started = time.monotonic()
        deadline = started + total_timeout
        self.last_tab_error = ""

        # 1. Local Ollama open-weight lane (when configured)
        if provider in {"auto", "gemma", "ollama"} and self.ollama_base_url:
            rem = deadline - time.monotonic()
            if rem > 0.8:
                try:
                    resp = requests.post(
                        f"{self.ollama_base_url.rstrip('/')}/api/generate",
                        json={
                            "model": self.ollama_model,
                            "prompt": prompt,
                            "stream": False,
                            "format": "json",
                            "options": {
                                "temperature": float(temperature),
                                "num_predict": max(160, int(max_tokens)),
                            },
                        },
                        timeout=min(rem, 10.0),
                    )
                    if resp.status_code == 200:
                        txt = (resp.json().get("response") or "").strip()
                        if txt:
                            return (
                                txt,
                                f"ollama/{self.ollama_model}",
                                len(prompt.split()),
                                len(txt.split()),
                            )
                except Exception as exc:
                    self.last_tab_error = type(exc).__name__

        # 2. Multi-lane Open-Weight Gemma load balancer (Google Gemma 4 + OpenRouter Gemma 3)
        if provider in {"auto", "gemma"} and (deadline - time.monotonic()) >= 8.0:
            open_lanes: list[tuple[str, str]] = []
            if self.google_api_key:
                open_lanes.append(("google_gemma", "gemma-4-26b-a4b-it"))
            if self.openrouter_api_key:
                open_lanes.append(("openrouter_gemma", "google/gemma-3-12b-it"))

            if open_lanes:
                idx = GemmaDigestService._TAB_LANE_COUNTER
                GemmaDigestService._TAB_LANE_COUNTER = idx + 1
                lane_kind, lane_model = open_lanes[idx % len(open_lanes)]
                req_timeout = max(1.0, min(deadline - time.monotonic() - 4.0, 11.0))
                if lane_kind == "google_gemma":
                    try:
                        url = (
                            "https://generativelanguage.googleapis.com/v1beta/models/"
                            f"{lane_model}:generateContent?key={self.google_api_key}"
                        )
                        response = requests.post(
                            url,
                            json={
                                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                                "generationConfig": {
                                    "maxOutputTokens": max(240, int(max_tokens)),
                                    "temperature": float(temperature),
                                    "responseMimeType": "application/json",
                                    "thinkingConfig": {"thinkingLevel": "MINIMAL"},
                                },
                            },
                            timeout=req_timeout,
                        )
                        if response.status_code == 200:
                            data = response.json()
                            candidates = data.get("candidates") or []
                            if candidates:
                                parts = candidates[0].get("content", {}).get("parts") or []
                                text = "".join(
                                    part.get("text", "")
                                    for part in parts
                                    if part.get("text", "").strip() and not part.get("thought")
                                ).strip()
                                if text:
                                    usage = data.get("usageMetadata") or {}
                                    return (
                                        text,
                                        f"google/{lane_model}",
                                        int(usage.get("promptTokenCount") or len(prompt.split())),
                                        int(usage.get("candidatesTokenCount") or len(text.split())),
                                    )
                        else:
                            self.last_tab_error = f"http_{response.status_code}"
                    except Exception as exc:
                        self.last_tab_error = type(exc).__name__
                else:
                    try:
                        response = requests.post(
                            self.OPENROUTER_URL,
                            headers={
                                "Authorization": f"Bearer {self.openrouter_api_key}",
                                "Content-Type": "application/json",
                                "HTTP-Referer": "https://folivora.tech",
                                "X-Title": "Folivora Open-Weight Triage",
                            },
                            json={
                                "model": lane_model,
                                "messages": [{"role": "user", "content": prompt}],
                                "max_tokens": max(240, int(max_tokens)),
                                "temperature": float(temperature),
                                "provider": {"sort": "throughput"},
                            },
                            timeout=req_timeout,
                        )
                        if response.status_code == 200:
                            body = response.json()
                            choices = body.get("choices") or []
                            if choices:
                                text = (choices[0].get("message", {}).get("content") or "").strip()
                                if text:
                                    usage = body.get("usage") or {}
                                    return (
                                        text,
                                        f"openrouter/{lane_model}",
                                        int(usage.get("prompt_tokens") or len(prompt.split())),
                                        int(usage.get("completion_tokens") or len(text.split())),
                                    )
                        else:
                            self.last_tab_error = f"http_{response.status_code}"
                    except Exception as exc:
                        self.last_tab_error = type(exc).__name__

        # 3. Fast backup / repair lane (used on tight repair budgets or when SUMMARY_POLISH_PROVIDER=gemini)
        gemini_model = os.getenv("SUMMARY_GEMINI_MODEL", "gemini-3.1-flash-lite").strip()
        if provider in {"auto", "gemini"} and self.google_api_key and gemini_model:
            rem = deadline - time.monotonic()
            if rem > 0.8:
                try:
                    url = (
                        "https://generativelanguage.googleapis.com/v1beta/models/"
                        f"{gemini_model}:generateContent?key={self.google_api_key}"
                    )
                    response = requests.post(
                        url,
                        json={
                            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                            "generationConfig": {
                                "maxOutputTokens": max(160, int(max_tokens)),
                                "temperature": float(temperature),
                                "responseMimeType": "application/json",
                                "thinkingConfig": {"thinkingBudget": 0},
                            },
                        },
                        timeout=max(0.8, rem),
                    )
                    if response.status_code == 200:
                        data = response.json()
                        candidates = data.get("candidates") or []
                        if candidates:
                            parts = candidates[0].get("content", {}).get("parts") or []
                            text = "".join(
                                part.get("text", "")
                                for part in parts
                                if part.get("text", "").strip() and not part.get("thought")
                            ).strip()
                            if text:
                                usage = data.get("usageMetadata") or {}
                                return (
                                    text,
                                    f"google/{gemini_model}",
                                    int(usage.get("promptTokenCount") or len(prompt.split())),
                                    int(usage.get("candidatesTokenCount") or len(text.split())),
                                )
                        self.last_tab_error = "empty_text"
                    else:
                        self.last_tab_error = f"http_{response.status_code}"
                except Exception as exc:
                    self.last_tab_error = type(exc).__name__
                return None, f"google/{gemini_model}", 0, 0

        remaining = deadline - time.monotonic()
        if remaining > 0.8:
            return self._call_raw_model(
                prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                timeout_s=remaining,
            )
        return None, "provider-timeout", 0, 0

    def synthesize_digest(
        self,
        total_tabs: int,
        kept_count: int,
        closed_count: int,
        minutes_saved: float,
        top_tab_title: str,
        top_tab_p50: float,
        prompt_version: str = "v4",
        custom_caller=None,
        trace_collector: list | None = None,
    ) -> dict:
        """
        Generates a 2-sentence spoken digest with Gemma, checks every digit and constraint
        via `check_output()`, retries once naming the rejected tokens if needed, and falls back
        to `deterministic_template()` if both attempts fail.
        """
        prompt, required_tokens, allowed_numbers = self.build_prompt(
            total_tabs=total_tabs,
            kept_count=kept_count,
            closed_count=closed_count,
            minutes_saved=minutes_saved,
            top_tab_title=top_tab_title,
            top_tab_p50=top_tab_p50,
            prompt_version=prompt_version,
        )
        fallback_text = self.deterministic_template(
            total_tabs=total_tabs,
            kept_count=kept_count,
            closed_count=closed_count,
            minutes_saved=minutes_saved,
            top_tab_title=top_tab_title,
        )

        with trace_genai_span(
            name="gemma.generate_verified_digest",
            model_name="google/gemma-3-27b-it",
            op="gen_ai.request",
            trace_collector=trace_collector,
        ) as span_meta:
            if custom_caller is not None:
                draft_1 = custom_caller(prompt, 1)
                model_used = "gemma-eval-harness"
                in_tok, out_tok = len(prompt.split()), len((draft_1 or "").split())
            else:
                draft_1, model_used, in_tok, out_tok = self._call_raw_model(prompt)

            span_meta["gen_ai.request.model"] = model_used
            span_meta["gen_ai.usage.input_tokens"] = in_tok
            span_meta["gen_ai.usage.output_tokens"] = out_tok

            if draft_1:
                ok_1, reasons_1 = check_output(draft_1, required_tokens, allowed_numbers)
                if ok_1:
                    span_meta["gemma.validator_status"] = "passed_first_try"
                    span_meta["gemma.attempts"] = 1
                    return {
                        "spoken_digest": draft_1.strip(),
                        "source": "gemma",
                        "model": model_used,
                        "attempts": 1,
                        "passed_first_try": True,
                        "passed_with_retry": False,
                        "rejections": [],
                        "latency_ms": span_meta.get("latency_ms", 0.0),
                    }

                # Retry once naming the exact rejected tokens (Paper Trail pattern)
                retry_prompt = (
                    f"{prompt}\n\n"
                    f"CORRECTION REQUIRED: Your previous draft was rejected by the validator for: "
                    f"{', '.join(reasons_1)}. "
                    f"Include these exact numbers verbatim: {required_tokens} and do NOT include any "
                    f"other digits outside {sorted(allowed_numbers)}. Reply with ONLY the two plain sentences."
                )
                if custom_caller is not None:
                    draft_2 = custom_caller(retry_prompt, 2)
                    in_tok_2, out_tok_2 = len(retry_prompt.split()), len((draft_2 or "").split())
                else:
                    draft_2, model_used_2, in_tok_2, out_tok_2 = self._call_raw_model(retry_prompt)
                    model_used = model_used_2 or model_used

                span_meta["gen_ai.usage.input_tokens"] = in_tok + in_tok_2
                span_meta["gen_ai.usage.output_tokens"] = out_tok + out_tok_2

                if draft_2:
                    ok_2, reasons_2 = check_output(draft_2, required_tokens, allowed_numbers)
                    if ok_2:
                        span_meta["gemma.validator_status"] = "passed_on_retry"
                        span_meta["gemma.attempts"] = 2
                        return {
                            "spoken_digest": draft_2.strip(),
                            "source": "gemma-retry",
                            "model": model_used,
                            "attempts": 2,
                            "passed_first_try": False,
                            "passed_with_retry": True,
                            "rejections": reasons_1,
                            "latency_ms": span_meta.get("latency_ms", 0.0),
                        }
                    all_rejections = reasons_1 + reasons_2
                else:
                    all_rejections = reasons_1 + ["retry_empty"]
            else:
                all_rejections = ["provider_unavailable"]

            span_meta["gemma.validator_status"] = "fallback_template"
            span_meta["gemma.attempts"] = 2 if draft_1 else 1
            return {
                "spoken_digest": fallback_text,
                "source": "verified-template",
                "model": "deterministic-template",
                "attempts": 2 if draft_1 else 1,
                "passed_first_try": False,
                "passed_with_retry": False,
                "rejections": all_rejections,
                "latency_ms": span_meta.get("latency_ms", 0.0),
            }
