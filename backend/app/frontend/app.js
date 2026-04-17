const API_PREFIX = "/api/v1";

const elements = {
  activityFeed: document.getElementById("activity-feed"),
  candidateValidations: document.getElementById("candidate-validations"),
  cycleOutput: document.getElementById("cycle-output"),
  focusBoard: document.getElementById("focus-board"),
  journalFeed: document.getElementById("journal-feed"),
  metricsGrid: document.getElementById("metrics-grid"),
  metricTemplate: document.getElementById("metric-card-template"),
  nextFocus: document.getElementById("next-focus"),
  openPositions: document.getElementById("open-positions"),
  pipelineDetail: document.getElementById("pipeline-detail"),
  pipelinesList: document.getElementById("pipelines-list"),
  researchTasks: document.getElementById("research-tasks"),
  statusBadge: document.getElementById("status-badge"),
  workQueue: document.getElementById("work-queue"),
};

const state = {
  dashboards: null,
  lastCycleResponse: null,
  selectedStrategyId: null,
};

document.querySelectorAll("[data-action]").forEach((button) => {
  button.addEventListener("click", async () => {
    button.disabled = true;
    setStatus(`Ejecutando ${button.dataset.action.toUpperCase()}...`);
    try {
      await handleAction(button.dataset.action);
    } catch (error) {
      setError(error);
    } finally {
      button.disabled = false;
    }
  });
});

boot();

async function boot() {
  setStatus("Cargando consola...");
  try {
    await refreshDashboard();
    setStatus("Sincronizado");
  } catch (error) {
    setError(error);
  }
}

async function handleAction(action) {
  if (action === "refresh") {
    await refreshDashboard();
    setStatus("Sincronizado");
    return;
  }

  if (action === "seed") {
    state.lastCycleResponse = await request("/bootstrap/seed", { method: "POST" });
  }

  if (action === "recalculate") {
    state.lastCycleResponse = await request("/strategy-health/recalculate", { method: "POST" });
  }

  if (action === "plan") {
    state.lastCycleResponse = await request("/orchestrator/plan", {
      method: "POST",
      body: JSON.stringify({
        cycle_date: new Date().toISOString().slice(0, 10),
        market_context: { source: "frontend-console" },
      }),
    });
  }

  if (action === "do") {
    state.lastCycleResponse = await request("/orchestrator/do", { method: "POST" });
  }

  if (action === "check") {
    state.lastCycleResponse = await request("/orchestrator/check", { method: "POST" });
  }

  if (action === "act") {
    state.lastCycleResponse = await request("/orchestrator/act", { method: "POST" });
  }

  renderCycleOutput();
  await refreshDashboard();
  setStatus(`Ultima accion: ${action.toUpperCase()}`);
}

async function refreshDashboard() {
  const [
    health,
    pipelines,
    queue,
    researchTasks,
    candidateValidations,
    changes,
    activations,
    journal,
    positions,
  ] = await Promise.all([
    request("/strategy-health"),
    request("/strategy-health/pipelines"),
    request("/work-queue"),
    request("/research/tasks"),
    request("/strategy-evolution/candidate-validations"),
    request("/strategy-evolution/changes"),
    request("/strategy-evolution/activations"),
    request("/journal"),
    request("/positions"),
  ]);

  state.dashboards = {
    activations,
    candidateValidations,
    changes,
    health,
    journal,
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
  const { health, pipelines, queue, researchTasks, candidateValidations, changes, activations, journal, positions } = state.dashboards;
  const openResearch = researchTasks.filter((task) => task.status !== "completed");
  const topItem = queue.items[0] || null;
  const activeStrategies = pipelines.filter((item) => item.active_version).length;
  const degradedStrategies = pipelines.filter((item) => item.strategy_status === "degraded").length;
  const candidateVersions = pipelines.reduce((acc, item) => acc + item.candidate_versions.length, 0);
  const promotedCandidates = candidateValidations.filter((item) => item.evaluation_status === "promote").length;
  const rejectedCandidates = candidateValidations.filter((item) => item.evaluation_status === "reject").length;
  const validationTrades = candidateValidations.reduce((acc, item) => acc + item.trade_count, 0);

  elements.nextFocus.textContent = topItem ? topItem.title : "Sin backlog prioritario";

  renderMetrics([
    ["Estrategias activas", activeStrategies, `${pipelines.length} pipelines visibles`],
    ["Estrategias degradadas", degradedStrategies, "Piden validacion o rediseno"],
    ["Versiones candidatas", candidateVersions, "Pendientes de validar o promover"],
    ["Research abierto", openResearch.length, "Incluye recovery research"],
    ["Backlog total", queue.total_items, topItem ? `Primero: ${topItem.priority}` : "Sin cola activa"],
    ["Promociones validadas", promotedCandidates, "Candidatas que ya demostraron edge"],
    ["Candidatas rechazadas", rejectedCandidates, "Miden lineas de recuperacion fallidas"],
    ["Trades de validacion", validationTrades, `${average(health.map((item) => item.fitness_score)).toFixed(2)} fitness medio`],
  ]);

  renderFocusBoard(queue, pipelines, openResearch);
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

function renderFocusBoard(queue, pipelines, openResearch) {
  const topItem = queue.items[0];
  const degradedWithCandidates = pipelines.filter(
    (item) => item.strategy_status === "degraded" && item.candidate_versions.length > 0,
  );

  const cards = [
    topItem
      ? {
          kicker: "Proximo paso",
          title: topItem.title,
          pills: [pill(topItem.priority, priorityClass(topItem.priority)), pill(topItem.item_type, "pill-info")],
          body: formatContext(topItem.context),
        }
      : {
          kicker: "Proximo paso",
          title: "No hay trabajo urgente",
          pills: [pill("IDLE", "pill-approved")],
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
      pills: [pill("Backlog", "pill-approved")],
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

function renderStackList(container, items, renderer, emptyMessage) {
  container.innerHTML = "";
  if (!items.length) {
    container.innerHTML = `<div class="stack-item"><p class="muted">${emptyMessage}</p></div>`;
    return;
  }

  items.forEach((item) => {
    const node = document.createElement("article");
    node.className = "stack-item";
    node.innerHTML = renderer(item);
    container.appendChild(node);
  });
}

function renderCycleOutput() {
  elements.cycleOutput.textContent = state.lastCycleResponse
    ? JSON.stringify(state.lastCycleResponse, null, 2)
    : "Todavia no se ha ejecutado ninguna fase manual.";
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
  setStatus("Error");
  state.lastCycleResponse = { error: message };
  renderCycleOutput();
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
  return Number.isFinite(value) ? `${(Number(value) * 100).toFixed(1)}%` : "n/a";
}

function formatPrice(value) {
  return Number.isFinite(value) ? Number(value).toFixed(2) : "n/a";
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
