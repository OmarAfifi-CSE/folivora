"""
build_dataset.py - Generates the 3,600-row browser tab triage dataset (`data/developer_tabs_telemetry.csv`)

Models how a human reader decides whether an open browser tab is worth reading/keeping
versus safe to close, driven primarily by:
1. `intent_match_score` (0.0 to 1.0): How strongly the page matches what the user cares about right now.
   If the user asks for "Flutter" and a page has 0 mentions of Flutter (`intent_match_score < 0.15`),
   its utility for the current session is near-zero (8% - 20%), even if it is a well-written article on another topic.
2. `information_density` (stored in `code_block_count` / factual signals column, 0 to 35): Concrete facts, metrics, and structured takeaways.
3. `table_count` (0 to 12): Structured comparisons and reference tables.
4. `heading_count` (1 to 45): Clear section organization.
5. `word_count` (80 to 9500): Reading length.
6. `link_density` (0.2 to 25.0): Outbound links per 100 words (high values indicate noisy directory hubs).
7. `age_days` (0.1 to 45.0): How long the tab has been sitting open.
"""

from pathlib import Path
import json
import numpy as np
import pandas as pd

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "developer_tabs_telemetry.csv"
HOF_CATALOG_PATH = Path(__file__).resolve().parent.parent / "data" / "dev_hall_of_fame_catalog.json"


def generate_tab_telemetry_dataset(n_rows: int = 3600, seed: int = 20261020) -> pd.DataFrame:
    rng = np.random.default_rng(seed)

    age_days = np.clip(rng.lognormal(mean=1.6, sigma=0.95, size=n_rows), 0.1, 45.0).round(2)
    archetype = rng.choice(["deep_guide", "long_essay", "tech_brief", "nav_hub"], size=n_rows, p=[0.35, 0.30, 0.20, 0.15])

    word_count = np.zeros(n_rows, dtype=np.int32)
    code_block_count = np.zeros(n_rows, dtype=np.int32)  # Factual & technical density signals
    table_count = np.zeros(n_rows, dtype=np.int32)
    heading_count = np.zeros(n_rows, dtype=np.int32)
    link_density = np.zeros(n_rows, dtype=np.float64)

    for i, arch in enumerate(archetype):
        if arch == "deep_guide":
            word_count[i] = int(np.clip(rng.normal(1450, 650), 200, 5500))
            code_block_count[i] = int(np.clip(rng.poisson(7.5), 2, 35))
            table_count[i] = int(np.clip(rng.poisson(1.8), 0, 10))
            heading_count[i] = int(np.clip(word_count[i] / 150 + rng.normal(0, 2), 3, 35))
            link_density[i] = float(np.clip(rng.normal(3.0, 1.3), 0.4, 10.0))
        elif arch == "long_essay":
            word_count[i] = int(np.clip(rng.normal(3900, 1500), 1000, 9500))
            code_block_count[i] = int(rng.choice([0, 0, 1, 2], p=[0.65, 0.15, 0.12, 0.08]))
            table_count[i] = int(rng.choice([0, 1], p=[0.88, 0.12]))
            heading_count[i] = int(np.clip(word_count[i] / 450 + rng.normal(0, 1.5), 1, 18))
            link_density[i] = float(np.clip(rng.normal(2.2, 1.1), 0.2, 8.0))
        elif arch == "tech_brief":
            word_count[i] = int(np.clip(rng.normal(980, 420), 150, 3200))
            code_block_count[i] = int(np.clip(rng.poisson(4.5), 1, 20))
            table_count[i] = int(np.clip(rng.poisson(1.0), 0, 6))
            heading_count[i] = int(np.clip(rng.poisson(5.5), 2, 22))
            link_density[i] = float(np.clip(rng.normal(4.2, 1.6), 0.8, 12.0))
        else:  # nav_hub
            word_count[i] = int(np.clip(rng.normal(650, 320), 80, 2200))
            code_block_count[i] = int(rng.choice([0, 1], p=[0.90, 0.10]))
            table_count[i] = int(np.clip(rng.poisson(1.5), 0, 10))
            heading_count[i] = int(np.clip(rng.poisson(9.0), 2, 45))
            link_density[i] = float(np.clip(rng.normal(14.5, 4.0), 5.0, 25.0))

    link_density = np.round(link_density, 2)
    intent_match_score = np.round(rng.beta(a=1.3, b=1.5, size=n_rows), 3)

    # Blend in real feature profiles from the 65 DEV Hall of Fame articles (both matched and unmatched queries)
    if HOF_CATALOG_PATH.exists():
        try:
            hof_data = json.loads(HOF_CATALOG_PATH.read_text(encoding="utf-8"))
            exhibits = hof_data.get("exhibits") or []
            for idx_ex, ex in enumerate(exhibits[:65]):
                # Row A: Strong topic match for this Hall of Fame article
                r_hi = idx_ex * 2
                if r_hi + 1 < n_rows:
                    word_count[r_hi] = int(np.clip(ex.get("word_count", 1200), 120, 9500))
                    code_block_count[r_hi] = int(np.clip(ex.get("code_block_count", 6), 1, 35))
                    table_count[r_hi] = int(np.clip(ex.get("table_count", 0), 0, 10))
                    heading_count[r_hi] = int(np.clip(ex.get("heading_count", 6), 1, 35))
                    link_density[r_hi] = float(np.clip(ex.get("link_density", 2.5), 0.3, 22.0))
                    intent_match_score[r_hi] = round(float(rng.uniform(0.76, 0.97)), 3)
                    archetype[r_hi] = "deep_guide"

                    # Row B: Unrelated query for the same Hall of Fame article (0 mentions)
                    r_lo = r_hi + 1
                    word_count[r_lo] = word_count[r_hi]
                    code_block_count[r_lo] = code_block_count[r_hi]
                    table_count[r_lo] = table_count[r_hi]
                    heading_count[r_lo] = heading_count[r_hi]
                    link_density[r_lo] = link_density[r_hi]
                    intent_match_score[r_lo] = round(float(rng.uniform(0.01, 0.06)), 3)
                    archetype[r_lo] = "deep_guide"
        except Exception:
            pass

    # Core utility formulation:
    # Topic relevance (`intent_match_score`) acts as the primary gate!
    # If a tab does NOT match the user's focus topic (`intent_match_score < 0.20`), its utility drops below 25.
    intrinsic_quality = (
        22.0 * np.tanh(code_block_count / 5.0)
        + 7.0 * np.tanh(table_count / 2.5)
        + 8.0 * np.tanh(heading_count / 8.0)
        - np.maximum(0.0, (link_density - 8.5) * 1.3)
        - 12.0 * np.log1p(age_days) / np.log1p(45.0)
    )

    # Strong multiplicative + additive coupling with `intent_match_score`
    intent_gate = 1.0 / (1.0 + np.exp(-10.0 * (intent_match_score - 0.32)))
    raw_score = (
        8.0
        + (52.0 * (intent_match_score ** 1.1))
        + (intrinsic_quality * (0.25 + 0.85 * intent_gate))
        + rng.normal(0.0, 4.2, size=n_rows)
    )
    action_utility_score = np.clip(raw_score, 3.0, 98.5).round(2)

    df = pd.DataFrame({
        "age_days": age_days,
        "word_count": word_count,
        "code_block_count": code_block_count,
        "table_count": table_count,
        "heading_count": heading_count,
        "link_density": link_density,
        "intent_match_score": intent_match_score,
        "archetype": archetype,
        "action_utility_score": action_utility_score,
    })
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(DATA_PATH, index=False)
    print(f"Saved {len(df)} rows to {DATA_PATH}")
    return df


if __name__ == "__main__":
    generate_tab_telemetry_dataset()
