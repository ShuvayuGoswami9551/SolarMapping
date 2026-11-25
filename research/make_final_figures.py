from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


BASE_DIR = Path(__file__).resolve().parent
RESULTS_DIR = BASE_DIR / "results"
FEBRUARY_DIR = RESULTS_DIR / "february_evaluation"
OUTPUT_DIR = RESULTS_DIR / "final_figures"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

data = pd.read_csv(
    FEBRUARY_DIR / "february_predictions.csv"
)

data["hour_ending_utc"] = pd.to_datetime(
    data["hour_ending_utc"], utc=True
)

data = data.sort_values("hour_ending_utc")

eligible = (
    data["eligible"]
    .astype(str)
    .str.lower()
    .eq("true")
)

valid = data.loc[eligible].copy()

if valid.empty:
    raise ValueError("No eligible February hours found.")

columns = ["ghi_target_wm2", "baseline", "linear"]

if not np.isfinite(valid[columns].to_numpy()).all():
    raise ValueError("Eligible rows contain invalid values.")

colors = {
    "observations": "#007f86",
    "baseline": "#d68b12",
    "linear": "#8055ad",
}

# Time series: preserve missing-hour gaps.
fig, ax = plt.subplots(figsize=(14, 5))

for column, label, color in [
    (
        "ghi_target_wm2",
        "SURFRAD observations",
        colors["observations"],
    ),
    (
        "baseline",
        "Uncorrected archive",
        colors["baseline"],
    ),
    (
        "linear",
        "Frozen January linear correction",
        colors["linear"],
    ),
]:
    ax.plot(
        data["hour_ending_utc"],
        data[column].where(eligible),
        label=label,
        color=color,
        linewidth=1.1,
        alpha=0.85,
    )

ax.set_title(
    "Bondville: February-initialized runs vs. observations\n"
    "Hours 1–24 after daily 00:00 UTC initialization"
)
ax.set_xlabel("Hour ending (UTC)")
ax.set_ylabel("Hourly mean irradiance (W/m²)")
ax.legend()
ax.grid(alpha=0.25)
fig.autofmt_xdate()
fig.tight_layout()

fig.savefig(
    OUTPUT_DIR / "february_timeseries.png",
    dpi=200,
)
plt.close(fig)

# Side-by-side scatter plots with identical scales.
upper = max(valid[columns].max().max(), 1) * 1.05

fig, axes = plt.subplots(
    1, 2, figsize=(12, 6), sharex=True, sharey=True
)

for ax, candidate, title in zip(
    axes,
    ["baseline", "linear"],
    ["Uncorrected archive", "Frozen January correction"],
):
    ax.scatter(
        valid["ghi_target_wm2"],
        valid[candidate],
        s=14,
        alpha=0.45,
        color=colors[candidate],
    )

    ax.plot(
        [0, upper],
        [0, upper],
        "--",
        color="gray",
        linewidth=1,
    )

    ax.set_xlim(0, upper)
    ax.set_ylim(0, upper)
    ax.set_aspect("equal", adjustable="box")
    ax.set_title(title)
    ax.set_xlabel("Observed irradiance (W/m²)")
    ax.grid(alpha=0.25)

axes[0].set_ylabel("Predicted irradiance (W/m²)")

fig.suptitle(
    "Bondville: February evaluation on identical eligible hours"
)
fig.tight_layout()

fig.savefig(
    OUTPUT_DIR / "february_scatter_comparison.png",
    dpi=200,
)
plt.close(fig)

# Combine the original test metrics without recomputing them.
january = pd.read_csv(
    RESULTS_DIR / "correction_experiment" / "test_metrics.csv"
)
february = pd.read_csv(
    FEBRUARY_DIR / "february_metrics.csv"
)

january["evaluation_period"] = (
    "January 26–31 initialized runs"
)
february["evaluation_period"] = (
    "February 1–28 initialized runs"
)

table_columns = [
    "evaluation_period",
    "candidate",
    "evaluation_subset",
    "matched_hours",
    "mae_wm2",
    "rmse_wm2",
    "mean_bias_wm2",
]

combined = pd.concat(
    [
        january[table_columns],
        february[table_columns],
    ],
    ignore_index=True,
)

combined.to_csv(
    OUTPUT_DIR / "final_test_metrics.csv",
    index=False,
)

print(f"February eligible hours plotted: {len(valid)}")
print("\nFINAL RESULTS")
print(combined.round(3).to_string(index=False))
print(f"\nFigures and table saved in: {OUTPUT_DIR}")