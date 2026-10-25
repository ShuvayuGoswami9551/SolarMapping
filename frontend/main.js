const API_BASE = "http://127.0.0.1:8000";

const grid = document.getElementById("grid");
const hourInput = document.getElementById("hourAhead");
const refreshBtn = document.getElementById("refreshBtn");

const points = [
  { lat: 40.34, lon: -94.87 },
  { lat: 40.34, lon: -94.77 },
  { lat: 40.44, lon: -94.87 },
  { lat: 40.44, lon: -94.77 },
  { lat: 40.54, lon: -94.87 },
  { lat: 40.54, lon: -94.77 },
  { lat: 40.64, lon: -94.87 },
  { lat: 40.64, lon: -94.77 },
];

const labels = {
  CLEAR: "Clear",
  PARTLY_CLOUDY: "Partly cloudy",
  OVERCAST: "Overcast",
};

function addLine(parent, text, bold = false) {
  const line = document.createElement(bold ? "strong" : "div");
  line.textContent = text;
  parent.appendChild(line);
}

async function fetchForecast(point, hourAhead) {
  const response = await fetch(`${API_BASE}/forecast`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      lat: point.lat,
      lon: point.lon,
      hour_ahead: hourAhead,
    }),
    signal: AbortSignal.timeout(30000),
  });

  if (!response.ok) {
    let message = `Backend error: HTTP ${response.status}`;

    try {
      const error = await response.json();

      if (typeof error.detail === "string") {
        message = error.detail;
      }
    } catch {
      // Keep the HTTP status message if the response is not JSON.
    }

    throw new Error(message);
  }

  return response.json();
}

async function refreshGrid() {
  const hourAhead = Number(hourInput.value);

  if (
    !Number.isInteger(hourAhead) ||
    hourAhead < 0 ||
    hourAhead > 6
  ) {
    alert("Enter a whole number from 0 to 6.");
    return;
  }

  refreshBtn.disabled = true;
  hourInput.disabled = true;
  refreshBtn.textContent = "Loading…";
  grid.replaceChildren();

  try {
    for (const point of points) {
      const cell = document.createElement("div");
      cell.className = "cell";
      cell.textContent = "Loading forecast…";
      grid.appendChild(cell);

      try {
        const forecast = await fetchForecast(point, hourAhead);

        cell.replaceChildren();
        cell.classList.add(forecast.condition_class);

        addLine(
          cell,
          labels[forecast.condition_class] ?? "Unknown",
          true
        );

        addLine(
          cell,
          `${Math.round(forecast.estimated_irradiance_wm2)} W/m²`
        );

        addLine(
          cell,
          `Cloud cover: ${Math.round(forecast.cloud_cover_percent)}%`
        );

        addLine(
          cell,
          `${point.lat.toFixed(2)}, ${point.lon.toFixed(2)}`
        );

        const time = new Date(
          forecast.forecast_time_utc
        ).toLocaleString([], {
          month: "short",
          day: "numeric",
          hour: "numeric",
          minute: "2-digit",
          timeZoneName: "short",
        });

        addLine(cell, `Hour ending: ${time}`);
        addLine(cell, "Source: Open-Meteo");
      } catch (error) {
        console.error("Forecast request failed:", error);

        cell.replaceChildren();
        addLine(cell, "Could not load forecast", true);

        const message =
          error instanceof TypeError
            ? "Cannot reach the backend. Check that Python is running on port 8000."
            : error.message;

        addLine(cell, message);
      }
    }
  } finally {
    refreshBtn.disabled = false;
    hourInput.disabled = false;
    refreshBtn.textContent = "Refresh data";
  }
}

refreshBtn.addEventListener("click", refreshGrid);
refreshGrid();