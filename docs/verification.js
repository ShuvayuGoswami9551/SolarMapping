(() => {
  "use strict";

  const API_BASE = (
    window.SOLAR_API_BASE || "http://127.0.0.1:8000"
  ).replace(/\/+$/, "");

  const VERIFICATION_URL = `${API_BASE}/verification/bondville`;

  const candidateNames = {
    baseline: "Provider baseline",
    additive: "Additive correction",
    linear: "Linear correction",
    random_forest: "Random Forest correction",
  };

  const partitionNames = {
    validation: "Validation (Jan 21–25 initializations)",
    test: "Exploratory test (Jan 26–31 initializations)",
  };

  const subsetNames = {
    all_valid_hours: "All eligible hours",
    observed_ghi_above_20_wm2: "Observed GHI > 20 W/m²",
  };

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function fmt(value, digits = 3) {
    return Number.isFinite(value) ? value.toFixed(digits) : "Unavailable";
  }

  function utcLabel(iso) {
    return iso.slice(5, 16).replace("T", " ");
  }

  function buildTable(caption, headings, rows) {
    const wrapper = el("div", "table-container");
    const table = el("table", "forecast-table");

    table.appendChild(el("caption", "", caption));

    const head = el("thead");
    const headRow = el("tr");

    for (const heading of headings) {
      const cell = el("th", "", heading);
      cell.scope = "col";
      headRow.appendChild(cell);
    }

    head.appendChild(headRow);
    table.appendChild(head);

    const body = el("tbody");

    for (const values of rows) {
      const row = el("tr");

      for (const value of values) {
        row.appendChild(el("td", "", String(value)));
      }

      body.appendChild(row);
    }

    table.appendChild(body);
    wrapper.appendChild(table);
    return wrapper;
  }

  function chartBox(id, label) {
    const box = el("div", "chart-container");
    const canvas = document.createElement("canvas");

    canvas.id = id;
    canvas.setAttribute("role", "img");
    canvas.setAttribute("aria-label", label);

    box.appendChild(canvas);
    return box;
  }

  function validDocument(doc) {
    return (
      doc &&
      Array.isArray(doc.series) &&
      doc.series.length > 0 &&
      doc.series.every(
        (row) =>
          typeof row.time_utc === "string" &&
          Number.isFinite(Date.parse(row.time_utc)) &&
          Number.isFinite(row.forecast_wm2) &&
          Number.isFinite(row.observed_wm2)
      ) &&
      doc.metrics &&
      doc.metrics.all_eligible_hours &&
      Array.isArray(doc.metrics_by_lead)
    );
  }

  function metricRow(label, block) {
    return [
      label,
      block.matched_hours,
      fmt(block.mae_wm2),
      fmt(block.rmse_wm2),
      fmt(block.mean_bias_wm2),
    ];
  }

  function experimentRows(rows) {
    return rows.map((row) => [
      partitionNames[row.partition] ?? row.partition,
      candidateNames[row.candidate] ?? row.candidate,
      subsetNames[row.evaluation_subset] ?? row.evaluation_subset,
      row.matched_hours,
      fmt(row.mae_wm2),
      fmt(row.rmse_wm2),
      fmt(row.mean_bias_wm2),
    ]);
  }

  function drawTimeSeries(doc) {
    const byTime = new Map(
      doc.series.map((row) => [Date.parse(row.time_utc), row])
    );

    const start = Date.parse(doc.series[0].time_utc);
    const end = Date.parse(
      doc.series[doc.series.length - 1].time_utc
    );

    const labels = [];
    const observed = [];
    const forecast = [];

    for (let time = start; time <= end; time += 3600000) {
      const row = byTime.get(time);

      labels.push(utcLabel(new Date(time).toISOString()));
      observed.push(row ? row.observed_wm2 : null);
      forecast.push(row ? row.forecast_wm2 : null);
    }

    new Chart(document.getElementById("verificationSeriesChart"), {
      type: "line",
      data: {
        labels,
        datasets: [
          {
            label: "SURFRAD observation",
            data: observed,
            borderColor: "#007f86",
            borderWidth: 1.5,
            pointRadius: 0,
            tension: 0,
            spanGaps: false,
          },
          {
            label: "Archived forecast",
            data: forecast,
            borderColor: "#d68b12",
            borderWidth: 1.2,
            pointRadius: 0,
            tension: 0,
            spanGaps: false,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { intersect: false, mode: "index" },
        scales: {
          x: {
            title: { display: true, text: "Hour ending (UTC)" },
            ticks: { maxTicksLimit: 8, maxRotation: 0 },
          },
          y: {
            beginAtZero: true,
            title: {
              display: true,
              text: "Hourly mean irradiance (W/m²)",
            },
          },
        },
      },
    });
  }

  function drawLeadChart(doc) {
    const leads = doc.metrics_by_lead;

    new Chart(document.getElementById("verificationLeadChart"), {
      type: "line",
      data: {
        labels: leads.map((row) => row.lead_hours),
        datasets: [
          {
            label: "RMSE (W/m²)",
            data: leads.map((row) => row.rmse_wm2),
            borderColor: "#10283f",
            backgroundColor: "#10283f",
            tension: 0,
          },
          {
            label: "MAE (W/m²)",
            data: leads.map((row) => row.mae_wm2),
            borderColor: "#d68b12",
            backgroundColor: "#d68b12",
            tension: 0,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { intersect: false, mode: "index" },
        scales: {
          x: {
            title: {
              display: true,
              text: "Hours after 00:00 UTC initialization",
            },
          },
          y: {
            beginAtZero: true,
            title: { display: true, text: "Error (W/m²)" },
          },
        },
      },
    });
  }

  function render(doc, content) {
    content.replaceChildren();

    const notice = el("div", "notice");
    notice.appendChild(
      el(
        "p",
        "",
        `${doc.location.name}, ${utcLabel(doc.period.first_target_hour_utc)} ` +
        `to ${utcLabel(doc.period.last_target_hour_utc)} UTC. ` +
        doc.scope_note
      )
    );
    content.appendChild(notice);

    content.appendChild(el("h3", "", "Baseline errors"));

    content.appendChild(
      buildTable(
        "Uncorrected archived forecast compared with SURFRAD.",
        [
          "Subset",
          "Hours",
          "MAE (W/m²)",
          "RMSE (W/m²)",
          "Mean bias (W/m²)",
        ],
        [
          metricRow(
            "All eligible hours",
            doc.metrics.all_eligible_hours
          ),
          ...(doc.metrics.observed_ghi_above_20_wm2
            ? [
                metricRow(
                  "Observed GHI > 20 W/m²",
                  doc.metrics.observed_ghi_above_20_wm2
                ),
              ]
            : []),
        ]
      )
    );

    content.appendChild(
      el(
        "p",
        "hint",
        "Error = forecast minus observation, so a negative mean " +
        "bias means average underprediction."
      )
    );

    content.appendChild(
      el("h3", "", "Observed and archived forecast irradiance")
    );

    content.appendChild(
      chartBox(
        "verificationSeriesChart",
        "Observed and archived forecast irradiance at Bondville"
      )
    );

    content.appendChild(
      el(
        "p",
        "hint",
        "Only hours passing the observation quality rule are plotted. " +
        "A line break marks an excluded hour."
      )
    );

    content.appendChild(el("h3", "", "Errors by forecast hour"));

    content.appendChild(
      chartBox(
        "verificationLeadChart",
        "Forecast error by hours after initialization"
      )
    );

    content.appendChild(
      el(
        "p",
        "hint",
        "Forecasts start daily at 00:00 UTC, so each forecast hour " +
        "also corresponds to a fixed time of day. These errors mix " +
        "time-of-day effects with forecast lead, and nighttime hours " +
        "are near zero. They do not isolate how skill changes with lead time."
      )
    );

    const experiments = el("details");
    experiments.appendChild(
      el("summary", "", "Correction experiments (did not consistently help)")
    );

    const headings = [
      "Partition",
      "Candidate",
      "Subset",
      "Hours",
      "MAE (W/m²)",
      "RMSE (W/m²)",
      "Mean bias (W/m²)",
    ];

    const validation = doc.correction_experiments.validation ?? [];
    const test = doc.correction_experiments.exploratory_test ?? [];

    if (validation.length > 0) {
      experiments.appendChild(
        buildTable(
          "Validation results",
          headings,
          experimentRows(validation)
        )
      );
    }

    if (test.length > 0) {
      experiments.appendChild(
        buildTable(
          "Exploratory test results",
          headings,
          experimentRows(test)
        )
      );
    }

    experiments.appendChild(
      el("p", "hint", doc.correction_experiments.note ?? "")
    );

    content.appendChild(experiments);

    const methods = el("details");
    methods.appendChild(el("summary", "", "Data and methods"));

    const list = el("ul", "details");

    for (const text of [
      `Forecast: ${doc.forecast.provider}, model ${doc.forecast.model}.`,
      `Initializations: ${doc.forecast.initialization}; ${doc.forecast.leads}.`,
      `Observations: ${doc.observations.source}; ${doc.observations.quantity}.`,
      doc.observations.eligibility,
      doc.forecast.provenance_note,
    ]) {
      list.appendChild(el("li", "", text));
    }

    methods.appendChild(list);
    content.appendChild(methods);

    if (typeof Chart === "undefined") {
      content.appendChild(
        el("p", "hint", "Chart library unavailable; tables are still shown.")
      );
      return;
    }

    drawTimeSeries(doc);
    drawLeadChart(doc);
  }

  async function load() {
    const about = document.getElementById("about-title");

    if (!about) return;

    const section = el("section", "panel");
    section.setAttribute("aria-labelledby", "verification-title");

    const heading = el(
      "h2",
      "",
      "Bondville verification: one station, January 2025"
    );
    heading.id = "verification-title";

    const status = el("p", "hint", "Loading verification data…");
    status.setAttribute("role", "status");

    const content = el("div");

    section.append(heading, status, content);
    about.closest("section").before(section);
    status.textContent = "Connecting to the forecast server…";

    if (window.solarEnsureBackend) {
      await window.solarEnsureBackend();
    }

    status.textContent = "Loading verification data…";

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 20000);

    try {
      const response = await fetch(VERIFICATION_URL, {
        signal: controller.signal,
      });

      if (!response.ok) {
        throw new Error(`Backend error: HTTP ${response.status}`);
      }

      const doc = await response.json();

      if (!validDocument(doc)) {
        throw new Error("The verification file has an unexpected format.");
      }

      status.textContent =
        `${doc.series.length} eligible hourly comparisons loaded.`;

      render(doc, content);
    } catch (error) {
      console.error("Verification data failed:", error);

      status.textContent =
        error.name === "AbortError"
          ? "Verification data timed out."
          : `Verification data unavailable: ${error.message}`;
    } finally {
      clearTimeout(timeoutId);
    }
  }

  load();
})();