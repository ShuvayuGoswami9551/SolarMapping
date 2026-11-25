from datetime import date, timedelta
from pathlib import Path
import json
import math
import time

import requests


BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "data" / "forecast_archive"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

START_DATE = date(2025, 1, 1)
END_DATE = date(2025, 1, 31)

API_URL = "https://single-runs-api.open-meteo.com/v1/forecast"


def validate_archive(document, expected_params):
    if document.get("request_parameters") != expected_params:
        raise ValueError("Saved request parameters do not match.")

    response = document["response"]

    if response.get("utc_offset_seconds") != 0:
        raise ValueError("Expected UTC timestamps.")

    if response["hourly_units"]["shortwave_radiation"] != "W/m²":
        raise ValueError("Unexpected irradiance units.")

    hourly = response["hourly"]
    timestamps = hourly["time"]
    radiation = hourly["shortwave_radiation"]

    if not timestamps or len(timestamps) != len(radiation):
        raise ValueError("Missing or inconsistent hourly arrays.")

    if len(set(timestamps)) != len(timestamps):
        raise ValueError("Duplicate hourly timestamps.")

    available = [
        value for value in radiation
        if value is not None
    ]

    if not available:
        raise ValueError("No irradiance values returned.")

    if not all(
        isinstance(value, (int, float))
        and math.isfinite(value)
        for value in available
    ):
        raise ValueError("Invalid irradiance values.")

    return len(available)


downloaded = 0
skipped = 0
failures = []

print("Starting January forecast archive download.", flush=True)
print(f"Output folder: {OUTPUT_DIR}", flush=True)

with requests.Session() as session:
    current_date = START_DATE

    while current_date <= END_DATE:
        run_time = f"{current_date.isoformat()}T00:00"

        params = {
            "latitude": 40.05,
            "longitude": -88.37,
            "models": "ecmwf_ifs",
            "run": run_time,
            "hourly": "shortwave_radiation",
            "timezone": "GMT",
            "forecast_days": 2,
        }

        filename = f"ecmwf_run_{current_date:%Y%m%d}_00.json"
        destination = OUTPUT_DIR / filename
        temporary = OUTPUT_DIR / f"{filename}.part"

        if destination.exists():
            try:
                document = json.loads(
                    destination.read_text(encoding="utf-8-sig")
                )

                count = validate_archive(document, params)

                print(
                    f"SKIP: {filename} "
                    f"({count} non-missing values)",
                    flush=True,
                )

                skipped += 1

            except (OSError, ValueError, KeyError, TypeError) as error:
                print(
                    f"CHECK EXISTING FILE: {filename}: {error}",
                    flush=True,
                )
                failures.append(filename)

            current_date += timedelta(days=1)
            continue

        for attempt in range(1, 4):
            print(
                f"Downloading {run_time} UTC "
                f"— attempt {attempt}/3...",
                flush=True,
            )

            try:
                response = session.get(
                    API_URL,
                    params=params,
                    timeout=(10, 90),
                )

                response.raise_for_status()

                document = {
                    "request_parameters": params,
                    "comparison_note": (
                        "Initialization time is not public release time. "
                        "Historical archive provenance requires verification."
                    ),
                    "response": response.json(),
                }

                count = validate_archive(document, params)

                temporary.write_text(
                    json.dumps(document, indent=2),
                    encoding="utf-8",
                )

                temporary.replace(destination)

                print(
                    f"SAVED: {filename} "
                    f"({count} non-missing values)",
                    flush=True,
                )

                downloaded += 1
                break

            except (
                requests.RequestException,
                OSError,
                ValueError,
                KeyError,
                TypeError,
            ) as error:
                temporary.unlink(missing_ok=True)

                print(f"Failed: {error}", flush=True)

                if attempt == 3:
                    failures.append(filename)
                else:
                    time.sleep(5 * attempt)

        time.sleep(2)
        current_date += timedelta(days=1)

print("\nDownload summary")
print(f"New runs downloaded: {downloaded}")
print(f"Existing runs checked and skipped: {skipped}")
print(f"Runs requiring attention: {len(failures)}")

if failures:
    for filename in failures:
        print(f"  {filename}")