// Records the demo video: a scripted walkthrough of the running app with on-screen captions.
// Usage: start the app (uvicorn app.main:app --port 8000), then
//   NODE_PATH=<dir containing playwright> node tools/record_demo.js <out_dir>
// Produces <out_dir>/<random>.webm; convert with ffmpeg (see docs/DEMO_SCRIPT.md).
const path = require('path');
const fs = require('fs');
let playwright;
try { playwright = require('playwright'); } catch (_) { playwright = require('/opt/npm-tools/node_modules/playwright'); }
const { chromium } = playwright;

const BASE = process.env.BASE_URL || 'http://localhost:8000';
const OUT = process.argv[2] || path.join(__dirname, '..', '.cache', 'video');
const ROOT = path.join(__dirname, '..');

const OVERLAY = () => {
  const install = () => {
    if (document.getElementById('demo-cap')) return;
    const style = document.createElement('style');
    style.textContent = `
      #demo-cap { position: fixed; left: 50%; bottom: 24px; transform: translateX(-50%); width: max-content; max-width: 1180px;
        background: rgba(20,24,33,.94); color: #fff; font: 500 22px/1.38 'IBM Plex Sans', system-ui, sans-serif;
        padding: 14px 24px; border-radius: 12px; z-index: 2147483000; box-shadow: 0 10px 34px rgba(0,0,0,.28);
        opacity: 0; transition: opacity .35s; pointer-events: none; }
      #demo-cap.on { opacity: 1; }
      #demo-cap b { color: #FF6EB9; font-weight: 600; }
      #demo-cursor { position: fixed; width: 22px; height: 22px; border-radius: 50%; border: 3px solid #E6007E;
        background: rgba(230,0,126,.14); z-index: 2147483001; pointer-events: none; transform: translate(-50%,-50%);
        transition: width .12s, height .12s, background .12s; left: -100px; top: -100px; }
      #demo-cursor.down { width: 36px; height: 36px; background: rgba(230,0,126,.35); }
      #demo-card { position: fixed; inset: 0; background: #F7F6F2; z-index: 2147482999; display: flex; flex-direction: column;
        align-items: center; justify-content: center; text-align: center; font-family: 'IBM Plex Sans', system-ui, sans-serif;
        color: #1B2230; opacity: 0; transition: opacity .5s; pointer-events: none; padding: 40px; }
      #demo-card.on { opacity: 1; }
      #demo-card h1 { font-size: 64px; font-weight: 600; letter-spacing: -.5px; }
      #demo-card h1 span { color: #E6007E; }
      #demo-card h2 { font-size: 34px; font-weight: 600; margin-bottom: 18px; }
      #demo-card p { font-size: 26px; color: #4A5468; margin-top: 16px; max-width: 1150px; line-height: 1.45; }
      #demo-card p b { color: #1B2230; }
      #demo-card img { max-width: 1340px; max-height: 600px; border-radius: 8px; box-shadow: 0 6px 30px rgba(0,0,0,.08); }
      #demo-card table { border-collapse: collapse; font-size: 22px; margin-top: 10px; }
      #demo-card th, #demo-card td { padding: 10px 18px; border-bottom: 1px solid #D6DAE2; text-align: left; }
      #demo-card th { color: #6B7385; font-weight: 500; font-size: 17px; text-transform: uppercase; letter-spacing: .6px; }
      #demo-card td.n { font-weight: 600; text-align: center; }
      #demo-card .ok { color: #2E7D32; }`;
    document.head.appendChild(style);
    for (const id of ['demo-cap', 'demo-cursor', 'demo-card']) {
      const d = document.createElement('div');
      d.id = id;
      document.body.appendChild(d);
    }
    const cur = document.getElementById('demo-cursor');
    document.addEventListener('mousemove', (e) => { cur.style.left = e.clientX + 'px'; cur.style.top = e.clientY + 'px'; }, true);
    document.addEventListener('mousedown', () => cur.classList.add('down'), true);
    document.addEventListener('mouseup', () => cur.classList.remove('down'), true);
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install); else install();
};

let mouse = { x: 720, y: 450 };
let T0 = 0;
const MARKS = {};
const mark = (name) => { MARKS[name] = (Date.now() - T0) / 1000; };

async function caption(p, html, ms = 0) {
  await p.evaluate((h) => { const c = document.getElementById('demo-cap'); c.innerHTML = h; c.classList.add('on'); }, html);
  if (ms) await p.waitForTimeout(ms);
}
async function hideCaption(p) {
  await p.evaluate(() => document.getElementById('demo-cap').classList.remove('on'));
  await p.waitForTimeout(350);
}
async function card(p, html, ms) {
  await p.evaluate((h) => { const c = document.getElementById('demo-card'); c.innerHTML = h; c.classList.add('on'); }, html);
  await p.waitForTimeout(ms);
  await p.evaluate(() => document.getElementById('demo-card').classList.remove('on'));
  await p.waitForTimeout(550);
}
async function moveTo(p, x, y, steps = 20) {
  await p.mouse.move(x, y, { steps });
  mouse = { x, y };
}
async function moveOver(p, sel, fx = 0.5, fy = 0.5) {
  const el = await p.waitForSelector(sel, { state: 'visible' });
  await el.scrollIntoViewIfNeeded();
  const b = await el.boundingBox();
  await moveTo(p, b.x + b.width * fx, b.y + b.height * fy);
  return el;
}
async function click(p, sel, fx = 0.5, fy = 0.5) {
  await moveOver(p, sel, fx, fy);
  await p.waitForTimeout(220);
  await p.mouse.down();
  await p.waitForTimeout(90);
  await p.mouse.up();
}
async function openFinding(p, rule, text, ms) {
  await click(p, `.finding[data-rule="${rule}"] .finding-head`, 0.4, 0.3);
  await p.waitForTimeout(700);
  if (text) await caption(p, text, ms);
}
async function scrollSide(p, sel) {
  await p.evaluate((s) => { const el = document.querySelector(s); if (el) el.scrollIntoView({ block: 'center', behavior: 'smooth' }); }, sel);
  await p.waitForTimeout(700);
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, recordVideo: { dir: OUT, size: { width: 1440, height: 900 } } });
  await ctx.addInitScript(OVERLAY);
  const p = await ctx.newPage();
  T0 = Date.now();
  const arch = 'data:image/png;base64,' + fs.readFileSync(path.join(ROOT, 'docs', 'architecture.png')).toString('base64');
  const evalSummary = JSON.parse(fs.readFileSync(path.join(ROOT, 'eval', 'results.json'), 'utf8')).summary;

  await p.goto(BASE + '/', { waitUntil: 'networkidle' });
  await p.waitForSelector('.sample');
  await moveTo(p, 720, 450, 1);

  // ---- title
  mark('start');
  await card(p, `<h1>Launch<span>Ready</span></h1><p>Is this packaging artwork ready to print?<br>Esko AIForge 2026 · Problem 2 — an AI-powered artwork checker</p>`, 4500);
  await card(p, `<h2>AI reads · code checks · people decide</h2><p>Every finding quotes the rule it comes from and shows its evidence on the artwork.<br>A <b>definite failure</b> only when code can prove it. Anything uncertain goes to <b>a person</b>.</p>`, 6000);

  // ---- setup
  await caption(p, `<b>Inputs:</b> Esko's sample carton (DeskPack PDF), Esko's US content sheet (Excel), and a 31-rule US rulebook (FDA 21 CFR 101 + company standards).`);
  await moveOver(p, '.sample[data-id="esko_granola"]', 0.55, 0.45);
  await p.waitForTimeout(6500);
  await caption(p, `Any artwork (print PDF or a photo), any rulebook (rule cards, PDF or Word) and any approved data (Excel, CSV, JSON) can be uploaded instead.`);
  await moveOver(p, '#check-form .drop[data-for="rulebook"]', 0.5, 0.5);
  await p.waitForTimeout(5000);
  await click(p, '#btn-run');
  mark('check1_start');
  await caption(p, `<b>Reading the print file:</b> exact text, fonts and positions from the PDF; dieline panels and the front panel; the barcode; text hidden under artwork.`);
  await p.waitForSelector('#result:not([hidden])', { timeout: 180000 });
  mark('check1_done');
  await p.waitForTimeout(1500);

  // ---- verdict
  await caption(p, `<b>Verdict:</b> not ready to print — 18 definite problems, 4 for a person to review, 7 passed, and 2 rules that come into force later.`);
  await moveOver(p, '#verdict .counts', 0.3, 0.5);
  await p.waitForTimeout(6500);

  // ---- findings
  await openFinding(p, 'U16', `<b>Cross-document reasoning:</b> “Suitable for Vegans” and a Certified Vegan mark — but <b>Honey</b> is in the ingredient list.`, 7500);
  await scrollSide(p, '.finding.open .rule-quote');
  await caption(p, `Each finding quotes the rule and its legal basis, explains why it matters, and says who decided — here, <b>deterministic code</b>.`, 7000);
  await openFinding(p, 'U14', `“<b>NO ADDED SUGAR</b>” on the top flap — yet honey, coconut sugar and cane sugar are ingredients.`, 7000);
  await openFinding(p, 'U06', `<b>Allergens:</b> almonds, pecans and walnuts are ingredients, but there is no “Contains:” statement — only “May contain” advisory text.`, 8000);
  await openFinding(p, 'U10', `<b>Arithmetic:</b> 166 kcal declared, but 34 g protein, 66 g carbohydrate and 15 g fat give about 535 kcal.`, 7000);
  await openFinding(p, 'U13', `<b>Against the content sheet:</b> carbohydrate 66 g on the label vs 15 g approved; sodium 23 mg vs 25 mg.`, 7000);
  await openFinding(p, 'U02', `<b>Placement:</b> the net quantity is only on the back panel. US rules require it on the principal display panel, in oz and g.`, 7500);

  // ---- review items
  await openFinding(p, 'U27', `<b>Print-file hygiene:</b> 19 lines of an old nutrition table are hidden under other artwork. They won't print, but they mislead proofreading.`);
  await click(p, '#t-hidden');
  await p.waitForTimeout(7500);
  await click(p, '#t-hidden');
  await openFinding(p, 'U15', `<b>Honest uncertainty:</b> “0 g added sugars” with honey is possible under rounding rules — so a person confirms it, instead of a false alarm.`, 8000);

  // ---- content sheet problems
  await p.evaluate(() => { document.querySelector('.side').scrollTop = 0; });
  await p.waitForTimeout(400);
  await click(p, '#source-issues summary', 0.3, 0.5);
  await caption(p, `The approved data is checked too: in the content sheet, sugars (18 g) exceed carbohydrate (15 g), and the calories don't match its own macronutrients.`, 8000);
  await click(p, '#source-issues summary', 0.3, 0.5);

  // ---- ask a question
  await openFinding(p, 'U02', null);
  await scrollSide(p, '.finding.open .ask');
  await click(p, '.finding.open .ask input', 0.3, 0.5);
  await p.keyboard.type('How do I fix this?', { delay: 55 });
  await click(p, '.finding.open .ask button');
  await caption(p, `Ask about any finding. With an API key switched on, the AI answers <b>only</b> from that finding's rule quote and evidence.`, 7500);

  // ---- rules tab: dates
  await click(p, '.tab[data-tab="rules"]');
  await caption(p, `<b>Rules tab:</b> 31 rules, each with its quote, check type and severity. Switch any rule off and re-run in under a second.`, 6000);
  await click(p, '#rr-date', 0.2, 0.5);
  await p.fill('#rr-date', '2027-01-02');
  await click(p, '#rr-run');
  await p.waitForTimeout(1200);
  await click(p, '.tab[data-tab="findings"]');
  await p.waitForTimeout(500);
  await openFinding(p, 'U30', `<b>Effective dates:</b> checked as of January 2027, the company's new QR-code rule now applies. The proposed FDA front-of-pack box stays under “Coming rules”.`, 8000);

  // ---- how it was checked
  await click(p, '.tab[data-tab="how"]');
  await caption(p, `<b>Traceability:</b> how the text was read, what was set aside (dieline guides, hidden text), and every AI action — accepted, rejected or downgraded.`, 7000);

  // ---- report
  const runId = new URL(p.url()).searchParams.get('run');
  await hideCaption(p);
  await p.goto(`${BASE}/api/runs/${runId}/report.html`, { waitUntil: 'networkidle' });
  await moveTo(p, 900, 500, 1);
  await caption(p, `<b>Printable report</b> with evidence crops, rule quotes and the verdict policy — ready to send to the design agency.`);
  await p.waitForTimeout(2500);
  for (let i = 0; i < 6; i++) { await p.mouse.wheel(0, 260); await p.waitForTimeout(450); }
  await p.waitForTimeout(1500);

  // ---- raster input: phone photo
  await hideCaption(p);
  await p.goto(BASE + '/', { waitUntil: 'networkidle' });
  await p.waitForSelector('.sample');
  await click(p, '.sample[data-id="cookies_photo"]', 0.55, 0.45);
  await caption(p, `<b>Raster input:</b> a phone photo of a printed proof (tilted, blurred, no scale), checked against an Indian FSSAI rulebook with neural OCR.`);
  await p.waitForTimeout(3500);
  await click(p, '#btn-run');
  mark('check2_start');
  await p.waitForSelector('#result:not([hidden])', { timeout: 180000 });
  mark('check2_done');
  await p.waitForTimeout(1200);
  await caption(p, `All 9 planted errors are found. What a photo can't prove (bold type, heights in mm) goes to a person — not to a false alarm.`);
  await moveOver(p, '#verdict .counts', 0.3, 0.5);
  await p.waitForTimeout(5000);
  await openFinding(p, 'R12', `Example: <b>soy</b> (from soy lecithin) is missing from the allergen statement — found on the photo, with the evidence boxed.`, 7000);
  await hideCaption(p);

  // ---- evaluation
  const row = (name, s) => `<tr><td>${name}</td><td class="n">${s.defects_flagged}/${s.defects}</td><td class="n">${s.defects_definite_fail}/${s.defects}</td><td class="n ok">${s.false_alarms}</td><td class="n">${s.avg_review_items}</td></tr>`;
  await card(p, `<h2>Tested on 84 artworks with known errors</h2>
    <p style="margin-top:0">26 single-error artworks + 2 clean ones, each as a print PDF, a flattened PNG and a simulated phone photo.</p>
    <table><tr><th>Input</th><th>Errors flagged</th><th>As definite FAIL</th><th>False alarms</th><th>Review items / file</th></tr>
    ${row('Print PDF', evalSummary.pdf)}${row('PNG, 200 dpi', evalSummary.png)}${row('Phone photo', evalSummary.photo)}</table>`, 10000);

  // ---- close
  await card(p, `<img src="${arch}"><p><b>AI reads · code checks · people decide.</b> Python · FastAPI · PyMuPDF · RapidOCR (PP-OCR) · OpenCV · zxing-cpp · optional Gemini / Claude / GPT.</p>`, 9000);
  await card(p, `<h1>Launch<span>Ready</span></h1><p>Fewer reprints, fewer recalls — and a checker people can trust.<br>Code, tests and evaluation: see the README.</p>`, 4000);

  mark('end');
  const video = p.video();
  await p.close();
  await ctx.close();
  await browser.close();
  const file = video ? await video.path() : null;
  fs.writeFileSync(path.join(OUT, 'marks.json'), JSON.stringify({ video: file, marks: MARKS }, null, 2));
  console.log('video saved:', file, MARKS);
})();
