import json
from pathlib import Path

from collections import OrderedDict, deque
from datetime import datetime, timezone
import os
import math
import threading
import time

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field


app = FastAPI(title="Solar Forecasting Prototype")


def allowed_origins():
    raw = os.environ.get(
        "ALLOWED_ORIGINS",
        "http://127.0.0.1:5500,http://localhost:5500,"
        "http://127.0.0.1:8080,http://localhost:8080",
    )

    origins = [
        item.strip().rstrip("/")
        for item in raw.split(",")
        if item.strip() and item.strip() != "*"
    ]

    return origins


app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


HOURLY_VARIABLES = [
    "shortwave_radiation",
    "cloud_cover",
    "temperature_2m",
    "relative_humidity_2m",
    "wind_speed_10m",
    "surface_pressure",
]

COORDINATE_DECIMALS = 2
CACHE_TTL_SECONDS = 600
CACHE_MAX_ENTRIES = 300
MAX_PROVIDER_CALLS_PER_MINUTE = 40
VERIFICATION_FILE = (
    Path(__file__).resolve().parent / "bondville_verification.json"
)

_cache = OrderedDict()
_provider_call_times = deque()
_lock = threading.Lock()


class ForecastRequest(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    hour_ahead: int = Field(default=0, ge=0, le=6)
    reference_hour_unix: int | None = Field(
        default=None,
        gt=0,
        multiple_of=3600,
    )


def utc_iso(timestamp):
    return datetime.fromtimestamp(
        timestamp,
        tz=timezone.utc,
    ).isoformat()


def requested_hour(req):
    if req.reference_hour_unix is not None:
        reference = req.reference_hour_unix
    else:
        now = datetime.now(timezone.utc).timestamp()
        reference = math.ceil(now / 3600) * 3600

    return reference + req.hour_ahead * 3600


def cloud_category(cloud_cover):
    if cloud_cover < 20:
        return "CLEAR"

    if cloud_cover <= 80:
        return "PARTLY_CLOUDY"

    return "OVERCAST"


def rounded_coordinates(req):
    # Adding 0.0 converts negative zero to zero.
    return (
        round(req.lat, COORDINATE_DECIMALS) + 0.0,
        round(req.lon, COORDINATE_DECIMALS) + 0.0,
    )


def finite_or_none(value):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None

    return number if math.isfinite(number) else None


def cache_get(key):
    now = time.monotonic()

    with _lock:
        entry = _cache.get(key)

        if entry is None:
            return None

        if now - entry["stored_at"] > CACHE_TTL_SECONDS:
            del _cache[key]
            return None

        _cache.move_to_end(key)
        return entry


def cache_put(key, data, retrieved_at):
    with _lock:
        _cache[key] = {
            "data": data,
            "retrieved_at": retrieved_at,
            "stored_at": time.monotonic(),
        }
        _cache.move_to_end(key)

        while len(_cache) > CACHE_MAX_ENTRIES:
            _cache.popitem(last=False)


def reserve_provider_call():
    now = time.monotonic()

    with _lock:
        while (
            _provider_call_times
            and now - _provider_call_times[0] > 60
        ):
            _provider_call_times.popleft()

        if len(_provider_call_times) >= MAX_PROVIDER_CALLS_PER_MINUTE:
            raise HTTPException(
                status_code=429,
                detail=(
                    "Too many new forecast requests. "
                    "Wait a minute and try again."
                ),
            )

        _provider_call_times.append(now)


def retrieve_forecast(req):
    lat, lon = rounded_coordinates(req)
    key = (lat, lon)

    cached = cache_get(key)

    if cached is not None:
        return cached["data"], cached["retrieved_at"], True

    reserve_provider_call()

    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": ",".join(HOURLY_VARIABLES),
        "temperature_unit": "celsius",
        "wind_speed_unit": "ms",
        "timezone": "GMT",
        "timeformat": "unixtime",
        "forecast_days": 3,
    }

    try:
        response = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params=params,
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()

    except requests.RequestException as exc:
        raise HTTPException(
            status_code=502,
            detail="Could not retrieve weather data. Try again shortly.",
        ) from exc

    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail="The weather provider returned an unreadable response.",
        ) from exc

    if not isinstance(data, dict) or "hourly" not in data:
        raise HTTPException(
            status_code=502,
            detail="The weather provider returned an unexpected response.",
        )

    retrieved_at = datetime.now(timezone.utc).isoformat()
    cache_put(key, data, retrieved_at)

    return data, retrieved_at, False


def required_number(values, index):
    value = float(values[index])

    if not math.isfinite(value):
        raise ValueError("A required forecast value is invalid.")

    return value


def optional_number(hourly, variable, index):
    values = hourly.get(variable)

    if not isinstance(values, list) or index >= len(values):
        return None

    return finite_or_none(values[index])


def build_series(data, start_time, count):
    try:
        hourly = data["hourly"]
        timestamps = hourly["time"]

        start_index = next(
            (
                index
                for index, timestamp in enumerate(timestamps)
                if timestamp == start_time
            ),
            None,
        )

        if start_index is None:
            raise ValueError("Requested hour is unavailable.")

        series = []

        for index in range(start_index, start_index + count):
            timestamp = required_number(timestamps, index)

            radiation = required_number(
                hourly["shortwave_radiation"],
                index,
            )

            clouds = required_number(
                hourly["cloud_cover"],
                index,
            )

            expected_time = start_time + len(series) * 3600

            if timestamp != expected_time:
                raise ValueError("Forecast hours are not consecutive.")

            if not 0 <= clouds <= 100:
                raise ValueError("Invalid cloud-cover percentage.")

            temperature = optional_number(
                hourly, "temperature_2m", index
            )
            humidity = optional_number(
                hourly, "relative_humidity_2m", index
            )
            wind = optional_number(
                hourly, "wind_speed_10m", index
            )
            pressure = optional_number(
                hourly, "surface_pressure", index
            )

            if humidity is not None and not 0 <= humidity <= 100:
                humidity = None

            if wind is not None and wind < 0:
                wind = None

            if pressure is not None and pressure <= 0:
                pressure = None

            series.append({
                "time_utc": utc_iso(timestamp),
                "interval_start_utc": utc_iso(timestamp - 3600),
                "irradiance_wm2": max(0.0, radiation),
                "cloud_cover_percent": clouds,
                "condition_class": cloud_category(clouds),
                "temperature_c": temperature,
                "relative_humidity_percent": humidity,
                "wind_speed_ms": wind,
                "surface_pressure_hpa": pressure,
            })

        return series

    except (
        KeyError,
        IndexError,
        TypeError,
        ValueError,
        OverflowError,
        OSError,
    ) as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                "The requested forecast hours were not available "
                "or contained unusable irradiance or cloud data. "
                "Try refreshing the dashboard."
            ),
        ) from exc


def response_metadata(req, retrieved_at, cache_hit, data):
    lat, lon = rounded_coordinates(req)

    return {
        "lat": lat,
        "lon": lon,
        "hour_ahead": req.hour_ahead,
        "retrieved_at_utc": retrieved_at,
        "cache_hit": cache_hit,
        "cache_ttl_seconds": CACHE_TTL_SECONDS,
        "provider_latitude": finite_or_none(data.get("latitude")),
        "provider_longitude": finite_or_none(data.get("longitude")),
        "provider_elevation_m": finite_or_none(data.get("elevation")),
        "source": "Open-Meteo",
        "method": "Weather-provider forecast; no custom ML model",
        "model_note": (
            "Model selected automatically by Open-Meteo; "
            "this app does not choose a specific model."
        ),
        "units": {
            "irradiance_wm2": "W/m²",
            "cloud_cover_percent": "%",
            "temperature_c": "°C",
            "relative_humidity_percent": "%",
            "wind_speed_ms": "m/s",
            "surface_pressure_hpa": "hPa",
        },
        "weather_timing": (
            "Atmospheric variables are forecast values at time_utc. "
            "Irradiance is the mean over the preceding hour."
        ),
    }


@app.get("/")
def home():
    return {
        "status": "ok",
        "backend_version": "3.2",
        "message": "Solar forecasting backend is running.",
        "cache_ttl_seconds": CACHE_TTL_SECONDS,
        "cache_entries": len(_cache),
        "max_provider_calls_per_minute": MAX_PROVIDER_CALLS_PER_MINUTE,
        "features": [
            "Hourly irradiance",
            "Cloud cover",
            "Temperature",
            "Relative humidity",
            "Wind speed",
            "Surface pressure",
            "Short-lived provider cache",
            "Arbitrary coordinates",
        ],
    }


@app.post("/forecast")
def forecast(req: ForecastRequest):
    data, retrieved_at, cache_hit = retrieve_forecast(req)
    hour = build_series(data, requested_hour(req), 1)[0]

    result = response_metadata(req, retrieved_at, cache_hit, data)

    result.update({
        "condition_class": hour["condition_class"],
        "estimated_irradiance_wm2": hour["irradiance_wm2"],
        "cloud_cover_percent": hour["cloud_cover_percent"],
        "forecast_time_utc": hour["time_utc"],
        "interval_start_utc": hour["interval_start_utc"],
        "temperature_c": hour["temperature_c"],
        "relative_humidity_percent": hour["relative_humidity_percent"],
        "wind_speed_ms": hour["wind_speed_ms"],
        "surface_pressure_hpa": hour["surface_pressure_hpa"],
        "radiation_period": (
            "Mean over the hour ending at forecast_time_utc"
        ),
    })

    return result


@app.post("/forecast-series")
def forecast_series(req: ForecastRequest):
    data, retrieved_at, cache_hit = retrieve_forecast(req)
    start_time = requested_hour(req)

    series = build_series(data, start_time, count=24)

    result = response_metadata(req, retrieved_at, cache_hit, data)

    result.update({
        "selected_time_utc": utc_iso(start_time),
        "radiation_period": "Mean over the hour ending at time_utc",
        "series": series,
    })

    return result

@app.get("/verification/bondville")
def bondville_verification():
    try:
        return json.loads(
            VERIFICATION_FILE.read_text(encoding="utf-8")
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail="Bondville verification data has not been generated.",
        ) from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=500,
            detail="Bondville verification data could not be read.",
        ) from exc