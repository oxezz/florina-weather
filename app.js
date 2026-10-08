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

  var state = {
    timer: null,
    inflight: false,
    lastGenerated: null,
    error: null,
    signature: null,
    skeletonsDropped: false,
    /* The last weather theme applied, so a change can be told from the first
       application - only a change should crossfade. */
    weatherTheme: null
  };

  /* Asked live rather than cached: a reader can change the setting without
     reloading, and the transition should respect that immediately. */
  function prefersReducedMotion() {
    return window.matchMedia
      && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

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

  /* Restarting a CSS animation needs the browser to notice the class leave and
     come back, and the usual way to force that is to read offsetWidth - which
     forces a synchronous layout. Doing it once per value meant eighteen
     reflows in a single render: the browser's own metrics put 352 ms in this
     function at 6x CPU throttling, and the profile showed almost no script in
     the long task at all, because the cost was the layout it triggered.
     Collect the nodes and pay for one reflow instead of eighteen. */
  var flashQueue = [];

  function flash(node) {
    flashQueue.push(node);
  }

  function flushFlash() {
    var nodes = flashQueue;
    flashQueue = [];
    if (!nodes.length) return;
    var i;
    for (i = 0; i < nodes.length; i++) nodes[i].classList.remove("flash");
    /* Two frames rather than a forced reflow. The browser has to notice the
       class gone before it can see it back, and letting it notice on its own
       schedule costs nothing and blocks nothing - where reading offsetWidth
       made the whole page wait for a layout. */
    requestAnimationFrame(function () {
      for (var j = 0; j < nodes.length; j++) nodes[j].classList.add("flash");
    });
  }

  function setBig(id, value) {
    var node = $(id);
    if (!node) return;
    var next = value === null || value === undefined ? "–" : String(value);
    if (node.textContent === next) return;
    node.textContent = next;
    flash(node);
  }

  /* The hero's temperature is the one value big enough to flash on its own,
     and it used to carry its own copy of the reflow trick. */
  function setFlashed(id, value) {
    var node = $(id);
    if (!node) return;
    var next = String(value);
    if (node.textContent === next) return;
    node.textContent = next;
    flash(node);
  }

  function clear(node) {
    while (node && node.firstChild) node.removeChild(node.firstChild);
  }

  /* Move a fragment's children into the document.

     They are built off-document first, so the browser has one tree to insert
     rather than forty-eight separate appends each wanting its own layout.

     Slicing this across frames was tried and does not work: with nothing
     forcing layout between slices, the browser simply batches every append
     into the one layout it was going to do at paint time anyway. Measured at
     6x CPU throttling, eight slices changed the long task by 541 -> 490 ms,
     which is noise. What does work is content-visibility on the cards below
     the fold, so most of that layout never happens until it is scrolled to. */
  function appendAll(host, fragment) {
    host.appendChild(fragment);
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
     from the current conditions. It goes on <html> too, so the root element
     can colour the canvas correctly from the very first frame. */
  function weatherThemeFor(current) {
    var code = Number(current.code);
    if (code >= 95) return "storm";
    if ([71, 73, 75, 77, 85, 86].indexOf(code) !== -1) return "snow";
    if ([51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82].indexOf(code) !== -1) return "rain";
    if (code === 45 || code === 48) return "fog";
    if (code === 0 || code === 1) return current.is_day ? "clear-day" : "clear-night";
    return "cloud";
  }

  /* ------------------------------------------------------------- the sky */

  /* A glow that follows the sun, so the backdrop reads as a time of day
     rather than only a weather condition.

     The list this came from said the app "already computes solar position".
     It does not: there is a daily solar *energy* total and a sunrise and
     sunset, and no azimuth or elevation anywhere. So the arc is approximated -
     the sun's height is taken as a sine across the daylight window, which is
     what a gradient needs and is not worth real ephemeris maths for.

     The clock used is current.time from the payload, which is already Florina
     local time. Reading the browser clock would put the sky in the wrong place
     for anyone looking from another country, and would be a silent bug. */
  function minutesOf(stamp) {
    var parts = String(stamp || "").split(":");
    if (parts.length !== 2) return null;
    var hours = parseInt(parts[0], 10);
    var minutes = parseInt(parts[1], 10);
    if (isNaN(hours) || isNaN(minutes)) return null;
    return hours * 60 + minutes;
  }

  function applySunSky(data) {
    var root = document.documentElement;
    var today = data.today || {};
    var now = minutesOf((data.current || {}).time);
    var rise = minutesOf(today.sunrise);
    var set = minutesOf(today.sunset);

    /* No sun, or nothing to go on: leave the defaults, which draw no glow. */
    if (now === null || rise === null || set === null || set <= rise ||
        now < rise || now > set) {
      root.style.setProperty("--sun-warmth", "0");
      return;
    }

    var arc = (now - rise) / (set - rise);
    var height = Math.sin(Math.PI * arc);          // 0 at the horizon, 1 at noon

    root.style.setProperty("--sun-x", (12 + arc * 76).toFixed(1) + "%");
    root.style.setProperty("--sun-y", (34 - height * 30).toFixed(1) + "%");
    /* Warmest at the edges of the day, neutral overhead. */
    root.style.setProperty("--sun-warmth", ((1 - height) * 0.55).toFixed(3));
  }

  function applyWeatherTheme(current) {
    var root = document.documentElement;
    var next = "theme-" + weatherThemeFor(current);

    /* The backdrop gradient is built from CSS variables, and gradients do not
       interpolate - so the sky used to snap from clear to rain. A view
       transition can crossfade it, scoped to the backdrop by name so the
       numbers underneath do not fade too (see the view-transition rules).

       Not on the first application: that would crossfade the page in on every
       load, which is not a weather change. Unsupported browsers and
       reduced-motion just get the class, which is what they had before. */
    var first = !state.weatherTheme;
    var changed = state.weatherTheme && state.weatherTheme !== next;
    state.weatherTheme = next;

    function swap() {
      var classes = root.className.split(/\s+/).filter(function (name) {
        return name && name.indexOf("theme-") !== 0;
      });
      classes.push(next);
      root.className = classes.join(" ");
    }

    if (!first && changed && document.startViewTransition && !prefersReducedMotion()) {
      document.startViewTransition(swap);
    } else {
      swap();
    }
  }

  /* Keep the browser chrome (iOS status bar, Android toolbar) in step. */
  function syncThemeColor() {
    var meta = document.querySelector('meta[name="theme-color"]');
    if (!meta) return;
    var value = getComputedStyle(document.documentElement)
      .getPropertyValue("--b1").trim();
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

  /* ---------------------------------------------------------------- extras */

  function renderNormal(normal) {
    var node = $("normal");
    if (!node) return;
    if (!normal || !normal.text) { node.hidden = true; return; }
    node.textContent = normal.text + " (μέσος όρος " + normal.years +
      " ετών: " + num(normal.value, 1) + "°)";
    node.hidden = false;
  }


  function renderStation(station) {
    var panel = $("station-panel");
    if (!panel) return;
    // The payload is {primary, all}: the card shows one station and the strip
    // at the bottom shows the rest. Reading it as flat left every field
    // undefined, which is what put "-° τώρα" and "στις undefined" on screen.
    var reading = station && station.primary;
    if (!reading) { panel.hidden = true; return; }
    panel.hidden = false;

    setText($("station-name"), reading.name);
    setText($("station-text"),
      num(reading.temp, 1) + "° τώρα" +
      (reading.day_min !== null && reading.day_min !== undefined &&
       reading.day_max !== null && reading.day_max !== undefined
        ? ", " + num(reading.day_min, 1) + "° έως " + num(reading.day_max, 1) +
          "° σήμερα"
        : ""));

    // The observation time is the point: this is measured, not modelled, and
    // it lags, so saying when keeps it honest rather than passing it off as
    // the current conditions.
    var parts = ["στις " + reading.observed + ", πριν " + reading.age_text];
    if (reading.humidity !== null && reading.humidity !== undefined) {
      parts.push("υγρασία " + num(reading.humidity) + "%");
    }
    if (reading.wind !== null && reading.wind !== undefined) {
      parts.push("άνεμος " + num(reading.wind, 1) + " km/h" +
                 (reading.wind_dir_text ? " " + reading.wind_dir_text : ""));
    }
    if (reading.rain_today !== null && reading.rain_today !== undefined) {
      parts.push("βροχή " + num(reading.rain_today, 1) + " mm");
    }
    if (reading.distance) parts.push(reading.distance);
    setText($("station-detail"), parts.join(" · "));
  }

  function renderComfort(comfort) {
    var card = $("dew-card");
    if (!card) return;
    if (!comfort) { card.hidden = true; return; }
    card.hidden = false;
    setBig("s-dew", num(comfort.dew_point, 1));
    setText($("s-dew-sub"), comfort.label +
      (comfort.spread >= 3
        ? " · " + num(comfort.low, 1) + "° έως " + num(comfort.high, 1) + "°"
        : ""));
  }

  function renderSolar(solar) {
    var card = $("solar-card");
    if (!card) return;
    if (!solar) { card.hidden = true; return; }
    card.hidden = false;
    setBig("s-solar", num(solar.energy, 1));
    setText($("s-solar-sub"),
      num(solar.share) + "% του κανονικού · " + solar.text);
  }

  function renderStationRefs(station) {
    var panel = $("station-strip");
    var host = $("station-refs");
    if (!panel || !host) return;
    // The card above already leads with the primary station, so the strip is
    // the comparison: the others, and only the others.
    var all = ((station && station.all) || []).filter(function (item) {
      return !item.primary;
    });
    if (!all.length) { panel.hidden = true; return; }
    panel.hidden = false;
    clear(host);

    all.forEach(function (item) {
      var row = el("div", "ref");
      row.appendChild(el("span", "ref-name", item.name));
      row.appendChild(el("span", "ref-temp", num(item.temp, 1) + "°"));
      row.appendChild(el("span", "ref-range",
        (item.day_min !== null && item.day_max !== null
          ? num(item.day_min, 1) + "° έως " + num(item.day_max, 1) + "°"
          : "")));
      // The time and age sit at the right, where the distance used to be. The
      // distance moved into the note below, and the age is the more useful
      // thing to have beside a reading that may be a day old.
      row.appendChild(el("span", "ref-age", item.observed + " · " + item.age_text));
      host.appendChild(row);
    });
  }


  function renderSnowClimate(snow) {
    var panel = $("snow-panel");
    if (!panel) return;
    if (!snow) { panel.hidden = true; return; }
    panel.hidden = false;

    // Just the number: the label beside it already says "μέρες με χιόνι",
    // and repeating the unit printed "24 μέρες μέρες με χιόνι".
    setText($("snow-days-now"), String(snow.days_now));
    setText($("snow-days-before"),
      "από " + snow.days_before + " κατά μέσο όρο παλιότερα");

    // Two bars per month: the old share as a pale track, today's as a fill on
    // top of it, so the shrink is the thing you see rather than two numbers.
    var host = $("snow-months");
    clear(host);
    snow.months.forEach(function (item) {
      var row = el("div", "snow-month" +
        (item.month === snow.current ? " is-now" : ""));
      row.appendChild(el("span", "snow-month-name", item.name));

      var track = el("div", "snow-track");
      var before = el("div", "snow-fill was");
      before.style.width = Math.round(item.before / snow.peak * 100) + "%";
      var now = el("div", "snow-fill now");
      now.style.width = Math.round(item.now / snow.peak * 100) + "%";
      track.appendChild(before);
      track.appendChild(now);
      row.appendChild(track);

      row.appendChild(el("span", "snow-month-val", item.now + "%"));
      row.appendChild(el("span", "snow-month-was", "ήταν " + item.before + "%"));
      host.appendChild(row);
    });

    var bars = $("snow-decades");
    clear(bars);
    (snow.decades || []).forEach(function (item) {
      var bar = el("div", "snow-decade");
      // 92% leaves headroom, so the tallest bar is not flush with the top.
      bar.style.height = Math.max(8, Math.round(
        item.days / snow.decade_peak * 92)) + "%";
      bar.title = item.decade + "s: " + item.days + " μέρες";
      bars.appendChild(bar);
    });

    setText($("snow-note"), snow.note || "");
  }

  function renderMountain(mountain) {
    var panel = $("mountain-panel");
    if (!panel) return;
    if (!mountain) { panel.hidden = true; return; }
    panel.hidden = false;

    var winter = mountain.season === "winter";
    // The title is just the place. The season is carried by the metric labels
    // below — snow depth and road in winter, the temperature gap in summer —
    // and explained in full behind the ?.
    setText($("mountain-name"), mountain.name);
    setText($("mountain-temp"),
      (mountain.temp === null ? "–" : num(mountain.temp, 1) + "°"));
    // The elevation, in both seasons. The temperature gap gets its own metric
    // below, and repeating it here printed the same number twice.
    setText($("mountain-gap"),
      mountain.elevation ? num(mountain.elevation) + " μ." : "");

    if (winter) {
      setText($("mountain-second-lbl"), "Χιόνι στο διάσελο");
      setText($("mountain-second"), num(mountain.depth, 0) + " εκ.");
      setText($("mountain-second-sub"),
        mountain.fall >= 1 ? "νέα χιόνι " + num(mountain.fall, 1) + " εκ." : "χωρίς νέα χιόνι");

      var road = mountain.road;
      setText($("mountain-third-lbl"), "Πρόσβαση");
      setText($("mountain-third"), road ? road.label : "Ανοιχτός");
      setText($("mountain-third-sub"),
        road && road.freezing ? "παγώνει στα " + num(road.freezing) + " μ." : "");
      var third = $("mountain-third");
      if (third && third.parentNode) {
        toneFor(third.parentNode, road ? road.color : null);
      }
    } else {
      setText($("mountain-second-lbl"), "Πιο δροσερά από την πόλη");
      setText($("mountain-second"),
        mountain.gap === null ? "–" : num(mountain.gap, 1) + "°");
      setText($("mountain-second-sub"),
        mountain.city_temp === null ? "" : "πόλη " + num(mountain.city_temp, 1) + "°");

      setText($("mountain-third-lbl"), "Άνεμος");
      setText($("mountain-third"),
        mountain.wind === null ? "–" : num(mountain.wind, 1) + " km/h");
      setText($("mountain-third-sub"),
        mountain.wind === null ? "" :
        (mountain.wind >= 40 ? "δυνατός στο ύψωμα" : "ήπιος στο ύψωμα"));
      var thirdSummer = $("mountain-third");
      if (thirdSummer && thirdSummer.parentNode) {
        toneFor(thirdSummer.parentNode, null);
      }
    }

    setText($("mountain-hint"), mountain.hint || "");
  }

  function renderGreeting(greeting) {
    var node = $("greet");
    if (!node) return;
    if (!greeting || !greeting.word) { node.hidden = true; return; }
    node.textContent = greeting.word + "!" +
      (greeting.text ? " " + greeting.text : "");
    node.hidden = false;
  }

  function renderOutfit(outfit) {
    var panel = $("outfit-panel");
    if (!panel) return;
    if (!outfit) { panel.hidden = true; return; }
    panel.hidden = false;
    setText($("outfit-ico"), outfit.emoji);
    setText($("outfit-text"), outfit.text);
    setText($("outfit-detail"), outfit.detail || "");

    var host = $("outfit-items");
    if (host) {
      clear(host);
      (outfit.items || []).forEach(function (item) {
        var chip = el("span", "ocard-item");
        setText(chip, item.emoji + " " + item.text);
        host.appendChild(chip);
      });
    }

    // Only shown when two conditions together say more than each does alone.
    var advice = $("outfit-advice");
    if (advice) {
      setText(advice, outfit.advice || "");
      advice.hidden = !outfit.advice;
    }
  }

  function renderSky(sky) {
    var row = $("moon-row");
    if (!row) return;
    if (!sky) { row.hidden = true; return; }
    row.hidden = false;
    setText($("moon-ico"), sky.emoji);
    setText($("moon-name"), sky.name);
    setText($("moon-meta"),
      "φωτισμένο " + num(sky.illumination * 100, 0) + "% · ανατολή " +
      sky.rise + " · δύση " + sky.set);

    var verdict = $("moon-verdict");
    if (!verdict) return;
    if (sky.stargazing) {
      setText(verdict, sky.stargazing.text);
      verdict.style.setProperty("--tone", sky.stargazing.color);
      verdict.hidden = false;
    } else {
      verdict.hidden = true;
    }
  }

  function renderHero(data) {
    var cur = data.current || {};
    setText($("sym"), cur.emoji || "\u2601\ufe0f");
    setFlashed("temp", num(cur.temp, 1));
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

    // A screen reader gets the shape of the data, not just the axis labels.
    // Without this the chart announced "temperature and rain probability" and
    // nothing about the numbers, which is the entire content.
    var wettest = hours.reduce(function (best, hour) {
      return Number(hour.precip_prob) > Number(best.precip_prob) ? hour : best;
    }, hours[0]);
    var summary = "Από " + num(hours[minIndex].temp, 1) + "°C στις " +
      hours[minIndex].time + " έως " + num(hours[maxIndex].temp, 1) +
      "°C στις " + hours[maxIndex].time + ".";
    summary += Number(wettest.precip_prob) > 0
      ? " Μέγιστη πιθανότητα βροχής " + num(wettest.precip_prob) + "% στις " +
        wettest.time + "."
      : " Χωρίς βροχή σε όλο το διάστημα.";
    var desc = svg("desc");
    desc.textContent = summary;
    node.insertBefore(desc, node.firstChild);

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
    /* Collected off-document; appendInSlices moves them across without one
       long layout. */
    var built = document.createDocumentFragment();

    hours.forEach(function (hour, index) {
      if (hour.day && hour.day !== previousDay) {
        previousDay = hour.day;
        if (index > 0) {
          var separator = el("div", "hour-sep");
          separator.appendChild(el("span", "day-sep",
            hour.day.slice(8) + "/" + hour.day.slice(5, 7)));
          built.appendChild(separator);
        }
      }

      var card = el("div", "h" + (index === 0 ? " now" : ""));
      card.title = hour.text + " · αίσθητη " + num(hour.apparent, 1) + "° · υγρασία " +
                   num(hour.humidity) + "% · άνεμος " + num(hour.wind, 1) + " km/h" +
                   (hour.visibility_text ? " · ορατότητα: " + hour.visibility_text : "");
      card.appendChild(el("span", "t", index === 0 ? "τώρα" : hour.time));
      card.appendChild(el("span", "i", hour.emoji));
      card.appendChild(el("span", "d", num(hour.temp, 1) + "°"));

      // A mini bar for the rain probability. Across adjacent cards it reads as
      // one continuous chart, which is far easier to scan than the numbers.
      //
      // The track is only drawn for hours that actually have a chance of rain.
      // Deciding that for the whole strip at once was wrong: a single hour at
      // 1% made all 48 tracks visible and 47 of them empty grey boxes.
      var chance = Number(hour.precip_prob) || 0;
      var gauge = el("div", chance > 0 ? "bar" : "bar idle");
      gauge.setAttribute("aria-hidden", "true");
      var fill = el("i");
      fill.style.setProperty("--rain", num(hour.precip_prob, 0));
      gauge.appendChild(fill);
      card.appendChild(gauge);

      card.appendChild(el("span", "r", num(hour.precip_prob) + "%"));
      card.appendChild(el("span", "w", num(hour.wind, 1) + " km/h"));
    built.appendChild(card);
    });

    appendAll(host, built);
    if (state.syncHourlyFade) state.syncHourlyFade();
  }

  /* The strip scrolls horizontally; fade whichever edge has more to show so
     it does not just look clipped. */
  function initHourlyFade() {
    var strip = $("hourly");
    if (!strip) return;
    var max = 0;

    /* Only scrollLeft changes while scrolling. Reading scrollWidth and
       clientWidth forces a layout, and the first read after 48 tiles are
       appended pays for laying all of them out - 492 ms at 6x throttling,
       which is the single longest task on the page. Defer it to its own frame:
       the layout the browser was going to do anyway then happens on its own
       schedule instead of being charged to the render. */
    function measure() {
      requestAnimationFrame(function () {
        max = strip.scrollWidth - strip.clientWidth;
        update();
      });
    }

    function update() {
      strip.classList.toggle("can-left", strip.scrollLeft > 4);
      strip.classList.toggle("can-right", max > 4 && strip.scrollLeft < max - 4);
    }

    strip.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", measure);
    state.syncHourlyFade = measure;
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

      // How far apart the models are on this particular day. Always rendered so
      // the cards stay the same height, empty when only one model answered.
      var agree = el("span", "agree");
      if (day.agreement) {
        agree.textContent = "\u00b1" + num(day.agreement.spread, 1) + "°";
        // The headline is the wider of the two spreads, so the tooltip has to
        // say which range it came from. Quoting the highs beside a figure the
        // lows produced is what made this read as a bug.
        var lows = day.agreement.driver === "min" && day.agreement.low_range;
        var band = lows ? day.agreement.low_range : day.agreement.range;
        agree.title = day.agreement.label + " · " + day.agreement.models +
                      " μοντέλα · " + (lows ? "ελάχιστες" : "μέγιστες") + " " +
                      num(band[0], 1) + "° έως " + num(band[1], 1) + "°";
        agree.style.setProperty("--tone", day.agreement.color);
      }
      card.appendChild(agree);

      card.appendChild(el("span", "up",
        "UV " + num(day.uv_max, 1) + " · ριπές ανέμου " + num(day.gust_max) + " km/h"));
      host.appendChild(card);
    });
  }

  /* ---------------------------------------------------- local conditions */

  /* Each block is optional and is simply absent when it has nothing to say,
     so the panel itself disappears for most of the summer. */
  function renderLocal(local) {
    var panel = $("local-panel");
    if (!panel) return;
    if (!local) { panel.hidden = true; return; }

    var shown = 0;
    shown += renderInversion(local.inversion);

    shown += renderFrost(local.frost);
    shown += renderHeating(local.heating);
    shown += renderSmog(local.smog);
    panel.hidden = shown === 0;
  }

  function toneFor(node, colour) {
    if (colour) node.style.setProperty("--tone", colour);
  }

  function renderInversion(inversion) {
    var card = $("local-inversion");
    if (!card) return 0;
    if (!inversion) { card.hidden = true; return 0; }
    card.hidden = false;
    toneFor(card, inversion.color);

    setBig("inversion-val", "+" + num(inversion.anomaly, 1) + "°");
    var parts = [inversion.label];
    parts.push("η πόλη " + num(inversion.valley_temp, 1) + "° έναντι " +
               num(inversion.slope_temp, 1) + "° στα " + num(inversion.slope_elev) + " μ.");
    if (inversion.spread !== null && inversion.spread !== undefined) {
      parts.push("μοντέλα ±" + num(inversion.spread, 1) + "°");
    }
    setText($("inversion-sub"), parts.join(" · "));
    return 1;
  }

  function renderFrost(frost) {
    var card = $("local-frost");
    if (!card) return 0;
    if (!frost) { card.hidden = true; return 0; }
    card.hidden = false;
    toneFor(card, frost.color);

    setBig("frost-val", num(frost.min, 1) + "°");

    var parts = [frost.label];
    if (frost.count > 0) {
      parts.push(frost.count === 1 ? "1 νύχτα με παγετό"
                                   : frost.count + " νύχτες με παγετό");
      parts.push("πρώτη " + frost.first.label);
    } else {
      parts.push("καμία νύχτα κάτω από 0°");
    }
    // The two readings disagree, and the gap is the interesting part: the
    // ground freezes while the air is still above zero.
    if (frost.ground_min !== null && frost.ground_min !== undefined &&
        frost.air_min !== null && frost.air_min !== undefined) {
      parts.push("έδαφος " + num(frost.ground_min, 1) + "° · αέρας " +
                 num(frost.air_min, 1) + "°");
    }
    setText($("frost-sub"), parts.join(" · "));
    return 1;
  }

  function renderHeating(heating) {
    var card = $("local-heating");
    if (!card) return 0;
    if (!heating) { card.hidden = true; return 0; }
    card.hidden = false;
    toneFor(card, null);

    setBig("heating-val", num(heating.month, 0));
    setText($("heating-sub"),
      "βαθμοημέρες τον " + heating.month_name + " (" + heating.month_days +
      " ημέρες) · σήμερα " + num(heating.today, 1) +
      " · βάση " + num(heating.base, 0) + "°");
    return 1;
  }

  function renderSmog(smog) {
    var card = $("local-smog");
    if (!card) return 0;
    if (!smog) { card.hidden = true; return 0; }
    card.hidden = false;
    toneFor(card, smog.color);

    setBig("smog-val", num(smog.peak, 1));
    var parts = [smog.label, "αιχμή " + smog.peak_time];
    if (smog.wind !== null && smog.wind !== undefined) {
      parts.push("άνεμος " + num(smog.wind, 1) + " km/h" +
                 (smog.pooling ? " (άπνοια)" : " — διασκορπίζονται"));
    }
    // Ventilation advice only makes sense while the smoke is actually pooling.
    if (smog.pooling) parts.push("καθαρότερος αέρας " + smog.cleanest_time);
    setText($("smog-sub"), parts.join(" · "));
    return 1;
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
    if (navigator.onLine === false) parts.push("εκτός σύνδεσης");
    if (state.fromCache) parts.push("\u26a0 παλιά δεδομένα (από τη μνήμη)");
    if (status.degraded && status.degraded.length) {
      parts.push("\u26a0 μη διαθέσιμα: " + status.degraded.join(", "));
    }
    if (state.error) parts.push("\u26a0 " + state.error);
    setText(node, parts.join(" · "));
    node.classList.toggle("warn", navigator.onLine === false ||
      Boolean(state.error) || Boolean(state.fromCache) ||
      Boolean(status.degraded && status.degraded.length));
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

  /* The inline skeletons inside value slots are wiped by textContent, and the
     tile skeletons by clear(). This sweeps up whatever is left — currently the
     chart placeholder — once real data has arrived. */
  function dropSkeletons() {
    if (state.skeletonsDropped) return;
    state.skeletonsDropped = true;
    var nodes = document.querySelectorAll("[data-skeleton]");
    for (var i = nodes.length - 1; i >= 0; i--) {
      if (nodes[i].parentNode) nodes[i].parentNode.removeChild(nodes[i]);
    }
  }

  function render(data) {
    state.lastGenerated = data.generated_at;
    safely("theme", function () { applyWeatherTheme(data.current || {}); });
    safely("sunsky", function () { applySunSky(data); });
    safely("alerts", function () { renderAlerts(data.alerts); });
    safely("hero", function () { renderHero(data); });
    safely("greeting", function () { renderGreeting(data.greeting); });
    safely("normal", function () { renderNormal(data.normal); });
    safely("outfit", function () { renderOutfit(data.outfit); });
    safely("station", function () { renderStation(data.station); });
    safely("refs", function () { renderStationRefs(data.station); });
    safely("snow", function () { renderSnowClimate(data.snow); });
    safely("mountain", function () { renderMountain(data.mountain); });
    safely("comfort", function () { renderComfort(data.comfort); });
    safely("solar", function () { renderSolar(data.solar); });
    safely("sky", function () { renderSky(data.sky); });
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
    safely("local", function () { renderLocal(data.local); });
    safely("status", function () { renderStatus(data); });
    safely("title", function () { document.title = "Καιρός · " + data.place; });
    safely("skeletons", dropSkeletons);
    /* Every changed number was collected on the way through; one reflow for
       all of them, once the DOM has stopped moving. */
    safely("flash", flushFlash);
    /* Kept for the next visit, which then has numbers before the network
       answers. Only the fresh data is stored: a restored copy is marked stale
       and has no business being remembered as current. */
    if (!(data.status && data.status.stale)) safely("remember", function () { remember(data); });
    syncThemeColor();
  }

  /* ----------------------------------------------------------------- fetch */

  function setBusy(busy) {
    var button = $("refresh");
    if (button) button.setAttribute("aria-busy", busy ? "true" : "false");
  }

  /* ------------------------------------------- painting the last report */

  /* The page waits for /api/weather before it shows a single number, and the
     service worker waits up to 9 s for the network before falling back to its
     own copy. On a poor connection that is a long time to look at skeletons,
     so the last report is kept here and painted at once.

     Labelled as older data through the existing stale flag, because the rule
     for this app is that stale weather is worse than none - and stale weather
     that says it is stale is worse than neither. */
  var LAST_REPORT = "florina:last-report";
  var KEEP_FOR_S = 6 * 3600;   // past this the numbers mean nothing anyway

  function storeStorage(entry) {
    try {
      window.localStorage.setItem(LAST_REPORT, JSON.stringify(entry));
    } catch (error) {
      /* Private mode, quota, a hostile extension: none worth failing over. */
    }
  }

  function readStorage() {
    try {
      var raw = window.localStorage.getItem(LAST_REPORT);
      if (!raw) return null;
      var entry = JSON.parse(raw);
      if (!entry || !entry.report || !entry.saved) return null;
      if (Date.now() / 1000 - entry.saved > KEEP_FOR_S) return null;
      return entry.report;
    } catch (error) {
      return null;
    }
  }

  function remember(data) {
    if (!data) return;
    storeStorage({ saved: Math.round(Date.now() / 1000), report: data });
  }

  function restore() {
    var data = readStorage();
    if (!data) return false;
    /* Marked stale so the hero's own stamp says so. The fresh fetch clears it
       a moment later, and if the fetch never lands the label stays - which is
       the honest outcome. */
    data.status = data.status || {};
    data.status.stale = true;
    render(data);
    return true;
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
      // The service worker tags anything it served from its own cache, which
      // happens when the network is down or slow. Saying so beats passing old
      // numbers off as current.
      state.fromCache = response.headers.get("X-SW-Source") === "cache";
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

  /* ------------------------------------------------------- offline & PWA */

  /* Browsers treat loopback as a secure context, so the worker runs in local
     development too — but not over plain http on a LAN address. */
  function serviceWorkersAllowed() {
    if (location.protocol === "https:") return true;
    var host = location.hostname;
    return host === "localhost" || host === "127.0.0.1" ||
           host === "::1" || host === "[::1]";
  }

  /* Installed to the home screen only, this is what turns the page into a
     standalone app on Android; iOS uses the apple-touch-icon meta tags in the
     template and needs no worker. */
  function initServiceWorker() {
    if (!("serviceWorker" in navigator)) return;
    if (!serviceWorkersAllowed()) return;
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("/sw.js").catch(function (error) {
        if (window.console && window.console.warn) {
          window.console.warn("[florina] service worker not registered:", error);
        }
      });
    });
  }

  function initOffline() {
    window.addEventListener("offline", function () {
      // Re-render the footer so the notice appears immediately.
      if (state.lastGenerated !== null) renderStatus({ status: {} });
    });
    window.addEventListener("online", function () { load(true); });
  }

  /* ------------------------------------------------------- explanations */

  /* "What does this mean?" toggles. Presented as buttons rather than
     title tooltips so a thumb and a keyboard can both reach them. */
  function initInfo() {
    var buttons = document.querySelectorAll(".info[aria-controls]");
    Array.prototype.forEach.call(buttons, function (button) {
      button.addEventListener("click", function () {
        var hint = $(button.getAttribute("aria-controls"));
        if (!hint) return;
        var open = button.getAttribute("aria-expanded") === "true";
        button.setAttribute("aria-expanded", open ? "false" : "true");
        hint.hidden = open;
      });
    });
  }

  /* ------------------------------------------------------- reduced effects */

  /* theme.js owns the preference and applies the class before the first paint;
     this only wires the button and keeps its label honest. */
  function initEffects() {
    var theme = window.FlorinaTheme;
    var button = $("fx-toggle");
    if (!button || !theme || !theme.setFx) return;

    function sync() {
      var reduced = theme.fx() === "reduced";
      button.setAttribute("aria-pressed", reduced ? "true" : "false");
      button.title = reduced
        ? "Ενεργοποίηση εφέ και θολούρας"
        : "Λιγότερη θόλωση και κίνηση, για πιο ομαλό κύλισμα";
    }

    button.addEventListener("click", function () {
      theme.setFx(theme.fx() === "reduced" ? "full" : "reduced");
      sync();
    });

    document.addEventListener("florina:fx", sync);
    sync();
  }

  /* ------------------------------------------------------------------ boot */

  initModes();
  initEffects();
  initGlassHighlight();
  initHourlyFade();
  initServiceWorker();
  initOffline();
  initInfo();
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

  /* Paint what we already had before asking for more, so a slow connection
     shows last time's numbers instead of skeletons. */
  restore();

  load(false);
})();
