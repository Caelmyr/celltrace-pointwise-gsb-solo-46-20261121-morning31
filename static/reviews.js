import {
  $, $$, ROLE, badge, button, changeLabel, dateLabel, definition, download, e, field, format,
  itemCard, metrics, percent, request, submit, table, wireForm,
} from "./ui.js";
import {renderComparisonPanel} from "./baselines.js";

const CATEGORIES = [["dark", "Dark area"], ["crack", "Crack"], ["suspect", "Needs review"], ["capture", "Capture artifact"], ["general", "General"]];
const STATES = [["open", "Open"], ["hold", "Hold"], ["confirmed", "Confirmed"], ["cleared", "Cleared"]];
const DECISIONS = [["release", "Release"], ["hold", "Hold"], ["reinspect", "Reinspect"]];

export function renderFindings(app, root) {
  const values = app.case.findings;
  const issued = app.case.state === "issued";
  const rows = values.length ? `<div class="table-wrap"><table class="finding-table"><thead><tr>
    <th scope="col">Finding</th><th scope="col">Cell</th><th scope="col">Review</th><th scope="col">Evidence</th><th scope="col">Actions</th></tr></thead>
    <tbody>${values.map((item) => `<tr><td><strong>${e(item.title)}</strong><br>
      <small>${e(CATEGORIES.find(([key]) => key === item.category)?.[1] ?? item.category)} · ${ROLE[item.source_role]}</small></td>
      <td class="num">${e(item.cell || "—")}</td>
      <td><span class="finding-status">${e(item.status)}</span></td>
      <td>${badge(changeLabel(item.source_status), item.source_status === "current" ? "" : "training")}</td>
      <td><div class="toolbar"><button type="button" data-evidence="${item.id}" class="compact">Show evidence</button>
      ${issued ? "" : `<button type="button" data-edit-finding="${item.id}" class="compact">Edit</button>`}</div></td></tr>`).join("")}
    </tbody></table></div>` : '<p class="empty-line">No findings recorded for this inspection.</p>';
  root.innerHTML = `<section class="card"><div class="card-header"><h2>Recorded image findings</h2>
    ${button("draw-new-finding", "Draw finding on image", "primary", issued || !["high", "low"].some((role) => app.case.active[role]))}</div>
    ${rows}</section>`;
  $("#draw-new-finding", root).onclick = () => {
    Object.assign(app.workspace, {mode: "polygon", points: [], measure: null});
    app.showTab("bench");
  };
  $$("[data-evidence]", root).forEach((node) => { node.onclick = () => showFindingEvidence(app, node.dataset.evidence); });
  $$("[data-edit-finding]", root).forEach((node) => {
    node.onclick = () => showFindingForm(app, values.find((item) => item.id === node.dataset.editFinding));
  });
}

export function showFindingForm(app, value) {
  const finding = value.id != null;
  const role = ROLE[value.source_role];
  const title = finding ? "Edit recorded finding" : "Record image finding";
  const root = app.openDialog(title, `<form><div class="fields">
    ${field("Finding title", "title", value.title, {required: true, wide: true, max: 100})}
    ${field("Category", "category", value.category, {values: CATEGORIES})}
    ${field("Review state", "status", value.status, {values: STATES})}
    ${field("Cell ID (optional)", "cell", value.cell, {max: 6})}
    ${field("Observation", "note", value.note, {multiline: true, wide: true, max: 1200})}
    </div><p class="small-copy">${e(role)} · ${value.points.length} region vertices</p>
    ${value.source_status === "source changed" ? '<p class="warning">The original image evidence is available from the finding list. Redraw the region to bind a fresh source.</p>' : ""}
    <p class="form-error" hidden></p><div class="form-actions">
    ${finding ? button("withdraw-finding", "Withdraw finding", "danger") : ""}${submit(finding ? "Save review" : "Save finding")}
    </div></form>`);
  $("#withdraw-finding", root)?.addEventListener("click", () => withdrawDialog(app, value));
  wireForm(root, async (values) => {
    const payload = {...values, source_role: value.source_role, points: value.points, revision: app.case.revision};
    const updated = await request(app.url(finding ? `/findings/${value.id}` : "/findings"), finding ? "PUT" : "POST", payload);
    app.workspace.points = [];
    app.workspace.measure = null;
    app.modal.close();
    await app.update(updated, finding ? "Finding review saved." : "Image finding saved.");
  });
}

function withdrawDialog(app, value) {
  const root = app.openDialog("Withdraw finding", `<form><p class="small-copy">Withdraw ${e(value.title)}? The withdrawal will appear in inspection history.</p>
    <p class="form-error" hidden></p><div class="form-actions"><button type="submit" class="danger">Withdraw finding</button></div></form>`);
  wireForm(root, async () => {
    const updated = await request(app.url(`/findings/${value.id}`), "DELETE", {revision: app.case.revision});
    app.modal.close();
    await app.update(updated, "Finding withdrawn.");
  });
}

function showFindingEvidence(app, id) {
  const path = `/findings/${id}`;
  app.showLoadDialog("Original finding evidence", app.url(path + "/data"), (root, item) => {
    root.innerHTML = `<div class="toolbar">${badge(changeLabel(item.lineage.state), item.lineage.state === "current" ? "" : "training")}</div>
      <div class="fields">
      <div><h3>Rectified evidence</h3><img class="card-image" src="${e(app.url(path + "/plane"))}" alt="Saved region on the rectified plane"></div>
      ${item.source_box ? `<div><h3>Native original crop</h3><img class="card-image" src="${e(app.url(path + "/source"))}" alt="Source pixels beneath the finding"></div>` : ""}
      </div>${definition([
        ["Observation", item.finding.title], ["Original", item.capture.name],
        ["Original digest", item.capture.digest], ["Support", percent(item.measurement.usable_fraction)],
        ["Median", format(item.measurement.signal.median, 5)], ["Area mm²", format(item.measurement.area_mm2)],
      ])}<div class="toolbar">${download(app.url(path + "/packet"), "Finding evidence ZIP")}</div>`;
  }, true);
}

export async function renderReport(app, root) {
  const viewAt = app.renderVersion;
  const item = app.case;
  if (item.issue) {
    root.innerHTML = `<div class="page-stack">
      ${itemCard("Issued inspection report", `<div class="section-stack">
         ${definition([
          ["Record", item.issue.id], ["Disposition", changeLabel(item.issue.disposition)],
          ["Issued", dateLabel(item.issue.at)], ["Source revision", item.issue.review_revision],
          ["Decision note", item.issue.decision_note || "—"],
        ])}
        <img class="card-image" src="${e(app.url("/report/cover"))}" alt="Archived image evidence with finding outlines">
        <div class="toolbar"><a class="button primary" href="${e(app.url("/report/html"))}" target="_blank" rel="noopener">Open printable report</a>
          ${download(app.url("/report/archive"), "Issued evidence ZIP")}
          ${download(app.url("/export/exchange"), "Working exchange ZIP")}</div></div>`)}
    </div>`;
    return;
  }
  root.innerHTML = itemCard("Report preparation", '<p class="small-copy">Assembling image findings…</p>');
  try {
    const report = await request(app.url("/report"));
    if (app.renderVersion !== viewAt) return;
    const sectionRows = Object.entries(report.sections).map(([key, section]) => ({
      analysis: changeLabel(key), rows: section.records,
    }));
    const messages = [...report.blockers, ...report.messages].map((message) => `<p class="warning">${e(message)}</p>`).join("");
    root.innerHTML = `<div class="page-stack">
      ${itemCard("Report preparation", `<div class="section-stack">${metrics([
        ["Selected originals", Object.keys(report.capture_inventory).length], ["Recorded findings", report.findings.length],
        ["Image measurements", sectionRows.length], ["Review revision", report.review_revision],
      ])}${messages}
      <div class="toolbar">
      ${button("issue-review-report", "Issue inspection report", "primary", report.blockers.length > 0)}
      ${download(app.url("/export/exchange"), "Working exchange ZIP")}
      </div></div>`)}
      <section class="card"><div class="card-header"><h2>Evidence to include</h2></div>
       ${table([["Measurement", "analysis"], ["Rows", "rows"]], sectionRows)}</section></div>`;
    $("#issue-review-report", root).onclick = () => issueDialog(app);
  } catch (error) {
    if (app.renderVersion !== viewAt) return;
    root.innerHTML = itemCard("Report preparation", `<p class="error">${e(error.message)}</p>`);
  }
}

function issueDialog(app) {
  const outstanding = app.case.findings.some((finding) => ["open", "hold"].includes(finding.status));
  const root = app.openDialog("Issue inspection report", `<form><div class="fields">
    ${field("Disposition", "disposition", outstanding ? "hold" : "release", {values: DECISIONS})}
    ${field("Decision note", "decision_note", "", {multiline: true, wide: true, max: 1400})}
    </div><p class="small-copy">Issuing captures the current measured evidence. Continue editing from a working copy after issue.</p>
    <p class="form-error" hidden></p><div class="form-actions">${submit("Issue report")}</div></form>`);
  wireForm(root, async (values) => {
    const saved = await request(app.url("/report"), "POST", {...values, revision: app.case.revision});
    app.modal.close();
    await app.update(saved, "Inspection report issued.");
  });
}

export function showHistory(app) {
  app.showLoadDialog("Inspection history", app.url("/events"), (root, value) => {
    root.innerHTML = table([
      ["Revision", "revision"], ["Time", "at", dateLabel], ["Event", "action", changeLabel],
    ], value.events);
    const entries = value.events.filter((entry) => Object.keys(entry.details).length);
    if (entries.length) {
      root.insertAdjacentHTML("beforeend", `<details><summary>Event details</summary><div class="list">${entries.map((entry) => `
        <div class="list-item"><div><strong>Revision ${entry.revision} · ${e(entry.action)}</strong>
        <small class="mono">${e(JSON.stringify(entry.details))}</small></div></div>`
      ).join("")}</div></details>`);
    }
  });
}

export async function renderLot(app, root) {
  const lot = app.case.lot;
  if (!lot) {
    root.innerHTML = itemCard("Lot review", '<p class="small-copy">Assign a production lot to open the lot contact sheet.</p>');
    return;
  }
  const base = (key) => `/api/lots/${key}?lot=${encodeURIComponent(lot)}`;
  root.innerHTML = itemCard("Lot review", '<p class="small-copy">Collecting image summaries…</p>');
  const viewAt = app.renderVersion;
  try {
    const values = await request(base("summary"));
    if (app.renderVersion !== viewAt) return;
    const flagged = values.unavailable.map((item) => `<p class="warning">${e(item.module)} · ${e(item.reason)}</p>`).join("");
    const records = table([
      ["Module", "module"], ["State", "state", changeLabel], ["Source", "measurement"], ["Comparable group", "pool"],
      ["Median signal", "median"], ["Usable cells", "usable_cells"], ["Dark regions", "dark_count"], ["Cracks", "crack_count"],
    ], values.rows, (row) => `<button data-case-jump="${row.id}" type="button" class="compact">Open</button>`);
    root.innerHTML = `<div class="page-stack">${itemCard(`Production lot ${lot}`, `<div class="section-stack">
      ${metrics([["Inspections shown", values.shown], ["With usable sources", values.ready], ["Capture-setting groups", values.pools.length]])}
      ${values.limited ? '<p class="info">Showing the 12 most recently edited inspections.</p>' : ""}${flagged}
      <div class="toolbar">${button("view-contact-sheet", "View contact sheet", "primary")}
      ${download(base("sheet"), "Contact sheet PNG")}${download(base("csv"), "Inspection CSV")}
      ${download(base("cells"), "Cell measurements CSV")}</div></div>`)}
      <section class="card"><div class="card-header"><h2>Inspection measurements</h2></div>${records}</section>
      <section class="card"><div class="card-header"><h2>Acquisition groups</h2></div>
      ${table([["Group", "id"], ["Inspections", "count"], ["Grid", "grid", (x) => x.join(" × ")],
        ["Samples", "bit_depth"], ["Current A", "current_a"], ["Exposure ms", "exposure_ms"]],
      values.pools)}</section></div>`;
    $("#view-contact-sheet", root).onclick = () => {
      request(base("sheet"), "GET", null, true).then((raw) => {
        const ref = URL.createObjectURL(raw);
        app.openDialog("Lot contact sheet", `<img class="pair-image" src="${ref}" alt="Panel image contact sheet for ${e(lot)}">`, true);
        app.modal.addEventListener("close", () => URL.revokeObjectURL(ref), {once: true});
      }).catch(app.fail);
    };
    $$("[data-case-jump]", root).forEach((entry) => {
      entry.onclick = () => { app.tab = "bench"; app.loadCase(entry.dataset.caseJump, true).catch(app.fail); };
    });
    const compareHost = document.createElement("div");
    root.appendChild(compareHost);
    renderComparisonPanel(app, compareHost).catch(app.fail);
  } catch (error) {
    if (app.renderVersion === viewAt) root.innerHTML = itemCard("Lot review", `<p class="error">${e(error.message)}</p>`);
  }
}
