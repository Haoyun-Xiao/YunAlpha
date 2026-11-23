const colors = {
  portfolio: "#c94a4a",
  benchmark: "#2f6f9f",
  drawdown: "#c94a4a",
  excess: "#7a8795",
  excessFill: "rgba(122, 135, 149, 0.26)",
};

const fundPalette = ["#2f6f9f", "#c94a4a", "#357a5b", "#b78318", "#7758a6", "#4f7896", "#d06f3c", "#5c6bc0", "#00897b", "#8d6e63"];
const sleevePalette = {
  defensive: "#357a5b",
  balanced: "#2f6f9f",
  growth: "#c94a4a",
};
const sleeveDefs = [
  { id: "defensive", name: "防守压仓" },
  { id: "balanced", name: "核心稳健" },
  { id: "growth", name: "进攻弹性" },
];
const industryCategoryMap = {
  家用电器: "消费",
  医药生物: "消费",
  食品饮料: "消费",
  社会服务: "消费",
  纺织服饰: "消费",
  商贸零售: "消费",
  农林牧渔: "消费",
  轻工制造: "消费",
  美容护理: "消费",
  电子: "TMT",
  计算机: "TMT",
  传媒: "TMT",
  通信: "TMT",
  汽车: "制造",
  电力设备: "制造",
  机械设备: "制造",
  环保: "制造",
  国防军工: "制造",
  综合: "制造",
  基础化工: "周期",
  石油石化: "周期",
  建筑装饰: "周期",
  建筑材料: "周期",
  交通运输: "周期",
  有色金属: "周期",
  钢铁: "周期",
  公用事业: "周期",
  煤炭: "周期",
  房地产: "金融",
  非银金融: "金融",
  银行: "金融",
};
const clusterDefs = [
  { id: "defensive_cluster", name: "防守压仓", labels: ["防守压仓"] },
  { id: "stable_cluster", name: "核心稳健", labels: ["核心稳健"] },
  { id: "growth_cluster", name: "进攻弹性", labels: ["进攻弹性", "进攻增强"] },
  { id: "low_efficiency_cluster", name: "低效鸡肋 / 未分组", labels: ["低效鸡肋", "未分组", ""] },
];
const weightTemplateRatios = {
  template_defensive: { defensive: 50, balanced: 40, growth: 10 },
  template_stable: { defensive: 30, balanced: 40, growth: 30 },
  template_aggressive: { defensive: 10, balanced: 30, growth: 60 },
};
const defaultFeeConfig = { purchase: 0, redemption: 0, transaction: 0 };
const dataFrequencyLabels = {
  daily: "日频",
  weekly: "周频",
  monthly: "月频",
};
const winRateLabels = {
  daily: "日胜率",
  weekly: "周胜率",
  monthly: "月胜率",
};
const factorLabels = {
  BETA: "BETA",
  RSTR: "动量",
  LNCAP: "市值",
  EARNYILD: "盈利",
  GROWTH: "成长",
  LEVERAGE: "杠杆",
  LIQUIDITY: "流动性",
  RESVOL: "残差波动",
  BTOP: "账面市值比",
  NLSIZE: "非线性市值",
};

const state = {
  funds: [],
  benchmarks: [],
  tradingCalendar: [],
  fundsById: new Map(),
  sleeves: sleeveDefs.map((sleeve) => ({ ...sleeve, weight: 0, internalMode: "equal", funds: [] })),
  activeSleeveId: "defensive",
  result: null,
  currentBacktestPayload: null,
  view: "backtest",
  portfolios: [],
  currentPortfolio: null,
  currentPortfolioId: null,
  portfolioSelectionToken: 0,
  portfolioDeleteMode: false,
  pendingPortfolioDeleteId: null,
  draggingPortfolioId: null,
  portfolioDragInsertAfter: false,
  modalPortfolio: null,
  activeRebalanceIndex: 0,
  draggingHoldingIndex: null,
  isPortfolioDirty: false,
  collapsedClusters: new Set(),
  modalHoldingCollapsedSleeves: new Set(),
  latestHoldingCollapsedSleeves: new Set(),
  resultScopes: {
    annualReturn: "portfolio",
    periodReturn: "portfolio",
    annualContributionTable: "funds",
  },
  performanceWindow: {
    start: 0,
    end: 0,
  },
  performanceRangePreset: "all",
  performanceChartMode: "return",
  analysisWindow: {
    start: 0,
    end: 0,
  },
  analysisRangePreset: "all",
  analysisAttribution: null,
  categoryTimelineFocus: null,
  resultView: "home",
  libraryCollapsed: false,
  expandedAttributionSleeves: new Set(),
  selectedAttributionFund: null,
  attributionContributionScope: "portfolio",
  expandedExposureSleeves: new Set(),
  selectedExposureFund: null,
  selectedExposureBreakdowns: {},
  selectedExposureScopes: {},
};

const els = {
  shell: document.querySelector(".shell"),
  portfolioCount: document.getElementById("portfolioCount"),
  toggleLibraryBtn: document.getElementById("toggleLibraryBtn"),
  portfolioSearchInput: document.getElementById("portfolioSearchInput"),
  portfolioList: document.getElementById("portfolioList"),
  portfolioHomeView: document.getElementById("portfolioHomeView"),
  portfolioDirtyNotice: document.getElementById("portfolioDirtyNotice"),
  portfolioOverview: document.getElementById("portfolioOverview"),
  latestHoldingTable: document.getElementById("latestHoldingTable"),
  transactionTable: document.getElementById("transactionTable"),
  assetCount: document.getElementById("assetCount"),
  assetList: document.getElementById("assetList"),
  modalAssetList: document.getElementById("modalAssetList"),
  searchInput: document.getElementById("searchInput"),
  libraryPanel: document.querySelector(".library-panel"),
  portfolioPanel: document.querySelector(".portfolio-panel"),
  resultTabs: document.getElementById("resultTabs"),
  newPortfolioBtn: document.getElementById("newPortfolioBtn"),
  adjustPortfolioBtn: document.getElementById("adjustPortfolioBtn"),
  portfolioBulkToggleBtn: document.getElementById("portfolioBulkToggleBtn"),
  portfolioDeleteConfirmModal: document.getElementById("portfolioDeleteConfirmModal"),
  portfolioDeleteConfirmText: document.getElementById("portfolioDeleteConfirmText"),
  closePortfolioDeleteConfirmBtn: document.getElementById("closePortfolioDeleteConfirmBtn"),
  cancelPortfolioDeleteBtn: document.getElementById("cancelPortfolioDeleteBtn"),
  confirmPortfolioDeleteBtn: document.getElementById("confirmPortfolioDeleteBtn"),
  frequencyButtons: document.querySelectorAll("[data-frequency]"),
  equalWeightBtn: document.getElementById("equalWeightBtn"),
  clearBtn: document.getElementById("clearBtn"),
  runBtn: document.getElementById("runBtn"),
  weightMode: document.getElementById("weightMode"),
  benchmarkSelect: document.getElementById("benchmarkSelect"),
  benchmarkPickerBtn: document.getElementById("benchmarkPickerBtn"),
  benchmarkPickerName: document.getElementById("benchmarkPickerName"),
  benchmarkPickerMeta: document.getElementById("benchmarkPickerMeta"),
  benchmarkModal: document.getElementById("benchmarkModal"),
  benchmarkSearchInput: document.getElementById("benchmarkSearchInput"),
  benchmarkTypeFilter: document.getElementById("benchmarkTypeFilter"),
  benchmarkList: document.getElementById("benchmarkList"),
  closeBenchmarkModalBtn: document.getElementById("closeBenchmarkModalBtn"),
  startDate: document.getElementById("startDate"),
  endDate: document.getElementById("endDate"),
  sleeveGrid: document.getElementById("sleeveGrid"),
  weightSummary: document.getElementById("weightSummary"),
  status: document.getElementById("status"),
  results: document.getElementById("results"),
  metrics: document.getElementById("metrics"),
  performanceModeSelect: document.getElementById("performanceModeSelect"),
  returnChartLegend: document.getElementById("returnChartLegend"),
  drawdownChartLegend: document.getElementById("drawdownChartLegend"),
  performanceRangeStart: document.getElementById("performanceRangeStart"),
  performanceRangeEnd: document.getElementById("performanceRangeEnd"),
  performanceRangeTrack: document.getElementById("performanceRangeTrack"),
  performanceRangePresets: document.getElementById("performanceRangePresets"),
  performanceRangeStartDate: document.getElementById("performanceRangeStartDate"),
  performanceRangeEndDate: document.getElementById("performanceRangeEndDate"),
  performanceRangeStartLabel: document.getElementById("performanceRangeStartLabel"),
  performanceRangeEndLabel: document.getElementById("performanceRangeEndLabel"),
  windowText: document.getElementById("windowText"),
  annualContributionTable: document.getElementById("annualContributionTable"),
  warningBox: document.getElementById("warningBox"),
  annualReturnScope: document.getElementById("annualReturnScope"),
  periodReturnScope: document.getElementById("periodReturnScope"),
  annualContributionTableScope: document.getElementById("annualContributionTableScope"),
  attributionView: document.getElementById("attributionView"),
  attributionWindowText: document.getElementById("attributionWindowText"),
  analysisWindowStartLabel: document.getElementById("analysisWindowStartLabel"),
  analysisWindowEndLabel: document.getElementById("analysisWindowEndLabel"),
  analysisWindowStatus: document.getElementById("analysisWindowStatus"),
  analysisWindowPresets: document.getElementById("analysisWindowPresets"),
  analysisRangeStart: document.getElementById("analysisRangeStart"),
  analysisRangeEnd: document.getElementById("analysisRangeEnd"),
  analysisRangeTrack: document.getElementById("analysisRangeTrack"),
  attributionRangeText: document.getElementById("attributionRangeText"),
  attributionRows: document.getElementById("attributionRows"),
  sleeveCorrelationMatrix: document.getElementById("sleeveCorrelationMatrix"),
  categoryTimelineLegend: document.getElementById("categoryTimelineLegend"),
  categoryTimelineChart: document.getElementById("categoryTimelineChart"),
  sleeveFundCorrelationList: document.getElementById("sleeveFundCorrelationList"),
  fundCorrelationSummary: document.getElementById("fundCorrelationSummary"),
  fundAttributionDrawer: document.getElementById("fundAttributionDrawer"),
  closeFundDrawerBtn: document.getElementById("closeFundDrawerBtn"),
  fundDrawerTitle: document.getElementById("fundDrawerTitle"),
  fundDrawerCode: document.getElementById("fundDrawerCode"),
  fundDrawerBody: document.getElementById("fundDrawerBody"),
  exposureView: document.getElementById("exposureView"),
  exposureWindowText: document.getElementById("exposureWindowText"),
  exposureDatePill: document.getElementById("exposureDatePill"),
  exposureBreakdownSections: document.getElementById("exposureBreakdownSections"),
  exposureFactorBars: document.getElementById("exposureFactorBars"),
  exposureFactorRows: document.getElementById("exposureFactorRows"),
  exposureHistoryTables: document.getElementById("exposureHistoryTables"),
  fundExposureDrawer: document.getElementById("fundExposureDrawer"),
  closeExposureDrawerBtn: document.getElementById("closeExposureDrawerBtn"),
  exposureDrawerTitle: document.getElementById("exposureDrawerTitle"),
  exposureDrawerCode: document.getElementById("exposureDrawerCode"),
  exposureDrawerBody: document.getElementById("exposureDrawerBody"),
  adjustPortfolioModal: document.getElementById("adjustPortfolioModal"),
  closeAdjustModalBtn: document.getElementById("closeAdjustModalBtn"),
  cancelAdjustModalBtn: document.getElementById("cancelAdjustModalBtn"),
  confirmAdjustModalBtn: document.getElementById("confirmAdjustModalBtn"),
  adjustPortfolioMeta: document.getElementById("adjustPortfolioMeta"),
  portfolioNameInput: document.getElementById("portfolioNameInput"),
  modalStartDate: document.getElementById("modalStartDate"),
  modalEndDate: document.getElementById("modalEndDate"),
  initialCashInput: document.getElementById("initialCashInput"),
  feeSettingsBtn: document.getElementById("feeSettingsBtn"),
  feeSettingsModal: document.getElementById("feeSettingsModal"),
  closeFeeSettingsModalBtn: document.getElementById("closeFeeSettingsModalBtn"),
  cancelFeeSettingsModalBtn: document.getElementById("cancelFeeSettingsModalBtn"),
  confirmFeeSettingsModalBtn: document.getElementById("confirmFeeSettingsModalBtn"),
  purchaseFeeInput: document.getElementById("purchaseFeeInput"),
  redemptionFeeInput: document.getElementById("redemptionFeeInput"),
  transactionFeeInput: document.getElementById("transactionFeeInput"),
  rebalanceFrequencySelect: document.getElementById("rebalanceFrequencySelect"),
  generateGlobalPlanBtn: document.getElementById("generateGlobalPlanBtn"),
  equalInsideBtn: document.getElementById("equalInsideBtn"),
  importExcelBtn: document.getElementById("importExcelBtn"),
  excelFileInput: document.getElementById("excelFileInput"),
  addRebalanceDateBtn: document.getElementById("addRebalanceDateBtn"),
  addRebalanceDateInput: document.getElementById("addRebalanceDateInput"),
  rebalanceTimeline: document.getElementById("rebalanceTimeline"),
  activeRebalanceDateTitle: document.getElementById("activeRebalanceDateTitle"),
  activeCashFlowInput: document.getElementById("activeCashFlowInput"),
  cashEstimatePanel: document.getElementById("cashEstimatePanel"),
  currentTemplateSelect: document.getElementById("currentTemplateSelect"),
  modalHoldingTable: document.getElementById("modalHoldingTable"),
  rebalanceValidation: document.getElementById("rebalanceValidation"),
};

init();

async function init() {
  wireEvents();
  try {
    const res = await fetch(apiPath("/api/assets"));
    if (!res.ok) throw new Error("基金池加载失败");
    const data = await res.json();
    state.funds = data.funds || [];
    state.benchmarks = data.benchmarks || [];
    state.tradingCalendar = data.tradingCalendar || [];
    state.fundsById = new Map(state.funds.map((fund) => [fund.id, fund]));
    els.startDate.value = "2021-01-01";
    els.endDate.value = latestDataDate();
    renderBenchmarks();
    renderFunds();
    renderBasket();
    setStatus(`已加载 ${state.funds.length} 只基金，本地数据包可离线使用。`, "ok");
  } catch (err) {
    setStatus(err.message || String(err), "error");
  }
}

function wireEvents() {
  els.searchInput.addEventListener("input", renderFunds);
  els.benchmarkPickerBtn.addEventListener("click", openBenchmarkModal);
  els.closeBenchmarkModalBtn.addEventListener("click", closeBenchmarkModal);
  els.benchmarkSearchInput.addEventListener("input", renderBenchmarkOptions);
  els.benchmarkTypeFilter.addEventListener("change", renderBenchmarkOptions);
  els.benchmarkModal.addEventListener("click", (event) => {
    if (event.target === els.benchmarkModal) closeBenchmarkModal();
  });
  els.equalWeightBtn.addEventListener("click", equalSleeveWeights);
  els.clearBtn.addEventListener("click", clearBasket);
  els.runBtn.addEventListener("click", runBacktest);
  els.resultTabs.addEventListener("click", handleResultTabClick);
  els.attributionRows.addEventListener("click", handleAttributionTableClick);
  els.closeFundDrawerBtn.addEventListener("click", closeFundDrawer);
  els.exposureView.addEventListener("click", handleExposureClick);
  els.exposureView.addEventListener("mouseover", handleExposureHover);
  els.closeExposureDrawerBtn.addEventListener("click", closeExposureFundDrawer);
  els.weightMode.addEventListener("change", handleWeightModeChange);
  els.performanceRangeStart.addEventListener("input", handlePerformanceRangeInput);
  els.performanceRangeEnd.addEventListener("input", handlePerformanceRangeInput);
  els.annualReturnScope.addEventListener("change", () => {
    state.resultScopes.annualReturn = els.annualReturnScope.value;
    if (state.result) renderAnnualReturnModule(state.result);
  });
  els.periodReturnScope.addEventListener("change", () => {
    state.resultScopes.periodReturn = els.periodReturnScope.value;
    if (state.result) renderPeriodReturnModule(state.result);
  });
  els.annualContributionTableScope.addEventListener("change", () => {
    state.resultScopes.annualContributionTable = els.annualContributionTableScope.value;
    if (state.result) renderAnnualContributionTable(state.result);
  });
  window.addEventListener("resize", () => {
    if (state.result) renderCharts(state.result);
  });
}

function renderBenchmarks() {
  const previousValue = els.benchmarkSelect.value;
  els.benchmarkSelect.innerHTML = "";
  state.benchmarks.forEach((benchmark) => {
    const option = document.createElement("option");
    option.value = benchmark.id;
    option.textContent = `${benchmark.name} (${benchmark.code})`;
    els.benchmarkSelect.appendChild(option);
  });
  if (state.benchmarks.some((benchmark) => benchmark.id === previousValue)) {
    els.benchmarkSelect.value = previousValue;
  } else if (state.benchmarks.some((benchmark) => benchmark.id === "000300.SH")) {
    els.benchmarkSelect.value = "000300.SH";
  } else if (state.benchmarks.length) {
    els.benchmarkSelect.value = state.benchmarks[0].id;
  }
  renderBenchmarkSummary();
}

function renderBenchmarkSummary() {
  const benchmark = currentBenchmark();
  if (!benchmark) {
    els.benchmarkPickerName.textContent = "未选择";
    els.benchmarkPickerMeta.textContent = "";
    return;
  }
  els.benchmarkPickerName.textContent = benchmark.name || benchmark.id;
  els.benchmarkPickerMeta.textContent = benchmarkMetaText(benchmark);
}

function currentBenchmark() {
  return state.benchmarks.find((item) => item.id === els.benchmarkSelect.value) || null;
}

function benchmarkMetaText(benchmark) {
  const kind = benchmarkKindLabel(benchmark);
  const code = benchmark.code || benchmark.id;
  if (benchmark.kind === "fund") {
    return `${code} · ${kind}${benchmark.manager ? ` · ${benchmark.manager}` : ""}`;
  }
  return code;
}

function benchmarkKindLabel(benchmark) {
  return benchmark.kind === "fund" ? "基金" : "指数";
}

function openBenchmarkModal() {
  els.benchmarkTypeFilter.value = "index";
  els.benchmarkSearchInput.value = "";
  renderBenchmarkOptions();
  els.benchmarkModal.classList.remove("hidden");
  setTimeout(() => els.benchmarkSearchInput.focus(), 0);
}

function closeBenchmarkModal() {
  els.benchmarkModal.classList.add("hidden");
}

function renderBenchmarkOptions() {
  const query = els.benchmarkSearchInput.value.trim().toLowerCase();
  const type = els.benchmarkTypeFilter.value || "index";
  const benchmarks = state.benchmarks.filter((benchmark) => {
    if (type !== "all" && benchmark.kind !== type) return false;
    const haystack = [
      benchmark.code,
      benchmark.name,
      benchmark.manager,
      benchmark.fund_type,
      benchmark.cluster_label,
      benchmark.role_label,
      benchmark.full_label,
    ].join(" ").toLowerCase();
    return !query || haystack.includes(query);
  });
  if (!benchmarks.length) {
    els.benchmarkList.innerHTML = `<div class="empty-state">暂无匹配基准</div>`;
    return;
  }
  els.benchmarkList.innerHTML = "";
  benchmarks.forEach((benchmark) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `benchmark-option ${benchmark.id === els.benchmarkSelect.value ? "selected" : ""}`;
    button.innerHTML = `
      <strong>${escapeHtml(benchmark.name || benchmark.id)}</strong>
      <span class="benchmark-kind">${escapeHtml(benchmarkKindLabel(benchmark))}</span>
      <span>${escapeHtml(benchmarkMetaText(benchmark))}</span>
    `;
    button.addEventListener("click", () => selectBenchmark(benchmark.id));
    els.benchmarkList.appendChild(button);
  });
}

async function selectBenchmark(benchmarkId) {
  if (!state.benchmarks.some((benchmark) => benchmark.id === benchmarkId)) return;
  els.benchmarkSelect.value = benchmarkId;
  renderBenchmarkSummary();
  closeBenchmarkModal();
  if (state.result && allSleeveFunds().length) {
    await runBacktest(false);
  }
}

function renderFunds() {
  const query = els.searchInput.value.trim().toLowerCase();
  const funds = state.funds.filter((fund) => {
    const haystack = [
      fund.code,
      fund.name,
      fund.manager,
      fund.company,
      fund.fund_type,
      fund.cluster_label,
      fund.role_label,
      fund.full_label,
    ].join(" ").toLowerCase();
    return !query || haystack.includes(query);
  });

  els.assetCount.textContent = `${funds.length} / ${state.funds.length} 只基金`;
  els.assetList.innerHTML = "";
  if (!funds.length) {
    els.assetList.innerHTML = `<div class="empty-state">没有匹配的基金</div>`;
    return;
  }

  const groupedFunds = groupFundsByCluster(funds);
  clusterDefs.forEach((cluster) => {
    const clusterFunds = groupedFunds.get(cluster.id) || [];
    if (!clusterFunds.length) return;

    const collapsed = !query && state.collapsedClusters.has(cluster.id);
    const section = document.createElement("section");
    section.className = "fund-cluster-section";
    section.innerHTML = `
      <button class="fund-cluster-head" type="button" aria-expanded="${String(!collapsed)}">
        <span class="fund-cluster-title">
          <span class="fund-cluster-toggle">${collapsed ? "+" : "-"}</span>
          <span>${escapeHtml(cluster.name)}</span>
        </span>
        <span class="fund-cluster-count">${clusterFunds.length} 只</span>
      </button>
    `;

    section.querySelector(".fund-cluster-head").addEventListener("click", () => {
      if (state.collapsedClusters.has(cluster.id)) {
        state.collapsedClusters.delete(cluster.id);
      } else {
        state.collapsedClusters.add(cluster.id);
      }
      renderFunds();
    });

    if (!collapsed) {
      const body = document.createElement("div");
      body.className = "fund-cluster-body";
      clusterFunds.forEach((fund) => body.appendChild(renderFundCard(fund)));
      section.appendChild(body);
    }

    els.assetList.appendChild(section);
  });
}

function groupFundsByCluster(funds) {
  const grouped = new Map(clusterDefs.map((cluster) => [cluster.id, []]));
  funds.forEach((fund) => {
    const clusterId = clusterIdForFund(fund);
    grouped.get(clusterId).push(fund);
  });
  return grouped;
}

function clusterIdForFund(fund) {
  const label = normalizedClusterLabel(fund);
  const matched = clusterDefs.find((cluster) => cluster.labels.includes(label));
  return matched ? matched.id : "low_efficiency_cluster";
}

function normalizedClusterLabel(fund) {
  return String(fund.cluster_label || "").trim();
}

function displayClusterLabel(fund) {
  return normalizedClusterLabel(fund) || "未分组";
}

function renderFundCard(fund) {
  const card = document.createElement("button");
  const clusterId = clusterIdForFund(fund);
  card.className = "asset-card";
  card.type = "button";
  card.draggable = true;
  card.innerHTML = `
    <div class="asset-name">
      <span>${escapeHtml(fund.name)}</span>
      <span class="cluster-badge ${clusterId}">${escapeHtml(displayClusterLabel(fund))}</span>
    </div>
    <div class="asset-code">${escapeHtml(fund.code)} · ${escapeHtml(fund.fund_type || "未填类型")} · ${escapeHtml(fund.manager || "未填经理")}</div>
    <div class="asset-dates">${fund.start} 至 ${fund.end}</div>
  `;
  card.addEventListener("click", () => addFund(fund.id, state.activeSleeveId));
  card.addEventListener("dragstart", (event) => {
    card.classList.add("dragging");
    event.dataTransfer.setData("text/plain", fund.id);
  });
  card.addEventListener("dragend", () => card.classList.remove("dragging"));
  return card;
}

function addFund(id, sleeveId) {
  if (!state.fundsById.has(id)) return;
  if (allSleeveFunds().some((item) => item.id === id)) {
    setStatus("这只基金已经在某个组里。", "");
    return;
  }
  const sleeve = findSleeve(sleeveId);
  if (!sleeve) return;
  if (!allSleeveFunds().length && els.weightMode.value === "manual") sleeve.weight = 100;
  sleeve.funds.push({ id, weight: 0 });
  state.activeSleeveId = sleeve.id;
  renderBasket();
  setStatus(`已添加到${sleeve.name}组，点击运行回测后才会计算。`, "");
}

function equalSleeveWeights() {
  const activeSleeves = nonEmptySleeves();
  if (!activeSleeves.length) {
    setStatus("请先选择基金。", "error");
    return;
  }
  const weight = roundNumber(100 / activeSleeves.length, 4);
  activeSleeves.forEach((sleeve, idx) => {
    sleeve.weight = idx === activeSleeves.length - 1 ? roundNumber(100 - weight * (activeSleeves.length - 1), 4) : weight;
    sleeve.internalMode = "equal";
  });
  state.sleeves.filter((sleeve) => !sleeve.funds.length).forEach((sleeve) => {
    sleeve.weight = 0;
  });
  els.weightMode.value = "equal";
  renderBasket();
  setStatus("已按非空组等权分配，组内也按等权处理，尚未运行回测。", "ok");
}

function clearBasket() {
  state.sleeves = sleeveDefs.map((sleeve) => ({ ...sleeve, weight: 0, internalMode: "equal", funds: [] }));
  state.activeSleeveId = "defensive";
  state.result = null;
  state.currentBacktestPayload = null;
  els.results.classList.add("hidden");
  renderBasket();
  setStatus("组合已清空。", "");
}

function handleWeightModeChange() {
  const mode = els.weightMode.value;
  if (isTemplateWeightMode(mode)) {
    applyWeightTemplate(mode);
  }
  renderBasket();
}

function applyWeightTemplate(mode) {
  const template = weightTemplateRatios[mode];
  state.sleeves.forEach((sleeve) => {
    sleeve.weight = Number(template[sleeve.id] || 0);
    sleeve.internalMode = "equal";
  });
}

function renderBasket() {
  const fixedOuterWeight = isFixedOuterWeightMode(els.weightMode.value);
  els.sleeveGrid.innerHTML = "";

  state.sleeves.forEach((sleeve) => {
    const isActive = sleeve.id === state.activeSleeveId;
    const displaySleeveWeight = sleeveDisplayWeight(sleeve);
    const card = document.createElement("section");
    card.className = `sleeve-card ${isActive ? "active" : ""}`;
    card.dataset.sleeveId = sleeve.id;
    card.innerHTML = `
      <div class="sleeve-head">
        <button class="sleeve-title" type="button">
          <strong>${escapeHtml(sleeve.name)}</strong>
          <span>${sleeve.funds.length} 只基金</span>
        </button>
        <label class="weight-input sleeve-weight">
          组权重%
          <input class="sleeve-weight-input" type="number" min="0" max="100" step="0.01" value="${escapeHtml(String(displaySleeveWeight))}" ${fixedOuterWeight ? "disabled" : ""} />
        </label>
      </div>
      <div class="sleeve-controls">
        <label>
          组内权重
          <select class="internal-mode">
            <option value="equal" ${sleeve.internalMode === "equal" ? "selected" : ""}>组内等权</option>
            <option value="manual" ${sleeve.internalMode === "manual" ? "selected" : ""}>组内手动</option>
          </select>
        </label>
        <button class="mini-btn equal-inside-btn" type="button">本组等权</button>
      </div>
      <div class="sleeve-dropzone">
        <div class="empty-state ${sleeve.funds.length ? "hidden" : ""}">拖到${escapeHtml(sleeve.name)}组，或先点本组再点左侧基金。</div>
        <div class="basket-list"></div>
      </div>
    `;
    card.querySelector(".sleeve-title").addEventListener("click", () => {
      state.activeSleeveId = sleeve.id;
      renderBasket();
    });
    card.querySelector(".sleeve-weight-input").addEventListener("input", (event) => {
      sleeve.weight = Number(event.target.value || 0);
      renderWeightSummary();
    });
    card.querySelector(".internal-mode").addEventListener("change", (event) => {
      sleeve.internalMode = event.target.value;
      renderBasket();
    });
    card.querySelector(".equal-inside-btn").addEventListener("click", () => {
      sleeve.internalMode = "equal";
      renderBasket();
      setStatus(`${sleeve.name}组已设为组内等权。`, "ok");
    });
    const dropzone = card.querySelector(".sleeve-dropzone");
    dropzone.addEventListener("dragover", (event) => {
      event.preventDefault();
      dropzone.classList.add("drag-over");
      state.activeSleeveId = sleeve.id;
    });
    dropzone.addEventListener("dragleave", () => dropzone.classList.remove("drag-over"));
    dropzone.addEventListener("drop", (event) => {
      event.preventDefault();
      dropzone.classList.remove("drag-over");
      const id = event.dataTransfer.getData("text/plain");
      if (id) addFund(id, sleeve.id);
    });

    const list = card.querySelector(".basket-list");
    const fundDisplayWeights = sleeveFundDisplayWeights(sleeve, displaySleeveWeight);
    sleeve.funds.forEach((item, index) => {
      const fund = state.fundsById.get(item.id);
      if (!fund) return;
      const row = document.createElement("div");
      row.className = "basket-item";
      row.innerHTML = `
        <div class="basket-main">
          <strong>${escapeHtml(fund.name)}</strong>
          <span>${escapeHtml(fund.code)} · ${escapeHtml(fund.manager || "未填经理")}</span>
        </div>
        <label class="weight-input">
          基金权重%
          <input type="number" min="0" max="100" step="0.01" value="${escapeHtml(String(fundDisplayWeights.get(item.id) || 0))}" ${sleeve.internalMode === "equal" ? "disabled" : ""} />
        </label>
        <button class="remove-btn" type="button" title="移除">×</button>
      `;
      row.querySelector("input").addEventListener("input", (event) => {
        sleeve.funds[index].weight = Number(event.target.value || 0);
        renderWeightSummary();
      });
      row.querySelector("button").addEventListener("click", () => {
        sleeve.funds = sleeve.funds.filter((entry) => entry.id !== item.id);
        if (!sleeve.funds.length) sleeve.weight = 0;
        renderBasket();
      });
      list.appendChild(row);
    });
    els.sleeveGrid.appendChild(card);
  });
  renderWeightSummary();
}

function renderWeightSummary() {
  if (!allSleeveFunds().length) {
    els.weightSummary.textContent = "当前未选择基金。";
    els.weightSummary.className = "weight-summary";
    return;
  }
  const validation = validateSleeves(true);
  const finalFunds = validation.ok ? validation.funds : [];
  const parts = state.sleeves
    .filter((sleeve) => sleeve.funds.length)
    .map((sleeve) => `${sleeve.name}${sleeveEffectiveWeight(sleeve).toFixed(2)}%`);
  els.weightSummary.textContent = validation.ok
    ? `组权重：${parts.join(" / ")}。最终 ${finalFunds.length} 只基金，合计 100.00%。`
    : validation.message;
  els.weightSummary.className = validation.ok ? "weight-summary ok" : "weight-summary error";
}

async function runBacktest() {
  if (!allSleeveFunds().length) {
    setStatus("请先选择至少 1 只基金。", "error");
    return;
  }
  const validation = validateSleeves(true);
  if (!validation.ok) {
    setStatus(validation.message, "error");
    renderWeightSummary();
    return;
  }
  const payload = buildBacktestPayload(validation.funds);
  els.runBtn.disabled = true;
  setStatus("正在回测...", "");
  try {
    const res = await fetch(apiPath("/api/backtest"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok || data.error) throw new Error(data.error || "回测失败");
    state.result = data;
    state.currentBacktestPayload = payload;
    renderResult(data);
    if ((data.warnings || []).length) {
      setStatus(backtestCompleteStatusText(state.currentPortfolio, `回测完成，但有 ${data.warnings.length} 条净值缺失处理提示。`), "ok");
    } else {
      setStatus(backtestCompleteStatusText(state.currentPortfolio), "ok");
    }
  } catch (err) {
    setStatus(err.message || String(err), "error");
  } finally {
    els.runBtn.disabled = false;
  }
}

function buildBacktestPayload(funds) {
  return {
    funds,
    sleeves: buildSleevePayload(),
    weightMode: "manual",
    benchmark: els.benchmarkSelect.value || "000300.SH",
    startDate: els.startDate.value,
    endDate: els.endDate.value,
    dataFrequency: currentDataFrequency(),
  };
}

function buildSleevePayload() {
  return state.sleeves
    .filter((sleeve) => sleeve.funds.length)
    .map((sleeve) => ({
      id: sleeve.id,
      name: sleeve.name,
      funds: sleeve.funds.map((fund) => fund.id),
    }));
}

async function fetchJson(url, options = {}) {
  const requestUrl = url.startsWith("/api/") ? apiPath(url) : url;
  const res = await fetch(requestUrl, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  const text = await res.text();
  let data;
  try {
    data = text ? JSON.parse(text) : {};
  } catch (err) {
    throw new Error(buildNonJsonError(url, res, text));
  }
  if (!res.ok || data.error) throw new Error(data.error || "请求失败");
  return data;
}

function apiPath(path) {
  return String(path || "").replace(/^\/+/, "");
}

function buildNonJsonError(url, response, text) {
  const preview = String(text || "").trim().slice(0, 40);
  if (url.startsWith("/api/strategies") && response.status === 404) {
    return "策略接口不可用：请关闭旧的回测引擎窗口或旧服务后重新启动平台。";
  }
  return `服务器返回了非 JSON 内容（HTTP ${response.status}）：${preview || "空响应"}`;
}

function isValidResultSnapshot(result) {
  return Boolean(
    result &&
      typeof result === "object" &&
      !result.error &&
      result.metrics &&
      typeof result.metrics === "object" &&
      result.window &&
      typeof result.window === "object" &&
      Array.isArray(result.funds),
  );
}

function isLegacyResultSnapshot(result) {
  if (!isValidResultSnapshot(result)) return false;
  const metrics = result.metrics || {};
  return ["drawdown_period", "drawdown_months", "recovery_months", "annual_win_rate"].some(
    (key) => !Object.prototype.hasOwnProperty.call(metrics, key),
  );
}

function findSleeve(sleeveId) {
  return state.sleeves.find((sleeve) => sleeve.id === sleeveId);
}

function allSleeveFunds() {
  return state.sleeves.flatMap((sleeve) => sleeve.funds);
}

function nonEmptySleeves() {
  return state.sleeves.filter((sleeve) => sleeve.funds.length);
}

function normalizeWeightMode(mode) {
  return mode === "manual" || mode === "equal" || isTemplateWeightMode(mode) ? mode : "manual";
}

function isTemplateWeightMode(mode) {
  return Object.prototype.hasOwnProperty.call(weightTemplateRatios, mode);
}

function isFixedOuterWeightMode(mode) {
  return mode === "equal" || isTemplateWeightMode(mode);
}

function sleeveDisplayWeight(sleeve) {
  const mode = els.weightMode.value;
  if (mode === "equal") {
    const activeSleeves = nonEmptySleeves();
    if (!activeSleeves.length || !sleeve.funds.length) return 0;
    const base = roundNumber(100 / activeSleeves.length, 4);
    const index = activeSleeves.findIndex((item) => item.id === sleeve.id);
    return index === activeSleeves.length - 1 ? roundNumber(100 - base * (activeSleeves.length - 1), 4) : base;
  }
  if (isTemplateWeightMode(mode)) return Number(weightTemplateRatios[mode][sleeve.id] || 0);
  return Number(sleeve.weight || 0);
}

function sleeveEffectiveWeight(sleeve) {
  if (!sleeve.funds.length) return 0;
  return sleeveDisplayWeight(sleeve);
}

function sleeveFundDisplayWeights(sleeve, sleeveWeight) {
  const weights = new Map();
  if (!sleeve.funds.length) return weights;
  if (sleeve.internalMode === "equal") {
    const base = roundNumber(sleeveWeight / sleeve.funds.length, 4);
    sleeve.funds.forEach((item, idx) => {
      weights.set(item.id, idx === sleeve.funds.length - 1 ? roundNumber(sleeveWeight - base * (sleeve.funds.length - 1), 4) : base);
    });
    return weights;
  }
  sleeve.funds.forEach((item) => weights.set(item.id, Number(item.weight || 0)));
  return weights;
}

function validateSleeves(strict) {
  const activeSleeves = nonEmptySleeves();
  if (!activeSleeves.length) return { ok: false, message: "请先选择至少 1 只基金。", funds: [] };

  if (!isFixedOuterWeightMode(els.weightMode.value)) {
    const emptyPositive = state.sleeves.find((sleeve) => !sleeve.funds.length && Number(sleeve.weight || 0) > 0.0001);
    if (emptyPositive) return { ok: false, message: `${emptyPositive.name}组没有基金，但设置了权重。`, funds: [] };
    const sleeveTotal = activeSleeves.reduce((sum, sleeve) => sum + Number(sleeve.weight || 0), 0);
    if (strict && Math.abs(sleeveTotal - 100) > 0.01) {
      return { ok: false, message: `三个组的权重合计必须等于 100%，当前为 ${sleeveTotal.toFixed(2)}%。`, funds: [] };
    }
  } else if (isTemplateWeightMode(els.weightMode.value)) {
    const template = weightTemplateRatios[els.weightMode.value];
    const emptyPositive = state.sleeves.find((sleeve) => !sleeve.funds.length && Number(template[sleeve.id] || 0) > 0.0001);
    if (emptyPositive) return { ok: false, message: `${emptyPositive.name}组没有基金，但模板设置了权重。`, funds: [] };
  }

  const finalFunds = [];
  for (const sleeve of activeSleeves) {
    const sleeveWeight = sleeveEffectiveWeight(sleeve);
    if (strict && sleeveWeight <= 0) return { ok: false, message: `${sleeve.name}组权重必须大于 0。`, funds: [] };
    const displayWeights = sleeveFundDisplayWeights(sleeve, sleeveWeight);
    const fundTotal = [...displayWeights.values()].reduce((sum, value) => sum + Number(value || 0), 0);
    if (sleeve.internalMode === "manual") {
      if (fundTotal - sleeveWeight > 0.01) {
        return { ok: false, message: `${sleeve.name}组内基金权重合计不能超过组权重：组权重 ${sleeveWeight.toFixed(2)}%，组内 ${fundTotal.toFixed(2)}%。`, funds: [] };
      }
      if (strict && Math.abs(fundTotal - sleeveWeight) > 0.01) {
        return { ok: false, message: `${sleeve.name}组内基金权重合计必须等于组权重：组权重 ${sleeveWeight.toFixed(2)}%，组内 ${fundTotal.toFixed(2)}%。`, funds: [] };
      }
    }
    sleeve.funds.forEach((item) => {
      const weight = Number(displayWeights.get(item.id) || 0);
      if (weight > 0) finalFunds.push({ id: item.id, weight });
    });
  }

  const finalTotal = finalFunds.reduce((sum, item) => sum + Number(item.weight || 0), 0);
  if (strict && Math.abs(finalTotal - 100) > 0.01) {
    return { ok: false, message: `最终基金权重合计必须等于 100%，当前为 ${finalTotal.toFixed(2)}%。`, funds: [] };
  }
  return { ok: true, message: "", funds: finalFunds };
}

function renderResult(result) {
  els.results.classList.remove("hidden");
  const rebalanceCount = Math.max((result.rebalance_dates || []).length - 1, 0);
  const pointCount = performancePointCount(result) || result.window.days;
  els.windowText.textContent = `${result.window.start} 至 ${result.window.end}，${pointCount} 个${frequencyLabel(result.frequency)}观察点，季度调仓 ${rebalanceCount} 次`;
  renderPortfolioOverview(result);
  renderMetrics(result.metrics, result);
  renderWarnings(result.warnings || []);
  renderScopeSelectors();
  resetPerformanceWindow(result);
  resetAnalysisWindow(result);
  renderCharts(result);
  renderAnnualContributionTable(result);
  renderAttributionView(result);
  renderExposureView(result);
  setResultView("home");
}

function handleResultTabClick(event) {
  const button = event.target.closest("[data-result-view]");
  if (!button) return;
  const view = button.dataset.resultView;
  if (view === "exposure") {
    setResultView("exposure");
    return;
  }
  if (view === "attribution" && !state.result) {
    setStatus("请先运行回测，再查看归因分析。", "error");
    return;
  }
  setResultView(view);
}

function setResultView(view) {
  state.resultView = view;
  els.resultTabs.querySelectorAll("[data-result-view]").forEach((button) => {
    button.classList.toggle("active", button.dataset.resultView === view);
  });
  const analysisMode = view !== "home";
  els.shell.classList.remove("analysis-mode");
  els.libraryPanel.classList.remove("hidden");
  els.portfolioPanel.classList.toggle("hidden", analysisMode);
  els.status.classList.toggle("hidden", analysisMode);
  els.results.classList.toggle("hidden", view !== "home" || !state.result);
  els.attributionView.classList.toggle("hidden", view !== "attribution");
  els.exposureView.classList.toggle("hidden", view !== "exposure");
  if (view === "attribution" && state.result) renderAttributionView(state.result);
  if (view === "exposure") renderExposureView(state.result);
  if (view !== "attribution") closeFundDrawer();
  if (view !== "exposure") closeExposureFundDrawer();
  if (view === "home" && state.result) renderCharts(state.result);
}

function renderMetrics(metrics, result = state.result) {
  const primaryItems = [
    ["累计收益", formatPercent(metrics.total_return)],
    ["年化收益", formatPercent(metrics.annual_return)],
    ["年化波动", formatPercent(metrics.annual_volatility)],
    ["最大回撤", formatPercent(metrics.max_drawdown)],
    ["夏普", formatNumber(metrics.sharpe, 2)],
    ["Calmar", formatNumber(metrics.calmar, 2)],
    [winRateMetricLabel(result), formatPercent(metrics.win_rate)],
  ];
  const secondaryItems = [
    ["回撤区间", formatText(metrics.drawdown_period)],
    ["回撤时长（月）", formatMonths(metrics.drawdown_months)],
    ["修复耗时（月）", formatRepairMonths(metrics.recovery_months)],
    ["年度胜率%（正收益占比）", formatPercent(metrics.annual_win_rate)],
  ];
  const riskRows = [...primaryItems, ...secondaryItems];
  const managementRows = [...renderCashMetricCards(metrics), ...renderPortfolioStateMetricCards()];
  els.metrics.innerHTML = `
    ${metricGroupHtml("收益风险指标", riskRows)}
    ${metricGroupHtml("组合管理指标", managementRows)}
  `;
}

function renderPortfolioOverview(result) {
  if (!els.portfolioOverview || !result) return;
  const metrics = result.metrics || {};
  const portfolioSeries = result.series?.portfolio || [];
  const overviewItems = [
    ["净资产", formatMoney(metrics.final_value), ""],
    ["累计盈亏", formatMoney(metrics.total_profit), signedClass(metrics.total_profit)],
    ["今日收益", formatPercent(seriesLatestReturn(portfolioSeries)), signedClass(seriesLatestReturn(portfolioSeries))],
    ["今年以来", formatPercent(seriesYtdReturn(portfolioSeries)), signedClass(seriesYtdReturn(portfolioSeries))],
    ["成立以来收益", formatPercent(metrics.total_return), signedClass(metrics.total_return)],
    ["年化收益", formatPercent(metrics.annual_return), signedClass(metrics.annual_return)],
    ["成立以来回撤", formatPercent(metrics.max_drawdown), signedClass(metrics.max_drawdown)],
  ];
  els.portfolioOverview.innerHTML = `
    <div class="overview-title">数据概览</div>
    <div class="overview-items">
      ${overviewItems.map(([label, value, className]) => `
        <div class="overview-item">
          <strong class="${escapeHtml(className)}">${escapeHtml(value)}</strong>
          <span>${escapeHtml(label)}</span>
        </div>
      `).join("")}
    </div>
  `;
}

function renderCashMetricCards(metrics) {
  if (!metrics || metrics.initial_cash === undefined) return [];
  return [
    ["最终资产", formatMoney(metrics.final_value)],
    ["累计净投入", formatMoney(metrics.net_contribution)],
    ["累计盈亏", formatMoney(metrics.total_profit)],
    ["现金余额", formatMoney(metrics.cash_balance)],
    ["累计手续费", formatMoney(metrics.total_fees)],
  ];
}

function metricCardHtml([label, value]) {
  return `<div class="metric"><span>${label}</span><strong>${value}</strong></div>`;
}

function renderPortfolioStateMetricCards() {
  const portfolio = state.currentPortfolio;
  if (!portfolio) return [];
  const latest = latestPlanNode(portfolio);
  const holdings = portfolio.latestHoldings?.length ? portfolio.latestHoldings : holdingsPayloadFromPlan(latest);
  return [
    ["最新持仓日", latest?.date || "-"],
    ...sleeveDefs.map((sleeve) => {
      const rows = holdings.filter((holding) => normalizeSleeveId(holding.sleeveId) === sleeve.id);
      const weight = rows.reduce((sum, holding) => sum + Number(holding.weight || 0), 0);
      return [sleeve.name, `${formatPercent(weight)} · ${rows.length} 只`];
    }),
  ];
}

function metricGroupHtml(title, rows) {
  return `
    <section class="metric-group">
      <h3>${escapeHtml(title)}</h3>
      <table>
        <tbody>
          ${rows.map(([label, value]) => `
            <tr>
              <th>${escapeHtml(label)}</th>
              <td>${escapeHtml(value)}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </section>
  `;
}

function seriesLatestReturn(points) {
  const clean = (points || []).filter((point) => Number.isFinite(Number(point.value)) && Number(point.value) > 0);
  if (clean.length < 2) return null;
  const latest = Number(clean[clean.length - 1].value);
  const previous = Number(clean[clean.length - 2].value);
  return previous > 0 ? latest / previous - 1 : null;
}

function seriesYtdReturn(points) {
  const clean = (points || []).filter((point) => Number.isFinite(Number(point.value)) && Number(point.value) > 0 && point.date);
  if (!clean.length) return null;
  const latest = clean[clean.length - 1];
  const latestDate = parseDate(latest.date);
  if (!latestDate) return null;
  const yearStart = new Date(latestDate.getFullYear(), 0, 1);
  const previousYearPoints = clean.filter((point) => {
    const date = parseDate(point.date);
    return date && date < yearStart;
  });
  const currentYearPoints = clean.filter((point) => {
    const date = parseDate(point.date);
    return date && date >= yearStart;
  });
  const basePoint = previousYearPoints[previousYearPoints.length - 1] || currentYearPoints[0];
  const base = Number(basePoint?.value);
  const latestValue = Number(latest.value);
  return base > 0 ? latestValue / base - 1 : null;
}

function renderCharts(result) {
  drawPerformanceCharts(result);
  renderAnnualReturnModule(result);
  drawAnnualContributionChart("annualContributionChart", annualSleeveContributionChartData(result));
  renderPeriodReturnModule(result);
  drawAnnualContributionChart("periodContributionChart", periodSleeveContributionChartData(result));
}

function resetPerformanceWindow(result) {
  const total = performancePointCount(result);
  state.performanceWindow = { start: 0, end: Math.max(total - 1, 0) };
  state.performanceRangePreset = "all";
  syncPerformanceRangeInputs(total);
}

function handlePerformanceModeChange() {
  state.performanceChartMode = els.performanceModeSelect?.value || "return";
  if (state.result) drawPerformanceCharts(state.result);
}

function handlePerformanceRangeInput(event) {
  if (!state.result) return;
  const total = performancePointCount(state.result);
  let start = Number(els.performanceRangeStart.value || 0);
  let end = Number(els.performanceRangeEnd.value || 0);

  if (start >= end) {
    if (event.target === els.performanceRangeStart) start = Math.max(0, end - 1);
    else end = Math.min(Math.max(total - 1, 0), start + 1);
  }

  state.performanceWindow = { start, end };
  state.performanceRangePreset = "";
  syncPerformanceRangeInputs(total);
  drawPerformanceCharts(state.result);
}

function handlePerformanceDateInput(event) {
  if (!state.result) return;
  const total = performancePointCount(state.result);
  const points = performanceBasePoints(state.result).slice(0, total);
  if (!points.length) return;

  let start = state.performanceWindow.start;
  let end = state.performanceWindow.end;
  const isStartInput = event.target === els.performanceRangeStartDate;
  const index = closestPerformancePointIndex(points, event.target.value, isStartInput ? "start" : "end");
  if (isStartInput) start = index;
  else end = index;

  if (start >= end) {
    if (isStartInput) start = Math.max(0, end - 1);
    else end = Math.min(total - 1, start + 1);
  }

  state.performanceWindow = { start, end };
  state.performanceRangePreset = "";
  syncPerformanceRangeInputs(total);
  drawPerformanceCharts(state.result);
}

function handlePerformanceRangePresetClick(event) {
  const button = event.target.closest("[data-range-preset]");
  if (!button || !state.result) return;
  const preset = button.dataset.rangePreset || "all";
  state.performanceRangePreset = preset;
  state.performanceWindow = performanceWindowForPreset(state.result, preset);
  syncPerformanceRangeInputs(performancePointCount(state.result));
  drawPerformanceCharts(state.result);
}

function performanceWindowForPreset(result, preset) {
  const points = performanceBasePoints(result);
  const total = points.length;
  if (!total || preset === "all") return { start: 0, end: Math.max(total - 1, 0) };
  const latestDate = parseDate(points[total - 1]?.date);
  if (!latestDate) return { start: 0, end: Math.max(total - 1, 0) };
  const targetDate = rangePresetStartDate(latestDate, preset);
  const start = Math.max(0, points.findIndex((point) => {
    const date = parseDate(point.date);
    return date && date >= targetDate;
  }));
  return { start: start < 0 ? 0 : start, end: total - 1 };
}

function rangePresetStartDate(latestDate, preset) {
  const date = new Date(latestDate);
  if (preset === "ytd") return new Date(date.getFullYear(), 0, 1);
  if (preset === "1m") date.setMonth(date.getMonth() - 1);
  else if (preset === "3m") date.setMonth(date.getMonth() - 3);
  else if (preset === "6m") date.setMonth(date.getMonth() - 6);
  else if (preset === "1y") date.setFullYear(date.getFullYear() - 1);
  else if (preset === "3y") date.setFullYear(date.getFullYear() - 3);
  return date;
}

function performanceBasePoints(result) {
  return result?.series?.portfolio || [];
}

function closestPerformancePointIndex(points, value, direction) {
  const targetDate = parseDate(value);
  if (!targetDate) return direction === "start" ? 0 : Math.max(points.length - 1, 0);
  if (direction === "start") {
    const index = points.findIndex((point) => {
      const date = parseDate(point.date);
      return date && date >= targetDate;
    });
    return index >= 0 ? index : Math.max(points.length - 1, 0);
  }
  for (let index = points.length - 1; index >= 0; index -= 1) {
    const date = parseDate(points[index].date);
    if (date && date <= targetDate) return index;
  }
  return 0;
}

function syncPerformanceRangeInputs(total) {
  const max = Math.max(total - 1, 0);
  const start = Math.max(0, Math.min(state.performanceWindow.start, max));
  const end = Math.max(start, Math.min(state.performanceWindow.end, max));
  state.performanceWindow = { start, end };

  [els.performanceRangeStart, els.performanceRangeEnd].forEach((input) => {
    input.min = "0";
    input.max = String(max);
    input.disabled = max < 1;
  });
  els.performanceRangeStart.value = String(start);
  els.performanceRangeEnd.value = String(end);

  const startPct = max > 0 ? (start / max) * 100 : 0;
  const endPct = max > 0 ? (end / max) * 100 : 100;
  els.performanceRangeTrack.style.left = `${startPct}%`;
  els.performanceRangeTrack.style.width = `${Math.max(endPct - startPct, 0)}%`;
  syncPerformanceRangePresetButtons();
  syncPerformanceDateInputs(performanceBasePoints(state.result).slice(0, total), start, end);
}

function syncPerformanceDateInputs(points, start, end) {
  const startInput = els.performanceRangeStartDate;
  const endInput = els.performanceRangeEndDate;
  if (!startInput || !endInput) return;
  const first = performanceDateInputValue(points[0]?.date);
  const last = performanceDateInputValue(points[points.length - 1]?.date);
  const disabled = points.length < 2;
  [startInput, endInput].forEach((input) => {
    input.min = first;
    input.max = last;
    input.disabled = disabled;
  });
  startInput.value = performanceDateInputValue(points[start]?.date);
  endInput.value = performanceDateInputValue(points[end]?.date);
}

function performanceDateInputValue(value) {
  const date = parseDate(value);
  return date ? formatDateInput(date) : "";
}

function syncPerformanceRangePresetButtons() {
  els.performanceRangePresets?.querySelectorAll("[data-range-preset]").forEach((button) => {
    button.classList.toggle("active", button.dataset.rangePreset === state.performanceRangePreset);
  });
}

function renderWarnings(warnings) {
  if (!warnings.length) {
    els.warningBox.classList.add("hidden");
    els.warningBox.innerHTML = "";
    return;
  }
  els.warningBox.classList.remove("hidden");
  els.warningBox.innerHTML = `<ul>${warnings.map((warning) => `<li>${escapeHtml(warning)}</li>`).join("")}</ul>`;
}

function renderScopeSelectors() {
  renderScopeSelect(els.annualReturnScope, "portfolio", "组合", state.resultScopes.annualReturn);
  renderScopeSelect(els.periodReturnScope, "portfolio", "组合", state.resultScopes.periodReturn);
  renderScopeSelect(els.annualContributionTableScope, "funds", "基金", state.resultScopes.annualContributionTable);
}

function renderScopeSelect(select, defaultValue, defaultLabel, selectedValue) {
  const scopes = [{ value: defaultValue, label: defaultLabel }, ...state.sleeves.filter((sleeve) => sleeve.funds.length).map((sleeve) => ({ value: sleeve.id, label: sleeve.name }))];
  const validValue = scopes.some((scope) => scope.value === selectedValue) ? selectedValue : defaultValue;
  select.innerHTML = scopes.map((scope) => `<option value="${escapeHtml(scope.value)}" ${scope.value === validValue ? "selected" : ""}>${escapeHtml(scope.label)}</option>`).join("");
  select.value = validValue;
  updateScopeState(select.id, validValue);
}

function updateScopeState(selectId, value) {
  if (selectId === "annualReturnScope") state.resultScopes.annualReturn = value;
  if (selectId === "periodReturnScope") state.resultScopes.periodReturn = value;
  if (selectId === "annualContributionTableScope") state.resultScopes.annualContributionTable = value;
}

function renderAnnualReturnModule(result) {
  const scope = state.resultScopes.annualReturn;
  const rows = annualReturnRowsForScope(result, scope);
  drawAnnualChart("annualReturnChart", rows, annualReturnLabels(scope));
}

function renderPeriodReturnModule(result) {
  const scope = state.resultScopes.periodReturn;
  const rows = periodReturnRowsForScope(result, scope);
  drawAnnualChart("periodReturnChart", rows, annualReturnLabels(scope));
}

function annualReturnLabels(scope) {
  return {
    portfolio: scope === "portfolio" ? "组合" : `${scopeLabel(scope)}组`,
    benchmark: currentBenchmarkLabel(),
    excess: "超额",
  };
}

function currentBenchmarkLabel() {
  const benchmark = state.benchmarks.find((item) => item.id === els.benchmarkSelect.value);
  return benchmark?.name || "基准";
}

function periodReturnRowsForScope(result, scope) {
  if (scope === "portfolio") return result.period_returns || [];
  const scale = scopeWeightScale(scope, result);
  const fundIds = scopeFundIds(scope);
  return (result.period_contributions || []).map((row) => {
    const sleeveReturn = sumFundValues(row.funds, fundIds) / scale;
    const base = (result.period_returns || []).find((item) => item.period === row.period) || {};
    return {
      period: row.period,
      label: row.label,
      portfolio: sleeveReturn,
      benchmark: base.benchmark ?? null,
      excess: base.benchmark === null || base.benchmark === undefined ? null : sleeveReturn - base.benchmark,
    };
  });
}

function annualReturnRowsForScope(result, scope) {
  if (scope === "portfolio") return result.annual_returns || [];
  const annualReturns = new Map((result.annual_returns || []).map((row) => [row.year, row]));
  const sleeveRows = result.attribution?.annual_sleeve_returns?.[scope];
  if (Array.isArray(sleeveRows)) {
    return sleeveRows.map((row) => {
      const base = annualReturns.get(row.year) || {};
      return {
        year: row.year,
        portfolio: row.return,
        benchmark: base.benchmark ?? null,
        excess: base.benchmark === null || base.benchmark === undefined ? null : row.return - base.benchmark,
      };
    });
  }
  const scale = scopeWeightScale(scope, result);
  const fundIds = scopeFundIds(scope);
  return (result.annual_contributions || []).map((row) => {
    const sleeveReturn = sumFundValues(row.funds, fundIds) / scale;
    const base = (result.annual_returns || []).find((item) => item.year === row.year) || {};
    return {
      year: row.year,
      portfolio: sleeveReturn,
      benchmark: base.benchmark ?? null,
      excess: base.benchmark === null || base.benchmark === undefined ? null : sleeveReturn - base.benchmark,
    };
  });
}

function annualSleeveContributionChartData(result) {
  const groups = state.sleeves.map((sleeve) => ({
    id: sleeve.id,
    name: sleeve.name,
    color: sleevePalette[sleeve.id] || colors.portfolio,
    funds: sleeve.funds,
  }));
  const rows = (result.annual_contributions || []).map((row) => {
    const values = {};
    groups.forEach((sleeve) => {
      values[sleeve.id] = sumFundValues(row.funds, sleeve.funds.map((fund) => fund.id));
    });
    return {
      year: row.year,
      values,
      total: Object.values(values).reduce((sum, value) => sum + Number(value || 0), 0),
    };
  });
  return { groups, rows };
}

function periodSleeveContributionChartData(result) {
  const groups = state.sleeves.map((sleeve) => ({
    id: sleeve.id,
    name: sleeve.name,
    color: sleevePalette[sleeve.id] || colors.portfolio,
    funds: sleeve.funds,
  }));
  const rows = (result.period_contributions || []).map((row) => {
    const values = {};
    groups.forEach((sleeve) => {
      values[sleeve.id] = sumFundValues(row.funds, sleeve.funds.map((fund) => fund.id));
    });
    return {
      period: row.period,
      label: row.label,
      values,
      total: Object.values(values).reduce((sum, value) => sum + Number(value || 0), 0),
    };
  });
  return { groups, rows };
}

function resultScopeData(result, scope) {
  if (scope === "funds" || scope === "portfolio") return result;
  const scale = scopeWeightScale(scope, result);
  const fundIds = scopeFundIds(scope);
  const fundSet = new Set(fundIds);
  return {
    ...result,
    funds: (result.funds || []).filter((fund) => fundSet.has(fund.id)).map((fund) => ({ ...fund, weight: (fund.weight || 0) / scale })),
    annual_contributions: (result.annual_contributions || []).map((row) => {
      const funds = {};
      fundIds.forEach((fundId) => {
        funds[fundId] = ((row.funds || {})[fundId] || 0) / scale;
      });
      return { year: row.year, funds, total: sumFundValues(row.funds || {}, fundIds) / scale };
    }),
    fund_contributions: (result.fund_contributions || [])
      .filter((row) => fundSet.has(row.id))
      .map((row) => ({ ...row, weight: (row.weight || 0) / scale, contribution: (row.contribution || 0) / scale })),
  };
}

function scopeFundIds(scope) {
  const sleeve = findSleeve(scope);
  return sleeve ? sleeve.funds.map((fund) => fund.id) : [];
}

function scopeLabel(scope) {
  return findSleeve(scope)?.name || "子组合";
}

function scopeWeightScale(scope, result) {
  const fundIds = new Set(scopeFundIds(scope));
  const weight = (result.funds || []).reduce((sum, fund) => (fundIds.has(fund.id) ? sum + Number(fund.weight || 0) : sum), 0);
  return Math.max(weight, 0.0001);
}

function sumFundValues(values, fundIds) {
  return fundIds.reduce((sum, fundId) => sum + Number((values || {})[fundId] || 0), 0);
}

function drawPerformanceCharts(result) {
  const windowSeries = performanceWindowSeries(result);
  const benchmarkLabel = currentBenchmarkLabel();
  const mode = effectivePerformanceMode(result);
  syncPerformanceModeSelect(mode, result);
  if (mode === "asset") {
    renderChartInlineLegend(els.returnChartLegend, [
      { label: "账户资产累计变化", color: colors.portfolio },
    ]);
    renderChartInlineLegend(els.drawdownChartLegend, [
      { label: "账户资产回撤", color: colors.drawdown },
    ]);
    drawAssetPerformanceChart("returnChart", windowSeries.account);
    drawLineChart("drawdownChart", [
      { label: "账户资产回撤", points: drawdownFromNavPoints(windowSeries.account), color: colors.drawdown },
    ], { percentAxis: true, strongZeroAxis: true, hideLegend: true });
  } else {
    renderChartInlineLegend(els.returnChartLegend, [
      { label: "累计超额", color: colors.excess },
      { label: "组合累计收益", color: colors.portfolio },
      { label: `${benchmarkLabel}累计收益`, color: colors.benchmark },
    ]);
    renderChartInlineLegend(els.drawdownChartLegend, [
      { label: "组合回撤", color: colors.drawdown },
      { label: `${benchmarkLabel}回撤`, color: colors.benchmark },
    ]);
    drawPerformanceChart("returnChart", windowSeries.portfolio, windowSeries.benchmark, benchmarkLabel);
    drawLineChart("drawdownChart", [
      { label: "组合回撤", points: drawdownFromNavPoints(windowSeries.portfolio), color: colors.drawdown },
      { label: `${benchmarkLabel}回撤`, points: drawdownFromNavPoints(windowSeries.benchmark), color: colors.benchmark },
    ], { percentAxis: true, strongZeroAxis: true, hideLegend: true });
  }
  renderPerformanceRangeLabels(windowSeries);
}

function renderChartInlineLegend(container, items) {
  if (!container) return;
  container.innerHTML = (items || []).map((item) => `
    <span><i style="background:${item.color}"></i>${escapeHtml(item.label)}</span>
  `).join("");
}

function performancePointCount(result) {
  return Math.min(
    (result?.series?.portfolio || []).length,
    (result?.series?.benchmark || []).length,
  );
}

function performanceWindowSeries(result) {
  const total = performancePointCount(result);
  if (!total) return { portfolio: [], benchmark: [], account: [] };
  syncPerformanceRangeInputs(total);
  const start = state.performanceWindow.start;
  const end = state.performanceWindow.end + 1;
  return {
    portfolio: (result.series.portfolio || []).slice(start, end),
    benchmark: (result.series.benchmark || []).slice(start, end),
    account: (result.series.account_value || []).slice(start, end),
  };
}

function renderPerformanceRangeLabels(windowSeries) {
  const active = state.performanceChartMode === "asset" && (windowSeries.account || []).length ? windowSeries.account : windowSeries.portfolio || [];
  els.performanceRangeStartLabel.textContent = active[0]?.date || "-";
  els.performanceRangeEndLabel.textContent = active[active.length - 1]?.date || "-";
}

function resetReturnPoints(points) {
  const first = (points || []).find((point) => Number.isFinite(Number(point.value)) && Number(point.value) > 0);
  if (!first) return [];
  const base = Number(first.value);
  return (points || []).map((point) => ({
    date: point.date,
    value: Number.isFinite(Number(point.value)) && Number(point.value) > 0 ? Number(point.value) / base - 1 : null,
  }));
}

function drawdownFromNavPoints(points) {
  let peak = null;
  return (points || []).map((point) => {
    const value = Number(point.value);
    if (!Number.isFinite(value) || value <= 0) return { date: point.date, value: null };
    peak = peak === null ? value : Math.max(peak, value);
    return { date: point.date, value: Math.min(0, value / peak - 1) };
  });
}

function drawPerformanceChart(canvasId, portfolioPoints, benchmarkPoints, benchmarkLabel = currentBenchmarkLabel()) {
  const portfolioReturns = resetReturnPoints(portfolioPoints);
  const benchmarkReturns = resetReturnPoints(benchmarkPoints);
  const benchmarkByDate = new Map(benchmarkReturns.map((point) => [point.date, point.value]));
  const excess = portfolioReturns
    .map((point) => ({ date: point.date, value: point.value - (benchmarkByDate.get(point.date) || 0) }))
    .filter((point) => Number.isFinite(point.value));
  drawLineChart(canvasId, [
    { label: "累计超额", points: excess, color: colors.excessFill, fill: true, hideLegend: true },
    { label: "累计超额", points: excess, color: colors.excess },
    { label: "组合累计收益", points: portfolioReturns, color: colors.portfolio },
    { label: `${benchmarkLabel}累计收益`, points: benchmarkReturns, color: colors.benchmark },
  ], { percentAxis: true, yearAxis: true, hideLegend: true });
}

function drawAssetPerformanceChart(canvasId, accountPoints) {
  const accountReturns = assetReturnPoints(accountPoints);
  drawLineChart(canvasId, [
    { label: "账户资产累计变化", points: accountReturns, color: colors.portfolio },
  ], { percentAxis: true, yearAxis: true, moneyAxisBase: assetSeriesBase(accountPoints), hideLegend: true });
}

function assetReturnPoints(points) {
  const first = (points || []).find((point) => Number.isFinite(Number(point.value)) && Number(point.value) > 0);
  if (!first) return [];
  const base = Number(first.value);
  return (points || []).map((point) => {
    const amount = Number(point.value);
    return {
      date: point.date,
      value: Number.isFinite(amount) && amount > 0 ? amount / base - 1 : null,
      amount: Number.isFinite(amount) ? amount : null,
    };
  });
}

function assetSeriesBase(points) {
  const first = (points || []).find((point) => Number.isFinite(Number(point.value)) && Number(point.value) > 0);
  return first ? Number(first.value) : null;
}

function effectivePerformanceMode(result) {
  if (state.performanceChartMode === "asset" && hasAccountValueSeries(result)) return "asset";
  return "return";
}

function hasAccountValueSeries(result) {
  return (result?.series?.account_value || []).some((point) => Number.isFinite(Number(point.value)) && Number(point.value) > 0);
}

function syncPerformanceModeSelect(mode, result) {
  if (!els.performanceModeSelect) return;
  const assetOption = [...els.performanceModeSelect.options].find((option) => option.value === "asset");
  if (assetOption) assetOption.disabled = !hasAccountValueSeries(result);
  els.performanceModeSelect.value = mode;
  state.performanceChartMode = mode;
}

function drawLineChart(canvasId, seriesList, options = {}) {
  const canvas = document.getElementById(canvasId);
  const { ctx, width, height } = prepareCanvas(canvas, 280);

  const bottomPad = options.yearAxis ? 42 : (height <= 120 ? 24 : 30);
  const rightPad = options.moneyAxisBase ? 76 : 18;
  const pad = height <= 120 ? { left: 54, right: rightPad, top: 18, bottom: bottomPad } : { left: 54, right: rightPad, top: 24, bottom: bottomPad };
  const values = seriesList.flatMap((line) => line.points.map((point) => point.value).filter((value) => value !== null));
  if (!values.length) {
    canvas.onmousemove = null;
    canvas.onmouseleave = null;
    return;
  }
  let min = Math.min(...values);
  let max = Math.max(...values);
  if (min === max) {
    min -= 0.05;
    max += 0.05;
  }
  const xMax = Math.max(...seriesList.map((line) => line.points.length - 1));

  const draw = (hover) => {
    ctx.clearRect(0, 0, width, height);
    drawAxes(ctx, width, height, pad, min, max, options.percentAxis);
    if (options.moneyAxisBase) drawMoneyAxis(ctx, width, height, pad, min, max, options.moneyAxisBase);
    if (options.yearAxis) drawYearAxis(ctx, seriesList, xMax, width, height, pad);
    const plotTop = options.strongZeroAxis ? valueToY(0, min, max, pad, height) : pad.top;
    ctx.save();
    ctx.beginPath();
    ctx.rect(pad.left, plotTop, width - pad.left - pad.right, height - pad.bottom - plotTop);
    ctx.clip();
    seriesList.forEach((line) => {
      ctx.beginPath();
      ctx.lineWidth = 2;
      ctx.strokeStyle = line.color;
      line.points.forEach((point, index) => {
        if (point.value === null) return;
        const x = indexToX(index, xMax, width, pad);
        const y = valueToY(point.value, min, max, pad, height);
        if (index === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      if (line.fill) {
        const lastIdx = line.points.length - 1;
        const lastX = indexToX(lastIdx, xMax, width, pad);
        const zeroY = valueToY(0, min, max, pad, height);
        ctx.lineTo(lastX, zeroY);
        ctx.lineTo(pad.left, zeroY);
        ctx.closePath();
        ctx.fillStyle = line.color;
        ctx.fill();
      }
      ctx.stroke();
    });
    ctx.restore();
    if (options.strongZeroAxis) drawStrongZeroAxis(ctx, width, height, pad, min, max);
    if (!options.hideLegend) drawLegend(ctx, seriesList, pad.left, 14);
    if (hover) {
      drawLineHover(
        ctx,
        hover,
        seriesList,
        xMax,
        width,
        height,
        pad,
        min,
        max,
        options.percentAxis,
        options.strongZeroAxis ? valueToY(0, min, max, pad, height) : null,
      );
      if (options.strongZeroAxis) drawStrongZeroAxis(ctx, width, height, pad, min, max);
    }
  };

  draw(null);
  canvas.onmousemove = (event) => {
    const { x, y } = canvasEventPoint(canvas, event, width, height);
    if (x < pad.left || x > width - pad.right || y < pad.top || y > height - pad.bottom) {
      draw(null);
      return;
    }
    const index = Math.max(0, Math.min(xMax, Math.round(((x - pad.left) / (width - pad.left - pad.right)) * xMax)));
    draw({ index, x: indexToX(index, xMax, width, pad), y });
  };
  canvas.onmouseleave = () => draw(null);
}

function drawYearAxis(ctx, seriesList, xMax, width, height, pad) {
  const points = (seriesList.find((line) => line.points?.length)?.points || []);
  const ticks = yearTicksFromPoints(points);
  if (!ticks.length) return;
  ctx.save();
  ctx.strokeStyle = "#dce3ea";
  ctx.fillStyle = "#657282";
  ctx.font = "11px Arial";
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  ticks.forEach((tick) => {
    const x = indexToX(tick.index, xMax, width, pad);
    ctx.beginPath();
    ctx.moveTo(x, height - pad.bottom);
    ctx.lineTo(x, height - pad.bottom + 5);
    ctx.stroke();
    ctx.fillText(String(tick.year), x, height - pad.bottom + 8);
  });
  ctx.restore();
}

function yearTicksFromPoints(points) {
  const ticks = [];
  let lastYear = null;
  (points || []).forEach((point, index) => {
    const date = new Date(point.date);
    if (Number.isNaN(date.getTime())) return;
    const year = date.getFullYear();
    if (year === lastYear) return;
    ticks.push({ year, index });
    lastYear = year;
  });
  return ticks;
}

function drawLineHover(ctx, hover, seriesList, xMax, width, height, pad, min, max, percentAxis, clipTop = null) {
  const date = seriesList.find((line) => line.points[hover.index])?.points[hover.index]?.date || "";
  const rows = seriesList
    .filter((line) => !line.fill)
    .map((line) => ({ line, point: line.points[hover.index] }))
    .filter((row) => row.point && row.point.value !== null);
  if (!rows.length) return;

  ctx.save();
  ctx.strokeStyle = "rgba(23,32,42,0.38)";
  ctx.lineWidth = 1;
  ctx.setLineDash([4, 4]);
  ctx.beginPath();
  ctx.moveTo(hover.x, pad.top);
  ctx.lineTo(hover.x, height - pad.bottom);
  ctx.moveTo(pad.left, hover.y);
  ctx.lineTo(width - pad.right, hover.y);
  ctx.stroke();
  ctx.setLineDash([]);

  ctx.save();
  if (clipTop !== null) {
    ctx.beginPath();
    ctx.rect(pad.left, clipTop, width - pad.left - pad.right, height - pad.bottom - clipTop);
    ctx.clip();
  }
  rows.forEach(({ line, point }) => {
    const y = valueToY(point.value, min, max, pad, height);
    ctx.fillStyle = line.color;
    ctx.beginPath();
    ctx.arc(hover.x, y, 3.5, 0, Math.PI * 2);
    ctx.fill();
  });
  ctx.restore();

  const lines = [date, ...rows.map(({ line, point }) => {
    const valueText = percentAxis ? formatPercent(point.value) : formatNumber(point.value, 3);
    const amountText = Number.isFinite(Number(point.amount)) ? ` · 资产 ${formatMoney(point.amount)}` : "";
    return `${line.label}: ${valueText}${amountText}`;
  })];
  ctx.font = "12px Arial";
  const boxWidth = Math.max(...lines.map((line) => ctx.measureText(line).width)) + 18;
  const boxHeight = lines.length * 18 + 10;
  const boxX = hover.x + boxWidth + 12 > width ? hover.x - boxWidth - 12 : hover.x + 12;
  const boxY = Math.min(Math.max(pad.top + 4, hover.y - boxHeight / 2), height - pad.bottom - boxHeight);
  ctx.fillStyle = "rgba(255,255,255,0.96)";
  ctx.strokeStyle = "rgba(101,114,130,0.35)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  roundedRect(ctx, boxX, boxY, boxWidth, boxHeight, 6);
  ctx.fill();
  ctx.stroke();
  ctx.textAlign = "left";
  ctx.textBaseline = "alphabetic";
  lines.forEach((line, idx) => {
    ctx.fillStyle = idx === 0 ? "#17202a" : "#394553";
    ctx.fillText(line, boxX + 9, boxY + 18 + idx * 18);
  });
  ctx.restore();
}

function drawBarTooltip(ctx, hit, mouseX, mouseY, width, height) {
  const lines = [`${hit.periodLabel || hit.year} · ${hit.label || hit.fundName}`, `贡献: ${formatPercent(hit.value)}`];
  ctx.save();
  ctx.font = "12px Arial";
  const boxWidth = Math.max(...lines.map((line) => ctx.measureText(line).width)) + 18;
  const boxHeight = lines.length * 18 + 10;
  const boxX = mouseX + boxWidth + 12 > width ? mouseX - boxWidth - 12 : mouseX + 12;
  const boxY = Math.min(Math.max(8, mouseY - boxHeight / 2), height - boxHeight - 8);
  ctx.fillStyle = "rgba(255,255,255,0.96)";
  ctx.strokeStyle = "rgba(101,114,130,0.35)";
  ctx.beginPath();
  roundedRect(ctx, boxX, boxY, boxWidth, boxHeight, 6);
  ctx.fill();
  ctx.stroke();
  lines.forEach((line, idx) => {
    ctx.fillStyle = idx === 0 ? "#17202a" : "#394553";
    ctx.fillText(line, boxX + 9, boxY + 18 + idx * 18);
  });
  ctx.restore();
}

function drawYAxisUnitLabel(ctx, label, pad) {
  ctx.save();
  ctx.fillStyle = "#657282";
  ctx.font = "11px Arial";
  ctx.textAlign = "right";
  ctx.textBaseline = "top";
  ctx.fillText(label, pad.left - 8, Math.max(4, pad.top - 18));
  ctx.restore();
}

function formatPercentLabel(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "";
  return (Number(value) * 100).toFixed(2);
}

function formatPercentIntegerLabel(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "";
  return String(Math.round(Number(value) * 100));
}

function drawNonOverlappingLabel(ctx, placedLabels, text, x, y, direction, bounds) {
  if (!text) return;
  const width = ctx.measureText(text).width + 6;
  const height = 12;
  let labelY = y;
  let rect = null;
  for (let attempt = 0; attempt < 8; attempt += 1) {
    labelY = Math.max(bounds.top, Math.min(bounds.bottom, labelY));
    rect = {
      left: x - width / 2,
      right: x + width / 2,
      top: labelY - height + 2,
      bottom: labelY + 2,
    };
    const overlaps = placedLabels.some((item) => (
      rect.left < item.right && rect.right > item.left && rect.top < item.bottom && rect.bottom > item.top
    ));
    if (!overlaps) break;
    labelY += direction * (height + 2);
  }
  if (rect) placedLabels.push(rect);
  ctx.fillText(text, x, labelY);
}

function drawAnnualChart(canvasId, rows, labels = { portfolio: "组合", benchmark: "基准", excess: "超额" }) {
  const canvas = document.getElementById(canvasId);
  const { ctx, width, height } = prepareCanvas(canvas, 280);
  ctx.clearRect(0, 0, width, height);
  if (!rows.length) return;

  const pad = { left: 54, right: 18, top: 44, bottom: 42 };
  const values = rows.flatMap((row) => [row.portfolio, row.benchmark, row.excess].filter((v) => v !== null));
  const min = Math.min(0, ...values);
  const max = Math.max(0, ...values);
  drawAxes(ctx, width, height, pad, min, max, true, formatPercentIntegerLabel);
  drawYAxisUnitLabel(ctx, "收益率 %", pad);
  const plotWidth = width - pad.left - pad.right;
  const barGroup = plotWidth / rows.length;
  const barWidth = Math.min(18, Math.max(8, barGroup * 0.18));
  const barOffset = barWidth * 1.35;
  const zeroY = valueToY(0, min, max, pad, height);
  const placedLabels = [];

  rows.forEach((row, idx) => {
    const cx = pad.left + idx * barGroup + barGroup / 2;
    [
      ["portfolio", colors.portfolio, -barOffset],
      ["benchmark", colors.benchmark, 0],
      ["excess", colors.excess, barOffset],
    ].forEach(([key, color, offset]) => {
      const value = row[key];
      if (value === null || value === undefined || Number.isNaN(Number(value))) return;
      const y = valueToY(value, min, max, pad, height);
      ctx.fillStyle = color;
      ctx.fillRect(cx + offset - barWidth / 2, Math.min(y, zeroY), barWidth, Math.abs(zeroY - y));
      ctx.fillStyle = "#394553";
      ctx.font = "10px Arial";
      ctx.textAlign = "center";
      drawNonOverlappingLabel(
        ctx,
        placedLabels,
        formatPercentLabel(value),
        cx + offset,
        y + (value >= 0 ? -4 : 12),
        value >= 0 ? -1 : 1,
        { top: Math.max(8, pad.top - 20), bottom: height - 12 },
      );
    });
    ctx.fillStyle = "#657282";
    ctx.font = "12px Arial";
    ctx.textAlign = "center";
    ctx.fillText(chartRowLabel(row), cx, height - 14);
  });
  drawLegend(ctx, [
    { label: labels.portfolio, color: colors.portfolio },
    { label: labels.benchmark, color: colors.benchmark },
    { label: labels.excess, color: colors.excess },
  ], pad.left, 14);
}

function drawAnnualContributionChart(canvasId, data) {
  const canvas = document.getElementById(canvasId);
  const { ctx, width, height } = prepareCanvas(canvas, 300);

  const rows = data?.rows || [];
  const groups = data?.groups || [];
  if (!rows.length || !groups.length) {
    canvas.onmousemove = null;
    canvas.onmouseleave = null;
    ctx.clearRect(0, 0, width, height);
    return;
  }

  const pad = { left: 54, right: 18, top: 56, bottom: 34 };
  const values = rows.flatMap((row) => groups.map((group) => row.values[group.id] || 0));
  const positiveMax = Math.max(0, ...rows.map((row) => groups.reduce((sum, group) => sum + Math.max(row.values[group.id] || 0, 0), 0)));
  const negativeMin = Math.min(0, ...rows.map((row) => groups.reduce((sum, group) => sum + Math.min(row.values[group.id] || 0, 0), 0)));
  const min = Math.min(negativeMin, ...values);
  const max = Math.max(positiveMax, ...values);
  const plotWidth = width - pad.left - pad.right;
  const barGroup = plotWidth / rows.length;
  const baseBarWidth = Math.min(32, Math.max(12, barGroup * 0.38));
  let segments = [];

  const draw = (highlightGroupId) => {
    segments = [];
    ctx.clearRect(0, 0, width, height);
    drawAxes(ctx, width, height, pad, min, max, true, formatPercentIntegerLabel);
    drawYAxisUnitLabel(ctx, "收益率 %", pad);
    const placedLabels = [];
    const contributionLabels = [];
    rows.forEach((row, rowIndex) => {
      const baseX = pad.left + rowIndex * barGroup + barGroup / 2 - baseBarWidth / 2;
      let posBase = 0;
      let negBase = 0;
      groups.forEach((group, groupIndex) => {
        const value = row.values[group.id] || 0;
        if (!value) return;
        const from = value >= 0 ? posBase : negBase;
        const to = from + value;
        const y1 = valueToY(from, min, max, pad, height);
        const y2 = valueToY(to, min, max, pad, height);
        const isHighlight = highlightGroupId && group.id === highlightGroupId;
        const isDimmed = highlightGroupId && group.id !== highlightGroupId;
        const grow = isHighlight ? 2 : 0;
        const x = baseX - grow;
        const y = Math.min(y1, y2) - grow;
        const h = Math.max(1, Math.abs(y2 - y1)) + grow * 2;
        const w = baseBarWidth + grow * 2;
        ctx.save();
        ctx.globalAlpha = isDimmed ? 0.22 : 1;
        ctx.fillStyle = group.color || fundPalette[groupIndex % fundPalette.length];
        ctx.fillRect(x, y, w, h);
        if (isHighlight) {
          ctx.strokeStyle = "#17202a";
          ctx.lineWidth = 1.2;
          ctx.strokeRect(x, y, w, h);
        }
        ctx.restore();
        const hitPaddingX = 4;
        const minHitHeight = 12;
        const hitHeight = Math.max(h, minHitHeight);
        segments.push({
          x,
          y,
          w,
          h,
          hitX: x - hitPaddingX,
          hitY: y + h / 2 - hitHeight / 2,
          hitW: w + hitPaddingX * 2,
          hitH: hitHeight,
          groupId: group.id,
          label: group.name,
          periodLabel: chartRowLabel(row),
          value,
        });
        if (value >= 0) posBase = to;
        else negBase = to;
      });
      const total = row.total || 0;
      const labelY = valueToY(total >= 0 ? posBase : negBase, min, max, pad, height) + (total >= 0 ? -8 : 14);
      contributionLabels.push({
        text: formatPercentLabel(total),
        x: baseX + baseBarWidth / 2,
        y: labelY,
        direction: total >= 0 ? -1 : 1,
      });
      ctx.fillStyle = "#657282";
      ctx.font = "11px Arial";
      ctx.fillText(chartRowLabel(row), baseX + baseBarWidth / 2, height - 10);
    });
    ctx.fillStyle = "#394553";
    ctx.font = "9px Arial";
    ctx.textAlign = "center";
    contributionLabels.forEach((label) => {
      drawNonOverlappingLabel(ctx, placedLabels, label.text, label.x, label.y, label.direction, {
        top: Math.max(8, pad.top - 20),
        bottom: height - 12,
      });
    });
    drawLegend(ctx, [
      ...groups.map((group, idx) => ({ label: group.name, color: group.color || fundPalette[idx % fundPalette.length] })),
    ], pad.left, 20);
  };

  draw(null);
  canvas.onmousemove = (event) => {
    const { x, y } = canvasEventPoint(canvas, event, width, height);
    const hit = segments.find((segment) => x >= segment.hitX && x <= segment.hitX + segment.hitW && y >= segment.hitY && y <= segment.hitY + segment.hitH);
    canvas.style.cursor = hit ? "pointer" : "default";
    draw(hit ? hit.groupId : null);
    if (hit) {
      drawBarTooltip(ctx, hit, x, y, width, height);
    }
  };
  canvas.onmouseleave = () => {
    canvas.style.cursor = "default";
    draw(null);
  };
}

function chartRowLabel(row) {
  return String(row.label || row.year || row.period || "");
}

function prepareCanvas(canvas, fallbackHeight) {
  if (!canvas.dataset.chartHeight) {
    canvas.dataset.chartHeight = canvas.getAttribute("height") || String(fallbackHeight);
  }
  const height = Number(canvas.dataset.chartHeight) || fallbackHeight;
  const rect = canvas.getBoundingClientRect();
  const width = rect.width || canvas.clientWidth || canvas.parentElement.clientWidth || 640;
  const dpr = window.devicePixelRatio || 1;
  canvas.style.height = `${height}px`;
  canvas.width = Math.round(width * dpr);
  canvas.height = Math.round(height * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, width, height };
}

function canvasEventPoint(canvas, event, width, height) {
  const rect = canvas.getBoundingClientRect();
  const xScale = width / (rect.width || width || 1);
  const yScale = height / (rect.height || height || 1);
  return {
    x: (event.clientX - rect.left) * xScale,
    y: (event.clientY - rect.top) * yScale,
  };
}

function drawAxes(ctx, width, height, pad, min, max, percentAxis, labelFormatter = null) {
  ctx.strokeStyle = "#dce3ea";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(pad.left, pad.top);
  ctx.lineTo(pad.left, height - pad.bottom);
  ctx.lineTo(width - pad.right, height - pad.bottom);
  ctx.stroke();

  ctx.fillStyle = "#657282";
  ctx.font = "12px Arial";
  ctx.textAlign = "right";
  const tickCount = height <= 120 ? 2 : 4;
  for (let i = 0; i <= tickCount; i += 1) {
    const value = min + (max - min) * (i / tickCount);
    const label = labelFormatter ? labelFormatter(value) : (percentAxis ? formatPercent(value) : formatNumber(value, 2));
    const y = valueToY(value, min, max, pad, height);
    ctx.strokeStyle = "#eef2f5";
    ctx.beginPath();
    ctx.moveTo(pad.left, y);
    ctx.lineTo(width - pad.right, y);
    ctx.stroke();
    ctx.fillText(label, pad.left - 8, y + 4);
  }
}

function drawMoneyAxis(ctx, width, height, pad, min, max, baseAmount) {
  if (!Number.isFinite(Number(baseAmount)) || Number(baseAmount) <= 0) return;
  const tickCount = height <= 120 ? 2 : 4;
  ctx.save();
  ctx.strokeStyle = "#dce3ea";
  ctx.fillStyle = "#657282";
  ctx.font = "12px Arial";
  ctx.textAlign = "left";
  ctx.beginPath();
  ctx.moveTo(width - pad.right, pad.top);
  ctx.lineTo(width - pad.right, height - pad.bottom);
  ctx.stroke();
  for (let i = 0; i <= tickCount; i += 1) {
    const value = min + (max - min) * (i / tickCount);
    const y = valueToY(value, min, max, pad, height);
    const amount = Number(baseAmount) * (1 + value);
    ctx.fillText(formatMoneyShort(amount), width - pad.right + 8, y + 4);
  }
  ctx.restore();
}

function drawStrongZeroAxis(ctx, width, height, pad, min, max) {
  if (min > 0 || max < 0) return;
  const zeroY = valueToY(0, min, max, pad, height);
  ctx.save();
  ctx.strokeStyle = "#111827";
  ctx.lineWidth = 1.4;
  ctx.beginPath();
  ctx.moveTo(pad.left, zeroY);
  ctx.lineTo(width - pad.right, zeroY);
  ctx.stroke();
  ctx.restore();
}

function valueToY(value, min, max, pad, height) {
  return pad.top + (1 - (value - min) / (max - min || 1)) * (height - pad.top - pad.bottom);
}

function indexToX(index, xMax, width, pad) {
  return pad.left + (index / Math.max(xMax, 1)) * (width - pad.left - pad.right);
}

function roundedRect(ctx, x, y, width, height, radius) {
  const r = Math.min(radius, width / 2, height / 2);
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + width - r, y);
  ctx.quadraticCurveTo(x + width, y, x + width, y + r);
  ctx.lineTo(x + width, y + height - r);
  ctx.quadraticCurveTo(x + width, y + height, x + width - r, y + height);
  ctx.lineTo(x + r, y + height);
  ctx.quadraticCurveTo(x, y + height, x, y + height - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
}

function drawLegend(ctx, items, x, y, maxWidth = Infinity) {
  let cursor = x;
  let rowY = y;
  (items || []).forEach((item) => {
    if (item.hideLegend) return;
    const itemWidth = ctx.measureText(item.label).width + 68;
    if (cursor > x && cursor + itemWidth > x + maxWidth) {
      cursor = x;
      rowY += 16;
    }
    ctx.fillStyle = item.color;
    ctx.fillRect(cursor, rowY - 8, 18, 3);
    ctx.fillStyle = "#394553";
    ctx.font = "12px Arial";
    ctx.textAlign = "left";
    ctx.fillText(item.label, cursor + 24, rowY - 4);
    cursor += itemWidth;
  });
}

function resetAnalysisWindow(result) {
  const total = analysisPointCount(result);
  state.analysisAttribution = null;
  state.analysisWindow = { start: 0, end: Math.max(total - 1, 0) };
  state.analysisRangePreset = "all";
  setAnalysisWindowStatus("");
  syncAnalysisRangeInputs(total, result);
}

function handleAnalysisRangeInput(event) {
  if (!state.result) return;
  const total = analysisPointCount(state.result);
  let start = Number(els.analysisRangeStart.value || 0);
  let end = Number(els.analysisRangeEnd.value || 0);
  if (start >= end) {
    if (event.target === els.analysisRangeStart) start = Math.max(0, end - 1);
    else end = Math.min(Math.max(total - 1, 0), start + 1);
  }
  state.analysisWindow = { start, end };
  state.analysisRangePreset = "";
  syncAnalysisRangeInputs(total, state.result);
}

function handleAnalysisWindowPresetClick(event) {
  const button = event.target.closest("[data-analysis-preset]");
  if (!button || !state.result) return;
  state.analysisRangePreset = button.dataset.analysisPreset || "all";
  state.analysisWindow = analysisWindowForPreset(state.result, state.analysisRangePreset);
  syncAnalysisRangeInputs(analysisPointCount(state.result), state.result);
  refreshAnalysisWindow();
}

function analysisWindowForPreset(result, preset) {
  const points = analysisBasePoints(result);
  const total = points.length;
  if (!total || preset === "all") return { start: 0, end: Math.max(total - 1, 0) };
  const latestDate = parseDate(points[total - 1]?.date);
  if (!latestDate) return { start: 0, end: Math.max(total - 1, 0) };
  const targetDate = rangePresetStartDate(latestDate, preset);
  const start = points.findIndex((point) => {
    const date = parseDate(point.date);
    return date && date >= targetDate;
  });
  return { start: start < 0 ? 0 : start, end: total - 1 };
}

function analysisPointCount(result = state.result) {
  return analysisBasePoints(result).length;
}

function analysisBasePoints(result = state.result) {
  return result?.series?.portfolio || [];
}

function syncAnalysisRangeInputs(total = analysisPointCount(), result = state.result) {
  if (!els.analysisRangeStart || !els.analysisRangeEnd) return;
  const points = analysisBasePoints(result);
  const max = Math.max(total - 1, 0);
  const start = Math.max(0, Math.min(state.analysisWindow.start, max));
  const end = Math.max(start, Math.min(state.analysisWindow.end, max));
  state.analysisWindow = { start, end };
  [els.analysisRangeStart, els.analysisRangeEnd].forEach((input) => {
    input.min = "0";
    input.max = String(max);
    input.disabled = max < 1;
  });
  els.analysisRangeStart.value = String(start);
  els.analysisRangeEnd.value = String(end);
  const startPct = max > 0 ? (start / max) * 100 : 0;
  const endPct = max > 0 ? (end / max) * 100 : 100;
  els.analysisRangeTrack.style.left = `${startPct}%`;
  els.analysisRangeTrack.style.width = `${Math.max(endPct - startPct, 0)}%`;
  els.analysisWindowStartLabel.textContent = points[start]?.date || "-";
  els.analysisWindowEndLabel.textContent = points[end]?.date || "-";
  syncAnalysisRangePresetButtons();
}

function syncAnalysisRangePresetButtons() {
  els.analysisWindowPresets?.querySelectorAll("[data-analysis-preset]").forEach((button) => {
    button.classList.toggle("active", button.dataset.analysisPreset === state.analysisRangePreset);
  });
}

function analysisWindowDates() {
  const points = analysisBasePoints(state.result);
  const start = points[state.analysisWindow.start]?.date;
  const end = points[state.analysisWindow.end]?.date;
  return { start, end };
}

function activeAnalysisResult() {
  if (!state.analysisAttribution) return state.result;
  return { ...state.result, attribution: state.analysisAttribution };
}

function setAnalysisWindowStatus(message = "") {
  if (els.analysisWindowStatus) els.analysisWindowStatus.textContent = message;
}

async function refreshAnalysisWindow() {
  if (!state.result || !state.currentPortfolio?.id) return;
  const dates = analysisWindowDates();
  if (!dates.start || !dates.end) return;
  try {
    setAnalysisWindowStatus("正在计算组合分析区间...");
    const data = await fetchJson(`/api/portfolios/${encodeURIComponent(state.currentPortfolio.id)}/analysis-window`, {
      method: "POST",
      body: JSON.stringify({
        frequency: currentDataFrequency(),
        startDate: dates.start,
        endDate: dates.end,
      }),
    });
    state.analysisAttribution = data.attribution || null;
    renderAttributionView({ ...state.result, attribution: data.attribution });
    setAnalysisWindowStatus("");
  } catch (err) {
    setAnalysisWindowStatus("");
    setStatus(err.message || String(err), "error");
  }
}

function renderAttributionView(result) {
  const attribution = result?.attribution;
  if (!attribution) {
    els.attributionWindowText.textContent = "请先运行回测。";
    els.attributionRangeText.textContent = "";
    els.attributionRows.innerHTML = `<div class="empty-state">请先运行回测</div>`;
    els.sleeveCorrelationMatrix.innerHTML = `<div class="empty-state">暂无数据</div>`;
    renderCategoryTimeline(null);
    els.sleeveFundCorrelationList.innerHTML = `<div class="empty-state">暂无数据</div>`;
    els.fundCorrelationSummary.innerHTML = `<div class="empty-state">暂无数据</div>`;
    closeFundDrawer();
    return;
  }

  const windowInfo = attribution.window || {};
  els.attributionWindowText.textContent = `${formatText(windowInfo.start)} 至 ${formatText(windowInfo.end)}`;
  els.attributionRangeText.textContent = `组合最大回撤区间：${formatText(windowInfo.drawdown_peak)} 至 ${formatText(windowInfo.drawdown_trough)}`;
  renderAttributionTable(attribution);
  renderSleeveCorrelation(attribution.correlation?.sleeves);
  renderCategoryTimeline(result?.exposure?.category_timeline);
  renderSleeveFundCorrelations(attribution.correlation?.sleeve_fund_matrices);
  renderFundCorrelationSummary(attribution.correlation);
  if (state.selectedAttributionFund && attribution.fund_details?.[state.selectedAttributionFund]) {
    openFundDrawer(state.selectedAttributionFund, attribution);
  } else {
    closeFundDrawer();
  }
}

function renderAttributionTable(attribution) {
  const rows = attribution.rows || [];
  if (!rows.length) {
    els.attributionRows.innerHTML = `<div class="empty-state">暂无归因行</div>`;
    return;
  }
  const scope = state.attributionContributionScope;
  const visibleRows = attributionVisibleRows(rows, scope);
  const barScale = contributionBarScale(visibleRows, scope);
  const contributionShareHint = scope === "subset"
    ? "该对象收益贡献占当前子集收益的比例；子集收益为负或接近 0 时不展示。"
    : "该对象收益贡献占组合总收益贡献的比例；组合总收益为负或接近 0 时不展示。";
  els.attributionRows.innerHTML = `
    <table class="attribution-table">
      <colgroup class="attribution-colgroup">
        <col class="attr-col-name">
        <col class="attr-col-weight">
        <col class="attr-col-weight">
        <col class="attr-col-return">
        <col class="attr-col-return">
        <col class="attr-col-period">
        <col class="attr-col-days">
        <col class="attr-col-days">
        <col class="attr-col-contribution">
        <col class="attr-col-share">
        <col class="attr-col-contribution">
        <col class="attr-col-contribution">
      </colgroup>
      <thead>
        <tr>
          <th colspan="3">基础信息</th>
          <th colspan="5" class="group-start">自身表现 ${tooltipIcon("自身表现按该对象自己的净值曲线计算，不等同于对组合的贡献。")}</th>
          <th colspan="4" class="group-start">
            <div class="contribution-group-head">
              <span>贡献归因</span>
              <span class="scope-toggle" role="group" aria-label="贡献归因口径">
                <button class="${scope === "portfolio" ? "active" : ""}" type="button" data-attribution-scope="portfolio">对组合</button>
                <button class="${scope === "subset" ? "active" : ""}" type="button" data-attribution-scope="subset">对子集</button>
              </span>
            </div>
          </th>
        </tr>
        <tr>
          <th>名称</th>
          <th>平均权重</th>
          <th>当前权重</th>
          <th class="group-start">区间收益</th>
          <th>最大回撤</th>
          <th>回撤区间</th>
          <th>回撤时长</th>
          <th>修复耗时</th>
          <th class="group-start">收益贡献 ${tooltipIcon("每日实际权重乘以当日收益，并沿当前口径净值路径累加。")}</th>
          <th>贡献占比 ${tooltipIcon(contributionShareHint)}</th>
          <th>波动贡献 ${tooltipIcon("用协方差分解计算的年化波动百分点贡献。")}</th>
          <th>回撤贡献 ${tooltipIcon("组合或子集回撤区间内的贡献影响。")}</th>
        </tr>
      </thead>
      <tbody>
        ${visibleRows.map((row) => attributionRowHtml(row, scope, barScale)).join("")}
      </tbody>
    </table>
  `;
}

function attributionVisibleRows(rows, scope) {
  return rows.filter((row) => {
    if (scope === "subset" && row.type === "portfolio") return false;
    return row.type !== "fund" || state.expandedAttributionSleeves.has(row.parent_id);
  });
}

function attributionRowHtml(row, scope, barScale) {
  const isFund = row.type === "fund";
  const rowAttrs = isFund ? ` data-attribution-fund="${escapeHtml(row.id)}"` : "";
  const rowClass = `attribution-row ${escapeHtml(row.type)} level-${Number(row.level || 0)}`;
  const display = attributionDisplayValues(row, scope);
  return `
    <tr class="${rowClass}"${rowAttrs}>
      <td>${attributionNameCell(row)}</td>
      <td>${formatPercent(row.average_weight)}</td>
      <td>${formatPercent(row.current_weight)}</td>
      ${percentTd(row.period_return, "group-start")}
      ${percentTd(row.max_drawdown)}
      <td>${escapeHtml(formatText(row.drawdown_period))}</td>
      <td>${escapeHtml(formatDays(row.drawdown_days))}</td>
      <td>${escapeHtml(formatRecoveryDays(row.recovery_days))}</td>
      ${contributionBarCell(display.returnContribution, barScale.returnContribution, "group-start")}
      ${percentTd(display.returnContributionShare)}
      ${contributionBarCell(display.volatilityContribution, barScale.volatilityContribution)}
      ${contributionBarCell(display.drawdownContribution, barScale.drawdownContribution)}
    </tr>
  `;
}

function attributionDisplayValues(row, scope) {
  if (scope === "subset") {
    if (row.type === "sleeve") {
      return {
        returnContribution: row.period_return,
        returnContributionShare: 1,
        volatilityContribution: row.annual_volatility,
        drawdownContribution: row.max_drawdown,
      };
    }
    if (row.type === "fund") {
      return {
        returnContribution: row.return_contribution_to_parent,
        returnContributionShare: row.return_contribution_share_to_parent,
        volatilityContribution: row.volatility_contribution_to_parent,
        drawdownContribution: row.drawdown_contribution_to_parent ?? row.drawdown_drag_to_parent,
      };
    }
  }
  return {
    returnContribution: row.return_contribution,
    returnContributionShare: row.return_contribution_share,
    volatilityContribution: row.volatility_contribution,
    drawdownContribution: row.drawdown_contribution ?? row.drawdown_drag,
  };
}

function attributionNameCell(row) {
  const name = escapeHtml(row.name || row.id);
  const code = row.code ? `<span>${escapeHtml(row.code)}</span>` : "";
  if (row.type === "sleeve") {
    const expanded = state.expandedAttributionSleeves.has(row.id);
    return `
      <button class="attribution-toggle" type="button" data-attribution-toggle="${escapeHtml(row.id)}" aria-expanded="${expanded ? "true" : "false"}">
        ${expanded ? "−" : "+"}
      </button>
      <strong>${name}</strong>${code}
    `;
  }
  if (row.type === "fund") {
    return `<strong>${name}</strong>`;
  }
  return `<strong>${name}</strong>${code}`;
}

function percentTd(value, extraClass = "") {
  const classes = [signedClass(value), extraClass].filter(Boolean).join(" ");
  return `<td class="${escapeHtml(classes)}">${formatPercent(value)}</td>`;
}

function contributionBarScale(rows, scope) {
  return {
    returnContribution: maxAbs(rows.map((row) => attributionDisplayValues(row, scope).returnContribution)),
    volatilityContribution: maxAbs(rows.map((row) => attributionDisplayValues(row, scope).volatilityContribution)),
    drawdownContribution: maxAbs(rows.map((row) => attributionDisplayValues(row, scope).drawdownContribution)),
  };
}

function maxAbs(values) {
  return values.reduce((max, value) => {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return max;
    return Math.max(max, Math.abs(Number(value)));
  }, 0);
}

function contributionBarCell(value, maxValue, extraClass = "") {
  const number = Number(value);
  const valid = value !== null && value !== undefined && !Number.isNaN(number);
  const width = valid && maxValue > 0 ? Math.max(4, Math.min(75, Math.abs(number) / maxValue * 75)) : 0;
  const classes = [signedClass(value), extraClass].filter(Boolean).join(" ");
  const tone = number < 0 ? "negative" : "positive";
  return `
    <td class="${escapeHtml(classes)}">
      <div class="contribution-cell">
        <span class="contribution-bar-track ${tone}" aria-hidden="true">
          <span class="contribution-bar ${tone}" style="width:${width}%"></span>
          <span class="contribution-value">${formatPercent(value)}</span>
        </span>
      </div>
    </td>
  `;
}

function handleAttributionTableClick(event) {
  const scopeButton = event.target.closest("[data-attribution-scope]");
  if (scopeButton) {
    state.attributionContributionScope = scopeButton.dataset.attributionScope;
    if (state.result) renderAttributionView(activeAnalysisResult());
    return;
  }

  const toggle = event.target.closest("[data-attribution-toggle]");
  if (toggle) {
    const sleeveId = toggle.dataset.attributionToggle;
    if (state.expandedAttributionSleeves.has(sleeveId)) state.expandedAttributionSleeves.delete(sleeveId);
    else state.expandedAttributionSleeves.add(sleeveId);
    if (state.result) renderAttributionView(activeAnalysisResult());
    return;
  }

  const row = event.target.closest("[data-attribution-fund]");
  if (row && state.result?.attribution) {
    openFundDrawer(row.dataset.attributionFund, state.result.attribution);
  }
}

function renderSleeveCorrelation(correlation) {
  if (!correlation || !(correlation.labels || []).length) {
    els.sleeveCorrelationMatrix.innerHTML = `<div class="empty-state">暂无子组合相关性</div>`;
    return;
  }
  els.sleeveCorrelationMatrix.innerHTML = `
    <div class="correlation-summary-line">平均相关系数 <strong>${formatCorrelation(correlation.average)}</strong></div>
    ${correlationMatrixHtml(correlation.labels, correlation.matrix, "sleeve")}
  `;
}

function renderCategoryTimeline(timeline) {
  const valid = timeline && Array.isArray(timeline.years) && timeline.years.length && Array.isArray(timeline.series) && timeline.series.length;
  if (!valid) {
    if (els.categoryTimelineLegend) els.categoryTimelineLegend.innerHTML = "";
    const canvas = els.categoryTimelineChart;
    if (canvas) {
      const { ctx, width, height } = prepareCanvas(canvas, 260);
      ctx.clearRect(0, 0, width, height);
      ctx.fillStyle = "#657282";
      ctx.font = "12px Arial";
      ctx.textAlign = "center";
      ctx.fillText("暂无大类配置时序数据", width / 2, height / 2);
      canvas.onmousemove = null;
      canvas.onmouseleave = null;
    }
    return;
  }
  if (state.categoryTimelineFocus && !(timeline.series || []).some((item) => item.name === state.categoryTimelineFocus)) {
    state.categoryTimelineFocus = null;
  }
  renderCategoryTimelineLegend(timeline);
  drawCategoryTimelineChart(timeline, null, state.categoryTimelineFocus);
}

function categoryTimelinePalette(name) {
  return {
    TMT: "#a371f7",
    制造: "#58a6ff",
    周期: "#3fb950",
    消费: "#d29922",
    金融: "#f85149",
  }[name] || "#8b949e";
}

function renderCategoryTimelineLegend(timeline) {
  if (!els.categoryTimelineLegend) return;
  els.categoryTimelineLegend.innerHTML = (timeline.series || []).map((item) => {
    const color = item.color || categoryTimelinePalette(item.name);
    return `
      <button type="button" data-category-timeline-focus="${escapeHtml(item.name)}">
        <i style="border-color:${color}"></i><b style="background:${color}"></b><span>${escapeHtml(item.name)}</span>
      </button>
    `;
  }).join("");
  updateCategoryTimelineLegendState(state.categoryTimelineFocus);
  els.categoryTimelineLegend.querySelectorAll("[data-category-timeline-focus]").forEach((button) => {
    button.addEventListener("mouseenter", () => {
      updateCategoryTimelineLegendState(button.dataset.categoryTimelineFocus);
      drawCategoryTimelineChart(timeline, null, button.dataset.categoryTimelineFocus);
    });
    button.addEventListener("mouseleave", () => {
      updateCategoryTimelineLegendState(null);
      drawCategoryTimelineChart(timeline, null, null);
    });
  });
}

function updateCategoryTimelineLegendState(focusName) {
  state.categoryTimelineFocus = focusName;
  if (!els.categoryTimelineLegend) return;
  els.categoryTimelineLegend.querySelectorAll("[data-category-timeline-focus]").forEach((button) => {
    button.classList.toggle("active", !focusName || button.dataset.categoryTimelineFocus === focusName);
  });
}

function drawSmoothCategoryLine(ctx, points, move = true) {
  if (!points.length) return;
  if (move) ctx.moveTo(points[0].x, points[0].y);
  else ctx.lineTo(points[0].x, points[0].y);
  if (points.length === 1) return;
  const tension = 0.18;
  for (let index = 0; index < points.length - 1; index += 1) {
    const current = points[index];
    const next = points[index + 1];
    const dx = (next.x - current.x) * tension;
    ctx.bezierCurveTo(current.x + dx, current.y, next.x - dx, next.y, next.x, next.y);
  }
}

function fillSmoothCategoryArea(ctx, topPoints, bottomPoints) {
  drawSmoothCategoryLine(ctx, topPoints);
  drawSmoothCategoryLine(ctx, [...bottomPoints].reverse(), false);
  ctx.closePath();
}

function drawCategoryPointLabels(ctx, points, values, color, pad) {
  if (!points.length) return;
  ctx.save();
  ctx.font = "11px Arial";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  points.forEach((point, index) => {
    const text = formatPercent(Number(values[index] || 0));
    const aboveY = point.y - 15;
    const labelY = aboveY < pad.top ? point.y + 15 : aboveY;
    ctx.lineWidth = 3;
    ctx.strokeStyle = "rgba(255, 255, 255, 0.78)";
    ctx.strokeText(text, point.x, labelY);
    ctx.fillStyle = "rgba(23, 32, 42, 0.86)";
    ctx.fillText(text, point.x, labelY);
  });
  ctx.restore();
}

function drawCategoryTimelineChart(timeline, hoverIndex = null, focusName = state.categoryTimelineFocus) {
  const canvas = els.categoryTimelineChart;
  if (!canvas) return;
  const { ctx, width, height } = prepareCanvas(canvas, 260);
  const years = timeline.years || [];
  const series = timeline.series || [];
  const pad = { left: 56, right: 16, top: 36, bottom: 36 };
  const xMax = Math.max(years.length - 1, 1);
  const plotWidth = width - pad.left - pad.right;
  const plotHeight = height - pad.top - pad.bottom;
  const xAt = (index) => pad.left + (index / xMax) * plotWidth;
  const yAt = (value) => pad.top + (1 - Math.max(0, Math.min(1, value))) * plotHeight;
  const focusedSeries = focusName ? series.find((item) => item.name === focusName) : null;
  const stacks = years.map((_, index) => {
    let base = 0;
    return series.map((item) => {
      const value = Math.max(0, Number(item.values?.[index] || 0));
      const entry = { name: item.name, color: item.color || categoryTimelinePalette(item.name), value, bottom: base, top: base + value };
      base += value;
      return entry;
    });
  });

  ctx.clearRect(0, 0, width, height);
  ctx.save();
  ctx.strokeStyle = "#e5e7eb";
  ctx.fillStyle = "#657282";
  ctx.font = "11px Arial";
  ctx.textAlign = "right";
  ctx.textBaseline = "middle";
  for (let tick = 0; tick <= 5; tick += 1) {
    const value = tick / 5;
    const y = yAt(value);
    ctx.beginPath();
    ctx.moveTo(pad.left, y);
    ctx.lineTo(width - pad.right, y);
    ctx.stroke();
    ctx.fillText(`${Math.round(value * 100)}%`, pad.left - 8, y);
  }
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  years.forEach((year, index) => {
    ctx.fillText(String(year), xAt(index), height - pad.bottom + 9);
  });
  ctx.fillText("年度", pad.left + plotWidth / 2, height - 14);
  ctx.textAlign = "left";
  ctx.textBaseline = "alphabetic";
  ctx.fillText("占基金净值比 (%)", pad.left, 14);

  if (focusedSeries) {
    const color = focusedSeries.color || categoryTimelinePalette(focusedSeries.name);
    const topPoints = years.map((_, index) => ({
      x: xAt(index),
      y: yAt(Math.max(0, Number(focusedSeries.values?.[index] || 0))),
    }));
    const bottomPoints = years.map((_, index) => ({ x: xAt(index), y: yAt(0) }));
    ctx.beginPath();
    fillSmoothCategoryArea(ctx, topPoints, bottomPoints);
    ctx.globalAlpha = 0.28;
    ctx.fillStyle = color;
    ctx.fill();
    ctx.globalAlpha = 1;
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.lineWidth = 2;
    ctx.beginPath();
    drawSmoothCategoryLine(ctx, topPoints);
    ctx.stroke();
    topPoints.forEach((point) => {
      ctx.beginPath();
      ctx.arc(point.x, point.y, 2.4, 0, Math.PI * 2);
      ctx.fill();
    });
    drawCategoryPointLabels(ctx, topPoints, focusedSeries.values || [], color, pad);
  } else {
    series.forEach((item, seriesIndex) => {
      const color = item.color || categoryTimelinePalette(item.name);
      const topPoints = years.map((_, index) => ({
        x: xAt(index),
        y: yAt(stacks[index][seriesIndex].top),
      }));
      const bottomPoints = years.map((_, index) => ({
        x: xAt(index),
        y: yAt(stacks[index][seriesIndex].bottom),
      }));
      ctx.beginPath();
      fillSmoothCategoryArea(ctx, topPoints, bottomPoints);
      ctx.globalAlpha = 0.24;
      ctx.fillStyle = color;
      ctx.fill();
      ctx.globalAlpha = 1;
      ctx.strokeStyle = color;
      ctx.fillStyle = color;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      drawSmoothCategoryLine(ctx, topPoints);
      ctx.stroke();
      topPoints.forEach((point) => {
        ctx.beginPath();
        ctx.arc(point.x, point.y, 1.8, 0, Math.PI * 2);
        ctx.fill();
      });
    });
  }
  ctx.restore();

  if (hoverIndex !== null && years[hoverIndex] !== undefined) {
    drawCategoryTimelineTooltip(ctx, timeline, stacks, hoverIndex, xAt(hoverIndex), width, height, pad, focusName);
  }

  els.categoryTimelineChart.onmousemove = (event) => {
    const { x, y } = canvasEventPoint(canvas, event, width, height);
    if (x < pad.left || x > width - pad.right || y < pad.top || y > height - pad.bottom) {
      drawCategoryTimelineChart(timeline, null, state.categoryTimelineFocus);
      return;
    }
    const index = Math.max(0, Math.min(years.length - 1, Math.round(((x - pad.left) / plotWidth) * xMax)));
    drawCategoryTimelineChart(timeline, index, state.categoryTimelineFocus);
  };
  els.categoryTimelineChart.onmouseleave = () => drawCategoryTimelineChart(timeline, null, state.categoryTimelineFocus);
}

function drawCategoryTimelineTooltip(ctx, timeline, stacks, index, x, width, height, pad, focusName = null) {
  ctx.save();
  ctx.strokeStyle = "rgba(23,32,42,0.35)";
  ctx.setLineDash([4, 4]);
  ctx.beginPath();
  ctx.moveTo(x, pad.top);
  ctx.lineTo(x, height - pad.bottom);
  ctx.stroke();
  ctx.setLineDash([]);
  const rows = (timeline.series || [])
    .map((item, seriesIndex) => ({
      name: item.name,
      color: item.color || categoryTimelinePalette(item.name),
      value: focusName ? Math.max(0, Number(item.values?.[index] || 0)) : (stacks[index][seriesIndex]?.value || 0),
    }))
    .filter((item) => !focusName || item.name === focusName);
  const lines = [String(timeline.years[index]), ...rows.map((row) => `${row.name}: ${formatPercent(row.value)}`)];
  ctx.font = "12px Arial";
  const boxWidth = Math.max(...lines.map((line) => ctx.measureText(line).width)) + 22;
  const boxHeight = lines.length * 18 + 12;
  const boxX = x + boxWidth + 12 > width ? x - boxWidth - 12 : x + 12;
  const boxY = Math.max(pad.top + 4, Math.min(height - pad.bottom - boxHeight, pad.top + 14));
  ctx.fillStyle = "rgba(255,255,255,0.96)";
  ctx.strokeStyle = "#dce3ea";
  ctx.lineWidth = 1;
  roundedRect(ctx, boxX, boxY, boxWidth, boxHeight, 8);
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = "#17202a";
  ctx.textAlign = "left";
  ctx.textBaseline = "top";
  lines.forEach((line, rowIndex) => {
    ctx.fillStyle = rowIndex === 0 ? "#17202a" : rows[rowIndex - 1].color;
    ctx.fillText(line, boxX + 11, boxY + 8 + rowIndex * 18);
  });
  ctx.restore();
}

function renderSleeveFundCorrelations(items) {
  const matrices = Array.isArray(items) ? items : [];
  if (!matrices.length) {
    els.sleeveFundCorrelationList.innerHTML = `<div class="empty-state">暂无子集合内部相关性</div>`;
    return;
  }
  els.sleeveFundCorrelationList.innerHTML = matrices.map((item) => `
    <div class="sleeve-fund-correlation-row">
      <div class="sleeve-fund-correlation-meta">
        <strong>${escapeHtml(item.name || item.id || "子集合")}</strong>
        <span>平均相关系数 ${formatCorrelation(item.average)}</span>
      </div>
      ${correlationMatrixHtml(item.labels || [], item.matrix || [], "tight")}
    </div>
  `).join("");
}

function renderFundCorrelationSummary(correlation) {
  const summary = correlation?.fund_summary;
  if (!summary) {
    els.fundCorrelationSummary.innerHTML = `<div class="empty-state">暂无基金互补性数据</div>`;
    return;
  }
  els.fundCorrelationSummary.innerHTML = `
    <div class="fund-matrix-block">
      ${correlationMatrixHtml(correlation.fund_matrix?.labels || [], correlation.fund_matrix?.matrix || [])}
    </div>
  `;
}

function correlationMatrixHtml(labels, matrix, variant = "") {
  if (!labels.length || !matrix.length) return `<div class="empty-state">暂无矩阵数据</div>`;
  const variantClass = variant ? ` ${escapeHtml(variant)}` : "";
  const columnWidth = correlationColumnWidth(labels, variant);
  const minColumnWidth = variant === "tight" ? 38 : variant === "compact" ? 44 : 56;
  const minWidth = Math.max(variant === "tight" ? 0 : 420, (labels.length + 1) * Math.max(minColumnWidth, columnWidth));
  return `
    <div class="correlation-matrix-wrap${variantClass}">
      <table class="correlation-matrix${variantClass}" style="--correlation-col-width:${columnWidth}px;min-width:${minWidth}px">
        <colgroup>
          <col class="correlation-label-col">
          ${labels.map(() => `<col class="correlation-value-col">`).join("")}
        </colgroup>
        <thead>
          <tr><th></th>${labels.map((label) => `<th><span class="correlation-label-text">${escapeHtml(label)}</span></th>`).join("")}</tr>
        </thead>
        <tbody>
          ${matrix.map((row, rowIndex) => `
            <tr>
              <th><span class="correlation-label-text">${escapeHtml(labels[rowIndex] || "")}</span></th>
              ${row.map((value) => `<td style="background:${correlationColor(value)}">${formatCorrelation(value)}</td>`).join("")}
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function correlationColumnWidth(labels, variant = "") {
  const base = variant === "tight" ? 38 : variant === "compact" ? 44 : 56;
  const max = variant === "tight" ? 128 : variant === "compact" ? 144 : 168;
  const charWidth = variant === "tight" ? 11 : 12;
  const longest = labels.reduce((length, label) => Math.max(length, [...String(label || "")].length), 0);
  const estimated = Math.ceil(longest / 2) * charWidth + 18;
  return Math.min(max, Math.max(base, estimated));
}

function correlationColor(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "#f8fafc";
  const alpha = 0.12 + Math.min(Math.abs(Number(value)), 1) * 0.62;
  return Number(value) >= 0 ? `rgba(201, 74, 74, ${alpha})` : `rgba(68, 178, 76, ${alpha})`;
}

function openFundDrawer(fundId, attribution) {
  const row = (attribution.rows || []).find((item) => item.id === fundId && item.type === "fund");
  const detail = attribution.fund_details?.[fundId];
  if (!row || !detail) return;
  state.selectedAttributionFund = fundId;
  els.fundDrawerTitle.textContent = row.name || fundId;
  els.fundDrawerCode.textContent = row.code || fundId;
  els.fundDrawerBody.innerHTML = fundDrawerHtml(detail, row);
  els.fundAttributionDrawer.classList.remove("hidden");
}

function closeFundDrawer() {
  state.selectedAttributionFund = null;
  els.fundAttributionDrawer.classList.add("hidden");
}

function fundDrawerHtml(detail, row) {
  const self = detail.self || {};
  const impact = detail.impact || {};
  const annual = detail.annual || {};
  const scope = state.attributionContributionScope;
  const display = attributionDisplayValues(row, scope);
  const scopeLabelText = scope === "subset" ? "对子集" : "对组合";
  return `
    <section>
      <h4>基金自身表现</h4>
      <div class="drawer-metric-grid">
        ${drawerMetricHtml("区间收益", formatPercent(self.period_return))}
        ${drawerMetricHtml("最大回撤", formatPercent(self.max_drawdown))}
        ${drawerMetricHtml("回撤区间", formatText(self.drawdown_period))}
        ${drawerMetricHtml("回撤时长", formatDays(self.drawdown_days))}
        ${drawerMetricHtml("修复耗时", formatRecoveryDays(self.recovery_days))}
      </div>
    </section>
    <section>
      <h4>组合影响</h4>
      <div class="drawer-metric-grid">
        ${drawerMetricHtml("贡献口径", scopeLabelText)}
        ${drawerMetricHtml("平均权重", formatPercent(impact.average_weight))}
        ${drawerMetricHtml("当前权重", formatPercent(impact.current_weight))}
        ${drawerMetricHtml("收益贡献", formatPercent(display.returnContribution))}
        ${drawerMetricHtml("波动贡献", formatPercent(display.volatilityContribution))}
        ${drawerMetricHtml("回撤贡献", formatPercent(display.drawdownContribution))}
      </div>
    </section>
    <section>
      <h4>年度摘要</h4>
      <div class="drawer-metric-grid">
        ${drawerMetricHtml("最好贡献年份", annualContributionLabel(annual.best_year))}
        ${drawerMetricHtml("最差贡献年份", annualContributionLabel(annual.worst_year))}
        ${drawerMetricHtml("正贡献年份数", formatCount(annual.positive_years))}
        ${drawerMetricHtml("负贡献年份数", formatCount(annual.negative_years))}
      </div>
    </section>
  `;
}

function renderExposureView(result) {
  const exposure = result?.exposure;
  if (!exposure) {
    els.exposureWindowText.textContent = "请先运行回测。";
    els.exposureDatePill.textContent = "画像日期 -";
    els.exposureBreakdownSections.innerHTML = `<div class="empty-state">请先运行回测</div>`;
    els.exposureFactorBars.innerHTML = `<div class="empty-state">请先运行回测</div>`;
    els.exposureFactorRows.innerHTML = `<div class="empty-state">暂无风格数据</div>`;
    els.exposureHistoryTables.innerHTML = `<div class="empty-state">请先运行回测</div>`;
    closeExposureFundDrawer();
    return;
  }

  els.exposureWindowText.innerHTML = `基于最新披露持仓与画像数据，A股口径，港股暂不纳入穿透统计${exposureCoverageWarningHtml(exposure)}`;
  els.exposureDatePill.textContent = `画像日期 ${formatText(exposure.snapshot_date)} · 持仓报告期 ${formatText(exposure.holding_report_date)}`;
  renderExposureBreakdownSections(exposure);
  renderExposureFactorBars(exposure);
  renderExposureFactorRows(exposure);
  renderExposureHistoryTables(exposure);
  if (state.selectedExposureFund && exposure.fund_details?.[state.selectedExposureFund]) {
    openExposureFundDrawer(state.selectedExposureFund, exposure);
  } else {
    closeExposureFundDrawer();
  }
}

function exposureCoverageWarningHtml(exposure) {
  const coverage = exposure.coverage || {};
  const labels = { style: "风格画像", industry: "行业画像", disclosure: "持仓披露" };
  const gaps = Object.entries(coverage).filter(([, item]) => (item?.missing_funds || []).length);
  if (!gaps.length) return "";
  const detail = gaps.map(([key, item]) => {
    const names = (item.missing_funds || []).slice(0, 3).map((fund) => fund.name || fund.id).join("、");
    const suffix = (item.missing_funds || []).length > 3 ? "等" : "";
    return `${labels[key] || key}覆盖${formatPercent(item.coverage_ratio)}，缺少${names}${suffix}`;
  }).join("；");
  return `<span class="exposure-coverage-warning">部分基金缺少画像或披露数据，当前结果按可用基金归一计算：${escapeHtml(detail)}</span>`;
}

function renderExposureBreakdownSections(exposure) {
  const sections = exposureBreakdownSections(exposure);
  if (!sections.length) {
    els.exposureBreakdownSections.innerHTML = `<div class="empty-state">暂无行业和持仓穿透数据</div>`;
    return;
  }
  els.exposureBreakdownSections.innerHTML = sections.map((section) => {
    const scopes = exposureBreakdownScopes(exposure, section);
    const scopeId = selectedExposureBreakdownScope(section, scopes);
    const items = scopedExposureBreakdownItems(section, scopeId);
    const scopedSection = { ...section, items };
    const selectedKey = selectedExposureBreakdownKey(scopedSection, scopeId);
    const selectedItem = items.find((item) => item.key === selectedKey) || items[0] || null;
    return `
      <article class="attribution-panel exposure-panel exposure-breakdown-section" data-exposure-breakdown-section="${escapeHtml(section.id)}">
        <div class="table-title exposure-breakdown-title-row">
          <div>
            <h3>${escapeHtml(section.title || "-")}</h3>
          </div>
          ${exposureBreakdownDetailTitle(selectedItem)}
        </div>
        <div class="exposure-breakdown-layout">
          <section class="exposure-pie-panel">
            <div class="exposure-pie-toolbar">
              ${exposureBreakdownScopeButtons(section, scopes, scopeId)}
            </div>
            <div class="exposure-pie-content">
              ${exposurePieSvg(scopedSection, selectedItem, scopeId)}
              ${exposurePieLegend(scopedSection, selectedItem, scopeId)}
            </div>
          </section>
          <section class="exposure-breakdown-detail">
            ${renderExposureBreakdownDetail(scopedSection, selectedItem)}
          </section>
        </div>
      </article>
    `;
  }).join("");
}

function exposureBreakdownSections(exposure) {
  if (Array.isArray(exposure.breakdown_sections) && exposure.breakdown_sections.length) {
    return exposure.breakdown_sections;
  }
  return legacyExposureBreakdownSections(exposure);
}

function exposureBreakdownScopes(exposure, section = null) {
  if (section?.scopes) {
    const scopeIds = Object.keys(section.scopes || {});
    const scoped = scopeIds
      .filter((scopeId) => Array.isArray(section.scopes[scopeId]))
      .map((scopeId) => ({ id: String(scopeId), name: exposureScopeLabel({ id: scopeId }) }));
    if (scoped.length) return scoped;
  }
  const scopes = exposure.drilldown?.scopes || [];
  const normalized = scopes
    .filter((scope) => scope?.id)
    .map((scope) => ({ id: String(scope.id), name: exposureScopeLabel(scope) }));
  return normalized.length ? normalized : [{ id: "portfolio", name: "组合" }];
}

function exposureScopeLabel(scope) {
  if (!scope || scope.id === "portfolio") return "组合";
  return scope.name || sleeveDefs.find((sleeve) => sleeve.id === scope.id)?.name || scope.id;
}

function selectedExposureBreakdownScope(section, scopes) {
  const current = state.selectedExposureScopes?.[section.id];
  if (current && scopes.some((scope) => scope.id === current)) return current;
  state.selectedExposureScopes[section.id] = "portfolio";
  return "portfolio";
}

function scopedExposureBreakdownItems(section, scopeId) {
  if (section.scopes && Array.isArray(section.scopes[scopeId])) return section.scopes[scopeId];
  if (scopeId === "portfolio") return section.items || [];
  return [];
}

function exposureBreakdownScopeButtons(section, scopes, activeScopeId) {
  if (scopes.length <= 1) return "";
  return `
    <div class="exposure-scope-toggle" role="group" aria-label="${escapeHtml(section.title || "暴露")}范围">
      ${scopes.map((scope) => `
        <button class="${scope.id === activeScopeId ? "active" : ""}" type="button" data-exposure-breakdown-section="${escapeHtml(section.id)}" data-exposure-breakdown-scope-select="${escapeHtml(scope.id)}">
          ${escapeHtml(scope.name)}
        </button>
      `).join("")}
    </div>
  `;
}

function legacyExposureBreakdownSections(exposure) {
  const scopeIds = legacyExposureScopeIds(exposure);
  const l1Items = legacyL1ItemsForScope(exposure, "portfolio");
  const categoryItems = legacyCategoryItemsFromL1(l1Items);
  const l2Rows = legacyRowsForScope(exposure, "l2_by_scope", "portfolio");
  const holdingRows = legacyRowsForScope(exposure, "holdings_by_scope", "portfolio");
  const sections = [];
  if (categoryItems.length) {
    sections.push({
      id: "category",
      title: "大类",
      items: categoryItems,
      scopes: legacyScopedBreakdownItems(scopeIds, (scopeId) => legacyCategoryItemsFromL1(legacyL1ItemsForScope(exposure, scopeId))),
    });
  }
  if (l1Items.length) {
    sections.push({
      id: "l1",
      title: "一级行业",
      items: l1Items,
      scopes: legacyScopedBreakdownItems(scopeIds, (scopeId) => legacyL1ItemsForScope(exposure, scopeId)),
    });
  }
  if (l2Rows.length) {
    const l2Mapper = (row) => ({
      key: `${row.l1 || "-"}::${row.industry || "-"}`,
      label: row.industry || "-",
      l1: row.l1 || "-",
      weight: Number(row.weight || 0),
      sources: legacySourceRows(row.main_funds || []),
    });
    sections.push({
      id: "l2",
      title: "二级行业",
      items: legacyLimitedBreakdownItems(l2Rows, 20, l2Mapper),
      scopes: legacyScopedBreakdownItems(scopeIds, (scopeId) => legacyLimitedBreakdownItems(legacyRowsForScope(exposure, "l2_by_scope", scopeId), 20, l2Mapper)),
    });
  }
  if (holdingRows.length) {
    const holdingMapper = (row) => ({
      key: row.code || row.name || "-",
      label: row.name || row.code || "-",
      code: row.code || "-",
      l1: row.l1 || "-",
      l2: row.l2 || "-",
      weight: Number(row.weight || 0),
      sources: row.main_sleeve ? [{ name: row.main_sleeve, sleeve: row.main_sleeve, contribution: Number(row.weight || 0), legacy: true }] : [],
    });
    sections.push({
      id: "stock",
      title: "个股",
      items: legacyLimitedBreakdownItems(holdingRows, 10, holdingMapper),
      scopes: legacyScopedBreakdownItems(scopeIds, (scopeId) => legacyLimitedBreakdownItems(legacyRowsForScope(exposure, "holdings_by_scope", scopeId), 10, holdingMapper)),
    });
  }
  return sections;
}

function legacyExposureScopeIds(exposure) {
  const scopes = exposure.drilldown?.scopes || [];
  const ids = scopes.map((scope) => String(scope.id || "")).filter(Boolean);
  return ids.length ? ids : ["portfolio"];
}

function legacyScopedBreakdownItems(scopeIds, builder) {
  return Object.fromEntries(scopeIds.map((scopeId) => [scopeId, builder(scopeId)]));
}

function legacyL1ItemsForScope(exposure, scopeId) {
  const matrixRows = exposure.industry_l1?.matrix_rows || [];
  if (matrixRows.length) {
    return matrixRows
      .map((row) => [row.industry, row[scopeId]])
      .filter(([, value]) => value !== null && value !== undefined && !Number.isNaN(Number(value)) && Number(value) > 0)
      .sort((left, right) => Number(right[1]) - Number(left[1]))
      .map(([label, value]) => ({ key: label, label, weight: Number(value), sources: [], legacy: true }));
  }
  if (scopeId !== "portfolio") return [];
  const l1Portfolio = exposure.industry_l1?.portfolio || {};
  return Object.entries(l1Portfolio)
    .filter(([, value]) => value !== null && value !== undefined && !Number.isNaN(Number(value)) && Number(value) > 0)
    .sort((left, right) => Number(right[1]) - Number(left[1]))
    .map(([label, value]) => ({ key: label, label, weight: Number(value), sources: [], legacy: true }));
}

function legacyRowsForScope(exposure, bucket, scopeId) {
  return exposure.drilldown?.[bucket]?.[scopeId] || [];
}

function legacyCategoryItemsFromL1(l1Items) {
  const grouped = new Map();
  l1Items.forEach((item) => {
    const category = industryCategoryMap[item.label] || "制造";
    const existing = grouped.get(category) || { key: category, label: category, weight: 0, sources: [], legacy: true };
    existing.weight += Number(item.weight || 0);
    grouped.set(category, existing);
  });
  return [...grouped.values()].sort((left, right) => Number(right.weight || 0) - Number(left.weight || 0));
}

function legacyLimitedBreakdownItems(rows, limit, mapper) {
  const items = rows
    .map(mapper)
    .filter((item) => Number(item.weight) > 0)
    .sort((left, right) => Number(right.weight || 0) - Number(left.weight || 0));
  if (items.length <= limit) return items;
  const visible = items.slice(0, limit);
  const tail = items.slice(limit);
  const tailWeight = tail.reduce((sum, item) => sum + Number(item.weight || 0), 0);
  visible.push({
    key: "other",
    label: "其他",
    weight: tailWeight,
    is_other: true,
    sources: legacyMergedSources(tail),
  });
  return visible;
}

function legacySourceRows(items) {
  return items.map((item) => ({
    fund_id: item.id,
    name: item.name || item.id || "-",
    sleeve: item.sleeve || "-",
    contribution: Number(item.weight || 0),
    legacy: true,
  }));
}

function legacyMergedSources(items) {
  const sourceMap = new Map();
  items.forEach((item) => {
    (item.sources || []).forEach((source) => {
      const key = source.fund_id || source.sleeve || source.name || "其他";
      const existing = sourceMap.get(key) || { ...source, contribution: 0 };
      existing.contribution += Number(source.contribution || 0);
      sourceMap.set(key, existing);
    });
  });
  return [...sourceMap.values()].sort((left, right) => Number(right.contribution || 0) - Number(left.contribution || 0));
}

function exposureBreakdownStateKey(sectionId, scopeId) {
  return `${sectionId}:${scopeId || "portfolio"}`;
}

function selectedExposureBreakdownKey(section, scopeId = "portfolio") {
  const items = section.items || [];
  const stateKey = exposureBreakdownStateKey(section.id, scopeId);
  const current = state.selectedExposureBreakdowns?.[stateKey];
  if (current && items.some((item) => item.key === current)) return current;
  const fallback = items[0]?.key || "";
  if (fallback) state.selectedExposureBreakdowns[stateKey] = fallback;
  return fallback;
}

function exposurePieSvg(section, selectedItem, scopeId = "portfolio") {
  const items = (section.items || []).filter((item) => Number(item.weight) > 0);
  const total = items.reduce((sum, item) => sum + Number(item.weight || 0), 0);
  if (!items.length || total <= 0) return `<div class="empty-state">暂无分布数据</div>`;
  let offset = 0;
  const circles = items.map((item, index) => {
    const pct = Number(item.weight || 0) / total * 100;
    const dash = `${pct} ${Math.max(0, 100 - pct)}`;
    const dashOffset = 25 - offset;
    offset += pct;
    const selected = selectedItem?.key === item.key;
    return `
      <circle
        class="exposure-pie-slice ${selected ? "active" : ""}"
        cx="50" cy="50" r="36"
        fill="none"
        stroke="${exposurePieColor(index)}"
        stroke-width="${selected ? 19 : 16}"
        stroke-dasharray="${dash}"
        stroke-dashoffset="${dashOffset}"
        pathLength="100"
        data-exposure-breakdown-section="${escapeHtml(section.id)}"
        data-exposure-breakdown-scope="${escapeHtml(scopeId)}"
        data-exposure-breakdown-item="${escapeHtml(item.key)}"
        data-exposure-breakdown-trigger="pie"
      ></circle>
    `;
  }).join("");
  return `
    <svg class="exposure-pie" viewBox="0 0 100 100" role="img" aria-label="${escapeHtml(section.title || "穿透分布")}">
      <circle cx="50" cy="50" r="28" fill="#ffffff"></circle>
      ${circles}
      <text x="50" y="48" text-anchor="middle">${escapeHtml(selectedItem?.label || "-")}</text>
      <text x="50" y="62" text-anchor="middle">${formatPercent(selectedItem?.weight)}</text>
    </svg>
  `;
}

function exposurePieLegend(section, selectedItem, scopeId = "portfolio") {
  const items = section.items || [];
  return `
    <div class="exposure-pie-legend">
      ${items.map((item, index) => `
        <button class="${selectedItem?.key === item.key ? "active" : ""}" type="button" data-exposure-breakdown-section="${escapeHtml(section.id)}" data-exposure-breakdown-scope="${escapeHtml(scopeId)}" data-exposure-breakdown-item="${escapeHtml(item.key)}" data-exposure-breakdown-trigger="legend">
          <i style="background:${exposurePieColor(index)}"></i>
          <span>${escapeHtml(item.label || "-")}</span>
          <strong>${formatPercent(item.weight)}</strong>
        </button>
      `).join("")}
    </div>
  `;
}

function renderExposureBreakdownDetail(section, item) {
  if (!item) return `<div class="empty-state">暂无可查看项</div>`;
  const sources = item.sources || [];
  return `
    <div class="exposure-source-table-wrap">
      <table class="exposure-source-table">
        <colgroup class="exposure-source-colgroup">
          <col class="exposure-source-fund-col">
          <col class="exposure-source-sleeve-col">
          <col class="exposure-source-contribution-col">
        </colgroup>
        <thead>
          <tr>
            <th>基金</th>
            <th>子组合</th>
            <th>组合内贡献</th>
          </tr>
        </thead>
        <tbody>
          ${sources.map((source) => `
            <tr>
              <td>${source.fund_id ? `<button class="text-link" type="button" data-exposure-fund="${escapeHtml(source.fund_id)}">${escapeHtml(source.name || "-")}</button>` : escapeHtml(source.name || "-")}</td>
              <td>${escapeHtml(source.sleeve || "-")}</td>
              <td class="numeric-cell">${formatPercent(source.contribution)}</td>
            </tr>
          `).join("") || `<tr><td colspan="3" class="empty-cell">暂无来源基金</td></tr>`}
        </tbody>
      </table>
    </div>
  `;
}

function exposureBreakdownDetailTitle(item) {
  return `<div class="breakdown-detail-title"><strong>${escapeHtml(item?.label || "-")}</strong></div>`;
}

function exposurePieColor(index) {
  const palette = ["#2f6f9f", "#c94a4a", "#357a5b", "#b78318", "#7758a6", "#d06f3c", "#4f7896", "#8d6e63", "#00897b", "#5c6bc0", "#9f6b3f", "#6a7f2c"];
  return palette[index % palette.length];
}

function renderExposureFactorBars(exposure) {
  const portfolio = (exposure.factor_rows || []).find((row) => row.id === "portfolio");
  const names = exposure.factor_names || [];
  const values = portfolio?.values || {};
  const sortedNames = sortedFactorNames(names, values);
  const maxValue = maxAbs(sortedNames.map((name) => values[name]));
  els.exposureFactorBars.innerHTML = sortedNames
    .map((name) => factorBarHtml(name, values[name], maxValue))
    .join("") || `<div class="empty-state">暂无风格暴露</div>`;
}

function factorDisplayName(name) {
  return factorLabels[name] || name;
}

function sortedFactorNames(names, values) {
  return [...(names || [])].sort((left, right) => {
    const leftValue = Number(values?.[left]);
    const rightValue = Number(values?.[right]);
    const leftValid = Number.isFinite(leftValue);
    const rightValid = Number.isFinite(rightValue);
    if (leftValid && rightValid) return rightValue - leftValue;
    if (leftValid) return -1;
    if (rightValid) return 1;
    return String(left).localeCompare(String(right));
  });
}

function factorBarHtml(name, value, maxValue) {
  const number = Number(value);
  const valid = value !== null && value !== undefined && !Number.isNaN(number);
  const width = valid && maxValue > 0 ? Math.min(50, Math.abs(number) / maxValue * 50) : 0;
  const left = number < 0 ? 50 - width : 50;
  const tone = number < 0 ? "negative" : "positive";
  const label = factorDisplayName(name);
  return `
    <div class="factor-bar-row">
      <span>${escapeHtml(label)}</span>
      <div class="factor-axis">
        <i class="zero-line"></i>
        <b class="${tone}" style="left:${left}%;width:${width}%"></b>
      </div>
      <strong class="${signedClass(value)}">${formatExposureNumber(value)}</strong>
    </div>
  `;
}

function renderExposureFactorRows(exposure) {
  const rows = exposure.factor_rows || [];
  const names = exposure.factor_names || [];
  if (!rows.length || !names.length) {
    els.exposureFactorRows.innerHTML = `<div class="empty-state">暂无风格穿透矩阵</div>`;
    return;
  }
  const visibleRows = rows.filter((row) => row.type !== "fund" || state.expandedExposureSleeves.has(row.parent_id));
  els.exposureFactorRows.innerHTML = `
    <table class="exposure-matrix factor-exposure-matrix">
      <thead>
        <tr>
          <th class="sticky-col">名称</th>
          ${names.map((name) => `<th>${escapeHtml(factorDisplayName(name))}</th>`).join("")}
        </tr>
      </thead>
      <tbody>
        ${visibleRows.map((row) => exposureFactorRowHtml(row, names)).join("")}
      </tbody>
    </table>
  `;
}

function renderExposureHistoryTables(exposure) {
  const history = exposure?.history_tables || {};
  const industryHtml = exposureIndustryHistoryTableHtml(history.industry_l1);
  const holdingsHtml = exposureTopHoldingsHistoryTableHtml(history.top_holdings);
  els.exposureHistoryTables.innerHTML = `
    <article class="attribution-panel exposure-history-card">
      <div class="table-title">
        <h3>历年一级行业配置</h3>
      </div>
      ${industryHtml}
    </article>
    <article class="attribution-panel exposure-history-card">
      <div class="table-title">
        <h3>前十大个股配置</h3>
      </div>
      ${holdingsHtml}
    </article>
	  `;
  bindExposureIndustryHistorySort(els.exposureHistoryTables);
  bindExposureHoldingHistoryTable(els.exposureHistoryTables);
}

function exposureIndustryHistoryTableHtml(table) {
  const periods = table?.periods || [];
  const rows = table?.rows || [];
  if (!periods.length || !rows.length) return `<div class="empty-state">暂无历史一级行业配置</div>`;
  const maxValue = Math.max(...rows.flatMap((row) => periods.map((period) => Number(row.values?.[period] || 0))), 0);
  return `
    <div class="exposure-history-scroll">
      <table class="exposure-history-table exposure-industry-history-table">
        <thead>
	          <tr>
	            <th class="history-sticky-col">行业名称</th>
	            ${periods.map((period) => `
	              <th>
	                <button class="history-sort-button" type="button" data-exposure-history-sort="${escapeHtml(period)}" title="点击切换顺序、倒序、默认排序">
	                  <span>${escapeHtml(period)}</span><b></b>
	                </button>
	              </th>
	            `).join("")}
	          </tr>
	        </thead>
	        <tbody>
	          ${rows.map((row, rowIndex) => `
	            <tr data-default-index="${rowIndex}">
	              <td class="history-sticky-col">${escapeHtml(row.industry || "-")}</td>
	              ${periods.map((period) => {
	                const value = Number(row.values?.[period] || 0);
                const text = value > 0 ? formatPercent(value) : "—";
                return `<td data-value="${escapeHtml(value)}" style="background:${exposureHistoryHeatColor(value, maxValue)}">${escapeHtml(text)}</td>`;
              }).join("")}
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function bindExposureIndustryHistorySort(scope) {
  (scope || document).querySelectorAll(".exposure-industry-history-table:not([data-sort-bound])").forEach((table) => {
    table.setAttribute("data-sort-bound", "1");
    table.addEventListener("click", (event) => {
      const button = event.target.closest("[data-exposure-history-sort]");
      if (!button || !table.contains(button)) return;
      const state = nextExposureHistorySortState(button.dataset.sortState || "default");
      table.querySelectorAll("[data-exposure-history-sort]").forEach((item) => {
        item.dataset.sortState = "default";
        item.classList.remove("active", "ascending", "descending");
        const marker = item.querySelector("b");
        if (marker) marker.textContent = "";
      });
      button.dataset.sortState = state;
      button.classList.toggle("active", state !== "default");
      button.classList.toggle("ascending", state === "ascending");
      button.classList.toggle("descending", state === "descending");
      const marker = button.querySelector("b");
      if (marker) marker.textContent = state === "ascending" ? "↑" : state === "descending" ? "↓" : "";
      sortExposureIndustryRows(table, button.dataset.exposureHistorySort, state);
    });
  });
}

function nextExposureHistorySortState(current) {
  if (current === "ascending") return "descending";
  if (current === "descending") return "default";
  return "ascending";
}

function sortExposureIndustryRows(table, period, state) {
  const tbody = table.querySelector("tbody");
  if (!tbody) return;
  const headers = [...table.querySelectorAll("thead th")];
  const columnIndex = headers.findIndex((header) => header.querySelector("[data-exposure-history-sort]")?.dataset.exposureHistorySort === period);
  const rows = [...tbody.querySelectorAll("tr")];
  const sorted = state === "default"
    ? rows.sort((left, right) => Number(left.dataset.defaultIndex || 0) - Number(right.dataset.defaultIndex || 0))
    : rows.sort((left, right) => {
      const leftValue = Number(left.children[columnIndex]?.dataset.value || 0);
      const rightValue = Number(right.children[columnIndex]?.dataset.value || 0);
      const leftMissing = !Number.isFinite(leftValue) || leftValue <= 0;
      const rightMissing = !Number.isFinite(rightValue) || rightValue <= 0;
      if (leftMissing !== rightMissing) return leftMissing ? 1 : -1;
      if (leftValue === rightValue) return Number(left.dataset.defaultIndex || 0) - Number(right.dataset.defaultIndex || 0);
      return state === "ascending" ? leftValue - rightValue : rightValue - leftValue;
    });
  sorted.forEach((row) => tbody.appendChild(row));
}

function exposureTopHoldingsHistoryTableHtml(table) {
  const rows = table?.rows || [];
  if (!rows.length) return `<div class="empty-state">暂无历史前十大个股配置</div>`;
  const stockFrequency = new Map();
  rows.forEach((row) => (row.holdings || []).forEach((holding) => {
    const name = holding.name || holding.code || "-";
    stockFrequency.set(name, (stockFrequency.get(name) || 0) + 1);
  }));
  const stockColors = new Map(
    [...stockFrequency.keys()]
      .sort((left, right) => stockFrequency.get(right) - stockFrequency.get(left) || String(left).localeCompare(String(right)))
      .map((name, index) => [name, exposureStockColor(index)])
  );
  return `
    <div class="exposure-history-scroll">
      <table class="exposure-history-table exposure-holding-history-table">
        <thead>
          <tr>
            <th class="history-sticky-col">报告期</th>
            ${Array.from({ length: 10 }, (_, index) => `<th>第${index + 1}大</th>`).join("")}
          </tr>
        </thead>
        <tbody>
          ${rows.map((row) => `
            <tr>
              <td class="history-sticky-col">${escapeHtml(row.period || "-")}</td>
              ${Array.from({ length: 10 }, (_, index) => {
                const holding = (row.holdings || [])[index];
                if (!holding) return `<td class="history-empty-cell">—</td>`;
                const name = holding.name || holding.code || "-";
                const color = stockColors.get(name) || exposureStockColor(index);
                return `
                  <td class="history-stock-cell" data-history-stock="${escapeHtml(name)}" title="${escapeHtml(`${name} ${formatPercent(holding.weight)}`)}" style="--stock-bg:${color.bg};--stock-fg:${color.fg}">
                    <div>${escapeHtml(name)}</div>
                    <span>${formatPercent(holding.weight)}</span>
                  </td>
                `;
              }).join("")}
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function exposureHistoryHeatColor(value, maxValue) {
  const number = Number(value);
  if (!Number.isFinite(number) || number <= 0 || maxValue <= 0) return "transparent";
  const ratio = Math.min(number / maxValue, 1);
  const intensity = Math.pow(ratio, 0.55);
  const alpha = 0.06 + intensity * 0.66;
  return `rgba(201, 74, 74, ${alpha.toFixed(2)})`;
}

function exposureStockColor(index) {
  const hue = (index * 137.508 + 8) % 360;
  return {
    bg: `hsl(${hue.toFixed(0)}, 34%, 83%)`,
    fg: `hsl(${hue.toFixed(0)}, 46%, 24%)`,
  };
}

function bindExposureHoldingHistoryTable(scope) {
  (scope || document).querySelectorAll(".exposure-holding-history-table:not([data-bound])").forEach((table) => {
    table.setAttribute("data-bound", "1");
    let pinned = null;
    const highlight = (name) => {
      table.querySelectorAll(".history-stock-cell").forEach((cell) => {
        cell.classList.toggle("dimmed", Boolean(name) && cell.dataset.historyStock !== name);
      });
    };
    table.addEventListener("mouseover", (event) => {
      if (pinned) return;
      const cell = event.target.closest(".history-stock-cell");
      if (cell) highlight(cell.dataset.historyStock);
    });
    table.addEventListener("mouseout", (event) => {
      if (!pinned && event.target.closest(".history-stock-cell")) highlight(null);
    });
    table.addEventListener("click", (event) => {
      const cell = event.target.closest(".history-stock-cell");
      if (!cell) {
        pinned = null;
        highlight(null);
        return;
      }
      const name = cell.dataset.historyStock;
      pinned = pinned === name ? null : name;
      highlight(pinned);
    });
  });
}

function exposureFactorRowHtml(row, names) {
  const isFund = row.type === "fund";
  const attrs = isFund ? ` data-exposure-fund="${escapeHtml(row.id)}"` : "";
  return `
    <tr class="exposure-row ${escapeHtml(row.type)} level-${Number(row.level || 0)}"${attrs}>
      <td class="sticky-col">${exposureNameCell(row)}</td>
      ${names.map((name) => exposureHeatCell(row.values?.[name], false)).join("")}
    </tr>
  `;
}

function exposureNameCell(row) {
  const name = escapeHtml(row.name || row.id);
  if (row.type === "sleeve") {
    const expanded = state.expandedExposureSleeves.has(row.id);
    return `
      <button class="attribution-toggle" type="button" data-exposure-toggle="${escapeHtml(row.id)}" aria-expanded="${expanded ? "true" : "false"}">
        ${expanded ? "−" : "+"}
      </button>
      <strong>${name}</strong>
    `;
  }
  return `<strong>${name}</strong>`;
}

function exposureHeatCell(value, isPercent) {
  const number = Number(value);
  const valid = value !== null && value !== undefined && !Number.isNaN(number);
  const text = isPercent ? formatPercent(value) : formatExposureNumber(value);
  return `<td style="background:${exposureHeatColor(valid ? number : null, isPercent)}">${escapeHtml(text)}</td>`;
}

function exposureHeatColor(value, isPercent) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "#f8fafc";
  const scale = isPercent ? Math.min(Math.abs(Number(value)) / 0.2, 1) : Math.min(Math.abs(Number(value)) / 1.5, 1);
  const alpha = 0.06 + scale * 0.26;
  return Number(value) >= 0 ? `rgba(47, 111, 159, ${alpha})` : `rgba(201, 74, 74, ${alpha})`;
}

function selectExposureBreakdown(sectionId, itemKey, scopeId = "portfolio") {
  const stateKey = exposureBreakdownStateKey(sectionId, scopeId);
  if (!sectionId || !itemKey || state.selectedExposureBreakdowns[stateKey] === itemKey) return;
  state.selectedExposureBreakdowns[stateKey] = itemKey;
  if (state.result?.exposure) renderExposureBreakdownSections(state.result.exposure);
}

function selectExposureBreakdownScope(sectionId, scopeId) {
  if (!sectionId || !scopeId || state.selectedExposureScopes[sectionId] === scopeId) return;
  state.selectedExposureScopes[sectionId] = scopeId;
  if (state.result?.exposure) renderExposureBreakdownSections(state.result.exposure);
}

function handleExposureHover(event) {
  const breakdownItem = event.target.closest(".exposure-pie-slice[data-exposure-breakdown-item]");
  if (breakdownItem) {
    selectExposureBreakdown(breakdownItem.dataset.exposureBreakdownSection, breakdownItem.dataset.exposureBreakdownItem, breakdownItem.dataset.exposureBreakdownScope);
  }
}

function handleExposureClick(event) {
  const scopeButton = event.target.closest("[data-exposure-breakdown-scope-select]");
  if (scopeButton) {
    selectExposureBreakdownScope(scopeButton.dataset.exposureBreakdownSection, scopeButton.dataset.exposureBreakdownScopeSelect);
    return;
  }

  const breakdownItem = event.target.closest('[data-exposure-breakdown-trigger="legend"]');
  if (breakdownItem) {
    selectExposureBreakdown(breakdownItem.dataset.exposureBreakdownSection, breakdownItem.dataset.exposureBreakdownItem, breakdownItem.dataset.exposureBreakdownScope);
    return;
  }

  const toggle = event.target.closest("[data-exposure-toggle]");
  if (toggle) {
    const sleeveId = toggle.dataset.exposureToggle;
    if (state.expandedExposureSleeves.has(sleeveId)) state.expandedExposureSleeves.delete(sleeveId);
    else state.expandedExposureSleeves.add(sleeveId);
    if (state.result) renderExposureView(state.result);
    return;
  }

  const fund = event.target.closest("[data-exposure-fund]");
  if (fund && state.result?.exposure) {
    openExposureFundDrawer(fund.dataset.exposureFund, state.result.exposure);
  }
}

function openExposureFundDrawer(fundId, exposure) {
  const detail = exposure.fund_details?.[fundId];
  if (!detail) return;
  state.selectedExposureFund = fundId;
  els.exposureDrawerTitle.textContent = detail.name || fundId;
  els.exposureDrawerCode.textContent = "";
  els.exposureDrawerBody.innerHTML = exposureDrawerHtml(detail, exposure.factor_names || []);
  els.fundExposureDrawer.classList.remove("hidden");
}

function closeExposureFundDrawer() {
  state.selectedExposureFund = null;
  els.fundExposureDrawer.classList.add("hidden");
}

function exposureDrawerHtml(detail, factorNames) {
  const sortedNames = sortedFactorNames(factorNames, detail.factor_values || {});
  const maxFactorValue = maxAbs(sortedNames.map((name) => detail.factor_values?.[name]));
  return `
    <section>
      <h4>Top5 一级行业</h4>
      <div class="drawer-list">
        ${(detail.top_industries || []).map((item) => `<div><span>${escapeHtml(item.industry)}</span><strong>${formatPercent(item.weight)}</strong></div>`).join("") || `<div class="empty-state">暂无行业分布</div>`}
      </div>
    </section>
    <section>
      <h4>Top10 二级行业</h4>
      <div class="drawer-list">
        ${(detail.top_l2_industries || []).map((item) => `<div><span>${escapeHtml(item.industry)}</span><strong>${formatPercent(item.weight)}</strong></div>`).join("") || `<div class="empty-state">暂无二级行业分布</div>`}
      </div>
    </section>
    <section>
      <h4>Top10 A股重仓股</h4>
      <div class="drawer-table-wrap">
        <table>
          <thead><tr><th>股票</th><th>代码</th><th>权重</th></tr></thead>
          <tbody>
            ${(detail.top_holdings || []).map((item) => `<tr><td>${escapeHtml(item.name)}</td><td>${escapeHtml(item.code)}</td><td>${formatPercent(item.weight)}</td></tr>`).join("")}
          </tbody>
        </table>
      </div>
    </section>
    <section>
      <h4>风格暴露（Barra/CNE5）</h4>
      <div class="factor-bar-list compact">
        ${sortedNames.map((name) => factorBarHtml(name, detail.factor_values?.[name], maxFactorValue)).join("")}
      </div>
    </section>
  `;
}

function formatExposureNumber(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return Number(value).toFixed(2);
}

function drawerMetricHtml(label, value) {
  return `
    <div class="drawer-metric">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
    </div>
  `;
}

function annualContributionLabel(item) {
  if (!item || item.year === null || item.year === undefined) return "-";
  return `${item.year} ${formatPercent(item.value)}`;
}

function tooltipIcon(text) {
  const safeText = escapeHtml(text);
  return `<span class="tooltip-icon" tabindex="0" aria-label="${safeText}" title="${safeText}">!</span>`;
}

function renderAnnualContributionTable(result) {
  const scopedResult = resultScopeData(result, state.resultScopes.annualContributionTable);
  const funds = scopedResult.funds || [];
  const headers = ["年份", ...funds.map((fund) => fund.name), "合计"];
  const rows = (scopedResult.annual_contributions || []).map((row) => [
    row.year,
    ...funds.map((fund) => signedPercentCell(row.funds[fund.id])),
    signedPercentCell(row.total),
  ]);
  els.annualContributionTable.innerHTML = makeTable(headers, rows);
}

function makeTable(headers, rows) {
  if (!rows.length) return `<div class="empty-state">暂无数据</div>`;
  return `
    <table>
      <thead><tr>${headers.map((header) => `<th>${escapeHtml(String(header))}</th>`).join("")}</tr></thead>
      <tbody>
        ${rows.map((row) => `<tr>${row.map((cell) => tableCellHtml(cell)).join("")}</tr>`).join("")}
      </tbody>
    </table>
  `;
}

function signedPercentCell(value) {
  return {
    html: formatPercent(value),
    className: signedClass(value),
  };
}

function signedClass(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value)) || Number(value) === 0) return "";
  return Number(value) > 0 ? "signed positive" : "signed negative";
}

function tableCellHtml(cell) {
  if (cell && typeof cell === "object" && Object.prototype.hasOwnProperty.call(cell, "html")) {
    const className = cell.className ? ` class="${escapeHtml(cell.className)}"` : "";
    return `<td${className}>${cell.html}</td>`;
  }
  return `<td>${cell}</td>`;
}

function setStatus(text, type) {
  els.status.textContent = text;
  els.status.className = `status ${type || ""}`;
}

function backtestCompleteStatusText(currentPortfolio, message = "回测完成。") {
  const name = String(currentPortfolio?.name || "").trim();
  return name ? `${name}：${message}` : message;
}

function formatPercent(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return `${(Number(value) * 100).toFixed(2)}%`;
}

function formatNumber(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return Number(value).toFixed(digits);
}

function formatMoney(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return Number(value).toLocaleString("zh-CN", { maximumFractionDigits: 2 });
}

function formatMoneyShort(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  const number = Number(value);
  if (Math.abs(number) >= 10000) return `${(number / 10000).toFixed(1)}万`;
  return `${number.toFixed(0)}`;
}

function numberOrNull(value) {
  if (value === null || value === undefined || value === "" || Number.isNaN(Number(value))) return null;
  return Number(value);
}

function formatText(value) {
  return value === null || value === undefined || value === "" ? "-" : String(value);
}

function formatMonths(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  const number = Number(value);
  return Number.isInteger(number) ? String(number) : number.toFixed(1);
}

function formatRepairMonths(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "未修复";
  return formatMonths(value);
}

function formatDays(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return `${Math.round(Number(value))} 天`;
}

function formatRecoveryDays(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "未修复";
  return formatDays(value);
}

function formatCorrelation(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return Number(value).toFixed(2);
}

function formatCount(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return String(Number(value));
}

function signedClassValue(value) {
  const text = String(value ?? "");
  if (text.startsWith("-")) return "signed negative";
  if (text !== "-" && text !== "" && text !== "0.00%" && text !== "0.00") return "signed positive";
  return "";
}

function formatDateTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString("zh-CN", { hour12: false });
}

function toReturnPoints(points) {
  return (points || []).map((point) => ({ date: point.date, value: point.value === null ? null : point.value - 1 }));
}

function roundNumber(value, digits) {
  const base = 10 ** digits;
  return Math.round(value * base) / base;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function init() {
  wireEvents();
  try {
    const res = await fetch(apiPath("/api/assets"));
    if (!res.ok) throw new Error("基金池加载失败");
    const data = await res.json();
    state.funds = data.funds || [];
    state.benchmarks = data.benchmarks || [];
    state.tradingCalendar = data.tradingCalendar || [];
    state.fundsById = new Map(state.funds.map((fund) => [fund.id, fund]));
    els.startDate.value = "2021-01-01";
    els.endDate.value = latestDataDate();
    renderBenchmarks();
    await loadPortfolios();
    if (!state.currentPortfolio) {
      state.currentPortfolio = createEmptyPortfolio();
      state.currentPortfolioId = null;
      portfolioToSleeves(state.currentPortfolio);
    }
    syncFrequencyButtons();
    renderLibraryCollapsedState();
    renderPortfolioHome();
    setResultView("backtest");
    setStatus(`已加载 ${state.funds.length} 只基金，本地数据包可离线使用。`, "ok");
  } catch (err) {
    setStatus(err.message || String(err), "error");
  }
}

function wireEvents() {
  on(els.portfolioSearchInput, "input", renderPortfolios);
  on(els.toggleLibraryBtn, "click", toggleLibraryPanel);
  on(els.portfolioBulkToggleBtn, "click", togglePortfolioDeleteMode);
  on(els.closePortfolioDeleteConfirmBtn, "click", closePortfolioDeleteConfirm);
  on(els.cancelPortfolioDeleteBtn, "click", closePortfolioDeleteConfirm);
  on(els.confirmPortfolioDeleteBtn, "click", confirmDeletePortfolio);
  on(els.portfolioDeleteConfirmModal, "click", (event) => {
    if (event.target === els.portfolioDeleteConfirmModal) closePortfolioDeleteConfirm();
  });
  on(els.newPortfolioBtn, "click", newPortfolio);
  on(els.adjustPortfolioBtn, "click", openAdjustPortfolioModal);
  on(els.runBtn, "click", runBacktest);
  els.frequencyButtons.forEach((button) => on(button, "click", handleFrequencyChange));
  on(els.searchInput, "input", renderFunds);
  on(els.benchmarkPickerBtn, "click", openBenchmarkModal);
  on(els.closeBenchmarkModalBtn, "click", closeBenchmarkModal);
  on(els.benchmarkSearchInput, "input", renderBenchmarkOptions);
  on(els.benchmarkTypeFilter, "change", renderBenchmarkOptions);
  on(els.benchmarkModal, "click", (event) => {
    if (event.target === els.benchmarkModal) closeBenchmarkModal();
  });
  on(els.resultTabs, "click", handleResultTabClick);
  on(els.latestHoldingTable, "click", handleLatestHoldingTableClick);
  on(els.attributionRows, "click", handleAttributionTableClick);
  on(els.analysisWindowPresets, "click", handleAnalysisWindowPresetClick);
  on(els.analysisRangeStart, "input", handleAnalysisRangeInput);
  on(els.analysisRangeEnd, "input", handleAnalysisRangeInput);
  on(els.analysisRangeStart, "change", refreshAnalysisWindow);
  on(els.analysisRangeEnd, "change", refreshAnalysisWindow);
  on(els.closeFundDrawerBtn, "click", closeFundDrawer);
  on(els.exposureView, "click", handleExposureClick);
  on(els.exposureView, "mouseover", handleExposureHover);
  on(els.closeExposureDrawerBtn, "click", closeExposureFundDrawer);
  on(els.performanceModeSelect, "change", handlePerformanceModeChange);
  on(els.performanceRangePresets, "click", handlePerformanceRangePresetClick);
  on(els.performanceRangeStart, "input", handlePerformanceRangeInput);
  on(els.performanceRangeEnd, "input", handlePerformanceRangeInput);
  on(els.performanceRangeStartDate, "change", handlePerformanceDateInput);
  on(els.performanceRangeEndDate, "change", handlePerformanceDateInput);
  on(els.annualReturnScope, "change", () => {
    state.resultScopes.annualReturn = els.annualReturnScope.value;
    if (state.result) renderAnnualReturnModule(state.result);
  });
  on(els.periodReturnScope, "change", () => {
    state.resultScopes.periodReturn = els.periodReturnScope.value;
    if (state.result) renderPeriodReturnModule(state.result);
  });
  on(els.annualContributionTableScope, "change", () => {
    state.resultScopes.annualContributionTable = els.annualContributionTableScope.value;
    if (state.result) renderAnnualContributionTable(state.result);
  });
  on(els.closeAdjustModalBtn, "click", closeAdjustPortfolioModal);
  on(els.cancelAdjustModalBtn, "click", closeAdjustPortfolioModal);
  on(els.confirmAdjustModalBtn, "click", confirmAdjustPortfolioModal);
  on(els.initialCashInput, "input", updateCashSettingsFromInputs);
  on(els.feeSettingsBtn, "click", openFeeSettingsModal);
  on(els.closeFeeSettingsModalBtn, "click", closeFeeSettingsModal);
  on(els.cancelFeeSettingsModalBtn, "click", closeFeeSettingsModal);
  on(els.confirmFeeSettingsModalBtn, "click", confirmFeeSettingsModal);
  on(els.activeCashFlowInput, "input", updateActiveCashFlow);
  on(els.generateGlobalPlanBtn, "click", generateRebalancePlanFromCurrentNode);
  on(els.equalInsideBtn, "click", equalInsideCurrentNode);
  on(els.currentTemplateSelect, "change", applyTemplateToActiveNode);
  on(els.importExcelBtn, "click", () => els.excelFileInput.click());
  on(els.excelFileInput, "change", importRebalanceExcel);
  on(els.addRebalanceDateBtn, "click", openAddRebalanceDatePicker);
  on(els.addRebalanceDateInput, "change", addRebalanceDateFromInput);
  on(els.adjustPortfolioModal, "click", (event) => {
    if (event.target === els.adjustPortfolioModal) closeAdjustPortfolioModal();
  });
  on(els.feeSettingsModal, "click", (event) => {
    if (event.target === els.feeSettingsModal) closeFeeSettingsModal();
  });
  window.addEventListener("resize", () => {
    if (state.result) renderCharts(state.result);
  });
}

function on(element, eventName, handler) {
  if (element) element.addEventListener(eventName, handler);
}

function normalizeDataFrequency(value) {
  const frequency = String(value || "daily").trim().toLowerCase();
  return Object.prototype.hasOwnProperty.call(dataFrequencyLabels, frequency) ? frequency : "daily";
}

function currentDataFrequency() {
  return normalizeDataFrequency(state.currentPortfolio?.dataFrequency || state.result?.frequency || "daily");
}

function frequencyLabel(frequency) {
  return dataFrequencyLabels[normalizeDataFrequency(frequency)] || dataFrequencyLabels.daily;
}

function syncFrequencyButtons(frequency = currentDataFrequency()) {
  const normalized = normalizeDataFrequency(frequency);
  els.frequencyButtons.forEach((button) => {
    button.classList.toggle("active", button.dataset.frequency === normalized);
  });
}

async function handleFrequencyChange(event) {
  const button = event.target.closest("[data-frequency]");
  if (!button) return;
  const frequency = normalizeDataFrequency(button.dataset.frequency);
  if (!state.currentPortfolio) state.currentPortfolio = createEmptyPortfolio();
  if (frequency === currentDataFrequency()) {
    syncFrequencyButtons(frequency);
    return;
  }
  state.currentPortfolio.dataFrequency = frequency;
  syncFrequencyButtons(frequency);
  const cached = cachedResultForFrequency(state.currentPortfolio, frequency);
  if (cached) {
    state.result = cached;
    state.currentPortfolio.lastBacktestSnapshot = cached;
    renderResult(cached, false);
    renderPortfolioHome();
    return;
  }
  if (state.currentPortfolio.id) {
    setStatus(`正在加载${frequencyLabel(frequency)}快照...`, "");
    try {
      const snapshot = await fetchPortfolioSnapshot(state.currentPortfolio.id, frequency);
      if (snapshot) {
        if (!state.currentPortfolio.backtestSnapshots) state.currentPortfolio.backtestSnapshots = {};
        state.currentPortfolio.backtestSnapshots[normalizeDataFrequency(snapshot.frequency || frequency)] = snapshot;
        state.currentPortfolio.lastBacktestSnapshot = snapshot;
        state.result = snapshot;
        renderResult(snapshot, false);
        renderPortfolioHome();
        setStatus("", "");
        return;
      }
    } catch (err) {
      setStatus(err.message || String(err), "error");
      return;
    }
  }
  state.result = null;
  els.results.classList.add("hidden");
  renderPortfolioHome();
  setStatus(`已切换为${frequencyLabel(frequency)}，该频率暂无缓存，请点击回测生成三频缓存。`, "ok");
}

function winRateMetricLabel(result = state.result) {
  return winRateLabels[normalizeDataFrequency(result?.frequency || state.currentPortfolio?.dataFrequency)] || winRateLabels.daily;
}

function cachedResultForFrequency(portfolio, frequency) {
  const normalized = normalizeDataFrequency(frequency);
  const snapshot = portfolio?.backtestSnapshots?.[normalized];
  if (snapshot) return snapshot;
  const latest = portfolio?.lastBacktestSnapshot;
  return normalizeDataFrequency(latest?.frequency) === normalized ? latest : null;
}

async function fetchPortfolioSnapshot(portfolioId, frequency) {
  const normalized = normalizeDataFrequency(frequency);
  const data = await fetchJson(`/api/portfolios/${encodeURIComponent(portfolioId)}/snapshots/${encodeURIComponent(normalized)}`);
  return data.snapshot || null;
}

function activePortfolioSnapshot(portfolio = state.currentPortfolio) {
  return cachedResultForFrequency(portfolio, portfolio?.dataFrequency) || portfolio?.lastBacktestSnapshot || null;
}

async function loadPortfolios(preferredPortfolioId = null) {
  els.portfolioCount.textContent = "正在加载组合...";
  try {
    await refreshPortfolioList(preferredPortfolioId);
    const targetId = preferredPortfolioId || state.currentPortfolioId;
    const target = state.portfolios.find((portfolio) => portfolio.id === targetId) || state.portfolios[0];
    if (target) await selectPortfolio(target.id);
  } catch (err) {
    state.portfolios = [];
    renderPortfolios();
    els.portfolioCount.textContent = "组合加载失败";
    setStatus(err.message || String(err), "error");
  }
}

async function refreshPortfolioList(activePortfolioId = state.currentPortfolioId) {
  const data = await fetchJson("/api/portfolios");
  state.portfolios = data.portfolios || [];
  if (activePortfolioId) state.currentPortfolioId = activePortfolioId;
  renderPortfolios();
  return state.portfolios;
}

function toggleLibraryPanel() {
  state.libraryCollapsed = !state.libraryCollapsed;
  renderLibraryCollapsedState();
  if (state.result) renderCharts(state.result);
}

function renderLibraryCollapsedState() {
  els.shell.classList.toggle("library-collapsed", state.libraryCollapsed);
  els.toggleLibraryBtn.textContent = state.libraryCollapsed ? "›" : "‹";
  els.toggleLibraryBtn.title = state.libraryCollapsed ? "展开组合库" : "折叠组合库";
  els.toggleLibraryBtn.setAttribute("aria-label", state.libraryCollapsed ? "展开组合库" : "折叠组合库");
}

function renderPortfolios() {
  const query = (els.portfolioSearchInput?.value || "").trim().toLowerCase();
  const portfolios = state.portfolios.filter((portfolio) => {
    const text = `${portfolio.name || ""} ${portfolio.benchmark || ""}`.toLowerCase();
    return !query || text.includes(query);
  });
  els.portfolioCount.textContent = `${portfolios.length} / ${state.portfolios.length} 个组合`;
  renderPortfolioDeleteControls();
  if (!portfolios.length) {
    els.portfolioList.innerHTML = `<div class="empty-state">暂无组合，点击新建组合开始。</div>`;
    return;
  }
  els.portfolioList.innerHTML = "";
  portfolios.forEach((portfolio) => {
    const card = document.createElement("div");
    card.tabIndex = 0;
    card.setAttribute("role", "button");
    card.dataset.portfolioCard = portfolio.id;
    card.className = [
      "portfolio-list-card",
      portfolio.id === state.currentPortfolioId ? "active" : "",
      state.portfolioDeleteMode ? "operation-mode" : "",
    ].filter(Boolean).join(" ");
    const summary = portfolio.summary || {};
    card.innerHTML = `
      <div class="portfolio-list-content">
        <strong>${escapeHtml(portfolio.name || "未命名组合")}</strong>
        <span>${escapeHtml(portfolio.latestHoldingDate || "无持仓")} · ${Number(portfolio.rebalanceCount || 0)} 个调仓日</span>
        <em>${escapeHtml(summary.window || "尚未回测")}</em>
      </div>
      ${state.portfolioDeleteMode ? `
        <div class="portfolio-card-actions">
          <button class="portfolio-delete-btn" type="button" data-delete-portfolio="${escapeHtml(portfolio.id)}" aria-label="删除 ${escapeHtml(portfolio.name || "未命名组合")}">×</button>
          <button class="portfolio-move-btn" draggable="true" type="button" data-drag-portfolio="${escapeHtml(portfolio.id)}" aria-label="拖动排序" title="拖动排序">
            <span></span><span></span><span></span>
          </button>
        </div>
      ` : ""}
    `;
    const handleCardAction = () => {
      selectPortfolio(portfolio.id);
    };
    card.addEventListener("click", (event) => {
      if (event.target.closest(".portfolio-card-actions")) return;
      handleCardAction();
    });
    card.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      handleCardAction();
    });
    if (state.portfolioDeleteMode) wirePortfolioOperationCard(card, portfolio);
    els.portfolioList.appendChild(card);
  });
}

function renderPortfolioDeleteControls() {
  if (!els.portfolioBulkToggleBtn) return;
  els.portfolioBulkToggleBtn.textContent = state.portfolioDeleteMode ? "完成" : "操作";
}

function togglePortfolioDeleteMode() {
  state.portfolioDeleteMode = !state.portfolioDeleteMode;
  state.draggingPortfolioId = null;
  state.pendingPortfolioDeleteId = null;
  closePortfolioDeleteConfirm();
  renderPortfolios();
}

function wirePortfolioOperationCard(card, portfolio) {
  const deleteButton = card.querySelector("[data-delete-portfolio]");
  const moveButton = card.querySelector("[data-drag-portfolio]");
  deleteButton?.addEventListener("click", (event) => {
    event.stopPropagation();
    openPortfolioDeleteConfirm(portfolio.id);
  });
  moveButton?.addEventListener("click", (event) => event.stopPropagation());
  moveButton?.addEventListener("dragstart", (event) => {
    event.stopPropagation();
    state.draggingPortfolioId = portfolio.id;
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", portfolio.id);
    card.classList.add("dragging");
  });
  moveButton?.addEventListener("dragend", () => {
    clearPortfolioDragState();
  });
  card.addEventListener("dragover", handlePortfolioDragOver);
  card.addEventListener("dragleave", () => {
    card.classList.remove("drag-over-before", "drag-over-after");
  });
  card.addEventListener("drop", handlePortfolioDrop);
}

function openPortfolioDeleteConfirm(portfolioId) {
  const portfolio = state.portfolios.find((item) => item.id === portfolioId);
  if (!portfolio) return;
  state.pendingPortfolioDeleteId = portfolioId;
  if (els.portfolioDeleteConfirmText) {
    els.portfolioDeleteConfirmText.textContent = `确认删除「${portfolio.name || "未命名组合"}」？删除后无法从组合库恢复。`;
  }
  els.portfolioDeleteConfirmModal?.classList.remove("hidden");
}

function closePortfolioDeleteConfirm() {
  state.pendingPortfolioDeleteId = null;
  els.portfolioDeleteConfirmModal?.classList.add("hidden");
  if (els.confirmPortfolioDeleteBtn) els.confirmPortfolioDeleteBtn.disabled = false;
}

async function confirmDeletePortfolio() {
  const portfolioId = state.pendingPortfolioDeleteId;
  if (!portfolioId) return;
  if (els.confirmPortfolioDeleteBtn) els.confirmPortfolioDeleteBtn.disabled = true;
  try {
    await fetchJson(`/api/portfolios/${encodeURIComponent(portfolioId)}`, { method: "DELETE" });
    const deletedCurrent = portfolioId === state.currentPortfolioId;
    closePortfolioDeleteConfirm();
    if (deletedCurrent) {
      state.currentPortfolio = null;
      state.currentPortfolioId = null;
      state.result = null;
      state.isPortfolioDirty = false;
    }
    const nextPortfolio = state.portfolios.find((portfolio) => portfolio.id !== portfolioId);
    const preferredPortfolioId = deletedCurrent ? nextPortfolio?.id || null : state.currentPortfolioId;
    await loadPortfolios(preferredPortfolioId);
    if (!state.currentPortfolio) {
      state.currentPortfolio = createEmptyPortfolio();
      portfolioToSleeves(state.currentPortfolio);
      renderPortfolioHome();
    }
    setStatus("已删除组合。", "ok");
  } catch (err) {
    if (els.confirmPortfolioDeleteBtn) els.confirmPortfolioDeleteBtn.disabled = false;
    setStatus(err.message || String(err), "error");
    renderPortfolioDeleteControls();
  }
}

function handlePortfolioDragOver(event) {
  if (!state.draggingPortfolioId) return;
  const card = event.currentTarget;
  const targetId = card.dataset.portfolioCard;
  if (!targetId || targetId === state.draggingPortfolioId) return;
  event.preventDefault();
  const rect = card.getBoundingClientRect();
  state.portfolioDragInsertAfter = event.clientY > rect.top + rect.height / 2;
  els.portfolioList.querySelectorAll(".drag-over-before,.drag-over-after").forEach((item) => {
    item.classList.remove("drag-over-before", "drag-over-after");
  });
  card.classList.add(state.portfolioDragInsertAfter ? "drag-over-after" : "drag-over-before");
}

async function handlePortfolioDrop(event) {
  if (!state.draggingPortfolioId) return;
  event.preventDefault();
  const targetId = event.currentTarget.dataset.portfolioCard;
  const changed = reorderPortfolioInState(state.draggingPortfolioId, targetId, state.portfolioDragInsertAfter);
  clearPortfolioDragState();
  if (!changed) return;
  renderPortfolios();
  await savePortfolioOrder();
}

function reorderPortfolioInState(fromId, targetId, insertAfter) {
  if (!fromId || !targetId || fromId === targetId) return false;
  const fromIndex = state.portfolios.findIndex((portfolio) => portfolio.id === fromId);
  const targetIndex = state.portfolios.findIndex((portfolio) => portfolio.id === targetId);
  if (fromIndex < 0 || targetIndex < 0) return false;
  const [moved] = state.portfolios.splice(fromIndex, 1);
  const adjustedTargetIndex = state.portfolios.findIndex((portfolio) => portfolio.id === targetId);
  const insertIndex = adjustedTargetIndex + (insertAfter ? 1 : 0);
  state.portfolios.splice(insertIndex, 0, moved);
  return true;
}

function clearPortfolioDragState() {
  state.draggingPortfolioId = null;
  state.portfolioDragInsertAfter = false;
  els.portfolioList?.querySelectorAll(".dragging,.drag-over-before,.drag-over-after").forEach((item) => {
    item.classList.remove("dragging", "drag-over-before", "drag-over-after");
  });
}

async function savePortfolioOrder() {
  try {
    const data = await fetchJson("/api/portfolios/order", {
      method: "POST",
      body: JSON.stringify({ ids: state.portfolios.map((portfolio) => portfolio.id) }),
    });
    state.portfolios = data.portfolios || state.portfolios;
    renderPortfolios();
  } catch (err) {
    setStatus(err.message || String(err), "error");
    await refreshPortfolioList();
  }
}

function syncCurrentPortfolioSummary() {
  const portfolio = state.currentPortfolio;
  if (!portfolio?.id) {
    renderPortfolios();
    return;
  }
  const index = state.portfolios.findIndex((item) => item.id === portfolio.id);
  if (index < 0) {
    renderPortfolios();
    return;
  }
  const latest = latestPlanNode(portfolio);
  state.portfolios[index] = {
    ...state.portfolios[index],
    name: portfolio.name,
    benchmark: portfolio.benchmark,
    latestHoldingDate: latest?.date || state.portfolios[index].latestHoldingDate,
    rebalanceCount: (portfolio.rebalancePlan || []).length,
  };
  renderPortfolios();
}

async function selectPortfolio(portfolioId) {
  const token = ++state.portfolioSelectionToken;
  const summary = state.portfolios.find((portfolio) => portfolio.id === portfolioId);
  if (summary) {
    state.currentPortfolio = {
      ...state.currentPortfolio,
      ...summary,
      id: portfolioId,
      dataFrequency: normalizeDataFrequency(summary.dataFrequency || state.currentPortfolio?.dataFrequency),
      rebalancePlan: [],
      latestHoldings: [],
      backtestSnapshots: {},
      lastBacktestSnapshot: null,
    };
    state.currentPortfolioId = portfolioId;
    state.isPortfolioDirty = false;
    state.result = null;
    renderPortfolios();
    renderPortfolioHome();
  }
  const portfolio = await fetchJson(`/api/portfolios/${encodeURIComponent(portfolioId)}/active`);
  if (token !== state.portfolioSelectionToken) return;
  state.currentPortfolio = portfolio;
  state.currentPortfolioId = portfolio.id;
  state.isPortfolioDirty = false;
  state.result = activePortfolioSnapshot(portfolio);
  portfolioToSleeves(portfolio);
  syncPortfolioControls(portfolio);
  renderPortfolios();
  renderPortfolioHome();
  if (state.result) renderResult(state.result, false);
  else if (portfolio.hasBacktest) loadSelectedPortfolioSnapshot(portfolio.id, currentDataFrequency(), token);
}

async function loadSelectedPortfolioSnapshot(portfolioId, frequency, token) {
  try {
    const snapshot = await fetchPortfolioSnapshot(portfolioId, frequency);
    if (token !== state.portfolioSelectionToken || portfolioId !== state.currentPortfolioId) return;
    if (!snapshot) return;
    if (!state.currentPortfolio.backtestSnapshots) state.currentPortfolio.backtestSnapshots = {};
    state.currentPortfolio.backtestSnapshots[normalizeDataFrequency(snapshot.frequency || frequency)] = snapshot;
    state.currentPortfolio.lastBacktestSnapshot = snapshot;
    state.result = snapshot;
    renderResult(snapshot, false);
    renderPortfolioHome();
  } catch (err) {
    if (token !== state.portfolioSelectionToken || portfolioId !== state.currentPortfolioId) return;
    setStatus(err.message || String(err), "error");
  }
}

function latestDataDate() {
  return state.tradingCalendar[state.tradingCalendar.length - 1] || "";
}

function createEmptyPortfolio() {
  return {
    id: null,
    name: "新组合",
    benchmark: els.benchmarkSelect.value || "000300.SH",
    startDate: els.startDate.value || "2021-01-01",
    endDate: els.endDate.value || latestDataDate(),
    dataFrequency: "daily",
    rebalanceFrequency: "quarterly",
    globalTemplate: "equal",
    initialCash: 1000000,
    feeConfig: { ...defaultFeeConfig },
    rebalancePlan: [],
    backtestSnapshots: {},
    lastBacktestSnapshot: null,
    latestHoldings: [],
  };
}

function syncPortfolioControls(portfolio) {
  els.benchmarkSelect.value = portfolio.benchmark || "000300.SH";
  els.startDate.value = portfolio.startDate || "2021-01-01";
  els.endDate.value = portfolio.endDate || latestDataDate();
  portfolio.dataFrequency = normalizeDataFrequency(portfolio.dataFrequency);
  syncFrequencyButtons(portfolio.dataFrequency);
  renderBenchmarkSummary();
}

function portfolioToSleeves(portfolio) {
  const latest = latestPlanNode(portfolio);
  state.sleeves = sleeveDefs.map((definition) => ({
    ...definition,
    weight: 0,
    internalMode: "manual",
    funds: [],
  }));
  if (!latest) return;
  (latest.holdings || []).forEach((holding) => {
    const sleeveId = normalizeSleeveId(holding.sleeveId);
    const sleeve = findSleeve(sleeveId);
    if (!sleeve) return;
    const weight = holdingWeightPercent(holding);
    sleeve.weight += weight;
    sleeve.funds.push({ id: holding.fundId || holding.id || holding.code, weight });
  });
}

function renderPortfolioHome() {
  const portfolio = state.currentPortfolio;
  if (!portfolio) return;
  const latest = latestPlanNode(portfolio);
  const latestHoldings = portfolio.latestHoldings?.length ? portfolio.latestHoldings : holdingsPayloadFromPlan(latest);
  const activeSnapshot = activePortfolioSnapshot(portfolio);
  els.portfolioDirtyNotice.classList.toggle("hidden", !state.isPortfolioDirty);
  els.portfolioHomeView.classList.toggle("hidden", !state.isPortfolioDirty || state.resultView !== "backtest");
  renderLatestHoldingTable(latestHoldings);
  renderTransactionTable(activeSnapshot?.transactions || []);
}

function renderLatestHoldingTable(holdings) {
  if (!holdings.length) {
    els.latestHoldingTable.innerHTML = `<div class="empty-state">暂无持仓，请点击调整持仓。</div>`;
    return;
  }
  const hasRealPositions = holdings.some((holding) => hasRealPositionFields(holding));
  els.latestHoldingTable.innerHTML = `
    <table>
      <thead>
        ${hasRealPositions ? `
          <tr>
            <th>基金</th>
            <th>持仓权重</th>
            <th>持仓市值</th>
            <th>浮动盈亏</th>
            <th>盈亏比例</th>
            <th>权重漂移</th>
            <th>最新日期</th>
          </tr>
        ` : `
          <tr>
            <th>基金名称</th>
            <th>持仓权重</th>
            <th>权重漂移</th>
            <th>最新日期</th>
            <th>最新涨幅</th>
            <th>今年以来</th>
          </tr>
        `}
      </thead>
      <tbody>
        ${latestHoldingGroupedRows(holdings, hasRealPositions)}
      </tbody>
    </table>
  `;
}

function latestHoldingGroupedRows(holdings, hasRealPositions) {
  return sleeveDefs.map((sleeve) => {
    const rows = holdings.filter((holding) => normalizeSleeveId(holding.sleeveId) === sleeve.id);
    const collapsed = state.latestHoldingCollapsedSleeves.has(sleeve.id);
    return [
      latestHoldingSleeveRow(sleeve, rows, collapsed, hasRealPositions),
      ...(collapsed ? [] : rows.map((holding) => latestHoldingFundRow(holding, hasRealPositions))),
    ].join("");
  }).join("");
}

function latestHoldingSleeveRow(sleeve, rows, collapsed, hasRealPositions) {
  const weight = rows.reduce((sum, holding) => sum + Number(holding.weight || 0), 0);
  const targetWeight = rows.reduce((sum, holding) => sum + Number(holding.targetWeight ?? holding.target_weight ?? holding.weight ?? 0), 0);
  const marketValue = sumPositionField(rows, "marketValue", "market_value");
  const profit = sumPositionField(rows, "profit");
  const costAmount = sumPositionField(rows, "costAmount", "cost_amount");
  const profitPct = costAmount > 0 ? profit / costAmount : null;
  const toggle = collapsed ? "▸" : "▾";
  if (hasRealPositions) {
    return `
      <tr class="latest-sleeve-row" data-sleeve-id="${escapeHtml(sleeve.id)}">
        <td>
          <button class="latest-sleeve-toggle" type="button" data-latest-sleeve-toggle="${escapeHtml(sleeve.id)}">
            <span>${toggle}</span><strong>${escapeHtml(sleeve.name)}</strong><small>${rows.length} 只</small>
          </button>
        </td>
        <td>${formatPercent(weight)}</td>
        <td>${formatMoney(marketValue)}</td>
        <td class="${signedClass(profit)}">${formatMoney(profit)}</td>
        <td class="${signedClass(profitPct)}">${formatPercent(profitPct)}</td>
        <td>${holdingWeightDriftText({ targetWeight, weight })}</td>
        <td></td>
      </tr>
    `;
  }
  return `
    <tr class="latest-sleeve-row" data-sleeve-id="${escapeHtml(sleeve.id)}">
      <td>
        <button class="latest-sleeve-toggle" type="button" data-latest-sleeve-toggle="${escapeHtml(sleeve.id)}">
          <span>${toggle}</span><strong>${escapeHtml(sleeve.name)}</strong><small>${rows.length} 只</small>
        </button>
      </td>
      <td>${formatPercent(weight)}</td>
      <td>${holdingWeightDriftText({ targetWeight, weight })}</td>
      <td></td>
      <td></td>
      <td></td>
    </tr>
  `;
}

function latestHoldingFundRow(holding, hasRealPositions) {
  if (hasRealPositions) {
    return `
      <tr class="latest-fund-row">
        <td class="latest-fund-name">${escapeHtml(holding.name || fundName(holding.fundId))}</td>
        <td>${formatPercent(holding.weight)}</td>
        <td>${formatMoney(positionField(holding, "marketValue", "market_value"))}</td>
        <td class="${signedClass(positionField(holding, "profit"))}">${formatMoney(positionField(holding, "profit"))}</td>
        <td class="${signedClass(positionField(holding, "profitPct", "profit_pct"))}">${formatPercent(positionField(holding, "profitPct", "profit_pct"))}</td>
        <td>${holdingWeightDriftText(holding)}</td>
        <td>${escapeHtml(holding.navDate || "-")}</td>
      </tr>
    `;
  }
  return `
    <tr class="latest-fund-row">
      <td class="latest-fund-name">${escapeHtml(holding.name || fundName(holding.fundId))}</td>
      <td>${formatPercent(holding.weight)}</td>
      <td>${holdingWeightDriftText(holding)}</td>
      <td>${escapeHtml(holding.navDate || "-")}</td>
      <td class="${signedClass(holding.dailyReturn)}">${formatPercent(holding.dailyReturn)}</td>
      <td class="${signedClass(holding.ytdReturn)}">${formatPercent(holding.ytdReturn)}</td>
    </tr>
  `;
}

function holdingWeightDriftText(holding) {
  const currentWeight = positionField(holding, "weight");
  const targetWeight = positionField(holding, "targetWeight", "target_weight");
  if (targetWeight === null || targetWeight === undefined || Number.isNaN(Number(targetWeight))) {
    return formatPercent(currentWeight);
  }
  return `${formatPercent(targetWeight)} 到 ${formatPercent(currentWeight)}`;
}

function sumPositionField(rows, ...keys) {
  return rows.reduce((sum, row) => {
    const value = positionField(row, ...keys);
    return sum + (Number.isFinite(Number(value)) ? Number(value) : 0);
  }, 0);
}

function handleLatestHoldingTableClick(event) {
  const button = event.target.closest("[data-latest-sleeve-toggle]");
  if (!button) return;
  const sleeveId = button.dataset.latestSleeveToggle;
  if (state.latestHoldingCollapsedSleeves.has(sleeveId)) state.latestHoldingCollapsedSleeves.delete(sleeveId);
  else state.latestHoldingCollapsedSleeves.add(sleeveId);
  renderPortfolioHome();
}

function renderTransactionTable(transactions) {
  if (!els.transactionTable) return;
  if (!transactions.length) {
    els.transactionTable.innerHTML = `<div class="empty-state">暂无交易流水，资金模式回测后显示。</div>`;
    return;
  }
  const rows = [...transactions].sort((left, right) => String(right.date).localeCompare(String(left.date)));
  els.transactionTable.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>日期</th>
          <th>基金</th>
          <th>类型</th>
          <th>权重变化</th>
          <th>净值</th>
          <th>份额</th>
          <th>成交金额</th>
          <th>费用</th>
          <th>现金变动</th>
        </tr>
      </thead>
      <tbody>
        ${rows.map((transaction) => `
          <tr>
            <td>${escapeHtml(transaction.date || "-")}</td>
            <td>${escapeHtml(transaction.fundId ? fundName(transaction.fundId) : "-")}</td>
            <td>${escapeHtml(transaction.action || transaction.type || "-")}</td>
            <td>${escapeHtml(transaction.weight_change || "-")}</td>
            <td>${formatNumber(transaction.price, 4)}</td>
            <td>${formatNumber(transaction.quantity, 2)}</td>
            <td>${formatMoney(transaction.amount)}</td>
            <td>${formatMoney(transaction.fee)}</td>
            <td class="${signedClass(transaction.cash_change)}">${formatMoney(transaction.cash_change)}</td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  `;
}

function cashModeSummary(portfolio) {
  const snapshot = activePortfolioSnapshot(portfolio);
  const metrics = snapshot?.metrics || {};
  const hasCashSnapshot = Boolean(snapshot?.cash_mode);
  const hasInitialCash = portfolio?.initialCash !== undefined && portfolio?.initialCash !== null && portfolio?.initialCash !== "";
  return {
    cashMode: hasCashSnapshot || hasInitialCash,
    initialCash: numberOrNull(portfolio?.initialCash),
    finalValue: numberOrNull(metrics.final_value),
    totalProfit: numberOrNull(metrics.total_profit),
    cashBalance: numberOrNull(metrics.cash_balance),
    totalFees: numberOrNull(metrics.total_fees),
  };
}

function hasRealPositionFields(holding) {
  return positionField(holding, "quantity") !== null || positionField(holding, "marketValue", "market_value") !== null;
}

function positionField(holding, ...keys) {
  for (const key of keys) {
    const value = holding?.[key];
    if (value !== null && value !== undefined && value !== "") return value;
  }
  return null;
}

function ensureCashDefaults(portfolio) {
  if (!portfolio) return;
  if (portfolio.initialCash === undefined || portfolio.initialCash === null || portfolio.initialCash === "") {
    portfolio.initialCash = 1000000;
  }
  if (!portfolio.feeConfig || typeof portfolio.feeConfig !== "object") {
    portfolio.feeConfig = { ...defaultFeeConfig };
  }
  portfolio.feeConfig.purchase = Math.max(0, Number(portfolio.feeConfig.purchase ?? 0) || 0);
  portfolio.feeConfig.redemption = Math.max(0, Number(portfolio.feeConfig.redemption ?? 0) || 0);
  portfolio.feeConfig.transaction = Math.max(0, Number(portfolio.feeConfig.transaction ?? 0) || 0);
}

function updateCashSettingsFromInputs() {
  if (!state.modalPortfolio) return;
  state.modalPortfolio.initialCash = Math.max(0, Number(els.initialCashInput?.value || 0));
  ensureCashDefaults(state.modalPortfolio);
  renderModalHoldingTable();
  renderCashEstimate();
}

function openFeeSettingsModal() {
  if (!state.modalPortfolio) return;
  ensureCashDefaults(state.modalPortfolio);
  els.purchaseFeeInput.value = percentInputValue(state.modalPortfolio.feeConfig.purchase);
  els.redemptionFeeInput.value = percentInputValue(state.modalPortfolio.feeConfig.redemption);
  els.transactionFeeInput.value = percentInputValue(state.modalPortfolio.feeConfig.transaction);
  els.feeSettingsModal.classList.remove("hidden");
}

function closeFeeSettingsModal() {
  els.feeSettingsModal.classList.add("hidden");
}

function confirmFeeSettingsModal() {
  if (!state.modalPortfolio) return;
  state.modalPortfolio.feeConfig = {
    purchase: feePercentFromInput(els.purchaseFeeInput),
    redemption: feePercentFromInput(els.redemptionFeeInput),
    transaction: feePercentFromInput(els.transactionFeeInput),
  };
  closeFeeSettingsModal();
  renderModalHoldingTable();
  renderCashEstimate();
}

function feePercentFromInput(input) {
  return Math.max(0, Number(input?.value || 0) / 100);
}

function updateActiveCashFlow() {
  const node = activeNode();
  if (!node) return;
  node.cashFlow = Number(els.activeCashFlowInput?.value || 0);
  node.source = node.source === "initial" ? "initial" : "manual";
  els.activeCashFlowInput.className = signedClass(node.cashFlow);
  renderTimeline();
  renderModalHoldingTable();
  renderCashEstimate();
}

function renderCashEstimate() {
  const node = activeNode();
  if (!els.cashEstimatePanel || !node) return;
  const beforeAsset = estimatedAssetBeforeNode(node);
  const cashFlow = Number(node.cashFlow || 0);
  const targetAsset = beforeAsset + cashFlow;
  els.cashEstimatePanel.innerHTML = `
    <span>调仓后资产 <strong>${formatMoney(targetAsset)}</strong></span>
  `;
}

function estimatedAssetBeforeNode(node) {
  const plan = [...(state.modalPortfolio?.rebalancePlan || [])].sort((a, b) => String(a.date).localeCompare(String(b.date)));
  let asset = Number(state.modalPortfolio?.initialCash || 0);
  for (const item of plan) {
    if (item === node) break;
    asset += Number(item.cashFlow || 0);
  }
  return asset;
}

function estimatedNodeAsset(node) {
  if (!node) return 0;
  return estimatedAssetBeforeNode(node) + Number(node.cashFlow || 0);
}

function cashFlowTimelineLabel(value) {
  const number = Number(value || 0);
  if (!number) return "";
  return ` · ${number > 0 ? "+" : ""}${formatMoneyShort(number)}`;
}

function percentInputValue(rate) {
  return String(roundNumber((Number(rate) || 0) * 100, 4));
}

function formatWeightInputValue(value) {
  return String(roundNumber(Number(value) || 0, 2));
}

function newPortfolio() {
  state.currentPortfolio = createEmptyPortfolio();
  state.currentPortfolioId = null;
  state.result = null;
  state.isPortfolioDirty = true;
  syncPortfolioControls(state.currentPortfolio);
  portfolioToSleeves(state.currentPortfolio);
  renderPortfolios();
  renderPortfolioHome();
  openAdjustPortfolioModal();
}

function openAdjustPortfolioModal() {
  state.modalPortfolio = clonePortfolio(state.currentPortfolio || createEmptyPortfolio());
  state.modalHoldingCollapsedSleeves = new Set();
  ensureCashDefaults(state.modalPortfolio);
  if (!state.modalPortfolio.rebalancePlan.length) {
    state.modalPortfolio.rebalancePlan.push({
      date: state.modalPortfolio.startDate || new Date().toISOString().slice(0, 10),
      source: "initial",
      cashFlow: 0,
      holdings: [],
    });
  }
  state.modalPortfolio.rebalancePlan.forEach((node) => {
    if (node.cashFlow === undefined || node.cashFlow === null) node.cashFlow = 0;
  });
  state.activeRebalanceIndex = Math.min(state.activeRebalanceIndex, state.modalPortfolio.rebalancePlan.length - 1);
  els.portfolioNameInput.value = state.modalPortfolio.name || "";
  els.modalStartDate.value = state.modalPortfolio.startDate || els.startDate.value || "2021-01-01";
  els.modalEndDate.value = state.modalPortfolio.endDate || els.endDate.value || latestDataDate();
  els.initialCashInput.value = String(state.modalPortfolio.initialCash || 1000000);
  els.purchaseFeeInput.value = percentInputValue(state.modalPortfolio.feeConfig?.purchase ?? 0);
  els.redemptionFeeInput.value = percentInputValue(state.modalPortfolio.feeConfig?.redemption ?? 0);
  els.transactionFeeInput.value = percentInputValue(state.modalPortfolio.feeConfig?.transaction ?? 0);
  els.rebalanceFrequencySelect.value = normalizeRebalanceFrequency(state.modalPortfolio.rebalanceFrequency);
  if (els.currentTemplateSelect) els.currentTemplateSelect.value = normalizeGlobalTemplate(state.modalPortfolio.globalTemplate);
  renderAdjustPortfolioModal();
  els.adjustPortfolioModal.classList.remove("hidden");
}

function closeAdjustPortfolioModal() {
  els.adjustPortfolioModal.classList.add("hidden");
  closeFeeSettingsModal();
  state.modalPortfolio = null;
}

function renderAdjustPortfolioModal() {
  if (!state.modalPortfolio) return;
  els.adjustPortfolioMeta.textContent = `${els.modalStartDate.value || "-"} 至 ${els.modalEndDate.value || "-"} · ${state.modalPortfolio.rebalancePlan.length} 个调仓日`;
  renderTimeline();
  renderFunds();
  renderModalHoldingTable();
  renderCashEstimate();
  renderRebalanceValidation();
}

function renderTimeline() {
  const plan = state.modalPortfolio.rebalancePlan || [];
  els.rebalanceTimeline.innerHTML = plan.map((node, index) => `
    <div class="timeline-row ${index === state.activeRebalanceIndex ? "active" : ""}">
      <button class="timeline-select" type="button" data-rebalance-index="${index}">
        <strong>${escapeHtml(node.date || "-")}</strong>
        <span>${escapeHtml(sourceLabel(node.source))}${cashFlowTimelineLabel(node.cashFlow)}</span>
      </button>
      <span class="timeline-actions">
        <button class="timeline-action-btn timeline-icon-edit" type="button" data-edit-rebalance-date="${index}" aria-label="修改日期" title="修改日期">✎</button>
        <button class="timeline-action-btn timeline-icon-delete danger-mini" type="button" data-delete-rebalance-date="${index}" aria-label="删除节点" title="删除节点" ${plan.length <= 1 ? "disabled" : ""}>×</button>
        <input class="date-picker-proxy" type="date" value="${escapeHtml(node.date || "")}" data-edit-rebalance-input="${index}" />
      </span>
    </div>
  `).join("") || `<div class="empty-state">暂无调仓日</div>`;
  els.rebalanceTimeline.querySelectorAll("[data-rebalance-index]").forEach((button) => {
    button.addEventListener("click", () => {
      state.activeRebalanceIndex = Number(button.dataset.rebalanceIndex || 0);
      renderAdjustPortfolioModal();
    });
  });
  els.rebalanceTimeline.querySelectorAll("[data-edit-rebalance-date]").forEach((button) => {
    button.addEventListener("click", () => {
      const input = els.rebalanceTimeline.querySelector(`[data-edit-rebalance-input="${button.dataset.editRebalanceDate}"]`);
      if (!input) return;
      if (typeof input.showPicker === "function") input.showPicker();
      else {
        input.focus();
        input.click();
      }
    });
  });
  els.rebalanceTimeline.querySelectorAll("[data-edit-rebalance-input]").forEach((input) => {
    input.addEventListener("change", () => updateRebalanceDate(Number(input.dataset.editRebalanceInput), input.value));
  });
  els.rebalanceTimeline.querySelectorAll("[data-delete-rebalance-date]").forEach((button) => {
    button.addEventListener("click", () => deleteRebalanceDate(Number(button.dataset.deleteRebalanceDate)));
  });
}

function updateRebalanceDate(index, value) {
  const date = parseDate(value);
  const plan = state.modalPortfolio?.rebalancePlan || [];
  if (!date || index < 0 || index >= plan.length) return;
  const node = plan[index];
  node.date = formatDateInput(date);
  if (node.source !== "initial") node.source = "manual";
  plan.sort((left, right) => String(left.date).localeCompare(String(right.date)));
  state.activeRebalanceIndex = plan.findIndex((item) => item === node);
  renderAdjustPortfolioModal();
}

function deleteRebalanceDate(index) {
  const plan = state.modalPortfolio?.rebalancePlan || [];
  if (plan.length <= 1 || index < 0 || index >= plan.length) return;
  plan.splice(index, 1);
  state.activeRebalanceIndex = Math.max(0, Math.min(index, plan.length - 1));
  renderAdjustPortfolioModal();
}

function renderFunds() {
  const query = (els.searchInput?.value || "").trim().toLowerCase();
  const funds = state.funds.filter((fund) => {
    const haystack = [fund.name, fund.manager, fund.code, fund.cluster_label].join(" ").toLowerCase();
    return !query || haystack.includes(query);
  });
  els.assetCount.textContent = `${funds.length} / ${state.funds.length} 只基金`;
  const target = els.modalAssetList || els.assetList;
  target.innerHTML = "";
  if (!funds.length) {
    target.innerHTML = `<div class="empty-state">没有匹配的基金</div>`;
    return;
  }
  const groupedFunds = groupFundsByCluster(funds);
  clusterDefs.forEach((cluster) => {
    const clusterFunds = groupedFunds.get(cluster.id) || [];
    if (!clusterFunds.length) return;
    const collapsed = !query && state.collapsedClusters.has(cluster.id);
    const section = document.createElement("section");
    section.className = "fund-cluster-section";
    section.innerHTML = `
      <button class="fund-cluster-head" type="button" aria-expanded="${String(!collapsed)}">
        <span class="fund-cluster-title">
          <span class="fund-cluster-toggle">${collapsed ? "+" : "-"}</span>
          <span>${escapeHtml(cluster.name)}</span>
        </span>
        <span class="fund-cluster-count">${clusterFunds.length} 只</span>
      </button>
    `;
    section.querySelector(".fund-cluster-head").addEventListener("click", () => {
      if (state.collapsedClusters.has(cluster.id)) state.collapsedClusters.delete(cluster.id);
      else state.collapsedClusters.add(cluster.id);
      renderFunds();
    });
    if (!collapsed) {
      const body = document.createElement("div");
      body.className = "fund-cluster-body compact";
      clusterFunds.forEach((fund) => body.appendChild(renderFundCard(fund)));
      section.appendChild(body);
    }
    target.appendChild(section);
  });
}

function renderFundCard(fund) {
  const node = activeNode();
  const exists = Boolean(node?.holdings?.some((holding) => holding.fundId === fund.id));
  const card = document.createElement("button");
  card.className = `asset-card compact ${exists ? "disabled" : ""}`;
  card.type = "button";
  card.innerHTML = `
    <div class="asset-name"><span>${escapeHtml(fund.name)}</span></div>
    <div class="asset-code">${escapeHtml(fund.manager || "未填经理")} · ${escapeHtml(fund.manager_start_date || "任职起始未知")}</div>
  `;
  card.addEventListener("click", () => toggleFundInActiveNode(fund.id));
  return card;
}

function toggleFundInActiveNode(id) {
  const node = activeNode();
  if (!node) return;
  const existingIndex = node.holdings.findIndex((holding) => holding.fundId === id);
  if (existingIndex >= 0) {
    node.holdings.splice(existingIndex, 1);
    node.source = "manual";
    renderAdjustPortfolioModal();
    return;
  }
  addFund(id);
}

function addFund(id, sleeveId = null) {
  const node = activeNode();
  if (!node || !state.fundsById.has(id)) return;
  if (node.holdings.some((holding) => holding.fundId === id)) return;
  node.holdings.push({
    fundId: id,
    sleeveId: sleeveId || defaultSleeveForFund(fundMeta(id)),
    weight: 0,
  });
  node.source = node.source === "initial" ? "initial" : "manual";
  renderAdjustPortfolioModal();
}

function renderModalHoldingTable() {
  const node = activeNode();
  els.activeRebalanceDateTitle.textContent = node ? `当前调仓日 ${node.date}` : "当前调仓日";
  if (els.activeCashFlowInput) {
    els.activeCashFlowInput.value = String(Number(node?.cashFlow || 0));
    els.activeCashFlowInput.className = signedClass(Number(node?.cashFlow || 0));
  }
  if (!node || !node.holdings.length) {
    els.modalHoldingTable.innerHTML = `<div class="empty-state">从左侧基金池加入基金。</div>`;
    return;
  }
  ensureNodeSleeveTargets(node);
  const grouped = new Map(sleeveDefs.map((sleeve) => [sleeve.id, []]));
  node.holdings.forEach((holding, index) => {
    const sleeveId = normalizeSleeveId(holding.sleeveId);
    grouped.get(sleeveId).push({ holding, index });
  });
  els.modalHoldingTable.innerHTML = sleeveDefs.map((sleeve) => {
    const collapsed = state.modalHoldingCollapsedSleeves.has(sleeve.id);
    const holdings = grouped.get(sleeve.id) || [];
    return `
      <section class="holding-sleeve-section ${collapsed ? "collapsed" : ""}" data-sleeve-dropzone="${escapeHtml(sleeve.id)}">
        <div class="holding-sleeve-head compact">
          <div class="sleeve-title-controls">
            <h4>${escapeHtml(sleeve.name)}</h4>
            <label class="sleeve-target-label">
              <span>权重</span>
              <input class="sleeve-target-input" type="number" min="0" max="100" step="1" value="${escapeHtml(String(sleeveTargetWeightForNode(node, sleeve.id)))}" data-sleeve-weight="${escapeHtml(sleeve.id)}" />
            </label>
          </div>
          <span class="sleeve-actual-weight" data-sleeve-actual-weight="${escapeHtml(sleeve.id)}">
            累计权重 <strong>${formatPercent(sleeveHoldingWeightTotal(node, sleeve.id) / 100)}</strong>
          </span>
          <button class="sleeve-collapse-btn" type="button" data-toggle-modal-sleeve="${escapeHtml(sleeve.id)}" aria-label="${collapsed ? "展开" : "折叠"}${escapeHtml(sleeve.name)}" title="${collapsed ? "展开" : "折叠"}${escapeHtml(sleeve.name)}">
            <span aria-hidden="true">▾</span>
          </button>
        </div>
        ${collapsed ? "" : `
          <table>
            <colgroup>
              <col class="fund-name-col" />
              <col class="manager-col" />
              <col class="tenure-col" />
              <col class="weight-col" />
              <col class="amount-col" />
              <col class="action-col" />
              <col class="move-col" />
            </colgroup>
            <thead>
              <tr>
                <th>基金名称</th>
                <th>基金经理</th>
                <th>起始任职区间</th>
                <th>权重%</th>
                <th>预计金额</th>
                <th>操作</th>
                <th>移动</th>
              </tr>
            </thead>
            <tbody>
              ${holdings.map(({ holding, index }) => holdingRowHtml(holding, index, estimatedNodeAsset(node))).join("") || `<tr><td colspan="7" class="muted-cell">暂无基金</td></tr>`}
            </tbody>
          </table>
        `}
      </section>
    `;
  }).join("");
  els.modalHoldingTable.querySelectorAll("[data-toggle-modal-sleeve]").forEach((button) => {
    button.addEventListener("click", () => {
      const sleeveId = button.dataset.toggleModalSleeve;
      if (state.modalHoldingCollapsedSleeves.has(sleeveId)) {
        state.modalHoldingCollapsedSleeves.delete(sleeveId);
      } else {
        state.modalHoldingCollapsedSleeves.add(sleeveId);
      }
      renderModalHoldingTable();
    });
  });
  els.modalHoldingTable.querySelectorAll("[data-holding-weight]").forEach((input) => {
    input.addEventListener("input", () => {
      const holding = node.holdings[Number(input.dataset.holdingWeight)];
      holding.weight = Number(input.value || 0);
      node.source = "manual";
      updateSleeveActualWeightDisplays(node);
      renderRebalanceValidation();
    });
    input.addEventListener("change", () => {
      input.value = formatWeightInputValue(input.value);
    });
  });
  els.modalHoldingTable.querySelectorAll("[data-sleeve-weight]").forEach((input) => {
    input.addEventListener("change", () => updateSleeveTargetWeight(input.dataset.sleeveWeight, input.value));
  });
  els.modalHoldingTable.querySelectorAll("[data-remove-holding]").forEach((button) => {
    button.addEventListener("click", () => {
      node.holdings.splice(Number(button.dataset.removeHolding), 1);
      node.source = "manual";
      renderAdjustPortfolioModal();
    });
  });
  els.modalHoldingTable.querySelectorAll("[data-drag-holding]").forEach((button) => {
    button.addEventListener("dragstart", (event) => {
      event.dataTransfer.setData("text/plain", button.dataset.dragHolding);
      event.dataTransfer.effectAllowed = "move";
      state.draggingHoldingIndex = Number(button.dataset.dragHolding);
      button.closest("tr")?.classList.add("dragging");
    });
    button.addEventListener("dragend", () => {
      state.draggingHoldingIndex = null;
      els.modalHoldingTable.querySelectorAll(".drag-over,.dragging").forEach((row) => row.classList.remove("drag-over", "dragging"));
      renderAdjustPortfolioModal();
    });
  });
  els.modalHoldingTable.querySelectorAll("[data-holding-row]").forEach((row) => {
    row.addEventListener("dragover", handleHoldingDragOver);
    row.addEventListener("dragleave", () => row.classList.remove("drag-over"));
    row.addEventListener("drop", (event) => {
      event.preventDefault();
      const fromIndex = Number(event.dataTransfer.getData("text/plain"));
      const toIndex = Number(row.dataset.holdingRow);
      moveHoldingInActiveNode(fromIndex, toIndex, row.dataset.sleeveId);
    });
  });
  els.modalHoldingTable.querySelectorAll("[data-sleeve-dropzone]").forEach((section) => {
    section.addEventListener("dragover", handleSleeveDropzoneDragOver);
    section.addEventListener("drop", (event) => {
      event.preventDefault();
      const fromIndex = Number(event.dataTransfer.getData("text/plain"));
      moveHoldingInActiveNode(fromIndex, insertionIndexForSleeve(section.dataset.sleeveDropzone), section.dataset.sleeveDropzone);
    });
  });
}

function sleeveHoldingWeightTotal(node, sleeveId) {
  return (node?.holdings || [])
    .filter((holding) => normalizeSleeveId(holding.sleeveId) === sleeveId)
    .reduce((sum, holding) => sum + holdingWeightPercent(holding), 0);
}

function updateSleeveActualWeightDisplays(node) {
  els.modalHoldingTable.querySelectorAll("[data-sleeve-actual-weight]").forEach((element) => {
    const sleeveId = element.dataset.sleeveActualWeight;
    const value = sleeveHoldingWeightTotal(node, sleeveId) / 100;
    const target = element.querySelector("strong");
    if (target) target.textContent = formatPercent(value);
  });
}

function holdingRowHtml(holding, index, estimatedAsset = 0) {
  const fund = fundMeta(holding.fundId);
  const expectedAmount = estimatedAsset * holdingWeightPercent(holding) / 100;
  return `
    <tr class="holding-row" data-holding-row="${index}" data-sleeve-id="${escapeHtml(normalizeSleeveId(holding.sleeveId))}">
      <td>${escapeHtml(fund?.name || holding.fundId)}</td>
      <td>${escapeHtml(fund?.manager || "-")}</td>
      <td>${escapeHtml(fund?.manager_start_date || "-")}</td>
      <td><input class="table-weight-input" type="number" min="0" max="100" step="0.01" value="${escapeHtml(formatWeightInputValue(holdingWeightPercent(holding)))}" data-holding-weight="${index}" /></td>
      <td>${formatMoney(expectedAmount)}</td>
      <td><button class="remove-holding-btn danger-mini" type="button" data-remove-holding="${index}" aria-label="删除基金" title="删除基金">×</button></td>
      <td><button class="drag-handle" draggable="true" type="button" data-drag-holding="${index}" aria-label="上下拖动排序"><span></span><span></span><span></span></button></td>
    </tr>
  `;
}

function handleHoldingDragOver(event) {
  event.preventDefault();
  const row = event.currentTarget;
  const fromIndex = state.draggingHoldingIndex;
  const toIndex = Number(row.dataset.holdingRow);
  row.classList.add("drag-over");
  if (Number.isInteger(fromIndex) && Number.isInteger(toIndex)) {
    moveHoldingInActiveNode(fromIndex, toIndex, row.dataset.sleeveId, false);
  }
}

function handleSleeveDropzoneDragOver(event) {
  event.preventDefault();
  const section = event.currentTarget;
  if (event.target.closest("[data-holding-row]")) return;
  const fromIndex = state.draggingHoldingIndex;
  if (Number.isInteger(fromIndex)) {
    moveHoldingInActiveNode(fromIndex, insertionIndexForSleeve(section.dataset.sleeveDropzone), section.dataset.sleeveDropzone, false);
  }
}

function insertionIndexForSleeve(targetSleeveId) {
  const node = activeNode();
  if (!node) return 0;
  let lastIndex = -1;
  node.holdings.forEach((holding, index) => {
    if (normalizeSleeveId(holding.sleeveId) === targetSleeveId) lastIndex = index;
  });
  return lastIndex >= 0 ? lastIndex + 1 : node.holdings.length;
}

function moveHoldingInActiveNode(fromIndex, toIndex, targetSleeveId = null, refresh = true) {
  const node = activeNode();
  if (!node || !Number.isInteger(fromIndex) || !Number.isInteger(toIndex)) return;
  if (fromIndex < 0 || toIndex < 0 || fromIndex >= node.holdings.length) return;
  const targetSleeve = targetSleeveId ? normalizeSleeveId(targetSleeveId) : null;
  const currentSleeve = normalizeSleeveId(node.holdings[fromIndex].sleeveId);
  if (fromIndex === toIndex && (!targetSleeve || targetSleeve === currentSleeve)) return;
  const [item] = node.holdings.splice(fromIndex, 1);
  if (targetSleeve) item.sleeveId = targetSleeve;
  const boundedToIndex = Math.max(0, Math.min(toIndex, node.holdings.length));
  node.holdings.splice(boundedToIndex, 0, item);
  node.source = "manual";
  state.draggingHoldingIndex = boundedToIndex;
  if (refresh) renderModalHoldingTable();
  else renderModalHoldingTable();
}

function renderRebalanceValidation() {
  const validation = validateRebalancePlan(state.modalPortfolio?.rebalancePlan || []);
  els.rebalanceValidation.className = `rebalance-validation current-day-validation ${validation.ok ? "ok" : "error"}`;
  const visibleMessages = validation.messages.slice(0, 2);
  if (validation.messages.length > 2) visibleMessages[1] = `另有 ${validation.messages.length - 1} 项需处理`;
  els.rebalanceValidation.innerHTML = visibleMessages.map((message) => `<p>${escapeHtml(message)}</p>`).join("");
  return validation;
}

function generateRebalancePlanFromCurrentNode() {
  const mode = document.querySelector('input[name="rebalanceApplyMode"]:checked')?.value || "forward";
  const current = activeNode();
  if (!current || !current.holdings.length) {
    renderRebalanceValidation();
    setStatus("请先在当前调仓日加入基金，再生成调仓。", "error");
    return;
  }
  if (mode === "single") {
    current.source = "manual";
    renderAdjustPortfolioModal();
    return;
  }
  applyCurrentNodeToFutureNodes();
}

function globalTemplateRatios(template) {
  if (template === "defensive") return { defensive: 50, balanced: 40, growth: 10 };
  if (template === "stable") return { defensive: 30, balanced: 40, growth: 30 };
  if (template === "aggressive") return { defensive: 10, balanced: 30, growth: 60 };
  return { defensive: 33.3333, balanced: 33.3333, growth: 33.3334 };
}

function frequencyRebalanceDates(startDate, endDate, frequency) {
  const normalizedFrequency = normalizeRebalanceFrequency(frequency);
  if (normalizedFrequency === "none") return [];
  const start = parseDate(startDate);
  const end = parseDate(endDate);
  if (!start || !end || start > end) return [];
  const startKey = formatDateInput(start);
  const endKey = formatDateInput(end);
  const months = new Set(
    normalizedFrequency === "monthly"
      ? Array.from({ length: 12 }, (_, index) => index)
      : normalizedFrequency === "semiannual"
        ? [0, 6]
        : [0, 3, 6, 9],
  );
  const dates = [];
  let cursor = new Date(start.getFullYear(), 0, 1);
  while (cursor <= end) {
    if (months.has(cursor.getMonth())) {
      const tradingDate = nextTradingDate(formatDateInput(cursor));
      if (tradingDate && tradingDate > startKey && tradingDate <= endKey) dates.push(tradingDate);
    }
    cursor = new Date(cursor.getFullYear(), cursor.getMonth() + 1, 1);
  }
  return [...new Set(dates)].sort();
}

function nextTradingDate(dateKey) {
  const parsed = parseDate(dateKey);
  if (!parsed) return "";
  const key = formatDateInput(parsed);
  const calendar = Array.isArray(state.tradingCalendar) ? state.tradingCalendar : [];
  if (calendar.length) {
    let left = 0;
    let right = calendar.length - 1;
    let match = "";
    while (left <= right) {
      const middle = Math.floor((left + right) / 2);
      if (calendar[middle] >= key) {
        match = calendar[middle];
        right = middle - 1;
      } else {
        left = middle + 1;
      }
    }
    if (match) return match;
  }
  const date = parseDate(key);
  if (!date) return key;
  while ([0, 6].includes(date.getDay())) date.setDate(date.getDate() + 1);
  return formatDateInput(date);
}

function ensureNodeSleeveTargets(node) {
  if (!node) return {};
  if (!node.sleeveTargets) {
    const activeSleeveIds = activeSleeveIdsForNode(node);
    const existing = {};
    activeSleeveIds.forEach((sleeveId) => {
      existing[sleeveId] = sleeveWeightForNode(node, sleeveId);
    });
    const existingTotal = activeSleeveIds.reduce((sum, sleeveId) => sum + Number(existing[sleeveId] || 0), 0);
    node.sleeveTargets = existingTotal > 0
      ? existing
      : normalizeRatiosForActiveSleeves(globalTemplateRatios(els.currentTemplateSelect?.value || "equal"), activeSleeveIds);
  }
  sleeveDefs.forEach((sleeve) => {
    if (!Number.isFinite(Number(node.sleeveTargets[sleeve.id]))) node.sleeveTargets[sleeve.id] = 0;
  });
  return node.sleeveTargets;
}

function cloneSleeveTargets(targets) {
  return { ...sleeveDefs.reduce((acc, sleeve) => ({ ...acc, [sleeve.id]: 0 }), {}), ...(targets || {}) };
}

function sleeveTargetWeightForNode(node, sleeveId) {
  const targets = ensureNodeSleeveTargets(node);
  return roundNumber(Number(targets[normalizeSleeveId(sleeveId)] || 0), 4);
}

function sleeveWeightForNode(node, sleeveId) {
  const total = (node?.holdings || [])
    .filter((holding) => normalizeSleeveId(holding.sleeveId) === sleeveId)
    .reduce((sum, holding) => sum + holdingWeightPercent(holding), 0);
  return roundNumber(total, 4);
}

function activeSleeveIdsForNode(node) {
  return activeSleeveIdsForHoldings(node?.holdings || []);
}

function activeSleeveIdsForHoldings(holdings) {
  return sleeveDefs
    .map((sleeve) => sleeve.id)
    .filter((sleeveId) => (holdings || []).some((holding) => normalizeSleeveId(holding.sleeveId) === sleeveId));
}

function redistributeSleeveWeight(node, sleeveId, totalWeight, mode = "equal") {
  const rows = (node?.holdings || []).filter((holding) => normalizeSleeveId(holding.sleeveId) === sleeveId);
  if (!rows.length) return;
  const target = Math.max(0, Math.min(100, Number(totalWeight) || 0));
  const currentTotal = rows.reduce((sum, holding) => sum + holdingWeightPercent(holding), 0);
  const preserveRelative = mode === "proportional" && currentTotal > 0;
  let used = 0;
  rows.forEach((holding, index) => {
    const value = index === rows.length - 1
      ? roundNumber(target - used, 4)
      : roundNumber(preserveRelative ? holdingWeightPercent(holding) / currentTotal * target : target / rows.length, 4);
    holding.weight = value;
    used += value;
  });
}

function updateSleeveTargetWeight(sleeveId, value) {
  const node = activeNode();
  if (!node) return;
  const normalizedSleeveId = normalizeSleeveId(sleeveId);
  const targets = ensureNodeSleeveTargets(node);
  targets[normalizedSleeveId] = roundNumber(Math.max(0, Math.min(100, Number(value) || 0)), 4);
  redistributeSleeveWeight(node, normalizedSleeveId, targets[normalizedSleeveId], "proportional");
  node.source = "manual";
  renderAdjustPortfolioModal();
}

function applyTemplateToActiveNode(options = {}) {
  const node = activeNode();
  if (!node || !node.holdings.length) {
    if (!options.silent) setStatus("请先在当前调仓日加入基金。", "error");
    return;
  }
  const activeSleeveIds = activeSleeveIdsForNode(node);
  const targets = normalizeRatiosForActiveSleeves(globalTemplateRatios(options.template || els.currentTemplateSelect?.value), activeSleeveIds);
  node.sleeveTargets = cloneSleeveTargets(targets);
  activeSleeveIds.forEach((sleeveId) => redistributeSleeveWeight(node, sleeveId, targets[sleeveId], "equal"));
  node.source = "manual";
  renderAdjustPortfolioModal();
}

function equalInsideCurrentNode() {
  const node = activeNode();
  if (!node || !node.holdings.length) return;
  const targets = sleeveTargetsForEqualInside(node);
  sleeveDefs.forEach((sleeve) => {
    redistributeSleeveWeight(node, sleeve.id, targets[sleeve.id], "equal");
  });
  node.source = "manual";
  renderAdjustPortfolioModal();
}

function sleeveTargetsForEqualInside(node) {
  const activeSleeveIds = activeSleeveIdsForNode(node);
  const targets = ensureNodeSleeveTargets(node);
  const hasEveryActiveSleeveTarget = activeSleeveIds.every((sleeveId) => Number(targets[sleeveId] || 0) > 0);
  if (hasEveryActiveSleeveTarget) {
    return targets;
  }
  const fallbackTemplate = els.currentTemplateSelect?.value || "equal";
  return normalizeRatiosForActiveSleeves(globalTemplateRatios(fallbackTemplate), activeSleeveIds);
}

function normalizeRatiosForActiveSleeves(ratios, activeSleeveIds) {
  const total = activeSleeveIds.reduce((sum, sleeveId) => sum + Number(ratios[sleeveId] || 0), 0);
  if (total <= 0) {
    const equal = roundNumber(100 / activeSleeveIds.length, 4);
    return Object.fromEntries(activeSleeveIds.map((sleeveId, index) => [
      sleeveId,
      index === activeSleeveIds.length - 1 ? roundNumber(100 - equal * (activeSleeveIds.length - 1), 4) : equal,
    ]));
  }
  const normalized = {};
  activeSleeveIds.forEach((sleeveId, index) => {
    if (index === activeSleeveIds.length - 1) {
      const used = activeSleeveIds.slice(0, -1).reduce((sum, id) => sum + Number(normalized[id] || 0), 0);
      normalized[sleeveId] = roundNumber(100 - used, 4);
    } else {
      normalized[sleeveId] = roundNumber(Number(ratios[sleeveId] || 0) / total * 100, 4);
    }
  });
  return normalized;
}

function applyCurrentNodeToFutureNodes() {
  const node = activeNode();
  if (!node || !state.modalPortfolio) return;
  const currentDate = String(node.date || "");
  const endDate = els.modalEndDate.value || state.modalPortfolio.endDate || "";
  const generatedDates = frequencyRebalanceDates(currentDate, endDate, els.rebalanceFrequencySelect?.value);
  pruneFutureRebalancePlanByFrequency(node, generatedDates);
  const existingDates = new Set((state.modalPortfolio.rebalancePlan || []).map((item) => String(item.date || "")));
  generatedDates.forEach((date) => {
    if (!date || existingDates.has(date)) return;
    state.modalPortfolio.rebalancePlan.push({
      date,
      source: "forward_applied",
      cashFlow: 0,
      holdings: [],
      sleeveTargets: {},
    });
    existingDates.add(date);
  });
  state.modalPortfolio.rebalancePlan.sort((left, right) => String(left.date).localeCompare(String(right.date)));
  state.activeRebalanceIndex = state.modalPortfolio.rebalancePlan.findIndex((item) => item === node);
  const sleeveTargets = cloneSleeveTargets(ensureNodeSleeveTargets(node));
  const holdings = cloneHoldings(node.holdings);
  state.modalPortfolio.rebalancePlan.forEach((item, index) => {
    if (String(item.date || "") < currentDate) return;
    item.holdings = cloneHoldings(holdings);
    item.sleeveTargets = cloneSleeveTargets(sleeveTargets);
    item.source = index === state.activeRebalanceIndex ? "manual" : "forward_applied";
  });
  renderAdjustPortfolioModal();
}

function pruneFutureRebalancePlanByFrequency(currentNode, generatedDates) {
  if (!state.modalPortfolio || !currentNode) return;
  const currentDate = String(currentNode.date || "");
  const allowedDates = new Set((generatedDates || []).map((date) => String(date || "")));
  state.modalPortfolio.rebalancePlan = (state.modalPortfolio.rebalancePlan || []).filter((item) => {
    if (String(item.date || "") <= currentDate) return true;
    return allowedDates.has(String(item.date || ""));
  });
}

function openAddRebalanceDatePicker() {
  const current = activeNode();
  els.addRebalanceDateInput.value = current?.date || els.modalStartDate.value || "";
  if (typeof els.addRebalanceDateInput.showPicker === "function") {
    els.addRebalanceDateInput.showPicker();
  } else {
    els.addRebalanceDateInput.focus();
    els.addRebalanceDateInput.click();
  }
}

function addRebalanceDateFromInput() {
  const date = parseDate(els.addRebalanceDateInput.value);
  if (!date) return;
  const node = {
    date: formatDateInput(date),
    source: "manual",
    cashFlow: 0,
    holdings: cloneHoldings(activeNode()?.holdings || []),
    sleeveTargets: cloneSleeveTargets(ensureNodeSleeveTargets(activeNode())),
  };
  state.modalPortfolio.rebalancePlan.push(node);
  state.modalPortfolio.rebalancePlan.sort((a, b) => String(a.date).localeCompare(String(b.date)));
  state.activeRebalanceIndex = state.modalPortfolio.rebalancePlan.findIndex((item) => item === node);
  els.addRebalanceDateInput.value = "";
  renderAdjustPortfolioModal();
}

async function importRebalanceExcel(event) {
  const file = event.target.files?.[0];
  if (!file) return;
  try {
    const body = await fetchJson("/api/rebalance-plan/import", {
      method: "POST",
      body: JSON.stringify({
        filename: file.name,
        contentBase64: await fileToBase64(file),
      }),
    });
    const plan = body.rebalancePlan || [];
    plan.forEach((node) => {
      if (node.cashFlow === undefined || node.cashFlow === null) node.cashFlow = 0;
      ensureNodeSleeveTargets(node);
    });
    state.modalPortfolio.rebalancePlan = plan;
    state.activeRebalanceIndex = 0;
    renderAdjustPortfolioModal();
    setStatus(`已导入 ${plan.length} 个调仓日，确认后再保存组合。`, "ok");
  } catch (err) {
    setStatus(err.message || String(err), "error");
  } finally {
    event.target.value = "";
  }
}

async function fileToBase64(file) {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  const chunkSize = 0x8000;
  for (let index = 0; index < bytes.length; index += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(index, index + chunkSize));
  }
  return btoa(binary);
}

function parseDelimitedRows(text) {
  const lines = String(text || "").split(/\r?\n/).filter((line) => line.trim());
  if (!lines.length) throw new Error("导入文件为空");
  const delimiter = lines[0].includes("\t") ? "\t" : ",";
  const headers = lines[0].split(delimiter).map((item) => item.trim());
  return lines.slice(1).map((line) => {
    const values = line.split(delimiter);
    return Object.fromEntries(headers.map((header, index) => [header, (values[index] || "").trim()]));
  });
}

function planFromImportedRows(rows) {
  const grouped = new Map();
  rows.forEach((row) => {
    const date = row["调仓日期"];
    const fund = matchImportedFund(row["基金代码"], row["基金名称"]);
    if (!date || !parseDate(date)) throw new Error(`日期非法：${date || "空"}`);
    if (!fund) throw new Error(`基金无法匹配：${row["基金代码"] || row["基金名称"] || "空"}`);
    const sleeveId = sleeveIdFromChinese(row["子组合"]);
    const weight = Number(row["权重%"]);
    if (!Number.isFinite(weight)) throw new Error(`权重非法：${row["基金名称"] || fund.name}`);
    grouped.set(date, grouped.get(date) || []);
    grouped.get(date).push({ fundId: fund.id, sleeveId, weight });
  });
  const plan = [...grouped.entries()].map(([date, holdings]) => ({ date, source: "excel_imported", holdings }));
  plan.forEach((node) => ensureNodeSleeveTargets(node));
  const validation = validateRebalancePlan(plan);
  if (!validation.ok) throw new Error(validation.messages.join("；"));
  return plan.sort((a, b) => String(a.date).localeCompare(String(b.date)));
}

async function confirmAdjustPortfolioModal() {
  if (!state.modalPortfolio) return;
  updateCashSettingsFromInputs();
  state.modalPortfolio.name = els.portfolioNameInput.value.trim();
  state.modalPortfolio.startDate = els.modalStartDate.value;
  state.modalPortfolio.endDate = els.modalEndDate.value;
  state.modalPortfolio.benchmark = els.benchmarkSelect.value || "000300.SH";
  state.modalPortfolio.rebalanceFrequency = els.rebalanceFrequencySelect.value || "quarterly";
  state.modalPortfolio.globalTemplate = els.currentTemplateSelect?.value || state.modalPortfolio.globalTemplate || "equal";
  if (!state.modalPortfolio.name) {
    setStatus("组合名称不能为空。", "error");
    return;
  }
  if (state.modalPortfolio.initialCash !== undefined && Number(state.modalPortfolio.initialCash || 0) <= 0) {
    setStatus("初始资金必须大于 0。", "error");
    return;
  }
  const validation = validateRebalancePlan(state.modalPortfolio.rebalancePlan || []);
  if (!validation.ok) {
    renderRebalanceValidation();
    return;
  }
  state.currentPortfolio = clonePortfolio(state.modalPortfolio);
  state.currentPortfolioId = state.currentPortfolio.id || null;
  state.isPortfolioDirty = true;
  syncPortfolioControls(state.currentPortfolio);
  portfolioToSleeves(state.currentPortfolio);
  syncCurrentPortfolioSummary();
  els.confirmAdjustModalBtn.disabled = true;
  try {
    const saved = await saveCurrentPortfolio(false);
    if (!saved) return;
    closeAdjustPortfolioModal();
    renderPortfolioHome();
  } catch (err) {
    setStatus(err.message || String(err), "error");
  } finally {
    els.confirmAdjustModalBtn.disabled = false;
  }
}

function portfolioSavePayload(portfolio) {
  const payload = { ...(portfolio || {}) };
  delete payload.backtestSnapshots;
  delete payload.lastBacktestSnapshot;
  delete payload.latestHoldings;
  return payload;
}

function applySavedPortfolioCacheState(previous, saved) {
  if (saved.hasBacktest === false) {
    state.result = null;
    state.analysisAttribution = null;
    return {
      ...previous,
      ...saved,
      backtestSnapshots: {},
      lastBacktestSnapshot: null,
      latestHoldings: saved.latestHoldings || [],
    };
  }
  return {
    ...previous,
    ...saved,
    backtestSnapshots: saved.backtestSnapshots || previous.backtestSnapshots || {},
    lastBacktestSnapshot: saved.lastBacktestSnapshot || previous.lastBacktestSnapshot || null,
    latestHoldings: saved.latestHoldings || previous.latestHoldings || [],
  };
}

async function saveCurrentPortfolio(runAfterSave = false) {
  if (!state.currentPortfolio) return null;
  const validation = validateRebalancePlan(state.currentPortfolio.rebalancePlan || []);
  if (!validation.ok) {
    setStatus(validation.messages.join("；"), "error");
    return null;
  }
  const payload = portfolioSavePayload({
    ...state.currentPortfolio,
    benchmark: els.benchmarkSelect.value || state.currentPortfolio.benchmark || "000300.SH",
    startDate: els.startDate.value || state.currentPortfolio.startDate,
    endDate: els.endDate.value || state.currentPortfolio.endDate,
    dataFrequency: currentDataFrequency(),
  });
  const path = state.currentPortfolioId ? `/api/portfolios/${encodeURIComponent(state.currentPortfolioId)}` : "/api/portfolios";
  const method = state.currentPortfolioId ? "PUT" : "POST";
  const saved = await fetchJson(path, { method, body: JSON.stringify(payload) });
  const previous = state.currentPortfolio || {};
  state.currentPortfolio = applySavedPortfolioCacheState(previous, saved);
  state.currentPortfolioId = saved.id;
  state.isPortfolioDirty = false;
  portfolioToSleeves(state.currentPortfolio);
  syncPortfolioControls(state.currentPortfolio);
  if (!runAfterSave) await loadPortfolios(saved.id);
  if (!runAfterSave) {
    renderPortfolioHome();
    setStatus(`已保存组合：${saved.name}`, "ok");
  }
  return saved;
}

async function runBacktest(switchView = true) {
  if (!state.currentPortfolio) {
    setStatus("请先新建或选择组合。", "error");
    return;
  }
  const validation = validateRebalancePlan(state.currentPortfolio.rebalancePlan || []);
  if (!validation.ok) {
    setStatus(validation.messages.join("；"), "error");
    return;
  }
  els.runBtn.disabled = true;
  setStatus("正在回测...", "");
  try {
    const saved = await saveCurrentPortfolio(true);
    if (!saved) return;
    const body = await fetchJson(`/api/portfolios/${encodeURIComponent(saved.id)}/backtest`, { method: "POST" });
    state.currentPortfolio = {
      ...saved,
      ...(body.portfolio || {}),
      backtestSnapshots: saved.backtestSnapshots || {},
      lastBacktestSnapshot: body.result || saved.lastBacktestSnapshot || null,
      latestHoldings: body.portfolio?.latestHoldings || saved.latestHoldings || [],
    };
    state.currentPortfolioId = state.currentPortfolio.id;
    state.result = body.result;
    state.currentBacktestPayload = buildBacktestPayload();
    state.isPortfolioDirty = false;
    portfolioToSleeves(state.currentPortfolio);
    syncPortfolioControls(state.currentPortfolio);
    await refreshPortfolioList(state.currentPortfolio.id);
    renderResult(state.result, switchView);
    renderPortfolioHome();
    setStatus(backtestCompleteStatusText(state.currentPortfolio), "ok");
  } catch (err) {
    setStatus(err.message || String(err), "error");
  } finally {
    els.runBtn.disabled = false;
  }
}

function buildBacktestPayload() {
  const portfolio = state.currentPortfolio || createEmptyPortfolio();
  return {
    funds: fundsFromPlan(portfolio.rebalancePlan),
    sleeves: buildSleevePayload(),
    weightMode: "manual",
    benchmark: els.benchmarkSelect.value || portfolio.benchmark || "000300.SH",
    startDate: els.startDate.value || portfolio.startDate,
    endDate: els.endDate.value || portfolio.endDate,
    dataFrequency: currentDataFrequency(),
    rebalancePlan: portfolio.rebalancePlan || [],
    initialCash: portfolio.initialCash,
    feeConfig: portfolio.feeConfig,
  };
}

function buildSleevePayload() {
  const portfolio = state.currentPortfolio || state.modalPortfolio;
  const bySleeve = new Map(sleeveDefs.map((sleeve) => [sleeve.id, new Set()]));
  (portfolio?.rebalancePlan || []).forEach((node) => {
    (node.holdings || []).forEach((holding) => {
      const sleeveId = normalizeSleeveId(holding.sleeveId);
      if (bySleeve.has(sleeveId)) bySleeve.get(sleeveId).add(holding.fundId);
    });
  });
  return sleeveDefs
    .map((sleeve) => ({ id: sleeve.id, name: sleeve.name, funds: [...bySleeve.get(sleeve.id)] }))
    .filter((sleeve) => sleeve.funds.length);
}

function renderResult(result, switchView = true) {
  els.results.classList.remove("hidden");
  const rebalanceCount = Math.max((result.rebalance_dates || []).length - 1, 0);
  const pointCount = performancePointCount(result) || result.window.days;
  els.windowText.textContent = `${result.window.start} 至 ${result.window.end}，${pointCount} 个${frequencyLabel(result.frequency)}观察点，调仓 ${rebalanceCount} 次`;
  renderPortfolioOverview(result);
  renderMetrics(result.metrics, result);
  renderWarnings(result.warnings || []);
  renderScopeSelectors();
  resetPerformanceWindow(result);
  resetAnalysisWindow(result);
  renderCharts(result);
  renderAnnualContributionTable(result);
  renderAttributionView(result);
  renderExposureView(result);
  setResultView(switchView ? "backtest" : state.resultView);
}

function handleResultTabClick(event) {
  const button = event.target.closest("[data-result-view]");
  if (!button) return;
  const view = button.dataset.resultView;
  if (view === "attribution" && !state.result) {
    setStatus("请先运行回测。", "error");
    return;
  }
  setResultView(view);
}

function setResultView(view) {
  const normalized = view === "home" || view === "portfolioHome" ? "backtest" : (view === "exposure" ? "attribution" : view);
  state.resultView = normalized;
  els.resultTabs.querySelectorAll("[data-result-view]").forEach((button) => {
    button.classList.toggle("active", button.dataset.resultView === normalized);
  });
  const isAnalysisView = normalized === "attribution";
  els.shell.classList.remove("analysis-mode");
  els.libraryPanel.classList.remove("hidden");
  if (normalized === "backtest") renderPortfolioHome();
  else els.portfolioHomeView.classList.add("hidden");
  els.status.classList.toggle("hidden", isAnalysisView);
  els.results.classList.toggle("hidden", normalized !== "backtest" || !state.result);
  els.attributionView.classList.toggle("hidden", normalized !== "attribution");
  if (normalized === "attribution" && state.result) renderAttributionView(activeAnalysisResult());
  if (normalized === "attribution" && state.result) renderExposureView(state.result);
  if (normalized !== "attribution") {
    closeFundDrawer();
    closeExposureFundDrawer();
  }
  if (normalized === "backtest" && state.result) renderCharts(state.result);
}

function renderBasket() {
  renderPortfolioHome();
}

function validateRebalancePlan(plan) {
  const messages = [];
  if (!Array.isArray(plan) || !plan.length) {
    return { ok: false, messages: ["调仓计划为空"] };
  }
  plan.forEach((node) => {
    if (!parseDate(node.date)) messages.push(`${node.date || "空日期"}：日期非法`);
    if (!Array.isArray(node.holdings) || !node.holdings.length) {
      messages.push(`${node.date || "空日期"}：空调仓日`);
      return;
    }
    const seen = new Set();
    let total = 0;
    node.holdings.forEach((holding) => {
      if (!holding.fundId) messages.push(`${node.date}：基金为空`);
      if (seen.has(holding.fundId)) messages.push(`${node.date}：重复基金 ${holding.fundId}`);
      seen.add(holding.fundId);
      total += holdingWeightPercent(holding);
    });
    if (Math.abs(total - 100) > 0.01) messages.push(`${node.date}：权重 ${total.toFixed(2)}%，需 100%`);
    const targets = ensureNodeSleeveTargets(node);
    const targetTotal = sleeveDefs.reduce((sum, sleeve) => sum + Number(targets[sleeve.id] || 0), 0);
    if (Math.abs(targetTotal - 100) > 0.01) {
      messages.push(`${node.date}：子组合目标 ${targetTotal.toFixed(2)}%，需 100%`);
    }
    sleeveDefs.forEach((sleeve) => {
      const target = Number(targets[sleeve.id] || 0);
      const actual = sleeveWeightForNode(node, sleeve.id);
      if (Math.abs(actual - target) > 0.01) {
        messages.push(`${node.date}：${sleeve.name} ${actual.toFixed(2)}%，目标 ${target.toFixed(2)}%`);
      }
    });
  });
  if (!messages.length) messages.push("校验通过：目标持仓完整");
  return { ok: messages.length === 1 && messages[0].startsWith("校验通过"), messages };
}

function activeNode() {
  return state.modalPortfolio?.rebalancePlan?.[state.activeRebalanceIndex] || null;
}

function latestPlanNode(portfolio) {
  const plan = portfolio?.rebalancePlan || [];
  if (!plan.length) return null;
  return [...plan].sort((a, b) => String(a.date).localeCompare(String(b.date))).at(-1);
}

function holdingsPayloadFromPlan(node) {
  if (!node) return [];
  return (node.holdings || []).map((holding) => {
    const fund = fundMeta(holding.fundId);
    return {
      fundId: holding.fundId,
      sleeveId: holding.sleeveId,
      name: fund?.name || holding.fundId,
      manager: fund?.manager || "",
      weight: holdingWeightPercent(holding) / 100,
      latestNav: null,
      navDate: "",
      dailyReturn: null,
      ytdReturn: null,
    };
  });
}

function fundsFromPlan(plan) {
  const ids = [];
  (plan || []).forEach((node) => {
    (node.holdings || []).forEach((holding) => {
      if (holding.fundId && !ids.includes(holding.fundId)) ids.push(holding.fundId);
    });
  });
  return ids.map((id) => ({ id }));
}

function clonePortfolio(portfolio) {
  return JSON.parse(JSON.stringify(portfolio || createEmptyPortfolio()));
}

function clonePlanNode(node) {
  return JSON.parse(JSON.stringify(node));
}

function cloneHoldings(holdings) {
  return JSON.parse(JSON.stringify(holdings || []));
}

function fundMeta(fundId) {
  return state.fundsById.get(fundId);
}

function fundName(fundId) {
  return fundMeta(fundId)?.name || fundId || "-";
}

function holdingWeightPercent(holding) {
  const value = Number(holding?.weight || 0);
  return value <= 1 ? value * 100 : value;
}

function normalizeSleeveId(value) {
  if (value === "defensive" || value === "防守" || value === "防守压仓") return "defensive";
  if (value === "balanced" || value === "stable" || value === "稳健" || value === "核心稳健") return "balanced";
  if (value === "growth" || value === "aggressive" || value === "进攻" || value === "进攻弹性" || value === "进攻增强") return "growth";
  return "balanced";
}

function sleeveIdFromChinese(value) {
  return normalizeSleeveId(String(value || "").trim());
}

function defaultSleeveForFund(fund) {
  const clusterId = clusterIdForFund(fund || {});
  if (clusterId === "defensive_cluster") return "defensive";
  if (clusterId === "growth_cluster") return "growth";
  return "balanced";
}

function sourceLabel(source) {
  return {
    initial: "初始",
    manual: "手动调仓",
    template: "全局设置",
    forward_applied: "后续覆盖",
    excel_imported: "导入",
  }[source] || "手动调仓";
}

function normalizeGlobalTemplate(value) {
  if (value === "defensive" || value === "stable" || value === "aggressive") return value;
  return "equal";
}

function normalizeRebalanceFrequency(value) {
  return ["none", "monthly", "quarterly", "semiannual"].includes(value) ? value : "quarterly";
}

function matchImportedFund(code, name) {
  const cleanCode = String(code || "").trim();
  const cleanName = String(name || "").trim();
  if (cleanCode && state.fundsById.has(cleanCode)) return state.fundsById.get(cleanCode);
  return state.funds.find((fund) => cleanName && fund.name === cleanName) || null;
}

function parseDate(value) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatDateInput(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}
