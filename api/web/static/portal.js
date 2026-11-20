"use strict";

// Portal profesional. La API decide qué puede hacer cada rol; aquí solo se ocultan las opciones que
// no corresponden. Todo se pinta con textContent (nunca HTML del servidor).

const STROKES = { forehand: "Forehand", backhand: "Revés", serve: "Saque", volley: "Volea" };
const CATEGORIES = { fundamental: "Fundamental", sincronizacion: "Sincronización" };
const OPS = { lt: "<", lte: "≤", gt: ">", gte: "≥", eq: "=", between: "entre" };
const STATUS = {
  draft: "Borrador", submitted: "Pendiente de validación", changes_requested: "Cambios pedidos",
  approved: "Aprobada", rejected: "Rechazada", withdrawn: "Retirada",
};
const ROLES = { admin: "Admin", expert: "Experto", validator: "Validador" };
const SPECIALTIES = { tennis_coach: "Entrenador de tenis", biomechanics: "Biomecánica", physio: "Fisioterapia", fitness: "Preparación física" };

const $ = (sel) => document.querySelector(sel);
const state = { me: null, stroke: "forehand", catalog: [], draft: null };

boot();

async function boot() {
  try {
    await Auth.init();
  } catch (err) {
    flash(err.message, true);
  }
  $("#login").onclick = () => Auth.login();
  $("#logout").onclick = () => Auth.logout();
  if (!Auth.isLoggedIn()) {
    $("#login-card").hidden = false;
    return;
  }
  $("#logout").hidden = !Auth.enabled();
  $("#session-user").textContent = Auth.displayName();
  try {
    state.me = await api("/v1/me");
  } catch (err) {
    flash(err.message, true);
    return;
  }
  renderTabs();
}

function has(...roles) {
  return roles.some((r) => state.me.roles.includes(r));
}

function renderTabs() {
  const tabs = [
    ["kb", "Base de conocimiento", has("expert", "validator", "admin"), showKnowledge],
    ["mine", "Mis propuestas", has("expert"), showMyProposals],
    ["review", "Validación", has("validator"), showReviewInbox],
    ["metrics", "Solicitudes de métricas", has("expert", "validator", "admin"), showMetricRequests],
    ["admin", "Administración", has("admin"), showAdmin],
    ["profile", "Mi perfil", has("expert", "validator"), showProfile],
  ].filter((t) => t[2]);
  if (tabs.length === 0) {
    $("#view").replaceChildren(el("div", { className: "card" },
      el("p", {}, "Tu cuenta no tiene un rol profesional. El administrador invita a expertos y validadores.")));
    return;
  }
  $("#tabs").replaceChildren(...tabs.map(([id, label, , show]) => {
    const b = el("button", { type: "button", className: "secondary", id: `tab-${id}` }, label);
    b.onclick = () => select(id, show);
    return b;
  }));
  select(tabs[0][0], tabs[0][3]);
  if (has("expert", "validator") && !state.me.profile) {
    flash("Completa tu perfil profesional (pestaña «Mi perfil»): así se firman tus aportes dentro del portal.");
  }
}

function select(id, show) {
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.id === `tab-${id}`));
  flash("");
  show().catch((err) => flash(err.message, true));
}

// ---------- Base de conocimiento ----------

async function showKnowledge() {
  const view = $("#view");
  const picker = strokePicker(() => showKnowledge());
  let release;
  try {
    release = await api(`/v1/kb/strokes/${state.stroke}`);
  } catch (err) {
    view.replaceChildren(picker, el("p", { className: "card" }, `${STROKES[state.stroke]}: todavía no tiene base de conocimiento publicada.`));
    return;
  }
  await loadCatalog();
  const kb = release.knowledge_base;
  const actions = has("expert") ? button("Proponer una regla nueva", () => openEditor({ rule: emptyRule() })) : "";
  const errors = kb.rules.filter((r) => r.polarity !== "strength");
  const strengths = kb.rules.filter((r) => r.polarity === "strength");
  const history = await api(`/v1/kb/releases?stroke=${state.stroke}`);
  view.replaceChildren(
    picker,
    el("div", { className: "card" },
      el("p", {}, el("strong", {}, `Versión activa: ${release.version}`), ` · ${kb.rules.length} reglas · `,
        `${kb.rules.filter((r) => r.validated).length} validadas`),
      actions),
    el("h2", {}, "Errores"), ...errors.map((r) => ruleCard(r)),
    el("h2", {}, "Puntos fuertes"), ...strengths.map((r) => ruleCard(r)),
    el("h2", {}, "Historial de versiones"), releasesTable(history.items),
  );
}

function strokePicker(onChange) {
  const select = el("select", { "aria-label": "Golpe" },
    ...Object.entries(STROKES).map(([v, l]) => el("option", { value: v, selected: v === state.stroke }, l)));
  select.onchange = () => {
    state.stroke = select.value;
    state.catalog = [];
    onChange();
  };
  return el("label", { className: "inline" }, "Golpe ", select);
}

function ruleCard(rule) {
  const actions = [];
  if (has("expert")) {
    actions.push(button("Proponer cambio", () => openEditor({ rule: structuredClone(rule) }), "secondary"));
    actions.push(button("Proponer retirarla", () => openEditor({ removeId: rule.id }), "danger"));
  }
  return el("article", { className: "card rule" },
    el("div", { className: "rule-head" },
      el("strong", {}, `${rule.id} · ${rule.title}`),
      el("span", {}, tag(CATEGORIES[rule.category] || rule.category, rule.category),
        tag(rule.validated ? "Validada" : "Provisional", rule.validated ? "validated" : "provisional"))),
    el("p", { className: "mono" }, conditionsText(rule)),
    el("p", {}, rule.explanation),
    rule.polarity === "strength"
      ? el("p", { className: "correction" }, el("strong", {}, "Por qué importa: "), rule.why_it_matters)
      : el("p", { className: "correction" }, el("strong", {}, "Corrección: "), rule.correction),
    listLine("Ejercicios", rule.drills), listLine("Causado por", rule.caused_by),
    listLine("No se elogia si aparece", rule.suppressed_by), listLine("Referencias", rule.references),
    el("p", { className: "muted" }, `Impacto ${rule.impact}/5 · dificultad ${rule.difficulty}/5`),
    actions.length ? el("div", { className: "actions" }, ...actions) : "");
}

function releasesTable(items) {
  const canActivate = has("validator", "admin");
  return el("table", {},
    el("thead", {}, el("tr", {}, ...["Versión", "Fecha", "Cambios", "Estado", ""].map((h) => el("th", {}, h)))),
    el("tbody", {}, ...items.map((r) => el("tr", {},
      el("td", {}, r.version + (r.active ? " (activa)" : "")),
      el("td", {}, new Date(r.created_at).toLocaleString()),
      el("td", {}, r.changelog || "—"),
      el("td", {}, r.status === "invalid" ? `Rechazada por el worker: ${r.invalid_reason || ""}` : "Válida"),
      el("td", {}, canActivate && !r.active && r.status === "valid"
        ? button("Reactivar (rollback)", () => activate(r), "secondary") : "")))));
}

async function activate(release) {
  if (!confirm(`¿Volver a la versión ${release.version}? Los próximos análisis usarán esa versión.`)) return;
  await act(() => api(`/v1/kb/releases/${release.id}/activate`, { method: "POST" }), `Versión ${release.version} activa.`);
  showKnowledge();
}

// ---------- Editor de propuestas (experto) ----------

function emptyRule() {
  return {
    id: "", title: "", category: "fundamental", polarity: "error", impact: 3, difficulty: 2,
    when: { all: [{ metric: "", op: "lt", value: 0 }] }, explanation: "", correction: "",
  };
}

// openEditor abre una propuesta nueva (con un primer cambio) o continúa una existente.
async function openEditor({ rule, removeId, proposal }) {
  await loadCatalog();
  if (proposal) {
    state.draft = { id: proposal.id, stroke: proposal.stroke, title: proposal.title, rationale: proposal.rationale,
      evidence: proposal.evidence, changes: structuredClone(proposal.changes) };
    state.stroke = proposal.stroke;
  } else {
    state.draft = { id: null, stroke: state.stroke, title: "", rationale: "", evidence: "", changes: [] };
    if (removeId) state.draft.changes.push({ op: "remove_rule", rule_id: removeId });
  }
  renderEditor(rule || null);
}

function renderEditor(editingRule) {
  const d = state.draft;
  const title = input(d.title, "Título de la propuesta");
  const rationale = textarea(d.rationale, "Por qué este cambio (el validador decide con esto)");
  const evidence = textarea(d.evidence, "Evidencia: referencias, observaciones, casos revisados");
  const sync = () => Object.assign(d, { title: title.value, rationale: rationale.value, evidence: evidence.value });
  const ruleBox = el("div", {});
  const showRuleForm = (rule, index) => ruleBox.replaceChildren(ruleForm(rule, (built) => {
    sync();
    const target = built.id;
    const existing = d.changes.findIndex((c) => (c.rule?.id || c.rule_id) === target);
    const change = { op: "upsert_rule", rule: built };
    if (index !== undefined) d.changes[index] = change;
    else if (existing >= 0) d.changes[existing] = change;
    else d.changes.push(change);
    renderEditor(null);
  }));
  const changes = el("ol", {}, ...d.changes.map((c, i) => el("li", {},
    c.op === "remove_rule" ? `Retirar ${c.rule_id}` : `Crear/editar ${c.rule.id} · ${c.rule.title}`,
    " ", c.op === "upsert_rule" ? button("Editar", () => { sync(); showRuleForm(structuredClone(c.rule), i); }, "link") : "",
    " ", button("Quitar", () => { sync(); d.changes.splice(i, 1); renderEditor(null); }, "link"))));
  $("#view").replaceChildren(
    el("h2", {}, d.id ? "Editar propuesta" : `Nueva propuesta · ${STROKES[d.stroke]}`),
    el("div", { className: "card form" },
      label("Título", title), label("Motivo", rationale), label("Evidencia (opcional)", evidence),
      el("h3", {}, "Cambios incluidos"),
      d.changes.length ? changes : el("p", { className: "muted" }, "Aún no hay cambios. Añade al menos uno."),
      button("Añadir otra regla nueva", () => { sync(); showRuleForm(emptyRule()); }, "secondary")),
    ruleBox,
    el("div", { className: "actions" },
      button("Guardar borrador", () => { sync(); saveDraft(false); }, "secondary"),
      button("Enviar a validación", () => { sync(); saveDraft(true); }),
      button("Cancelar", () => select("mine", showMyProposals), "link")),
  );
  if (editingRule) showRuleForm(editingRule);
}

function ruleForm(rule, onAdd) {
  const metrics = state.catalog.filter((m) => m.stroke === state.draft.stroke);
  const f = {
    id: input(rule.id, "FH-PREP-002"), title: input(rule.title, "Título visible para el jugador"),
    category: selectOf(CATEGORIES, rule.category), polarity: selectOf({ error: "Error a corregir", strength: "Punto fuerte" }, rule.polarity || "error"),
    impact: number(rule.impact, 1, 5), difficulty: number(rule.difficulty, 1, 5),
    combinator: selectOf({ all: "Se cumplen TODAS", any: "Se cumple ALGUNA" }, rule.when.any ? "any" : "all"),
    explanation: textarea(rule.explanation, "Qué observa el sistema, en lenguaje de jugador"),
    correction: textarea(rule.correction || "", "Cómo corregirlo (errores)"),
    why: textarea(rule.why_it_matters || "", "Por qué importa (puntos fuertes)"),
    maintain: textarea(rule.how_to_maintain || "", "Cómo aprovecharlo (puntos fuertes, opcional)"),
    drills: textarea((rule.drills || []).join("\n"), "Un ejercicio por línea"),
    causedBy: input((rule.caused_by || []).join(", "), "IDs de errores que lo explican, separados por comas"),
    suppressedBy: input((rule.suppressed_by || []).join(", "), "IDs de errores que impiden elogiarlo"),
    references: textarea((rule.references || []).join("\n"), "Una referencia por línea (p. ej. PMID 24149800)"),
  };
  f.id.readOnly = rule.id !== ""; // editar una regla existente no cambia su id (cambiarlo crearía otra)
  const condBox = el("div", {});
  let conditions = structuredClone(rule.when.all || rule.when.any || []);
  const renderConds = () => condBox.replaceChildren(...conditions.map((c, i) => conditionRow(c, metrics, () => {
    conditions.splice(i, 1);
    renderConds();
  })), button("Añadir condición", () => { conditions.push({ metric: "", op: "lt", value: 0 }); renderConds(); }, "link"));
  renderConds();
  const previewOut = el("p", { className: "muted" });
  const build = () => {
    const r = {
      id: f.id.value.trim().toUpperCase(), title: f.title.value.trim(), category: f.category.value, polarity: f.polarity.value,
      impact: Number(f.impact.value), difficulty: Number(f.difficulty.value),
      when: { [f.combinator.value]: conditions.map((c) => ({ ...c })) }, explanation: f.explanation.value.trim(),
    };
    if (r.polarity === "error") r.correction = f.correction.value.trim();
    else {
      r.why_it_matters = f.why.value.trim();
      if (f.maintain.value.trim()) r.how_to_maintain = f.maintain.value.trim();
    }
    const lines = (t) => t.split("\n").map((s) => s.trim()).filter(Boolean);
    const ids = (t) => t.split(",").map((s) => s.trim().toUpperCase()).filter(Boolean);
    if (lines(f.drills.value).length) r.drills = lines(f.drills.value);
    if (lines(f.references.value).length) r.references = lines(f.references.value);
    if (r.polarity === "error" && ids(f.causedBy.value).length) r.caused_by = ids(f.causedBy.value);
    if (r.polarity === "strength" && ids(f.suppressedBy.value).length) r.suppressed_by = ids(f.suppressedBy.value);
    return r;
  };
  const preview = async () => {
    try {
      const res = await api("/v1/kb/preview", { method: "POST", body: { stroke: state.draft.stroke, rule: build() } });
      const fmt = (c) => (c ? `se dispara en ${c.fired} de ${c.measurable} análisis medibles` : "—");
      previewOut.textContent = `Sobre los últimos ${res.samples} análisis (con permiso de revisión): ahora ${fmt(res.impact.before)}; con tu cambio ${fmt(res.impact.after)}.`;
    } catch (err) {
      previewOut.textContent = err.message;
    }
  };
  return el("div", { className: "card form" },
    el("h3", {}, "Regla"),
    el("div", { className: "fields" }, label("ID", f.id), label("Tipo", f.polarity), label("Categoría", f.category),
      label("Impacto (1-5)", f.impact), label("Dificultad (1-5)", f.difficulty)),
    label("Título", f.title),
    el("h4", {}, "Cuándo se dispara"), label("Combinación", f.combinator), condBox,
    label("Explicación", f.explanation), label("Corrección (si es un error)", f.correction),
    label("Por qué importa (si es un punto fuerte)", f.why), label("Cómo aprovecharlo", f.maintain),
    label("Ejercicios", f.drills), label("Causado por", f.causedBy), label("No elogiar si aparece", f.suppressedBy),
    label("Referencias", f.references),
    el("div", { className: "actions" }, button("Previsualizar impacto", preview, "secondary"),
      button("Añadir a la propuesta", () => onAdd(build()))),
    previewOut);
}

function conditionRow(c, metrics, onRemove) {
  const metric = el("select", {}, el("option", { value: "" }, "— métrica —"),
    ...metrics.map((m) => el("option", { value: m.id, selected: m.id === c.metric }, `${m.label} (${m.unit})`)));
  const op = selectOf(OPS, c.op);
  const isRange = Array.isArray(c.value);
  const v1 = number(isRange ? c.value[0] : c.value);
  const v2 = number(isRange ? c.value[1] : 0);
  const sync = () => {
    c.metric = metric.value;
    c.op = op.value;
    v2.hidden = op.value !== "between";
    c.value = op.value === "between" ? [Number(v1.value), Number(v2.value)] : Number(v1.value);
  };
  [metric, op, v1, v2].forEach((x) => (x.oninput = sync));
  sync();
  return el("div", { className: "condition" }, metric, op, v1, v2, button("✕", onRemove, "link"));
}

async function saveDraft(submit) {
  const d = state.draft;
  const body = { stroke: d.stroke, title: d.title, rationale: d.rationale, evidence: d.evidence, changes: d.changes };
  try {
    const saved = d.id
      ? await api(`/v1/expert/proposals/${d.id}`, { method: "PUT", body })
      : await api("/v1/expert/proposals", { method: "POST", body });
    d.id = saved.id;
    if (submit) await api(`/v1/expert/proposals/${d.id}/submit`, { method: "POST" });
    await select("mine", showMyProposals);
    flash(submit ? "Propuesta enviada a validación." : "Borrador guardado.");
  } catch (err) {
    flash(err.message, true);
  }
}

// ---------- Listados y detalle de propuestas ----------

async function showMyProposals() {
  const page = await api("/v1/kb/proposals?mine=true");
  $("#view").replaceChildren(
    el("div", { className: "actions" }, button("Nueva propuesta", () => openEditor({ rule: emptyRule() }))),
    proposalList(page.items, "Todavía no has hecho propuestas."));
}

async function showReviewInbox() {
  const page = await api("/v1/kb/proposals?status=submitted");
  $("#view").replaceChildren(el("h2", {}, "Pendientes de validación"),
    proposalList(page.items, "No hay propuestas pendientes."));
}

function proposalList(items, emptyText) {
  if (!items.length) return el("p", { className: "card muted" }, emptyText);
  return el("ul", { className: "plain" }, ...items.map((p) => el("li", { className: "card" },
    button(`${p.title}`, () => showProposal(p.id), "link"),
    el("p", { className: "muted" }, `${STROKES[p.stroke]} · ${STATUS[p.status]} · ${p.author_name} · `,
      new Date(p.updated_at).toLocaleString()),
    p.decision_note ? el("p", {}, el("strong", {}, "Nota del validador: "), p.decision_note) : "")));
}

async function showProposal(id) {
  const d = await api(`/v1/kb/proposals/${id}`);
  const p = d.proposal;
  const mine = p.author_id === state.me.id;
  const current = Object.fromEntries((d.active_release?.knowledge_base.rules || []).map((r) => [r.id, r]));
  const impacts = Object.fromEntries(d.impacts.map((i) => [i.rule_id, i]));
  const blocks = [
    el("h2", {}, p.title),
    el("p", { className: "muted" }, `${STROKES[p.stroke]} · ${STATUS[p.status]} · propuesta por ${p.author_name}`,
      d.active_release ? ` · versión activa ${d.active_release.version}` : ""),
    d.problems ? el("p", { className: "warning" }, `No se puede aplicar tal cual: ${d.problems}`) : "",
    d.stale ? el("p", { className: "warning" }, "La base cambió desde que se envió: el experto debe revisarla y reenviarla.") : "",
    el("div", { className: "card" }, el("h3", {}, "Motivo"), el("p", { className: "pre" }, p.rationale),
      p.evidence ? el("div", {}, el("h3", {}, "Evidencia"), el("p", { className: "pre" }, p.evidence)) : ""),
    el("h3", {}, "Cambios"),
    ...p.changes.map((c) => changeDiff(c, current, impacts)),
  ];
  if (p.decision_note) blocks.push(el("p", { className: "card" }, el("strong", {}, `Decisión (${p.decider_name}): `), p.decision_note));
  blocks.push(commentsBox(p, d.comments));
  if (mine && (p.status === "draft" || p.status === "changes_requested")) {
    blocks.push(el("div", { className: "actions" }, button("Editar", () => openEditor({ proposal: p }))));
  }
  if (mine && ["draft", "submitted", "changes_requested"].includes(p.status)) {
    blocks.push(button("Retirar propuesta", async () => {
      await act(() => api(`/v1/expert/proposals/${p.id}/withdraw`, { method: "POST" }), "Propuesta retirada.");
      showProposal(p.id);
    }, "danger"));
  }
  if (has("validator") && p.status === "submitted") blocks.push(decisionBox(p, mine));
  $("#view").replaceChildren(...blocks);
}

function changeDiff(c, current, impacts) {
  const id = c.rule?.id || c.rule_id;
  const before = current[id];
  const impact = impacts[id];
  const count = (x) => (x ? `${x.fired}/${x.measurable}` : "—");
  return el("div", { className: "card" },
    el("strong", {}, c.op === "remove_rule" ? `Retirar ${id}` : before ? `Editar ${id}` : `Nueva regla ${id}`),
    impact ? el("p", { className: "muted" }, `Se dispara en análisis recientes: antes ${count(impact.before)} → después ${count(impact.after)}`) : "",
    el("div", { className: "diff" },
      el("div", {}, el("h4", {}, "Actual"), el("pre", {}, before ? JSON.stringify(before, null, 2) : "(no existe)")),
      el("div", {}, el("h4", {}, "Propuesto"), el("pre", {}, c.rule ? JSON.stringify(c.rule, null, 2) : "(se retira)"))));
}

function commentsBox(p, comments) {
  const body = textarea("", "Escribe un comentario");
  return el("div", { className: "card" }, el("h3", {}, "Conversación"),
    ...comments.map((c) => el("p", {}, el("strong", {}, `${c.author_name}: `), c.body,
      el("span", { className: "muted" }, ` · ${new Date(c.created_at).toLocaleString()}`))),
    body, button("Comentar", async () => {
      await act(() => api(`/v1/kb/proposals/${p.id}/comments`, { method: "POST", body: { body: body.value } }), "");
      showProposal(p.id);
    }, "secondary"));
}

function decisionBox(p, mine) {
  if (mine) return el("p", { className: "warning" }, "Es tu propia propuesta: otro validador debe decidirla.");
  const note = textarea("", "Nota para el experto (obligatoria al rechazar o pedir cambios)");
  const decide = (action, okText) => async () => {
    await act(() => api(`/v1/validator/proposals/${p.id}/${action}`, { method: "POST", body: { note: note.value } }), okText);
    showProposal(p.id);
  };
  return el("div", { className: "card" }, el("h3", {}, "Decisión"), note,
    el("div", { className: "actions" },
      button("Aprobar y publicar", decide("approve", "Aprobada: se publicó una versión nueva y ya la usan los análisis.")),
      button("Pedir cambios", decide("request-changes", "Se pidieron cambios al experto."), "secondary"),
      button("Rechazar", decide("reject", "Propuesta rechazada."), "danger")));
}

// ---------- Solicitudes de métricas ----------

async function showMetricRequests() {
  const page = await api("/v1/kb/metric-requests");
  const blocks = [el("p", { className: "muted" },
    "Las reglas solo pueden usar métricas que el sistema ya sabe medir. Si necesitas otra, pídela aquí: la implementa ingeniería.")];
  if (has("expert")) {
    const stroke = selectOf(STROKES, state.stroke);
    const name = input("", "p. ej. Estabilidad de la cabeza en el impacto");
    const description = textarea("", "Qué mediría, en qué fase y por qué importa");
    blocks.push(el("div", { className: "card form" }, label("Golpe", stroke), label("Nombre", name), label("Descripción", description),
      button("Enviar solicitud", async () => {
        await act(() => api("/v1/expert/metric-requests", { method: "POST",
          body: { stroke: stroke.value, name: name.value, description: description.value } }), "Solicitud enviada.");
        showMetricRequests();
      })));
  }
  blocks.push(el("ul", { className: "plain" }, ...page.items.map((m) => el("li", { className: "card" },
    el("strong", {}, `${m.name} · ${STROKES[m.stroke]}`), el("p", {}, m.description),
    el("p", { className: "muted" }, `${m.author_name} · ${m.status} · ${new Date(m.created_at).toLocaleDateString()}`)))));
  $("#view").replaceChildren(...blocks);
}

// ---------- Administración ----------

async function showAdmin() {
  const [users, invitations, audit] = await Promise.all([api("/v1/admin/users"), api("/v1/admin/invitations"), api("/v1/admin/audit")]);
  const email = input("", "correo@ejemplo.com");
  const roleChecks = roleCheckboxes([]);
  $("#view").replaceChildren(
    el("h2", {}, "Invitar a un profesional"),
    el("div", { className: "card form" },
      el("p", { className: "muted" }, "La persona recibe los roles al iniciar sesión con ese email verificado. Si ya tiene cuenta, se aplican al momento."),
      label("Email", email), roleChecks.node,
      button("Invitar", async () => {
        const res = await act(() => api("/v1/admin/invitations", { method: "POST", body: { email: email.value, roles: roleChecks.value() } }));
        if (res) flash(res.applied_to_existing_user ? "Roles aplicados a la cuenta existente." : "Invitación creada: comparte el enlace de la aplicación con esa persona.");
        showAdmin();
      })),
    el("h2", {}, "Usuarios"), usersTable(users.items),
    el("h2", {}, "Invitaciones"), invitationsTable(invitations.items),
    el("h2", {}, "Auditoría"), auditTable(audit.items));
}

function roleCheckboxes(selected) {
  const boxes = Object.entries(ROLES).map(([value, text]) => {
    const box = el("input", { type: "checkbox", value, checked: selected.includes(value) });
    return [box, el("label", { className: "check inline" }, box, text)];
  });
  return { node: el("div", { className: "roles" }, ...boxes.map((b) => b[1])), value: () => boxes.filter((b) => b[0].checked).map((b) => b[0].value) };
}

function usersTable(users) {
  return el("table", {},
    el("thead", {}, el("tr", {}, ...["Email", "Roles", "Estado", ""].map((h) => el("th", {}, h)))),
    el("tbody", {}, ...users.map((u) => {
      const roles = roleCheckboxes(u.roles);
      const status = selectOf({ active: "Activa", suspended: "Suspendida" }, u.status);
      const self = u.id === state.me.id;
      status.disabled = self;
      status.onchange = async () => {
        await act(() => api(`/v1/admin/users/${u.id}/status`, { method: "PUT", body: { status: status.value } }), "Estado actualizado.");
      };
      return el("tr", {},
        el("td", {}, u.email || "(sin email)", u.email_verified ? "" : el("span", { className: "muted" }, " · sin verificar")),
        el("td", {}, roles.node),
        el("td", {}, status),
        el("td", {}, button("Guardar roles", () =>
          act(() => api(`/v1/admin/users/${u.id}/roles`, { method: "PUT", body: { roles: roles.value() } }), "Roles actualizados."), "secondary")));
    })));
}

function invitationsTable(items) {
  return el("table", {},
    el("thead", {}, el("tr", {}, ...["Email", "Roles", "Estado", "Caduca", ""].map((h) => el("th", {}, h)))),
    el("tbody", {}, ...items.map((i) => el("tr", {},
      el("td", {}, i.email), el("td", {}, i.roles.map((r) => ROLES[r]).join(", ")), el("td", {}, i.status),
      el("td", {}, new Date(i.expires_at).toLocaleDateString()),
      el("td", {}, i.status === "pending" ? button("Revocar", async () => {
        await act(() => api(`/v1/admin/invitations/${i.id}`, { method: "DELETE" }), "Invitación revocada.");
        showAdmin();
      }, "danger") : "")))));
}

function auditTable(items) {
  return el("table", {},
    el("thead", {}, el("tr", {}, ...["Fecha", "Acción", "Objeto", "Detalle"].map((h) => el("th", {}, h)))),
    el("tbody", {}, ...items.map((e) => el("tr", {},
      el("td", {}, new Date(e.created_at).toLocaleString()), el("td", {}, e.action), el("td", {}, e.target),
      el("td", { className: "mono" }, JSON.stringify(e.details))))));
}

// ---------- Perfil ----------

async function showProfile() {
  const p = state.me.profile || {};
  const name = input(p.display_name || "", "Nombre con el que firmas tus aportes");
  const specialty = selectOf(SPECIALTIES, p.specialty || "tennis_coach");
  const certifications = textarea(p.certifications || "", "Titulaciones (ITF, PTR, RPT, máster en biomecánica…)");
  const bio = textarea(p.bio || "", "Experiencia");
  $("#view").replaceChildren(el("div", { className: "card form" },
    el("p", {}, `Roles: ${state.me.roles.map((r) => ROLES[r]).join(", ")}`),
    label("Nombre", name), label("Especialidad", specialty), label("Certificaciones", certifications), label("Biografía", bio),
    button("Guardar perfil", async () => {
      const saved = await act(() => api("/v1/me/profile", { method: "PUT",
        body: { display_name: name.value, specialty: specialty.value, certifications: certifications.value, bio: bio.value } }), "Perfil guardado.");
      if (saved) state.me.profile = saved;
    })));
}

// ---------- utilidades ----------

async function loadCatalog() {
  if (state.catalog.length) return;
  state.catalog = (await api("/v1/kb/metrics")).items;
}

function metricLabel(id) {
  const m = state.catalog.find((x) => x.id === id && x.stroke === state.stroke) || state.catalog.find((x) => x.id === id);
  return m ? `${m.label} (${m.unit})` : id;
}

function conditionsText(rule) {
  const conds = rule.when.all || rule.when.any || [];
  const join = rule.when.all ? " y " : " o ";
  return conds.map((c) => Array.isArray(c.value)
    ? `${metricLabel(c.metric)} entre ${c.value[0]} y ${c.value[1]}`
    : `${metricLabel(c.metric)} ${OPS[c.op]} ${c.value}`).join(join);
}

async function api(url, { method = "GET", body } = {}) {
  const headers = new Headers();
  const token = await Auth.accessToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  else if (Auth.enabled()) throw new Error("Tu sesión ha caducado. Recarga la página e inicia sesión.");
  if (body !== undefined) headers.set("Content-Type", "application/json");
  const res = await fetch(url, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  if (res.status === 204) return null;
  const payload = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(payload.error?.message || `Error ${res.status}`);
  return payload;
}

// act ejecuta una acción mostrando el resultado o el error; devuelve el resultado o undefined.
async function act(fn, okText) {
  try {
    const res = await fn();
    if (okText) flash(okText);
    return res ?? true;
  } catch (err) {
    flash(err.message, true);
    return undefined;
  }
}

function flash(text, error = false) {
  const node = $("#flash");
  node.textContent = text;
  node.className = error ? "error-text" : "ok-text";
}

function button(text, onclick, kind = "") {
  const b = el("button", { type: "button", className: kind }, text);
  b.onclick = onclick;
  return b;
}

const input = (value, placeholder) => el("input", { type: "text", value: value ?? "", placeholder });
const textarea = (value, placeholder) => el("textarea", { value: value ?? "", placeholder, rows: 3 });
const number = (value, min, max) => el("input", { type: "number", value: value ?? 0, step: "any", ...(min ? { min, max } : {}) });
const label = (text, control) => el("label", {}, text, control);
const tag = (text, kind) => el("span", { className: `tag ${kind}` }, text);
const listLine = (title, items) => (items?.length ? el("p", { className: "muted" }, `${title}: ${items.join(" · ")}`) : "");

function selectOf(options, current) {
  return el("select", {}, ...Object.entries(options).map(([v, l]) => el("option", { value: v, selected: v === current }, l)));
}

// Construye nodos con textContent: nunca se inyecta HTML del servidor (evita XSS).
function el(tag, props, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (k.startsWith("aria-")) node.setAttribute(k, v);
    else node[k] = v;
  }
  node.append(...children.filter((c) => c !== "" && c !== null && c !== undefined));
  return node;
}
