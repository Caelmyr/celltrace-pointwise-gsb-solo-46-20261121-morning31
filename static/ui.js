export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
export const escapeHtml = (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
export const e = escapeHtml;

export const ROLE = {high: "High current", low: "Low current", dark: "Dark reference", flat: "Flat reference", surface: "Surface photo"};
export const availableRoles = (inspection) => ["high", "low"].filter((role) => inspection.active[role]);
export const readable = (value) => value ? String(value).replaceAll("_", " ") : "—";
export const format = (value, count = 3) => typeof value === "number"
  ? new Intl.NumberFormat("en", {maximumFractionDigits: count}).format(value) : value ?? "—";
export const percent = (value) => value == null ? "—" : format(value * 100, 1) + "%";
export const dateLabel = (value) => value
  ? new Intl.DateTimeFormat("en", {dateStyle: "medium", timeStyle: "short"}).format(new Date(value)) : "—";

export function named(path, query = {}) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value == null || value === "") continue;
    if (Array.isArray(value)) value.forEach((entry) => search.append(key, entry));
    else search.append(key, value);
  }
  return search.size ? path + "?" + search : path;
}

export async function request(url, method = "GET", data = null, asBlob = false) {
  const inputs = {method};
  if (data instanceof FormData) inputs.body = data;
  else if (data !== null) {
    inputs.headers = {"Content-Type": "application/json"};
    inputs.body = JSON.stringify(data);
  }
  const response = await fetch(url, inputs);
  if (!response.ok) {
    let detail = {};
    try {
      detail = await response.json();
    } catch {
      detail = {};
    }
    throw new Error(detail.error || `Request failed (${response.status})`);
  }
  return asBlob ? response.blob() : response.json();
}

export function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 3000);
}

export const badge = (label, variant = "") => `<span class="status-badge ${variant}">${e(label)}</span>`;
export const chip = (label, variant = "") => `<span class="chip ${variant}">${e(label)}</span>`;
export const download = (href, label) => `<a class="button" href="${e(href)}" download>${e(label)}</a>`;
export const button = (id, label, classes = "", disabled = false) =>
  `<button type="button" id="${e(id)}" class="${e(classes)}" ${disabled ? "disabled" : ""}>${e(label)}</button>`;
export const changeLabel = (value) => readable(value).replace(/^[a-z]/, (c) => c.toUpperCase());

export function metrics(values) {
  return `<div class="metric-grid">${values.map(([label, value]) => `
    <div><strong>${e(value ?? "—")}</strong><small>${e(label)}</small></div>`).join("")}</div>`;
}

export function definition(rows) {
  return `<dl class="definition">${rows.map(([label, value]) => `
    <div><dt>${e(label)}</dt><dd>${e(value ?? "—")}</dd></div>`).join("")}</dl>`;
}

export function histogram(rows, label) {
  if (!rows?.length) return "";
  const max = Math.max(1, ...rows.map((row) => row.pixels));
  return `<div class="chart" role="img" aria-label="${e(label)}">${rows.map((row) =>
    `<i style="--size:${Math.max(2, row.pixels / max * 100)}%" title="${format(row.from)} – ${format(row.to)}  ${format(row.pixels, 0)} px"></i>`
  ).join("")}</div>`;
}

export function field(label, name, value = "", options = {}) {
  const wide = options.wide ? "wide" : "";
  const required = options.required ? "required" : "";
  const disabled = options.disabled ? "disabled" : "";
  if (options.multiline) return `<label class="${wide}">${e(label)}
    <textarea aria-label="${e(label)}" name="${e(name)}" ${required} ${disabled} maxlength="${options.max || 2000}">${e(value)}</textarea></label>`;
  if (options.values) return `<label class="${wide}">${e(label)}
    <select aria-label="${e(label)}" name="${e(name)}" ${required} ${disabled}>${options.values.map(([id, desc]) =>
      `<option value="${e(id)}" ${String(value) === String(id) ? "selected" : ""}>${e(desc)}</option>`
    ).join("")}</select></label>`;
  return `<label class="${wide}">${e(label)}<input aria-label="${e(label)}" name="${e(name)}" type="${e(options.type || "text")}"
    value="${e(value)}" ${required} ${disabled}
    ${options.max == null ? "" : `max="${e(options.max)}" maxlength="${e(options.max)}"`}
    ${options.min == null ? "" : `min="${e(options.min)}"`}
    ${options.step == null ? "" : `step="${e(options.step)}"`} ${options.accept ? `accept="${e(options.accept)}"` : ""}
    autocomplete="off"></label>`;
}

export const num = (label, name, value, min, max, step = 1, required = true) =>
  field(label, name, value ?? "", {type: "number", min, max, step, required});

export const submit = (label, method = "save") =>
  `<button type="submit" class="primary" value="${e(method)}">${e(label)}</button>`;

export function table(headers, data, rowButton = null) {
  if (!data?.length) return '<p class="empty-line">No rows in this measurement.</p>';
  return `<div class="table-wrap"><table><thead><tr>${headers.map((entry) => `<th scope="col">${e(entry[0])}</th>`).join("")}
    ${rowButton ? '<th scope="col"><span class="muted">Inspect</span></th>' : ""}
    </tr></thead><tbody>${data.map((row) => `<tr>${headers.map((entry) => {
      const [label, key, formatCell] = entry;
      const value = formatCell ? formatCell(row[key], row) : format(row[key]);
      const numeric = typeof row[key] === "number";
      return `<td ${numeric ? 'class="num"' : ""}>${e(value)}</td>`;
    }).join("")}${rowButton ? `<td>${rowButton(row)}</td>` : ""}</tr>`).join("")}</tbody></table></div>`;
}

export function itemCard(title, body, tools = "") {
  return `<section class="card"><div class="card-header"><h2>${e(title)}</h2>${tools}</div><div class="card-pad">${body}</div></section>`;
}

export function clicks(root, selector, action) {
  $$(selector, root).forEach((node) => node.addEventListener("click", () => action(node)));
}

export function wireForm(root, callback) {
  const form = $("form", root);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const incoming = new FormData(form);
    const active = $$("button", form);
    const error = $(".form-error", form);
    if (error) error.hidden = true;
    for (const control of active) control.disabled = true;
    try {
      await callback(Object.fromEntries(incoming), incoming, event.submitter, form);
    } catch (exception) {
      if (error) {
        error.textContent = exception.message;
        error.hidden = false;
      }
    } finally {
      for (const control of active) control.disabled = false;
    }
  });
  return form;
}

export function normalizedPoint(event, element) {
  const area = element.getBoundingClientRect();
  return [
    Math.min(1, Math.max(0, (event.clientX - area.left) / area.width)),
    Math.min(1, Math.max(0, (event.clientY - area.top) / area.height)),
  ];
}

export function bindCanvas(canvas, image, renderer) {
  function redraw() {
    const area = image.getBoundingClientRect();
    const px = Math.max(1, self.devicePixelRatio || 1);
    canvas.width = Math.round(area.width * px);
    canvas.height = Math.round(area.height * px);
    if (!canvas.width || !canvas.height) return;
    const ctx = canvas.getContext("2d");
    ctx.resetTransform();
    ctx.scale(px, px);
    renderer(ctx, area.width, area.height);
  }
  image.addEventListener("load", redraw);
  const observer = new ResizeObserver(() => {
    if (canvas.isConnected) redraw();
    else observer.disconnect();
  });
  observer.observe(image);
  redraw();
  return redraw;
}

export function drawPoints(ctx, width, height, points, {close = false, color = "#9af1cd", numbered = false} = {}) {
  const coordinates = points.map(([x, y]) => [x * width, y * height]);
  if (!coordinates.length) return;
  ctx.beginPath();
  ctx.moveTo(...coordinates[0]);
  coordinates.slice(1).forEach((point) => ctx.lineTo(...point));
  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  if (close && coordinates.length > 2) {
    ctx.closePath();
    ctx.fillStyle = "#76e0ab28";
    ctx.fill();
  }
  ctx.stroke();
  coordinates.forEach(([x, y], index) => {
    ctx.beginPath();
    ctx.arc(x, y, 4, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
    if (numbered) {
      ctx.fillStyle = "#101d23";
      ctx.fillRect(x + 6, y - 13, 15, 17);
      ctx.fillStyle = color;
      ctx.font = "12px system-ui";
      ctx.fillText(String(index + 1), x + 10, y);
    }
  });
}
