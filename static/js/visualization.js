/**
 * Thread-interaction timeline visualization using D3.js.
 *
 * Renders a Gantt-chart-style timeline where each row represents a thread
 * and each block represents a variable access (read / write / lock).
 * Conflicting accesses are connected by a highlighted arc.
 */

const VIZ = (() => {
  // ── Layout constants ────────────────────────────────────────────────────
  const LANE_HEIGHT    = 56;   // px per thread row
  const LANE_PADDING   = 8;    // vertical padding inside a lane
  const LABEL_WIDTH    = 120;  // px for left thread-label column
  const TIME_SCALE     = 48;   // px per logical time unit
  const BLOCK_HEIGHT   = LANE_HEIGHT - LANE_PADDING * 2;
  const MIN_BLOCK_W    = 20;
  const ARC_OFFSET     = 22;   // how far above lanes the conflict arcs sit
  const MARGIN         = { top: 12, right: 20, bottom: 20 };

  // ── Colour palette ──────────────────────────────────────────────────────
  const ACCESS_COLORS = {
    write:  "#f44336",
    read:   "#2196f3",
    lock:   "#4caf50",
    unlock: "#8bc34a",
  };

  const CONFLICT_COLOR = "#ff5722";

  // ── State ───────────────────────────────────────────────────────────────
  let _timeline = null;

  // ── Tooltip ─────────────────────────────────────────────────────────────
  const tooltip = d3.select(".viz-tooltip");

  function showTooltip(event, d) {
    let html = `<strong>${d.access_type.toUpperCase()}</strong>`;
    html += ` — <code>${d.variable}</code><br>`;
    html += `Thread: <em>${d.thread}</em><br>`;
    if (d.line) html += `Line: ${d.line}<br>`;
    if (d.protected_by) html += `🔒 Protected by <code>${d.protected_by}</code>`;
    else if (d.access_type === "read" || d.access_type === "write") {
      html += `<span style="color:#f44336">⚠ Unprotected</span>`;
    }
    if (d.is_conflict) html += `<br><span style="color:#ff5722;font-weight:700">❌ RACE CONDITION</span>`;

    tooltip
      .html(html)
      .style("left",  (event.clientX + 12) + "px")
      .style("top",   (event.clientY - 10) + "px")
      .classed("visible", true);
  }

  function hideTooltip() {
    tooltip.classed("visible", false);
  }

  // ── Main render ─────────────────────────────────────────────────────────
  function render(timeline) {
    _timeline = timeline;

    const container = document.getElementById("timeline-svg-wrapper");
    if (!container) return;
    container.innerHTML = "";

    const threads   = timeline.threads   || [];
    const events    = timeline.events    || [];
    const conflicts = timeline.conflicts || [];

    if (threads.length === 0) return;

    // Compute SVG dimensions
    const maxTime = Math.max(...events.map(e => e.end), 1);
    const svgW = LABEL_WIDTH + maxTime * TIME_SCALE + MARGIN.right;
    const svgH = MARGIN.top + threads.length * LANE_HEIGHT + ARC_OFFSET + MARGIN.bottom;

    const svg = d3.select(container)
      .append("svg")
      .attr("id", "timeline-svg")
      .attr("width", svgW)
      .attr("height", svgH)
      .attr("role", "img")
      .attr("aria-label", "Thread interaction timeline");

    // ── Background lanes ──────────────────────────────────────────────────
    const lanesG = svg.append("g").attr("class", "lanes");

    threads.forEach((thread, i) => {
      const y = MARGIN.top + ARC_OFFSET + i * LANE_HEIGHT;

      lanesG.append("rect")
        .attr("x", 0)
        .attr("y", y)
        .attr("width", svgW)
        .attr("height", LANE_HEIGHT)
        .attr("fill", i % 2 === 0 ? "#1e2130" : "#1a1d27")
        .attr("stroke", "#2e3250")
        .attr("stroke-width", 0.5);

      // Thread label
      const labelG = lanesG.append("g")
        .attr("transform", `translate(0, ${y + LANE_HEIGHT / 2})`);

      // Coloured thread indicator bar
      labelG.append("rect")
        .attr("x", 0)
        .attr("y", -LANE_HEIGHT / 2)
        .attr("width", 4)
        .attr("height", LANE_HEIGHT)
        .attr("fill", thread.color || "#6c63ff");

      labelG.append("text")
        .attr("x", 10)
        .attr("y", 5)
        .attr("fill", "#e8eaf6")
        .attr("font-size", "12px")
        .attr("font-family", "monospace")
        .attr("dominant-baseline", "middle")
        .text(_truncate(thread.name, 14));
    });

    // ── Time axis grid ────────────────────────────────────────────────────
    const axisG = svg.append("g").attr("class", "axis");
    const gridTop = MARGIN.top + ARC_OFFSET;
    const gridBottom = gridTop + threads.length * LANE_HEIGHT;

    for (let t = 0; t <= maxTime; t += 2) {
      const x = LABEL_WIDTH + t * TIME_SCALE;
      axisG.append("line")
        .attr("x1", x).attr("y1", gridTop)
        .attr("x2", x).attr("y2", gridBottom)
        .attr("stroke", "#2e3250")
        .attr("stroke-dasharray", "3,3")
        .attr("stroke-width", 0.8);

      axisG.append("text")
        .attr("x", x)
        .attr("y", gridTop - 4)
        .attr("fill", "#9fa8b3")
        .attr("font-size", "10px")
        .attr("text-anchor", "middle")
        .text(`t=${t}`);
    }

    // ── Conflict arcs ─────────────────────────────────────────────────────
    const arcsG = svg.append("g").attr("class", "conflict-arcs");

    const eventById = {};
    events.forEach(e => { eventById[e.id] = e; });

    const threadIndex = {};
    threads.forEach((t, i) => { threadIndex[t.id] = i; });

    conflicts.forEach(cf => {
      const e1 = eventById[cf.event1_id];
      const e2 = eventById[cf.event2_id];
      if (!e1 || !e2) return;

      const x1 = LABEL_WIDTH + ((e1.start + e1.end) / 2) * TIME_SCALE;
      const x2 = LABEL_WIDTH + ((e2.start + e2.end) / 2) * TIME_SCALE;
      const y1 = MARGIN.top + ARC_OFFSET + (threadIndex[e1.thread] || 0) * LANE_HEIGHT + LANE_HEIGHT / 2;
      const y2 = MARGIN.top + ARC_OFFSET + (threadIndex[e2.thread] || 0) * LANE_HEIGHT + LANE_HEIGHT / 2;

      // Draw a curved path between the two conflicting events
      const mx = (x1 + x2) / 2;
      const my = Math.min(y1, y2) - ARC_OFFSET;

      arcsG.append("path")
        .attr("d", `M ${x1} ${y1} Q ${mx} ${my} ${x2} ${y2}`)
        .attr("fill", "none")
        .attr("stroke", CONFLICT_COLOR)
        .attr("stroke-width", 1.5)
        .attr("stroke-dasharray", "5,3")
        .attr("opacity", 0.85);

      // Conflict label
      arcsG.append("text")
        .attr("x", mx)
        .attr("y", my - 4)
        .attr("text-anchor", "middle")
        .attr("fill", CONFLICT_COLOR)
        .attr("font-size", "10px")
        .attr("font-weight", "bold")
        .text(`⚡ ${cf.conflict_type}`);
    });

    // ── Event blocks ──────────────────────────────────────────────────────
    const eventsG = svg.append("g").attr("class", "events");

    events.forEach(ev => {
      const tIdx = threadIndex[ev.thread];
      if (tIdx === undefined) return;

      const x = LABEL_WIDTH + ev.start * TIME_SCALE;
      const w = Math.max(MIN_BLOCK_W, (ev.end - ev.start) * TIME_SCALE - 2);
      const y = MARGIN.top + ARC_OFFSET + tIdx * LANE_HEIGHT + LANE_PADDING;
      const color = ACCESS_COLORS[ev.access_type] || "#9e9e9e";

      const isConflict = ev.is_conflict;
      const g = eventsG.append("g")
        .attr("transform", `translate(${x}, ${y})`)
        .attr("cursor", "pointer")
        .on("mousemove", (event) => showTooltip(event, ev))
        .on("mouseleave", hideTooltip);

      // Block background
      g.append("rect")
        .attr("width", w)
        .attr("height", BLOCK_HEIGHT)
        .attr("rx", 4)
        .attr("fill", color)
        .attr("fill-opacity", isConflict ? 0.9 : 0.55)
        .attr("stroke", isConflict ? CONFLICT_COLOR : color)
        .attr("stroke-width", isConflict ? 2 : 1);

      // Conflict glow
      if (isConflict) {
        g.append("rect")
          .attr("width", w + 4)
          .attr("height", BLOCK_HEIGHT + 4)
          .attr("x", -2)
          .attr("y", -2)
          .attr("rx", 6)
          .attr("fill", "none")
          .attr("stroke", CONFLICT_COLOR)
          .attr("stroke-width", 1)
          .attr("stroke-dasharray", "4,2")
          .attr("opacity", 0.6);
      }

      // Label text
      const label = ev.access_type === "lock"   ? "🔒"
                  : ev.access_type === "unlock" ? "🔓"
                  : `${ev.access_type === "write" ? "W" : "R"}:${_truncate(ev.variable, 8)}`;

      if (w > 20) {
        g.append("text")
          .attr("x", w / 2)
          .attr("y", BLOCK_HEIGHT / 2 + 1)
          .attr("text-anchor", "middle")
          .attr("dominant-baseline", "middle")
          .attr("fill", "#fff")
          .attr("font-size", "10px")
          .attr("font-family", "monospace")
          .attr("pointer-events", "none")
          .text(label);
      }
    });
  }

  // ── Helpers ──────────────────────────────────────────────────────────────
  function _truncate(str, max) {
    return str.length > max ? str.slice(0, max - 1) + "…" : str;
  }

  function clear() {
    const c = document.getElementById("timeline-svg-wrapper");
    if (c) c.innerHTML = "";
    _timeline = null;
  }

  return { render, clear };
})();
