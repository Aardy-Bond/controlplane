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
const pFriendly = (p) => {
  if (p == null) return "—";
  if (p < 0.001) return "very unlikely by chance";
  if (p < 0.05) return `unlikely by chance (p ≈ ${Number(p).toFixed(3)})`;
  return `p ≈ ${Number(p).toFixed(3)}`;
};

const WORKLOADS = {
  A: { name: "Customer support", hint: "Talks to customers · can move money · 150 ms budget" },
  B: { name: "Internal knowledge", hint: "Answers staff questions · respects who can see what" },
  C: { name: "Underwriting", hint: "Long checklist · ~55 steps · no rush" },
};
const workloadName = (id) => (WORKLOADS[id] ? WORKLOADS[id].name : id);
const workloadLabel = (id) =>
  WORKLOADS[id] ? `${WORKLOADS[id].name} <span class="dim">(${esc(id)})</span>` : esc(id);

const COST_LABEL = {
  us: "Instant",
  ms: "Fast",
  llm: "Needs a model",
};
const costLabel = (c) => COST_LABEL[c] || c || "—";

const KIND_LABEL = {
  binding: "Identity",
  schema: "Shape",
  precondition: "Preconditions",
  provenance: "Where it came from",
  entitlement: "Permissions",
  progress: "Making progress",
  budget: "Budget",
  safety: "Safety",
  semantic: "Meaning",
};
const kindLabel = (c) => KIND_LABEL[c] || c || "—";

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

function setView(name, opts = {}) {
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${name}`));
  $$("#nav button, #tabbar button").forEach((b) =>
    b.classList.toggle("active", b.dataset.view === name)
  );
  $("#topbar-title").textContent = TITLES[name] || name;
  $("#sidebar").classList.remove("open");
  const fn = VIEWS[name];
  if (fn) fn(opts);
  const q = opts && opts.lag ? `?lag=${encodeURIComponent(opts.lag)}` : "";
  history.replaceState(null, "", `#${name}${q}`);
}

function wireNav() {
  $$("#nav button, #tabbar button").forEach((b) =>
    b.addEventListener("click", () => setView(b.dataset.view))
  );
  $("#menu-btn").addEventListener("click", () => $("#sidebar").classList.toggle("open"));
  $("#scrim").addEventListener("click", closeDrawer);
  $("#drawer-close").addEventListener("click", closeDrawer);
  $("#tenant").addEventListener("change", () => {
    const raw = (location.hash || "#overview").slice(1);
    const name = raw.split("?")[0];
    setView(TITLES[name] ? name : "overview");
  });
}

function openDrawer(title, bodyHtml) {
  $("#drawer-title").textContent = title;
  $("#drawer-body").innerHTML = bodyHtml;
  $("#drawer").classList.add("open");
  $("#drawer").setAttribute("aria-hidden", "false");
  $("#scrim").classList.add("open");
  const close = $("#drawer-close");
  if (close) close.focus();
}
function closeDrawer() {
  $("#drawer").classList.remove("open");
  $("#drawer").setAttribute("aria-hidden", "true");
  $("#scrim").classList.remove("open");
}

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeDrawer();
});

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
  root.innerHTML = `<div class="empty">Loading evidence…</div>`;

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
    root.innerHTML = "";
    root.appendChild(hero);
    hero.querySelectorAll("[data-go]").forEach((b) =>
      b.addEventListener("click", () => setView(b.dataset.go))
    );
    root.appendChild(el("div", "empty", `Could not load evidence: ${esc(e.message)}`));
    return;
  }

  root.innerHTML = "";
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
             where to rewind, not just that something is wrong.</li>
         <li>Half the problems are caught on the spot. Those are easy. The interesting ones sat
             unnoticed for many steps — that is where guessing “whatever just happened” fails,
             and a fast search of the audit log is what lands on the real origin.</li>
         <li>Every number on this page comes from run files in the repository.
             Nothing here calls a model or spends money.</li>
       </ol>`
    )
  );

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
    "where guessing the previous step usually misses",
    late.length ? "warn" : ""
  );
  root.appendChild(stats);

  // Spotlight the late-detection story — this is the claim that actually lands.
  if (loc && loc.by_lag && loc.by_lag.caught_late && loc.by_lag.caught_late.n) {
    const lateB = loc.by_lag.caught_late;
    const deep = loc.by_lag.lag_6_plus;
    const featured = loc.featured_baseline || "previous_step";
    const prevLate = ((lateB.baselines || {})[featured] || {}).exact_step_pct;
    const prevDeep = deep && deep.n ? ((deep.baselines || {})[featured] || {}).exact_step_pct : null;
    const spotlight = el("div", "panel spotlight");
    spotlight.innerHTML = `
      <h3>Where this actually earns its keep</h3>
      <p class="sub">Half the planted faults were caught within one step. On those, blaming the
      previous step is already right most of the time — no clever search needed. The other half
      went unnoticed longer. That is the hard half.</p>
      <div class="grid-3">
        <div class="stat">
          <div class="k">Caught late (2+ steps later)</div>
          <div class="v">${lateB.n}</div>
          <div class="foot">avg delay ${num(lateB.mean_lag, 1)} steps</div>
        </div>
        <div class="stat">
          <div class="k">ControlPlane still exact</div>
          <div class="v ok">${pct(lateB.ours.exact_step_pct)}</div>
          <div class="foot">on those ${lateB.n} hard cases</div>
        </div>
        <div class="stat">
          <div class="k">“Blame previous step” exact</div>
          <div class="v danger">${pct(prevLate)}</div>
          <div class="foot">${
            deep && deep.n
              ? `and ${pct(prevDeep)} on the ${deep.n} cases that sat ~${Math.round(deep.mean_lag)} steps`
              : "collapses once the delay grows"
          }</div>
        </div>
      </div>
      <div class="hero-cta" style="margin-top:1rem">
        <button class="btn" data-go-lag="late">Browse the late catches</button>
        <button class="btn ghost" data-go-lag="deep">Only the 40+ step ones</button>
        <button class="btn ghost" id="btn-hard-case">Open one hard case</button>
      </div>`;
    root.appendChild(spotlight);
    spotlight.querySelectorAll("[data-go-lag]").forEach((b) =>
      b.addEventListener("click", () => setView("incidents", { lag: b.dataset.goLag }))
    );
    const hard = incidents
      .filter((i) => (i.delta_detect || 0) >= 40 && i.localization_error === 0)
      .sort((a, b) => b.delta_detect - a.delta_detect)[0];
    const hardBtn = spotlight.querySelector("#btn-hard-case");
    if (hard && hardBtn) {
      hardBtn.addEventListener("click", () => {
        // Open the drawer immediately from Overview — don't wait on a view remount.
        showIncident(hard);
      });
    } else if (hardBtn) {
      hardBtn.remove();
    }
  }

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
      <div class="arch-step"><b>1 · Record</b>Write a sealed step to the audit log</div>
      <div class="arch-step"><b>2 · Check</b>Run safety checks — instant ones before the action, deeper ones in the background</div>
      <div class="arch-step"><b>3 · Pinpoint</b>If something fails, search the log for the last correct step</div>
      <div class="arch-step"><b>4 · Recover</b>Undo what can be undone, restore state, and let the agent try a different plan</div>
    </div>`;
  root.appendChild(archPanel);

  // Headline charts
  const chartsRow = el("div", "grid-2");
  const successPanel = el("div", "panel");
  successPanel.innerHTML = `<h3>Does watching the agent help?</h3>
    <p class="sub">Same tasks, supervisor off vs on. Catching a problem without being able to
    rewind leaves the agent stuck — worse than doing nothing. Rewinding is what turns a stop into a fix.</p>
    <div id="chart-success"></div>`;
  chartsRow.appendChild(successPanel);

  const locPanel = el("div", "panel");
  locPanel.innerHTML = `<h3>Finding the step that caused it</h3>
    <p class="sub">Split by how long the problem went unnoticed. A simple guess works when it is
    caught right away. It falls apart when it is not.</p>
    <div id="chart-loc"></div>
    <div class="legend" id="chart-loc-legend"></div>`;
  chartsRow.appendChild(locPanel);
  root.appendChild(chartsRow);

  if (exp && exp.conditions) {
    const conds = ["off", "on", "on+detect_only", "on+deterministic_only"];
    const labels = {
      off: "Off",
      on: "Full on",
      "on+detect_only": "Catch only",
      "on+deterministic_only": "No model judge",
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
        `<span><i style="background:var(--ok)"></i>ControlPlane</span>` +
        `<span><i style="background:var(--warn)"></i>Guess: previous step</span>`;
    }
  } else {
    $("#chart-loc").appendChild(
      el("div", "dim", "Run the ablation ladder to populate the localization comparison.")
    );
  }

  // Workload mix — only full-on runs, otherwise "off" and "catch only" drag the % down
  const mix = el("div", "panel");
  mix.innerHTML = `<h3>Three kinds of work, one set of checks</h3>
    <p class="sub">Success rate with the full supervisor on. What differs across workloads is how urgently a check must run.</p>
    <div class="grid-3" id="workload-cards"></div>`;
  root.appendChild(mix);
  const cards = mix.querySelector("#workload-cards");
  const byW = {};
  runs
    .filter((r) => r.condition === "on")
    .forEach((r) => {
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
         <div class="foot">${esc(w.hint)} · ${s.n} full-on runs</div>`
      )
    );
  });
}

/* ------------------------------------------------------------------ */
/* incidents                                                           */
/* ------------------------------------------------------------------ */

async function viewIncidents(opts = {}) {
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

  // Prefer showing late catches first — those are the ones that make the point.
  // Within the same delay, prefer exact pinpoints so the list leads with wins.
  feed.sort((a, b) => {
    const da = a.delta_detect == null ? -1 : a.delta_detect;
    const db = b.delta_detect == null ? -1 : b.delta_detect;
    if (db !== da) return db - da;
    const ea = a.localization_error === 0 ? 0 : 1;
    const eb = b.localization_error === 0 ? 0 : 1;
    return ea - eb;
  });

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

  // Filters
  const filters = el("div", "filters");
  filters.innerHTML = `
    <label>Show
      <select id="flt-lag">
        <option value="all">All delays</option>
        <option value="0">Caught same step</option>
        <option value="1">1 step later</option>
        <option value="late">Caught late (2+)</option>
        <option value="deep">Sat for 40+ steps</option>
      </select>
    </label>
    <label>Workload
      <select id="flt-work">
        <option value="all">All</option>
        <option value="A">Customer support</option>
        <option value="B">Internal knowledge</option>
        <option value="C">Underwriting</option>
      </select>
    </label>
    <label>Kind
      <select id="flt-kind">
        <option value="all">All</option>
        <option value="traced">Traced to a planted fault</option>
        <option value="spontaneous">Agent's own mistake</option>
        <option value="false">False alarm</option>
      </select>
    </label>
    <span class="dim" id="flt-count"></span>`;
  root.appendChild(filters);

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

  const wrap = el("div", "table-wrap");
  const table = el("table");
  table.id = "inc-table";
  table.innerHTML = `<thead><tr>
    <th>Scenario</th><th>Workload</th><th>Safety check</th><th>When checked</th>
    <th>Caught at</th><th>Detection delay</th><th>Last correct step</th><th>Pinpoint error</th>
    <th>Outcome</th>
  </tr></thead><tbody></tbody>`;
  wrap.appendChild(table);
  root.appendChild(wrap);

  const renderRows = () => {
    const lag = $("#flt-lag").value;
    const work = $("#flt-work").value;
    const kind = $("#flt-kind").value;
    const filtered = feed.filter((i) => {
      if (work !== "all" && i.workload !== work) return false;
      if (kind === "traced" && i.localization_error === null) return false;
      if (kind === "spontaneous" && !i.spontaneous) return false;
      if (kind === "false" && !i.false_alarm) return false;
      if (lag === "0" && i.delta_detect !== 0) return false;
      if (lag === "1" && i.delta_detect !== 1) return false;
      if (lag === "late" && !(i.delta_detect > 1)) return false;
      if (lag === "deep" && !(i.delta_detect >= 6)) return false;
      return true;
    });
    $("#flt-count").textContent = `${filtered.length} shown`;
    const tbody = table.querySelector("tbody");
    tbody.innerHTML = "";
    if (!filtered.length) {
      const tr = el("tr");
      tr.innerHTML = `<td colspan="9"><div class="empty">Nothing matches these filters.</div></td>`;
      tbody.appendChild(tr);
      return;
    }
    filtered.forEach((i) => {
      const tr = el("tr", "clickable");
      const path =
        i.detected_by === "inline"
          ? `<span class="badge ok" title="Checked before the action ran">Instant</span>`
          : `<span class="badge warn" title="Checked in the background — may be too late">Background</span>`;
      let kindBadge = `<span class="badge info">Traced</span>`;
      if (i.false_alarm) kindBadge = `<span class="badge">False alarm</span>`;
      if (i.spontaneous) kindBadge = `<span class="badge warn">Agent's own</span>`;
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
            ? `<span class="badge warn">Handed to a human</span>`
            : "—";
      tr.innerHTML = `
        <td>${esc(i.scenario_id)}</td>
        <td>${workloadLabel(i.workload)}</td>
        <td><code title="${esc(kindLabel(i.invariant_class))}">${esc(i.invariant_id)}</code></td>
        <td>${path}</td>
        <td class="mono">${i.detected_at_step}</td>
        <td class="mono">${i.delta_detect ?? "—"}</td>
        <td class="mono">${i.last_good_step ?? "—"}</td>
        <td>${err}</td>
        <td>${outcome} ${kindBadge}</td>`;
      tr.addEventListener("click", () => showIncident(i));
      tbody.appendChild(tr);
    });
    labelCells(table);
  };
  filters.querySelectorAll("select").forEach((s) =>
    s.addEventListener("change", () => {
      renderRows();
      const lag = $("#flt-lag").value;
      const q = lag && lag !== "all" ? `?lag=${encodeURIComponent(lag)}` : "";
      history.replaceState(null, "", `#incidents${q}`);
    })
  );
  // Deep-link support: #incidents?lag=late|deep|0|1
  if (opts.lag && ["all", "0", "1", "late", "deep"].includes(opts.lag)) {
    $("#flt-lag").value = opts.lag;
  }
  renderRows();
}

async function showIncident(i) {
  let probeHtml = `<p class="dim">Loading the search steps…</p>`;
  openDrawer(
    `Problem in ${i.scenario_id}`,
    `<p>${esc(i.detail)}</p>
     <h4>Summary</h4>
     <p>Safety check <code>${esc(i.invariant_id)}</code> failed at step
     <b>${i.detected_at_step}</b>
     (${i.detected_by === "inline" ? "checked instantly, before the action" : "checked in the background — may already be too late"}).
     Last step that was still correct: <b>${i.last_good_step ?? "—"}</b>
     ${i.delta_detect != null ? `· went unnoticed for <b>${i.delta_detect}</b> step(s)` : ""}.</p>
     ${
       i.delta_detect != null && i.delta_detect > 1
         ? `<div class="note">Guessing “previous step” would have landed on step
            <b>${i.detected_at_step - 1}</b> — off by <b>${i.delta_detect}</b>.
            The search landed on <b>${i.last_good_step}</b>.</div>`
         : ""
     }
     <div id="probe-host">${probeHtml}</div>
     <h4>What happened next</h4>
     ${
       i.recovery && i.recovery.succeeded
         ? `<p><span class="badge ok">Recovered</span> The run was rewound and the agent continued from the last correct step.</p>`
         : i.recovery && i.recovery.escalated
           ? `<p><span class="badge warn">Handed to a human</span> Something irreversible had already happened, or no safe undo existed.</p>`
           : `<p class="dim">No recovery was attempted for this problem.</p>`
     }
     ${json(i.recovery || {})}
     <h4>Why it happened</h4>
     ${json(i.rca || {})}`
  );
  try {
    const detail = await api(`/runs/${encodeURIComponent(i.run_id)}/localization/${encodeURIComponent(i.incident_id)}`);
    const host = $("#probe-host");
    if (!host) return;
    host.innerHTML = `<h4>How the search found it</h4>
      <p class="dim">Each row is one look at the audit log: “was the run still healthy up to this step?”
      The answer jumps from yes to no exactly once — that jump is the last correct step.</p>
      <div id="probe-track"></div>`;
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
        "planted faults, blocked actions, and rewinds stay visible even after recovery."
    )
  );

  const filters = el("div", "filters");
  filters.innerHTML = `
    <label>Condition
      <select id="run-cond">
        <option value="all">All</option>
        <option value="on">Full on</option>
        <option value="off">Off</option>
        <option value="on+detect_only">Catch only</option>
        <option value="on+deterministic_only">No model judge</option>
      </select>
    </label>
    <label>Workload
      <select id="run-work">
        <option value="all">All</option>
        <option value="A">Customer support</option>
        <option value="B">Internal knowledge</option>
        <option value="C">Underwriting</option>
      </select>
    </label>
    <label>Result
      <select id="run-ok">
        <option value="all">All</option>
        <option value="ok">Finished</option>
        <option value="fail">Failed</option>
        <option value="harm">Harm occurred</option>
      </select>
    </label>
    <span class="dim" id="run-count"></span>`;
  root.appendChild(filters);

  const wrap = el("div", "table-wrap");
  const table = el("table");
  table.innerHTML = `<thead><tr>
    <th>Scenario</th><th>Workload</th><th>Condition</th><th>Seed</th>
    <th>Steps</th><th>Problems</th><th>Task result</th><th>Harm</th>
  </tr></thead><tbody></tbody>`;
  wrap.appendChild(table);
  root.appendChild(wrap);

  const COND_LABEL = {
    off: "Off",
    on: "Full on",
    "on+detect_only": "Catch only",
    "on+deterministic_only": "No model judge",
  };

  const render = () => {
    const cond = $("#run-cond").value;
    const work = $("#run-work").value;
    const ok = $("#run-ok").value;
    const filtered = rows.filter((r) => {
      if (cond !== "all" && r.condition !== cond) return false;
      if (work !== "all" && r.workload !== work) return false;
      if (ok === "ok" && !r.task_success) return false;
      if (ok === "fail" && r.task_success) return false;
      if (ok === "harm" && !r.harm_occurred) return false;
      return true;
    });
    $("#run-count").textContent = `${filtered.length} shown`;
    const tbody = table.querySelector("tbody");
    tbody.innerHTML = "";
    filtered.forEach((r) => {
      const tr = el("tr", "clickable");
      tr.innerHTML = `
        <td>${esc(r.scenario_id)}</td>
        <td>${workloadLabel(r.workload)}</td>
        <td>${esc(COND_LABEL[r.condition] || r.condition)}</td>
        <td class="mono">${r.seed}</td>
        <td class="mono">${r.steps}</td>
        <td class="mono">${r.incidents}</td>
        <td>${r.task_success ? `<span class="badge ok">Finished</span>` : `<span class="badge danger">Failed</span>`}</td>
        <td>${r.harm_occurred ? `<span class="badge danger">Yes</span>` : `<span class="badge ok">No</span>`}</td>`;
      tr.addEventListener("click", () => showRun(r.run_id));
      tbody.appendChild(tr);
    });
    labelCells(table);
  };
  filters.querySelectorAll("select").forEach((s) => s.addEventListener("change", render));
  render();
}

async function showRun(runId) {
  openDrawer(`Run ${runId}`, `<p class="dim">Loading timeline…</p>`);
  const SRC = {
    ok: "Returned OK",
    error_tagged: "Returned an error",
    denied: "Permission denied",
    unlabelled: "Unlabelled result",
  };
  const REV = {
    reversible: "Safe to retry",
    compensable: "Can be undone",
    irreversible: "Cannot be undone",
  };
  try {
    const tl = await api(`/runs/${encodeURIComponent(runId)}/timeline`);
    const body = $("#drawer-body");
    body.innerHTML = `
      <p><b>${esc((tl.scenario || {}).title || tl.scenario_id)}</b><br/>
      <span class="dim">${esc((tl.scenario || {}).narrative || "")}</span></p>
      <p>Workload: ${workloadLabel(tl.workload)} ·
         ${tl.task_success ? `<span class="badge ok">Finished</span>` : `<span class="badge danger">Failed</span>`}
         ${tl.harm && tl.harm.harm_occurred ? ` <span class="badge danger">Harm</span>` : ""}</p>
      <h4>Step ribbon</h4>
      <div class="legend">
        <span><i style="background:rgba(224,179,90,.5)"></i>Planted fault</span>
        <span><i style="background:rgba(224,122,106,.5)"></i>Alarm</span>
        <span><i style="background:rgba(122,167,224,.4)"></i>Rewound</span>
        <span><i style="background:rgba(125,211,192,.15)"></i>Abandoned attempt</span>
      </div>
      <div id="ribbon"></div>
      <h4>Selected step</h4>
      <div id="step-detail" class="dim">Click a step in the ribbon.</div>
      <h4>Audit integrity</h4>
      <p class="dim">${
        tl.integrity && tl.integrity.chain_intact
          ? "Hash chain intact — the audit log has not been tampered with."
          : tl.integrity && tl.integrity.note
            ? esc(tl.integrity.note)
            : "Integrity check unavailable."
      }</p>
      ${json(tl.integrity || {})}`;
    Charts.stepRibbon($("#ribbon"), tl.steps || [], {
      faults: tl.fault_steps || [],
      onSelect: (s) => {
        $("#step-detail").innerHTML = `
          <p><b>Step ${s.step}</b>${s.superseded ? " <span class='badge'>Abandoned attempt</span>" : ""}
          ${s.blocked ? " <span class='badge danger'>Blocked</span>" : ""}
          ${s.rollback_to != null ? ` <span class='badge info'>Rewound to ${s.rollback_to}</span>` : ""}</p>
          <p>Tool: <code>${esc(s.tool || "—")}</code></p>
          <p>Result: ${esc(SRC[s.source_class] || s.source_class || "—")}
             · Undo?: ${esc(REV[s.reversibility] || s.reversibility || "—")}</p>
          <p>${esc(s.narrative || "")}</p>
          ${s.fault ? `<p><span class="badge warn">Planted fault here</span></p>` : ""}
          ${(s.incidents || []).length ? `<p><span class="badge danger">${s.incidents.length} alarm(s) at this step</span></p>` : ""}
          ${json({ args: s.args, result: s.result })}`;
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
    <th>Check</th><th>Kind</th><th>Speed</th><th>Once broken, stays broken?</th>
    <th>When it fires</th><th>Proven to matter?</th>
  </tr></thead><tbody></tbody>`;
  const tbody = table.querySelector("tbody");
  guards.forEach((g) => {
    const tr = el("tr");
    const mono = g.monotone
      ? `<span class="badge ok" title="Once it fails, every later step also fails — so a fast search works">Yes</span>`
      : `<span class="badge warn" title="Can flip back — we estimate rather than search exactly">No</span>`;
    const proven =
      g.sabotage_validated || g.validated
        ? `<span class="badge ok">Yes</span>`
        : `<span class="badge">Not yet</span>`;
    const sev =
      g.severity === "block"
        ? `<span class="badge danger">Stops the action</span>`
        : g.severity === "warn"
          ? `<span class="badge warn">Warns</span>`
          : `<span class="badge">Notes</span>`;
    tr.innerHTML = `
      <td><code>${esc(g.id)}</code><div class="dim">${esc(g.description || g.summary || "")}</div></td>
      <td>${esc(kindLabel(g.class || g.invariant_class))}</td>
      <td><span class="badge" title="${esc(g.inline_cost_class || "")}">${esc(costLabel(g.inline_cost_class || g.cost_class || g.cost))}</span></td>
      <td>${mono}</td>
      <td>${sev}</td>
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
        "and how careful to be before an action that cannot be undone. Interactive work gets a tight " +
        "latency budget; batch work can afford deeper checks up front."
    )
  );
  const tiers = data.tiers || [];
  if (!tiers.length) {
    root.appendChild(el("div", "empty", "No risk profiles found."));
    return;
  }
  const TIER_TITLE = {
    "interactive-external": "Customer-facing",
    "interactive-internal": "Internal tools",
    "batch-analytical": "Batch / analytical",
  };
  const DOWN = {
    fail_closed_irreversible_open_reads:
      "Block irreversible actions; allow safe reads",
    fail_open: "Let the agent keep going",
    queue_and_hold: "Queue work and wait",
  };
  const grid = el("div", "grid-3");
  tiers.forEach((t) => {
    const instant = (t.inline_classes || []).map(kindLabel).join(", ") || "—";
    const background = (t.async_classes || []).map(kindLabel).join(", ") || "none";
    grid.appendChild(
      el(
        "div",
        "panel",
        `<h3>${esc(TIER_TITLE[t.name] || t.name)}</h3>
         <p class="sub">${esc(t.description || "")}</p>
         <p>Instant budget: <b class="mono">${esc(t.inline_budget_p95_ms ?? "—")} ms</b>
            <span class="dim">(p95)</span></p>
         <p>Background checks: <b>${
           Number(t.async_lag_steps) === 0
             ? "keep up with the agent (no trailing delay)"
             : `may trail by <span class="mono">${esc(t.async_lag_steps)} steps</span>`
         }</b></p>
         <p><b>Checked instantly:</b> ${esc(instant)}</p>
         <p><b>Checked in the background:</b> ${esc(background)}</p>
         <p class="dim">If the supervisor is down:
            ${esc(DOWN[t.on_supervisor_unavailable] || t.on_supervisor_unavailable || "—")}</p>`
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
             <td>${esc(TIER_TITLE[w.tier] || w.tier)}</td></tr>`
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
        "Read the split by detection delay before the overall average — " +
        "the overall number mixes easy catches with hard ones."
    )
  );

  const stats = el("div", "stat-row");
  stats.appendChild(
    el(
      "div",
      "stat",
      `<div class="k">Same tasks, off vs on</div>
       <div class="v ok">${paired.c_helped ?? 0} helped</div>
       <div class="foot">${paired.b_hurt ?? 0} hurt · ${pFriendly(paired.p_value)}</div>`
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
        `<div class="k">Exact pinpoints (all cases)</div>
         <div class="v ok">${pct(loc.ours.exact_step_pct)}</div>
         <div class="foot">${loc.ours.n} scored problems</div>`
      )
    );
  }
  root.appendChild(stats);

  // Stratified localization — the compelling chart
  const split = el("div", "panel");
  split.innerHTML = `<h3>Pinpointing: easy cases vs hard cases</h3>
    <p class="sub">When the problem is caught on the spot, guessing “previous step” is already right.
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
      const hasLlm = groups.some((g) => g.values[2].value > 0);
      if (!hasLlm) groups.forEach((g) => (g.values = g.values.slice(0, 2)));
      Charts.groupedBars($("#ev-loc"), groups, {
        keys: hasLlm ? ["ours", "prev", "llm"] : ["ours", "prev"],
        suffix: "%",
      });
      $("#ev-loc-legend").innerHTML =
        `<span><i style="background:var(--ok)"></i>ControlPlane</span>` +
        `<span><i style="background:var(--warn)"></i>Guess: previous step</span>` +
        (hasLlm ? `<span><i style="background:var(--info)"></i>Ask an LLM</span>` : "");
    }
  }

  // Pooled table
  const panel = el("div", "panel");
  panel.innerHTML = `<h3>All methods on the same problems</h3>
    <p class="sub">The fair cheap competitor is “guess the previous step”.
    “Blame the alarm step itself” is kept only as a labelled floor — it is wrong by design.</p>
    <div class="table-wrap"><table id="ev-table">
      <thead><tr>
        <th>Method</th><th>Exact</th><th>Within 1 step</th><th>Avg error (steps)</th>
        <th>Work done</th>
      </tr></thead>
      <tbody></tbody>
    </table></div>`;
  root.appendChild(panel);
  const tbody = panel.querySelector("tbody");
  const rows = [];
  if (loc.ours) {
    rows.push({
      name: "ControlPlane (search the audit log)",
      ...loc.ours,
      work: `${loc.ours.mean_calls ?? "—"} cheap checks · 0 model calls`,
    });
  }
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
    previous_step: "Guess: previous step (fair)",
    last_write: "Guess: last state-changing write",
    last_tool_call: "Guess: last tool call",
    llm_whole_trace: "Ask an LLM to read the whole trace",
    detected_at: "Blame the alarm step itself (floor)",
    random: "Pick a random earlier step",
  };
  order.forEach((k) => {
    if (names[k] && names[k].n) {
      const r = names[k];
      const work =
        k === "llm_whole_trace"
          ? `${r.mean_calls ?? 1} model call`
          : "no model calls";
      rows.push({ name: friendly[k] || k, ...r, work });
    }
  });
  rows.forEach((r) => {
    const tr = el("tr");
    tr.innerHTML = `
      <td>${esc(r.name)}</td>
      <td class="mono">${r.exact_step_pct ?? "—"}%</td>
      <td class="mono">${r.within_1_pct ?? "—"}%</td>
      <td class="mono">${r.mean_abs_error ?? "—"}</td>
      <td>${esc(r.work)}</td>`;
    tbody.appendChild(tr);
  });
  labelCells(panel.querySelector("table"));

  // Condition summary
  const condPanel = el("div", "panel");
  condPanel.innerHTML = `<h3>What happens when each piece is removed</h3>
    <p class="sub">Catching a problem without being able to rewind is worse than no supervisor at all for finishing the task. Rewinding is what turns a stop into a fix.</p>
    <div id="ev-conds"></div>`;
  root.appendChild(condPanel);
  if (exp.conditions) {
    const labels = {
      off: "Supervisor off",
      on: "Full system",
      "on+detect_only": "Catch only (no rewind)",
      "on+deterministic_only": "No model judge",
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
(function boot() {
  const raw = (location.hash || "#overview").slice(1);
  const [name, qs] = raw.split("?");
  const params = new URLSearchParams(qs || "");
  const opts = {};
  if (params.get("lag")) opts.lag = params.get("lag");
  setView(VIEWS[name] ? name : "overview", opts);
})();
