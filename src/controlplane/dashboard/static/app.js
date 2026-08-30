"use strict";

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

const tenant = () => $("#tenant").value.trim() || "meridian";
async function api(path) {
  const sep = path.includes("?") ? "&" : "?";
  const r = await fetch(`/api${path}${sep}tenant=${encodeURIComponent(tenant())}`);
  if (!r.ok) throw new Error(`${r.status} ${path}`);
  return r.json();
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
    root.appendChild(el("div", "empty", `Could not load incidents: ${esc(e.message)}`));
    return;
  }

  if (!feed.length) {
    root.appendChild(
      el("div", "empty", "No incidents on disk yet. Run <code>controlplane ladder</code> first.")
    );
    return;
  }

  const scored = feed.filter((i) => i.localization_error !== null);
  const exact = scored.filter((i) => i.localization_error === 0).length;
  const falseAlarms = feed.filter((i) => i.false_alarm).length;
  const spontaneous = feed.filter((i) => i.spontaneous).length;
  const recovered = feed.filter((i) => i.recovery && i.recovery.succeeded).length;
  const escalated = feed.filter((i) => i.recovery && i.recovery.escalated).length;
  const lateDetect = scored.filter((i) => i.delta_detect > 0).length;

  const cards = el("div", "cards");
  const card = (k, v, foot, cls) => {
    const c = el("div", "card");
    c.appendChild(el("div", "k", k));
    c.appendChild(el("div", "v" + (cls ? " " + cls : ""), v));
    if (foot) c.appendChild(el("div", "foot", foot));
    return c;
  };
  cards.appendChild(card("Incidents", feed.length, `${scored.length} traceable to an injection`));
  cards.appendChild(
    card(
      "Exact localization",
      scored.length ? `${Math.round((100 * exact) / scored.length)}%` : "—",
      `${exact} of ${scored.length} scored`
    )
  );
  cards.appendChild(card("Late detections", lateDetect, "found after the originating step"));
  cards.appendChild(card("Recovered", recovered, `${escalated} escalated instead`));
  cards.appendChild(
    card("Agent's own errors", spontaneous, "real defects, no injected origin")
  );
  cards.appendChild(
    card("False alarms", falseAlarms, "nothing was wrong")
  );
  root.appendChild(cards);

  root.appendChild(
    el(
      "div",
      "note",
      "Localization error is measured against the injected fault step, never against the " +
        "system's own opinion of what went wrong. Incidents split three ways: traceable to " +
        "an injection (scored), a real defect the agent introduced by itself (counted, but " +
        "there is no injected origin to score against), and false alarms where nothing was " +
        "wrong. Folding the middle group into either of the others would bias the headline " +
        "in one direction or the other."
    )
  );

  const table = el("table");
  table.innerHTML = `<thead><tr>
    <th>Scenario</th><th>Condition</th><th>Invariant</th><th>Path</th>
    <th>Detected</th><th>&Delta;detect</th><th>L</th><th>Error</th>
    <th>Method</th><th>Outcome</th><th>Detail</th>
  </tr></thead>`;
  const tb = el("tbody");

  for (const inc of feed) {
    const tr = el("tr", "clickable");
    const err = inc.localization_error;
    const errPill =
      err === null
        ? inc.spontaneous
          ? '<span class="pill info">agent error</span>'
          : '<span class="pill mute">false alarm</span>'
        : err === 0
        ? '<span class="pill ok">exact</span>'
        : `<span class="pill ${err <= 1 ? "warn" : "bad"}">±${err}</span>`;

    const rec = inc.recovery || {};
    const outcome = rec.escalated
      ? '<span class="pill warn">escalated</span>'
      : rec.succeeded
      ? '<span class="pill ok">recovered</span>'
      : rec.attempted
      ? '<span class="pill bad">failed</span>'
      : '<span class="pill mute">detect only</span>';

    tr.innerHTML = `
      <td><b>${esc(inc.scenario_id)}</b><div class="tiny dim">${esc(inc.workload)}</div></td>
      <td><span class="pill info">${esc(inc.condition)}</span></td>
      <td class="mono">${esc(inc.invariant_id)}</td>
      <td><span class="pill ${inc.detected_by === "inline" ? "ok" : "warn"}">${esc(
      inc.detected_by
    )}</span></td>
      <td class="num">${inc.detected_at_step}</td>
      <td class="num">${inc.delta_detect === null ? "—" : inc.delta_detect}</td>
      <td class="num">${inc.last_good_step ?? "—"}</td>
      <td>${errPill}</td>
      <td class="tiny mono dim">${esc(inc.method || "—")}<br>${inc.evaluations ?? "—"} evals</td>
      <td>${outcome}</td>
      <td class="wrap tiny dim">${esc(trunc(inc.detail, 110))}</td>`;
    tr.onclick = () => openIncident(inc);
    tb.appendChild(tr);
  }
  table.appendChild(tb);
  root.appendChild(table);
}

async function openIncident(inc) {
  openDrawer(`${inc.scenario_id} — ${inc.invariant_id}`);
  const body = $("#drawer-body");
  body.innerHTML = "";

  const summary = el("div", "block");
  summary.appendChild(el("h3", null, "Violation"));
  const kv = el("div", "kv");
  const row = (k, v) => {
    kv.appendChild(el("div", "k", k));
    kv.appendChild(el("div", "v", v));
  };
  row("run", esc(inc.run_id));
  row("condition", esc(inc.condition));
  row("class / severity", `${esc(inc.invariant_class)} / ${esc(inc.severity)}`);
  row("detected at step", `${inc.detected_at_step} (${esc(inc.detected_by)})`);
  row("reported last good", inc.last_good_step ?? "—");
  row("expected last good", inc.expected_last_good_step ?? "— (no fault injected)");
  row("error", inc.localization_error === null ? "n/a" : inc.localization_error);
  row("detail", esc(inc.detail));
  summary.appendChild(kv);
  body.appendChild(summary);

  if (inc.rca && Object.keys(inc.rca).length) {
    const b = el("div", "block");
    b.appendChild(el("h3", null, "Root cause — three layers"));
    const rca = el("div", "rca");
    for (const key of ["trigger", "amplifier", "concealer"]) {
      if (!inc.rca[key]) continue;
      const r = el("div", "rca-row");
      r.appendChild(el("div", "lbl", key));
      r.appendChild(el("div", "txt", esc(inc.rca[key])));
      rca.appendChild(r);
    }
    b.appendChild(rca);
    body.appendChild(b);
  }

  const probes = el("div", "block");
  probes.appendChild(el("h3", null, "Search — replayed live"));
  probes.appendChild(
    el(
      "div",
      "tiny dim",
      "Each probe below is re-evaluated now, against the saved ledger. " +
        "The point of the exact path is that you can check it."
    )
  );
  body.appendChild(probes);

  try {
    const d = await api(`/runs/${encodeURIComponent(inc.run_id)}/localization/${inc.incident_id}`);
    const wrap = el("div", "probes");
    if (!d.probes.length) {
      wrap.appendChild(el("div", "tiny dim", "Non-monotone invariant — provenance fallback used."));
    }
    for (const p of d.probes) {
      wrap.appendChild(
        el(
          "span",
          "probe " + (p.holds ? "holds" : "fails"),
          `prefix ≤${p.prefix} → ${p.holds ? "holds" : "FAILS"}`
        )
      );
    }
    probes.appendChild(wrap);
    probes.appendChild(
      el(
        "div",
        "tiny dim",
        `<br>${d.probes.length} evaluations, zero model calls. ` +
          `A linear scan over the same prefix would cost ${d.linear_scan_would_cost}. ` +
          `Invariant is ${d.monotone ? "monotone" : "non-monotone"}.`
      )
    );
  } catch (e) {
    probes.appendChild(el("div", "tiny dim", `Could not replay: ${esc(e.message)}`));
  }

  if (inc.recovery && Object.keys(inc.recovery).length) {
    const b = el("div", "block");
    b.appendChild(el("h3", null, "Recovery"));
    b.appendChild(el("div", "html", json(inc.recovery)));
    body.appendChild(b);
  }

  const link = el("div", "block");
  const btn = el("button", "pill info", "Open full run timeline →");
  btn.style.cssText = "cursor:pointer;border:none;padding:8px 14px;font-size:12px;";
  btn.onclick = () => {
    closeDrawer();
    switchTo("runs");
    openRun(inc.run_id);
  };
  link.appendChild(btn);
  body.appendChild(link);
}

/* ------------------------------------------------------------------ */
/* runs                                                                */
/* ------------------------------------------------------------------ */

async function viewRuns() {
  const root = $("#view-runs");
  root.innerHTML = "";
  const runs = await api("/runs");
  if (!runs.length) {
    root.appendChild(el("div", "empty", "No runs yet."));
    return;
  }

  root.appendChild(
    el(
      "div",
      "note",
      "Success is verified against the environment's final state, never against what " +
        "the agent said it did. Harm counts what survived to the end of the run."
    )
  );

  const table = el("table");
  table.innerHTML = `<thead><tr>
    <th>Scenario</th><th>Condition</th><th>Model</th><th>Steps</th>
    <th>Faults</th><th>Incidents</th><th>Success</th><th>Harm</th>
    <th>Inline p95</th><th>Cost</th>
  </tr></thead>`;
  const tb = el("tbody");
  for (const r of runs) {
    const tr = el("tr", "clickable");
    tr.innerHTML = `
      <td><b>${esc(r.scenario_id)}</b><div class="tiny dim">${esc(r.workload)} · seed ${
      r.seed
    }</div></td>
      <td><span class="pill info">${esc(r.condition)}</span></td>
      <td class="tiny mono dim">${esc(r.model)}</td>
      <td class="num">${r.steps}</td>
      <td class="num tiny">${r.fault_steps.length ? esc(r.fault_steps.join(",")) : "—"}</td>
      <td class="num">${r.incidents}</td>
      <td>${
        r.task_success
          ? '<span class="pill ok">pass</span>'
          : '<span class="pill bad">fail</span>'
      }</td>
      <td>${
        r.harm_occurred
          ? '<span class="pill bad">harm</span>'
          : '<span class="pill ok">none</span>'
      }</td>
      <td class="num tiny">${(r.inline_ms_p95 ?? 0).toFixed(3)} ms</td>
      <td class="num tiny">$${(r.usd ?? 0).toFixed(5)}</td>`;
    tr.onclick = () => openRun(r.run_id);
    tb.appendChild(tr);
  }
  table.appendChild(tb);
  root.appendChild(table);
}

async function openRun(runId) {
  openDrawer(runId);
  const body = $("#drawer-body");
  body.innerHTML = '<div class="empty">Loading…</div>';

  let d;
  try {
    d = await api(`/runs/${encodeURIComponent(runId)}/timeline`);
  } catch (e) {
    body.innerHTML = `<div class="empty">${esc(e.message)}</div>`;
    return;
  }
  body.innerHTML = "";

  if (d.scenario && d.scenario.narrative) {
    const b = el("div", "block");
    b.appendChild(el("h3", null, d.scenario.title || "Scenario"));
    b.appendChild(el("div", "tiny dim", esc(d.scenario.narrative)));
    body.appendChild(b);
  }

  const head = el("div", "block");
  const kv = el("div", "kv");
  const row = (k, v) => {
    kv.appendChild(el("div", "k", k));
    kv.appendChild(el("div", "v", v));
  };
  row("condition", esc(d.condition));
  row("model", esc(d.model));
  row("outcome", `${d.task_success ? "pass" : "fail"} — ${esc(d.success_detail)}`);
  row("injected at steps", d.fault_steps.length ? d.fault_steps.join(", ") : "none");
  row("harm", esc(JSON.stringify(d.harm)));
  const integ = d.integrity || {};
  row(
    "audit chain",
    integ.chain_intact === null
      ? "ledger not on disk"
      : `${integ.chain_intact ? "intact" : "BROKEN"} · replay ${
          integ.replay_identical ? "identical" : "DIFFERS"
        } · ${integ.physical_records} records / ${integ.logical_steps} live`
  );
  row("PII spans redacted at write", integ.pii_spans_redacted ?? "—");
  head.appendChild(kv);
  body.appendChild(head);

  const tl = el("div", "block");
  tl.appendChild(el("h3", null, `Timeline — ${d.steps.length} records`));
  tl.appendChild(
    el(
      "div",
      "tiny dim",
      "Faded rows are attempts that were rolled back. They stay visible because the " +
        "audit trail is append-only — hiding the abandoned branch would misrepresent " +
        "what the agent actually did.<br><br>"
    )
  );
  const wrap = el("div", "timeline");

  for (const s of d.steps) {
    let cls = "tl-step";
    if (s.superseded) cls += " superseded";
    if (s.rollback_to !== null && s.rollback_to !== undefined) cls += " rollback";
    else if (s.incidents && s.incidents.length) cls += " incident";
    else if (s.blocked) cls += " blocked";
    else if (s.fault) cls += " fault";
    else if (s.source_class === "ok") cls += " ok";

    const node = el("div", cls);
    node.appendChild(
      el("div", "idx", `${s.step}${s.epoch ? `<br><span class="tiny">e${s.epoch}</span>` : ""}`)
    );

    const main = el("div");
    const h = el("div", "tl-head");
    if (s.rollback_to !== null && s.rollback_to !== undefined) {
      h.innerHTML = `<span class="tl-tool">rollback → step ${s.rollback_to}</span>`;
    } else {
      h.innerHTML =
        `<span class="tl-tool">${esc(s.tool || "—")}</span>` +
        (s.reversibility
          ? `<span class="pill ${
              s.reversibility === "irreversible"
                ? "bad"
                : s.reversibility === "compensable"
                ? "warn"
                : "mute"
            }">${esc(s.reversibility)}</span>`
          : "") +
        (s.source_class && s.source_class !== "ok"
          ? `<span class="pill ${
              s.source_class === "denied" || s.source_class === "error_tagged" ? "bad" : "mute"
            }">${esc(s.source_class)}</span>`
          : "") +
        (s.blocked ? '<span class="pill block">blocked</span>' : "") +
        (s.superseded ? '<span class="pill mute">rolled back</span>' : "");
    }
    main.appendChild(h);

    if (s.args && Object.keys(s.args).length) {
      main.appendChild(el("div", "tl-args", esc(trunc(s.args, 200))));
    }
    if (s.result) {
      main.appendChild(el("div", "tl-result", "→ " + esc(trunc(s.result, 200))));
    }

    if (s.fault) {
      main.appendChild(
        el(
          "div",
          "tl-banner fault",
          `<b>fault ${esc(s.fault.fault_id)} injected here.</b> ${esc(
            trunc(s.fault.description, 150)
          )}`
        )
      );
    }

    for (const inc of s.incidents || []) {
      const loc = inc.localization || {};
      main.appendChild(
        el(
          "div",
          "tl-banner incident",
          `<b>${esc(inc.violation.invariant_id)}</b> (${esc(
            inc.violation.detected_by
          )})<br>${esc(inc.violation.detail)}<br>` +
            `<span class="tiny">localized to step ${loc.last_good_step} via ${esc(
              loc.method || "—"
            )} in ${loc.evaluations ?? "—"} evaluations</span>`
        )
      );
    }

    if (s.rollback_to !== null && s.rollback_to !== undefined) {
      main.appendChild(
        el(
          "div",
          "tl-banner rollback",
          `Logical view truncated to step ${s.rollback_to}. Everything after it is retained ` +
            `in the physical log but is no longer live.`
        )
      );
    }

    node.appendChild(main);
    wrap.appendChild(node);
  }
  tl.appendChild(wrap);
  body.appendChild(tl);
}

/* ------------------------------------------------------------------ */
/* guards                                                              */
/* ------------------------------------------------------------------ */

async function viewGuards() {
  const root = $("#view-guards");
  root.innerHTML = "";
  const d = await api("/guards");

  const pct = Math.round((100 * d.sabotage_validated) / d.active);
  const cards = el("div", "cards");
  const card = (k, v, foot) => {
    const c = el("div", "card");
    c.appendChild(el("div", "k", k));
    c.appendChild(el("div", "v", v));
    if (foot) c.appendChild(el("div", "foot", foot));
    return c;
  };
  cards.appendChild(card("Guards active", d.active, "loaded in the registry"));
  cards.appendChild(
    card("Proven load-bearing", `${d.sabotage_validated}/${d.active}`, `${pct}% of the library`)
  );
  cards.appendChild(card("Unvalidated", d.unvalidated.length, "no sabotage case yet"));
  root.appendChild(cards);

  const bar = el("div", "card");
  bar.appendChild(el("div", "k", "Sabotage coverage"));
  const b = el("div", "bar");
  b.innerHTML = `<i style="width:${pct}%"></i>`;
  bar.appendChild(b);
  bar.appendChild(
    el(
      "div",
      "foot",
      "A guard counts as proven only when the suite has a case where removing it lets " +
        "the fault through. Anything else is a guard we merely hope is working."
    )
  );
  root.appendChild(bar);
  root.appendChild(el("div", "block", ""));

  const table = el("table");
  table.innerHTML = `<thead><tr>
    <th>Invariant</th><th>Class</th><th>Monotone</th><th>Cost</th><th>Severity</th>
    <th>A</th><th>B</th><th>C</th><th>Sabotage</th><th>What it protects</th>
  </tr></thead>`;
  const tb = el("tbody");
  const place = (p) => {
    if (!p) return '<span class="pill mute">n/a</span>';
    if (p === "inline") return '<span class="pill ok">inline</span>';
    if (p === "async") return '<span class="pill warn">async</span>';
    return '<span class="pill mute">off</span>';
  };
  for (const g of d.guards) {
    const tr = el("tr");
    tr.innerHTML = `
      <td class="mono">${esc(g.id)}</td>
      <td class="tiny">${esc(g.class)}</td>
      <td>${
        g.monotone
          ? '<span class="pill ok">yes</span>'
          : '<span class="pill warn">no</span>'
      }</td>
      <td class="tiny mono">${esc(g.inline_cost_class)}</td>
      <td class="tiny">${esc(g.severity)}</td>
      <td>${place(g.placement.A)}</td>
      <td>${place(g.placement.B)}</td>
      <td>${place(g.placement.C)}</td>
      <td>${
        g.sabotage_validated
          ? '<span class="pill ok">proven</span>'
          : '<span class="pill mute">untested</span>'
      }</td>
      <td class="wrap tiny dim">${esc(trunc(g.description, 170))}</td>`;
    tb.appendChild(tr);
  }
  table.appendChild(tb);
  root.appendChild(table);
}

/* ------------------------------------------------------------------ */
/* policy                                                              */
/* ------------------------------------------------------------------ */

async function viewPolicy() {
  const root = $("#view-policy");
  root.innerHTML = "";
  const c = await api("/catalogue");

  root.appendChild(
    el(
      "div",
      "note",
      "The same invariant library runs across all three workloads. What differs is " +
        "placement: a check the interactive tier cannot afford inline is demoted to the " +
        "deep path, where it still fires — just later, and sometimes too late to prevent harm."
    )
  );

  const t1 = el("table");
  t1.innerHTML = `<thead><tr><th>Tier</th><th>Inline p95 budget</th><th>Inline classes</th>
    <th>Async classes</th><th>Async lag</th><th>Irreversible</th><th>If supervisor down</th></tr></thead>`;
  const tb1 = el("tbody");
  for (const t of c.tiers) {
    const tr = el("tr");
    tr.innerHTML = `<td><b>${esc(t.name)}</b></td>
      <td class="num">${t.inline_budget_p95_ms} ms</td>
      <td class="tiny">${esc(t.inline_classes.join(", ") || "—")}</td>
      <td class="tiny">${esc(t.async_classes.join(", ") || "—")}</td>
      <td class="num">${t.async_lag_steps} steps</td>
      <td class="tiny mono">${esc(t.irreversible_policy)}</td>
      <td class="tiny mono">${esc(t.on_supervisor_unavailable)}</td>`;
    tb1.appendChild(tr);
  }
  t1.appendChild(tb1);
  root.appendChild(el("div", "section-head", "<h2>Policy tiers</h2>"));
  root.appendChild(t1);

  root.appendChild(el("div", "block", ""));
  root.appendChild(el("div", "section-head", "<h2>Scenarios</h2>"));
  const t2 = el("table");
  t2.innerHTML = `<thead><tr><th>ID</th><th>Title</th><th>Workload</th><th>Faults</th>
    <th>Expects</th><th>Narrative</th></tr></thead>`;
  const tb2 = el("tbody");
  for (const s of c.scenarios) {
    const expects = [];
    if (s.expects_block) expects.push('<span class="pill block">block</span>');
    if (s.expects_escalation) expects.push('<span class="pill warn">escalation</span>');
    if (s.clean) expects.push('<span class="pill ok">no intervention</span>');
    const tr = el("tr");
    tr.innerHTML = `<td><b>${esc(s.id)}</b></td>
      <td>${esc(s.title)}</td>
      <td class="tiny">${esc(s.workload)}</td>
      <td class="tiny mono">${esc(s.faults.join(",") || "—")}</td>
      <td>${expects.join(" ") || "—"}</td>
      <td class="wrap tiny dim">${esc(trunc(s.narrative, 220))}</td>`;
    tb2.appendChild(tr);
  }
  t2.appendChild(tb2);
  root.appendChild(t2);

  root.appendChild(el("div", "block", ""));
  root.appendChild(el("div", "section-head", "<h2>Fault catalogue</h2>"));
  const t3 = el("table");
  t3.innerHTML = `<thead><tr><th>ID</th><th>Title</th><th>Environments</th><th>Status</th></tr></thead>`;
  const tb3 = el("tbody");
  for (const f of c.faults) {
    const tr = el("tr");
    tr.innerHTML = `<td class="mono">${esc(f.id)}</td>
      <td>${esc(f.title)}</td>
      <td class="tiny">${esc(f.envs.join(", "))}</td>
      <td>${
        f.held_out
          ? '<span class="pill warn">held out</span>'
          : '<span class="pill mute">development</span>'
      }</td>`;
    tb3.appendChild(tr);
  }
  t3.appendChild(tb3);
  root.appendChild(t3);
  root.appendChild(
    el(
      "div",
      "note",
      "Held-out faults are refused by the harness unless a holdout evaluation asks for " +
        "them explicitly. If the library only caught faults it was written against, it " +
        "would have memorised the catalogue rather than generalised."
    )
  );
}

/* ------------------------------------------------------------------ */
/* evidence                                                            */
/* ------------------------------------------------------------------ */

async function viewEvidence() {
  const root = $("#view-evidence");
  root.innerHTML = "";
  let d;
  try {
    d = await api("/experiment");
  } catch {
    root.appendChild(
      el("div", "empty", "No experiment on disk. Run <code>controlplane ladder</code>.")
    );
    return;
  }
  if (d.note) {
    root.appendChild(el("div", "empty", esc(d.note)));
    return;
  }

  root.appendChild(
    el(
      "div",
      "note",
      "Each rung removes exactly one thing, so each supports exactly one claim. " +
        "The paired test below compares supervisor off against on for the same task and " +
        "seed; unpaired cells are dropped and counted rather than pooled."
    )
  );

  const t = el("table");
  t.innerHTML = `<thead><tr><th>Condition</th><th>n</th><th>Task success</th>
    <th>Detections</th><th>Exact L</th><th>Recover@L</th><th>Regret</th>
    <th>False alarms /100 steps</th><th>Inline p95</th></tr></thead>`;
  const tb = el("tbody");
  for (const [cond, a] of Object.entries(d.conditions || {})) {
    const tr = el("tr");
    const ci = a.task_success_ci || [];
    tr.innerHTML = `<td><b>${esc(cond)}</b></td>
      <td class="num">${a.n}</td>
      <td class="num">${a.task_success_pct}%<div class="tiny dim">CI ${(
      100 * (ci[0] ?? 0)
    ).toFixed(0)}–${(100 * (ci[1] ?? 0)).toFixed(0)}</div></td>
      <td class="num">${a.detections}</td>
      <td class="num">${a.localization?.exact_step_pct ?? "—"}%</td>
      <td class="num">${a.recoverability_at_L_pct ?? "—"}</td>
      <td class="num">${a.intervention_regret_pct}%</td>
      <td class="num">${a.false_alarms_per_100_steps}</td>
      <td class="num">${(a.inline_ms_p95 ?? 0).toFixed(3)} ms</td>`;
    tb.appendChild(tr);
  }
  t.appendChild(tb);
  root.appendChild(el("div", "section-head", "<h2>Ablation ladder</h2>"));
  root.appendChild(t);

  root.appendChild(el("div", "block", ""));
  root.appendChild(el("div", "section-head", "<h2>Paired supervisor effect</h2>"));
  root.appendChild(el("div", "block", json(d.paired_supervisor_effect || {})));

  root.appendChild(el("div", "section-head", "<h2>Spend</h2>"));
  root.appendChild(el("div", "block", json(d.meter || {})));
}

/* ------------------------------------------------------------------ */
/* shell                                                               */
/* ------------------------------------------------------------------ */

const VIEWS = {
  incidents: viewIncidents,
  runs: viewRuns,
  guards: viewGuards,
  policy: viewPolicy,
  evidence: viewEvidence,
};

function switchTo(name) {
  document.querySelectorAll(".tabs button").forEach((b) => {
    b.classList.toggle("active", b.dataset.view === name);
  });
  document.querySelectorAll(".view").forEach((v) => {
    v.classList.toggle("active", v.id === `view-${name}`);
  });
  VIEWS[name]().catch((e) => {
    $(`#view-${name}`).innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  });
}

function openDrawer(title) {
  $("#drawer-title").textContent = title;
  $("#drawer").classList.add("open");
  $("#scrim").classList.add("open");
}
function closeDrawer() {
  $("#drawer").classList.remove("open");
  $("#scrim").classList.remove("open");
}

document.getElementById("tabs").addEventListener("click", (e) => {
  if (e.target.dataset.view) switchTo(e.target.dataset.view);
});
$("#drawer-close").onclick = closeDrawer;
$("#scrim").onclick = closeDrawer;
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeDrawer();
});
$("#tenant").addEventListener("change", () => {
  const active = document.querySelector(".tabs button.active").dataset.view;
  switchTo(active);
});

switchTo("incidents");
