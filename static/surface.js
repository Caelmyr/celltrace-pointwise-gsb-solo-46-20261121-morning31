import {
  $, $$, ROLE, badge, bindCanvas, button, definition, download, drawPoints, e, field, format,
  itemCard, metrics, normalizedPoint, num, percent, request, submit, table, wireForm,
} from "./ui.js";

const VERDICT_STYLE = {
  occluded: {label: "Occluded by surface mark", chip: ""},
  partial: {label: "Partially covered", chip: "neutral"},
  "edge-uncertain": {label: "Edge alignment uncertain", chip: "training"},
  "no-surface-evidence": {label: "No surface mark evidence", chip: "neutral"},
  "obscured-in-surface": {label: "Obscured in surface photo", chip: "training"},
};
const VERDICT_ORDER = ["occluded", "partial", "edge-uncertain", "obscured-in-surface", "no-surface-evidence"];
const MARK_KINDS = [["smudge", "Smudge"], ["streak", "Streak"], ["film", "Film / haze"], ["other", "Other"]];
const EL_ROLES = [["high", "High current"], ["low", "Low current"]];

export function renderSurface(app, root) {
  const item = app.case;
  const surface = Object.values(item.captures).find((frame) => frame.role === "surface");
  if (!surface) {
    root.innerHTML = itemCard("Surface cross-check", `<p class="small-copy">
        Upload a grayscale surface photo to cross-check the EL dark regions against visible contamination.</p>
      <div class="form-actions start">${button("surface-upload", "Upload surface photo", "primary",
      item.state === "issued")}</div>`);
    $("#surface-upload", root).onclick = () => app.showTab("captures");
    return;
  }
  const review = item.surface_review;
  const registered = review.surface_id === surface.id;
  const placement = item.placements[surface.id];
  const ctx = app.workspace;
  if (!ctx.surface) {
    ctx.surface = {mode: "view", points: [], role: "high", mark: null};
  }
  ctx.surface.role = ["high", "low"].includes(ctx.surface.role) ? ctx.surface.role : "high";
  const closed = item.state === "issued";
  const roleButtons = EL_ROLES.map(([key, label]) =>
    `<button type="button" data-el-role="${key}" aria-pressed="${ctx.surface.role === key}">${label}</button>`).join("");
  root.innerHTML = `<div class="page-stack">
    ${itemCard("Surface photo cross-check", `<div class="section-stack">
      <p class="small-copy">Mark visible contamination on the registered surface photo. Each EL dark region keeps its
        own identity; marks only state how much surface evidence supports it — partial cover and registration
        uncertainty are recorded instead of rewriting the dark region.</p>
      <div class="toolbar">
        ${button("surface-placement", placement ? "Edit panel corners" : "Register panel corners", "", closed)}
        ${button("surface-settings", "Cross-check settings", "compact", closed)}
      </div>
      ${definition([
        ["Surface photo", surface.name],
        ["Native size", `${surface.width} × ${surface.height} px`],
        ["Panel corners", placement ? "Registered" : "Not registered — using full frame"],
        ["Stored marks", review.marks.length],
      ])}
      ${registered ? "" : '<p class="warning">The stored review is bound to a different surface photo. Re-bind it in cross-check settings.</p>'}
    </div>`)}
    <section class="card"><div class="card-header"><h2>Surface evidence on the panel plane</h2>
      <div class="toolbar">${roleButtons}
        ${download(app.url("/surface/surface", {role: ctx.surface.role, download: 1}), "Marked surface PNG")}
        ${download(app.url("/surface/native", {role: ctx.surface.role, download: 1}), "Marked original PNG")}
      </div></div>
      <div class="card-pad section-stack">
        <div class="surface-viewer viewer-well">
          <div class="viewer-viewport">
            <img id="surface-plane-image" width="${item.plane.width}" height="${item.plane.height}"
              alt="Rectified surface photo with contamination marks"
              src="${e(app.url("/surface/surface", {role: ctx.surface.role, revision: item.revision}))}">
            <canvas id="surface-canvas" tabindex="0" aria-label="Draw a contamination mark on the surface plane"></canvas>
          </div>
        </div>
        <p class="tool-status" id="surface-message"></p>
        <div class="toolbar" id="surface-tools"></div>
      </div>
    </section>
    <section class="card"><div class="card-header"><h2>EL dark regions versus surface evidence</h2>
      <div class="toolbar">
        ${download(app.url("/surface/el", {role: ctx.surface.role, download: 1}), "Marked EL PNG")}
        ${download(app.url("/surface/csv", {role: ctx.surface.role}), "Cross-check CSV")}
      </div></div>
      <div class="card-pad section-stack" id="surface-results"><p class="small-copy">Loading cross-check…</p></div>
    </section>
  </div>`;
  $$("[data-el-role]", root).forEach((node) => {
    node.onclick = () => { ctx.surface.role = node.dataset.elRole; app.render(); };
  });
  $("#surface-placement", root).onclick = () => import("./captures.js").then((m) => m.showPlacement(app, surface));
  $("#surface-settings", root).onclick = () => settingsDialog(app, surface);
  const image = $("#surface-plane-image", root);
  const canvas = $("#surface-canvas", root);
  const redraw = bindCanvas(canvas, image, (painter, width, height) => {
    drawPoints(painter, width, height, ctx.surface.points,
      {close: true, numbered: true, color: "#f7a454"});
  });
  canvas.addEventListener("click", (event) => {
    if (closed || ctx.surface.mode === "view") return;
    const point = normalizedPoint(event, canvas);
    if (ctx.surface.points.length >= 48) return app.notify("A mark supports up to 48 vertices.", true);
    ctx.surface.points.push(point);
    redraw();
    paintTools(app, root, redraw);
  });
  paintTools(app, root, redraw);
  loadResults(app, root);
}

function paintTools(app, root, redraw) {
  const ctx = app.workspace.surface;
  const tools = $("#surface-tools", root);
  const message = $("#surface-message", root);
  const closed = app.case.state === "issued";
  if (ctx.mode === "view") {
    message.textContent = "Review the stored marks, or draw a new contamination outline.";
    tools.innerHTML = `${button("surface-draw", "Draw contamination mark", "primary", closed)}
      ${storedMarkButtons(app)}`;
    $("#surface-draw", tools)?.addEventListener("click", () => {
      ctx.mode = "draw"; ctx.points = []; app.render();
    });
    $$("[data-edit-mark]", tools).forEach((node) => {
      node.onclick = () => beginEdit(app, node.dataset.editMark);
    });
    return;
  }
  const editing = ctx.mark != null;
  message.textContent = `Click around the contamination outline — ${ctx.points.length} vertices. `
    + "A mark may cross cells and touch several dark regions.";
  tools.innerHTML = `<form class="toolbar">
    ${ctx.points.length >= 3 ? submit(editing ? "Save mark changes" : "Save mark") : button("surface-pending", "Save mark", "primary", true)}
    ${button("surface-cancel", "Cancel", "compact")}
    ${button("surface-undo", "Remove last", "compact", !ctx.points.length)}
    ${button("surface-clear", "Clear", "compact", !ctx.points.length)}</form>`;
  $("#surface-cancel", tools).onclick = () => { ctx.mode = "view"; ctx.points = []; ctx.mark = null; app.render(); };
  $("#surface-undo", tools).onclick = () => { ctx.points.pop(); redraw(); paintTools(app, root, redraw); };
  $("#surface-clear", tools).onclick = () => { ctx.points = []; redraw(); paintTools(app, root, redraw); };
  wireForm(tools, async () => {
    const mark = app.case.surface_review.marks.find((entry) => entry.id === ctx.mark);
    const base = {
      title: mark?.title || "Surface contamination",
      kind: mark?.kind || "smudge",
      note: mark?.note || "",
      points: ctx.points.map(([x, y]) => [Number(x.toFixed(5)), Number(y.toFixed(5))]),
    };
    await markFormDialog(app, base, editing);
    ctx.mode = "view"; ctx.points = []; ctx.mark = null;
  });
}

function storedMarkButtons(app) {
  const marks = app.case.surface_review.marks;
  if (!marks.length) return "";
  return marks.map((mark) =>
    `<button type="button" class="compact" data-edit-mark="${e(mark.id)}">Edit · ${e(mark.title)}</button>`).join("");
}

function beginEdit(app, markId) {
  const mark = app.case.surface_review.marks.find((entry) => entry.id === markId);
  const ctx = app.workspace.surface;
  ctx.mode = "draw";
  ctx.mark = markId;
  ctx.points = mark.points.map((point) => [...point]);
  app.render();
}

async function markFormDialog(app, base, editing) {
  const root = app.openDialog(editing ? "Edit contamination mark" : "Record contamination mark",
    `<form><div class="fields">
      ${field("Mark title", "title", base.title, {required: true, wide: true, max: 100})}
      ${field("Contamination kind", "kind", base.kind, {values: MARK_KINDS})}
      ${field("Observation note", "note", base.note, {multiline: true, wide: true, max: 1200})}
    </div><p class="small-copy">${base.points.length} vertices in plane coordinates.</p>
    <p class="form-error" hidden></p><div class="form-actions">${submit("Save mark")}</div></form>`);
  const ctx = app.workspace.surface;
  wireForm(root, async (values) => {
    const payload = {...values, points: base.points, revision: app.case.revision};
    const path = editing ? `/surface-mark-edit` : "/surface-mark-add";
    if (editing) payload.mark = ctx.mark;
    const saved = await request(app.url(path), "PATCH", payload);
    app.modal.close();
    await app.update(saved, editing ? "Surface mark updated." : "Surface mark recorded.");
  });
}

function settingsDialog(app, surface) {
  const review = app.case.surface_review;
  const root = app.openDialog("Surface cross-check settings", `<form><div class="fields">
    ${field("Surface photo", "surface_id", review.surface_id || surface.id,
      {values: Object.values(app.case.captures).filter((frame) => frame.role === "surface")
        .map((frame) => [frame.id, frame.name]), wide: true})}
    ${num("Registration edge tolerance (px)", "edge_tolerance_px", review.edge_tolerance_px, 0, 40)}
    ${field("Review note", "note", review.note, {multiline: true, wide: true, max: 2000})}
  </div><p class="small-copy">Overlap inside the tolerance rim is held as edge-uncertain instead of treated as
    confirmed cover. The note and verdicts are stored with the inspection.</p>
    <p class="form-error" hidden></p><div class="form-actions">${submit("Save settings")}</div></form>`);
  wireForm(root, async (values) => {
    const saved = await request(app.url("/surface-config"), "PATCH", {...values, revision: app.case.revision});
    app.modal.close();
    await app.update(saved, "Surface cross-check settings saved.");
  });
}

async function loadResults(app, root) {
  const ctx = app.workspace.surface;
  const host = $("#surface-results", root);
  const version = app.renderVersion;
  try {
    const data = await request(app.url("/surface/data", {role: ctx.role}));
    if (app.renderVersion !== version || !host.isConnected) return;
    const counts = VERDICT_ORDER.map((key) => [VERDICT_STYLE[key].label, data.counts[key] ?? 0]);
    host.innerHTML = metrics([
      ["Surface coverage", percent(data.surface_coverage)],
      ["Glare pixels", format(data.glare_pixels, 0)],
      ["Contamination marks", data.marks.length],
      ...counts,
    ]) + verdictTable(app, data);
    $$("[data-verdict-region]", host).forEach((node) => {
      node.onclick = () => verdictDialog(app, node.dataset.verdictRegion, node.dataset.current);
    });
  } catch (error) {
    if (host.isConnected) host.innerHTML = `<p class="error">${e(error.message)}</p>`;
  }
}

function verdictTable(app, data) {
  const rows = [...data.rows].sort((a, b) =>
    VERDICT_ORDER.indexOf(a.verdict) - VERDICT_ORDER.indexOf(b.verdict) || b.dark_pixels - a.dark_pixels);
  const body = rows.map((row) => {
    const style = VERDICT_STYLE[row.verdict];
    return `<tr>
      <td><strong>${e(row.dark_region)}</strong><br><small>${e(row.cell)}</small></td>
      <td class="num">${format(row.dark_pixels, 0)}</td>
      <td>${percent(row.cover_fraction)}</td>
      <td>${percent(row.rim_fraction)}</td>
      <td>${percent(row.support_fraction)}</td>
      <td>${badge(style.label, style.chip)}${row.manual_override ? badge("Manual", "training") : ""}</td>
      <td>${app.case.state === "issued" ? "" :
        `<button type="button" class="compact" data-verdict-region="${e(row.dark_region)}"
          data-current="${e(row.verdict)}">Review verdict</button>`}</td>
    </tr>`;
  }).join("");
  return `<div class="table-wrap"><table><thead><tr>
    <th scope="col">Dark region</th><th scope="col">Pixels</th><th scope="col">Mark cover</th>
    <th scope="col">Edge rim</th><th scope="col">Surface support</th><th scope="col">Verdict</th>
    <th scope="col"><span class="muted">Override</span></th></tr></thead><tbody>${body}</tbody></table></div>`;
}

function verdictDialog(app, region, current) {
  const options = [["auto", "Automatic (clear override)"], ...VERDICT_ORDER.map((key) => [key, VERDICT_STYLE[key].label])];
  const root = app.openDialog(`Override verdict · ${region}`, `<form><div class="fields">
    ${field("Verdict", "verdict", current, {values: options, wide: true})}
  </div><p class="small-copy">The automatic suggestion stays visible in the table. Overrides do not alter the
    measured dark region.</p><p class="form-error" hidden></p>
    <div class="form-actions">${submit("Save verdict")}</div></form>`);
  wireForm(root, async (values) => {
    const saved = await request(app.url("/surface-verdict"), "PATCH",
      {revision: app.case.revision, dark_region: region, verdict: values.verdict});
    app.modal.close();
    await app.update(saved, "Surface verdict saved.");
  });
}
