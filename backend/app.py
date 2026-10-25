from datetime import datetime, timezone
import math

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field


app = FastAPI(title="Solar Forecasting Prototype")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class ForecastRequest(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    hour_ahead: int = Field(default=0, ge=0, le=6)


def cloud_category(cloud_cover):
    # Simple descriptive categories, not satellite cloud-type detection.
    if cloud_cover < 20:
        return "CLEAR"

    if cloud_cover <= 80:
        return "PARTLY_CLOUDY"

    return "OVERCAST"


@app.get("/")
def home():
    return {
        "status": "ok",
        "message": "Solar forecasting backend is running.",
    }


@app.post("/forecast")
def forecast(req: ForecastRequest):
    params = {
        "latitude": req.lat,
        "longitude": req.lon,
        "hourly": "cloud_cover,shortwave_radiation",
        "timezone": "GMT",
        "timeformat": "unixtime",
        "forecast_days": 2,
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

    try:
        hourly = data["hourly"]
        timestamps = hourly["time"]

        # Use the next hourly timestamp, then advance by the selected hours.
        now = datetime.now(timezone.utc).timestamp()
        target = math.ceil(now / 3600) * 3600 + req.hour_ahead * 3600

        index = next(
            (i for i, timestamp in enumerate(timestamps)
             if timestamp >= target),
            None,
        )

        if index is None:
            raise ValueError("Requested time is outside forecast coverage.")

        cloud_cover = hourly["cloud_cover"][index]
        irradiance = hourly["shortwave_radiation"][index]
        timestamp = timestamps[index]

        if cloud_cover is None or irradiance is None:
            raise ValueError("Forecast values are missing.")

        cloud_cover = float(cloud_cover)
        irradiance = float(irradiance)

        if not math.isfinite(cloud_cover) or not math.isfinite(irradiance):
            raise ValueError("Forecast values are invalid.")

    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=502,
            detail="The weather provider did not return usable forecast data.",
        ) from exc

    return {
        "lat": req.lat,
        "lon": req.lon,
        "hour_ahead": req.hour_ahead,
        "condition_class": cloud_category(cloud_cover),
        "estimated_irradiance_wm2": max(0.0, irradiance),
        "cloud_cover_percent": cloud_cover,
        "forecast_time_utc": datetime.fromtimestamp(
            timestamp, tz=timezone.utc
        ).isoformat(),
        "source": "Open-Meteo",
        "method": "Weather-provider forecast; no custom ML model",
        "radiation_period": "Mean over the hour ending at forecast_time_utc",
    }