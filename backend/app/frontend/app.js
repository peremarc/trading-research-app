const API_PREFIX = "/api/v1";
const AUTO_REFRESH_MS = 10000;
const LEARNING_COMPARE_BASELINES_STORAGE_KEY = "trading-research.learning-compare-baselines.v1";

const elements = {
  chatArchive: document.getElementById("chat-archive"),
  chatConversationList: document.getElementById("chat-conversation-list"),
  chatConversationMeta: document.getElementById("chat-conversation-meta"),
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
  chatLlmSelect: document.getElementById("chat-llm-select"),
  chatNewConversation: document.getElementById("chat-new-conversation"),
  chatSend: document.getElementById("chat-send"),
  chatShowArchived: document.getElementById("chat-show-archived"),
  chatStatusHint: document.getElementById("chat-status-hint"),
  chatThread: document.getElementById("chat-thread"),
  claimReviewQueue: document.getElementById("claim-review-queue"),
  cycleOutput: document.getElementById("cycle-output"),
  corporateEventContext: document.getElementById("corporate-event-context"),
  durableClaims: document.getElementById("durable-claims"),
  focusBoard: document.getElementById("focus-board"),
  incidentBoard: document.getElementById("incident-board"),
  journalFeed: document.getElementById("journal-feed"),
  learningDetail: document.getElementById("learning-detail"),
  learningDistillations: document.getElementById("learning-distillations"),
  learningWorkflows: document.getElementById("learning-workflows"),
  macroResearchSignals: document.getElementById("macro-research-signals"),
  macroThemeWatchlists: document.getElementById("macro-theme-watchlists"),
  marketStateOverview: document.getElementById("market-state-overview"),
  marketStateTrail: document.getElementById("market-state-trail"),
  metricsGrid: document.getElementById("metrics-grid"),
  metricTemplate: document.getElementById("metric-card-template"),
  nextFocus: document.getElementById("next-focus"),
  openPositions: document.getElementById("open-positions"),
  operatorDisagreements: document.getElementById("operator-disagreements"),
  pipelineDetail: document.getElementById("pipeline-detail"),
  pipelinesList: document.getElementById("pipelines-list"),
  researchTasks: document.getElementById("research-tasks"),
  runtimeMemoryForm: document.getElementById("runtime-memory-form"),
  runtimeMemoryInspector: document.getElementById("runtime-memory-inspector"),
  runtimeMemoryPhaseInput: document.getElementById("runtime-memory-phase-input"),
  runtimeMemorySkillCodesInput: document.getElementById("runtime-memory-skill-codes-input"),
  runtimeMemoryStrategyInput: document.getElementById("runtime-memory-strategy-input"),
  runtimeSummary: document.getElementById("runtime-summary"),
  runtimeMemoryTickerInput: document.getElementById("runtime-memory-ticker-input"),
  skillActiveRevisions: document.getElementById("skill-active-revisions"),
  skillCandidates: document.getElementById("skill-candidates"),
  skillGaps: document.getElementById("skill-gaps"),
  statusBadge: document.getElementById("status-badge"),
  tickerTraceFeed: document.getElementById("ticker-trace-feed"),
  tickerTraceForm: document.getElementById("ticker-trace-form"),
  tickerTraceInput: document.getElementById("ticker-trace-input"),
  tickerTraceSummary: document.getElementById("ticker-trace-summary"),
  workQueue: document.getElementById("work-queue"),
};

const state = {
  chat: {
    conversations: [],
    presets: [],
    selectedConversation: null,
    selectedConversationId: null,
    showArchived: false,
  },
  dashboards: null,
  lastCycleResponse: null,
  learningCompareBaselineStore: {},
  learningDetail: {
    entityType: null,
    entityId: null,
    draft: null,
    compareBaselines: {},
    payload: null,
    runtimeMemory: null,
  },
  scheduler: null,
  selectedStrategyId: null,
  runtimeMemory: {
    payload: null,
    phase: "do",
    skillCodes: [],
    strategyVersionId: null,
    ticker: null,
  },
  trace: {
    payload: null,
    ticker: null,
  },
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

state.learningCompareBaselineStore = readLearningCompareBaselineStore();

elements.chatForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = elements.chatInput.value.trim();
  if (!message) return;
  await sendChatMessage(message);
});

elements.chatNewConversation?.addEventListener("click", async () => {
  await createChatConversation();
});

elements.chatArchive?.addEventListener("click", async () => {
  await archiveSelectedConversation();
});

elements.chatShowArchived?.addEventListener("change", async () => {
  state.chat.showArchived = Boolean(elements.chatShowArchived.checked);
  await loadChatWorkspace({ preserveSelection: true });
});

elements.chatLlmSelect?.addEventListener("change", async () => {
  const preset = elements.chatLlmSelect.value;
  if (!preset || !state.chat.selectedConversationId) return;
  await updateSelectedConversationPreset(preset);
});

elements.tickerTraceForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const ticker = elements.tickerTraceInput?.value?.trim();
  if (!ticker) return;
  await loadTickerTrace(ticker);
});

elements.runtimeMemoryForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const requestState = readRuntimeMemoryFormState();
  if (!requestState.ticker && requestState.strategyVersionId === null && !requestState.skillCodes.length) return;
  await loadRuntimeMemoryInspector(requestState);
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
  try {
    await Promise.all([refreshDashboard(), loadChatWorkspace({ preserveSelection: false })]);
  } catch (error) {
    setError(error);
  }
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
    skills,
    workflows,
    claims,
    operatorDisagreements,
    operatorDisagreementSummary,
    operatorDisagreementClusters,
    claimReviewQueue,
    journal,
    positions,
    marketStateSnapshots,
    macroContext,
    macroSignals,
    watchlists,
  ] = await Promise.all([
    request("/scheduler/status"),
    request("/strategy-health"),
    request("/strategy-health/pipelines"),
    request("/work-queue"),
    request("/research/tasks"),
    request("/strategy-evolution/candidate-validations"),
    request("/strategy-evolution/changes"),
    request("/strategy-evolution/activations"),
    request("/skills/dashboard"),
    request("/learning-workflows?limit=6"),
    request("/claims?limit=12"),
    request("/operator-disagreements?limit=12"),
    request("/operator-disagreements/summary"),
    request("/operator-disagreements/clusters?sync=true&limit=8&min_count=2"),
    request("/claims/review-queue?limit=12"),
    request("/journal"),
    request("/positions"),
    request("/macro/state-snapshots?limit=6"),
    request("/macro/context?limit=6"),
    request("/macro/signals?limit=8"),
    request("/watchlists"),
  ]);

  const corporateContextTickers = deriveCorporateContextTickers({
    journal,
    marketStateSnapshots,
    positions,
  });
  const corporateEventContexts = await Promise.all(
    corporateContextTickers.map((ticker) => fetchCorporateEventContext(ticker)),
  );

  state.scheduler = scheduler;
  state.dashboards = {
    activations,
    candidateValidations,
    changes,
    claimReviewQueue,
    claims,
    operatorDisagreements,
    operatorDisagreementSummary,
    operatorDisagreementClusters,
    corporateEventContexts,
    health,
    journal,
    workflows,
    macroContext,
    macroSignals,
    marketStateSnapshots,
    positions,
    pipelines,
    queue,
    researchTasks,
    skills,
    watchlists,
  };

  if (!pipelines.length) {
    state.selectedStrategyId = null;
  } else if (!pipelines.some((item) => item.strategy_id === state.selectedStrategyId)) {
    state.selectedStrategyId = pipelines[0].strategy_id;
  }

  renderDashboard();
  await refreshLearningDetail({ silent: true });
  await refreshTickerTraceFromDashboard();
  await refreshRuntimeMemoryInspectorFromDashboard();
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
    skills,
    corporateEventContexts,
    claimReviewQueue,
    claims,
    operatorDisagreements,
    operatorDisagreementSummary,
    operatorDisagreementClusters,
    journal,
    macroContext,
    macroSignals,
    marketStateSnapshots,
    positions,
    workflows,
    watchlists,
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
    calls_last_hour: 0,
    calls_today: 0,
    last_error: null,
    last_decision_summary: null,
  };
  const marketData = state.scheduler.market_data || {
    provider: "stub",
    probe_ticker: "SPY",
    status: "unknown",
    ready: false,
    using_fallback: true,
    source: null,
    last_price: null,
    provider_error: null,
    last_checked_at: null,
  };
  const governance = state.scheduler.learning_governance || {
    enabled: false,
    status: "idle",
    interval_minutes: 30,
    last_sync_started_at: null,
    last_sync_completed_at: null,
    sync_runs: 0,
    last_summary: null,
    last_error: null,
    last_changed_workflows: 0,
    last_open_workflows: 0,
    last_open_items: 0,
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
  const macroThemeWatchlists = watchlists.filter((watchlist) => String(watchlist.code || "").startsWith("macro_"));

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
    ["LLM ultima hora", ai.calls_last_hour || 0, "Incluye runtime autonomo y respuestas de chat con modelo real"],
    ["LLM hoy", ai.calls_today || 0, "Acumulado UTC del dia actual"],
    ["Learning loop", governance.enabled ? humanizeLabel(governance.status || "idle") : "OFF", summarizeGovernance(governance)],
    ["Market data", marketData.ready ? "REAL" : humanizeLabel(marketData.status || "fallback"), summarizeMarketDataStatus(marketData)],
    ["Incidencias abiertas", openIncidents.length, openIncidents.length ? "Revisar antes de reanudar" : "Sin errores bloqueantes"],
    ["Ciclos autonomos", bot.cycle_runs, bot.last_cycle_completed_at ? `Ultimo cierre: ${formatDate(bot.last_cycle_completed_at)}` : "Aun no ha cerrado ciclos"],
    ["Cadencia", formatCadence(bot), cadenceDescription(bot)],
    ["Regimen activo", latestMarketState ? humanizeLabel(latestMarketState.regime_label) : "PENDIENTE", latestMarketState ? `${(latestMarketState.pdca_phase || "general").toUpperCase()} · ${formatDate(latestMarketState.created_at)}` : "Ejecuta PLAN o DO para fijarlo"],
    ["Estrategias activas", activeStrategies, `${pipelines.length} pipelines visibles`],
    ["Versiones candidatas", candidateVersions, "Pendientes de validar o promover"],
    ["Research abierto", openResearch.length, "Incluye recovery research"],
    ["Macro signals", macroSignals.length, "Tesis macro/geopoliticas persistidas"],
    ["Macro watchlists", macroThemeWatchlists.length, "Universos tematicos ligados al research macro"],
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
  renderMacroResearchSignals(macroSignals);
  renderMacroThemeWatchlists(watchlists);
  renderCandidateValidations(candidateValidations);
  renderActivityFeed(changes, activations);
  renderSkillsDashboard(skills);
  renderClaimReviewQueue(claimReviewQueue);
  renderDurableClaims(claims);
  renderOperatorDisagreements(operatorDisagreements, operatorDisagreementSummary, operatorDisagreementClusters);
  renderLearningWorkflows(workflows);
  renderLearningDetail();
  renderOpenPositions(positions, pipelines);
  renderCorporateEventContexts(corporateEventContexts);
  renderJournalFeed(journal);
  renderCycleOutput();
}

function renderMarketState(snapshots, macroContext) {
  if (!elements.marketStateOverview || !elements.marketStateTrail) return;

  const latest = Array.isArray(snapshots) && snapshots.length ? snapshots[0] : null;
  const fallbackMacro = asObject(macroContext);
  const fallbackIndicators = Array.isArray(fallbackMacro.indicators) ? fallbackMacro.indicators : [];

  if (!latest) {
    elements.marketStateOverview.innerHTML = `
      <article class="world-state-card">
        <span class="focus-kicker">Snapshot pendiente</span>
        <h3>El bot todavia no ha capturado un Market State Snapshot persistido.</h3>
        <p class="muted">${escapeHtml(fallbackMacro.summary || "Ejecuta PLAN o DO para fijar el estado del mundo antes de decidir.")}</p>
        <div class="row">${renderPillList((fallbackMacro.active_regimes || []).slice(0, 4), "pill-candidate", "Sin regimen activo")}</div>
        <div class="snapshot-list">
          ${renderSnapshotItems(
            fallbackIndicators.slice(0, 6).map((indicator) => ({
              title: indicator.label || indicator.key || "Indicador",
              detail: summarizeMacroIndicator(indicator),
              aside: formatMacroIndicatorValue(indicator),
            })),
            "Sin indicadores macro externos disponibles.",
          )}
        </div>
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
  const macroIndicators = Array.isArray(macro.indicators) ? macro.indicators : fallbackIndicators;
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
              macroIndicators.slice(0, 6).map((indicator) => ({
                title: indicator.label || indicator.key || "Indicador",
                detail: summarizeMacroIndicator(indicator),
                aside: formatMacroIndicatorValue(indicator),
              })),
              "Sin indicadores macro externos disponibles.",
            )}
          </div>
          <div class="snapshot-list">
            ${renderSnapshotItems(
              calendarEvents.slice(0, 4).map((event) => ({
                title: event.title || event.event_type || "Evento",
                detail: summarizeMacroCalendarEvent(event),
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

function summarizeGovernance(context) {
  if (!context.enabled) {
    return "Gobernanza de workflows desactivada";
  }
  const lastSync = context.last_sync_completed_at
    ? formatDate(context.last_sync_completed_at)
    : "sin sync";
  const cadence = `${context.interval_minutes || 0}m`;
  if (context.last_error) {
    return `${cadence} · ${lastSync} · ${context.last_error}`;
  }
  return `${cadence} · ${lastSync} · ${context.last_open_workflows || 0} workflows abiertos / ${context.last_open_items || 0} items`;
}

function summarizeMarketDataStatus(context) {
  const provider = context.provider || "market_data";
  const source = context.source || provider;
  const checkedAt = context.last_checked_at ? formatDate(context.last_checked_at) : "sin sonda";
  const ticker = context.probe_ticker || "SPY";
  const price = typeof context.last_price === "number" ? ` · ${ticker} ${context.last_price.toFixed(2)}` : "";
  if (context.provider_error) {
    return `${source} · ${checkedAt}${price} · ${context.provider_error}`;
  }
  return `${source} · ${checkedAt}${price}`;
}

function runtimeBudgetCount(contextBudget, section, key, fallbackKey) {
  const sectionPayload = asObject(asObject(contextBudget)[section]);
  const directValue = sectionPayload[key];
  if (Number.isFinite(directValue)) return Number(directValue);
  const fallbackValue = asObject(contextBudget)[fallbackKey];
  if (Number.isFinite(fallbackValue)) return Number(fallbackValue);
  return null;
}

function normalizeTicker(value) {
  const normalized = String(value || "").trim().toUpperCase();
  return normalized || null;
}

function parseCommaSeparatedList(value) {
  const seen = new Set();
  return String(value || "")
    .split(",")
    .map((item) => item.trim())
    .filter((item) => {
      if (!item || seen.has(item)) return false;
      seen.add(item);
      return true;
    });
}

function summarizeContextBudget(contextBudget) {
  const payload = asObject(contextBudget);
  if (!Object.keys(payload).length) return null;
  const skillLoaded = runtimeBudgetCount(payload, "runtime_skills", "loaded_count", "loaded_runtime_skill_count");
  const skillAvailable = runtimeBudgetCount(payload, "runtime_skills", "available_count", "available_runtime_skill_count");
  const skillTrimmed = runtimeBudgetCount(payload, "runtime_skills", "truncated_count", "truncated_runtime_skill_count") || 0;
  const claimLoaded = runtimeBudgetCount(payload, "runtime_claims", "loaded_count", "loaded_runtime_claim_count");
  const claimAvailable = runtimeBudgetCount(payload, "runtime_claims", "available_count", "available_runtime_claim_count");
  const claimTrimmed = runtimeBudgetCount(payload, "runtime_claims", "truncated_count", "truncated_runtime_claim_count") || 0;
  const distillationLoaded = runtimeBudgetCount(
    payload,
    "runtime_distillations",
    "loaded_count",
    "loaded_runtime_distillation_count",
  );
  const distillationAvailable = runtimeBudgetCount(
    payload,
    "runtime_distillations",
    "available_count",
    "available_runtime_distillation_count",
  );
  const distillationTrimmed = runtimeBudgetCount(
    payload,
    "runtime_distillations",
    "truncated_count",
    "truncated_runtime_distillation_count",
  ) || 0;
  if (
    ![skillLoaded, skillAvailable, claimLoaded, claimAvailable, distillationLoaded, distillationAvailable]
      .some((value) => Number.isFinite(value))
  ) return null;
  const loadedTotal = (skillLoaded || 0) + (claimLoaded || 0) + (distillationLoaded || 0);
  const availableTotal = (skillAvailable || 0) + (claimAvailable || 0) + (distillationAvailable || 0);
  const detailParts = [];
  if (Number.isFinite(skillLoaded) || Number.isFinite(skillAvailable)) {
    detailParts.push(`skills ${skillLoaded ?? 0}/${skillAvailable ?? skillLoaded ?? 0}`);
  }
  if (Number.isFinite(claimLoaded) || Number.isFinite(claimAvailable)) {
    detailParts.push(`claims ${claimLoaded ?? 0}/${claimAvailable ?? claimLoaded ?? 0}`);
  }
  if (Number.isFinite(distillationLoaded) || Number.isFinite(distillationAvailable)) {
    detailParts.push(`distill ${distillationLoaded ?? 0}/${distillationAvailable ?? distillationLoaded ?? 0}`);
  }
  const trimmedTotal = skillTrimmed + claimTrimmed + distillationTrimmed;
  if (trimmedTotal > 0) detailParts.push(`trimmed ${trimmedTotal}`);
  return {
    headline: availableTotal > 0 ? `${loadedTotal}/${availableTotal} runtime items` : `${loadedTotal} runtime items`,
    detail: detailParts.join(" · "),
    truncated: trimmedTotal > 0,
  };
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
            pills: [pill(topItem.priority, priorityClass(topItem.priority)), pill(humanizeLabel(topItem.item_type), "pill-info")],
            body: formatWorkQueueItemContext(topItem),
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
  const items = Array.isArray(queue?.items) ? queue.items : [];
  const summary = asObject(queue?.summary);
  const summaryLines = buildWorkQueueSummaryLines(summary);

  elements.workQueue.innerHTML = "";

  if (summaryLines.length) {
    const summaryNode = document.createElement("article");
    summaryNode.className = "stack-item";
    summaryNode.innerHTML = `
      <h3>Estado del runtime</h3>
      <div class="row">
        ${pill(`${Number(summary.due_reanalysis_items) || 0} due`, Number(summary.due_reanalysis_items) > 0 ? "pill-reject" : "pill-approved")}
        ${pill(`${Number(summary.deferred_reanalysis_items) || 0} deferred`, Number(summary.deferred_reanalysis_items) > 0 ? "pill-degraded" : "pill-approved")}
        ${Number(summary.timing_samples) > 0 ? pill(`${Number(summary.timing_samples)} timings`, "pill-info") : ""}
      </div>
      ${summaryLines.map((line) => `<p class="muted">${escapeHtml(line)}</p>`).join("")}
    `;
    elements.workQueue.appendChild(summaryNode);
  }

  if (!items.length) {
    const emptyNode = document.createElement("article");
    emptyNode.className = "stack-item";
    emptyNode.innerHTML = `<p class="muted">${
      summaryLines.length
        ? "No hay items accionables en la cola ahora mismo."
        : "La cola de trabajo esta vacia."
    }</p>`;
    elements.workQueue.appendChild(emptyNode);
    return;
  }

  items.forEach((item) => {
    const node = document.createElement("article");
    node.className = "stack-item";
    node.innerHTML = `
      <h3>${escapeHtml(item.title)}</h3>
      <div class="row">
        ${pill(item.priority, priorityClass(item.priority))}
        ${pill(humanizeLabel(item.item_type), "pill-info")}
      </div>
      <p class="muted">${escapeHtml(formatWorkQueueItemContext(item))}</p>
    `;
    elements.workQueue.appendChild(node);
  });
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

function renderMacroResearchSignals(signals) {
  renderStackList(
    elements.macroResearchSignals,
    signals,
    (signal) => {
      const meta = asObject(signal.meta);
      const evidence = asObject(meta.evidence);
      const tickers = Array.isArray(meta.tickers) ? meta.tickers : [];
      const strategyIdeas = Array.isArray(evidence.strategy_ideas) ? evidence.strategy_ideas : [];
      const provider = evidence.provider ? `${evidence.provider}${evidence.model ? ` · ${evidence.model}` : ""}` : null;
      return `
        <h3>${escapeHtml(meta.scenario || humanizeLabel(meta.relevance || signal.key || "Macro signal"))}</h3>
        <div class="row">
          ${pill(humanizeLabel(meta.regime || "macro"), "pill-candidate")}
          ${pill(humanizeLabel(meta.relevance || "general"), "pill-info")}
          ${pill(describeMacroAnalysisMode(evidence.analysis_mode), evidence.analysis_mode === "ai" ? "pill-active" : "pill-approved")}
          ${pill(`imp ${formatConfidencePct(signal.importance)}`, "pill-approved")}
        </div>
        <p class="muted">${escapeHtml(signal.content || "Sin contenido persistido.")}</p>
        <p class="muted">${escapeHtml(summarizeMacroSignalMeta({
          tickers,
          timeframe: meta.timeframe,
          source: meta.source,
          provider,
          strategyIdeas,
          createdAt: signal.created_at,
        }))}</p>
      `;
    },
    "Aun no hay senales macro/geopoliticas persistidas. La rama existe, pero todavia no ha fijado una tesis nueva en memoria.",
  );
}

function renderMacroThemeWatchlists(watchlists) {
  const thematicWatchlists = watchlists.filter((watchlist) => String(watchlist.code || "").startsWith("macro_"));
  renderStackList(
    elements.macroThemeWatchlists,
    thematicWatchlists,
    (watchlist) => {
      const items = Array.isArray(watchlist.items) ? watchlist.items : [];
      const firstItemMetrics = asObject(items[0]?.key_metrics);
      const strategyIdeas = Array.isArray(firstItemMetrics.strategy_ideas) ? firstItemMetrics.strategy_ideas : [];
      const tickers = items.map((item) => item.ticker).filter(Boolean);
      return `
        <h3>${escapeHtml(watchlist.name || watchlist.code || "Macro watchlist")}</h3>
        <div class="row">
          ${pill(watchlist.code || "macro", "pill-info")}
          ${pill(watchlist.status || "active", watchlist.status === "active" ? "pill-active" : "pill-approved")}
          ${pill(`${items.length} tickers`, "pill-candidate")}
        </div>
        <p class="muted">${escapeHtml(watchlist.hypothesis || "Sin hipotesis registrada.")}</p>
        <p class="muted">${escapeHtml(summarizeMacroWatchlistMeta({
          regime: firstItemMetrics.regime,
          tickers,
          strategyIdeas,
          createdAt: watchlist.created_at,
        }))}</p>
      `;
    },
    "No hay watchlists tematicas macro creadas todavia.",
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

function renderSkillsDashboard(skillsDashboard) {
  const candidates = Array.isArray(skillsDashboard?.candidates) ? skillsDashboard.candidates : [];
  const revisions = Array.isArray(skillsDashboard?.active_revisions) ? skillsDashboard.active_revisions : [];
  const gaps = Array.isArray(skillsDashboard?.gaps) ? skillsDashboard.gaps : [];
  const distillations = Array.isArray(skillsDashboard?.distillations) ? skillsDashboard.distillations : [];

  renderStackList(
    elements.skillCandidates,
    candidates,
    (candidate) => {
      const statusClass = candidate.candidate_status === "validated"
        ? "pill-active"
        : candidate.candidate_status === "rejected"
          ? "pill-reject"
          : "pill-candidate";
      const activationClass = candidate.activation_status === "active"
        ? "pill-active"
        : candidate.activation_status === "pending_catalog_integration"
          ? "pill-degraded"
          : "pill-info";
      const canValidate = !["validated", "rejected"].includes(candidate.candidate_status);
      return `
        <h3>${escapeHtml(candidate.target_skill_code || candidate.key || "skill_candidate")}</h3>
        <div class="row">
          ${pill(candidate.candidate_status, statusClass)}
          ${candidate.activation_status ? pill(candidate.activation_status, activationClass) : ""}
          ${candidate.ticker ? pill(candidate.ticker, "pill-info") : ""}
          ${candidate.source_trade_review_id ? pill(`review ${candidate.source_trade_review_id}`, "pill-approved") : ""}
        </div>
        <p class="muted">${escapeHtml(candidate.summary || "Sin resumen.")}</p>
        <p class="muted">${escapeHtml(`accion=${candidate.candidate_action || "draft"} · ${formatDate(candidate.created_at)}`)}</p>
        ${
          canValidate
            ? `
              <div class="row skill-action-row">
                <button type="button" class="action action-success skill-validate-button" data-candidate-id="${candidate.id}" data-validation-mode="paper" data-validation-outcome="approve">Paper OK</button>
                <button type="button" class="action action-muted skill-validate-button" data-candidate-id="${candidate.id}" data-validation-mode="replay" data-validation-outcome="approve">Replay OK</button>
                <button type="button" class="action action-alert skill-validate-button" data-candidate-id="${candidate.id}" data-validation-mode="paper" data-validation-outcome="reject">Reject</button>
              </div>
            `
            : ""
        }
      `;
    },
    "No hay skill candidates pendientes.",
  );

  if (elements.skillCandidates) {
    elements.skillCandidates.querySelectorAll(".skill-validate-button").forEach((button) => {
      button.addEventListener("click", async () => {
        const candidateId = Number(button.dataset.candidateId);
        const validationMode = button.dataset.validationMode;
        const validationOutcome = button.dataset.validationOutcome;
        if (!Number.isFinite(candidateId) || !validationMode || !validationOutcome) return;
        button.disabled = true;
        try {
          await openLearningDetailWithDraft("skill_candidate", candidateId, {
            kind: "skill_candidate_validation",
            candidateId,
            validationMode,
            validationOutcome,
            summary: "",
            title: `${button.textContent?.trim() || "Validate"} candidate ${candidateId}`,
            submitLabel: button.textContent?.trim() || "Apply",
            subtitle: "Resume por que este candidate merece paper, replay o rechazo.",
          });
        } finally {
          button.disabled = false;
        }
      });
    });
  }

  renderStackList(
    elements.skillActiveRevisions,
    revisions,
    (revision) => `
      <h3>${escapeHtml(revision.skill_code || "revision")}</h3>
      <div class="row">
        ${pill(revision.activation_status || "inactive", revision.activation_status === "active" ? "pill-active" : "pill-degraded")}
        ${revision.validation_mode ? pill(revision.validation_mode, "pill-info") : ""}
        ${revision.ticker ? pill(revision.ticker, "pill-approved") : ""}
        ${revision.source_trade_review_id ? pill(`review ${revision.source_trade_review_id}`, "pill-candidate") : ""}
        ${renderLearningDetailButton({ entityType: "skill_revision", entityId: revision.id })}
      </div>
      <p class="muted">${escapeHtml(revision.revision_summary || "Sin resumen.")}</p>
      <p class="muted">${escapeHtml(`candidate=${revision.candidate_id ?? "n/a"} · ${formatDate(revision.created_at)}`)}</p>
    `,
    "No hay skill revisions activas todavia.",
  );
  attachLearningPanelButtons(elements.skillActiveRevisions);

  renderStackList(
    elements.skillGaps,
    gaps,
    (gap) => `
      <h3>${escapeHtml(gap.target_skill_code || gap.gap_type || "skill_gap")}</h3>
      <div class="row">
        ${pill(gap.gap_type || "gap", "pill-reject")}
        ${pill(gap.status || "open", gap.status === "open" ? "pill-degraded" : "pill-info")}
        ${gap.ticker ? pill(gap.ticker, "pill-info") : ""}
        ${gap.source_trade_review_id ? pill(`review ${gap.source_trade_review_id}`, "pill-candidate") : ""}
        ${renderLearningDetailButton({ entityType: "skill_gap", entityId: gap.id })}
      </div>
      <p class="muted">${escapeHtml(gap.summary || "Sin resumen.")}</p>
      <p class="muted">${escapeHtml(`skill=${gap.linked_skill_code || "none"} · accion=${gap.candidate_action || "n/a"} · ${formatDate(gap.created_at)}`)}</p>
      <div class="row skill-action-row">
        ${renderSkillGapActions(gap)}
      </div>
    `,
    "No hay skill gaps detectados.",
  );

  renderStackList(
    elements.learningDistillations,
    distillations,
    (digest) => {
      const meta = asObject(digest?.meta);
      const distillationType = String(meta.distillation_type || digest.memory_type || "digest").trim();
      const reviewStatus = String(meta.review_status || "pending").trim().toLowerCase();
      const reviewClass = reviewStatus === "applied" ? "pill-approved" : "pill-degraded";
      const reviewAction = String(meta.review_action || "").trim();
      const targetSkillCode = String(meta.target_skill_code || "").trim();
      const ticker = String(meta.ticker || "").trim();
      return `
        <h3>${escapeHtml(targetSkillCode || digest.key || "learning_distillation")}</h3>
        <div class="row">
          ${pill(distillationType, "pill-info")}
          ${pill(reviewStatus, reviewClass)}
          ${reviewAction ? pill(reviewAction, "pill-candidate") : ""}
          ${ticker ? pill(ticker, "pill-approved") : ""}
          ${renderLearningDetailButton({ entityType: "distillation_digest", entityId: digest.id })}
        </div>
        <p class="muted">${escapeHtml(digest.content || "Sin resumen.")}</p>
        <p class="muted">${escapeHtml(`scope=${digest.scope || "n/a"} · imp=${formatNumber(digest.importance)} · ${formatDate(meta.reviewed_at || digest.created_at)}`)}</p>
        <div class="row skill-action-row">
          ${renderDistillationReviewActions(digest)}
        </div>
      `;
    },
    "No hay digestos de distillation recientes.",
  );
  attachLearningPanelButtons(elements.learningDistillations);
}

function renderClaimReviewQueue(items) {
  renderStackList(
    elements.claimReviewQueue,
    Array.isArray(items) ? items : [],
    (item) => {
      const isRetired = item.status === "retired";
      const statusClass = item.status === "contested"
        ? "pill-degraded"
        : item.status === "contradicted"
          ? "pill-reject"
          : item.status === "validated"
            ? "pill-active"
            : "pill-info";
      const freshnessClass = item.freshness_state === "stale"
        ? "pill-reject"
        : item.freshness_state === "aging"
          ? "pill-degraded"
          : "pill-approved";
      return `
        <h3>${escapeHtml(item.claim_text || item.review_reason || "Claim review")}</h3>
        <div class="row">
          ${pill(item.status || "unknown", statusClass)}
          ${pill(item.freshness_state || "unknown", freshnessClass)}
          ${item.review_reason ? pill(item.review_reason, "pill-candidate") : ""}
          ${item.linked_ticker ? pill(item.linked_ticker, "pill-info") : ""}
        </div>
        <p class="muted">${escapeHtml(`support=${item.support_count ?? 0} · contradiction=${item.contradiction_count ?? 0} · evidence=${item.evidence_count ?? 0} · confidence=${formatNumber(item.confidence)}`)}</p>
        ${
          !isRetired
            ? `
              <div class="row skill-action-row">
                <button type="button" class="action action-success claim-review-button" data-claim-id="${item.claim_id}" data-review-outcome="confirm">Confirmar</button>
                <button type="button" class="action action-muted claim-review-button" data-claim-id="${item.claim_id}" data-review-outcome="contradict">Contradecir</button>
                <button type="button" class="action action-alert claim-review-button" data-claim-id="${item.claim_id}" data-review-outcome="retire">Retirar</button>
              </div>
            `
            : ""
        }
      `;
    },
    "No hay claims pendientes de revision.",
  );

  attachLearningPanelButtons(elements.claimReviewQueue);
}

function renderDurableClaims(claims) {
  renderStackList(
    elements.durableClaims,
    Array.isArray(claims) ? claims : [],
    (claim) => {
      const statusClass = claim.status === "validated"
        ? "pill-active"
        : claim.status === "supported"
          ? "pill-approved"
          : claim.status === "contested"
            ? "pill-degraded"
            : claim.status === "contradicted"
              ? "pill-reject"
              : "pill-info";
      const freshnessClass = claim.freshness_state === "stale"
        ? "pill-reject"
        : claim.freshness_state === "aging"
          ? "pill-degraded"
          : "pill-approved";
      const source = asObject(claim.meta).source;
      const linkedCandidateId = asObject(claim.meta).linked_skill_candidate_id;
      const canPromote = !linkedCandidateId && ["supported", "validated"].includes(claim.status);
      return `
        <h3>${escapeHtml(claim.claim_text || claim.key || "Claim")}</h3>
        <div class="row">
          ${pill(claim.status || "unknown", statusClass)}
          ${pill(claim.freshness_state || "unknown", freshnessClass)}
          ${claim.claim_type ? pill(claim.claim_type, "pill-candidate") : ""}
          ${claim.linked_ticker ? pill(claim.linked_ticker, "pill-info") : ""}
          ${linkedCandidateId ? pill(`candidate ${linkedCandidateId}`, "pill-active") : ""}
          ${source ? pill(source, "pill-info") : ""}
        </div>
        <p class="muted">${escapeHtml(`support=${claim.support_count ?? 0} · contradiction=${claim.contradiction_count ?? 0} · evidence=${claim.evidence_count ?? 0} · confidence=${formatNumber(claim.confidence)}`)}</p>
        <p class="muted">${escapeHtml(`scope=${claim.scope || "n/a"} · reviewed=${formatDate(claim.last_reviewed_at || claim.updated_at || claim.created_at)}`)}</p>
        ${
          canPromote
            ? `<div class="row skill-action-row"><button type="button" class="action action-success claim-promote-button" data-claim-id="${claim.id}">Promote</button></div>`
            : ""
        }
      `;
    },
    "Todavia no hay durable claims persistidos.",
  );
  attachLearningPanelButtons(elements.durableClaims);
}

function renderOperatorDisagreements(items, summary, clusters) {
  const disagreementItems = Array.isArray(items) ? items : [];
  const disagreementSummary = asObject(summary);
  const disagreementClusters = Array.isArray(clusters) ? clusters : [];
  const summaryParts = [
    renderOperatorDisagreementBuckets("tipo", disagreementSummary.by_disagreement_type),
    renderOperatorDisagreementBuckets("entity", disagreementSummary.by_entity_type),
    renderOperatorDisagreementBuckets("ticker", disagreementSummary.by_ticker),
    renderOperatorDisagreementBuckets("skill/claim", disagreementSummary.by_target_skill_code),
  ]
    .filter(Boolean)
    .join("");
  if (!elements.operatorDisagreements) return;
  elements.operatorDisagreements.innerHTML = "";
  if (summaryParts) {
    elements.operatorDisagreements.insertAdjacentHTML(
      "beforeend",
      `<article class="stack-item"><div class="snapshot-list">${summaryParts}</div></article>`,
    );
  }
  if (disagreementClusters.length) {
    const clusterMarkup = disagreementClusters
      .map(
        (cluster) => `
          <div class="snapshot-item">
            <div>
              <strong>${escapeHtml(cluster.target_skill_code || cluster.claim_key || cluster.disagreement_type || "cluster")}</strong>
              <p>${escapeHtml(`${cluster.event_count || 0} eventos · ${cluster.ticker || "global"} · ${cluster.entity_type || "entity"}`)}</p>
            </div>
            <span class="row">
              ${cluster.status ? pill(cluster.status, cluster.status === "promoted" ? "pill-active" : "pill-degraded") : ""}
              ${cluster.promoted_claim_id ? pill(`claim ${cluster.promoted_claim_id}`, "pill-approved") : ""}
              ${cluster.promoted_skill_gap_id ? pill(`gap ${cluster.promoted_skill_gap_id}`, "pill-candidate") : ""}
              ${
                !cluster.promoted_claim_id
                  ? `<button type="button" class="action action-success operator-disagreement-cluster-promote-button" data-cluster-id="${cluster.id}">Promote Claim</button>`
                  : ""
              }
              ${
                !cluster.promoted_skill_gap_id
                  ? `<button type="button" class="action action-muted operator-disagreement-cluster-promote-gap-button" data-cluster-id="${cluster.id}">Promote Gap</button>`
                  : ""
              }
            </span>
          </div>
        `,
      )
      .join("");
    elements.operatorDisagreements.insertAdjacentHTML(
      "beforeend",
      `<article class="stack-item"><h3>Repeated disagreement clusters</h3><div class="snapshot-list">${clusterMarkup}</div></article>`,
    );
  }
  if (!disagreementItems.length) {
    if (!summaryParts && !disagreementClusters.length) {
      elements.operatorDisagreements.innerHTML = `<div class="stack-item"><p class="muted">Todavia no hay desacuerdos estructurados del operador.</p></div>`;
    }
    attachLearningPanelButtons(elements.operatorDisagreements);
    return;
  }
  disagreementItems.forEach((item) => {
    const node = document.createElement("article");
    node.className = "stack-item";
    node.innerHTML = `
      <h3>${escapeHtml(item.summary || item.disagreement_type || "Operator disagreement")}</h3>
      <div class="row">
        ${item.disagreement_type ? pill(item.disagreement_type, "pill-degraded") : ""}
        ${item.entity_type ? pill(item.entity_type, "pill-info") : ""}
        ${item.ticker ? pill(item.ticker, "pill-approved") : ""}
        ${asObject(item.details).target_skill_code ? pill(asObject(item.details).target_skill_code, "pill-candidate") : ""}
        ${asObject(item.details).claim_key ? pill(asObject(item.details).claim_key, "pill-candidate") : ""}
      </div>
      <p class="muted">${escapeHtml(`action=${item.action || "n/a"} · source=${item.source || "n/a"} · created=${formatDate(item.created_at)}`)}</p>
    `;
    elements.operatorDisagreements.appendChild(node);
  });
  attachLearningPanelButtons(elements.operatorDisagreements);
}

function renderOperatorDisagreementBuckets(label, items) {
  const rows = Array.isArray(items) ? items : [];
  if (!rows.length) return "";
  return `
    <div class="snapshot-item">
      <span>${escapeHtml(label)}</span>
      <span class="muted">${escapeHtml(rows.map((item) => `${item.label} (${item.count})`).join(" · "))}</span>
    </div>
  `;
}

function renderLearningWorkflows(workflows) {
  renderStackList(
    elements.learningWorkflows,
    Array.isArray(workflows) ? workflows : [],
    (workflow) => {
      const statusClass = workflow.status === "resolved"
        ? "pill-approved"
        : workflow.priority === "high"
          ? "pill-reject"
          : "pill-degraded";
      const previewItems = Array.isArray(workflow.items) ? workflow.items.slice(0, 3) : [];
      return `
        <h3>${escapeHtml(workflow.title || workflow.workflow_type || "Workflow")}</h3>
        <div class="row">
          ${pill(workflow.status || "unknown", statusClass)}
          ${pill(workflow.priority || "normal", workflow.priority === "high" ? "pill-reject" : "pill-info")}
          ${pill(`${workflow.open_item_count ?? 0}/${workflow.item_count ?? 0} open`, "pill-candidate")}
        </div>
        <p class="muted">${escapeHtml(workflow.summary || "Sin resumen.")}</p>
        <p class="muted">${escapeHtml(`type=${workflow.workflow_type || "n/a"} · synced=${formatDate(workflow.last_synced_at || workflow.updated_at || workflow.created_at)}`)}</p>
        ${
          previewItems.length
            ? `
              <div class="snapshot-list">
                ${previewItems
                  .map(
                    (item) => `
                      <div class="snapshot-item">
                        <span>${escapeHtml(item.title || item.item_type || "item")}</span>
                        <span class="muted">${escapeHtml(`${item.item_type || "item"} · ${item.action_hint || item.status || "pending"}`)}</span>
                        <span class="row">
                          ${renderLearningDetailButton(mapWorkflowItemToEntity(item))}
                          ${renderWorkflowItemActions(workflow, item)}
                        </span>
                      </div>
                    `,
                  )
                  .join("")}
              </div>
            `
            : `<p class="muted">No hay items activos en este workflow.</p>`
        }
        ${
          Array.isArray(workflow.history) && workflow.history.length
            ? `
              <div class="snapshot-list">
                ${workflow.history
                  .slice(0, 3)
                  .map((entry) => renderWorkflowHistoryEntry(entry))
                  .join("")}
              </div>
            `
            : `<p class="muted">Sin historial operativo reciente.</p>`
        }
      `;
    },
    "Todavia no hay workflows de aprendizaje persistidos.",
  );

  attachLearningPanelButtons(elements.learningWorkflows);
}

function renderWorkflowHistoryEntry(entry) {
  const eventType = String(entry?.event_type || "history");
  const statusClass = eventType === "action"
    ? workflowResolutionPillClass(entry?.resolution_outcome)
    : "pill-info";
  const label = entry?.resolution_class || entry?.change_class || eventType;
  const metaBits = [];
  if (entry?.item_type) metaBits.push(entry.item_type);
  if (Number.isFinite(entry?.entity_id)) metaBits.push(`#${entry.entity_id}`);
  if (entry?.action) metaBits.push(entry.action);
  if (typeof entry?.open_item_count_after === "number") metaBits.push(`open=${entry.open_item_count_after}`);
  if (Array.isArray(entry?.added_items) && entry.added_items.length) metaBits.push(`+${entry.added_items.length}`);
  if (Array.isArray(entry?.removed_items) && entry.removed_items.length) metaBits.push(`-${entry.removed_items.length}`);
  return `
    <div class="snapshot-item">
      <span>${escapeHtml(entry?.summary || "Workflow event")}</span>
      <span class="muted">${escapeHtml(`${formatDate(entry?.timestamp)} · ${metaBits.join(" · ") || "sin detalle adicional"}`)}</span>
      <span class="row">
        ${pill(label, statusClass)}
        ${renderLearningDetailButton(mapHistoryEntryToEntity(entry))}
      </span>
    </div>
  `;
}

function workflowResolutionPillClass(outcome) {
  const normalized = String(outcome || "").trim().toLowerCase();
  if (normalized === "accepted") return "pill-approved";
  if (normalized === "rejected" || normalized === "retired") return "pill-reject";
  if (normalized === "dismissed") return "pill-degraded";
  return "pill-info";
}

function renderLearningDetailButton(target) {
  const entityType = String(target?.entityType || "").trim();
  const entityId = Number(target?.entityId);
  if (!entityType || !Number.isFinite(entityId)) return "";
  return `<button type="button" class="action action-muted learning-detail-button" data-entity-type="${escapeHtml(entityType)}" data-entity-id="${entityId}">Open</button>`;
}

function mapWorkflowItemToEntity(item) {
  const itemType = String(item?.item_type || "").trim();
  const entityId = Number(item?.entity_id);
  if (!Number.isFinite(entityId)) return null;
  if (itemType === "claim_review") return { entityType: "claim", entityId };
  if (itemType === "skill_gap") return { entityType: "skill_gap", entityId };
  if (itemType === "skill_candidate_audit") return { entityType: "skill_candidate", entityId };
  return null;
}

function mapHistoryEntryToEntity(entry) {
  return mapWorkflowItemToEntity({
    item_type: entry?.item_type,
    entity_id: entry?.entity_id,
  });
}

function renderWorkflowItemActions(workflow, item, { inlineReview = false } = {}) {
  const workflowId = Number(workflow.id);
  const entityId = Number(item.entity_id);
  const itemType = item.item_type;
  if (!Number.isFinite(workflowId) || !Number.isFinite(entityId) || !itemType) return "";
  if (itemType === "claim_review") {
    if (inlineReview) {
      return `
        <button type="button" class="action action-success learning-inline-review-button" data-kind="workflow_action" data-workflow-id="${workflowId}" data-item-type="${itemType}" data-entity-id="${entityId}" data-action="confirm" data-action-label="Confirm claim review ${entityId} in workflow ${workflowId}">Confirm</button>
        <button type="button" class="action action-muted learning-inline-review-button" data-kind="workflow_action" data-workflow-id="${workflowId}" data-item-type="${itemType}" data-entity-id="${entityId}" data-action="contradict" data-action-label="Contradict claim review ${entityId} in workflow ${workflowId}">Contradict</button>
        <button type="button" class="action action-alert learning-inline-review-button" data-kind="workflow_action" data-workflow-id="${workflowId}" data-item-type="${itemType}" data-entity-id="${entityId}" data-action="retire" data-action-label="Retire claim review ${entityId} in workflow ${workflowId}">Retire</button>
      `;
    }
    return `
      <button type="button" class="action action-success workflow-action-button" data-workflow-id="${workflowId}" data-item-type="${itemType}" data-entity-id="${entityId}" data-action="confirm">Confirm</button>
      <button type="button" class="action action-muted workflow-action-button" data-workflow-id="${workflowId}" data-item-type="${itemType}" data-entity-id="${entityId}" data-action="contradict">Contradict</button>
      <button type="button" class="action action-alert workflow-action-button" data-workflow-id="${workflowId}" data-item-type="${itemType}" data-entity-id="${entityId}" data-action="retire">Retire</button>
    `;
  }
  if (itemType === "skill_gap") {
    if (inlineReview) {
      return `
        <button type="button" class="action action-success learning-inline-review-button" data-kind="workflow_action" data-workflow-id="${workflowId}" data-item-type="${itemType}" data-entity-id="${entityId}" data-action="resolve" data-action-label="Resolve skill gap ${entityId} in workflow ${workflowId}">Resolve</button>
        <button type="button" class="action action-muted learning-inline-review-button" data-kind="workflow_action" data-workflow-id="${workflowId}" data-item-type="${itemType}" data-entity-id="${entityId}" data-action="dismiss" data-action-label="Dismiss skill gap ${entityId} in workflow ${workflowId}">Dismiss</button>
      `;
    }
    return `
      <button type="button" class="action action-success workflow-action-button" data-workflow-id="${workflowId}" data-item-type="${itemType}" data-entity-id="${entityId}" data-action="resolve">Resolve</button>
      <button type="button" class="action action-muted workflow-action-button" data-workflow-id="${workflowId}" data-item-type="${itemType}" data-entity-id="${entityId}" data-action="dismiss">Dismiss</button>
    `;
  }
  if (itemType === "skill_candidate_audit") {
    if (inlineReview) {
      return `
        <button type="button" class="action action-success learning-inline-review-button" data-kind="workflow_action" data-workflow-id="${workflowId}" data-item-type="${itemType}" data-entity-id="${entityId}" data-action="paper_approve" data-action-label="Approve candidate ${entityId} in paper for workflow ${workflowId}">Paper OK</button>
        <button type="button" class="action action-muted learning-inline-review-button" data-kind="workflow_action" data-workflow-id="${workflowId}" data-item-type="${itemType}" data-entity-id="${entityId}" data-action="replay_approve" data-action-label="Approve candidate ${entityId} in replay for workflow ${workflowId}">Replay OK</button>
        <button type="button" class="action action-alert learning-inline-review-button" data-kind="workflow_action" data-workflow-id="${workflowId}" data-item-type="${itemType}" data-entity-id="${entityId}" data-action="reject" data-action-label="Reject candidate ${entityId} in workflow ${workflowId}">Reject</button>
      `;
    }
    return `
      <button type="button" class="action action-success workflow-action-button" data-workflow-id="${workflowId}" data-item-type="${itemType}" data-entity-id="${entityId}" data-action="paper_approve">Paper OK</button>
      <button type="button" class="action action-muted workflow-action-button" data-workflow-id="${workflowId}" data-item-type="${itemType}" data-entity-id="${entityId}" data-action="replay_approve">Replay OK</button>
      <button type="button" class="action action-alert workflow-action-button" data-workflow-id="${workflowId}" data-item-type="${itemType}" data-entity-id="${entityId}" data-action="reject">Reject</button>
    `;
  }
  return "";
}

function renderClaimReviewActions(claim, { inlineReview = false } = {}) {
  if (!claim || claim.status === "retired") return "";
  if (inlineReview) {
    return `
      <button type="button" class="action action-success learning-inline-review-button" data-kind="claim_review" data-claim-id="${claim.id}" data-outcome="confirm" data-action-label="Confirm claim ${claim.id}">Confirm</button>
      <button type="button" class="action action-muted learning-inline-review-button" data-kind="claim_review" data-claim-id="${claim.id}" data-outcome="contradict" data-action-label="Contradict claim ${claim.id}">Contradict</button>
      <button type="button" class="action action-alert learning-inline-review-button" data-kind="claim_review" data-claim-id="${claim.id}" data-outcome="retire" data-action-label="Retire claim ${claim.id}">Retire</button>
    `;
  }
  return `
    <button type="button" class="action action-success claim-review-button" data-claim-id="${claim.id}" data-outcome="confirm">Confirm</button>
    <button type="button" class="action action-muted claim-review-button" data-claim-id="${claim.id}" data-outcome="contradict">Contradict</button>
    <button type="button" class="action action-alert claim-review-button" data-claim-id="${claim.id}" data-outcome="retire">Retire</button>
  `;
}

function renderSkillGapActions(gap, { inlineReview = false } = {}) {
  if (!gap || ["resolved", "dismissed"].includes(String(gap.status || "").toLowerCase())) return "";
  if (inlineReview) {
    return `
      ${!gap?.meta?.linked_skill_candidate_id ? `<button type="button" class="action action-success skill-gap-promote-button" data-gap-id="${gap.id}">Promote</button>` : ""}
      <button type="button" class="action action-success learning-inline-review-button" data-kind="skill_gap_review" data-gap-id="${gap.id}" data-outcome="resolve" data-action-label="Resolve skill gap ${gap.id}">Resolve</button>
      <button type="button" class="action action-muted learning-inline-review-button" data-kind="skill_gap_review" data-gap-id="${gap.id}" data-outcome="dismiss" data-action-label="Dismiss skill gap ${gap.id}">Dismiss</button>
    `;
  }
  return `
    ${!gap?.meta?.linked_skill_candidate_id ? `<button type="button" class="action action-success skill-gap-promote-button" data-gap-id="${gap.id}">Promote</button>` : ""}
    <button type="button" class="action action-success skill-gap-review-button" data-gap-id="${gap.id}" data-outcome="resolve">Resolve</button>
    <button type="button" class="action action-muted skill-gap-review-button" data-gap-id="${gap.id}" data-outcome="dismiss">Dismiss</button>
  `;
}

function renderSkillCandidateActions(candidate, { inlineReview = false } = {}) {
  if (!candidate || ["validated", "rejected"].includes(String(candidate.candidate_status || "").toLowerCase())) return "";
  if (inlineReview) {
    return `
      <button type="button" class="action action-success learning-inline-review-button" data-kind="skill_candidate_validation" data-candidate-id="${candidate.id}" data-validation-mode="paper" data-validation-outcome="approve" data-action-label="Approve candidate ${candidate.id} in paper">Paper OK</button>
      <button type="button" class="action action-muted learning-inline-review-button" data-kind="skill_candidate_validation" data-candidate-id="${candidate.id}" data-validation-mode="replay" data-validation-outcome="approve" data-action-label="Approve candidate ${candidate.id} in replay">Replay OK</button>
      <button type="button" class="action action-alert learning-inline-review-button" data-kind="skill_candidate_validation" data-candidate-id="${candidate.id}" data-validation-mode="paper" data-validation-outcome="reject" data-action-label="Reject candidate ${candidate.id}">Reject</button>
    `;
  }
  return `
    <button type="button" class="action action-success skill-candidate-validate-button" data-candidate-id="${candidate.id}" data-validation-mode="paper" data-validation-outcome="approve">Paper OK</button>
    <button type="button" class="action action-muted skill-candidate-validate-button" data-candidate-id="${candidate.id}" data-validation-mode="replay" data-validation-outcome="approve">Replay OK</button>
    <button type="button" class="action action-alert skill-candidate-validate-button" data-candidate-id="${candidate.id}" data-validation-mode="paper" data-validation-outcome="reject">Reject</button>
  `;
}

function renderDistillationReviewActions(digest, { inlineReview = false } = {}) {
  const digestId = Number(digest?.id);
  const meta = asObject(digest?.meta);
  const distillationType = String(meta.distillation_type || "").trim().toLowerCase();
  const reviewStatus = String(meta.review_status || "").trim().toLowerCase();
  if (!Number.isFinite(digestId)) return "";
  if (!["skill_gap_digest", "skill_candidate_digest"].includes(distillationType)) return "";
  if (reviewStatus === "applied") return "";
  if (inlineReview) {
    return `
      <button type="button" class="action action-success learning-inline-review-button" data-kind="distillation_review" data-digest-id="${digestId}" data-action="collapse" data-action-label="Collapse digest ${digestId}">Collapse</button>
      <button type="button" class="action action-muted learning-inline-review-button" data-kind="distillation_review" data-digest-id="${digestId}" data-action="retire" data-action-label="Retire digest ${digestId}">Retire</button>
    `;
  }
  return `
    <button type="button" class="action action-success distillation-review-button" data-digest-id="${digestId}" data-action="collapse">Collapse</button>
    <button type="button" class="action action-muted distillation-review-button" data-digest-id="${digestId}" data-action="retire">Retire</button>
  `;
}

async function refreshAfterLearningMutation(successMessage) {
  await refreshDashboard();
  await refreshLearningDetail({ silent: true });
  if (state.trace.ticker) {
    await loadTickerTrace(state.trace.ticker, { silent: true });
  }
  setStatus(successMessage);
}

async function validateSkillCandidate(
  candidateId,
  {
    validationMode,
    validationOutcome,
    summary = null,
    sampleSize = null,
    winRate = null,
    avgPnlPct = null,
    maxDrawdownPct = null,
    evidence = {},
    refresh = true,
  },
) {
  setStatus(`Validando skill candidate ${candidateId}...`);
  await request(`/skills/candidates/${candidateId}/validate`, {
    method: "POST",
    body: JSON.stringify({
      validation_mode: validationMode,
      validation_outcome: validationOutcome,
      summary: typeof summary === "string" && summary.trim() ? summary.trim() : null,
      sample_size: Number.isFinite(sampleSize) ? sampleSize : null,
      win_rate: Number.isFinite(winRate) ? winRate : null,
      avg_pnl_pct: Number.isFinite(avgPnlPct) ? avgPnlPct : null,
      max_drawdown_pct: Number.isFinite(maxDrawdownPct) ? maxDrawdownPct : null,
      evidence: {
        source: "operator_ui",
        capture_mode: "learning_detail_inline_form",
        ...asObject(evidence),
      },
      activate: validationOutcome === "approve",
    }),
  });
  const doneMessage = `Skill candidate ${candidateId} actualizado.`;
  if (refresh) {
    await refreshAfterLearningMutation(doneMessage);
  }
  return doneMessage;
}

async function reviewClaim(claimId, outcome, { summary = null, refresh = true } = {}) {
  const verbs = {
    confirm: "confirmar",
    contradict: "contradecir",
    retire: "retirar",
  };
  const summaryText = typeof summary === "string" ? summary.trim() : "";
  if (!summaryText) return null;
  setStatus(`Actualizando claim ${claimId}...`);
  await request(`/claims/${claimId}/review`, {
    method: "POST",
    body: JSON.stringify({
      outcome,
      summary: summaryText,
      strength: outcome === "contradict" ? 0.8 : 0.65,
      evidence_payload: { source: "operator_ui" },
    }),
  });
  const doneMessage = `Claim ${claimId} actualizado.`;
  if (refresh) {
    await refreshAfterLearningMutation(doneMessage);
  }
  return doneMessage;
}

async function promoteClaim(claimId) {
  setStatus(`Promoviendo claim ${claimId} a skill candidate...`);
  await request(`/claims/${claimId}/promote`, {
    method: "POST",
  });
  await refreshAfterLearningMutation(`Claim ${claimId} promovido a skill candidate.`);
}

async function promoteOperatorDisagreementCluster(clusterId) {
  setStatus(`Promoviendo cluster ${clusterId} a claim...`);
  await request(`/operator-disagreements/clusters/${clusterId}/promote`, {
    method: "POST",
  });
  await refreshAfterLearningMutation(`Cluster ${clusterId} promovido a claim.`);
}

async function promoteOperatorDisagreementClusterToGap(clusterId) {
  setStatus(`Promoviendo cluster ${clusterId} a skill gap...`);
  await request(`/operator-disagreements/clusters/${clusterId}/promote-gap`, {
    method: "POST",
  });
  await refreshAfterLearningMutation(`Cluster ${clusterId} promovido a skill gap.`);
}

async function promoteSkillGap(gapId) {
  setStatus(`Promoviendo skill gap ${gapId} a skill candidate...`);
  await request(`/skills/gaps/${gapId}/promote`, {
    method: "POST",
  });
  await refreshAfterLearningMutation(`Skill gap ${gapId} promovido a skill candidate.`);
}

async function reviewSkillGap(gapId, outcome, { summary = null, refresh = true } = {}) {
  const verbs = {
    resolve: "resolver",
    dismiss: "descartar",
  };
  const summaryText = typeof summary === "string" ? summary.trim() : "";
  if (!summaryText) return null;
  setStatus(`Actualizando skill gap ${gapId}...`);
  await request(`/skills/gaps/${gapId}/review`, {
    method: "POST",
    body: JSON.stringify({
      outcome,
      summary: summaryText,
    }),
  });
  const doneMessage = `Skill gap ${gapId} actualizado.`;
  if (refresh) {
    await refreshAfterLearningMutation(doneMessage);
  }
  return doneMessage;
}

async function reviewDistillationDigest(digestId, action, { summary = null, keepEntityId = null, refresh = true } = {}) {
  const summaryText = typeof summary === "string" ? summary.trim() : "";
  if (!summaryText) return null;
  setStatus(`Aplicando review sobre digest ${digestId}...`);
  await request(`/memory/maintenance/digests/${digestId}/review`, {
    method: "POST",
    body: JSON.stringify({
      action,
      summary: summaryText,
      keep_entity_id: Number.isFinite(Number(keepEntityId)) ? Number(keepEntityId) : null,
    }),
  });
  const doneMessage = `Digest ${digestId} actualizado.`;
  if (refresh) {
    await refreshAfterLearningMutation(doneMessage);
  }
  return doneMessage;
}

async function applyWorkflowAction({ workflowId, entityId, itemType, action, summary = null, refresh = true }) {
  const verbs = {
    confirm: "Confirmar",
    contradict: "Contradecir",
    retire: "Retirar",
    resolve: "Resolver",
    dismiss: "Descartar",
    paper_approve: "Aprobar en paper",
    replay_approve: "Aprobar en replay",
    reject: "Rechazar",
  };
  const summaryText = typeof summary === "string" ? summary.trim() : "";
  if (!summaryText) return null;
  setStatus(`Aplicando accion ${action} sobre workflow ${workflowId}...`);
  await request(`/learning-workflows/${workflowId}/actions`, {
    method: "POST",
    body: JSON.stringify({
      item_type: itemType,
      entity_id: entityId,
      action,
      summary: summaryText,
    }),
  });
  const doneMessage = `Workflow ${workflowId} actualizado.`;
  if (refresh) {
    await refreshAfterLearningMutation(doneMessage);
  }
  return doneMessage;
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
    (entry) => {
      const observations = asObject(entry.observations);
      const workflowId = observations.workflow_id;
      const workflowType = observations.workflow_type;
      const resolutionClass = observations.resolution_class;
      const resolutionOutcome = observations.resolution_outcome;
      const claimsApplied = Array.isArray(observations.claims_applied) ? observations.claims_applied : [];
      const runtimeSkills = Array.isArray(observations.runtime_skills) ? observations.runtime_skills : [];
      const runtimeClaims = Array.isArray(observations.runtime_claims) ? observations.runtime_claims : [];
      const runtimeDistillations = Array.isArray(observations.runtime_distillations) ? observations.runtime_distillations : [];
      const contextBudget = asObject(observations.context_budget);
      const budgetSummary = summarizeContextBudget(contextBudget);
      const claimPills = claimsApplied.slice(0, 3).map((item) => pill(item, "pill-candidate")).join("");
      const workflowPills = [
        workflowType ? pill(workflowType, "pill-info") : "",
        workflowId ? pill(`workflow ${workflowId}`, "pill-candidate") : "",
        resolutionClass ? pill(resolutionClass, workflowResolutionPillClass(resolutionOutcome)) : "",
        budgetSummary ? pill("runtime budget", budgetSummary.truncated ? "pill-degraded" : "pill-info") : "",
      ].join("");
      const detailButton = renderLearningDetailButton(mapJournalEntryToEntity(entry));
      const claimMeta = (runtimeSkills.length || runtimeClaims.length || runtimeDistillations.length)
        ? `<p class="muted">skills runtime=${runtimeSkills.length}${runtimeClaims.length ? ` · claims runtime=${runtimeClaims.length}` : ""}${runtimeDistillations.length ? ` · distillations runtime=${runtimeDistillations.length}` : ""}${claimsApplied.length ? ` · applied=${claimsApplied.length}` : ""}</p>`
        : "";
      const budgetMeta = budgetSummary
        ? `<p class="muted">${escapeHtml(budgetSummary.headline)} · ${escapeHtml(budgetSummary.detail)}</p>`
        : "";
      return `
        <h3>${escapeHtml(entry.decision || entry.entry_type)}</h3>
        <div class="row">
          ${pill(entry.entry_type, "pill-info")}
          ${entry.strategy_id ? pill(`strategy ${entry.strategy_id}`, "pill-candidate") : ""}
          ${entry.strategy_version_id ? pill(`v${entry.strategy_version_id}`, "pill-active") : ""}
          ${entry.ticker ? pill(entry.ticker, "pill-approved") : ""}
          ${workflowPills}
          ${claimPills}
          ${detailButton}
        </div>
        <p class="muted">${escapeHtml(entry.reasoning || "Sin razonamiento registrado.")}</p>
        ${claimMeta}
        ${budgetMeta}
        <p class="muted">${escapeHtml(formatJournalMeta(entry))}</p>
      `;
    },
    "Todavia no hay decisiones registradas en journal.",
  );
  attachLearningDetailButtons(elements.journalFeed);
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

function renderCorporateEventContexts(contexts) {
  renderStackList(
    elements.corporateEventContext,
    contexts,
    (context) => {
      const events = Array.isArray(context.events) ? context.events : [];
      const cache = asObject(context.cache);
      const sourceLabel = describeCorporateSource(context);
      const sourceClass = corporateSourceClass(context);
      const showCachePill = cache.provider === "alpha_vantage" && (
        context.source === "alpha_vantage" || context.used_fallback || Boolean(context.provider_error)
      );
      const cacheLabel = cache.available
        ? (cache.stale ? `cache stale · ${formatAgeSeconds(cache.age_seconds)}` : `cache ok · ${formatAgeSeconds(cache.age_seconds)}`)
        : "sin cache";
      const eventLines = events.length
        ? events
            .slice(0, 3)
            .map(
              (event) => `
                <div class="muted">
                  ${escapeHtml(event.title || event.event_type || "Evento")} · ${escapeHtml(event.event_date || "sin fecha")} · ${escapeHtml(describeEventSource(event.source))}
                </div>
              `,
            )
            .join("")
        : '<div class="muted">Sin eventos corporativos proximos en la ventana consultada.</div>';
      return `
        <h3>${escapeHtml(context.ticker || "Ticker")}</h3>
        <div class="row">
          ${pill(sourceLabel, sourceClass)}
          ${context.used_fallback ? pill("fallback", "pill-degraded") : ""}
          ${showCachePill ? pill(cacheLabel, cache.stale ? "pill-degraded" : "pill-info") : ""}
        </div>
        <p class="muted">${escapeHtml(summarizeCorporateContext(context))}</p>
        ${eventLines}
      `;
    },
    "No hay tickers relevantes para mostrar contexto corporativo ahora mismo.",
  );
}

async function refreshTickerTraceFromDashboard() {
  const ticker = state.trace.ticker || deriveDefaultTraceTicker(state.dashboards || {});
  if (!ticker) {
    renderTickerTrace();
    return;
  }
  await loadTickerTrace(ticker, { silent: true, syncRuntimeInspector: false });
}

async function loadTickerTrace(ticker, { silent = false, syncRuntimeInspector = true } = {}) {
  const normalizedTicker = normalizeTicker(ticker);
  if (!normalizedTicker) {
    state.trace.ticker = null;
    state.trace.payload = null;
    renderTickerTrace();
    return;
  }

  if (elements.tickerTraceInput) {
    elements.tickerTraceInput.value = normalizedTicker;
  }

  if (!silent) {
    setStatus(`Cargando trace de ${normalizedTicker}...`);
  }

  const payload = await request(`/journal/ticker-trace/${encodeURIComponent(normalizedTicker)}?limit=24`);
  state.trace.ticker = normalizedTicker;
  state.trace.payload = payload;
  renderTickerTrace();
  if (syncRuntimeInspector) {
    await refreshRuntimeMemoryInspectorFromDashboard({ preferredTicker: normalizedTicker, silent: true });
  }

  if (!silent) {
    setStatus(`Trace de ${normalizedTicker} actualizado.`);
  }
}

function readRuntimeMemoryFormState() {
  const rawStrategyVersionId = Number.parseInt(String(elements.runtimeMemoryStrategyInput?.value || "").trim(), 10);
  return {
    ticker: normalizeTicker(elements.runtimeMemoryTickerInput?.value),
    strategyVersionId: Number.isFinite(rawStrategyVersionId) && rawStrategyVersionId > 0 ? rawStrategyVersionId : null,
    skillCodes: parseCommaSeparatedList(elements.runtimeMemorySkillCodesInput?.value),
    phase: String(elements.runtimeMemoryPhaseInput?.value || "").trim() || "do",
  };
}

async function refreshRuntimeMemoryInspectorFromDashboard({ preferredTicker = null, silent = true } = {}) {
  const requestState = readRuntimeMemoryFormState();
  const effectiveTicker = requestState.ticker || normalizeTicker(preferredTicker) || state.trace.ticker || deriveDefaultTraceTicker(state.dashboards || {});
  if (!effectiveTicker && requestState.strategyVersionId === null && !requestState.skillCodes.length) {
    state.runtimeMemory = {
      payload: null,
      phase: requestState.phase,
      skillCodes: [],
      strategyVersionId: null,
      ticker: null,
    };
    renderRuntimeMemoryInspector();
    return;
  }
  await loadRuntimeMemoryInspector(
    {
      ticker: effectiveTicker,
      strategyVersionId: requestState.strategyVersionId,
      skillCodes: requestState.skillCodes,
      phase: requestState.phase,
    },
    {
      silent,
      writeInputs: !requestState.ticker && Boolean(effectiveTicker),
    },
  );
}

async function loadRuntimeMemoryInspector(
  {
    ticker = null,
    strategyVersionId = null,
    skillCodes = [],
    phase = "do",
  } = {},
  {
    silent = false,
    writeInputs = true,
  } = {},
) {
  const normalizedTicker = normalizeTicker(ticker);
  const normalizedStrategyVersionId = Number.isFinite(Number(strategyVersionId)) && Number(strategyVersionId) > 0
    ? Number(strategyVersionId)
    : null;
  const normalizedSkillCodes = Array.isArray(skillCodes)
    ? parseCommaSeparatedList(skillCodes.join(","))
    : parseCommaSeparatedList(skillCodes);
  const normalizedPhase = String(phase || "do").trim() || "do";

  if (!normalizedTicker && normalizedStrategyVersionId === null && !normalizedSkillCodes.length) {
    state.runtimeMemory = {
      payload: null,
      phase: normalizedPhase,
      skillCodes: [],
      strategyVersionId: null,
      ticker: null,
    };
    renderRuntimeMemoryInspector();
    if (state.trace.payload) {
      renderTickerTrace();
    }
    return;
  }

  if (!silent) {
    setStatus(`Cargando runtime memory${normalizedTicker ? ` de ${normalizedTicker}` : ""}...`);
  }

  const params = new URLSearchParams();
  if (normalizedTicker) params.set("ticker", normalizedTicker);
  if (normalizedStrategyVersionId !== null) params.set("strategy_version_id", String(normalizedStrategyVersionId));
  if (normalizedSkillCodes.length) params.set("skill_codes", normalizedSkillCodes.join(","));
  if (normalizedPhase) params.set("phase", normalizedPhase);

  const payload = await request(`/memory/runtime-inspect?${params.toString()}`);
  state.runtimeMemory = {
    payload,
    phase: String(asObject(payload?.resolved_skill_context).phase || normalizedPhase).trim() || "do",
    skillCodes: Array.isArray(payload?.requested_skill_codes) ? payload.requested_skill_codes : normalizedSkillCodes,
    strategyVersionId: Number.isFinite(Number(payload?.strategy_version_id))
      ? Number(payload.strategy_version_id)
      : normalizedStrategyVersionId,
    ticker: normalizeTicker(payload?.ticker || normalizedTicker),
  };

  if (writeInputs) {
    if (elements.runtimeMemoryTickerInput) {
      elements.runtimeMemoryTickerInput.value = state.runtimeMemory.ticker || "";
    }
    if (elements.runtimeMemoryStrategyInput) {
      elements.runtimeMemoryStrategyInput.value = state.runtimeMemory.strategyVersionId
        ? String(state.runtimeMemory.strategyVersionId)
        : "";
    }
    if (elements.runtimeMemorySkillCodesInput) {
      elements.runtimeMemorySkillCodesInput.value = state.runtimeMemory.skillCodes.join(", ");
    }
    if (elements.runtimeMemoryPhaseInput) {
      elements.runtimeMemoryPhaseInput.value = state.runtimeMemory.phase || "do";
    }
  }

  renderRuntimeMemoryInspector();
  if (state.trace.payload) {
    renderTickerTrace();
  }
  if (!silent) {
    setStatus(`Runtime memory inspeccionado${state.runtimeMemory.ticker ? ` para ${state.runtimeMemory.ticker}` : ""}.`);
  }
}

function renderRuntimeMemoryInspector() {
  if (!elements.runtimeMemoryInspector) return;
  const payload = state.runtimeMemory.payload;
  if (!payload) {
    elements.runtimeMemoryInspector.innerHTML = `
      <div class="stack-item">
        <strong>Sin inspeccion activa</strong>
        <p class="muted">Usa un ticker, una strategy version o skill codes para ver el contexto bounded que cargaria el agente.</p>
      </div>
    `;
    return;
  }

  const source = asObject(payload.skill_context_source);
  const context = asObject(payload.resolved_skill_context);
  const runtimeSkills = Array.isArray(payload.runtime_skills) ? payload.runtime_skills : [];
  const runtimeClaims = Array.isArray(payload.runtime_claims) ? payload.runtime_claims : [];
  const runtimeDistillations = Array.isArray(payload.runtime_distillations) ? payload.runtime_distillations : [];
  const budgetSummary = summarizeContextBudget(payload.context_budget);
  const appliedSkills = Array.isArray(context.applied_skills) ? context.applied_skills : [];
  const consideredSkills = Array.isArray(context.considered_skills) ? context.considered_skills : [];
  const primarySkill = String(context.primary_skill_code || "").trim();
  const sourceType = String(source.source_type || "none").trim() || "none";
  const requestSummary = [
    state.runtimeMemory.ticker || "sin ticker",
    state.runtimeMemory.strategyVersionId ? `strategy ${state.runtimeMemory.strategyVersionId}` : "sin strategy",
    state.runtimeMemory.skillCodes.length ? `skills ${state.runtimeMemory.skillCodes.join(", ")}` : "sin override",
  ].join(" · ");

  renderStackList(
    elements.runtimeMemoryInspector,
    [
      {
        title: "Selection",
        body: `
          <div class="row">
            ${pill(sourceType, sourceType === "none" ? "pill-degraded" : "pill-info")}
            ${primarySkill ? pill(primarySkill, "pill-approved") : ""}
            ${context.phase ? pill(`phase ${context.phase}`, "pill-candidate") : ""}
            ${payload.strategy_version_id ? pill(`strategy ${payload.strategy_version_id}`, "pill-info") : ""}
          </div>
          <p class="muted">${escapeHtml(requestSummary)}</p>
          <p class="muted">${escapeHtml(
            source.summary
              || (sourceType === "none"
                ? "No hay skill_context persistido para esta combinacion; solo se aplican overrides explicitos."
                : "Skill context resuelto desde la memoria operativa mas cercana."),
          )}</p>
          <p class="muted">${escapeHtml(
            budgetSummary
              ? `${budgetSummary.headline} · ${budgetSummary.detail}`
              : "Sin runtime items cargados para esta seleccion.",
          )}</p>
        `,
      },
      {
        title: "Resolved Skill Context",
        body: `
          <div class="snapshot-list">
            <div class="snapshot-item">
              <div>
                <strong>${escapeHtml(primarySkill || "sin primary skill")}</strong>
                <p>${escapeHtml(
                  `${appliedSkills.length} applied · ${consideredSkills.length} considered · ${context.routing_mode || "routing_mode n/a"}`,
                )}</p>
              </div>
              <span class="row">
                ${context.catalog_version ? pill(context.catalog_version, "pill-info") : ""}
                ${context.summary ? pill("summary", "pill-candidate") : ""}
              </span>
            </div>
          </div>
          <details>
            <summary>Ver JSON completo</summary>
            <pre class="terminal-card">${escapeHtml(JSON.stringify(context, null, 2))}</pre>
          </details>
        `,
      },
      {
        title: `Runtime Skills (${runtimeSkills.length})`,
        body: renderRuntimeMemorySkillPackets(runtimeSkills),
      },
      {
        title: `Runtime Claims (${runtimeClaims.length})`,
        body: renderRuntimeMemoryClaimPackets(runtimeClaims),
      },
      {
        title: `Runtime Distillations (${runtimeDistillations.length})`,
        body: renderRuntimeMemoryDistillationPackets(runtimeDistillations),
      },
    ],
    (section) => `
      <h3>${escapeHtml(section.title)}</h3>
      ${section.body}
    `,
    "Sin contexto runtime disponible.",
  );
  attachLearningDetailButtons(elements.runtimeMemoryInspector);
}

function renderRuntimeMemorySkillPackets(items) {
  if (!items.length) return `<p class="muted">No se cargaron runtime skills para esta seleccion.</p>`;
  return `
    <div class="snapshot-list">
      ${items.map((item) => {
        const procedureSteps = Array.isArray(item.procedure_steps) ? item.procedure_steps : [];
        return `
          <div class="snapshot-item">
            <div>
              <strong>${escapeHtml(item.skill_code || item.skill_name || "skill")}</strong>
              <p>${escapeHtml(item.selection_reason || item.objective || "Sin razon de seleccion registrada.")}</p>
              <p class="muted">${escapeHtml(
                procedureSteps.length
                  ? `steps: ${procedureSteps.slice(0, 3).join(" | ")}`
                  : item.validated_revision_summary || "Sin steps compactados.",
              )}</p>
            </div>
            <span class="row">
              ${item.category ? pill(item.category, "pill-info") : ""}
              ${item.instruction_source ? pill(item.instruction_source, "pill-candidate") : ""}
              ${Number.isFinite(item.confidence) ? pill(`conf ${Number(item.confidence).toFixed(2)}`, "pill-approved") : ""}
              ${renderLearningDetailButton(
                Number.isFinite(Number(item.validated_revision_id))
                  ? { entityType: "skill_revision", entityId: Number(item.validated_revision_id) }
                  : null,
              )}
            </span>
          </div>
        `;
      }).join("")}
    </div>
  `;
}

function renderRuntimeMemoryClaimPackets(items) {
  if (!items.length) return `<p class="muted">No se cargaron durable claims para esta seleccion.</p>`;
  return `
    <div class="snapshot-list">
      ${items.map((item) => `
        <div class="snapshot-item">
          <div>
            <strong>${escapeHtml(item.key || item.claim_type || "claim")}</strong>
            <p>${escapeHtml(item.claim_text || "Sin texto de claim.")}</p>
            <p class="muted">${escapeHtml(
              Array.isArray(item.evidence_summaries) && item.evidence_summaries.length
                ? item.evidence_summaries.slice(0, 2).join(" | ")
                : "Sin evidencia resumida en packet.",
            )}</p>
          </div>
          <span class="row">
            ${item.status ? pill(item.status, item.status === "supported" ? "pill-approved" : "pill-degraded") : ""}
            ${item.freshness_state ? pill(item.freshness_state, item.freshness_state === "current" ? "pill-active" : "pill-degraded") : ""}
            ${Number.isFinite(item.confidence) ? pill(`conf ${Number(item.confidence).toFixed(2)}`, "pill-candidate") : ""}
            ${renderLearningDetailButton(
              Number.isFinite(Number(item.claim_id))
                ? { entityType: "claim", entityId: Number(item.claim_id) }
                : null,
            )}
          </span>
        </div>
      `).join("")}
    </div>
  `;
}

function renderRuntimeMemoryDistillationPackets(items) {
  if (!items.length) return `<p class="muted">No se cargaron digestos revisados para esta seleccion.</p>`;
  return `
    <div class="snapshot-list">
      ${items.map((item) => `
        <div class="snapshot-item">
          <div>
            <strong>${escapeHtml(item.key || item.distillation_type || "distillation")}</strong>
            <p>${escapeHtml(item.summary || "Sin resumen de digest.")}</p>
            <p class="muted">${escapeHtml(item.review_summary || "Sin review summary.")}</p>
          </div>
          <span class="row">
            ${item.distillation_type ? pill(item.distillation_type, "pill-info") : ""}
            ${item.review_action ? pill(item.review_action, "pill-approved") : ""}
            ${item.target_skill_code ? pill(item.target_skill_code, "pill-candidate") : ""}
            ${renderLearningDetailButton(
              Number.isFinite(Number(item.digest_id))
                ? { entityType: "distillation_digest", entityId: Number(item.digest_id) }
                : null,
            )}
          </span>
        </div>
      `).join("")}
    </div>
  `;
}

function currentTraceRuntimeMemoryPayload() {
  const traceTicker = normalizeTicker(state.trace.ticker || asObject(state.trace.payload).ticker);
  const payload = state.runtimeMemory.payload;
  if (!traceTicker || !payload) return null;
  return normalizeTicker(payload.ticker) === traceTicker ? payload : null;
}

function renderTickerTraceRuntimeSnapshot() {
  const payload = currentTraceRuntimeMemoryPayload();
  if (!payload) return "";

  const source = asObject(payload.skill_context_source);
  const context = asObject(payload.resolved_skill_context);
  const runtimeSkills = Array.isArray(payload.runtime_skills) ? payload.runtime_skills : [];
  const runtimeClaims = Array.isArray(payload.runtime_claims) ? payload.runtime_claims : [];
  const runtimeDistillations = Array.isArray(payload.runtime_distillations) ? payload.runtime_distillations : [];
  const budgetSummary = summarizeContextBudget(payload.context_budget);
  const sourceType = String(source.source_type || "none").trim() || "none";
  const primarySkill = String(context.primary_skill_code || "").trim();
  const sections = [
    runtimeSkills.length
      ? `<div><strong>Skills</strong>${renderRuntimeMemorySkillPackets(runtimeSkills.slice(0, 2))}</div>`
      : "",
    runtimeClaims.length
      ? `<div><strong>Claims</strong>${renderRuntimeMemoryClaimPackets(runtimeClaims.slice(0, 2))}</div>`
      : "",
    runtimeDistillations.length
      ? `<div><strong>Distillations</strong>${renderRuntimeMemoryDistillationPackets(runtimeDistillations.slice(0, 2))}</div>`
      : "",
  ]
    .filter(Boolean)
    .join("");

  return `
    <article class="stack-item">
      <h3>Runtime Snapshot</h3>
      <div class="row">
        ${pill(sourceType, sourceType === "none" ? "pill-degraded" : "pill-info")}
        ${primarySkill ? pill(primarySkill, "pill-approved") : ""}
        ${context.phase ? pill(`phase ${context.phase}`, "pill-candidate") : ""}
        ${payload.strategy_version_id ? pill(`strategy ${payload.strategy_version_id}`, "pill-info") : ""}
        ${budgetSummary ? pill(budgetSummary.headline, budgetSummary.truncated ? "pill-degraded" : "pill-active") : ""}
      </div>
      <p class="muted">${escapeHtml(
        source.summary
          || context.summary
          || "Snapshot bounded del contexto runtime resuelto para este ticker.",
      )}</p>
      ${sections || `<p class="muted">No hay runtime packets cargados para este ticker.</p>`}
    </article>
  `;
}

function renderTickerTraceRuntimeEventDrilldown(event) {
  const details = asObject(event.details);
  const runtimeSkills = Array.isArray(details.runtime_skills) ? details.runtime_skills : [];
  const runtimeClaims = Array.isArray(details.runtime_claims) ? details.runtime_claims : [];
  const runtimeDistillations = Array.isArray(details.runtime_distillations) ? details.runtime_distillations : [];
  const budgetSummary = summarizeContextBudget(details.context_budget);
  if (!runtimeSkills.length && !runtimeClaims.length && !runtimeDistillations.length && !budgetSummary) return "";

  const sections = [
    runtimeSkills.length
      ? `<div><strong>Skills cargadas</strong>${renderRuntimeMemorySkillPackets(runtimeSkills.slice(0, 2))}</div>`
      : "",
    runtimeClaims.length
      ? `<div><strong>Claims cargadas</strong>${renderRuntimeMemoryClaimPackets(runtimeClaims.slice(0, 2))}</div>`
      : "",
    runtimeDistillations.length
      ? `<div><strong>Distillations cargadas</strong>${renderRuntimeMemoryDistillationPackets(runtimeDistillations.slice(0, 2))}</div>`
      : "",
  ]
    .filter(Boolean)
    .join("");

  return `
    <div class="stack-item">
      <div class="row">
        ${budgetSummary ? pill("runtime memory", budgetSummary.truncated ? "pill-degraded" : "pill-info") : ""}
        ${budgetSummary ? pill(budgetSummary.headline, "pill-candidate") : ""}
      </div>
      ${budgetSummary ? `<p class="muted">${escapeHtml(budgetSummary.detail)}</p>` : ""}
      ${sections}
    </div>
  `;
}

function renderTickerTrace() {
  if (!elements.tickerTraceSummary || !elements.tickerTraceFeed) return;
  const payload = state.trace.payload;
  const summary = asObject(payload?.summary);
  const ticker = state.trace.ticker || summary.ticker || "";

  if (!ticker) {
    elements.tickerTraceSummary.innerHTML = `
      <div class="stack-item">
        <strong>Sin ticker seleccionado</strong>
        <p class="muted">Escribe un ticker para ver una linea temporal unica con senales, bloqueos, LLM, journal y posiciones.</p>
      </div>
    `;
    elements.tickerTraceFeed.innerHTML = `<div class="stack-item"><p class="muted">Todavia no hay trace cargado.</p></div>`;
    return;
  }

  const latestDecisionPill = summary.latest_decision
    ? pill(summary.latest_decision, tickerTraceDecisionPillClass(summary.latest_decision))
    : "";
  const latestSourcePill = summary.latest_decision_source
    ? pill(summary.latest_decision_source, "pill-info")
    : "";
  const llmPill = summary.latest_llm_status
    ? pill(summary.latest_llm_status, summary.latest_llm_status === "reviewed" ? "pill-active" : "pill-degraded")
    : "";
  const providerPill = summary.latest_llm_provider ? pill(summary.latest_llm_provider, "pill-candidate") : "";
  const skillPill = summary.latest_primary_skill ? pill(summary.latest_primary_skill, "pill-approved") : "";
  const revisionPill = summary.latest_active_skill_revision ? pill(`rev ${summary.latest_active_skill_revision}`, "pill-active") : "";
  const budgetSummary = summarizeContextBudget({
    runtime_skills: {
      available_count: summary.latest_available_runtime_skill_count,
      loaded_count: summary.latest_loaded_runtime_skill_count,
      truncated_count:
        Number.isFinite(summary.latest_available_runtime_skill_count) && Number.isFinite(summary.latest_loaded_runtime_skill_count)
          ? Number(summary.latest_available_runtime_skill_count) - Number(summary.latest_loaded_runtime_skill_count)
          : null,
    },
    runtime_claims: {
      available_count: summary.latest_available_runtime_claim_count,
      loaded_count: summary.latest_loaded_runtime_claim_count,
      truncated_count:
        Number.isFinite(summary.latest_available_runtime_claim_count) && Number.isFinite(summary.latest_loaded_runtime_claim_count)
          ? Number(summary.latest_available_runtime_claim_count) - Number(summary.latest_loaded_runtime_claim_count)
          : null,
    },
    runtime_distillations: {
      available_count: summary.latest_available_runtime_distillation_count,
      loaded_count: summary.latest_loaded_runtime_distillation_count,
      truncated_count:
        Number.isFinite(summary.latest_available_runtime_distillation_count) && Number.isFinite(summary.latest_loaded_runtime_distillation_count)
          ? Number(summary.latest_available_runtime_distillation_count) - Number(summary.latest_loaded_runtime_distillation_count)
          : null,
    },
  });
  elements.tickerTraceSummary.innerHTML = `
    <div class="ticker-trace-summary-head">
      <div>
        <strong>${escapeHtml(ticker)}</strong>
        <p class="muted">Ultima senal ${escapeHtml(summary.latest_signal_at ? formatDate(summary.latest_signal_at) : "sin registrar")} · ${escapeHtml(summary.latest_signal_type || "sin tipo")}</p>
      </div>
      <div class="row">
        ${latestDecisionPill}
        ${latestSourcePill}
        ${llmPill}
        ${providerPill}
        ${skillPill}
        ${revisionPill}
      </div>
    </div>
    <div class="ticker-trace-grid">
      ${tickerTraceStat("Senales", String(summary.total_signals ?? 0), summary.latest_signal_status || "sin status")}
      ${tickerTraceStat("Journal", String(summary.total_journal_entries ?? 0), "entradas registradas")}
      ${tickerTraceStat("Posiciones", `${summary.open_positions ?? 0}/${summary.total_positions ?? 0}`, "abiertas/total")}
      ${tickerTraceStat("Score", Number.isFinite(summary.latest_score) ? Number(summary.latest_score).toFixed(2) : "n/a", "ultima senal")}
      ${tickerTraceStat("Skill", summary.latest_primary_skill || "n/a", summary.latest_active_skill_revision || "sin revision activa")}
      ${tickerTraceStat("AI budget", budgetSummary?.headline || "n/a", budgetSummary?.detail || "sin journal AI reciente")}
      ${tickerTraceStat("Latencia", Number.isFinite(summary.latest_timing_total_ms) ? `${Math.round(summary.latest_timing_total_ms)} ms` : "n/a", "analisis total")}
      ${tickerTraceStat("Cuello", summary.latest_timing_slowest_stage ? humanizeLabel(summary.latest_timing_slowest_stage) : "n/a", Number.isFinite(summary.latest_timing_slowest_stage_ms) ? `${Math.round(summary.latest_timing_slowest_stage_ms)} ms` : "sin dato")}
    </div>
    <p class="muted">${escapeHtml(summary.latest_guard_reason || "Sin guardrail dominante registrado en la ultima senal.")}</p>
    ${renderTickerTraceRuntimeSnapshot()}
  `;

  renderStackList(
    elements.tickerTraceFeed,
    Array.isArray(payload?.events) ? payload.events : [],
    (event) => `
      <h3>${escapeHtml(event.title || event.event_kind || "Evento")}</h3>
      ${renderTickerTraceWorkflowPills(event)}
      <div class="row">
        ${pill(event.event_kind || "event", "pill-info")}
        ${event.status ? pill(event.status, tickerTraceDecisionPillClass(event.status)) : ""}
        ${event.decision ? pill(event.decision, tickerTraceDecisionPillClass(event.decision)) : ""}
        ${event.decision_source ? pill(event.decision_source, "pill-candidate") : ""}
        ${event.llm_status ? pill(event.llm_status, event.llm_status === "reviewed" ? "pill-active" : "pill-degraded") : ""}
        ${event.llm_provider ? pill(event.llm_provider, "pill-info") : ""}
        ${renderLearningDetailButton(mapTickerTraceEventToEntity(event))}
      </div>
      <p class="muted">${escapeHtml(event.summary || "Sin resumen.")}</p>
      ${renderTickerTraceRuntimeEventDrilldown(event)}
      <p class="muted">${escapeHtml(buildTickerTraceMeta(event))}</p>
    `,
    `No hay eventos persistidos para ${ticker}.`,
  );
  attachLearningDetailButtons(elements.tickerTraceSummary);
  attachLearningDetailButtons(elements.tickerTraceFeed);
}

function renderTickerTraceWorkflowPills(event) {
  const workflow = asObject(asObject(event.details).workflow);
  if (!workflow.workflow_type && !workflow.workflow_id && !workflow.resolution_class) return "";
  return `
    <div class="row">
      ${workflow.workflow_type ? pill(workflow.workflow_type, "pill-info") : ""}
      ${workflow.workflow_id ? pill(`workflow ${workflow.workflow_id}`, "pill-candidate") : ""}
      ${workflow.resolution_class ? pill(workflow.resolution_class, workflowResolutionPillClass(workflow.resolution_outcome)) : ""}
    </div>
  `;
}

function mapJournalEntryToEntity(entry) {
  const observations = asObject(entry?.observations);
  const workflowType = String(observations.workflow_type || "").trim();
  const workflowId = Number(observations.workflow_id);
  const itemType = String(observations.item_type || "").trim();
  const entityId = Number(observations.entity_id);
  if (workflowType && Number.isFinite(workflowId)) return { entityType: "workflow", entityId: workflowId };
  if (!Number.isFinite(entityId)) return null;
  if (itemType === "claim_review") return { entityType: "claim", entityId };
  if (itemType === "skill_gap") return { entityType: "skill_gap", entityId };
  if (itemType === "skill_candidate_audit") return { entityType: "skill_candidate", entityId };
  return null;
}

function mapTickerTraceEventToEntity(event) {
  const workflow = asObject(asObject(event?.details).workflow);
  const workflowId = Number(workflow.workflow_id);
  const itemType = String(workflow.item_type || "").trim();
  const entityId = Number(workflow.entity_id);
  if (Number.isFinite(workflowId)) return { entityType: "workflow", entityId: workflowId };
  if (!Number.isFinite(entityId)) return null;
  if (itemType === "claim_review") return { entityType: "claim", entityId };
  if (itemType === "skill_gap") return { entityType: "skill_gap", entityId };
  if (itemType === "skill_candidate_audit") return { entityType: "skill_candidate", entityId };
  return null;
}

function attachLearningDetailButtons(container) {
  if (!container) return;
  container.querySelectorAll(".learning-detail-button").forEach((button) => {
    button.addEventListener("click", async () => {
      const entityType = button.dataset.entityType;
      const entityId = Number(button.dataset.entityId);
      if (!entityType || !Number.isFinite(entityId)) return;
      button.disabled = true;
      try {
        await loadLearningDetail(entityType, entityId);
      } finally {
        button.disabled = false;
      }
    });
  });
}

function attachWorkflowActionButtons(container) {
  if (!container) return;
  container.querySelectorAll(".workflow-action-button").forEach((button) => {
    button.addEventListener("click", async () => {
      const workflowId = Number(button.dataset.workflowId);
      const entityId = Number(button.dataset.entityId);
      const itemType = button.dataset.itemType;
      const action = button.dataset.action;
      if (!Number.isFinite(workflowId) || !Number.isFinite(entityId) || !itemType || !action) return;
      button.disabled = true;
      try {
        await openLearningDetailWithDraft("workflow", workflowId, {
          kind: "workflow_action",
          workflowId,
          entityId,
          itemType,
          action,
          summary: "",
          title: `${button.textContent?.trim() || "Apply"} workflow ${workflowId}`,
          submitLabel: button.textContent?.trim() || "Apply",
          subtitle: "Resume la accion antes de modificar este workflow.",
        });
      } finally {
        button.disabled = false;
      }
    });
  });
}

function attachClaimReviewButtons(container) {
  if (!container) return;
  container.querySelectorAll(".claim-review-button").forEach((button) => {
    button.addEventListener("click", async () => {
      const claimId = Number(button.dataset.claimId);
      const outcome = button.dataset.outcome || button.dataset.reviewOutcome;
      if (!Number.isFinite(claimId) || !outcome) return;
      button.disabled = true;
      try {
        await openLearningDetailWithDraft("claim", claimId, {
          kind: "claim_review",
          claimId,
          outcome,
          summary: "",
          title: `${button.textContent?.trim() || "Review"} claim ${claimId}`,
          submitLabel: button.textContent?.trim() || "Apply",
          subtitle: "Resume por que confirmas, contradices o retiras este claim.",
        });
      } finally {
        button.disabled = false;
      }
    });
  });
}

function attachClaimPromoteButtons(container) {
  if (!container) return;
  container.querySelectorAll(".claim-promote-button").forEach((button) => {
    button.addEventListener("click", async () => {
      const claimId = Number(button.dataset.claimId);
      if (!Number.isFinite(claimId)) return;
      button.disabled = true;
      try {
        await promoteClaim(claimId);
      } finally {
        button.disabled = false;
      }
    });
  });
}

function attachOperatorDisagreementClusterPromoteButtons(container) {
  if (!container) return;
  container.querySelectorAll(".operator-disagreement-cluster-promote-button").forEach((button) => {
    button.addEventListener("click", async () => {
      const clusterId = Number(button.dataset.clusterId);
      if (!Number.isFinite(clusterId)) return;
      button.disabled = true;
      try {
        await promoteOperatorDisagreementCluster(clusterId);
      } finally {
        button.disabled = false;
      }
    });
  });
  container.querySelectorAll(".operator-disagreement-cluster-promote-gap-button").forEach((button) => {
    button.addEventListener("click", async () => {
      const clusterId = Number(button.dataset.clusterId);
      if (!Number.isFinite(clusterId)) return;
      button.disabled = true;
      try {
        await promoteOperatorDisagreementClusterToGap(clusterId);
      } finally {
        button.disabled = false;
      }
    });
  });
}

function attachSkillGapReviewButtons(container) {
  if (!container) return;
  container.querySelectorAll(".skill-gap-promote-button").forEach((button) => {
    button.addEventListener("click", async () => {
      const gapId = Number(button.dataset.gapId);
      if (!Number.isFinite(gapId)) return;
      button.disabled = true;
      try {
        await promoteSkillGap(gapId);
      } finally {
        button.disabled = false;
      }
    });
  });
  container.querySelectorAll(".skill-gap-review-button").forEach((button) => {
    button.addEventListener("click", async () => {
      const gapId = Number(button.dataset.gapId);
      const outcome = button.dataset.outcome;
      if (!Number.isFinite(gapId) || !outcome) return;
      button.disabled = true;
      try {
        await openLearningDetailWithDraft("skill_gap", gapId, {
          kind: "skill_gap_review",
          gapId,
          outcome,
          summary: "",
          title: `${button.textContent?.trim() || "Review"} skill gap ${gapId}`,
          submitLabel: button.textContent?.trim() || "Apply",
          subtitle: "Resume por que este gap se resuelve o se descarta.",
        });
      } finally {
        button.disabled = false;
      }
    });
  });
}

function attachDistillationReviewButtons(container) {
  if (!container) return;
  container.querySelectorAll(".distillation-review-button").forEach((button) => {
    button.addEventListener("click", async () => {
      const digestId = Number(button.dataset.digestId);
      const action = button.dataset.action;
      if (!Number.isFinite(digestId) || !action) return;
      button.disabled = true;
      try {
        await openLearningDetailWithDraft("distillation_digest", digestId, {
          kind: "distillation_review",
          digestId,
          action,
          summary: "",
          title: `${button.textContent?.trim() || "Review"} digest ${digestId}`,
          submitLabel: button.textContent?.trim() || "Apply",
          subtitle: "Resume por que este digest debe colapsarse o retirarse.",
        });
      } finally {
        button.disabled = false;
      }
    });
  });
}

function attachSkillCandidateValidateButtons(container) {
  if (!container) return;
  container.querySelectorAll(".skill-candidate-validate-button").forEach((button) => {
    button.addEventListener("click", async () => {
      const candidateId = Number(button.dataset.candidateId);
      const validationMode = button.dataset.validationMode;
      const validationOutcome = button.dataset.validationOutcome;
      if (!Number.isFinite(candidateId) || !validationMode || !validationOutcome) return;
      button.disabled = true;
      try {
        await openLearningDetailWithDraft("skill_candidate", candidateId, {
          kind: "skill_candidate_validation",
          candidateId,
          validationMode,
          validationOutcome,
          summary: "",
          title: `${button.textContent?.trim() || "Validate"} candidate ${candidateId}`,
          submitLabel: button.textContent?.trim() || "Apply",
          subtitle: "Resume por que este candidate merece paper, replay o rechazo.",
        });
      } finally {
        button.disabled = false;
      }
    });
  });
}

function attachValidationCompareButtons(container) {
  if (!container) return;
  container.querySelectorAll(".validation-compare-baseline").forEach((button) => {
    button.addEventListener("click", async () => {
      const compareKey = String(button.dataset.compareKey || "").trim();
      const validationId = Number(button.dataset.validationId);
      if (!compareKey || !Number.isFinite(validationId)) return;
      button.disabled = true;
      try {
        await setLearningValidationBaseline(compareKey, validationId);
      } finally {
        button.disabled = false;
      }
    });
  });
  container.querySelectorAll(".validation-compare-reset").forEach((button) => {
    button.addEventListener("click", async () => {
      const compareKey = String(button.dataset.compareKey || "").trim();
      if (!compareKey) return;
      button.disabled = true;
      try {
        await clearLearningValidationBaseline(compareKey);
      } finally {
        button.disabled = false;
      }
    });
  });
}

function attachRuntimeMemoryLoadButtons(container) {
  if (!container) return;
  container.querySelectorAll(".runtime-memory-load-button").forEach((button) => {
    button.addEventListener("click", async () => {
      const rawPayload = String(button.dataset.runtimeRequest || "").trim();
      if (!rawPayload) return;
      let requestPayload;
      try {
        requestPayload = JSON.parse(rawPayload);
      } catch (_error) {
        return;
      }
      button.disabled = true;
      try {
        await loadRuntimeMemoryInspector(requestPayload, { silent: false, writeInputs: true });
      } finally {
        button.disabled = false;
      }
    });
  });
}

function attachLearningPanelButtons(container) {
  attachLearningDetailButtons(container);
  attachWorkflowActionButtons(container);
  attachClaimReviewButtons(container);
  attachClaimPromoteButtons(container);
  attachOperatorDisagreementClusterPromoteButtons(container);
  attachSkillGapReviewButtons(container);
  attachDistillationReviewButtons(container);
  attachSkillCandidateValidateButtons(container);
  attachValidationCompareButtons(container);
  attachRuntimeMemoryLoadButtons(container);
  attachLearningInlineReviewButtons(container);
  attachLearningInlineReviewForms(container);
}

async function loadLearningDetail(entityType, entityId, { silent = false, draft = null } = {}) {
  const normalizedType = String(entityType || "").trim();
  if (!normalizedType || !Number.isFinite(Number(entityId))) return;
  const numericId = Number(entityId);
  const preserveContext = state.learningDetail.entityType === normalizedType && state.learningDetail.entityId === numericId;
  const persistedBaselines = preserveContext
    ? asObject(state.learningDetail.compareBaselines)
    : getPersistedLearningCompareBaselines(normalizedType, numericId);
  if (!silent) {
    setStatus(`Cargando ${normalizedType} ${numericId}...`);
  }
  const payload = await fetchLearningDetail(normalizedType, numericId);
  const runtimeMemory = await fetchLearningDetailRuntimeMemory(normalizedType, payload);
  state.learningDetail = {
    entityType: normalizedType,
    entityId: numericId,
    draft: draft || (preserveContext ? state.learningDetail.draft : null),
    compareBaselines: persistedBaselines,
    payload,
    runtimeMemory,
  };
  renderLearningDetail();
  if (!silent) {
    setStatus(`${normalizedType} ${numericId} cargado.`);
  }
}

async function openLearningDetailWithDraft(entityType, entityId, draft) {
  await loadLearningDetail(entityType, entityId, {
    draft,
    silent: false,
  });
}

async function setLearningValidationBaseline(compareKey, validationId) {
  state.learningDetail.compareBaselines = {
    ...asObject(state.learningDetail.compareBaselines),
    [compareKey]: Number(validationId),
  };
  persistLearningCompareBaselines(
    state.learningDetail.entityType,
    state.learningDetail.entityId,
    state.learningDetail.compareBaselines,
  );
  await refreshLearningDetail({ silent: true });
  setStatus(`Baseline actualizado para ${compareKey}.`);
}

async function clearLearningValidationBaseline(compareKey) {
  const next = { ...asObject(state.learningDetail.compareBaselines) };
  delete next[compareKey];
  state.learningDetail.compareBaselines = next;
  persistLearningCompareBaselines(
    state.learningDetail.entityType,
    state.learningDetail.entityId,
    state.learningDetail.compareBaselines,
  );
  await refreshLearningDetail({ silent: true });
  setStatus(`Baseline reseteado para ${compareKey}.`);
}

function learningCompareBaselineStoreKey(entityType, entityId) {
  const normalizedType = String(entityType || "").trim();
  const numericId = Number(entityId);
  if (!normalizedType || !Number.isFinite(numericId)) return null;
  return `${normalizedType}:${numericId}`;
}

function readLearningCompareBaselineStore() {
  try {
    const raw = window.localStorage.getItem(LEARNING_COMPARE_BASELINES_STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    return asObject(parsed);
  } catch (_error) {
    return {};
  }
}

function writeLearningCompareBaselineStore(store) {
  try {
    window.localStorage.setItem(
      LEARNING_COMPARE_BASELINES_STORAGE_KEY,
      JSON.stringify(asObject(store)),
    );
  } catch (_error) {
    // Ignore storage failures; the compare surface still works for the session.
  }
}

function getPersistedLearningCompareBaselines(entityType, entityId) {
  const key = learningCompareBaselineStoreKey(entityType, entityId);
  if (!key) return {};
  return asObject(asObject(state.learningCompareBaselineStore)[key]);
}

function persistLearningCompareBaselines(entityType, entityId, baselines) {
  const key = learningCompareBaselineStoreKey(entityType, entityId);
  if (!key) return;
  const nextStore = {
    ...asObject(state.learningCompareBaselineStore),
  };
  const normalizedBaselines = asObject(baselines);
  if (Object.keys(normalizedBaselines).length) {
    nextStore[key] = normalizedBaselines;
  } else {
    delete nextStore[key];
  }
  state.learningCompareBaselineStore = nextStore;
  writeLearningCompareBaselineStore(nextStore);
}

async function fetchLearningDetail(entityType, entityId) {
  const compareBaselines = asObject(state.learningDetail.compareBaselines);
  if (entityType === "workflow") {
    return request(`/learning-workflows/${entityId}?history_limit=12`);
  }
  if (entityType === "claim") {
    const [claim, evidence, provenance] = await Promise.all([
      request(`/claims/${entityId}`),
      request(`/claims/${entityId}/evidence`),
      request(`/claims/${entityId}/provenance`),
    ]);
    return { claim, evidence, provenance };
  }
  if (entityType === "skill_gap") {
    return request(`/skills/gaps/${entityId}`);
  }
  if (entityType === "distillation_digest") {
    return request(`/memory/maintenance/digests/${entityId}`);
  }
  if (entityType === "skill_candidate") {
    const candidateBaselineQuery = Number.isFinite(Number(compareBaselines.candidate))
      ? `&baseline_validation_id=${Number(compareBaselines.candidate)}`
      : "";
    const [candidate, provenance, validations, validationSummary, validationCompare] = await Promise.all([
      request(`/skills/candidates/${entityId}`),
      request(`/skills/candidates/${entityId}/provenance`),
      request(`/skills/validations?candidate_id=${entityId}&limit=8`),
      request(`/skills/validations/summary?candidate_id=${entityId}&limit=20`),
      request(`/skills/validations/compare?candidate_id=${entityId}${candidateBaselineQuery}&limit=8`),
    ]);
    const skillCode = String(candidate?.target_skill_code || "").trim();
    const skillBaselineQuery = Number.isFinite(Number(compareBaselines.skill))
      ? `&baseline_validation_id=${Number(compareBaselines.skill)}`
      : "";
    const [skillValidations, skillValidationSummary, skillValidationCompare] = skillCode
      ? await Promise.all([
          request(`/skills/validations?skill_code=${encodeURIComponent(skillCode)}&limit=8`),
          request(`/skills/validations/summary?skill_code=${encodeURIComponent(skillCode)}&limit=50`),
          request(`/skills/validations/compare?skill_code=${encodeURIComponent(skillCode)}${skillBaselineQuery}&limit=8`),
        ])
      : [[], null, null];
    return { candidate, provenance, validations, validationSummary, validationCompare, skillValidations, skillValidationSummary, skillValidationCompare };
  }
  if (entityType === "skill_validation") {
    return request(`/skills/validations/${entityId}`);
  }
  if (entityType === "skill_revision") {
    const [revision, provenance] = await Promise.all([
      request(`/skills/revisions/${entityId}`),
      request(`/skills/revisions/${entityId}/provenance`),
    ]);
    const skillCode = String(revision?.skill_code || "").trim();
    const skillBaselineQuery = Number.isFinite(Number(compareBaselines.skill))
      ? `&baseline_validation_id=${Number(compareBaselines.skill)}`
      : "";
    const [validations, validationSummary, validationCompare] = skillCode
      ? await Promise.all([
          request(`/skills/validations?skill_code=${encodeURIComponent(skillCode)}&limit=8`),
          request(`/skills/validations/summary?skill_code=${encodeURIComponent(skillCode)}&limit=50`),
          request(`/skills/validations/compare?skill_code=${encodeURIComponent(skillCode)}${skillBaselineQuery}&limit=8`),
        ])
      : [[], null, null];
    return { revision, provenance, validations, validationSummary, validationCompare };
  }
  throw new Error(`Unsupported learning detail type: ${entityType}`);
}

async function fetchRuntimeMemoryInspectionPayload({
  ticker = null,
  strategyVersionId = null,
  skillCodes = [],
  phase = "do",
} = {}) {
  const normalizedTicker = normalizeTicker(ticker);
  const normalizedStrategyVersionId = Number.isFinite(Number(strategyVersionId)) && Number(strategyVersionId) > 0
    ? Number(strategyVersionId)
    : null;
  const normalizedSkillCodes = Array.isArray(skillCodes)
    ? parseCommaSeparatedList(skillCodes.join(","))
    : parseCommaSeparatedList(skillCodes);
  const normalizedPhase = String(phase || "do").trim() || "do";
  if (!normalizedTicker && normalizedStrategyVersionId === null && !normalizedSkillCodes.length) {
    return null;
  }
  const params = new URLSearchParams();
  if (normalizedTicker) params.set("ticker", normalizedTicker);
  if (normalizedStrategyVersionId !== null) params.set("strategy_version_id", String(normalizedStrategyVersionId));
  if (normalizedSkillCodes.length) params.set("skill_codes", normalizedSkillCodes.join(","));
  if (normalizedPhase) params.set("phase", normalizedPhase);
  return request(`/memory/runtime-inspect?${params.toString()}`);
}

function parsePositiveInt(value) {
  const normalized = Number(value);
  return Number.isFinite(normalized) && normalized > 0 ? normalized : null;
}

function strategyVersionIdFromScope(scope) {
  const normalized = String(scope || "").trim();
  if (!normalized.startsWith("strategy:")) return null;
  return parsePositiveInt(normalized.slice("strategy:".length));
}

function deriveLearningDetailRuntimeRequest(entityType, payload) {
  if (entityType === "workflow") {
    const workflow = asObject(payload);
    const context = asObject(workflow.context);
    const ticker = normalizeTicker(context.ticker || context.linked_ticker);
    const strategyVersionId = parsePositiveInt(context.strategy_version_id) || strategyVersionIdFromScope(workflow.scope);
    const skillCode = String(context.target_skill_code || context.primary_skill_code || "").trim();
    if (!ticker && strategyVersionId === null && !skillCode) return null;
    return {
      ticker,
      strategyVersionId,
      skillCodes: skillCode ? [skillCode] : [],
      phase: String(context.phase || "do").trim() || "do",
      label: "workflow runtime relevance",
    };
  }
  if (entityType === "claim") {
    const claim = asObject(payload?.claim);
    const provenance = asObject(payload?.provenance);
    const candidate = asObject(provenance.candidate);
    const revision = asObject(provenance.revision);
    const skillCode = String(
      candidate.target_skill_code
      || asObject(candidate.meta).target_skill_code
      || revision.skill_code
      || "",
    ).trim();
    const requestPayload = {
      ticker: normalizeTicker(claim.linked_ticker),
      strategyVersionId: parsePositiveInt(claim.strategy_version_id) || strategyVersionIdFromScope(claim.scope),
      skillCodes: skillCode ? [skillCode] : [],
      phase: "do",
      label: "claim runtime relevance",
    };
    if (!requestPayload.ticker && requestPayload.strategyVersionId === null && !requestPayload.skillCodes.length) return null;
    return requestPayload;
  }
  if (entityType === "skill_gap") {
    const gap = asObject(payload);
    const skillCode = String(gap.target_skill_code || gap.linked_skill_code || "").trim();
    const requestPayload = {
      ticker: normalizeTicker(gap.ticker),
      strategyVersionId: parsePositiveInt(gap.strategy_version_id) || strategyVersionIdFromScope(gap.scope),
      skillCodes: skillCode ? [skillCode] : [],
      phase: "do",
      label: "skill gap runtime relevance",
    };
    if (!requestPayload.ticker && requestPayload.strategyVersionId === null && !requestPayload.skillCodes.length) return null;
    return requestPayload;
  }
  if (entityType === "distillation_digest") {
    const digest = asObject(payload);
    const meta = asObject(digest.meta);
    const skillCode = String(meta.target_skill_code || "").trim();
    const requestPayload = {
      ticker: normalizeTicker(meta.ticker),
      strategyVersionId: parsePositiveInt(meta.strategy_version_id) || strategyVersionIdFromScope(meta.scope || digest.scope),
      skillCodes: skillCode ? [skillCode] : [],
      phase: "do",
      label: "digest runtime relevance",
    };
    if (!requestPayload.ticker && requestPayload.strategyVersionId === null && !requestPayload.skillCodes.length) return null;
    return requestPayload;
  }
  if (entityType === "skill_candidate") {
    const candidate = asObject(payload?.candidate) && Object.keys(asObject(payload?.candidate)).length
      ? asObject(payload.candidate)
      : asObject(payload);
    const skillCode = String(candidate.target_skill_code || "").trim();
    const requestPayload = {
      ticker: normalizeTicker(candidate.ticker),
      strategyVersionId: parsePositiveInt(candidate.strategy_version_id) || strategyVersionIdFromScope(candidate.scope),
      skillCodes: skillCode ? [skillCode] : [],
      phase: "do",
      label: "candidate runtime relevance",
    };
    if (!requestPayload.ticker && requestPayload.strategyVersionId === null && !requestPayload.skillCodes.length) return null;
    return requestPayload;
  }
  if (entityType === "skill_revision") {
    const revision = asObject(payload?.revision) && Object.keys(asObject(payload?.revision)).length
      ? asObject(payload.revision)
      : asObject(payload);
    const skillCode = String(revision.skill_code || "").trim();
    const requestPayload = {
      ticker: normalizeTicker(revision.ticker),
      strategyVersionId: parsePositiveInt(revision.strategy_version_id) || strategyVersionIdFromScope(revision.scope),
      skillCodes: skillCode ? [skillCode] : [],
      phase: "do",
      label: "revision runtime relevance",
    };
    if (!requestPayload.ticker && requestPayload.strategyVersionId === null && !requestPayload.skillCodes.length) return null;
    return requestPayload;
  }
  return null;
}

async function fetchLearningDetailRuntimeMemory(entityType, payload) {
  const requestPayload = deriveLearningDetailRuntimeRequest(entityType, payload);
  if (!requestPayload) return null;
  try {
    const runtimePayload = await fetchRuntimeMemoryInspectionPayload(requestPayload);
    return {
      request: requestPayload,
      payload: runtimePayload,
      error: null,
    };
  } catch (error) {
    return {
      request: requestPayload,
      payload: null,
      error: error instanceof Error ? error.message : String(error),
    };
  }
}

function runtimeMemoryRequestKey(requestPayload) {
  const payload = asObject(requestPayload);
  return JSON.stringify({
    ticker: normalizeTicker(payload.ticker),
    strategyVersionId: parsePositiveInt(payload.strategyVersionId ?? payload.strategy_version_id),
    skillCodes: parseCommaSeparatedList(Array.isArray(payload.skillCodes) ? payload.skillCodes.join(",") : payload.skill_codes),
    phase: String(payload.phase || "do").trim() || "do",
  });
}

function renderRuntimeMemoryLoadButton(requestPayload, { label = "Open In Inspector" } = {}) {
  if (!requestPayload) return "";
  const isSynced = runtimeMemoryRequestKey(requestPayload) === runtimeMemoryRequestKey({
    ticker: state.runtimeMemory.ticker,
    strategyVersionId: state.runtimeMemory.strategyVersionId,
    skillCodes: state.runtimeMemory.skillCodes,
    phase: state.runtimeMemory.phase,
  });
  if (isSynced) return pill("inspector synced", "pill-active");
  const payload = {
    ticker: normalizeTicker(requestPayload.ticker),
    strategyVersionId: parsePositiveInt(requestPayload.strategyVersionId),
    skillCodes: Array.isArray(requestPayload.skillCodes) ? requestPayload.skillCodes : [],
    phase: String(requestPayload.phase || "do").trim() || "do",
  };
  return `<button type="button" class="action action-muted runtime-memory-load-button" data-runtime-request="${escapeHtml(JSON.stringify(payload))}">${escapeHtml(label)}</button>`;
}

function buildLearningDetailRuntimeMatchSummary(entityType, entityId, runtimePayload, requestPayload) {
  const payload = asObject(runtimePayload);
  const runtimeSkills = Array.isArray(payload.runtime_skills) ? payload.runtime_skills : [];
  const runtimeClaims = Array.isArray(payload.runtime_claims) ? payload.runtime_claims : [];
  const runtimeDistillations = Array.isArray(payload.runtime_distillations) ? payload.runtime_distillations : [];
  const skillCodes = Array.isArray(requestPayload?.skillCodes) ? requestPayload.skillCodes : [];
  const detailId = parsePositiveInt(entityId);
  const skillMatched = skillCodes.some((code) => runtimeSkills.some((item) => String(item.skill_code || "").trim() === code));
  const digestMatched = detailId !== null && runtimeDistillations.some((item) => parsePositiveInt(item.digest_id) === detailId);
  const claimMatched = detailId !== null && runtimeClaims.some((item) => parsePositiveInt(item.claim_id) === detailId);
  const distillationSkillMatched = skillCodes.some((code) =>
    runtimeDistillations.some((item) => String(item.target_skill_code || "").trim() === code),
  );

  if (entityType === "claim") {
    return claimMatched ? "Claim loaded now in runtime memory." : "Claim not loaded now; only related runtime context is available.";
  }
  if (entityType === "distillation_digest") {
    return digestMatched ? "This digest is loaded now in runtime memory." : "This digest is not loaded now; only adjacent runtime context is available.";
  }
  if (["skill_gap", "skill_candidate", "skill_revision"].includes(entityType)) {
    if (skillMatched) return "Target skill is loaded now as a runtime skill packet.";
    if (distillationSkillMatched) return "Target skill is not loaded as a direct skill packet, but related distillation memory is active.";
    return "Target skill is not currently loaded in runtime memory.";
  }
  if (entityType === "workflow") {
    if (skillMatched || distillationSkillMatched || claimMatched || digestMatched) {
      return "Workflow scope overlaps with currently loaded runtime memory.";
    }
    return "Workflow scope is not currently reflected in loaded runtime packets.";
  }
  return "Runtime relevance resolved for this detail.";
}

function renderLearningDetailRuntimeMemoryPanel() {
  const runtimeMemory = state.learningDetail.runtimeMemory;
  if (!runtimeMemory || !runtimeMemory.request) return "";

  const requestPayload = asObject(runtimeMemory.request);
  const runtimePayload = runtimeMemory.payload;
  const error = String(runtimeMemory.error || "").trim();
  const requestSummary = [
    normalizeTicker(requestPayload.ticker) || "sin ticker",
    parsePositiveInt(requestPayload.strategyVersionId) ? `strategy ${parsePositiveInt(requestPayload.strategyVersionId)}` : "sin strategy",
    Array.isArray(requestPayload.skillCodes) && requestPayload.skillCodes.length ? `skills ${requestPayload.skillCodes.join(", ")}` : "sin skill override",
  ].join(" · ");

  if (!runtimePayload) {
    return `
      <article class="stack-item">
        <h3>Runtime Relevance</h3>
        <div class="row">
          ${pill("runtime inspect", "pill-degraded")}
          ${renderRuntimeMemoryLoadButton(requestPayload)}
        </div>
        <p class="muted">${escapeHtml(requestSummary)}</p>
        <p class="muted">${escapeHtml(error || "No se pudo resolver runtime memory para este detail.")}</p>
      </article>
    `;
  }

  const payload = asObject(runtimePayload);
  const source = asObject(payload.skill_context_source);
  const context = asObject(payload.resolved_skill_context);
  const runtimeSkills = Array.isArray(payload.runtime_skills) ? payload.runtime_skills : [];
  const runtimeClaims = Array.isArray(payload.runtime_claims) ? payload.runtime_claims : [];
  const runtimeDistillations = Array.isArray(payload.runtime_distillations) ? payload.runtime_distillations : [];
  const budgetSummary = summarizeContextBudget(payload.context_budget);
  const entityType = state.learningDetail.entityType;
  const entityId = state.learningDetail.entityId;
  const sourceType = String(source.source_type || "none").trim() || "none";
  const primarySkill = String(context.primary_skill_code || "").trim();
  const matchSummary = buildLearningDetailRuntimeMatchSummary(entityType, entityId, payload, requestPayload);

  return `
    <article class="stack-item">
      <h3>Runtime Relevance</h3>
      <div class="row">
        ${pill(sourceType, sourceType === "none" ? "pill-degraded" : "pill-info")}
        ${primarySkill ? pill(primarySkill, "pill-approved") : ""}
        ${context.phase ? pill(`phase ${context.phase}`, "pill-candidate") : ""}
        ${budgetSummary ? pill(budgetSummary.headline, budgetSummary.truncated ? "pill-degraded" : "pill-active") : ""}
        ${renderRuntimeMemoryLoadButton(requestPayload)}
      </div>
      <p class="muted">${escapeHtml(requestSummary)}</p>
      <p class="muted">${escapeHtml(matchSummary)}</p>
      <p class="muted">${escapeHtml(
        source.summary
          || context.summary
          || "Runtime memory bounded resuelto para este artefacto.",
      )}</p>
      ${runtimeSkills.length ? `<div><strong>Runtime Skills</strong>${renderRuntimeMemorySkillPackets(runtimeSkills.slice(0, 2))}</div>` : ""}
      ${runtimeClaims.length ? `<div><strong>Runtime Claims</strong>${renderRuntimeMemoryClaimPackets(runtimeClaims.slice(0, 2))}</div>` : ""}
      ${runtimeDistillations.length ? `<div><strong>Runtime Distillations</strong>${renderRuntimeMemoryDistillationPackets(runtimeDistillations.slice(0, 2))}</div>` : ""}
      ${
        !runtimeSkills.length && !runtimeClaims.length && !runtimeDistillations.length
          ? `<p class="muted">No hay runtime packets cargados para esta combinacion.</p>`
          : ""
      }
    </article>
  `;
}

function renderLearningDetail() {
  if (!elements.learningDetail) return;
  const entityType = state.learningDetail.entityType;
  const entityId = state.learningDetail.entityId;
  const payload = state.learningDetail.payload;
  if (!entityType || !payload) {
    elements.learningDetail.innerHTML = `<div class="stack-item"><p class="muted">Selecciona un workflow, claim, gap, candidate o digest para ver su detalle enlazado.</p></div>`;
    return;
  }
  const runtimePanel = renderLearningDetailRuntimeMemoryPanel();
  if (entityType === "workflow") {
    elements.learningDetail.innerHTML = renderWorkflowDetailPanel(payload) + runtimePanel + renderLearningActionComposer();
    attachLearningPanelButtons(elements.learningDetail);
    return;
  }
  if (entityType === "claim") {
    elements.learningDetail.innerHTML = renderClaimDetailPanel(payload) + runtimePanel + renderLearningActionComposer();
    attachLearningPanelButtons(elements.learningDetail);
    return;
  }
  if (entityType === "skill_gap") {
    elements.learningDetail.innerHTML = renderSkillGapDetailPanel(payload) + runtimePanel + renderLearningActionComposer();
    attachLearningPanelButtons(elements.learningDetail);
    return;
  }
  if (entityType === "distillation_digest") {
    elements.learningDetail.innerHTML = renderDistillationDigestDetailPanel(payload) + runtimePanel + renderLearningActionComposer();
    attachLearningPanelButtons(elements.learningDetail);
    return;
  }
  if (entityType === "skill_candidate") {
    elements.learningDetail.innerHTML = renderSkillCandidateDetailPanel(payload) + runtimePanel + renderLearningActionComposer();
    attachLearningPanelButtons(elements.learningDetail);
    return;
  }
  if (entityType === "skill_validation") {
    elements.learningDetail.innerHTML = renderSkillValidationDetailPanel(payload) + runtimePanel;
    attachLearningPanelButtons(elements.learningDetail);
    return;
  }
  if (entityType === "skill_revision") {
    elements.learningDetail.innerHTML = renderSkillRevisionDetailPanel(payload) + runtimePanel;
    attachLearningPanelButtons(elements.learningDetail);
    return;
  }
  elements.learningDetail.innerHTML = `<div class="stack-item"><p class="muted">No hay render para ${escapeHtml(entityType)} ${entityId}.</p></div>`;
}

async function refreshLearningDetail({ silent = true } = {}) {
  const entityType = state.learningDetail.entityType;
  const entityId = state.learningDetail.entityId;
  if (!entityType || !Number.isFinite(Number(entityId))) return;
  await loadLearningDetail(entityType, entityId, { silent });
}

function renderWorkflowDetailPanel(workflow) {
  const items = Array.isArray(workflow?.items) ? workflow.items : [];
  const history = Array.isArray(workflow?.history) ? workflow.history : [];
  return `
    <article class="stack-item">
      <h3>${escapeHtml(workflow?.title || workflow?.workflow_type || "Workflow")}</h3>
      <div class="row">
        ${pill(workflow?.workflow_type || "workflow", "pill-info")}
        ${pill(workflow?.status || "unknown", workflow?.status === "resolved" ? "pill-approved" : "pill-degraded")}
        ${pill(workflow?.priority || "normal", workflow?.priority === "high" ? "pill-reject" : "pill-candidate")}
        ${pill(`${workflow?.open_item_count ?? 0}/${workflow?.item_count ?? 0} open`, "pill-candidate")}
      </div>
      <p class="muted">${escapeHtml(workflow?.summary || "Sin resumen.")}</p>
      <p class="muted">${escapeHtml(`scope=${workflow?.scope || "n/a"} · synced=${formatDate(workflow?.last_synced_at || workflow?.updated_at || workflow?.created_at)}`)}</p>
      <div class="snapshot-list">
        ${items.length
          ? items.slice(0, 6).map((item) => `
              <div class="snapshot-item">
                <span>${escapeHtml(item.title || item.item_type || "item")}</span>
                <span class="muted">${escapeHtml(`${item.item_type || "item"} · ${item.status || "pending"}`)}</span>
                <span class="row">
                  ${renderLearningDetailButton(mapWorkflowItemToEntity(item))}
                  ${renderWorkflowItemActions(workflow, item, { inlineReview: true })}
                </span>
              </div>
            `).join("")
          : `<div class="muted">Sin items activos.</div>`}
      </div>
      <div class="snapshot-list">
        ${history.length
          ? history.slice(0, 8).map((entry) => renderWorkflowHistoryEntry(entry)).join("")
          : `<div class="muted">Sin historial.</div>`}
      </div>
    </article>
  `;
}

function renderClaimDetailPanel(payload) {
  const claim = asObject(payload?.claim);
  const evidence = Array.isArray(payload?.evidence) ? payload.evidence : [];
  const provenance = asObject(payload?.provenance);
  const linkedCandidateId = claim?.meta?.linked_skill_candidate_id;
  const canPromote = !linkedCandidateId && ["supported", "validated"].includes(claim.status);
  return `
    <article class="stack-item">
      <h3>${escapeHtml(claim.claim_text || `Claim ${claim.id || ""}`)}</h3>
      <div class="row">
        ${pill(claim.claim_type || "claim", "pill-info")}
        ${pill(claim.status || "unknown", claim.status === "validated" ? "pill-approved" : "pill-degraded")}
        ${pill(claim.freshness_state || "unknown", "pill-candidate")}
        ${claim.linked_ticker ? pill(claim.linked_ticker, "pill-approved") : ""}
        ${linkedCandidateId ? renderLearningDetailButton({ entityType: "skill_candidate", entityId: linkedCandidateId }) : ""}
      </div>
      <p class="muted">${escapeHtml(`scope=${claim.scope || "n/a"} · support=${claim.support_count ?? 0} · contradiction=${claim.contradiction_count ?? 0} · confidence=${formatNumber(claim.confidence)}`)}</p>
      <div class="row skill-action-row">
        ${renderClaimReviewActions(claim, { inlineReview: true })}
        ${canPromote ? `<button type="button" class="action action-success claim-promote-button" data-claim-id="${claim.id}">Promote</button>` : ""}
      </div>
      ${renderSkillProvenancePanel(provenance, "claim")}
      <div class="snapshot-list">
        ${evidence.length
          ? evidence.slice(0, 8).map((entry) => `
              <div class="snapshot-item">
                <span>${escapeHtml(entry.summary || entry.source_type || "Evidence")}</span>
                <span class="muted">${escapeHtml(`${entry.stance || "support"} · strength=${formatNumber(entry.strength)} · ${formatDate(entry.observed_at || entry.created_at)}`)}</span>
              </div>
            `).join("")
          : `<div class="muted">Sin evidencia registrada.</div>`}
      </div>
    </article>
  `;
}

function renderSkillGapDetailPanel(gap) {
  return `
    <article class="stack-item">
      <h3>${escapeHtml(gap?.summary || `Skill gap ${gap?.id || ""}`)}</h3>
      <div class="row">
        ${pill(gap?.gap_type || "skill_gap", "pill-info")}
        ${pill(gap?.status || "open", gap?.status === "resolved" ? "pill-approved" : gap?.status === "dismissed" ? "pill-degraded" : "pill-reject")}
        ${gap?.ticker ? pill(gap.ticker, "pill-approved") : ""}
        ${gap?.target_skill_code ? pill(gap.target_skill_code, "pill-candidate") : ""}
      </div>
      <p class="muted">${escapeHtml(`scope=${gap?.scope || "n/a"} · source=${gap?.source_type || "n/a"} · importance=${formatNumber(gap?.importance)}`)}</p>
      <div class="row skill-action-row">
        ${renderSkillGapActions(gap, { inlineReview: true })}
      </div>
      <pre class="code-block">${escapeHtml(JSON.stringify(gap?.meta || {}, null, 2))}</pre>
    </article>
  `;
}

function renderDistillationDigestDetailPanel(digest) {
  const meta = asObject(digest?.meta);
  const reviewStatus = String(meta.review_status || "pending").trim().toLowerCase();
  const distillationType = String(meta.distillation_type || digest?.memory_type || "distillation").trim();
  const targetSkillCode = String(meta.target_skill_code || "").trim();
  const effect = asObject(meta.review_effect);
  return `
    <article class="stack-item">
      <h3>${escapeHtml(targetSkillCode || digest?.key || `Digest ${digest?.id || ""}`)}</h3>
      <div class="row">
        ${pill(distillationType, "pill-info")}
        ${pill(reviewStatus, reviewStatus === "applied" ? "pill-approved" : "pill-degraded")}
        ${meta.review_action ? pill(meta.review_action, "pill-candidate") : ""}
        ${meta.ticker ? pill(meta.ticker, "pill-approved") : ""}
      </div>
      <p class="muted">${escapeHtml(digest?.content || "Sin resumen.")}</p>
      <p class="muted">${escapeHtml(`scope=${digest?.scope || "n/a"} · importance=${formatNumber(digest?.importance)} · created=${formatDate(digest?.created_at)}`)}</p>
      <div class="row skill-action-row">
        ${renderDistillationReviewActions(digest, { inlineReview: true })}
      </div>
      ${meta.review_summary ? `<p class="muted">${escapeHtml(`review=${meta.review_summary}`)}</p>` : ""}
      ${Object.keys(effect).length ? `<pre class="code-block">${escapeHtml(JSON.stringify(effect, null, 2))}</pre>` : ""}
      <pre class="code-block">${escapeHtml(JSON.stringify(meta, null, 2))}</pre>
    </article>
  `;
}

function renderSkillCandidateDetailPanel(candidate) {
  const candidatePayload = asObject(candidate?.candidate) && Object.keys(asObject(candidate?.candidate)).length
    ? asObject(candidate.candidate)
    : asObject(candidate);
  const provenance = asObject(candidate?.provenance);
  const validations = Array.isArray(candidate?.validations) ? candidate.validations : [];
  const validationSummary = asObject(candidate?.validationSummary);
  const validationCompare = asObject(candidate?.validationCompare);
  const skillValidations = Array.isArray(candidate?.skillValidations) ? candidate.skillValidations : [];
  const skillValidationSummary = asObject(candidate?.skillValidationSummary);
  const skillValidationCompare = asObject(candidate?.skillValidationCompare);
  const sourceClaimId = candidatePayload?.meta?.source_claim_id;
  const meta = asObject(candidatePayload?.meta);
  const activeRevisionId = Number(meta.active_revision_id);
  const latestValidationRecordId = Number(candidatePayload?.latest_validation_record_id ?? meta.latest_validation_record_id);
  const lastValidationEvidence = asObject(meta.last_validation_evidence);
  const validationBits = [];
  if (Number.isFinite(parseDraftNumber(meta.last_validation_sample_size))) {
    validationBits.push(`n=${formatDraftNumber(parseDraftNumber(meta.last_validation_sample_size))}`);
  }
  if (Number.isFinite(parseDraftNumber(meta.last_validation_win_rate))) {
    validationBits.push(`win=${formatDraftNumber(parseDraftNumber(meta.last_validation_win_rate))}%`);
  }
  if (Number.isFinite(parseDraftNumber(meta.last_validation_avg_pnl_pct))) {
    validationBits.push(`avg=${formatDraftNumber(parseDraftNumber(meta.last_validation_avg_pnl_pct))}%`);
  }
  if (Number.isFinite(parseDraftNumber(meta.last_validation_max_drawdown_pct))) {
    validationBits.push(`dd=${formatDraftNumber(parseDraftNumber(meta.last_validation_max_drawdown_pct))}%`);
  }
  const evidenceBits = [];
  if (lastValidationEvidence.run_id) {
    evidenceBits.push(`run=${lastValidationEvidence.run_id}`);
  }
  if (lastValidationEvidence.note) {
    evidenceBits.push(`note=${lastValidationEvidence.note}`);
  }
  const artifactUrl = String(lastValidationEvidence.artifact_url || "").trim();
  return `
    <article class="stack-item">
      <h3>${escapeHtml(candidatePayload?.summary || `Skill candidate ${candidatePayload?.id || ""}`)}</h3>
      <div class="row">
        ${pill(candidatePayload?.candidate_status || "draft", candidatePayload?.candidate_status === "validated" ? "pill-approved" : candidatePayload?.candidate_status === "rejected" ? "pill-reject" : "pill-candidate")}
        ${pill(candidatePayload?.candidate_action || "candidate", "pill-info")}
        ${candidatePayload?.ticker ? pill(candidatePayload.ticker, "pill-approved") : ""}
        ${candidatePayload?.target_skill_code ? pill(candidatePayload.target_skill_code, "pill-candidate") : ""}
        ${sourceClaimId ? renderLearningDetailButton({ entityType: "claim", entityId: sourceClaimId }) : ""}
        ${Number.isFinite(latestValidationRecordId) ? renderLearningDetailButton({ entityType: "skill_validation", entityId: latestValidationRecordId }) : ""}
        ${Number.isFinite(activeRevisionId) ? renderLearningDetailButton({ entityType: "skill_revision", entityId: activeRevisionId }) : ""}
      </div>
      <p class="muted">${escapeHtml(`scope=${candidatePayload?.scope || "n/a"} · source=${candidatePayload?.source_type || "n/a"} · importance=${formatNumber(candidatePayload?.importance)}`)}</p>
      ${validationBits.length ? `<p class="muted">${escapeHtml(`last validation · ${validationBits.join(" · ")}`)}</p>` : ""}
      ${evidenceBits.length ? `<p class="muted">${escapeHtml(`evidence · ${evidenceBits.join(" · ")}`)}</p>` : ""}
      ${artifactUrl ? `<p class="muted">artifact · <a href="${escapeHtml(artifactUrl)}" target="_blank" rel="noreferrer">open link</a></p>` : ""}
      <div class="row skill-action-row">
        ${renderSkillCandidateActions(candidatePayload, { inlineReview: true })}
      </div>
      ${renderSkillProvenancePanel(provenance, "skill_candidate")}
      ${renderSkillValidationSummaryPanel(validationSummary, { title: "Candidate Validation Summary" })}
      ${renderSkillValidationComparePanel(validationCompare, { title: "Candidate Validation Compare" })}
      ${renderSkillValidationHistoryPanel(validations, {
        title: "Candidate Validation History",
        currentValidationId: latestValidationRecordId,
      })}
      ${renderSkillValidationSummaryPanel(skillValidationSummary, { title: "Skill Validation Summary" })}
      ${renderSkillValidationComparePanel(skillValidationCompare, { title: "Skill Validation Compare" })}
      ${renderSkillValidationHistoryPanel(skillValidations, {
        title: "Skill Validation History",
        currentValidationId: latestValidationRecordId,
      })}
      <pre class="code-block">${escapeHtml(JSON.stringify(candidatePayload?.meta || {}, null, 2))}</pre>
    </article>
  `;
}

function renderSkillRevisionDetailPanel(revision) {
  const revisionPayload = asObject(revision?.revision) && Object.keys(asObject(revision?.revision)).length
    ? asObject(revision.revision)
    : asObject(revision);
  const provenance = asObject(revision?.provenance);
  const validations = Array.isArray(revision?.validations) ? revision.validations : [];
  const validationSummary = asObject(revision?.validationSummary);
  const validationCompare = asObject(revision?.validationCompare);
  const meta = asObject(revisionPayload?.meta);
  const evidence = asObject(meta.evidence);
  const candidateId = Number(revisionPayload?.candidate_id);
  const validationRecordId = Number(revisionPayload?.validation_record_id ?? meta.validation_record_id);
  const runId = String(evidence.run_id || "").trim();
  const artifactUrl = String(evidence.artifact_url || "").trim();
  const evidenceNote = String(evidence.note || "").trim();
  const metricBits = [];
  if (Number.isFinite(parseDraftNumber(meta.sample_size))) metricBits.push(`n=${formatDraftNumber(parseDraftNumber(meta.sample_size))}`);
  if (Number.isFinite(parseDraftNumber(meta.win_rate))) metricBits.push(`win=${formatDraftNumber(parseDraftNumber(meta.win_rate))}%`);
  if (Number.isFinite(parseDraftNumber(meta.avg_pnl_pct))) metricBits.push(`avg=${formatDraftNumber(parseDraftNumber(meta.avg_pnl_pct))}%`);
  if (Number.isFinite(parseDraftNumber(meta.max_drawdown_pct))) metricBits.push(`dd=${formatDraftNumber(parseDraftNumber(meta.max_drawdown_pct))}%`);
  return `
    <article class="stack-item">
      <h3>${escapeHtml(revisionPayload?.skill_code || `Revision ${revisionPayload?.id || ""}`)}</h3>
      <div class="row">
        ${pill(revisionPayload?.activation_status || "inactive", revisionPayload?.activation_status === "active" ? "pill-approved" : "pill-degraded")}
        ${revisionPayload?.validation_mode ? pill(revisionPayload.validation_mode, "pill-info") : ""}
        ${revisionPayload?.validation_outcome ? pill(revisionPayload.validation_outcome, revisionPayload.validation_outcome === "approved" ? "pill-approved" : "pill-reject") : ""}
        ${revisionPayload?.ticker ? pill(revisionPayload.ticker, "pill-approved") : ""}
        ${Number.isFinite(candidateId) ? renderLearningDetailButton({ entityType: "skill_candidate", entityId: candidateId }) : ""}
        ${Number.isFinite(validationRecordId) ? renderLearningDetailButton({ entityType: "skill_validation", entityId: validationRecordId }) : ""}
      </div>
      <p class="muted">${escapeHtml(revisionPayload?.revision_summary || "Sin resumen.")}</p>
      <p class="muted">${escapeHtml(`created=${formatDate(revisionPayload?.created_at)} · strategy_version=${revisionPayload?.strategy_version_id || "n/a"}`)}</p>
      ${metricBits.length ? `<p class="muted">${escapeHtml(`validation · ${metricBits.join(" · ")}`)}</p>` : ""}
      ${runId ? `<p class="muted">${escapeHtml(`run · ${runId}`)}</p>` : ""}
      ${evidenceNote ? `<p class="muted">${escapeHtml(`note · ${evidenceNote}`)}</p>` : ""}
      ${artifactUrl ? `<p class="muted">artifact · <a href="${escapeHtml(artifactUrl)}" target="_blank" rel="noreferrer">open link</a></p>` : ""}
      ${renderSkillProvenancePanel(provenance, "skill_revision")}
      ${renderSkillValidationSummaryPanel(validationSummary, { title: "Skill Validation Summary" })}
      ${renderSkillValidationComparePanel(validationCompare, { title: "Skill Validation Compare" })}
      ${renderSkillValidationHistoryPanel(validations, {
        title: "Skill Validation History",
        currentValidationId: validationRecordId,
      })}
      <pre class="code-block">${escapeHtml(JSON.stringify(revisionPayload?.meta || {}, null, 2))}</pre>
    </article>
  `;
}

function renderSkillValidationDetailPanel(record) {
  const payload = asObject(record);
  const candidateId = Number(payload.candidate_id);
  const revisionId = Number(payload.revision_id);
  const metricBits = [];
  if (Number.isFinite(parseDraftNumber(payload.sample_size))) metricBits.push(`n=${formatDraftNumber(parseDraftNumber(payload.sample_size))}`);
  if (Number.isFinite(parseDraftNumber(payload.win_rate))) metricBits.push(`win=${formatDraftNumber(parseDraftNumber(payload.win_rate))}%`);
  if (Number.isFinite(parseDraftNumber(payload.avg_pnl_pct))) metricBits.push(`avg=${formatDraftNumber(parseDraftNumber(payload.avg_pnl_pct))}%`);
  if (Number.isFinite(parseDraftNumber(payload.max_drawdown_pct))) metricBits.push(`dd=${formatDraftNumber(parseDraftNumber(payload.max_drawdown_pct))}%`);
  const artifactUrl = String(payload.artifact_url || "").trim();
  return `
    <article class="stack-item">
      <h3>${escapeHtml(payload.summary || `Validation ${payload.id || ""}`)}</h3>
      <div class="row">
        ${pill(payload.validation_mode || "validation", "pill-info")}
        ${pill(payload.validation_outcome || "unknown", payload.validation_outcome === "approved" ? "pill-approved" : "pill-reject")}
        ${Number.isFinite(candidateId) ? renderLearningDetailButton({ entityType: "skill_candidate", entityId: candidateId }) : ""}
        ${Number.isFinite(revisionId) ? renderLearningDetailButton({ entityType: "skill_revision", entityId: revisionId }) : ""}
      </div>
      <p class="muted">${escapeHtml(`created=${formatDate(payload.created_at)}${payload.run_id ? ` · run=${payload.run_id}` : ""}`)}</p>
      ${metricBits.length ? `<p class="muted">${escapeHtml(`metrics · ${metricBits.join(" · ")}`)}</p>` : ""}
      ${payload.evidence_note ? `<p class="muted">${escapeHtml(`note · ${payload.evidence_note}`)}</p>` : ""}
      ${artifactUrl ? `<p class="muted">artifact · <a href="${escapeHtml(artifactUrl)}" target="_blank" rel="noreferrer">open link</a></p>` : ""}
      <pre class="code-block">${escapeHtml(JSON.stringify(payload.evidence_payload || {}, null, 2))}</pre>
    </article>
  `;
}

function renderSkillValidationHistoryPanel(records, { title, currentValidationId = null } = {}) {
  const items = Array.isArray(records) ? records : [];
  if (!items.length) return "";
  return `
    <div class="snapshot-list">
      <div class="snapshot-item">
        <strong>${escapeHtml(title || "Validation History")}</strong>
        <span class="muted">${escapeHtml(`${items.length} records`)}</span>
        <span></span>
      </div>
      ${items.slice(0, 8).map((item) => {
        const metricBits = [];
        if (Number.isFinite(parseDraftNumber(item.sample_size))) metricBits.push(`n=${formatDraftNumber(parseDraftNumber(item.sample_size))}`);
        if (Number.isFinite(parseDraftNumber(item.win_rate))) metricBits.push(`win=${formatDraftNumber(parseDraftNumber(item.win_rate))}%`);
        if (Number.isFinite(parseDraftNumber(item.avg_pnl_pct))) metricBits.push(`avg=${formatDraftNumber(parseDraftNumber(item.avg_pnl_pct))}%`);
        const summary = [item.validation_mode, item.validation_outcome, item.run_id].filter(Boolean).join(" · ");
        return `
          <div class="snapshot-item">
            <span>${escapeHtml(item.summary || `Validation ${item.id}`)}</span>
            <span class="muted">${escapeHtml(`${summary}${metricBits.length ? ` · ${metricBits.join(" · ")}` : ""}`)}</span>
            <span class="row">
              ${Number(item.id) === Number(currentValidationId)
                ? pill("current", "pill-info")
                : renderLearningDetailButton({ entityType: "skill_validation", entityId: item.id })}
            </span>
          </div>
        `;
      }).join("")}
    </div>
  `;
}

function renderSkillValidationSummaryPanel(summary, { title } = {}) {
  const payload = asObject(summary);
  if (!Object.keys(payload).length || !Number.isFinite(Number(payload.record_count)) || Number(payload.record_count) <= 0) {
    return "";
  }
  const winRateDelta = asObject(payload.win_rate_delta);
  const avgPnlDelta = asObject(payload.avg_pnl_pct_delta);
  const drawdownDelta = asObject(payload.max_drawdown_pct_delta);
  const trendBits = [];
  if (Number.isFinite(parseDraftNumber(winRateDelta.delta))) {
    trendBits.push(`win Δ ${formatSignedNumber(parseDraftNumber(winRateDelta.delta), 1)}pp`);
  }
  if (Number.isFinite(parseDraftNumber(avgPnlDelta.delta))) {
    trendBits.push(`avg Δ ${formatSignedPct(parseDraftNumber(avgPnlDelta.delta))}`);
  }
  if (Number.isFinite(parseDraftNumber(drawdownDelta.delta))) {
    trendBits.push(`dd Δ ${formatSignedPct(parseDraftNumber(drawdownDelta.delta))}`);
  }
  return `
    <div class="snapshot-list">
      <div class="snapshot-item">
        <strong>${escapeHtml(title || "Validation Summary")}</strong>
        <span class="muted">${escapeHtml(`${payload.record_count || 0} records · ${payload.approved_count || 0} approved · ${payload.rejected_count || 0} rejected`)}</span>
        <span>${payload.latest_validation_id ? renderLearningDetailButton({ entityType: "skill_validation", entityId: payload.latest_validation_id }) : ""}</span>
      </div>
      <div class="snapshot-item">
        <span>${escapeHtml(`avg win ${formatNullablePct(payload.avg_win_rate)}`)}</span>
        <span class="muted">${escapeHtml(`best ${formatNullablePct(payload.best_win_rate)} · latest run ${payload.latest_run_id || "n/a"}`)}</span>
        <span></span>
      </div>
      <div class="snapshot-item">
        <span>${escapeHtml(`avg pnl ${formatNullablePct(payload.avg_avg_pnl_pct)}`)}</span>
        <span class="muted">${escapeHtml(`best ${formatNullablePct(payload.best_avg_pnl_pct)} · worst dd ${formatNullablePct(payload.worst_max_drawdown_pct)}`)}</span>
        <span></span>
      </div>
      ${trendBits.length ? `
        <div class="snapshot-item">
          <span>${escapeHtml("latest vs previous")}</span>
          <span class="muted">${escapeHtml(trendBits.join(" · "))}</span>
          <span></span>
        </div>
      ` : ""}
    </div>
  `;
}

function renderSkillValidationComparePanel(compare, { title } = {}) {
  const payload = asObject(compare);
  const rows = Array.isArray(payload.rows) ? payload.rows : [];
  if (!rows.length) return "";
  const compareKey = payload.scope_type === "candidate" ? "candidate" : "skill";
  return `
    <div class="snapshot-list">
      <div class="snapshot-item">
        <strong>${escapeHtml(title || "Validation Compare")}</strong>
        <span class="muted">${escapeHtml(`${payload.row_count || rows.length} rows · base ${payload.baseline_run_id || payload.baseline_validation_id || "n/a"}`)}</span>
        <span class="row">
          ${payload.baseline_validation_id ? renderLearningDetailButton({ entityType: "skill_validation", entityId: payload.baseline_validation_id }) : ""}
          ${payload.custom_baseline_applied ? `<button type="button" class="action action-muted validation-compare-reset" data-compare-key="${escapeHtml(compareKey)}">Reset</button>` : ""}
        </span>
      </div>
      ${rows.slice(0, 6).map((row) => {
        const deltaBits = [];
        if (Number.isFinite(parseDraftNumber(row.win_rate_delta_vs_base))) {
          deltaBits.push(`win ${formatSignedNumber(parseDraftNumber(row.win_rate_delta_vs_base), 1)}pp`);
        }
        if (Number.isFinite(parseDraftNumber(row.avg_pnl_pct_delta_vs_base))) {
          deltaBits.push(`avg ${formatSignedPct(parseDraftNumber(row.avg_pnl_pct_delta_vs_base))}`);
        }
        if (Number.isFinite(parseDraftNumber(row.max_drawdown_pct_delta_vs_base))) {
          deltaBits.push(`dd ${formatSignedPct(parseDraftNumber(row.max_drawdown_pct_delta_vs_base))}`);
        }
        const metricBits = [];
        if (Number.isFinite(parseDraftNumber(row.sample_size))) metricBits.push(`n=${formatDraftNumber(parseDraftNumber(row.sample_size))}`);
        if (Number.isFinite(parseDraftNumber(row.win_rate))) metricBits.push(`win=${formatDraftNumber(parseDraftNumber(row.win_rate))}%`);
        if (Number.isFinite(parseDraftNumber(row.avg_pnl_pct))) metricBits.push(`avg=${formatDraftNumber(parseDraftNumber(row.avg_pnl_pct))}%`);
        return `
          <div class="snapshot-item">
            <span>${escapeHtml(`${row.validation_mode || "validation"} · ${row.validation_outcome || "unknown"}${row.run_id ? ` · ${row.run_id}` : ""}`)}</span>
            <span class="muted">${escapeHtml(`${metricBits.join(" · ")}${deltaBits.length ? ` · Δ ${deltaBits.join(" · ")}` : ""}`)}</span>
            <span class="row">
              ${row.is_base
                ? pill("base", "pill-info")
                : `${renderLearningDetailButton({ entityType: "skill_validation", entityId: row.validation_id })}<button type="button" class="action action-muted validation-compare-baseline" data-compare-key="${escapeHtml(compareKey)}" data-validation-id="${row.validation_id}">Use as base</button>`}
            </span>
          </div>
        `;
      }).join("")}
    </div>
  `;
}

function renderSkillProvenancePanel(provenance, currentEntityType) {
  const claim = asObject(provenance?.claim);
  const candidate = asObject(provenance?.candidate);
  const revision = asObject(provenance?.revision);
  const items = [];
  if (claim.id) {
    items.push({
      label: "Claim",
      entityType: "claim",
      entityId: claim.id,
      title: claim.claim_text || `Claim ${claim.id}`,
      meta: `${claim.status || "unknown"} · ${claim.linked_ticker || "no ticker"}`,
      current: currentEntityType === "claim",
    });
  }
  if (candidate.id) {
    items.push({
      label: "Candidate",
      entityType: "skill_candidate",
      entityId: candidate.id,
      title: candidate.summary || `Candidate ${candidate.id}`,
      meta: `${candidate.candidate_status || "draft"} · ${candidate.target_skill_code || "no skill"}`,
      current: currentEntityType === "skill_candidate",
    });
  }
  if (revision.id) {
    items.push({
      label: "Revision",
      entityType: "skill_revision",
      entityId: revision.id,
      title: revision.revision_summary || `Revision ${revision.id}`,
      meta: `${revision.activation_status || "inactive"} · ${revision.skill_code || "no skill"}`,
      current: currentEntityType === "skill_revision",
    });
  }
  if (!items.length) return "";
  return `
    <div class="snapshot-list">
      ${items.map((item) => `
        <div class="snapshot-item">
          <span>${escapeHtml(`${item.label} · ${item.title}`)}</span>
          <span class="muted">${escapeHtml(item.meta)}</span>
          <span class="row">
            ${item.current ? pill("current", "pill-info") : renderLearningDetailButton({ entityType: item.entityType, entityId: item.entityId })}
          </span>
        </div>
      `).join("")}
    </div>
  `;
}

function renderLearningActionComposer() {
  const draft = asObject(state.learningDetail.draft);
  if (!draft.kind) return "";
  const summary = typeof draft.summary === "string" ? draft.summary : "";
  const validationFields = draft.kind === "skill_candidate_validation"
    ? `
      <div class="learning-inline-review-metrics">
        <label class="learning-inline-review-label">
          Sample Size
          <input class="learning-inline-review-input" data-role="learning-inline-number" data-field="sampleSize" type="number" min="1" step="1" value="${escapeHtml(formatDraftNumber(draft.sampleSize))}" />
        </label>
        <label class="learning-inline-review-label">
          Win Rate %
          <input class="learning-inline-review-input" data-role="learning-inline-number" data-field="winRate" type="number" step="0.1" value="${escapeHtml(formatDraftNumber(draft.winRate))}" />
        </label>
        <label class="learning-inline-review-label">
          Avg PnL %
          <input class="learning-inline-review-input" data-role="learning-inline-number" data-field="avgPnlPct" type="number" step="0.1" value="${escapeHtml(formatDraftNumber(draft.avgPnlPct))}" />
        </label>
        <label class="learning-inline-review-label">
          Max Drawdown %
          <input class="learning-inline-review-input" data-role="learning-inline-number" data-field="maxDrawdownPct" type="number" step="0.1" value="${escapeHtml(formatDraftNumber(draft.maxDrawdownPct))}" />
        </label>
        <label class="learning-inline-review-label learning-inline-review-span-2">
          Validation Run ID
          <input class="learning-inline-review-input" data-role="learning-inline-text" data-field="runId" type="text" value="${escapeHtml(String(draft.runId || ""))}" />
        </label>
        <label class="learning-inline-review-label learning-inline-review-span-2">
          Artifact URL
          <input class="learning-inline-review-input" data-role="learning-inline-text" data-field="artifactUrl" type="url" value="${escapeHtml(String(draft.artifactUrl || ""))}" />
        </label>
        <label class="learning-inline-review-label learning-inline-review-span-2">
          Evidence Note
          <input class="learning-inline-review-input" data-role="learning-inline-text" data-field="evidenceNote" type="text" value="${escapeHtml(String(draft.evidenceNote || ""))}" />
        </label>
      </div>
    `
    : "";
  return `
    <article class="stack-item learning-inline-review-card">
      <h3>${escapeHtml(draft.title || "Governance action")}</h3>
      <p class="muted">${escapeHtml(draft.subtitle || "Resume la accion para dejar una traza clara en el loop de aprendizaje.")}</p>
      <form class="learning-inline-review-form">
        <label class="learning-inline-review-label">
          Summary
          <textarea class="learning-inline-review-textarea" data-role="learning-inline-summary" rows="4" placeholder="${escapeHtml(draft.placeholder || "Resume por que se confirma, contradice, resuelve o rechaza esta entidad...")}">${escapeHtml(summary)}</textarea>
        </label>
        ${validationFields}
        <div class="row skill-action-row">
          <button type="submit" class="action action-success">${escapeHtml(draft.submitLabel || "Apply")}</button>
          <button type="button" class="action action-muted learning-inline-review-cancel">Cancel</button>
        </div>
      </form>
    </article>
  `;
}

function openLearningInlineReviewDraft(draft) {
  state.learningDetail.draft = draft;
  renderLearningDetail();
}

function formatDraftNumber(value) {
  return Number.isFinite(value) ? String(value) : "";
}

function parseDraftNumber(rawValue) {
  if (rawValue == null) return null;
  const trimmed = String(rawValue).trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : null;
}

function parseDraftText(rawValue) {
  if (rawValue == null) return null;
  const trimmed = String(rawValue).trim();
  return trimmed || null;
}

function clearLearningInlineReviewDraft() {
  state.learningDetail.draft = null;
  renderLearningDetail();
}

function attachLearningInlineReviewButtons(container) {
  if (!container) return;
  container.querySelectorAll(".learning-inline-review-button").forEach((button) => {
    button.addEventListener("click", () => {
      const kind = String(button.dataset.kind || "").trim();
      if (!kind) return;
      const baseDraft = {
        kind,
        summary: "",
        title: button.dataset.actionLabel || "Governance action",
        submitLabel: button.textContent?.trim() || "Apply",
        subtitle: "Esta nota queda persistida como resumen de la accion de gobernanza.",
      };
      if (kind === "claim_review") {
        const claimId = Number(button.dataset.claimId);
        const outcome = button.dataset.outcome;
        if (!Number.isFinite(claimId) || !outcome) return;
        openLearningInlineReviewDraft({
          ...baseDraft,
          claimId,
          outcome,
        });
        return;
      }
      if (kind === "skill_gap_review") {
        const gapId = Number(button.dataset.gapId);
        const outcome = button.dataset.outcome;
        if (!Number.isFinite(gapId) || !outcome) return;
        openLearningInlineReviewDraft({
          ...baseDraft,
          gapId,
          outcome,
        });
        return;
      }
      if (kind === "workflow_action") {
        const workflowId = Number(button.dataset.workflowId);
        const entityId = Number(button.dataset.entityId);
        const itemType = button.dataset.itemType;
        const action = button.dataset.action;
        if (!Number.isFinite(workflowId) || !Number.isFinite(entityId) || !itemType || !action) return;
        openLearningInlineReviewDraft({
          ...baseDraft,
          workflowId,
          entityId,
          itemType,
          action,
        });
        return;
      }
      if (kind === "distillation_review") {
        const digestId = Number(button.dataset.digestId);
        const action = button.dataset.action;
        if (!Number.isFinite(digestId) || !action) return;
        openLearningInlineReviewDraft({
          ...baseDraft,
          digestId,
          action,
        });
        return;
      }
      if (kind === "skill_candidate_validation") {
        const candidateId = Number(button.dataset.candidateId);
        const validationMode = button.dataset.validationMode;
        const validationOutcome = button.dataset.validationOutcome;
        if (!Number.isFinite(candidateId) || !validationMode || !validationOutcome) return;
        const candidateMeta = state.learningDetail.entityType === "skill_candidate" && state.learningDetail.entityId === candidateId
          ? asObject(asObject(asObject(state.learningDetail.payload).candidate).meta)
          : {};
        const lastEvidence = asObject(candidateMeta.last_validation_evidence);
        openLearningInlineReviewDraft({
          ...baseDraft,
          candidateId,
          validationMode,
          validationOutcome,
          sampleSize: parseDraftNumber(candidateMeta.last_validation_sample_size),
          winRate: parseDraftNumber(candidateMeta.last_validation_win_rate),
          avgPnlPct: parseDraftNumber(candidateMeta.last_validation_avg_pnl_pct),
          maxDrawdownPct: parseDraftNumber(candidateMeta.last_validation_max_drawdown_pct),
          runId: parseDraftText(lastEvidence.run_id),
          artifactUrl: parseDraftText(lastEvidence.artifact_url),
          evidenceNote: parseDraftText(lastEvidence.note),
          placeholder: "Resume por que el candidate merece paper, replay o rechazo.",
        });
      }
    });
  });
}

function attachLearningInlineReviewForms(container) {
  if (!container) return;
  container.querySelectorAll(".learning-inline-review-textarea").forEach((textarea) => {
    textarea.addEventListener("input", () => {
      if (!state.learningDetail.draft) return;
      state.learningDetail.draft = {
        ...state.learningDetail.draft,
        summary: textarea.value,
      };
    });
  });
  container.querySelectorAll('[data-role="learning-inline-number"]').forEach((input) => {
    input.addEventListener("input", () => {
      if (!state.learningDetail.draft) return;
      const field = input.dataset.field;
      if (!field) return;
      state.learningDetail.draft = {
        ...state.learningDetail.draft,
        [field]: parseDraftNumber(input.value),
      };
    });
  });
  container.querySelectorAll('[data-role="learning-inline-text"]').forEach((input) => {
    input.addEventListener("input", () => {
      if (!state.learningDetail.draft) return;
      const field = input.dataset.field;
      if (!field) return;
      state.learningDetail.draft = {
        ...state.learningDetail.draft,
        [field]: input.value,
      };
    });
  });
  container.querySelectorAll(".learning-inline-review-cancel").forEach((button) => {
    button.addEventListener("click", () => {
      clearLearningInlineReviewDraft();
    });
  });
  container.querySelectorAll(".learning-inline-review-form").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const draft = asObject(state.learningDetail.draft);
      const summary = String(draft.summary || "").trim();
      if (!summary) {
        setStatus("Escribe un resumen antes de aplicar la accion.");
        return;
      }
      const submitButton = form.querySelector('button[type="submit"]');
      if (submitButton) submitButton.disabled = true;
      try {
        let doneMessage = null;
        if (draft.kind === "claim_review") {
          doneMessage = await reviewClaim(draft.claimId, draft.outcome, { summary, refresh: false });
        } else if (draft.kind === "skill_gap_review") {
          doneMessage = await reviewSkillGap(draft.gapId, draft.outcome, { summary, refresh: false });
        } else if (draft.kind === "skill_candidate_validation") {
          doneMessage = await validateSkillCandidate(draft.candidateId, {
            validationMode: draft.validationMode,
            validationOutcome: draft.validationOutcome,
            summary,
            sampleSize: parseDraftNumber(draft.sampleSize),
            winRate: parseDraftNumber(draft.winRate),
            avgPnlPct: parseDraftNumber(draft.avgPnlPct),
            maxDrawdownPct: parseDraftNumber(draft.maxDrawdownPct),
            evidence: {
              run_id: parseDraftText(draft.runId),
              artifact_url: parseDraftText(draft.artifactUrl),
              note: parseDraftText(draft.evidenceNote),
            },
            refresh: false,
          });
        } else if (draft.kind === "distillation_review") {
          doneMessage = await reviewDistillationDigest(draft.digestId, draft.action, {
            summary,
            keepEntityId: parseDraftNumber(draft.keepEntityId),
            refresh: false,
          });
        } else if (draft.kind === "workflow_action") {
          doneMessage = await applyWorkflowAction({
            workflowId: draft.workflowId,
            entityId: draft.entityId,
            itemType: draft.itemType,
            action: draft.action,
            summary,
            refresh: false,
          });
        }
        if (!doneMessage) return;
        state.learningDetail.draft = null;
        await refreshAfterLearningMutation(doneMessage);
      } finally {
        if (submitButton) submitButton.disabled = false;
      }
    });
  });
}

function deriveDefaultTraceTicker(dashboards) {
  const positions = Array.isArray(dashboards?.positions) ? dashboards.positions : [];
  const openPosition = positions.find((item) => item.status === "open" && item.ticker);
  if (openPosition?.ticker) return String(openPosition.ticker).trim().toUpperCase();

  const journal = Array.isArray(dashboards?.journal) ? dashboards.journal : [];
  const recentJournal = journal.find((item) => item.ticker);
  if (recentJournal?.ticker) return String(recentJournal.ticker).trim().toUpperCase();

  const queueItems = Array.isArray(dashboards?.queue?.items) ? dashboards.queue.items : [];
  for (const item of queueItems) {
    const description = `${item.title || ""} ${item.summary || ""}`;
    const match = description.match(/\b[A-Z]{1,5}\b/);
    if (match) return match[0];
  }
  return "";
}

function tickerTraceStat(label, value, aside) {
  return `
    <div class="ticker-trace-stat">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
      <small>${escapeHtml(aside)}</small>
    </div>
  `;
}

function tickerTraceDecisionPillClass(value) {
  const normalized = String(value || "").toLowerCase();
  if (normalized.includes("enter") || normalized.includes("open") || normalized.includes("reviewed")) return "pill-active";
  if (normalized.includes("watch")) return "pill-candidate";
  if (normalized.includes("block") || normalized.includes("reject") || normalized.includes("discard") || normalized.includes("exit")) {
    return "pill-reject";
  }
  return "pill-info";
}

function buildTickerTraceMeta(event) {
  const details = asObject(event.details);
  const parts = [formatDate(event.timestamp)];
  if (Number.isFinite(details.quality_score)) {
    parts.push(`score ${Number(details.quality_score).toFixed(2)}`);
  }
  if (Number.isFinite(details.entry_price)) {
    parts.push(`entry ${formatPrice(details.entry_price)}`);
  }
  if (Number.isFinite(details.exit_price)) {
    parts.push(`exit ${formatPrice(details.exit_price)}`);
  }
  if (Number.isFinite(details.pnl_pct)) {
    parts.push(`PnL ${formatSignedPct(details.pnl_pct)}`);
  }

  const guardResults = asObject(details.guard_results);
  const guardReasons = Array.isArray(guardResults.reasons) ? guardResults.reasons : [];
  if (guardResults.blocked && guardReasons[0]) {
    parts.push(`guard ${guardReasons[0]}`);
  }

  const decisionTrace = asObject(details.decision_trace);
  if (decisionTrace.final_reason) {
    parts.push(decisionTrace.final_reason);
  }
  const skillContext = asObject(details.skill_context);
  if (typeof skillContext.primary_skill_code === "string" && skillContext.primary_skill_code) {
    parts.push(`skill ${skillContext.primary_skill_code}`);
  }
  if (Array.isArray(skillContext.active_revisions) && skillContext.active_revisions[0]?.skill_code) {
    parts.push(`rev ${skillContext.active_revisions[0].skill_code}`);
  }
  const budgetSummary = summarizeContextBudget(details.context_budget);
  if (budgetSummary?.detail) {
    parts.push(`budget ${budgetSummary.detail}`);
  }
  const skillCandidate = asObject(details.skill_candidate);
  if (typeof skillCandidate.target_skill_code === "string" && skillCandidate.target_skill_code) {
    parts.push(`candidate ${skillCandidate.target_skill_code}`);
  }
  const workflow = asObject(details.workflow);
  if (workflow.workflow_type) {
    parts.push(`workflow ${workflow.workflow_type}`);
  }
  if (workflow.workflow_id) {
    parts.push(`workflow_id ${workflow.workflow_id}`);
  }
  if (workflow.resolution_class) {
    parts.push(`resolution ${workflow.resolution_class}`);
  }
  const operatorDisagreement = asObject(details.operator_disagreement);
  if (operatorDisagreement.disagreement_type) {
    parts.push(`disagreement ${operatorDisagreement.disagreement_type}`);
  }
  const timingProfile = asObject(details.timing_profile);
  if (Number.isFinite(timingProfile.total_ms)) {
    parts.push(`total ${Math.round(Number(timingProfile.total_ms))} ms`);
  } else if (Number.isFinite(timingProfile.total_ms_before_journal)) {
    parts.push(`total ${Math.round(Number(timingProfile.total_ms_before_journal))} ms`);
  }
  if (typeof timingProfile.slowest_stage === "string" && timingProfile.slowest_stage) {
    const slowestMs = Number.isFinite(timingProfile.slowest_stage_ms) ? `${Math.round(Number(timingProfile.slowest_stage_ms))} ms` : "n/a";
    parts.push(`cuello ${humanizeLabel(timingProfile.slowest_stage)} ${slowestMs}`);
  }
  const executionPlanTiming = asObject(details.execution_plan_timing);
  if (typeof executionPlanTiming.slowest_tool === "string" && executionPlanTiming.slowest_tool) {
    const slowestToolMs = Number.isFinite(executionPlanTiming.slowest_tool_ms)
      ? `${Math.round(Number(executionPlanTiming.slowest_tool_ms))} ms`
      : "n/a";
    parts.push(`tool ${executionPlanTiming.slowest_tool} ${slowestToolMs}`);
  }
  if (event.tags && event.tags.length) {
    parts.push(`tags ${event.tags.join(", ")}`);
  }
  return parts.join(" · ");
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

async function fetchCorporateEventContext(ticker) {
  try {
    return await request(`/calendar/corporate-context/${encodeURIComponent(ticker)}?days_ahead=45`);
  } catch (error) {
    return {
      ticker,
      source: "error",
      used_fallback: false,
      provider_error: error instanceof Error ? error.message : String(error),
      fallback_reason: null,
      events: [],
      cache: null,
    };
  }
}

function deriveCorporateContextTickers({ journal, marketStateSnapshots, positions }) {
  const tickers = [];
  const seen = new Set();
  const pushTicker = (value) => {
    if (typeof value !== "string") return;
    const cleaned = value.trim().toUpperCase();
    if (!cleaned || seen.has(cleaned)) return;
    seen.add(cleaned);
    tickers.push(cleaned);
  };

  positions
    .filter((position) => position.status === "open")
    .forEach((position) => pushTicker(position.ticker));

  const latestSnapshot = Array.isArray(marketStateSnapshots) ? marketStateSnapshots[0] : null;
  const latestPayload = asObject(latestSnapshot?.snapshot_payload);
  const protocolState = asObject(latestPayload.market_state_snapshot);
  const activeWatchlists = Array.isArray(protocolState.active_watchlists) ? protocolState.active_watchlists : [];
  activeWatchlists.forEach((watchlist) => {
    const watchlistTickers = Array.isArray(watchlist.tickers) ? watchlist.tickers : [];
    watchlistTickers.slice(0, 3).forEach(pushTicker);
  });

  (Array.isArray(journal) ? journal : []).forEach((entry) => pushTicker(entry.ticker));
  return tickers.slice(0, 6);
}

function renderCycleOutput() {
  elements.cycleOutput.textContent = state.lastCycleResponse
    ? JSON.stringify(state.lastCycleResponse, null, 2)
    : JSON.stringify(state.scheduler?.bot || { status: "idle" }, null, 2);
}

async function loadChatWorkspace({ preserveSelection = true } = {}) {
  const includeArchived = state.chat.showArchived ? "true" : "false";
  const [presets, conversations] = await Promise.all([
    request("/chat/presets"),
    request(`/chat/conversations?include_archived=${includeArchived}&limit=80`),
  ]);

  state.chat.presets = Array.isArray(presets) ? presets : [];
  state.chat.conversations = Array.isArray(conversations) ? conversations : [];

  const selectedId = preserveSelection ? state.chat.selectedConversationId : null;
  const stillExists = state.chat.conversations.some((item) => item.id === selectedId);
  if (!stillExists) {
    state.chat.selectedConversationId = state.chat.conversations[0]?.id || null;
  }

  renderChatPresets();
  renderConversationList();

  if (!state.chat.selectedConversationId && !state.chat.conversations.length) {
    await createChatConversation();
    return;
  }

  if (state.chat.selectedConversationId) {
    await loadConversationDetail(state.chat.selectedConversationId);
  } else {
    state.chat.selectedConversation = null;
    renderChatMeta();
    renderChatThread();
  }
}

async function createChatConversation() {
  const preset = elements.chatLlmSelect?.value || preferredChatPresetKey();
  const created = await request("/chat/conversations", {
    method: "POST",
    body: JSON.stringify({ preferred_llm: preset }),
  });
  state.chat.selectedConversationId = created.id;
  await loadChatWorkspace({ preserveSelection: true });
}

async function loadConversationDetail(conversationId) {
  if (!conversationId) {
    state.chat.selectedConversation = null;
    renderChatMeta();
    renderChatThread();
    return;
  }
  const detail = await request(`/chat/conversations/${conversationId}`);
  state.chat.selectedConversation = detail;
  state.chat.selectedConversationId = detail.id;
  renderConversationList();
  renderChatMeta();
  renderChatThread();
}

function renderChatPresets() {
  if (!elements.chatLlmSelect) return;
  const presets = Array.isArray(state.chat.presets) ? state.chat.presets : [];
  elements.chatLlmSelect.innerHTML = presets
    .map((preset) => {
      const status = preset.ready ? "" : " (not ready)";
      return `<option value="${escapeHtml(preset.key)}">${escapeHtml(preset.label + status)}</option>`;
    })
    .join("");
  const selectedPreset = state.chat.selectedConversation?.preferred_llm || preferredChatPresetKey();
  if (selectedPreset) {
    elements.chatLlmSelect.value = selectedPreset;
  }
}

function renderConversationList() {
  if (!elements.chatConversationList) return;
  const conversations = Array.isArray(state.chat.conversations) ? state.chat.conversations : [];
  if (!conversations.length) {
    elements.chatConversationList.innerHTML = `<div class="muted">Todavia no hay conversaciones persistidas.</div>`;
    return;
  }
  elements.chatConversationList.innerHTML = conversations
    .map((conversation) => {
      const activeClass = conversation.id === state.chat.selectedConversationId ? "chat-conversation-card-active" : "";
      const ticker = conversation.linked_ticker ? pill(conversation.linked_ticker, "pill-info") : "";
      const labels = Array.isArray(conversation.labels)
        ? conversation.labels.slice(0, 3).map((item) => pill(item, "pill-approved")).join("")
        : "";
      return `
        <article class="chat-conversation-card ${activeClass}" data-conversation-id="${conversation.id}">
          <div class="chat-conversation-top">
            <div>
              <div class="chat-conversation-title">${escapeHtml(conversation.title || "Conversacion")}</div>
              <small class="muted">${escapeHtml(formatDate(conversation.updated_at))}</small>
            </div>
            ${pill(conversation.status, conversation.status === "archived" ? "pill-degraded" : "pill-info")}
          </div>
          <p class="chat-conversation-summary">${escapeHtml(conversation.summary || "Sin resumen todavia.")}</p>
          <div class="chat-meta">
            ${ticker}
            ${labels}
            ${pill(conversation.preferred_llm || "n/a", "pill-active")}
          </div>
        </article>
      `;
    })
    .join("");

  elements.chatConversationList.querySelectorAll("[data-conversation-id]").forEach((node) => {
    node.addEventListener("click", async () => {
      const conversationId = Number(node.dataset.conversationId);
      if (!Number.isFinite(conversationId)) return;
      await loadConversationDetail(conversationId);
    });
  });
}

function renderChatThread() {
  if (!elements.chatThread) return;
  const messages = Array.isArray(state.chat.selectedConversation?.messages)
    ? state.chat.selectedConversation.messages
    : [];
  if (!messages.length) {
    elements.chatThread.innerHTML = `
      <div class="chat-thread-empty">
        <strong>Conversacion vacia</strong>
        <p>Abre una idea, pide una review de ticker o debate una tesis para que el bot pueda guardar memoria o abrir research.</p>
      </div>
    `;
    renderChatMeta();
    return;
  }

  elements.chatThread.innerHTML = "";
  messages.forEach((message) => {
    const context = asObject(message.context);
    const llmInfo =
      message.role === "assistant"
        ? [
            context.used_provider ? pill(context.used_provider, "pill-info") : "",
            context.used_model ? pill(context.used_model, "pill-active") : "",
            context.reasoning_effort ? pill(`RE=${context.reasoning_effort}`, "pill-candidate") : "",
            context.fallback_used ? pill("fallback", "pill-degraded") : "",
          ].join("")
        : "";
    const actions = Array.isArray(message.actions_taken)
      ? message.actions_taken.map((item) => pill(item.action, "pill-approved")).join("")
      : "";
    const tickerPills = Array.isArray(context.tickers)
      ? context.tickers.map((ticker) => pill(ticker, "pill-info")).join("")
      : "";
    const node = document.createElement("article");
    node.className = `chat-bubble ${message.role === "user" ? "chat-bubble-user" : "chat-bubble-assistant"}`;
    node.innerHTML = `
      <div class="chat-meta">
        <span>${message.role === "user" ? "Tu" : "Bot"}</span>
        ${message.message_type ? pill(message.message_type, message.role === "user" ? "pill-approved" : "pill-info") : ""}
        ${context.topic ? pill(context.topic, "pill-candidate") : ""}
        ${tickerPills}
      </div>
      <div class="chat-text">${escapeHtml(message.content)}</div>
      <div class="chat-message-footer">
        <small class="muted">${escapeHtml(formatDate(message.created_at))}</small>
        <div class="chat-llm-note">${llmInfo}</div>
      </div>
      <div class="chat-action-list">${actions}</div>
    `;
    elements.chatThread.appendChild(node);
  });
  elements.chatThread.scrollTop = elements.chatThread.scrollHeight;
  renderChatMeta();
}

async function sendChatMessage(message) {
  if (!state.chat.selectedConversationId) {
    await createChatConversation();
  }

  elements.chatSend.disabled = true;
  if (elements.chatStatusHint) {
    elements.chatStatusHint.textContent = "Enviando mensaje y persistiendo hilo...";
  }

  try {
    const payload = await request(`/chat/conversations/${state.chat.selectedConversationId}/messages`, {
      method: "POST",
      body: JSON.stringify({
        content: message,
        llm_preset: elements.chatLlmSelect?.value || undefined,
      }),
    });
    elements.chatInput.value = "";
    await loadChatWorkspace({ preserveSelection: true });
    renderSuggestedPrompts(suggestPromptsFromTurn(payload));
  } finally {
    elements.chatSend.disabled = false;
    renderChatMeta();
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

function renderChatMeta() {
  if (!elements.chatConversationMeta) return;
  const conversation = state.chat.selectedConversation;
  if (!conversation) {
    elements.chatConversationMeta.innerHTML = `
      <strong>Sin conversacion seleccionada</strong>
      <p>Crea un hilo nuevo para empezar a debatir ideas o revisar tickers.</p>
    `;
    if (elements.chatArchive) elements.chatArchive.disabled = true;
    if (elements.chatStatusHint) {
      elements.chatStatusHint.textContent = "Selecciona o crea una conversacion para empezar.";
    }
    return;
  }

  const preset = currentChatPreset();
  const labels = Array.isArray(conversation.labels)
    ? conversation.labels.slice(0, 4).map((item) => pill(item, "pill-approved")).join("")
    : "";
  const ticker = conversation.linked_ticker ? pill(conversation.linked_ticker, "pill-info") : "";
  const modelStatus = preset
    ? preset.ready
      ? pill("preset ready", "pill-active")
      : pill("preset not ready", "pill-degraded")
    : "";
  elements.chatConversationMeta.innerHTML = `
    <div>
      <strong>${escapeHtml(conversation.title || "Conversacion")}</strong>
      <p>${escapeHtml(conversation.summary || "El bot guarda aqui el resumen y la tesis dominante del hilo.")}</p>
    </div>
    <div class="chat-meta">
      ${ticker}
      ${labels}
      ${pill(conversation.preferred_llm || "n/a", "pill-info")}
      ${modelStatus}
    </div>
  `;
  if (elements.chatLlmSelect && conversation.preferred_llm) {
    elements.chatLlmSelect.value = conversation.preferred_llm;
  }
  if (elements.chatArchive) {
    elements.chatArchive.disabled = conversation.status === "archived";
  }
  if (elements.chatSend) {
    elements.chatSend.disabled = conversation.status === "archived";
  }
  if (elements.chatInput) {
    elements.chatInput.disabled = conversation.status === "archived";
  }
  if (elements.chatStatusHint) {
    const lastAssistant = [...(conversation.messages || [])].reverse().find((item) => item.role === "assistant");
    const lastContext = asObject(lastAssistant?.context);
    if (lastContext.fallback_used && lastContext.provider_error) {
      elements.chatStatusHint.textContent = `Fallback visible: ${lastContext.provider_error}`;
    } else if (preset && !preset.ready) {
      elements.chatStatusHint.textContent = `${preset.label} no esta listo; el hilo usara la capa local y lo dejara trazado.`;
    } else {
      elements.chatStatusHint.textContent = "El hilo guarda modelo, mensajes y acciones trazables.";
    }
  }
}

async function updateSelectedConversationPreset(presetKey) {
  if (!state.chat.selectedConversationId || !presetKey) return;
  await request(`/chat/conversations/${state.chat.selectedConversationId}`, {
    method: "PATCH",
    body: JSON.stringify({ preferred_llm: presetKey }),
  });
  await loadChatWorkspace({ preserveSelection: true });
}

async function archiveSelectedConversation() {
  if (!state.chat.selectedConversationId) return;
  await request(`/chat/conversations/${state.chat.selectedConversationId}/archive`, {
    method: "POST",
  });
  await loadChatWorkspace({ preserveSelection: false });
}

function preferredChatPresetKey() {
  const presets = Array.isArray(state.chat.presets) ? state.chat.presets : [];
  const readyPreset = presets.find((item) => item.ready);
  return readyPreset?.key || presets[0]?.key || "gemini-2.5-flash";
}

function currentChatPreset() {
  const key = state.chat.selectedConversation?.preferred_llm || elements.chatLlmSelect?.value;
  return (state.chat.presets || []).find((item) => item.key === key) || null;
}

function suggestPromptsFromTurn(turn) {
  const assistant = asObject(turn?.assistant_message);
  const context = asObject(assistant.context);
  const tickers = Array.isArray(context.tickers) ? context.tickers : [];
  if (context.topic === "ticker_review" && tickers[0]) {
    return [
      `Que invalidaria la tesis de ${tickers[0]}`,
      `Merece ${tickers[0]} research o solo watch`,
      `Que riesgos ves en ${tickers[0]}`,
    ];
  }
  if (context.topic === "investment_idea_discussion") {
    return [
      "Convierte esta idea en una tarea de research",
      "Que datos faltan para validar esta tesis",
      "No estoy de acuerdo con ese argumento",
    ];
  }
  return [
    "Dame un resumen general",
    "Que has descubierto hoy",
    "Que estas haciendo ahora",
  ];
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

function formatWorkQueueItemContext(item) {
  const context = asObject(item?.context);
  if (item?.item_type === "watchlist_reanalysis_due") {
    const parts = [];
    if (context.watchlist_code) parts.push(context.watchlist_code);
    if (context.gate_reason) parts.push(humanizeLabel(context.gate_reason));
    if (context.last_evaluated_at) {
      parts.push(`ultima revision ${formatDate(context.last_evaluated_at)}`);
    } else {
      parts.push("sin revision previa");
    }
    if (context.next_reanalysis_at) {
      parts.push(`due ${formatDate(context.next_reanalysis_at)}`);
    }
    const cadenceSeconds = Number(context.check_interval_seconds);
    if (Number.isFinite(cadenceSeconds) && cadenceSeconds > 0) {
      parts.push(`cadencia ${cadenceSeconds}s`);
    }
    if (context.market_session_label) {
      parts.push(`sesion ${humanizeLabel(context.market_session_label)}`);
    }
    return parts.join(" · ") || "Reanalisis pendiente.";
  }
  return formatContext(context);
}

function buildWorkQueueSummaryLines(summary = {}) {
  const lines = [];
  const backlogParts = [];
  const dueItems = Number(summary.due_reanalysis_items);
  const deferredItems = Number(summary.deferred_reanalysis_items);
  const runtimeAwareItems = Number(summary.runtime_aware_watchlist_items);
  const hasBacklogTelemetry =
    dueItems > 0 ||
    deferredItems > 0 ||
    runtimeAwareItems > 0 ||
    Boolean(summary.next_reanalysis_ticker && summary.next_reanalysis_at);
  if (hasBacklogTelemetry) {
    if (Number.isFinite(dueItems)) backlogParts.push(`due ${dueItems}`);
    if (Number.isFinite(deferredItems)) backlogParts.push(`deferred ${deferredItems}`);
    if (Number.isFinite(runtimeAwareItems) && runtimeAwareItems > 0) {
      backlogParts.push(`runtime-aware ${runtimeAwareItems}`);
    }
    if (summary.next_reanalysis_ticker && summary.next_reanalysis_at) {
      backlogParts.push(`siguiente ${summary.next_reanalysis_ticker} ${formatDate(summary.next_reanalysis_at)}`);
    }
    lines.push(backlogParts.join(" · "));
  }

  const timingSamples = Number(summary.timing_samples);
  if (Number.isFinite(timingSamples) && timingSamples > 0) {
    const timingParts = [`muestras ${timingSamples}`];
    if (Number.isFinite(Number(summary.avg_total_ms))) {
      timingParts.push(`total ${formatDurationMs(summary.avg_total_ms)}`);
    }
    if (Number.isFinite(Number(summary.avg_decision_context_ms))) {
      timingParts.push(`decision_context ${formatDurationMs(summary.avg_decision_context_ms)}`);
    }
    if (Number.isFinite(Number(summary.avg_reanalysis_gate_ms))) {
      timingParts.push(`reanalysis_gate ${formatDurationMs(summary.avg_reanalysis_gate_ms)}`);
    }
    if (summary.dominant_decision_context_stage) {
      const stageMs = Number(summary.dominant_decision_context_stage_avg_ms);
      timingParts.push(
        `cuello ${humanizeLabel(summary.dominant_decision_context_stage)}${Number.isFinite(stageMs) ? ` ${formatDurationMs(stageMs)}` : ""}`,
      );
    } else if (summary.dominant_stage) {
      const stageMs = Number(summary.dominant_stage_avg_ms);
      timingParts.push(
        `cuello ${humanizeLabel(summary.dominant_stage)}${Number.isFinite(stageMs) ? ` ${formatDurationMs(stageMs)}` : ""}`,
      );
    }
  if (summary.timing_last_signal_at) {
      timingParts.push(`ult. senal ${formatDate(summary.timing_last_signal_at)}`);
    }
    lines.push(timingParts.join(" · "));
  }

  const marketDataStatus = asObject(summary.market_data_provider_status);
  const marketDataGateParts = Object.entries(marketDataStatus)
    .filter(([, status]) => asObject(status).configured && Number(asObject(status).concurrency_limit) > 0)
    .map(([key, status]) => `${humanizeLabel(key)} x${Number(asObject(status).concurrency_limit)}`);
  if (marketDataGateParts.length) {
    lines.push(`market data gates ${marketDataGateParts.join(" · ")}`);
  }

  const marketDataCooldownParts = Object.entries(marketDataStatus)
    .filter(([, status]) => asObject(status).cooling_down)
    .map(([key, status]) => {
      const runtimeStatus = asObject(status);
      const remaining = Number(runtimeStatus.cooldown_remaining_seconds);
      const provider = runtimeStatus.provider ? humanizeLabel(runtimeStatus.provider) : "provider";
      return `${humanizeLabel(key)} ${provider} ${formatCooldownSeconds(remaining)}`;
    });
  if (marketDataCooldownParts.length) {
    lines.push(`market data cooldown ${marketDataCooldownParts.join(" · ")}`);
  }

  const calendarStatus = asObject(summary.calendar_provider_status);
  const gateParts = Object.entries(calendarStatus)
    .filter(([, status]) => asObject(status).configured && Number(asObject(status).concurrency_limit) > 0)
    .map(([key, status]) => `${humanizeLabel(key)} x${Number(asObject(status).concurrency_limit)}`);
  if (gateParts.length) {
    lines.push(`calendar gates ${gateParts.join(" · ")}`);
  }

  const cooldownParts = Object.entries(calendarStatus)
    .filter(([, status]) => asObject(status).cooling_down)
    .map(([key, status]) => {
      const runtimeStatus = asObject(status);
      const remaining = Number(runtimeStatus.cooldown_remaining_seconds);
      const provider = runtimeStatus.provider ? humanizeLabel(runtimeStatus.provider) : "provider";
      return `${humanizeLabel(key)} ${provider} ${formatCooldownSeconds(remaining)}`;
    });
  if (cooldownParts.length) {
    lines.push(`calendar cooldown ${cooldownParts.join(" · ")}`);
  }

  const newsStatus = asObject(summary.news_provider_status);
  const newsGateParts = Object.entries(newsStatus)
    .filter(([, status]) => asObject(status).configured && Number(asObject(status).concurrency_limit) > 0)
    .map(([key, status]) => `${humanizeLabel(key)} x${Number(asObject(status).concurrency_limit)}`);
  if (newsGateParts.length) {
    lines.push(`news gates ${newsGateParts.join(" · ")}`);
  }

  const newsCooldownParts = Object.entries(newsStatus)
    .filter(([, status]) => asObject(status).cooling_down)
    .map(([key, status]) => {
      const runtimeStatus = asObject(status);
      const remaining = Number(runtimeStatus.cooldown_remaining_seconds);
      const provider = runtimeStatus.provider ? humanizeLabel(runtimeStatus.provider) : "provider";
      return `${humanizeLabel(key)} ${provider} ${formatCooldownSeconds(remaining)}`;
    });
  if (newsCooldownParts.length) {
    lines.push(`news cooldown ${newsCooldownParts.join(" · ")}`);
  }
  return lines;
}

function formatJournalMeta(entry) {
  const observations = asObject(entry.observations);
  const parts = [];
  if (entry.position_id) parts.push(`position=${entry.position_id}`);
  if (observations.workflow_type) parts.push(`workflow=${observations.workflow_type}`);
  if (observations.workflow_id) parts.push(`workflow_id=${observations.workflow_id}`);
  if (observations.resolution_class) parts.push(`resolution=${observations.resolution_class}`);
  const budgetSummary = summarizeContextBudget(observations.context_budget);
  if (budgetSummary?.detail) parts.push(`budget=${budgetSummary.detail}`);
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

function formatAgeSeconds(value) {
  if (!Number.isFinite(value)) return "edad n/d";
  const seconds = Math.max(Number(value), 0);
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h`;
  return `${Math.round(seconds / 86400)}d`;
}

function describeCorporateSource(context) {
  if (context.source === "ibkr_proxy") return "IBKR proxy";
  if (context.source === "alpha_vantage") return "Alpha fallback";
  if (context.source === "error") return "Error";
  if (context.source === "none") return "Sin fuente";
  return context.source || "Sin fuente";
}

function describeEventSource(source) {
  if (source === "ibkr_proxy") return "IBKR proxy";
  if (source === "alpha_vantage") return "Alpha cache";
  return source || "fuente n/d";
}

function corporateSourceClass(context) {
  if (context.source === "ibkr_proxy") return "pill-active";
  if (context.source === "alpha_vantage") return "pill-degraded";
  if (context.source === "error") return "pill-reject";
  return "pill-approved";
}

function summarizeCorporateContext(context) {
  const events = Array.isArray(context.events) ? context.events : [];
  const source = describeCorporateSource(context).toLowerCase();
  const cache = asObject(context.cache);
  if (context.provider_error && context.used_fallback) {
    return `Usando ${source} tras fallo del origen principal: ${context.provider_error}`;
  }
  if (context.provider_error && !events.length) {
    return context.provider_error;
  }
  if (context.source === "alpha_vantage" && cache.available) {
    return `Fallback en caché ${cache.stale ? "stale" : "vigente"} · sync ${formatDate(cache.cached_at)} · ttl ${cache.ttl_seconds || 0}s`;
  }
  if (context.source === "ibkr_proxy" && context.fallback_reason === "no_events_in_window") {
    return "El proxy no devolvio eventos proximos y no hubo respaldo util en la ventana.";
  }
  if (!events.length) {
    return "No hay eventos corporativos proximos para este ticker en la ventana actual.";
  }
  return `Fuente activa ${source} · ${events.length} evento${events.length === 1 ? "" : "s"} visibles`;
}

function describeMacroCalendarSource(source) {
  if (source === "fred") return "FRED";
  if (source === "federal_reserve") return "Federal Reserve";
  if (source === "ecb") return "ECB";
  if (source === "bea") return "BEA";
  if (source === "finnhub") return "Finnhub";
  return source || "fuente n/d";
}

function summarizeMacroCalendarEvent(event) {
  const parts = [];
  parts.push(describeMacroCalendarSource(event?.source));
  if (event?.impact) {
    parts.push(`${event.impact} impact`);
  }
  if (event?.actual) {
    parts.push(`actual ${event.actual}`);
  }
  if (event?.previous) {
    parts.push(`prev ${event.previous}`);
  }
  if (event?.estimate) {
    parts.push(`est ${event.estimate}`);
  }
  return parts.join(" · ");
}

function describeMacroAnalysisMode(value) {
  if (value === "ai") return "LLM";
  if (value === "heuristic_fallback") return "Fallback";
  if (value === "heuristic") return "Heuristico";
  return humanizeLabel(value || "sin modo");
}

function summarizeMacroSignalMeta({ tickers, timeframe, source, provider, strategyIdeas, createdAt }) {
  const parts = [];
  if (Array.isArray(tickers) && tickers.length) {
    parts.push(`activos ${tickers.slice(0, 4).join(", ")}`);
  }
  if (timeframe) {
    parts.push(`horizonte ${timeframe}`);
  }
  if (Array.isArray(strategyIdeas) && strategyIdeas.length) {
    parts.push(`${strategyIdeas.length} ideas`);
  }
  if (provider) {
    parts.push(provider);
  } else if (source) {
    parts.push(source);
  }
  if (createdAt) {
    parts.push(formatDate(createdAt));
  }
  return parts.join(" · ") || "Sin metadatos";
}

function summarizeMacroWatchlistMeta({ regime, tickers, strategyIdeas, createdAt }) {
  const parts = [];
  if (regime) {
    parts.push(`regimen ${humanizeLabel(regime)}`);
  }
  if (Array.isArray(tickers) && tickers.length) {
    parts.push(`tickers ${tickers.slice(0, 5).join(", ")}`);
  }
  if (Array.isArray(strategyIdeas) && strategyIdeas.length) {
    parts.push(`${strategyIdeas.length} ideas de explotacion`);
  }
  if (createdAt) {
    parts.push(formatDate(createdAt));
  }
  return parts.join(" · ") || "Sin detalle";
}

function formatNumber(value) {
  return Number.isFinite(value) ? Number(value).toFixed(2) : "n/a";
}

function formatDurationMs(value) {
  if (!Number.isFinite(Number(value))) return "n/a";
  const ms = Number(value);
  if (ms >= 1000) {
    return `${(ms / 1000).toFixed(ms >= 10_000 ? 1 : 2)} s`;
  }
  return `${Math.round(ms)} ms`;
}

function formatCooldownSeconds(value) {
  if (!Number.isFinite(Number(value))) return "n/a";
  const seconds = Math.max(Number(value), 0);
  if (seconds >= 60) {
    return `${(seconds / 60).toFixed(1)}m`;
  }
  return `${seconds.toFixed(seconds >= 10 ? 0 : 1)}s`;
}

function formatPct(value) {
  return Number.isFinite(value) ? `${Number(value).toFixed(1)}%` : "n/a";
}

function formatNullablePct(value) {
  return Number.isFinite(Number(value)) ? `${Number(value).toFixed(1)}%` : "n/a";
}

function formatConfidencePct(value) {
  return Number.isFinite(value) ? `${Math.round(Number(value) * 100)}%` : "n/a";
}

function formatPrice(value) {
  return Number.isFinite(value) ? Number(value).toFixed(2) : "n/a";
}

function formatSignedPct(value) {
  if (!Number.isFinite(value)) return "n/a";
  const pct = Number(value);
  return `${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%`;
}

function formatSignedNumber(value, digits = 1) {
  if (!Number.isFinite(Number(value))) return "n/a";
  const numeric = Number(value);
  return `${numeric >= 0 ? "+" : ""}${numeric.toFixed(digits)}`;
}

function formatSignedPctFromDecimal(value) {
  if (!Number.isFinite(value)) return "n/a";
  const pct = Number(value) * 100;
  return `${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%`;
}

function formatMacroIndicatorValue(indicator) {
  const value = Number(indicator?.value);
  if (!Number.isFinite(value)) {
    return indicator?.status === "unavailable" ? "n/d" : "n/a";
  }
  const unit = String(indicator?.unit || "").trim().toLowerCase();
  if (unit === "usd") return `$${value.toFixed(value >= 100 ? 2 : 4)}`;
  if (unit === "%") return `${value.toFixed(2)}%`;
  if (unit === "score") return `${Math.round(value)}`;
  if (unit === "pts" || unit === "points") return value.toFixed(2);
  return `${value.toFixed(2)} ${indicator?.unit || ""}`.trim();
}

function summarizeMacroIndicator(indicator) {
  const parts = [];
  if (indicator?.interpretation) {
    parts.push(humanizeLabel(indicator.interpretation));
  }
  if (Number.isFinite(indicator?.change)) {
    const change = Number(indicator.change);
    const unit = String(indicator?.unit || "").trim();
    if (unit === "%") {
      parts.push(`${change >= 0 ? "+" : ""}${change.toFixed(2)} pts vs prev`);
    } else if (unit.toLowerCase() === "usd") {
      parts.push(`${change >= 0 ? "+" : ""}$${Math.abs(change).toFixed(Math.abs(change) >= 100 ? 2 : 4)} vs prev`);
    } else {
      parts.push(`${change >= 0 ? "+" : ""}${change.toFixed(2)}${unit && unit !== "score" ? ` ${unit}` : ""} vs prev`);
    }
  }
  if (Number.isFinite(indicator?.change_pct)) {
    parts.push(formatSignedPct(indicator.change_pct));
  }
  if (indicator?.as_of) {
    parts.push(`as of ${formatDate(indicator.as_of)}`);
  }
  if (indicator?.detail) {
    parts.push(indicator.detail);
  }
  if (!parts.length && indicator?.status === "unavailable") {
    parts.push("No disponible");
  }
  return parts.join(" · ");
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
  const priceAction = context.price_action_context || {};
  const mstr =
    context.mstr_context ||
    context.decision_context?.mstr_context ||
    {};
  const expiry =
    context.expiry_context ||
    context.decision_context?.calendar_context?.expiry_context ||
    {};
  const skillContext =
    context.skill_context ||
    context.decision_context?.skill_context ||
    {};
  const details = [];

  if (context.source) details.push(`source=${context.source}`);
  if (Number.isFinite(context.risk_reward)) details.push(`R/R=${Number(context.risk_reward).toFixed(2)}`);
  if (Number.isFinite(quant.relative_volume)) details.push(`relVol=${Number(quant.relative_volume).toFixed(2)}`);
  if (Number.isFinite(quant.rsi_14)) details.push(`RSI=${Number(quant.rsi_14).toFixed(1)}`);
  if (Number.isFinite(quant.month_performance)) details.push(`month=${(Number(quant.month_performance) * 100).toFixed(1)}%`);
  if (priceAction.primary_signal_code) details.push(`PA=${priceAction.primary_signal_code}`);
  if (typeof skillContext.primary_skill_code === "string" && skillContext.primary_skill_code) {
    details.push(`SK=${skillContext.primary_skill_code}`);
  }
  if (Array.isArray(skillContext.active_revisions) && skillContext.active_revisions[0]?.skill_code) {
    details.push(`SKrev=${skillContext.active_revisions[0].skill_code}`);
  }
  if (Number.isFinite(priceAction.signal_count) && Number(priceAction.signal_count) > 0) {
    details.push(`PAx${Number(priceAction.signal_count)}`);
  }
  if (typeof priceAction.higher_timeframe_bias === "string" && priceAction.higher_timeframe_bias) {
    details.push(`HTF=${priceAction.higher_timeframe_bias}`);
  }
  if (
    typeof priceAction.follow_through_state === "string" &&
    priceAction.follow_through_state &&
    priceAction.follow_through_state !== "none"
  ) {
    details.push(`FT=${priceAction.follow_through_state}`);
  }
  if (Number.isFinite(mstr?.current_mnav)) {
    details.push(`mNAV=${Number(mstr.current_mnav).toFixed(2)}x`);
  }
  if (typeof mstr?.atm_risk_context === "string" && mstr.atm_risk_context && mstr.atm_risk_context !== "unavailable") {
    details.push(`ATM=${mstr.atm_risk_context}`);
  }
  if (typeof mstr?.bps_trend === "string" && mstr.bps_trend && mstr.bps_trend !== "unknown") {
    details.push(`BPS=${mstr.bps_trend}`);
  }
  if (mstr?.recent_btc_purchase === true) {
    details.push("BTCbuy=recent");
  }
  if (expiry && typeof expiry === "object" && expiry.phase && expiry.phase !== "normal") {
    details.push(`Expiry=${expiry.phase}`);
  } else if (expiry && typeof expiry === "object" && expiry.expiration_week) {
    details.push("Expiry=week");
  }
  if (Number.isInteger(expiry?.days_to_event)) {
    const days = Number(expiry.days_to_event);
    if (days >= 0) {
      details.push(`ExpD=${days}`);
    }
  }

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
