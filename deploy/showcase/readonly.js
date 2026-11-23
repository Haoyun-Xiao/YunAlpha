(function () {
  "use strict";

  const path = window.location.pathname;
  const isMacroBacktest = path.startsWith("/YunAlpha/workspaces/pring/");
  const isFundDesk = path.startsWith("/YunAlpha/workspaces/fof/");
  if (!isMacroBacktest && !isFundDesk) return;

  function addBadge() {
    const badge = document.createElement("div");
    badge.innerHTML = '<strong style="display:block;font-size:16px;line-height:1.25">访客只读模式</strong><span style="display:block;margin-top:4px;font-size:12px;line-height:1.3;opacity:.82">可交互 · 不保存更改</span>';
    badge.style.cssText = "position:fixed;right:18px;bottom:18px;z-index:2147483646;min-width:154px;padding:12px 16px;border:1px solid rgba(255,255,255,.28);border-radius:6px;background:#173d67;color:#fff;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC',sans-serif;box-shadow:0 8px 24px rgba(0,0,0,.26);text-align:left";
    badge.title = "可浏览、载入和临时回测；不能保存、修改或删除公共策略";
    badge.setAttribute("role", "status");
    badge.setAttribute("aria-label", "访客只读模式，可交互，不保存更改");
    document.body.appendChild(badge);
  }

  function hide(selector) {
    document.querySelectorAll(selector).forEach((node) => {
      node.style.setProperty("display", "none", "important");
    });
  }

  function guardDynamicButtons() {
    if (isMacroBacktest) {
      hide("#saveStrategyBtn,#tagManagerBtn,#strategyModal,#tagManagerModal,[data-strategy-action='edit'],[data-strategy-action='duplicate'],[data-strategy-action='delete']");
    }
    if (isFundDesk) {
      hide("#newPortfolioBtn,#portfolioBulkToggleBtn,#portfolioDeleteConfirmModal,[data-delete-portfolio],[data-move-portfolio]");
    }
  }

  async function runFundDeskBacktest(event) {
    event.preventDefault();
    event.stopImmediatePropagation();
    if (!state.currentPortfolio) {
      setStatus("请先选择已有组合。", "error");
      return;
    }
    const validation = validateRebalancePlan(state.currentPortfolio.rebalancePlan || []);
    if (!validation.ok) {
      setStatus(validation.messages.join("；"), "error");
      return;
    }
    els.runBtn.disabled = true;
    setStatus("正在进行访客临时回测...", "");
    try {
      const payload = portfolioSavePayload({
        ...state.currentPortfolio,
        benchmark: els.benchmarkSelect.value || state.currentPortfolio.benchmark || "000300.SH",
        startDate: els.startDate.value || state.currentPortfolio.startDate,
        endDate: els.endDate.value || state.currentPortfolio.endDate,
        dataFrequency: currentDataFrequency(),
      });
      const result = await fetchJson("/api/backtest", { method: "POST", body: JSON.stringify(payload) });
      state.result = result;
      state.currentBacktestPayload = payload;
      state.currentPortfolio = {
        ...state.currentPortfolio,
        lastBacktestSnapshot: result,
        backtestSnapshots: {
          ...(state.currentPortfolio.backtestSnapshots || {}),
          [currentDataFrequency()]: result,
        },
      };
      renderResult(result, true);
      renderPortfolioHome();
      setStatus("临时回测完成，结果未写入公共组合。", "ok");
    } catch (error) {
      setStatus(error.message || String(error), "error");
    } finally {
      els.runBtn.disabled = false;
    }
  }

  function applyFundDeskAdjustment(event) {
    event.preventDefault();
    event.stopImmediatePropagation();
    if (!state.modalPortfolio) return;
    updateCashSettingsFromInputs();
    state.modalPortfolio.name = els.portfolioNameInput.value.trim();
    state.modalPortfolio.startDate = els.modalStartDate.value;
    state.modalPortfolio.endDate = els.modalEndDate.value;
    state.modalPortfolio.benchmark = els.benchmarkSelect.value || "000300.SH";
    state.modalPortfolio.rebalanceFrequency = els.rebalanceFrequencySelect.value || "quarterly";
    state.modalPortfolio.globalTemplate = els.currentTemplateSelect?.value || state.modalPortfolio.globalTemplate || "equal";
    const validation = validateRebalancePlan(state.modalPortfolio.rebalancePlan || []);
    if (!validation.ok) {
      renderRebalanceValidation();
      return;
    }
    const originalId = state.currentPortfolioId;
    state.currentPortfolio = clonePortfolio(state.modalPortfolio);
    state.currentPortfolio.id = originalId;
    state.currentPortfolioId = originalId;
    state.isPortfolioDirty = true;
    syncPortfolioControls(state.currentPortfolio);
    portfolioToSleeves(state.currentPortfolio);
    syncCurrentPortfolioSummary();
    closeAdjustPortfolioModal();
    renderPortfolioHome();
    setStatus("临时调整已应用；运行回测不会修改公共组合。", "ok");
  }

  async function runExistingMacroStrategy(event) {
    const button = event.target.closest("[data-strategy-action='rerun']");
    if (!button) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    const strategy = state.strategies.find((item) => item.id === button.dataset.id);
    if (!strategy || !loadStrategyIntoWorkspace(strategy)) return;
    button.disabled = true;
    setStatus(`正在临时回测策略“${strategy.name}”...`, "");
    try {
      const result = await apiRequest("/api/backtest", { method: "POST", body: strategy.config });
      state.result = result;
      state.lastSuccessfulConfigSignature = currentConfigSignature();
      renderResult(result);
      setStatus(`策略“${strategy.name}”临时回测完成，结果未写入公共策略库。`, "ok");
    } catch (error) {
      setStatus(error.message || String(error), "error");
    } finally {
      button.disabled = false;
    }
  }

  function initialize() {
    addBadge();
    guardDynamicButtons();
    new MutationObserver(guardDynamicButtons).observe(document.body, { childList: true, subtree: true });
    if (isFundDesk) {
      const confirmButton = document.getElementById("confirmAdjustModalBtn");
      if (confirmButton) {
        confirmButton.textContent = "临时应用";
        confirmButton.addEventListener("click", applyFundDeskAdjustment, true);
      }
      document.getElementById("runBtn")?.addEventListener("click", runFundDeskBacktest, true);
    }
    if (isMacroBacktest) {
      document.getElementById("strategyCards")?.addEventListener("click", runExistingMacroStrategy, true);
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initialize);
  else initialize();
})();
