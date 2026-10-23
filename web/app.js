"use strict";
/* LaunchReady front end — plain JavaScript, no build step. */

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const S = {
  status: null, sample: null, run: null, active: null, page: 0,
  view: { scale: 1, tx: 0, ty: 0, fit: 1 }, numbers: new Map(), tab: "findings",
};

const VERDICT_TEXT = { NOT_READY: "Not ready to print", NEEDS_REVIEW: "Needs a person", READY: "Ready to print" };
const STEPS = [
  ["Reading the artwork", "Read the artwork"],
  ["Reading the approved", "Read the approved product data"],
  ["Extracting facts", "Extract facts"],
  ["Reading the rulebook", "Read the rulebook"],
  ["Running", "Run the checks and verify each problem"],
];

async function api(url, opts = {}) {
  const r = await fetch(url, opts);
  let data = null;
  try { data = await r.json(); } catch (_) { /* not JSON */ }
  if (!r.ok) throw new Error((data && (data.detail || data.error)) || `The server answered ${r.status}.`);
  return data;
}

/* ================================================================ setup screen */

async function boot() {
  wireForm();
  wireViewer();
  wireTabs();
  $("#btn-new").addEventListener("click", () => { history.replaceState(null, "", "/"); showScreen("setup"); });
  try {
    S.status = await api("/api/status");
  } catch (e) {
    $("#setup-title").textContent = "The LaunchReady server is not reachable";
    return;
  }
  renderAiStatus(S.status.llm);
  renderSamples(S.status.samples);
  renderRulebookSelect(S.status.rulebooks);
  $("#as-of").value = S.status.today;
  const runId = new URLSearchParams(location.search).get("run");
  if (runId) {
    try { await openRun(runId); return; } catch (_) { history.replaceState(null, "", "/"); }
  }
  if (S.status.samples.length) selectSample(S.status.samples[0].id);
}

function renderAiStatus(llm) {
  const el = $("#ai-status");
  if (llm && llm.enabled) {
    el.textContent = `AI assist on: ${llm.model}`;
    el.classList.add("on");
    el.title = "An LLM reads free-text rulebooks, double-checks raster artwork, judges subjective rules and answers questions. Every AI output is verified before use.";
  } else if (llm && llm.configured) {
    el.textContent = "AI assist unavailable";
    el.title = `An API key is set but the AI service could not be reached (${llm.last_error || "network"}). All deterministic checks still run; judgment rules go to a person.`;
  } else {
    el.textContent = "AI assist off";
    el.title = "No LLM API key configured. All deterministic checks run; judgment rules go to a person. Add a key in .env to switch AI assist on.";
  }
}

function renderSamples(samples) {
  const list = $("#sample-list");
  list.innerHTML = "";
  for (const s of samples) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "sample";
    b.dataset.id = s.id;
    b.setAttribute("role", "listitem");
    b.setAttribute("aria-pressed", "false");
    const isEsko = /esko/i.test(s.source || "");
    b.innerHTML = `<img src="/api/samples/${encodeURIComponent(s.id)}/thumb.png" alt="" loading="lazy">
      <div><h3>${esc(s.title)}</h3><p>${esc(s.description)}</p>
      <span class="src ${isEsko ? "esko" : ""}">${esc(isEsko ? "Esko sample file" : "Synthetic test file")}</span>
      <span class="src">${esc(s.market || "")}</span></div>`;
    b.addEventListener("click", () => selectSample(s.id));
    list.appendChild(b);
  }
}

function renderRulebookSelect(rulebooks) {
  const sel = $("#rulebook-select");
  sel.innerHTML = rulebooks.map((r) => `<option value="${esc(r.name)}">${esc(r.title)}</option>`).join("");
}

function selectSample(id) {
  S.sample = S.status.samples.find((s) => s.id === id) || null;
  $$(".sample").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.id === id)));
  if (S.sample) {
    $("#rulebook-select").value = S.sample.rulebook;
    $("#selected-hint").textContent = `Using the sample “${S.sample.title}”. Upload a file below to replace any part of it.`;
    setDropText("artwork", null, S.sample.artwork);
    setDropText("approved", null, S.sample.approved);
  }
}

function setDropText(name, file, sampleName) {
  const drop = $(`.drop[data-for="${name}"]`);
  const txt = $(".drop-file", drop);
  if (file) { txt.textContent = file.name; drop.classList.add("has-file"); return; }
  drop.classList.remove("has-file");
  txt.textContent = sampleName ? `Sample: ${sampleName}` : txt.dataset.empty;
}

function wireForm() {
  for (const drop of $$(".drop")) {
    const input = $("input", drop);
    input.addEventListener("change", () => {
      const f = input.files[0];
      const name = drop.dataset.for;
      setDropText(name, f, S.sample && !f ? S.sample[name === "artwork" ? "artwork" : "approved"] : null);
      if (name === "artwork" && f) $("#selected-hint").textContent = "Your artwork will be checked. Add the rulebook and approved data that belong to it.";
    });
    ["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, () => drop.classList.add("drag")));
    ["dragleave", "drop"].forEach((t) => drop.addEventListener(t, () => drop.classList.remove("drag")));
  }
  $("#check-form").addEventListener("submit", submitCheck);
}

async function submitCheck(ev) {
  ev.preventDefault();
  const err = $("#form-error");
  err.hidden = true;
  const art = $('input[name="artwork"]').files[0];
  const rb = $('input[name="rulebook"]').files[0];
  const ap = $('input[name="approved"]').files[0];
  if (!art && !S.sample) { err.textContent = "Choose a sample or upload an artwork file."; err.hidden = false; return; }
  const fd = new FormData();
  if (art) fd.append("artwork", art);
  else fd.append("sample_id", S.sample.id);
  if (rb) fd.append("rulebook", rb); else fd.append("rulebook_name", $("#rulebook-select").value);
  if (ap) fd.append("approved", ap);
  else if (art) fd.append("use_sample_approved", "false");
  fd.append("as_of", $("#as-of").value);
  $("#btn-run").disabled = true;
  showScreen("progress");
  renderSteps(-1);
  try {
    const job = await api("/api/check", { method: "POST", body: fd });
    await pollRun(job.id);
  } catch (e) {
    showScreen("setup");
    err.textContent = e.message;
    err.hidden = false;
  } finally {
    $("#btn-run").disabled = false;
  }
}

function renderSteps(current) {
  $("#progress-steps").innerHTML = STEPS.map(([, label], i) =>
    `<li class="${i < current ? "done" : i === current ? "now" : ""}">${esc(label)}</li>`).join("");
}

async function pollRun(id) {
  for (;;) {
    const r = await api(`/api/runs/${id}`);
    if (r.status === "done") { history.replaceState(null, "", `?run=${id}`); showResult(r); return; }
    if (r.status === "error") throw new Error(r.error || "The check failed.");
    $("#progress-stage").textContent = r.stage || "Working…";
    $("#progress-time").textContent = r.elapsed ? `${r.elapsed.toFixed(0)} s` : "";
    const idx = STEPS.findIndex(([p]) => (r.stage || "").startsWith(p));
    renderSteps(idx);
    await sleep(500);
  }
}

async function openRun(id) {
  showScreen("progress");
  await pollRun(id);
}

function showScreen(name) {
  $("#setup").hidden = name !== "setup";
  $("#progress").hidden = name !== "progress";
  $("#result").hidden = name !== "result";
  $("#btn-new").hidden = name === "setup";
}

/* ================================================================ result */

function showResult(run) {
  S.run = run;
  S.active = null;
  S.page = 0;
  numberFindings();
  showScreen("result");
  renderVerdict();
  renderSourceIssues();
  renderFindings();
  renderRules();
  renderData();
  renderHow();
  setupPages();
  loadPage(0);
  selectTab(S.tab);
}

function allFindings() {
  return [...S.run.findings, ...S.run.future.map((f) => ({ ...f, future: true }))];
}

function numberFindings() {
  S.numbers = new Map();
  let n = 0;
  for (const f of S.run.findings) {
    if (f.verdict === "FAIL" || f.verdict === "REVIEW") S.numbers.set(f.rule_id, ++n);
  }
}

function renderVerdict() {
  const r = S.run;
  const s = r.summary;
  const c = s.counts;
  const futureTxt = s.future_risks
    ? ` ${s.future_risks} rule${s.future_risks > 1 ? "s" : ""} not yet in force would also flag this artwork.`
    : "";
  $("#verdict").innerHTML = `
    <div><span class="stamp ${s.status}">${esc(VERDICT_TEXT[s.status])}</span></div>
    <h2>${esc(s.headline)}</h2>
    <div class="counts">
      <div class="count FAIL"><b>${c.FAIL}</b><span>must fix</span></div>
      <div class="count REVIEW"><b>${c.REVIEW}</b><span>need a person</span></div>
      <div class="count PASS"><b>${c.PASS}</b><span>passed</span></div>
      <div class="count NA"><b>${c.NA}</b><span>not applicable</span></div>
    </div>
    <p class="verdict-meta">${esc(r.artwork.filename)} was checked against ${r.rulebook.rules.length} rules (${esc(r.rulebook.name)})${r.approved.name ? ` and the approved data in ${esc(r.approved.name)}` : ""}, as of ${esc(fmtDate(r.as_of))}.${esc(futureTxt)}</p>
    <div class="verdict-actions">
      <a class="btn small" href="/api/runs/${r.id}/report.html" target="_blank" rel="noopener">Open printable report</a>
      <a class="btn small" href="/api/runs/${r.id}/report.json">Download results (JSON)</a>
    </div>`;
}

function renderSourceIssues() {
  const w = (S.run.approved && S.run.approved.warnings) || [];
  const el = $("#source-issues");
  if (!w.length) { el.innerHTML = ""; return; }
  const data = w.filter((x) => !x.message.startsWith("Content-sheet note"));
  const notes = w.filter((x) => x.message.startsWith("Content-sheet note"));
  el.innerHTML = `<details class="callout source">
    <summary>The approved data has ${w.length} problem${w.length > 1 ? "s" : ""} of its own</summary>
    ${data.length ? `<ul>${data.map((x) => `<li>${esc(x.message)}</li>`).join("")}</ul>` : ""}
    ${notes.length ? `<p class="hint" style="margin-top:6px">Open questions written in the sheet:</p><ul>${notes.map((x) => `<li>${esc(x.message.replace(/^Content-sheet note on '([^']+)': /, "$1: "))}</li>`).join("")}</ul>` : ""}
    <p class="hint">Fix these in the source data too, or the next artwork will inherit them.</p></details>`;
}

const GROUPS = [
  ["FAIL", "Must fix", "Definite problems: deterministic code found a violation on a confident reading."],
  ["REVIEW", "Needs a person", "Uncertain readings, judgment calls and AI suggestions. A person decides."],
  ["FUTURE", "Coming rules", "Rules not yet in force on the check date. Shows what will need to change."],
  ["PASS", "Passed", "Each pass shows the evidence that satisfied the rule."],
  ["NA", "Not applicable", "The rule does not apply to this artwork or data."],
];

function renderFindings() {
  const panel = $("#tab-findings");
  panel.innerHTML = "";
  const items = allFindings();
  for (const [key, title, note] of GROUPS) {
    const list = items.filter((f) => (key === "FUTURE" ? f.future : !f.future && f.verdict === key));
    if (!list.length) continue;
    const g = document.createElement(key === "PASS" || key === "NA" ? "details" : "section");
    g.className = "group";
    g.innerHTML = key === "PASS" || key === "NA"
      ? `<summary><h3 style="display:inline-flex">${esc(title)} <span class="n">${list.length}</span></h3></summary><p class="group-note">${esc(note)}</p>`
      : `<h3>${esc(title)} <span class="n">${list.length}</span></h3><p class="group-note">${esc(note)}</p>`;
    for (const f of list) g.appendChild(findingEl(f, key));
    panel.appendChild(g);
  }
}

function findingEl(f, group) {
  const el = $("#tpl-finding").content.firstElementChild.cloneNode(true);
  const cls = group === "FUTURE" ? "FUTURE" : f.verdict;
  el.classList.add(cls);
  el.dataset.rule = f.rule_id;
  const num = S.numbers.get(f.rule_id);
  $(".pin-num", el).textContent = num ? num : { PASS: "✓", NA: "–", FUTURE: "!" }[cls] || "";
  $(".finding-title", el).innerHTML = `${esc(f.title)}<small>${esc(f.rule_id)} · ${esc(shorten(f.message, 120))}</small>`;
  $(".finding-meta", el).innerHTML = `<span class="sev ${esc(f.severity)}">${esc(f.severity)}</span>`;
  const head = $(".finding-head", el);
  head.addEventListener("click", () => toggleFinding(f.rule_id));
  return el;
}

function shorten(s, n) { s = String(s || ""); return s.length > n ? s.slice(0, n - 1) + "…" : s; }

function toggleFinding(ruleId, opts = {}) {
  const el = $(`.finding[data-rule="${CSS.escape(ruleId)}"]`);
  if (!el) return;
  const open = el.classList.contains("open") && !opts.forceOpen;
  $$(".finding.open").forEach((x) => { x.classList.remove("open"); $(".finding-body", x).hidden = true; $(".finding-head", x).setAttribute("aria-expanded", "false"); });
  if (open) { S.active = null; drawOverlay(); captionFor(null); return; }
  const f = allFindings().find((x) => x.rule_id === ruleId);
  el.classList.add("open");
  const body = $(".finding-body", el);
  body.innerHTML = findingBody(f);
  body.hidden = false;
  $(".finding-head", el).setAttribute("aria-expanded", "true");
  wireFindingBody(body, f);
  const det = el.closest("details");
  if (det) det.open = true;
  if (opts.scroll) el.scrollIntoView({ block: "nearest", behavior: reduceMotion ? "auto" : "smooth" });
  S.active = f.rule_id;
  focusFinding(f);
}

function findingBody(f) {
  const src = f.source || {};
  const cite = [src.doc, src.section || (src.page ? `page ${src.page}` : "")].filter(Boolean).join(", ");
  const method = {
    code: ["code", "Decided by deterministic code"],
    ai: ["ai", "Suggested by AI — a person confirms"],
    none: ["none", "Needs human judgment"],
  }[f.method] || ["code", f.method];
  const ef = f.expected || f.found
    ? `<div class="ef"><div><b>Expected</b>${esc(f.expected || "—")}</div><div><b>Found on the artwork</b>${esc(f.found || "—")}</div></div>` : "";
  const diff = f.diff && f.diff.length && f.diff.some((d) => d.op !== "equal")
    ? `<div class="diff" aria-label="Word differences">${f.diff.map((d) => d.op === "delete" ? `<del>${esc(d.text)}</del>` : d.op === "insert" ? `<ins>${esc(d.text)}</ins>` : esc(d.text)).join(" ")}</div>` : "";
  const chips = (f.evidence || []).filter((e) => e.label).map((e, i) =>
    `<button class="chip" data-ev="${i}">${esc(shorten(e.label, 40))}</button>`).join("");
  return `
    <p class="msg">${esc(f.message)}</p>
    ${ef}${diff}
    ${chips ? `<div class="ev-chips">${chips}</div>` : ""}
    <blockquote class="rule-quote">“${esc(f.quote)}”<cite>${esc(cite)}${f.basis ? ` — ${esc(f.basis)}` : ""}</cite></blockquote>
    ${f.why ? `<p class="why"><b>Why it matters:</b> ${esc(f.why)}</p>` : ""}
    <div class="decided"><span class="badge ${method[0]}">${esc(method[1])}</span><span>Reading confidence ${Math.round((f.confidence || 0) * 100)}%</span>${f.timeline ? `<span class="badge future">${esc(f.timeline)}</span>` : ""}</div>
    ${f.notes && f.notes.length ? `<ul class="notes">${f.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : ""}
    <form class="ask"><input type="text" maxlength="400" placeholder="Ask about this finding, e.g. “How do I fix it?”" aria-label="Question about this finding"><button class="btn small" type="submit">Ask</button></form>
    <div class="answer-box"></div>`;
}

function wireFindingBody(body, f) {
  $$(".chip", body).forEach((c) => c.addEventListener("click", () => {
    const e = f.evidence[Number(c.dataset.ev)];
    if (e.page !== S.page) loadPage(e.page, () => zoomTo(e.bbox, true));
    else zoomTo(e.bbox, true);
  }));
  $(".ask", body).addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const input = $("input", ev.target);
    const q = input.value.trim();
    if (!q) return;
    const box = $(".answer-box", body);
    box.innerHTML = `<div class="answer">Thinking…</div>`;
    try {
      const a = await api(`/api/runs/${S.run.id}/ask`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ rule_id: f.rule_id, question: q }),
      });
      box.innerHTML = `<div class="answer">${esc(a.answer)}<small>${a.source === "ai" ? "Answered by the AI assistant from this finding's evidence only." : "Answered from the finding's own data (AI assist is off)."}</small></div>`;
    } catch (e) {
      box.innerHTML = `<div class="answer">${esc(e.message)}</div>`;
    }
  });
}

/* ================================================================ rules tab */

function renderRules() {
  const r = S.run;
  const rules = r.rulebook.rules;
  const enabled = rules.filter((x) => x.enabled).length;
  $("#tab-rules").innerHTML = `
    <div class="rules-tools">
      <p class="hint">${esc(r.rulebook.name)}: ${rules.length} rules (${esc(r.rulebook.kind)}). Switch rules off or change the date to see what would apply, then re-run. Re-running reuses the extracted artwork, so it takes under a second.</p>
      <div class="row"><label for="rr-date">Rules in force on</label><input type="date" id="rr-date" value="${esc(r.as_of)}">
      <button class="btn small" id="rr-run">Re-run checks</button><span class="hint" id="rr-count">${enabled} of ${rules.length} rules on</span></div>
    </div>
    <div id="rule-list">${rules.map((x) => `
      <label class="rule-row"><input type="checkbox" data-rule="${esc(x.id)}" ${x.enabled ? "checked" : ""}>
        <span><b>${esc(x.id)} ${esc(x.title)}</b>
        <span class="q" style="display:block">“${esc(shorten(x.quote, 220))}”</span>
        <span class="tags"><span>${esc(x.check)}</span><span>${esc(x.severity)}</span>${x.status !== "in_force" ? `<span>${esc(x.status.replace("_", " "))}${x.effective_from ? " from " + esc(fmtDate(x.effective_from)) : ""}</span>` : ""}<span>${esc({ rulebook: "from the rulebook", ai_compiled: "compiled by AI (quote verified)", pattern_compiled: "compiled offline" }[x.origin] || x.origin)}</span></span></span>
      </label>`).join("")}</div>`;
  $$("#rule-list input").forEach((cb) => cb.addEventListener("change", () => {
    const on = $$("#rule-list input").filter((x) => x.checked).length;
    $("#rr-count").textContent = `${on} of ${rules.length} rules on`;
  }));
  $("#rr-run").addEventListener("click", rerun);
}

async function rerun() {
  const disabled = $$("#rule-list input").filter((x) => !x.checked).map((x) => x.dataset.rule);
  const btn = $("#rr-run");
  btn.disabled = true;
  try {
    const run = await api(`/api/runs/${S.run.id}/rerun`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ disabled, as_of: $("#rr-date").value }),
    });
    showResult({ ...run, status: "done" });
  } catch (e) {
    alert(e.message);
  } finally {
    btn.disabled = false;
  }
}

/* ================================================================ data tab */

function fmtVal(v) {
  if (v == null) return "—";
  if (Array.isArray(v)) return v.join("; ");
  if (typeof v === "object") return Object.entries(v).map(([k, x]) => `${k}: ${typeof x === "object" ? JSON.stringify(x) : x}`).join(", ");
  if (typeof v === "boolean") return v ? "yes" : "no";
  return String(v);
}

function nutritionTable(n, appr) {
  const v = n.value || {};
  const us = v.format === "us";
  const vals = us ? v.per_serve || {} : v.per_100 || {};
  const apprVals = (us ? appr.nutrition_per_serving : appr.nutrition_per_100g) || {};
  const dv = v.dv || {};
  const label = (k) => k.replace(/_(g|mg|mcg|kcal)$/, "").replace(/_/g, " ");
  const unit = (k) => (k.match(/_(g|mg|mcg|kcal)$/) || [, ""])[1];
  const rows = Object.keys({ ...vals, ...apprVals }).map((k) => {
    const a = vals[k], b = apprVals[k];
    const bad = a != null && b != null && Math.abs(a - b) > Math.max(0.02 * Math.abs(b), unit(k) === "mg" || unit(k) === "kcal" ? 1 : 0.051);
    return `<tr class="${bad ? "bad" : ""}"><td>${esc(label(k))}</td><td class="num">${a ?? "—"} ${a != null ? esc(unit(k)) : ""}</td><td class="num">${dv[k] != null ? esc(dv[k]) + "%" : ""}</td><td class="num">${b ?? "—"} ${b != null ? esc(unit(k)) : ""}</td></tr>`;
  }).join("");
  return `<p class="section-title">Nutrition ${us ? "per serving (US Nutrition Facts)" : "per 100 g"}</p>
    <table class="kv"><thead><tr><th>Nutrient</th><th class="num">Artwork</th><th class="num">%DV</th><th class="num">Approved</th></tr></thead><tbody>${rows}</tbody></table>
    ${us ? `<p class="hint" style="margin:-8px 0 14px">Serving size printed as “${esc(v.serving_size_raw ?? "—")}”, ${esc(v.servings_per_container ?? "—")} servings per container.</p>` : ""}`;
}

function renderData() {
  const r = S.run;
  const appr = r.approved.data || {};
  const facts = r.facts.filter((f) => f.key !== "nutrition");
  const n = r.facts.find((f) => f.key === "nutrition");
  const factRows = facts.map((f, i) => `<tr><th>${esc(f.label)}</th><td>${esc(shorten(fmtVal(f.value), 220))}
      <div class="hint">${esc(f.method === "parser" ? "text layer" : f.method === "vision" ? "image analysis" : "AI")}, confidence ${Math.round(f.confidence * 100)}%
      ${(f.line_ids && f.line_ids.length) || (f.attrs && f.attrs.bbox) ? ` <button class="link" data-fact="${i}">Show on artwork</button>` : ""}</div></td></tr>`).join("");
  const notes = r.approved.notes || {};
  const apprRows = Object.entries(appr).filter(([k]) => !/^nutrition|^dv_/.test(k)).map(([k, v]) =>
    `<tr><th>${esc(k.replace(/_/g, " "))}</th><td>${esc(shorten(fmtVal(v), 260))}${notes[k] ? `<div class="hint">Note in sheet: ${esc(shorten(notes[k], 200))}</div>` : ""}</td></tr>`).join("");
  $("#tab-data").innerHTML = `
    <p class="hint">Everything below was read from the files. Each artwork fact links to the place it came from.</p>
    <p class="section-title">Read from the artwork</p>
    <table class="kv"><tbody>${factRows || `<tr><td>No facts were extracted.</td></tr>`}</tbody></table>
    ${n ? nutritionTable(n, appr) : ""}
    <p class="section-title">Approved product data ${r.approved.name ? `(${esc(r.approved.name)}, read as ${esc(r.approved.kind)})` : ""}</p>
    ${apprRows ? `<table class="kv"><tbody>${apprRows}</tbody></table>` : `<p class="hint">No approved data was supplied, so rules that compare with it are marked not applicable.</p>`}`;
  $$("#tab-data button[data-fact]").forEach((b) => b.addEventListener("click", () => {
    const f = facts[Number(b.dataset.fact)];
    const boxes = f.line_ids.map((id) => r.artwork.lines.find((l) => l.id === id)).filter(Boolean);
    S.active = null;
    S.factBoxes = boxes.length ? boxes.map((l) => ({ page: l.page, bbox: l.bbox, label: f.label })) :
      [{ page: f.attrs.page || 0, bbox: f.attrs.bbox, label: f.label }];
    const go = () => { drawOverlay(); zoomTo(unionBox(S.factBoxes.map((x) => x.bbox)), true); };
    if (S.factBoxes[0].page !== S.page) loadPage(S.factBoxes[0].page, go); else go();
    captionFor(null, `${f.label}: ${shorten(fmtVal(f.value), 160)}`);
  }));
}

/* ================================================================ how tab */

function renderHow() {
  const r = S.run;
  const a = r.artwork;
  const m = a.meta || {};
  const lines = a.lines || [];
  const hidden = lines.filter((l) => l.hidden && !l.technical).length;
  const tech = lines.filter((l) => l.technical).length;
  const graphic = lines.filter((l) => l.graphic).length;
  const merged = lines.filter((l) => l.merged).length;
  const ai = r.ai || {};
  const events = (ai.events || []).map((e) => `<div class="event"><span class="a ${esc(e.action)}">${esc(e.stage)} · ${esc(e.action)}</span> ${esc(e.detail)}</div>`).join("");
  $("#tab-how").innerHTML = `
    <ol class="steps">
      <li><b>Read the artwork.</b> ${a.kind === "pdf" ? (a.quality === "vector" ? "Exact text, font sizes and positions came from the PDF's text layer (no OCR guessing)." : "The PDF has no usable text layer, so it was read with OCR.") : "The image was straightened and read with OCR (two independent readings for short or uncertain text)."}
        ${m.panels_from ? ` ${a.panels.length} panels were taken from the file's ${esc(m.panels_from)}.` : ` ${a.panels.length} panel(s) detected.`}
        ${tech ? ` ${tech} dieline or proof annotations (e.g. “Front panel”, “Art orientation”) were set aside as non-printing.` : ""}
        ${hidden ? ` ${hidden} lines hidden under other artwork were excluded.` : ""}
        ${merged ? ` ${merged} curved or stacked headings were rebuilt from single letters.` : ""}
        ${graphic ? ` ${graphic} words inside logos, seals and barcodes were read by OCR.` : ""}
        ${a.barcodes.length ? ` Barcode decoded: ${esc(a.barcodes.map((b) => b.text).join(", "))}.` : ""}</li>
      <li><b>Extract facts.</b> ${r.facts.length} facts (quantities, ingredients, nutrition panel, claims, marks), each linked to the lines it came from.</li>
      <li><b>Load the rules.</b> ${r.rulebook.rules.length} rules from ${esc(r.rulebook.name)} (${esc(r.rulebook.kind)}). Every rule keeps the sentence it was taken from.</li>
      <li><b>Run the checks.</b> Each rule runs as deterministic code. Before anything is reported missing, a second look searches for near-matches (and, on photos, re-reads the image at double resolution).</li>
      <li><b>Decide.</b> Must fix only when code proves a violation on a confident reading. Uncertain readings, judgment calls and anything produced by AI go to “Needs a person”.</li>
    </ol>
    ${a.warnings.length ? `<p class="section-title">Notes from reading the artwork</p><ul class="notes">${a.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>` : ""}
    <p class="section-title">AI assist</p>
    <p class="hint">${ai.llm && ai.llm.enabled ? `On (${esc(ai.llm.provider)} ${esc(ai.llm.model)}). Each AI output below was verified before it could affect a result.` : "Off — no API key configured. Deterministic checks ran in full. The judgment rule was sent to a person, and follow-up answers come from the finding's own data."}</p>
    ${events || ""}
    <p class="section-title">Time taken</p>
    <table class="kv"><tbody>${Object.entries(r.timings).map(([k, v]) => `<tr><th>${esc(k)}</th><td class="num">${v.toFixed(1)} s</td></tr>`).join("")}</tbody></table>`;
}

/* ================================================================ tabs */

function wireTabs() {
  $$(".tab").forEach((t) => t.addEventListener("click", () => selectTab(t.dataset.tab)));
}

function selectTab(name) {
  S.tab = name;
  $$(".tab").forEach((t) => t.setAttribute("aria-selected", String(t.dataset.tab === name)));
  $$(".tabpanel").forEach((p) => { p.hidden = p.id !== `tab-${name}`; });
}

/* ================================================================ artwork viewer */

function setupPages() {
  const sel = $("#page-select");
  const pages = S.run.artwork.pages;
  sel.hidden = pages.length < 2;
  sel.innerHTML = pages.map((p, i) => `<option value="${i}">Page ${i + 1}</option>`).join("");
  $("#viewer-file").textContent = S.run.artwork.filename;
}

function loadPage(n, then) {
  const page = S.run.artwork.pages[n];
  S.page = n;
  $("#page-select").value = String(n);
  const img = $("#page-img");
  img.onload = () => {
    const w = img.naturalWidth, h = img.naturalHeight;
    const canvas = $("#canvas");
    canvas.style.width = `${w}px`;
    canvas.style.height = `${h}px`;
    const svg = $("#overlay");
    svg.setAttribute("width", w);
    svg.setAttribute("height", h);
    svg.setAttribute("viewBox", `0 0 ${page.width} ${page.height}`);
    fit(false);
    drawOverlay();
    if (then) then();
  };
  img.src = page.image_url;
}

function stageSize() {
  const st = $("#stage");
  return [st.clientWidth, st.clientHeight];
}

function fit(animate = true) {
  const img = $("#page-img");
  if (!img.naturalWidth) return;
  const [W, H] = stageSize();
  const s = Math.min((W - 40) / img.naturalWidth, (H - 40) / img.naturalHeight);
  S.view.fit = s;
  setView(s, (W - img.naturalWidth * s) / 2, (H - img.naturalHeight * s) / 2, animate);
}

function setView(scale, tx, ty, animate) {
  const v = S.view;
  if (!animate || reduceMotion) {
    Object.assign(v, { scale, tx, ty });
    applyView();
    return;
  }
  const from = { ...v };
  const t0 = performance.now();
  const dur = 380;
  const step = (now) => {
    const k = Math.min(1, (now - t0) / dur);
    const e = 1 - Math.pow(1 - k, 3);
    v.scale = from.scale + (scale - from.scale) * e;
    v.tx = from.tx + (tx - from.tx) * e;
    v.ty = from.ty + (ty - from.ty) * e;
    applyView();
    if (k < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

function applyView() {
  const v = S.view;
  $("#canvas").style.transform = `translate(${v.tx}px, ${v.ty}px) scale(${v.scale})`;
  placePins();
}

function toScreen(x, y) {
  const rs = S.run.artwork.pages[S.page].render_scale;
  const v = S.view;
  return [v.tx + x * rs * v.scale, v.ty + y * rs * v.scale];
}

function unionBox(boxes) {
  boxes = boxes.filter(Boolean);
  if (!boxes.length) return null;
  return [Math.min(...boxes.map((b) => b[0])), Math.min(...boxes.map((b) => b[1])),
    Math.max(...boxes.map((b) => b[2])), Math.max(...boxes.map((b) => b[3]))];
}

function zoomTo(b, animate) {
  if (!b) return;
  const rs = S.run.artwork.pages[S.page].render_scale;
  const [W, H] = stageSize();
  let [x0, y0, x1, y1] = b.map((v) => v * rs);
  const minSide = 260;
  if (x1 - x0 < minSide) { const c = (x0 + x1) / 2; x0 = c - minSide / 2; x1 = c + minSide / 2; }
  if (y1 - y0 < minSide * 0.6) { const c = (y0 + y1) / 2; y0 = c - minSide * 0.3; y1 = c + minSide * 0.3; }
  const pad = 50;
  let s = Math.min((W - 2 * pad) / (x1 - x0), (H - 2 * pad) / (y1 - y0));
  s = Math.max(S.view.fit, Math.min(s, 3));
  setView(s, W / 2 - ((x0 + x1) / 2) * s, H / 2 - ((y0 + y1) / 2) * s, animate);
}

function focusFinding(f) {
  S.factBoxes = null;
  const ev = (f.evidence || []).filter((e) => e.bbox);
  const pick = ev.filter((e) => e.kind !== "panel");
  const boxes = (pick.length ? pick : ev);
  captionFor(f);
  if (!boxes.length) { drawOverlay(); fit(true); return; }
  const page = boxes[0].page;
  const go = () => { drawOverlay(); zoomTo(unionBox(boxes.filter((e) => e.page === page).map((e) => e.bbox)), true); };
  if (page !== S.page) loadPage(page, go); else go();
}

function captionFor(f, text) {
  const el = $("#viewer-caption");
  if (text) { el.textContent = text; return; }
  if (!f) { el.textContent = "Select a finding to see its evidence on the artwork. Drag to move, scroll to zoom."; return; }
  const n = S.numbers.get(f.rule_id);
  const where = (f.evidence || []).length ? "" : " This finding has no single location (something is missing or the whole panel is affected).";
  el.textContent = `${n ? n + ". " : ""}${f.rule_id} ${f.title}.${where}`;
}

const SVGNS = "http://www.w3.org/2000/svg";

function rect(svg, b, cls) {
  const r = document.createElementNS(SVGNS, "rect");
  r.setAttribute("x", b[0]);
  r.setAttribute("y", b[1]);
  r.setAttribute("width", Math.max(0.5, b[2] - b[0]));
  r.setAttribute("height", Math.max(0.5, b[3] - b[1]));
  r.setAttribute("class", cls);
  svg.appendChild(r);
  return r;
}

function drawOverlay() {
  const svg = $("#overlay");
  svg.innerHTML = `<defs><pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="6" stroke="rgba(192,38,45,.45)" stroke-width="2"/></pattern></defs>`;
  const a = S.run.artwork;
  if ($("#t-panels").checked) {
    for (const p of a.panels.filter((p) => p.page === S.page)) rect(svg, p.bbox, `panel${p.is_pdp ? " pdp" : ""}`);
  }
  if ($("#t-hidden").checked) {
    for (const l of a.lines.filter((l) => l.page === S.page)) {
      if (l.technical) rect(svg, l.bbox, "tech");
      else if (l.hidden) rect(svg, l.bbox, "hid");
    }
  }
  const f = S.active ? allFindings().find((x) => x.rule_id === S.active) : null;
  if (f) {
    const cls = f.future ? "FUTURE" : f.verdict;
    for (const e of (f.evidence || []).filter((e) => e.page === S.page && e.bbox)) {
      rect(svg, e.bbox, `ev ${cls}${e.kind === "panel" ? " panel-ev" : ""}`);
    }
  }
  if (S.factBoxes) for (const e of S.factBoxes.filter((e) => e.page === S.page)) rect(svg, e.bbox, "ev PASS");
  placePins();
}

function placePins() {
  const layer = $("#pins");
  if (!S.run) return;
  layer.innerHTML = "";
  const a = S.run.artwork;
  const page = S.page;
  if ($("#t-panels").checked) {
    for (const p of a.panels.filter((p) => p.page === page)) {
      const [x, y] = toScreen(p.bbox[0], p.bbox[1]);
      const t = document.createElement("span");
      t.className = "tagl panel";
      t.style.left = `${x + 2}px`;
      t.style.top = `${y}px`;
      t.textContent = p.label;
      layer.appendChild(t);
    }
  }
  const f = S.active ? allFindings().find((x) => x.rule_id === S.active) : null;
  if (f) {
    const cls = f.future ? "FUTURE" : f.verdict;
    const seen = new Set();
    for (const e of (f.evidence || []).filter((e) => e.page === page && e.bbox && e.label)) {
      const key = `${Math.round(e.bbox[0])}:${Math.round(e.bbox[1])}`;
      if (seen.has(key)) continue;
      seen.add(key);
      const [x, y] = toScreen(e.bbox[0], e.bbox[1]);
      const t = document.createElement("span");
      t.className = `tagl ${cls}`;
      t.style.left = `${x}px`;
      t.style.top = `${y - 3}px`;
      t.textContent = e.label;
      layer.appendChild(t);
    }
  }
  if (!$("#t-pins").checked) return;
  for (const g of S.run.findings) {
    const n = S.numbers.get(g.rule_id);
    if (!n) continue;
    const ev = (g.evidence || []).filter((e) => e.bbox && e.page === page);
    const e = ev.find((x) => x.kind !== "panel") || ev[0];
    if (!e) continue;
    const [x, y] = toScreen(e.bbox[0], (e.bbox[1] + e.bbox[3]) / 2);
    const pin = document.createElement("button");
    pin.className = `pin ${g.verdict}${S.active === g.rule_id ? " active" : ""}`;
    pin.style.left = `${x - 14}px`;
    pin.style.top = `${y}px`;
    pin.textContent = n;
    pin.title = `${n}. ${g.title}`;
    pin.setAttribute("aria-label", `Problem ${n}: ${g.title}`);
    pin.addEventListener("click", (ev) => { ev.stopPropagation(); selectTab("findings"); toggleFinding(g.rule_id, { forceOpen: true, scroll: true }); });
    layer.appendChild(pin);
  }
}

function wireViewer() {
  const stage = $("#stage");
  let drag = null;
  stage.addEventListener("pointerdown", (e) => {
    if (e.target.closest(".pin")) return;
    drag = { x: e.clientX, y: e.clientY, tx: S.view.tx, ty: S.view.ty };
    stage.setPointerCapture(e.pointerId);
    stage.classList.add("dragging");
  });
  stage.addEventListener("pointermove", (e) => {
    if (!drag) return;
    S.view.tx = drag.tx + e.clientX - drag.x;
    S.view.ty = drag.ty + e.clientY - drag.y;
    applyView();
  });
  const end = () => { drag = null; stage.classList.remove("dragging"); };
  stage.addEventListener("pointerup", end);
  stage.addEventListener("pointercancel", end);
  stage.addEventListener("wheel", (e) => {
    e.preventDefault();
    const r = stage.getBoundingClientRect();
    zoomAt(e.clientX - r.left, e.clientY - r.top, Math.exp(-e.deltaY * 0.0015));
  }, { passive: false });
  stage.addEventListener("keydown", (e) => {
    const [W, H] = stageSize();
    if (e.key === "+" || e.key === "=") zoomAt(W / 2, H / 2, 1.25);
    else if (e.key === "-") zoomAt(W / 2, H / 2, 0.8);
    else if (e.key === "0") fit(true);
    else if (e.key.startsWith("Arrow")) {
      const d = 60;
      if (e.key === "ArrowLeft") S.view.tx += d;
      if (e.key === "ArrowRight") S.view.tx -= d;
      if (e.key === "ArrowUp") S.view.ty += d;
      if (e.key === "ArrowDown") S.view.ty -= d;
      applyView();
    } else return;
    e.preventDefault();
  });
  $("#z-in").addEventListener("click", () => { const [W, H] = stageSize(); zoomAt(W / 2, H / 2, 1.3); });
  $("#z-out").addEventListener("click", () => { const [W, H] = stageSize(); zoomAt(W / 2, H / 2, 1 / 1.3); });
  $("#z-fit").addEventListener("click", () => fit(true));
  ["#t-pins", "#t-panels", "#t-hidden"].forEach((id) => $(id).addEventListener("change", drawOverlay));
  $("#page-select").addEventListener("change", (e) => loadPage(Number(e.target.value)));
  window.addEventListener("resize", () => { if (S.run && !$("#result").hidden) fit(false); });
}

function zoomAt(cx, cy, factor) {
  const v = S.view;
  const s = Math.max(v.fit * 0.5, Math.min(v.scale * factor, 6));
  const k = s / v.scale;
  v.tx = cx - (cx - v.tx) * k;
  v.ty = cy - (cy - v.ty) * k;
  v.scale = s;
  applyView();
}

function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(`${iso}T00:00:00`);
  return isNaN(d) ? iso : d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

boot();
