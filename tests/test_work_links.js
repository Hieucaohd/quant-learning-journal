const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const events = {};
const lesson = { open: false, parentElement: null };
const unrelated = { open: false };
let focused = false;
let scrolled = false;
const task = {
  open: false,
  matches: (selector) => selector === "details",
  parentElement: { closest: () => lesson },
  querySelector: () => ({ focus: () => { focused = true; } }),
  scrollIntoView: () => { scrolled = true; },
};
const window = {
  location: { hash: "#task-42" },
  addEventListener: (event, callback) => { events[event] = callback; },
};
vm.runInNewContext(fs.readFileSync("app/static/work_links.js", "utf8"), {
  window,
  document: { getElementById: (key) => key === "task-42" ? task : null },
  requestAnimationFrame: (callback) => callback(),
});
assert.ok(task.open && lesson.open && focused && scrolled);
assert.equal(unrelated.open, false);
task.open = lesson.open = false;
window.location.hash = "#task-999";
events.hashchange();
assert.equal(task.open, false);
window.location.hash = "#task-42";
events.hashchange();
assert.ok(task.open && lesson.open);
console.log("Work links: initial navigation, nested details, scrolling, hash changes and missing tasks passed.");
