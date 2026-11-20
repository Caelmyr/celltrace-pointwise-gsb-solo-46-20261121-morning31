import {
  $, $$, availableRoles, badge, button, changeLabel, dateLabel, e, field, named, request, submit, wireForm,
} from "./ui.js";
import {renderWorkbench} from "./bench.js";
import {renderCaptures, showPlane} from "./captures.js";
import {renderFindings, renderLot, renderReport, showHistory} from "./reviews.js";

const TABS = [
  ["bench", "Image bench"], ["captures", "Captures"], ["findings", "Findings"],
  ["report", "Report"], ["lot", "Lot review"],
];

class CellTraceApp {
  constructor() {
    this.main = $("#main-content");
    this.modal = $("#dialog");
    this.toast = $("#status");
    this.inspections = [];
    this.case = null;
    this.workspace = {layer: "panel", role: "high", grid: true, labels: true, findings: true, mode: "probe", zoom: 100,
      points: [], cell: "", probe: null, measure: null, strip: null};
    this.tab = "bench";
    this.searchTimer = null;
    this.toastTimer = null;
    this.renderVersion = 0;
    this.recentQuery = 0;
  }

  async start() {
    $("#new-inspection").addEventListener("click", () => this.newInspection());
    $("#open-demo").addEventListener("click", () => this.demoDialog());
    $("#import-inspection").addEventListener("click", () => this.exchangeDialog());
    $("#search").addEventListener("input", () => {
      clearTimeout(this.searchTimer);
      this.searchTimer = setTimeout(() => this.refreshCases().catch(this.fail), 160);
    });
    addEventListener("popstate", () => this.followUrl());
    await this.refreshCases();
    await this.followUrl();
  }

  followUrl = async () => {
    const address = new URL(location.href);
    this.tab = TABS.some(([key]) => key === address.searchParams.get("view")) ? address.searchParams.get("view") : "bench";
    const id = address.searchParams.get("case") || this.inspections[0]?.id;
    if (id) {
      try {
        await this.loadCase(id, false);
      } catch (error) {
        this.fail(error);
        this.case = null;
        this.render();
      }
    } else {
      this.render();
    }
  };

  url(suffix = "", query = {}) {
    if (!this.case) return null;
    return named(`/api/inspections/${this.case.id}${suffix}`, query);
  }

  navigate(replace = false) {
    const p = named("/", {case: this.case?.id, view: this.tab === "bench" ? null : this.tab});
    if (replace) history.replaceState(null, "", p);
    else history.pushState(null, "", p);
  }

  async refreshCases() {
    const query = ++this.recentQuery;
    const values = await request(named("/api/inspections", {q: $("#search").value.trim()}));
    if (query !== this.recentQuery) return;
    this.inspections = values;
    this.renderCases();
  }

  renderCases() {
    $("#inspection-count").textContent = this.inspections.length;
    const view = $("#case-list");
    view.innerHTML = this.inspections.length ? this.inspections.map((item) => `
      <button type="button" class="case-button ${item.id === this.case?.id ? "selected" : ""}" data-case="${item.id}"
        aria-current="${item.id === this.case?.id ? "page" : "false"}">
        <strong>${e(item.module)}</strong>
        <small>${e(item.lot || changeLabel(item.state))} · ${item.image_count} image${item.image_count === 1 ? "" : "s"}</small>
        <i class="dot ${item.state === "issued" ? "" : item.synthetic ? "warm" : "muted"}"></i>
      </button>`).join("") : '<p class="empty-line">No matching inspections.</p>';
    $$("button[data-case]", view).forEach((node) => node.addEventListener("click", () =>
      this.loadCase(node.dataset.case, true).catch(this.fail)));
  }

  async loadCase(id, writeUrl) {
    this.main.setAttribute("aria-busy", "true");
    const data = await request(`/api/inspections/${id}`);
    const changed = this.case?.id !== data.id;
    this.case = data;
    if (changed) {
      Object.assign(this.workspace, {
        role: availableRoles(data)[0] ?? "high", layer: "panel",
        mode: "probe", cell: "", points: [], measure: null, probe: null, zoom: 100, strip: null,
      });
    }
    if (writeUrl) this.navigate();
    this.renderCases();
    this.render();
  }

  update = async (item, message) => {
    const switched = this.case?.id !== item.id;
    this.case = item;
    if (!item.active[this.workspace.role]) this.workspace.role = availableRoles(item)[0] || "high";
    this.workspace.probe = null;
    this.workspace.measure = null;
    if (switched) {
      this.tab = "bench";
      Object.assign(this.workspace, {layer: "panel", cell: "", points: [], mode: "probe", zoom: 100, strip: null});
    }
    this.navigate(true);
    this.render();
    this.refreshCases().catch(this.fail);
    if (message) this.notify(message);
  };

  async patch(operation, body, message) {
    const item = await request(this.url(`/${operation}`), "PATCH", {...body, revision: this.case.revision});
    await this.update(item, message);
    return item;
  }

  notify(message, isError = false) {
    this.toast.textContent = message;
    this.toast.classList.toggle("error", isError);
    this.toast.hidden = false;
    clearTimeout(this.toastTimer);
    this.toastTimer = setTimeout(() => { this.toast.hidden = true; }, 6000);
  }

  fail = (error) => this.notify(error.message || String(error), true);

  showTab(tab) {
    this.tab = tab;
    this.navigate();
    this.render();
  }

  render() {
    this.renderVersion += 1;
    this.main.setAttribute("aria-busy", "false");
    if (!this.case) {
      this.main.innerHTML = `<section class="hero">
        <h1>Electroluminescence inspection</h1>
        <p>Review paired EL captures, align the module, measure image evidence, and issue a local inspection record.</p>
        <div class="toolbar">${button("hero-sample", "Open training sample", "primary")}${button("hero-new", "New inspection")}</div>
      </section>`;
      $("#hero-sample").onclick = () => this.demoDialog();
      $("#hero-new").onclick = () => this.newInspection();
      return;
    }
    const item = this.case;
    const synthetic = Object.values(item.captures).some((image) => image.synthetic);
    this.main.innerHTML = `
      <header class="page-header">
        <div><div class="crumbs"><span>Inspections</span><span class="slash">/</span><span>${e(item.lot || "Unassigned lot")}</span></div>
          <div class="headline"><h1>${e(item.module)}</h1>
           ${badge(item.state === "issued" ? "Issued" : "Working", item.state === "issued" ? "" : "neutral")}
           ${synthetic ? badge("Synthetic data", "training") : ""}</div>
          <p class="subtitle">${e(item.operator ? `${item.operator} · ` : "")}Revision ${item.revision} · ${dateLabel(item.updated_at)}</p>
        </div>
        <nav class="toolbar right" aria-label="Inspection actions">
          ${button("case-history", "History", "quiet")}
          ${button("case-edit", item.state === "issued" ? "Working copy" : "Edit details")}
          ${button("case-plane", "Panel plane")}
        </nav>
      </header>
      <nav class="tabs" aria-label="Inspection views">${TABS.map(([key, name]) =>
        `<button type="button" data-tab="${key}" class="${this.tab === key ? "selected" : ""}"
          ${this.tab === key ? 'aria-current="page"' : ""}>${name}</button>`).join("")}</nav>
      <section id="active-view"></section>`;
    $("#case-history").onclick = () => showHistory(this);
    $("#case-edit").onclick = () => item.state === "issued" ? this.copyDialog() : this.detailsDialog();
    $("#case-plane").onclick = () => showPlane(this);
    $$("[data-tab]").forEach((node) => { node.onclick = () => this.showTab(node.dataset.tab); });
    const view = $("#active-view");
    const renderTab = {bench: renderWorkbench, captures: renderCaptures, findings: renderFindings, report: renderReport, lot: renderLot};
    renderTab[this.tab](this, view);
  }

  openDialog(title, body, wide = false) {
    if (this.modal.open) this.modal.close();
    this.modal.classList.toggle("wide", wide);
    const content = $("#dialog-content");
    content.innerHTML = `<div class="dialog-head"><h2 id="dialog-title">${e(title)}</h2>
      <button type="button" id="close-dialog" class="icon-only quiet" aria-label="Close dialog">✕</button></div>
      <div class="dialog-body">${body}</div>`;
    $("#close-dialog", content).onclick = () => this.modal.close();
    this.modal.showModal();
    return $(".dialog-body", content);
  }

  async showLoadDialog(title, path, draw, wide = false) {
    const root = this.openDialog(title, '<p class="small-copy">Loading…</p>', wide);
    try {
      const result = await request(path);
      if (!root.isConnected || !this.modal.open) return;
      draw(root, result);
    } catch (error) {
      if (root.isConnected) root.innerHTML = `<p class="error">${e(error.message)}</p>`;
    }
  }

  newInspection() {
    const root = this.openDialog("New inspection", `<form><div class="fields">${detailFields({})}</div>
      <p class="form-error" hidden></p><div class="form-actions">${submit("Create inspection")}</div></form>`);
    wireForm(root, async (values) => {
      const item = await request("/api/inspections", "POST", values);
      this.modal.close();
      await this.update(item, "Inspection created.");
      this.showTab("captures");
    });
  }

  detailsDialog() {
    const root = this.openDialog("Edit inspection details", `<form><div class="fields">${detailFields(this.case)}</div>
      <p class="form-error" hidden></p><div class="form-actions">${submit("Save details")}</div></form>`);
    wireForm(root, async (values) => {
      await this.patch("metadata", values, "Inspection details saved.");
      this.modal.close();
    });
  }

  copyDialog() {
    const root = this.openDialog("Create a working copy", `<form><div class="fields">${field("Module serial", "module",
      `${this.case.module} review`.slice(0, 80), {required: true, max: 80, wide: true})}</div>
      <p class="small-copy">The copy uses the saved originals, image settings and findings.</p><p class="form-error" hidden></p>
      <div class="form-actions">${submit("Create working copy")}</div></form>`);
    wireForm(root, async (values) => {
      const item = await request(this.url("/copy"), "POST", values);
      this.modal.close();
      await this.update(item, "Working copy created.");
    });
  }

  demoDialog() {
    const root = this.openDialog("Open synthetic inspection", `<form><div class="fields">${field("Training material", "variant", "standard", {
      wide: true, values: [["standard", "EL-24017 · Paired production sample"], ["stress", "EL-24029 · Cropped low-current source"]],
    })}</div><p class="small-copy">Numerically generated high, low, dark and flat images with visible image-quality differences.</p>
    <p class="form-error" hidden></p><div class="form-actions">${submit("Load sample")}</div></form>`);
    wireForm(root, async (values) => {
      const item = await request(`/api/demo/${values.variant}`, "POST");
      this.modal.close();
      await this.update(item, "Synthetic inspection loaded.");
    });
  }

  exchangeDialog() {
    const root = this.openDialog("Import inspection exchange", `<form>
      <div class="fields">${field("CellTrace exchange ZIP", "file", "", {type: "file", required: true, accept: ".zip", wide: true})}
        ${field("Module serial override", "module", "", {max: 80, wide: true})}</div>
      <div id="archive-info"></div><p class="form-error" hidden></p><div class="form-actions">
        <button type="submit" value="preview">Check package</button>${submit("Import verified originals", "restore")}</div></form>`);
    wireForm(root, async (values, data, command) => {
      const result = await request(`/api/import/${command.value}`, "POST", data);
      if (command.value === "preview") {
        $("#archive-info", root).innerHTML = `<p class="info">${e(result.module)} · ${result.capture_count} captures ·
          ${result.findings} findings · ${e(result.verification)}</p>`;
      } else {
        this.modal.close();
        await this.update(result, "Inspection exchange restored.");
      }
    });
  }
}

function detailFields(item) {
  return [
    field("Module serial", "module", item.module, {max: 80, required: true}),
    field("Production lot", "lot", item.lot, {max: 80}),
    field("Operator", "operator", item.operator, {max: 80}),
    field("Notes", "notes", item.notes, {wide: true, multiline: true, max: 2000}),
  ].join("");
}

const app = new CellTraceApp();
app.start().catch((error) => {
  app.main.setAttribute("aria-busy", "false");
  app.fail(error);
  app.render();
});
