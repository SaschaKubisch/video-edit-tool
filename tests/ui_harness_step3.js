// Runs the script of ui.html in Node with a stub for the page, against a real server, to check the
// cards of step 3: the creative LUT, the loudness, the exports and the render button.
// Usage: node ui_harness_step3.js <ui.html> <base url>
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
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

(async () => {
  await run("loadFilm()");
  out.lutOptions = element("filmLutSelect").innerHTML;
  out.lutValueBefore = element("filmLutSelect").value;
  out.loudBefore = element("filmLoudMode").value;
  out.loudTarget = element("filmLoudTarget").textContent;
  out.exportsEmpty = element("filmExportTable").innerHTML;

  await run("filmSetLut('slay/slay-04-film.cube')");
  out.lutValueAfter = element("filmLutSelect").value;
  out.creditAfter = element("filmLutCredit").textContent;
  await run("filmSetLut('')");
  out.lutValueNone = element("filmLutSelect").value;
  out.creditNone = element("filmLutCredit").textContent;
  await run("filmSetLut('../outside.cube')");
  out.errorBadLut = element("filmError").textContent;

  await run("filmSetLoud('film_only')");
  out.loudAfter = element("filmLoudMode").value;
  await run("filmSetLoud('segment_then_film')");

  // an export: the button sends the preset, the server renders, the page loads the result
  await run("filmExportPreset('film-4k')");
  out.jobStarted = run("filmJob");
  for (let i = 0; i < 400; i++) {
    const s = await (await fetch(base + "/api/status")).json();
    if (s.step === "rendered" || s.step === "error") { out.finalStatus = s; break; }
    await sleep(100);
  }
  run("filmStatus(" + JSON.stringify(out.finalStatus) + ")");   // what the page's polling does
  await sleep(300);
  await run("loadFilm()");
  out.exportsAfter = element("filmExportTable").innerHTML;
  out.readoutAfterRender = element("filmLoudReadout").innerHTML;
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e.stack); process.exit(1); });
