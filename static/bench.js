import {
  $, $$, ROLE, availableRoles, badge, bindCanvas, button, changeLabel, definition, download,
  drawPoints, e, field, format, histogram, itemCard, metrics, normalizedPoint,
  num, percent, request, saveBlob, submit, table, wireForm,
} from "./ui.js";
import {showCalibration, showPlacement, showPlacementReview} from "./captures.js";
import {showFindingForm} from "./reviews.js";

const LAYERS = [
  ["panel", "EL image"], ["support", "Support"], ["quality", "Quality"], ["intensity", "Cell levels"],
  ["dark", "Dark regions"], ["cracks", "Crack traces"], ["response", "Current response"],
  ["strings", "Strings"], ["calibration", "Calibration"],
];
const TABS = Object.fromEntries(LAYERS);
const LEGENDS = {
  panel: [["#87dcc6", "Cell grid"], ["#c880ad", "Unavailable"]],
  support: [["#216c66", "Usable"], ["#b83c6d", "Clipped"], ["#bf813a", "Reference gap"], ["#383354", "Outside"]],
  quality: [["#ec5a8d", "Clipped"], ["#dc9b49", "Reference gap"], ["#73a8d4", "Low signal"]],
  intensity: [["#464197", "Lower signal"], ["#8ad0a1", "Higher signal"]],
  dark: [["#f5a968", "Dark evidence"]],
  cracks: [["#ff7b98", "Trace"], ["#ffe2a1", "Endpoint"], ["#9ce8d4", "Junction"], ["#638fc0", "Busbar band"]],
  response: [["#a1387b", "Weaker"], ["#f6ca78", "Reference range"], ["#20579e", "Stronger"]],
  strings: [["#a1387b", "Lower relative signal"], ["#40aeac", "Higher relative signal"]],
  calibration: [["#dc9b49", "Invalid reference"], ["#38649d", "Signal at floor"]],
};

const COLS = {
  quality: [["Cell", "cell"], ["Usable", "usable_fraction", percent], ["Clipped", "clipped_pixels"], ["Floor px", "near_floor_pixels"],
    ["Review flags", "flags", join]],
  intensity: [["Cell", "cell"], ["Support", "support", percent], ["Median", "median"], ["P10", "p10"], ["P90", "p90"],
    ["Relative", "relative_panel"]],
  dark: [["Region", "id"], ["Cell", "cell"], ["Pixels", "pixels"], ["Area mm²", "area_mm2"], ["Depth", "depth", percent],
    ["Open boundary", "open_boundary", join]],
  cracks: [["Trace", "id"], ["Cell", "cell"], ["Length px", "length_px"], ["Length mm", "length_mm"], ["Axis °", "axis_degrees"],
    ["Ends", "endpoints", length], ["Junctions", "junctions", length]],
  response: [["Cell", "cell"], ["Overlap", "support", percent], ["Ratio", "ratio"], ["Relative", "relative"],
    ["Weak px", "weak_fraction", percent], ["Assessment", "assessment"]],
  strings: [["String", "string"], ["Support", "coverage", percent], ["Median", "median_relative"], ["Range", "p90_p10_span"],
    ["Flags", "flags", join], ["Cells", "cells", join]],
};

function join(x) { return x?.length ? x.join(", ") : "—"; }
function length(x) { return x?.length ?? 0; }

export function renderWorkbench(app, root) {
  const ctx = app.workspace;
  const item = app.case;
  const roles = availableRoles(item);
  if (!roles.length) {
    root.innerHTML = itemCard("EL image bench", `<p class="small-copy">Add a high- or low-current grayscale image to begin.</p>
      <div class="form-actions start">${button("bench-upload", "Add captures", "primary")}</div>`);
    $("#bench-upload", root).onclick = () => app.showTab("captures");
    return;
  }
  if (ctx.layer === "response" && roles.length < 2) ctx.layer = "panel";
  if (!roles.includes(ctx.role)) ctx.role = roles[0];
  const displayRole = ctx.layer === "response" ? "high" : ctx.role;
  const roleButtons = roles.map((role) =>
    `<button type="button" data-role="${role}" aria-pressed="${role === displayRole}" ${ctx.layer === "response" ? "disabled" : ""}>${ROLE[role]}</button>`
  ).join("");
  const modes = [
    ["probe", "Probe"], ["polygon", "Region"], ["line", "Transect"], ["pair", "Compare pair"],
  ].map(([key, label]) => `<button type="button" class="compact" data-mode="${key}" aria-pressed="${ctx.mode === key}"
      ${key === "pair" && roles.length < 2 ? "disabled" : ""}>${label}</button>`).join("");
  root.innerHTML = `<div class="bench"><div class="section-stack">
    <section class="viewer-shell" aria-label="Inspection image viewer">
      <div class="viewer-top">
        <div class="toolbar">${roleButtons}</div><div class="toolbar">
        <button id="toggle-grid" aria-pressed="${ctx.grid}" type="button">Grid</button>
        ${button("display-settings", "Levels", "compact", item.state === "issued")}
        <button type="button" class="compact" id="zoom-view" title="Cycle image zoom">${ctx.zoom}%</button></div>
      </div>
      <div class="layers" aria-label="Measurement layers">${LAYERS.map(([key, label]) => `
        <button type="button" data-layer="${key}" class="${key === ctx.layer ? "selected" : ""}"
        ${key === "response" && roles.length < 2 ? "disabled" : ""}>${label}</button>`).join("")}</div>
      <div class="viewer-well">
        <div class="viewer-viewport" id="bench-viewport" style="width:${ctx.zoom}%">
          <img id="plane-image" width="${item.plane.width}" height="${item.plane.height}" alt="${e(TABS[ctx.layer])} for ${e(item.module)}"
             src="${e(layerUrl(app, displayRole, ctx.cell))}">
          <canvas id="plane-overlay" tabindex="0" aria-label="Inspect the image by clicking, or press Enter to sample its center"></canvas>
        </div>
      </div>
      <div class="viewer-footer">
        <span>${item.plane.width} × ${item.plane.height} px · ${item.plane.rows} × ${item.plane.cols} cells</span>
        ${legend(ctx.layer)}
      </div>
    </section>
    <section class="card"><div class="card-header"><h2>Image tools</h2><div class="toolbar">${modes}</div></div>
      <p class="tool-status" id="tool-message"></p>
      <div class="card-pad" id="tool-controls"></div>
    </section>
    <section class="card" id="layer-results"></section>
  </div><aside class="section-stack inspector" aria-label="Image measurement inspector">
    <section class="card"><div class="card-header"><h2>Layer summary</h2><span class="chip">${e(TABS[ctx.layer])}</span></div>
      <div class="card-pad" id="layer-metrics"><p class="small-copy">Loading measurement…</p></div></section>
    <section class="card" id="cell-card">
      <div class="card-header"><h2>Cell and pixel</h2>${ctx.cell ? badge(ctx.cell, "neutral") : ""}</div>
      <div class="card-pad" id="pixel-data"><p class="small-copy">Select a point in the image or a cell in a measurement table.</p></div>
    </section>
    ${selectedCapture(app, displayRole)}
    <section class="card"><div class="card-header"><h2>Image export</h2></div>
      <div class="card-pad toolbar">
      ${download(app.url(`/layer/${ctx.layer}.png`, {role: displayRole, grid: ctx.grid ? 1 : 0, findings: 1, download: 1}), "Layer PNG")}
      ${download(app.url("/export/plane", {role: displayRole}), "Calibrated TIFF")}
      ${download(app.url("/export/sheet", {role: displayRole}), "Cell contact sheet")}
      ${button("pack-images", "Cell evidence…")}
      </div></section>
  </aside></div>`;
  const plane = $("#plane-image", root);
  const canvas = $("#plane-overlay", root);
  const version = app.renderVersion;
  const redraw = bindCanvas(canvas, plane, (painter, width, height) => {
    drawPoints(painter, width, height, ctx.points, {
      close: ctx.mode === "polygon",
      numbered: ctx.mode === "polygon" || (ctx.mode === "line" && ctx.points.length > 2),
    });
    if (ctx.probe?.normalized) paintCrosshair(painter, width, height, ctx.probe.normalized);
  });
  plane.addEventListener("error", async () => {
    try {
      await request(plane.getAttribute("src"), "GET", null, true);
    } catch (error) {
      if (plane.isConnected) $("#tool-message", root).textContent = error.message;
    }
  });
  function go(point) {
    if (ctx.mode === "probe") probeAt(app, root, displayRole, point).then(redraw).catch(app.fail);
    else {
      const limit = ctx.mode === "polygon" ? 48 : 24;
      if (ctx.points.length >= limit) {
        return app.notify(ctx.mode === "polygon"
          ? "The region supports up to 48 points."
          : "A transect path supports up to 24 vertices.", true);
      }
      ctx.points.push(point);
      redraw();
      configureTools(app, root, displayRole, redraw);
    }
  }
  canvas.addEventListener("click", (event) => go(normalizedPoint(event, canvas)));
  canvas.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      go([.5, .5]);
    }
  });
  $$("[data-role]", root).forEach((node) => {
    node.onclick = () => { Object.assign(ctx, {role: node.dataset.role, probe: null, measure: null, points: []}); app.render(); };
  });
  $$("[data-layer]", root).forEach((node) => {
    node.onclick = () => { Object.assign(ctx, {layer: node.dataset.layer, probe: null, measure: null, points: []}); app.render(); };
  });
  $$("[data-mode]", root).forEach((node) => {
    node.onclick = () => {
      const mode = node.dataset.mode;
      if (mode === "pair") {
        compareDialog(app);
      } else {
        Object.assign(ctx, {mode, points: [], measure: null});
        $$("[data-mode]", root).forEach((btn) => btn.setAttribute("aria-pressed", String(node === btn)));
        configureTools(app, root, displayRole, redraw);
        redraw();
      }
    };
  });
  $("#toggle-grid", root).onclick = () => { ctx.grid = !ctx.grid; app.render(); };
  $("#zoom-view", root).onclick = (event) => {
    ctx.zoom = {100: 125, 125: 160, 160: 200, 200: 100}[ctx.zoom];
    $("#bench-viewport", root).style.width = `${ctx.zoom}%`;
    event.currentTarget.textContent = `${ctx.zoom}%`;
  };
  $("#display-settings", root).onclick = () => displayDialog(app);
  $("#pack-images", root).onclick = () => cellExportDialog(app, displayRole);
  $("#edit-placement", root).onclick = () => showPlacement(app, item.captures[item.active[displayRole]]);
  $("#diagnose-placement", root).onclick = () => showPlacementReview(app, displayRole);
  $("#bench-calibration", root).onclick = () => showCalibration(app);
  $("#measurement-settings", root)?.addEventListener("click", () => analysisDialog(app));
  configureTools(app, root, displayRole, redraw);
  const view = ctx.layer === "support" || ctx.layer === "panel" ? "quality" : ctx.layer;
  loadLayer(app, root, view, displayRole, version);
  if (ctx.cell) loadCell(app, root, displayRole, ctx.cell).catch(app.fail);
}

function legend(layer) {
  return `<span class="legend-line">${LEGENDS[layer].map(([color, label]) =>
    `<span><i style="--color:${color}"></i>${label}</span>`).join("")}</span>`;
}

function selectedCapture(app, role) {
  const selected = app.case.captures[app.case.active[role]];
  return `<section class="card"><div class="card-header"><h2>Selected capture</h2>${badge(ROLE[role], "neutral")}</div>
    <div class="card-pad section-stack">${definition([
      ["Image", selected.name], ["Native size", `${selected.width} × ${selected.height} px`], ["Samples", `${selected.bit_depth} bit`],
      ["Drive", selected.current_a == null ? "—" : `${selected.current_a} A`], ["Exposure", `${selected.exposure_ms} ms`],
    ])}<div class="toolbar">${button("edit-placement", "Corner placement")}
      ${button("diagnose-placement", "Check grid")}
      ${button("bench-calibration", "References")}</div></div></section>`;
}

function layerUrl(app, role, cell = "") {
  const ctx = app.workspace;
  return app.url(`/layer/${ctx.layer}.png`, {
    role, grid: ctx.grid ? 1 : 0, labels: ctx.labels ? 1 : 0,
    findings: ctx.findings ? 1 : 0, cell, revision: app.case.revision,
  });
}

function metricRows(kind, value) {
  const get = (label, key, fmt = format) => [label, fmt(value[key])];
  return {
    quality: [
      get("Usable pixels", "usable_pixels"), get("Clipped pixels", "clipped_pixels"),
      get("Reference gaps", "calibration_gap_pixels"), get("Cells flagged", "cell_review_count"),
    ],
    calibration: [
      get("Source coverage", "coverage", percent), get("Calibrated usable", "usable", percent),
      get("Invalid references", "invalid_calibration", percent), get("At signal floor", "floor_signal", percent),
    ],
    intensity: [get("Usable cells", "eligible_cells"), get("Median cell signal", "median")],
    dark: [
      get("Connected regions", "count"), get("Affected cells", "covered_cells"),
      get("Dark pixels", "dark_pixels"), get("Measured dark area", "dark_fraction", percent),
    ],
    cracks: [
      get("Candidate traces", "count"), get("Cells with traces", "cells_with_traces"),
      get("Length in pixels", "trace_length_px"), get("Length in mm", "trace_length_mm"),
    ],
    response: [
      get("Usable cells", "usable_cells"), get("Weak cells", "weak_cells"),
      get("Ratio reference", "reference_ratio"), get("Overlap pixels", "shared_pixels"),
    ],
    strings: [get("Strings", "groups"), get("Flagged strings", "groups_with_flags")],
  }[kind] || [];
}

async function loadLayer(app, root, kind, role, version) {
  const summary = $("#layer-metrics", root);
  const result = $("#layer-results", root);
  try {
    const model = await request(app.url(`/measure/${kind}`, {role}));
    if (app.renderVersion !== version) return;
    summary.innerHTML = metrics(metricRows(kind, model.summary))
      + histogram(model.summary.histogram ?? model.summary.relative_histogram, "Signal distribution")
      + (model.summary.messages || []).map((message) => `<p class="warning">${e(message)}</p>`).join("");
    result.innerHTML = `<div class="card-header"><h2>${e(TABS[kind])} measurements</h2><div class="toolbar">
      ${button("measurement-settings", "Thresholds", "compact", app.case.state === "issued")}
      ${download(app.url(`/measure/${kind}`, {role, format: "csv"}), "CSV")}</div></div>`;
    result.innerHTML += kind === "calibration"
      ? `<div class="card-pad">${definition([
        ["Dark reference", model.summary.dark ? "Applied" : "Disabled or skipped"],
        ["Flat reference", model.summary.flat ? "Applied" : "Disabled or skipped"],
        ["Flat normalization", format(model.summary.flat_normalization)],
        ["Calibrated signal", format(model.summary.signal.median)],
      ])}</div>`
      : table(COLS[kind], model.rows, (row) => row.cell
        ? `<button type="button" class="compact" data-cell="${e(row.cell)}" aria-label="Inspect ${e(row.cell)}">Inspect</button>`
        : "");
    $$("[data-cell]", result).forEach((node) => {
      node.onclick = () => selectCell(app, root, role, node.dataset.cell);
    });
    $("#measurement-settings", result).onclick = () => analysisDialog(app);
  } catch (error) {
    if (app.renderVersion !== version) return;
    summary.innerHTML = `<p class="error">${e(error.message)}</p>`;
  }
}

function paintCrosshair(ctx, width, height, [px, py]) {
  const x = px * width;
  const y = py * height;
  ctx.beginPath();
  ctx.moveTo(x - 12, y);
  ctx.lineTo(x + 12, y);
  ctx.moveTo(x, y - 12);
  ctx.lineTo(x, y + 12);
  ctx.strokeStyle = "#ffda97";
  ctx.lineWidth = 1.5;
  ctx.stroke();
}

async function probeAt(app, root, role, normalized) {
  const x = Math.min(app.case.plane.width - 1, Math.floor(normalized[0] * app.case.plane.width));
  const y = Math.min(app.case.plane.height - 1, Math.floor(normalized[1] * app.case.plane.height));
  const ctx = app.workspace;
  const viewAt = app.renderVersion;
  const probe = await request(app.url("/pixel", {role, x, y, pair: ctx.layer === "response" ? 1 : 0}));
  if (viewAt !== app.renderVersion) return;
  ctx.probe = {...probe, normalized};
  selectCell(app, root, role, probe.cell);
}

function selectCell(app, root, role, cell) {
  app.workspace.cell = cell || "";
  const plane = $("#plane-image", root);
  plane.src = layerUrl(app, role, cell);
  loadCell(app, root, role, cell).catch(app.fail);
}

async function loadCell(app, root, role, cell) {
  if (!cell) return;
  const version = app.renderVersion;
  const power = app.workspace;
  const value = await request(app.url(`/cells/${cell}`, {role}));
  if (version !== app.renderVersion || power.cell !== cell) return;
  const probe = power.probe;
  const plot = $("#pixel-data", root);
  $(".card-header", $("#cell-card", root)).innerHTML = `<h2>Cell and pixel</h2>${badge(cell, "neutral")}`;
  plot.innerHTML = `<div class="section-stack">${definition([
    ["Cell", cell], ["String", value.string], ["Median signal", format(value.intensity.median)], ["Support", percent(value.intensity.support)],
    ["Dark regions", value.dark.length], ["Crack traces", value.cracks.length],
    ...(probe ? [["Plane pixel", `${probe.x}, ${probe.y}`], ["Sensor point", probe.source.map((x) => format(x, 2)).join(", ")],
      ["Signal", format(probe.signal, 5)], ["Status", probe.warnings.join(", ") || "Usable"],
      ...(probe.response ? [["Paired ratio", format(probe.response.ratio)]] : [])] : []),
  ])}<img class="card-image" src="${e(app.url(`/cells/${cell}/panel.png`, {role}))}" alt="${cell} detail">
  <div class="toolbar">${download(app.url("/export/cells", {role, cell}), "Cell evidence")}</div></div>`;
}

function configureTools(app, root, role, redraw) {
  const ctx = app.workspace;
  const mode = ctx.mode;
  const tool = $("#tool-controls", root);
  const status = $("#tool-message", root);
  if (mode === "probe") {
    status.textContent = "Pick a point to read calibrated signal and original sensor coordinates.";
    tool.innerHTML = `<div class="toolbar">${button("clear-probe", "Clear selection", "compact", !ctx.cell)}</div>`;
    $("#clear-probe", tool).onclick = () => {
      Object.assign(ctx, {cell: "", probe: null});
      app.render();
    };
    return;
  }
  if (mode === "polygon") {
    status.textContent = `Click around the region. ${ctx.points.length} vertices.`;
    tool.innerHTML = `<div class="toolbar">${button("measure-shape", "Measure region", "primary", ctx.points.length < 3)}
      ${button("record-shape", "Save finding", "", ctx.points.length < 3 || app.case.state === "issued")}
      ${button("undo-point", "Remove last", "compact", !ctx.points.length)}
      ${button("clear-shape", "Clear", "compact", !ctx.points.length)}</div>
      <div id="region-result"></div>`;
    $("#measure-shape", tool).onclick = () => measureRegion(app, tool, role);
    $("#record-shape", tool).onclick = () => showFindingForm(app, {
      points: ctx.points.map((value) => [...value]), source_role: role, cell: ctx.cell, status: "open", category: "suspect",
    });
    $("#undo-point", tool).onclick = () => { ctx.points.pop(); redraw(); configureTools(app, root, role, redraw); };
    $("#clear-shape", tool).onclick = () => { ctx.points = []; redraw(); configureTools(app, root, role, redraw); };
    return;
  }
  const count = ctx.points.length;
  status.textContent = count < 2
    ? "Click the transect vertices in order. Two points make a straight strip; add more to follow a bend."
    : `${count} vertices, ${count - 1} segment${count > 2 ? "s" : ""}. Run the cross-section or keep adding points.`;
  tool.innerHTML = `<form class="section-stack"><div class="fields">
    ${num("Strip width (odd px)", "width", ctx.strip?.width ?? 5, 1, 51, 2)}
    ${num("Interval (px)", "interval", ctx.strip?.interval ?? 1, .5, 24, .5)}</div>
    <p class="form-error" hidden></p><div class="toolbar">
    ${count >= 2 ? submit("Measure transect") : button("pending-transect", "Measure transect", "primary", true)}
    ${button("undo-line", "Remove last", "compact", !count)}
    ${button("clear-line", "Clear", "compact", !count)}
    ${button("save-line", "Save path…", "compact", count < 2 || app.case.state === "issued")}</div></form>
    <div id="line-result"></div>${savedPathList(app)}`;
  $("#undo-line", tool).onclick = () => { ctx.points.pop(); redraw(); configureTools(app, root, role, redraw); };
  $("#clear-line", tool).onclick = () => { ctx.points = []; redraw(); configureTools(app, root, role, redraw); };
  $("#save-line", tool).onclick = () => {
    const settings = Object.fromEntries(new FormData($("form", tool)));
    savePathDialog(app, role, settings);
  };
  $$("[data-open-path]", tool).forEach((node) => {
    node.onclick = () => {
      const path = (app.case.paths || []).find((entry) => entry.id === node.dataset.openPath);
      if (!path) return;
      ctx.points = path.points.map((value) => [...value]);
      ctx.strip = {width: path.width, interval: path.interval};
      redraw();
      configureTools(app, root, role, redraw);
      if (path.source_role !== role) app.notify(`Path saved for ${ROLE[path.source_role].toLowerCase()}; measuring the current view.`);
    };
  });
  $$("[data-drop-path]", tool).forEach((node) => {
    node.onclick = async () => {
      try {
        const updated = await request(app.url(`/paths/${node.dataset.dropPath}`), "DELETE", {revision: app.case.revision});
        await app.update(updated, "Transect path removed.");
      } catch (error) {
        app.fail(error);
      }
    };
  });
  wireForm(tool, async (params) => {
    const payload = {...params, role, points: ctx.points.map((value) => [...value])};
    ctx.strip = {width: params.width, interval: params.interval};
    const result = await request(app.url("/transect"), "POST", payload);
    const other = $("#line-result", tool);
    const segments = result.summary.segments || [];
    const overlaps = result.summary.overlaps || [];
    other.innerHTML = `<div class="section-stack">${metrics([
      ["Distance", result.summary.mm_traveled ? `${format(result.summary.mm_traveled)} mm` : `${format(result.summary.pixels_traveled)} px`],
      ["Usable positions", `${result.summary.data_positions} / ${result.summary.positions}`],
      ...(result.summary.shared_positions ? [["Shared corner stations", result.summary.shared_positions]] : []),
    ])}
    ${segments.length > 1 ? table(SEGMENT_COLS, segments) : ""}
    ${overlaps.map((zone) => `<p class="info">Segments ${zone.segments.join(" & ")} share stations
      ${zone.first}–${zone.last} (${zone.positions} positions) around ${format(zone.start)}–${format(zone.end)} px.</p>`).join("")}
    <div class="toolbar">${button("transect-csv", "Profile CSV")}${button("transect-chart", "Profile plot")}</div></div>`;
    $("#transect-csv", other).onclick = () => downloadTool(app, "/transect", payload, "csv");
    $("#transect-chart", other).onclick = () => showTransectPlot(app, payload);
  });
}

const SEGMENT_COLS = [
  ["Seg", "segment"], ["Stations", "stations"], ["Usable", "data_positions"],
  ["Cells", "cells", join], ["Shared", "shared"],
  ["Median", "signal", (value) => format(value?.median, 4)],
];

function savedPathList(app) {
  const paths = app.case.paths || [];
  if (!paths.length) return "";
  return `<div class="section-stack"><h3>Saved transect paths</h3><div class="list">${paths.map((path) => `
    <div class="list-item"><div><strong>${e(path.name)}</strong>
    <small>${path.points.length} vertices · width ${path.width} · interval ${path.interval} px · ${e(ROLE[path.source_role])}</small></div>
    <div class="toolbar"><button type="button" class="compact" data-open-path="${path.id}">Open</button>
    <button type="button" class="compact" data-drop-path="${path.id}" ${app.case.state === "issued" ? "disabled" : ""}>Delete</button>
    </div></div>`).join("")}</div></div>`;
}

function savePathDialog(app, role, settings) {
  const ctx = app.workspace;
  const count = (app.case.paths || []).length;
  const root = app.openDialog("Save transect path", `<form><div class="fields">
    ${field("Path name", "name", `Path ${count + 1}`, {required: true, wide: true, max: 80})}
    </div><p class="small-copy">${ctx.points.length} vertices · strip width ${e(settings.width)} px ·
      interval ${e(settings.interval)} px · ${e(ROLE[role])}. The path stays with this inspection and can be reopened on the image.</p>
    <p class="form-error" hidden></p><div class="form-actions">${submit("Save path")}</div></form>`);
  wireForm(root, async ({name}) => {
    const payload = {
      name, source_role: role, points: ctx.points.map((value) => [...value]),
      width: settings.width, interval: settings.interval, revision: app.case.revision,
    };
    const updated = await request(app.url("/paths"), "POST", payload);
    app.modal.close();
    await app.update(updated, "Transect path saved.");
  });
}

async function downloadTool(app, path, payload, format) {
  try {
    const raw = await request(app.url(path), "POST", {...payload, format}, true);
    saveBlob(raw, `${app.case.module}${path.replace("/", "-")}.${format}`);
  } catch (error) {
    app.fail(error);
  }
}

async function measureRegion(app, tool, role) {
  const output = $("#region-result", tool);
  const data = {role, points: app.workspace.points.map((value) => [...value])};
  output.textContent = "Measuring…";
  try {
    const result = await request(app.url("/polygon"), "POST", data);
    output.innerHTML = `<div class="section-stack">${metrics([
      ["Area pixels", format(result.pixels)], ["Support", percent(result.usable_fraction)],
      ["Area mm²", format(result.area_mm2)], ["Median signal", format(result.signal.median)],
    ])}${histogram(result.histogram, "Region signal histogram")}
    <div class="toolbar">${button("region-png", "Region PNG")}</div></div>`;
    $("#region-png", tool).onclick = () => downloadTool(app, "/polygon", data, "png");
  } catch (error) {
    output.innerHTML = `<p class="error">${e(error.message)}</p>`;
  }
}

async function showTransectPlot(app, payload) {
  const root = app.openDialog("Transect profile", '<p class="small-copy">Generating plot…</p>', true);
  try {
    const plot = await request(app.url("/transect"), "POST", {...payload, format: "png"}, true);
    if (!root.isConnected) return;
    const reference = URL.createObjectURL(plot);
    root.innerHTML = `<img class="pair-image" src="${e(reference)}" alt="Image signal along the selected line">
      <div class="toolbar">${button("save-profile", "Plot PNG")}</div>`;
    $("#save-profile", root).onclick = () => saveBlob(plot, `${app.case.module}-profile.png`);
    app.modal.addEventListener("close", () => URL.revokeObjectURL(reference), {once: true});
  } catch (error) {
    root.textContent = error.message;
  }
}

function displayDialog(app) {
  const values = app.case.display;
  const root = app.openDialog("Display levels", `<form><div class="fields">
    ${num("Black level", "lower", values.lower, 0, 1.8, .001)}
    ${num("White level", "upper", values.upper, .001, 2, .001)}
    ${num("Gamma", "gamma", values.gamma, .12, 4, .01)}</div>
    <p class="small-copy">Display levels affect preview contrast. Measurements use the calibrated values.</p>
    <p class="form-error" hidden></p><div class="form-actions">${submit("Apply levels")}</div></form>`);
  wireForm(root, async (data) => { await app.patch("display", data, "Display levels saved."); app.modal.close(); });
}

function analysisDialog(app) {
  const settings = app.case.analysis;
  const root = app.openDialog("Measurement thresholds", `<form><div class="fields">
    ${num("Dark fraction", "dark_fraction", settings.dark_fraction, .05, .95, .01)}
    ${num("Minimum dark area (px)", "minimum_area", settings.minimum_area, 4, 3500)}
    ${num("Crack contrast", "crack_contrast", settings.crack_contrast, .025, .45, .005)}
    ${num("Minimum trace length (px)", "minimum_length", settings.minimum_length, 4, 250)}
    ${num("Required cell support", "usable_cell", settings.usable_cell, .1, .98, .01)}
    ${num("Relative response floor", "current_ratio", settings.current_ratio, .15, .95, .01)}
    ${num("Trace border clearance (px)", "ignore_margin", settings.ignore_margin, 0, 8)}
    ${num("Busbar half width (px)", "busbar_radius", settings.busbar_radius, 1, 16)}
    ${field("Busbar positions across the cell (fractions)", "busbars", (settings.busbars || []).join(", "), {wide: true, max: 90})}
    </div><p class="form-error" hidden></p><div class="form-actions">${submit("Save thresholds")}</div></form>`);
  wireForm(root, async (data) => { await app.patch("analysis", data, "Measurements recalculated."); app.modal.close(); });
}

function compareDialog(app) {
  const root = app.openDialog("Compare low and high current", `<div class="paired-control"><div class="fields">
    ${field("View", "mode", "side", {values: [["side", "Side by side"], ["wipe", "Divider"], ["checker", "Checkerboard"]]})}
    ${field("Levels", "contrast", "shared", {values: [["shared", "Shared intensity scale"], ["individual", "Auto per image"]]})}
    <label class="wide">Divider<input type="range" name="wipe" min="0" max="1" value=".5" step=".025"></label>
    </div><img id="paired-image" class="pair-image" alt="Rectified high and low current comparison">
    <div id="pair-result"></div></div>`, true);
  let timer;
  let requestId = 0;
  const repaint = async () => {
    const now = ++requestId;
    const settings = Object.fromEntries($$("select,input", root).map((node) => [node.name, node.value]));
    if (settings.mode !== "wipe") delete settings.wipe;
    $("#paired-image", root).src = app.url("/pair/png", settings);
    try {
      const detail = await request(app.url("/pair/data", settings));
      if (now !== requestId || !root.isConnected) return;
      $("#pair-result", root).innerHTML = metrics([
        ["Shared pixels", format(detail.paired_pixels)], ["High only", format(detail.high_only)],
        ["Low only", format(detail.low_only)], ["Outside or invalid in both", format(detail.missing_both)],
      ]);
    } catch (error) {
      if (root.isConnected) $("#pair-result", root).innerHTML = `<p class="error">${e(error.message)}</p>`;
    }
  };
  $$("select,input", root).forEach((node) => node.oninput = () => { clearTimeout(timer); timer = setTimeout(repaint, 120); });
  repaint();
}

function cellExportDialog(app, role) {
  const root = app.openDialog("Export cell evidence", `<form><div class="fields">
    ${field("Cell IDs, separated by commas", "cells", app.workspace.cell,
      {wide: true, required: true, max: 180})}
    </div><p class="small-copy">Up to 18 selected cells. Includes image crops, calibrated measurements and source settings.</p>
    <p class="form-error" hidden></p><div class="form-actions">${submit("Download evidence ZIP")}</div></form>`);
  wireForm(root, async ({cells}) => {
    const data = await request(app.url("/export/cells", {role, cell: cells.split(",").map((value) => value.trim()).filter(Boolean)}),
      "GET", null, true);
    saveBlob(data, `${app.case.module}-cells.zip`);
  });
}
