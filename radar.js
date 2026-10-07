/* ==========================================================================
   Radar-lite — paints the tiny grid from /api/radar on a <canvas>.
   No map library, no tiles, no images: a few KB of JSON per refresh.

   Markup (put it after the 7-day card in template.html, load this file
   after app.js):

   <section class="card glass panel" id="radar-panel" hidden>
     <div class="panel-head">
       <h2>Ραντάρ βροχής</h2>
       <span class="chip muted" id="radar-time"></span>
     </div>
     <canvas id="radar-cv" role="img" aria-label="Ραντάρ βροχής γύρω από τη Φλώρινα"></canvas>
     <p class="sub" id="radar-note" aria-live="polite"></p>
     <button type="button" class="info" id="radar-play" aria-label="Παύση">&#10074;&#10074;</button>
     <p class="sub">Ραντάρ: <a href="https://www.rainviewer.com" target="_blank" rel="noopener">RainViewer</a></p>
   </section>

   CSS:  #radar-cv { display:block; width:100%; max-width:360px; aspect-ratio:1; margin:0 auto; color:inherit }
   ========================================================================== */
"use strict";

(function () {
  var panel = document.getElementById("radar-panel");
  if (!panel) return;

  var canvas = document.getElementById("radar-cv");
  var ctx = canvas.getContext("2d");
  var timeEl = document.getElementById("radar-time");
  var noteEl = document.getElementById("radar-note");
  var playBtn = document.getElementById("radar-play");

  var SIZE = 320;                      /* logical px */
  var REFRESH_MS = 600000;             /* matches the server's 10-minute step */
  var MAX_AGE_S = 2400;                /* stale radar is worse than none */
  var TOWNS = [
    ["Καστοριά", 40.5192, 21.2687],
    ["Έδεσσα", 40.8022, 22.0476],
    ["Μοναστήρι", 41.0297, 21.3347],
    ["Πτολεμαΐδα", 40.5167, 21.6833]
  ];
  var clock = new Intl.DateTimeFormat("el-GR", {
    hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: "Europe/Athens"
  });
  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  var data = null, frames = [], colors = [], layer = null;
  var current = 0, playing = false, timer = 0, visible = false, loadedAt = 0;

  var dpr = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = canvas.height = SIZE * dpr;

  /* ---- data ------------------------------------------------------------ */

  function expand(runs, n) {
    var cells = new Uint8Array(n * n), at = 0;
    for (var i = 0; i < runs.length; i += 2) {
      cells.fill(runs[i], at, at + runs[i + 1]);
      at += runs[i + 1];
    }
    return cells;
  }

  function parseColor(css) {
    var m = /rgba\((\d+),(\d+),(\d+),([\d.]+)\)/.exec(css) || [0, 0, 0, 0, 0];
    return [+m[1], +m[2], +m[3], Math.round(m[4] * 255)];
  }

  function load() {
    fetch("/api/radar", { cache: "no-store" })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (json) {
        if (Date.now() / 1000 - json.generated > MAX_AGE_S || !json.frames.length) {
          throw new Error("stale");
        }
        data = json;
        colors = json.palette.map(parseColor);
        frames = json.frames.map(function (runs) { return expand(runs, json.n); });
        loadedAt = Date.now();
        layer = layer || document.createElement("canvas");
        layer.width = layer.height = json.n;
        var wet = frames.some(function (f) { return f.some(function (v) { return v; }); });
        panel.hidden = false;
        canvas.hidden = playBtn.hidden = !wet;
        timeEl.hidden = !wet;
        noteEl.textContent = wet ? "" : "Δεν εντοπίζεται βροχή σε ακτίνα " + json.km + " χλμ.";
        if (wet) { current = frames.length - 1; draw(); play(!reduceMotion); }
        else { stop(); }
      })
      .catch(function () { panel.hidden = true; stop(); });
  }

  /* ---- drawing --------------------------------------------------------- */

  function mx(lon) { return (lon + 180) / 360; }
  function my(lat) {
    var s = Math.sin(lat * Math.PI / 180);
    return 0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI);
  }
  function project(lat, lon) {
    var span = 2 * data.half;
    return [
      SIZE / 2 + (mx(lon) - mx(data.center[1])) / span * SIZE,
      SIZE / 2 + (my(lat) - my(data.center[0])) / span * SIZE
    ];
  }

  function draw() {
    if (!data) return;
    var n = data.n, cells = frames[current];
    var lctx = layer.getContext("2d");
    var img = lctx.createImageData(n, n);
    for (var i = 0; i < cells.length; i++) {
      var c = cells[i] ? colors[cells[i] - 1] : null;
      if (c) {
        img.data[i * 4] = c[0]; img.data[i * 4 + 1] = c[1];
        img.data[i * 4 + 2] = c[2]; img.data[i * 4 + 3] = c[3];
      }
    }
    lctx.putImageData(img, 0, 0);

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, SIZE, SIZE);
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(layer, 0, 0, SIZE, SIZE);

    /* rings and landmarks take the card's text colour, so both themes work */
    var ink = getComputedStyle(canvas).color;
    ctx.strokeStyle = ctx.fillStyle = ink;
    ctx.lineWidth = 1;
    ctx.globalAlpha = 0.28;
    for (var km = 30; km <= data.km; km += 30) {
      ctx.beginPath();
      ctx.arc(SIZE / 2, SIZE / 2, km / data.km * SIZE / 2, 0, 6.2832);
      ctx.stroke();
    }
    ctx.globalAlpha = 0.75;
    ctx.font = "11px system-ui, sans-serif";
    TOWNS.forEach(function (t) {
      var p = project(t[1], t[2]);
      if (p[0] < 4 || p[0] > SIZE - 4 || p[1] < 4 || p[1] > SIZE - 4) return;
      ctx.beginPath(); ctx.arc(p[0], p[1], 2, 0, 6.2832); ctx.fill();
      ctx.textAlign = p[0] > SIZE * 0.75 ? "right" : "left";
      ctx.fillText(t[0], p[0] + (ctx.textAlign === "left" ? 5 : -5), p[1] - 4);
    });
    ctx.globalAlpha = 1;
    ctx.beginPath(); ctx.arc(SIZE / 2, SIZE / 2, 4, 0, 6.2832); ctx.fill();
    ctx.textAlign = "left";
    ctx.fillText("Φλώρινα", SIZE / 2 + 7, SIZE / 2 + 4);

    timeEl.textContent = clock.format(new Date(data.t[current] * 1000));
  }

  /* ---- loop ------------------------------------------------------------ */

  function tick() {
    current = (current + 1) % frames.length;
    draw();
    timer = setTimeout(tick, current === frames.length - 1 ? 1600 : 550);
  }
  function play(on) {
    clearTimeout(timer);
    playing = !!on && frames.length > 1 && visible && !document.hidden;
    playBtn.innerHTML = playing ? "&#10074;&#10074;" : "&#9654;";
    playBtn.setAttribute("aria-label", playing ? "Παύση" : "Αναπαραγωγή");
    if (playing) timer = setTimeout(tick, 550);
    playBtn.dataset.want = on ? "1" : "";
  }
  function stop() { clearTimeout(timer); playing = false; }

  playBtn.addEventListener("click", function () {
    var want = !playing;
    if (want && current === frames.length - 1) current = -1;
    play(want);
  });

  /* ---- lifecycle: only work while the card is on screen ----------------- */

  function resume() {
    if (!visible || document.hidden) { stop(); return; }
    if (!data || Date.now() - loadedAt > REFRESH_MS) load();
    else if (playBtn.dataset.want === "1") play(true);
  }
  document.addEventListener("visibilitychange", resume);
  document.addEventListener("florina:mode", draw);   /* recolour rings on theme change */

  /* A hidden element never intersects, so watch a zero-height sentinel
     placed just before the panel instead. */
  if ("IntersectionObserver" in window) {
    var sentinel = document.createElement("div");
    panel.parentNode.insertBefore(sentinel, panel);
    new IntersectionObserver(function (entries) {
      visible = entries[0].isIntersecting;
      resume();
    }, { rootMargin: "200px" }).observe(sentinel);
  } else {
    visible = true;
    load();
  }
})();
