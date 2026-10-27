// Runs the script of ui.html in Node with a stub for the page, against a real server, to check the
// cards of step 4: the text box of a scene, the cues, the form of an export, the titles, the translations.
// Usage: node ui_harness_step4.js <ui.html> <base url>
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
      id, style: {}, innerHTML: "", textContent: "", value: "", disabled: false, checked: true,
      currentTime: 0, duration: 100, className: "",
      classList: { toggle() {}, add() {}, remove() {} },
      addEventListener() {}, setAttribute(k, v) { attrs[k] = v; }, getAttribute(k) { return attrs[k] ?? null; },
      querySelectorAll() { return []; },
    };
  }
  return elements[id];
}

let confirmAnswer = true;
const questions = [];
const sandbox = {
  console,
  setInterval: () => 1, clearInterval() {}, setTimeout, clearTimeout,
  confirm: (q) => { questions.push(q); return confirmAnswer; }, alert() {},
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
  out.formRows = element("filmFormRows").innerHTML;
  out.boxes0 = element("filmSceneBoxes").innerHTML;
  out.cardsShown = [element("filmSubtitlesCard").style.display, element("filmTranslateCard").style.display,
                    element("filmTitlesCard").style.display];

  // the text box of a scene: type, save
  run("sceneDrafts['s003'] = 'Um, the pan is hot. We add the sausage and wait for it to cook'");
  await run("filmSceneText('s003')");
  out.boxes1 = element("filmSceneBoxes").innerHTML;
  out.draftKept = run("Object.prototype.hasOwnProperty.call(sceneDrafts, 's003')");
  out.cueCount1 = run("film.words.cues.filter(c => c.segment === 's003').length");
  out.sceneText = run("film.summary.rows[2].text");

  // move a cue the way a drag does, then save the box again: the page asks first
  const cue = run("JSON.stringify(film.words.cues.filter(c => c.segment === 's003')[0])");
  const c = JSON.parse(cue);
  out.dragMove = run(`JSON.stringify(dragCue({mode:'left', start:${c.film_start_s}, end:${c.film_end_s}, sceneStart:${run("film.summary.rows[2].film_start_s")},
      sceneLength:${run("film.summary.rows[2].length_s")}, width:1000}, 100))`);   // the start moves 10 percent of the scene later
  out.dragClamp = run(`JSON.stringify(dragCue({mode:'move', start:10, end:12, sceneStart:5, sceneLength:20, width:200}, 100000))`);
  out.dragLeft = run(`JSON.stringify(dragCue({mode:'left', start:10, end:12, sceneStart:5, sceneLength:20, width:200}, 100000))`);
  out.dragRight = run(`JSON.stringify(dragCue({mode:'right', start:10, end:12, sceneStart:5, sceneLength:20, width:200}, -100000))`);
  const moved = JSON.parse(out.dragMove);
  element("cueStart_" + c.id).value = String(moved.start);
  element("cueEnd_" + c.id).value = String(moved.end);
  await run(`filmCueTimes('${c.id}')`);
  out.lockedAfterMove = run(`film.words.cues.find(c => c.id === '${c.id}').locked`);

  confirmAnswer = false;
  run("sceneDrafts['s003'] = 'A different text.'");
  await run("filmSceneText('s003')");
  out.textAfterRefusal = run("film.summary.rows[2].text");
  out.draftAfterRefusal = run("sceneDrafts['s003']");
  confirmAnswer = true;
  await run("filmSceneText('s003')");
  out.textAfterConfirm = run("film.summary.rows[2].text");
  out.cuesAfterConfirm = run("JSON.stringify(film.words.cues.filter(c => c.segment === 's003').map(c => c.text))");
  out.question = questions[0];

  // numbers that are not numbers, an unknown cue time
  element("cueStart_c001").value = "soon";
  await run("filmCueTimes('c001')");
  out.badTimeError = element("filmError").textContent;

  // the form of an export
  out.formsBefore = run("JSON.stringify(film.words.forms)");
  await run("filmSetForm('film-4k', 'burned')");
  out.formsAfter = run("JSON.stringify(film.words.forms.map(f => f.form))");

  // titles
  element("newTitleText").value = "Monday kitchen";
  element("newTitleStart").value = "10";
  element("newTitleLength").value = "3";
  element("newTitleTemplate").value = "lower_third";
  await run("filmAddTitle()");
  out.titles = run("JSON.stringify(film.words.titles.map(t => [t.id, t.text, t.template, t.start_s, t.end_s]))");
  out.titleTable = element("filmTitlesTable").innerHTML;
  await run("filmEditTitle('t002', {text: 'Tuesday'})");
  out.titleEdited = run("film.words.titles.find(t => t.id === 't002').text");
  await run("filmOp(null, [{op:'remove_title', title:'t002'}])");
  out.titlesAfterRemove = run("film.words.titles.length");

  // a cue added at the playhead, removed
  run("document.getElementById('filmPlayer').currentTime = 0; document.getElementById('filmPlayer').style.display = 'block'");
  const beforeCount = run("film.words.cues.length");
  await run("filmAddCue('s002')");
  out.cuesAfterAdd = run("film.words.cues.length") - beforeCount;
  const added = run("film.words.cues.filter(c => c.segment === 's002').map(c => c.id).pop()");
  await run(`filmOp(null, [{op:'remove_cue', cue:'${added}'}])`);
  out.cuesAfterRemove = run("film.words.cues.length") - beforeCount;

  // the caption the page draws when the preview is out of date
  run("film.preview = {exists: true, up_to_date: false}");
  run("updateCaption(3.5)");
  out.caption = element("filmCaption").textContent;
  run("updateCaption(0.5)");
  out.captionNone = element("filmCaption").textContent;

  // the SRT file
  out.srt = JSON.stringify(await run("filmWriteSrt()"));
  out.srtMsg = element("filmWordsMsg").textContent;

  // translations: no key
  out.transMsg = element("filmTransMsg").textContent;
  out.transDisabled = element("filmTransBtn").disabled;
  out.langs = element("filmLangs").innerHTML;
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e.stack); process.exit(1); });
