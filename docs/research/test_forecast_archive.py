from pathlib import Path
import json

import requests


BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "data" / "forecast_archive"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

url = "https://single-runs-api.open-meteo.com/v1/forecast"

params = {
    "latitude": 40.05,
    "longitude": -88.37,
    "models": "ecmwf_ifs",
    "run": "2025-01-01T00:00",
    "hourly": "shortwave_radiation",
    "timezone": "GMT",
    "forecast_days": 2,
}

print("Requesting one archived ECMWF run...", flush=True)

try:
    response = requests.get(
        url,
        params=params,
        timeout=(10, 90),
    )

    print(f"HTTP status: {response.status_code}", flush=True)

    if not response.ok:
        print(response.text[:2000])
        raise SystemExit(
            "Archive request failed. Do not proceed to bulk downloading."
        )

    data = response.json()

    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    radiation = hourly.get("shortwave_radiation", [])

    if not times or len(times) != len(radiation):
        raise ValueError("Missing or inconsistent hourly arrays.")

    available = sum(value is not None for value in radiation)

    if available == 0:
        raise ValueError("The archive returned no irradiance values.")

    output_file = OUTPUT_DIR / "ecmwf_run_20250101_00.json"

    saved_data = {
        "request_parameters": params,
        "archive_caution": (
            "January 2025 ECMWF archive is described as hindcasts. "
            "Initialization time is not public availability time."
        ),
        "response": data,
    }

    output_file.write_text(
        json.dumps(saved_data, indent=2),
        encoding="utf-8",
    )

    print(f"Hourly timestamps returned: {len(times)}")
    print(f"Non-missing irradiance values: {available}")
    print(f"First timestamp: {times[0]}")
    print(f"Last timestamp: {times[-1]}")
    print(f"Units: {data.get('hourly_units', {})}")
    print(f"Saved: {output_file}")

except requests.RequestException as error:
    raise SystemExit(f"Network request failed: {error}")

except (ValueError, OSError) as error:
    raise SystemExit(f"Could not validate or save archive data: {error}")