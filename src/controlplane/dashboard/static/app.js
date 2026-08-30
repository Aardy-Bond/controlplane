"use strict";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];
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
const pct = (n) => (n == null || Number.isNaN(n) ? "—" : `${Math.round(n)}%`);
const num = (n, d = 1) => (n == null ? "—" : Number(n).toFixed(d));

const WORKLOADS = {
  A: { name: "Customer support", hint: "External-facing · moves money · 150 ms budget" },
  B: { name: "Internal knowledge", hint: "Entitlement boundaries" },
  C: { name: "Underwriting", hint: "Long-horizon · ~55 steps · batch" },
};
const workloadName = (id) => (WORKLOADS[id] ? WORKLOADS[id].name : id);
const workloadLabel = (id) =>
  WORKLOADS[id] ? `${WORKLOADS[id].name} <span class="dim">(${esc(id)})</span>` : esc(id);

const TITLES = {
  overview: "Overview",
  incidents: "Problems caught",
  runs: "Agent runs",
  guards: "Safety checks",
  policy: "Risk profiles",
  evidence: "The evidence",
};

const tenant = () => $("#tenant").value.trim() || "meridian";
async function api(path) {
  const sep = path.includes("?") ? "&" : "?";
  const r = await fetch(`/api${path}${sep}tenant=${encodeURIComponent(tenant())}`);
  if (!r.ok) throw new Error(`${r.status} ${path}`);
  return r.json();
}

/* ------------------------------------------------------------------ */
/* navigation                                                          */
/* ------------------------------------------------------------------ */

function setView(name) {
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${name}`));
  $$("#nav button, #tabbar button").forEach((b) =>
    b.classList.toggle("active", b.dataset.view === name)
  );
  $("#topbar-title").textContent = TITLES[name] || name;
  $("#sidebar").classList.remove("open");
  const fn = VIEWS[name];
  if (fn) fn();
  history.replaceState(null, "", `#${name}`);
}

function wireNav() {
  $$("#nav button, #tabbar button").forEach((b) =>
    b.addEventListener("click", () => setView(b.dataset.view))
  );
  $("#menu-btn").addEventListener("click", () => $("#sidebar").classList.toggle("open"));
  $("#scrim").addEventListener("click", closeDrawer);
  $("#drawer-close").addEventListener("click", closeDrawer);
  $("#tenant").addEventListener("change", () => {
    const hash = (location.hash || "#overview").slice(1);
    setView(TITLES[hash] ? hash : "overview");
  });
}

function openDrawer(title, bodyHtml) {
  $("#drawer-title").textContent = title;
  $("#drawer-body").innerHTML = bodyHtml;
  $("#drawer").classList.add("open");
  $("#drawer").setAttribute("aria-hidden", "false");
  $("#scrim").classList.add("open");
}
function closeDrawer() {
  $("#drawer").classList.remove("open");
  $("#drawer").setAttribute("aria-hidden", "true");
  $("#scrim").classList.remove("open");
}

function labelCells(table) {
  const headers = [...table.querySelectorAll("thead th")].map((th) => th.textContent.trim());
  table.querySelectorAll("tbody tr").forEach((tr) => {
    [...tr.children].forEach((td, i) => td.setAttribute("data-label", headers[i] || ""));
  });
}

/* ------------------------------------------------------------------ */
/* overview                                                            */
/* ------------------------------------------------------------------ */

async function viewOverview() {
  const root = $("#view-overview");
  root.innerHTML = "";

  const hero = el(
    "div",
    "hero",
    `<p class="hero-brand">ControlPlane</p>
     <h2>Catch the step that went wrong — not just the moment it showed up.</h2>
     <p>When a tool-using agent makes a bad call, the damage often appears many steps later.
     ControlPlane watches every action, stops the unsafe ones, finds the last step that was
     still correct, and rolls the run back there.</p>
     <div class="hero-cta">
       <button class="btn" data-go="evidence">See the measured results</button>
       <button class="btn ghost" data-go="incidents">Browse problems caught</button>
     </div>`
  );
  root.appendChild(hero);
  hero.querySelectorAll("[data-go]").forEach((b) =>
    b.addEventListener("click", () => setView(b.dataset.go))
  );

  root.appendChild(
    el(
      "div",
      "how-to",
      `<h3>How to read this</h3>
       <ol>
         <li>An <b>agent run</b> is one attempt at a task — looking up a customer, answering an
             internal question, or working through an underwriting checklist.</li>
         <li>A <b>safety check</b> watches each step. When one fails, that is a problem we caught.</li>
         <li><b>Pinpointing</b> means finding the last step that was still correct — so we know
             where to roll back, not just that something is wrong.</li>
         <li>The interesting cases are the ones where the problem went unnoticed for many steps.
             Blaming “whatever just happened” fails there; a fast search over the audit log does not.</li>
         <li>Every number on this page comes from the run files checked into the repository.
             Nothing here calls a model or spends money.</li>
       </ol>`
    )
  );

  let exp = null;
  let incidents = [];
  let runs = [];
  try {
    [exp, incidents, runs] = await Promise.all([
      api("/experiment").catch(() => null),
      api("/incidents"),
      api("/runs"),
    ]);
  } catch (e) {
    root.appendChild(el("div", "empty", `Could not load evidence: ${esc(e.message)}`));
    return;
  }

  const on = exp && exp.conditions && exp.conditions.on;
  const off = exp && exp.conditions && exp.conditions.off;
  const paired = exp && exp.paired_supervisor_effect;
  const loc = exp && exp.localization_vs_baselines;
  const scored = incidents.filter((i) => i.localization_error !== null);
  const late = scored.filter((i) => i.delta_detect > 1);
  const exact = scored.filter((i) => i.localization_error === 0).length;

  const stats = el("div", "stat-row");
  const addStat = (k, v, foot, cls) => {
    const s = el("div", "stat");
    s.appendChild(el("div", "k", k));
    s.appendChild(el("div", "v" + (cls ? " " + cls : ""), v));
    if (foot) s.appendChild(el("div", "foot", foot));
    stats.appendChild(s);
  };
  addStat(
    "Tasks finished with supervisor on",
    on ? pct(on.task_success_pct) : "—",
    off ? `vs ${pct(off.task_success_pct)} with it off` : "",
    "ok"
  );
  addStat(
    "Harmful outcomes prevented",
    paired ? `${paired.harm_off} → ${paired.harm_on}` : "—",
    "off → on, same tasks",
    paired && paired.harm_on === 0 ? "ok" : "warn"
  );
  addStat(
    "Pinpointed exactly",
    scored.length ? pct((100 * exact) / scored.length) : "—",
    `${exact} of ${scored.length} scored problems`,
    "ok"
  );
  addStat(
    "Caught after a delay",
    String(late.length),
    "where cheap guesses usually miss",
    late.length ? "warn" : ""
  );
  root.appendChild(stats);

  // Architecture
  const archPanel = el("div", "panel");
  archPanel.innerHTML = `
    <h3>What it does at each step</h3>
    <p class="sub">Sits between the agent and its tools. No agent code changes required.</p>
    <div class="arch">
      <div class="arch-node"><strong>Agent</strong><span>plans the next action</span></div>
      <div class="arch-arrow">→</div>
      <div class="arch-node core"><strong>ControlPlane</strong><span>records · checks · decides</span></div>
      <div class="arch-arrow">→</div>
      <div class="arch-node"><strong>Tool</strong><span>lookup, write, refund…</span></div>
    </div>
    <div class="arch-steps">
      <div class="arch-step"><b>1 · Record</b>Write a tamper-evident step to the audit log</div>
      <div class="arch-step"><b>2 · Check</b>Run safety checks — instant ones before the action, deeper ones in the background</div>
      <div class="arch-step"><b>3 · Pinpoint</b>If something fails, binary-search the log for the last correct step</div>
      <div class="arch-step"><b>4 · Recover</b>Undo what can be undone, restore state, and let the agent try a different plan</div>
    </div>`;
  root.appendChild(archPanel);

  // Headline charts
  const chartsRow = el("div", "grid-2");
  const successPanel = el("div", "panel");
  successPanel.innerHTML = `<h3>Does supervision help?</h3>
    <p class="sub">Same tasks, supervisor off vs on. Detection alone (no rollback) makes things worse — recovery is what converts a stop into a fix.</p>
    <div id="chart-success"></div>`;
  chartsRow.appendChild(successPanel);

  const locPanel = el("div", "panel");
  locPanel.innerHTML = `<h3>Finding the step that caused it</h3>
    <p class="sub">Split by how long the problem went unnoticed. Cheap guesses work when it is caught immediately; they collapse when it is not.</p>
    <div id="chart-loc"></div>
    <div class="legend" id="chart-loc-legend"></div>`;
  chartsRow.appendChild(locPanel);
  root.appendChild(chartsRow);

  if (exp && exp.conditions) {
    const conds = ["off", "on", "on+detect_only", "on+deterministic_only"];
    const labels = {
      off: "Off",
      on: "Full on",
      "on+detect_only": "Detect only",
      "on+deterministic_only": "Checks only",
    };
    const series = conds
      .filter((c) => exp.conditions[c])
      .map((c, i) => ({
        label: labels[c] || c,
        value: exp.conditions[c].task_success_pct || 0,
        color: ["var(--faint)", "var(--ok)", "var(--danger)", "var(--accent)"][i],
      }));
    Charts.barChart($("#chart-success"), series, {
      suffix: "%",
      format: (v) => `${Math.round(v)}%`,
      label: "Task success by condition",
    });
  }

  if (loc && loc.by_lag) {
    const imm = loc.by_lag.caught_immediately;
    const lateB = loc.by_lag.caught_late;
    const featured = loc.featured_baseline || "previous_step";
    const groups = [];
    if (imm && imm.n) {
      groups.push({
        label: `Caught right away (n=${imm.n})`,
        values: [
          { key: "ours", value: imm.ours.exact_step_pct || 0, color: "var(--ok)" },
          {
            key: "prev",
            value: (imm.baselines[featured] || {}).exact_step_pct || 0,
            color: "var(--warn)",
          },
        ],
      });
    }
    if (lateB && lateB.n) {
      groups.push({
        label: `Caught late (n=${lateB.n})`,
        values: [
          { key: "ours", value: lateB.ours.exact_step_pct || 0, color: "var(--ok)" },
          {
            key: "prev",
            value: (lateB.baselines[featured] || {}).exact_step_pct || 0,
            color: "var(--warn)",
          },
        ],
      });
    }
    if (groups.length) {
      Charts.groupedBars($("#chart-loc"), groups, {
        keys: ["ours", "prev"],
        suffix: "%",
        label: "Exact pinpoint rate by detection delay",
      });
      $("#chart-loc-legend").innerHTML =
        `<span><i style="background:var(--ok)"></i>ControlPlane (binary search)</span>` +
        `<span><i style="background:var(--warn)"></i>Blame the previous step (fair cheap guess)</span>`;
    }
  } else {
    $("#chart-loc").appendChild(
      el("div", "dim", "Run the ablation ladder to populate the localization comparison.")
    );
  }

  // Workload mix
  const mix = el("div", "panel");
  mix.innerHTML = `<h3>Three kinds of work, one set of checks</h3>
    <p class="sub">What differs is how urgently a check must run — interactive work cannot afford the deep path inline.</p>
    <div class="grid-3" id="workload-cards"></div>`;
  root.appendChild(mix);
  const cards = mix.querySelector("#workload-cards");
  const byW = {};
  runs.forEach((r) => {
    byW[r.workload] = byW[r.workload] || { n: 0, ok: 0 };
    byW[r.workload].n += 1;
    if (r.task_success) byW[r.workload].ok += 1;
  });
  Object.keys(WORKLOADS).forEach((id) => {
    const w = WORKLOADS[id];
    const s = byW[id] || { n: 0, ok: 0 };
    cards.appendChild(
      el(
        "div",
        "stat",
        `<div class="k">${esc(w.name)}</div>
         <div class="v" style="font-size:1.35rem">${s.n ? pct((100 * s.ok) / s.n) : "—"}</div>
         <div class="foot">${esc(w.hint)} · ${s.n} runs</div>`
      )
    );
  });
}

/* ------------------------------------------------------------------ */
/* incidents                                                           */
/* ------------------------------------------------------------------ */

async function viewIncidents() {
  const root = $("#view-incidents");
  root.innerHTML = "";
  let feed;
  try {
    feed = await api("/incidents");
  } catch (e) {
    root.appendChild(el("div", "empty", `Could not load: ${esc(e.message)}`));
    return;
  }
  if (!feed.length) {
    root.appendChild(
      el("div", "empty", "No problems on disk yet. Run <code>controlplane ladder</code> first.")
    );
    return;
  }

  const scored = feed.filter((i) => i.localization_error !== null);
  const exact = scored.filter((i) => i.localization_error === 0).length;
  const late = scored.filter((i) => i.delta_detect > 1).length;
  const recovered = feed.filter((i) => i.recovery && i.recovery.succeeded).length;
  const spontaneous = feed.filter((i) => i.spontaneous).length;
  const falseAlarms = feed.filter((i) => i.false_alarm).length;

  const stats = el("div", "stat-row");
  const card = (k, v, foot, cls) => {
    const c = el("div", "stat");
    c.appendChild(el("div", "k", k));
    c.appendChild(el("div", "v" + (cls ? " " + cls : ""), v));
    if (foot) c.appendChild(el("div", "foot", foot));
    return c;
  };
  stats.appendChild(card("Problems caught", feed.length, `${scored.length} traced to a planted fault`));
  stats.appendChild(
    card("Pinpointed exactly", scored.length ? pct((100 * exact) / scored.length) : "—", `${exact} of ${scored.length}`)
  );
  stats.appendChild(card("Caught after a delay", late, "more than one step later", "warn"));
  stats.appendChild(card("Recovered automatically", recovered, `${feed.filter((i) => i.recovery && i.recovery.escalated).length} handed to a human`));
  stats.appendChild(card("Agent's own mistakes", spontaneous, "real defects, no planted origin"));
  stats.appendChild(card("False alarms", falseAlarms, "nothing was actually wrong"));
  root.appendChild(stats);

  root.appendChild(
    el(
      "div",
      "note",
      "Pinpoint accuracy is measured against the planted fault step — never against the system's own opinion. " +
        "Problems split three ways: traced to a planted fault (scored), the agent's own mistake (counted, but no planted origin to score against), " +
        "and false alarms. Detection delay is how many steps the problem went unnoticed."
    )
  );

  // Lag donut
  const lagPanel = el("div", "panel");
  lagPanel.innerHTML = `<h3>How long did problems go unnoticed?</h3>
    <p class="sub">Same-step catches are easy to blame. The long delays are where search earns its keep.</p>
    <div style="display:flex;flex-wrap:wrap;gap:1.25rem;align-items:center">
      <div id="lag-donut"></div>
      <div id="lag-legend" class="legend" style="flex-direction:column;align-items:flex-start"></div>
    </div>`;
  root.appendChild(lagPanel);
  const bands = [
    { label: "Same step", color: "var(--ok)", test: (d) => d === 0 },
    { label: "1 step later", color: "var(--accent)", test: (d) => d === 1 },
    { label: "2–5 steps", color: "var(--warn)", test: (d) => d >= 2 && d <= 5 },
    { label: "40+ steps", color: "var(--danger)", test: (d) => d >= 6 },
  ];
  const parts = bands
    .map((b) => ({
      ...b,
      value: scored.filter((i) => b.test(i.delta_detect)).length,
    }))
    .filter((b) => b.value);
  if (parts.length) {
    Charts.donut($("#lag-donut"), parts, {
      center: String(scored.length),
      size: 140,
    });
    $("#lag-legend").innerHTML = parts
      .map((p) => `<span><i style="background:${p.color}"></i>${p.label}: <b>${p.value}</b></span>`)
      .join("");
  }

  const table = el("table");
  table.innerHTML = `<thead><tr>
    <th>Scenario</th><th>Workload</th><th>Safety check</th><th>When checked</th>
    <th>Caught at</th><th>Detection delay</th><th>Last correct step</th><th>Pinpoint error</th>
    <th>Outcome</th>
  </tr></thead><tbody></tbody>`;
  const tbody = table.querySelector("tbody");
  feed.forEach((i) => {
    const tr = el("tr", "clickable");
    const path =
      i.detected_by === "inline"
        ? `<span class="badge ok" title="Checked before the action ran">Instant</span>`
        : `<span class="badge warn" title="Checked in the background — may be too late">Background</span>`;
    let kind = `<span class="badge info">Traced</span>`;
    if (i.false_alarm) kind = `<span class="badge">False alarm</span>`;
    if (i.spontaneous) kind = `<span class="badge warn">Agent's own</span>`;
    const err =
      i.localization_error === null
        ? "—"
        : i.localization_error === 0
          ? `<span class="badge ok">Exact</span>`
          : `<span class="badge danger">off by ${i.localization_error}</span>`;
    const outcome =
      i.recovery && i.recovery.succeeded
        ? `<span class="badge ok">Recovered</span>`
        : i.recovery && i.recovery.escalated
          ? `<span class="badge warn">Escalated</span>`
          : "—";
    tr.innerHTML = `
      <td>${esc(i.scenario_id)}</td>
      <td>${workloadLabel(i.workload)}</td>
      <td><code title="${esc(i.invariant_class)}">${esc(i.invariant_id)}</code></td>
      <td>${path}</td>
      <td class="mono">${i.detected_at_step}</td>
      <td class="mono">${i.delta_detect ?? "—"}</td>
      <td class="mono">${i.last_good_step ?? "—"}</td>
      <td>${err}</td>
      <td>${outcome} ${kind}</td>`;
    tr.addEventListener("click", () => showIncident(i));
    tbody.appendChild(tr);
  });
  const wrap = el("div", "table-wrap");
  wrap.appendChild(table);
  root.appendChild(wrap);
  labelCells(table);
}

async function showIncident(i) {
  let probeHtml = `<p class="dim">Loading the search steps…</p>`;
  openDrawer(
    `Problem in ${i.scenario_id}`,
    `<p>${esc(i.detail)}</p>
     <h4>Summary</h4>
     <p>Safety check <code>${esc(i.invariant_id)}</code> failed at step
     <b>${i.detected_at_step}</b>
     (${i.detected_by === "inline" ? "checked instantly" : "checked in the background"}).
     Last step that was still correct: <b>${i.last_good_step ?? "—"}</b>
     ${i.delta_detect != null ? `· went unnoticed for <b>${i.delta_detect}</b> step(s)` : ""}.</p>
     <div id="probe-host">${probeHtml}</div>
     <h4>Recovery</h4>
     ${json(i.recovery || {})}
     <h4>Root-cause notes</h4>
     ${json(i.rca || {})}`
  );
  try {
    const detail = await api(`/runs/${encodeURIComponent(i.run_id)}/localization/${encodeURIComponent(i.incident_id)}`);
    const host = $("#probe-host");
    if (!host) return;
    host.innerHTML = `<h4>How the search found it</h4><div id="probe-track"></div>`;
    Charts.probeTrack($("#probe-track"), detail.probes || [], {
      lastGood: (detail.localization || {}).last_good_step,
      max: i.detected_at_step,
    });
  } catch (_) {
    /* non-fatal */
  }
}

/* ------------------------------------------------------------------ */
/* runs                                                                */
/* ------------------------------------------------------------------ */

async function viewRuns() {
  const root = $("#view-runs");
  root.innerHTML = "";
  let rows;
  try {
    rows = await api("/runs");
  } catch (e) {
    root.appendChild(el("div", "empty", `Could not load: ${esc(e.message)}`));
    return;
  }
  root.appendChild(
    el(
      "div",
      "note",
      "Each row is one agent attempt at a task. Open a run to see the step-by-step ribbon — " +
        "planted faults, blocked actions, and rollbacks stay visible even after recovery."
    )
  );
  const table = el("table");
  table.innerHTML = `<thead><tr>
    <th>Scenario</th><th>Workload</th><th>Condition</th><th>Seed</th>
    <th>Steps</th><th>Problems</th><th>Task result</th><th>Harm</th>
  </tr></thead><tbody></tbody>`;
  const tbody = table.querySelector("tbody");
  rows.forEach((r) => {
    const tr = el("tr", "clickable");
    tr.innerHTML = `
      <td>${esc(r.scenario_id)}</td>
      <td>${workloadLabel(r.workload)}</td>
      <td><code>${esc(r.condition)}</code></td>
      <td class="mono">${r.seed}</td>
      <td class="mono">${r.steps}</td>
      <td class="mono">${r.incidents}</td>
      <td>${r.task_success ? `<span class="badge ok">Finished</span>` : `<span class="badge danger">Failed</span>`}</td>
      <td>${r.harm_occurred ? `<span class="badge danger">Yes</span>` : `<span class="badge ok">No</span>`}</td>`;
    tr.addEventListener("click", () => showRun(r.run_id));
    tbody.appendChild(tr);
  });
  const wrap = el("div", "table-wrap");
  wrap.appendChild(table);
  root.appendChild(wrap);
  labelCells(table);
}

async function showRun(runId) {
  openDrawer(`Run ${runId}`, `<p class="dim">Loading timeline…</p>`);
  try {
    const tl = await api(`/runs/${encodeURIComponent(runId)}/timeline`);
    const body = $("#drawer-body");
    body.innerHTML = `
      <p><b>${esc((tl.scenario || {}).title || tl.scenario_id)}</b><br/>
      <span class="dim">${esc((tl.scenario || {}).narrative || "")}</span></p>
      <p>Workload: ${workloadLabel(tl.workload)} ·
         Condition: <code>${esc(tl.condition)}</code> ·
         ${tl.task_success ? `<span class="badge ok">Finished</span>` : `<span class="badge danger">Failed</span>`}</p>
      <h4>Step ribbon</h4>
      <div class="legend">
        <span><i style="background:rgba(224,179,90,.5)"></i>Planted fault</span>
        <span><i style="background:rgba(224,122,106,.5)"></i>Alarm</span>
        <span><i style="background:rgba(122,167,224,.4)"></i>Rollback</span>
      </div>
      <div id="ribbon"></div>
      <h4>Selected step</h4>
      <div id="step-detail" class="dim">Click a step in the ribbon.</div>
      <h4>Audit integrity</h4>
      ${json(tl.integrity || {})}`;
    Charts.stepRibbon($("#ribbon"), tl.steps || [], {
      faults: tl.fault_steps || [],
      onSelect: (s) => {
        $("#step-detail").innerHTML = `
          <p><b>Step ${s.step}</b>${s.superseded ? " <span class='badge'>Abandoned attempt</span>" : ""}
          ${s.blocked ? " <span class='badge danger'>Blocked</span>" : ""}
          ${s.rollback_to != null ? ` <span class='badge info'>Rolled back to ${s.rollback_to}</span>` : ""}</p>
          <p>Tool: <code>${esc(s.tool || "—")}</code>
             · Status: <code>${esc(s.source_class || "—")}</code>
             · Can undo?: <code>${esc(s.reversibility || "—")}</code></p>
          <p>${esc(s.narrative || "")}</p>
          ${s.fault ? `<p class="badge warn">Planted fault: ${esc(s.fault.fault_id || s.fault.id || "yes")}</p>` : ""}
          ${json({ args: s.args, result: s.result, incidents: s.incidents })}`;
      },
    });
  } catch (e) {
    $("#drawer-body").innerHTML = `<p class="dim">${esc(e.message)}</p>`;
  }
}

/* ------------------------------------------------------------------ */
/* guards                                                              */
/* ------------------------------------------------------------------ */

async function viewGuards() {
  const root = $("#view-guards");
  root.innerHTML = "";
  let data;
  try {
    data = await api("/guards");
  } catch (e) {
    root.appendChild(el("div", "empty", `Could not load: ${esc(e.message)}`));
    return;
  }
  const guards = data.guards || data || [];
  const validated = guards.filter((g) => g.sabotage_validated || g.validated).length;
  root.appendChild(
    el(
      "div",
      "note",
      "A safety check is only trusted when removing it lets a known bug through. " +
        "That sabotage test is what “proven to matter” means below — an inactive check and a check with nothing to catch look identical otherwise."
    )
  );
  const stats = el("div", "stat-row");
  stats.appendChild(
    el("div", "stat", `<div class="k">Safety checks</div><div class="v">${guards.length}</div>`)
  );
  stats.appendChild(
    el(
      "div",
      "stat",
      `<div class="k">Proven to matter</div><div class="v ok">${validated}</div>
       <div class="foot">${guards.length - validated} not yet sabotage-tested</div>`
    )
  );
  root.appendChild(stats);

  const table = el("table");
  table.innerHTML = `<thead><tr>
    <th>Check</th><th>Kind</th><th>Cost</th><th>Once broken, stays broken?</th>
    <th>Severity</th><th>Proven to matter?</th>
  </tr></thead><tbody></tbody>`;
  const tbody = table.querySelector("tbody");
  guards.forEach((g) => {
    const tr = el("tr");
    const mono = g.monotone
      ? `<span class="badge ok" title="Monotone — binary search works">Yes</span>`
      : `<span class="badge warn" title="Non-monotone — softer estimate">No</span>`;
    const proven =
      g.sabotage_validated || g.validated
        ? `<span class="badge ok">Yes</span>`
        : `<span class="badge">Not yet</span>`;
    tr.innerHTML = `
      <td><code>${esc(g.id)}</code><div class="dim">${esc(g.summary || g.description || "")}</div></td>
      <td>${esc(g.class || g.invariant_class || "")}</td>
      <td class="mono">${esc(g.inline_cost_class || g.cost_class || g.cost || "")}</td>
      <td>${mono}</td>
      <td>${esc(g.severity || "")}</td>
      <td>${proven}</td>`;
    tbody.appendChild(tr);
  });
  const wrap = el("div", "table-wrap");
  wrap.appendChild(table);
  root.appendChild(wrap);
  labelCells(table);
}

/* ------------------------------------------------------------------ */
/* policy                                                              */
/* ------------------------------------------------------------------ */

async function viewPolicy() {
  const root = $("#view-policy");
  root.innerHTML = "";
  let data;
  try {
    data = await api("/policy");
  } catch (e) {
    root.appendChild(el("div", "empty", `Could not load: ${esc(e.message)}`));
    return;
  }
  root.appendChild(
    el(
      "div",
      "note",
      "A risk profile decides which safety checks run instantly vs in the background, " +
        "and how aggressive recovery is. Interactive work gets a tight latency budget; batch work can afford deeper checks up front."
    )
  );
  const tiers = data.tiers || [];
  const grid = el("div", "grid-3");
  tiers.forEach((t) => {
    grid.appendChild(
      el(
        "div",
        "panel",
        `<h3>${esc(t.name || t.id)}</h3>
         <p class="sub">${esc(t.description || "")}</p>
         <p>Instant budget (p95): <b class="mono">${esc(t.inline_budget_p95_ms ?? t.inline_budget_ms ?? "—")} ms</b></p>
         <p>Background lag allowance: <b class="mono">${esc(t.async_lag_steps ?? "—")} steps</b></p>
         <p>Active checks: <b>${(t.invariants || t.active_invariants || []).length || "—"}</b></p>`
      )
    );
  });
  root.appendChild(grid);

  const workloads = data.workloads || [];
  if (workloads.length) {
    const panel = el("div", "panel");
    panel.innerHTML = `<h3>Which profile each workload uses</h3><div class="table-wrap"><table>
      <thead><tr><th>Workload</th><th>Profile</th></tr></thead>
      <tbody>${workloads
        .map(
          (w) =>
            `<tr><td>${workloadLabel(w.workload || w.id)} — ${esc(w.name || "")}</td>
             <td><code>${esc(w.tier)}</code></td></tr>`
        )
        .join("")}</tbody></table></div>`;
    root.appendChild(panel);
  }
}

/* ------------------------------------------------------------------ */
/* evidence                                                            */
/* ------------------------------------------------------------------ */

async function viewEvidence() {
  const root = $("#view-evidence");
  root.innerHTML = "";
  let exp;
  try {
    exp = await api("/experiment");
  } catch (e) {
    root.appendChild(
      el("div", "empty", "No experiment on disk yet. Run <code>controlplane ladder</code> first.")
    );
    return;
  }
  if (exp.note) {
    root.appendChild(el("div", "empty", esc(exp.note)));
    return;
  }

  const paired = exp.paired_supervisor_effect || {};
  const loc = exp.localization_vs_baselines || {};

  root.appendChild(
    el(
      "div",
      "note",
      "These numbers are generated from the run files in the repository. " +
        (loc.reading || "")
    )
  );

  const stats = el("div", "stat-row");
  stats.appendChild(
    el(
      "div",
      "stat",
      `<div class="k">Head-to-head (off vs on)</div>
       <div class="v ok">${paired.c_helped ?? 0} helped</div>
       <div class="foot">${paired.b_hurt ?? 0} hurt · p = ${paired.p_value ?? "—"}</div>`
    )
  );
  stats.appendChild(
    el(
      "div",
      "stat",
      `<div class="k">Harmful outcomes</div>
       <div class="v">${paired.harm_off ?? "—"} → ${paired.harm_on ?? "—"}</div>
       <div class="foot">supervisor off → on</div>`
    )
  );
  if (loc.ours) {
    stats.appendChild(
      el(
        "div",
        "stat",
        `<div class="k">Exact pinpoints (pooled)</div>
         <div class="v ok">${pct(loc.ours.exact_step_pct)}</div>
         <div class="foot">n = ${loc.ours.n}</div>`
      )
    );
  }
  root.appendChild(stats);

  // Stratified localization — the compelling chart
  const split = el("div", "panel");
  split.innerHTML = `<h3>Pinpointing: easy cases vs hard cases</h3>
    <p class="sub">When the problem is caught on the spot, blaming the previous step is already right.
    When it surfaces dozens of steps later, that guess collapses — and exact search does not.</p>
    <div id="ev-loc"></div>
    <div class="legend" id="ev-loc-legend"></div>`;
  root.appendChild(split);

  if (loc.by_lag) {
    const order = ["lag_0", "lag_1", "lag_2_5", "lag_6_plus"];
    const labels = {
      lag_0: "Same step",
      lag_1: "1 later",
      lag_2_5: "2–5 later",
      lag_6_plus: "40+ later",
    };
    const featured = loc.featured_baseline || "previous_step";
    const groups = order
      .map((k) => {
        const b = loc.by_lag[k];
        if (!b || !b.n) return null;
        return {
          label: `${labels[k]} (n=${b.n})`,
          values: [
            { key: "ours", value: b.ours.exact_step_pct || 0, color: "var(--ok)" },
            {
              key: "prev",
              value: (b.baselines[featured] || {}).exact_step_pct || 0,
              color: "var(--warn)",
            },
            {
              key: "llm",
              value: (b.baselines.llm_whole_trace || {}).exact_step_pct || 0,
              color: "var(--info)",
            },
          ],
        };
      })
      .filter(Boolean);
    if (groups.length) {
      // Only show llm bar when it has data in any group
      const hasLlm = groups.some((g) => g.values[2].value > 0);
      if (!hasLlm) groups.forEach((g) => (g.values = g.values.slice(0, 2)));
      Charts.groupedBars($("#ev-loc"), groups, {
        keys: hasLlm ? ["ours", "prev", "llm"] : ["ours", "prev"],
        suffix: "%",
      });
      $("#ev-loc-legend").innerHTML =
        `<span><i style="background:var(--ok)"></i>ControlPlane</span>` +
        `<span><i style="background:var(--warn)"></i>Blame previous step</span>` +
        (hasLlm ? `<span><i style="background:var(--info)"></i>Ask an LLM</span>` : "");
    }
  }

  // Pooled table
  const panel = el("div", "panel");
  panel.innerHTML = `<h3>All methods on the same problems</h3>
    <p class="sub">Featured cheap competitor is “blame the previous step”. “Blame the alarm step itself” is a labelled sanity floor — it is wrong by design.</p>
    <div class="table-wrap"><table id="ev-table">
      <thead><tr><th>Method</th><th>Exact</th><th>Within 1 step</th><th>Avg error</th><th>Model calls</th></tr></thead>
      <tbody></tbody>
    </table></div>`;
  root.appendChild(panel);
  const tbody = panel.querySelector("tbody");
  const rows = [];
  if (loc.ours) rows.push({ name: "ControlPlane (binary search)", ...loc.ours, featured: true });
  const names = loc.baselines || {};
  const order = [
    "previous_step",
    "last_write",
    "last_tool_call",
    "llm_whole_trace",
    "detected_at",
    "random",
  ];
  const friendly = {
    previous_step: "Blame the previous step (fair)",
    last_write: "Blame the last state-changing write",
    last_tool_call: "Blame the last tool call",
    llm_whole_trace: "Ask an LLM to read the whole trace",
    detected_at: "Blame the alarm step itself (sanity floor)",
    random: "Pick a random earlier step",
  };
  order.forEach((k) => {
    if (names[k] && names[k].n) rows.push({ name: friendly[k] || k, ...names[k] });
  });
  rows.forEach((r) => {
    const tr = el("tr");
    tr.innerHTML = `
      <td>${esc(r.name)}</td>
      <td class="mono">${r.exact_step_pct ?? "—"}%</td>
      <td class="mono">${r.within_1_pct ?? "—"}%</td>
      <td class="mono">${r.mean_abs_error ?? "—"}</td>
      <td class="mono">${r.mean_calls ?? "—"}</td>`;
    tbody.appendChild(tr);
  });
  labelCells(panel.querySelector("table"));

  // Condition summary
  const condPanel = el("div", "panel");
  condPanel.innerHTML = `<h3>What happens when each piece is removed</h3>
    <p class="sub">Detection without recovery is worse than no supervisor at all for finishing the task. Recovery is what converts a stop into a fix.</p>
    <div id="ev-conds"></div>`;
  root.appendChild(condPanel);
  if (exp.conditions) {
    const labels = {
      off: "Supervisor off",
      on: "Full system",
      "on+detect_only": "Detect only (no rollback)",
      "on+deterministic_only": "Deterministic checks only",
    };
    const series = Object.keys(labels)
      .filter((k) => exp.conditions[k])
      .map((k, i) => ({
        label: labels[k],
        value: exp.conditions[k].task_success_pct || 0,
        color: ["var(--faint)", "var(--ok)", "var(--danger)", "var(--accent)"][i],
      }));
    Charts.barChart($("#ev-conds"), series, {
      suffix: "%",
      format: (v) => `${Math.round(v)}%`,
    });
  }
}

/* ------------------------------------------------------------------ */
/* boot                                                                */
/* ------------------------------------------------------------------ */

const VIEWS = {
  overview: viewOverview,
  incidents: viewIncidents,
  runs: viewRuns,
  guards: viewGuards,
  policy: viewPolicy,
  evidence: viewEvidence,
};

wireNav();
const initial = (location.hash || "#overview").slice(1);
setView(VIEWS[initial] ? initial : "overview");
