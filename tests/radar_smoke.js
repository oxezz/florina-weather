/* Runtime smoke test for radar.js, driven by test_frontend.py when Node.js
   is available. The string-matching tests cannot see a strict-mode
   ReferenceError — the exact bug that kept the radar card hidden for a whole
   release — so this actually executes the script against a minimal DOM and
   checks that a fresh payload unhides the panel. */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const elements = {};

function ctx2d() {
  const noop = function () {};
  return {
    createImageData: (n) => ({ data: new Uint8ClampedArray(n * n * 4) }),
    putImageData: noop, setTransform: noop, clearRect: noop, save: noop,
    restore: noop, beginPath: noop, moveTo: noop, lineTo: noop,
    closePath: noop, stroke: noop, fill: noop, arc: noop, fillText: noop,
    drawImage: noop, strokeStyle: "", fillStyle: "", lineWidth: 1,
    globalAlpha: 1, font: "", textAlign: "", imageSmoothingEnabled: true,
    imageSmoothingQuality: "high"
  };
}

function el(id) {
  if (!elements[id]) {
    elements[id] = {
      id: id, hidden: true, textContent: "", innerHTML: "", value: "0",
      max: "0", width: 0, height: 0, dataset: {}, listeners: {},
      style: { setProperty: function () {} },
      addEventListener: function (type, fn) { this.listeners[type] = fn; },
      setAttribute: function () {},
      getContext: function () { return ctx2d(); },
      parentNode: { insertBefore: function () {} }
    };
  }
  return elements[id];
}

/* A wet frame followed by a dry one: the card must show, and the runaway
   animation loop must not start on its own. */
const payload = {
  generated: Math.floor(Date.now() / 1000), n: 4, km: 90, half: 0.01,
  center: [40.78, 21.41], palette: ["rgba(10,20,30,0.6)"],
  t: [1000, 2000], place: "Φλώρινα",
  frames: [[0, 5, 1, 11], [0, 16]]
};

const windowMock = {
  matchMedia: () => ({ matches: false }),
  devicePixelRatio: 1,
  getComputedStyle: () => ({ color: "rgba(1,2,3,0.9)" }),
  localStorage: { getItem: () => null, setItem: function () {} },
  console: console
};

const context = {
  console: console,
  window: windowMock,
  getComputedStyle: windowMock.getComputedStyle,
  document: {
    hidden: false,
    getElementById: el,
    createElement: (tag) => el("created-" + tag + "-" + Math.random()),
    addEventListener: function () {},
    dispatchEvent: function () {}
  },
  CustomEvent: function (name, init) { return { name: name, detail: init && init.detail }; },
  Intl: Intl, Date: Date, Math: Math,
  Uint8Array: Uint8Array, Uint8ClampedArray: Uint8ClampedArray,
  setTimeout: setTimeout, clearTimeout: clearTimeout,
  fetch: (url) => Promise.resolve({
    ok: true,
    json: async () => url.indexOf("geography") >= 0
      ? { borders: [], lakes: [] } : payload
  }),
  IntersectionObserver: class {
    constructor(cb) { this.cb = cb; }
    observe() { this.cb([{ isIntersecting: true }]); }
  }
};
context.window.document = context.document;

let failed = false;
process.on("uncaughtException", (error) => {
  console.error("uncaught:", error);
  failed = true;
});
process.on("unhandledRejection", (error) => {
  console.error("unhandled rejection:", error);
  failed = true;
});

const source = fs.readFileSync(path.join(__dirname, "..", "radar.js"), "utf8");
vm.runInNewContext(source, context);

setTimeout(() => {
  const panel = elements["radar-panel"];
  const note = elements["radar-note"];
  if (failed) process.exit(1);
  if (!panel || panel.hidden) {
    console.error("radar panel stayed hidden after a valid payload");
    process.exit(1);
  }
  if (note.textContent !== "") {
    console.error("a wet radar should not print the dry note");
    process.exit(1);
  }
  console.log("radar-smoke-ok");
  process.exit(0);
}, 300);
