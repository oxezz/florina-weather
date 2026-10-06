/* ==========================================================================
   Καιρός · Φλώρινα — client
   Fetches /api/weather and renders the page. Refreshes in the background
   without reloading, pauses while the tab is hidden, and never injects
   upstream text as HTML (Meteoalarm descriptions are third-party data).
   ========================================================================== */
"use strict";

(function () {
  var API = "/api/weather";
  var REFRESH = Math.max(30, Number(document.body.dataset.refresh) || 180);

  var state = { timer: null, inflight: false, lastGenerated: null, error: null, signature: null };

  function $(id) { return document.getElementById(id); }

  /* ---------------------------------------------------------------- utils */

  function num(value, decimals) {
    if (value === null || value === undefined || value === "") return "–";
    var n = Number(value);
    if (!isFinite(n)) return "–";
    return decimals ? n.toFixed(decimals) : String(Math.round(n));
  }

  function setText(node, value) {
    if (!node) return;
    var next = value === null || value === undefined ? "" : String(value);
    if (node.textContent !== next) node.textContent = next;
  }

  function setBig(id, value) {
    var node = $(id);
    if (!node) return;
    var next = value === null || value === undefined ? "–" : String(value);
    if (node.textContent === next) return;
    node.textContent = next;
    node.classList.remove("flash");
    void node.offsetWidth; /* restart the animation */
    node.classList.add("flash");
  }

  function clear(node) {
    while (node && node.firstChild) node.removeChild(node.firstChild);
  }

  function el(tag, className, content) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (content !== undefined && content !== null) node.textContent = String(content);
    return node;
  }

  var SVG = "http://www.w3.org/2000/svg";
  function svg(tag, attrs) {
    var node = document.createElementNS(SVG, tag);
    for (var key in attrs) {
      if (Object.prototype.hasOwnProperty.call(attrs, key)) {
        node.setAttribute(key, attrs[key]);
      }
    }
    return node;
  }

  function clamp(value, low, high) {
    return Math.max(low, Math.min(high, value));
  }

  function minutesOf(hhmm) {
    if (typeof hhmm !== "string" || hhmm.indexOf(":") === -1) return null;
    var parts = hhmm.split(":");
    var h = Number(parts[0]), m = Number(parts[1]);
    if (!isFinite(h) || !isFinite(m)) return null;
    return h * 60 + m;
  }

  function clockOf(date) {
    return String(date.getHours()).padStart(2, "0") + ":" +
           String(date.getMinutes()).padStart(2, "0");
  }

  /* ---------------------------------------------------------------- theme */

  /* Light/dark lives in theme.js on <html>; this only picks the backdrop hue
     from the current conditions, on <body>. */
  function weatherThemeFor(current) {
    var code = Number(current.code);
    if (code >= 95) return "storm";
    if ([71, 73, 75, 77, 85, 86].indexOf(code) !== -1) return "snow";
    if ([51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82].indexOf(code) !== -1) return "rain";
    if (code === 45 || code === 48) return "fog";
    if (code === 0 || code === 1) return current.is_day ? "clear-day" : "clear-night";
    return "cloud";
  }

  function applyWeatherTheme(current) {
    var next = "theme-" + weatherThemeFor(current);
    var classes = document.body.className.split(/\s+/).filter(function (name) {
      return name && name.indexOf("theme-") !== 0;
    });
    classes.push(next);
    document.body.className = classes.join(" ");
  }

  /* Keep the browser chrome (iOS status bar, Android toolbar) in step. */
  function syncThemeColor() {
    var meta = document.querySelector('meta[name="theme-color"]');
    if (!meta) return;
    var value = getComputedStyle(document.body).getPropertyValue("--b1").trim();
    if (value) meta.setAttribute("content", value);
  }

  /* ------------------------------------------------------- appearance UI */

  function initModes() {
    var theme = window.FlorinaTheme;
    if (!theme) return;
    var buttons = document.querySelectorAll(".modes button[data-mode]");
    if (!buttons.length) return;

    function sync() {
      var current = theme.get();
      Array.prototype.forEach.call(buttons, function (button) {
        button.setAttribute("aria-pressed",
          button.dataset.mode === current ? "true" : "false");
      });
    }

    Array.prototype.forEach.call(buttons, function (button) {
      button.addEventListener("click", function () {
        theme.set(button.dataset.mode);
        sync();
        syncThemeColor();
      });
    });

    document.addEventListener("florina:mode", sync);
    sync();
  }

  /* ------------------------------------------------- glass pointer sheen */

  function initGlassHighlight() {
    if (!window.matchMedia || !window.matchMedia("(hover: hover) and (pointer: fine)").matches) return;
    var queued = null;
    var scheduled = false;

    function flush() {
      scheduled = false;
      if (!queued) return;
      var node = queued.node, x = queued.x, y = queued.y;
      queued = null;
      var rect = node.getBoundingClientRect();
      node.style.setProperty("--mx", (x - rect.left) + "px");
      node.style.setProperty("--my", (y - rect.top) + "px");
    }

    document.addEventListener("pointermove", function (event) {
      if (event.pointerType && event.pointerType !== "mouse") return;
      var target = event.target;
      var node = target && target.closest ? target.closest(".glass") : null;
      if (!node) return;
      queued = { node: node, x: event.clientX, y: event.clientY };
      if (scheduled) return;
      scheduled = true;
      requestAnimationFrame(flush);
    }, { passive: true });
  }

  /* --------------------------------------------------------------- alerts */

  function renderAlerts(alerts) {
    var host = $("alerts");
    if (!host) return;
    clear(host);

    if (!alerts || !alerts.length) {
      host.hidden = false;
      var ok = el("div", "alert-all-clear");
      ok.appendChild(el("span", null, "\u2705"));
      ok.appendChild(el("span", null, "Καμία ενεργή προειδοποίηση για την περιοχή."));
      host.appendChild(ok);
      return;
    }

    host.hidden = false;
    alerts.forEach(function (alert) {
      var card = el("div", "alert");
      card.style.setProperty("--level", alert.color || "#facc15");
      card.appendChild(el("div", "alert-icon", alert.icon || "\u26a0\ufe0f"));

      var body = el("div");
      var title = el("div", "alert-title");
      var label = el("strong", null, alert.level_label + " προειδοποίηση · " + alert.hazard);
      title.appendChild(label);
      title.appendChild(el("span", "alert-area", alert.area));
      body.appendChild(title);

      var window_ = [alert.onset_text, alert.expires_text].filter(Boolean).join(" \u2192 ");
      if (window_) body.appendChild(el("div", "alert-window", window_));

      if (alert.description) {
        var details = el("details", "alert-body");
        details.appendChild(el("summary", null, alert.description.length > 110 ? "Λεπτομέρειες" : "Περιγραφή"));
        details.appendChild(el("p", null, alert.description));
        if (alert.description.length <= 110) details.open = true;
        body.appendChild(details);
      }

      card.appendChild(body);
      host.appendChild(card);
    });
  }

  /* ----------------------------------------------------------------- hero */

  function renderHero(data) {
    var cur = data.current || {};
    setText($("sym"), cur.emoji || "\u2601\ufe0f");
    var tempNode = $("temp");
    if (tempNode) {
      var next = num(cur.temp, 1);
      if (tempNode.textContent !== next) {
        tempNode.textContent = next;
        tempNode.classList.remove("flash");
        void tempNode.offsetWidth;
        tempNode.classList.add("flash");
      }
    }
    setText($("code"), cur.text || "");
    setText($("summary"), data.summary || "");

    var dateText = cur.date_text || "";
    var suffix = data.status && data.status.stale ? " · \u26a0 παλαιότερα δεδομένα" : "";
    setText($("stamp"), dateText + " · ενημέρωση " + (cur.time || "–") + suffix);
  }

  /* ---------------------------------------------------------------- stats */

  function renderStats(cur) {
    setBig("s-apparent", num(cur.apparent, 1) + "°");
    setBig("s-humidity", num(cur.humidity) + "%");
    setBig("s-wind", num(cur.wind, 1));
    setText($("s-wind-sub"),
      (cur.wind_dir_text ? cur.wind_dir_text + " " + (cur.wind_arrow || "") + " · " : "") +
      cur.beaufort + " Μπφ (" + cur.beaufort_text + ")");

    setBig("s-gusts", num(cur.gusts, 1));
    setBig("s-rain", num(cur.precip, 1));
    setBig("s-cloud", num(cur.cloud) + "%");
    setBig("s-pressure", num(cur.pressure));
    setBig("s-visibility", cur.visibility === null || cur.visibility === undefined
      ? "–" : num(cur.visibility / 1000, 0) + " km");
    setText($("s-visibility-sub"), cur.visibility_text || "");
  }

  /* ------------------------------------------------------------------ sun */

  function renderSun(data) {
    var today = data.today;
    if (!today) return;
    setText($("sun-rise"), today.sunrise);
    setText($("sun-set"), today.sunset);
    setText($("sun-daylight"), today.daylight);
    setText($("sun-sunshine"), today.sunshine);

    var chip = $("uv-chip");
    if (chip) {
      chip.hidden = false;
      clear(chip);
      chip.appendChild(el("span", "dot")).style.setProperty("--c", today.uv_color || "#94a3b8");
      chip.appendChild(el("span", null, "UV " + num(today.uv_max, 1) + " · " + today.uv_level));
    }

    var now = minutesOf(data.current.time);
    var rise = minutesOf(today.sunrise);
    var set = minutesOf(today.sunset);
    var fill = $("sun-fill");
    if (fill) {
      var pct = 0;
      if (now !== null && rise !== null && set !== null && set > rise) {
        pct = clamp((now - rise) / (set - rise), 0, 1) * 100;
      } else if (now !== null && set !== null && now > set) {
        pct = 100;
      }
      fill.style.width = pct.toFixed(1) + "%";
    }
  }

  /* ---------------------------------------------------------------- chart */

  function renderChart(hours) {
    var node = $("chart");
    if (!node || !hours || hours.length < 2) return;
    clear(node);
    node.setAttribute("aria-label",
      "Θερμοκρασία και πιθανότητα βροχής για τις επόμενες " + hours.length + " ώρες");

    var W = 960, H = 250;
    var padL = 42, padR = 16, padT = 26;
    var axisY = 172, precipTop = 182, precipBottom = 210, labelY = 228;
    var plotW = W - padL - padR;

    var temps = hours.map(function (h) { return Number(h.temp); })
      .filter(function (v) { return isFinite(v); });
    if (!temps.length) return;
    var lo = Math.min.apply(null, temps);
    var hi = Math.max.apply(null, temps);
    if (hi - lo < 1) { hi += 0.5; lo -= 0.5; }
    var span = hi - lo;
    var step = plotW / (hours.length - 1);

    function x(i) { return padL + i * step; }
    function y(v) { return axisY - ((v - lo) / span) * (axisY - padT); }

    /* gradient for the area under the line */
    var defs = svg("defs");
    var grad = svg("linearGradient", { id: "tempFill", x1: "0", y1: "0", x2: "0", y2: "1" });
    grad.appendChild(svg("stop", { offset: "0%", "stop-color": "#ffd166", "stop-opacity": "0.42" }));
    grad.appendChild(svg("stop", { offset: "100%", "stop-color": "#ffd166", "stop-opacity": "0.02" }));
    defs.appendChild(grad);
    node.appendChild(defs);

    /* night bands */
    var i = 0;
    while (i < hours.length) {
      if (hours[i].is_day) { i++; continue; }
      var j = i;
      while (j + 1 < hours.length && !hours[j + 1].is_day) j++;
      var x0 = clamp(x(i) - step / 2, padL, W - padR);
      var x1 = clamp(x(j) + step / 2, padL, W - padR);
      if (x1 > x0) {
        node.appendChild(svg("rect", {
          class: "night-band", x: x0, y: padT - 6,
          width: x1 - x0, height: axisY - padT + 6, rx: 3
        }));
      }
      i = j + 1;
    }

    /* horizontal grid */
    [hi, (hi + lo) / 2, lo].forEach(function (value) {
      node.appendChild(svg("line", {
        class: "axis-line", x1: padL - 6, y1: y(value), x2: W - padR, y2: y(value)
      }));
      var label = svg("text", { x: padL - 12, y: y(value) + 4, "text-anchor": "end" });
      label.textContent = Math.round(value) + "°";
      node.appendChild(label);
    });

    /* precipitation bars */
    var barW = Math.max(2, step * 0.44);
    hours.forEach(function (hour, index) {
      var prob = Number(hour.precip_prob);
      if (!isFinite(prob) || prob <= 0) return;
      var height = (clamp(prob, 0, 100) / 100) * (precipBottom - precipTop);
      node.appendChild(svg("rect", {
        class: "precip-bar", x: x(index) - barW / 2, y: precipBottom - height,
        width: barW, height: height, rx: Math.min(2, barW / 2), opacity: 0.35 + prob / 250
      }));
    });

    /* temperature line */
    var points = hours.map(function (hour, index) { return [x(index), y(Number(hour.temp))]; });
    var line = smoothPath(points);
    node.appendChild(svg("path", {
      class: "temp-area",
      d: line + " L " + points[points.length - 1][0] + " " + axisY +
         " L " + points[0][0] + " " + axisY + " Z"
    }));
    node.appendChild(svg("path", { class: "temp-line", d: line }));

    /* highlight the extremes */
    var maxIndex = 0, minIndex = 0;
    hours.forEach(function (hour, index) {
      if (Number(hour.temp) > Number(hours[maxIndex].temp)) maxIndex = index;
      if (Number(hour.temp) < Number(hours[minIndex].temp)) minIndex = index;
    });
    [[maxIndex, -12], [minIndex, 18]].forEach(function (pair) {
      var index = pair[0];
      if (!isFinite(Number(hours[index].temp))) return;
      node.appendChild(svg("circle", {
        class: "temp-dot", cx: x(index), cy: y(Number(hours[index].temp)), r: 3.4
      }));
      var label = svg("text", {
        class: "temp-label", x: clamp(x(index), padL + 12, W - padR - 12),
        y: y(Number(hours[index].temp)) + pair[1], "text-anchor": "middle"
      });
      label.textContent = num(hours[index].temp, 1) + "°";
      node.appendChild(label);
    });

    /* morning/day separators + time labels */
    hours.forEach(function (hour, index) {
      if (index % 6 !== 0) return;
      var label = svg("text", { x: x(index), y: labelY, "text-anchor": "middle" });
      label.textContent = hour.time;
      node.appendChild(label);
      node.appendChild(svg("line", {
        class: "axis-line", x1: x(index), y1: axisY, x2: x(index), y2: precipBottom
      }));
    });

    /* "now" marker */
    node.appendChild(svg("line", {
      class: "now-line", x1: x(0), y1: padT - 8, x2: x(0), y2: precipBottom
    }));
    var nowLabel = svg("text", { x: x(0) + 4, y: labelY + 16, "text-anchor": "start" });
    nowLabel.textContent = "τώρα";
    node.appendChild(nowLabel);
  }

  function smoothPath(points) {
    if (!points.length) return "";
    if (points.length < 3) {
      return points.map(function (p, i) { return (i ? "L " : "M ") + p[0] + " " + p[1]; }).join(" ");
    }
    var d = "M " + points[0][0] + " " + points[0][1];
    var t = 0.17;
    for (var i = 0; i < points.length - 1; i++) {
      var p0 = points[i - 1] || points[i];
      var p1 = points[i];
      var p2 = points[i + 1];
      var p3 = points[i + 2] || p2;
      var c1x = p1[0] + (p2[0] - p0[0]) * t;
      var c1y = p1[1] + (p2[1] - p0[1]) * t;
      var c2x = p2[0] - (p3[0] - p1[0]) * t;
      var c2y = p2[1] - (p3[1] - p1[1]) * t;
      d += " C " + c1x + " " + c1y + " " + c2x + " " + c2y + " " + p2[0] + " " + p2[1];
    }
    return d;
  }

  /* ---------------------------------------------------------- hourly strip */

  function renderHourly(hours) {
    var host = $("hourly");
    if (!host) return;
    clear(host);
    if (!hours || !hours.length) return;
    var previousDay = null;

    hours.forEach(function (hour, index) {
      if (hour.day && hour.day !== previousDay) {
        previousDay = hour.day;
        if (index > 0) {
          var separator = el("div", "hour-sep");
          separator.appendChild(el("span", "day-sep",
            hour.day.slice(8) + "/" + hour.day.slice(5, 7)));
          host.appendChild(separator);
        }
      }

      var card = el("div", "h" + (index === 0 ? " now" : ""));
      card.title = hour.text + " · αίσθητη " + num(hour.apparent, 1) + "° · υγρασία " +
                   num(hour.humidity) + "% · άνεμος " + num(hour.wind, 1) + " km/h" +
                   (hour.visibility_text ? " · ορατότητα: " + hour.visibility_text : "");
      card.appendChild(el("span", "t", index === 0 ? "τώρα" : hour.time));
      card.appendChild(el("span", "i", hour.emoji));
      card.appendChild(el("span", "d", num(hour.temp, 1) + "°"));
      card.appendChild(el("span", "r", num(hour.precip_prob) + "%"));
      card.appendChild(el("span", "w", num(hour.wind, 1) + " km/h"));
      host.appendChild(card);
    });

    if (state.syncHourlyFade) state.syncHourlyFade();
  }

  /* The strip scrolls horizontally; fade whichever edge has more to show so
     it does not just look clipped. */
  function initHourlyFade() {
    var strip = $("hourly");
    if (!strip) return;

    function update() {
      var max = strip.scrollWidth - strip.clientWidth;
      strip.classList.toggle("can-left", strip.scrollLeft > 4);
      strip.classList.toggle("can-right", max > 4 && strip.scrollLeft < max - 4);
    }

    strip.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update);
    state.syncHourlyFade = update;
  }

  /* ---------------------------------------------------------------- daily */

  function renderDays(days) {
    var host = $("days");
    if (!host) return;
    clear(host);
    if (!days || !days.length) return;

    var lo = Math.min.apply(null, days.map(function (d) { return Number(d.min); }));
    var hi = Math.max.apply(null, days.map(function (d) { return Number(d.max); }));
    var span = hi - lo || 1;

    days.forEach(function (day, index) {
      var card = el("div", "day" + (index === 0 ? " today" : ""));
      card.appendChild(el("span", "dn", day.label));
      card.appendChild(el("span", "dd", day.day + " " + day.month));
      card.appendChild(el("span", "i", day.emoji));
      card.appendChild(el("span", "c", day.text));

      var range = el("div", "range");
      var fill = el("div", "range-fill");
      fill.style.left = clamp((Number(day.min) - lo) / span, 0, 1) * 100 + "%";
      fill.style.width = clamp((Number(day.max) - Number(day.min)) / span, 0, 1) * 100 + "%";
      range.appendChild(fill);
      card.appendChild(range);

      var temp = el("span", "dt", num(day.max, 0) + "°");
      temp.appendChild(el("span", "lo", " / " + num(day.min, 0) + "°"));
      card.appendChild(temp);

      card.appendChild(el("span", "dr",
        day.precip_sum > 0.05
          ? num(day.precip_sum, 1) + " mm · " + num(day.precip_prob) + "%"
          : "\u2013 "));
      card.appendChild(el("span", "up",
        "UV " + num(day.uv_max, 1) + " · ριπές ανέμου " + num(day.gust_max) + " km/h"));
      host.appendChild(card);
    });
  }

  /* ------------------------------------------------------------------ air */

  function renderAir(air) {
    var panel = $("air-panel");
    if (!panel) return;
    if (!air) { panel.hidden = true; return; }
    panel.hidden = false;

    setBig("air-aqi", num(air.aqi));
    setText($("air-aqi-label"), air.label);
    setBig("air-pm", num(air.pm2_5, 1) + " / " + num(air.pm10, 1));

    var chip = $("air-chip");
    if (chip) {
      chip.hidden = false;
      clear(chip);
      chip.appendChild(el("span", "dot")).style.setProperty("--c", air.color || "#94a3b8");
      chip.appendChild(el("span", null, air.label));
    }

    var host = $("air-pollens");
    clear(host);
    if (!air.pollens || !air.pollens.length) {
      host.appendChild(el("span", "sub", "Καμία αξιόλογη συγκέντρωση."));
      return;
    }
    air.pollens.forEach(function (pollen) {
      var tag = el("span", "pollen", pollen.name + " ");
      tag.appendChild(el("strong", null, num(pollen.value, 1)));
      host.appendChild(tag);
    });
  }

  /* --------------------------------------------------------------- status */

  function renderStatus(data) {
    var status = data.status || {};
    var node = $("foot-status");
    if (!node) return;
    var parts = [];
    parts.push("Έκδοση " + (document.body.dataset.version || "?"));
    if (status.age !== null && status.age !== undefined) {
      parts.push("δεδομένα πριν " + Math.round(status.age) + " δευτ.");
    }
    if (status.degraded && status.degraded.length) {
      parts.push("\u26a0 μη διαθέσιμα: " + status.degraded.join(", "));
    }
    if (state.error) parts.push("\u26a0 " + state.error);
    setText(node, parts.join(" · "));
    node.classList.toggle("warn", Boolean(state.error) || Boolean(status.degraded && status.degraded.length));
  }

  /* ---------------------------------------------------------------- render */

  /* One broken section must not blank the rest of the page, and it must not
     fail silently either — the failure is logged and surfaced in the footer. */
  function safely(name, work) {
    try {
      work();
    } catch (error) {
      if (window.console && window.console.error) {
        window.console.error("[florina] " + name + " failed:", error);
      }
      state.error = name + ": " + (error && error.message ? error.message : error);
    }
  }

  function render(data) {
    state.lastGenerated = data.generated_at;
    safely("theme", function () { applyWeatherTheme(data.current || {}); });
    safely("alerts", function () { renderAlerts(data.alerts); });
    safely("hero", function () { renderHero(data); });
    safely("stats", function () { renderStats(data.current || {}); });
    safely("sun", function () { renderSun(data); });

    /* Rebuilding the chart, 48 hour tiles and 7 day cards is the expensive
       part of a refresh. On a 180s timer most refreshes come back with byte
       -identical data, so skip the whole rebuild when nothing changed. */
    var heavy = JSON.stringify(data.hourly) + "|" + JSON.stringify(data.daily);
    if (heavy !== state.signature) {
      state.signature = heavy;
      safely("chart", function () { renderChart(data.hourly || []); });
      safely("hourly", function () { renderHourly(data.hourly || []); });
      safely("daily", function () { renderDays(data.daily || []); });
    }

    safely("air", function () { renderAir(data.air); });
    safely("status", function () { renderStatus(data); });
    safely("title", function () { document.title = "Καιρός · " + data.place; });
    syncThemeColor();
  }

  /* ----------------------------------------------------------------- fetch */

  function setBusy(busy) {
    var button = $("refresh");
    if (button) button.setAttribute("aria-busy", busy ? "true" : "false");
  }

  function load(force) {
    if (state.inflight) return Promise.resolve();
    state.inflight = true;
    setBusy(true);
    return fetch(API + (force ? "?force" : ""), {
      headers: { Accept: "application/json" },
      cache: "no-store"
    }).then(function (response) {
      if (!response.ok) throw new Error("HTTP " + response.status);
      return response.json();
    }).then(function (data) {
      state.error = null;
      render(data);
    }).catch(function (error) {
      if (window.console && window.console.error) {
        window.console.error("[florina] load failed:", error);
      }
      state.error = error.message || String(error);
      if (state.lastGenerated === null) {
        setText($("code"), "Δεν ήταν δυνατή η φόρτωση δεδομένων.");
        setText($("stamp"), state.error);
      } else {
        renderStatus({ status: {} });
      }
    }).then(function () {
      state.inflight = false;
      setBusy(false);
      schedule();
    });
  }

  function schedule() {
    window.clearTimeout(state.timer);
    if (document.hidden) return;
    state.timer = window.setTimeout(function () { load(false); }, REFRESH * 1000);
  }

  /* ------------------------------------------------------------------ boot */

  initModes();
  initGlassHighlight();
  initHourlyFade();
  syncThemeColor();

  var button = $("refresh");
  if (button) {
    button.addEventListener("click", function () { load(true); });
  }

  document.addEventListener("visibilitychange", function () {
    if (document.hidden) {
      window.clearTimeout(state.timer);
    } else {
      load(false);
    }
  });

  window.addEventListener("online", function () { load(true); });

  load(false);
})();
