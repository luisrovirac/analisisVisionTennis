"use strict";

// Visor de esqueleto compartido (web del jugador y portal de expertos). Dibuja sobre el video la pose
// por fotograma de poses/<id>.json (contracts/pose_sequence.schema.json, índices MediaPipe de 33 puntos)
// y marca las fases del golpe (report.segment). Sin dependencias: expone window.SkeletonViewer.
(function () {
  // Huesos en índices MediaPipe Pose. Se omiten cara y manos: RTMPose no los estima y no aportan
  // al análisis. El cuello se dibuja aparte (nariz → punto medio de los hombros).
  const BONES = [
    [11, 12], [11, 23], [12, 24], [23, 24], // tronco
    [11, 13], [13, 15], [12, 14], [14, 16], // brazos
    [23, 25], [25, 27], [24, 26], [26, 28], // piernas
    [27, 29], [29, 31], [27, 31], [28, 30], [30, 32], [28, 32], // pies
  ];
  const NOSE = 0;
  const LEFT_IDS = new Set([11, 13, 15, 23, 25, 27, 29, 31]);
  const RIGHT_IDS = new Set([12, 14, 16, 24, 26, 28, 30, 32]);
  const JOINTS = [NOSE, ...LEFT_IDS, ...RIGHT_IDS];
  // Misma lógica que la "puerta de evidencia" del worker (min_metric_confidence = 0.5): lo que el
  // estimador vio con poca seguridad se dibuja tenue, para que el usuario no lo tome como exacto.
  const RELIABLE = 0.5;
  const NEVER_SEEN = 0.05; // landmarks que el estimador no produce (p. ej. pies con RTMPose)
  const COLORS = { left: "#4fc3f7", right: "#ffb74d", center: "#f5f5f5", interpolated: "#9e9e9e",
    error: "#ff5252", strength: "#69f0ae" };
  const PHASES = [
    { key: "start_frame", label: "Inicio" },
    { key: "backswing_frame", label: "Armado" },
    { key: "impact_frame", label: "Impacto" },
    { key: "end_frame", label: "Final" },
  ];
  const SPEEDS = [0.25, 0.5, 1];

  function create(container, { videoUrl, pose, segment }) {
    const fps = pose.fps;
    const frames = pose.frames;
    const seen = everSeen(frames);
    const state = { frame: 0, showSkeleton: true, highlight: null, skeletonOnly: !videoUrl, destroyed: false };

    const video = el("video", { playsInline: true, muted: true, preload: "auto", className: "sv-video" });
    const canvas = el("canvas", { className: "sv-canvas" });
    const stage = el("div", { className: "sv-stage" }, video, canvas);
    const scrub = el("input", { type: "range", min: 0, max: Math.max(0, frames.length - 1), step: 1, value: 0,
      className: "sv-scrub", ariaLabel: "Posición en el golpe" });
    const marks = el("div", { className: "sv-marks" });
    const playBtn = button("▶", "Reproducir / pausar");
    const prevBtn = button("⟨ 1", "Fotograma anterior");
    const nextBtn = button("1 ⟩", "Fotograma siguiente");
    const speed = el("select", { ariaLabel: "Velocidad" },
      ...SPEEDS.map((s) => el("option", { value: s, selected: s === 0.5 }, `${s}×`)));
    const toggle = el("input", { type: "checkbox", checked: true });
    const phaseBtns = PHASES.filter((p) => segment && Number.isInteger(segment[p.key])).map((p) => {
      const b = button(p.label, `Ir a: ${p.label}`);
      b.addEventListener("click", () => seekToFrame(segment[p.key]));
      return b;
    });
    const frameLabel = el("span", { className: "sv-frame muted" });
    const note = el("p", { className: "sv-note muted", hidden: true });
    const root = el("div", { className: "sv" },
      stage,
      el("div", { className: "sv-timeline" }, marks, scrub),
      el("div", { className: "sv-controls" }, playBtn, prevBtn, nextBtn, speed, ...phaseBtns,
        el("label", { className: "inline sv-toggle" }, toggle, "Esqueleto"), frameLabel),
      el("p", { className: "sv-legend muted" },
        "Trazo tenue = el sistema vio esa articulación con poca seguridad; gris = fotograma estimado (sin detección)."),
      note);
    container.replaceChildren(root);

    const clock = videoUrl ? videoClock(video, fps, onFrame) : syntheticClock(frames.length / fps, onFrame);
    let activeClock = clock;
    if (videoUrl) {
      video.src = videoUrl;
      video.addEventListener("error", () => fallBackToSkeletonOnly(
        "Tu navegador no puede reproducir este formato de video; se muestra solo el esqueleto."), { once: true });
      video.addEventListener("loadedmetadata", checkVideoMatchesPose, { once: true });
    } else {
      stage.classList.add("sv-skeleton-only");
    }
    activeClock.setRate(Number(speed.value));
    renderMarks();

    playBtn.addEventListener("click", () => (activeClock.paused() ? activeClock.play() : activeClock.pause()));
    prevBtn.addEventListener("click", () => seekToFrame(state.frame - 1));
    nextBtn.addEventListener("click", () => seekToFrame(state.frame + 1));
    speed.addEventListener("change", () => activeClock.setRate(Number(speed.value)));
    toggle.addEventListener("change", () => { state.showSkeleton = toggle.checked; draw(); });
    scrub.addEventListener("input", () => seekToFrame(Number(scrub.value)));
    const resize = new ResizeObserver(draw);
    resize.observe(stage);

    function onFrame(time) {
      state.frame = clampFrame(Math.floor(time * fps + 0.01));
      scrub.value = state.frame;
      playBtn.textContent = activeClock.paused() ? "▶" : "❚❚";
      frameLabel.textContent = `Fotograma ${state.frame + 1}/${frames.length}${phaseName(state.frame)}`;
      draw();
    }

    function seekToFrame(frame) {
      activeClock.pause();
      activeClock.seek((clampFrame(frame) + 0.5) / fps); // a mitad del fotograma: evita caer en el anterior
    }

    function clampFrame(f) {
      return Math.max(0, Math.min(frames.length - 1, f));
    }

    function phaseName(frame) {
      const hit = segment && PHASES.find((p) => segment[p.key] === frame);
      return hit ? ` · ${hit.label}` : "";
    }

    function renderMarks() {
      if (!segment || frames.length < 2) return;
      marks.replaceChildren(...PHASES.filter((p) => Number.isInteger(segment[p.key])).map((p) => {
        const left = (100 * segment[p.key]) / (frames.length - 1);
        return el("span", { className: `sv-mark sv-mark-${p.key}`, title: p.label, style: `left:${left}%` }, p.label);
      }));
    }

    // OpenCV (worker) y el navegador aplican por separado la rotación de los metadatos del video:
    // si la relación de aspecto no coincide, el esqueleto quedaría girado sobre la imagen.
    function checkVideoMatchesPose() {
      const drift = Math.abs(video.duration - frames.length / fps);
      if (Number.isFinite(drift) && drift > 0.3) {
        showNote("El video tiene frecuencia de fotogramas variable: el esqueleto puede desfasarse unos fotogramas.");
      }
      if (!pose.width || !pose.height || !video.videoWidth) return;
      const videoAspect = video.videoWidth / video.videoHeight;
      const poseAspect = pose.width / pose.height;
      if (Math.abs(videoAspect - poseAspect) / poseAspect > 0.03) {
        fallBackToSkeletonOnly("La orientación del video no coincide con la analizada; se muestra solo el esqueleto.");
      }
    }

    function fallBackToSkeletonOnly(message) {
      if (state.skeletonOnly || state.destroyed) return;
      state.skeletonOnly = true;
      const at = activeClock.time();
      activeClock.destroy();
      video.removeAttribute("src");
      video.load();
      stage.classList.add("sv-skeleton-only");
      activeClock = syntheticClock(frames.length / fps, onFrame);
      activeClock.setRate(Number(speed.value));
      activeClock.seek(at);
      showNote(message);
    }

    function showNote(message) {
      note.hidden = false;
      note.textContent = note.textContent ? `${note.textContent} ${message}` : message;
    }

    function contentRect() {
      const w = stage.clientWidth;
      const h = stage.clientHeight;
      const aspect = state.skeletonOnly || !video.videoWidth
        ? (pose.width && pose.height ? pose.width / pose.height : 16 / 9)
        : video.videoWidth / video.videoHeight;
      // object-fit: contain → bandas negras arriba/abajo o a los lados.
      const cw = Math.min(w, h * aspect);
      const ch = cw / aspect;
      return { x: (w - cw) / 2, y: (h - ch) / 2, w: cw, h: ch };
    }

    function draw() {
      if (state.destroyed) return;
      const dpr = window.devicePixelRatio || 1;
      const w = stage.clientWidth;
      const h = stage.clientHeight;
      if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
        canvas.width = Math.round(w * dpr);
        canvas.height = Math.round(h * dpr);
      }
      const ctx = canvas.getContext("2d");
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      const f = frames[state.frame];
      if (!state.showSkeleton || !f) return;
      const r = contentRect();
      const pt = (i) => ({ x: r.x + f.image[i][0] * r.w, y: r.y + f.image[i][1] * r.h });
      const usable = (i) => seen[i] && !(f.image[i][0] === 0 && f.image[i][1] === 0);
      const vis = (i) => (f.detected ? f.visibility[i] : 0);
      const lineWidth = Math.max(2, r.h / 160);
      const hl = state.highlight;

      const stroke = (a, b, color, width, faint) => {
        ctx.beginPath();
        ctx.strokeStyle = color;
        ctx.lineWidth = width;
        ctx.globalAlpha = faint ? 0.45 : 1;
        ctx.setLineDash(faint ? [6, 5] : []);
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
      };
      const colorOf = (a, b) => {
        if (!f.detected) return COLORS.interpolated;
        if (LEFT_IDS.has(a) && LEFT_IDS.has(b)) return COLORS.left;
        if (RIGHT_IDS.has(a) && RIGHT_IDS.has(b)) return COLORS.right;
        return COLORS.center;
      };

      ctx.lineCap = "round";
      for (const [a, b] of BONES) {
        if (!usable(a) || !usable(b)) continue;
        const faint = Math.min(vis(a), vis(b)) < RELIABLE;
        stroke(pt(a), pt(b), colorOf(a, b), lineWidth, faint);
      }
      if (usable(NOSE) && usable(11) && usable(12)) {
        const l = pt(11);
        const rr = pt(12);
        stroke(pt(NOSE), { x: (l.x + rr.x) / 2, y: (l.y + rr.y) / 2 },
          f.detected ? COLORS.center : COLORS.interpolated, lineWidth, Math.min(vis(NOSE), vis(11), vis(12)) < RELIABLE);
      }
      if (hl) {
        const color = COLORS[hl.tone] || COLORS.error;
        for (const [a, b] of BONES) {
          if (hl.ids.has(a) && hl.ids.has(b) && usable(a) && usable(b)) stroke(pt(a), pt(b), color, lineWidth * 2.2, false);
        }
      }
      ctx.setLineDash([]);
      for (const i of JOINTS) {
        if (!usable(i)) continue;
        const p = pt(i);
        const marked = hl && hl.ids.has(i);
        ctx.globalAlpha = vis(i) < RELIABLE && !marked ? 0.45 : 1;
        ctx.beginPath();
        ctx.fillStyle = marked ? COLORS[hl.tone] || COLORS.error : f.detected ? "#ffffff" : COLORS.interpolated;
        ctx.arc(p.x, p.y, marked ? lineWidth * 2.4 : lineWidth * 1.3, 0, 2 * Math.PI);
        ctx.fill();
      }
      ctx.globalAlpha = 1;
    }

    return {
      seekToFrame,
      // Resalta los landmarks (índices MediaPipe) en los que se apoya un hallazgo; tone: "error" | "strength".
      highlight(landmarks, tone = "error") {
        state.highlight = landmarks && landmarks.length ? { ids: new Set(landmarks), tone } : null;
        draw();
      },
      focus(frame, landmarks, tone) {
        this.highlight(landmarks, tone);
        seekToFrame(frame);
        root.scrollIntoView({ behavior: "smooth", block: "center" });
      },
      destroy() {
        state.destroyed = true;
        resize.disconnect();
        activeClock.destroy();
        video.removeAttribute("src");
        video.load();
        container.replaceChildren();
      },
    };
  }

  // Reloj respaldado por el <video>: requestVideoFrameCallback da el instante exacto de cada fotograma
  // pintado; sin él (navegadores antiguos) se recurre a requestAnimationFrame + currentTime.
  function videoClock(video, fps, onFrame) {
    let alive = true;
    const hasRVFC = "requestVideoFrameCallback" in HTMLVideoElement.prototype;
    if (hasRVFC) {
      const tick = (_now, meta) => {
        if (!alive) return;
        onFrame(meta.mediaTime);
        video.requestVideoFrameCallback(tick);
      };
      video.requestVideoFrameCallback(tick);
    } else {
      const loop = () => {
        if (!alive) return;
        onFrame(video.currentTime);
        requestAnimationFrame(loop);
      };
      requestAnimationFrame(loop);
    }
    // rVFC no se dispara al pausar/reanudar sin cambio de fotograma: refrescar el botón igualmente.
    const refresh = () => alive && onFrame(video.currentTime);
    video.addEventListener("play", refresh);
    video.addEventListener("pause", refresh);
    video.addEventListener("ended", refresh);
    return {
      play: () => video.play().catch(() => {}),
      pause: () => video.pause(),
      paused: () => video.paused,
      seek: (t) => { video.currentTime = Math.max(0, Math.min(t, (video.duration || t) - 0.5 / fps)); },
      time: () => video.currentTime,
      setRate: (r) => { video.playbackRate = r; },
      destroy: () => { alive = false; video.pause(); },
    };
  }

  // Reloj sin video (modo "solo esqueleto"): avanza con requestAnimationFrame a la velocidad elegida.
  function syntheticClock(duration, onFrame) {
    let time = 0;
    let rate = 1;
    let paused = true;
    let last = null;
    let alive = true;
    const loop = (now) => {
      if (!alive) return;
      if (!paused) {
        if (last !== null) time += ((now - last) / 1000) * rate;
        if (time >= duration) { time = duration; paused = true; }
      }
      last = now;
      onFrame(Math.min(time, duration - 1e-6));
      requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
    return {
      play: () => { if (time >= duration) time = 0; paused = false; last = null; },
      pause: () => { paused = true; },
      paused: () => paused,
      seek: (t) => { time = Math.max(0, Math.min(t, duration)); },
      time: () => time,
      setRate: (r) => { rate = r; },
      destroy: () => { alive = false; },
    };
  }

  function everSeen(frames) {
    const seen = new Array(33).fill(false);
    for (const f of frames) {
      if (!f.detected) continue;
      f.visibility.forEach((v, i) => { if (v > NEVER_SEEN) seen[i] = true; });
    }
    return seen;
  }

  // Unión de los landmarks de las métricas citadas como evidencia (report 1.2: metrics[].landmarks).
  function landmarksFor(report, metricIds) {
    const byId = new Map((report.metrics || []).map((m) => [m.id, m]));
    const ids = new Set();
    for (const id of metricIds) (byId.get(id)?.landmarks || []).forEach((i) => ids.add(i));
    return [...ids];
  }

  const PHASE_LABELS = { preparation: "Preparación", acceleration: "Aceleración", follow_through: "Terminación" };
  const PHASE_STATUS = { low_confidence: "no se vio con suficiente seguridad", too_short: "demasiado corta para comparar" };

  // Tarjeta de la comparación informativa con golpes de referencia (report 1.2: reference_comparison).
  // Sin puntaje cuando no hay referencias o el jugador no se vio bien: nunca se inventa un número.
  function comparisonCard(rc) {
    if (!rc) return null;
    const card = el("section", { className: "card sv-compare" }, el("h2", {}, "Similitud con un golpe de referencia"));
    if (rc.status === "no_references" || rc.status === "low_confidence") {
      card.append(el("p", { className: "muted" }, rc.note || "No se pudo comparar este golpe."));
      return card;
    }
    const ref = rc.references_used?.[0];
    card.append(el("p", {}, el("strong", {}, `${Math.round(rc.overall_similarity)} / 100`),
      ref ? ` frente a «${ref.label}»` : ""));
    for (const p of rc.phases || []) {
      const label = PHASE_LABELS[p.phase] || p.phase;
      if (p.similarity === null || p.similarity === undefined) {
        card.append(el("p", { className: "muted sv-phase" }, `${label}: ${PHASE_STATUS[p.status] || p.status}.`));
        continue;
      }
      const bar = el("span", { className: "sv-bar" }, el("span", { style: `width:${Math.max(2, Math.min(100, p.similarity))}%` }));
      card.append(el("div", { className: "sv-phase" }, el("span", {}, label), bar, el("span", {}, `${Math.round(p.similarity)}`)));
    }
    card.append(el("p", { className: "muted" }, rc.note || ""));
    return card;
  }

  // Carga la pose desde la URL firmada (sin cabecera Authorization: la firma ya autoriza).
  async function loadPose(poseUrl) {
    const res = await fetch(poseUrl);
    if (!res.ok) throw new Error(`No se pudo descargar el esqueleto (${res.status}).`);
    return res.json();
  }

  function button(text, title) {
    return el("button", { type: "button", className: "secondary sv-btn", title }, text);
  }

  function el(tag, props, ...children) {
    const { style, ariaLabel, ...rest } = props || {};
    const node = Object.assign(document.createElement(tag), rest);
    if (style) node.setAttribute("style", style);
    if (ariaLabel) node.setAttribute("aria-label", ariaLabel);
    node.append(...children);
    return node;
  }

  window.SkeletonViewer = { create, landmarksFor, loadPose, comparisonCard };
})();
