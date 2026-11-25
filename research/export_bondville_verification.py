from datetime import datetime, timezone
from pathlib import Path
import json

import numpy as np
import pandas as pd


BASE_DIR = Path(__file__).resolve().parent

FEATURES_FILE = (
    BASE_DIR
    / "results"
    / "feature_dataset"
    / "bondville_january_features_all_hours.csv"
)

RF_DIR = BASE_DIR / "results" / "random_forest_experiment"

OUTPUT_DIR = BASE_DIR / "results" / "website"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

OUTPUT_FILE = OUTPUT_DIR / "bondville_verification.json"


def read_boolean(series, name):
    values = series.astype(str).str.strip().str.lower()

    if not values.isin(["true", "false"]).all():
        raise ValueError(f"Invalid Boolean values in {name}.")

    return values.eq("true")


def iso_utc(series):
    return series.dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def metric_block(frame):
    errors = (
        frame["model_ghi_wm2"] - frame["ghi_target_wm2"]
    ).to_numpy(dtype=float)

    return {
        "matched_hours": int(len(frame)),
        "mae_wm2": round(float(np.mean(np.abs(errors))), 3),
        "rmse_wm2": round(float(np.sqrt(np.mean(errors ** 2))), 3),
        "mean_bias_wm2": round(float(np.mean(errors)), 3),
    }


def read_experiment_rows(path):
    if not path.exists():
        return []

    table = pd.read_csv(path)

    rows = []

    for record in table.to_dict("records"):
        row = {}

        for key, value in record.items():
            if isinstance(value, (int, float, np.integer, np.floating)):
                if not np.isfinite(value):
                    raise ValueError(f"Nonfinite value in {path.name}")
                row[key] = round(float(value), 3)
            else:
                row[key] = str(value)

        row["matched_hours"] = int(row["matched_hours"])
        rows.append(row)

    return rows


data = pd.read_csv(FEATURES_FILE)

required = {
    "hour_ending_utc",
    "run_initialization_utc",
    "lead_hours_from_initialization",
    "model_ghi_wm2",
    "ghi_target_wm2",
    "eligible",
}

missing = required - set(data.columns)

if missing:
    raise ValueError(f"Missing columns: {sorted(missing)}")

for column in ["hour_ending_utc", "run_initialization_utc"]:
    data[column] = pd.to_datetime(
        data[column], utc=True, errors="raise"
    )

for column in [
    "lead_hours_from_initialization",
    "model_ghi_wm2",
    "ghi_target_wm2",
]:
    data[column] = pd.to_numeric(data[column], errors="coerce")

eligible = read_boolean(data["eligible"], "eligible")

valid = (
    data.loc[eligible]
    .sort_values("hour_ending_utc")
    .reset_index(drop=True)
)

numeric = valid[
    [
        "lead_hours_from_initialization",
        "model_ghi_wm2",
        "ghi_target_wm2",
    ]
].to_numpy(dtype=float)

if valid.empty or not np.isfinite(numeric).all():
    raise ValueError("Eligible rows are empty or contain bad values.")

if valid["hour_ending_utc"].duplicated().any():
    raise ValueError("Duplicate target timestamps.")

by_lead = []

for lead, group in valid.groupby("lead_hours_from_initialization"):
    block = metric_block(group)
    block["lead_hours"] = int(lead)
    by_lead.append(block)

series = pd.DataFrame({
    "time_utc": iso_utc(valid["hour_ending_utc"]),
    "initialization_utc": iso_utc(valid["run_initialization_utc"]),
    "lead_hours": valid["lead_hours_from_initialization"].astype(int),
    "forecast_wm2": valid["model_ghi_wm2"].round(3),
    "observed_wm2": valid["ghi_target_wm2"].round(3),
}).to_dict("records")

validation_rows = read_experiment_rows(
    RF_DIR / "validation_metrics.csv"
)
test_rows = read_experiment_rows(
    RF_DIR / "test_metrics.csv"
)

document = {
    "schema_version": 1,
    "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    "location": {
        "name": "Bondville, Illinois (SURFRAD station)",
        "latitude": 40.05,
        "longitude": -88.37,
        "elevation_m": 213,
    },
    "period": {
        "first_target_hour_utc": series[0]["time_utc"],
        "last_target_hour_utc": series[-1]["time_utc"],
    },
    "forecast": {
        "provider": "Open-Meteo Single Runs API",
        "model": "ecmwf_ifs",
        "initialization": "Daily 00:00 UTC, January 2025",
        "leads": "Hours 1-24 after each initialization",
        "provenance_note": (
            "Historical archive provenance requires verification. "
            "These are not verified as-issued operational forecasts."
        ),
    },
    "observations": {
        "source": "NOAA SURFRAD",
        "quantity": "Hourly mean global horizontal irradiance",
        "eligibility": (
            "Hours required 60 valid minutes after quality filtering."
        ),
    },
    "metrics": {
        "all_eligible_hours": metric_block(valid),
        "observed_ghi_above_20_wm2": metric_block(
            valid.loc[valid["ghi_target_wm2"] > 20]
        ),
    },
    "metrics_by_lead": by_lead,
    "correction_experiments": {
        "validation": validation_rows,
        "exploratory_test": test_rows,
        "note": (
            "Validation: January 21-25 initializations. Exploratory "
            "test: January 26-31 initializations, previously inspected. "
            "The Random Forest was not evaluated on the test period."
        ),
    },
    "scope_note": (
        "One station, one model and one month. This does not establish "
        "accuracy at other locations."
    ),
    "series": series,
}

OUTPUT_FILE.write_text(
    json.dumps(
        document,
        indent=2,
        ensure_ascii=False,
        allow_nan=False,
    ),
    encoding="utf-8",
)

print(f"Saved: {OUTPUT_FILE}")
print(f"Series hours: {len(series)}")
print(json.dumps(document["metrics"], indent=2))
print(f"Validation rows: {len(validation_rows)}")
print(f"Test rows: {len(test_rows)}")