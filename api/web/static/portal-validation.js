"use strict";

// Validación formal de las reglas (se carga después de portal.js y usa sus utilidades: api, el,
// button, flash, state…). Revisión de casos, campañas Delphi ciegas, calibración contra el ground
// truth de consenso y KPIs de calidad. Nada de esto cambia el análisis de ningún usuario.

const VSTATUS = {
  heuristic_unvalidated: ["Heurística sin validar", "provisional"],
  expert_reviewed: ["Revisada por experto", "validated"],
  empirically_validated: ["Validada con datos", "empirical"],
};
const VERDICTS = { correct: "Acertó", false_positive: "Falso positivo", missed: "Se le escapó", unsure: "No sé" };
const LEVELS = { beginner: "Principiante", intermediate: "Intermedio", advanced: "Avanzado", pro: "Profesional" };
const BANDS = { reliable: "fiable", tentative: "tentativo", unusable: "no usable" };
const EVIDENCE_LEVELS = { "": "—", provisional: "Provisional", literature: "Literatura", expert_validated: "Validada por expertos" };
const SCALE = { 1: "1 · muy deficiente", 2: "2 · deficiente", 3: "3 · algo deficiente", 4: "4 · aceptable",
  5: "5 · bueno", 6: "6 · muy bueno", 7: "7 · excelente" };

function ruleStatus(rule) {
  return rule.validation_status || (rule.validated ? "expert_reviewed" : "heuristic_unvalidated");
}

function statusTag(status) {
  const [text, kind] = VSTATUS[status] || VSTATUS.heuristic_unvalidated;
  return tag(text, kind);
}

const pct = (x) => (x === null || x === undefined ? "—" : `${Math.round(x * 100)}%`);
const rateText = (r) => (r ? `${pct(r.value)} [${pct(r.low)}–${pct(r.high)}] n=${r.n}` : "—");
const alphaText = (a, band) => (a === null || a === undefined ? "—" : `${a.toFixed(2)}${band ? ` (${BANDS[band] || band})` : ""}`);

// Escala 1-7 del aspecto que evalúa la regla, o «no evaluable» con esta grabación.
function ratingSelect(item) {
  const current = item?.not_evaluable ? "ne" : item?.rating ? String(item.rating) : "";
  const s = el("select", { "aria-label": "Calificación" }, el("option", { value: "" }, "— sin calificar —"),
    ...Object.entries(SCALE).map(([v, l]) => el("option", { value: v, selected: v === current }, l)),
    el("option", { value: "ne", selected: current === "ne" }, "No evaluable"));
  return s;
}

function readRating(select) {
  if (select.value === "") return null;
  if (select.value === "ne") return { rating: null, not_evaluable: true };
  return { rating: Number(select.value), not_evaluable: false };
}

// ---------- Revisión libre dentro de un caso ----------

// reviewPanel: el experto califica cada regla (1-7) y dice si el sistema acertó. Ve el agregado
// anónimo de los demás. Es control de calidad: el análisis del jugador no cambia.
async function reviewPanel(caseId, report, stroke) {
  const [reviews, release] = await Promise.all([
    api(`/v1/kb/cases/${caseId}/reviews`),
    api(`/v1/kb/strokes/${stroke}`).catch(() => null),
  ]);
  const rules = release?.knowledge_base.rules || [];
  const fired = new Set([...(report.findings || []), ...(report.strengths || [])].map((f) => f.rule_id));
  const mine = Object.fromEntries((reviews.mine?.items || []).map((i) => [i.rule_id, i]));
  const others = Object.fromEntries(reviews.rules.map((r) => [r.rule_id, r]));
  const canReview = has("expert", "validator");
  const rows = [];
  const row = (rule) => {
    const rating = ratingSelect(mine[rule.id]);
    const verdict = selectOf({ "": "— veredicto —", ...VERDICTS }, mine[rule.id]?.verdict || "");
    const comment = input(mine[rule.id]?.comment || "", "Comentario (opcional)");
    [rating, verdict, comment].forEach((x) => (x.disabled = !canReview));
    rows.push({ rule, rating, verdict, comment });
    const o = others[rule.id];
    const agg = o && o.ratings.n
      ? `Equipo: mediana ${o.ratings.median} (n=${o.ratings.n})` +
        Object.entries(o.verdicts).map(([v, n]) => ` · ${VERDICTS[v]} ${n}`).join("")
      : "Sin revisiones del equipo.";
    return el("tr", {},
      el("td", {}, el("strong", {}, rule.id), ` ${rule.title}`, el("div", { className: "muted" }, agg)),
      el("td", {}, fired.has(rule.id) ? tag("Disparó", "provisional") : el("span", { className: "muted" }, "No disparó")),
      el("td", {}, rating), el("td", {}, verdict), el("td", {}, comment));
  };
  const head = el("thead", {}, el("tr", {}, ...["Regla", "Sistema", "Tu calificación del aspecto", "¿Acertó?", ""].map((h) => el("th", {}, h))));
  const firedRules = rules.filter((r) => fired.has(r.id));
  const rest = rules.filter((r) => !fired.has(r.id));
  const level = selectOf({ "": "— nivel del jugador —", ...LEVELS }, reviews.mine?.player_level || "");
  const notes = textarea(reviews.mine?.notes || "", "Notas generales del caso");
  level.disabled = notes.disabled = !canReview;
  const save = async () => {
    const items = rows.map(({ rule, rating, verdict, comment }) => {
      const r = readRating(rating);
      if (!r) return null;
      return { rule_id: rule.id, ...r, verdict: verdict.value || null, comment: comment.value.trim() };
    }).filter(Boolean);
    if (!items.length) {
      flash("Califica al menos una regla.", true);
      return;
    }
    await act(() => api(`/v1/kb/cases/${caseId}/review`, { method: "PUT",
      body: { player_level: level.value || null, notes: notes.value, items } }), "Revisión guardada. Gracias.");
  };
  const actions = [];
  if (canReview) actions.push(button("Guardar mi revisión", save));
  if (has("validator", "admin")) {
    actions.push(button("Agregar al corpus de validación", () =>
      act(() => api("/v1/kb/corpus", { method: "POST", body: { analysis_id: caseId, note: "" } }),
        "Caso agregado al corpus."), "secondary"));
  }
  return el("div", { className: "card" },
    el("h2", {}, "Revisión experta"),
    el("p", { className: "muted" },
      "Califica de 1 (muy deficiente) a 7 (excelente) el aspecto que evalúa cada regla, o marca «no evaluable» si la grabación no lo permite. " +
      "Después indica si el sistema acertó. " + (reviews.others ? `Otros ${reviews.others} expertos ya revisaron este caso.` : "")),
    el("div", { className: "fields" }, label("Nivel del jugador", level)),
    el("table", {}, head, el("tbody", {}, ...firedRules.map(row))),
    rest.length ? el("details", {}, el("summary", {}, `Reglas que no dispararon (${rest.length}): ¿se le escapó alguna?`),
      el("table", {}, head.cloneNode(true), el("tbody", {}, ...rest.map(row)))) : "",
    label("Notas", notes),
    el("div", { className: "actions" }, ...actions));
}

// ---------- Campañas Delphi y corpus ----------

async function showCampaigns() {
  const coordinator = has("validator", "admin");
  const [campaigns, corpus] = await Promise.all([
    api(`/v1/kb/campaigns?stroke=${state.stroke}`),
    api(`/v1/kb/corpus?stroke=${state.stroke}`),
  ]);
  const blocks = [
    strokePicker(() => showCampaigns()),
    el("div", { className: "card" }, el("p", { className: "muted" },
      "Una campaña es una calificación ciega por varios expertos de los mismos casos del corpus, en rondas (método Delphi). " +
      "En cada ronda los evaluadores ven solo el video y el esqueleto; a partir de la segunda, también la distribución anónima del grupo. " +
      "Al cerrar, la mediana de consenso de cada caso pasa a ser el «ground truth» con el que se calibran y validan las reglas.")),
    el("h2", {}, "Campañas"),
    campaigns.items.length ? campaignsTable(campaigns.items) : el("p", { className: "card muted" }, "Todavía no hay campañas para este golpe."),
  ];
  if (coordinator) blocks.push(el("h2", {}, "Nueva campaña"), await campaignForm(corpus.items));
  blocks.push(el("h2", {}, `Corpus de validación (${corpus.items.length})`), corpusTable(corpus.items, coordinator));
  $("#view").replaceChildren(...blocks);
}

function campaignsTable(items) {
  return el("table", {},
    el("thead", {}, el("tr", {}, ...["Campaña", "Estado", "Ronda", "Casos", "Evaluadores", ""].map((h) => el("th", {}, h)))),
    el("tbody", {}, ...items.map((c) => el("tr", {},
      el("td", {}, c.title), el("td", {}, c.status === "open" ? "Abierta" : "Cerrada"),
      el("td", {}, `${c.current_round} de ${c.max_rounds}`), el("td", {}, String(c.cases)), el("td", {}, String(c.raters)),
      el("td", {}, button("Abrir", () => showCampaign(c.id).catch((e) => flash(e.message, true)), "secondary"))))));
}

function corpusTable(items, coordinator) {
  if (!items.length) {
    return el("p", { className: "card muted" }, "Vacío. Agrega casos desde la pestaña «Casos» (botón «Agregar al corpus»).");
  }
  return el("table", {},
    el("thead", {}, el("tr", {}, ...["Caso", "Conjunto", "Reglas con ground truth", "Agregado", ""].map((h) => el("th", {}, h)))),
    el("tbody", {}, ...items.map((e) => el("tr", {},
      el("td", {}, button(e.analysis_id.slice(0, 8), () => showCase(e.analysis_id).catch((x) => flash(x.message, true)), "link")),
      el("td", {}, e.split === "test" ? "Test (medir)" : "Calibración (ajustar)"),
      el("td", {}, String(e.ground_truth_rules)),
      el("td", {}, new Date(e.added_at).toLocaleDateString()),
      el("td", {}, coordinator ? button("Quitar", async () => {
        if (!confirm("¿Quitar este caso del corpus?")) return;
        await act(() => api(`/v1/kb/corpus/${e.analysis_id}`, { method: "DELETE" }), "Caso quitado del corpus.");
        showCampaigns();
      }, "danger") : "")))));
}

async function campaignForm(corpus) {
  const [release, pros] = await Promise.all([
    api(`/v1/kb/strokes/${state.stroke}`).catch(() => null),
    api("/v1/kb/professionals"),
  ]);
  if (!release) return el("p", { className: "card muted" }, "Este golpe no tiene base de conocimiento publicada.");
  const checks = (items, text, checked) => items.map((it) => {
    const box = el("input", { type: "checkbox", value: it.id, checked });
    return { box, node: el("label", { className: "check" }, box, ` ${text(it)}`) };
  });
  const rules = checks(release.knowledge_base.rules, (r) => `${r.id} · ${r.title}`, true);
  const cases = checks(corpus.map((e) => ({ id: e.analysis_id, split: e.split })),
    (e) => `${e.id.slice(0, 8)} (${e.split === "test" ? "test" : "calibración"})`, true);
  const raters = checks(pros.items, (p) => `${p.name} · ${p.roles.map((r) => ROLES[r]).join(", ")}`, false);
  const title = input("", "p. ej. Forehand · piloto noviembre");
  const consensus = number(70, 51, 100);
  const minRaters = number(3, 2, 50);
  const rounds = number(3, 1, 5);
  const picked = (list) => list.filter((x) => x.box.checked).map((x) => x.box.value);
  const create = async () => {
    const res = await act(() => api("/v1/kb/campaigns", { method: "POST", body: {
      stroke: state.stroke, title: title.value, rule_ids: picked(rules), case_ids: picked(cases), rater_ids: picked(raters),
      consensus_pct: Number(consensus.value) / 100, min_raters: Number(minRaters.value), max_rounds: Number(rounds.value),
    } }), "Campaña creada: los evaluadores ya pueden calificar.");
    if (res) showCampaign(res.id);
  };
  return el("div", { className: "card form" },
    label("Título", title),
    el("p", { className: "muted" },
      "Consenso definido de antemano: un caso tiene consenso si al menos el % indicado de las calificaciones cae a ±1 de la mediana, con un mínimo de evaluadores."),
    el("div", { className: "fields" }, label("Consenso (%)", consensus), label("Mínimo de evaluadores", minRaters), label("Rondas máximas", rounds)),
    el("details", { open: true }, el("summary", {}, "Evaluadores (expertos o validadores; recomendado 3-5)"), ...raters.map((x) => x.node)),
    el("details", {}, el("summary", {}, `Casos del corpus (${cases.length})`), ...cases.map((x) => x.node)),
    el("details", {}, el("summary", {}, `Reglas a calificar (${rules.length})`), ...rules.map((x) => x.node)),
    button("Crear campaña", create));
}

async function showCampaign(id) {
  closeViewer();
  const d = await api(`/v1/kb/campaigns/${id}`);
  const c = d.campaign;
  const coordinator = has("validator", "admin");
  const blocks = [
    el("div", { className: "actions" }, button("← Campañas", () => showCampaigns().catch((e) => flash(e.message, true)), "secondary")),
    el("h2", {}, c.title),
    el("p", { className: "muted" }, `${STROKES[c.stroke]} · ${c.status === "open" ? "abierta" : "cerrada"} · ronda ${c.current_round} de ${c.max_rounds} · ` +
      `consenso: ≥ ${Math.round(c.consensus_pct * 100)}% a ±1 de la mediana con ≥ ${c.min_raters} evaluadores`),
  ];
  if (d.progress.length) {
    blocks.push(el("h3", {}, "Progreso de la ronda actual"), el("table", {},
      el("thead", {}, el("tr", {}, el("th", {}, "Evaluador"), el("th", {}, "Casos calificados"))),
      el("tbody", {}, ...d.progress.map((p) => el("tr", {},
        el("td", {}, p.name || (p.is_me ? "Tú" : "—")), el("td", {}, `${p.done} de ${p.total}`))))));
  }
  for (const round of d.rounds) {
    blocks.push(el("h3", {}, `Acuerdo · ronda ${round.round}${round.round === c.current_round && c.status === "open" ? " (en curso)" : ""}`),
      agreementTable(round.rules));
  }
  if (d.is_rater && c.status === "open") {
    const done = new Set(d.my_done);
    blocks.push(el("h3", {}, "Tus casos"), el("ul", { className: "plain" }, ...c.case_ids.map((caseId, i) => el("li", {},
      button(`Caso ${i + 1}`, () => showCampaignCase(c.id, caseId).catch((e) => flash(e.message, true)), "link"),
      done.has(caseId) ? el("span", { className: "ok-text" }, " ✓ calificado") : el("span", { className: "muted" }, " pendiente")))));
  }
  if (coordinator && c.status === "open") {
    blocks.push(el("div", { className: "actions" },
      c.current_round < c.max_rounds ? button("Abrir la siguiente ronda", async () => {
        let res = await act(() => api(`/v1/kb/campaigns/${c.id}/advance`, { method: "POST", body: { force: false } }));
        if (!res && confirm("Faltan calificaciones. ¿Abrir la siguiente ronda igualmente?")) {
          res = await act(() => api(`/v1/kb/campaigns/${c.id}/advance`, { method: "POST", body: { force: true } }));
        }
        if (res) showCampaign(c.id);
      }, "secondary") : "",
      button("Cerrar y fijar el ground truth", async () => {
        if (!confirm("Al cerrar, las medianas de esta ronda pasan a ser el ground truth. ¿Continuar?")) return;
        if (await act(() => api(`/v1/kb/campaigns/${c.id}/close`, { method: "POST" }), "Campaña cerrada: ground truth actualizado.")) showCampaign(c.id);
      })));
  }
  $("#view").replaceChildren(...blocks);
}

function agreementTable(rules) {
  return el("table", {},
    el("thead", {}, el("tr", {}, ...["Regla", "α de Krippendorff (ordinal)", "Casos con ≥2 notas", "Casos con consenso", "No evaluable"].map((h) => el("th", {}, h)))),
    el("tbody", {}, ...rules.map((r) => el("tr", {},
      el("td", { className: "mono" }, r.rule_id),
      el("td", { className: r.band === "unusable" ? "error-text" : "" }, alphaText(r.alpha, r.band)),
      el("td", {}, String(r.units)), el("td", {}, pct(r.consensus_rate)), el("td", {}, String(r.not_evaluable))))));
}

// Vista ciega: solo video, esqueleto y fases. Ni hallazgos ni métricas del sistema (sesgo de anclaje).
async function showCampaignCase(campaignId, caseId) {
  closeViewer();
  const b = await api(`/v1/kb/campaigns/${campaignId}/cases/${caseId}`);
  const mine = Object.fromEntries((b.mine?.items || []).map((i) => [i.rule_id, i]));
  const feedback = Object.fromEntries(b.feedback.map((f) => [f.rule_id, f]));
  const box = el("div", {}, el("p", { className: "muted" }, "Cargando video y esqueleto…"));
  const rows = b.rules.map((rule) => {
    const rating = ratingSelect(mine[rule.id]);
    const comment = input(mine[rule.id]?.comment || "", "Comentario (opcional)");
    const f = feedback[rule.id];
    const fb = f ? el("div", { className: "muted" },
      f.group.n ? `Ronda anterior: mediana ${f.group.median} (Q1 ${f.group.q1} – Q3 ${f.group.q3}), ` +
        `distribución ${f.group.counts.map((n, i) => `${i + 1}:${n}`).join(" ")}` : "Ronda anterior: sin calificaciones.",
      f.my_rating ? ` · tu nota fue ${f.my_rating}` : f.my_not_evaluable ? " · la marcaste no evaluable" : "") : "";
    return { rule, rating, comment, node: el("tr", {},
      el("td", {}, el("strong", {}, rule.title), el("div", { className: "muted" }, rule.explanation), fb),
      el("td", {}, rating), el("td", {}, comment)) };
  });
  const save = async () => {
    const items = rows.map(({ rule, rating, comment }) => {
      const r = readRating(rating);
      return r ? { rule_id: rule.id, ...r, comment: comment.value.trim() } : null;
    }).filter(Boolean);
    if (items.length !== rows.length && !confirm("Hay aspectos sin calificar. ¿Guardar igualmente?")) return;
    if (await act(() => api(`/v1/kb/campaigns/${campaignId}/cases/${caseId}/review`, { method: "PUT", body: { items } }),
      "Calificación guardada.")) {
      const next = (await api(`/v1/kb/campaigns/${campaignId}`)).campaign.case_ids;
      const i = next.indexOf(caseId);
      if (i >= 0 && i + 1 < next.length) showCampaignCase(campaignId, next[i + 1]).catch((e) => flash(e.message, true));
      else showCampaign(campaignId);
    }
  };
  $("#view").replaceChildren(
    el("div", { className: "actions" }, button("← Campaña", () => showCampaign(campaignId).catch((e) => flash(e.message, true)), "secondary")),
    el("div", { className: "card" },
      el("p", {}, el("strong", {}, `${b.campaign.title} · ronda ${b.campaign.current_round}`),
        ` · ${STROKES[b.stroke_type] || ""} · vista ${VIEWS[b.camera_view] || b.camera_view} · ${b.handedness === "left" ? "zurdo" : "diestro"}`),
      el("p", { className: "muted" }, "Calificación ciega: no se muestra lo que dijo el sistema. Puntúa cada aspecto de 1 a 7 según lo que ves."),
      box),
    el("table", {}, el("thead", {}, el("tr", {}, ...["Aspecto", "Calificación", ""].map((h) => el("th", {}, h)))),
      el("tbody", {}, ...rows.map((r) => r.node))),
    el("div", { className: "actions" }, button("Guardar y seguir", save)));
  try {
    const pose = await SkeletonViewer.loadPose(b.pose_url);
    if (!box.isConnected) return;
    state.viewer = SkeletonViewer.create(box, { videoUrl: b.video_url, pose, segment: b.segment || {} });
  } catch (err) {
    box.replaceChildren(el("p", { className: "muted" }, `No se pudo cargar el video: ${err.message}`));
  }
}

// ---------- Calibración en el editor de reglas ----------

// calibrationBox: barre el umbral de una condición contra el ground truth (conjunto de calibración)
// y deja elegir el punto con un deslizador. Nunca usa los casos de test.
function calibrationBox(getRule, setThreshold) {
  const out = el("div", {});
  const run = async () => {
    const rule = getRule();
    const conds = rule.when.all || rule.when.any || [];
    const idx = conds.findIndex((c) => c.op !== "between" && c.metric);
    if (idx < 0) {
      out.replaceChildren(el("p", { className: "muted" }, "Añade una condición (que no sea «entre») para calibrar su umbral."));
      return;
    }
    const current = Number(conds[idx].value);
    const span = Math.max(Math.abs(current) * 0.25, 1);
    const sweep = { condition: idx, from: current - span, to: current + span, steps: 21 };
    let res;
    try {
      res = await api("/v1/kb/calibrate", { method: "POST", body: { stroke: state.draft.stroke, rule, split: "calibration", sweep } });
    } catch (err) {
      out.replaceChildren(el("p", { className: "error-text" }, err.message));
      return;
    }
    if (!res.labeled) {
      out.replaceChildren(el("p", { className: "muted" }, "Todavía no hay ground truth para esta regla: cierra una campaña que la incluya."));
      return;
    }
    const f1 = (p) => p.f1 ?? -1;
    const best = res.curve.reduce((a, p) => (f1(p) > f1(a) ? p : a), res.curve[0]);
    const slider = el("input", { type: "range", min: sweep.from, max: sweep.to, step: (sweep.to - sweep.from) / 100, value: current });
    const live = el("p", {});
    const show = (s, t) => live.replaceChildren(el("strong", {}, `${metricLabel(conds[idx].metric)} ${OPS[conds[idx].op]} ${Number(t).toFixed(2)}: `),
      `precisión ${rateText(s.precision)} · recall ${rateText(s.recall)} · F1 ${s.f1 === null ? "—" : s.f1.toFixed(2)} · ` +
      `VP ${s.confusion.tp} FP ${s.confusion.fp} FN ${s.confusion.fn} VN ${s.confusion.tn}`);
    show(res, current);
    let timer;
    slider.oninput = () => {
      clearTimeout(timer);
      timer = setTimeout(async () => {
        const t = Number(slider.value);
        const candidate = structuredClone(rule);
        (candidate.when.all || candidate.when.any)[idx].value = t;
        try {
          show(await api("/v1/kb/calibrate", { method: "POST", body: { stroke: state.draft.stroke, rule: candidate, split: "calibration" } }), t);
        } catch (err) {
          live.textContent = err.message;
        }
      }, 250);
    };
    out.replaceChildren(
      el("p", { className: "muted" }, `${res.labeled} casos de calibración con ground truth (${res.unmeasurable} sin métricas fiables). ` +
        `Mejor F1 con umbral ${best.threshold.toFixed(2)} (F1 ${best.f1 === null ? "—" : best.f1.toFixed(2)}).`),
      slider, live,
      el("div", { className: "actions" },
        button("Usar el umbral del deslizador", () => setThreshold(idx, Number(Number(slider.value).toFixed(2))), "secondary"),
        button(`Usar el mejor F1 (${best.threshold.toFixed(2)})`, () => setThreshold(idx, Number(best.threshold.toFixed(2))), "secondary")),
      el("details", {}, el("summary", {}, "Curva completa"), el("table", {},
        el("thead", {}, el("tr", {}, ...["Umbral", "Precisión", "Recall", "F1"].map((h) => el("th", {}, h)))),
        el("tbody", {}, ...res.curve.map((p) => el("tr", { className: p === best ? "ok-text" : "" },
          el("td", {}, p.threshold.toFixed(2)), el("td", {}, pct(p.precision?.value)), el("td", {}, pct(p.recall?.value)),
          el("td", {}, p.f1 === null ? "—" : p.f1.toFixed(2))))))));
  };
  return el("div", { className: "card" }, el("h4", {}, "Calibrar contra el ground truth de los expertos"),
    el("p", { className: "muted" }, "Usa solo los casos de calibración; los de test se reservan para validar sin sesgo."),
    button("Calibrar", run, "secondary"), out);
}

// evidenceLine: cómo acierta la regla propuesta en el conjunto de test (lo que mira el validador).
function evidenceLine(stroke, rule) {
  const p = el("p", { className: "muted" }, "Midiendo contra el ground truth de test…");
  api("/v1/kb/calibrate", { method: "POST", body: { stroke, rule, split: "test" } }).then((res) => {
    p.textContent = res.labeled
      ? `En test (${res.labeled} casos con ground truth, ${res.unmeasurable} sin medir): precisión ${rateText(res.precision)} · recall ${rateText(res.recall)}.`
      : "Sin ground truth de test para esta regla todavía.";
  }).catch((err) => (p.textContent = err.message));
  return p;
}

// ---------- Calidad de la base de conocimiento ----------

async function showQuality() {
  const view = $("#view");
  const picker = strokePicker(() => showQuality());
  let q;
  try {
    q = await api(`/v1/kb/quality?stroke=${state.stroke}`);
  } catch (err) {
    view.replaceChildren(picker, el("p", { className: "card" }, err.message));
    return;
  }
  const kpi = (title, value, note) => el("div", { className: "kpi" }, el("span", { className: "muted" }, title),
    el("strong", {}, value), note ? el("span", { className: "muted" }, note) : "");
  view.replaceChildren(
    picker,
    el("div", { className: "card" },
      el("p", {}, el("strong", {}, `${STROKES[q.stroke]} · versión ${q.version}`), ` · ${q.rules.length} reglas`),
      el("div", { className: "kpis" },
        kpi("Reglas validadas", `${Math.round(q.validated_pct)}%`, `${Math.round(q.empirical_pct)}% con datos`),
        kpi("Acuerdo medio (α)", alphaText(q.mean_alpha), "≥ 0,80 fiable · ≥ 0,667 tentativo"),
        kpi("Acierto en test", q.test_accuracy ? pct(q.test_accuracy.value) : "—",
          q.test_accuracy ? `IC 95% ${pct(q.test_accuracy.low)}–${pct(q.test_accuracy.high)} · n=${q.test_accuracy.n}` : "sin ground truth"),
        kpi("Corpus", String(q.corpus.total), `${q.corpus.calibration} calibración · ${q.corpus.test} test`),
        kpi("Ground truth", String(q.ground_truth_items), "pares caso × regla con consenso"),
        kpi("Necesitan auditoría", String(q.rules_needing_audit), "revisión trimestral"))),
    el("h2", {}, "Por regla"),
    el("table", {},
      el("thead", {}, el("tr", {}, ...["Regla", "Estado", "α", "Precisión (test)", "Recall (test)", "Medible", "Revisiones", "Auditoría"].map((h) => el("th", {}, h)))),
      el("tbody", {}, ...q.rules.map((r) => el("tr", {},
        el("td", {}, el("strong", { className: "mono" }, r.rule_id), el("div", { className: "muted" }, r.title)),
        el("td", {}, statusTag(r.validation_status)),
        el("td", { className: r.alpha_band === "unusable" ? "error-text" : "" }, alphaText(r.alpha, r.alpha_band)),
        el("td", {}, rateText(r.test.precision)), el("td", {}, rateText(r.test.recall)),
        el("td", {}, pct(r.measurable_rate)),
        el("td", {}, `✔ ${r.reviews.correct} · FP ${r.reviews.false_positive} · ✖ ${r.reviews.missed}`,
          r.reviews.last_reviewed_at ? el("div", { className: "muted" }, `última ${new Date(r.reviews.last_reviewed_at).toLocaleDateString()}`) : ""),
        el("td", {}, r.needs_audit.length ? el("ul", { className: "audit" }, ...r.needs_audit.map((a) => el("li", {}, a))) : el("span", { className: "ok-text" }, "OK"))))),
    ),
    el("p", { className: "muted" }, `Generado ${new Date(q.generated_at).toLocaleString()}. Precisión y recall se miden solo en los casos de test, con intervalo de Wilson al 95%.`));
}

// changelogCell: qué cambió en una versión, con el tipo de salto semver y quién la validó.
function changelogCell(r) {
  const entries = r.changelog_entries || [];
  const action = { added: "Nueva", modified: "Editada", removed: "Retirada" };
  return el("div", {},
    el("div", {}, r.bump ? tag(r.bump, r.bump === "major" ? "provisional" : "validated") : "", r.changelog || "—"),
    ...entries.map((e) => el("div", { className: "muted" },
      `${action[e.action] || e.action} ${e.rule_id}`,
      e.fields?.length ? ` (${e.fields.join(", ")})` : "",
      ...(e.thresholds || []).map((t) => ` · ${t.metric}: ${t.before || "—"} → ${t.after || "—"}`))),
    r.validated_by_name ? el("div", { className: "muted" }, `Propuso ${r.proposed_by_name || "—"} · validó ${r.validated_by_name}`) : "");
}
