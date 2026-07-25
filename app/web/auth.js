// Frontend auth layer (self-contained; does not touch app.js).
// - Reads /api/me. When AUTH is off (dev), everything behaves as before.
// - When AUTH is on: shows a login gate, then an org-onboarding gate, then
//   applies role gating (members can't see admin-only controls).
(function () {
  let ME = null;

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  async function fetchMe() {
    try {
      const r = await fetch("/api/me");
      if (r.status === 401) return { authed: false, auth_enabled: true };
      return { authed: true, ...(await r.json()) };
    } catch {
      return { authed: false };
    }
  }

  function showGate(html) {
    const g = $("gate");
    g.innerHTML = `<div class="gate-card">${html}</div>`;
    g.style.display = "flex";
  }
  const hideGate = () => { const g = $("gate"); g.style.display = "none"; g.innerHTML = ""; };

  function renderLogin() {
    showGate(`
      <div class="gate-logo">🤖</div>
      <h2>AI Team Member</h2>
      <p class="hint">Sign in to your workspace to continue.</p>
      <a class="gate-btn" href="/auth/login">Sign in with Scalekit</a>
      <p class="hint" style="margin-top:14px"><a href="/auth/login?switch=1">Use a different account</a></p>`);
  }

  function renderOnboarding() {
    showGate(`
      <h2>Create your organization</h2>
      <p class="hint">Signed in as <strong>${esc((ME.user && ME.user.email) || "you")}</strong> ✓ — you'll be the admin and can invite teammates next.</p>
      <form id="org-form" class="form">
        <input name="name" placeholder="Organization name" required />
        <button type="submit">Create organization</button>
      </form>
      <p class="hint" style="margin-top:14px">
        <a href="/auth/login?switch=1">Sign in as a different account</a>
        &nbsp;·&nbsp;
        <a href="#" id="signout-link">Sign out</a>
      </p>`);
    $("org-form").onsubmit = async (e) => {
      e.preventDefault();
      const name = new FormData(e.target).get("name");
      const btn = e.target.querySelector("button");
      btn.disabled = true; btn.textContent = "Creating…";
      const r = await fetch("/api/orgs", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      if (r.ok) location.reload();
      else {
        const d = await r.json().catch(() => ({}));
        btn.disabled = false; btn.textContent = "Create organization";
        alert(d.error || "Could not create organization");
      }
    };
    $("signout-link").onclick = (e) => { e.preventDefault(); logout(); };
  }

  function renderUserbar() {
    const bar = $("userbar");
    if (!bar || !ME || !ME.authed) return;
    const u = ME.user || {};
    bar.innerHTML = `
      <div class="userbar-id">
        <div class="userbar-name">${esc(u.name || u.email || "You")}</div>
        <div class="userbar-org">${esc(ME.org ? ME.org.name : "No org")}${ME.role ? " · " + esc(ME.role) : ""}</div>
      </div>
      <div style="display:flex;gap:6px">
        <button class="ghost" id="switch-btn" title="Sign in as a different user">Switch</button>
        <button class="ghost" id="logout-btn">Sign out</button>
      </div>`;
    $("logout-btn").onclick = logout;
    $("switch-btn").onclick = () => { location.href = "/auth/login?switch=1"; };
  }

  async function logout() {
    let url = "/";
    try {
      const d = await (await fetch("/auth/logout", { method: "POST" })).json();
      if (d.logout_url) url = d.logout_url;
    } catch {}
    location.href = url;
  }

  function applyRoleGate() {
    const isMember = !!(ME && ME.role && ME.role !== "admin");
    document.body.classList.toggle("is-member", isMember);
  }

  async function loadMembers() {
    const el = $("members-list");
    if (!el) return;
    const r = await fetch("/api/orgs/members");
    if (!r.ok) { el.innerHTML = `<p class="hint">Join or create an org to see members.</p>`; return; }
    const members = await r.json();
    el.innerHTML = members.map((m) => `
      <div class="item"><div class="top">
        <div><div class="name">${esc(m.name || m.email || m.user_id)}</div>
          <div class="uri">${esc(m.email || "")}</div></div>
        <div style="display:flex;gap:8px;align-items:center">
          <span class="badge">${esc(m.role)}</span>
          <span class="badge ${m.status === "active" ? "ready" : "pending"}">${esc(m.status)}</span>
        </div>
      </div></div>`).join("") || `<p class="hint">No members yet.</p>`;
  }

  function wireOrg() {
    const f = $("invite-form");
    if (f) {
      f.onsubmit = async (e) => {
        e.preventDefault();
        const d = new FormData(f);
        const r = await fetch("/api/orgs/invite", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email: d.get("email"), name: d.get("name") || null }),
        });
        const data = await r.json().catch(() => ({}));
        if (r.ok) { f.reset(); loadMembers(); }
        else alert(data.error || "Invite failed");
      };
    }
    document.querySelectorAll('.tab[data-section="organization"]').forEach((t) =>
      t.addEventListener("click", loadMembers));
  }

  async function init() {
    ME = await fetchMe();
    window.ME = ME;  // expose for app.js if it wants role info
    if (ME.auth_enabled === true && !ME.authed) return renderLogin();
    if (ME.authed && ME.auth_enabled === true && !ME.org) return renderOnboarding();
    hideGate();
    applyRoleGate();
    renderUserbar();
    wireOrg();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
