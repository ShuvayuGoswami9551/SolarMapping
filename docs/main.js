const API_BASE = (
  window.SOLAR_API_BASE || "http://127.0.0.1:8000"
).replace(/\/+$/, "");
const CUSTOM_KEY = "custom";
const STALE_REFERENCE_MS = 3 * 3600 * 1000;

const grid = document.getElementById("grid");
const hourInput = document.getElementById("hourAhead");
const refreshBtn = document.getElementById("refreshBtn");
const mapStatus = document.getElementById("mapStatus");
const downloadCsvBtn = document.getElementById("downloadCsvBtn");

const chartLocation = document.getElementById("chartLocation");
const chartStatus = document.getElementById("chartStatus");
const tableBody = document.getElementById("forecastTableBody");
const selectedTimeLabel =
  document.getElementById("selectedForecastTime");

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

const weatherFields = [
  {
    key: "temperature_c",
    label: "Temperature at 2 m",
    unit: "°C",
    digits: 1,
  },
  {
    key: "relative_humidity_percent",
    label: "Relative humidity at 2 m",
    unit: "%",
    digits: 0,
  },
  {
    key: "wind_speed_ms",
    label: "Wind speed at 10 m",
    unit: "m/s",
    digits: 1,
  },
  {
    key: "surface_pressure_hpa",
    label: "Surface pressure",
    unit: "hPa",
    digits: 1,
  },
];

let map;
let markerLayer;
let loading = false;
let customLoading = false;
let selectedKey = 1;
let selectedForecast = null;
let irradianceChart = null;

let customPoint = null;
let customCell = null;
let currentReferenceHourUnix = null;
let currentHourAhead = 0;

const forecastCache = new Map();
const markerCache = new Map();

let weatherGrid;
let weatherLocation;
let weatherTimestamp;
let sourceList;
let sourceRetrieved;


let backendCheckedAt = 0;
let backendWake = null;

async function wakeBackend() {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 90000);

  try {
    const response = await fetch(`${API_BASE}/`, {
      signal: controller.signal,
    });
    return response.ok;
  } catch {
    return false;
  } finally {
    clearTimeout(timeoutId);
  }
}

function ensureBackend() {
  if (Date.now() - backendCheckedAt < 10 * 60 * 1000) {
    return Promise.resolve(true);
  }

  if (!backendWake) {
    backendWake = wakeBackend().then((ok) => {
      if (ok) backendCheckedAt = Date.now();
      backendWake = null;
      return ok;
    });
  }

  return backendWake;
}

window.solarEnsureBackend = ensureBackend;

function labelFor(key) {
  return key === CUSTOM_KEY ? "Selected point" : `Location ${key}`;
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


function formatNumber(value, digits = 0) {
  return Number.isFinite(value)
    ? value.toFixed(digits)
    : "Unavailable";
}


function irradianceColor(value) {
  if (value < 100) return "#64748b";
  if (value < 300) return "#60a5fa";
  if (value < 600) return "#facc15";
  return "#f97316";
}


function ageText(isoTime) {
  const seconds = Math.max(
    0,
    Math.round((Date.now() - Date.parse(isoTime)) / 1000)
  );

  if (seconds < 60) return "less than a minute ago";

  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;

  const hours = Math.floor(minutes / 60);
  return `${hours} h ${minutes % 60} min ago`;
}


function retrievedText(data) {
  return (
    `Retrieved: ${formatTime(data.retrieved_at_utc)} ` +
    `(${ageText(data.retrieved_at_utc)}` +
    `${data.cache_hit ? "; served from short-term cache" : ""})`
  );
}


function initializeWeatherPanel() {
  const section = document.createElement("section");
  section.className = "panel";
  section.setAttribute("aria-labelledby", "weather-title");

  const heading = document.createElement("h2");
  heading.id = "weather-title";
  heading.textContent = "Atmospheric conditions at the selected hour";

  weatherLocation = document.createElement("p");
  weatherLocation.textContent = "Select a forecast location.";

  weatherTimestamp = document.createElement("p");
  weatherTimestamp.className = "hint";

  weatherGrid = document.createElement("div");
  weatherGrid.className = "overview";
  weatherGrid.setAttribute(
    "aria-label",
    "Selected location's atmospheric forecast"
  );

  const note = document.createElement("p");
  note.className = "hint";
  note.textContent =
    "Provider-supplied atmospheric context, not inputs to a " +
    "custom machine-learning model. Weather values apply at " +
    "the displayed timestamp; irradiance averages the preceding hour.";

  const details = document.createElement("details");
  const summary = document.createElement("summary");
  summary.textContent = "Data source and timing";

  sourceList = document.createElement("ul");
  sourceList.className = "details";

  details.append(summary, sourceList);

  section.append(
    heading,
    weatherLocation,
    weatherGrid,
    weatherTimestamp,
    note,
    details
  );

  const chartPanel = document
    .getElementById("chart-title")
    .closest("section");

  chartPanel.before(section);
}


function initializeTableHeaders() {
  const headerRow = tableBody
    .closest("table")
    .querySelector("thead tr");

  headerRow.replaceChildren();

  const headings = [
    "Hour ending",
    "Irradiance (W/m²)",
    "Cloud cover (%)",
    "Temperature (°C)",
    "Relative humidity (%)",
    "Wind speed (m/s)",
    "Surface pressure (hPa)",
  ];

  for (const text of headings) {
    const cell = document.createElement("th");
    cell.scope = "col";
    cell.textContent = text;
    headerRow.appendChild(cell);
  }
}


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

  map.fitBounds(
    L.latLngBounds(points.map((point) => [point.lat, point.lon])),
    {
      padding: [35, 35],
      maxZoom: 11,
    }
  );

  L.control.scale().addTo(map);
  map.on("click", onMapClick);
}


async function fetchForecastSeries(
  point,
  hourAhead,
  referenceHourUnix
) {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 30000);

  try {
    const response = await fetch(`${API_BASE}/forecast-series`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        lat: point.lat,
        lon: point.lon,
        hour_ahead: hourAhead,
        reference_hour_unix: referenceHourUnix,
      }),
      signal: controller.signal,
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

    const data = await response.json();
    backendCheckedAt = Date.now();

    const expectedStart =
      (referenceHourUnix + hourAhead * 3600) * 1000;

    if (
      !Array.isArray(data.series) ||
      data.series.length !== 24 ||
      !data.series.every((hour, index) =>
        Date.parse(hour.time_utc) ===
          expectedStart + index * 3600000 &&
        Number.isFinite(hour.irradiance_wm2) &&
        hour.irradiance_wm2 >= 0 &&
        Number.isFinite(hour.cloud_cover_percent) &&
        hour.cloud_cover_percent >= 0 &&
        hour.cloud_cover_percent <= 100
      ) ||
      !Number.isFinite(Date.parse(data.retrieved_at_utc))
    ) {
      throw new Error(
        "The backend returned incomplete or invalid hourly data."
      );
    }

    return data;
  } finally {
    clearTimeout(timeoutId);
  }
}


function errorMessage(error) {
  if (error.name === "AbortError") {
    return "The request timed out. Try refreshing.";
  }

  if (error instanceof TypeError) {
    return "Cannot reach the backend. Check the Python terminal.";
  }

  return error.message;
}


function createPopup(data, point, key) {
  const hour = data.series[0];

  const popup = document.createElement("div");
  popup.className = "forecast-popup";

  const heading = document.createElement("h3");
  heading.textContent =
    key === CUSTOM_KEY ? "Selected point" : `Sample location ${key}`;
  popup.appendChild(heading);

  addLine(
    popup,
    `${point.lat.toFixed(2)}, ${point.lon.toFixed(2)}`
  );

  addLine(
    popup,
    `${formatNumber(hour.irradiance_wm2)} W/m²`,
    true
  );

  addLine(
    popup,
    `Cloud cover: ${formatNumber(hour.cloud_cover_percent)}%`
  );

  addLine(
    popup,
    `Condition: ${labels[hour.condition_class] ?? "Unknown"}`
  );

  for (const field of weatherFields) {
    const value = hour[field.key];

    addLine(
      popup,
      `${field.label}: ` +
      (Number.isFinite(value)
        ? `${formatNumber(value, field.digits)} ${field.unit}`
        : "Unavailable")
    );
  }

  addLine(popup, `Hour ending: ${formatTime(hour.time_utc)}`);
  addLine(popup, `Source: ${data.source}`);

  if (key === CUSTOM_KEY) {
    addLine(popup, "Provider forecast; not validated here.");
  }

  return popup;
}


function displayLocation(data, point, cell, key) {
  const hour = data.series[0];
  const color = irradianceColor(hour.irradiance_wm2);
  const isCustom = key === CUSTOM_KEY;
  const label = labelFor(key);

  const previous = markerCache.get(key);
  if (previous) markerLayer.removeLayer(previous);

  const marker = L.circleMarker([point.lat, point.lon], {
    radius: 12,
    color: "#10283f",
    weight: 2,
    fillColor: color,
    fillOpacity: 0.95,
    dashArray: isCustom ? "4 3" : null,
    bubblingMouseEvents: false,
  }).addTo(markerLayer);

  markerCache.set(key, marker);

  marker.bindPopup(createPopup(data, point, key));

  marker.bindTooltip(
    `${label}: ${formatNumber(hour.irradiance_wm2)} W/m²`
  );

  marker.on("popupopen", () => selectLocation(key));

  cell.replaceChildren();
  cell.dataset.locationKey = String(key);
  cell.style.borderTop = `6px solid ${color}`;

  addLine(cell, label, true);

  addLine(
    cell,
    `${point.lat.toFixed(2)}, ${point.lon.toFixed(2)}`
  );

  addLine(
    cell,
    `${formatNumber(hour.irradiance_wm2)} W/m²`,
    true
  );

  addLine(
    cell,
    `${labels[hour.condition_class] ?? "Unknown"} · ` +
    `${formatNumber(hour.cloud_cover_percent)}% cloud`
  );

  addLine(cell, `Hour ending: ${formatTime(hour.time_utc)}`);

  const button = document.createElement("button");
  button.type = "button";
  button.textContent = "Select location";

  button.addEventListener("click", () => {
    selectLocation(key);
    map.setView([point.lat, point.lon], 12);
    marker.openPopup();
  });

  cell.appendChild(button);
}


function showCellError(cell, label, error) {
  cell.replaceChildren();
  cell.style.borderTop = "";
  delete cell.dataset.locationKey;
  addLine(cell, `${label}: unavailable`, true);
  addLine(cell, errorMessage(error));
}


function clearSelection() {
  selectedForecast = null;
  downloadCsvBtn.disabled = true;
  tableBody.replaceChildren();
  weatherGrid.replaceChildren();
  sourceList.replaceChildren();
  sourceRetrieved = null;

  weatherLocation.textContent = "Waiting for location forecasts…";
  weatherTimestamp.textContent = "";

  if (irradianceChart) {
    irradianceChart.destroy();
    irradianceChart = null;
  }
}


function renderWeather() {
  const data = selectedForecast;
  const hour = data.series[0];

  weatherLocation.textContent =
    `${data.label}: ${data.lat.toFixed(2)}, ` +
    `${data.lon.toFixed(2)}` +
    (data.key === CUSTOM_KEY
      ? " (user-selected; not one of the sample locations)"
      : "");

  weatherGrid.replaceChildren();

  const fields = [
    {
      key: "cloud_cover_percent",
      label: "Cloud cover",
      unit: "%",
      digits: 0,
    },
    ...weatherFields,
  ];

  for (const field of fields) {
    const card = document.createElement("div");
    card.className = "overview-card";

    const label = document.createElement("span");
    label.className = "overview-label";
    label.textContent = field.label;

    const value = document.createElement("span");
    value.className = "overview-value";

    const rawValue = hour[field.key];

    value.textContent = Number.isFinite(rawValue)
      ? `${formatNumber(rawValue, field.digits)} ${field.unit}`
      : "Unavailable";

    card.append(label, value);
    weatherGrid.appendChild(card);
  }

  weatherTimestamp.textContent =
    `Forecast valid: ${formatTime(hour.time_utc)}.`;
}


function renderSource() {
  const data = selectedForecast;

  sourceList.replaceChildren();

  const items = [
    `Source: ${data.source}. ${data.method}.`,
    data.model_note ?? "Model selected automatically by the provider.",
    `Requested point: ${data.lat.toFixed(2)}, ${data.lon.toFixed(2)}.` +
      (Number.isFinite(data.provider_latitude) &&
      Number.isFinite(data.provider_longitude)
        ? ` Provider grid point: ` +
          `${data.provider_latitude.toFixed(2)}, ` +
          `${data.provider_longitude.toFixed(2)}`
        : "") +
      (Number.isFinite(data.provider_elevation_m)
        ? `; elevation ${data.provider_elevation_m.toFixed(0)} m.`
        : "."),
    null,
    "Irradiance is a mean over the hour ending at the displayed " +
      "time. Weather values apply at that timestamp.",
    "No accuracy validation is claimed for this location. The " +
      "separate Bondville, Illinois study does not establish " +
      "accuracy here.",
  ];

  for (const text of items) {
    const item = document.createElement("li");

    if (text === null) {
      sourceRetrieved = item;
      item.textContent = retrievedText(data);
    } else {
      item.textContent = text;
    }

    sourceList.appendChild(item);
  }
}


function selectLocation(key) {
  const data = forecastCache.get(key);
  if (!data) return;

  selectedKey = key;
  selectedForecast = { ...data, key, label: labelFor(key) };

  document.querySelectorAll(".cell").forEach((cell) => {
    cell.classList.toggle(
      "selected",
      cell.dataset.locationKey === String(key)
    );
  });

  markerCache.forEach((marker, markerKey) => {
    marker.setStyle({
      weight: markerKey === key ? 4 : 2,
    });
    marker.setRadius(markerKey === key ? 14 : 12);
  });

  renderWeather();
  renderSource();
  renderChart();
}


function renderChart() {
  const data = selectedForecast;
  const series = data.series;

  chartLocation.textContent =
    `${data.label}: ${data.lat.toFixed(2)}, ${data.lon.toFixed(2)}`;

  tableBody.replaceChildren();

  if (irradianceChart) {
    irradianceChart.destroy();
    irradianceChart = null;
  }

  for (const [index, hour] of series.entries()) {
    const row = document.createElement("tr");

    if (index === 0) row.className = "selected-hour";

    const values = [
      formatTime(hour.time_utc),
      formatNumber(hour.irradiance_wm2),
      formatNumber(hour.cloud_cover_percent),
      ...weatherFields.map((field) =>
        formatNumber(hour[field.key], field.digits)
      ),
    ];

    for (const value of values) {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.appendChild(cell);
    }

    tableBody.appendChild(row);
  }

  downloadCsvBtn.disabled = false;

  if (typeof Chart === "undefined") {
    chartStatus.textContent =
      "Chart library unavailable. Use the hourly table or CSV.";
    return;
  }

  irradianceChart = new Chart(
    document.getElementById("irradianceChart"),
    {
      type: "line",
      data: {
        labels: series.map((hour) => formatTime(hour.time_utc)),
        datasets: [
          {
            label: "Forecast irradiance (W/m²)",
            data: series.map((hour) => hour.irradiance_wm2),
            borderColor: "#007f86",
            backgroundColor: "rgba(0, 127, 134, 0.12)",
            fill: true,
            tension: 0,
            pointRadius: series.map((_, i) => i === 0 ? 6 : 3),
            pointBackgroundColor: series.map((_, i) =>
              i === 0 ? "#d68b12" : "#007f86"
            ),
            pointHoverRadius: 7,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: {
          intersect: false,
          mode: "index",
        },
        scales: {
          x: {
            title: {
              display: true,
              text: "Hour ending — browser local time",
            },
            ticks: { maxTicksLimit: 8 },
          },
          y: {
            beginAtZero: true,
            title: {
              display: true,
              text: "Irradiance (W/m²)",
            },
          },
        },
      },
    }
  );

  chartStatus.textContent =
    `Showing 24 hours beginning ${formatTime(series[0].time_utc)}. ` +
    "The highlighted first point matches the map and weather panel.";
}


function firstSampleKey() {
  for (const key of forecastCache.keys()) {
    if (key !== CUSTOM_KEY) return key;
  }
  return forecastCache.keys().next().value;
}


async function loadCustomPoint(point, select) {
  customLoading = true;
  refreshBtn.disabled = true;

  if (!customCell) {
    customCell = document.createElement("div");
    customCell.className = "cell custom";
    grid.appendChild(customCell);
  }

  const previous = markerCache.get(CUSTOM_KEY);
  if (previous) markerLayer.removeLayer(previous);
  markerCache.delete(CUSTOM_KEY);
  forecastCache.delete(CUSTOM_KEY);

  customCell.replaceChildren();
  customCell.style.borderTop = "";
  customCell.classList.remove("selected");
  customCell.textContent = "Loading selected point…";

  mapStatus.textContent =
    `Loading forecast for ${point.lat.toFixed(2)}, ` +
    `${point.lon.toFixed(2)}…`;

  try {
    const data = await fetchForecastSeries(
      point,
      currentHourAhead,
      currentReferenceHourUnix
    );

    forecastCache.set(CUSTOM_KEY, data);
    displayLocation(data, point, customCell, CUSTOM_KEY);

    mapStatus.textContent =
      "Selected-point forecast loaded. It is a provider forecast " +
      "and has not been validated.";

    if (select) {
      selectLocation(CUSTOM_KEY);
    }
  } catch (error) {
    console.error("Selected-point request failed:", error);

    showCellError(customCell, "Selected point", error);
    mapStatus.textContent = "Selected-point forecast unavailable.";

    if (selectedKey === CUSTOM_KEY && forecastCache.size > 0) {
      selectLocation(firstSampleKey());
    }
  } finally {
    customLoading = false;
    if (!loading) refreshBtn.disabled = false;
  }
}


function onMapClick(event) {
  if (loading || customLoading) return;

  if (currentReferenceHourUnix === null) {
    mapStatus.textContent = "Wait for the first forecasts to load.";
    return;
  }

  if (Date.now() - currentReferenceHourUnix * 1000 >
      STALE_REFERENCE_MS) {
    mapStatus.textContent =
      "The displayed forecast hour is out of date. " +
      "Press Refresh data first.";
    return;
  }

  const wrapped = event.latlng.wrap();

  const lat = Number(wrapped.lat.toFixed(2));
  const lon = Number(wrapped.lng.toFixed(2));

  if (!(Math.abs(lat) <= 90 && Math.abs(lon) <= 180)) return;

  customPoint = { lat, lon };
  loadCustomPoint(customPoint, true);
}


async function refreshForecasts() {
  if (loading || customLoading) return;

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

  const referenceHourUnix =
    Math.ceil(Date.now() / 1000 / 3600) * 3600;

  currentReferenceHourUnix = referenceHourUnix;
  currentHourAhead = hourAhead;

  const selectedTime = new Date(
    (referenceHourUnix + hourAhead * 3600) * 1000
  ).toISOString();

  loading = true;
  refreshBtn.disabled = true;
  hourInput.disabled = true;
  refreshBtn.textContent = "Loading…";

  selectedTimeLabel.textContent =
    `Requested hour ending: ${formatTime(selectedTime)}`;

  forecastCache.clear();
  markerCache.clear();
  markerLayer.clearLayers();
  grid.replaceChildren();
  customCell = null;
  clearSelection();

  chartLocation.textContent = "Waiting for location forecasts…";
  chartStatus.textContent = "Loading forecast data…";

  let loadedSamples = 0;

  try {
    await ensureBackend();
    for (const [index, point] of points.entries()) {
      mapStatus.textContent =
        "Connecting to the forecast server. If it was idle, " +
        "this can take up to a minute…";

      await ensureBackend();
      const locationNumber = index + 1;

      mapStatus.textContent =
        `Loading location ${locationNumber} of ${points.length}…`;

      const cell = document.createElement("div");
      cell.className = "cell";
      cell.textContent = `Loading location ${locationNumber}…`;
      grid.appendChild(cell);

      try {
        const data = await fetchForecastSeries(
          point,
          hourAhead,
          referenceHourUnix
        );

        displayLocation(data, point, cell, locationNumber);
        forecastCache.set(locationNumber, data);
        loadedSamples += 1;
      } catch (error) {
        console.error("Forecast request failed:", error);
        showCellError(cell, `Location ${locationNumber}`, error);
      }
    }

    const hadCustomSelected = selectedKey === CUSTOM_KEY;

    if (customPoint) {
      await loadCustomPoint(customPoint, false);
      refreshBtn.disabled = true;
    }

    mapStatus.textContent =
      `Loaded ${loadedSamples} of ${points.length} sample locations` +
      (customPoint && forecastCache.has(CUSTOM_KEY)
        ? " plus the selected point."
        : ".");

    if (forecastCache.size > 0) {
      selectedTimeLabel.textContent =
        `Forecast hour ending: ${formatTime(selectedTime)}`;

      const key =
        forecastCache.has(selectedKey) && (
          selectedKey !== CUSTOM_KEY || hadCustomSelected
        )
          ? selectedKey
          : firstSampleKey();

      selectLocation(key);
    } else {
      selectedTimeLabel.textContent =
        "Forecast time unavailable because no forecasts loaded.";

      chartLocation.textContent = "No location available.";
      chartStatus.textContent =
        "Check the error on a location card.";

      weatherLocation.textContent = "No forecast available.";
    }
  } finally {
    loading = false;
    refreshBtn.disabled = false;
    hourInput.disabled = false;
    refreshBtn.textContent = "Refresh data";
  }
}


function escapeCsvValue(value) {
  return `"${String(value ?? "").replaceAll('"', '""')}"`;
}


function downloadForecastCsv() {
  if (!selectedForecast) return;

  const forecast = selectedForecast;
  const isCustom = forecast.key === CUSTOM_KEY;

  const headers = [
    "location_label",
    "location_type",
    "latitude",
    "longitude",
    "provider_latitude",
    "provider_longitude",
    "provider_elevation_m",
    "interval_start_utc",
    "hour_ending_utc",
    "mean_irradiance_wm2",
    "cloud_cover_percent",
    "temperature_c",
    "relative_humidity_percent",
    "wind_speed_ms",
    "surface_pressure_hpa",
    "source",
    "method",
    "retrieved_at_utc",
    "served_from_cache",
    "radiation_period",
    "weather_timing",
    "validation_status",
  ];

  const rows = forecast.series.map((hour) => [
    forecast.label,
    isCustom ? "user_selected_point" : "sample_location",
    forecast.lat,
    forecast.lon,
    forecast.provider_latitude,
    forecast.provider_longitude,
    forecast.provider_elevation_m,
    hour.interval_start_utc,
    hour.time_utc,
    hour.irradiance_wm2,
    hour.cloud_cover_percent,
    hour.temperature_c,
    hour.relative_humidity_percent,
    hour.wind_speed_ms,
    hour.surface_pressure_hpa,
    forecast.source,
    forecast.method,
    forecast.retrieved_at_utc,
    forecast.cache_hit,
    "Mean over the hour ending at hour_ending_utc",
    "Atmospheric values apply at hour_ending_utc",
    "Not validated at this location",
  ]);

  const csv = [headers, ...rows]
    .map((row) => row.map(escapeCsvValue).join(","))
    .join("\r\n");

  const blob = new Blob(["\uFEFF", csv], {
    type: "text/csv;charset=utf-8;",
  });

  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");

  const locationPart = isCustom
    ? `selected-point-${forecast.lat.toFixed(2)}_` +
      `${forecast.lon.toFixed(2)}`
    : `location-${forecast.key}`;

  link.href = url;
  link.download =
    `solar-forecast-${locationPart}-` +
    `${forecast.series[0].time_utc.slice(0, 10)}.csv`;

  document.body.appendChild(link);
  link.click();
  link.remove();

  setTimeout(() => URL.revokeObjectURL(url), 10000);
}


initializeWeatherPanel();
initializeTableHeaders();

downloadCsvBtn.addEventListener("click", downloadForecastCsv);

setInterval(() => {
  if (selectedForecast && sourceRetrieved) {
    sourceRetrieved.textContent = retrievedText(selectedForecast);
  }
}, 30000);

const browserTimezone =
  Intl.DateTimeFormat().resolvedOptions().timeZone;

document.getElementById("timezoneNote").textContent =
  `Displayed times use your browser's time zone: ${browserTimezone}. ` +
  "CSV timestamps use UTC.";

if (typeof L === "undefined") {
  mapStatus.textContent =
    "The map library could not load. Check your internet connection.";
  refreshBtn.disabled = true;
} else {
  initializeMap();
  refreshBtn.addEventListener("click", refreshForecasts);
  refreshForecasts();
}