from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_weight_template_options_are_available() -> None:
    html = (ROOT / "backtest_platform/static/index.html").read_text(encoding="utf-8")

    adjust_modal = html[html.index('id="adjustPortfolioModal"') : html.index('id="feeSettingsModal"')]
    assert 'id="globalTemplateSelect"' not in adjust_modal
    assert "全局调仓模板" not in adjust_modal
    assert 'id="rebalanceFrequencySelect"' in adjust_modal
    assert '<option value="none">无</option>' in adjust_modal
    assert '<option value="monthly">月度</option>' in adjust_modal
    assert '<option value="quarterly" selected>季度</option>' in adjust_modal
    assert '<option value="semiannual">半年度</option>' in adjust_modal
    assert adjust_modal.index("应用到后续调仓日") < adjust_modal.index("仅修改当前调仓日")
    assert 'value="forward" checked' in adjust_modal
    assert "应用到指定日期前" not in adjust_modal
    assert 'id="applyForwardBtn"' not in adjust_modal
    assert 'class="rebalance-rule-panel"' not in adjust_modal
    toolbar = adjust_modal[adjust_modal.index('<div class="adjust-toolbar">') : adjust_modal.index('<div class="adjust-layout">')]
    actions = adjust_modal[adjust_modal.index('<div class="modal-actions">') :]
    current_day_head = adjust_modal[adjust_modal.index('id="activeRebalanceDateTitle"') : adjust_modal.index('<div class="cash-flow-strip compact">')]
    cash_flow_strip = adjust_modal[adjust_modal.index('<div class="cash-flow-strip compact">') : adjust_modal.index('<div id="modalHoldingTable"')]
    assert "应用到后续调仓日" in toolbar
    assert "仅修改当前调仓日" in toolbar
    assert 'id="generateGlobalPlanBtn"' not in toolbar
    assert actions.index('id="generateGlobalPlanBtn"') < actions.index('id="confirmAdjustModalBtn"')
    assert 'id="generateGlobalPlanBtn" class="primary-btn"' in actions
    assert 'id="rebalanceValidation"' not in current_day_head
    assert cash_flow_strip.index('id="cashEstimatePanel"') < cash_flow_strip.index('id="rebalanceValidation"')


def test_weight_template_ratios_are_defined_in_frontend_logic() -> None:
    js = (ROOT / "backtest_platform/static/app.js").read_text(encoding="utf-8")

    assert 'template_defensive: { defensive: 50, balanced: 40, growth: 10 }' in js
    assert 'template_stable: { defensive: 30, balanced: 40, growth: 30 }' in js
    assert 'template_aggressive: { defensive: 10, balanced: 30, growth: 60 }' in js
    assert "globalTemplateSelect" not in js
    assert "applyForwardBtn" not in js
    assert "applyUntilDate" not in js
    assert "globalPlanConfirmModal" not in js
    assert "function globalTemplateRatios(template)" in js
    assert "function generateRebalancePlanFromCurrentNode()" in js
    assert "function frequencyRebalanceDates" in js
    assert "function nextTradingDate" in js
    assert "function pruneFutureRebalancePlanByFrequency(currentNode, generatedDates)" in js
    apply_fn = js[js.index("function applyCurrentNodeToFutureNodes") : js.index("function openAddRebalanceDatePicker")]
    assert "pruneFutureRebalancePlanByFrequency(node, generatedDates);" in apply_fn
    assert "if (String(item.date || \"\") <= currentDate) return true;" in js
    assert "return allowedDates.has(String(item.date || \"\"));" in js
    assert 'state.modalPortfolio.rebalanceFrequency = els.rebalanceFrequencySelect.value || "quarterly"' in js
