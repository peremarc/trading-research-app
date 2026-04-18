const API_PREFIX = "/api/v1";
const AUTO_REFRESH_MS = 10000;

const elements = {
  activityFeed: document.getElementById("activity-feed"),
  botAttentionCard: document.getElementById("bot-attention-card"),
  botAttentionDetail: document.getElementById("bot-attention-detail"),
  botAttentionHeadline: document.getElementById("bot-attention-headline"),
  botAttentionPill: document.getElementById("bot-attention-pill"),
  botCycleRuns: document.getElementById("bot-cycle-runs"),
  botLastSuccess: document.getElementById("bot-last-success"),
  botNextRun: document.getElementById("bot-next-run"),
  botPauseReason: document.getElementById("bot-pause-reason"),
  botPhase: document.getElementById("bot-phase"),
  botRuntimeDetail: document.getElementById("bot-runtime-detail"),
  botStatusHeadline: document.getElementById("bot-status-headline"),
  botToggle: document.getElementById("bot-toggle"),
  candidateValidations: document.getElementById("candidate-validations"),
  chatForm: document.getElementById("chat-form"),
  chatInput: document.getElementById("chat-input"),
  chatSend: document.getElementById("chat-send"),
  chatThread: document.getElementById("chat-thread"),
  cycleOutput: document.getElementById("cycle-output"),
  focusBoard: document.getElementById("focus-board"),
  incidentBoard: document.getElementById("incident-board"),
  journalFeed: document.getElementById("journal-feed"),
  marketStateOverview: document.getElementById("market-state-overview"),
  marketStateTrail: document.getElementById("market-state-trail"),
  metricsGrid: document.getElementById("metrics-grid"),
  metricTemplate: document.getElementById("metric-card-template"),
  nextFocus: document.getElementById("next-focus"),
  openPositions: document.getElementById("open-positions"),
  pipelineDetail: document.getElementById("pipeline-detail"),
  pipelinesList: document.getElementById("pipelines-list"),
  researchTasks: document.getElementById("research-tasks"),
  runtimeSummary: document.getElementById("runtime-summary"),
  statusBadge: document.getElementById("status-badge"),
  workQueue: document.getElementById("work-queue"),
};

const state = {
  chatMessages: [],
  dashboards: null,
  lastCycleResponse: null,
  scheduler: null,
  selectedStrategyId: null,
};

document.querySelectorAll("[data-action]").forEach((button) => {
  button.addEventListener("click", async () => {
    button.disabled = true;
    setStatus(button.dataset.action === "toggle-bot" ? "Actualizando bot..." : "Refrescando...");
    try {
      await handleAction(button.dataset.action);
    } catch (error) {
      setError(error);
    } finally {
      button.disabled = false;
    }
  });
});

elements.chatForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = elements.chatInput.value.trim();
  if (!message) return;
  await sendChatMessage(message);
});

document.querySelectorAll("[data-chat-prompt]").forEach((button) => {
  button.addEventListener("click", async () => {
    const message = button.dataset.chatPrompt?.trim();
    if (!message) return;
    await sendChatMessage(message);
  });
});

boot();
window.setInterval(() => {
  refreshDashboard().catch(setError);
}, AUTO_REFRESH_MS);

async function boot() {
  setStatus("Cargando consola...");
  seedChat();
  try {
    await refreshDashboard();
  } catch (error) {
    setError(error);
  }
}

function seedChat() {
  if (state.chatMessages.length) return;
  state.chatMessages = [
    {
      role: "assistant",
      topic: "overview",
      text: "Puedes preguntarme que he descubierto, que estoy haciendo, que herramientas faltan o un resumen de las ultimas operaciones.",
    },
  ];
  renderChatThread();
}

async function handleAction(action) {
  if (action === "refresh") {
    await refreshDashboard();
    return;
  }

  if (action === "toggle-bot") {
    const isRunning = state.scheduler?.bot?.status === "running";
    state.lastCycleResponse = await request(isRunning ? "/scheduler/pause" : "/scheduler/start", { method: "POST" });
    renderCycleOutput();
    await refreshDashboard();
  }
}

async function refreshDashboard() {
  const [
    scheduler,
    health,
    pipelines,
    queue,
    researchTasks,
    candidateValidations,
    changes,
    activations,
    journal,
    positions,
    marketStateSnapshots,
    macroContext,
  ] = await Promise.all([
    request("/scheduler/status"),
    request("/strategy-health"),
    request("/strategy-health/pipelines"),
    request("/work-queue"),
    request("/research/tasks"),
    request("/strategy-evolution/candidate-validations"),
    request("/strategy-evolution/changes"),
    request("/strategy-evolution/activations"),
    request("/journal"),
    request("/positions"),
    request("/macro/state-snapshots?limit=6"),
    request("/macro/context?limit=6"),
  ]);

  state.scheduler = scheduler;
  state.dashboards = {
    activations,
    candidateValidations,
    changes,
    health,
    journal,
    macroContext,
    marketStateSnapshots,
    positions,
    pipelines,
    queue,
    researchTasks,
  };

  if (!pipelines.length) {
    state.selectedStrategyId = null;
  } else if (!pipelines.some((item) => item.strategy_id === state.selectedStrategyId)) {
    state.selectedStrategyId = pipelines[0].strategy_id;
  }

  renderDashboard();
}

function renderDashboard() {
  const {
    health,
    pipelines,
    queue,
    researchTasks,
    candidateValidations,
    changes,
    activations,
    journal,
    macroContext,
    marketStateSnapshots,
    positions,
  } = state.dashboards;
  const bot = state.scheduler.bot;
  const ai = state.scheduler.ai || {
    enabled: false,
    provider: "gemini",
    model: "gemini-2.5-flash",
    ready: false,
    fallback_provider: "openai_compatible",
    fallback_model: "qwen2.5:3b",
    active_provider: null,
    active_model: null,
    decision_count: 0,
    fallback_count: 0,
    last_error: null,
    last_decision_summary: null,
  };
  const incidents = bot.incidents || [];
  const openIncidents = incidents.filter((incident) => incident.status === "open");
  const openResearch = researchTasks.filter((task) => task.status !== "completed");
  const topItem = queue.items[0] || null;
  const activeStrategies = pipelines.filter((item) => item.active_version).length;
  const candidateVersions = pipelines.reduce((acc, item) => acc + item.candidate_versions.length, 0);
  const promotedCandidates = candidateValidations.filter((item) => item.evaluation_status === "promote").length;
  const rejectedCandidates = candidateValidations.filter((item) => item.evaluation_status === "reject").length;
  const latestMarketState = marketStateSnapshots[0] || null;

  elements.nextFocus.textContent = openIncidents.length
    ? openIncidents[0].title
    : topItem
      ? topItem.title
      : "Sin backlog prioritario";
  elements.runtimeSummary.textContent = summarizeRuntime(bot);
  updateBotBadge(bot);
  updateBotToggle(bot);
  renderRuntimeCards(bot, state.scheduler.jobs || []);

  renderMetrics([
    ["Bot", bot.status.toUpperCase(), bot.pause_reason || "Sin bloqueo activo"],
    ["Fase", bot.current_phase ? bot.current_phase.toUpperCase() : (bot.last_successful_phase || "idle").toUpperCase(), bot.current_phase ? "Ciclo en ejecucion" : `Ultima fase OK: ${bot.last_successful_phase || "ninguna"}`],
    ["Motor IA", ai.enabled ? (ai.ready ? "ACTIVO" : "DEGRADADO") : "OFF", ai.last_error || ai.last_decision_summary || "Sin decision registrada"],
    ["Proveedor IA", ai.active_provider || ai.provider || "-", ai.active_model ? `${ai.active_model} · decisiones ${ai.decision_count}` : "Sin proveedor activo"],
    ["Fallback IA", ai.fallback_model || "-", ai.enabled ? `${ai.fallback_provider || "-"} · usos ${ai.fallback_count || 0}` : "Cadena IA desactivada"],
    ["Incidencias abiertas", openIncidents.length, openIncidents.length ? "Revisar antes de reanudar" : "Sin errores bloqueantes"],
    ["Ciclos autonomos", bot.cycle_runs, bot.last_cycle_completed_at ? `Ultimo cierre: ${formatDate(bot.last_cycle_completed_at)}` : "Aun no ha cerrado ciclos"],
    ["Cadencia", formatCadence(bot), cadenceDescription(bot)],
    ["Regimen activo", latestMarketState ? humanizeLabel(latestMarketState.regime_label) : "PENDIENTE", latestMarketState ? `${(latestMarketState.pdca_phase || "general").toUpperCase()} · ${formatDate(latestMarketState.created_at)}` : "Ejecuta PLAN o DO para fijarlo"],
    ["Estrategias activas", activeStrategies, `${pipelines.length} pipelines visibles`],
    ["Versiones candidatas", candidateVersions, "Pendientes de validar o promover"],
    ["Research abierto", openResearch.length, "Incluye recovery research"],
    ["Backlog total", queue.total_items, topItem ? `Primero: ${topItem.priority}` : "Sin cola activa"],
    ["Promociones validadas", promotedCandidates, "Candidatas que ya demostraron edge"],
    ["Candidatas rechazadas", rejectedCandidates, "Versiones archivadas tras validacion"],
    ["Posiciones abiertas", positions.filter((position) => position.status === "open").length, "Exposicion paper actual"],
    ["Fitness medio", average(health.map((item) => item.fitness_score)).toFixed(2), "Salud agregada del laboratorio"],
  ]);

  renderIncidents(incidents);
  renderMarketState(marketStateSnapshots, macroContext);
  renderFocusBoard(queue, pipelines, openResearch, bot);
  renderPipelines(pipelines);
  renderPipelineDetail(pipelines.find((item) => item.strategy_id === state.selectedStrategyId) || null);
  renderWorkQueue(queue);
  renderResearchTasks(researchTasks);
  renderCandidateValidations(candidateValidations);
  renderActivityFeed(changes, activations);
  renderOpenPositions(positions, pipelines);
  renderJournalFeed(journal);
  renderCycleOutput();
}

function renderMarketState(snapshots, macroContext) {
  if (!elements.marketStateOverview || !elements.marketStateTrail) return;

  const latest = Array.isArray(snapshots) && snapshots.length ? snapshots[0] : null;
  const fallbackMacro = asObject(macroContext);

  if (!latest) {
    elements.marketStateOverview.innerHTML = `
      <article class="world-state-card">
        <span class="focus-kicker">Snapshot pendiente</span>
        <h3>El bot todavia no ha capturado un Market State Snapshot persistido.</h3>
        <p class="muted">${escapeHtml(fallbackMacro.summary || "Ejecuta PLAN o DO para fijar el estado del mundo antes de decidir.")}</p>
        <div class="row">${renderPillList((fallbackMacro.active_regimes || []).slice(0, 4), "pill-candidate", "Sin regimen activo")}</div>
      </article>
    `;
    renderStackList(
      elements.marketStateTrail,
      [],
      () => "",
      "Aun no hay snapshots. Ejecuta PLAN o DO para abrir la traza del regimen operativo.",
    );
    return;
  }

  const payload = asObject(latest.snapshot_payload);
  const protocolState = asObject(payload.market_state_snapshot);
  const benchmark = asObject(payload.benchmark_snapshot);
  const backlog = asObject(payload.backlog);
  const macro = asObject(payload.macro_context, fallbackMacro);
  const regime = asObject(payload.market_regime);
  const calendarEvents = Array.isArray(payload.calendar_events)
    ? payload.calendar_events
    : (Array.isArray(protocolState.corporate_calendar) ? protocolState.corporate_calendar : []);
  const activeWatchlists = Array.isArray(protocolState.active_watchlists) ? protocolState.active_watchlists : [];
  const openPositions = Array.isArray(protocolState.open_positions) ? protocolState.open_positions : [];
  const activeRegimes = Array.isArray(macro.active_regimes) ? macro.active_regimes : [];
  const calendarError = typeof payload.calendar_error === "string" && payload.calendar_error ? payload.calendar_error : null;
  const executionMode = protocolState.execution_mode || latest.execution_mode || "global";
  const regimeLabel = latest.regime_label || regime.label || "range_mixed";
  const confidence = latest.regime_confidence ?? regime.confidence ?? null;
  const exposureItems = [
    ...activeWatchlists.slice(0, 3).map((watchlist) => ({
      title: watchlist.name || watchlist.code || "Watchlist",
      detail: watchlist.tickers?.length ? `tickers ${watchlist.tickers.slice(0, 3).join(", ")}` : "Sin tickers destacados",
      aside: `${watchlist.active_item_count ?? watchlist.item_count ?? 0} activos`,
    })),
    ...openPositions.slice(0, 3).map((position) => ({
      title: `${position.ticker || "?"} · ${position.side || "long"}`,
      detail: position.thesis || "Sin thesis persistida",
      aside: position.stop_price ? `stop ${formatPrice(position.stop_price)}` : "sin stop",
    })),
  ];
  const benchmarkSummary = [
    Number.isFinite(benchmark.price) ? `price ${formatPrice(benchmark.price)}` : null,
    Number.isFinite(benchmark.month_performance) ? `MTD ${formatSignedPctFromDecimal(benchmark.month_performance)}` : null,
  ].filter(Boolean).join(" · ") || `ticker ${latest.benchmark_ticker || "SPY"}`;

  elements.marketStateOverview.innerHTML = `
    <article class="world-state-card">
      <div class="world-state-top">
        <div>
          <span class="focus-kicker">Snapshot activo</span>
          <h3>${escapeHtml(humanizeLabel(regimeLabel))}</h3>
          <p class="muted">${escapeHtml(latest.summary || "Sin resumen operativo.")}</p>
        </div>
        <div class="row">
          ${pill((latest.pdca_phase || "general").toUpperCase(), "pill-info")}
          ${pill(humanizeLabel(latest.trigger || "snapshot"), "pill-approved")}
          ${pill(`conf ${formatConfidencePct(confidence)}`, regimePillClass(regimeLabel))}
        </div>
      </div>

      <div class="world-state-stat-grid">
        ${snapshotStat("Benchmark", latest.benchmark_ticker || "SPY", benchmarkSummary)}
        ${snapshotStat("Modo", humanizeLabel(executionMode), `trigger ${latest.trigger || "snapshot"}`)}
        ${snapshotStat("Posiciones", String(backlog.open_positions_count ?? openPositions.length ?? 0), openPositions.length ? `${openPositions.length} capturadas` : "Sin exposicion abierta")}
        ${snapshotStat("Watchlists", String(backlog.active_watchlists_count ?? activeWatchlists.length ?? 0), activeWatchlists.length ? activeWatchlists.slice(0, 2).map((item) => item.code || item.name || "watchlist").join(" · ") : "Sin listas activas")}
        ${snapshotStat("Research", String(backlog.open_research_tasks ?? 0), `${backlog.pending_reviews ?? 0} reviews pendientes`)}
        ${snapshotStat("Confianza", formatConfidencePct(confidence), humanizeLabel(regimeLabel))}
      </div>

      <div class="world-state-detail-grid">
        <div class="meta-block">
          <strong>Macro y calendario</strong>
          <div class="muted">${escapeHtml(macro.summary || fallbackMacro.summary || "Sin resumen macro persistido.")}</div>
          <div class="row">${renderPillList(activeRegimes.slice(0, 4), "pill-candidate", "Sin tags macro activas")}</div>
          <div class="snapshot-list">
            ${renderSnapshotItems(
              calendarEvents.slice(0, 4).map((event) => ({
                title: event.title || event.event_type || "Evento",
                detail: event.source || event.event_type || "Calendario",
                aside: event.event_date || "sin fecha",
              })),
              calendarError || "Sin eventos macro proximos registrados.",
            )}
          </div>
        </div>

        <div class="meta-block">
          <strong>Watchlists y exposicion</strong>
          <div class="muted">${escapeHtml(`Snapshot ${latest.id} · ${formatDate(latest.created_at)} · mode ${executionMode}`)}</div>
          <div class="snapshot-list">
            ${renderSnapshotItems(exposureItems, "Sin watchlists ni posiciones abiertas en esta captura.")}
          </div>
        </div>
      </div>
    </article>
  `;

  renderStackList(
    elements.marketStateTrail,
    snapshots.slice(0, 6),
    (snapshot) => {
      const snapshotPayload = asObject(snapshot.snapshot_payload);
      const snapshotBacklog = asObject(snapshotPayload.backlog);
      return `
        <h3>${escapeHtml(humanizeLabel(snapshot.regime_label || "snapshot"))}</h3>
        <div class="row">
          ${pill((snapshot.pdca_phase || "general").toUpperCase(), "pill-info")}
          ${pill(humanizeLabel(snapshot.trigger || "snapshot"), "pill-approved")}
          ${pill(`conf ${formatConfidencePct(snapshot.regime_confidence)}`, regimePillClass(snapshot.regime_label))}
        </div>
        <p class="muted">${escapeHtml(snapshot.summary || "Sin resumen.")}</p>
        <p class="muted">
          open=${snapshotBacklog.open_positions_count ?? 0}
          · watchlists=${snapshotBacklog.active_watchlists_count ?? 0}
          · ${escapeHtml(formatDate(snapshot.created_at))}
        </p>
      `;
    },
    "Aun no hay snapshots registrados.",
  );
}

function renderRuntimeCards(bot, jobs) {
  const nextRun = jobs[0]?.next_run_time || null;
  const openIncident = bot.incidents.find((incident) => incident.status === "open") || null;

  elements.botStatusHeadline.textContent = bot.status.toUpperCase();
  elements.botRuntimeDetail.textContent = summarizeRuntime(bot);
  elements.botPhase.textContent = (bot.current_phase || bot.last_successful_phase || "idle").toUpperCase();
  elements.botNextRun.textContent = nextRun ? formatDate(nextRun) : "sin programar";
  elements.botCycleRuns.textContent = String(bot.cycle_runs ?? 0);
  elements.botLastSuccess.textContent = (bot.last_successful_phase || "ninguna").toUpperCase();

  if (openIncident) {
    elements.botAttentionCard.classList.add("is-alert");
    elements.botAttentionHeadline.textContent = openIncident.title;
    elements.botAttentionDetail.textContent = openIncident.detail;
    elements.botAttentionPill.textContent = "Incidencia abierta";
    elements.botAttentionPill.className = "pill pill-reject";
    elements.botPauseReason.textContent = bot.pause_reason || "Bloqueado";
    elements.botPauseReason.className = "pill pill-degraded";
  } else {
    elements.botAttentionCard.classList.remove("is-alert");
    elements.botAttentionHeadline.textContent = bot.status === "running" ? "Bot operativo" : "Sin incidencias";
    elements.botAttentionDetail.textContent = bot.status === "running"
      ? "El bot esta ejecutando su bucle autonomo y seguira mientras no aparezcan incidencias."
      : "No hay incidencias abiertas. Puedes arrancar el bot cuando quieras.";
    elements.botAttentionPill.textContent = bot.status === "running" ? "Operando" : "Estable";
    elements.botAttentionPill.className = `pill ${bot.status === "running" ? "pill-active" : "pill-approved"}`;
    elements.botPauseReason.textContent = bot.pause_reason || "Sin bloqueo";
    elements.botPauseReason.className = "pill pill-approved";
  }
}

function updateBotBadge(bot) {
  elements.statusBadge.textContent = bot.status.toUpperCase();
  elements.statusBadge.className = `status-badge ${botStatusClass(bot)}`;
}

function updateBotToggle(bot) {
  const isRunning = bot.status === "running";
  elements.botToggle.textContent = isRunning ? "Pausar Bot" : "Arrancar Bot";
  elements.botToggle.className = `action ${isRunning ? "action-alert" : "action-success"}`;
}

function summarizeRuntime(bot) {
  if (bot.requires_attention && bot.incidents.length) {
    return `Bloqueado por incidencia desde ${formatDate(bot.incidents[0].detected_at)}`;
  }
  if (bot.status === "running") {
    if (bot.current_phase) {
      return `Ejecutando fase ${bot.current_phase.toUpperCase()} · ciclos completados ${bot.cycle_runs}`;
    }
    return `Bot en ${formatCadence(bot).toLowerCase()} · ultimo cierre ${formatDate(bot.last_cycle_completed_at)}`;
  }
  return bot.pause_reason || "Bot en pausa.";
}

function formatCadence(bot) {
  if (bot.cadence_mode === "continuous") {
    return `CONTINUO · ${bot.continuous_idle_seconds}s`;
  }
  return `CADA ${bot.interval_minutes}M`;
}

function cadenceDescription(bot) {
  if (bot.cadence_mode === "continuous") {
    return "Lanza el siguiente ciclo al terminar el anterior";
  }
  return "Cadencia del bucle autonomo";
}

function renderMetrics(cards) {
  elements.metricsGrid.innerHTML = "";
  cards.forEach(([label, value, footnote]) => {
    const card = elements.metricTemplate.content.firstElementChild.cloneNode(true);
    card.querySelector(".metric-label").textContent = label;
    card.querySelector(".metric-value").textContent = String(value);
    card.querySelector(".metric-footnote").textContent = footnote;
    elements.metricsGrid.appendChild(card);
  });
}

function renderIncidents(incidents) {
  renderStackList(
    elements.incidentBoard,
    incidents,
    (incident) => `
      <div class="stack-item incident-card${incident.status === "resolved" ? " is-resolved" : ""}">
        <h3>${escapeHtml(incident.title)}</h3>
        <div class="row">
          ${pill(incident.source, "pill-info")}
          ${pill(incident.status, incident.status === "open" ? "pill-reject" : "pill-active")}
        </div>
        <p class="muted">${escapeHtml(incident.detail)}</p>
        <p class="muted">detectada=${escapeHtml(formatDate(incident.detected_at))}${incident.resolved_at ? ` · resuelta=${escapeHtml(formatDate(incident.resolved_at))}` : ""}</p>
      </div>
    `,
    "Sin incidencias registradas. Si una API falla, el bot se pausara automaticamente aqui.",
    true,
  );
}

function renderFocusBoard(queue, pipelines, openResearch, bot) {
  const topItem = queue.items[0];
  const degradedWithCandidates = pipelines.filter(
    (item) => item.strategy_status === "degraded" && item.candidate_versions.length > 0,
  );
  const latestIncident = bot.incidents.find((incident) => incident.status === "open") || null;

  const cards = [
    latestIncident
      ? {
          kicker: "Bloqueo activo",
          title: latestIncident.title,
          pills: [pill("Incidencia", "pill-reject"), pill(latestIncident.source, "pill-info")],
          body: latestIncident.detail,
        }
      : topItem
        ? {
            kicker: "Siguiente foco",
            title: topItem.title,
            pills: [pill(topItem.priority, priorityClass(topItem.priority)), pill(topItem.item_type, "pill-info")],
            body: formatContext(topItem.context),
          }
        : {
            kicker: "Siguiente foco",
            title: "No hay trabajo urgente",
            pills: [pill("IDLE", "pill-active")],
            body: "El sistema no tiene cola priorizada en este momento.",
          },
    {
      kicker: "Recuperacion",
      title: `${degradedWithCandidates.length} estrategias degradadas con candidata`,
      pills: [pill("Candidate validation", "pill-candidate")],
      body: degradedWithCandidates.length
        ? degradedWithCandidates.map((item) => item.strategy_code).join(" - ")
        : "Ninguna estrategia degradada tiene candidata pendiente.",
    },
    {
      kicker: "Research",
      title: `${openResearch.length} tareas abiertas`,
      pills: [pill(bot.status === "running" ? "Autonomous" : "Paused", botStatusClass(bot))],
      body: openResearch.length
        ? openResearch.slice(0, 3).map((task) => task.title).join(" - ")
        : "No hay research tasks abiertas.",
    },
  ];

  elements.focusBoard.innerHTML = cards
    .map(
      (card) => `
        <article class="focus-card">
          <span class="focus-kicker">${card.kicker}</span>
          <h3>${escapeHtml(card.title)}</h3>
          <div class="row">${card.pills.join("")}</div>
          <p class="muted">${escapeHtml(card.body)}</p>
        </article>
      `,
    )
    .join("");
}

function renderPipelines(pipelines) {
  elements.pipelinesList.innerHTML = "";
  if (!pipelines.length) {
    elements.pipelinesList.innerHTML = '<div class="empty-state">Todavia no hay estrategias sembradas.</div>';
    return;
  }

  pipelines.forEach((pipeline) => {
    const isSelected = pipeline.strategy_id === state.selectedStrategyId;
    const card = document.createElement("article");
    card.className = `pipeline-card${isSelected ? " is-selected" : ""}`;
    card.innerHTML = `
      <div class="pipeline-top">
        <div>
          <h3 class="pipeline-title">${escapeHtml(pipeline.strategy_name)}</h3>
          <div class="pipeline-code">${escapeHtml(pipeline.strategy_code)} - status=${escapeHtml(pipeline.strategy_status)}</div>
        </div>
        <div class="row">
          ${pill(`viva ${pipeline.active_version ? `v${pipeline.active_version.version}` : "no"}`, pipeline.active_version ? "pill-active" : "pill-approved")}
          ${pill(`${pipeline.candidate_versions.length} candidate`, "pill-candidate")}
          ${pill(`${pipeline.degraded_versions.length} degraded`, "pill-degraded")}
        </div>
      </div>
      <div class="pipeline-grid">
        <div class="meta-block">
          <strong>Versionado</strong>
          <div class="muted">Approved: ${describeVersions(pipeline.approved_versions)}</div>
          <div class="muted">Candidate: ${describeVersions(pipeline.candidate_versions)}</div>
        </div>
        <div class="meta-block">
          <strong>Senal</strong>
          <div class="muted">Fitness: ${formatNumber(pipeline.latest_scorecard?.fitness_score)}</div>
          <div class="muted">Trades cerrados: ${pipeline.latest_scorecard?.closed_trades_count ?? 0}</div>
        </div>
      </div>
    `;
    card.addEventListener("click", () => {
      state.selectedStrategyId = pipeline.strategy_id;
      renderPipelines(pipelines);
      renderPipelineDetail(pipeline);
    });
    elements.pipelinesList.appendChild(card);
  });
}

function renderPipelineDetail(pipeline) {
  if (!pipeline) {
    elements.pipelineDetail.innerHTML = '<div class="detail-card empty-state">Selecciona una estrategia para ver su detalle.</div>';
    return;
  }

  const validations = pipeline.latest_candidate_validations || [];
  elements.pipelineDetail.innerHTML = `
    <article class="detail-card">
      <h3>${escapeHtml(pipeline.strategy_name)}</h3>
      <p class="muted">${escapeHtml(pipeline.strategy_code)} - ${escapeHtml(pipeline.strategy_status)}</p>
      <div class="detail-grid">
        <div class="meta-block">
          <strong>Pipeline</strong>
          <div class="muted">Activa: ${pipeline.active_version ? `v${pipeline.active_version.version}` : "ninguna"}</div>
          <div class="muted">Candidate: ${describeVersions(pipeline.candidate_versions)}</div>
          <div class="muted">Degraded: ${describeVersions(pipeline.degraded_versions)}</div>
          <div class="muted">Archived: ${describeVersions(pipeline.archived_versions)}</div>
        </div>
        <div class="meta-block">
          <strong>Scorecard</strong>
          <div class="muted">Signals: ${pipeline.latest_scorecard?.signals_count ?? 0}</div>
          <div class="muted">Closed trades: ${pipeline.latest_scorecard?.closed_trades_count ?? 0}</div>
          <div class="muted">Fitness: ${formatNumber(pipeline.latest_scorecard?.fitness_score)}</div>
          <div class="muted">Activity: ${formatNumber(pipeline.latest_scorecard?.activity_score)}</div>
        </div>
      </div>
      <div class="meta-block">
        <strong>Ultimas validaciones</strong>
        ${
          validations.length
            ? validations
                .map(
                  (item) => `
                    <div class="muted">
                      v${item.candidate_version_number} - ${item.evaluation_status} - ${item.trade_count} trades -
                      win rate ${formatPct(item.win_rate)}
                    </div>
                  `,
                )
                .join("")
            : '<div class="muted">Sin snapshots de candidate validation.</div>'
        }
      </div>
    </article>
  `;
}

function renderWorkQueue(queue) {
  renderStackList(
    elements.workQueue,
    queue.items,
    (item) => `
      <h3>${escapeHtml(item.title)}</h3>
      <div class="row">
        ${pill(item.priority, priorityClass(item.priority))}
        ${pill(item.item_type, "pill-info")}
      </div>
      <p class="muted">${escapeHtml(formatContext(item.context))}</p>
    `,
    "La cola de trabajo esta vacia.",
  );
}

function renderResearchTasks(tasks) {
  renderStackList(
    elements.researchTasks,
    tasks,
    (task) => `
      <h3>${escapeHtml(task.title)}</h3>
      <div class="row">
        ${pill(task.priority, task.priority === "high" ? "pill-candidate" : "pill-approved")}
        ${pill(task.task_type, "pill-info")}
        ${pill(task.status, task.status === "completed" ? "pill-active" : "pill-approved")}
      </div>
      <p class="muted">${escapeHtml(task.hypothesis)}</p>
    `,
    "No hay research tasks registradas.",
  );
}

function renderCandidateValidations(items) {
  renderStackList(
    elements.candidateValidations,
    items,
    (item) => `
      <h3>Strategy ${item.strategy_id} - v${item.candidate_version_number}</h3>
      <div class="row">
        ${pill(item.evaluation_status, validationClass(item.evaluation_status))}
        ${pill(`${item.trade_count} trades`, "pill-info")}
      </div>
      <p class="muted">
        wins=${item.wins} losses=${item.losses}
        - avg pnl=${formatNumber(item.avg_pnl_pct)}
        - drawdown=${formatNumber(item.avg_drawdown_pct)}
        - ${formatDate(item.generated_at)}
      </p>
    `,
    "No hay snapshots de validacion.",
  );
}

function renderActivityFeed(changes, activations) {
  const items = [
    ...changes.slice(0, 5).map((item) => ({
      title: `Cambio de estrategia #${item.strategy_id}`,
      body: item.change_reason,
      meta: `auto=${item.applied_automatically}`,
      timestamp: item.created_at,
      pillText: "change",
      pillClass: "pill-candidate",
    })),
    ...activations.slice(0, 5).map((item) => ({
      title: `Activacion de estrategia #${item.strategy_id}`,
      body: item.activation_reason,
      meta: `v${item.activated_version_id}`,
      timestamp: item.created_at,
      pillText: "activation",
      pillClass: "pill-active",
    })),
  ]
    .sort((left, right) => new Date(right.timestamp).getTime() - new Date(left.timestamp).getTime())
    .slice(0, 8);

  renderStackList(
    elements.activityFeed,
    items,
    (item) => `
      <h3>${escapeHtml(item.title)}</h3>
      <div class="row">${pill(item.pillText, item.pillClass)}</div>
      <p class="muted">${escapeHtml(item.body)}</p>
      <p class="muted">${escapeHtml(item.meta)} - ${escapeHtml(formatDate(item.timestamp))}</p>
    `,
    "Todavia no hay eventos recientes.",
  );
}

function renderJournalFeed(entries) {
  renderStackList(
    elements.journalFeed,
    entries.slice(0, 12),
    (entry) => `
      <h3>${escapeHtml(entry.decision || entry.entry_type)}</h3>
      <div class="row">
        ${pill(entry.entry_type, "pill-info")}
        ${entry.strategy_id ? pill(`strategy ${entry.strategy_id}`, "pill-candidate") : ""}
        ${entry.strategy_version_id ? pill(`v${entry.strategy_version_id}`, "pill-active") : ""}
        ${entry.ticker ? pill(entry.ticker, "pill-approved") : ""}
      </div>
      <p class="muted">${escapeHtml(entry.reasoning || "Sin razonamiento registrado.")}</p>
      <p class="muted">${escapeHtml(formatJournalMeta(entry))}</p>
    `,
    "Todavia no hay decisiones registradas en journal.",
  );
}

function renderOpenPositions(positions, pipelines) {
  const openPositions = positions.filter((position) => position.status === "open");
  const strategyByVersionId = new Map();

  pipelines.forEach((pipeline) => {
    const versions = [
      ...(pipeline.approved_versions || []),
      ...(pipeline.candidate_versions || []),
      ...(pipeline.degraded_versions || []),
      ...(pipeline.archived_versions || []),
      ...(pipeline.active_version ? [pipeline.active_version] : []),
    ];

    versions.forEach((version) => {
      strategyByVersionId.set(version.id, {
        strategyCode: pipeline.strategy_code,
        strategyName: pipeline.strategy_name,
        versionNumber: version.version,
      });
    });
  });

  elements.openPositions.innerHTML = "";
  if (!openPositions.length) {
    elements.openPositions.innerHTML = '<div class="stack-item"><p class="muted">No hay posiciones abiertas.</p></div>';
    return;
  }

  const header = document.createElement("div");
  header.className = "positions-row positions-row-header";
  header.innerHTML = `
    <div>Ticker</div>
    <div>Estrategia</div>
    <div>Entrada</div>
    <div>Razonamiento</div>
  `;
  elements.openPositions.appendChild(header);

  openPositions.forEach((position) => {
    const strategy = strategyByVersionId.get(position.strategy_version_id) || null;
    const row = document.createElement("article");
    row.className = "positions-row";
    row.innerHTML = `
      <div>
        <strong>${escapeHtml(position.ticker)}</strong>
        <div class="muted">${escapeHtml(position.side)} · ${escapeHtml(position.account_mode)}</div>
      </div>
      <div>
        <strong>${escapeHtml(describeStrategy(position, strategy))}</strong>
        <div class="muted">${escapeHtml(describeExecutionMode(position))}</div>
      </div>
      <div>
        <strong>${escapeHtml(formatPrice(position.entry_price))}</strong>
        <div class="muted">stop=${escapeHtml(formatPrice(position.stop_price))} · target=${escapeHtml(formatPrice(position.target_price))}</div>
      </div>
      <div>
        <strong>${escapeHtml(position.thesis || "Sin thesis registrada")}</strong>
        <div class="muted">${escapeHtml(buildReasoningChain(position))}</div>
      </div>
    `;
    elements.openPositions.appendChild(row);
  });
}

function renderStackList(container, items, renderer, emptyMessage, renderAlreadyWrapped = false) {
  container.innerHTML = "";
  if (!items.length) {
    container.innerHTML = `<div class="stack-item"><p class="muted">${emptyMessage}</p></div>`;
    return;
  }

  items.forEach((item) => {
    if (renderAlreadyWrapped) {
      container.insertAdjacentHTML("beforeend", renderer(item));
      return;
    }
    const node = document.createElement("article");
    node.className = "stack-item";
    node.innerHTML = renderer(item);
    container.appendChild(node);
  });
}

function renderCycleOutput() {
  elements.cycleOutput.textContent = state.lastCycleResponse
    ? JSON.stringify(state.lastCycleResponse, null, 2)
    : JSON.stringify(state.scheduler?.bot || { status: "idle" }, null, 2);
}

function renderChatThread() {
  if (!elements.chatThread) return;

  elements.chatThread.innerHTML = "";
  state.chatMessages.forEach((message) => {
    const node = document.createElement("article");
    node.className = `chat-bubble ${message.role === "user" ? "chat-bubble-user" : "chat-bubble-assistant"}`;
    node.innerHTML = `
      <div class="chat-meta">
        <span>${message.role === "user" ? "Tu" : "Bot"}</span>
        ${message.topic ? pill(message.topic, message.role === "user" ? "pill-approved" : "pill-info") : ""}
      </div>
      <div class="chat-text">${escapeHtml(message.text)}</div>
    `;
    elements.chatThread.appendChild(node);
  });
  elements.chatThread.scrollTop = elements.chatThread.scrollHeight;
}

async function sendChatMessage(message) {
  state.chatMessages.push({ role: "user", text: message });
  renderChatThread();
  elements.chatInput.value = "";
  elements.chatSend.disabled = true;

  try {
    const payload = await request("/chat", {
      method: "POST",
      body: JSON.stringify({ message }),
    });
    state.chatMessages.push({
      role: "assistant",
      topic: payload.topic,
      text: payload.reply,
    });
    renderChatThread();
    renderSuggestedPrompts(payload.suggested_prompts || []);
  } catch (error) {
    state.chatMessages.push({
      role: "assistant",
      topic: "error",
      text: error instanceof Error ? error.message : String(error),
    });
    renderChatThread();
    throw error;
  } finally {
    elements.chatSend.disabled = false;
  }
}

function renderSuggestedPrompts(prompts) {
  const container = document.querySelector(".chat-suggestions");
  if (!container || !Array.isArray(prompts) || !prompts.length) return;
  container.innerHTML = prompts
    .map(
      (prompt) => `
        <button class="chat-chip" data-chat-prompt="${escapeHtml(prompt)}">${escapeHtml(prompt)}</button>
      `,
    )
    .join("");
  container.querySelectorAll("[data-chat-prompt]").forEach((button) => {
    button.addEventListener("click", async () => {
      const message = button.dataset.chatPrompt?.trim();
      if (!message) return;
      await sendChatMessage(message);
    });
  });
}

async function request(path, options = {}) {
  const response = await fetch(`${API_PREFIX}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });

  if (!response.ok) {
    const payload = await safeJson(response);
    const detail = payload?.detail || `Request failed: ${response.status}`;
    state.lastCycleResponse = { error: detail, path };
    renderCycleOutput();
    throw new Error(detail);
  }

  return safeJson(response);
}

async function safeJson(response) {
  const text = await response.text();
  return text ? JSON.parse(text) : null;
}

function setStatus(text) {
  elements.statusBadge.textContent = text;
}

function setError(error) {
  const message = error instanceof Error ? error.message : String(error);
  state.lastCycleResponse = { error: message, captured_at: new Date().toISOString() };
  renderCycleOutput();
  elements.statusBadge.textContent = "ERROR";
  elements.statusBadge.className = "status-badge pill-reject";
  elements.runtimeSummary.textContent = message;
}

function botStatusClass(bot) {
  if (bot.requires_attention) return "pill-reject";
  if (bot.status === "running") return "pill-active";
  return "pill-approved";
}

function pill(text, className) {
  return `<span class="pill ${className}">${escapeHtml(String(text))}</span>`;
}

function priorityClass(priority) {
  if (priority === "P1") return "pill-reject";
  if (priority === "P2") return "pill-degraded";
  if (priority === "P3") return "pill-candidate";
  return "pill-approved";
}

function validationClass(status) {
  if (status === "promote") return "pill-active";
  if (status === "reject") return "pill-reject";
  if (status === "observe") return "pill-degraded";
  return "pill-approved";
}

function describeVersions(versions) {
  return versions.length ? versions.map((item) => `v${item.version}`).join(", ") : "ninguna";
}

function formatContext(context = {}) {
  const entries = Object.entries(context);
  if (!entries.length) return "Sin contexto adicional.";
  return entries
    .map(([key, value]) => `${key}=${Array.isArray(value) ? value.join(",") : value}`)
    .join(" - ");
}

function formatJournalMeta(entry) {
  const parts = [];
  if (entry.position_id) parts.push(`position=${entry.position_id}`);
  if (entry.outcome) parts.push(`outcome=${entry.outcome}`);
  if (entry.lessons) parts.push(`lesson=${entry.lessons}`);
  parts.push(formatDate(entry.event_time));
  return parts.join(" - ");
}

function snapshotStat(label, value, footnote) {
  return `
    <div class="snapshot-stat">
      <span class="snapshot-stat-label">${escapeHtml(label)}</span>
      <strong class="snapshot-stat-value">${escapeHtml(value)}</strong>
      <small class="snapshot-stat-footnote">${escapeHtml(footnote)}</small>
    </div>
  `;
}

function renderSnapshotItems(items, emptyMessage) {
  if (!Array.isArray(items) || !items.length) {
    return `<div class="muted">${escapeHtml(emptyMessage)}</div>`;
  }
  return items
    .map(
      (item) => `
        <div class="snapshot-item">
          <div>
            <strong>${escapeHtml(item.title || "Item")}</strong>
            <p>${escapeHtml(item.detail || "Sin detalle")}</p>
          </div>
          <span>${escapeHtml(item.aside || "")}</span>
        </div>
      `,
    )
    .join("");
}

function renderPillList(values, className, emptyText) {
  if (!Array.isArray(values) || !values.length) {
    return emptyText ? pill(emptyText, "pill-approved") : "";
  }
  return values.map((value) => pill(value, className)).join("");
}

function formatDate(value) {
  if (!value) return "sin fecha";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString("es-ES", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatNumber(value) {
  return Number.isFinite(value) ? Number(value).toFixed(2) : "n/a";
}

function formatPct(value) {
  return Number.isFinite(value) ? `${Number(value).toFixed(1)}%` : "n/a";
}

function formatConfidencePct(value) {
  return Number.isFinite(value) ? `${Math.round(Number(value) * 100)}%` : "n/a";
}

function formatPrice(value) {
  return Number.isFinite(value) ? Number(value).toFixed(2) : "n/a";
}

function formatSignedPctFromDecimal(value) {
  if (!Number.isFinite(value)) return "n/a";
  const pct = Number(value) * 100;
  return `${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%`;
}

function humanizeLabel(value) {
  const cleaned = String(value || "").trim().replaceAll("_", " ").replaceAll("-", " ");
  if (!cleaned) return "Sin definir";
  return cleaned.charAt(0).toUpperCase() + cleaned.slice(1);
}

function regimePillClass(label) {
  const normalized = String(label || "").toLowerCase();
  if (normalized.includes("bull")) return "pill-active";
  if (normalized.includes("risk") || normalized.includes("uncert")) return "pill-reject";
  if (normalized.includes("range") || normalized.includes("mixed") || normalized.includes("selective")) return "pill-degraded";
  return "pill-info";
}

function asObject(value, fallback = {}) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : fallback;
}

function describeStrategy(position, strategy) {
  if (!strategy) {
    return position.strategy_version_id ? `version ${position.strategy_version_id}` : "Sin estrategia enlazada";
  }
  return `${strategy.strategyCode} · ${strategy.strategyName} · v${strategy.versionNumber}`;
}

function describeExecutionMode(position) {
  const mode = position.entry_context?.execution_mode;
  return mode ? `mode=${mode}` : "mode=default";
}

function buildReasoningChain(position) {
  const context = position.entry_context || {};
  const quant = context.quant_summary || {};
  const details = [];

  if (context.source) details.push(`source=${context.source}`);
  if (Number.isFinite(context.risk_reward)) details.push(`R/R=${Number(context.risk_reward).toFixed(2)}`);
  if (Number.isFinite(quant.relative_volume)) details.push(`relVol=${Number(quant.relative_volume).toFixed(2)}`);
  if (Number.isFinite(quant.rsi_14)) details.push(`RSI=${Number(quant.rsi_14).toFixed(1)}`);
  if (Number.isFinite(quant.month_performance)) details.push(`month=${(Number(quant.month_performance) * 100).toFixed(1)}%`);

  return details.length ? details.join(" · ") : "Sin cadena de razonamiento estructurada";
}

function average(values) {
  const numeric = values.filter((value) => Number.isFinite(value));
  if (!numeric.length) return 0;
  return numeric.reduce((acc, value) => acc + value, 0) / numeric.length;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}
