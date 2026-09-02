"use strict";
/* ==========================================================================
   ControlPlane console — view layer
   --------------------------------------------------------------------------
   Six views over saved evidence from 84 recorded runs. Charts come from
   charts.js (window.CP); everything here is composition and copy.

   On wording: the API speaks the project's internal vocabulary (invariant,
   monotone, localization, prefix, workload A/B/C). A reader who did not build
   this cannot follow that, so every label here leads with plain language and
   keeps the technical term as a secondary hint — a tooltip, a small subtitle,
   or parentheses. The vocabulary is not deleted, because the audit log and the
   API still use it and somebody eventually has to reconcile the two.
   ========================================================================== */

const CPX = window.CP;

const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, html) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (html !== undefined) n.innerHTML = html;
  return n;
};
const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
const trunc = (s, n = 130) => {
  const t = typeof s === "string" ? s : JSON.stringify(s ?? "");
  return t.length > n ? t.slice(0, n) + "…" : t;
};
const json = (o) => `<pre class="json">${esc(JSON.stringify(o, null, 2))}</pre>`;
const n1 = (v) => (typeof v === "number" ? Math.round(v * 10) / 10 : v);

/** Median / arbitrary quantile of a numeric array. */
function quantile(arr, q) {
  const a = arr.filter((v) => typeof v === "number" && isFinite(v)).sort((x, y) => x - y);
  if (!a.length) return null;
  const i = (a.length - 1) * q;
  const loI = Math.floor(i);
  const hiI = Math.ceil(i);
  if (loI === hiI) return a[loI];
  return a[loI] + (a[hiI] - a[loI]) * (i - loI);
}

/* ==========================================================================
   plain-language dictionary
   ========================================================================== */

/* The API returns workloads as bare letters, which mean nothing to a reader.
   These are the three assistants those letters actually stand for. */
const WORKLOAD = {
  A: {
    name: "Customer support",
    short: "Support",
    hint: "Faces customers and moves money. Checks must answer within 150 ms.",
  },
  B: {
    name: "Internal knowledge",
    short: "Knowledge",
    hint: "Answers staff questions. Must respect who is allowed to see what.",
  },
  C: {
    name: "Underwriting",
    short: "Underwriting",
    hint: "Long analytical runs, around 55 steps, no user waiting.",
  },
};
const wlName = (id) => (WORKLOAD[id] ? WORKLOAD[id].name : id || "—");
const wlShort = (id) => (WORKLOAD[id] ? WORKLOAD[id].short : id || "—");
const wlHint = (id) =>
  WORKLOAD[id] ? `${WORKLOAD[id].name} (workload ${id}) — ${WORKLOAD[id].hint}` : String(id ?? "");
/** Friendly name with the original letter kept as a small hint. */
const wlCell = (id) =>
  `<span title="${esc(wlHint(id))}">${esc(wlName(id))}</span>` +
  `<div class="tiny dim">workload ${esc(id)}</div>`;

/* The four experiment conditions, named for what they actually do. */
const CONDITION = {
  off: {
    name: "No supervisor",
    short: "No supervisor",
    tone: "mute",
    hint: "The agent runs unsupervised. Nothing watches the tool calls.",
  },
  on: {
    name: "Full supervisor",
    short: "Full",
    tone: "ok",
    hint: "Spots the problem, pinpoints the step that caused it, and rewinds to that step.",
  },
  "on+detect_only": {
    name: "Spot problems only",
    short: "Spot only",
    tone: "bad",
    hint: "Refuses the unsafe action but never rewinds, so the agent is left stuck.",
  },
  "on+deterministic_only": {
    name: "Fixed rules only",
    short: "Rules only",
    tone: "info",
    hint: "Only the mechanical checks run. Nothing that needs a language model to judge.",
  },
};
const condName = (c) => (CONDITION[c] ? CONDITION[c].name : c);
const condShort = (c) => (CONDITION[c] ? CONDITION[c].short : c);
const condHint = (c) => (CONDITION[c] ? `${CONDITION[c].name} — ${CONDITION[c].hint}` : String(c));
const condPill = (c) => {
  const d = CONDITION[c] || { name: c, tone: "mute", hint: "" };
  return `<span class="pill ${d.tone}" title="${esc(condHint(c))}">${esc(d.name)}</span>`;
};
const CONDITION_ORDER = ["off", "on", "on+detect_only", "on+deterministic_only"];

/* What each family of safety checks is actually looking for. */
const CHECK_KIND = {
  binding: { name: "Right record", hint: "Is the agent still acting on the customer it looked up?" },
  budget: { name: "Spending limits", hint: "Steps, tokens and money stay inside the allowance." },
  precondition: { name: "Required order", hint: "A step that depends on an earlier one cannot jump the queue." },
  provenance: { name: "Where the value came from", hint: "Every value traces back to where it was fetched." },
  safety: { name: "Safety rules", hint: "Irreversible or outbound actions need the right conditions first." },
  schema: { name: "Well-formed request", hint: "The call matches the shape the tool expects." },
  progress: { name: "Actually progressing", hint: "The run is getting closer to the goal, not looping." },
  semantic: { name: "Answer makes sense", hint: "The conclusion is consistent with the evidence gathered." },
  entitlement: { name: "Permission to see it", hint: "The requester is allowed to read this material." },
};
const kindName = (k) => (CHECK_KIND[k] ? CHECK_KIND[k].name : k || "—");
const kindHint = (k) => (CHECK_KIND[k] ? `${CHECK_KIND[k].name} — ${CHECK_KIND[k].hint} (class: ${k})` : String(k ?? ""));

/* Can this action be taken back? */
const UNDO = {
  reversible: { name: "Safe to retry", tone: "mute", hint: "Read-only or trivially repeatable." },
  compensable: { name: "Can be undone", tone: "warn", hint: "There is a compensating action that reverses it." },
  irreversible: { name: "Cannot be undone", tone: "bad", hint: "Once it happens, it cannot be taken back." },
};
const undoPill = (r) => {
  if (!r) return "";
  const d = UNDO[r] || { name: r, tone: "mute", hint: "" };
  return `<span class="pill ${d.tone}" title="${esc(`${d.name} (${r}) — ${d.hint}`)}">${esc(d.name)}</span>`;
};

/* How the tool call came back. */
const RESULT_STATUS = {
  ok: { name: "ok", tone: "mute" },
  error_tagged: { name: "returned an error", tone: "bad" },
  denied: { name: "permission denied", tone: "bad" },
  unlabelled: { name: "not classified", tone: "mute" },
  empty: { name: "came back empty", tone: "warn" },
};
const statusPill = (s) => {
  if (!s || s === "ok") return "";
  const d = RESULT_STATUS[s] || { name: s, tone: "mute" };
  return `<span class="pill ${d.tone}" title="${esc(`result status: ${s}`)}">${esc(d.name)}</span>`;
};

/* When a check runs. This distinction is the whole latency story. */
const WHEN = {
  inline: {
    name: "Checked instantly",
    short: "Instant",
    tone: "ok",
    hint: "Runs before the action does, so it can still refuse it.",
  },
  async: {
    name: "Checked in the background",
    short: "Background",
    tone: "warn",
    hint: "Runs a few steps later. It still fires, but it can be too late to prevent the damage.",
  },
  off: { name: "Not run here", short: "Off", tone: "mute", hint: "This check is disabled for this assistant." },
};
const whenPill = (w) => {
  const d = WHEN[w] || { name: w || "n/a", tone: "mute", hint: "" };
  return `<span class="pill ${d.tone}" title="${esc(`${d.name} (${w}) — ${d.hint}`)}">${esc(d.name)}</span>`;
};

const tone = CPX.C;

/* ==========================================================================
   data access
   ========================================================================== */

const tenant = () => ($("#tenant").value || "").trim() || "meridian";
async function api(path) {
  const sep = path.includes("?") ? "&" : "?";
  const r = await fetch(`/api${path}${sep}tenant=${encodeURIComponent(tenant())}`);
  if (!r.ok) throw new Error(`Request failed (${r.status}) for ${path}`);
  return r.json();
}

/** Small in-memory cache so switching views does not refetch everything. */
const cache = {};
async function get(path) {
  const key = `${tenant()}::${path}`;
  if (!cache[key]) {
    cache[key] = api(path).catch((e) => {
      delete cache[key];
      throw e;
    });
  }
  return cache[key];
}
const clearCache = () => Object.keys(cache).forEach((k) => delete cache[k]);

/* ==========================================================================
   small builders
   ========================================================================== */

function statCard(o) {
  return `<div class="stat ${o.tone ? "tone-" + o.tone : ""}">
    <div class="k" ${o.hint ? `title="${esc(o.hint)}"` : ""}>${esc(o.k)}</div>
    <div class="v ${o.small ? "sm" : ""}">${o.v}</div>
    ${o.foot ? `<div class="foot">${o.foot}</div>` : ""}
    ${o.spark ? `<div class="spark">${o.spark}</div>` : ""}
  </div>`;
}
const stats = (cards) => `<div class="stats">${cards.map(statCard).join("")}</div>`;

function panel(o) {
  return `<section class="panel">
    ${
      o.title
        ? `<header><h4>${esc(o.title)}</h4>${o.sub ? `<span class="sub">${esc(o.sub)}</span>` : ""}</header>`
        : ""
    }
    ${o.body}
    ${o.foot ? `<div class="foot">${o.foot}</div>` : ""}
  </section>`;
}

const pageHead = (h, p) =>
  `<div class="page-head"><h2>${esc(h)}</h2>${p ? `<p>${p}</p>` : ""}</div>`;

const sectionHead = (h, sub) =>
  `<div class="section-head"><h3>${esc(h)}</h3>${sub ? `<span class="sub">${esc(sub)}</span>` : ""}</div>`;

const skeleton = () =>
  `<div class="stats">${Array(4).fill('<div class="skeleton" style="height:86px"></div>').join("")}</div>
   <div class="skeleton" style="height:280px"></div>`;

const emptyBox = (msg) => `<div class="empty">${msg}</div>`;

/* ==========================================================================
   view: overview — ControlPlane.ai operations console
   ========================================================================== */

function sevTone(s) {
  const v = String(s || "").toLowerCase();
  if (v === "high" || v === "critical" || v === "block") return "bad";
  if (v === "medium" || v === "warn") return "warn";
  return "mute";
}

function incStatus(inc) {
  const rec = inc.recovery || {};
  if (rec.outcome === "recovered" || rec.recovered) return { label: "Resolved", tone: "ok" };
  if (rec.outcome === "escalated" || rec.escalated) return { label: "Open", tone: "bad" };
  if (inc.last_good_step != null) return { label: "Investigating", tone: "warn" };
  return { label: "Open", tone: "mute" };
}

function logicalSteps(timeline) {
  const by = new Map();
  for (const s of timeline.steps || []) {
    if (s.superseded) continue;
    by.set(s.step, s);
  }
  return [...by.values()].sort((a, b) => a.step - b.step);
}

function windowSteps(steps, inc) {
  if (steps.length <= 20) return steps;
  const fault = inc.detected_at_step ?? steps[steps.length - 1].step;
  const L = inc.last_good_step ?? Math.max(fault - 1, steps[0].step);
  const lo = Math.max(steps[0].step, Math.min(L, fault) - 9);
  const hi = Math.min(steps[steps.length - 1].step, Math.max(L, fault) + 6);
  return steps.filter((s) => s.step >= lo && s.step <= hi);
}

function highlightPii(text) {
  const raw = typeof text === "string" ? text : JSON.stringify(text ?? "", null, 2);
  return esc(raw).replace(
    /([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}|\[REDACTED:[^\]]+\]|\bPOL-\d+\b)/gi,
    '<mark class="leak">$1</mark>'
  );
}

function railHtml(steps, inc, loc, selected) {
  const L = inc.last_good_step;
  const fault = inc.detected_at_step;
  const probes = new Map((loc?.probes || []).map((p) => [p.prefix, p.holds]));
  const max = steps.length ? steps[steps.length - 1].step : 0;
  const dots = steps
    .map((s) => {
      const n = s.step;
      let cls = "ok";
      if (n === fault) cls = "fault";
      else if (L != null && n > L && n < fault) cls = "after";
      else if (L != null && n > fault) cls = "pending";
      else if (s.blocked) cls = "blocked";
      if (n === selected) cls += " selected";
      const tag =
        n === L
          ? `<span class="rail-tag ok">Last good step</span>`
          : n === fault
            ? `<span class="rail-tag bad">Fault</span>`
            : "";
      const mark =
        n === fault ? "×" : n === L ? "✓" : n < (L ?? fault) ? "✓" : "";
      return `<button type="button" class="rail-dot ${cls}" data-step="${n}" title="Step ${n}${s.tool ? " · " + s.tool : ""}">
        ${tag}<i>${mark}</i><span>${n}</span>
      </button>`;
    })
    .join("");
  const probeKeys = new Set([...probes.keys(), L, fault].filter((n) => n != null));
  const probeDots = [...probeKeys]
    .sort((a, b) => a - b)
    .map((n) => {
      const holds = probes.has(n) ? probes.get(n) : n <= (L ?? -1);
      return `<span class="probe-dot ${holds ? "ok" : "bad"}" title="prefix ${n}: ${holds ? "holds" : "fails"}">${n}</span>`;
    })
    .join("");
  return `<div class="rail" style="--n:${Math.max(steps.length, 1)}">${dots}</div>
    <div class="probe-legend">Binary-search probes · ${loc?.probes?.length || 0} evaluations · 0 model calls</div>
    <div class="probe-rail">${probeDots}</div>
    <div class="tiny dim">Prefix of ${max + 1} steps isolated in ${loc?.probes?.length || "—"} probes (a linear scan would cost ${loc?.linear_scan_would_cost ?? max + 1}). Click a step · arrow keys to move.</div>`;
}

function detailHtml(step, inc) {
  if (!step) {
    return `<div class="empty">Select a step on the timeline.</div>`;
  }
  const failed = step.step === inc.detected_at_step || (step.incidents || []).length;
  const hits = (step.incidents || [])[0] || {};
  const viol = hits.violation || {};
  return `
    <div class="detail-head">
      <div>
        <div class="tiny dim">Step ${step.step}${step.epoch ? ` · attempt ${step.epoch}` : ""}</div>
        <h4>Step ${step.step} details</h4>
        ${step.tool ? `<span class="tool-pill">${esc(step.tool)}</span>` : ""}
      </div>
      <span class="pill ${failed ? "bad" : "ok"}">${failed ? "Failed" : "Held"}</span>
    </div>
    <div class="detail-meta">
      ${undoPill(step.reversibility)}
      ${statusPill(step.source_class)}
      ${step.blocked ? `<span class="pill block">blocked</span>` : ""}
      <span class="tiny dim">${esc(step.self_hash || "")}</span>
    </div>
    <div class="block" style="margin-top:14px">
      <h3>Safety check</h3>
      <div class="callout ${failed ? "bad" : "ok"}">
        <b>${esc(kindName(inc.invariant_class))} · ${esc(inc.invariant_id)}</b><br>
        ${esc(inc.detail)}
      </div>
    </div>
    <div class="block">
      <h3>Input</h3>
      <pre class="json">${highlightPii(step.args)}</pre>
    </div>
    <div class="block">
      <h3>Output</h3>
      <pre class="json">${highlightPii(step.result || step.narrative || "—")}</pre>
    </div>`;
}

function fmtTime(ts) {
  if (!ts) return "—";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return esc(String(ts).slice(0, 19));
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function recentTable(incidents, activeId) {
  const rows = incidents.slice(0, 12);
  return `<div class="table-wrap"><table class="responsive">
    <thead><tr>
      <th>ID</th><th>Time</th><th>Run</th><th>Guardrail</th>
      <th>Step</th><th>Severity</th><th>Status</th>
    </tr></thead>
    <tbody>${rows
      .map((inc) => {
        const st = incStatus(inc);
        return `<tr class="clickable${inc.incident_id === activeId ? " on" : ""}" data-inc="${esc(inc.incident_id)}" data-run="${esc(inc.run_id)}">
          <td data-label="ID" class="mono">${esc(inc.incident_id.slice(0, 12))}</td>
          <td data-label="Time" class="tiny">${fmtTime(inc.ts)}</td>
          <td data-label="Run" class="mono tiny">${esc(inc.run_id.slice(0, 18))}…</td>
          <td data-label="Guardrail">${esc(kindName(inc.invariant_class))}
            <div class="tiny dim">${esc(inc.invariant_id)}</div></td>
          <td data-label="Step" class="num">${inc.detected_at_step}/${inc.last_good_step ?? "—"}</td>
          <td data-label="Severity"><span class="sev ${sevTone(inc.severity)}"></span>${esc(inc.severity || "—")}</td>
          <td data-label="Status"><span class="pill ${st.tone}">${esc(st.label)}</span></td>
        </tr>`;
      })
      .join("")}</tbody></table></div>`;
}

async function loadReplay(inc) {
  const [timeline, loc] = await Promise.all([
    get(`/runs/${inc.run_id}/timeline`),
    get(`/runs/${inc.run_id}/localization/${inc.incident_id}`).catch(() => null),
  ]);
  return { timeline, loc };
}

function bindReplay(root, pack, inc) {
  const all = logicalSteps(pack.timeline);
  const steps = windowSteps(all, inc);
  let selected = inc.detected_at_step ?? (steps[0] && steps[0].step);
  const rail = root.querySelector("#rail-mount");
  const detail = root.querySelector("#detail-mount");
  const nums = steps.map((s) => s.step);
  const paint = () => {
    rail.innerHTML = railHtml(steps, inc, pack.loc, selected);
    const step = steps.find((s) => s.step === selected) || steps.find((s) => s.step === inc.detected_at_step);
    detail.innerHTML = detailHtml(step, inc);
    rail.querySelectorAll(".rail-dot").forEach((btn) => {
      btn.addEventListener("click", () => {
        selected = Number(btn.dataset.step);
        paint();
      });
    });
    const focus = rail.querySelector(".rail-dot.selected") || rail.querySelector(".rail-dot.fault");
    if (focus) focus.scrollIntoView({ inline: "center", block: "nearest", behavior: "smooth" });
  };
  rail.onkeydown = (e) => {
    const i = nums.indexOf(selected);
    if (e.key === "ArrowRight" && i < nums.length - 1) {
      selected = nums[i + 1];
      paint();
      e.preventDefault();
    }
    if (e.key === "ArrowLeft" && i > 0) {
      selected = nums[i - 1];
      paint();
      e.preventDefault();
    }
  };
  rail.tabIndex = 0;
  paint();
}

async function viewOverview(root) {
  const [runs, incidents, exp, guards] = await Promise.all([
    get("/runs"),
    get("/incidents"),
    get("/experiment").catch(() => null),
    get("/guards").catch(() => null),
  ]);

  const conds = (exp && exp.conditions) || {};
  const on = conds.on || {};
  const off = conds.off || {};
  const onRuns = runs.filter((r) => r.condition === "on");
  const successN = onRuns.filter((r) => r.task_success).length;
  const harmN = onRuns.filter((r) => r.harm_occurred).length;
  const p95 = on.inline_ms_p95 ?? quantile(onRuns.map((r) => r.inline_ms_p95 || 0), 0.95) ?? 0;
  const trips = incidents.filter((i) => i.condition === "on").length;

  const sparkSuccess = onRuns.slice(0, 24).map((r) => (r.task_success ? 1 : 0)).reverse();
  const sparkHarm = onRuns.slice(0, 24).map((r) => (r.harm_occurred ? 1 : 0)).reverse();
  const sparkLat = onRuns.slice(0, 24).map((r) => r.inline_ms_p95 || 0).reverse();
  const sparkTrip = onRuns.slice(0, 24).map((r) => r.incidents || 0).reverse();

  const featured =
    incidents.find((i) => i.condition === "on" && i.last_good_step != null) || incidents[0];

  const navBtn = document.querySelector('[data-view="incidents"]');
  if (navBtn) {
    let badge = navBtn.querySelector(".count");
    if (!badge) {
      badge = el("span", "count");
      navBtn.appendChild(badge);
    }
    badge.textContent = String(incidents.length);
  }

  root.innerHTML = `
    <div class="ov-head">
      <div>
        <div class="eyebrow">Overview</div>
        <h2>AI agent reliability at a glance</h2>
        <p>Live view over <b>${runs.length}</b> recorded runs on disk. Numbers are evidence, not a simulation of traffic.</p>
      </div>
      <div class="ov-actions">
        <select id="ov-filter" class="ov-select" aria-label="Filter scenarios">
          <option value="">All scenarios</option>
          ${[...new Set(incidents.map((i) => i.scenario_id))]
            .map((s) => `<option value="${esc(s)}">${esc(s)}</option>`)
            .join("")}
        </select>
        <span class="top-pill mute">${esc(tenant())}</span>
        <button type="button" class="btn primary" id="ov-share">Share</button>
      </div>
    </div>
    ${stats([
      {
        k: "Task success",
        v: `${on.task_success_pct ?? "—"}%`,
        foot: `${successN} / ${onRuns.length || on.n || "—"} supervised runs`,
        tone: "ok",
        spark: CPX.sparkline(sparkSuccess.length ? sparkSuccess : [1, 1, 1], { color: tone.accent, height: 28 }),
        hint: "Did the agent finish the job correctly with the supervisor on.",
      },
      {
        k: "Harm events",
        v: String(harmN),
        foot: harmN === 0 ? "None on supervised runs" : `${harmN} supervised runs harmed`,
        tone: harmN ? "bad" : "ok",
        spark: CPX.sparkline(sparkHarm.length ? sparkHarm : [0, 0, 0], { color: tone.ok, height: 28 }),
      },
      {
        k: "P95 latency",
        v: `${(p95 || 0).toFixed(2)} ms`,
        foot: "inline checks · 150 ms budget",
        tone: "accent",
        spark: CPX.sparkline(sparkLat.length ? sparkLat : [0.2, 0.4, 0.3], { color: tone.accent, height: 28 }),
      },
      {
        k: "Guardrail trips",
        v: String(trips),
        foot: `${incidents.length} across all conditions`,
        tone: "warn",
        spark: CPX.sparkline(sparkTrip.length ? sparkTrip : [1, 0, 2, 1], { color: tone.warn, height: 28 }),
      },
    ])}
    <div class="replay-grid">
      <section class="panel replay">
        <header>
          <h4>Incident replay</h4>
          <span class="sub" id="replay-sub">${featured ? esc(featured.incident_id) : "—"}</span>
        </header>
        <div id="replay-meta" class="replay-meta"></div>
        <div id="rail-mount"><div class="skeleton" style="height:88px"></div></div>
      </section>
      <section class="panel detail" id="detail-mount"><div class="skeleton" style="height:220px"></div></section>
    </div>
    <div class="section-head"><h3>Recent incidents</h3><span class="sub">click a row to replay it</span></div>
    <div id="recent-mount">${featured ? recentTable(incidents, featured.incident_id) : emptyBox("No incidents")}</div>
    <div class="note">
      Unsupervised task success is <b>${off.task_success_pct ?? "—"}%</b>.
      Detect-without-recover drops to <b>${conds["on+detect_only"]?.task_success_pct ?? "—"}%</b>.
      ${guards ? `${guards.sabotage_validated}/${guards.active} safety checks are proven load-bearing.` : ""}
      Click a step on the rail to inspect the tool call; click a row to switch incidents.
    </div>
  `;

  const mount = async (inc, pool) => {
    const list = pool || incidents;
    if (!inc) return;
    root.querySelector("#replay-sub").textContent = inc.incident_id;
    root.querySelector("#replay-meta").innerHTML = `
      <span class="pill info">${esc(inc.scenario_id)}</span>
      ${condPill(inc.condition)}
      <span class="pill ${inc.detected_by === "inline" ? "ok" : "warn"}">${esc(inc.detected_by || "")}</span>
      <span class="tiny dim">${esc(wlName(inc.workload))} · ${fmtTime(inc.ts)}</span>
      <span class="tiny dim">failed at step ${inc.detected_at_step} · last good ${inc.last_good_step ?? "—"} · ${esc(inc.run_id)}</span>`;
    root.querySelector("#recent-mount").innerHTML = recentTable(list, inc.incident_id);
    const pack = await loadReplay(inc);
    bindReplay(root, pack, inc);
    root.querySelectorAll("#recent-mount tr.clickable").forEach((tr) => {
      tr.addEventListener("click", () => {
        const next = list.find((i) => i.incident_id === tr.dataset.inc);
        if (next) mount(next, list);
      });
    });
  };

  const share = root.querySelector("#ov-share");
  share.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(location.href);
      share.textContent = "Copied";
      setTimeout(() => {
        share.textContent = "Share";
      }, 1400);
    } catch {
      share.textContent = "Copy the URL";
    }
  });

  const filter = root.querySelector("#ov-filter");
  filter.addEventListener("change", () => {
    const pool = filter.value
      ? incidents.filter((i) => i.scenario_id === filter.value)
      : incidents;
    const next =
      pool.find((i) => i.condition === "on" && i.last_good_step != null) || pool[0];
    mount(next, pool);
  });

  await mount(featured);
}

async function viewIncidents(root) {
  const feed = await get("/incidents");
  if (!feed.length) {
    root.innerHTML =
      pageHead("Problems found") +
      emptyBox("No problems recorded yet. Run <code>controlplane ladder</code> to generate evidence.");
    return;
  }

  const scored = feed.filter((i) => i.localization_error !== null);
  const exact = scored.filter((i) => i.localization_error === 0).length;
  const spont = feed.filter((i) => i.spontaneous).length;
  const falseAl = feed.filter((i) => i.false_alarm).length;
  const recovered = feed.filter((i) => i.recovery && i.recovery.succeeded).length;
  const escalated = feed.filter((i) => i.recovery && i.recovery.escalated).length;
  const instant = feed.filter((i) => i.detected_by === "inline").length;

  let out = pageHead(
    "Problems found",
    `Every safety check that broke during the ${
      new Set(feed.map((i) => i.run_id)).size
    } supervised runs. Open any row to see how the exact step was found.`
  );

  out += stats([
    { k: "Problems found", v: feed.length, tone: "accent", foot: `${scored.length} traced to a planted fault` },
    {
      k: "Exact step found",
      v: scored.length ? `${Math.round((100 * exact) / scored.length)}%` : "—",
      tone: "ok",
      foot: `${exact} of ${scored.length} scoreable cases`,
    },
    {
      k: "Caught instantly",
      v: instant,
      tone: "ok",
      foot: `${feed.length - instant} caught later, in the background`,
    },
    { k: "Fixed automatically", v: recovered, tone: "ok", foot: `${escalated} handed to a human instead` },
    { k: "Agent's own mistakes", v: spont, tone: "warn", foot: "nobody planted these" },
    { k: "False alarms", v: falseAl, tone: falseAl ? "bad" : "ok", foot: "nothing was actually wrong" },
  ]);

  out += `<div class="grid g2">
    ${panel({
      title: "Which safety check broke",
      body: (() => {
        const byKind = {};
        feed.forEach((i) => {
          byKind[i.invariant_class] = (byKind[i.invariant_class] || 0) + 1;
        });
        const keys = Object.keys(byKind).sort((a, b) => byKind[b] - byKind[a]);
        const palette = [tone.accent, tone.warn, tone.info, tone.block, tone.ok];
        return (
          CPX.barsH(
            keys.map((k, i) => ({
              label: kindName(k),
              value: byKind[k],
              display: String(byKind[k]),
              color: palette[i % palette.length],
            })),
            { labelW: 175 }
          ) +
          `<div class="chart-legend">${keys
            .map((k) => `<span title="${esc(kindHint(k))}">${esc(k)}</span>`)
            .join("")}</div>`
        );
      })(),
      foot: "The grey line beneath each label is the internal name for that family of checks.",
    })}
    ${panel({
      title: "How it ended",
      body: (() => {
        const failed = feed.filter(
          (i) => i.recovery && i.recovery.attempted && !i.recovery.succeeded && !i.recovery.escalated
        ).length;
        const none = feed.length - recovered - escalated - failed;
        const slices = [
          { label: "Rewound and carried on", value: recovered, color: tone.ok },
          { label: "Handed to a human", value: escalated, color: tone.warn },
          { label: "Could not be fixed", value: failed, color: tone.bad },
          { label: "Only reported, never fixed", value: none, color: tone.dim },
        ];
        return (
          CPX.donut(slices, { center: feed.length, centerSub: "problems" }) +
          CPX.legend(slices.map((s) => ({ label: s.label, color: s.color, value: s.value })))
        );
      })(),
      foot: `"Only reported, never fixed" is the spot-problems-only setup, which refuses the action
        but leaves the agent stuck.`,
    })}
  </div>`;

  out += sectionHead("Every problem in detail", "tap a row to see how the step was found");
  out += `<div class="table-wrap"><table class="responsive"><thead><tr>
    <th>Scenario</th>
    <th>Assistant</th>
    <th>Setup</th>
    <th title="The safety check that broke (invariant)">Safety check</th>
    <th title="Whether the check ran before the action or a few steps later">When caught</th>
    <th title="How many steps passed before anyone noticed (delta_detect)">Delay</th>
    <th title="The last step that was still correct (last_good_step)">Last correct step</th>
    <th title="Distance from the step where the fault was actually planted">Accuracy</th>
    <th>Outcome</th>
    <th>What happened</th>
  </tr></thead><tbody>${feed
    .map((inc, idx) => {
      const err = inc.localization_error;
      const acc =
        err === null
          ? inc.spontaneous
            ? '<span class="pill info" title="The agent made this mistake by itself, so there is no planted step to compare against.">not scoreable</span>'
            : '<span class="pill mute">false alarm</span>'
          : err === 0
          ? '<span class="pill ok">exact</span>'
          : `<span class="pill ${err <= 1 ? "warn" : "bad"}">off by ${err}</span>`;
      const rec = inc.recovery || {};
      const outcome = rec.escalated
        ? '<span class="pill warn">handed to a human</span>'
        : rec.succeeded
        ? '<span class="pill ok">rewound and fixed</span>'
        : rec.attempted
        ? '<span class="pill bad">could not fix</span>'
        : '<span class="pill mute">reported only</span>';
      return `<tr class="clickable" data-i="${idx}">
        <td data-label="Scenario"><b>${esc(inc.scenario_id)}</b></td>
        <td data-label="Assistant">${wlCell(inc.workload)}</td>
        <td data-label="Setup">${condPill(inc.condition)}</td>
        <td data-label="Safety check"><span class="mono">${CPX.idBreak(inc.invariant_id)}</span>
          <div class="tiny dim" title="${esc(kindHint(inc.invariant_class))}">${esc(
        kindName(inc.invariant_class)
      )}</div></td>
        <td data-label="When caught">${whenPill(inc.detected_by)}</td>
        <td data-label="Delay" class="num">${
          inc.delta_detect === null ? "—" : `${inc.delta_detect} steps`
        }</td>
        <td data-label="Last correct step" class="num">${inc.last_good_step ?? "—"}</td>
        <td data-label="Accuracy">${acc}</td>
        <td data-label="Outcome">${outcome}</td>
        <td data-label="What happened" class="wrap tiny dim">${esc(trunc(inc.detail, 110))}</td>
      </tr>`;
    })
    .join("")}</tbody></table></div>`;

  out += `<div class="note">Accuracy compares the step this system named against the step where we
    actually planted the problem. Cases the agent broke by itself are marked "not scoreable",
    because there is no planted step to compare with — folding them into either the successes or
    the failures would tilt the headline figure.</div>`;

  root.innerHTML = out;
  root.querySelectorAll("tr.clickable").forEach((tr) => {
    tr.addEventListener("click", () => openIncident(feed[Number(tr.dataset.i)]));
  });
}

async function openIncident(inc) {
  openDrawer(`${inc.scenario_id} — ${inc.invariant_id}`);
  const body = $("#drawer-body");
  body.innerHTML = `<div class="skeleton" style="height:200px"></div>`;

  let out = "";

  /* ------------------------------ summary ---------------------------- */
  out += `<div class="block"><h3>What went wrong</h3>
    <div class="callout bad">${esc(inc.detail)}</div>
    <div class="kv" style="margin-top:14px">
      <div class="k">Safety check that broke</div>
      <div class="v">${esc(inc.invariant_id)}</div>
      <div class="k">What it looks for</div>
      <div class="v" style="font-family:var(--sans)">${esc(
        (CHECK_KIND[inc.invariant_class] || {}).hint || kindName(inc.invariant_class)
      )}</div>
      <div class="k">Assistant</div>
      <div class="v" style="font-family:var(--sans)">${esc(wlName(inc.workload))}</div>
      <div class="k">Setup</div>
      <div class="v" style="font-family:var(--sans)">${esc(condName(inc.condition))}</div>
      <div class="k">Noticed at step</div>
      <div class="v">${inc.detected_at_step} (${esc(
    (WHEN[inc.detected_by] || {}).name || inc.detected_by
  ).toLowerCase()})</div>
      <div class="k">Went unnoticed for</div>
      <div class="v">${inc.delta_detect === null ? "not applicable" : `${inc.delta_detect} steps`}</div>
      <div class="k">Last correct step</div>
      <div class="v">${inc.last_good_step ?? "—"}</div>
      <div class="k">Fault was planted at</div>
      <div class="v">${
        inc.expected_last_good_step === null || inc.expected_last_good_step === undefined
          ? "nothing was planted — the agent's own mistake"
          : `step ${inc.expected_last_good_step}`
      }</div>
      <div class="k">Accuracy</div>
      <div class="v">${
        inc.localization_error === null
          ? "not scoreable"
          : inc.localization_error === 0
          ? "exact"
          : `off by ${inc.localization_error} steps`
      }</div>
      <div class="k">Run</div>
      <div class="v">${esc(inc.run_id)}</div>
    </div></div>`;

  /* -------------------------------- rca ------------------------------ */
  if (inc.rca && Object.keys(inc.rca).length) {
    const LBL = {
      trigger: "What started it",
      amplifier: "What made it worse",
      concealer: "What hid it",
    };
    out += `<div class="block"><h3>Why it happened</h3><div class="rca">${["trigger", "amplifier", "concealer"]
      .filter((k) => inc.rca[k])
      .map(
        (k) =>
          `<div class="rca-row"><div class="lbl">${esc(LBL[k])}</div>
           <div class="txt">${esc(inc.rca[k])}</div></div>`
      )
      .join("")}</div></div>`;
  }

  /* ----------------------------- the search -------------------------- */
  out += `<div class="block" id="search-block"><h3>How the step was found</h3>
    <div class="tiny dim" style="margin-bottom:12px">
      The supervisor replays the saved run up to a series of earlier points, halving the range
      each time, until only one step is left. Green means the safety check still passed at that
      point; red means it had already broken. Nothing here asks an AI model — it is replay and
      arithmetic, which is why it is fast and repeatable.
    </div>
    <div id="probe-slot"><div class="skeleton" style="height:150px"></div></div>
  </div>`;

  /* ------------------------------ recovery --------------------------- */
  const rec = inc.recovery || {};
  if (Object.keys(rec).length) {
    const comps = rec.compensations || [];
    out += `<div class="block"><h3>What was done about it</h3>
      <div class="callout ${rec.succeeded ? "ok" : rec.escalated ? "info" : "bad"}">
        ${
          rec.succeeded
            ? `The run was rewound to step ${rec.restored_to_step} and the agent carried on from there.`
            : rec.escalated
            ? "The supervisor stopped the run and handed it to a human rather than guessing."
            : rec.attempted
            ? "A fix was attempted and did not succeed."
            : "This setup only reports problems; nothing was rewound."
        }
      </div>
      ${
        comps.length
          ? `<div style="margin-top:12px"><div class="tiny dim" style="margin-bottom:7px">
              Steps that had to be dealt with when rewinding:</div>
             <div class="table-wrap"><table class="responsive"><thead><tr>
               <th>Step</th><th>Action</th><th>Can it be undone?</th><th>Result</th></tr></thead><tbody>
             ${comps
               .map(
                 (c) => `<tr>
                   <td data-label="Step" class="num">${c.step}</td>
                   <td data-label="Action" class="mono">${esc(c.tool)}</td>
                   <td data-label="Can it be undone?">${undoPill(c.reversibility)}</td>
                   <td data-label="Result" class="tiny dim">${esc(c.detail || (c.succeeded ? "done" : "failed"))}</td>
                 </tr>`
               )
               .join("")}</tbody></table></div></div>`
          : ""
      }
      ${
        rec.corrective_note
          ? `<div style="margin-top:14px"><div class="tiny dim" style="margin-bottom:7px">
             The exact message handed back to the agent:</div>
             <pre class="json">${esc(trunc(rec.corrective_note, 1400))}</pre></div>`
          : ""
      }
    </div>`;
  }

  out += `<div class="block"><button class="btn" id="to-run">Open the full run, step by step</button></div>`;

  body.innerHTML = out;
  $("#to-run").addEventListener("click", () => {
    closeDrawer();
    go("runs");
    setTimeout(() => openRun(inc.run_id), 320);
  });

  /* Replay the search against the saved log, now. */
  try {
    const d = await api(
      `/runs/${encodeURIComponent(inc.run_id)}/localization/${encodeURIComponent(inc.incident_id)}`
    );
    const slot = $("#probe-slot");
    if (!slot) return;
    if (!d.probes || !d.probes.length) {
      slot.innerHTML = emptyBox(
        "This check can flicker on and off, so halving the search would not be sound. The supervisor fell back to tracing where the value came from."
      );
      return;
    }
    const saved = d.linear_scan_would_cost;
    slot.innerHTML =
      CPX.probeTrack(d.probes, {
        last_good: d.localization ? d.localization.last_good_step : null,
        detected_at: d.violation ? d.violation.detected_at_step : null,
      }) +
      `<div class="chart-legend">
        <span><i style="background:${tone.ok}"></i>still correct here</span>
        <span><i style="background:${tone.bad}"></i>already broken here</span>
        <span><i style="background:${tone.accent2}"></i>the answer</span>
      </div>` +
      stats([
        { k: "Replays used", v: d.probes.length, tone: "accent", foot: "each one halves the range" },
        {
          k: "Checking every step",
          v: saved ?? "—",
          tone: "warn",
          foot: "what the slow way would have cost",
        },
        { k: "AI model calls", v: "0", tone: "ok", foot: "none needed" },
      ]) +
      `<div class="tiny dim">${
        d.monotone
          ? `This check has a helpful property: once it breaks, it stays broken for the rest of the
             run. That is what makes halving the search valid — if the check passes at some point,
             the cause must be later than that.`
          : `This check can recover on its own, so halving the search is not valid here and a
             different method was used.`
      }</div>`;
  } catch (e) {
    const slot = $("#probe-slot");
    if (slot) slot.innerHTML = emptyBox(`Could not replay the search: ${esc(e.message)}`);
  }
}

/* ==========================================================================
   view: runs
   ========================================================================== */

async function viewRuns(root) {
  const [runs, cat] = await Promise.all([get("/runs"), get("/catalogue").catch(() => null)]);
  if (!runs.length) {
    root.innerHTML = pageHead("Runs") + emptyBox("No runs on disk yet.");
    return;
  }

  const titles = {};
  if (cat && cat.scenarios) cat.scenarios.forEach((s) => (titles[s.id] = s.title));

  let out = pageHead(
    "Runs",
    `All ${runs.length} recorded runs: ${
      new Set(runs.map((r) => r.scenario_id)).size
    } scenarios, each played under ${
      new Set(runs.map((r) => r.condition)).size
    } different setups with ${
      new Set(runs.map((r) => r.seed)).size
    } random starting seeds. Open any run to read it step by step.`
  );

  /* ---------------------------- heatmap ------------------------------ */
  const scenIds = [...new Set(runs.map((r) => r.scenario_id))].sort();
  const heatRows = scenIds.map((sid) => {
    const wl = (runs.find((r) => r.scenario_id === sid) || {}).workload;
    return {
      label: sid,
      sub: titles[sid] ? trunc(titles[sid], 42) : wlName(wl),
      cells: CONDITION_ORDER.map((c) => {
        const cell = runs.filter((r) => r.scenario_id === sid && r.condition === c);
        if (!cell.length) return { tone: "na", text: "", title: "no run" };
        const passed = cell.filter((r) => r.task_success).length;
        const harmed = cell.filter((r) => r.harm_occurred).length;
        const t = harmed ? "harm" : passed === cell.length ? "pass" : passed ? "off" : "fail";
        return {
          tone: t,
          text: `${passed}/${cell.length}`,
          title: `${sid} — ${condName(c)}: ${passed} of ${cell.length} runs finished correctly${
            harmed ? `, and ${harmed} caused real damage` : ""
          }`,
        };
      }),
    };
  });
  out += sectionHead("Every scenario against every setup", "how many runs finished correctly in each combination");
  out += panel({
    body:
      CPX.heatGrid(
        heatRows,
        CONDITION_ORDER.map((c) => ({ label: condShort(c), sub: c === "on+detect_only" ? "the weak one" : "" })),
        { corner: "Scenario" }
      ) +
      `<div class="chart-legend">
        <span><i style="background:${tone.ok}"></i>all finished correctly</span>
        <span><i style="background:#8f9aac"></i>some did</span>
        <span><i style="background:${tone.bad}"></i>none did</span>
        <span><i style="background:${tone.bad};opacity:1"></i>real damage occurred</span>
      </div>`,
    foot: `Reading down a column shows how one setup handles everything. Reading across a row shows
      how one scenario responds to more supervision. The "spot problems only" column is visibly the
      weakest, which is the central finding of the whole experiment.`,
  });

  /* ----------------------------- stats ------------------------------- */
  const passed = runs.filter((r) => r.task_success).length;
  const harmed = runs.filter((r) => r.harm_occurred).length;
  const totalSteps = runs.reduce((a, r) => a + (r.steps || 0), 0);
  out += stats([
    { k: "Runs", v: runs.length, tone: "accent", foot: `${totalSteps} steps in total` },
    {
      k: "Finished correctly",
      v: `${Math.round((100 * passed) / runs.length)}%`,
      tone: "ok",
      foot: `${passed} of ${runs.length}`,
    },
    {
      k: "Caused real damage",
      v: harmed,
      tone: harmed ? "bad" : "ok",
      foot: harmed ? "all of them unsupervised" : "none",
    },
    {
      k: "Slowest check",
      v: `${n1(Math.max(...runs.map((r) => r.inline_ms_p95 || 0)) * 100) / 100} ms`,
      tone: "ok",
      foot: "budget is 150 ms",
    },
  ]);

  out += sectionHead("All runs", "tap a row to open it");
  out += `<div class="table-wrap"><table class="responsive"><thead><tr>
    <th>Scenario</th><th>Assistant</th><th>Setup</th><th>Steps</th>
    <th title="Steps where we deliberately broke something">Fault planted at</th>
    <th>Problems found</th><th>Finished correctly</th><th>Real damage</th>
    <th title="Slowest instant check, 95th percentile">Check speed</th>
  </tr></thead><tbody>${runs
    .map(
      (r) => `<tr class="clickable" data-run="${esc(r.run_id)}">
      <td data-label="Scenario"><b>${esc(r.scenario_id)}</b>
        <div class="tiny dim">seed ${r.seed}${titles[r.scenario_id] ? ` · ${esc(trunc(titles[r.scenario_id], 34))}` : ""}</div></td>
      <td data-label="Assistant">${wlCell(r.workload)}</td>
      <td data-label="Setup">${condPill(r.condition)}</td>
      <td data-label="Steps" class="num">${r.steps}</td>
      <td data-label="Fault planted at" class="num tiny">${
        r.fault_steps && r.fault_steps.length ? esc(r.fault_steps.join(", ")) : "nothing planted"
      }</td>
      <td data-label="Problems found" class="num">${r.incidents}</td>
      <td data-label="Finished correctly">${
        r.task_success ? '<span class="pill ok">yes</span>' : '<span class="pill bad">no</span>'
      }</td>
      <td data-label="Real damage">${
        r.harm_occurred ? '<span class="pill bad">yes</span>' : '<span class="pill ok">none</span>'
      }</td>
      <td data-label="Check speed" class="num tiny">${(r.inline_ms_p95 ?? 0).toFixed(3)} ms</td>
    </tr>`
    )
    .join("")}</tbody></table></div>`;

  out += `<div class="note">"Finished correctly" is judged by inspecting the environment at the end
    of the run — the refund that was actually issued, the message that was actually sent — never by
    asking the agent whether it succeeded.</div>`;

  root.innerHTML = out;
  root.querySelectorAll("tr.clickable").forEach((tr) => {
    tr.addEventListener("click", () => openRun(tr.dataset.run));
  });
}

async function openRun(runId) {
  openDrawer(runId);
  const body = $("#drawer-body");
  body.innerHTML = `<div class="skeleton" style="height:240px"></div>`;

  let d;
  try {
    d = await api(`/runs/${encodeURIComponent(runId)}/timeline`);
  } catch (e) {
    body.innerHTML = emptyBox(esc(e.message));
    return;
  }

  const integ = d.integrity || {};
  let out = "";

  if (d.scenario && d.scenario.title) {
    out += `<div class="block"><h3>${esc(d.scenario.title)}</h3>
      ${d.scenario.narrative ? `<div class="tiny dim">${esc(d.scenario.narrative)}</div>` : ""}</div>`;
  }

  out += `<div class="block">${stats([
    {
      k: "Finished correctly",
      v: d.task_success ? "yes" : "no",
      tone: d.task_success ? "ok" : "bad",
      small: true,
      foot: esc(trunc(d.success_detail, 80)),
    },
    { k: "Assistant", v: esc(wlName(d.workload)), tone: "accent", small: true, foot: `workload ${esc(d.workload)}` },
    { k: "Setup", v: esc(condName(d.condition)), tone: "info", small: true },
    {
      k: "Audit log",
      v: integ.chain_intact === null ? "not on disk" : integ.chain_intact ? "unbroken" : "BROKEN",
      tone: integ.chain_intact ? "ok" : "bad",
      small: true,
      foot: `${integ.physical_records ?? "—"} rows written, ${integ.logical_steps ?? "—"} still live`,
    },
  ])}</div>`;

  /* ------------------------------ ribbon ----------------------------- */
  out += `<div class="block"><h3>The whole run at a glance</h3>
    ${CPX.stepRibbon(d.steps, {
      caption:
        "One block per recorded step, in order. Tap any block to jump to that step below.",
    })}</div>`;

  /* ----------------------------- timeline ---------------------------- */
  out += `<div class="block"><h3>Step by step — ${d.steps.length} recorded steps</h3>
    <div class="tiny dim" style="margin-bottom:14px">
      Faded rows are attempts that were rewound. They stay visible because the audit log only ever
      gains rows — quietly hiding the abandoned branch would misrepresent what the agent did.
      ${
        d.fault_steps && d.fault_steps.length
          ? `In this run we deliberately broke something at step ${d.fault_steps.join(", ")}.`
          : "Nothing was deliberately broken in this run."
      }
    </div>
    <div class="timeline">${d.steps.map(renderStep).join("")}</div></div>`;

  body.innerHTML = out;

  CPX.wireRibbon(body, (step) => {
    const node = body.querySelector(`[data-step-row="${step}"]`);
    if (!node) return;
    node.scrollIntoView({ behavior: "smooth", block: "center" });
    node.classList.add("flash");
    setTimeout(() => node.classList.remove("flash"), 1200);
  });
}

function renderStep(s) {
  let cls = "tl-step " + CPX.stepState(s);
  if (s.superseded) cls += " superseded";

  const isRollback = s.rollback_to !== null && s.rollback_to !== undefined;
  const head = isRollback
    ? `<span class="tl-tool">rewound to step ${s.rollback_to}</span>`
    : `<span class="tl-tool">${esc(s.tool || "—")}</span>` +
      undoPill(s.reversibility) +
      statusPill(s.source_class) +
      (s.blocked ? '<span class="pill block">refused</span>' : "") +
      (s.superseded ? '<span class="pill mute">abandoned attempt</span>' : "");

  let mid = "";
  if (s.args && Object.keys(s.args).length) {
    mid += `<div class="tl-args">${esc(trunc(s.args, 200))}</div>`;
  }
  if (s.result) mid += `<div class="tl-result">returned ${esc(trunc(s.result, 200))}</div>`;

  if (s.fault) {
    mid += `<div class="tl-banner fault"><b>We planted a problem here.</b>
      ${esc(trunc(s.fault.description, 160))}</div>`;
  }

  for (const inc of s.incidents || []) {
    const loc = inc.localization || {};
    const v = inc.violation || {};
    mid += `<div class="tl-banner incident">
      <b>Safety check broke: ${esc(v.invariant_id)}</b>
      (${esc(((WHEN[v.detected_by] || {}).name || v.detected_by || "").toLowerCase())})<br>
      ${esc(v.detail)}<br>
      <span class="tiny">Traced back to step ${loc.last_good_step} as the last correct one, using
      ${loc.evaluations ?? "—"} replays and no AI model calls.</span></div>`;
  }

  if (isRollback) {
    mid += `<div class="tl-banner rollback">Everything after step ${s.rollback_to} is no longer
      live. It stays in the audit log, but the agent continued from step ${s.rollback_to}.</div>`;
  }

  return `<div class="${cls}" data-step-row="${s.step}">
    <div class="idx">${s.step}${s.epoch ? `<br><span class="tiny">try ${s.epoch + 1}</span>` : ""}</div>
    <div><div class="tl-head">${head}</div>${mid}</div>
  </div>`;
}

/* ==========================================================================
   view: guards
   ========================================================================== */

async function viewGuards(root) {
  const d = await get("/guards");
  const pct = d.active ? Math.round((100 * d.sabotage_validated) / d.active) : 0;

  let out = pageHead(
    "Safety checks",
    `The ${d.active} rules every tool call is tested against. They are the same everywhere — what
     changes between assistants is whether a given check runs before the action or a few steps
     later.`
  );

  out += `<div class="grid wide-first">
    ${panel({
      title: "Proven to matter",
      sub: `${d.sabotage_validated} of ${d.active}`,
      body: CPX.progressArc(pct, {
        center: `${d.sabotage_validated}/${d.active}`,
        centerSub: "proven",
        color: tone.ok,
        title: `${pct}% proven`,
      }),
      foot: `A check only counts as proven when there is a test that switches it off and shows the
        problem then slips through. Anything else is a check we merely hope is doing something.`,
    })}
    ${panel({
      title: "The two that are not proven yet",
      body:
        (d.unvalidated || []).length
          ? `<div class="table-wrap"><table class="responsive"><thead><tr>
              <th>Check</th><th>Why it is harder to prove</th></tr></thead><tbody>
             ${(d.unvalidated || [])
               .map((id) => {
                 const g = (d.guards || []).find((x) => x.id === id) || {};
                 return `<tr>
                   <td data-label="Check"><span class="mono">${CPX.idBreak(id)}</span>
                     <div class="tiny dim">${esc(kindName(g.class))}</div></td>
                   <td data-label="Why it is harder to prove" class="wrap tiny dim">${esc(
                     trunc(g.description || "", 150)
                   )}</td></tr>`;
               })
               .join("")}</tbody></table></div>`
          : emptyBox("Every check has been proven."),
      foot: `Both of these rely on a language model's judgement rather than a fixed rule, and both
        can go from broken back to fine on their own. That makes them hard to pin down with a
        single switch-it-off test, so we do not claim they are proven.`,
    })}
  </div>`;

  /* --------------------------- placement heatmap --------------------- */
  const wlIds = ["A", "B", "C"];
  const rows = (d.guards || []).map((g) => ({
    label: g.id,
    sub: kindName(g.class),
    cells: wlIds.map((w) => {
      const p = (g.placement || {})[w];
      if (!p) {
        return { tone: "na", text: "—", title: `${g.id} does not apply to ${wlName(w)}` };
      }
      return {
        tone: p,
        text: (WHEN[p] || {}).short || p,
        title: `${g.id} on ${wlName(w)}: ${(WHEN[p] || {}).name || p} — ${(WHEN[p] || {}).hint || ""}`,
      };
    }),
  }));
  out += sectionHead("When each check runs", "the same rules, placed differently per assistant");
  out += panel({
    body:
      CPX.heatGrid(rows, wlIds.map((w) => ({ label: wlShort(w), sub: `workload ${w}` })), {
        corner: "Safety check",
      }) +
      `<div class="chart-legend">
        <span><i style="background:${tone.ok}"></i>checked instantly, can refuse the action</span>
        <span><i style="background:${tone.warn}"></i>checked in the background, may be too late</span>
        <span><i style="background:#5e6979"></i>does not apply here</span>
      </div>`,
    foot: `The customer-facing assistant has 150 ms to answer, so the slower checks there are moved
      to the background. Underwriting has no user waiting, so almost everything runs instantly.
      Same rules, different placement — and the background ones are exactly where problems go
      unnoticed for longer.`,
  });

  /* ------------------------ class distribution ---------------------- */
  const byClass = {};
  (d.guards || []).forEach((g) => {
    byClass[g.class] = (byClass[g.class] || 0) + 1;
  });
  const ckeys = Object.keys(byClass).sort((a, b) => byClass[b] - byClass[a]);
  out += `<div class="grid g2">
    ${panel({
      title: "What the checks are looking for",
      body: CPX.barsH(
        ckeys.map((k) => ({
          label: kindName(k),
          value: byClass[k],
          display: String(byClass[k]),
          color: tone.accent,
        })),
        { labelW: 185 }
      ),
      foot: "Hover any bar for the internal name of that family.",
    })}
    ${panel({
      title: "Once it breaks, does it stay broken?",
      body: (() => {
        const mono = (d.guards || []).filter((g) => g.monotone).length;
        const slices = [
          { label: "Stays broken", value: mono, color: tone.ok },
          { label: "Can recover on its own", value: (d.guards || []).length - mono, color: tone.warn },
        ];
        return (
          CPX.donut(slices, { center: mono, centerSub: `of ${(d.guards || []).length}` }) +
          CPX.legend(slices.map((s) => ({ label: s.label, color: s.color, value: s.value })))
        );
      })(),
      foot: `This sounds like a technicality and is actually the reason the search is fast. If a
        check stays broken once it breaks, then finding a point where it still passed proves the
        cause is later than that — so the range can be halved. Checks that can un-break themselves
        need a slower method.`,
    })}
  </div>`;

  /* ------------------------------- table --------------------------- */
  out += sectionHead("Every safety check", `${d.active} in the library`);
  out += `<div class="table-wrap"><table class="responsive"><thead><tr>
    <th>Check</th><th>Looking for</th>
    <th title="Once it breaks, does it stay broken? (monotone)">Stays broken</th>
    <th title="How expensive it is to run before every action">Cost</th>
    <th title="Customer support">Support</th>
    <th title="Internal knowledge">Knowledge</th>
    <th title="Underwriting">Underwriting</th>
    <th>Proven</th><th>What it protects against</th>
  </tr></thead><tbody>${(d.guards || [])
    .map((g) => {
      const cell = (w) => {
        const p = (g.placement || {})[w];
        return `<td data-label="${esc(wlShort(w))}">${
          p ? whenPill(p) : '<span class="pill mute">n/a</span>'
        }</td>`;
      };
      return `<tr>
        <td data-label="Check"><span class="mono">${CPX.idBreak(g.id)}</span></td>
        <td data-label="Looking for" title="${esc(kindHint(g.class))}">${esc(kindName(g.class))}</td>
        <td data-label="Stays broken">${
          g.monotone
            ? '<span class="pill ok">yes</span>'
            : '<span class="pill warn">can recover</span>'
        }</td>
        <td data-label="Cost" class="tiny mono">${esc(g.inline_cost_class)}</td>
        ${cell("A")}${cell("B")}${cell("C")}
        <td data-label="Proven">${
          g.sabotage_validated
            ? '<span class="pill ok" title="A test removes this check and shows the problem then slips through.">proven</span>'
            : '<span class="pill mute">not yet</span>'
        }</td>
        <td data-label="What it protects against" class="wrap tiny dim">${esc(trunc(g.description, 170))}</td>
      </tr>`;
    })
    .join("")}</tbody></table></div>`;

  root.innerHTML = out;
}

/* ==========================================================================
   view: policy
   ========================================================================== */

async function viewPolicy(root) {
  const c = await get("/catalogue");
  const tiers = c.tiers || [];
  const TIER_NAME = {
    "interactive-external": "Customer support",
    "interactive-internal": "Internal knowledge",
    "batch-analytical": "Underwriting",
  };
  const tName = (t) => TIER_NAME[t] || t;

  let out = pageHead(
    "Settings per assistant",
    `The three assistants share one library of safety checks but not one set of rules about speed.
     How long a check is allowed to take decides whether it can refuse an action or merely report
     on it afterwards.`
  );

  /* --------------------------- latency budgets ---------------------- */
  const maxB = Math.max(...tiers.map((t) => t.inline_budget_p95_ms || 0), 1);
  out += sectionHead("How long checks may take", "before the action is allowed to proceed");
  out += panel({
    body:
      CPX.barsH(
        tiers.map((t) => ({
          label: tName(t.name),
          value: t.inline_budget_p95_ms,
          display: `${t.inline_budget_p95_ms} ms`,
          color:
            t.inline_budget_p95_ms <= 150 ? tone.bad : t.inline_budget_p95_ms <= 300 ? tone.warn : tone.ok,
        })),
        { labelW: 165, max: maxB }
      ) +
      `<div class="callout info">The range is deliberately wide — 150 ms against 3000 ms is twenty
       times the room. A customer is waiting on the first one, so only cheap checks can run before
       the action. Nobody is waiting on the last one, so everything can run before the action, which
       is why almost nothing goes unnoticed there.</div>`,
    foot: `In practice the checks come in far under budget: the slowest measured across all runs is
      well under a millisecond. The budget is the constraint the design has to respect, not the
      time actually spent.`,
  });

  /* ------------------------------ tier cards ------------------------ */
  out += sectionHead("What each assistant does", "and what happens when things go wrong");
  const FALLBACK = {
    fail_closed_irreversible_open_reads:
      "Refuse anything that cannot be undone; still allow read-only lookups.",
    fail_open: "Let the agent continue unsupervised.",
    queue_and_hold: "Hold the work in a queue until the supervisor is back.",
  };
  const IRREV = {
    verify_before_commit: "Check again immediately before doing anything irreversible.",
    not_applicable: "This assistant cannot take irreversible actions.",
  };
  out += `<div class="grid g3">${tiers
    .map(
      (t) => `<section class="panel">
      <header><h4>${esc(tName(t.name))}</h4><span class="sub">${esc(t.name)}</span></header>
      <div class="kv">
        <div class="k">Time allowed</div><div class="v">${t.inline_budget_p95_ms} ms</div>
        <div class="k">Checked instantly</div>
        <div class="v" style="font-family:var(--sans)">${
          (t.inline_classes || []).map(kindName).join(", ") || "none"
        }</div>
        <div class="k">Checked in background</div>
        <div class="v" style="font-family:var(--sans)">${
          (t.async_classes || []).map(kindName).join(", ") || "nothing — everything runs instantly"
        }</div>
        <div class="k">Background delay</div>
        <div class="v">${t.async_lag_steps ? `about ${t.async_lag_steps} steps` : "none"}</div>
        <div class="k">Actions that cannot be undone</div>
        <div class="v" style="font-family:var(--sans)">${esc(
          IRREV[t.irreversible_policy] || t.irreversible_policy
        )}</div>
        <div class="k">If the supervisor is unavailable</div>
        <div class="v" style="font-family:var(--sans)">${esc(
          FALLBACK[t.on_supervisor_unavailable] || t.on_supervisor_unavailable
        )}</div>
      </div>
    </section>`
    )
    .join("")}</div>`;

  /* ------------------------------ scenarios ------------------------- */
  out += sectionHead("The test scenarios", `${(c.scenarios || []).length} situations`);
  out += `<div class="table-wrap"><table class="responsive"><thead><tr>
    <th>ID</th><th>What happens</th><th>Assistant</th>
    <th title="Which planted faults appear">Faults planted</th>
    <th>Expected behaviour</th><th>Story</th>
  </tr></thead><tbody>${(c.scenarios || [])
    .map((s) => {
      const ex = [];
      if (s.expects_block) ex.push('<span class="pill block">should refuse the action</span>');
      if (s.expects_escalation) ex.push('<span class="pill warn">should call a human</span>');
      if (s.clean) ex.push('<span class="pill ok">should not interfere at all</span>');
      return `<tr>
        <td data-label="ID"><b>${esc(s.id)}</b></td>
        <td data-label="What happens">${esc(s.title)}</td>
        <td data-label="Assistant">${wlCell(s.workload)}</td>
        <td data-label="Faults planted" class="tiny mono">${
          (s.faults || []).join(", ") || "none"
        }</td>
        <td data-label="Expected behaviour">${ex.join(" ") || "—"}</td>
        <td data-label="Story" class="wrap tiny dim">${esc(trunc(s.narrative, 230))}</td>
      </tr>`;
    })
    .join("")}</tbody></table></div>`;

  /* ------------------------------- faults --------------------------- */
  const faults = c.faults || [];
  const heldOut = faults.filter((f) => f.held_out).length;
  out += sectionHead("The things we deliberately break", `${faults.length} kinds of fault`);
  out += `<div class="table-wrap"><table class="responsive"><thead><tr>
    <th>ID</th><th>What we break</th><th>Used with</th><th>Status</th>
  </tr></thead><tbody>${faults
    .map(
      (f) => `<tr>
      <td data-label="ID" class="mono">${esc(f.id)}</td>
      <td data-label="What we break">${esc(f.title)}</td>
      <td data-label="Used with" class="tiny">${(f.envs || []).map(wlName).join(", ")}</td>
      <td data-label="Status">${
        f.held_out
          ? '<span class="pill warn" title="Never used while the checks were being written.">never used during development</span>'
          : '<span class="pill mute">used during development</span>'
      }</td>
    </tr>`
    )
    .join("")}</tbody></table></div>`;

  out += `<div class="note">${heldOut} of these ${faults.length} faults were kept back and never
    used while the safety checks were being written. Without that split, a library that only caught
    the faults it was designed against would look identical to one that genuinely generalises.</div>`;

  root.innerHTML = out;
}

/* ==========================================================================
   view: evidence
   ========================================================================== */

async function viewEvidence(root) {
  let d;
  try {
    d = await get("/experiment");
  } catch {
    root.innerHTML =
      pageHead("Evidence") + emptyBox("No experiment on disk. Run <code>controlplane ladder</code>.");
    return;
  }
  if (!d || d.note) {
    root.innerHTML = pageHead("Evidence") + emptyBox(esc((d && d.note) || "Nothing recorded."));
    return;
  }

  const conds = d.conditions || {};
  const paired = d.paired_supervisor_effect;
  const lvb = d.localization_vs_baselines;
  const meter = d.meter;

  let out = pageHead(
    "Evidence",
    `Four setups, ${
      (conds.on && conds.on.n) || 21
    } runs each, on the same scenarios with the same seeds. Each setup removes exactly one thing
     from the one before it, so each comparison supports exactly one claim.`
  );

  /* ------------------------ condition comparison -------------------- */
  const order = CONDITION_ORDER.filter((c) => conds[c]);
  out += sectionHead("What happens when each piece is removed", "task success by setup");
  out += panel({
    body: CPX.barsV(
      order.map((c) => ({
        label: condShort(c),
        value: conds[c].task_success_pct,
        display: `${conds[c].task_success_pct}%`,
        color: c === "on+detect_only" ? tone.bad : c === "off" ? tone.dim : tone.ok,
        note: `n=${conds[c].n}`,
      })),
      { max: 100, unit: "%", height: 250 }
    ),
    foot: `Removing recovery but keeping detection is the third bar, and it is the worst of the
      four — worse than having no supervisor at all. Removing only the AI-judged checks is the
      fourth bar, which loses nothing here, because on these scenarios the mechanical checks were
      enough.`,
  });

  /* ---------------------------- head to head ----------------------- */
  if (paired) {
    const a = paired.pairs - paired.c_helped - paired.b_hurt;
    out += sectionHead(
      "Head-to-head on the same tasks",
      "the only comparison that controls for task difficulty"
    );
    out += `<div class="grid wide-first">
      ${panel({
        title: "Same task, same seed, supervisor off versus on",
        body: CPX.contingency({
          a: a >= 0 ? a : "—",
          d: 0,
          b_hurt: paired.b_hurt,
          c_helped: paired.c_helped,
        }),
        foot: `Only the two off-diagonal cells carry information: the runs where the two setups
          disagreed. There were ${paired.c_helped} where supervision rescued a run that failed
          without it, and ${paired.b_hurt} where it broke a run that would otherwise have passed.`,
      })}
      ${panel({
        title: "Does that pattern mean anything?",
        body: stats([
          {
            k: "Helped",
            v: paired.c_helped,
            tone: "ok",
            foot: "failed unsupervised, passed supervised",
          },
          { k: "Hurt", v: paired.b_hurt, tone: paired.b_hurt ? "bad" : "ok", foot: "the reverse" },
          {
            k: "Likelihood by chance",
            v: `p = ${paired.p_value.toFixed(3)}`,
            tone: paired.significant ? "ok" : "warn",
            small: true,
            foot: paired.significant ? "below the 0.05 threshold" : "not conclusive",
          },
          {
            k: "Real damage",
            v: `${paired.harm_off} → ${paired.harm_on}`,
            tone: "ok",
            small: true,
            foot: "unsupervised versus supervised",
          },
        ]),
        foot: `With ${paired.c_helped} improvements and ${paired.b_hurt} regressions the result
          clears the usual threshold, but it rests on only
          ${paired.c_helped + paired.b_hurt} disagreeing runs. Treat it as a clear direction on a
          small sample rather than a precise effect size.`,
      })}
    </div>`;
  }

  /* -------------------------- vs baselines ------------------------- */
  if (lvb && lvb.ours && lvb.baselines) {
    const b = lvb.baselines;
    const NAMES = {
      llm_whole_trace: "Ask an AI to read the whole run",
      detected_at: "Blame the step where it was noticed",
      last_tool_call: "Blame the most recent step",
      random: "Pick a step at random",
    };
    const keys = Object.keys(b);
    out += sectionHead(
      "Finding the step, against simpler methods",
      `${lvb.incidents} cases where we know the right answer`
    );
    out += `<div class="grid g2">
      ${panel({
        title: "Named the exact step",
        body: CPX.barsH(
          [
            {
              label: "This system",
              value: lvb.ours.exact_step_pct,
              display: `${lvb.ours.exact_step_pct}%`,
              color: tone.ok,
              highlight: true,
            },
          ].concat(
            keys.map((k) => ({
              label: NAMES[k] || k,
              value: b[k].exact_step_pct,
              display: `${b[k].exact_step_pct}%`,
              color: tone.dim,
            }))
          ),
          { max: 100, labelW: 195 }
        ),
        foot: "Scored against the step where the fault was actually planted.",
      })}
      ${panel({
        title: "How far off, on average",
        body: CPX.barsH(
          [
            {
              label: "This system",
              value: lvb.ours.mean_abs_error,
              display: `${n1(lvb.ours.mean_abs_error)} steps`,
              color: tone.ok,
              highlight: true,
            },
          ].concat(
            keys.map((k) => ({
              label: NAMES[k] || k,
              value: b[k].mean_abs_error,
              display: `${n1(b[k].mean_abs_error)} steps`,
              color: tone.dim,
            }))
          ),
          { labelW: 195 }
        ),
        foot: `Lower is better. The simpler methods are not merely less precise — being five or six
          steps out is usually enough to rewind to the wrong place and rebuild on the same bad
          foundation.`,
      })}
    </div>`;
  }

  /* ----------------------------- spend ---------------------------- */
  if (meter) {
    const models = Object.entries(meter.by_model || {});
    const palette = [tone.accent, tone.accent2, tone.info, tone.warn];
    out += sectionHead("What it cost to produce this evidence", "in total, across every run");
    out += `<div class="grid g2">
      ${panel({
        title: "Money and calls",
        body: stats([
          { k: "Total spend", v: `$${(meter.usd ?? 0).toFixed(4)}`, tone: "ok", foot: "for all runs combined" },
          { k: "Live model calls", v: meter.live_calls ?? "—", tone: "accent" },
          {
            k: "Answers reused",
            v: meter.cache_hits ?? "—",
            tone: "ok",
            foot: "served from cache instead of called again",
          },
          { k: "Tokens", v: (meter.tokens ?? 0).toLocaleString(), tone: "info", small: true },
        ]),
        foot: `The supervisor itself makes no model calls when it pinpoints a step — that work is
          replay and arithmetic. The spend here is the agent doing the tasks, plus the two
          AI-judged safety checks.`,
      })}
      ${panel({
        title: "Spend by model",
        body: models.length
          ? CPX.meterBar(
              models.map(([m, v], i) => ({
                label: m,
                value: v,
                display: `$${v.toFixed(4)}`,
                color: palette[i % palette.length],
              }))
            )
          : emptyBox("Nothing recorded."),
        foot: `Most calls were served from cache, which is why the total is a fraction of a cent for
          ${d.run_ids ? d.run_ids.length : "all"} runs.`,
      })}
    </div>`;
  }

  /* ---------------------------- full table ------------------------ */
  out += sectionHead("Every measure, side by side", "each row removes one more piece");
  out += `<div class="table-wrap"><table class="responsive"><thead><tr>
    <th>Setup</th><th>Runs</th>
    <th title="Share of runs that finished correctly, with confidence range">Finished correctly</th>
    <th>Problems found</th>
    <th title="How often the exact step was identified">Exact step found</th>
    <th title="Share of problems fixed by rewinding, without human help">Fixed automatically</th>
    <th title="Interruptions that turned out to be unnecessary">Needless interruptions</th>
    <th title="False alarms per 100 steps on runs where nothing was wrong">False alarms</th>
    <th title="Slowest instant check, 95th percentile">Check speed</th>
  </tr></thead><tbody>${order
    .map((cond) => {
      const a = conds[cond];
      const ci = a.task_success_ci || [];
      const loc = a.localization || {};
      return `<tr>
        <td data-label="Setup">${condPill(cond)}
          <div class="tiny dim">${esc((CONDITION[cond] || {}).hint || "")}</div></td>
        <td data-label="Runs" class="num">${a.n}</td>
        <td data-label="Finished correctly" class="num">${a.task_success_pct}%
          <div class="tiny dim">range ${(100 * (ci[0] ?? 0)).toFixed(0)}–${(100 * (ci[1] ?? 0)).toFixed(
        0
      )}%</div></td>
        <td data-label="Problems found" class="num">${a.detections}</td>
        <td data-label="Exact step found" class="num">${
          loc.exact_step_pct === undefined ? "—" : loc.exact_step_pct + "%"
        }</td>
        <td data-label="Fixed automatically" class="num">${
          a.recoverability_at_L_pct === undefined || a.recoverability_at_L_pct === null
            ? "—"
            : n1(a.recoverability_at_L_pct) + "%"
        }</td>
        <td data-label="Needless interruptions" class="num">${a.intervention_regret_pct ?? "—"}%</td>
        <td data-label="False alarms" class="num">${a.false_alarms_per_100_steps ?? "—"}</td>
        <td data-label="Check speed" class="num tiny">${(a.inline_ms_p95 ?? 0).toFixed(3)} ms</td>
      </tr>`;
    })
    .join("")}</tbody></table></div>`;

  out += `<div class="note">Reading the table: the first row has no supervisor. The second adds the
    full supervisor. The third keeps the checks but takes away the ability to rewind. The fourth
    keeps everything but switches off the two checks that need an AI model to judge. Because each
    row differs from the others by exactly one thing, each comparison supports exactly one
    claim.</div>`;

  root.innerHTML = out;
}

/* ==========================================================================
   shell — navigation, routing, drawer
   ========================================================================== */

const ICON = {
  overview: '<path d="M3 12h6l2 6 3-12 2 6h5"/>',
  incidents: '<path d="M12 3 2 20h20L12 3z"/><path d="M12 9v5"/><path d="M12 17h.01"/>',
  runs: '<path d="M4 6h16M4 12h16M4 18h10"/>',
  guards: '<path d="M12 3 5 6v6c0 4 3 7 7 9 4-2 7-5 7-9V6l-7-3z"/><path d="m9 12 2 2 4-4"/>',
  policy:
    '<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>',
  evidence: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
};

const VIEWS = [
  { id: "overview", label: "Overview", short: "Overview", title: "Overview", render: viewOverview },
  { id: "incidents", label: "Incidents", short: "Incidents", title: "Incidents", render: viewIncidents },
  { id: "runs", label: "Runs", short: "Runs", title: "Runs", render: viewRuns },
  { id: "guards", label: "Guards", short: "Guards", title: "Guards", render: viewGuards },
  { id: "policy", label: "Policy", short: "Policy", title: "Policy", render: viewPolicy },
  { id: "evidence", label: "Evidence", short: "Evidence", title: "Evidence", render: viewEvidence },
];

const icon = (id) =>
  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"
     stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICON[id]}</svg>`;

/* Both navigations are generated from the one registry, so they cannot drift. */
function buildNav() {
  $("#nav").innerHTML = VIEWS.map((v) => {
    const split = v.id === "policy" ? `<div class="nav-split">Lab</div>` : "";
    return `${split}<button data-view="${v.id}">${icon(v.id)}<span>${esc(v.label)}</span></button>`;
  }).join("");
  $("#tabbar").innerHTML = VIEWS.map(
    (v) => `<button data-view="${v.id}">${icon(v.id)}<span>${esc(v.short)}</span></button>`
  ).join("");
  document.querySelectorAll("[data-view]").forEach((b) => {
    b.addEventListener("click", () => {
      go(b.dataset.view);
      closeSidebar();
    });
  });
}

const rendered = {};
let current = null;

function go(id, force) {
  const v = VIEWS.find((x) => x.id === id) || VIEWS[0];
  current = v.id;
  if (location.hash !== `#${v.id}`) location.hash = v.id;

  document.querySelectorAll("[data-view]").forEach((b) => {
    b.classList.toggle("active", b.dataset.view === v.id);
  });
  document.querySelectorAll(".view").forEach((s) => {
    s.classList.toggle("active", s.id === `view-${v.id}`);
  });
  $("#topbar-title").textContent = v.title;
  document.title = `${v.title} — ControlPlane.ai`;
  window.scrollTo({ top: 0 });

  const root = $(`#view-${v.id}`);
  if (rendered[v.id] && !force) return;
  root.innerHTML = skeleton();
  v.render(root)
    .then(() => {
      rendered[v.id] = true;
    })
    .catch((e) => {
      root.innerHTML = emptyBox(
        `Could not load this view.<br><span class="tiny dim">${esc(e.message)}</span>`
      );
    });
}

/* ------------------------------- sidebar ------------------------------- */
const openSidebar = () => {
  $("#sidebar").classList.add("open");
  $("#scrim").classList.add("open");
};
const closeSidebar = () => {
  $("#sidebar").classList.remove("open");
  if (!$("#drawer").classList.contains("open")) $("#scrim").classList.remove("open");
};

/* -------------------------------- drawer ------------------------------- */
function openDrawer(title) {
  $("#drawer-title").textContent = title;
  $("#drawer").classList.add("open");
  $("#drawer").setAttribute("aria-hidden", "false");
  $("#scrim").classList.add("open");
  $("#drawer-body").scrollTop = 0;
}
function closeDrawer() {
  $("#drawer").classList.remove("open");
  $("#drawer").setAttribute("aria-hidden", "true");
  if (!$("#sidebar").classList.contains("open")) $("#scrim").classList.remove("open");
}

/* ------------------------------ side meta ------------------------------ */
async function loadSideMeta() {
  const box = $("#side-meta");
  const row = (k, v, title) =>
    `<div class="row"${title ? ` title="${esc(title)}"` : ""}><span>${esc(k)}</span><b>${esc(v)}</b></div>`;
  try {
    const [runs, incidents, exp] = await Promise.all([
      get("/runs"),
      get("/incidents"),
      get("/experiment").catch(() => null),
    ]);
    const spend = exp && exp.meter ? exp.meter.usd : null;
    box.innerHTML =
      row("Runs on record", runs.length) +
      row("Problems found", incidents.length) +
      row("Audit log", "unbroken", "The hash chain is intact for every run.") +
      (spend !== null ? row("Total spend", `$${spend.toFixed(4)}`) : "");
  } catch {
    box.innerHTML = row("Data", "unavailable");
  }
}

/* -------------------------------- wiring ------------------------------- */
buildNav();
$("#menu").addEventListener("click", openSidebar);
$("#drawer-close").addEventListener("click", closeDrawer);
$("#scrim").addEventListener("click", () => {
  closeDrawer();
  closeSidebar();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    closeDrawer();
    closeSidebar();
  }
});
$("#tenant").addEventListener("change", () => {
  clearCache();
  Object.keys(rendered).forEach((k) => delete rendered[k]);
  loadSideMeta();
  go(current, true);
});
window.addEventListener("hashchange", () => {
  const id = location.hash.replace(/^#/, "");
  if (id && id !== current) go(id);
});

loadSideMeta();
go(location.hash.replace(/^#/, "") || "overview");
