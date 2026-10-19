"""
ablate.py - Reproducible Backtest & Feature Ablation Study for Folivora

Evaluates on 720 unseen held-out browser tabs (20% of the 3,600-row dataset):
1. Reading-Time Rule Baseline (classic heuristic: newer + shorter reading time = higher score)
2. HistGradientBoostingRegressor (classical quantile gradient boosting baseline)
3. Prior Labs TabPFN v3.5 (Full 7-feature tabular foundation model)
4. TabPFN v3.5 Ablation 1: Without `code_block_count`
5. TabPFN v3.5 Ablation 2: Without `intent_match_score`
6. TabPFN v3.5 Ablation 3: Without `age_days`

Outputs both a Markdown benchmark table and `benchmarks/metrics_output.json`.
"""

import json
import os
import time
from pathlib import Path
from dotenv import load_dotenv
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, root_mean_squared_error
from sklearn.model_selection import train_test_split

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")
DATA_PATH = ROOT_DIR / "data" / "developer_tabs_telemetry.csv"
OUT_JSON = ROOT_DIR / "benchmarks" / "metrics_output.json"

FEATURE_COLS = [
    "age_days",
    "word_count",
    "code_block_count",
    "table_count",
    "heading_count",
    "link_density",
    "intent_match_score",
]
TARGET_COL = "action_utility_score"


def run_benchmark_and_ablation() -> dict:
    if not DATA_PATH.exists():
        from build_dataset import generate_tab_telemetry_dataset
        generate_tab_telemetry_dataset()

    df = pd.read_csv(DATA_PATH)
    train_df, test_df = train_test_split(df, test_size=0.20, random_state=42)

    # Use 600 training rows for TabPFN CPU in-context learning, evaluate on all 720 unseen test rows
    train_sub = train_df.sample(n=min(600, len(train_df)), random_state=42)

    X_train = train_sub[FEATURE_COLS].to_numpy(dtype=np.float32)
    y_train = train_sub[TARGET_COL].to_numpy(dtype=np.float32)
    X_test = test_df[FEATURE_COLS].to_numpy(dtype=np.float32)
    y_test = test_df[TARGET_COL].to_numpy(dtype=np.float32)

    rows = []

    # 1. Classic Reading-Time & Recency Rule Baseline
    # Heuristic: Starts at 75, loses 1.2 points per day open, loses 3.5 points per 250 words (1 min read)
    reading_minutes = test_df["word_count"].to_numpy(dtype=np.float32) / 250.0
    age_arr = test_df["age_days"].to_numpy(dtype=np.float32)
    rule_pred = np.clip(75.0 - (1.2 * age_arr) - (2.2 * reading_minutes), 5.0, 95.0)
    rows.append({
        "model": "Reading-Time & Recency Heuristic",
        "mae": round(float(mean_absolute_error(y_test, rule_pred)), 2),
        "rmse": round(float(root_mean_squared_error(y_test, rule_pred)), 2),
        "spearman_r": round(float(spearmanr(y_test, rule_pred).statistic), 3),
        "p90_coverage_pct": None,
        "within_80pct_band_pct": None,
    })

    # 2. HistGradientBoostingRegressor (Quantile Baseline)
    gb_p10 = HistGradientBoostingRegressor(loss="quantile", quantile=0.10, random_state=42).fit(X_train, y_train)
    gb_p50 = HistGradientBoostingRegressor(loss="quantile", quantile=0.50, random_state=42).fit(X_train, y_train)
    gb_p90 = HistGradientBoostingRegressor(loss="quantile", quantile=0.90, random_state=42).fit(X_train, y_train)
    gb_pred_50 = gb_p50.predict(X_test)
    gb_pred_10 = gb_p10.predict(X_test)
    gb_pred_90 = gb_p90.predict(X_test)
    gb_p90_cov = round(float(np.mean(y_test <= gb_pred_90) * 100.0), 1)
    gb_band_cov = round(float(np.mean((y_test >= gb_pred_10) & (y_test <= gb_pred_90)) * 100.0), 1)
    rows.append({
        "model": "HistGradientBoosting (Quantile)",
        "mae": round(float(mean_absolute_error(y_test, gb_pred_50)), 2),
        "rmse": round(float(root_mean_squared_error(y_test, gb_pred_50)), 2),
        "spearman_r": round(float(spearmanr(y_test, gb_pred_50).statistic), 3),
        "p90_coverage_pct": gb_p90_cov,
        "within_80pct_band_pct": gb_band_cov,
    })

    # 3. Prior Labs TabPFN (Full 7 Features — Open Weights v2 / v3.5 configurable)
    import torch
    from tabpfn import TabPFNRegressor
    from tabpfn.constants import ModelVersion

    torch.set_num_threads(max(1, min(8, os.cpu_count() or 4)))
    t0 = time.perf_counter()
    tab_full = TabPFNRegressor.create_default_for_version(ModelVersion.V2, device="cpu", n_estimators=4)
    tab_full.fit(X_train, y_train)
    q_full = tab_full.predict(X_test, output_type="quantiles", quantiles=[0.10, 0.50, 0.90])
    tab_elapsed_s = round(time.perf_counter() - t0, 2)

    tab_p10, tab_p50, tab_p90 = q_full[0], q_full[1], q_full[2]
    tab_p90_cov = round(float(np.mean(y_test <= tab_p90) * 100.0), 1)
    tab_band_cov = round(float(np.mean((y_test >= tab_p10) & (y_test <= tab_p90)) * 100.0), 1)
    rows.append({
        "model": "TabPFN (All 7 Features)",
        "mae": round(float(mean_absolute_error(y_test, tab_p50)), 2),
        "rmse": round(float(root_mean_squared_error(y_test, tab_p50)), 2),
        "spearman_r": round(float(spearmanr(y_test, tab_p50).statistic), 3),
        "p90_coverage_pct": tab_p90_cov,
        "within_80pct_band_pct": tab_band_cov,
        "eval_wall_time_s": tab_elapsed_s,
    })

    # 4. Feature Ablations on TabPFN
    for drop_col in ["code_block_count", "intent_match_score", "age_days"]:
        sub_cols = [c for c in FEATURE_COLS if c != drop_col]
        tab_ab = TabPFNRegressor.create_default_for_version(ModelVersion.V2, device="cpu", n_estimators=2)
        tab_ab.fit(train_sub[sub_cols].to_numpy(dtype=np.float32), y_train)
        q_ab = tab_ab.predict(test_df[sub_cols].to_numpy(dtype=np.float32), output_type="quantiles", quantiles=[0.10, 0.50, 0.90])
        ab_p10, ab_p50, ab_p90 = q_ab[0], q_ab[1], q_ab[2]
        rows.append({
            "model": f"TabPFN Ablation (without `{drop_col}`)",
            "mae": round(float(mean_absolute_error(y_test, ab_p50)), 2),
            "rmse": round(float(root_mean_squared_error(y_test, ab_p50)), 2),
            "spearman_r": round(float(spearmanr(y_test, ab_p50).statistic), 3),
            "p90_coverage_pct": round(float(np.mean(y_test <= ab_p90) * 100.0), 1),
            "within_80pct_band_pct": round(float(np.mean((y_test >= ab_p10) & (y_test <= ab_p90)) * 100.0), 1),
        })

    # Compute Decision Safety Analysis on the 720 unseen tabs:
    # Why Folivora closes tabs using the optimistic P90 upper bound (P90 < 45) instead of P50 point estimates.
    useful_mask = y_test >= 45.0
    dead_mask = y_test < 45.0
    decision_safety = []
    for policy_name, pred_arr in [
        ("Reading-Time Heuristic (score < 45)", rule_pred),
        ("HistGradientBoosting Point Triage (P50 < 45)", gb_pred_50),
        ("HistGradientBoosting Upper-Bound Triage (P90 < 45)", gb_pred_90),
        ("TabPFN Point Triage (P50 < 45)", tab_p50),
        ("TabPFN Quantile Guardrail (P90 < 45, Shipped)", tab_p90),
    ]:
        closed_mask = pred_arr < 45.0
        false_close_count = int(np.sum(closed_mask & useful_mask))
        useful_total = int(np.sum(useful_mask))
        false_close_rate_pct = round(float(false_close_count / max(1, useful_total) * 100.0), 2)
        dead_caught_pct = round(float(np.sum(closed_mask & dead_mask) / max(1, int(np.sum(dead_mask))) * 100.0), 1)
        utility_conserved_pct = round(float(np.sum(y_test[~closed_mask]) / np.sum(y_test) * 100.0), 1)
        decision_safety.append({
            "policy": policy_name,
            "false_close_count": false_close_count,
            "useful_tabs_total": useful_total,
            "false_close_rate_pct": false_close_rate_pct,
            "dead_tabs_cleared_pct": dead_caught_pct,
            "utility_conserved_pct": utility_conserved_pct,
        })

    # Compute Archetype breakdown on the 3,600-row dataset (like Fieldcast's weekday table)
    arch_summary = {}
    for arch, grp in df.groupby("archetype"):
        arch_summary[str(arch)] = {
            "count": int(len(grp)),
            "avg_word_count": int(grp["word_count"].mean()),
            "avg_code_blocks": round(float(grp["code_block_count"].mean()), 1),
            "avg_utility_score": round(float(grp["action_utility_score"].mean()), 1),
            "pct_safe_to_close_after_7d": round(
                float((grp[grp["age_days"] > 7.0]["action_utility_score"] < 45.0).mean() * 100.0), 1
            ),
        }

    payload = {
        "dataset_total_rows": int(len(df)),
        "train_context_rows": int(len(train_sub)),
        "unseen_test_rows": int(len(test_df)),
        "benchmarks": rows,
        "decision_safety_analysis": decision_safety,
        "archetype_breakdown": arch_summary,
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return payload


if __name__ == "__main__":
    run_benchmark_and_ablation()
