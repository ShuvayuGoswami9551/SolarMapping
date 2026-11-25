from pathlib import Path
import json

import numpy as np
import pandas as pd


BASE_DIR = Path(__file__).resolve().parent
ARCHIVE_DIR = BASE_DIR / "data" / "forecast_archive"
OBSERVATION_FILE = (
    BASE_DIR / "processed" / "bondville_hourly_processed.csv"
)
PARAMETER_FILE = (
    BASE_DIR / "results" / "correction_experiment" / "parameters.json"
)
OUTPUT_DIR = BASE_DIR / "results" / "february_evaluation"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Load the original January parameters. Do not fit new ones.
with PARAMETER_FILE.open(encoding="utf-8") as file:
    parameters = json.load(file)

intercept = float(parameters["linear_intercept_wm2"])
slope = float(parameters["linear_slope"])

if not np.isfinite([intercept, slope]).all():
    raise ValueError("January correction parameters are invalid.")

frames = []

for day in pd.date_range("2025-02-01", "2025-02-28", freq="D"):
    archive_file = ARCHIVE_DIR / f"ecmwf_run_{day:%Y%m%d}_00.json"

    with archive_file.open(encoding="utf-8-sig") as file:
        archive = json.load(file)

    params = archive["request_parameters"]
    response = archive["response"]

    run_time = pd.to_datetime(params["run"], utc=True)
    expected_run = day.tz_localize("UTC")

    if run_time != expected_run:
        raise ValueError(f"Unexpected run in {archive_file.name}")

    if params["models"] != "ecmwf_ifs":
        raise ValueError(f"Unexpected model in {archive_file.name}")

    if response.get("utc_offset_seconds") != 0:
        raise ValueError(f"Expected UTC in {archive_file.name}")

    if response["hourly_units"]["shortwave_radiation"] != "W/m²":
        raise ValueError(f"Unexpected units in {archive_file.name}")

    hourly = response["hourly"]

    frame = pd.DataFrame({
        "hour_ending_utc": pd.to_datetime(hourly["time"], utc=True),
        "baseline": pd.to_numeric(
            pd.Series(hourly["shortwave_radiation"]),
            errors="coerce",
        ),
    })

    if frame["hour_ending_utc"].duplicated().any():
        raise ValueError(f"Duplicate timestamps in {archive_file.name}")

    frame["run_initialization_utc"] = run_time
    frame["lead_hours_from_initialization"] = (
        (frame["hour_ending_utc"] - run_time)
        / pd.Timedelta(hours=1)
    )

    selected = frame.loc[
        frame["lead_hours_from_initialization"].between(1, 24)
    ].copy()

    if (
        len(selected) != 24
        or set(selected["lead_hours_from_initialization"])
        != set(range(1, 25))
    ):
        raise ValueError(
            f"Missing or unexpected leads in {archive_file.name}"
        )

    frames.append(selected)

model = pd.concat(frames, ignore_index=True)
model = model.sort_values("hour_ending_utc")

if model["hour_ending_utc"].duplicated().any():
    raise ValueError("Overlapping target hours across runs.")

observations = pd.read_csv(OBSERVATION_FILE)

observations["hour_ending_utc"] = pd.to_datetime(
    observations["hour_ending_utc"], utc=True
)

if observations["hour_ending_utc"].duplicated().any():
    raise ValueError("Duplicate observation timestamps.")

for column in [
    "ghi_target_wm2",
    "valid_minute_count",
    "component_minute_count",
]:
    observations[column] = pd.to_numeric(
        observations[column], errors="coerce"
    )

matched = model.merge(
    observations[
        [
            "hour_ending_utc",
            "ghi_target_wm2",
            "valid_minute_count",
            "component_minute_count",
        ]
    ],
    on="hour_ending_utc",
    how="left",
    validate="one_to_one",
    indicator="observation_match",
)

matched["eligible"] = (
    matched["valid_minute_count"].eq(60)
    & np.isfinite(matched["ghi_target_wm2"])
    & np.isfinite(matched["baseline"])
)

matched["linear"] = np.maximum(
    0,
    intercept + slope * matched["baseline"],
)

valid = matched.loc[matched["eligible"]].copy()

if valid.empty:
    raise ValueError("No valid matched February hours.")

subsets = {
    "all_valid_hours": valid,
    "observed_ghi_above_20_wm2": valid.loc[
        valid["ghi_target_wm2"] > 20
    ],
}

rows = []

for subset_name, subset in subsets.items():
    if subset.empty:
        continue

    for candidate in ["baseline", "linear"]:
        errors = (
            subset[candidate] - subset["ghi_target_wm2"]
        ).to_numpy()

        rows.append({
            "evaluation_period": "february_initialized_runs",
            "candidate": candidate,
            "evaluation_subset": subset_name,
            "matched_hours": len(subset),
            "mae_wm2": np.mean(np.abs(errors)),
            "rmse_wm2": np.sqrt(np.mean(errors ** 2)),
            "mean_bias_wm2": np.mean(errors),
        })

metrics = pd.DataFrame(rows)

matched.to_csv(
    OUTPUT_DIR / "february_predictions.csv",
    index=False,
)

metrics.to_csv(
    OUTPUT_DIR / "february_metrics.csv",
    index=False,
)

matched.loc[~matched["eligible"]].to_csv(
    OUTPUT_DIR / "february_excluded_hours.csv",
    index=False,
)

with (OUTPUT_DIR / "frozen_january_parameters.json").open(
    "w", encoding="utf-8"
) as file:
    json.dump(parameters, file, indent=2)

print(f"Daily runs loaded: {len(frames)}")
print(f"Selected hourly targets: {len(model)}")
print(f"Valid matched hours: {len(valid)}")
print(f"Excluded hours: {(~matched['eligible']).sum()}")
print(
    "Targets without an observation timestamp: "
    f"{matched['observation_match'].eq('left_only').sum()}"
)

print(f"\nFrozen January intercept: {intercept:.6f} W/m²")
print(f"Frozen January slope: {slope:.6f}")
print("No parameters fitted on February.")

print("\nFEBRUARY METRICS")
print(metrics.round(3).to_string(index=False))

print(f"\nResults saved in: {OUTPUT_DIR}")