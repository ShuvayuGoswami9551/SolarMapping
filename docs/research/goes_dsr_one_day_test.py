from datetime import datetime, timezone
from pathlib import Path
import re
import sys

import numpy as np
import pandas as pd
import s3fs
import xarray as xr


BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD_DIR = BASE_DIR / "data" / "goes_dsr_test"
OUTPUT_DIR = BASE_DIR / "results" / "satellite_test"

DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

BUCKET = "noaa-goes16"
PRODUCT = "ABI-L2-DSRC"
TEST_DATE = pd.Timestamp("2025-01-10", tz="UTC")

STATION_LAT = 40.05
STATION_LON = -88.37

DOWNLOAD_START_HOUR_UTC = 15
DOWNLOAD_END_HOUR_UTC = 21
MAX_DOWNLOADS = 7   

NAME_PATTERN = re.compile(
    r"_s(\d{4})(\d{3})(\d{2})(\d{2})(\d{2})\d"
    r"_e(\d{4})(\d{3})(\d{2})(\d{2})(\d{2})\d_c"
)


def parse_scan_times(name):
    match = NAME_PATTERN.search(name)

    if not match:
        return None, None

    values = match.groups()

    def make(parts):
        year, day, hour, minute, second = parts
        return datetime.strptime(
            f"{year}{day}{hour}{minute}{second}",
            "%Y%j%H%M%S",
        ).replace(tzinfo=timezone.utc)

    return make(values[:5]), make(values[5:])


def latlon_to_scan_angles(lat, lon, projection):
    r_eq = float(projection.attrs["semi_major_axis"])
    r_pol = float(projection.attrs["semi_minor_axis"])
    height = float(projection.attrs["perspective_point_height"]) + r_eq
    lon_origin = np.radians(
        float(projection.attrs["longitude_of_projection_origin"])
    )

    phi = np.radians(lat)
    lam = np.radians(lon)

    phi_c = np.arctan((r_pol ** 2 / r_eq ** 2) * np.tan(phi))
    e_squared = (r_eq ** 2 - r_pol ** 2) / r_eq ** 2
    radius = r_pol / np.sqrt(1 - e_squared * np.cos(phi_c) ** 2)

    sx = height - radius * np.cos(phi_c) * np.cos(lam - lon_origin)
    sy = -radius * np.cos(phi_c) * np.sin(lam - lon_origin)
    sz = radius * np.sin(phi_c)

    if height * (height - sx) < sy ** 2 + (r_eq ** 2 / r_pol ** 2) * sz ** 2:
        raise ValueError("Station is not visible from the satellite.")

    y = np.arctan(sz / sx)
    x = np.arcsin(-sy / np.sqrt(sx ** 2 + sy ** 2 + sz ** 2))

    return float(x), float(y)


def scan_angles_to_latlon(x, y, projection):
    r_eq = float(projection.attrs["semi_major_axis"])
    r_pol = float(projection.attrs["semi_minor_axis"])
    height = float(projection.attrs["perspective_point_height"]) + r_eq
    lon_origin = np.radians(
        float(projection.attrs["longitude_of_projection_origin"])
    )

    a = np.sin(x) ** 2 + np.cos(x) ** 2 * (
        np.cos(y) ** 2 + (r_eq ** 2 / r_pol ** 2) * np.sin(y) ** 2
    )
    b = -2 * height * np.cos(x) * np.cos(y)
    c = height ** 2 - r_eq ** 2

    rs = (-b - np.sqrt(b ** 2 - 4 * a * c)) / (2 * a)

    sx = rs * np.cos(x) * np.cos(y)
    sy = -rs * np.sin(x)
    sz = rs * np.cos(x) * np.sin(y)

    lat = np.degrees(
        np.arctan(
            (r_eq ** 2 / r_pol ** 2)
            * sz
            / np.sqrt((height - sx) ** 2 + sy ** 2)
        )
    )

    lon = np.degrees(lon_origin - np.arctan(sy / (height - sx)))

    return float(lat), float(lon)


def haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, [lat1, lon1, lat2, lon2])
    a = (
        np.sin((lat2 - lat1) / 2) ** 2
        + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    )
    return float(6371.0088 * 2 * np.arcsin(np.sqrt(a)))


fs = s3fs.S3FileSystem(anon=True)
day_of_year = TEST_DATE.dayofyear

listed = []

for hour in range(24):
    prefix = (
        f"{BUCKET}/{PRODUCT}/{TEST_DATE.year}/"
        f"{day_of_year:03d}/{hour:02d}/"
    )

    try:
        entries = fs.ls(prefix, detail=True)
    except FileNotFoundError:
        continue

    for entry in entries:
        if not entry["name"].endswith(".nc"):
            continue

        start, end = parse_scan_times(entry["name"])

        listed.append({
            "key": entry["name"],
            "size_bytes": entry["size"],
            "scan_start_utc": start,
            "scan_end_utc": end,
        })

if not listed:
    print(f"No files found under {BUCKET}/{PRODUCT} for {TEST_DATE.date()}.")

    try:
        matches = [
            path for path in fs.ls(BUCKET) if "DSR" in path
        ]
        print("Folders in the bucket containing 'DSR':")
        for path in matches:
            print(f"  {path}")
    except Exception as error:
        print(f"Could not list the bucket: {error}")

    sys.exit(1)

catalog = pd.DataFrame(listed).sort_values("scan_start_utc")

print(f"Files listed for {TEST_DATE.date()}: {len(catalog)}")
print(f"Total listed size: {catalog['size_bytes'].sum() / 1e6:.1f} MB")
print(f"First scan start: {catalog['scan_start_utc'].iloc[0]}")
print(f"Last scan start: {catalog['scan_start_utc'].iloc[-1]}")

gaps = (
    catalog["scan_start_utc"].diff().dropna().dt.total_seconds() / 60
)

if not gaps.empty:
    print("\nMinutes between consecutive scan starts:")
    print(gaps.round(1).value_counts().sort_index().to_string())

catalog.to_csv(OUTPUT_DIR / "file_catalog.csv", index=False)

selected = catalog.loc[
    catalog["scan_start_utc"].dt.hour.between(
        DOWNLOAD_START_HOUR_UTC, DOWNLOAD_END_HOUR_UTC
    )
    & catalog["scan_start_utc"].dt.minute.eq(0)
].head(MAX_DOWNLOADS)

print(f"\nFiles selected for download: {len(selected)}")

rows = []
inspected = False

for item in selected.itertuples():
    local_path = DOWNLOAD_DIR / Path(item.key).name

    if not local_path.exists():
        temporary = local_path.with_suffix(".nc.part")
        fs.get(item.key, str(temporary))
        temporary.replace(local_path)

    with xr.open_dataset(local_path) as dataset:
        if "DSR" not in dataset.variables:
            print(f"Variables found: {list(dataset.variables)}")
            raise SystemExit("Expected a variable named DSR.")

        if "goes_imager_projection" not in dataset.variables:
            print(f"Variables found: {list(dataset.variables)}")
            raise SystemExit("Projection variable not found.")

        projection = dataset["goes_imager_projection"]
        x_values = dataset["x"].to_numpy()
        y_values = dataset["y"].to_numpy()

        x0, y0 = latlon_to_scan_angles(
            STATION_LAT, STATION_LON, projection
        )

        ix = int(np.argmin(np.abs(x_values - x0)))
        iy = int(np.argmin(np.abs(y_values - y0)))

        pixel_lat, pixel_lon = scan_angles_to_latlon(
            float(x_values[ix]), float(y_values[iy]), projection
        )

        distance = haversine_km(
            STATION_LAT, STATION_LON, pixel_lat, pixel_lon
        )

        if not inspected:
            height = float(
                projection.attrs["perspective_point_height"]
            )

            x_step_km = abs(np.median(np.diff(x_values))) * height / 1000
            y_step_km = abs(np.median(np.diff(y_values))) * height / 1000

            print(f"\nInspecting: {local_path.name}")
            print(f"Dimensions: {dict(dataset.sizes)}")
            print(f"Variables: {list(dataset.data_vars)}")
            print(f"DSR units: {dataset['DSR'].attrs.get('units')}")
            print(f"DSR long name: {dataset['DSR'].attrs.get('long_name')}")
            print(
                f"Approximate grid spacing at the satellite sub-point: "
                f"{x_step_km:.2f} km by {y_step_km:.2f} km"
            )

            if "DQF" in dataset.variables:
                for key in ["flag_values", "flag_meanings"]:
                    print(f"DQF {key}: {dataset['DQF'].attrs.get(key)}")

            print(
                f"Nearest pixel index: x={ix}, y={iy} of "
                f"{len(x_values)} by {len(y_values)}"
            )
            print(
                f"Pixel center: {pixel_lat:.3f}, {pixel_lon:.3f} "
                f"({distance:.1f} km from the station)"
            )

            inspected = True

        dsr = float(dataset["DSR"].to_numpy()[iy, ix])

        dqf = (
            int(dataset["DQF"].to_numpy()[iy, ix])
            if "DQF" in dataset.variables
            and np.isfinite(dataset["DQF"].to_numpy()[iy, ix])
            else None
        )

    rows.append({
        "file": local_path.name,
        "scan_start_utc": item.scan_start_utc,
        "scan_end_utc": item.scan_end_utc,
        "dsr_wm2": dsr,
        "dqf": dqf,
        "pixel_x_index": ix,
        "pixel_y_index": iy,
        "pixel_latitude": pixel_lat,
        "pixel_longitude": pixel_lon,
        "distance_to_station_km": distance,
    })

result = pd.DataFrame(rows)

result.to_csv(
    OUTPUT_DIR / "goes_dsr_test_pixel.csv",
    index=False,
)

print("\nExtracted values")
print(
    result[
        ["scan_start_utc", "dsr_wm2", "dqf", "distance_to_station_km"]
    ].round(2).to_string(index=False)
)

print(f"\nSaved results in: {OUTPUT_DIR}")