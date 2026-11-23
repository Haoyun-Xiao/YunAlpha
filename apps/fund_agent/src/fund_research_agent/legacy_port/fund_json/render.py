from __future__ import annotations

import json
from collections import Counter
from datetime import date

import numpy as np
import pandas as pd

from fund_research_agent.domain.report_data import annual_return

from .calculations import annual_and_cumulative, pnl_by_quarter
from .lake import LakeRepository
from .model import FundSpec
from .periods import display_tenure, last_completed_quarter


def dumps(value: object, *, pretty: bool = False) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        indent=4 if pretty else None,
        separators=None if pretty else (",", ":"),
    )


def pct(value: object) -> str | None:
    return f"{float(value) * 100:.2f}%" if pd.notna(value) else None

def number(value, digits=2):
    return round(float(value), digits) if pd.notna(value) and np.isfinite(float(value)) else None


def with_manager(spec: FundSpec, payload: dict[str, object]) -> str:
    return dumps({spec.root_key: payload, "基金经理": spec.manager_name})


def render_config(spec: FundSpec, df: pd.DataFrame) -> str:
    result: dict[str, list[dict[str, object]]] = {
        "大类板块": [],
        "一级行业": [],
        "二级行业": [],
        "重仓个股": [],
    }
    for period, group in df.groupby("report_period", sort=True):
        rp = pd.Timestamp(period).strftime("%Y%m%d")
        group = group.dropna(subset=["占净值比"])
        if group.empty:
            continue
        if rp.endswith(("0630", "1231")):
            for label, value in (
                group.groupby("大类")["占净值比"].sum().sort_values(ascending=False).items()
            ):
                if label:
                    result["大类板块"].append(
                        {"报告期": rp, "大类": label, "占基金净值比": pct(value)}
                    )
            for (sw1, category), value in (
                group.groupby(["申万一级", "大类"])["占净值比"]
                .sum()
                .sort_values(ascending=False)
                .items()
            ):
                if sw1:
                    result["一级行业"].append(
                        {
                            "报告期": rp,
                            "申万一级": sw1,
                            "所属大类": category or "",
                            "占基金净值比": pct(value),
                        }
                    )
            for (sw2, sw1), value in (
                group.groupby(["申万二级", "申万一级"])["占净值比"]
                .sum()
                .sort_values(ascending=False)
                .items()
            ):
                if sw2:
                    result["二级行业"].append(
                        {
                            "报告期": rp,
                            "申万二级": sw2,
                            "所属一级": sw1 or "",
                            "占基金净值比": pct(value),
                        }
                    )
        for row in group.nlargest(10, "占净值比").itertuples():
            result["重仓个股"].append(
                {
                    "报告期": rp,
                    "股票代码": row.holding_symbol,
                    "股票名称": row.股票名称,
                    "所属二级": row.申万二级 or "",
                    "占基金净值比": pct(row.占净值比),
                    "持仓市值(万元)": number(row.market_value / 10000),
                    "持仓数量(万股)": number(row.shares / 10000),
                }
            )
    mapping = {}
    for row in df.itertuples():
        if row.申万一级 and row.股票名称:
            mapping.setdefault(row.申万一级, set()).add(row.股票名称)
    result["行业个股映射"] = {key: sorted(value) for key, value in mapping.items()}
    return with_manager(spec, result)


def feature_payload(df: pd.DataFrame, *, include_quarters: bool = False) -> dict[str, list[dict[str, object]]]:
    output: list[dict[str, object]] = []
    for period, group in df.groupby("report_period", sort=True):
        rp = pd.Timestamp(period).strftime("%Y%m%d")
        if not include_quarters and not rp.endswith(("0630", "1231")):
            continue
        group = group.dropna(subset=["占净值比"])
        if group.empty:
            continue

        def weighted_average(column: str, sample: pd.DataFrame = group) -> float:
            valid = sample[sample[column].notna() & sample["股票市值权重"].notna()]
            denominator = valid["股票市值权重"].sum()
            return (
                float((valid[column] * valid["股票市值权重"]).sum() / denominator)
                if denominator
                else None
            )

        output.append(
            {
                "报告期": rp,
                "披露口径": "full_disclosure" if rp.endswith(("0630", "1231")) and len(group) > 10 else "top10_or_partial",
                "样本股票数": len(group),
                "样本净值覆盖率": pct(group["占净值比"].sum()),
                "指标覆盖": {
                    col: {
                        "有效样本数": int(group[col].notna().sum()),
                        "有效市值权重": number(group.loc[group[col].notna(), "股票市值权重"].sum() / group["股票市值权重"].sum(), 6) if group["股票市值权重"].sum() else None,
                        "裁剪范围": {"PE": [0, 200], "PB": [0, 20], "ROE": [-.5, 1]}.get(col),
                        "裁剪影响净值权重": number(group.loc[(group.get(col + "_raw", group[col]) != group[col]) & group[col].notna(), "占净值比"].sum(), 6),
                        "裁剪明细": [{"股票": row.get("股票名称"), "股票代码": row.get("holding_symbol"), "原始值": number(row.get(col + "_raw"), 6), "裁剪值": number(row[col], 6), "净值权重": number(row["占净值比"], 6)} for _, row in group.loc[(group.get(col + "_raw", group[col]) != group[col]) & group[col].notna()].iterrows()],
                        "数据日期范围": [str(group[field].min().date()), str(group[field].max().date())] if (field := ("roe_report_date" if col == "ROE" else "trade_date")) in group and group[field].notna().any() else None,
                    } for col in ["PE", "PB", "SIZE", "ROE", "股息率"]
                },
                "股票仓位": pct(group["占净值比"].sum()),
                "前五大行业集中度": pct(
                    group.loc[group["申万一级"].notna() & group["申万一级"].ne("")].groupby("申万一级")["占净值比"].sum().nlargest(5).sum()
                ) if group["申万一级"].fillna("").ne("").any() else None,
                "前十大个股集中度": pct(group.nlargest(10, "占净值比")["占净值比"].sum()),
                "占比大于1%个股数": int((group["占净值比"] > 0.01).sum()),
                "加权平均PE": number(weighted_average("PE")),
                "加权平均PB": number(weighted_average("PB")),
                "加权平均SIZE": number(weighted_average("SIZE")),
                "加权平均ROE": pct(weighted_average("ROE")),
                "加权平均股息率": pct(weighted_average("股息率")),
            }
        )
    return {"配置特征": output}


def preference_payload(df: pd.DataFrame) -> dict[str, list[dict[str, object]]]:
    industries: Counter[str] = Counter()
    stocks: Counter[str] = Counter()
    for period, group in df.groupby("report_period", sort=True):
        rp = pd.Timestamp(period).strftime("%Y%m%d")
        group = group.dropna(subset=["占净值比"])
        if rp.endswith(("0630", "1231")):
            industries.update(
                str(label)
                for label in group.groupby("申万一级")["占净值比"].sum().nlargest(5).index
                if label
            )
        stocks.update(str(value) for value in group.nlargest(10, "占净值比")["股票名称"] if value)

    def sort_count(items):
        return sorted(items, key=lambda item: (-item[1], item[0]))

    return {
        "行业前五出现次数": [
            {"申万一级": key, "次数": value} for key, value in sort_count(industries.items())
        ],
        "个股前十出现次数": [
            {"股票名称": key, "次数": value} for key, value in sort_count(stocks.items())
        ],
    }


def tracking_payload(df: pd.DataFrame) -> dict[str, dict[str, object]]:
    if df.empty:
        return {"配置跟踪": {}}
    periods = sorted(df["report_period"].dropna().unique())
    semi = [value for value in periods if pd.Timestamp(value).strftime("%m%d") in {"0630", "1231"}]
    if not semi:
        return {"配置跟踪": {}}
    latest_a, previous_a = semi[-1], semi[-2] if len(semi) > 1 else None
    latest_q, previous_q = periods[-1], periods[-2] if len(periods) > 1 else None
    result: dict[str, object] = {}

    def grouped(period: object, column: str) -> dict[str, float]:
        group = df[df["report_period"] == period].dropna(subset=["占净值比"])
        return group.groupby(column)["占净值比"].sum().to_dict()

    def changes(
        current: dict[str, float], prior: dict[str, float], label: str, limit: int | None = None
    ) -> dict[str, list[dict[str, str]]]:
        diff = sorted(
            ((key, current.get(key, 0) - prior.get(key, 0)) for key in set(current) | set(prior)),
            key=lambda item: item[1],
            reverse=True,
        )
        up = [{label: key, "变动": f"↑{value * 100:.2f}%"} for key, value in diff if value > 0.0001]
        down = [
            {label: key, "变动": f"↓{-value * 100:.2f}%"}
            for key, value in reversed(diff)
            if value < -0.0001
        ]
        return {
            "加仓" if not limit else "加仓前五": up[:limit],
            "减仓" if not limit else "减仓前五": down[:limit],
        }

    current_category = grouped(latest_a, "大类")
    prior_category = grouped(previous_a, "大类") if previous_a is not None else {}
    la = pd.Timestamp(latest_a).strftime("%Y%m%d")
    result[f"大类配置({la})"] = [
        {"大类": key, "占净值比": pct(value)}
        for key, value in sorted(current_category.items(), key=lambda item: item[1], reverse=True)
    ]
    if previous_a is not None:
        pa = pd.Timestamp(previous_a).strftime("%Y%m%d")
        result[f"大类配置变化({la}-{pa})"] = changes(current_category, prior_category, "大类")

    current_industry = grouped(latest_a, "申万一级")
    prior_industry = grouped(previous_a, "申万一级") if previous_a is not None else {}
    result[f"前五大行业({la})"] = [
        {"行业": key, "占净值比": pct(value)}
        for key, value in sorted(current_industry.items(), key=lambda item: item[1], reverse=True)[
            :5
        ]
    ]
    if previous_a is not None:
        result[f"行业配置变化({la}-{pa})"] = changes(current_industry, prior_industry, "行业", 5)

    def top_stocks(period: object) -> dict[str, float]:
        group = df[df["report_period"] == period].dropna(subset=["占净值比"])
        top = group.nlargest(10, "占净值比")
        return dict(zip(top["股票名称"], top["占净值比"], strict=True))

    current_stocks = top_stocks(latest_q)
    prior_stocks = top_stocks(previous_q) if previous_q is not None else {}
    lq = pd.Timestamp(latest_q).strftime("%Y%m%d")
    result[f"前十大个股({lq})"] = [
        {"个股": key, "占净值比": pct(value)} for key, value in current_stocks.items()
    ]
    if previous_q is not None:
        pq = pd.Timestamp(previous_q).strftime("%Y%m%d")
        added, reduced, new, exited = [], [], [], []
        for name, value in current_stocks.items():
            if name not in prior_stocks:
                new.append(f"{name}({pct(value)})")
            elif value > prior_stocks[name]:
                added.append(f"{name}({pct(value)};↑{pct(value - prior_stocks[name])})")
            elif value < prior_stocks[name]:
                reduced.append(f"{name}({pct(value)};↓{pct(prior_stocks[name] - value)})")
        for name, value in prior_stocks.items():
            if name not in current_stocks:
                exited.append(f"{name}(前期占比{pct(value)})")
        result[f"个股配置变化({lq}-{pq})"] = {
            "加仓": added or ["无"],
            "减仓": reduced or ["无"],
            "新进": new or ["无"],
            "未进入本期前十大（不代表清仓）": exited or ["无"],
        }
    return {"配置跟踪": result}


def render_capability(
    spec: FundSpec, preference: dict[str, object], cumulative: dict[str, dict[str, int]], years: int
) -> str:
    threshold = max(1, years - 2)
    industry_frequency = {item["申万一级"]: item["次数"] for item in preference["行业前五出现次数"]}
    stock_frequency = {item["股票名称"]: item["次数"] for item in preference["个股前十出现次数"]}

    def quadrant(
        frequency: dict[str, int], pnl: dict[str, int], high: bool, profit: bool
    ) -> list[tuple[str, int, int]]:
        values = []
        for name, value in pnl.items():
            count = frequency.get(name, 0)
            if (count >= threshold) == high and (value > 0) == profit:
                values.append((name, count, value))
        return sorted(values, key=lambda item: item[2], reverse=profit)

    def line(values: list[tuple[str, int, int]]) -> str:
        return "，".join(f"{name}({count}次，{value}万元)" for name, count, value in values) or "无"

    titles = (
        ("象限一：优势板块・高置信度", True, True),
        ("象限二：优势板块・有待验证", False, True),
        ("象限三：劣势板块・菜且爱玩", True, False),
        ("象限四：劣势板块・能力不足", False, False),
    )
    parts = [
        f"基金经理：{spec.manager_name}（{spec.fund_name}）",
        "----",
        f"基金能力圈四象限（T={threshold}，单位：万元）",
    ]
    for title, high, profit in titles:
        parts.extend(
            [
                "",
                title,
                (f"长期重仓（≥{threshold} 次）" if high else f"短期重仓（<{threshold} 次）")
                + ("+ 盈利" if profit else "+ 亏损"),
                "行业：" + line(quadrant(industry_frequency, cumulative["一级"], high, profit)),
                "个股：" + line(quadrant(stock_frequency, cumulative["个股"], high, profit)),
            ]
        )
    unseen_profit = [
        (name, 0, value)
        for name, value in cumulative["一级"].items()
        if name not in industry_frequency and value > 0
    ]
    unseen_loss = [
        (name, 0, value)
        for name, value in cumulative["一级"].items()
        if name not in industry_frequency and value <= 0
    ]
    parts.extend(
        [
            "",
            "从未进入前五的行业",
            "盈利行业（未入 Top5）",
            line(unseen_profit),
            "",
            "亏损行业（未入 Top5）",
            line(unseen_loss),
        ]
    )
    return "\n".join(parts) + "\n"


def render_scale(spec: FundSpec, repo: LakeRepository, as_of: date) -> str:
    scale = repo.scales(spec.fund_symbol)
    start = pd.Timestamp(spec.tenure_start) + pd.offsets.QuarterEnd(0)
    end = last_completed_quarter(min(spec.tenure_end, as_of) if spec.tenure_end else as_of)
    scale = scale[(scale["report_period"] >= start) & (scale["report_period"] <= end)]
    manager = repo.manager_scales(
        spec.manager_name, scale["report_period"].tolist(), spec.fund_symbol
    )
    values = {}
    for row in scale.itertuples():
        own = number(row.scale_yi, 4)
        managed = manager.get(row.report_period)
        values[f"{row.report_period.year}Q{(row.report_period.month - 1) // 3 + 1}"] = {
            "基金规模": own,
            "管理规模": number(managed, 4),
            "规模口径": "同基金已识别份额合计；缺份额时为空",
        }
    return with_manager(spec, {"规模变动(亿元)": values})


def render_turnover(spec: FundSpec, repo: LakeRepository, as_of: date) -> str:
    end_year = min(spec.tenure_end.year, as_of.year) if spec.tenure_end else as_of.year
    values = {
        f"{year}年": {"基金换手率": round(value, 4)}
        for year, value in repo.turnover(spec.fund_symbol).items()
        if spec.tenure_start.year <= year <= end_year
    }
    return with_manager(spec, {"年度换手(倍)": values})


def render_reports(spec: FundSpec, repo: LakeRepository, as_of: date) -> str:
    reports = repo.reports(spec.fund_symbol)
    if not reports.empty:
        reports["report_period"] = pd.to_datetime(reports["report_period"])
        reports = reports[
            (reports["report_period"] >= pd.Timestamp(spec.tenure_start))
            & (reports["report_period"] <= pd.Timestamp(as_of))
        ]
    payload = {}
    for row in reports.itertuples():
        item = {}
        if pd.notna(row.strategy_analysis) and str(row.strategy_analysis).strip():
            item["基金投资策略和运作分析"] = str(row.strategy_analysis).strip()
        if pd.notna(row.outlook) and str(row.outlook).strip():
            item["宏观经济证券市场及行业走势展望"] = str(row.outlook).strip()
        if item:
            payload[str(row.report_label)] = item
    return dumps({f"{spec.fund_symbol}_{spec.manager_name}_{spec.fund_name}": payload})


def drawdown(nav: pd.Series) -> tuple[float, str, int, str]:
    running = nav.cummax()
    series = nav / running - 1
    trough = series.idxmin()
    peak_value = running.loc[trough]
    peak = nav.loc[:trough][nav.loc[:trough] == peak_value].index[-1]
    maximum = float(series.loc[trough] * 100)
    recovery = nav.loc[trough:]
    recovery = recovery[recovery >= nav.loc[peak]]
    repair = "未修复" if recovery.empty else str(round((recovery.index[0] - trough).days / 30, 1))
    return (
        maximum,
        f"{peak.year}年{peak.month}月-{trough.year}年{trough.month}月",
        (trough.year - peak.year) * 12 + trough.month - peak.month,
        repair,
    )


def render_dashboard(spec: FundSpec, repo: LakeRepository, as_of: date) -> str:
    nav = repo.nav(spec.fund_symbol)
    nav = nav[nav.index <= pd.Timestamp(as_of)]
    tenure_end = pd.Timestamp(spec.tenure_end) if spec.tenure_end else pd.Timestamp(as_of)
    cutoff = min(tenure_end, nav.index.max())
    manager_start = pd.Timestamp(spec.tenure_start)
    metric_start = (
        manager_start if manager_start > pd.Timestamp("2016-01-01") else pd.Timestamp("2015-12-31")
    )
    sample = nav[(nav.index >= metric_start) & (nav.index <= cutoff)]
    if len(sample) < 2:
        raise ValueError(f"{spec.fund_symbol} has insufficient NAV inside manager tenure")
    days = (sample.index[-1] - sample.index[0]).days
    annualized = (sample.iloc[-1] / sample.iloc[0]) ** (365 / days) - 1
    volatility = sample.pct_change().dropna().std() * np.sqrt(252)
    max_dd, dd_range, dd_months, repair = drawdown(sample)
    found = repo.fund_found_date(spec.fund_symbol)
    annual_base = (
        found
        if found is not None and found > pd.Timestamp("2016-01-01")
        else pd.Timestamp("2015-12-31")
    )
    yearly = {}
    for year in range(max(2016, annual_base.year), cutoff.year + 1):
        result = annual_return(nav, year, cutoff)
        if result["value"] is not None:
            yearly[year] = result["value"] * 100
    tenure_years = sorted(yearly)
    win_rate = (
        100 * sum(yearly[year] > 0 for year in tenure_years) / len(tenure_years)
        if tenure_years
        else None
    )
    total = sum(yearly[year] for year in tenure_years)
    single = max((yearly[year] for year in tenure_years), default=0) / total * 100 if total else 0
    role = "低效鸡肋"
    if (
        annualized * 100 >= 12
        and volatility * 100 <= 15
        and max_dd >= -15
        and (win_rate or 0) >= 75
    ):
        role = "极致防守"
    elif (
        annualized * 100 >= 6
        and volatility * 100 <= 23
        and max_dd >= -32
        and (win_rate is None or win_rate >= 50)
    ):
        role = "核心稳健"
    elif annualized * 100 >= 5:
        role = "进攻弹性"
    tags = ["高收益" if annualized >= 0.12 else "中收益" if annualized >= 0.06 else "低收益"]
    tags.append("低波" if volatility <= 0.15 else "中波" if volatility <= 0.25 else "高波")
    tags.append("浅回撤" if max_dd >= -20 else "中回撤" if max_dd >= -32 else "深回撤")
    output = {
        "基金代码": spec.fund_symbol,
        "基金简称": spec.fund_name,
        "基金经理": spec.manager_name,
        "实际净值截止日": nav.index.max().date().isoformat(),
        "研究截止日": as_of.isoformat(),
        "年度收益口径": "上年末最后有效净值至本年最后有效净值；成立首年为非完整年度",
        "收益回撤": {
            "任职区间": display_tenure(spec.tenure_start, spec.tenure_end),
            "任职年化\n(%)": round(annualized * 100, 2),
            "年化波动\n（%）": round(volatility * 100, 2),
            "最大回撤\n（%）": round(max_dd, 2),
            "回撤区间": dd_range,
            "回撤时长\n（月）": dd_months,
            "修复耗时\n(月)": repair,
            "年度胜率%\n（正收益占比）": round(win_rate, 2) if win_rate is not None else None,
            "单一年度收益占比%\n（分布特征）": f"{single:.2f}",
            "角色标签": role,
            "完整标签": f"{role}·{'/'.join(tags)}",
            "标签聚类": "防守压仓" if role == "极致防守" else role,
        },
        "年度收益(%)": {
            f"{year}年收益": round(yearly[year], 2) if year in yearly else None
            for year in range(as_of.year, 2015, -1)
        },
    }
    return dumps(output, pretty=True)


def render_all(
    spec: FundSpec, repo: LakeRepository, holdings: pd.DataFrame, as_of: date
) -> dict[str, str]:
    quarterly = pnl_by_quarter(holdings, as_of)
    last_date = last_completed_quarter(min(spec.tenure_end, as_of) if spec.tenure_end else as_of)
    for period in pd.period_range(spec.tenure_start, last_date, freq="Q"):
        quarterly.setdefault(str(period), {"大类": {}, "一级": {}, "个股": {}})
    quarterly = dict(sorted(quarterly.items()))
    annual, cumulative = annual_and_cumulative(quarterly)
    preference = preference_payload(holdings)
    years = len({key[:4] for key in quarterly})
    feature_data = feature_payload(holdings)
    quarter_features = feature_payload(holdings, include_quarters=True)["配置特征"]
    if quarter_features:
        latest = max(quarter_features, key=lambda row: row["报告期"])
        period = pd.Timestamp(latest["报告期"])
        sample = holdings[holdings["report_period"] == period].dropna(subset=["占净值比"]).nlargest(10, "占净值比")
        feature_data["最新季度快照"] = {
            **latest, "披露口径": "latest_top10", "说明": "前十大样本指标，不等同完整持仓。完整持仓分析保留原半年报/年报口径。",
            "前十大": [{"股票代码": r.holding_symbol, "股票名称": r.股票名称, "占净值比": pct(r.占净值比)} for r in sample.itertuples()],
        }
        feature_data["最新季度快照"].update(feature_payload(sample, include_quarters=True)["配置特征"][0] if not sample.empty else {})
        feature_data["最新季度快照"]["披露口径"] = "latest_top10"
    return {
        "基金配置": render_config(spec, holdings),
        "季度盈亏": with_manager(spec, {"季度盈亏(万元)": quarterly}),
        "年度盈亏": with_manager(spec, {"年度盈亏(万元)": annual}),
        "累计盈亏": with_manager(spec, {"累计盈亏(万元)": {"全阶段累计": cumulative}}),
        "配置特征": with_manager(spec, feature_data),
        "配置偏好": with_manager(spec, preference),
        "配置跟踪": with_manager(spec, tracking_payload(holdings)),
        "投资能力": render_capability(spec, preference, cumulative, years),
        "基金规模": render_scale(spec, repo, as_of),
        "基金换手": render_turnover(spec, repo, as_of),
        "季度运作报告": render_reports(spec, repo, as_of),
        "数据看板": render_dashboard(spec, repo, as_of),
    }
