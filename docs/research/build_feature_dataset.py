from pathlib import Path
import json

import numpy as np
import pandas as pd


BASE_DIR = Path(__file__).resolve().parent

FEATURE_DIR = BASE_DIR / "data" / "forecast_features"
ORIGINAL_DIR = BASE_DIR / "data" / "forecast_archive"

OBSERVATION_FILE = (
    BASE_DIR / "processed" / "bondville_hourly_processed.csv"
)

OUTPUT_DIR = BASE_DIR / "results" / "feature_dataset"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

VARIABLE_NAMES = {
    "shortwave_radiation": "model_ghi_wm2",
    "cloud_cover": "cloud_cover_percent",
    "temperature_2m": "temperature_c",
    "relative_humidity_2m": "relative_humidity_percent",
    "wind_speed_10m": "wind_speed_ms",
    "surface_pressure": "surface_pressure_hpa",
}

EXPECTED_UNITS = {
    "shortwave_radiation": "W/m²",
    "cloud_cover": "%",
    "temperature_2m": "°C",
    "relative_humidity_2m": "%",
    "wind_speed_10m": "m/s",
    "surface_pressure": "hPa",
}

WEATHER_COLUMNS = [
    "cloud_cover_percent",
    "temperature_c",
    "relative_humidity_percent",
    "wind_speed_ms",
    "surface_pressure_hpa",
]


def parse_hourly_times(hourly, params):
    if params.get("timeformat") == "unixtime":
        return pd.to_datetime(
            hourly["time"],
            unit="s",
            utc=True,
            errors="raise",
        )

    return pd.to_datetime(
        hourly["time"],
        utc=True,
        errors="raise",
    )


def select_target_hours(frame, run_time, filename):
    if frame["hour_ending_utc"].isna().any():
        raise ValueError(f"Missing timestamp in {filename}")

    if frame["hour_ending_utc"].duplicated().any():
        raise ValueError(f"Duplicate timestamps in {filename}")

    frame = frame.copy()

    frame["run_initialization_utc"] = run_time

    frame["lead_hours_from_initialization"] = (
        (frame["hour_ending_utc"] - run_time)
        / pd.Timedelta(hours=1)
    )

    selected = frame.loc[
        frame["lead_hours_from_initialization"].between(1, 24)
    ].copy()

    leads = selected["lead_hours_from_initialization"]

    if len(selected) != 24 or set(leads) != set(range(1, 25)):
        raise ValueError(
            f"{filename} does not contain all leads 1–24."
        )

    selected["lead_hours_from_initialization"] = (
        selected["lead_hours_from_initialization"].astype(int)
    )

    return selected.sort_values("hour_ending_utc")


def check_original_irradiance(day, feature_frame):
    original_file = (
        ORIGINAL_DIR / f"ecmwf_run_{day:%Y%m%d}_00.json"
    )

    if not original_file.exists():
        raise FileNotFoundError(
            f"Original archive needed for comparison: {original_file}"
        )

    document = json.loads(
        original_file.read_text(encoding="utf-8-sig")
    )

    params = document["request_parameters"]
    response = document["response"]
    hourly = response["hourly"]

    run_time = pd.to_datetime(params["run"], utc=True)

    if (
        run_time != day.tz_localize("UTC")
        or params["models"] != "ecmwf_ifs"
        or response.get("utc_offset_seconds") != 0
        or response["hourly_units"]["shortwave_radiation"] != "W/m²"
    ):
        raise ValueError(
            f"Unexpected original archive metadata: {original_file.name}"
        )

    original = pd.DataFrame({
        "hour_ending_utc": parse_hourly_times(hourly, params),
        "original_ghi_wm2": pd.to_numeric(
            pd.Series(hourly["shortwave_radiation"]),
            errors="coerce",
        ),
    })

    original = select_target_hours(
        original,
        run_time,
        original_file.name,
    )

    comparison = feature_frame[
        ["hour_ending_utc", "model_ghi_wm2"]
    ].merge(
        original[["hour_ending_utc", "original_ghi_wm2"]],
        on="hour_ending_utc",
        how="outer",
        validate="one_to_one",
        indicator=True,
    )

    same_values = np.isclose(
        comparison["model_ghi_wm2"],
        comparison["original_ghi_wm2"],
        rtol=0,
        atol=1e-6,
        equal_nan=True,
    )

    different = (
        comparison["_merge"].ne("both").to_numpy()
        | ~same_values
    )

    if different.any():
        report = OUTPUT_DIR / (
            f"irradiance_mismatch_{day:%Y%m%d}.csv"
        )

        comparison.loc[different].to_csv(report, index=False)

        raise ValueError(
            "New and original irradiance differ. "
            f"Inspect {report.name} before continuing."
        )


def calculate_metrics(frame, label):
    errors = frame["model_ghi_wm2"] - frame["ghi_target_wm2"]

    return {
        "evaluation_subset": label,
        "matched_hours": len(frame),
        "mae_wm2": errors.abs().mean(),
        "rmse_wm2": np.sqrt((errors ** 2).mean()),
        "mean_bias_wm2": errors.mean(),
    }


frames = []

for day in pd.date_range("2025-01-01", "2025-01-31", freq="D"):
    archive_file = (
        FEATURE_DIR / f"ecmwf_features_{day:%Y%m%d}_00.json"
    )

    if not archive_file.exists():
        raise FileNotFoundError(
            f"Missing feature archive: {archive_file.name}"
        )

    archive = json.loads(
        archive_file.read_text(encoding="utf-8-sig")
    )

    params = archive["request_parameters"]
    response = archive["response"]

    run_time = pd.to_datetime(params["run"], utc=True)

    if (
        run_time != day.tz_localize("UTC")
        or params["models"] != "ecmwf_ifs"
    ):
        raise ValueError(
            f"Unexpected model or run in {archive_file.name}"
        )

    if (
        not np.isclose(float(params["latitude"]), 40.05)
        or not np.isclose(float(params["longitude"]), -88.37)
    ):
        raise ValueError(
            f"Unexpected location in {archive_file.name}"
        )

    if response.get("utc_offset_seconds") != 0:
        raise ValueError("Expected UTC timestamps.")

    hourly = response["hourly"]

    frame_data = {
        "hour_ending_utc": parse_hourly_times(hourly, params),
    }

    for api_name, column_name in VARIABLE_NAMES.items():
        unit = response["hourly_units"].get(api_name)

        if unit != EXPECTED_UNITS[api_name]:
            raise ValueError(
                f"Unexpected units for {api_name}: {unit!r}"
            )

        values = hourly[api_name]

        if len(values) != len(hourly["time"]):
            raise ValueError(
                f"Inconsistent array length: {api_name}"
            )

        frame_data[column_name] = pd.to_numeric(
            pd.Series(values),
            errors="coerce",
        ).to_numpy()

    frame = pd.DataFrame(frame_data)

    selected = select_target_hours(
        frame,
        run_time,
        archive_file.name,
    )

    check_original_irradiance(day, selected)

    selected["archive_filename"] = archive_file.name
    frames.append(selected)


model = pd.concat(frames, ignore_index=True)
model = model.sort_values("hour_ending_utc").reset_index(drop=True)

if model["hour_ending_utc"].duplicated().any():
    raise ValueError("Overlapping target hours across runs.")


observations = pd.read_csv(OBSERVATION_FILE)

observation_columns = [
    "hour_ending_utc",
    "ghi_target_wm2",
    "valid_minute_count",
    "component_minute_count",
]

missing_columns = set(observation_columns) - set(observations.columns)

if missing_columns:
    raise ValueError(
        f"Missing observation columns: {sorted(missing_columns)}"
    )

observations["hour_ending_utc"] = pd.to_datetime(
    observations["hour_ending_utc"],
    utc=True,
    errors="raise",
)

if observations["hour_ending_utc"].isna().any():
    raise ValueError("Missing observation timestamps.")

if observations["hour_ending_utc"].duplicated().any():
    raise ValueError("Duplicate observation timestamps.")

for column in observation_columns[1:]:
    observations[column] = pd.to_numeric(
        observations[column],
        errors="coerce",
    )


matched = model.merge(
    observations[observation_columns],
    on="hour_ending_utc",
    how="left",
    validate="one_to_one",
    indicator="observation_join_status",
)

# Preserve the original comparison's eligibility rule.
matched["eligible"] = (
    matched["valid_minute_count"].eq(60)
    & np.isfinite(matched["ghi_target_wm2"])
    & np.isfinite(matched["model_ghi_wm2"])
)

matched["weather_predictors_complete"] = np.isfinite(
    matched[WEATHER_COLUMNS]
).all(axis=1)

matched["ml_eligible"] = (
    matched["eligible"]
    & matched["weather_predictors_complete"]
)

matched["error_wm2"] = (
    matched["model_ghi_wm2"] - matched["ghi_target_wm2"]
).where(matched["eligible"])

matched["interval_start_utc"] = (
    matched["hour_ending_utc"] - pd.Timedelta(hours=1)
)

valid = matched.loc[matched["eligible"]].copy()
ml_ready = matched.loc[matched["ml_eligible"]].copy()

if valid.empty:
    raise ValueError("No usable matched baseline hours.")


metric_rows = []

for subset, label in [
    (valid, "baseline_all_valid_hours"),
    (
        valid.loc[valid["ghi_target_wm2"] > 20],
        "baseline_observed_ghi_above_20",
    ),
    (ml_ready, "complete_features_all_valid_hours"),
    (
        ml_ready.loc[ml_ready["ghi_target_wm2"] > 20],
        "complete_features_observed_ghi_above_20",
    ),
]:
    if not subset.empty:
        metric_rows.append(calculate_metrics(subset, label))

metrics = pd.DataFrame(metric_rows)

coverage = pd.DataFrame([
    {
        "predictor": column,
        "selected_hours": len(matched),
        "finite_hours": int(np.isfinite(matched[column]).sum()),
        "missing_or_nonfinite_hours": int(
            (~np.isfinite(matched[column])).sum()
        ),
    }
    for column in ["model_ghi_wm2", *WEATHER_COLUMNS]
])

matched.to_csv(
    OUTPUT_DIR / "bondville_january_features_all_hours.csv",
    index=False,
)

ml_ready.to_csv(
    OUTPUT_DIR / "bondville_january_features_complete_cases.csv",
    index=False,
)

metrics.to_csv(
    OUTPUT_DIR / "bondville_january_feature_baseline_metrics.csv",
    index=False,
)

coverage.to_csv(
    OUTPUT_DIR / "bondville_january_predictor_coverage.csv",
    index=False,
)


print(f"Daily runs loaded: {len(frames)}")
print(f"Selected hourly targets: {len(model)}")
print("Original irradiance comparison: PASSED")
print(f"Valid matched baseline hours: {len(valid)}")
print(f"Complete-feature matched hours: {len(ml_ready)}")
print(
    "Baseline-valid hours missing weather predictors: "
    f"{int((matched['eligible'] & ~matched['ml_eligible']).sum())}"
)
print(
    "Target hours without an observation row: "
    f"{int(matched['observation_join_status'].eq('left_only').sum())}"
)
print(f"First target: {matched['hour_ending_utc'].min()}")
print(f"Last target: {matched['hour_ending_utc'].max()}")

print("\nPredictor coverage")
print(coverage.to_string(index=False))

print("\nBaseline metrics")
print(metrics.round(3).to_string(index=False))

if ml_ready.empty:
    print(
        "\nWARNING: No complete-feature matched hours. "
        "Do not train a model yet."
    )

print(f"\nResults saved in: {OUTPUT_DIR}")