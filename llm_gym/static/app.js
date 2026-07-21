// LLM Gym — minimal vanilla-JS frontend. No framework, no innerHTML:
// every node is built with createElement and text goes in via textContent,
// so external data (academic titles, model names) can never inject markup.
"use strict";

const $ = (id) => document.getElementById(id);
const api = async (path, opts) => (await fetch(path, opts)).json();
const post = (path, body) =>
  api(path, { method: "POST", headers: { "Content-Type": "application/json" },
              body: JSON.stringify(body || {}) });

// Tiny safe DOM builder. h("div", {class:"x", onclick:fn}, "text", childNode)
function h(tag, attrs, ...children) {
  const e = document.createElement(tag);
  if (attrs) for (const [k, v] of Object.entries(attrs)) {
    if (v == null) continue;
    if (k === "class") e.className = v;
    else if (k.startsWith("on") && typeof v === "function") e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    e.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return e;
}
const fill = (node, ...kids) => { node.replaceChildren(); kids.flat().forEach((k) => {
  if (k != null && k !== false) node.append(k.nodeType ? k : document.createTextNode(String(k)));
}); };
const badge = (text, cls) => h("span", { class: "badge " + (cls || "") }, tr(text));

let SETTINGS = null;
let CURRENT_POOL = null;

// ---------- navigation (deep-linkable via #hash) ----------
function activateView(view) {
  const a = document.querySelector(`.nav a[data-view="${view}"]`);
  if (!a) return;
  document.querySelectorAll(".nav a").forEach((x) => x.classList.remove("active"));
  document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
  a.classList.add("active");
  $(view).classList.add("active");
  $("topTitle").textContent = a.textContent;
  if (location.hash.slice(1) !== view) location.hash = view;
  if (view === "adapters") loadAdapters();
  if (view === "pool") loadPools();
  if (view === "pipeline") loadPipeline();
  if (view === "settings") fillSettings();
  if (view === "live") startLive(); else stopLive();
}
document.querySelectorAll(".nav a").forEach((a) =>
  a.addEventListener("click", () => activateView(a.dataset.view)));
window.addEventListener("hashchange", () =>
  activateView(location.hash.slice(1) || "dashboard"));

// ---------- dashboard ----------
async function loadSystem() {
  const d = await api("/api/system");
  const s = d.system;
  $("dotOllama").className = "dot " + (d.ollama_up ? "on" : "off");
  $("dotBackend").className = "dot " + (s.backend === "none" ? "warn" : "on");
  $("backendName").textContent = s.backend;

  // Setup banner: show the exact install command for this machine when no
  // real backend is installed yet.
  if (!s.backend_ready) {
    $("setupCard").style.display = "block";
    $("setupCmd").textContent = s.install_cmd;
    $("setupPlatform").textContent = s.apple_silicon ? "Apple Silicon (MLX)"
      : s.cuda ? "your NVIDIA GPU (PEFT)" : "CPU (PEFT)";
  } else {
    $("setupCard").style.display = "none";
  }
  const kv = (k, v) => [h("dt", null, tr(k)), h("dd", null, v)];
  fill($("sysKv"),
    kv("OS / arch", `${s.os} ${s.arch}`), kv("GPU", s.gpu_name),
    kv("Apple Silicon", String(s.apple_silicon)), kv("CUDA", String(s.cuda)),
    kv("Memory budget", `${s.usable_gb} GB (RAM ${s.total_ram_gb} GB)`),
    kv("Backend", s.backend), kv("Ollama installed", String(s.ollama_installed)));
  fill($("sysOptions"), s.options.map((o) => h("tr", null,
    h("td", { class: "mono" }, o.label), h("td", { class: "mono" }, o.min_gb + " GB"),
    h("td", null, o.runnable ? badge("yes", "ok") : badge("no", "bad")),
    h("td", null, o.recommended ? badge("recommended", "ok") : ""))));
  fill($("sysNotes"), s.notes.map((n) => h("div", { class: "notice" }, n)));
}

async function loadModels() {
  const d = await api("/api/ollama/models");
  if (d.ok && d.models.length)
    fill($("ollamaModels"), d.models.map((m) => h("tr", null,
      h("td", { class: "mono" }, m.name), h("td", null, m.family || "-"),
      h("td", { class: "mono" }, m.size_gb + " GB"))));
  else
    fill($("ollamaModels"), h("tr", null,
      h("td", { colspan: "3", class: "muted" }, d.ok ? tr("No models pulled yet.") : d.error)));
}

$("btnPull").addEventListener("click", async () => {
  const model = $("pullModel").value.trim();
  if (!model) return;
  $("btnPull").textContent = "Pulling…";
  await post("/api/ollama/pull", { model });
  $("btnPull").textContent = "Pull";
  loadModels();
});
$("btnRefreshModels").addEventListener("click", loadModels);

// copy the setup command
$("copyCmd").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText($("setupCmd").textContent);
    $("copyCmd").textContent = "Copied";
    setTimeout(() => { $("copyCmd").textContent = "Copy"; }, 1500);
  } catch (e) { alert($("setupCmd").textContent); }
});

// in-page "go to FAQ" style links
document.querySelectorAll("[data-goto]").forEach((el) => {
  el.addEventListener("click", () => {
    const tab = document.querySelector(`.nav a[data-view="${el.dataset.goto}"]`);
    if (tab) tab.click();
  });
});

async function loadQueue() {
  const q = await api("/api/queue");
  const rows = (q.recent || []).map((j) => {
    let info = "";
    try { info = j.result ? (JSON.parse(j.result).verdict || "") : ""; } catch (e) {}
    if (j.status === "pending" && j.run_at) {
      const mins = Math.max(0, Math.round((j.run_at * 1000 - Date.now()) / 60000));
      info = mins > 0 ? `~${mins} ${tr("min")}` : tr("soon");
    }
    const cls = { done: "ok", failed: "bad", running: "warn" }[j.status] || "";
    return h("tr", null,
      h("td", { class: "mono" }, j.id), h("td", null, j.adapter),
      h("td", null, badge(j.status, cls)), h("td", null, info),
      h("td", null, h("button", { class: "ghost", onclick: () => showLog(j.id) }, tr("log"))));
  });
  fill($("queueRows"), rows.length ? rows
    : h("tr", null, h("td", { colspan: "5", class: "muted" }, tr("No jobs yet."))));
}

async function showLog(id) {
  $("logCard").style.display = "block";
  $("logJobId").textContent = "#" + id;
  const d = await api(`/api/queue/${id}/log`);
  $("logBox").textContent = d.log || tr("no log yet");
  $("logCard").scrollIntoView({ behavior: "smooth" });
}

// ---------- adapters ----------
// Configured defaults first, then whatever is actually installed in Ollama
// (the "scanner") — so the dropdown reflects real, servable models, not just
// the two configured sizes. Falls back to the configured-only list if the
// scan fails (Ollama down, etc.) rather than leaving the select empty.
async function setBaseOptions(select, selected) {
  const opts = [SETTINGS.base_model_small, SETTINGS.base_model_large];
  if (SETTINGS.active_base_model && !opts.includes(SETTINGS.active_base_model))
    opts.unshift(SETTINGS.active_base_model);
  try {
    const d = await api("/api/ollama/models");
    for (const m of (d.models || [])) {
      if (m.name && !opts.includes(m.name)) opts.push(m.name);
    }
  } catch (e) { /* Ollama scan unavailable — configured defaults still work */ }
  fill(select, opts.map((o) => {
    const opt = h("option", null, o);
    if (o === selected) opt.selected = true;
    return opt;
  }));
}

async function loadAdapters() {
  if (!SETTINGS) SETTINGS = await api("/api/settings");
  await setBaseOptions($("aBase"), SETTINGS.active_base_model || SETTINGS.base_model_small);
  const d = await api("/api/adapters");
  fill($("adapterRows"), d.adapters.length
    ? d.adapters.map((a) => h("tr", null,
        h("td", null, h("button", { class: "ghost", onclick: () => editAdapter(a.name) }, a.name)),
        h("td", { class: "mono" }, a.base_model), h("td", { class: "mono" }, a.lora_rank),
        h("td", null, a.trained ? badge("yes", "ok") : badge("no"),
          a.champion ? [" ", h("span", { class: "badge", title: `${a.champion.pass_rate}% pass` }, "v" + a.champion.version)] : null, " ",
          h("button", { class: "ghost", title: tr("rename"),
            onclick: () => renameAdapter(a.name) }, tr("rename")), " ",
          a.champion && a.champion.can_rollback
            ? [h("button", { class: "ghost", onclick: () => rollbackAdapter(a.name) }, tr("rollback")), " "] : null,
          h("button", { class: "ghost", style: "color:#a23b2d",
            title: tr("delete"), onclick: () => deleteAdapter(a.name) }, tr("delete")))))
    : h("tr", null, h("td", { colspan: "4", class: "muted" }, tr("No adapters defined yet."))));
}

async function editAdapter(name) {
  const a = await api(`/api/adapters/${name}`);
  $("aName").value = a.name; $("aDesc").value = a.description;
  $("aObjective").value = a.objective;
  $("aCaps").value = (a.capabilities || []).join("\n");
  $("aAccept").value = (a.acceptance_prompts || []).join("\n");
  await setBaseOptions($("aBase"), a.base_model);
  $("aTrainingMode").value = a.training_mode || "lora";
  updateFullWarning();
  $("aPool").value = a.pool; $("aRank").value = a.lora_rank;
  $("aLr").value = a.learning_rate; $("aIters").value = a.iters; $("aDrop").value = a.lora_dropout;
  const b = a.brief || {};
  $("aSubject").value = b.subject || ""; $("aScope").value = b.scope || "";
  $("aBriefQ").value = (b.questions || []).join("\n");
  $("aLangs").value = (b.languages || []).join(", ");
  $("aRecency").value = b.recency_years || 0;
  const c = a.confluence || {};
  $("aConflOn").checked = !!c.enabled; $("aConflUrl").value = c.base_url || "";
  $("aConflAuth").value = c.auth_type || "bearer"; $("aConflEmail").value = c.email || "";
  $("aConflSpace").value = c.space || ""; $("aConflTok").value = "";
  $("aConflToken").style.display = c.has_token ? "inline-block" : "none";
  const j = a.jira || {};
  $("aJiraOn").checked = !!j.enabled; $("aJiraUrl").value = j.base_url || "";
  $("aJiraAuth").value = j.auth_type || "bearer"; $("aJiraEmail").value = j.email || "";
  $("aJiraProject").value = j.project || ""; $("aJiraTok").value = "";
  $("aJiraToken").style.display = j.has_token ? "inline-block" : "none";
  $("adapterFormTitle").textContent = "Edit: " + a.name;
}

async function renameAdapter(name) {
  const next = prompt(tr("Rename adapter — this moves the spec, trained adapter, its pool and history."), name);
  if (next === null) return;
  const clean = next.trim();
  if (!clean || clean === name) return;
  let resp, r;
  try {
    resp = await fetch(`/api/adapters/${encodeURIComponent(name)}/rename`,
      { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ new_name: clean }) });
    r = await resp.json().catch(() => ({}));
  } catch (e) { return alert(tr("Rename failed.")); }
  if (!resp.ok || !r.ok) return alert(r.detail || r.error || tr("Rename failed."));

  const wasPlSelected = (PL.adapter === name);
  if ($("aName").value.trim() === name) editAdapter(clean);   // keep the edit form in sync
  loadAdapters();
  if (typeof loadPools === "function") loadPools();
  // Keep the auto-pipeline view from holding the stale (now-404) name.
  if (typeof loadPipeline === "function") {
    await loadPipeline();
    if (wasPlSelected) { $("plAdapter").value = clean; PL.adapter = clean; refreshPipeline(); }
  }
}

async function rollbackAdapter(name) {
  if (!confirm(tr("Roll back to the previous version?") + "\n" + name)) return;
  const r = await post(`/api/adapters/${encodeURIComponent(name)}/rollback`);
  if (r.ok) { alert(tr("Rolled back to") + " v" + r.champion.version); loadAdapters(); }
  else alert(r.error || tr("Nothing to roll back to."));
}

async function deleteAdapter(name) {
  if (!confirm(`Adapter "${name}" löschen?\nSpec + trainierter Adapter werden entfernt. Pool-Daten bleiben erhalten.`)) return;
  const purge = confirm("Auch die Trainingspool-Daten dieses Adapters löschen?\nOK = Pool mitlöschen · Abbrechen = Pool behalten");
  const r = await (await fetch(`/api/adapters/${name}?purge_pool=${purge}`, { method: "DELETE" })).json();
  if (r.ok) { loadAdapters(); if (typeof loadPools === "function") loadPools(); }
  else alert(r.detail || r.error || "Fehler beim Löschen");
}

function updateFullWarning() {
  $("aFullWarning").style.display = $("aTrainingMode").value === "full" ? "block" : "none";
}
$("aTrainingMode").addEventListener("change", updateFullWarning);

function readAdapterForm() {
  const lines = (id) => $(id).value.split("\n").map((s) => s.trim()).filter(Boolean);
  return {
    name: $("aName").value.trim(), description: $("aDesc").value.trim(),
    objective: $("aObjective").value.trim(),
    capabilities: lines("aCaps"), acceptance_prompts: lines("aAccept"),
    base_model: $("aBase").value, pool: $("aPool").value.trim() || $("aName").value.trim(),
    training_mode: $("aTrainingMode").value,
    lora_rank: parseInt($("aRank").value), learning_rate: parseFloat($("aLr").value),
    iters: parseInt($("aIters").value), lora_dropout: parseFloat($("aDrop").value),
    brief: {
      subject: $("aSubject").value.trim(), scope: $("aScope").value.trim(),
      questions: lines("aBriefQ"),
      languages: $("aLangs").value.split(",").map((s) => s.trim()).filter(Boolean),
      recency_years: parseInt($("aRecency").value) || 0,
    },
    confluence: {
      enabled: $("aConflOn").checked, base_url: $("aConflUrl").value.trim(),
      auth_type: $("aConflAuth").value, email: $("aConflEmail").value.trim(),
      space: $("aConflSpace").value.trim(),
      token: $("aConflTok").value,   // plaintext only if newly typed; server encrypts
    },
    jira: {
      enabled: $("aJiraOn").checked, base_url: $("aJiraUrl").value.trim(),
      auth_type: $("aJiraAuth").value, email: $("aJiraEmail").value.trim(),
      project: $("aJiraProject").value.trim(),
      token: $("aJiraTok").value,
    },
  };
}

// Pull a readable message out of a FastAPI error body (422 = detail is a list of
// {loc,msg}; HTTPException = detail is a string).
function apiError(r) {
  const d = r && r.detail;
  const clean = (s) => String(s || "").replace(/^Value error,\s*/, "");
  if (Array.isArray(d)) return d.map((e) => clean(e.msg)).filter(Boolean).join("; ");
  if (typeof d === "string") return clean(d);
  return "";
}

$("btnSaveAdapter").addEventListener("click", async () => {
  const spec = readAdapterForm();
  if (!spec.name) return alert(tr("Name required."));
  const resp = await fetch("/api/adapters", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(spec) });
  const r = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    return alert(apiError(r) || tr("Could not save the adapter — names may use letters, digits, '.', '_', '-' only."));
  }
  loadAdapters();
  if (typeof loadPipeline === "function") loadPipeline();   // new adapter shows in the pipeline selector too
});

$("btnTrain").addEventListener("click", async () => {
  const name = $("aName").value.trim();
  if (!name) return alert("Save the adapter first.");
  const r = await post(`/api/adapters/${name}/train`);
  alert(r.ok ? `Queued job #${r.job_id}` + (r.starts_in_min ? ` (starts in ~${r.starts_in_min} min, cooldown)` : "")
             : r.error);
});

$("btnTrainDpo").addEventListener("click", async () => {
  const name = $("aName").value.trim();
  if (!name) return alert("Save the adapter first.");
  const r = await post(`/api/adapters/${name}/train-rlhf`);
  alert(r.ok ? `Queued RLHF/DPO job #${r.job_id}` : (r.error || "Failed to queue RLHF training."));
});

$("btnPlan").addEventListener("click", async () => {
  const name = $("aName").value.trim();
  if (!name) return;
  const p = await api(`/api/adapters/${name}/plan`);
  $("planCard").style.display = "block";
  $("planName").textContent = `${p.served_model}  (base ${p.base_model}, ${p.backend})`;
  $("planSteps").textContent = p.steps.join("\n");
  $("planNote").textContent = p.adapter_only_note;
  $("planCard").scrollIntoView({ behavior: "smooth" });
});

$("btnAssign").addEventListener("click", async () => {
  const r = await post(`/api/adapters/${$("aName").value.trim()}/assign`);
  alert(r.ok ? `Created Ollama model "${r.served_model}".` : r.error);
});

// ---------- pool ----------
async function loadPools() {
  const d = await api("/api/pools");
  fill($("poolSelect"), d.pools.length
    ? d.pools.map((p) => h("option", null, p))
    : h("option", { value: "" }, tr("(none yet)")));
  if (d.pools.length) { CURRENT_POOL = $("poolSelect").value; poolStats(); fbStats(); }
}
$("poolSelect").addEventListener("change", () => { CURRENT_POOL = $("poolSelect").value; poolStats(); fbStats(); });
const activePool = () => $("poolNew").value.trim() || $("poolSelect").value || CURRENT_POOL;

async function poolStats() {
  const name = activePool();
  if (!name) return;
  const s = await api(`/api/pool/${name}/stats`);
  const kv = (k, v) => [h("dt", null, tr(k)), h("dd", null, v)];
  fill($("poolStats"),
    kv("pool", s.name), kv("train", s.train), kv("valid", s.valid),
    kv("gold", s.gold), kv("collected", s.collected), kv("raw (uncurated)", s.raw),
    kv("unique", s.unique), kv("duplicates", s.duplicates));
}
$("btnPoolStats").addEventListener("click", poolStats);
$("btnPoolBuild").addEventListener("click", async () => {
  const r = await post(`/api/pool/${activePool()}/build`);
  alert(`merged: ${r.merged}, leakage dropped: ${r.dropped_leakage}`); poolStats();
});
$("btnPoolPush").addEventListener("click", async () => {
  alert((await post(`/api/pool/${activePool()}/push`)).msg);
});

$("btnPoolSearch").addEventListener("click", async () => {
  const d = await api(`/api/pool/${activePool()}/search?q=${encodeURIComponent($("poolQuery").value)}`);
  fill($("poolSearchResults"), d.hits.length
    ? d.hits.map((hit) => h("div", { class: "result-item" },
        h("span", { class: "src" }, `${hit.split}#${hit.line}`),
        h("div", { class: "mono", style: "white-space:pre-wrap;font-size:12px" },
          JSON.stringify(hit.example.messages))))
    : h("p", { class: "muted" }, tr("No matches.")));
});

$("btnPoolAppend").addEventListener("click", async () => {
  const lines = $("poolAppend").value.split("\n").map((s) => s.trim()).filter(Boolean);
  let examples;
  try { examples = lines.map((l) => JSON.parse(l)); }
  catch (e) { return alert("Invalid JSON on one of the lines."); }
  const r = await post(`/api/pool/${activePool()}/append`, { examples, split: $("poolSplit").value });
  alert(`added ${r.added}, skipped ${r.skipped}`); poolStats();
});

// academic search
$("btnAcademic").addEventListener("click", async () => {
  const sources = [...document.querySelectorAll(".acSrc:checked")].map((c) => c.value);
  $("btnAcademic").textContent = "Searching…";
  const d = await post("/api/academic/search", { query: $("acQuery").value, sources, limit: 8 });
  $("btnAcademic").textContent = "Search";
  fill($("acResults"), d.results.length ? d.results.map((p) => p.error
    ? h("div", { class: "result-item muted" }, `${p.source}: ${p.error}`)
    : h("div", { class: "result-item" },
        h("h4", null, p.title),
        h("div", { class: "src" }, `${p.source} · ${p.year || "?"} · ${(p.authors || []).slice(0, 3).join(", ")}`),
        h("p", { style: "font-size:12.5px" }, (p.abstract || "").slice(0, 280) + ((p.abstract || "").length > 280 ? "…" : "")),
        h("button", { class: "ghost", onclick: () => addCandidate(p) }, "Add as candidate")))
    : h("p", { class: "muted" }, tr("No results.")));
});

async function addCandidate(p) {
  // OpenAlex / Semantic Scholar hits often have no abstract; the backend rejects
  // an example with an empty answer (added 0) — say why here instead of a silent
  // no-op that looks like a bug.
  if (!(p.abstract || "").trim()) {
    alert("This result has no abstract, so there's no answer text to train on — skipped. Pick a result that has an abstract.");
    return;
  }
  const example = {
    messages: [
      { role: "user", content: "Summarise the key contribution of: " + p.title },
      { role: "assistant", content: p.abstract || "" },
    ],
    _meta: { source: "academic:" + p.source, verified: false, quality_score: 50, citation: p.url },
  };
  const r = await post(`/api/pool/${activePool()}/append`, { examples: [example], split: "raw" });
  alert(`Added candidate to raw/ (added ${r.added}). Curate + verify before promoting to gold.`);
}

// gold
$("btnGold").addEventListener("click", async () => {
  const r = await post(`/api/pool/${activePool()}/gold`, {
    pool: activePool(), problem_prompt: $("goldPrompt").value,
    solution_text: $("goldSolution").value,
    executed_ok: $("goldExec").checked, verified: $("goldVerified").checked,
  });
  alert(r.ok ? `Added gold pair (added ${r.added}).` : r.reason);
  poolStats();
});

// human feedback -> RLHF (DPO) preference pairs
async function fbStats() {
  const name = activePool();
  if (!name) return;
  const s = await api(`/api/pool/${name}/feedback`);
  const kv = (k, v) => [h("dt", null, tr(k)), h("dd", null, v)];
  const bySource = Object.entries(s.by_source || {}).map(([k, v]) => `${k}: ${v}`).join(", ") || "—";
  fill($("fbStats"), kv("preference pairs", s.total), kv("by source", bySource));
}
$("btnFbSubmit").addEventListener("click", async () => {
  const r = await post(`/api/pool/${activePool()}/feedback`, {
    prompt: $("fbPrompt").value, chosen: $("fbChosen").value, rejected: $("fbRejected").value,
  });
  alert(r.ok ? "Preference pair recorded." : (r.reason || "Failed to record pair."));
  if (r.ok) { $("fbPrompt").value = ""; $("fbChosen").value = ""; $("fbRejected").value = ""; }
  fbStats();
});
$("btnFbFromVerify").addEventListener("click", async () => {
  const r = await post(`/api/pool/${activePool()}/feedback/from-verify`);
  alert(`Derived ${r.added} new pair(s) from ${r.candidates} gold/verify-failure match(es).`);
  fbStats();
});

// ---------- settings ----------
async function fillSettings() {
  SETTINGS = await api("/api/settings");
  const s = SETTINGS;
  $("sOllama").value = s.ollama_host; $("sSmall").value = s.base_model_small;
  $("sLarge").value = s.base_model_large; $("sActive").value = s.active_base_model;
  $("sBackend").value = s.backend;
  $("sCdSeed").value = s.cooldown.seed_minutes; $("sCdFull").value = s.cooldown.full_minutes;
  $("sCdMax").value = s.cooldown.max_minutes;
  $("sRank").value = s.training.lora_rank; $("sIters").value = s.training.iters;
  $("sLr").value = s.training.learning_rate;
  if ($("sProfile")) $("sProfile").value = s.train_profile || "balanced";
  if ($("sPolicy")) $("sPolicy").value = s.pipeline_policy || "auto_verygood";
  if ($("sContinuous")) $("sContinuous").checked = !!s.continuous_enabled;
  if ($("sContMin")) $("sContMin").value = s.continuous_min_new_gold;
  $("sGitRemote").value = s.git.remote; $("sGitBranch").value = s.git.branch;
  $("sColPages").value = s.collector.max_pages; $("sColSecs").value = s.collector.max_seconds;
  $("sColRate").value = s.collector.rate_limit_seconds;
  $("sColProvider").value = s.collector.web_search; $("sColSearx").value = s.collector.searx_url;
  $("sJudgeMode").value = s.judge.mode; $("sJudgeModel").value = s.judge.ollama_model;
  $("sJudgeEval").value = s.judge.eval_model || "";
  $("sJudgeUrl").value = s.judge.public_base_url; $("sJudgepublic").value = s.judge.public_model;
  $("sRlhfEnabled").checked = !!s.rlhf.enabled; $("sRlhfBeta").value = s.rlhf.beta;
  $("sRlhfLr").value = s.rlhf.learning_rate; $("sRlhfIters").value = s.rlhf.iters;
  $("sRlhfMinPairs").value = s.rlhf.min_pairs; $("sRlhfAutoPairs").checked = !!s.rlhf.auto_pairs_from_verify;
}

$("btnSaveSettings").addEventListener("click", async () => {
  const payload = {
    ollama_host: $("sOllama").value, base_model_small: $("sSmall").value,
    base_model_large: $("sLarge").value, active_base_model: $("sActive").value,
    backend: $("sBackend").value,
    train_profile: $("sProfile").value, pipeline_policy: $("sPolicy").value,
    continuous_enabled: $("sContinuous").checked, continuous_min_new_gold: +$("sContMin").value,
    cooldown: { ...SETTINGS.cooldown, seed_minutes: +$("sCdSeed").value,
      full_minutes: +$("sCdFull").value, max_minutes: +$("sCdMax").value },
    training: { ...SETTINGS.training, lora_rank: +$("sRank").value,
      iters: +$("sIters").value, learning_rate: parseFloat($("sLr").value) },
    git: { ...SETTINGS.git, remote: $("sGitRemote").value, branch: $("sGitBranch").value },
    collector: { ...SETTINGS.collector, max_pages: +$("sColPages").value,
      max_seconds: +$("sColSecs").value, rate_limit_seconds: parseFloat($("sColRate").value),
      web_search: $("sColProvider").value, searx_url: $("sColSearx").value },
    judge: { ...SETTINGS.judge, mode: $("sJudgeMode").value, ollama_model: $("sJudgeModel").value,
      eval_model: $("sJudgeEval").value, public_base_url: $("sJudgeUrl").value,
      public_model: $("sJudgepublic").value },
    rlhf: { ...SETTINGS.rlhf, enabled: $("sRlhfEnabled").checked, beta: parseFloat($("sRlhfBeta").value),
      learning_rate: parseFloat($("sRlhfLr").value), iters: +$("sRlhfIters").value,
      min_pairs: +$("sRlhfMinPairs").value, auto_pairs_from_verify: $("sRlhfAutoPairs").checked },
  };
  SETTINGS = (await post("/api/settings", payload)).settings;
  alert("Saved.");
});

// ---------- auto-pipeline ----------
const PL = { adapter: null, timer: null, collectTimer: null };
const STEP_LABELS = { collect: "Collect", analyse: "Analyse", gate_tier: "Tier",
  train: "Train", check: "Check", verify: "Verify", cooldown: "Cooldown",
  gate_promote: "Promote", done: "Done" };

async function loadPipeline() {
  const d = await api("/api/adapters");
  fill($("plAdapter"), d.adapters.length
    ? d.adapters.map((a) => h("option", null, a.name))
    : h("option", { value: "" }, tr("(define an adapter first)")));
  PL.adapter = $("plAdapter").value || null;
  resetPipelineUI();
  if (PL.adapter) refreshPipeline();
}
$("plAdapter").addEventListener("change", () => {
  PL.adapter = $("plAdapter").value; resetPipelineUI(); refreshPipeline();
});

function resetPipelineUI() {
  ["plGateTier", "plAnalysisCard", "plVerifyCard", "plCooldownCard",
   "plGatePromote", "plLogCard"].forEach((id) => { $(id).style.display = "none"; });
}

$("plRun").addEventListener("click", async () => {
  if (!PL.adapter) return;
  const r = await post(`/api/adapters/${PL.adapter}/pipeline/start`);
  if (!r.ok) return alert(r.error);
  startPipelinePolling();
});
$("plCollect").addEventListener("click", async () => {
  if (!PL.adapter) return;
  const topic = prompt(
    "Thema für die intensive Recherche?\n(leer lassen = aus dem Adapter ableiten)", "") || "";
  const r = await post(`/api/adapters/${PL.adapter}/collect?topic=${encodeURIComponent(topic)}`);
  if (!r.ok) return alert(r.error);
  $("plLogCard").style.display = "block";
  pollCollect();
});
$("plAnalyse").addEventListener("click", async () => {
  if (!PL.adapter) return;
  renderAnalysis(await api(`/api/adapters/${PL.adapter}/analysis`));
});
$("plVerify").addEventListener("click", async () => {
  if (!PL.adapter) return;
  $("plVerifyCard").style.display = "block";
  $("plVerdict").textContent = tr("running…");
  renderVerify(await post(`/api/adapters/${PL.adapter}/verify`));
});

$("plAdvise").addEventListener("click", async () => {
  if (!PL.adapter) return;
  PL.topic = prompt(
    "Thema für diesen Lauf? (leer = den gespeicherten Research-Brief des Adapters nutzen)",
    PL.topic || "") || "";
  $("plAdviseCard").style.display = "block";
  $("plAdviseEngine").textContent = tr("thinking… (local LLM, may take ~20s)");
  PL.plan = await post(`/api/adapters/${PL.adapter}/advise`, { topic: PL.topic });
  renderAdvice(PL.plan);
});

$("plClarifyPlan").addEventListener("click", async () => {
  const answers = [...document.querySelectorAll(".clarifyA")].map((i) => i.value.trim());
  $("plAdviseEngine").textContent = "plant mit deinen Antworten…";
  PL.plan = await post(`/api/adapters/${PL.adapter}/advise`, { topic: PL.topic, answers });
  renderAdvice(PL.plan);
});

$("plClarifySave").addEventListener("click", async () => {
  const answers = [...document.querySelectorAll(".clarifyA")].map((i) => i.value.trim());
  const spec = await api(`/api/adapters/${PL.adapter}`);
  spec.brief = spec.brief || {};
  if (!spec.brief.subject && answers[0]) spec.brief.subject = answers[0];
  spec.brief.scope = [spec.brief.scope, ...answers.slice(1)].filter(Boolean).join(" · ");
  await post("/api/adapters", spec);
  alert("In den Brief des Adapters übernommen. Recommend/Collect nutzen ihn jetzt dauerhaft.");
  PL.plan = await post(`/api/adapters/${PL.adapter}/advise`, { topic: PL.topic });
  renderAdvice(PL.plan);
});

function renderAdvice(p) {
  $("plAdviseCard").style.display = "block";
  $("plAdviseEngine").textContent = p.engine || "";
  // Thin brief -> show clarifying questions instead of a (guessed) plan.
  if (p.needs_clarification) {
    $("plClarify").style.display = "block";
    fill($("plClarifyQs"), (p.clarifying_questions || []).map((q) =>
      h("div", { style: "margin:6px 0" },
        h("label", null, q),
        h("input", { class: "clarifyA", placeholder: "deine Antwort…" }))));
  } else {
    $("plClarify").style.display = "none";
  }
  const kv = (k, v) => [h("dt", null, tr(k)), h("dd", null, v)];
  fill($("plAdviseHead"),
    kv("domain", p.domain || "-"),
    kv("sources", (p.source_categories || []).join(", ")),
    kv("recommended tier", p.min_tier || "-"));
  fill($("plAdviseEmphasis"), (p.emphasis || []).length
    ? h("ul", null, (p.emphasis || []).map((e) => h("li", null, e)))
    : h("p", { class: "muted" }, "—"));
  fill($("plAdviseQueries"), (p.queries || []).map((q) => h("li", null, q)));
  const caps = p.suggested_capabilities || [];
  const prompts = p.suggested_acceptance_prompts || [];
  fill($("plAdviseSuggest"),
    h("p", { class: "muted", style: "margin:2px 0" }, `${caps.length} capabilities, ${prompts.length} acceptance prompts`),
    h("div", { class: "muted", style: "font-size:11px" }, p.rationale || ""));
}

$("plApplySuggest").addEventListener("click", async () => {
  if (!PL.plan || !PL.adapter) return;
  const spec = await api(`/api/adapters/${PL.adapter}`);
  const merge = (a, b) => [...new Set([...(a || []), ...(b || [])])];
  spec.capabilities = merge(spec.capabilities, PL.plan.suggested_capabilities);
  spec.acceptance_prompts = merge(spec.acceptance_prompts, PL.plan.suggested_acceptance_prompts);
  if (PL.plan.min_tier) spec.min_tier = PL.plan.min_tier;
  await post("/api/adapters", spec);
  alert("Applied: capabilities + acceptance prompts merged into the adapter, tier set to " + (PL.plan.min_tier || spec.min_tier) + ".");
});

$("plSmartCollect").addEventListener("click", async () => {
  if (!PL.adapter) return;
  const t = encodeURIComponent(PL.topic || "");
  const r = await post(`/api/adapters/${PL.adapter}/collect?advise=true&topic=${t}`);
  if (!r.ok) return alert(r.error);
  $("plLogCard").style.display = "block";
  pollCollect();
});

function startPipelinePolling() {
  if (PL.timer) clearInterval(PL.timer);
  PL.timer = setInterval(refreshPipeline, 3000);
  refreshPipeline();
}

async function refreshPipeline() {
  if (!PL.adapter) return;
  const st = await api(`/api/adapters/${PL.adapter}/pipeline`);
  renderStepper(st);
  const data = st.data || {};
  if (data.log) { $("plLogCard").style.display = "block"; $("plLog").textContent = data.log.join("\n"); }
  if (data.analysis) renderAnalysis(data.analysis);
  $("plGateTier").style.display = (st.status === "waiting" && st.stage === "gate_tier") ? "block" : "none";
  if (data.verify) renderVerify(data.verify, data);
  if (data.cooldown) renderCooldown(data.cooldown);
  $("plGatePromote").style.display = (st.status === "waiting" && st.stage === "gate_promote") ? "block" : "none";
  if (["done", "failed", "idle"].includes(st.status) || !st.stage) {
    if (PL.timer) { clearInterval(PL.timer); PL.timer = null; }
  }
}

function renderStepper(st) {
  const stages = st.stages || Object.keys(STEP_LABELS);
  const cur = stages.indexOf(st.stage);
  fill($("plStepper"), stages.map((s, i) => {
    let cls = "step-chip";
    if (st.stage === s && st.status === "waiting") cls += " wait";
    else if (st.stage === s && st.status !== "done") cls += " active";
    else if (i < cur || st.status === "done") cls += " done";
    return h("div", { class: cls }, h("span", { class: "n" }, i + 1), tr(STEP_LABELS[s] || s));
  }));
  $("plMeta").textContent = st.stage ? `${tr("stage")}: ${tr(st.stage)} · ${tr("status")}: ${tr(st.status)}` : tr("not started");
}

function renderAnalysis(rep) {
  $("plAnalysisCard").style.display = "block";
  $("plGrade").textContent = "grade " + rep.pool_grade;
  const tiers = rep.tiers || {};
  const maxT = Math.max(1, ...Object.values(tiers));
  fill($("plTiers"), ["platin", "gold", "silver", "bronze"].map((t) =>
    h("div", { class: `tierbar t-${t}` },
      h("span", { class: "lbl" }, t),
      h("div", { class: "track" }, h("div", { class: "fill",
        style: `width:${Math.round(100 * (tiers[t] || 0) / maxT)}%` })),
      h("span", { class: "num" }, tiers[t] || 0))));
  const f = rep.forecast || {}; const t = rep.totals || {};
  const kv = (k, v) => [h("dt", null, tr(k)), h("dd", null, v)];
  fill($("plForecast"),
    kv("expected", f.expected_verdict || "-"),
    kv("val loss ~", f.val_loss_low != null ? `${f.val_loss_low}–${f.val_loss_high}` : "-"),
    kv("confidence", f.confidence || "-"),
    kv("usable @tier", t.usable_at_tier ?? "-"));
  const cov = rep.coverage || [];
  const maxC = Math.max(1, ...cov.map((c) => c.examples));
  fill($("plCoverage"), cov.length ? cov.map((c) => h("div", { class: "covbar" },
    h("div", null, h("div", { style: "font-size:11px" }, c.capability),
      h("div", { class: "track" }, h("div", { class: "fill",
        style: `width:${Math.round(100 * c.examples / maxC)}%` }))),
    h("span", { class: "num" }, c.examples)))
    : h("p", { class: "muted" }, "No capabilities listed on the adapter."));
  fill($("plPoolStats"),
    kv("total / unique", `${t.all} / ${t.unique}`),
    kv("duplicates", `${t.duplicates} (${Math.round((t.duplicate_ratio || 0) * 100)}%)`),
    kv("gold / train / collected", `${t.gold} / ${t.train} / ${t.collected}`),
    kv("valid (holdout)", t.valid),
    kv("with citation", rep.citation_pct + "%"),
    kv("verified", rep.verified_pct + "%"),
    kv("avg relevance", rep.avg_relevance));
}

const EVAL_NOTE = {
  trained: "answered by the assigned adapter",
  base: "answered by the base model (un-adapted baseline)",
  override: "answered by the configured eval model",
  mlx: "answered by the local MLX adapter",
  unavailable: "no eval model available — score is not meaningful",
};

function renderVerify(v, pipelineData) {
  $("plVerifyCard").style.display = "block";
  let verdictTxt = `${v.pass_rate}% pass · avg ${v.avg_score}`;
  // Same fairness check as the list view: a good-looking score can still be worse
  // than doing nothing (the untrained base model) — surface it here too so the
  // detail view can never show a silently-missing regression the list view catches.
  if (pipelineData && pipelineData.worse_than_base) {
    verdictTxt += `  ⚠ ${tr("WORSE THAN BASE MODEL")} (${tr("base")} ${pipelineData.base_avg}, ` +
      `${pipelineData.delta_vs_base >= 0 ? "+" : ""}${pipelineData.delta_vs_base}) — ` +
      tr("training hurt this adapter");
  }
  $("plVerdict").textContent = verdictTxt;
  // Same "why can't I promote this" gap as the main dashboard — sec_block/
  // head_loss were computed server-side but never shown anywhere near the
  // Promote/Discard buttons, so the choice looked unexplained.
  const reasons = [];
  if (pipelineData && pipelineData.sec_block) {
    reasons.push("🛡 " + tr("Cannot promote — this adapter fails the security check") +
      ": " + (pipelineData.sec_why || ""));
  }
  if (pipelineData && pipelineData.head_loss) {
    reasons.push("⚠ " + tr("Cannot promote — this candidate loses on the frozen eval set"));
  }
  $("plGateReasons").textContent = reasons.join(" · ");
  $("plGateReasons").style.display = reasons.length ? "block" : "none";
  const evalNote = v.eval_mode
    ? ` · ${tr(EVAL_NOTE[v.eval_mode] || v.eval_mode)}${v.eval_model ? " (" + v.eval_model + ")" : ""}`
    : "";
  $("plVerifyMeta").textContent = `${v.overall} (${tr("judge")}: ${v.judge})${evalNote}`;
  fill($("plVerifyItems"), (v.items || []).map((it) => h("div", { class: "vitem" },
    h("div", { class: "q" }, badge(it.passed ? "PASS" : "FAIL", it.passed ? "ok" : "bad"), " ", it.prompt),
    h("div", { class: "a" }, it.answer),
    h("div", { class: "muted", style: "font-size:11px" }, `score ${it.score} — ${it.reason}`))));
}

function renderCooldown(cd) {
  $("plCooldownCard").style.display = "block";
  const until = cd.cooldown_until ? new Date(cd.cooldown_until * 1000).toLocaleString() : "-";
  const kv = (k, v) => [h("dt", null, tr(k)), h("dd", null, v)];
  fill($("plCooldown"), kv("recommended", (cd.optimal_cooldown_min || 0) + " min"),
    kv("locked until", until));
}

// Promote in particular can take minutes (it runs the full fuse->GGUF->
// quantize->ollama deploy synchronously before the request resolves) with the
// card looking completely unchanged the whole time -- no spinner, no disabled
// state. Found live: a user re-clicked "Promote" repeatedly thinking nothing
// had happened, when the first click was already being processed. Disable the
// row and say so instead of leaving a live-looking button to re-click.
async function runGateAction(container, clicked, body) {
  const buttons = [...container.querySelectorAll("button[data-tier], button[data-promote]")];
  const original = clicked.textContent;
  buttons.forEach((b) => { b.disabled = true; });
  clicked.textContent = tr("Working — this can take a few minutes...");
  try {
    await post(`/api/adapters/${PL.adapter}/pipeline/gate`, body);
    startPipelinePolling();
  } finally {
    buttons.forEach((b) => { b.disabled = false; });
    clicked.textContent = original;
  }
}
document.querySelectorAll("#plGateTier button[data-tier]").forEach((b) =>
  b.addEventListener("click", () => runGateAction($("plGateTier"), b, { tier: b.dataset.tier })));
document.querySelectorAll("#plGatePromote button[data-promote]").forEach((b) =>
  b.addEventListener("click", () =>
    runGateAction($("plGatePromote"), b, { promote: b.dataset.promote === "yes" })));

async function pollCollect() {
  if (PL.collectTimer) clearInterval(PL.collectTimer);
  const tick = async () => {
    const st = await api(`/api/adapters/${PL.adapter}/collect/status`);
    $("plLog").textContent = st.log || "(starting…)";
    if (st.state !== "running") {
      clearInterval(PL.collectTimer); PL.collectTimer = null;
      renderAnalysis(await api(`/api/adapters/${PL.adapter}/analysis`));
    }
  };
  PL.collectTimer = setInterval(tick, 2000); tick();
}

// ---------- live ----------
const BAND_LABEL = { sehr_gut: "very good", gut: "good", schlecht: "weak" };

// Green "Approve" + red "Discard + collect more" at an open promote gate. The
// recommended action (from the verdict) is the filled button, the other a ghost.
function gateButtons(pl) {
  const rec = pl.recommended;
  const approve = h("button", { class: rec === "promote" ? "btn-go" : "ghost",
    onclick: () => gatePromote(pl.adapter, true) }, tr("Approve"));
  const discard = h("button", { class: rec === "recollect" ? "btn-stop" : "ghost",
    onclick: () => gatePromote(pl.adapter, false) }, tr("Discard + collect more"));
  return h("div", { class: "gate-btns" }, approve, " ", discard);
}

async function gatePromote(adapter, promote) {
  const verb = promote ? tr("approve") : tr("discard");
  if (!confirm(`${adapter}: ${verb}?`)) return;
  await post(`/api/adapters/${encodeURIComponent(adapter)}/pipeline/gate`, { promote });
  loadLive();
}

const LIVE = { timer: null };

function startLive() {
  if (LIVE.timer) return;
  loadLive();
  LIVE.timer = setInterval(loadLive, 1500);
}
function stopLive() {
  if (LIVE.timer) { clearInterval(LIVE.timer); LIVE.timer = null; }
}

const fmtDur = (s) => {
  s = Math.max(0, Math.floor(s || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};
const ago = (epoch) => {
  if (!epoch) return "—";
  const s = Math.floor(Date.now() / 1000 - epoch);
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  return `${Math.floor(s / 3600)}h`;
};
const pill = (label, value, cls) =>
  h("span", { class: "pill " + (cls || "") }, h("b", null, tr(label) + ": "), String(value));

// Turns /api/live's gpu_holder into a readable line for a "waiting for GPU"
// card: who holds it, what they're doing, and (for red-team, the one stage
// that can run 2+ hours) live progress with a self-correcting ETA measured
// from this run's own actual throughput so far.
const GPU_HOLDER_KIND_LABEL = {
  "llm-gym": "training",
  "llm-gym-pipeline-check": "running acceptance prompts",
  "llm-gym-pipeline-verify": "judging",
  "llm-gym-pipeline-h2h": "head-to-head eval",
  "llm-gym-pipeline-redteam": "security red-team",
  "llm-gym-deploy": "deploying",
};

function gpuHolderLine(holder) {
  if (!holder) return tr("another adapter is still training/verifying. Not stuck.");
  const kindLabel = tr(GPU_HOLDER_KIND_LABEL[holder.kind] || holder.info || holder.kind);
  let line = `${holder.adapter} — ${kindLabel} (⏱ ${fmtDur(holder.elapsed_s)})`;
  const p = holder.progress;
  if (p && p.total) {
    const stepsDone = p.phase === "grade" ? p.total + p.done : p.done;
    const stepsTotal = p.total * 2;
    const phaseLabel = tr(p.phase === "grade" ? "grading" : "generating");
    line += ` — ${phaseLabel} ${p.done}/${p.total}`;
    if (stepsDone > 0) {
      const elapsed = Date.now() / 1000 - p.started;
      const remaining = Math.max(0, Math.round((elapsed / stepsDone) * (stepsTotal - stepsDone)));
      line += ` (~${fmtDur(remaining)} ${tr("remaining")})`;
    }
  }
  return line;
}

async function loadLive() {
  let d;
  try { d = await api("/api/live"); } catch (e) { return; }
  const w = d.where || {};
  const busy = !!d.training || (d.pipelines || []).length || (d.collections || []).length;
  $("liveDot").className = "dot " + (busy ? "on" : "off");
  fill($("liveWhere"),
    pill("host", w.host || "?"),
    pill("backend", (w.backend || "?") + (w.backend_ready ? "" : " · " + tr("simulate")), w.backend_ready ? "ok" : "warn"),
    pill("device", w.device || "?"),
    pill("memory", (w.usable_gb || 0) + " GB"),
    pill("Ollama", w.ollama_up ? tr("up") : tr("down"), w.ollama_up ? "ok" : "bad"));

  const t = d.training;
  if (t) {
    const p = t.progress || {};
    // remember the live log's scroll state before we re-render it
    const _oldLog = $("liveTrain").querySelector(".logbox");
    let _stick = true, _prevTop = 0;
    if (_oldLog) {
      _prevTop = _oldLog.scrollTop;
      _stick = (_oldLog.scrollHeight - _oldLog.scrollTop - _oldLog.clientHeight) < 40;
    }
    // "running" at the DB level starts the instant _run() begins — well before
    // the shared GPU lease is actually granted. Without this, a job queued
    // behind another adapter's long verify/red-team stage shows "running ⏱
    // 117:36" with zero iterations — indistinguishable from a genuine hang.
    // waiting_for_gpu (computed backend-side against the real lease holder)
    // makes that distinction visible instead of leaving the user to guess.
    fill($("liveTrain"),
      h("div", { class: "live-head" }, h("strong", null, t.adapter),
        h("span", { class: "muted" }, ` ${tr("trains on")} ${w.host} · ${t.backend} · ${w.device}`)),
      t.waiting_for_gpu
        ? h("div", { class: "verdict-headline warn" },
            "⏳ " + tr("Waiting for the GPU") + " — " + gpuHolderLine(d.gpu_holder))
        : null,
      h("div", { class: "live-meta" }, badge(t.waiting_for_gpu ? "waiting for GPU" : "running", t.waiting_for_gpu ? "warn" : "ok"), " ",
        h("span", { class: "mono" }, `⏱ ${fmtDur(t.elapsed_s)}`),
        p.iter != null ? h("span", { class: "mono" }, `  iter ${p.iter}/${p.total != null ? p.total : "?"}`) : null,
        p.loss != null ? h("span", { class: "mono" }, `  loss ${p.loss}`) : null),
      p.pct != null ? h("div", { class: "bar" }, h("div", { class: "barfill", style: `width:${p.pct}%` })) : null,
      h("pre", { class: "logbox" }, t.log_tail || ""));
    // follow the tail by default; keep the user's position if they scrolled up
    const _newLog = $("liveTrain").querySelector(".logbox");
    if (_newLog) _newLog.scrollTop = _stick ? _newLog.scrollHeight : _prevTop;
  } else {
    fill($("liveTrain"), h("div", { class: "muted" }, tr("No training running.")));
  }

  const acts = [];
  (d.pipelines || []).forEach((pl) => {
    let st, sub = "", gate = null, headline = null;
    if (pl.status === "running") { st = badge("active", "ok"); sub = pl.last || ""; }
    else if (pl.status === "awaiting_train") { st = badge("training (GPU queue)", "ok"); sub = pl.last || ""; }
    else if (pl.status === "waiting") {
      st = badge("waiting for approval", "warn");
      const v = pl.verify;
      // The band label alone ("very good"/"good") is an ABSOLUTE quality score — it
      // says nothing about whether TRAINING was worth it. A "very good" adapter that
      // still does worse than the untrained base model is a bad outcome dressed up as
      // a good one. Found live: a card read "sehr gut · 100% pass · avg 76.2" with a
      // red discard button and no explanation at all — the base model scored 89.4 on
      // the identical test, so training made this WORSE, not better, yet the loudest
      // text on screen said "very good". Fix: the headline itself must say the true
      // verdict in plain language — no digging into raw numbers required to see it.
      if (pl.sec_block) {
        headline = h("div", { class: "verdict-headline bad" },
          "🛡 " + tr("Cannot promote — this adapter fails the security check"));
        sub = tr("{reason}. A leaky adapter is never auto-shipped; discard and improve the data before trying again.")
          .replace("{reason}", pl.sec_why || "");
      } else if (pl.head_loss) {
        headline = h("div", { class: "verdict-headline bad" },
          "⚠ " + tr("Cannot promote — this candidate loses on the frozen eval set"));
        sub = tr(pl.head_loss_why === "fails to beat the base model"
          ? "It fails to beat the untrained base model on held-out questions."
          : "It loses to the currently live version on held-out questions.");
      } else if (pl.worse_than_base) {
        headline = h("div", { class: "verdict-headline bad" },
          "❌ " + tr("This training made the adapter WORSE, not better"));
        sub = tr("The untrained base model already scores {base} on this exact test — this trained version only reaches {trained}. Discard it and collect more/better data.")
          .replace("{base}", pl.base_avg).replace("{trained}", v ? v.avg : "?");
      } else if (pl.regression) {
        headline = h("div", { class: "verdict-headline bad" },
          "⚠ " + tr("This training is worse than the version already live — do not promote it"));
        sub = v && v.pass_rate != null
          ? `${tr(BAND_LABEL[pl.band] || pl.band || "")} · ${v.pass_rate}% ${tr("pass")} · avg ${v.avg}`
          : "";
      } else if (v && v.pass_rate != null) {
        const bandCls = { sehr_gut: "ok", gut: "ok", schlecht: "bad" }[pl.band] || "warn";
        headline = h("div", { class: `verdict-headline ${bandCls}` },
          tr(BAND_LABEL[pl.band] || pl.band || ""));
        sub = `${v.pass_rate}% ${tr("pass")} · avg ${v.avg}` +
          (pl.base_avg != null ? ` · ${tr("base model")} ${pl.base_avg}` : "");
      } else {
        sub = tr("waiting for your decision");
      }
      gate = gateButtons(pl);
    }
    else { st = badge("queued"); }
    const head = [h("strong", null, pl.adapter), " ", badge(STEP_LABELS[pl.stage] || pl.stage), " ", st];
    if (pl.status === "queued" && pl.position) head.push(" ", h("span", { class: "muted small" }, "#" + pl.position));
    acts.push(h("div", { class: "live-row" + (pl.status === "running" ? " live-active" : "") },
      h("div", null, head), headline, sub ? h("div", { class: "muted mono small" }, sub) : null, gate));
  });
  (d.collections || []).forEach((c) => acts.push(h("div", { class: "live-row" },
    h("strong", null, c.adapter), " ", badge("collecting", "ok"),
    h("div", { class: "muted mono small" }, c.last || ""))));
  fill($("liveActivity"), acts.length ? acts : h("div", { class: "muted" }, tr("Nothing collecting right now.")));

  const runs = d.recent_runs || [];
  fill($("liveRuns"), runs.length
    ? runs.map((r) => h("tr", null,
        h("td", null, "#" + r.id), h("td", null, r.adapter),
        h("td", null, badge(r.status, r.status === "done" ? "ok" : r.status === "failed" ? "bad" : "")),
        h("td", null, r.status === "done" ? verdictBadge(r.verdict, r.val_loss) : "—"),
        h("td", null, verifyCell(r.verify)),
        h("td", { class: "muted" }, ago(r.when))))
    : h("tr", null, h("td", { colspan: "6", class: "muted" }, tr("No jobs yet."))));
}

// Short, colour-coded training verdict (full text + val_loss on hover).
function verdictBadge(verdict, valLoss) {
  if (!verdict) return "—";
  const head = verdict.split(" — ")[0].split(".")[0].trim();
  const cls = /good|solid|match|ship/i.test(head) ? "ok"
    : /weak|partly/i.test(head) ? "warn"
    : /overfit|broken|fail/i.test(head) ? "bad" : "";
  const title = verdict + (valLoss != null ? `  ·  val_loss ${valLoss}` : "");
  return h("span", { class: "badge " + cls, title }, head);
}

// Verify pass-rate / avg, coloured by pass-rate, with the eval mode on hover.
function verifyCell(vf) {
  if (!vf || vf.pass_rate == null) return "—";
  const cls = vf.pass_rate >= 80 ? "ok" : vf.pass_rate >= 50 ? "warn" : "bad";
  return h("span", { class: "badge " + cls, title: tr(EVAL_NOTE[vf.mode] || vf.mode || "") },
    `${vf.pass_rate}% · ${vf.avg}`);
}

// ---------- boot + polling ----------
(async function init() {
  SETTINGS = await api("/api/settings");
  loadSystem(); loadModels(); loadQueue();
  setInterval(loadQueue, 4000);
  const start = location.hash.slice(1);
  if (start) activateView(start);
})();
