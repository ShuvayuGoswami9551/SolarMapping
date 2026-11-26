from pathlib import Path
import json

import numpy as np
import pandas as pd


BASE_DIR = Path(__file__).resolve().parent
RESULTS_DIR = BASE_DIR / "results"
INPUT_FILE = RESULTS_DIR / "bondville_january_comparison.csv"
OUTPUT_DIR = RESULTS_DIR / "correction_experiment"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

data = pd.read_csv(INPUT_FILE)

for column in ["hour_ending_utc", "run_initialization_utc"]:
    data[column] = pd.to_datetime(data[column], utc=True)

eligible = (
    data["eligible"]
    .astype(str)
    .str.lower()
    .eq("true")
)

data = data.loc[eligible].copy()

numeric_columns = ["model_ghi_wm2", "ghi_target_wm2"]

if not np.isfinite(data[numeric_columns].to_numpy()).all():
    raise ValueError("Eligible rows contain missing or infinite values.")

if data["hour_ending_utc"].duplicated().any():
    raise ValueError("Duplicate target timestamps.")

run_time = data["run_initialization_utc"]

start = pd.Timestamp("2025-01-01", tz="UTC")
validation_start = pd.Timestamp("2025-01-21", tz="UTC")
test_start = pd.Timestamp("2025-01-26", tz="UTC")
end = pd.Timestamp("2025-02-01", tz="UTC")

if not ((run_time >= start) & (run_time < end)).all():
    raise ValueError("Unexpected run initialization outside January.")

data["partition"] = np.select(
    [
        run_time < validation_start,
        run_time < test_start,
    ],
    ["training", "validation"],
    default="test",
)

for name in ["training", "validation", "test"]:
    if data.loc[data["partition"].eq(name)].empty:
        raise ValueError(f"No eligible rows in {name} partition.")

training = data.loc[data["partition"].eq("training")]

x_train = training["model_ghi_wm2"].to_numpy()
y_train = training["ghi_target_wm2"].to_numpy()

offset = float(np.mean(y_train - x_train))

design = np.column_stack([np.ones(len(x_train)), x_train])
coefficients, _, rank, _ = np.linalg.lstsq(
    design, y_train, rcond=None
)

if rank != 2:
    raise ValueError("Training data cannot identify a linear correction.")

intercept, slope = map(float, coefficients)

x = data["model_ghi_wm2"].to_numpy()

data["baseline"] = x
data["additive"] = np.maximum(0, x + offset)
data["linear"] = np.maximum(0, intercept + slope * x)

candidates = ["baseline", "additive", "linear"]


def score(frame, prediction_column, subset, partition):
    error = (
        frame[prediction_column] - frame["ghi_target_wm2"]
    ).to_numpy()

    return {
        "partition": partition,
        "candidate": prediction_column,
        "evaluation_subset": subset,
        "matched_hours": len(frame),
        "mae_wm2": float(np.mean(np.abs(error))),
        "rmse_wm2": float(np.sqrt(np.mean(error ** 2))),
        "mean_bias_wm2": float(np.mean(error)),
    }


def score_subsets(frame, prediction_column, partition):
    rows = [
        score(
            frame,
            prediction_column,
            "all_valid_hours",
            partition,
        )
    ]

    above_threshold = frame.loc[frame["ghi_target_wm2"] > 20]

    if not above_threshold.empty:
        rows.append(
            score(
                above_threshold,
                prediction_column,
                "observed_ghi_above_20_wm2",
                partition,
            )
        )

    return rows


validation = data.loc[data["partition"].eq("validation")]

if not (validation["ghi_target_wm2"] > 20).any():
    raise ValueError("No above-threshold validation hours.")

validation_rows = []

for candidate in candidates:
    validation_rows.extend(
        score_subsets(validation, candidate, "validation")
    )

validation_metrics = pd.DataFrame(validation_rows)

selection_scores = (
    validation_metrics.loc[
        validation_metrics["evaluation_subset"].eq(
            "observed_ghi_above_20_wm2"
        )
    ]
    .set_index("candidate")["rmse_wm2"]
)

selected = min(
    candidates,
    key=lambda candidate: selection_scores.loc[candidate],
)

test = data.loc[data["partition"].eq("test")]

test_rows = []
for candidate in dict.fromkeys(["baseline", selected]):
    test_rows.extend(score_subsets(test, candidate, "test"))

test_metrics = pd.DataFrame(test_rows)

parameters = {
    "additive_offset_wm2": offset,
    "linear_intercept_wm2": intercept,
    "linear_slope": slope,
    "fitting_subset": "all eligible training hours",
    "prediction_rule": (
        "Corrected predictions clipped to a minimum of zero; "
        "no observation-based application rule"
    ),
    "selection_metric": (
        "Validation RMSE for observed irradiance above 20 W/m²"
    ),
    "selected_candidate": selected,
    "refit_after_selection": False,
    "interpretation": (
        "Exploratory January experiment; full month previously inspected"
    ),
}

validation_metrics.to_csv(
    OUTPUT_DIR / "validation_metrics.csv", index=False
)
test_metrics.to_csv(
    OUTPUT_DIR / "test_metrics.csv", index=False
)

# Save validation predictions and only the evaluated test predictions.
prediction_columns = ["baseline"]
if selected != "baseline":
    prediction_columns.append(selected)

metadata_columns = [
    "hour_ending_utc",
    "run_initialization_utc",
    "lead_hours_from_initialization",
    "partition",
    "ghi_target_wm2",
]

data.loc[
    data["partition"].eq("validation"),
    metadata_columns + candidates,
].to_csv(OUTPUT_DIR / "validation_predictions.csv", index=False)

test[
    metadata_columns + prediction_columns
].to_csv(OUTPUT_DIR / "test_predictions.csv", index=False)

with (OUTPUT_DIR / "parameters.json").open(
    "w", encoding="utf-8"
) as file:
    json.dump(parameters, file, indent=2)

print("Eligible hours by partition:")
print(data.groupby("partition").size().to_string())

print(f"\nAdditive offset: {offset:.4f} W/m²")
print(f"Linear intercept: {intercept:.4f} W/m²")
print(f"Linear slope: {slope:.6f}")

print("\nVALIDATION METRICS")
print(validation_metrics.round(3).to_string(index=False))

print(f"\nSelected candidate: {selected}")
print("Selection: lowest validation above-20 W/m² RMSE")

print("\nTEST METRICS")
print(test_metrics.round(3).to_string(index=False))

print(f"\nResults saved in: {OUTPUT_DIR}")