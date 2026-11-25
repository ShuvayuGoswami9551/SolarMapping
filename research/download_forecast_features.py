from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import json
import math
import time

import requests


BASE_DIR = Path(__file__).resolve().parent

OUTPUT_DIR = BASE_DIR / "data" / "forecast_features"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

START_DATE = date(2025, 1, 1)
END_DATE = date(2025, 1, 31)

API_URL = "https://single-runs-api.open-meteo.com/v1/forecast"

VARIABLES = [
    "shortwave_radiation",
    "cloud_cover",
    "temperature_2m",
    "relative_humidity_2m",
    "wind_speed_10m",
    "surface_pressure",
]

EXPECTED_UNITS = {
    "shortwave_radiation": "W/m²",
    "cloud_cover": "%",
    "temperature_2m": "°C",
    "relative_humidity_2m": "%",
    "wind_speed_10m": "m/s",
    "surface_pressure": "hPa",
}


def finite_number(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def request_parameters(current_date):
    return {
        "latitude": 40.05,
        "longitude": -88.37,
        "models": "ecmwf_ifs",
        "run": f"{current_date.isoformat()}T00:00",
        "hourly": ",".join(VARIABLES),
        "temperature_unit": "celsius",
        "wind_speed_unit": "ms",
        "timezone": "GMT",
        "timeformat": "unixtime",
        "forecast_days": 2,
    }


def validate_archive(document, expected_params):
    if document.get("request_parameters") != expected_params:
        raise ValueError("Saved request parameters do not match.")

    response = document["response"]

    if response.get("utc_offset_seconds") != 0:
        raise ValueError("Expected UTC timestamps.")

    hourly = response["hourly"]
    units = response["hourly_units"]
    timestamps = hourly["time"]

    if not timestamps:
        raise ValueError("No hourly timestamps returned.")

    if not all(finite_number(value) for value in timestamps):
        raise ValueError("Expected numeric Unix timestamps.")

    if len(set(timestamps)) != len(timestamps):
        raise ValueError("Duplicate hourly timestamps.")

    if timestamps != sorted(timestamps):
        raise ValueError("Hourly timestamps are not ordered.")

    initialization = datetime.fromisoformat(
        expected_params["run"]
    ).replace(tzinfo=timezone.utc)

    initialization_unix = int(initialization.timestamp())

    timestamp_index = {
        timestamp: index
        for index, timestamp in enumerate(timestamps)
    }

    target_times = [
        initialization_unix + lead * 3600
        for lead in range(1, 25)
    ]

    missing_times = [
        timestamp
        for timestamp in target_times
        if timestamp not in timestamp_index
    ]

    if missing_times:
        raise ValueError(
            f"Missing {len(missing_times)} timestamps "
            "within forecast leads 1–24."
        )

    target_indices = [
        timestamp_index[timestamp]
        for timestamp in target_times
    ]

    counts = {}

    for variable in VARIABLES:
        if units.get(variable) != EXPECTED_UNITS[variable]:
            raise ValueError(
                f"Unexpected units for {variable}: "
                f"{units.get(variable)!r}"
            )

        values = hourly[variable]

        if not isinstance(values, list):
            raise ValueError(f"{variable} is not an hourly array.")

        if len(values) != len(timestamps):
            raise ValueError(
                f"Inconsistent array length for {variable}."
            )

        if any(
            value is not None and not finite_number(value)
            for value in values
        ):
            raise ValueError(
                f"Invalid non-missing value in {variable}."
            )

        count = sum(
            finite_number(values[index])
            for index in target_indices
        )

        counts[variable] = count

        if count == 0:
            raise ValueError(
                f"No usable {variable} values at leads 1–24. "
                "Check variable availability for this archive."
            )

        if variable in {"cloud_cover", "relative_humidity_2m"}:
            if any(
                finite_number(values[index])
                and not 0 <= values[index] <= 100
                for index in target_indices
            ):
                raise ValueError(
                    f"Out-of-range percentage in {variable}."
                )

        if variable == "wind_speed_10m":
            if any(
                finite_number(values[index])
                and values[index] < 0
                for index in target_indices
            ):
                raise ValueError("Negative wind speed.")

        if variable == "surface_pressure":
            if any(
                finite_number(values[index])
                and values[index] <= 0
                for index in target_indices
            ):
                raise ValueError("Nonpositive surface pressure.")

    return counts


def download_run(session, params, destination):
    temporary = destination.with_suffix(".json.part")

    for attempt in range(1, 4):
        print(
            f"Downloading {params['run']} UTC "
            f"— attempt {attempt}/3",
            flush=True,
        )

        try:
            response = session.get(
                API_URL,
                params=params,
                timeout=(10, 90),
            )

            if not response.ok:
                raise ValueError(
                    f"HTTP {response.status_code}: "
                    f"{response.text[:500]}"
                )

            document = {
                "schema_version": "forecast_features_v1",
                "retrieved_at_utc": datetime.now(
                    timezone.utc
                ).isoformat(),
                "request_parameters": params,
                "comparison_note": (
                    "Initialization time is not public release time. "
                    "Historical archive provenance requires verification. "
                    "Provider documentation describes early ECMWF IFS "
                    "archive coverage as including hindcasts. Do not "
                    "assume these are verified as-issued operational "
                    "forecasts."
                ),
                "target_selection": (
                    "Forecast leads 1–24 after initialization."
                ),
                "response": response.json(),
            }

            counts = validate_archive(document, params)
            document["valid_target_hour_counts"] = counts

            temporary.write_text(
                json.dumps(
                    document,
                    indent=2,
                    ensure_ascii=False,
                    allow_nan=False,
                ),
                encoding="utf-8",
            )

            temporary.replace(destination)
            return counts

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
                raise RuntimeError(str(error)) from error

            time.sleep(5 * attempt)


def main():
    downloaded = 0
    skipped = 0
    failures = []

    current_date = START_DATE
    first_run_verified = False

    print("Starting atmospheric feature archive download.", flush=True)
    print(f"Output folder: {OUTPUT_DIR}", flush=True)

    with requests.Session() as session:
        while current_date <= END_DATE:
            params = request_parameters(current_date)

            filename = (
                f"ecmwf_features_{current_date:%Y%m%d}_00.json"
            )

            destination = OUTPUT_DIR / filename

            try:
                if destination.exists():
                    document = json.loads(
                        destination.read_text(
                            encoding="utf-8-sig"
                        )
                    )

                    counts = validate_archive(document, params)
                    skipped += 1
                    print(f"SKIP: {filename}", flush=True)

                else:
                    counts = download_run(
                        session,
                        params,
                        destination,
                    )

                    downloaded += 1
                    print(f"SAVED: {filename}", flush=True)

                for variable, count in counts.items():
                    print(
                        f"  {variable}: {count}/24 valid target hours",
                        flush=True,
                    )

                first_run_verified = True

            except (
                OSError,
                ValueError,
                KeyError,
                TypeError,
                RuntimeError,
            ) as error:
                failures.append({
                    "filename": filename,
                    "error": str(error),
                })

                print(
                    f"NEEDS ATTENTION: {filename}: {error}",
                    flush=True,
                )

                if not first_run_verified:
                    print(
                        "Stopping because the first run did not pass. "
                        "Resolve this before downloading the month.",
                        flush=True,
                    )
                    break

            current_date += timedelta(days=1)
            time.sleep(2)

    print("\nDownload summary")
    print(f"New runs downloaded: {downloaded}")
    print(f"Existing runs validated: {skipped}")
    print(f"Runs requiring attention: {len(failures)}")

    for failure in failures:
        print(
            f"  {failure['filename']}: {failure['error']}"
        )


if __name__ == "__main__":
    main()