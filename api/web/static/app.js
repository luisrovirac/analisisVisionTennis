"use strict";

const POLL_INTERVAL_MS = 2000;
const STATUS_TEXT = {
  awaiting_upload: "Esperando el video…",
  queued: "En cola para analizar…",
  processing: "Analizando tu golpe (detectando la pose y midiendo)…",
};
const CATEGORY_LABEL = { fundamental: "Fundamental", sincronizacion: "Sincronización" };

const STATUS_LABEL = { ...STATUS_TEXT, completed: "Completado", failed: "Falló" };
// Mientras el worker procesa, la API rechaza el borrado (409): el botón se desactiva.
const BUSY_STATUSES = new Set(["queued", "processing"]);
let shownAnalysisId = null;
let viewer = null; // SkeletonViewer del análisis mostrado
let shownReport = null;

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
  if (Auth.isLoggedIn()) await Promise.all([loadPreferences(), loadHistory(), loadMe()]);
}

// Si la cuenta tiene un rol profesional (experto, validador o admin) se muestra el acceso al portal.
async function loadMe() {
  try {
    const me = await parseResponse(await apiFetch("/v1/me"));
    $("#portal-link").hidden = me.roles.length === 0;
  } catch {
    $("#portal-link").hidden = true;
  }
}

// La mano dominante se guarda en la cuenta del usuario: la comparten la web y el móvil.
const handednessSelect = form.elements.handedness;

async function loadPreferences() {
  try {
    const prefs = await parseResponse(await apiFetch("/v1/me/preferences"));
    handednessSelect.value = prefs.handedness;
  } catch (err) {
    showStatus(`No pudimos cargar tu mano dominante: ${err.message}`, { error: true });
  }
}

handednessSelect.addEventListener("change", async () => {
  try {
    const res = await apiFetch("/v1/me/preferences", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ handedness: handednessSelect.value }),
    });
    await parseResponse(res);
  } catch (err) {
    showStatus(`No pudimos guardar tu mano dominante: ${err.message}`, { error: true });
  }
});

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
  const label = `${when} · ${a.stroke_type || "detectando…"}`;
  const open = el("button", { type: "button", className: "link" }, label);
  open.addEventListener("click", () => openAnalysis(a.id));
  const busy = BUSY_STATUSES.has(a.status);
  const remove = el("button", {
    type: "button",
    className: "danger",
    disabled: busy,
    title: busy ? "Espera a que termine el análisis para eliminarlo" : "Eliminar este análisis",
  }, "Eliminar");
  remove.addEventListener("click", () => deleteAnalysis(a.id, label, remove));
  return el("li", {}, open,
    el("span", { className: "history-actions" },
      el("span", { className: `status ${a.status}` }, STATUS_LABEL[a.status] || a.status), remove));
}

async function deleteAnalysis(id, label, button) {
  if (!confirm(`¿Eliminar el análisis del ${label}? Se borrarán el video y el informe, y no se puede deshacer.`)) return;
  button.disabled = true;
  try {
    const res = await apiFetch(`/v1/analyses/${id}`, { method: "DELETE" });
    if (!res.ok && res.status !== 404) await parseResponse(res); // 404: ya no existía, el objetivo se cumple
    if (shownAnalysisId === id) {
      $("#result").hidden = true;
      shownAnalysisId = null;
      closeViewer();
    }
    await loadHistory();
  } catch (err) {
    button.disabled = false;
    alert(err.message);
  }
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
    expert_review_allowed: data.get("expert_review_allowed") === "on",
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

// Los campos de la revisión 1.1 del reporte (strengths, diagnosis, drill_plan, reliability,
// knowledge_status) son opcionales: un reporte antiguo se sigue mostrando como antes.
function render(analysis) {
  const report = analysis.report;
  shownAnalysisId = analysis.id;
  shownReport = report;
  showViewer(analysis);
  $("#status").hidden = true;
  $("#result").hidden = false;
  $("#warnings").replaceChildren(...report.quality.warnings.map((w) => el("p", { className: "warning" }, w)));
  renderDiagnosis(report.diagnosis);
  const comparison = SkeletonViewer.comparisonCard(report.reference_comparison);
  $("#comparison").replaceChildren(...(comparison ? [comparison] : []));
  const strengths = report.strengths || [];
  $("#strengths-section").hidden = strengths.length === 0;
  $("#strengths").replaceChildren(...strengths.map(strengthItem));
  $("#findings").replaceChildren(...findingItems(report.findings));
  renderPlan(report.drill_plan);
  const rel = report.reliability;
  $("#reliability").textContent = rel
    ? `Fiabilidad del informe: ${RELIABILITY_LABEL[rel.level]} (${Math.round(rel.score * 100)}%). ${rel.reasons.join(" ")}`
    : "";
  const ks = report.knowledge_status;
  $("#knowledge-status").textContent = ks ? KNOWLEDGE_LABEL[ks.status](ks) : "";
  const review = $("#result-expert-review");
  review.checked = analysis.expert_review_allowed !== false;
  review.onchange = () => updateExpertReview(analysis.id, review);
  $("#metrics tbody").replaceChildren(...report.metrics.map(metricRow));
}

// El video con el esqueleto se carga aparte (URLs firmadas de /media) para no retrasar el informe.
async function showViewer(analysis) {
  closeViewer();
  const section = $("#viewer-section");
  const box = $("#viewer");
  section.hidden = false;
  box.replaceChildren(el("p", { className: "muted" }, "Cargando video y esqueleto…"));
  try {
    const media = await parseResponse(await apiFetch(`/v1/analyses/${analysis.id}/media`));
    const pose = await SkeletonViewer.loadPose(media.pose_url);
    if (shownAnalysisId !== analysis.id) return; // el usuario abrió otro análisis entretanto
    viewer = SkeletonViewer.create(box, { videoUrl: media.video_url, pose, segment: analysis.report.segment });
  } catch (err) {
    if (shownAnalysisId === analysis.id) box.replaceChildren(el("p", { className: "muted" }, `No pudimos cargar el video: ${err.message}`));
  }
}

function closeViewer() {
  if (viewer) viewer.destroy();
  viewer = null;
}

// Salta al impacto y resalta las articulaciones de las métricas que sustentan el hallazgo.
function showInVideo(evidence, tone) {
  if (!viewer || !shownReport) return;
  const landmarks = SkeletonViewer.landmarksFor(shownReport, Object.keys(evidence || {}));
  viewer.focus(shownReport.segment.impact_frame, landmarks, tone);
}

function videoButton(evidence, tone) {
  const b = el("button", { type: "button", className: "secondary sv-jump" }, "Ver en el video");
  b.addEventListener("click", () => showInVideo(evidence, tone));
  return b;
}

const RELIABILITY_LABEL = { high: "alta", medium: "media", low: "baja" };
const KNOWLEDGE_LABEL = {
  validated: () => "Todas las reglas usadas están validadas por el equipo de expertos.",
  partially_validated: (k) =>
    `${k.validated_rules} de ${k.total_rules} reglas validadas por el equipo de expertos; el resto son provisionales.`,
  provisional: () => "Las reglas de análisis aún son provisionales: el equipo de expertos las está validando.",
};

function renderDiagnosis(d) {
  const box = $("#diagnosis");
  box.hidden = !d;
  if (!d) return;
  const parts = [el("h2", {}, "Resumen"), el("p", {}, d.summary)];
  if (d.unmeasurable.length) {
    parts.push(el("p", { className: "muted" }, `No se pudo medir con esta grabación: ${d.unmeasurable.join(", ")}.`));
  }
  box.replaceChildren(...parts);
}

function strengthItem(s) {
  return el("li", {},
    el("h3", {}, s.title),
    el("p", {}, s.why_it_matters),
    s.how_to_maintain ? el("p", { className: "correction" }, el("strong", {}, "Cómo aprovecharlo: "), s.how_to_maintain) : "",
    videoButton(s.evidence, "strength"));
}

function renderPlan(plan) {
  $("#plan-section").hidden = !plan;
  if (!plan) return;
  $("#plan").replaceChildren(...plan.items.map((i) =>
    el("li", {}, el("strong", {}, `Semana ${i.week}: ${i.focus}`),
      i.drills.length ? el("ul", {}, ...i.drills.map((d) => el("li", {}, d))) : "")));
  $("#plan-next").textContent = `Vuelve a grabarte en ${plan.reanalyze_after_weeks} semana(s) para medir tu progreso.`;
}

async function updateExpertReview(id, checkbox) {
  checkbox.disabled = true;
  try {
    const res = await apiFetch(`/v1/analyses/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expert_review_allowed: checkbox.checked }),
    });
    await parseResponse(res);
  } catch (err) {
    checkbox.checked = !checkbox.checked;
    alert(err.message);
  } finally {
    checkbox.disabled = false;
  }
}

function findingItems(findings) {
  if (findings.length === 0) {
    return [el("li", {}, el("p", {}, "No detectamos errores con las reglas actuales. ¡Buen golpe!"))];
  }
  return findings.map((f) =>
    el("li", {},
      el("span", { className: `tag ${f.category}` }, CATEGORY_LABEL[f.category] || f.category),
      f.validated === undefined ? "" : el("span", { className: `tag ${f.validated ? "validated" : "provisional"}` },
        f.validated ? "Regla validada" : "Regla provisional"),
      el("h3", {}, f.title),
      el("p", {}, f.explanation),
      el("p", { className: "correction" }, el("strong", {}, "Corrección: "), f.correction),
      f.drills.length ? el("ul", {}, ...f.drills.map((d) => el("li", {}, d))) : "",
      videoButton(f.evidence, "error")
    )
  );
}

function metricRow(m) {
  const value = m.unit === "bool" ? (m.value ? "sí" : "no") : `${m.value.toFixed(2)} ${m.unit}`;
  const label = el("button", { type: "button", className: "link", title: "Ver en el video" }, m.label);
  label.addEventListener("click", () => showInVideo({ [m.id]: m.value }, "error"));
  return el("tr", {}, el("td", {}, label), el("td", {}, value), el("td", {}, `${Math.round(m.confidence * 100)}%`));
}

// Construye nodos con textContent: nunca se inyecta HTML del servidor (evita XSS).
function el(tag, props, ...children) {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children);
  return node;
}
