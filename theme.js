/* ==========================================================================
   Appearance bootstrap.
   Loaded synchronously from <head> so the correct material is on <html>
   before the first paint — otherwise switching to light mode would flash dark.

   The choice is stored in localStorage; "auto" follows the system setting and
   keeps following it if the OS changes while the page is open.
   ========================================================================== */
"use strict";

(function () {
  var KEY = "florina.mode";
  var MODES = ["auto", "light", "dark"];
  var root = document.documentElement;
  var media = window.matchMedia("(prefers-color-scheme: light)");

  function stored() {
    try {
      var value = window.localStorage.getItem(KEY);
      return MODES.indexOf(value) >= 0 ? value : "auto";
    } catch (error) {
      return "auto"; /* private mode, storage disabled */
    }
  }

  /* "?mode=dark" overrides the stored choice for one visit without saving it,
     which makes a particular appearance linkable. */
  function fromQuery() {
    var match = /[?&]mode=(auto|light|dark)(?:&|$)/.exec(window.location.search);
    return match ? match[1] : null;
  }

  function preferred() {
    return fromQuery() || stored();
  }

  function resolve(mode) {
    if (mode === "auto") return media.matches ? "light" : "dark";
    return mode;
  }

  function persist(mode) {
    try { window.localStorage.setItem(KEY, mode); } catch (error) { /* ignore */ }
  }

  function apply(mode) {
    var actual = resolve(mode);
    var light = actual === "light";
    root.classList.toggle("mode-light", light);
    root.classList.toggle("mode-dark", !light);
    root.setAttribute("data-mode", mode);

    /* The browser chrome has to follow, or a light page sits under a dark
       address bar. `color-scheme` also repaints form controls and scrollbars. */
    root.style.colorScheme = light ? "light" : "dark";
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", light ? "#eaf3fb" : "#0c2339");

    document.dispatchEvent(new CustomEvent("florina:mode", {
      detail: { mode: mode, actual: actual }
    }));
  }

  var api = {
    modes: MODES.slice(),
    get: preferred,
    resolve: function () { return resolve(preferred()); },
    set: function (mode) {
      if (MODES.indexOf(mode) < 0) mode = "auto";
      persist(mode);
      apply(mode);
      return mode;
    }
  };

  /* Follow the OS while the user has not overridden it. */
  var onSystemChange = function () {
    if (preferred() === "auto") apply("auto");
  };
  if (media.addEventListener) media.addEventListener("change", onSystemChange);
  else if (media.addListener) media.addListener(onSystemChange);

  apply(preferred());

  /* ------------------------------------------------------------ effects */

  /* A visible switch for the expensive visuals, in both directions.

     The hover media query is a guess about what a touch device can afford.
     Usually right; the reader is the one who knows when it is wrong, so this
     can hand blur back as well as take it away.

     Two inputs, and they are not the same kind of thing. The OS preference is
     a *preference* and is followed. Whether the device has a fine pointer is a
     *capability*, and the honest starting value for a touch device is reduced,
     because that is already what it renders - the switch then means "give me
     the full thing anyway", which is the point of it. */
  var FX_KEY = "florina.fx";
  var fxMedia = window.matchMedia("(prefers-reduced-motion: reduce)");
  var hoverMedia = window.matchMedia("(hover: hover) and (pointer: fine)");

  function fxStored() {
    try {
      var value = window.localStorage.getItem(FX_KEY);
      return value === "full" || value === "reduced" ? value : null;
    } catch (error) {
      return null;
    }
  }

  /* "?fx=full" overrides for one visit without saving, like ?mode=. */
  function fxFromQuery() {
    var match = /[?&]fx=(full|reduced)(?:&|$)/.exec(window.location.search);
    return match ? match[1] : null;
  }

  function fxDefault() {
    if (fxMedia.matches) return "reduced";
    return hoverMedia.matches ? "full" : "reduced";
  }

  function fxPreferred() {
    return fxFromQuery() || fxStored() || fxDefault();
  }

  function applyFx(value) {
    var reduced = value === "reduced";
    root.classList.toggle("fx-reduced", reduced);
    root.classList.toggle("fx-full", !reduced);
    root.setAttribute("data-fx", value);
    document.dispatchEvent(new CustomEvent("florina:fx", {
      detail: { fx: value }
    }));
  }

  api.fx = fxPreferred;
  api.fxDefault = fxDefault;
  api.setFx = function (value) {
    if (value !== "full" && value !== "reduced") value = fxDefault();
    try { window.localStorage.setItem(FX_KEY, value); } catch (error) { /* ignore */ }
    applyFx(value);
    return value;
  };

  applyFx(fxPreferred());

  /* A device that gains or loses a fine pointer mid-session - a tablet with a
     trackpad attached - should re-derive, unless the reader has chosen. */
  var onHoverChange = function () {
    if (!fxStored() && !fxFromQuery()) applyFx(fxDefault());
  };
  if (hoverMedia.addEventListener) hoverMedia.addEventListener("change", onHoverChange);
  else if (hoverMedia.addListener) hoverMedia.addListener(onHoverChange);

  window.FlorinaTheme = api;
})();
