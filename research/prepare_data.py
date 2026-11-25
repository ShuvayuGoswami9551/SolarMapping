from pathlib import Path

import numpy as np
import pandas as pd


BASE_DIR = Path(__file__).resolve().parent

DATA_DIR = BASE_DIR / "data"

INPUT_FILES = sorted(
    path
    for path in DATA_DIR.iterdir()
    if path.name.startswith("bon")
    and (
        path.name.endswith(".dat")
        or path.name.endswith(".dat.txt")
    )
)

if not INPUT_FILES:
    raise FileNotFoundError(
        f"No Bondville observation files found in {DATA_DIR}"
    )
OUTPUT_DIR = BASE_DIR / "processed"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# First eight columns contain date, time, and solar geometry.
columns = [
    "year",
    "day_of_year",
    "month",
    "day",
    "hour",
    "minute",
    "decimal_hour",
    "solar_zenith_deg",
]

# Each measurement is followed by its own quality-control flag.
measurements = [
    "global_solar_wm2",
    "upwelling_solar_wm2",
    "direct_normal_wm2",
    "diffuse_solar_wm2",
    "downwelling_ir_wm2",
    "downwelling_case_temp_k",
    "downwelling_dome_temp_k",
    "upwelling_ir_wm2",
    "upwelling_case_temp_k",
    "upwelling_dome_temp_k",
    "uvb_mwm2",
    "par_wm2",
    "net_solar_wm2",
    "net_ir_wm2",
    "total_net_wm2",
    "air_temperature_c",
    "relative_humidity_percent",
    "wind_speed_ms",
    "wind_direction_deg",
    "pressure_mb",
]

for name in measurements:
    columns.extend([name, f"{name}_qc"])

frames = []
station_name = None
station_metadata = None

for input_file in INPUT_FILES:
    with input_file.open(encoding="utf-8-sig") as file:
        current_station = file.readline().strip()
        current_metadata = file.readline().strip()

    if station_name is None:
        station_name = current_station
        station_metadata = current_metadata

    elif (
        current_station != station_name
        or current_metadata != station_metadata
    ):
        raise ValueError(
            f"Station metadata differs in {input_file.name}. "
            "Check that all files belong to the same station."
        )

    frame = pd.read_csv(
        input_file,
        sep=r"\s+",
        skiprows=2,
        header=None,
    )

    if frame.shape[1] != len(columns):
        raise ValueError(
            f"{input_file.name}: expected {len(columns)} columns, "
            f"but found {frame.shape[1]}."
        )

    frame.columns = columns
    frame["source_file"] = input_file.name

    frames.append(frame)

    print(f"Loaded {input_file.name}: {len(frame)} rows")

raw = pd.concat(frames, ignore_index=True)

raw["time_utc"] = pd.to_datetime(
    raw[["year", "month", "day", "hour", "minute"]],
    utc=True,
    errors="raise",
)

if raw["time_utc"].duplicated().any():
    raise ValueError("Duplicate timestamps found; inspect the input file.")

raw = raw.sort_values("time_utc").set_index("time_utc")

expected_times = pd.date_range(
    start=raw.index.min(),
    end=raw.index.max(),
    freq="1min",
)

missing_times = expected_times.difference(raw.index)

print(f"Missing minute timestamps within loaded period: {len(missing_times)}")

if len(missing_times):
    print("First missing timestamps:")
    print(missing_times[:10])

clean = raw.copy()

# Retain the original measurements in raw; filter a separate copy.
for name in measurements:
    clean[name] = raw[name].where(
        raw[f"{name}_qc"].eq(0)
        & raw[name].ne(-9999.9)
        & np.isfinite(raw[name])
    )

zenith = clean["solar_zenith_deg"]
valid_geometry = zenith.between(0, 180)

cos_zenith = np.cos(np.deg2rad(zenith)).clip(lower=0)

# Project the direct beam onto a horizontal surface.
# Clip small negative component values for this derived product only.
components_available = (
    clean["direct_normal_wm2"].notna()
    & clean["diffuse_solar_wm2"].notna()
    & valid_geometry
)

component_ghi = (
    clean["direct_normal_wm2"].clip(lower=0) * cos_zenith
    + clean["diffuse_solar_wm2"].clip(lower=0)
).where(components_available)

# Prefer component-derived GHI; use the global sensor as a fallback.
fallback_ghi = clean["global_solar_wm2"].clip(lower=0)

minute = pd.DataFrame(index=raw.index)

minute["global_sensor_raw_wm2"] = raw["global_solar_wm2"]
minute["global_sensor_qc"] = raw["global_solar_wm2_qc"]
minute["direct_normal_raw_wm2"] = raw["direct_normal_wm2"]
minute["direct_normal_qc"] = raw["direct_normal_wm2_qc"]
minute["diffuse_raw_wm2"] = raw["diffuse_solar_wm2"]
minute["diffuse_qc"] = raw["diffuse_solar_wm2_qc"]
minute["solar_zenith_deg"] = zenith

minute["ghi_wm2"] = component_ghi.combine_first(fallback_ghi)

minute["ghi_source"] = np.select(
    [
        component_ghi.notna(),
        fallback_ghi.notna(),
    ],
    [
        "direct_plus_diffuse",
        "global_sensor_fallback",
    ],
    default="missing",
)

# SURFRAD timestamps mark the END of each one-minute interval.
# An hour ending at 01:00 therefore includes 00:01 through 01:00.
hourly = minute["ghi_wm2"].resample(
    "1h",
    closed="right",
    label="right",
).agg(["mean", "count"])

hourly.columns = [
    "ghi_mean_available_wm2",
    "valid_minute_count",
]

hourly["component_minute_count"] = (
    minute["ghi_source"]
    .eq("direct_plus_diffuse")
    .astype(int)
    .resample("1h", closed="right", label="right")
    .sum()
)

# Only complete hours become comparison targets.
hourly["complete_hour"] = hourly["valid_minute_count"].eq(60)

hourly["ghi_target_wm2"] = hourly[
    "ghi_mean_available_wm2"
].where(hourly["complete_hour"])

hourly.index.name = "hour_ending_utc"

minute_file = OUTPUT_DIR / "bondville_minute_processed.csv"
hourly_file = OUTPUT_DIR / "bondville_hourly_processed.csv"

minute.to_csv(minute_file)
hourly.to_csv(hourly_file)

print(f"Station: {station_name}")
print(f"Metadata: {station_metadata}")
print(f"Observation rows: {len(raw)}")
print(f"First timestamp: {raw.index.min()}")
print(f"Last timestamp: {raw.index.max()}")
print(f"Complete hourly targets: {hourly['complete_hour'].sum()}")
print(f"Minute CSV: {minute_file}")
print(f"Hourly CSV: {hourly_file}")