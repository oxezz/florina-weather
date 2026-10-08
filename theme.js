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

  /* A visible switch for reduced effects, rather than only honouring the OS
     setting. Blur is the most expensive thing on this page and the backdrop is
     most of it, so a phone that struggles has no way to say so otherwise.

     Defaults to whatever the system asks for. Someone who has already told
     their phone they want less motion should not have to say it twice, but
     they can still override it here - which is why the choice is stored
     separately from the system setting rather than assumed to match it. */
  var FX_KEY = "florina.fx";
  var fxMedia = window.matchMedia("(prefers-reduced-motion: reduce)");

  function fxStored() {
    try {
      var value = window.localStorage.getItem(FX_KEY);
      return value === "full" || value === "reduced" ? value : null;
    } catch (error) {
      return null;
    }
  }

  /* "?fx=reduced" overrides for one visit without saving, like ?mode=. */
  function fxFromQuery() {
    var match = /[?&]fx=(full|reduced)(?:&|$)/.exec(window.location.search);
    return match ? match[1] : null;
  }

  function fxPreferred() {
    return fxFromQuery() || fxStored() || (fxMedia.matches ? "reduced" : "full");
  }

  function applyFx(value) {
    root.classList.toggle("reduce-fx", value === "reduced");
    root.setAttribute("data-fx", value);
    document.dispatchEvent(new CustomEvent("florina:fx", {
      detail: { fx: value }
    }));
  }

  api.fx = fxPreferred;
  api.setFx = function (value) {
    if (value !== "full" && value !== "reduced") value = "full";
    try { window.localStorage.setItem(FX_KEY, value); } catch (error) { /* ignore */ }
    applyFx(value);
    return value;
  };

  applyFx(fxPreferred());

  window.FlorinaTheme = api;
})();
