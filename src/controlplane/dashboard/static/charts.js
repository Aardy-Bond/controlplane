"use strict";
/* ==========================================================================
   Charts — hand-rolled SVG, no dependencies.
   --------------------------------------------------------------------------
   A charting library would be more capable than this and would also be the
   only third-party code on the page. These are a handful of shapes drawn from
   arrays of numbers; the dashboard is small enough that owning them outright
   costs less than the dependency, and it keeps the page working from a file
   path with no network at all.

   Every function returns an SVG (or small HTML) string. They are sized with a
   viewBox and no fixed width, so the container decides how big they are and
   they stay sharp at any breakpoint without a resize listener.
   ========================================================================== */

const C = {
  accent: "#A100FF",
  accent2: "#C2A3FF",
  ok: "#34d399",
  warn: "#fbbf24",
  bad: "#f87171",
  block: "#c084fc",
  info: "#C2A3FF",
  line: "#1e2531",
  dim: "#5e6979",
  muted: "#8f9aac",
  text: "#e8ecf2",
};

const esc2 = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );

/** Unique id per gradient/clip, so two charts on one page cannot collide. */
let _uid = 0;
const uid = (p) => `${p}${++_uid}`;

/* The charts that stay as SVG still scale their text with the viewBox. Below
   the breakpoint where the layout goes single-column, panels are roughly a
   phone wide, so a narrower viewBox is used: less drawing space, but the text
   inside it ends up proportionally larger and stays readable. */
const isNarrow = () =>
  typeof window !== "undefined" && window.innerWidth ? window.innerWidth < 960 : false;
const pick = (wide, narrow) => (isNarrow() ? narrow : wide);

/** Insert break opportunities in dotted identifiers so they wrap at the dots
    rather than at an arbitrary character when a column is narrow. */
const idBreak = (s) => esc2(s).replace(/([._])/g, "$1<wbr>");

/* --------------------------------------------------------------------------
   horizontal bars — the workhorse
   --------------------------------------------------------------------------
   Deliberately HTML rather than SVG. An SVG with a fixed viewBox scales its
   text along with everything else, so a 12px label inside a 640-wide chart
   becomes about 6px once the chart is squeezed into a 340px phone column —
   legible on a desktop, useless on a phone. Real text in real elements never
   scales, wraps by itself, and reflows from "label beside bar" to "label above
   bar" with one media query.
   -------------------------------------------------------------------------- */

/**
 * @param rows [{label, value, display, color, sub, highlight, title}]
 * @param opts {max}
 */
function hBars(rows, opts = {}) {
  const max = opts.max ?? Math.max(...rows.map((r) => r.value), 1);
  return (
    `<div class="hbars">` +
    rows
      .map((r) => {
        const color = r.color || C.accent;
        const w = max > 0 ? Math.max((r.value / max) * 100, r.value > 0 ? 0.8 : 0) : 0;
        const tip = r.title || `${r.label}: ${r.display ?? r.value}`;
        return `<div class="hbar${r.highlight ? " hi" : ""}" title="${esc2(tip)}">
        <span class="hb-label">${esc2(r.label)}${
          r.sub ? `<span class="hb-sub">${esc2(r.sub)}</span>` : ""
        }</span>
        <span class="hb-track"><i style="width:${w.toFixed(2)}%;background:linear-gradient(90deg,${color},${color}8c)"></i></span>
        <span class="hb-val"${r.highlight ? ` style="color:${color}"` : ""}>${esc2(
          r.display ?? r.value
        )}</span>
      </div>`;
      })
      .join("") +
    `</div>`
  );
}

/* --------------------------------------------------------------------------
   vertical grouped bars
   -------------------------------------------------------------------------- */

/**
 * @param groups [{label, value, display, color, note}]
 */
function vBars(groups, opts = {}) {
  const W = opts.width ?? pick(620, 430);
  const H = opts.height ?? 230;
  const padB = 52;
  const padT = 26;
  const padL = 34;
  const plotH = H - padB - padT;
  const max = opts.max ?? Math.max(...groups.map((g) => g.value), 1);
  const slot = (W - padL) / groups.length;
  const barW = Math.min(opts.barW ?? 74, slot * 0.6);

  let out = `<svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" role="img">`;

  // gridlines with axis labels
  const ticks = opts.ticks ?? 4;
  for (let t = 0; t <= ticks; t++) {
    const v = (max / ticks) * t;
    const y = padT + plotH - (v / max) * plotH;
    out += `<line class="gridline" x1="${padL}" y1="${y}" x2="${W}" y2="${y}"/>`;
    out += `<text class="tick" x="${padL - 8}" y="${y + 3.5}" text-anchor="end">${Math.round(
      v
    )}${opts.unit ?? ""}</text>`;
  }

  groups.forEach((g, i) => {
    const cx = padL + slot * i + slot / 2;
    const h = Math.max((g.value / max) * plotH, g.value > 0 ? 2 : 0);
    const y = padT + plotH - h;
    const grad = uid("v");
    const color = g.color || C.accent;
    out += `<defs><linearGradient id="${grad}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="${color}" stop-opacity=".95"/>
      <stop offset="1" stop-color="${color}" stop-opacity=".38"/></linearGradient></defs>`;
    out += `<rect class="bar" x="${cx - barW / 2}" y="${y}" width="${barW}" height="${h}" rx="7"
      fill="url(#${grad})"><title>${esc2(g.label)}: ${esc2(g.display ?? g.value)}</title></rect>`;
    out += `<text class="val" x="${cx}" y="${y - 8}" text-anchor="middle" font-size="13">${esc2(
      g.display ?? g.value
    )}</text>`;
    // Wrap long condition names onto a second line rather than letting them collide.
    const parts = String(g.label).split(/\s|\+/).filter(Boolean);
    if (parts.length > 1 && String(g.label).length > 9) {
      out += `<text class="tick" x="${cx}" y="${H - 30}" text-anchor="middle" font-size="11">${esc2(
        parts[0]
      )}</text>`;
      out += `<text class="tick" x="${cx}" y="${H - 17}" text-anchor="middle" font-size="11">${esc2(
        parts.slice(1).join(" ")
      )}</text>`;
    } else {
      out += `<text class="tick" x="${cx}" y="${H - 30}" text-anchor="middle" font-size="11">${esc2(
        g.label
      )}</text>`;
    }
    if (g.note) {
      out += `<text class="tick" x="${cx}" y="${H - 5}" text-anchor="middle" font-size="9.5" fill="${
        g.noteColor || C.dim
      }">${esc2(g.note)}</text>`;
    }
  });

  out += `<line class="axis" x1="${padL}" y1="${padT + plotH}" x2="${W}" y2="${padT + plotH}"/>`;
  out += `</svg>`;
  return out;
}

/* --------------------------------------------------------------------------
   donut
   -------------------------------------------------------------------------- */

/**
 * @param slices [{label, value, color}]
 */
function donut(slices, opts = {}) {
  const size = 168;
  const r = 62;
  const sw = opts.stroke ?? 20;
  const cx = size / 2;
  const cy = size / 2;
  const total = slices.reduce((a, s) => a + s.value, 0);
  const circ = 2 * Math.PI * r;

  let out = `<svg class="chart" viewBox="0 0 ${size} ${size}" style="max-width:${size}px;margin:0 auto" role="img">`;
  out += `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="#ffffff0d" stroke-width="${sw}"/>`;

  if (total > 0) {
    let acc = 0;
    for (const s of slices) {
      if (!s.value) continue;
      const frac = s.value / total;
      const len = frac * circ;
      out += `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none"
        stroke="${s.color}" stroke-width="${sw}" stroke-linecap="butt"
        stroke-dasharray="${len} ${circ - len}"
        stroke-dashoffset="${-acc * circ}"
        transform="rotate(-90 ${cx} ${cy})">
        <title>${esc2(s.label)}: ${s.value}</title></circle>`;
      acc += frac;
    }
  }

  out += `<text x="${cx}" y="${cy - 2}" text-anchor="middle" font-size="26" font-weight="650"
    fill="#e8ecf2" font-variant-numeric="tabular-nums">${esc2(opts.center ?? total)}</text>`;
  if (opts.centerSub) {
    out += `<text x="${cx}" y="${cy + 16}" text-anchor="middle" font-size="10.5" fill="${
      C.dim
    }">${esc2(opts.centerSub)}</text>`;
  }
  out += `</svg>`;
  return out;
}

function legend(items) {
  return (
    `<div class="chart-legend">` +
    items
      .map(
        (i) =>
          `<span><i style="background:${i.color}"></i>${esc2(i.label)}${
            i.value !== undefined ? ` <b style="color:#e8ecf2;font-weight:640">${esc2(i.value)}</b>` : ""
          }</span>`
      )
      .join("") +
    `</div>`
  );
}

/* --------------------------------------------------------------------------
   progress arc / gauge
   -------------------------------------------------------------------------- */

/**
 * 270-degree gauge. Used for sabotage coverage (18/20).
 * @param pct 0..100
 */
function progressArc(pct, opts = {}) {
  const size = 176;
  const cx = size / 2;
  const cy = size / 2 + 6;
  const r = 62;
  const sw = opts.stroke ?? 16;
  const color = opts.color || C.ok;
  const frac = Math.max(0, Math.min(100, pct)) / 100;

  // 270deg sweep starting bottom-left (135deg) going clockwise.
  const start = 135;
  const sweep = 270;
  const pt = (deg) => {
    const rad = (deg * Math.PI) / 180;
    return [cx + r * Math.cos(rad), cy + r * Math.sin(rad)];
  };
  const arc = (fromDeg, toDeg) => {
    const [x1, y1] = pt(fromDeg);
    const [x2, y2] = pt(toDeg);
    const large = Math.abs(toDeg - fromDeg) > 180 ? 1 : 0;
    return `M ${x1.toFixed(2)} ${y1.toFixed(2)} A ${r} ${r} 0 ${large} 1 ${x2.toFixed(
      2
    )} ${y2.toFixed(2)}`;
  };

  const grad = uid("pa");
  let out = `<svg class="chart" viewBox="0 0 ${size} ${size}" style="max-width:${size}px;margin:0 auto" role="img">
    <defs><linearGradient id="${grad}" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="${color}"/>
      <stop offset="1" stop-color="${opts.color2 || C.accent}"/></linearGradient></defs>`;
  out += `<path d="${arc(start, start + sweep)}" fill="none" stroke="#ffffff0f"
    stroke-width="${sw}" stroke-linecap="round"/>`;
  if (frac > 0) {
    out += `<path d="${arc(start, start + sweep * frac)}" fill="none" stroke="url(#${grad})"
      stroke-width="${sw}" stroke-linecap="round">
      <title>${esc2(opts.title || `${pct}%`)}</title></path>`;
  }
  out += `<text x="${cx}" y="${cy - 2}" text-anchor="middle" font-size="30" font-weight="660"
    fill="#e8ecf2" font-variant-numeric="tabular-nums">${esc2(opts.center ?? Math.round(pct) + "%")}</text>`;
  if (opts.centerSub) {
    out += `<text x="${cx}" y="${cy + 18}" text-anchor="middle" font-size="10.5"
      fill="${C.dim}">${esc2(opts.centerSub)}</text>`;
  }
  out += `</svg>`;
  return out;
}

/* --------------------------------------------------------------------------
   distribution strip — median / p90 / max on one axis
   -------------------------------------------------------------------------- */

function distStrip(marks, opts = {}) {
  const W = opts.width ?? pick(620, 440);
  const H = 92;
  const padL = 26;
  const padR = 26;
  const y = 44;
  const plotW = W - padL - padR;
  const max = opts.max ?? Math.max(...marks.map((m) => m.value), 1);

  let out = `<svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" role="img">`;
  const grad = uid("d");
  out += `<defs><linearGradient id="${grad}" x1="0" x2="1">
    <stop offset="0" stop-color="${C.ok}" stop-opacity=".55"/>
    <stop offset=".5" stop-color="${C.warn}" stop-opacity=".5"/>
    <stop offset="1" stop-color="${C.bad}" stop-opacity=".5"/></linearGradient></defs>`;
  out += `<rect x="${padL}" y="${y - 6}" width="${plotW}" height="12" rx="6" fill="url(#${grad})"/>`;

  // Stagger labels vertically when two marks are close enough to collide.
  let lastX = -999;
  marks.forEach((m) => {
    const x = padL + (m.value / max) * plotW;
    const tight = x - lastX < 64;
    const labelY = tight ? y - 34 : y - 23;
    out += `<line x1="${x}" y1="${y - 16}" x2="${x}" y2="${y + 16}" stroke="#e8ecf2" stroke-width="2" opacity=".8"/>`;
    out += `<text class="val" x="${x}" y="${labelY}" text-anchor="middle" font-size="12.5">${esc2(
      m.display ?? m.value
    )}</text>`;
    out += `<text class="tick" x="${x}" y="${y + 32}" text-anchor="middle" font-size="10.5">${esc2(
      m.label
    )}</text>`;
    lastX = x;
  });

  out += `</svg>`;
  return out;
}

/* --------------------------------------------------------------------------
   histogram
   -------------------------------------------------------------------------- */

/**
 * Bins raw values and draws a column chart. Used for detection lag, which is
 * heavily zero-inflated, so the first bin usually dominates.
 */
function histogram(values, opts = {}) {
  const W = opts.width ?? pick(620, 440);
  const H = opts.height ?? 180;
  const padL = 34;
  const padB = 40;
  const padT = 20;
  const plotH = H - padB - padT;
  const plotW = W - padL - 10;

  const vals = values.filter((v) => typeof v === "number" && isFinite(v));
  if (!vals.length) return "";
  const lo = opts.min ?? Math.min(...vals);
  const hi = opts.max ?? Math.max(...vals);
  const nBins = opts.bins ?? 12;
  const width = (hi - lo) / nBins || 1;

  const bins = new Array(nBins).fill(0);
  for (const v of vals) {
    let i = Math.floor((v - lo) / width);
    if (i >= nBins) i = nBins - 1;
    if (i < 0) i = 0;
    bins[i]++;
  }
  const peak = Math.max(...bins, 1);
  const bw = plotW / nBins;

  let out = `<svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" role="img">`;
  for (let t = 0; t <= 2; t++) {
    const v = (peak / 2) * t;
    const y = padT + plotH - (v / peak) * plotH;
    out += `<line class="gridline" x1="${padL}" y1="${y}" x2="${W}" y2="${y}"/>`;
    out += `<text class="tick" x="${padL - 8}" y="${y + 3.5}" text-anchor="end">${Math.round(
      v
    )}</text>`;
  }
  bins.forEach((count, i) => {
    if (!count) return;
    const h = Math.max((count / peak) * plotH, 2);
    const x = padL + i * bw;
    const y = padT + plotH - h;
    const grad = uid("h");
    const color = i === 0 ? C.ok : i >= nBins - 3 ? C.bad : C.warn;
    out += `<defs><linearGradient id="${grad}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="${color}" stop-opacity=".9"/>
      <stop offset="1" stop-color="${color}" stop-opacity=".3"/></linearGradient></defs>`;
    out += `<rect x="${x + 1.5}" y="${y}" width="${Math.max(bw - 3, 1)}" height="${h}" rx="3"
      fill="url(#${grad})"><title>${(lo + i * width).toFixed(0)}–${(
      lo +
      (i + 1) * width
    ).toFixed(0)} ${esc2(opts.unit || "")}: ${count}</title></rect>`;
  });
  out += `<line class="axis" x1="${padL}" y1="${padT + plotH}" x2="${W}" y2="${padT + plotH}"/>`;
  out += `<text class="tick" x="${padL}" y="${H - 18}" font-size="10.5">${lo.toFixed(0)}</text>`;
  out += `<text class="tick" x="${W - 10}" y="${H - 18}" text-anchor="end" font-size="10.5">${hi.toFixed(
    0
  )}</text>`;
  if (opts.axisLabel) {
    out += `<text class="tick" x="${padL + plotW / 2}" y="${H - 4}" text-anchor="middle"
      font-size="10">${esc2(opts.axisLabel)}</text>`;
  }
  out += `</svg>`;
  return out;
}

/* --------------------------------------------------------------------------
   sparkline
   -------------------------------------------------------------------------- */

function sparkline(values, opts = {}) {
  if (!values.length) return "";
  const W = 120;
  const H = opts.height ?? 26;
  const max = Math.max(...values, 1);
  const min = Math.min(...values, 0);
  const span = max - min || 1;
  const pts = values.map((v, i) => {
    const x = (i / Math.max(values.length - 1, 1)) * W;
    const y = H - ((v - min) / span) * (H - 4) - 2;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const color = opts.color || C.accent;
  const grad = uid("s");
  return `<svg class="chart spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="height:${H}px" role="img">
    <defs><linearGradient id="${grad}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="${color}" stop-opacity=".35"/>
      <stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs>
    <polygon points="0,${H} ${pts.join(" ")} ${W},${H}" fill="url(#${grad})"/>
    <polyline points="${pts.join(" ")}" fill="none" stroke="${color}" stroke-width="1.6"
      stroke-linejoin="round" stroke-linecap="round"/>
  </svg>`;
}

/* --------------------------------------------------------------------------
   2x2 contingency (McNemar)
   -------------------------------------------------------------------------- */

function contingency(m) {
  const cell = (v, label, tone, accent) => `
    <div style="background:${tone};border:1px solid ${
      accent || "var(--line,#1e2531)"
    };border-radius:10px;padding:14px 10px;text-align:center">
      <div style="font-size:24px;font-weight:660;font-variant-numeric:tabular-nums;color:${
        accent || "#e8ecf2"
      }">${esc2(v)}</div>
      <div style="font-size:10.5px;color:var(--muted,#8f9aac);margin-top:3px;line-height:1.35">${esc2(
        label
      )}</div>
    </div>`;
  const hd = (t) =>
    `<div style="font-size:9.5px;text-transform:uppercase;letter-spacing:.07em;color:var(--dim,#5e6979);text-align:center;font-weight:650">${t}</div>`;
  const rh = (t) =>
    `<div style="font-size:9.5px;text-transform:uppercase;letter-spacing:.07em;color:var(--dim,#5e6979);font-weight:650">${t}</div>`;
  return `
    <div style="display:grid;grid-template-columns:auto repeat(2,minmax(0,1fr));gap:8px;align-items:center">
      <div></div>${hd("on&nbsp;passed")}${hd("on&nbsp;failed")}
      ${rh("off&nbsp;passed")}
      ${cell(m.a ?? "—", "both passed", "#ffffff06")}
      ${cell(m.b_hurt ?? 0, "supervisor hurt", m.b_hurt ? "#f8717118" : "#ffffff06", m.b_hurt ? C.bad : null)}
      ${rh("off&nbsp;failed")}
      ${cell(m.c_helped ?? 0, "supervisor helped", m.c_helped ? "#34d39918" : "#ffffff06", m.c_helped ? C.ok : null)}
      ${cell(m.d ?? "—", "both failed", "#ffffff06")}
    </div>`;
}

/* --------------------------------------------------------------------------
   architecture diagram
   -------------------------------------------------------------------------- */

function archDiagram() {
  return `
<svg class="arch" viewBox="0 0 860 300" preserveAspectRatio="xMidYMid meet" role="img"
     aria-label="Agent calls pass through the ControlPlane interceptor before reaching tools">
  <defs>
    <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
      <path d="M0 0 10 5 0 10z" fill="#2a3342"/>
    </marker>
    <marker id="arrow-accent" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
      <path d="M0 0 10 5 0 10z" fill="#5b8dff"/>
    </marker>
    <linearGradient id="cp" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#5b8dff" stop-opacity=".16"/>
      <stop offset="1" stop-color="#8b5cf6" stop-opacity=".10"/>
    </linearGradient>
  </defs>

  <!-- the agent -->
  <rect class="box" x="6" y="112" width="136" height="70"/>
  <text class="t" x="74" y="140" text-anchor="middle">The AI agent</text>
  <text class="s" x="74" y="159" text-anchor="middle">plans, acts, repeats</text>

  <path class="flow-live" d="M148 147 H190" marker-end="url(#arrow-accent)"/>

  <!-- the supervisor -->
  <rect x="196" y="26" width="474" height="248" rx="16" fill="url(#cp)" stroke="#5b8dff4d"/>
  <text class="s" x="216" y="50" fill="#5b8dff" font-size="10.5" font-weight="700"
        letter-spacing=".08em">THE SUPERVISOR</text>

  <rect class="box box-accent" x="216" y="112" width="140" height="70"/>
  <text class="t" x="286" y="140" text-anchor="middle">Held first</text>
  <text class="s" x="286" y="159" text-anchor="middle">before it can run</text>

  <path class="flow-live" d="M360 147 H392" marker-end="url(#arrow-accent)"/>

  <rect class="box box-accent" x="396" y="112" width="140" height="70"/>
  <text class="t" x="466" y="140" text-anchor="middle">Safety checks</text>
  <text class="s" x="466" y="159" text-anchor="middle">20 of them</text>

  <path class="flow-live" d="M540 147 H568" marker-end="url(#arrow-accent)"/>

  <!-- audit log -->
  <rect class="box" x="216" y="206" width="320" height="52"/>
  <text class="t" x="376" y="228" text-anchor="middle" font-size="12">Written to the audit log</text>
  <text class="s" x="376" y="245" text-anchor="middle">only ever added to · each row hash-signed</text>
  <path class="flow" d="M286 186 V202" marker-end="url(#arrow)"/>
  <path class="flow" d="M466 186 V202" marker-end="url(#arrow)"/>

  <!-- what happens when a check breaks -->
  <rect class="box" x="216" y="60" width="320" height="44"/>
  <text class="s" x="376" y="78" text-anchor="middle" font-size="10.4">If a check breaks, the saved run is</text>
  <text class="s" x="376" y="93" text-anchor="middle" font-size="10.4">replayed to find the last correct step</text>
  <path class="flow" d="M376 110 V106"/>

  <!-- the gate -->
  <circle cx="592" cy="147" r="20" fill="#0e1218" stroke="#5b8dff66" stroke-width="1.6"/>
  <text class="s" x="592" y="152" text-anchor="middle" font-size="15" fill="#5b8dff">&#9095;</text>
  <text class="s" x="592" y="184" text-anchor="middle" font-size="9.6">allowed or refused</text>

  <path class="flow-live" d="M614 147 H706" marker-end="url(#arrow-accent)"/>

  <!-- the tools -->
  <rect class="box" x="710" y="86" width="144" height="42"/>
  <text class="t" x="782" y="112" text-anchor="middle" font-size="11.6">Safe to retry</text>
  <rect class="box" x="710" y="134" width="144" height="42"/>
  <text class="t" x="782" y="160" text-anchor="middle" font-size="11.6">Can be undone</text>
  <rect class="box" x="710" y="182" width="144" height="42" stroke="#f8717152"/>
  <text class="t" x="782" y="208" text-anchor="middle" font-size="11.6" fill="#f87171">Cannot be undone</text>
  <text class="s" x="782" y="72" text-anchor="middle">THE TOOLS IT CAN USE</text>
</svg>`;
}

/* --------------------------------------------------------------------------
   probe track — the narrowing search, drawn
   --------------------------------------------------------------------------
   Built from divs rather than SVG so it uses the .probe-* rules in app.css:
   the shrinking green/red window, the grow-in animation and the small-screen
   column widths all live there. Only the geometry is computed here, as
   percentages, so the component reflows with its container.
   -------------------------------------------------------------------------- */

/**
 * @param probes [{prefix, holds, role, lo?, hi?}]  — lo/hi are absent on the
 *        first probe, and lo can be -1 meaning "before the run started".
 * @param opts {last_good, detected_at}
 */
function probeTrack(probes, opts = {}) {
  if (!probes || !probes.length) return "";

  // Domain covers every step we probed, every window edge, and the answer.
  const seen = [];
  probes.forEach((p) => {
    seen.push(p.prefix);
    if (typeof p.lo === "number") seen.push(p.lo);
    if (typeof p.hi === "number") seen.push(p.hi);
  });
  if (typeof opts.last_good === "number") seen.push(opts.last_good);
  if (typeof opts.detected_at === "number") seen.push(opts.detected_at);
  const lo = Math.min(...seen, 0);
  let hi = Math.max(...seen, 1);
  if (hi === lo) hi = lo + 1;
  const pct = (v) => ((v - lo) / (hi - lo)) * 100;

  const rows = probes
    .map((p, i) => {
      const wLo = typeof p.lo === "number" ? p.lo : lo;
      const wHi = typeof p.hi === "number" ? p.hi : hi;
      const a = pct(Math.min(wLo, wHi));
      const b = pct(Math.max(wLo, wHi));
      const tone = p.holds ? "holds" : "fails";
      const verdict = p.holds ? `\u2264${p.prefix} ok` : `\u2264${p.prefix} broken`;
      const tip = `Check ${i + 1}: we replayed the run up to step ${p.prefix}. The safety check ${
        p.holds ? "still passed" : "had already broken"
      } there.${p.role ? ` (${p.role})` : ""} Still to search: steps ${wLo} to ${wHi}.`;
      return `<div class="probe-row">
        <div class="n">Check ${i + 1}</div>
        <div class="probe-bar" title="${esc2(tip)}">
          <div class="span ${tone}" style="left:${a.toFixed(2)}%;width:${Math.max(
        b - a,
        1.5
      ).toFixed(2)}%"></div>
          <div class="mark" style="left:${pct(p.prefix).toFixed(2)}%"></div>
        </div>
        <div class="verdict ${tone}">${esc2(verdict)}</div>
      </div>`;
    })
    .join("");

  const answer =
    typeof opts.last_good === "number"
      ? `<div class="probe-row">
           <div class="n">Answer</div>
           <div class="probe-bar" title="The last step that was still correct.">
             <div class="mark" style="left:${pct(opts.last_good).toFixed(
               2
             )}%;background:${C.accent2};opacity:1;width:3px"></div>
           </div>
           <div class="verdict" style="color:${C.accent2}">step ${opts.last_good}</div>
         </div>`
      : "";

  return `<div class="probe-track">${rows}${answer}</div>
    <div class="probe-scale"><span>step ${lo}</span><span>step ${hi}</span></div>`;
}

/* --------------------------------------------------------------------------
   step ribbon — one cell per ledger record
   --------------------------------------------------------------------------
   Divs rather than SVG here: these are click targets that need to wrap onto
   as many lines as the phone width demands, which is exactly what flex-wrap
   already does well.
   -------------------------------------------------------------------------- */

/** Classify a timeline step into the state its cell should show. */
function stepState(s) {
  if (s.rollback_to !== null && s.rollback_to !== undefined) return "rollback";
  if (s.superseded) return "superseded";
  if (s.incidents && s.incidents.length) return "incident";
  if (s.blocked) return "blocked";
  if (s.fault) return "fault";
  return "ok";
}

const RIBBON_TONE = {
  ok: C.ok,
  fault: C.warn,
  incident: C.bad,
  blocked: C.block,
  rollback: C.accent,
  superseded: "#39414f",
};

/**
 * Returns an HTML string. Call wireRibbon() after inserting it to attach
 * click handlers (keeps this module free of DOM assumptions).
 */
const RIBBON_WORDS = {
  ok: "went fine",
  fault: "problem planted here",
  incident: "safety check broke",
  blocked: "action refused",
  rollback: "rewound to a good step",
  superseded: "abandoned attempt",
};

function stepRibbon(steps, opts = {}) {
  if (!steps || !steps.length) return "";
  const cells = steps
    .map((s) => {
      const st = stepState(s);
      const label = `Step ${s.step}${s.tool ? ` — ${s.tool}` : ""} — ${RIBBON_WORDS[st]}`;
      return `<button type="button" class="cell ${st}" data-step="${s.step}"
        title="${esc2(label)}" aria-label="${esc2(label)}"></button>`;
    })
    .join("");
  const counts = steps.reduce((a, s) => {
    const st = stepState(s);
    a[st] = (a[st] || 0) + 1;
    return a;
  }, {});
  const order = ["ok", "fault", "incident", "blocked", "rollback", "superseded"];
  const key = order
    .filter((k) => counts[k])
    .map(
      (k) =>
        `<span><i style="background:${RIBBON_TONE[k]}"></i>${RIBBON_WORDS[k]} <b style="color:#e8ecf2">${counts[k]}</b></span>`
    )
    .join("");
  return `<div class="ribbon-wrap">
    <div class="ribbon">${cells}</div>
  </div>
  <div class="chart-legend">${key}</div>
  ${opts.caption ? `<div class="tiny dim" style="margin-top:8px">${esc2(opts.caption)}</div>` : ""}`;
}

/** Attach click handlers to a ribbon that is already in the document. */
function wireRibbon(rootEl, onPick) {
  if (!rootEl) return;
  rootEl.querySelectorAll(".ribbon .cell").forEach((c) => {
    c.addEventListener("click", () => onPick(Number(c.dataset.step)));
  });
}

/* --------------------------------------------------------------------------
   heat grid
   -------------------------------------------------------------------------- */

/**
 * @param rows [{label, sub, cells:[{tone, text, title}]}]
 * @param cols [string]
 */
/**
 * @param rows [{label, sub, cells:[{tone, text, title}]}]
 * @param cols [{label, sub}] or [string]
 */
function heatGrid(rows, cols, opts = {}) {
  const head = cols
    .map((c) => {
      const label = typeof c === "string" ? c : c.label;
      const sub = typeof c === "string" ? null : c.sub;
      return `<th scope="col">${esc2(label)}${
        sub ? `<div class="tiny dim" style="font-weight:500;text-transform:none;letter-spacing:0">${esc2(sub)}</div>` : ""
      }</th>`;
    })
    .join("");
  const body = rows
    .map(
      (r) =>
        `<tr><th scope="row" style="text-align:left;font-weight:600">${esc2(r.label)}${
          r.sub
            ? `<div class="tiny dim" style="font-weight:500;text-transform:none;letter-spacing:0">${esc2(
                r.sub
              )}</div>`
            : ""
        }</th>` +
        r.cells
          .map(
            (c) =>
              `<td class="cell"><span class="sq ${c.tone}" title="${esc2(
                c.title || ""
              )}">${esc2(c.text ?? "")}</span></td>`
          )
          .join("") +
        `</tr>`
    )
    .join("");
  return `<div class="heat"><table>
    <thead><tr><th scope="col" style="text-align:left">${esc2(opts.corner || "")}</th>${head}</tr></thead>
    <tbody>${body}</tbody></table></div>`;
}

/* --------------------------------------------------------------------------
   stacked meter — spend / token split
   -------------------------------------------------------------------------- */

function meterBar(parts, opts = {}) {
  const total = parts.reduce((a, p) => a + p.value, 0) || 1;
  const segs = parts
    .filter((p) => p.value > 0)
    .map(
      (p) =>
        `<i style="width:${((100 * p.value) / total).toFixed(2)}%;background:${p.color}"
          title="${esc2(p.label)}: ${esc2(p.display ?? p.value)}"></i>`
    )
    .join("");
  return `<div class="meter">${segs}</div>${
    opts.legend === false
      ? ""
      : legend(parts.map((p) => ({ label: p.label, color: p.color, value: p.display ?? p.value })))
  }`;
}

window.CP = {
  C,
  esc: esc2,
  idBreak,
  isNarrow,
  hBars,
  barsH: hBars,
  vBars,
  barsV: vBars,
  donut,
  legend,
  progressArc,
  distStrip,
  histogram,
  sparkline,
  contingency,
  archDiagram,
  probeTrack,
  stepRibbon,
  wireRibbon,
  stepState,
  heatGrid,
  meterBar,
};
// The first draft of this file exported `Charts`; keep the old name working.
window.Charts = window.CP;
