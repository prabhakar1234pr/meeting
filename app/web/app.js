const api = {
  async get(path) { const r = await fetch(path); return r.json(); },
  async post(path, body) {
    const r = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return r.json();
  },
  async patch(path, body) {
    const r = await fetch(path, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return r.json();
  },
  async del(path) { const r = await fetch(path, { method: "DELETE" }); return r.json(); },
};

let SOURCES = [];
let AGENTS = [];

// ─── Nav ──────────────────────────────────────────────────────
function activate(section) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.section === section));
  document.querySelectorAll(".section").forEach((s) => s.classList.toggle("active", s.id === section));
  if (section === "briefs") loadMeetingsList();
  if (section === "agents") loadAgentsList();
  if (section === "connections") loadConnections();
  if (section === "activity") loadActions();
}
document.querySelectorAll(".tab").forEach((tab) => {
  tab.onclick = () => activate(tab.dataset.section);
});

// ─── Knowledge Store ──────────────────────────────────────────
// Toggle the URI input vs the file picker based on the selected source type.
const sourceType = document.getElementById("source-type");
sourceType.onchange = () => {
  const isFile = sourceType.value === "file";
  const uri = document.getElementById("source-uri");
  const file = document.getElementById("source-file");
  uri.style.display = isFile ? "none" : "";
  uri.required = !isFile;
  file.style.display = isFile ? "" : "none";
  file.required = isFile;
};

document.getElementById("source-form").onsubmit = async (e) => {
  e.preventDefault();
  const f = e.target;
  const d = new FormData(f);
  if (sourceType.value === "file") {
    const fd = new FormData();
    fd.append("name", d.get("name"));
    fd.append("file", document.getElementById("source-file").files[0]);
    await fetch("/api/sources/upload", { method: "POST", body: fd });
  } else {
    await api.post("/api/sources", {
      name: d.get("name"), type: "github", uri: d.get("uri"),
    });
  }
  f.reset();
  sourceType.dispatchEvent(new Event("change"));
  loadSources();
};

async function loadSources() {
  SOURCES = await api.get("/api/sources");
  const el = document.getElementById("sources-list");
  el.innerHTML = SOURCES.map((s) => `
    <div class="item">
      <div class="top">
        <div><div class="name">${esc(s.name)}</div><div class="uri">${esc(s.uri)}</div></div>
        <div style="display:flex;align-items:center;gap:8px">
          <span class="badge ${s.status}">${s.status}${s.chunk_count ? ` · ${s.chunk_count} chunks` : ""}</span>
          <button class="ghost" onclick="deleteSource('${s.id}')">Delete</button>
        </div>
      </div>
      ${s.error ? `<div class="uri" style="color:var(--err)">${esc(s.error)}</div>` : ""}
    </div>`).join("") || `<p class="hint">No sources yet.</p>`;
  // Poll while anything is still ingesting.
  if (SOURCES.some((s) => s.status === "pending" || s.status === "ingesting")) {
    setTimeout(loadSources, 2500);
  }
}

async function deleteSource(id) {
  if (!confirm("Delete this source? It's removed from the Knowledge Store and any agents using it.")) return;
  await fetch(`/api/sources/${id}`, { method: "DELETE" });
  loadSources();
  if (typeof loadAgents === "function") loadAgents();
}

// ─── Agents ───────────────────────────────────────────────────
document.getElementById("agent-form").onsubmit = async (e) => {
  e.preventDefault();
  const f = e.target;
  const d = new FormData(f);
  const agent = await api.post("/api/agents", {
    name: d.get("name"),
    wake_phrase: d.get("wake_phrase"),
    system_prompt: d.get("system_prompt"),
  });
  f.reset();
  activate("agents");
  // Jump straight into the new agent's config so sources can be connected.
  if (agent && agent.id) openAgent(agent.id);
};

async function loadAgentsList() {
  document.getElementById("agent-detail-view").style.display = "none";
  document.getElementById("agents-list-view").style.display = "";
  AGENTS = await api.get("/api/agents");
  if (!SOURCES.length) SOURCES = await api.get("/api/sources");
  const el = document.getElementById("agents-list");
  el.innerHTML = AGENTS.map((a) => {
    const n = (a.source_ids || []).length;
    return `<div class="item" style="cursor:pointer" onclick="openAgent('${a.id}')">
      <div class="top">
        <div>
          <div class="name">${esc(a.name)}</div>
          ${a.system_prompt ? `<div class="uri">${esc(a.system_prompt)}</div>` : `<div class="uri">No system prompt yet.</div>`}
        </div>
        <span class="badge">${n} source${n === 1 ? "" : "s"}</span>
      </div>
    </div>`;
  }).join("") || `<p class="hint">No agents yet. Create one to get started.</p>`;
  populateAgentSelects();
}

// Agent config page: edit fields + connect knowledge sources.
async function openAgent(agentId) {
  const a = await api.get(`/api/agents/${agentId}`);
  if (!SOURCES.length) SOURCES = await api.get("/api/sources");
  const connected = new Set(a.source_ids || []);
  const chips = SOURCES.map((s) => `
    <button type="button" class="src-chip ${connected.has(s.id) ? "on" : ""}"
            data-id="${s.id}" onclick="toggleSource('${a.id}', this)">
      <span class="src-check" aria-hidden="true"></span>
      <span class="src-name">${esc(s.name)}</span>
      <span class="badge ${s.status}">${s.status}</span>
    </button>`).join("") ||
    `<p class="hint">No sources yet. Add them in the Knowledge Store first.</p>`;

  document.getElementById("agent-detail-view").innerHTML = `
    <button class="ghost" onclick="loadAgentsList()">&larr; Back to agents</button>
    <div class="card" style="margin-top:14px">
      <form class="form" onsubmit="return saveAgent(event, '${a.id}')">
        <label class="field"><span>Name</span>
          <input name="name" value="${esc(a.name)}" required /></label>
        <label class="field"><span>Wake phrase</span>
          <input name="wake_phrase" value="${esc(a.wake_phrase || "")}" placeholder="e.g. Hey Ada" /></label>
        <label class="field"><span>System prompt</span>
          <textarea name="system_prompt" rows="4" placeholder="How should this teammate behave?">${esc(a.system_prompt || "")}</textarea></label>
        <div class="row-actions">
          <button type="submit">Save changes</button>
          <span class="save-note" id="agent-save-note"></span>
        </div>
      </form>
    </div>
    <div class="card">
      <div class="config-head">
        <div class="name">Knowledge sources</div>
        <span class="hint" id="src-count">${connected.size} connected</span>
      </div>
      <p class="hint">Toggle which sources this teammate can pull from. Changes save automatically.</p>
      <div class="src-grid">${chips}</div>
    </div>`;
  document.getElementById("agents-list-view").style.display = "none";
  document.getElementById("agent-detail-view").style.display = "";
}

async function toggleSource(agentId, btn) {
  btn.classList.toggle("on");
  const ids = [...document.querySelectorAll(".src-chip.on")].map((b) => b.dataset.id);
  const count = document.getElementById("src-count");
  if (count) count.textContent = `${ids.length} connected`;
  await api.post(`/api/agents/${agentId}/sources`, { source_ids: ids });
}

async function saveAgent(e, agentId) {
  e.preventDefault();
  const d = new FormData(e.target);
  const note = document.getElementById("agent-save-note");
  if (note) note.textContent = "Saving…";
  await api.patch(`/api/agents/${agentId}`, {
    name: d.get("name"),
    wake_phrase: d.get("wake_phrase"),
    system_prompt: d.get("system_prompt"),
  });
  if (note) {
    note.textContent = "Saved";
    note.classList.add("ok");
    setTimeout(() => { note.textContent = ""; note.classList.remove("ok"); }, 1600);
  }
  return false;
}

function populateAgentSelects() {
  const opts = AGENTS.map((a) => `<option value="${a.id}">${esc(a.name)}</option>`).join("");
  document.getElementById("meeting-agent").innerHTML = opts;
  document.getElementById("chat-agent").innerHTML = opts;
}

// ─── Meetings ─────────────────────────────────────────────────
document.getElementById("meeting-form").onsubmit = async (e) => {
  e.preventDefault();
  const f = e.target;
  const d = new FormData(f);
  const res = await api.post("/api/meetings", {
    agent_id: d.get("agent_id"),
    meeting_link: d.get("meeting_link"),
    title: d.get("title"),
  });
  const box = document.getElementById("meeting-result");
  if (res.error) {
    box.innerHTML = `<div class="item" style="color:var(--err)">${esc(res.error)}${res.detail ? `<pre>${esc(res.detail)}</pre>` : ""}</div>`;
  } else {
    box.innerHTML = `<div class="item"><div class="name">Agent dispatched</div>
      <div class="uri">bot_id: ${esc(res.bot?.bot_id || "?")} · status: ${esc(res.bot?.status || "?")}</div></div>`;
    f.reset();
  }
};

// ─── Briefs: list of meetings → per-meeting brief detail ──────
document.getElementById("refresh-briefs").onclick = loadMeetingsList;

function fmtWhen(ts) {
  return ts ? new Date(ts * 1000).toLocaleString() : "";
}

async function loadMeetingsList() {
  document.getElementById("brief-detail-view").style.display = "none";
  document.getElementById("briefs-list-view").style.display = "";
  const meetings = await api.get("/api/meetings");
  const el = document.getElementById("briefs-list");
  el.innerHTML = meetings.map((m) => {
    const meta = [m.agent_name, fmtWhen(m.started_at)].filter(Boolean).map(esc).join(" · ");
    const badge = m.brief_count > 0
      ? `<span class="badge ready">brief ready</span>`
      : `<span class="badge">${esc(m.status)}</span>`;
    return `<div class="item" style="cursor:pointer" onclick="openBrief('${m.id}')">
      <div class="top">
        <div><div class="name">${esc(m.title || "Untitled meeting")}</div>
          <div class="uri">${meta}</div></div>
        ${badge}
      </div>
    </div>`;
  }).join("") || `<p class="hint">No meetings yet.</p>`;
}

async function openBrief(meetingId) {
  const data = await api.get(`/api/meetings/${meetingId}`);
  const m = data.meeting || {};
  const b = data.brief;
  const body = b
    ? `<div class="md">${md(b.brief || "")}</div>
       <details><summary>Full notes</summary><div class="md">${md(b.notes || "")}</div></details>
       ${data.transcript ? `<details><summary>Transcript</summary><pre>${esc(data.transcript)}</pre></details>` : ""}`
    : `<p class="hint">No brief for this meeting yet.</p>
       <button onclick="genBrief('${m.id}', this)">Generate brief</button>`;
  document.getElementById("brief-detail-view").innerHTML = `
    <button class="ghost" onclick="loadMeetingsList()">&larr; Back to meetings</button>
    <div class="card" style="margin-top:14px">
      <div class="top">
        <div class="name" style="font-size:18px">${esc(m.title || "Untitled meeting")}</div>
        <span class="badge">${esc(m.agent_name || "")}</span>
      </div>
      <div class="uri">${[fmtWhen(m.started_at), m.status].filter(Boolean).map(esc).join(" · ")}</div>
      ${body}
    </div>`;
  document.getElementById("briefs-list-view").style.display = "none";
  document.getElementById("brief-detail-view").style.display = "";
}

async function genBrief(meetingId, btn) {
  if (btn) { btn.textContent = "Generating…"; btn.disabled = true; }
  await api.post(`/api/meetings/${meetingId}/brief`, {});
  openBrief(meetingId);
}

// ─── Chat (streaming, markdown-rendered) ──────────────────────
document.getElementById("chat-form").onsubmit = async (e) => {
  e.preventDefault();
  const input = document.getElementById("chat-message");
  const msg = input.value.trim();
  const agentId = document.getElementById("chat-agent").value;
  if (!msg || !agentId) return;

  addMsg("user", msg);
  input.value = "";
  const log = document.getElementById("chat-log");
  const bot = addMsg("bot", "");
  bot.innerHTML = `<span class="typing"><i></i><i></i><i></i></span>`;

  try {
    // Tool-aware: the agent may propose an action instead of answering.
    const res = await api.post("/api/chat", { agent_id: agentId, message: msg });
    if (res.proposed_action) {
      renderProposal(bot, res.proposed_action);
    } else {
      bot.innerHTML = md(res.answer || res.error || "(no response)");
    }
  } catch (err) {
    bot.innerHTML = md(`Sorry, something went wrong. ${err?.message || err}`);
  } finally {
    log.scrollTop = log.scrollHeight;
  }
};

// Render an action the agent wants to take, gated behind a human confirm.
function renderProposal(bot, action) {
  bot.classList.remove("md");
  bot.innerHTML = `
    <div class="action-card" data-id="${action.id}">
      <div class="action-head">Proposed action</div>
      <div class="action-summary">${esc(action.summary)}</div>
      <pre class="action-args">${esc(JSON.stringify(action.args, null, 2))}</pre>
      <div class="action-btns">
        <button onclick="confirmAction('${action.id}', this)">Confirm &amp; run</button>
        <button class="ghost" onclick="cancelAction('${action.id}', this)">Cancel</button>
      </div>
    </div>`;
}

async function confirmAction(id, btn) {
  const card = btn.closest(".action-card");
  card.querySelectorAll("button").forEach((b) => (b.disabled = true));
  btn.textContent = "Running…";
  const res = await api.post(`/api/actions/${id}/confirm`, {});
  const r = res.result || {};
  if (res.ok) {
    const link = r.url ? ` <a href="${esc(r.url)}" target="_blank" rel="noopener noreferrer">open</a>` : "";
    card.innerHTML = `<div class="action-done">${esc(r.summary || "Done")}${link}</div>`;
    card.classList.add("done");
  } else {
    card.innerHTML = `<div class="action-error">${esc(r.error || res.error || "Action failed")}</div>`;
    card.classList.add("error");
  }
}

async function cancelAction(id, btn) {
  const card = btn.closest(".action-card");
  await api.post(`/api/actions/${id}/cancel`, {});
  card.innerHTML = `<div class="action-cancelled">Cancelled.</div>`;
  card.classList.add("cancelled");
}

// ─── Connections (Scalekit identities) ────────────────────────
async function loadConnections() {
  const data = await api.get("/api/connections");
  const note = document.getElementById("connections-note");
  if (note) note.style.display = data.scalekit_configured ? "none" : "";
  const el = document.getElementById("connections-list");
  el.innerHTML = (data.connections || []).map((c) => {
    const badge = c.status === "active" ? `<span class="badge ready">connected</span>`
      : c.status === "pending" ? `<span class="badge pending">authorizing</span>`
      : `<span class="badge">not connected</span>`;
    const label = c.status === "active" ? "Reauthorize" : "Connect";
    const cls = c.status === "active" ? "ghost" : "";
    // Once a connector is linked (or mid-authorization), let the user drop it so
    // they can reconnect as a different account/repo.
    const linked = c.status === "active" || c.status === "pending";
    const disconnectBtn = linked
      ? `<button class="ghost danger" onclick="disconnectConnector('${c.key}', this)">Disconnect</button>`
      : "";
    return `<div class="item"><div class="top">
      <div><div class="name">${esc(c.label)}</div><div class="uri">${esc(c.note)}</div></div>
      <div style="display:flex;align-items:center;gap:10px">${badge}
        <button class="${cls}" onclick="connectConnector('${c.key}', this)">${label}</button>
        ${disconnectBtn}</div>
    </div></div>`;
  }).join("") || `<p class="hint">No connectors available.</p>`;
}

async function connectConnector(key, btn) {
  const label = btn.textContent;
  btn.disabled = true; btn.textContent = "…";
  const res = await api.post(`/api/connections/${key}/authorize`, {});
  if (res.error) { alert(res.error); btn.disabled = false; btn.textContent = label; return; }
  if (res.status === "active") { loadConnections(); return; }
  if (res.link) {
    window.open(res.link, "_blank", "noopener");
    pollConnection(key);           // flips to "connected" once OAuth completes
  }
  btn.disabled = false; btn.textContent = label;
}

async function disconnectConnector(key, btn) {
  if (!confirm("Disconnect this account? You'll need to reconnect to use it again.")) return;
  const label = btn.textContent;
  btn.disabled = true; btn.textContent = "…";
  const res = await api.del(`/api/connections/${key}`);
  if (res && res.error) { alert(res.error); btn.disabled = false; btn.textContent = label; return; }
  loadConnections();               // re-renders as "not connected" with a Connect button
}

async function pollConnection(key, tries = 0) {
  const res = await api.get(`/api/connections/${key}`);
  loadConnections();
  if (res.status !== "active" && tries < 40) {
    setTimeout(() => pollConnection(key, tries + 1), 2000);
  }
}

// ─── Activity (action audit trail) ────────────────────────────
async function loadActions() {
  const actions = await api.get("/api/actions");
  const el = document.getElementById("actions-list");
  el.innerHTML = (actions || []).map((a) => {
    const cls = { done: "ready", error: "error", pending: "pending", proposed: "pending" }[a.status] || "";
    let extra = "";
    try {
      const r = JSON.parse(a.result || "{}");
      if (r.url) extra = `<div class="uri"><a href="${esc(r.url)}" target="_blank" rel="noopener noreferrer">${esc(r.url)}</a></div>`;
      else if (r.error) extra = `<div class="uri" style="color:var(--err)">${esc(r.error)}</div>`;
    } catch (e) { /* no structured result */ }
    const meta = [`as ${a.identity}`, a.agent_name, fmtWhen(a.created_at)].filter(Boolean).map(esc).join(" · ");
    return `<div class="item"><div class="top">
      <div><div class="name">${esc(a.summary || a.tool)}</div><div class="uri">${meta}</div>${extra}</div>
      <span class="badge ${cls}">${esc(a.status)}</span>
    </div></div>`;
  }).join("") || `<p class="hint">No actions yet. Ask an agent to do something in Chat.</p>`;
}

function addMsg(role, text) {
  const log = document.getElementById("chat-log");
  const div = document.createElement("div");
  div.className = role === "bot" ? "msg bot md" : `msg ${role}`;
  if (role === "bot") div.innerHTML = md(text);
  else div.textContent = text;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
  return div;
}

// ─── utils / init ─────────────────────────────────────────────
function esc(s) {
  return String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

// Minimal Markdown → HTML. Non-code text is HTML-escaped before any
// markup is applied, so raw HTML in the source can't inject; only the
// markdown rules below emit tags. Fenced code is handled separately so
// its contents are never parsed as markdown.
function md(src) {
  if (!src) return "";
  const s = String(src);
  const re = /```[^\n]*\n?([\s\S]*?)```/g;
  let out = "", last = 0, m;
  while ((m = re.exec(s)) !== null) {
    out += mdBlocks(s.slice(last, m.index));
    out += `<pre><code>${esc(m[1].replace(/\n$/, ""))}</code></pre>`;
    last = re.lastIndex;
  }
  out += mdBlocks(s.slice(last));
  return out;
}

function mdBlocks(text) {
  return esc(text).split(/\n{2,}/).map((raw) => {
    const b = raw.trim();
    if (!b) return "";
    let m;
    if ((m = b.match(/^(#{1,6})\s+(.*)$/))) return `<h${m[1].length}>${inline(m[2])}</h${m[1].length}>`;
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(b)) return "<hr />";
    const lines = b.split("\n");
    if (lines.every((l) => /^>\s?/.test(l)))
      return `<blockquote>${inline(lines.map((l) => l.replace(/^>\s?/, "")).join("<br />"))}</blockquote>`;
    if (lines.every((l) => /^\s*[-*+]\s+/.test(l)))
      return `<ul>${lines.map((l) => `<li>${inline(l.replace(/^\s*[-*+]\s+/, ""))}</li>`).join("")}</ul>`;
    if (lines.every((l) => /^\s*\d+\.\s+/.test(l)))
      return `<ol>${lines.map((l) => `<li>${inline(l.replace(/^\s*\d+\.\s+/, ""))}</li>`).join("")}</ol>`;
    return `<p>${inline(b.replace(/\n/g, "<br />"))}</p>`;
  }).join("\n");
}

// inline formatting, run on already-escaped text
function inline(s) {
  return s
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>")
    .replace(/~~([^~]+)~~/g, "<del>$1</del>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
}

loadSources();
loadAgentsList();
