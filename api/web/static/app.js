"use strict";

const POLL_INTERVAL_MS = 2000;
const STATUS_TEXT = {
  awaiting_upload: "Esperando el video…",
  queued: "En cola para analizar…",
  processing: "Analizando tu golpe (detectando la pose y midiendo)…",
};
const CATEGORY_LABEL = { fundamental: "Fundamental", sincronizacion: "Sincronización" };

const STATUS_LABEL = { ...STATUS_TEXT, completed: "Completado", failed: "Falló" };

const $ = (sel) => document.querySelector(sel);
const form = $("#upload-form");

boot();

async function boot() {
  try {
    await Auth.init();
  } catch (err) {
    showStatus(err.message, { error: true });
  }
  renderSession();
  if (Auth.isLoggedIn()) await loadHistory();
}

function renderSession() {
  const loggedIn = Auth.isLoggedIn();
  form.hidden = !loggedIn;
  $("#history").hidden = !loggedIn;
  $("#login-card").hidden = loggedIn;
  $("#session").hidden = !Auth.enabled() || !loggedIn;
  $("#session-user").textContent = loggedIn ? Auth.displayName() : "";
}

$("#login").addEventListener("click", () => Auth.login());
$("#logout").addEventListener("click", () => Auth.logout());
$("#refresh-history").addEventListener("click", () => loadHistory());

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = new FormData(form);
  setBusy(true);
  try {
    const ticket = await createAnalysis(data);
    await uploadVideo(ticket.upload_url, data.get("video"));
    const analysis = await postJSON(`/v1/analyses/${ticket.analysis.id}/submit`);
    render(await pollUntilDone(analysis.id));
  } catch (err) {
    showStatus(err.message, { error: true });
  } finally {
    setBusy(false);
    loadHistory();
  }
});

// apiFetch añade el bearer token; si la sesión ya no es válida vuelve a mostrar el login.
async function apiFetch(url, options = {}) {
  const headers = new Headers(options.headers);
  const token = await Auth.accessToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  else if (Auth.enabled()) return sessionExpired();
  const res = await fetch(url, { ...options, headers });
  if (res.status === 401) return sessionExpired();
  return res;
}

function sessionExpired() {
  renderSession();
  throw new Error("Tu sesión ha caducado. Inicia sesión de nuevo.");
}

async function loadHistory() {
  const list = $("#history-list");
  try {
    const page = await parseResponse(await apiFetch("/v1/analyses?limit=20"));
    list.replaceChildren(...(page.items.length ? page.items.map(historyItem) : [el("li", { className: "muted" }, "Aún no tienes análisis.")]));
  } catch (err) {
    list.replaceChildren(el("li", { className: "error-text" }, err.message));
  }
}

function historyItem(a) {
  const when = new Date(a.created_at).toLocaleString();
  const button = el("button", { type: "button", className: "link" }, `${when} · ${a.stroke_type || "detectando…"}`);
  button.addEventListener("click", () => openAnalysis(a.id));
  return el("li", {}, button, el("span", { className: `status ${a.status}` }, STATUS_LABEL[a.status] || a.status));
}

async function openAnalysis(id) {
  setBusy(true);
  try {
    render(await pollUntilDone(id));
  } catch (err) {
    showStatus(err.message, { error: true });
  } finally {
    setBusy(false);
  }
}

async function createAnalysis(data) {
  showStatus("Preparando la subida…");
  return postJSON("/v1/analyses", {
    stroke_type: data.get("stroke_type") || undefined, // vacío = que lo detecte el clasificador
    handedness: data.get("handedness"),
    camera_view: data.get("camera_view"),
  });
}

async function postJSON(url, body) {
  const res = await apiFetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  return parseResponse(res);
}

async function parseResponse(res) {
  const payload = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(payload.error?.message || `Error ${res.status}`);
  return payload;
}

// XMLHttpRequest en lugar de fetch para poder mostrar el progreso de subida.
function uploadVideo(url, file) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", url);
    xhr.setRequestHeader("Content-Type", file.type || "application/octet-stream");
    xhr.upload.onprogress = (e) => e.lengthComputable && showStatus("Subiendo video…", { progress: (e.loaded / e.total) * 100 });
    xhr.onload = () => (xhr.status < 300 ? resolve() : reject(new Error(`La subida falló (${xhr.status}).`)));
    xhr.onerror = () => reject(new Error("No se pudo conectar con el almacenamiento para subir el video."));
    xhr.send(file);
  });
}

async function pollUntilDone(id) {
  for (;;) {
    const analysis = await parseResponse(await apiFetch(`/v1/analyses/${id}`));
    if (analysis.status === "completed") return analysis;
    if (analysis.status === "failed") throw new Error(analysis.error?.message || "El análisis falló.");
    showStatus(STATUS_TEXT[analysis.status] || analysis.status);
    await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
  }
}

function setBusy(busy) {
  form.querySelector("button").disabled = busy;
  if (busy) $("#result").hidden = true;
}

function showStatus(text, { progress, error = false } = {}) {
  $("#status").hidden = false;
  const el = $("#status-text");
  el.textContent = text;
  el.className = error ? "error-text" : "";
  const bar = $("#progress");
  bar.hidden = error;
  if (progress === undefined) bar.removeAttribute("value");
  else bar.value = progress;
}

function render(analysis) {
  const report = analysis.report;
  $("#status").hidden = true;
  $("#result").hidden = false;
  $("#warnings").replaceChildren(...report.quality.warnings.map((w) => el("p", { className: "warning" }, w)));
  $("#findings").replaceChildren(...findingItems(report.findings));
  $("#metrics tbody").replaceChildren(...report.metrics.map(metricRow));
}

function findingItems(findings) {
  if (findings.length === 0) {
    return [el("li", {}, el("p", {}, "No detectamos errores con las reglas actuales. ¡Buen golpe!"))];
  }
  return findings.map((f) =>
    el("li", {},
      el("span", { className: `tag ${f.category}` }, CATEGORY_LABEL[f.category] || f.category),
      el("h3", {}, f.title),
      el("p", {}, f.explanation),
      el("p", { className: "correction" }, el("strong", {}, "Corrección: "), f.correction),
      f.drills.length ? el("ul", {}, ...f.drills.map((d) => el("li", {}, d))) : ""
    )
  );
}

function metricRow(m) {
  const value = m.unit === "bool" ? (m.value ? "sí" : "no") : `${m.value.toFixed(2)} ${m.unit}`;
  return el("tr", {}, el("td", {}, m.label), el("td", {}, value), el("td", {}, `${Math.round(m.confidence * 100)}%`));
}

// Construye nodos con textContent: nunca se inyecta HTML del servidor (evita XSS).
function el(tag, props, ...children) {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children);
  return node;
}
