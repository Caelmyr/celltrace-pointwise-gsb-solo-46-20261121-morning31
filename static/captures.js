import {
  $, $$, ROLE, badge, bindCanvas, button, definition, download, drawPoints, e, field, format,
  histogram, itemCard, metrics, normalizedPoint, num, percent, request, submit, table, wireForm,
} from "./ui.js";

const SENSOR_AXIS = [["rows", "Sensor rows"], ["columns", "Sensor columns"]];
const PLACEMENT_OPTIONS = [["identity", "As captured"], ["rotate180", "Rotate 180°"], ["mirror-x", "Mirror left to right"], ["mirror-y", "Mirror top to bottom"]];
const REF_ROLES = ["dark", "flat"];

export function renderCaptures(app, root) {
  const active = app.case.active;
  const closed = app.case.state === "issued";
  const frames = Object.values(app.case.captures);
  const content = Object.keys(ROLE).map((role) => {
    const values = frames.filter((capture) => capture.role === role);
    if (!values.length) return "";
    return `<section><h2 class="section-title">${ROLE[role]}</h2><div class="capture-grid">${values.map((frame) => {
      const selected = active[role] === frame.id;
      const assignedReference = Object.values(app.case.calibration).includes(frame.id);
      return `<article class="card"><img class="capture-preview" loading="lazy" alt="${e(frame.name)} preview"
        src="${e(app.url(`/captures/${frame.id}/thumbnail`))}">
        <div class="capture-info"><div class="toolbar">${selected ? badge("Selected") : ""}
        ${assignedReference ? badge("Applied reference", "neutral") : ""}</div>
        <h3>${e(frame.name)}</h3><p class="small-copy">${frame.width} × ${frame.height} · ${frame.bit_depth} bit · ${frame.exposure_ms} ms
        ${frame.current_a ? ` · ${frame.current_a} A` : ""}</p>
        <div class="row-actions">
          <button type="button" data-action="details" data-frame="${frame.id}" class="compact">Image details</button>
          ${["high", "low", "surface"].includes(role) ? `<button type="button" data-action="placement" data-frame="${frame.id}" class="compact">Corners</button>` :
            `<button type="button" data-action="reference" data-frame="${frame.id}" class="compact">Banding</button>`}
          ${closed || selected ? "" : `<button type="button" data-action="select" data-frame="${frame.id}" class="compact">Use this image</button>`}
          ${download(app.url(`/captures/${frame.id}/raw`), "Original")}
        </div></div></article>`;
    }).join("")}</div></section>`;
  }).join("");
  root.innerHTML = `<div class="page-stack">
    ${itemCard("Original image inventory", `<div class="toolbar">
      ${button("capture-upload", "Upload original", "primary", closed)}${button("capture-calibrate", "Assign references")}
      ${button("reference-drift", "Compare reference captures", "", !REF_ROLES.some((role) => frames.filter((cap) => cap.role === role).length >= 2))}
    </div>${frames.length ? "" : '<p class="empty-line">Grayscale 8- or 16-bit PNG and TIFF images. Maximum 22 MiB per original.</p>'}`)}
    ${content}</div>`;
  $("#capture-upload", root).onclick = () => uploadDialog(app);
  $("#capture-calibrate", root).onclick = () => showCalibration(app);
  $("#reference-drift", root).onclick = () => referenceComparisonDialog(app);
  $$("[data-action]", root).forEach((node) => {
    node.onclick = () => {
      const frame = app.case.captures[node.dataset.frame];
      switch (node.dataset.action) {
        case "details": statsDialog(app, frame); break;
        case "placement": showPlacement(app, frame); break;
        case "reference": bandingDialog(app, frame); break;
        case "select": app.patch("selection", {capture: frame.id}, `${ROLE[frame.role]} selected.`).catch(app.fail); break;
      }
    };
  });
}

function uploadDialog(app) {
  const root = app.openDialog("Upload a sensor original", `<form><div class="fields">
    ${field("Original grayscale PNG or TIFF", "file", "", {type: "file", accept: ".png,.tif,.tiff", required: true, wide: true})}
    ${field("Capture role", "role", app.case.active.high ? "low" : "high", {values: Object.entries(ROLE)})}
    ${num("Exposure (ms)", "exposure_ms", 100, .01, 600000, .01)}
    ${num("Drive current (A)", "current_a", null, .001, 100, .001, false)}
    ${num("White level (optional)", "white_level", null, 16, 65535, 1, false)}</div>
    <label class="small-copy"><input type="checkbox" name="select" value="true" checked> Select for this role after upload</label>
    <p class="form-error" hidden></p><div class="form-actions">${submit("Import original")}</div></form>`);
  const selector = $('select[name=role]', root);
  const current = $('input[name=current_a]', root);
  function toggleCurrent() {
    const sensorRole = ["high", "low"].includes(selector.value);
    current.disabled = !sensorRole;
    if (!sensorRole) current.value = "";
  }
  selector.onchange = toggleCurrent;
  toggleCurrent();
  wireForm(root, async (_, data) => {
    data.set("revision", app.case.revision);
    if (!data.has("select")) data.set("select", "false");
    const updated = await request(app.url("/captures"), "POST", data);
    app.modal.close();
    await app.update(updated, "Original imported.");
  });
}

export function showPlane(app) {
  const model = app.case.plane;
  const disabled = app.case.state === "issued";
  const root = app.openDialog("Configured panel plane", `<form><fieldset ${disabled ? "disabled" : ""}>
    <div class="fields">
      ${num("Rectified width (px)", "width", model.width, 96, 1800)}
      ${num("Rectified height (px)", "height", model.height, 96, 1800)}
      ${num("Cell rows", "rows", model.rows, 1, 16)}
      ${num("Cell columns", "cols", model.cols, 1, 24)}
      ${num("Physical width (mm)", "physical_width", model.physical_width, 20, 12000, .01, false)}
      ${num("Physical height (mm)", "physical_height", model.physical_height, 20, 12000, .01, false)}
      ${num("Cell measurement inset", "inset", model.inset, 0, .3, .01)}
      ${field("String direction", "string_axis", model.string_axis, {values: [["columns", "Column groups"], ["rows", "Row groups"]]})}
      ${num("Rows or columns per string", "string_size", model.string_size, 1, 24)}
    </div></fieldset>
    <p class="small-copy">Corners are saved per EL capture. Enter both physical dimensions to include metric distances and areas.</p>
    <p class="form-error" hidden></p><div class="form-actions">
      ${disabled ? "" : submit("Apply plane")}</div></form>`);
  wireForm(root, async (values) => {
    await app.patch("plane", values, "Panel plane updated.");
    app.modal.close();
  });
}

export function showCalibration(app) {
  const references = app.case.calibration;
  const captures = Object.values(app.case.captures);
  const disabled = app.case.state === "issued";
  const choices = (role) => [["", "Disabled"], ...captures.filter((frame) => frame.role === role)
    .map((frame) => [frame.id, `${frame.name} · ${frame.exposure_ms} ms`])];
  const root = app.openDialog("Sensor calibration", `<form><fieldset ${disabled ? "disabled" : ""}>
    <div class="fields">${field("Dark reference", "dark_id", references.dark_id, {values: choices("dark"), wide: true})}
    ${field("Flat reference", "flat_id", references.flat_id, {values: choices("flat"), wide: true})}
    ${num("Minimum normalized flat coverage", "flat_floor", references.flat_floor, .05, .8, .01)}</div></fieldset>
    <p class="small-copy">Calibration uses native sensor coordinates. Dark reference exposure must match the selected EL image.</p>
    <p class="form-error" hidden></p><div class="form-actions">
      ${disabled ? "" : submit("Apply references")}</div></form>`);
  wireForm(root, async (values) => { await app.patch("calibration", values, "Reference selection saved."); app.modal.close(); });
}

export function showPlacement(app, capture) {
  const current = app.case.placements[capture.id];
  const disabled = app.case.state === "issued";
  const edit = [...current.corners.map(([x, y]) => [x / capture.width, y / capture.height])];
  const labels = ["Top left", "Top right", "Bottom right", "Bottom left"];
  const fields = labels.map((label, index) => [
    num(`${label} X`, `x${index}`, current.corners[index][0], -.25 * capture.width, 1.25 * capture.width, .001),
    num(`${label} Y`, `y${index}`, current.corners[index][1], -.25 * capture.height, 1.25 * capture.height, .001),
  ].join("")).join("");
  const root = app.openDialog(`Place panel corners · ${ROLE[capture.role]}`, `<form class="section-stack">
    <p class="small-copy">Point clockwise from the camera image’s top left, or enter native pixel coordinates. Use orientation to align physical cell labels.</p>
    <div class="dialog-view"><div class="viewer-viewport">
      <img id="capture-placement-image" src="${e(app.url(`/captures/${capture.id}/preview`))}" alt="Original sensor image used to place corners">
      <canvas id="placement-canvas" aria-label="Panel corner placement" tabindex="0"></canvas>
    </div></div>
    <div id="place-next" class="small-copy"></div>
    <fieldset ${disabled ? "disabled" : ""}><div class="fields">${fields}
      ${field("Camera orientation", "orientation", current.orientation, {values: PLACEMENT_OPTIONS, wide: true})}</div></fieldset>
    <p class="form-error" hidden></p><div class="form-actions">
      ${button("placement-reset", "Start corner sequence", "", disabled)}
      ${disabled ? "" : submit("Save corner placement")}
    </div></form>`, true);
  let cursor = 0;
  const image = $("#capture-placement-image", root);
  const canvas = $("#placement-canvas", root);
  const bump = () => { $("#place-next", root).textContent = disabled ? "Issued placement" : `Next corner: ${labels[cursor]}`; };
  bump();
  const redraw = bindCanvas(canvas, image, (painter, w, h) => drawPoints(painter, w, h, edit, {close: true, numbered: true}));
  function change(index, x, y) {
    edit[index] = [x, y];
    $(`[name=x${index}]`, root).value = (x * capture.width).toFixed(3);
    $(`[name=y${index}]`, root).value = (y * capture.height).toFixed(3);
    redraw();
  }
  canvas.onclick = (event) => {
    if (disabled) return;
    const [x, y] = normalizedPoint(event, canvas);
    change(cursor, x, y);
    cursor = (cursor + 1) % 4;
    bump();
  };
  $("#placement-reset", root).onclick = () => { cursor = 0; bump(); };
  labels.forEach((_, index) => {
    const x = $(`[name=x${index}]`, root);
    const y = $(`[name=y${index}]`, root);
    function update() {
      if (!x.value || !y.value) return;
      edit[index] = [Number(x.value) / capture.width, Number(y.value) / capture.height];
      redraw();
    }
    x.oninput = update;
    y.oninput = update;
  });
  wireForm(root, async (values) => {
    const corners = labels.map((_, index) => [Number(values[`x${index}`]), Number(values[`y${index}`])]);
    await app.patch("placement", {capture: capture.id, corners, orientation: values.orientation}, "Corner placement updated.");
    app.modal.close();
  });
}

export function showPlacementReview(app, role) {
  app.showLoadDialog("Grid placement check", app.url("/placement-check", {role}), (root, data) => {
    root.innerHTML = `<img class="pair-image" src="${e(app.url("/placement-check", {role, format: "png"}))}" alt="Declared grid versus detected separator samples">
      ${metrics([
        ["Testable positions", `${data.summary.testable} / ${data.summary.positions}`],
        ["Median offset", data.summary.median_abs_offset == null ? "—" : `${format(data.summary.median_abs_offset)} px`],
        ["Positions offset", data.summary.offset], ["Frame coverage", percent(1 - data.summary.plane_outside_fraction)],
      ])}${table([["Edge", "edge"], ["Usable", "usable_fraction", percent], ["Outside px", "out_of_frame"],
        ["Clipped px", "clipped"], ["Reference gap", "calibration_gap"]], data.edges)}
        <div class="toolbar">${download(app.url("/placement-check", {role, format: "csv"}), "Separator sample CSV")}</div>`;
  }, true);
}

function statsDialog(app, capture) {
  const base = `/captures/${capture.id}`;
  app.showLoadDialog("Original image details", app.url(base + "/stats"), (root, stats) => {
    root.innerHTML = `<img class="card-image" src="${e(app.url(base + "/preview"))}" alt="${e(capture.name)} sensor preview">
      ${definition([
        ["Name", capture.name], ["Role", ROLE[capture.role]], ["Samples", `${capture.bit_depth} bit / ${capture.white_level} ceiling`],
        ["Native dimensions", `${capture.width} × ${capture.height}`], ["Exposure", `${capture.exposure_ms} ms`],
        ["Drive", capture.current_a === null ? "—" : `${capture.current_a} A`], ["Original SHA-256", capture.digest],
      ])}${metrics([["Clipped pixels", format(stats.clipped_pixels)], ["Zero codes", format(stats.zero_pixels)],
        ["Median native level", format(stats.signal.median)], ["Local contrast P90", format(stats.local_contrast.p90, 5)]])}
      ${histogram(stats.histogram, "Native sensor signal histogram")}
      <div class="toolbar">${download(app.url(base + "/raw"), "Download original")}
      ${button("capture-metadata-edit", "Acquisition metadata", "", app.case.state === "issued")}
      ${button("capture-detach", "Detach original", "danger", app.case.state === "issued")}</div>`;
    $("#capture-metadata-edit", root).onclick = () => captureMetadataDialog(app, capture);
    $("#capture-detach", root).onclick = () => detachDialog(app, capture);
  }, true);
}

function captureMetadataDialog(app, frame) {
  const root = app.openDialog("Acquisition metadata", `<form><div class="fields">
    ${num("Exposure (ms)", "exposure_ms", frame.exposure_ms, .01, 600000, .01)}
    ${num("Sensor white level", "white_level", frame.white_level, 16, (2**frame.bit_depth) - 1)}
    ${REF_ROLES.includes(frame.role) ? "" : num("Drive current (A)", "current_a", frame.current_a, .001, 100, .001, false)}
    </div><p class="form-error" hidden></p><div class="form-actions">${submit("Apply capture metadata")}</div></form>`);
  wireForm(root, async (values) => {
    await app.patch("capture-metadata", {...values, capture: frame.id}, "Acquisition metadata saved.");
    app.modal.close();
  });
}

function detachDialog(app, frame) {
  const root = app.openDialog("Detach capture", `<form><p class="small-copy">Detach ${e(frame.name)} from this inspection?</p>
    <p class="form-error" hidden></p><div class="form-actions"><button type="submit" class="danger">Detach original</button></div></form>`);
  wireForm(root, async () => {
    const saved = await request(app.url(`/captures/${frame.id}`), "DELETE", {revision: app.case.revision});
    app.modal.close();
    await app.update(saved, "Capture detached.");
  });
}

function bandingDialog(app, frame) {
  const root = app.openDialog("Reference banding", `<div class="fields">${field("Sensor direction", "axis", "rows", {values: SENSOR_AXIS})}</div>
    <div id="banding-details" class="section-stack"></div>`, true);
  const selector = $('select[name="axis"]', root);
  let loading = 0;
  async function refresh() {
    const latest = ++loading;
    const details = $("#banding-details", root);
    const base = `/captures/${frame.id}/reference/${selector.value}`;
    details.innerHTML = '<p class="small-copy">Reading reference…</p>';
    try {
      const model = await request(app.url(base + "/data"));
      if (latest !== loading || !root.isConnected) return;
      details.innerHTML = `<img class="pair-image" src="${e(app.url(base + "/plot"))}" alt="Native reference signal and its smooth trend">
      ${metrics([["Usable lines", format(model.summary.usable_lines)], ["Reference lines", format(model.summary.all_lines)],
        ["Outlying intervals", model.summary.outlying_runs.length], ["Periodic peaks", model.summary.periodic_peaks.length]])}
      <img class="card-image" src="${e(app.url(base + "/image"))}" alt="Reference image with banding markers">`;
    } catch (error) {
      if (details.isConnected) details.innerHTML = `<p class="error">${e(error.message)}</p>`;
    }
  }
  selector.onchange = refresh;
  refresh();
}

function referenceComparisonDialog(app) {
  const frames = Object.values(app.case.captures).filter((frame) => REF_ROLES.includes(frame.role));
  const options = frames.map((frame) => [frame.id, frame.name]);
  const root = app.openDialog("Reference repeatability", `<form class="section-stack"><div class="fields">
    ${field("Earlier original", "before", frames[0]?.id, {values: options})}
    ${field("Later original", "after", frames[1]?.id, {values: options})}
    ${num("Diagnostic tiles per axis", "tiles", 6, 2, 12)}</div>
    <p class="form-error" hidden></p><div class="form-actions">${submit("Compare references")}</div></form>
    <div id="reference-result" class="section-stack"></div>`, true);
  wireForm(root, async (values) => {
    const detail = await request(app.url("/reference-comparison/data", values));
    $("#reference-result", root).innerHTML = `<img class="pair-image" src="${e(app.url("/reference-comparison/image", values))}" alt="Matched previews and drift map">
      ${metrics([["Shared pixels", format(detail.summary.common_pixels)], ["Lost pixels", format(detail.summary.lost_pixels)],
        ["Gained pixels", format(detail.summary.gained_pixels)], ["Median drift", format(detail.summary.median_drift, 5)]])}
      <div class="toolbar">${download(app.url("/reference-comparison/csv", values), "Tile measurements CSV")}</div>
      ${table([["Tile", "region"], ["Common", "common"], ["Gained", "gained"], ["Lost", "lost"],
        ["Median change", "median_delta", (value) => format(value, 6)]], detail.tiles)}`;
  });
}
