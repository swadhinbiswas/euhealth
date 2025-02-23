/* EU Healthcare Workforce dashboard — data binding and chart rendering.
   No framework and no CDN: this must work offline and from a file:// URL. */
(function () {
  "use strict";

  var D = window.__DATA__;
  var REFERENCE_PHYSICIANS = 3.3;   // OECD doctors per 1,000
  var REFERENCE_NURSES = 9.0;

  // ---- formatting ---------------------------------------------------------
  function fmtInt(n) {
    if (n === null || n === undefined || isNaN(n)) return "—";
    return Math.round(n).toLocaleString("en-GB");
  }
  function fmtNum(n, d) {
    if (n === null || n === undefined || isNaN(n)) return "—";
    return Number(n).toLocaleString("en-GB", {
      minimumFractionDigits: d === undefined ? 1 : d,
      maximumFractionDigits: d === undefined ? 1 : d
    });
  }
  function fmtPct(n, d) {
    if (n === null || n === undefined || isNaN(n)) return "—";
    return fmtNum(n, d === undefined ? 1 : d) + "%";
  }
  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }
  function esc(s) {
    return String(s === null || s === undefined ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

  /* Text content is set via textContent everywhere above, which is what keeps
     this injection-safe. This helper exists for the one place markup is
     assembled as a string. */
  function table(columns, rows, opts) {
    opts = opts || {};
    var wrap = el("div", "panel" + (opts.scroll ? " scroll" : ""));
    if (opts.caption) {
      wrap.appendChild(el("div", "panel-caption", opts.caption));
    }
    {
      var t = el("table");
      var thead = el("thead"), tr = el("tr");
      columns.forEach(function (c) {
        tr.appendChild(el("th", c.num ? "num" : null, c.label));
      });
      thead.appendChild(tr);
      t.appendChild(thead);
      var tbody = el("tbody");
      rows.forEach(function (row) {
        var r = el("tr");
        columns.forEach(function (c) {
          var value = c.get ? c.get(row) : row[c.key];
          if (value instanceof Node) {
            var td = el("td", c.num ? "num" : null);
            td.appendChild(value);
            r.appendChild(td);
          } else {
            r.appendChild(el("td", c.num ? "num" : null, value));
          }
        });
        tbody.appendChild(r);
      });
      t.appendChild(tbody);
      wrap.appendChild(t);
    }
    return wrap;
  }

  /* Horizontal bars built from divs. A missing value renders an explicit
     "not reported" row rather than a zero-length bar, which would read as
     a real measurement of zero. */
  function barList(rows, opts) {
    opts = opts || {};
    var wrap = el("div");
    var max = 0;
    rows.forEach(function (r) {
      var v = opts.get(r);
      if (v !== null && v !== undefined && v > max) max = v;
    });
    rows.forEach(function (r) {
      var v = opts.get(r);
      var row = el("div", "bar-row" + (v === null || v === undefined ? " missing" : ""));
      row.appendChild(el("div", "name", opts.name(r)));
      var track = el("div", "bar-track");
      if (v !== null && v !== undefined && max > 0) {
        var fill = el("div", "bar-fill" + (opts.cls ? " " + opts.cls(r) : ""));
        fill.style.width = Math.max(1, (v / max) * 100) + "%";
        track.appendChild(fill);
      }
      if (opts.marker !== undefined && opts.marker !== null && max > 0) {
        var mark = el("div", "marker");
        mark.style.left = Math.min(99.5, (opts.marker / max) * 100) + "%";
        mark.title = opts.markerLabel || "reference";
        track.appendChild(mark);
      }
      row.appendChild(track);
      row.appendChild(el("div", "val",
        (v === null || v === undefined) ? "not reported" : opts.fmt(v)));
      wrap.appendChild(row);
    });
    return wrap;
  }

  /* Inline SVG line chart. SVG rather than canvas so it stays crisp in
     screenshots and prints, and needs no external library. */
  function lineChart(series, opts) {
    opts = opts || {};
    var W = 640, H = opts.height || 220;
    var m = { t: 12, r: 14, b: 26, l: 44 };
    var iw = W - m.l - m.r, ih = H - m.t - m.b;
    var all = [];
    series.forEach(function (s) {
      s.points.forEach(function (p) {
        if (p.y !== null && p.y !== undefined) all.push(p.y);
      });
    });
    if (!all.length) return el("div", null, "no data");
    var min = opts.min !== undefined ? opts.min : Math.min.apply(null, all);
    var max = opts.max !== undefined ? opts.max : Math.max.apply(null, all);
    if (max === min) max = min + 1;
    var years = series[0].points.map(function (p) { return p.x; });
    var xmin = Math.min.apply(null, years), xmax = Math.max.apply(null, years);

    function sx(x) {
      return m.l + (xmax === xmin ? iw / 2 : ((x - xmin) / (xmax - xmin)) * iw);
    }
    function sy(y) { return m.t + ih - ((y - min) / (max - min)) * ih; }

    var ns = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 " + W + " " + H);
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", opts.ariaLabel || "trend chart");

    // horizontal gridlines + y labels
    for (var g = 0; g <= 4; g++) {
      var yv = min + ((max - min) * g) / 4;
      var yy = sy(yv);
      var line = document.createElementNS(ns, "line");
      line.setAttribute("x1", m.l); line.setAttribute("x2", W - m.r);
      line.setAttribute("y1", yy); line.setAttribute("y2", yy);
      line.setAttribute("class", "grid-line");
      svg.appendChild(line);
      var lab = document.createElementNS(ns, "text");
      lab.setAttribute("x", m.l - 8); lab.setAttribute("y", yy + 3.5);
      lab.setAttribute("class", "axis-label");
      lab.setAttribute("text-anchor", "end");
      lab.textContent = opts.fmtY ? opts.fmtY(yv) : String(Math.round(yv));
      svg.appendChild(lab);
    }
    // x labels
    var every = Math.max(1, Math.ceil(years.length / 8));
    years.forEach(function (yr, i) {
      if (i % every && i !== years.length - 1) return;
      var t = document.createElementNS(ns, "text");
      t.setAttribute("x", sx(yr)); t.setAttribute("y", H - 8);
      t.setAttribute("class", "axis-label");
      t.setAttribute("text-anchor", "middle");
      t.textContent = yr;
      svg.appendChild(t);
    });
    // axis
    var axis = document.createElementNS(ns, "line");
    axis.setAttribute("x1", m.l); axis.setAttribute("x2", W - m.r);
    axis.setAttribute("y1", m.t + ih); axis.setAttribute("y2", m.t + ih);
    axis.setAttribute("class", "axis-line");
    svg.appendChild(axis);

    series.forEach(function (s) {
      var d = "";
      var started = false;
      s.points.forEach(function (p) {
        if (p.y === null || p.y === undefined) { started = false; return; }
        d += (started ? "L" : "M") + sx(p.x) + " " + sy(p.y) + " ";
        started = true;
      });
      var path = document.createElementNS(ns, "path");
      path.setAttribute("d", d);
      path.setAttribute("class", "series-line");
      path.setAttribute("stroke", s.color);
      svg.appendChild(path);
      s.points.forEach(function (p) {
        if (p.y === null || p.y === undefined) return;
        var c = document.createElementNS(ns, "circle");
        c.setAttribute("cx", sx(p.x)); c.setAttribute("cy", sy(p.y));
        c.setAttribute("r", 3);
        c.setAttribute("class", "series-dot");
        c.setAttribute("fill", s.color);
        var title = document.createElementNS(ns, "title");
        title.textContent = p.x + ": " + (opts.fmtY ? opts.fmtY(p.y) : p.y);
        c.appendChild(title);
        svg.appendChild(c);
      });
    });
    return svg;
  }

  function tierBadge(tier) {
    var span = el("span", "tier tier-" + (tier || "Unknown"), tier || "Unknown");
    return span;
  }

  // ---- page 1: executive overview -----------------------------------------
  function renderKpis(host) {
    var k = D.kpis, y = D.reference_year;
    var gap = k.workforce_gap;
    var gapPct = k.required_workers ? (gap / k.required_workers) * 100 : null;
    var actual = k.required_workers - gap;

    /* The aggregate workforce gap is a surplus: summed across 27 countries, the
       actual workforce exceeds the requirement implied by the OECD reference
       ratios. That is arithmetically correct but analytically misleading,
       because the reference ratio is a target average and the countries
       below it are the ones that matter. The KPI therefore shows the count of
       under-supplied countries, and the sign is stated rather than implied. */
    var underSupplied = D.coverage.filter(function (r) {
      return r.physicians_per_1000 !== null
        && r.physicians_per_1000 < REFERENCE_PHYSICIANS;
    }).length;

    var tiles = [
      { label: "Active doctors", value: fmtInt(k.doctors), unit: "",
        foot: y + " · reporting national totals", tone: "" },
      { label: "Active nurses", value: fmtInt(k.nurses), unit: "",
        foot: "six member states publish no nurse data", tone: "warn" },
      { label: "Countries below the reference", value: String(underSupplied),
        unit: "of " + D.coverage.length,
        foot: underSupplied > 0
          ? "fewer than 3.3 physicians per 1,000 in " + y
          : "every country cleared 3.3 by " + y +
            " (Hungary was last, in 2020)",
        tone: underSupplied > 0 ? "warn" : "good" },
      { label: "Retirement risk", value: fmtPct(k.retirement_risk_pct), unit: "",
        foot: "share of the workforce aged 55+", tone: "" }
    ];

    tiles.forEach(function (t) {
      var card = el("div", "panel kpi");
      card.appendChild(el("div", "label", t.label));
      var v = el("div", "value", t.value);
      if (t.unit) {
        var u = el("span", "unit", " " + t.unit);
        v.appendChild(u);
      }
      card.appendChild(v);
      if (t.foot) card.appendChild(el("div", "foot " + t.tone, t.foot));
      host.appendChild(card);
    });
  }

  /* The coverage section has its own chart container, separate from the
     retirement grid, so this returns the panel rather than appending it. */
  function renderCoverage() {
    var rows = D.coverage.filter(function (r) {
      return r.physicians_per_1000 !== null;
    });
    rows.sort(function (a, b) {
      return a.physicians_per_1000 - b.physicians_per_1000;
    });
    var panel = el("div", "panel");
    panel.appendChild(el("div", "panel-caption",
      "Physicians per 1,000 population · " + D.reference_year +
      " · marker shows the OECD reference of 3.3"));
    panel.appendChild(barList(rows.slice(0, 14), {
      name: function (r) { return r.country_name; },
      get: function (r) { return r.physicians_per_1000; },
      fmt: function (v) { return fmtNum(v, 2); },
      cls: function (r) {
        return r.physicians_per_1000 < REFERENCE_PHYSICIANS ? "pos" : "";
      },
      marker: REFERENCE_PHYSICIANS,
      markerLabel: "OECD reference 3.3"
    }));
    return panel;
  }

  // ---- page 3: retirement --------------------------------------------------
  function renderRetirement(host) {
    var rows = D.retirement.slice(0, 14);
    var panel = el("div", "panel");
    panel.appendChild(el("div", "panel-caption",
      "Physicians aged 55+ and 65+, " +
      (D.retirement_year || D.reference_year) +
      " · bars above 50% are shaded"));
    panel.appendChild(barList(rows, {
      name: function (r) { return r.country_name; },
      get: function (r) { return r.pct_55_plus; },
      fmt: function (v) { return fmtPct(v); },
      cls: function (r) { return r.pct_55_plus > 50 ? "pos" : ""; },
      marker: 50,
      markerLabel: "50% threshold"
    }));
    host.appendChild(panel);

    /* The bar chart and the table show the same two measures; the table adds
       the 65+ split, which the chart cannot show without becoming unclear. */
    host.appendChild(table([
      { label: "Country", key: "country_name" },
      { label: "Aged 55+", num: true, get: function (r) { return fmtPct(r.pct_55_plus); } },
      { label: "Aged 65+", num: true, get: function (r) { return fmtPct(r.pct_65_plus); } }
    ], rows, {
      caption: "Detail · the 55+ band includes those already past retirement age"
    }));
  }

  // ---- page 5: age pyramid -------------------------------------------------
  function renderPyramid(host) {
    var groups = {};
    D.age_pyramid.forEach(function (r) {
      if (!groups[r.age_group_code]) {
        groups[r.age_group_code] = {
          code: r.age_group_code, label: r.age_group_label,
          order: r.sort_order, Male: 0, Female: 0
        };
      }
      groups[r.age_group_code][r.sex_label] = r.workers;
    });
    var bands = Object.keys(groups).map(function (k) { return groups[k]; })
      .sort(function (a, b) { return a.order - b.order; });
    var max = 0;
    bands.forEach(function (b) { max = Math.max(max, b.Male, b.Female); });

    var panel = el("div", "panel");
    panel.appendChild(el("div", "panel-caption",
      (D.pyramid_country || "") +
      " · physicians by age band and sex · " + D.reference_year));
    var body = el("div", "pyramid");

    /* Centre column carries the label, so the two bars grow outward from
       the middle rather than from opposite screen edges. */
    bands.forEach(function (b) {
      var row = el("div", "pyr-row");
      row.appendChild(el("div", "pyr-val", fmtInt(b.Male)));

      var left = el("div", "side left");
      var lb = el("div", "pyr-left");
      lb.style.width = (max ? (b.Male / max) * 100 : 0) + "%";
      left.appendChild(lb);
      row.appendChild(left);

      row.appendChild(el("div", "pyr-label", b.label));

      var right = el("div", "side");
      var rb = el("div", "pyr-right");
      rb.style.width = (max ? (b.Female / max) * 100 : 0) + "%";
      right.appendChild(rb);
      row.appendChild(right);

      row.appendChild(el("div", "pyr-val", fmtInt(b.Female)));
      body.appendChild(row);
    });
    panel.appendChild(body);
    var lg = el("div", "pyr-legend");
    var sw1 = el("span", "swatch"); sw1.style.background = "var(--accent-2)";
    lg.appendChild(sw1); lg.appendChild(el("span", null, "male"));
    var sw2 = el("span", "swatch"); sw2.style.background = "var(--accent)";
    lg.appendChild(sw2); lg.appendChild(el("span", null, "female"));
    panel.appendChild(lg);
    host.appendChild(panel);
  }

  // ---- page 7: medical deserts --------------------------------------------
  function renderRegional(host) {
    var rows = D.regional.slice(0, 16);
    var panel = el("div", "panel");
    panel.appendChild(el("div", "panel-caption",
      "Observed regional coverage, physicians per 1,000 · " +
      "every region shown reconciles to its national total"));
    panel.appendChild(barList(rows, {
      name: function (r) {
        return (r.region_name || r.nuts_code) + " · " + r.nuts_code;
      },
      get: function (r) { return r.workers_per_1000; },
      fmt: function (v) { return fmtNum(v, 2); },
      cls: function (r) {
        return r.access_tier === "Critical" ? "pos" : "";
      }
    }));

    var counts = {};
    D.regional.forEach(function (r) {
      counts[r.access_tier] = (counts[r.access_tier] || 0) + 1;
    });
    var legend = el("div", "legend");
    ["Critical", "High", "Medium", "Low", "Unknown"].forEach(function (t) {
      var wrap = el("span");
      var sw = el("span", "swatch");
      sw.style.background = "var(--risk-" + t.toLowerCase() + ")";
      wrap.appendChild(sw);
      wrap.appendChild(el("span", null, t + " (" + (counts[t] || 0) + ")"));
      legend.appendChild(wrap);
    });
    host.appendChild(panel);
    host.appendChild(legend);
  }

  // ---- page 8: forecasts ---------------------------------------------------
  function renderForecast(host) {
    var rows = D.forecast.filter(function (r) { return r.year === 2030; })
      .slice(0, 12);
    var panel = el("div", "panel");
    panel.appendChild(el("div", "panel-caption",
      "Projected workforce, 2030 · champion model (ARIMA, 4.99% MAPE) " +
      "against a carry-forward baseline"));
    panel.appendChild(barList(rows, {
      name: function (r) {
        return r.country_name + " · " +
          (r.profession_code === "PHYS" ? "physicians" : "nurses");
      },
      get: function (r) { return r.forecast; },
      fmt: function (v) { return fmtInt(v); }
    }));
    host.appendChild(panel);

    var chart = el("div", "panel chart");
    var trend = D.coverage_trend;
    if (trend.length) {
      chart.appendChild(lineChart([
        {
          color: "var(--accent)",
          points: trend.map(function (r) {
            return { x: r.year, y: r.physicians_per_1000 };
          })
        },
        {
          color: "var(--accent-2)",
          points: trend.map(function (r) {
            return { x: r.year, y: r.nurses_per_1000 };
          })
        }
      ], {
        fmtY: function (v) { return fmtNum(v, 1); },
        ariaLabel: "Germany physicians and nurses per 1,000 population by year"
      }));
      var lg = el("div", "legend");
      [["var(--accent)", "physicians per 1,000"],
       ["var(--accent-2)", "nurses per 1,000"]].forEach(function (pair) {
        var s = el("span");
        var sw = el("span", "swatch"); sw.style.background = pair[0];
        s.appendChild(sw);
        s.appendChild(el("span", null, pair[1]));
        lg.appendChild(s);
      });
      chart.appendChild(lg);
    }
    host.appendChild(chart);

    if (D.model_mape && D.model_mape.length) {
      host.appendChild(table([
        { label: "Model", key: "model" },
        { label: "MAPE %", num: true, get: function (r) { return fmtNum(r.mape, 3); } },
        { label: "sMAPE %", num: true, get: function (r) { return fmtNum(r.smape, 3); } },
        { label: "RMSE", num: true, get: function (r) { return fmtInt(r.rmse); } }
      ], D.model_mape, {
        caption: "Model comparison · walk-forward validation · " +
          "four expanding-window folds · identical test set"
      }));
    }
  }

  // ---- data quality --------------------------------------------------------
  function renderQuality(host) {
    var q = D.quality || {};
    var tiles = [
      { label: "Physicians DQ score", value: q.dq_physicians !== null && q.dq_physicians !== undefined ? fmtNum(q.dq_physicians, 1) : "—", foot: "out of 100" },
      { label: "Nurses DQ score", value: q.dq_nurses !== null && q.dq_nurses !== undefined ? fmtNum(q.dq_nurses, 1) : "—", foot: "six countries unreported" },
      { label: "Verified regional rows", value: fmtInt(q.regional_rows), foot: q.regional_countries + " countries" },
      { label: "Best model MAPE", value: q.model_mape && q.model_mape.length ? fmtNum(q.model_mape[0].mape, 2) + "%" : "—", foot: "ARIMA, walk-forward" }
    ];
    tiles.forEach(function (t) {
      var card = el("div", "panel kpi");
      card.appendChild(el("div", "label", t.label));
      card.appendChild(el("div", "value", t.value));
      if (t.foot) card.appendChild(el("div", "foot", t.foot));
      host.appendChild(card);
    });

    if (D.structural_breaks && D.structural_breaks.length) {
      host.appendChild(table([
        { label: "Country", get: function (r) { return r.country_code; } },
        { label: "Profession", get: function (r) {
            return r.profession_code === "PHYS" ? "physicians" : "nurses"; } },
        { label: "Year", num: true, key: "break_year" },
        { label: "Shift", num: true, get: function (r) {
            return fmtNum(r.magnitude_pct, 1) + "%"; } }
      ], D.structural_breaks, {
        caption: "Largest structural breaks · reporting-method changes, " +
          "not workforce movement · excluded from trend claims"
      }));
    }
  }

  // ---- theme ---------------------------------------------------------------
  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    try { localStorage.setItem("eu-health-theme", theme); } catch (e) {}
    var btn = document.getElementById("theme-toggle");
    if (btn) {
      btn.textContent = theme === "dark" ? "Light mode" : "Dark mode";
      btn.setAttribute("aria-label",
        theme === "dark" ? "Switch to light mode" : "Switch to dark mode");
    }
  }
  function initialTheme() {
    var saved = null;
    try { saved = localStorage.getItem("eu-health-theme"); } catch (e) {}
    if (saved) return saved;
    return window.matchMedia &&
           window.matchMedia("(prefers-color-scheme: dark)").matches
           ? "dark" : "light";
  }

  // ---- boot ---------------------------------------------------------------
  function render() {
    var map = {
      "kpis": renderKpis,
      "retirement": renderRetirement,
      "pyramid": renderPyramid,
      "regional": renderRegional,
      "forecast": renderForecast,
      "quality": renderQuality
    };
    Object.keys(map).forEach(function (id) {
      var host = document.getElementById(id);
      if (host) { clear(host); map[id](host); }
    });
    var coverageHost = document.getElementById("coverage-chart");
    if (coverageHost) { clear(coverageHost); coverageHost.appendChild(renderCoverage()); }
  }

  document.addEventListener("DOMContentLoaded", function () {
    applyTheme(initialTheme());
    var btn = document.getElementById("theme-toggle");
    if (btn) {
      btn.addEventListener("click", function () {
        var current = document.documentElement.getAttribute("data-theme");
        applyTheme(current === "dark" ? "light" : "dark");
      });
    }
    render();
  });
})();