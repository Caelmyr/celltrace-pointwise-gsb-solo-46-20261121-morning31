import {
  $, $$, badge, button, changeLabel, dateLabel, definition, download, e, field, format,
  itemCard, metrics, num, percent, request, submit, table, wireForm,
} from "./ui.js";

const EVIDENCE_LABEL = {firm: "Firm evidence", limited: "Limited evidence", unsupported: "No reference data"};
const STATUS_LABEL = {
  "in-family": "In family",
  "out-of-family": "Out of family",
  "limited-ok": "Limited evidence",
  "no-evidence": "No reference data",
  "not-measured": "Not measured",
};

export async function renderBaselines(app, root) {
  root.innerHTML = itemCard("Recipe brightness baselines", '<p class="small-copy">Loading reference library…</p>');
  try {
    const [recipeList, baselineList, inspections] = await Promise.all([
      request("/api/baselines/recipes"),
      request("/api/baselines?retired=1"),
      request("/api/inspections"),
    ]);
    drawLibrary(app, root, recipeList.recipes, baselineList, inspections);
  } catch (error) {
    root.innerHTML = itemCard("Recipe brightness baselines", `<p class="error">${e(error.message)}</p>`);
  }
}

function drawLibrary(app, root, recipes, baselines, inspections) {
  const issued = inspections.filter((item) => item.state === "issued" && item.active_roles.includes("high"));
  const grouped = new Map();
  for (const baseline of baselines) {
    if (!grouped.has(baseline.recipe)) grouped.set(baseline.recipe, []);
    grouped.get(baseline.recipe).push(baseline);
  }
  const recipeCards = recipes.length
    ? recipes.map((entry) => {
        const versions = grouped.get(entry.recipe) || [];
        return `<article class="card baseline-recipe">
          <div class="card-header"><h2>${e(entry.recipe)}</h2>
            <span class="chip">${entry.active_versions} active · v${entry.latest_version}</span></div>
          <div class="card-pad section-stack">
            <div class="table-wrap"><table><tbody>${versions.map((baseline) => `
              <tr><td><strong>v${baseline.version} · ${e(baseline.name)}</strong><br>
                <small>${dateLabel(baseline.updated_at)} · ${baseline.sample_count} reference panels ·
                grid ${baseline.grid.join(" × ")} · digest <span class="mono">${e((baseline.digest || "").slice(0, 12))}</span></small></td>
              <td>${badge(changeLabel(baseline.state), baseline.state === "active" ? "" : "neutral")}</td>
              <td><div class="toolbar">
                <button type="button" class="compact" data-open-baseline="${e(baseline.id)}">Open</button>
                ${download(`/api/baselines/${baseline.id}/image`, "Map PNG")}
              </div></td></tr>`).join("")}</tbody></table></div>
          </div></article>`;
      }).join("")
    : '<p class="empty-line">No brightness baselines yet. Build one from issued, confirmed-passing inspections.</p>';
  root.innerHTML = `<div class="page-stack">
    ${itemCard("Recipe brightness baselines", `<div class="toolbar">
      ${button("new-baseline", "Build baseline", "primary", issued.length < 2)}
      </div><p class="small-copy">A baseline stores the normal per-cell brightness shape of a recipe.
      Only issued inspections with a high-current capture can be used as reference panels.
      Positions seen by fewer than three panels are marked as limited evidence.</p>`)}
    ${issued.length < 2 ? `<p class="warning">At least two issued inspections with high-current captures are needed; ${issued.length} available.</p>` : ""}
    ${recipeCards}
  </div>`;
  $("#new-baseline", root)?.addEventListener("click", () => buildDialog(app, issued));
  $$("[data-open-baseline]", root).forEach((node) => {
    node.onclick = () => openBaseline(app, node.dataset.openBaseline);
  });
}

function buildDialog(app, issued) {
  const checks = issued.map((item) => `
    <label class="check-row"><input type="checkbox" name="pick" value="${e(item.id)}">
      <span><strong>${e(item.module)}</strong><small>${e(item.lot || "unassigned lot")} · rev ${item.revision} ·
      ${item.image_count} images</small></span></label>`).join("");
  const root = app.openDialog("Build a recipe brightness baseline", `<form><div class="fields">
    ${field("Recipe name", "recipe", app.case?.lot || "", {required: true, max: 80, wide: true})}
    ${field("Baseline display name", "name", "Brightness reference", {required: true, max: 100, wide: true})}
    ${num("Position z-score limit", "z_limit", 3.0, 1, 8, 0.1)}
    ${num("Position low-ratio floor", "low_ratio", 0.82, 0.3, 1, 0.01)}
    ${num("Whole-panel dim ratio", "global_dim_ratio", 0.85, 0.3, 1, 0.01)}
    ${field("Notes", "note", "", {multiline: true, wide: true, max: 2000})}
    </div>
    <div class="pick-list">${checks || '<p class="small-copy">No eligible issued inspections.</p>'}</div>
    <p class="small-copy">Select at least two panels sharing the same cell grid. The same recipe name creates a new immutable version.</p>
    <p class="form-error" hidden></p><div class="form-actions">${submit("Publish baseline")}</div></form>`, true);
  wireForm(root, async (values) => {
    const inspection_ids = $$("input[name=pick]:checked", root).map((node) => node.value);
    const result = await request("/api/baselines", "POST", {
      recipe: values.recipe, name: values.name, note: values.note, inspection_ids,
      z_limit: Number(values.z_limit), low_ratio: Number(values.low_ratio),
      global_dim_ratio: Number(values.global_dim_ratio),
    });
    app.modal.close();
    app.notify(`Baseline v${result.version} published from ${result.sample_count} panels.`);
    app.render();
    openBaseline(app, result.id);
  });
}

async function openBaseline(app, identifier) {
  const root = app.openDialog("Brightness baseline", '<p class="small-copy">Loading baseline…</p>', true);
  try {
    const baseline = await request(`/api/baselines/${identifier}`);
    const summary = baseline.summary;
    root.innerHTML = `<div class="baseline-detail">
      <div class="card"><div class="card-header"><h2>${e(baseline.name)} · v${baseline.version}</h2>
        ${badge(changeLabel(baseline.state), baseline.state === "active" ? "" : "neutral")}</div>
        <div class="card-pad section-stack">
          ${metrics([
            ["Recipe", baseline.recipe], ["Reference panels", baseline.sources.length],
            ["Firm positions", summary.firm_cells], ["Limited positions", summary.limited_cells],
            ["No-data positions", summary.unsupported_cells], ["Grid", baseline.grid.join(" × ")],
          ])}
          ${definition([
            ["Created", dateLabel(baseline.created_at)], ["Updated", dateLabel(baseline.updated_at)],
            ["Content digest", (baseline.digest || "").slice(0, 24) + "…"],
            ["Position z limit", baseline.z_limit], ["Low-ratio floor", baseline.low_ratio],
            ["Global dim ratio", baseline.global_dim_ratio], ["Note", baseline.note || "—"],
          ])}
          <details open><summary>Acquisition settings kept with this reference</summary>
            ${definition(acquisitionRows(baseline.acquisition))}</details>
          <details><summary>Reference panels (${baseline.sources.length})</summary>
            ${table([
              ["Module", "module"], ["Lot", "lot"], ["Issued", "issued_at", dateLabel],
              ["Current A", "current_a"], ["Exposure ms", "exposure_ms"], ["Panel median", "panel_median"],
              ["Cells", "eligible_cells"],
            ], baseline.sources.map((s) => ({...s})))}</details>
          <details><summary>Position statistics</summary>
            ${table([
              ["Cell", "cell"], ["Evidence", "evidence", (v) => EVIDENCE_LABEL[v] || v],
              ["Panels n", "samples"], ["Relative median", "relative_median"], ["Relative MAD", "relative_mad"],
              ["Absolute median", "absolute_median"], ["Contributors", "source_modules", (v) => v.join(", ")],
            ], baseline.cells)}</details>
          <div class="toolbar">
            ${download(`/api/baselines/${identifier}/image`, "Reference map PNG")}
            ${download(`/api/baselines/${identifier}/csv?table=cells`, "Positions CSV")}
            ${download(`/api/baselines/${identifier}/csv?table=sources`, "Panels CSV")}
            ${baseline.state === "active"
              ? button("retire-baseline", "Retire version", "compact")
              : button("reactivate-baseline", "Reactivate version", "compact")}
          </div>
          <details><summary>Version history</summary>${table(
            [["Time", "at", dateLabel], ["Event", "action", changeLabel]],
            baseline.events,
          )}</details>
        </div></div>
      <img class="baseline-map" src="${e(`/api/baselines/${identifier}/image`)}" alt="Baseline brightness map">
    </div>`;
    const retire = $("#retire-baseline", root);
    retire?.addEventListener("click", async () => {
      await request(`/api/baselines/${identifier}/retire`, "POST");
      app.modal.close();
      app.notify(`Baseline v${baseline.version} retired.`);
      app.render();
    });
    $("#reactivate-baseline", root)?.addEventListener("click", async () => {
      await request(`/api/baselines/${identifier}/reactivate`, "POST");
      app.modal.close();
      app.notify(`Baseline v${baseline.version} reactivated.`);
      app.render();
    });
  } catch (error) {
    root.innerHTML = `<p class="error">${e(error.message)}</p>`;
  }
}

function acquisitionRows(acq) {
  return [
    ["Capture role", acq.role], ["Drive current A", (acq.current_a || []).join(", ") || "not declared"],
    ["Exposure ms", (acq.exposure_ms || []).join(", ")], ["Bit depth", (acq.bit_depth || []).join(", ")],
    ["White level", (acq.white_level || []).join(", ")],
    ["Dark reference used", acq.dark_reference_used ? "yes" : "no"],
    ["Flat reference used", acq.flat_reference_used ? "yes" : "no"],
    ["Signal basis", acq.signal],
  ];
}

// ---------------------------------------------------------------- comparison for the open inspection
export async function renderComparisonPanel(app, root) {
  const item = app.case;
  if (!item.active.high) {
    root.innerHTML = itemCard("Brightness baseline check",
      '<p class="small-copy">Select a high-current capture to compare against the recipe baseline.</p>');
    return;
  }
  const recipe = item.lot;
  root.innerHTML = itemCard("Brightness baseline check", '<p class="small-copy">Checking recipe reference…</p>');
  if (!recipe) {
    root.innerHTML = itemCard("Brightness baseline check",
      '<p class="small-copy">Assign a production lot (used as the recipe name) to enable baseline comparison.</p>');
    return;
  }
  let available = [];
  try {
    available = (await request(`/api/baselines?recipe=${encodeURIComponent(recipe)}&retired=1`))
      .filter((entry) => entry.state === "active");
  } catch (error) {
    root.innerHTML = itemCard("Brightness baseline check", `<p class="error">${e(error.message)}</p>`);
    return;
  }
  const selectedId = root.dataset.baselineId
    || available.find((entry) => entry.version === Math.max(...available.map((b) => b.version)))?.id || "";
  let comparison;
  try {
    comparison = await request(
      `/api/inspections/${item.id}/baseline-comparison/data?baseline=${encodeURIComponent(selectedId)}`
    );
  } catch (error) {
    root.innerHTML = itemCard("Brightness baseline check",
      `<p class="warning">${e(error.message)}</p>
       <p class="small-copy">Open the Baselines view to publish a reference for recipe ${e(recipe)}.</p>`);
    return;
  }
  const counts = comparison.counts;
  const panel = comparison.panel;
  const base = `/api/inspections/${item.id}/baseline-comparison?baseline=${encodeURIComponent(selectedId)}`;
  const selector = available.length > 1 ? field(
    "Adopted reference version", "baseline-version", selectedId,
    {values: available.map((entry) => [entry.id, `v${entry.version} · ${entry.name} (${entry.sample_count} panels)`])},
  ) : "";
  root.innerHTML = `<div class="page-stack">
    ${itemCard(`Recipe check · ${e(comparison.baseline.name)} v${comparison.baseline.version}`, `<div class="section-stack">
      ${selector}
      ${metrics([
        ["Out of family", counts.out_of_family], ["In family", counts.in_family],
        ["Limited evidence", counts.limited_evidence], ["No reference data", counts.no_evidence],
        ["Panel absolute ratio", format(panel.absolute_ratio, 3)],
        ["Whole-panel dim", panel.global_dim ? "YES" : "no"],
      ])}
      ${comparison.messages.map((message) => `<p class="${message.startsWith("Whole-panel") || message.includes("weak") ? "warning" : "info"}">${e(message)}</p>`).join("")}
      <details open><summary>Acquisition settings of the adopted reference</summary>
        ${definition(acquisitionRows(comparison.acquisition))}</details>
      <div class="toolbar">
        ${download(`${base}/image`, "Comparison map PNG")}
        ${download(`${base}/overlay`, "Overlay PNG")}
        ${download(`${base}/csv`, "Comparison CSV")}
        ${button("open-adopted-baseline", "Adopted baseline details", "compact")}
      </div></div>`)}
    <section class="card"><div class="card-header"><h2>Cell positions</h2></div>
      ${table([
        ["Cell", "cell"],
        ["Status", "status", (v) => STATUS_LABEL[v] || v],
        ["Evidence", "evidence", (v) => EVIDENCE_LABEL[v] || v],
        ["Reference n", "reference_samples"],
        ["Observed relative", "observed_relative"],
        ["Reference median", "reference_relative_median"],
        ["Absolute ratio", "absolute_ratio"],
        ["Robust z", "z"],
        ["Support", "support", percent],
        ["Reference panels", "reference_modules", (v) => v.join(", ")],
        ["Reason", "reasons", (v) => v.join("; ")],
      ], comparison.rows)}</section>
  </div>`;
  const versionSelect = $("select[name=baseline-version]", root);
  versionSelect?.addEventListener("change", () => {
    root.dataset.baselineId = versionSelect.value;
    renderComparisonPanel(app, root).catch(app.fail);
  });
  $("#open-adopted-baseline", root).onclick = () => openBaseline(app, comparison.baseline.id);
}
