# Evaluation Report: Gemma Output Grounding (`eval/EVAL.md`)

> **Reproducibility:** Run `python eval/run_eval.py` to execute all 60 tab-pile test scenarios across prompt versions `v1` through `v4` against `check_output()` in [`src/gemma_service.py`](../src/gemma_service.py).

---

## 1. Why a Language Model Needs a Strict Digit Gate

In **Folivora**, Prior Labs' **TabPFN** scores and ranks your open browser tabs, and plain Python computes the exact numbers:
- `total_tabs` (e.g., `14`)
- `closed_count` (e.g., `9` tabs below the `P90` threshold)
- `kept_count` (e.g., `5` high-utility reference tabs)
- `minutes_saved` (e.g., `149.1` minutes of reading saved at 220 words/minute)

Gemma's only job is turning those locked numbers into a natural 2-sentence spoken summary for ElevenLabs to read aloud. It is never allowed to round a decimal, invent an extra number, or add markdown formatting that breaks text-to-speech.

### The 4 Rules Enforced by `check_output()`
A candidate draft from Gemma passes if and only if:
1. **Strict Digit Allowlist:** Every numeric literal in the text (`\b\d+(?:\.\d+)?\b`) must exist in Python's verified `allowed_numbers` set. Rounding `149.1` to `"about 150"` or inventing `"in 3 seconds"` is immediately rejected.
2. **Required Facts Verbatim:** `closed_count`, `kept_count`, and `minutes_saved` must all appear verbatim.
3. **Spoken Length Bound:** Between `10` and `42` words so the audio brief stays under 15 seconds.
4. **Zero AI Preambles or Markdown:** Rejects `"Sure, here is..."`, `"**bold**"`, or cliches (`delve`, `game-changer`).

If Attempt 1 fails, `synthesize_digest()` retries **once**, naming the exact rejected tokens in the retry prompt. If Attempt 2 also fails, the app serves our deterministic template and displays a `Verified Template` badge on the UI card.

---

## 2. Four Prompts on the Same 60 Test Piles (`n = 60`)

| Prompt Version | What Changed | Passed First Try | Printed (with 1 Retry) | Used Backup Template |
|---|---|---:|---:|---:|
| **v1** | Plain instruction (*"Summarize this tab cleanup in two short sentences"*) | 32 / 60 (53.3%) | 53 / 60 (88.3%) | 7 / 60 (11.7%) |
| **v2** | Added explicit 38-word cap + *"Plain text only"* | 45 / 60 (75.0%) | 59 / 60 (98.3%) | 1 / 60 (1.7%) |
| **v3** | Added *"Copy numbers character-for-character without rounding; do not invent other numbers"* | 50 / 60 (83.3%) | 58 / 60 (96.7%) | 2 / 60 (3.3%) |
| **v4 (Shipped)** | Explicit 2-sentence structure + retry prompt names the exact rejected tokens | **55 / 60 (91.7%)** | **59 / 60 (98.3%)** | **1 / 60 (1.7%)** |

---

## 3. What Reading the Rejections Found

1. **Why `v1` failed 46.7% of first tries:**
   - 20 of the 28 first-try failures in `v1` were caused by Gemma trying to sound conversational by **rounding decimal minutes** (turning `79.6 minutes` into `"about 80 minutes"`) and adding `**bold**` around the closed tab count.
   - Telling `v3` and `v4` to *"Copy numbers character-for-character (never round decimals)"* eliminated rounding failures completely.
2. **Why `v3` still had 10 first-try rejections:**
   - Even when told not to round, the model occasionally added a harmless-looking phrase like *"in 3 seconds"* or *"1-page cheat sheet"* written with a digit `"1"` or `"3"` that wasn't in the input facts.
   - Writing `"one-page cheat sheet"` in words in the prompt and naming the exact offending digit (`unverified_or_rounded_number:4`) in the retry prompt pushed total verified Gemma delivery to **59 / 60 (98.3%)**.
