from pathlib import Path
import json

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import RandomForestRegressor


BASE_DIR = Path(__file__).resolve().parent

INPUT_FILE = (
    BASE_DIR
    / "results"
    / "feature_dataset"
    / "bondville_january_features_all_hours.csv"
)

OUTPUT_DIR = (
    BASE_DIR / "results" / "random_forest_experiment"
)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

FEATURES = [
    "model_ghi_wm2",
    "cloud_cover_percent",
    "temperature_c",
    "relative_humidity_percent",
    "wind_speed_ms",
    "surface_pressure_hpa",
    "lead_hours_from_initialization",
]

CANDIDATES = [
    "baseline",
    "additive",
    "linear",
    "random_forest",
]

RF_PARAMETERS = {
    "n_estimators": 300,
    "max_depth": 5,
    "min_samples_leaf": 10,
    "max_features": 1.0,
    "bootstrap": True,
    "random_state": 42,
    "n_jobs": -1,
}


def read_boolean(series, name):
    values = series.astype(str).str.strip().str.lower()

    if not values.isin(["true", "false"]).all():
        raise ValueError(f"Invalid Boolean values in {name}.")

    return values.eq("true")


def score(frame, candidate, subset, partition):
    errors = (
        frame[candidate] - frame["ghi_target_wm2"]
    ).to_numpy(dtype=float)

    return {
        "partition": partition,
        "candidate": candidate,
        "evaluation_subset": subset,
        "matched_hours": len(frame),
        "mae_wm2": float(np.mean(np.abs(errors))),
        "rmse_wm2": float(np.sqrt(np.mean(errors ** 2))),
        "mean_bias_wm2": float(np.mean(errors)),
    }


def score_subsets(frame, candidate, partition):
    rows = [
        score(
            frame,
            candidate,
            "all_valid_hours",
            partition,
        )
    ]

    above_threshold = frame.loc[
        frame["ghi_target_wm2"] > 20
    ]

    if not above_threshold.empty:
        rows.append(
            score(
                above_threshold,
                candidate,
                "observed_ghi_above_20_wm2",
                partition,
            )
        )

    return rows


data = pd.read_csv(INPUT_FILE)

required = {
    "hour_ending_utc",
    "run_initialization_utc",
    "ghi_target_wm2",
    "eligible",
    "ml_eligible",
    *FEATURES,
}

missing = required - set(data.columns)

if missing:
    raise ValueError(f"Missing columns: {sorted(missing)}")

for column in ["hour_ending_utc", "run_initialization_utc"]:
    data[column] = pd.to_datetime(
        data[column],
        utc=True,
        errors="raise",
    )

    if data[column].isna().any():
        raise ValueError(f"Missing timestamps in {column}.")

if data["hour_ending_utc"].duplicated().any():
    raise ValueError("Duplicate target timestamps.")

eligible = read_boolean(data["eligible"], "eligible")
ml_eligible = read_boolean(data["ml_eligible"], "ml_eligible")

if not eligible.equals(ml_eligible):
    raise ValueError(
        "Baseline and complete-feature samples differ. "
        "Review the comparison sample before training."
    )

data = data.loc[ml_eligible].copy()
data = data.sort_values("hour_ending_utc").reset_index(drop=True)

numeric_columns = FEATURES + ["ghi_target_wm2"]

for column in numeric_columns:
    data[column] = pd.to_numeric(
        data[column],
        errors="raise",
    )

if not np.isfinite(
    data[numeric_columns].to_numpy(dtype=float)
).all():
    raise ValueError("Nonfinite values in model inputs or target.")

run_time = data["run_initialization_utc"]

start = pd.Timestamp("2025-01-01", tz="UTC")
validation_start = pd.Timestamp("2025-01-21", tz="UTC")
test_start = pd.Timestamp("2025-01-26", tz="UTC")
end = pd.Timestamp("2025-02-01", tz="UTC")

if not ((run_time >= start) & (run_time < end)).all():
    raise ValueError("Run initialization outside January.")

actual_leads = (
    (data["hour_ending_utc"] - run_time)
    / pd.Timedelta(hours=1)
)

if not np.allclose(
    actual_leads,
    data["lead_hours_from_initialization"],
    rtol=0,
    atol=0,
):
    raise ValueError("Lead-hour metadata does not match timestamps.")

if not actual_leads.isin(range(1, 25)).all():
    raise ValueError("Unexpected forecast lead.")

data["partition"] = np.select(
    [
        run_time < validation_start,
        run_time < test_start,
    ],
    ["training", "validation"],
    default="test",
)

expected_counts = {
    "training": 479,
    "validation": 120,
    "test": 144,
}

actual_counts = data.groupby("partition").size().to_dict()

if actual_counts != expected_counts:
    raise ValueError(
        f"Partition counts changed: {actual_counts}. "
        f"Expected: {expected_counts}."
    )

training = data.loc[data["partition"].eq("training")].copy()

x_train = training["model_ghi_wm2"].to_numpy(dtype=float)
y_train = training["ghi_target_wm2"].to_numpy(dtype=float)

offset = float(np.mean(y_train - x_train))

design = np.column_stack([
    np.ones(len(x_train)),
    x_train,
])

coefficients, _, rank, _ = np.linalg.lstsq(
    design,
    y_train,
    rcond=None,
)

if rank != 2:
    raise ValueError("Cannot identify the linear correction.")

intercept, slope = map(float, coefficients)

forest = RandomForestRegressor(**RF_PARAMETERS)

forest.fit(
    training[FEATURES],
    y_train - x_train,
)


def predict_candidates(frame):
    result = frame.copy()

    baseline = result["model_ghi_wm2"].to_numpy(dtype=float)

    result["baseline"] = baseline
    result["additive"] = np.maximum(0, baseline + offset)
    result["linear"] = np.maximum(
        0,
        intercept + slope * baseline,
    )

    predicted_correction = forest.predict(result[FEATURES])

    result["random_forest"] = np.maximum(
        0,
        baseline + predicted_correction,
    )

    if not np.isfinite(
        result[CANDIDATES].to_numpy(dtype=float)
    ).all():
        raise ValueError("Nonfinite predictions.")

    return result


validation = predict_candidates(
    data.loc[data["partition"].eq("validation")]
)

if not (validation["ghi_target_wm2"] > 20).any():
    raise ValueError("No above-threshold validation hours.")

validation_rows = []

for candidate in CANDIDATES:
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

# Candidate order breaks exact ties in favor of simpler methods.
selected = min(
    CANDIDATES,
    key=lambda candidate: selection_scores.loc[candidate],
)

# Selection is finalized before test predictions are evaluated.
test = data.loc[data["partition"].eq("test")].copy()
baseline_test = test["model_ghi_wm2"].to_numpy(dtype=float)

test["baseline"] = baseline_test

if selected == "additive":
    test[selected] = np.maximum(0, baseline_test + offset)

elif selected == "linear":
    test[selected] = np.maximum(
        0,
        intercept + slope * baseline_test,
    )

elif selected == "random_forest":
    test[selected] = np.maximum(
        0,
        baseline_test + forest.predict(test[FEATURES]),
    )

evaluated_test_candidates = list(
    dict.fromkeys(["baseline", selected])
)

test_rows = []

for candidate in evaluated_test_candidates:
    test_rows.extend(
        score_subsets(test, candidate, "test")
    )

test_metrics = pd.DataFrame(test_rows)

metadata_columns = [
    "hour_ending_utc",
    "run_initialization_utc",
    "lead_hours_from_initialization",
    "partition",
    "ghi_target_wm2",
]

validation[
    metadata_columns + CANDIDATES
].to_csv(
    OUTPUT_DIR / "validation_predictions.csv",
    index=False,
)

test[
    metadata_columns + evaluated_test_candidates
].to_csv(
    OUTPUT_DIR / "test_predictions.csv",
    index=False,
)

validation_metrics.to_csv(
    OUTPUT_DIR / "validation_metrics.csv",
    index=False,
)

test_metrics.to_csv(
    OUTPUT_DIR / "test_metrics.csv",
    index=False,
)

data[
    metadata_columns
].to_csv(
    OUTPUT_DIR / "partition_manifest.csv",
    index=False,
)

parameters = {
    "features": FEATURES,
    "partition_basis": "run_initialization_utc",
    "partition_counts": {
        name: int(count)
        for name, count in actual_counts.items()
    },
    "training_initializations": "2025-01-01 through 2025-01-20",
    "validation_initializations": "2025-01-21 through 2025-01-25",
    "test_initializations": "2025-01-26 through 2025-01-31",
    "additive_offset_wm2": offset,
    "linear_intercept_wm2": intercept,
    "linear_slope": slope,
    "random_forest_parameters": RF_PARAMETERS,
    "random_forest_target": "observed GHI minus provider GHI",
    "random_forest_prediction": (
        "max(0, provider GHI + predicted correction)"
    ),
    "fitting_subset": "all eligible training hours",
    "selection_metric": (
        "Validation RMSE for observed GHI above 20 W/m²"
    ),
    "selected_candidate": selected,
    "refit_after_selection": False,
    "sklearn_version": sklearn.__version__,
    "interpretation": (
        "Exploratory January experiment. January data and earlier "
        "test results were previously inspected. This is not a new "
        "untouched final test or evidence of regional accuracy."
    ),
}

with (OUTPUT_DIR / "parameters.json").open(
    "w",
    encoding="utf-8",
) as file:
    json.dump(parameters, file, indent=2)

joblib.dump(
    {
        "model": forest,
        "features": FEATURES,
        "target": "observed GHI minus provider GHI",
        "selected_for_use": selected == "random_forest",
        "parameters": parameters,
    },
    OUTPUT_DIR / "random_forest_candidate.joblib",
)

print("Eligible hours by partition:")
print(data.groupby("partition").size().to_string())

print(f"\nAdditive offset: {offset:.6f} W/m²")
print(f"Linear intercept: {intercept:.6f} W/m²")
print(f"Linear slope: {slope:.6f}")

print("\nVALIDATION METRICS")
print(validation_metrics.round(3).to_string(index=False))

print(f"\nSelected candidate: {selected}")
print("Selection: lowest validation above-20 W/m² RMSE")

print("\nEXPLORATORY TEST METRICS")
print(test_metrics.round(3).to_string(index=False))

print(
    "\nThe Random Forest candidate was saved, "
    "but the website has not been changed."
)

print(f"\nResults saved in: {OUTPUT_DIR}")