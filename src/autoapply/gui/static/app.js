"use strict";

// ---------------------------------------------------------------- plumbing

const params = new URLSearchParams(location.search);
const TOKEN = params.get("token") || sessionStorage.getItem("autoapply-token") || "";
sessionStorage.setItem("autoapply-token", TOKEN);
if (params.has("token")) history.replaceState(null, "", "/");

async function api(path, { method = "GET", body, raw } = {}) {
  const headers = { "X-Autoapply-Token": TOKEN };
  let payload;
  if (raw !== undefined) payload = raw;
  else if (body !== undefined) { headers["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
  const res = await fetch(path, { method, headers, body: payload });
  let data = {};
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

const $ = (sel) => document.querySelector(sel);

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === false || v == null) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k in el && typeof v !== "string") el[k] = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null && c !== false) el.append(c instanceof Node ? c : String(c));
  return el;
}

let toastTimer;
function toast(message, isError = false) {
  const t = $("#toast");
  t.textContent = message;
  t.className = "toast" + (isError ? " error" : "");
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), isError ? 9000 : 4000);
}

async function busy(button, work) {
  button.disabled = true;
  try { return await work(); }
  catch (e) { toast(e.message, true); }
  finally { button.disabled = false; }
}

// ---------------------------------------------------------------- navigation

const loaded = new Set();
function show(view) {
  document.querySelectorAll(".view").forEach((v) => (v.hidden = v.id !== `view-${view}`));
  document.querySelectorAll(".nav").forEach((b) =>
    b.dataset.view === view ? b.setAttribute("aria-current", "page") : b.removeAttribute("aria-current"));
  if (!loaded.has(view)) {
    loaded.add(view);
    if (view === "profile") loadProfile();
    if (view === "settings") loadSettings();
  }
}
document.querySelectorAll(".nav").forEach((b) => b.addEventListener("click", () => show(b.dataset.view)));

// ---------------------------------------------------------------- postings

const STATUS_LABEL = {
  discovered: "To review", queued: "Queued", in_progress: "Applying", pending_input: "Waiting on you",
  applied: "Applied", failed: "Failed", skipped: "Skipped",
};
const QUEUEABLE = new Set(["discovered", "queued", "failed", "skipped", "pending_input"]);
let jobs = [];

async function loadJobs() {
  try { jobs = (await api("/api/jobs")).jobs; renderJobs(); }
  catch (e) { toast(e.message, true); }
}

function setStatus(ids, status) {
  return api("/api/jobs/status", { method: "POST", body: { ids, status } });
}

function renderJobs() {
  const filter = $("#status-filter").value;
  const visible = jobs.filter((j) =>
    filter === "all" ? true : filter === "active" ? j.status !== "applied" && j.status !== "skipped" : j.status === filter);
  const body = $("#jobs-body");
  body.replaceChildren(...visible.map((j) => h("tr", { "data-id": j.job_id },
    h("td", { class: "c-check" }, h("input", {
      type: "checkbox", "aria-label": `Queue ${j.company} ${j.role}`,
      checked: j.status === "queued", disabled: !QUEUEABLE.has(j.status),
      onchange: async (ev) => {
        try { await setStatus([j.job_id], ev.target.checked ? "queued" : "discovered"); }
        catch (e) { toast(e.message, true); ev.target.checked = !ev.target.checked; }
      },
    })),
    h("td", { class: "company" }, j.company),
    h("td", { class: "role" }, h("a", { href: j.url, target: "_blank", rel: "noopener" }, j.role)),
    h("td", { class: "loc" }, j.location || "—"),
    h("td", {}, h("span", { class: "portal" + (j.automated ? "" : " manual"),
      title: j.automated ? "Filled in automatically" : "Opens for you to apply by hand" },
      j.automated ? `${cap(j.portal_type)}` : `${cap(j.portal_type)} (by hand)`)),
    h("td", {}, h("span", { class: `status ${j.status}`, title: j.notes || "" }, STATUS_LABEL[j.status] || j.status)),
    h("td", { class: "row-actions" },
      h("button", { type: "button", class: "link", onclick: () => openHistory(j) }, "History"),
      QUEUEABLE.has(j.status) && j.status !== "skipped"
        ? h("button", { type: "button", class: "link", onclick: () => setStatus([j.job_id], "skipped").catch((e) => toast(e.message, true)) }, "Skip")
        : null),
  )));
  const empty = $("#jobs-empty");
  empty.hidden = visible.length > 0;
  empty.textContent = jobs.length
    ? "Nothing matches this filter."
    : "No postings yet. Use Find new postings to pull from the job boards in Settings, or add one by its URL.";
  updateCounts();
}

function cap(s) { return s ? s[0].toUpperCase() + s.slice(1) : s; }

function updateCounts() {
  const queued = jobs.filter((j) => j.status === "queued").length;
  const pending = jobs.filter((j) => j.status === "pending_input").length;
  $("#queued-count").textContent = queued ? `${queued} queued` : "";
  $("#count-queued").textContent = `(${queued})`;
  $("#count-pending").textContent = `(${pending})`;
}

$("#status-filter").addEventListener("change", renderJobs);
$("#go-run").addEventListener("click", () => show("run"));
$("#fetch-jobs").addEventListener("click", (ev) => busy(ev.currentTarget, async () => {
  const r = await api("/api/jobs/fetch", { method: "POST" });
  toast(`Found ${r.matched} matching postings, ${r.new} new.`);
  await loadJobs();
}));

const addDialog = $("#add-dialog");
$("#open-add").addEventListener("click", () => { $("#add-error").hidden = true; addDialog.showModal(); });
$("#add-cancel").addEventListener("click", () => addDialog.close());
$("#add-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const data = Object.fromEntries(new FormData(ev.target));
  try {
    await api("/api/jobs/add", { method: "POST", body: data });
    ev.target.reset();
    addDialog.close();
    toast("Added and queued.");
    await loadJobs();
  } catch (e) { $("#add-error").textContent = e.message; $("#add-error").hidden = false; }
});

const historyDialog = $("#history-dialog");
async function openHistory(job) {
  $("#history-title").textContent = `${job.company}: ${job.role}`;
  const rows = (await api(`/api/jobs/log?id=${encodeURIComponent(job.job_id)}`)).log;
  $("#history-log").replaceChildren(...(rows.length ? rows : [{ timestamp: "", event: "", detail: "No history yet." }])
    .map((r) => h("li", { class: r.level === "warning" || r.level === "error" ? r.level : "" },
      r.timestamp ? h("time", {}, new Date(r.timestamp).toLocaleString()) : null,
      `${r.event ? r.event + " " : ""}${r.detail}`)));
  historyDialog.showModal();
}
$("#history-close").addEventListener("click", () => historyDialog.close());

// ---------------------------------------------------------------- run

let running = false;
function renderRun(run) {
  running = run.state === "running";
  $("#start-run").hidden = running;
  $("#stop-run").hidden = !running;
  $("#run-indicator").hidden = !running;
  $("#run-form").querySelectorAll("input").forEach((i) => (i.disabled = running));
  if (running) {
    $("#run-status").textContent = run.dry_run ? "Practice run in progress. Nothing will be submitted." : "Applying…";
  } else if (run.summary && Object.keys(run.summary).length) {
    const parts = Object.entries(run.summary).map(([k, v]) => `${v} ${(STATUS_LABEL[k] || k).toLowerCase()}`);
    $("#run-status").textContent = `Finished: ${parts.join(", ")}.`;
  } else if (run.summary) {
    $("#run-status").textContent = "Finished.";
  }
}

$("#run-form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  busy($("#start-run"), async () => {
    const r = await api("/api/run", {
      method: "POST",
      body: {
        mode: new FormData(ev.target).get("mode"),
        headless: !$("#opt-visible").checked,
        dry_run: $("#opt-dry").checked,
        limit: Number($("#opt-limit").value) || null,
      },
    });
    renderRun(r.run);
  });
});
$("#stop-run").addEventListener("click", (ev) => busy(ev.currentTarget, () => api("/api/run/stop", { method: "POST" })));

const logEl = $("#log");
function addLog(e) {
  const nearBottom = logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 40;
  const t = e.ts ? new Date(e.ts) : new Date();
  logEl.append(h("li", { class: e.level || "" },
    h("time", {}, t.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })), e.text));
  while (logEl.childElementCount > 1500) logEl.firstElementChild.remove();
  if (nearBottom) logEl.scrollTop = logEl.scrollHeight;
}
$("#clear-log").addEventListener("click", () => logEl.replaceChildren());

// ---------------------------------------------------------------- prompts (autoapply needs you)

const prompts = new Map();
let shownPrompt = null;
const promptDialog = $("#prompt");
promptDialog.addEventListener("cancel", (ev) => ev.preventDefault()); // Esc must not dismiss a question

function updateNeedsBadge() {
  const badge = $("#nav-needs");
  badge.hidden = prompts.size === 0;
  badge.textContent = String(prompts.size);
  document.title = prompts.size ? `(${prompts.size}) Input needed: autoapply` : "autoapply";
}

function chime() {
  try {
    const ctx = new AudioContext();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.frequency.value = 880;
    gain.gain.setValueAtTime(0.0001, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.15, ctx.currentTime + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.35);
    osc.connect(gain).connect(ctx.destination);
    osc.start(); osc.stop(ctx.currentTime + 0.4);
  } catch { /* audio unavailable */ }
}

async function respond(id, data) {
  try {
    await api(`/api/prompt/${id}`, { method: "POST", body: data });
  } catch (e) { toast(e.message, true); }
  closePrompt(id);
}

function closePrompt(id) {
  prompts.delete(id);
  if (shownPrompt === id) { shownPrompt = null; promptDialog.close(); }
  updateNeedsBadge();
  showNextPrompt();
}

function showNextPrompt() {
  if (shownPrompt || prompts.size === 0) return;
  const p = prompts.values().next().value;
  shownPrompt = p.id;
  renderPrompt(p);
  if (!promptDialog.open) promptDialog.showModal();
}

function btn(label, onclick, primary = false) {
  return h("button", { type: "button", class: primary ? "primary" : "", onclick }, label);
}

function renderPrompt(p) {
  $("#prompt-title").textContent = p.title;
  $("#prompt-job").textContent = p.job ? `${p.job.company}: ${p.job.role}` : "";
  $("#prompt-reason").textContent = "";
  const content = $("#prompt-content");
  const actions = $("#prompt-actions");
  content.replaceChildren();
  actions.replaceChildren();
  const q = $("#prompt-question");

  if (p.kind === "field") {
    q.textContent = p.question;
    $("#prompt-reason").textContent = p.reason + (p.required ? "" : " (optional)");
    let input = null;
    const remember = h("input", { type: "checkbox", id: "prompt-remember" });
    const rememberRow = h("label", { class: "choice" }, remember, "Save this answer for this exact question next time");
    const answer = (value) => respond(p.id, { action: "answer", value, remember: remember.checked });

    if (p.options && p.options.length) {
      content.append(h("div", { class: "options" },
        p.options.map((o) => h("button", { type: "button", class: "option", onclick: () => answer(o) }, o))));
      content.append(rememberRow);
    } else if (p.field_kind === "checkbox") {
      actions.append(btn("Tick it", () => answer("y"), true), btn("Leave it unticked", () => answer("n")));
    } else {
      if (p.field_kind === "textarea") {
        input = h("textarea", { "aria-label": "Your answer" });
      } else {
        input = h("input", { type: "text", "aria-label": "Your answer",
          placeholder: p.field_kind === "file" ? "Full path to the file" : "" });
        input.addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); submitTyped(); } });
      }
      content.append(input);
      if (p.field_kind !== "file") content.append(rememberRow);
      const submitTyped = () => { if (input.value.trim()) answer(input.value); else input.focus(); };
      actions.append(btn("Use this answer", submitTyped, true));
      setTimeout(() => input.focus(), 30);
    }
    actions.append(h("span", { class: "spacer" }));
    if (p.can_use_browser) actions.append(btn("I filled it in the browser", () => respond(p.id, { action: "manual" })));
    actions.append(btn("Skip question", () => respond(p.id, { action: "skip_field" })));
    actions.append(btn("Skip this posting", () => respond(p.id, { action: "skip_job" })));
  } else if (p.kind === "submit") {
    q.textContent = "Check the form before it's sent";
    $("#prompt-reason").textContent = p.can_use_browser
      ? "Everything autoapply filled in is below. The browser window shows the full form."
      : "Everything autoapply filled in is below.";
    content.append(h("div", { class: "filled" }, h("table", {}, h("tbody", {},
      (p.filled.length ? p.filled : [{ field: "Nothing", value: "was filled automatically" }])
        .map((r) => h("tr", {}, h("td", {}, r.field), h("td", {}, r.value)))))));
    actions.append(btn("Submit application", () => respond(p.id, { decision: "submit" }), true));
    if (p.can_use_browser) actions.append(btn("I submitted it in the browser", () => respond(p.id, { decision: "manual" })));
    actions.append(h("span", { class: "spacer" }), btn("Don't submit", () => respond(p.id, { decision: "abort" })));
  } else if (p.kind === "challenge") {
    q.textContent = p.message;
    actions.append(btn("Continue", () => respond(p.id, { action: "continue" }), true),
      btn("Skip this posting", () => respond(p.id, { action: "skip" })));
  } else if (p.kind === "outcome") {
    q.textContent = p.message;
    actions.append(btn("Yes, it was submitted", () => respond(p.id, { submitted: true }), true),
      btn("No", () => respond(p.id, { submitted: false })));
  } else if (p.kind === "manual") {
    q.textContent = p.message;
    actions.append(btn("I applied", () => respond(p.id, { outcome: "applied" }), true),
      btn("Do it later", () => respond(p.id, { outcome: "later" })),
      btn("Skip this posting", () => respond(p.id, { outcome: "skip" })));
  } else {
    q.textContent = p.message || "Paused";
    actions.append(btn("Continue", () => respond(p.id, {}), true));
  }
}

function addPrompt(p, fresh) {
  if (prompts.has(p.id)) return;
  prompts.set(p.id, p);
  updateNeedsBadge();
  if (fresh) chime();
  showNextPrompt();
}

// ---------------------------------------------------------------- profile

async function loadProfile() {
  try {
    const f = await api("/api/files/profile");
    $("#profile-text").value = f.text;
    $("#profile-path").textContent = f.path;
  } catch (e) { toast(e.message, true); }
  loadResume();
}

async function loadResume() {
  const r = await api("/api/resume").catch(() => null);
  if (!r) return;
  $("#resume-status").textContent = r.exists
    ? `Using ${r.path} (${Math.round(r.size / 1024)} KB).`
    : "No resume yet. Choose the PDF you want uploaded with every application.";
}

$("#resume-file").addEventListener("change", async (ev) => {
  const file = ev.target.files[0];
  if (!file) return;
  try {
    await api("/api/resume", { method: "PUT", raw: file });
    toast("Resume saved.");
    loadResume();
  } catch (e) { toast(e.message, true); }
  ev.target.value = "";
});

async function saveFile(name, textarea, errorBox, button) {
  errorBox.hidden = true;
  await busy(button, async () => {
    try {
      await api(`/api/files/${name}`, { method: "PUT", body: { text: textarea.value } });
      toast(name === "profile" ? "Details saved." : "Settings saved.");
    } catch (e) { errorBox.textContent = e.message; errorBox.hidden = false; }
  });
}
$("#save-profile").addEventListener("click", (ev) =>
  saveFile("profile", $("#profile-text"), $("#profile-error"), ev.currentTarget));
$("#build-profile").addEventListener("click", (ev) => busy(ev.currentTarget, async () => {
  const { profile: p } = await api("/api/profile/build", { method: "POST", body: { github: $("#build-github").checked } });
  const box = $("#profile-summary");
  box.textContent = `${p.name} (${p.email}), ${p.school || "no school listed"}. ` +
    `${p.experience} roles, ${p.projects} projects and ${p.skills} skills, built from ${p.sources.join(", ")}.`;
  box.hidden = false;
}));

// ---------------------------------------------------------------- settings

async function loadSettings() {
  try {
    const f = await api("/api/files/config");
    $("#config-text").value = f.text;
    $("#config-path").textContent = f.path;
  } catch (e) { toast(e.message, true); }
  loadChecks();
}

async function loadChecks() {
  const body = $("#checks-body");
  body.replaceChildren(h("tr", {}, h("td", { class: "muted" }, "Checking…")));
  try {
    const { checks } = await api("/api/doctor");
    body.replaceChildren(...checks.map((c) => h("tr", {},
      h("td", {}, c.name),
      h("td", { class: c.ok ? "ok" : c.warn_only ? "warn" : "bad" }, c.detail))));
  } catch (e) { toast(e.message, true); }
}
$("#recheck").addEventListener("click", loadChecks);
$("#save-config").addEventListener("click", (ev) =>
  saveFile("config", $("#config-text"), $("#config-error"), ev.currentTarget));
$("#install-browser").addEventListener("click", (ev) => busy(ev.currentTarget, async () => {
  toast("Downloading Chromium. Progress appears under Apply.");
  const r = await api("/api/install-browser", { method: "POST" });
  toast(r.ok ? "Chromium is ready." : "Chromium download failed. See Apply for details.", !r.ok);
  loadChecks();
}));

$("#quit").addEventListener("click", async () => {
  if (running && !confirm("An application run is in progress. Stop it and quit?")) return;
  await api("/api/quit", { method: "POST" }).catch(() => {});
  document.body.replaceChildren(h("p", { class: "empty" }, "autoapply has quit. You can close this window."));
  setTimeout(() => window.close(), 300);
});

// ---------------------------------------------------------------- live updates

let refreshTimer;
function refreshJobsSoon() { clearTimeout(refreshTimer); refreshTimer = setTimeout(loadJobs, 150); }

function connect() {
  const es = new EventSource(`/api/events?token=${encodeURIComponent(TOKEN)}`);
  es.onmessage = (msg) => {
    const e = JSON.parse(msg.data);
    if (e.type === "log") addLog(e);
    else if (e.type === "prompt") addPrompt(e.prompt, true);
    else if (e.type === "prompt_closed") { if (prompts.has(e.id)) closePrompt(e.id); }
    else if (e.type === "run") renderRun(e.run);
    else if (e.type === "job") {
      const j = jobs.find((x) => x.job_id === e.id);
      if (j) { j.status = e.status; renderJobs(); } else refreshJobsSoon();
    } else if (e.type === "jobs") refreshJobsSoon();
  };
}

async function init() {
  try {
    const s = await api("/api/state");
    $("#version").textContent = `autoapply ${s.version}. Workspace: ${s.workspace}`;
    $("#opt-visible").checked = !s.defaults.headless;
    $("#opt-dry").checked = s.defaults.dry_run;
    $("#opt-limit").value = s.defaults.limit;
    s.log.forEach(addLog);
    renderRun(s.run);
    s.prompts.forEach((p) => addPrompt(p, false));
  } catch (e) {
    toast(`Couldn't reach autoapply: ${e.message}`, true);
    return;
  }
  connect();
  await loadJobs();
}
init();
