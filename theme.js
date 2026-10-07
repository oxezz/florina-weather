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

  window.FlorinaTheme = api;
})();
