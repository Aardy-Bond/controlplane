/*! Dependency-free SVG charts for the ControlPlane dashboard. */
(function (global) {
  "use strict";

  const NS = "http://www.w3.org/2000/svg";

  function svgEl(name, attrs = {}, text) {
    const n = document.createElementNS(NS, name);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, String(v));
    if (text != null) n.textContent = text;
    return n;
  }

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  const PALETTE = [
    "var(--accent)",
    "var(--ok)",
    "var(--warn)",
    "var(--danger)",
    "var(--info)",
    "#7dd3c0",
    "#e8b86d",
    "#9db4ff",
  ];

  function barChart(host, series, opts = {}) {
    clear(host);
    const w = opts.width || host.clientWidth || 560;
    const h = opts.height || 220;
    const pad = { t: 16, r: 12, b: 36, l: 40 };
    const svg = svgEl("svg", {
      viewBox: `0 0 ${w} ${h}`,
      width: "100%",
      height: h,
      role: "img",
      "aria-label": opts.label || "bar chart",
    });
    const max = Math.max(1, ...(series.map((s) => s.value) || [1]));
    const innerW = w - pad.l - pad.r;
    const innerH = h - pad.t - pad.b;
    const gap = 8;
    const barW = Math.max(12, (innerW - gap * (series.length - 1)) / series.length);

    // grid
    for (let i = 0; i <= 4; i++) {
      const y = pad.t + (innerH * i) / 4;
      svg.appendChild(
        svgEl("line", {
          x1: pad.l,
          x2: w - pad.r,
          y1: y,
          y2: y,
          stroke: "var(--line)",
          "stroke-width": 1,
        })
      );
      const val = Math.round(max * (1 - i / 4));
      svg.appendChild(
        svgEl(
          "text",
          {
            x: pad.l - 8,
            y: y + 4,
            fill: "var(--muted)",
            "font-size": 11,
            "text-anchor": "end",
            "font-family": "var(--font-mono)",
          },
          String(opts.suffix ? val + opts.suffix : val)
        )
      );
    }

    series.forEach((s, i) => {
      const x = pad.l + i * (barW + gap);
      const bh = (s.value / max) * innerH;
      const y = pad.t + innerH - bh;
      const color = s.color || PALETTE[i % PALETTE.length];
      const rect = svgEl("rect", {
        x,
        y,
        width: barW,
        height: Math.max(2, bh),
        rx: 4,
        fill: color,
        class: "chart-bar",
      });
      rect.style.animationDelay = `${i * 60}ms`;
      svg.appendChild(rect);
      svg.appendChild(
        svgEl(
          "text",
          {
            x: x + barW / 2,
            y: y - 6,
            fill: "var(--ink)",
            "font-size": 11,
            "text-anchor": "middle",
            "font-family": "var(--font-mono)",
            "font-weight": 600,
          },
          opts.format ? opts.format(s.value) : String(s.value)
        )
      );
      svg.appendChild(
        svgEl(
          "text",
          {
            x: x + barW / 2,
            y: h - 12,
            fill: "var(--muted)",
            "font-size": 11,
            "text-anchor": "middle",
          },
          s.label
        )
      );
    });
    host.appendChild(svg);
    return svg;
  }

  function groupedBars(host, groups, opts = {}) {
    // groups: [{label, values: [{key, value, color}]}]
    clear(host);
    const keys = opts.keys || (groups[0] ? groups[0].values.map((v) => v.key) : []);
    const w = opts.width || host.clientWidth || 640;
    const h = opts.height || 240;
    const pad = { t: 20, r: 12, b: 40, l: 44 };
    const svg = svgEl("svg", {
      viewBox: `0 0 ${w} ${h}`,
      width: "100%",
      height: h,
      role: "img",
      "aria-label": opts.label || "grouped bar chart",
    });
    const allVals = groups.flatMap((g) => g.values.map((v) => v.value));
    const max = Math.max(1, ...allVals);
    const innerW = w - pad.l - pad.r;
    const innerH = h - pad.t - pad.b;
    const groupGap = 18;
    const groupW = (innerW - groupGap * (groups.length - 1)) / Math.max(1, groups.length);
    const barGap = 4;
    const barW = Math.max(8, (groupW - barGap * (keys.length - 1)) / Math.max(1, keys.length));

    for (let i = 0; i <= 4; i++) {
      const y = pad.t + (innerH * i) / 4;
      svg.appendChild(
        svgEl("line", {
          x1: pad.l,
          x2: w - pad.r,
          y1: y,
          y2: y,
          stroke: "var(--line)",
          "stroke-width": 1,
        })
      );
      svg.appendChild(
        svgEl(
          "text",
          {
            x: pad.l - 8,
            y: y + 4,
            fill: "var(--muted)",
            "font-size": 11,
            "text-anchor": "end",
            "font-family": "var(--font-mono)",
          },
          String(Math.round(max * (1 - i / 4))) + (opts.suffix || "")
        )
      );
    }

    groups.forEach((g, gi) => {
      const gx = pad.l + gi * (groupW + groupGap);
      g.values.forEach((v, vi) => {
        const bh = (v.value / max) * innerH;
        const x = gx + vi * (barW + barGap);
        const y = pad.t + innerH - bh;
        const rect = svgEl("rect", {
          x,
          y,
          width: barW,
          height: Math.max(2, bh),
          rx: 3,
          fill: v.color || PALETTE[vi % PALETTE.length],
          class: "chart-bar",
        });
        rect.style.animationDelay = `${(gi * keys.length + vi) * 40}ms`;
        svg.appendChild(rect);
      });
      svg.appendChild(
        svgEl(
          "text",
          {
            x: gx + groupW / 2,
            y: h - 14,
            fill: "var(--muted)",
            "font-size": groupW < 90 ? 10 : 12,
            "text-anchor": "middle",
          },
          g.label.length > 22 && groupW < 120 ? g.label.slice(0, 20) + "…" : g.label
        )
      );
    });
    host.appendChild(svg);
    return svg;
  }

  function donut(host, parts, opts = {}) {
    clear(host);
    const size = opts.size || 160;
    const stroke = opts.stroke || 18;
    const r = (size - stroke) / 2;
    const c = 2 * Math.PI * r;
    const svg = svgEl("svg", {
      viewBox: `0 0 ${size} ${size}`,
      width: size,
      height: size,
      role: "img",
    });
    const total = parts.reduce((a, p) => a + p.value, 0) || 1;
    let offset = 0;
    parts.forEach((p, i) => {
      const len = (p.value / total) * c;
      const circle = svgEl("circle", {
        cx: size / 2,
        cy: size / 2,
        r,
        fill: "none",
        stroke: p.color || PALETTE[i % PALETTE.length],
        "stroke-width": stroke,
        "stroke-dasharray": `${len} ${c - len}`,
        "stroke-dashoffset": -offset,
        transform: `rotate(-90 ${size / 2} ${size / 2})`,
        class: "chart-arc",
      });
      circle.style.animationDelay = `${i * 80}ms`;
      svg.appendChild(circle);
      offset += len;
    });
    const center = opts.center || "";
    if (center) {
      svg.appendChild(
        svgEl(
          "text",
          {
            x: size / 2,
            y: size / 2 + 6,
            fill: "var(--ink)",
            "font-size": 22,
            "font-weight": 700,
            "text-anchor": "middle",
            "font-family": "var(--font-display)",
          },
          center
        )
      );
    }
    host.appendChild(svg);
    return svg;
  }

  function heatmap(host, matrix, opts = {}) {
    // matrix: {rows: string[], cols: string[], cells: number[][] } values 0..1 or counts
    clear(host);
    const { rows, cols, cells } = matrix;
    const cell = opts.cell || 36;
    const labelW = opts.labelW || 110;
    const w = labelW + cols.length * cell + 8;
    const h = 28 + rows.length * cell + 8;
    const svg = svgEl("svg", {
      viewBox: `0 0 ${w} ${h}`,
      width: "100%",
      height: h,
      role: "img",
    });
    cols.forEach((c, i) => {
      svg.appendChild(
        svgEl(
          "text",
          {
            x: labelW + i * cell + cell / 2,
            y: 16,
            fill: "var(--muted)",
            "font-size": 11,
            "text-anchor": "middle",
          },
          c
        )
      );
    });
    rows.forEach((r, ri) => {
      svg.appendChild(
        svgEl(
          "text",
          {
            x: labelW - 8,
            y: 28 + ri * cell + cell / 2 + 4,
            fill: "var(--muted)",
            "font-size": 11,
            "text-anchor": "end",
          },
          r
        )
      );
      cols.forEach((_, ci) => {
        const v = (cells[ri] && cells[ri][ci]) || 0;
        const intensity = Math.min(1, v);
        const rect = svgEl("rect", {
          x: labelW + ci * cell + 2,
          y: 28 + ri * cell + 2,
          width: cell - 4,
          height: cell - 4,
          rx: 6,
          fill: `rgba(125, 211, 192, ${0.12 + intensity * 0.75})`,
          stroke: "var(--line)",
          "stroke-width": 1,
        });
        rect.setAttribute("title", `${r} × ${cols[ci]}: ${v}`);
        svg.appendChild(rect);
        if (opts.showValues) {
          svg.appendChild(
            svgEl(
              "text",
              {
                x: labelW + ci * cell + cell / 2,
                y: 28 + ri * cell + cell / 2 + 4,
                fill: "var(--ink)",
                "font-size": 11,
                "text-anchor": "middle",
                "font-family": "var(--font-mono)",
              },
              String(v)
            )
          );
        }
      });
    });
    host.appendChild(svg);
    return svg;
  }

  function probeTrack(host, probes, opts = {}) {
    // probes: [{prefix, holds, role}] — binary-search visualization
    clear(host);
    const wrap = document.createElement("div");
    wrap.className = "probe-track";
    const max = Math.max(1, ...(probes.map((p) => p.prefix) || [1]), opts.max || 1);
    probes.forEach((p, i) => {
      const node = document.createElement("div");
      node.className = "probe " + (p.holds ? "holds" : "fails");
      node.style.setProperty("--i", i);
      node.innerHTML =
        `<span class="probe-step">step ${p.prefix}</span>` +
        `<span class="probe-verdict">${p.holds ? "still ok" : "already broken"}</span>` +
        `<span class="probe-role">${p.role || ""}</span>`;
      const bar = document.createElement("div");
      bar.className = "probe-bar";
      bar.style.width = `${Math.max(8, (p.prefix / max) * 100)}%`;
      node.appendChild(bar);
      wrap.appendChild(node);
    });
    if (opts.lastGood != null) {
      const mark = document.createElement("div");
      mark.className = "probe-answer";
      mark.textContent = `Last step that was still correct: ${opts.lastGood}`;
      wrap.appendChild(mark);
    }
    host.appendChild(wrap);
  }

  function stepRibbon(host, steps, opts = {}) {
    clear(host);
    const ribbon = document.createElement("div");
    ribbon.className = "step-ribbon";
    const faultSet = new Set(opts.faults || []);
    steps.forEach((s) => {
      const cell = document.createElement("button");
      cell.type = "button";
      cell.className = "step-cell";
      if (s.blocked) cell.classList.add("blocked");
      if (s.superseded) cell.classList.add("superseded");
      if (s.rollback_to != null) cell.classList.add("rollback");
      if (faultSet.has(s.step)) cell.classList.add("fault");
      if (s.incidents && s.incidents.length) cell.classList.add("alarm");
      cell.title = `Step ${s.step}${s.tool ? ": " + s.tool : ""}`;
      cell.innerHTML = `<span>${s.step}</span>`;
      if (opts.onSelect) cell.addEventListener("click", () => opts.onSelect(s));
      ribbon.appendChild(cell);
    });
    host.appendChild(ribbon);
  }

  global.Charts = { barChart, groupedBars, donut, heatmap, probeTrack, stepRibbon };
})(window);
