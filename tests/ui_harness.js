// Runs the script of ui.html in Node with a stub for the page, against a real server, to
// check the Film tab's logic without a browser: it loads the film, builds the table, sends
// trims, and shows the server's refusals. Usage: node ui_harness.js <ui.html> <base url>
const fs = require("fs");
const vm = require("vm");

const [htmlPath, base] = process.argv.slice(2);
const html = fs.readFileSync(htmlPath, "utf8");
const script = html.match(/<script>([\s\S]*)<\/script>/)[1];

const elements = {};
function element(id) {
  if (!elements[id]) {
    const attrs = {};
    elements[id] = {
      id, style: {}, innerHTML: "", textContent: "", value: "", disabled: false, checked: false,
      currentTime: 0, duration: 100, className: "",
      classList: { toggle() {}, add() {}, remove() {} },
      addEventListener() {}, setAttribute(k, v) { attrs[k] = v; }, getAttribute(k) { return attrs[k] ?? null; },
      querySelectorAll() { return []; },
    };
  }
  return elements[id];
}

const sandbox = {
  console,
  setInterval: () => 1, clearInterval() {}, setTimeout, clearTimeout,
  confirm: () => true, alert() {},
  fetch: (path, opts) => fetch(base + path, opts),
  document: {
    getElementById: element,
    querySelector: () => element("_q"),
    querySelectorAll: () => [],
    addEventListener() {},
  },
};
const ctx = vm.createContext(sandbox);
vm.runInContext(script, ctx);

const run = (code) => vm.runInContext(code, ctx);
const out = {};

(async () => {
  await run("loadFilm()");
  out.loaded = run("film !== null");
  out.hash0 = run("film.hash");
  out.table = element("filmTable").innerHTML;
  out.summaryLine = element("filmSummaryLine").textContent;
  out.badge = element("filmPreviewBadge").textContent;
  out.rows0 = JSON.parse(run("JSON.stringify(film.summary.rows.map(r => [r.id, r.in, r.out, r.enabled]))"));

  await run("filmOp(null, [{op: 'trim_in', segment: 's001', frames: 30}])");
  out.afterTrim = JSON.parse(run("JSON.stringify(film.summary.rows.map(r => [r.id, r.in, r.out, r.enabled]))"));
  out.hash1 = run("film.hash");
  out.errorAfterTrim = element("filmError").textContent;

  await run("filmOp(null, [{op: 'set_enabled', segment: 's002', enabled: false}])");
  out.afterRemove = JSON.parse(run("JSON.stringify(film.summary.rows.map(r => [r.id, r.enabled, r.film_start_frame]))"));
  out.tableAfterRemove = element("filmTable").innerHTML;

  await run("filmOp(null, [{op: 'move', segment: 's003', to_index: 0}])");
  out.afterMove = JSON.parse(run("JSON.stringify(film.summary.rows.map(r => r.id))"));

  await run("filmOp(null, [{op: 'trim_out', segment: 's001', frames: 100000}])");
  out.errorInvalid = element("filmError").textContent;
  out.rowsAfterInvalid = JSON.parse(run("JSON.stringify(film.summary.rows.map(r => [r.id, r.out]))"));

  // the sync offset per clip: a typed value, a decimal comma, a mistake, a value out of range, empty
  const hands = () => JSON.parse(run("JSON.stringify(film.summary.clips.map(c => [c.source, c.offset_hand_s, c.offset_s]))"));
  out.clipsBefore = hands();
  await run("filmOffset({value: '-0.5'}, 'A002_01011540_C002')");
  out.afterOffset = hands();
  out.clipsTable = element("filmClipsTable").innerHTML;
  out.errorAfterOffset = element("filmError").textContent;
  await run("filmOffset({value: '0,25'}, 'A002_01011540_C002')");
  out.afterComma = hands();
  await run("filmOffset({value: 'abc'}, 'A002_01011540_C002')");
  out.errorNotANumber = element("filmError").textContent;
  out.afterNotANumber = hands();
  await run("filmOffset({value: '99'}, 'A002_01011540_C002')");
  out.errorOutOfRange = element("filmError").textContent;
  out.afterOutOfRange = hands();
  out.pendingNote = run("pendingText({pending: {picture: [], sound: ['s001', 's002']}})");
  out.pendingNone = run("pendingText({pending: {picture: ['s003'], sound: []}})");
  await run("filmOffset({value: '  '}, 'A002_01011540_C002')");
  out.afterReset = hands();
  out.clipsTableAfterReset = element("filmClipsTable").innerHTML;

  run("film.hash = '" + "0".repeat(64) + "'");
  await run("filmOp(null, [{op: 'set_label', segment: 's001', label: 'x'}])");
  out.errorStale = element("filmError").textContent;
  out.hashAfterStale = run("film.hash");

  console.log(JSON.stringify(out));
})().catch(e => { console.error(e.stack); process.exit(1); });
