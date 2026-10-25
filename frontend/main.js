const API_BASE = "http://127.0.0.1:8000";

const grid = document.getElementById("grid");
const hourInput = document.getElementById("hourAhead");
const refreshBtn = document.getElementById("refreshBtn");
const mapStatus = document.getElementById("mapStatus");

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

let map;
let markerLayer;
let loading = false;

function initializeMap() {
  map = L.map("map");

  L.tileLayer(
    "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    {
      maxZoom: 19,
      attribution:
        '&copy; <a href="https://www.openstreetmap.org/copyright">' +
        "OpenStreetMap contributors</a>",
    }
  ).addTo(map);

  markerLayer = L.layerGroup().addTo(map);

  const bounds = L.latLngBounds(
    points.map((point) => [point.lat, point.lon])
  );

  map.fitBounds(bounds, {
    padding: [35, 35],
    maxZoom: 11,
  });

  L.control.scale().addTo(map);
}

function irradianceColor(value) {
  if (value < 100) return "#64748b";
  if (value < 300) return "#60a5fa";
  if (value < 600) return "#facc15";
  return "#f97316";
}

function addLine(parent, text, bold = false) {
  const line = document.createElement(bold ? "strong" : "div");
  line.textContent = text;
  parent.appendChild(line);
}

function formatTime(isoTime) {
  return new Date(isoTime).toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  });
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
      // Keep the HTTP status message.
    }

    throw new Error(message);
  }

  const forecast = await response.json();

  if (
    !Number.isFinite(forecast.estimated_irradiance_wm2) ||
    !Number.isFinite(forecast.cloud_cover_percent) ||
    !Number.isFinite(Date.parse(forecast.forecast_time_utc))
  ) {
    throw new Error("The backend returned invalid forecast values.");
  }

  return forecast;
}

function createPopup(forecast, point, locationNumber) {
  const popup = document.createElement("div");
  popup.className = "forecast-popup";

  const heading = document.createElement("h3");
  heading.textContent = `Sample location ${locationNumber}`;
  popup.appendChild(heading);

  addLine(
    popup,
    `${point.lat.toFixed(2)}, ${point.lon.toFixed(2)}`
  );

  addLine(
    popup,
    `${Math.round(forecast.estimated_irradiance_wm2)} W/m²`,
    true
  );

  addLine(
    popup,
    `Cloud cover: ${Math.round(forecast.cloud_cover_percent)}%`
  );

  addLine(
    popup,
    `Condition: ${labels[forecast.condition_class] ?? "Unknown"}`
  );

  addLine(
    popup,
    `Hour ending: ${formatTime(forecast.forecast_time_utc)}`
  );

  addLine(popup, "Hourly mean irradiance");
  addLine(popup, "Source: Open-Meteo");

  return popup;
}

function displayForecast(forecast, point, cell, locationNumber) {
  const color = irradianceColor(
    forecast.estimated_irradiance_wm2
  );

  const marker = L.circleMarker([point.lat, point.lon], {
    radius: 12,
    color: "#10283f",
    weight: 2,
    fillColor: color,
    fillOpacity: 0.95,
  }).addTo(markerLayer);

  marker.bindPopup(
    createPopup(forecast, point, locationNumber)
  );

  marker.bindTooltip(
    `Location ${locationNumber}: ` +
    `${Math.round(forecast.estimated_irradiance_wm2)} W/m²`
  );

  cell.replaceChildren();
  cell.style.borderTop = `6px solid ${color}`;

  addLine(cell, `Location ${locationNumber}`, true);

  addLine(
    cell,
    `${point.lat.toFixed(2)}, ${point.lon.toFixed(2)}`
  );

  addLine(
    cell,
    `${Math.round(forecast.estimated_irradiance_wm2)} W/m²`,
    true
  );

  addLine(
    cell,
    `${labels[forecast.condition_class] ?? "Unknown"} · ` +
    `${Math.round(forecast.cloud_cover_percent)}% cloud`
  );

  addLine(
    cell,
    `Hour ending: ${formatTime(forecast.forecast_time_utc)}`
  );

  const viewButton = document.createElement("button");
  viewButton.type = "button";
  viewButton.textContent = "View on map";

  viewButton.addEventListener("click", () => {
    map.setView([point.lat, point.lon], 12);
    marker.openPopup();

    document.getElementById("map").scrollIntoView({
      behavior: "smooth",
      block: "center",
    });
  });

  cell.appendChild(viewButton);
}

async function refreshForecasts() {
  if (loading) return;

  const hourAhead = Number(hourInput.value);

  if (
    hourInput.value.trim() === "" ||
    !Number.isInteger(hourAhead) ||
    hourAhead < 0 ||
    hourAhead > 6
  ) {
    alert("Enter a whole number from 0 to 6.");
    return;
  }

  loading = true;
  refreshBtn.disabled = true;
  hourInput.disabled = true;
  refreshBtn.textContent = "Loading…";

  grid.replaceChildren();
  markerLayer.clearLayers();

  let successful = 0;

  try {
    // Load sequentially to keep provider requests modest.
    for (const [index, point] of points.entries()) {
      mapStatus.textContent =
        `Loading location ${index + 1} of ${points.length}…`;

      const cell = document.createElement("div");
      cell.className = "cell";
      cell.textContent = `Loading location ${index + 1}…`;
      grid.appendChild(cell);

      try {
        const forecast = await fetchForecast(point, hourAhead);

        displayForecast(forecast, point, cell, index + 1);
        successful += 1;
      } catch (error) {
        console.error("Forecast request failed:", error);

        cell.replaceChildren();
        addLine(cell, `Location ${index + 1}: unavailable`, true);

        const message =
          error.name === "TimeoutError"
            ? "The request timed out. Try refreshing."
            : error instanceof TypeError
              ? "Cannot reach the backend. Check the Python terminal."
              : error.message;

        addLine(cell, message);
      }
    }

    mapStatus.textContent =
      successful === points.length
        ? `Loaded all ${successful} locations. Click a marker for details.`
        : `Loaded ${successful} of ${points.length} locations. ` +
          "See the cards below for errors.";
  } finally {
    loading = false;
    refreshBtn.disabled = false;
    hourInput.disabled = false;
    refreshBtn.textContent = "Refresh data";
  }
}

if (typeof L === "undefined") {
  mapStatus.textContent =
    "The map library could not load. Check your internet connection " +
    "and the Leaflet script tag in index.html.";

  refreshBtn.disabled = true;
} else {
  initializeMap();
  refreshBtn.addEventListener("click", refreshForecasts);
  refreshForecasts();
}