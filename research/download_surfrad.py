from datetime import date, timedelta
from pathlib import Path
import time

import requests


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

print("SURFRAD downloader started.", flush=True)
print(f"Data folder: {DATA_DIR}", flush=True)

START_DATE = date(2025, 1, 1)
END_DATE = date(2025, 2, 1)  # Inclusive; completes January's last hour.

BASE_URL = "https://gml.noaa.gov/aftp/data/radiation/surfrad/bon"


def validate_file(path, expected_date):
    with path.open(encoding="utf-8-sig") as file:
        station = file.readline().strip()
        metadata = file.readline().strip()
        rows = [line.split() for line in file if line.strip()]

    if station.lower() != "bondville":
        raise ValueError("File does not identify the Bondville station.")

    if not metadata or not rows:
        raise ValueError("Header or observation rows are missing.")

    seen_times = set()

    for row in rows:
        if len(row) != 48:
            raise ValueError("Unexpected number of data columns.")

        values = [float(value) for value in row]

        year, day_of_year, month, day, hour, minute = values[:6]

        if any(
            not value.is_integer()
            for value in values[:6]
        ):
            raise ValueError("Invalid date or time fields.")

        observation_date = date(int(year), int(month), int(day))

        if observation_date != expected_date:
            raise ValueError("File contains an unexpected date.")

        if int(day_of_year) != expected_date.timetuple().tm_yday:
            raise ValueError("Day-of-year field does not match the date.")

        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("Invalid hour or minute.")

        timestamp = (int(hour), int(minute))

        if timestamp in seen_times:
            raise ValueError("Duplicate minute timestamp.")

        seen_times.add(timestamp)

    return len(rows)


downloaded = 0
skipped = 0
failures = []

with requests.Session() as session:
    current_date = START_DATE

    while current_date <= END_DATE:
        filename = f"bon{current_date:%y%j}.dat"
        destination = DATA_DIR / filename
        text_copy = DATA_DIR / f"{filename}.txt"
        temporary = DATA_DIR / f"{filename}.part"

        existing = next(
            (
                path for path in (destination, text_copy)
                if path.exists()
            ),
            None,
        )

        if existing is not None:
            try:
                count = validate_file(existing, current_date)
                print(f"SKIP: {existing.name} ({count} rows)")
                skipped += 1

            except (OSError, ValueError) as error:
                print(f"CHECK EXISTING FILE: {existing.name}: {error}")
                failures.append(filename)

            current_date += timedelta(days=1)
            continue

        url = f"{BASE_URL}/{current_date.year}/{filename}"

        for attempt in range(1, 4):
            try:
                print(
                    f"Downloading {filename} — attempt {attempt}/3...",
                    flush=True,
                )

                response = session.get(url, timeout=(10, 30))
                response.raise_for_status()

                temporary.write_bytes(response.content)
                count = validate_file(temporary, current_date)
                temporary.replace(destination)

                print(f"SAVED: {filename} ({count} rows)")
                downloaded += 1
                break

            except (requests.RequestException, OSError, ValueError) as error:
                temporary.unlink(missing_ok=True)

                print(
                    f"Attempt {attempt}/3 failed for {filename}: {error}"
                )

                if attempt == 3:
                    failures.append(filename)
                else:
                    time.sleep(3 * attempt)

        time.sleep(1)
        current_date += timedelta(days=1)

print("\nDownload summary")
print(f"New files downloaded: {downloaded}")
print(f"Existing files checked and skipped: {skipped}")
print(f"Files requiring attention: {len(failures)}")

if failures:
    print("Check these files:")
    for filename in failures:
        print(f"  {filename}")