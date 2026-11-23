from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_result_area_uses_overview_and_side_metric_table() -> None:
    html = (ROOT / "backtest_platform/static/index.html").read_text(encoding="utf-8")
    js = (ROOT / "backtest_platform/static/app.js").read_text(encoding="utf-8")
    css = (ROOT / "backtest_platform/static/styles.css").read_text(encoding="utf-8")

    assert "回测分析" not in html
    assert "组合概览" in html
    assert "回测结果" not in html
    assert 'id="portfolioHomeName"' not in html
    assert 'id="portfolioHomeMeta"' not in html
    assert "portfolioHomeName" not in js
    assert "portfolioHomeMeta" not in js
    assert html.index('id="portfolioOverview"') < html.index('id="performanceChart"') < html.index('id="metrics"') < html.index('id="annualReturnChart"')
    assert 'class="home-performance-layout"' in html
    assert 'class="metrics metric-table-panel"' in html
    assert 'id="returnChart"' in html
    assert 'id="drawdownChart"' in html
    assert 'id="performanceModeSelect"' in html
    assert '<option value="return">收益率曲线</option>' in html
    assert '<option value="asset">资产曲线</option>' in html
    assert 'id="accountValueChart"' not in html
    assert "数据概览" in js
    assert "收益风险指标" in js
    assert "组合管理指标" in js
    assert "function renderPortfolioOverview" in js
    assert ".home-performance-layout" in css
    overview_css = css[css.index(".portfolio-overview") : css.index(".overview-title")]
    assert "background: #ffffff;" in overview_css
    assert "background: #f3f5fa;" not in overview_css
    assert ".metric-group table" in css
    metric_td_css = css[css.index(".metric-group td {") : css.index(".metric-placeholder")]
    assert "text-align: left;" in metric_td_css
    assert "text-align: right;" not in metric_td_css
    compact_metric_css = css[css.index(".metric-group th,") : css.index(".metric-group th {")]
    assert "padding: 5px 0;" in compact_metric_css
    assert "line-height: 1.28;" in compact_metric_css
    assert "累计收益" in html
    assert "回撤走势" in html
    assert "累计资产走势" not in html
    assert 'id="returnChartLegend"' in html
    assert 'id="drawdownChartLegend"' in html
    assert 'id="returnChartMetaPrefix"' not in html
    assert "returnChartMetaPrefix" not in js
    assert 'id="returnBenchmarkLabel"' not in html
    assert "returnBenchmarkLabel" not in js
    assert 'id="returnChartMetaSuffix"' not in html
    assert "returnChartMetaSuffix" not in js
    assert 'id="drawdownChartMetaPrefix"' not in html
    assert "drawdownChartMetaPrefix" not in js
    assert 'id="drawdownBenchmarkLabel"' not in html
    assert "drawdownBenchmarkLabel" not in js
    assert 'class="performance-window-toolbar"' in html
    assert 'id="performanceRangeStart"' in html
    assert 'id="performanceRangeEnd"' in html
    assert 'id="performanceRangePresets"' in html
    assert 'id="performanceRangeStartDate"' in html
    assert 'id="performanceRangeEndDate"' in html
    assert 'for="performanceRangeStartDate">起点' not in html
    assert 'for="performanceRangeEndDate">终点' not in html
    assert html.index('data-range-preset="all"') < html.index('id="performanceRangeStartDate"') < html.index('id="performanceRangeEndDate"')
    assert html.index('id="performanceRangePresets"') < html.index('id="returnChart"') < html.index('class="performance-range"')
    assert "今年以来" in html
    assert "近3月" in html
    assert "成立以来" in html
    assert ">显示区间<" not in html
    assert 'height="300"' in html
    assert 'height="120"' in html
    assert 'height="360"' not in html
    assert 'height="390"' not in html
    assert 'id="annualTable"' not in html
    assert 'id="annualReturnChart"' in html
    assert "drawPerformanceCharts(result)" in js
    assert "function drawAssetPerformanceChart" in js
    assert "function renderChartInlineLegend" in js
    assert "hideLegend: true" in js
    assert "function assetReturnPoints" in js
    assert "function drawMoneyAxis" in js
    assert "formatMoney(point.amount)" in js
    assert "moneyAxisBase" in js
    assert "handlePerformanceRangePresetClick" in js
    assert "series.account_value" in js
    assert "function effectivePerformanceMode" in js
    assert "function performanceWindowSeries" in js
    assert "function handlePerformanceDateInput" in js
    assert "function syncPerformanceDateInputs" in js
    assert "function closestPerformancePointIndex" in js
    assert "function resetReturnPoints" in js
    assert "yearAxis: true" in js
    assert "function drawYearAxis" in js
    assert "function yearTicksFromPoints" in js
    assert "function formatPercentIntegerLabel" in js
    assert "drawAxes(ctx, width, height, pad, min, max, true, formatPercentIntegerLabel)" in js
    assert "drawAxes(ctx, width, height, pad, min, max, true, formatPercentIntegerLabel);" in js
    assert 'portfolio: "#c94a4a"' in js
    assert 'benchmark: "#2f6f9f"' in js
    assert 'drawdown: "#c94a4a"' in js
    assert 'excess: "#7a8795"' in js
    assert 'excessFill: "rgba(122, 135, 149, 0.26)"' in js
    assert '{ label: "累计超额", points: excess, color: colors.excessFill, fill: true, hideLegend: true }' in js
    assert "items.filter((item) => !item.hideLegend).forEach" not in js
    assert ".chart-inline-legend" in css
    assert ".chart-inline-legend i" in css
    assert ".performance-window-toolbar" in css
    assert ".performance-date-controls" in css
    date_control_css = css[css.index(".performance-date-controls") : css.index(".performance-date-controls input")]
    assert "margin-left: auto;" not in date_control_css
    assert "minmax(182px, 1.45fr) minmax(182px, 1.45fr) repeat(5, minmax(92px, 0.8fr))" in css
    assert "Math.min(0, value / peak - 1)" in js
    assert "strongZeroAxis: true" in js
    assert "function drawStrongZeroAxis" in js
    assert "ctx.rect(pad.left, plotTop" in js
    assert "ctx.lineWidth = 1.4;" in js
    assert ".range-dual input[type=\"range\"]::-webkit-slider-thumb" in css
    performance_range_css = css[css.index(".range-dual") : css.index(".range-dual input")]
    assert ".range-dual::before" not in performance_range_css
    assert "height: 14px;" in performance_range_css
    assert "top: 3px;" in performance_range_css
    assert "height: 8px;" in performance_range_css
    assert "rgba(47, 111, 159, 0.18)" in performance_range_css


def test_performance_range_handles_are_rectangular_buttons() -> None:
    css = (ROOT / "backtest_platform/static/styles.css").read_text(encoding="utf-8")

    assert '.range-dual input[type="range"]::-webkit-slider-thumb' in css
    thumb_css = css[css.index('.range-dual input[type="range"]::-webkit-slider-thumb') : css.index('.range-dual input[type="range"]::-moz-range-track')]
    assert "width: 7px;" in thumb_css
    assert "height: 12px;" in thumb_css
    assert "border-radius: 2px;" in thumb_css
    assert "background: var(--blue);" in thumb_css


def test_annual_contribution_chart_uses_sleeve_contributions() -> None:
    html = (ROOT / "backtest_platform/static/index.html").read_text(encoding="utf-8")
    js = (ROOT / "backtest_platform/static/app.js").read_text(encoding="utf-8")
    css = (ROOT / "backtest_platform/static/styles.css").read_text(encoding="utf-8")

    assert 'id="annualContributionChart"' in html
    assert 'class="chart-grid result-return-grid"' in html
    assert html.index('id="annualReturnChart"') < html.index('id="annualContributionChart"') < html.index('id="periodReturnChart"') < html.index('id="periodContributionChart"')
    assert '<h3>年度收益</h3>' in html
    assert '<h3>年度收益拆解</h3>' in html
    assert '<h3>区间收益</h3>' in html
    assert '<h3>区间收益拆解</h3>' in html
    assert "<span>防守压仓 / 核心稳健 / 进攻弹性</span>" not in html
    assert 'id="annualReturnChart" height="210"' in html
    assert 'id="annualContributionChart" height="210"' in html
    assert 'id="periodReturnScope"' in html
    assert 'id="periodReturnChart" height="210"' in html
    assert 'id="periodContributionChart" height="210"' in html
    assert ".result-return-grid" in css
    assert ".chart-stack" in css
    assert "grid-template-columns: 260px minmax(0, 1fr);" in css
    assert "grid-template-columns: 240px minmax(0, 1fr);" in css
    assert "drawAnnualContributionChart(\"annualContributionChart\", annualSleeveContributionChartData(result))" in js
    assert "renderPeriodReturnModule(result)" in js
    assert "drawAnnualContributionChart(\"periodContributionChart\", periodSleeveContributionChartData(result))" in js
    assert "function annualSleeveContributionChartData(result)" in js
    assert "function periodSleeveContributionChartData(result)" in js
    assert "function periodReturnRowsForScope(result, scope)" in js
    assert js.count("drawAxes(ctx, width, height, pad, min, max, true, formatPercentIntegerLabel)") == 2
    assert "sumFundValues(row.funds, sleeve.funds.map((fund) => fund.id))" in js
    assert "drawAnnualContributionReturnLine" not in js
    annual_data_fn = js[js.index("function annualSleeveContributionChartData") : js.index("function periodSleeveContributionChartData")]
    assert "portfolio:" not in annual_data_fn
    assert "benchmark:" not in annual_data_fn
    assert "drawYAxisUnitLabel(ctx, \"收益率 %\", pad)" in js
    assert "ctx.textAlign = \"right\";" in js
    assert "pad.left - 8" in js
    assert "formatPercentLabel(value)" in js
    assert "function drawNonOverlappingLabel" in js
    assert "const contributionLabels = [];" in js
    assert "contributionLabels.forEach" in js
    assert "y + (value >= 0 ? -4 : 12)" in js
    assert "total >= 0 ? -8 : 14" in js
    assert "Math.min(18, Math.max(8, barGroup * 0.18))" in js
    assert "Math.min(32, Math.max(12, barGroup * 0.38))" in js
    assert "const pad = { left: 54, right: 18, top: 56, bottom: 34 };" in js
    assert "], pad.left, 20);" in js


def test_canvas_hover_uses_visible_canvas_coordinates_and_tolerant_bar_hitboxes() -> None:
    js = (ROOT / "backtest_platform/static/app.js").read_text(encoding="utf-8")

    assert "const rect = canvas.getBoundingClientRect();" in js
    assert "const width = rect.width || canvas.clientWidth || canvas.parentElement.clientWidth || 640;" in js
    assert "function canvasEventPoint(canvas, event, width, height)" in js
    assert "canvasEventPoint(canvas, event, width, height)" in js
    assert "const hitPaddingX = 4;" in js
    assert "const minHitHeight = 12;" in js
    assert "hitX" in js
    assert "x >= segment.hitX" in js
    line_hover_fn = js[js.index("function drawLineHover") : js.index("function drawBarTooltip")]
    tooltip_box_start = line_hover_fn.index("roundedRect(ctx, boxX, boxY, boxWidth, boxHeight, 6);")
    tooltip_text_start = line_hover_fn.index("lines.forEach((line, idx) => {")
    assert "ctx.textAlign = \"left\";" in line_hover_fn[tooltip_box_start:tooltip_text_start]


def test_top_benchmark_picker_replaces_inline_benchmark_select() -> None:
    html = (ROOT / "backtest_platform/static/index.html").read_text(encoding="utf-8")
    js = (ROOT / "backtest_platform/static/app.js").read_text(encoding="utf-8")

    assert 'id="benchmarkPickerBtn"' in html
    assert 'id="benchmarkModal"' in html
    assert 'id="benchmarkTypeFilter"' in html
    assert 'class="benchmark-field hidden"' in html
    assert "function openBenchmarkModal()" in js
    assert "els.benchmarkTypeFilter.value = \"index\"" in js
    assert "async function selectBenchmark" in js
    benchmark_handler = js[js.index("async function selectBenchmark") : js.index("function renderFunds")]
    assert "await runBacktest(false)" in benchmark_handler


def test_home_result_modules_remove_contribution_summary_and_rename_annual_breakdown() -> None:
    html = (ROOT / "backtest_platform/static/index.html").read_text(encoding="utf-8")
    js = (ROOT / "backtest_platform/static/app.js").read_text(encoding="utf-8")

    assert 'id="annualReturnScope"' in html
    assert 'id="annualContributionTableScope"' in html
    assert 'id="contributionTableScope"' not in html
    assert 'id="contributionTable"' not in html
    assert "收益贡献汇总" not in html
    assert "年度收益拆解" in html
    assert "renderContributionTable" not in js
    assert '"signed positive"' in js
    assert '"signed negative"' in js


def test_attribution_view_static_structure_and_payload_sleeves() -> None:
    html = (ROOT / "backtest_platform/static/index.html").read_text(encoding="utf-8")
    js = (ROOT / "backtest_platform/static/app.js").read_text(encoding="utf-8")

    assert 'id="resultTabs"' in html
    assert 'data-result-view="backtest"' in html
    assert 'data-result-view="attribution"' in html
    assert 'data-result-view="exposure"' not in html
    assert "归因分析" not in html
    assert "组合分析" in html
    assert 'id="attributionView"' in html
    assert 'id="attributionRows"' in html
    assert 'id="sleeveFundCorrelationList"' in html
    assert 'id="exposureView"' in html
    assert html.index('id="attributionRows"') < html.index('id="exposureView"')
    exposure_view_tag = html[html.index('id="exposureView"') : html.index(">", html.index('id="exposureView"'))]
    assert "hidden" not in exposure_view_tag
    assert 'id="fundAttributionDrawer"' in html
    assert "归因结论" not in html
    assert "attributionConclusionCards" not in html
    assert "attributionConclusionCards" not in js
    assert "function renderAttributionConclusions" not in js
    assert "波动贡献" in html
    assert "回撤贡献" in html
    assert "回撤期拖累" not in html
    assert "风险贡献" not in html
    assert "function buildSleevePayload()" in js
    assert "sleeves: buildSleevePayload()" in js
    assert "function renderAttributionView(result)" in js
    assert "function tooltipIcon" in js


def test_portfolio_library_remains_available_on_analysis_view() -> None:
    html = (ROOT / "backtest_platform/static/index.html").read_text(encoding="utf-8")
    js = (ROOT / "backtest_platform/static/app.js").read_text(encoding="utf-8")
    css = (ROOT / "backtest_platform/static/styles.css").read_text(encoding="utf-8")

    assert html.index('class="library-panel"') < html.index('id="attributionView"')
    final_set_view = js[js.rindex("function setResultView") : js.index("function renderBasket", js.rindex("function setResultView"))]
    assert 'els.libraryPanel.classList.toggle("hidden", analysisMode)' not in final_set_view
    assert 'els.libraryPanel.classList.remove("hidden")' in final_set_view
    assert 'els.shell.classList.toggle("analysis-mode", analysisMode)' not in final_set_view
    assert ".shell.analysis-mode {\n  grid-template-columns: minmax(0, 1fr);" not in css
    assert ".shell.analysis-mode.library-collapsed {\n  grid-template-columns: minmax(0, 1fr);" not in css


def test_attribution_view_has_temporary_analysis_window_control() -> None:
    html = (ROOT / "backtest_platform/static/index.html").read_text(encoding="utf-8")
    js = (ROOT / "backtest_platform/static/app.js").read_text(encoding="utf-8")
    css = (ROOT / "backtest_platform/static/styles.css").read_text(encoding="utf-8")

    assert 'id="analysisWindowStartLabel"' in html
    assert 'id="analysisWindowEndLabel"' in html
    assert 'id="analysisWindowStatus"' in html
    assert 'id="analysisWindowPresets"' in html
    assert 'id="analysisRangeStart"' in html
    assert 'id="analysisRangeEnd"' in html
    assert 'id="analysisRangeTrack"' in html
    assert html.index("<h2>组合分析</h2>") < html.index('id="analysisWindowPresets"') < html.index('id="attributionRows"')
    assert "近1月" in html
    assert "近3月" in html
    assert "近6月" in html
    assert "近1年" in html
    assert "近3年" in html
    assert "成立以来" in html

    assert "analysisWindow" in js
    assert "function resetAnalysisWindow" in js
    assert "function syncAnalysisRangeInputs" in js
    assert "function handleAnalysisRangeInput" in js
    assert "async function refreshAnalysisWindow" in js
    assert "/analysis-window" in js
    assert "function setAnalysisWindowStatus" in js
    assert 'setAnalysisWindowStatus("正在计算组合分析区间...")' in js
    assert 'setStatus("正在计算组合分析区间...", "")' not in js
    assert "renderAttributionView({ ...state.result, attribution: data.attribution })" in js
    refresh_fn = js[js.index("async function refreshAnalysisWindow") : js.index("function renderAttributionView")]
    assert "state.result =" not in refresh_fn
    assert "renderExposureView" not in refresh_fn

    assert ".analysis-window-control" in css
    assert ".analysis-range-row" in css
    assert ".analysis-window-status" in css
    assert ".analysis-window-status:empty" not in css
    assert ".analysis-range-dual" in css
    assert ".analysis-range-row .analysis-range-dual" in css
    assert "grid-column: 2;" in css
    analysis_range_css = css[css.index(".analysis-range-dual") : css.index(".analysis-range-dual input")]
    assert ".analysis-range-dual::before" not in analysis_range_css
    assert "height: 14px;" in analysis_range_css
    assert "top: 3px;" in analysis_range_css
    assert "height: 8px;" in analysis_range_css
    assert "rgba(47, 111, 159, 0.18)" in css
    assert "width: 7px;" in css
    assert "border-radius: 2px;" in css


def test_attribution_correlation_heatmaps_are_grouped_by_scope() -> None:
    html = (ROOT / "backtest_platform/static/index.html").read_text(encoding="utf-8")
    js = (ROOT / "backtest_platform/static/app.js").read_text(encoding="utf-8")
    css = (ROOT / "backtest_platform/static/styles.css").read_text(encoding="utf-8")

    assert "attribution-correlation-layout" in html
    assert "sleeve-correlation-panel" in html
    assert "category-timeline-panel" in html
    assert "大类配置时序" in html
    assert 'id="categoryTimelineLegend"' in html
    assert 'id="categoryTimelineChart"' in html
    assert "sleeve-fund-correlation-panel" in html
    assert "sleeveFundCorrelationList" in html
    assert "attribution-fund-correlation-panel" in html
    assert html.index("sleeveCorrelationMatrix") < html.index("categoryTimelineChart") < html.index("sleeveFundCorrelationList")
    assert html.index("sleeveFundCorrelationList") < html.index("fundCorrelationSummary")

    assert "function renderSleeveFundCorrelations" in js
    assert "sleeve_fund_matrices" in js
    assert "category_timeline" in js
    assert "function renderCategoryTimeline" in js
    assert "function drawCategoryTimelineChart" in js
    assert "function renderCategoryTimelineLegend" in js
    assert "function categoryTimelinePalette" in js
    assert "function updateCategoryTimelineLegendState" in js
    assert "function drawSmoothCategoryLine" in js
    assert "function fillSmoothCategoryArea" in js
    assert "function drawCategoryPointLabels" in js
    assert "bezierCurveTo" in js
    assert "drawCategoryPointLabels(ctx, topPoints, focusedSeries.values || [], color, pad)" in js
    point_label_fn = js[js.index("function drawCategoryPointLabels") : js.index("function drawCategoryTimelineChart")]
    assert "rgba(23, 32, 42, 0.86)" in point_label_fn
    assert "strokeText(text" in point_label_fn
    assert "roundedRect" not in point_label_fn
    assert "drawCategoryTimelineChart(timeline, null, button.dataset.categoryTimelineFocus)" in js
    assert "drawCategoryTimelineChart(timeline, null, null)" in js
    assert "categoryTimelineChart.onmousemove" in js
    assert "function correlationColumnWidth" in js
    assert "子集合内部基金相关性" in html
    assert 'correlationMatrixHtml(correlation.labels, correlation.matrix, "sleeve")' in js
    assert "sleeve-fund-correlation-row" in js
    assert '<article class="attribution-panel sleeve-fund-correlation-panel">' not in js
    assert 'correlationMatrixHtml(item.labels || [], item.matrix || [], "tight")' in js
    assert '<colgroup>' in js
    assert 'class="correlation-label-col"' in js
    assert 'class="correlation-value-col"' in js
    assert 'class="correlation-label-text"' in js
    assert "全部基金相关性热力图" in html
    assert "基金互补性摘要" not in html
    fund_correlation_fn = js[js.index("function renderFundCorrelationSummary") : js.index("function correlationMatrixHtml")]
    assert "fund-correlation-cards" not in fund_correlation_fn
    assert "correlationMetricHtml" not in fund_correlation_fn
    assert "<h4>全部基金相关性热力图</h4>" not in fund_correlation_fn
    assert "查看基金热力图" not in js
    assert "fund-matrix-details" not in js

    assert ".attribution-correlation-layout" in css
    attribution_correlation_css = css[css.index(".attribution-correlation-layout") : css.index(".sleeve-correlation-panel,")]
    assert "grid-template-columns: minmax(320px, 0.85fr) minmax(460px, 1.15fr);" in attribution_correlation_css
    assert ".category-timeline-panel" in css
    assert ".category-timeline-legend" in css
    assert ".category-timeline-panel canvas" in css
    assert ".sleeve-fund-correlation-list" in css
    assert "grid-template-columns: 92px minmax(0, 1fr);" in css
    assert "gap: 12px;" in css[css.index(".sleeve-fund-correlation-list") : css.index(".sleeve-fund-correlation-row")]
    sleeve_row_css = css[css.index(".sleeve-fund-correlation-row") : css.index(".sleeve-fund-correlation-row:first-child")]
    assert "padding-top: 12px;" in sleeve_row_css
    assert "border-top: 2px solid #cbd5e1;" in sleeve_row_css
    assert ".attribution-fund-correlation-panel" in css
    assert ".correlation-matrix.sleeve" in css
    sleeve_matrix_css = css[css.index(".correlation-matrix.sleeve") : css.index(".correlation-matrix.compact")]
    assert "width: calc(100% * 5 / 6);" in sleeve_matrix_css
    assert "min-width: 0;" in sleeve_matrix_css
    assert ".correlation-matrix.compact" in css
    assert ".correlation-matrix.tight" in css
    correlation_css = css[css.index(".correlation-matrix {") : css.index(".correlation-matrix.compact")]
    assert "width: 100%;" in correlation_css
    assert "min-width: 420px;" in correlation_css
    assert "table-layout: fixed;" in correlation_css
    assert "border: 1px solid #cbd5e1;" in correlation_css
    correlation_cell_css = css[css.index(".correlation-matrix th,") : css.index(".correlation-matrix th {")]
    assert "min-width: 50px;" in correlation_cell_css
    assert "height:" not in correlation_cell_css
    assert "box-sizing: border-box;" in correlation_cell_css
    assert "vertical-align: middle;" in correlation_cell_css
    assert ".correlation-label-text" in css
    label_text_start = css.index(".correlation-label-text")
    label_text_css = css[label_text_start : css.index(".correlation-matrix td {", label_text_start)]
    assert "-webkit-line-clamp: 2;" in label_text_css
    assert "-webkit-box-orient: vertical;" in label_text_css
    assert "overflow: hidden;" in label_text_css
    assert ".correlation-label-col" in css
    assert ".correlation-value-col" in css
    label_col_css = css[css.index(".correlation-label-col") : css.index(".correlation-value-col")]
    assert "width: var(--correlation-col-width, 46px);" in label_col_css
    assert "min-width: 30px;" in css
    assert "const minColumnWidth = variant === \"tight\" ? 38 : variant === \"compact\" ? 44 : 56;" in js
    assert "Math.max(variant === \"tight\" ? 0 : 420" in js
    assert "const columnWidth = correlationColumnWidth(labels, variant);" in js
    assert "--correlation-col-width:${columnWidth}px" in js
    assert "white-space: normal;" in css
    assert "overflow-wrap: anywhere;" in css
    assert "word-break: break-word;" in css
    correlation_color_fn = js[js.index("function correlationColor") : js.index("function openFundDrawer")]
    assert "0.12 + Math.min(Math.abs(Number(value)), 1) * 0.62" in correlation_color_fn
    assert "rgba(201, 74, 74," in correlation_color_fn
    assert "rgba(68, 178, 76," in correlation_color_fn
    assert "rgba(47, 111, 159," not in correlation_color_fn


def test_result_tabs_live_inside_header_next_to_title() -> None:
    html = (ROOT / "backtest_platform/static/index.html").read_text(encoding="utf-8")
    css = (ROOT / "backtest_platform/static/styles.css").read_text(encoding="utf-8")

    header_start = html.index('<header class="topbar">')
    header_end = html.index("</header>")
    tabs_index = html.index('id="resultTabs"')

    assert header_start < tabs_index < header_end
    assert 'class="top-title"' in html
    assert "body:has(.shell.analysis-mode) .top-benchmark" not in css
    assert "body:has(.shell.analysis-mode) .top-actions" not in css


def test_attribution_table_uses_subset_copy_and_contribution_group_at_right() -> None:
    js = (ROOT / "backtest_platform/static/app.js").read_text(encoding="utf-8")
    css = (ROOT / "backtest_platform/static/styles.css").read_text(encoding="utf-8")

    assert "对子集" in js
    assert "对子组" not in js
    assert '<th colspan="3">基础信息</th>' in js
    assert 'colspan="5" class="group-start">自身表现' in js
    assert "贡献归因" in js
    assert "收益解释" not in js
    assert "波动与回撤" not in js
    assert "回撤贡献" in js
    assert "回撤期拖累" not in js
    assert js.index('percentTd(row.period_return, "group-start")') < js.index("percentTd(row.max_drawdown)")
    assert js.index("percentTd(row.max_drawdown)") < js.index("contributionBarCell(display.returnContribution")
    assert js.index("contributionBarCell(display.returnContribution") < js.index("contributionBarCell(display.drawdownContribution")
    bar_cell_fn = js[js.index("function contributionBarCell") : js.index("function handleAttributionTableClick")]
    assert "Math.abs(number) / maxValue * 75" in bar_cell_fn
    assert "Math.min(75," in bar_cell_fn
    name_cell_fn = js[js.index("function attributionNameCell") : js.index("function percentTd")]
    fund_branch_start = name_cell_fn.index('if (row.type === "fund")')
    fund_branch_end = name_cell_fn.index("return `<strong>${name}</strong>${code}`;", fund_branch_start)
    fund_branch = name_cell_fn[fund_branch_start:fund_branch_end]
    assert "return `<strong>${name}</strong>`;" in fund_branch
    assert "return `<strong>${name}</strong>${code}`;" not in fund_branch
    attribution_wrap_css = css[css.index(".attribution-table-wrap") : css.index(".correlation-matrix-wrap")]
    assert "overflow: visible;" in attribution_wrap_css
    assert "overflow: auto;" not in attribution_wrap_css
    assert ".attribution-table {" in css
    assert "table-layout: fixed;" in css
    assert '<colgroup class="attribution-colgroup">' in js
    assert 'class="attr-col-name"' in js
    assert 'class="attr-col-contribution"' in js
    compact_attr_css = css[css.index(".attribution-table th,") : css.index(".attribution-table th {")]
    assert "padding: 6px clamp(1px, 0.22vw, 4px);" in compact_attr_css
    assert ".attribution-table th:not(:first-child)" in css
    assert ".attribution-table td:not(:first-child)" in css
    assert ".attribution-table thead tr:nth-child(2) th:first-child" in css
    assert ".attribution-table tbody td:first-child" in css
    assert "text-align: center;" in compact_attr_css
    name_align_css = css[
        css.index(".attribution-table thead tr:nth-child(2) th:first-child") :
        css.index(".attribution-table th:not(:first-child)")
    ]
    assert "text-align: left;" in name_align_css
    assert "white-space: nowrap;" in css[css.index(".attribution-table th:not(:first-child)") : css.index(".attribution-table .group-start")]
    attribution_table_css = css[css.index(".attribution-table {") : css.index(".attribution-table th,")]
    assert "font-size: 11px;" in attribution_table_css
    assert "font-size: 10px;" not in attribution_table_css
    assert ".attr-col-name" in css
    assert ".attr-col-contribution" in css
    assert "width: 8.6%;" in css[css.index(".attr-col-contribution") : css.index(".attr-col-share")]
    assert "width: 8.2%;" in css[css.index(".attr-col-share") : css.index(".attribution-table th,")]
    assert "width: 7.4%;" in css[css.index(".attr-col-return") : css.index(".attr-col-period")]
    assert "width: 12.2%;" in css[css.index(".attr-col-period") : css.index(".attr-col-days")]
    assert "width: 7.6%;" in css[css.index(".attr-col-days") : css.index(".attr-col-contribution")]
    contribution_css = css[css.index(".contribution-cell") : css.index(".contribution-bar.negative")]
    assert "grid-template-columns: minmax(0, 1fr);" in contribution_css
    assert "height: 18px;" in contribution_css
    assert "position: absolute;" in contribution_css
    assert "text-align: center;" in contribution_css
    assert "font-variant-numeric: tabular-nums;" in contribution_css


def test_attribution_contribution_scope_toggle_and_bars_exist() -> None:
    js = (ROOT / "backtest_platform/static/app.js").read_text(encoding="utf-8")
    css = (ROOT / "backtest_platform/static/styles.css").read_text(encoding="utf-8")

    assert 'attributionContributionScope: "portfolio"' in js
    assert 'data-attribution-scope="portfolio"' in js
    assert 'data-attribution-scope="subset"' in js
    assert "function attributionDisplayValues" in js
    assert "function contributionBarCell" in js
    assert "function contributionBarScale" in js
    assert "return_contribution_share_to_parent" in js
    assert "volatility_contribution_to_parent" in js
    assert "drawdown_contribution_to_parent" in js
    assert "<strong>对组合" not in js
    assert "<span>对子集" not in js
    assert ".contribution-bar" in css
    assert ".group-start" in css


def test_benchmark_labels_use_selected_benchmark_name() -> None:
    js = (ROOT / "backtest_platform/static/app.js").read_text(encoding="utf-8")

    assert "function currentBenchmarkLabel()" in js
    assert "benchmark: currentBenchmarkLabel()," in js


def test_metrics_render_second_row_with_drawdown_profile_slots() -> None:
    js = (ROOT / "backtest_platform/static/app.js").read_text(encoding="utf-8")

    assert '"回撤区间", formatText(metrics.drawdown_period)' in js
    assert '"回撤时长（月）", formatMonths(metrics.drawdown_months)' in js
    assert '"修复耗时（月）", formatRepairMonths(metrics.recovery_months)' in js
    assert '"年度胜率%（正收益占比）", formatPercent(metrics.annual_win_rate)' in js
    assert "metric-placeholder" not in js
    assert "function formatRepairMonths" in js


def test_strategy_frontend_entry_is_removed_but_legacy_backend_compatibility_can_remain() -> None:
    html = (ROOT / "backtest_platform/static/index.html").read_text(encoding="utf-8")
    js = (ROOT / "backtest_platform/static/app.js").read_text(encoding="utf-8")

    assert 'id="saveStrategyBtn"' not in html
    assert "保存为策略" not in html
    assert 'id="strategyLibraryView"' not in html
    assert 'id="strategyModal"' not in html
    assert "saveStrategyBtn" not in js
    assert "openStrategyModal" not in js
    assert "confirmStrategyModal" not in js
    assert "renderStrategies" not in js


def test_frequency_selector_is_in_topbar_and_payload_drives_frequency_labels() -> None:
    html = (ROOT / "backtest_platform/static/index.html").read_text(encoding="utf-8")
    js = (ROOT / "backtest_platform/static/app.js").read_text(encoding="utf-8")
    css = (ROOT / "backtest_platform/static/styles.css").read_text(encoding="utf-8")

    header = html[html.index('<header class="topbar">') : html.index("</header>")]
    assert 'data-frequency="daily"' in header
    assert 'data-frequency="weekly"' in header
    assert 'data-frequency="monthly"' in header
    assert "日频" in header
    assert "周频" in header
    assert "月频" in header
    assert "dataFrequency: \"daily\"" in js
    assert "dataFrequency: currentDataFrequency()" in js
    assert "function handleFrequencyChange" in js
    assert "function winRateMetricLabel" in js
    assert "function cachedResultForFrequency" in js
    handler = js[js.index("async function handleFrequencyChange") : js.index("function winRateMetricLabel")]
    assert "cachedResultForFrequency" in handler
    assert "runBacktest" not in handler
    assert "renderResult(cached, false)" in handler
    assert "isPortfolioDirty" not in handler
    assert "缓存快照" not in handler
    assert '["日胜率", formatPercent(metrics.win_rate)]' not in js
    assert ".frequency-toggle" in css


def test_portfolio_save_and_backtest_uses_slim_payload_and_single_list_refresh() -> None:
    js = (ROOT / "backtest_platform/static/app.js").read_text(encoding="utf-8")
    html = (ROOT / "backtest_platform/static/index.html").read_text(encoding="utf-8")

    assert 'id="savePortfolioBtn"' not in html
    assert ">保存组合</button>" not in html
    assert 'id="runBtn" class="primary-btn" type="button">回测</button>' in html
    assert "function portfolioSavePayload" in js
    save_payload = js[js.index("function portfolioSavePayload") : js.index("async function saveCurrentPortfolio")]
    assert "backtestSnapshots" in save_payload
    assert "lastBacktestSnapshot" in save_payload
    assert "latestHoldings" in save_payload
    save_start = js.index("async function saveCurrentPortfolio")
    save_fn = js[save_start : js.index("async function runBacktest", save_start)]
    assert "portfolioSavePayload(" in save_fn
    assert "if (!runAfterSave) await loadPortfolios(saved.id);" in save_fn
    run_start = js.index("async function runBacktest", save_start)
    run_fn = js[run_start : js.index("function buildBacktestPayload", run_start)]
    confirm_start = js.index("async function confirmAdjustPortfolioModal")
    confirm_fn = js[confirm_start : js.index("function portfolioSavePayload", confirm_start)]
    assert "await saveCurrentPortfolio(false)" in confirm_fn
    assert "持仓已更新，尚未回测。" not in confirm_fn
    assert "await refreshPortfolioList(state.currentPortfolio.id);" in run_fn
    assert "await loadPortfolios(state.currentPortfolio.id);" not in run_fn
    assert "fetchPortfolioSnapshot" in js
    assert "/snapshots/" in js
    assert "applySavedPortfolioCacheState" in save_fn
    cache_fn = js[js.index("function applySavedPortfolioCacheState") : js.index("async function runBacktest", js.index("function applySavedPortfolioCacheState"))]
    assert "saved.hasBacktest === false" in cache_fn
    assert "state.result = null" in cache_fn
    assert "state.analysisAttribution = null" in cache_fn


def test_portfolio_selection_uses_active_snapshot_endpoint_instead_of_full_payload() -> None:
    js = (ROOT / "backtest_platform/static/app.js").read_text(encoding="utf-8")

    select_start = js.index("async function selectPortfolio")
    select_fn = js[select_start : js.index("function createEmptyPortfolio", select_start)]
    assert "/active" in select_fn
    assert "fetchJson(`/api/portfolios/${encodeURIComponent(portfolioId)}/active`)" in select_fn
    assert "loadSelectedPortfolioSnapshot(portfolio.id, currentDataFrequency(), token);" in select_fn
    assert select_fn.index("renderPortfolioHome();") < select_fn.index("loadSelectedPortfolioSnapshot")
    assert "function loadSelectedPortfolioSnapshot" in js
    loader_start = js.index("async function loadSelectedPortfolioSnapshot")
    loader_fn = js[loader_start : js.index("function createEmptyPortfolio", loader_start)]
    assert "fetchPortfolioSnapshot(portfolioId, frequency)" in loader_fn
    assert "token !== state.portfolioSelectionToken" in loader_fn


def test_portfolio_management_home_and_adjust_modal_static_structure() -> None:
    html = (ROOT / "backtest_platform/static/index.html").read_text(encoding="utf-8")
    js = (ROOT / "backtest_platform/static/app.js").read_text(encoding="utf-8")
    css = (ROOT / "backtest_platform/static/styles.css").read_text(encoding="utf-8")

    assert "基金组合管理平台" in html
    assert 'data-result-view="backtest"' in html
    assert 'data-result-view="portfolioHome"' not in html
    assert 'data-result-view="portfolioLibrary"' not in html
    assert 'id="portfolioList"' in html
    assert 'id="portfolioBulkToggleBtn"' in html
    assert 'id="portfolioBulkDeleteBtn"' not in html
    assert "操作" in html
    assert 'id="portfolioDeleteConfirmModal"' in html
    assert 'id="toggleLibraryBtn"' in html
    assert 'aria-label="折叠组合库"' in html
    assert "portfolioDeleteMode: false" in js
    assert "pendingPortfolioDeleteId: null" in js
    assert "draggingPortfolioId: null" in js
    assert "function togglePortfolioDeleteMode" in js
    assert "function openPortfolioDeleteConfirm" in js
    assert "function confirmDeletePortfolio" in js
    assert "function savePortfolioOrder" in js
    assert "data-delete-portfolio" in js
    assert "data-drag-portfolio" in js
    assert "/api/portfolios/${encodeURIComponent(portfolioId)}" in js
    assert "method: \"DELETE\"" in js
    assert "libraryCollapsed: false" in js
    assert "function toggleLibraryPanel" in js
    assert "function renderLibraryCollapsedState" in js
    assert 'els.shell.classList.toggle("library-collapsed", state.libraryCollapsed);' in js
    assert 'on(els.toggleLibraryBtn, "click", toggleLibraryPanel);' in js
    assert ".shell.library-collapsed" in css
    assert "grid-template-columns: 42px minmax(0, 1fr);" in css[css.index(".shell.library-collapsed") : css.index(".library-panel")]
    assert ".shell.analysis-mode.library-collapsed" not in css
    assert ".shell.library-collapsed .library-panel" in css
    collapsed_panel_start = css.index(".shell.library-collapsed .library-panel {")
    collapsed_panel_end = css.index(".shell.library-collapsed .library-panel .panel-head", collapsed_panel_start)
    assert "width: 42px;" in css[collapsed_panel_start:collapsed_panel_end]
    assert 'class="portfolio-summary-grid"' not in html
    assert 'id="latestHoldingTable"' in html
    assert html.index('id="annualContributionTable"') < html.index('id="latestHoldingTable"') < html.index('id="transactionTable"')
    assert html.index('id="portfolioHomeView"') < html.index('id="results"')
    latest_table_html = html[html.index('id="latestHoldingTable"') : html.index('class="table-card transaction-card"')]
    assert "持仓权重" in latest_table_html
    assert "权重漂移" in latest_table_html
    assert "最新日期" in latest_table_html
    assert "基金经理" not in latest_table_html
    assert "最新净值" not in latest_table_html
    assert "净值日期" not in latest_table_html
    assert "持仓份额" not in latest_table_html
    assert latest_table_html.index("持仓权重") < latest_table_html.index("权重漂移") < latest_table_html.index("最新日期")
    assert "最新涨幅" in html
    assert "今年以来" in html
    assert "最新账户资产" not in html
    assert "累计盈亏" in js
    assert "现金余额" in js
    assert "累计手续费" in js
    assert "renderPortfolioStateMetricCards" in js
    assert "[\"最新持仓日\", latest?.date || \"-\"]" in js
    assert 'id="transactionTable"' in html
    assert "交易流水" in html
    assert "权重变化" in html
    assert "现金变动" in html
    assert "renderTransactionTable" in js
    assert "transaction.fundId ? fundName(transaction.fundId) : \"-\"" in js
    assert "transaction.cash_change" in js
    assert "transaction.weight_change" in js
    assert "持仓成本" not in html
    assert "持仓成本" not in js
    assert "<th>价格</th>" not in html
    assert "<th>现金余额</th>" not in html
    assert ".transaction-card thead th" in css
    assert "position: sticky;" in css
    latest_table_fn = js[js.index("function renderLatestHoldingTable") : js.index("function sumPositionField")]
    assert "<th>当前真实权重</th>" not in latest_table_fn
    assert "<th>持仓权重</th>" in latest_table_fn
    assert "<th>权重漂移</th>" in latest_table_fn
    assert "<th>浮动盈亏</th>" in latest_table_fn
    assert "<th>盈亏比例</th>" in latest_table_fn
    assert "<th>持仓份额</th>" not in latest_table_fn
    assert "<th>基金经理</th>" not in latest_table_fn
    assert "<th>最新净值</th>" not in latest_table_fn
    assert "<th>净值日期</th>" not in latest_table_fn
    assert "function holdingWeightDriftText" in js
    assert '`${formatPercent(targetWeight)} 到 ${formatPercent(currentWeight)}`' in js
    assert "<td>${formatPercent(holding.weight)}</td>" in latest_table_fn
    assert "持有收益率" not in html
    assert "当日涨幅" not in html
    assert "formatNumber(holding.latestNav, 4)" not in latest_table_fn
    assert "holding.ytdReturn" in js
    assert "latestHoldingCollapsedSleeves: new Set()" in js
    assert "function latestHoldingGroupedRows" in js
    assert "function handleLatestHoldingTableClick" in js
    assert "data-latest-sleeve-toggle" in js
    assert ".latest-sleeve-row" in css
    assert ".latest-fund-name" in css
    assert "renderCashMetricCards" in js
    assert "renderAccountValueChart" not in js
    assert 'id="adjustPortfolioModal"' in html
    assert 'id="initialCashInput"' in html
    assert 'id="feeSettingsBtn"' in html
    assert 'id="feeSettingsModal"' in html
    assert 'id="purchaseFeeInput"' in html
    assert 'id="redemptionFeeInput"' in html
    assert 'id="transactionFeeInput"' in html
    assert "费率设置" in html
    assert "交易费率%" in html
    assert 'id="rebalanceTimeline"' in html
    assert 'id="modalAssetList"' in html
    assert 'id="modalHoldingTable"' in html
    assert 'id="rebalanceValidation"' in html
    assert 'id="activeCashFlowInput"' in html
    assert 'id="cashEstimatePanel"' in html
    assert "申购/赎回金额" in html
    assert "本日资金划转" not in html
    assert "本日资金变动" not in js
    assert "调仓后资产" in html
    assert "调仓后目标资产" not in html
    assert "预计手续费" not in html
    assert "预计现金残留" not in html
    assert "全局调仓模板" not in html
    assert "调仓频率" in html
    assert 'id="rebalanceFrequencySelect"' in html
    assert '<option value="monthly">月度</option>' in html
    assert '<option value="quarterly" selected>季度</option>' in html
    assert '<option value="semiannual">半年度</option>' in html
    assert "三组等权" in html
    current_template_html = html[html.index('id="currentTemplateSelect"') : html.index("</select>", html.index('id="currentTemplateSelect"'))]
    assert '<option value="defensive">防御</option>' in current_template_html
    assert '<option value="defensive">防守</option>' not in current_template_html
    assert "均衡" in html
    assert "进取" in html
    assert '<option value="template_aggressive">进取</option>' in html
    assert html.count('<option value="aggressive">进取</option>') == 1
    assert "组内等权" in html
    assert "导入Excel" in html
    assert 'class="ghost-btn import-excel-btn"' in html
    toolbar = html[html.index('<div class="adjust-toolbar">') : html.index('<div class="adjust-layout">')]
    current_day_head = html[html.index('id="activeRebalanceDateTitle"') : html.index('<div class="cash-flow-strip compact">')]
    cash_flow_strip = html[html.index('<div class="cash-flow-strip compact">') : html.index('<div id="modalHoldingTable"')]
    modal_actions = html[html.index('<div class="modal-actions">', html.index('id="adjustPortfolioModal"')) : html.index('id="feeSettingsModal"')]
    assert 'id="equalInsideBtn"' not in toolbar
    assert current_day_head.index('id="equalInsideBtn"') < current_day_head.index('id="currentTemplateSelect"')
    assert 'id="rebalanceValidation"' not in current_day_head
    assert cash_flow_strip.index('id="cashEstimatePanel"') < cash_flow_strip.index('id="rebalanceValidation"')
    assert 'id="generateGlobalPlanBtn"' not in toolbar
    assert modal_actions.index('id="generateGlobalPlanBtn"') < modal_actions.index('id="confirmAdjustModalBtn"')
    assert 'id="generateGlobalPlanBtn" class="primary-btn"' in modal_actions
    assert 'els.rebalanceValidation.className = `rebalance-validation current-day-validation ${validation.ok ? "ok" : "error"}`' in js
    assert 'messages: ["调仓计划为空"]' in js
    assert 'messages.push(`${node.date}：权重 ${total.toFixed(2)}%，需 100%`);' in js
    assert 'messages.push("校验通过：目标持仓完整");' in js
    assert "复制本日持仓" not in html
    assert ">粘贴<" not in html
    assert "copyHoldingBtn" not in html
    assert "pasteHoldingBtn" not in html
    assert "copyCurrentHoldings" not in js
    assert "pasteHoldingsToCurrentNode" not in js
    assert "copiedHoldings" not in js
    assert "copiedSleeveTargets" not in js
    assert "应用到选中日期区间" not in html
    assert "新增交易" in html
    assert "新增日期" not in html
    assert 'id="addRebalanceDateInput"' in html
    assert "showPicker" in js
    assert "data-edit-rebalance-date" in js
    assert "data-delete-rebalance-date" in js
    assert "timeline-icon-edit" in js
    assert "timeline-icon-delete" in js
    assert ">改</button>" not in js
    assert ">删</button>" not in js
    assert "function updateRebalanceDate" in js
    assert "function deleteRebalanceDate" in js
    assert 'id="currentTemplateSelect"' in html
    assert 'id="applyCurrentTemplateBtn"' not in html
    assert 'id="activeRebalanceSource"' not in html
    assert 'on(els.currentTemplateSelect, "change", applyTemplateToActiveNode)' in js
    assert "function applyTemplateToActiveNode" in js
    assert "function updateSleeveTargetWeight" in js
    assert "data-sleeve-weight" in js
    assert "sleeveTargets" in js
    assert "function ensureNodeSleeveTargets" in js
    assert "function sleeveTargetWeightForNode" in js
    assert 'value="${escapeHtml(String(sleeveTargetWeightForNode(node, sleeve.id)))}"' in js
    assert "refreshSleeveTargetInput" not in js
    assert "子组合目标" in js
    assert "应用到指定日期前" not in html
    assert 'id="applyUntilDate"' not in html
    assert "function applyCurrentNodeToDate" not in js
    assert "prompt(" not in js
    assert 'id="globalPlanConfirmModal"' not in html
    assert 'id="confirmGlobalPlanConfirmBtn"' not in html
    assert "全局初始化" not in html
    assert "会删除所有手动和后续的调仓日" not in html
    assert "function openGlobalPlanConfirmModal" not in js
    assert "function applyGlobalPlanChoice" not in js
    assert "function getSelectedGlobalPlanChoice" not in js
    assert 'manual: "手动调仓"' in js
    assert 'template: "全局设置"' in js
    assert 'forward_applied: "后续覆盖"' in js
    assert 'on(els.generateGlobalPlanBtn, "click", generateRebalancePlanFromCurrentNode)' in js
    assert "applyGlobalPlanChoice(choiceButton.dataset.globalPlanChoice)" not in js
    assert "drag-handle" in js
    assert "function toggleFundInActiveNode" in js
    assert "function handleHoldingDragOver" in js
    assert "data-sleeve-dropzone" in js
    assert "targetSleeveId" in js
    assert ".asset-card.compact" in css
    assert "font-size: 11px" in css
    assert ".modal-holding-table th,\n.modal-holding-table td {\n  height: 30px;" in css
    assert ".table-weight-input {\n  width: 72px;" in css
    assert "padding-right: 20px" in css
    assert ".timeline-select strong {\n  font-size: 11px;" in css
    assert ".timeline-action-btn {\n  width: 22px;" in css
    assert "data-holding-sleeve" not in js
    assert "应用到后续调仓日" in html
    assert "从当前调仓日起应用到后续调仓日" not in html
    assert ">应用<" not in html
    assert 'class="rebalance-rule-panel"' not in html
    assert "rebalance-rule-panel" not in css
    assert ".rebalance-rule-inline" in css
    assert ".current-day-validation" in css
    assert "function renderPortfolioHome" in js
    assert "function openAdjustPortfolioModal" in js
    assert "function generateRebalancePlanFromCurrentNode" in js
    assert "function frequencyRebalanceDates" in js
    assert "function nextTradingDate" in js
    assert "function syncCurrentPortfolioSummary" in js
    assert "syncCurrentPortfolioSummary();" in js
    assert "function importRebalanceExcel" in js
    assert "function applyCurrentNodeToFutureNodes" in js
    assert "function updateActiveCashFlow" in js
    assert "function openFeeSettingsModal" in js
    assert "function confirmFeeSettingsModal" in js
    assert "function renderCashEstimate" in js
    assert "function cashModeSummary" in js
    assert "function sleeveTargetsForEqualInside" in js
    assert "function sleeveHoldingWeightTotal" in js
    assert "function updateSleeveActualWeightDisplays" in js
    assert "data-sleeve-actual-weight" in js
    assert "累计权重" in js
    assert 'class="sleeve-title-controls"' in js
    assert 'class="sleeve-actual-weight"' in js
    assert ".holding-sleeve-head.compact .sleeve-actual-weight" in css
    assert "grid-column: 4;" in css
    assert "normalizeRatiosForActiveSleeves(globalTemplateRatios(fallbackTemplate)" in js
    assert "els.currentTemplateSelect?.value || \"equal\"" in js
    assert 'step="1"' in js
    assert 'aria-label="删除基金"' in js
    assert ">×</button>" in js


def test_adjust_toolbar_fee_modal_and_fund_pool_are_compact() -> None:
    html = (ROOT / "backtest_platform/static/index.html").read_text(encoding="utf-8")
    css = (ROOT / "backtest_platform/static/styles.css").read_text(encoding="utf-8")

    toolbar = html[html.index('<div class="adjust-toolbar">') : html.index('<div class="adjust-layout">')]
    assert toolbar.index('id="feeSettingsBtn"') < toolbar.index('id="rebalanceFrequencySelect"')
    assert 'id="generateGlobalPlanBtn"' not in toolbar
    assert toolbar.index('id="importExcelBtn"') > toolbar.index('id="rebalanceFrequencySelect"')
    assert 'id="copyHoldingBtn"' not in toolbar
    assert 'id="pasteHoldingBtn"' not in toolbar
    assert 'id="purchaseFeeInput"' not in toolbar
    assert 'id="redemptionFeeInput"' not in toolbar
    js = (ROOT / "backtest_platform/static/app.js").read_text(encoding="utf-8")
    assert "已加入" not in js
    assert ".import-excel-btn" in css
    assert "margin-left: auto;" in css
    modal_search_css = css[css.index(".modal-search") : css.index(".asset-card.compact", css.index(".modal-search"))]
    assert "height: 28px;" in modal_search_css
    assert "font-size: 12px;" in modal_search_css
    assert "grid-template-columns: 190px 190px minmax(0, 1fr);" in css
    assert ".modal-fund-pool .fund-cluster-head" in css
    assert "min-height: 26px;" in css


def test_adjust_modal_uses_compact_holding_layout() -> None:
    html = (ROOT / "backtest_platform/static/index.html").read_text(encoding="utf-8")
    js = (ROOT / "backtest_platform/static/app.js").read_text(encoding="utf-8")
    css = (ROOT / "backtest_platform/static/styles.css").read_text(encoding="utf-8")

    assert 'class="adjust-modal-title"' in html
    title_block = html[html.index('class="adjust-modal-title"') : html.index('id="closeAdjustModalBtn"')]
    assert title_block.index('id="adjustPortfolioTitle"') < title_block.index('id="adjustPortfolioMeta"')
    assert '<label class="sleeve-target-label">' in js
    assert "<span>权重</span>" in js
    assert "<span>权重%</span>" not in js[js.index("function renderModalHoldingTable") : js.index("function handleHoldingDragOver")]
    assert ".adjust-modal-title" in css
    assert 'input[type="number"]::-webkit-outer-spin-button' in css
    assert 'input[type="number"]::-webkit-inner-spin-button' in css
    assert "-webkit-appearance: none;" in css
    assert 'input[type="number"] {\n  -moz-appearance: textfield;' in css
    assert ".cash-flow-strip.compact" in css
    assert "grid-template-columns: max-content max-content minmax(0, 1fr);" in css
    assert ".cash-flow-strip.compact .current-day-validation" in css
    assert "grid-template-columns: auto 92px;" in css
    assert "width: 92px;" in css
    assert "min-height: 26px;" in css
    assert ".holding-sleeve-head.compact" in css
    assert ".holding-sleeve-head.compact .sleeve-target-label" in css
    assert ".holding-sleeve-head.compact .sleeve-actual-weight" in css
    assert "grid-template-columns: 30% 13% 15% 12% 16% 8% 6%;" in css
    assert "flex-direction: row;" in css
    assert "min-height: 26px;" in css
    assert ".modal-holding-table.compact" in css
    assert ".adjust-toolbar .compact-label" in css
    assert ".adjust-toolbar input,\n.adjust-toolbar select" in css
    assert ".adjust-toolbar > .ghost-btn" in css
    assert "align-self: flex-end;" in css
    assert ".modal-holding-table.compact th,\n.modal-holding-table.compact td" in css
    assert "text-align: center;" in css
    assert ".current-day-tools #equalInsideBtn" in css
    assert ".modal-holding-table.compact .table-weight-input" in css
    compact_weight_css = css[css.index(".modal-holding-table.compact .table-weight-input") : css.index(".modal-holding-table.compact .remove-holding-btn")]
    assert "width: 58px;" in compact_weight_css
    assert "padding: 0 4px;" in compact_weight_css
    assert 'step="0.01" value="${escapeHtml(formatWeightInputValue(holdingWeightPercent(holding)))}"' in js
    assert "function formatWeightInputValue(value)" in js
    assert ".remove-holding-btn" in css
    assert "width: 22px;" in css
    assert "height: 22px;" in css


def test_sleeve_display_names_match_fund_cluster_names() -> None:
    html = (ROOT / "backtest_platform/static/index.html").read_text(encoding="utf-8")
    js = (ROOT / "backtest_platform/static/app.js").read_text(encoding="utf-8")
    server = (ROOT / "backtest_platform/server.py").read_text(encoding="utf-8")

    assert '{ id: "defensive", name: "防守压仓" }' in js
    assert '{ id: "balanced", name: "核心稳健" }' in js
    assert '{ id: "growth", name: "进攻弹性" }' in js
    assert 'sleeve_names = {"defensive": "防守压仓", "balanced": "核心稳健", "growth": "进攻弹性"}' in server
    assert 'value === "防守压仓"' in js
    assert 'value === "核心稳健"' in js
    assert 'value === "进攻弹性"' in js
    assert 'label in {"防守", "防守压仓", "defensive"}' in server
    assert 'label in {"稳健", "核心稳健", "balanced", "stable"}' in server
    assert 'value === "进攻增强"' in js
    assert 'label in {"进攻", "进攻弹性", "进攻增强", "growth", "aggressive"}' in server


def test_exposure_view_static_structure_and_enabled_tab() -> None:
    html = (ROOT / "backtest_platform/static/index.html").read_text(encoding="utf-8")
    js = (ROOT / "backtest_platform/static/app.js").read_text(encoding="utf-8")
    css = (ROOT / "backtest_platform/static/styles.css").read_text(encoding="utf-8")

    assert 'data-result-view="exposure"' not in html
    assert 'id="exposureView"' in html
    assert html.index('id="attributionView"') < html.index('id="exposureView"')
    assert 'id="exposureBreakdownSections"' in html
    assert "风格暴露" in html
    assert "风格穿透矩阵" in html
    assert html.index('id="exposureBreakdownSections"') < html.index("风格暴露")
    assert "大类 / 一级行业 / 二级行业 / 个股" in html
    exposure_section = html[html.index('id="exposureView"') :]
    assert "行业暴露" not in exposure_section
    assert "基准权重" not in exposure_section
    assert "风险贡献" not in exposure_section
    assert "收益贡献" not in exposure_section
    assert "大类" in exposure_section
    assert 'id="exposureFactorRows"' in html
    assert 'id="fundExposureDrawer"' in html
    assert 'id="exposureIndustryBars"' not in html
    assert 'id="exposureIndustryMatrix"' not in html
    assert 'id="exposureDrilldownTable"' not in html
    assert 'id="exposureScopeFilter"' not in html
    assert 'id="exposureFactorNotes"' not in html
    assert "renderExposureScopeOptions" not in js
    assert "renderExposureFactorNotes" not in js
    assert "exposureStrongestFactor" not in js
    assert "function renderExposureView(result)" in js
    assert "持仓报告期" in js
    assert "exposure.holding_report_date" in js
    assert "function exposureCoverageWarningHtml" in js
    assert "部分基金缺少画像或披露数据" in js
    assert "exposure.coverage" in js
    assert "function renderExposureBreakdownSections" in js
    assert "function exposureBreakdownSections" in js
    assert "function exposureBreakdownScopeButtons" in js
    assert "function selectedExposureBreakdownScope" in js
    assert "function scopedExposureBreakdownItems" in js
    assert "function selectExposureBreakdownScope" in js
    exposure_scopes_fn = js[js.index("function exposureBreakdownScopes") : js.index("function exposureScopeLabel")]
    assert "Object.keys(section.scopes || {})" in exposure_scopes_fn
    assert "selectedExposureScopes: {}" in js
    assert "function legacyExposureBreakdownSections" in js
    assert "function legacyScopedBreakdownItems" in js
    assert "function legacyL1ItemsForScope" in js
    assert "function legacyRowsForScope" in js
    assert "industryCategoryMap" in js
    assert "legacyCategoryItemsFromL1" in js
    assert 'id: "category"' in js
    assert "exposure.industry_l1?.portfolio" in js
    assert 'legacyRowsForScope(exposure, "l2_by_scope", "portfolio")' in js
    assert 'legacyRowsForScope(exposure, "holdings_by_scope", "portfolio")' in js
    assert "function exposurePieSvg" in js
    assert "function exposurePieLegend" in js
    assert "handleExposureHover" in js
    assert 'event.target.closest(".exposure-pie-slice[data-exposure-breakdown-item]")' in js
    assert 'data-exposure-breakdown-trigger="pie"' in js
    assert "mouseover" in js
    assert "function exposurePieCallouts" not in js
    assert "svgShortLabel" not in js
    assert "section.subtitle" not in js
    assert "左侧为组合分布" not in js
    assert "function renderExposureBreakdownDetail" in js
    breakdown_sections_fn = js[js.index("function renderExposureBreakdownSections") : js.index("function exposureBreakdownSections")]
    title_block = breakdown_sections_fn[breakdown_sections_fn.index('<div class="table-title exposure-breakdown-title-row">') : breakdown_sections_fn.index('<div class="exposure-breakdown-layout">')]
    pie_panel_block = breakdown_sections_fn[breakdown_sections_fn.index('<section class="exposure-pie-panel">') : breakdown_sections_fn.index('<section class="exposure-breakdown-detail">')]
    assert "exposureBreakdownScopeButtons" not in title_block
    assert "exposureBreakdownDetailTitle(selectedItem)" in title_block
    assert "exposureBreakdownScopeButtons" in pie_panel_block
    assert 'class="exposure-pie-content"' in breakdown_sections_fn
    detail_fn = js[js.index("function renderExposureBreakdownDetail") : js.index("function exposurePieColor")]
    assert "<em>${formatPercent(item.weight)}</em>" not in detail_fn
    assert "<span>${escapeHtml(section.title || \"-\")}</span>" not in detail_fn
    assert "breakdown-detail-head compact" not in detail_fn
    assert "function exposureBreakdownDetailTitle" in js
    assert "data-exposure-breakdown-item" in js
    assert "data-exposure-breakdown-scope" in js
    assert "data-exposure-breakdown-scope-select" in js
    assert "breakdown_sections" in js
    set_view_fn = js[js.rindex("function setResultView") :]
    assert 'normalized === "attribution"' in set_view_fn
    assert 'normalized === "exposure"' not in set_view_fn
    assert 'els.exposureView.classList.toggle("hidden"' not in set_view_fn
    assert 'if (normalized === "attribution" && state.result) renderExposureView(state.result);' in set_view_fn
    assert "data-exposure-toggle" in js
    assert "data-exposure-fund" in js
    assert "data-exposure-industry-sort" not in js
    assert "data-exposure-drilldown-tab" not in js
    assert "data-exposure-scope" not in js
    assert "function factorDisplayName" in js
    assert "function sortedFactorNames" in js
    factor_bars_fn = js[js.index("function renderExposureFactorBars") : js.index("function factorDisplayName")]
    assert "sortedFactorNames(names, values)" in factor_bars_fn
    assert "市值" in js
    assert "盈利" in js
    assert "非线性市值" in js
    assert "function openExposureFundDrawer" in js
    assert "source-fund-cell" not in js
    assert "numeric-cell" in js
    assert ".exposure-breakdown-section" in css
    exposure_section_css = css[css.index(".exposure-breakdown-section") : css.index(".exposure-breakdown-layout")]
    assert "height: 360px;" in exposure_section_css
    assert "overflow: hidden;" in exposure_section_css
    assert ".exposure-pie" in css
    assert ".exposure-pie-toolbar" in css
    assert ".exposure-pie-content" in css
    assert ".exposure-pie-callouts" not in css
    assert ".exposure-pie-legend" in css
    assert ".exposure-scope-toggle" in css
    assert ".exposure-scope-toggle button.active" in css
    assert ".exposure-coverage-warning" in css
    pie_panel_css = css[css.index(".exposure-pie-panel") : css.index(".exposure-pie-toolbar")]
    assert "grid-template-rows: auto minmax(0, 1fr);" in pie_panel_css
    assert "grid-template-columns: 150px 220px;" not in pie_panel_css
    pie_content_css = css[css.index(".exposure-pie-content") : css.index(".exposure-pie {")]
    assert "grid-template-columns: 150px 220px;" in pie_content_css
    assert "justify-content: center;" in pie_content_css
    assert "gap: 30px;" in pie_content_css
    assert "padding-left: 60px;" in pie_content_css
    pie_legend_css = css[css.index(".exposure-pie-legend {") : css.index(".exposure-pie-legend button")]
    assert "box-sizing: border-box;" in pie_legend_css
    assert "padding-right: 10px;" in pie_legend_css
    assert "scrollbar-gutter: stable;" in pie_legend_css
    pie_legend_button_css = css[css.index(".exposure-pie-legend button") : css.index(".exposure-pie-legend button.active")]
    assert "grid-template-columns: 9px minmax(82px, 96px) 48px;" in pie_legend_button_css
    assert "justify-content: center;" in pie_legend_button_css
    assert ".exposure-breakdown-detail" in css
    assert ".exposure-breakdown-title-row" in css
    assert ".breakdown-detail-title" in css
    detail_css = css[css.index(".exposure-breakdown-detail") : css.index(".exposure-pie-panel")]
    assert "display: grid;" in detail_css
    assert "grid-template-rows: minmax(0, 1fr);" in detail_css
    assert ".exposure-source-table-wrap" in css
    table_wrap_css = css[css.index(".exposure-source-table-wrap") : css.index(".exposure-source-table {")]
    assert "overflow: auto;" in table_wrap_css
    assert '<colgroup class="exposure-source-colgroup">' in js
    assert 'class="exposure-source-fund-col"' in js
    assert 'class="exposure-source-sleeve-col"' in js
    assert 'class="exposure-source-contribution-col"' in js
    assert ".exposure-source-fund-col" in css
    assert ".exposure-source-sleeve-col" in css
    assert ".exposure-source-contribution-col" in css
    assert "width: 42%;" in css
    assert "width: 27%;" in css
    assert "width: 31%;" in css
    factor_grid_css = css[css.index(".exposure-factor-grid {") : css.index(".exposure-factor-grid > section")]
    assert "grid-template-columns: minmax(260px, 0.65fr) minmax(620px, 1.35fr);" in factor_grid_css
    matrix_cell_css = css[css.index(".exposure-matrix th,") : css.index(".exposure-matrix th {")]
    matrix_css = css[css.index(".exposure-matrix {") : css.index(".exposure-matrix th,")]
    assert "min-width: 620px;" in matrix_css
    assert "min-width: 48px;" in matrix_cell_css
    assert "text-align: center;" in matrix_cell_css
    matrix_sticky_css = css[css.index(".exposure-matrix .sticky-col") : css.index(".exposure-matrix thead .sticky-col")]
    assert "min-width: 112px;" in matrix_sticky_css
    drawer_table_css = css[css.index(".drawer-table-wrap th,") : css.index(".correlation-matrix th,")]
    assert "text-align: center;" in drawer_table_css
    source_cell_css = css[css.index(".exposure-source-table th,") : css.index(".exposure-source-table th {")]
    assert "text-align: center;" in source_cell_css
    assert "text-align: left;" not in source_cell_css
    assert 'escapeHtml(source.name || "-")' in js
    assert 'source.name || source.fund_id || "-"' not in js
    assert "els.exposureDrawerCode.textContent = \"\";" in js
    drawer_fn = js[js.index("function exposureDrawerHtml") : js.index("function formatExposureNumber")]
    assert "基金暴露摘要" not in drawer_fn
    assert "drawer-metric-grid" not in drawer_fn
    assert "top_l2_industries" in drawer_fn
    assert drawer_fn.index("Top5 一级行业") < drawer_fn.index("Top10 二级行业") < drawer_fn.index("Top10 A股重仓股") < drawer_fn.index("风格暴露（Barra/CNE5）")
    assert "const sortedNames = sortedFactorNames(factorNames, detail.factor_values || {})" in drawer_fn
    assert "sortedNames.map((name) => factorBarHtml" in drawer_fn
    assert ".industry-sort-button" not in css
    assert ".exposure-matrix" in css


def test_exposure_history_tables_static_structure() -> None:
    html = (ROOT / "backtest_platform/static/index.html").read_text(encoding="utf-8")
    js = (ROOT / "backtest_platform/static/app.js").read_text(encoding="utf-8")
    css = (ROOT / "backtest_platform/static/styles.css").read_text(encoding="utf-8")

    assert 'id="exposureHistoryTables"' in html
    assert html.index('id="exposureFactorRows"') < html.index('id="exposureHistoryTables"')
    assert "历年一级行业配置" in js
    assert "前十大个股配置" in js
    assert "function renderExposureHistoryTables" in js
    assert "function exposureIndustryHistoryTableHtml" in js
    assert "function exposureTopHoldingsHistoryTableHtml" in js
    assert "function exposureStockColor" in js
    assert "function exposureHistoryHeatColor" in js
    assert "Math.pow(ratio, 0.55)" in js
    assert "0.06 + intensity * 0.66" in js
    assert "data-exposure-history-sort" in js
    assert "function bindExposureIndustryHistorySort" in js
    assert "function nextExposureHistorySortState" in js
    assert ".history-sort-button" in css
    assert ".history-sort-button.active" in css
    assert "function bindExposureHoldingHistoryTable" in js
    assert "history_tables" in js
    assert ".exposure-history-grid" in css
    assert ".exposure-industry-history-table" in css
    assert ".exposure-holding-history-table" in css
    assert ".history-stock-cell.dimmed" in css


def test_backtest_complete_status_includes_portfolio_name() -> None:
    js = (ROOT / "backtest_platform/static/app.js").read_text(encoding="utf-8")

    assert "function backtestCompleteStatusText" in js
    assert 'setStatus(backtestCompleteStatusText(state.currentPortfolio), "ok")' in js
    assert "currentPortfolio?.name" in js


def test_analysis_window_keeps_fund_drawer_on_full_snapshot_attribution() -> None:
    js = (ROOT / "backtest_platform/static/app.js").read_text(encoding="utf-8")

    assert "function activeAnalysisResult" in js
    assert "renderAttributionView(activeAnalysisResult())" in js
    click_fn = js[js.index("function handleAttributionTableClick") : js.index("function renderSleeveCorrelation")]
    assert "openFundDrawer(row.dataset.attributionFund, state.result.attribution)" in click_fn
    assert "activeAnalysisResult().attribution" not in click_fn


def test_portfolio_library_operation_mode_replaces_bulk_delete_checkboxes() -> None:
    html = (ROOT / "backtest_platform/static/index.html").read_text(encoding="utf-8")
    js = (ROOT / "backtest_platform/static/app.js").read_text(encoding="utf-8")
    css = (ROOT / "backtest_platform/static/styles.css").read_text(encoding="utf-8")

    assert '<button id="portfolioBulkToggleBtn" class="library-action-btn" type="button">操作</button>' in html
    assert 'id="portfolioBulkDeleteBtn"' not in html
    assert 'id="portfolioDeleteConfirmModal"' in html
    assert 'data-delete-portfolio="${escapeHtml(portfolio.id)}"' in js
    assert 'data-drag-portfolio="${escapeHtml(portfolio.id)}"' in js
    assert "function openPortfolioDeleteConfirm" in js
    assert "function savePortfolioOrder" in js
    assert 'fetchJson("/api/portfolios/order"' in js
    assert "portfolio-delete-checkbox" not in js
    assert "deleteSelectedPortfolios" not in js
    assert "window.confirm" not in js
    assert ".portfolio-card-actions" in css
    assert ".portfolio-move-btn" in css
    assert ".portfolio-list-card.drag-over-before" in css
    assert ".portfolio-list-card.drag-over-after" in css


def test_attribution_contribution_bars_show_value_inside_translucent_track() -> None:
    js = (ROOT / "backtest_platform/static/app.js").read_text(encoding="utf-8")
    css = (ROOT / "backtest_platform/static/styles.css").read_text(encoding="utf-8")

    bar_cell_fn = js[js.index("function contributionBarCell") : js.index("function handleAttributionTableClick")]
    assert '<span class="contribution-bar-track ${tone}" aria-hidden="true">' in bar_cell_fn
    assert '<span class="contribution-value">${formatPercent(value)}</span>' in bar_cell_fn
    assert "grid-template-columns: minmax(0, 1fr);" in css
    track_css = css[css.index(".contribution-bar-track") : css.index(".contribution-bar {")]
    assert "height: 18px;" in track_css
    assert "position: relative;" in track_css
    assert "background: rgba(122, 135, 149, 0.12);" in track_css
    value_css = css[css.index(".contribution-value") : css.index(".contribution-bar-track")]
    assert "position: absolute;" in value_css
    assert "text-align: center;" in value_css
    positive_css = css[css.index(".contribution-bar.positive") : css.index(".contribution-bar.negative")]
    assert "rgba(201, 74, 74, 0.38)" in positive_css
