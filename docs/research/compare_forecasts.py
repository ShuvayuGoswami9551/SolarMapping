from pathlib import Path
import json

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


BASE_DIR = Path(__file__).resolve().parent
ARCHIVE_DIR = BASE_DIR / "data" / "forecast_archive"
OBSERVATION_FILE = (
    BASE_DIR / "processed" / "bondville_hourly_processed.csv"
)
OUTPUT_DIR = BASE_DIR / "results"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

frames = []

for day in pd.date_range("2025-01-01", "2025-01-31", freq="D"):
    archive_file = ARCHIVE_DIR / f"ecmwf_run_{day:%Y%m%d}_00.json"

    if not archive_file.exists():
        raise FileNotFoundError(f"Missing archive: {archive_file.name}")

    with archive_file.open(encoding="utf-8-sig") as file:
        archive = json.load(file)

    params = archive["request_parameters"]
    response = archive["response"]

    run_time = pd.to_datetime(params["run"], utc=True)
    expected_run = day.tz_localize("UTC")

    if run_time != expected_run or params["models"] != "ecmwf_ifs":
        raise ValueError(f"Unexpected model or run in {archive_file.name}")

    if response.get("utc_offset_seconds") != 0:
        raise ValueError("Expected UTC timestamps.")

    if response["hourly_units"]["shortwave_radiation"] != "W/m²":
        raise ValueError("Unexpected irradiance units.")

    hourly = response["hourly"]

    frame = pd.DataFrame({
        "hour_ending_utc": pd.to_datetime(hourly["time"], utc=True),
        "model_ghi_wm2": pd.to_numeric(
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

    leads = selected["lead_hours_from_initialization"]

    if len(selected) != 24 or set(leads) != set(range(1, 25)):
        raise ValueError(
            f"{archive_file.name} does not contain all leads 1–24."
        )

    frames.append(selected)

model = pd.concat(frames, ignore_index=True)
model = model.sort_values("hour_ending_utc")

if model["hour_ending_utc"].duplicated().any():
    raise ValueError("Overlapping target hours across selected runs.")

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
)

matched["eligible"] = (
    matched["valid_minute_count"].eq(60)
    & np.isfinite(matched["ghi_target_wm2"])
    & np.isfinite(matched["model_ghi_wm2"])
)

matched["error_wm2"] = (
    matched["model_ghi_wm2"] - matched["ghi_target_wm2"]
).where(matched["eligible"])

valid = matched.loc[matched["eligible"]].copy()

if valid.empty:
    raise ValueError("No usable matched hours.")


def calculate_metrics(frame, label):
    errors = frame["error_wm2"]

    return {
        "evaluation_subset": label,
        "matched_hours": len(frame),
        "mae_wm2": errors.abs().mean(),
        "rmse_wm2": np.sqrt((errors ** 2).mean()),
        "mean_bias_wm2": errors.mean(),
    }


metrics_rows = [
    calculate_metrics(valid, "all_valid_hours"),
]

sunlit = valid.loc[valid["ghi_target_wm2"] > 20]

if not sunlit.empty:
    metrics_rows.append(
        calculate_metrics(sunlit, "observed_ghi_above_20_wm2")
    )

metrics = pd.DataFrame(metrics_rows)

lead_rows = []

for lead, group in valid.groupby("lead_hours_from_initialization"):
    row = calculate_metrics(group, "all_valid_hours")
    row["lead_hours_from_initialization"] = lead
    lead_rows.append(row)

lead_metrics = pd.DataFrame(lead_rows)

matched.to_csv(
    OUTPUT_DIR / "bondville_january_comparison.csv",
    index=False,
)

metrics.to_csv(
    OUTPUT_DIR / "bondville_january_metrics.csv",
    index=False,
)

lead_metrics.to_csv(
    OUTPUT_DIR / "bondville_january_metrics_by_lead.csv",
    index=False,
)

plot_data = matched.set_index("hour_ending_utc")

fig, ax = plt.subplots(figsize=(14, 5))

ax.plot(
    plot_data.index,
    plot_data["ghi_target_wm2"].where(plot_data["eligible"]),
    label="SURFRAD observations",
    color="#007f86",
    linewidth=1.3,
)

ax.plot(
    plot_data.index,
    plot_data["model_ghi_wm2"].where(plot_data["eligible"]),
    label="Archived ECMWF model",
    color="#e49b16",
    linewidth=1.1,
    alpha=0.8,
)

ax.set_title(
    "Bondville: January-initialized ECMWF runs vs. observations\n"
    "Hours 1–24 after each daily 00:00 UTC initialization"
)
ax.set_xlabel("Hour ending (UTC)")
ax.set_ylabel("Hourly mean irradiance (W/m²)")
ax.legend()
ax.grid(alpha=0.25)
fig.autofmt_xdate()
fig.tight_layout()

fig.savefig(
    OUTPUT_DIR / "bondville_january_timeseries.png",
    dpi=200,
)
plt.close(fig)

fig, ax = plt.subplots(figsize=(6, 6))

ax.scatter(
    valid["ghi_target_wm2"],
    valid["model_ghi_wm2"],
    s=14,
    alpha=0.45,
    color="#007f86",
)

upper = max(
    valid["ghi_target_wm2"].max(),
    valid["model_ghi_wm2"].max(),
    1,
) * 1.05

ax.plot([0, upper], [0, upper], "--", color="gray")
ax.set_xlim(0, upper)
ax.set_ylim(0, upper)
ax.set_aspect("equal", adjustable="box")
ax.set_xlabel("Observed irradiance (W/m²)")
ax.set_ylabel("Archived model irradiance (W/m²)")
ax.set_title("Bondville: model vs. observations")
ax.grid(alpha=0.25)
fig.tight_layout()

fig.savefig(
    OUTPUT_DIR / "bondville_january_scatter.png",
    dpi=200,
)
plt.close(fig)

print(f"Daily runs loaded: {len(frames)}")
print(f"Selected hourly targets: {len(model)}")
print(f"Valid matched hours: {len(valid)}")
print(f"Excluded hours: {(~matched['eligible']).sum()}")
print(f"First target: {matched['hour_ending_utc'].min()}")
print(f"Last target: {matched['hour_ending_utc'].max()}")
print()
print(metrics.round(3).to_string(index=False))
print(f"\nResults saved in: {OUTPUT_DIR}")