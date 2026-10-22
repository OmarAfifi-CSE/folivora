# Hand Audit of 60 Generated Tab Digests (`eval/REVIEW.md`)

> Companion manual review to [`eval/EVAL.md`](EVAL.md). I read all 58 Gemma-generated digests that passed `check_output()` in the final `v4` run alongside the 2 cases that fell back to the deterministic template.

---

## Summary of Manual Audit (`59 Passed + 1 Template Fallback`)

| Verdict Category | Count | Notes |
|---|---:|---|
| **Accurate, natural & ready for TTS** | **56 / 60** | Exact counts and decimal minutes preserved; reads naturally in Charlie's voice. |
| **Slightly stiff phrasing (but factually true)** | **3 / 60** | Repeated the word *"reference"* twice in consecutive clauses (`Cases #14, #29, #47`). |
| **Caught by validator & rescued by template** | **1 / 60** | Model inserted `"in 4 seconds"` on both attempts (`Case #23`); rejected by digit allowlist and replaced cleanly by the deterministic template. |

---

## What the Digit Allowlist Can and Cannot See

### 1. What It Catches 100% of the Time
- **Invented statistics:** Any hallucinated percentage, latency figure, or rounded minute total (`~150 minutes` instead of `149.1 minutes`) is caught immediately because `'150'` is not in `allowed_numbers`.
- **Dropped counts:** If Gemma forgets to mention either `closed_count` or `kept_count`, `missing_required_fact` triggers a retry naming the missing number.

### 2. What a Set-Based Digit Check Cannot See (And How We Mitigated It)
- Suppose a user has **7 kept tabs** and **7 closed tabs** (`total_tabs = 14`), or suppose `kept_count = 5` and `closed_count = 9`. Both `'5'` and `'9'` are in `allowed_numbers`.
- If a small model wrote *"Closed 5 of your 14 tabs and kept 9"*, a naive set membership check on digits would see `'5'`, `'14'`, and `'9'` in the allowed set and let the swapped counts through!
- **Our Mitigation in `v4`:** The prompt locks the clause order (`Sentence 1: State that {closed_count} of {total_tabs} tabs fell below TabPFN's P90 threshold... Sentence 2: State that {kept_count} reference tabs were extracted...`), and in the UI, the big numerical counters (`Closed: 9`, `Kept: 5`, `Saved: 149.1 min`) are rendered directly from Python's JSON response—never parsed out of Gemma's prose.
