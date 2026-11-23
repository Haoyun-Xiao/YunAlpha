from __future__ import annotations

import math

import pandas as pd
import pytest

from backtest_platform.fund_backtest import (
    BacktestRequest,
    FundSeries,
    run_fund_backtest_analysis_window,
    run_fund_backtest,
)


def series(values: list[float], start: str = "2021-01-01") -> pd.Series:
    dates = pd.bdate_range(start, periods=len(values))
    return pd.Series(values, index=dates, dtype=float)


def test_backtest_request_default_end_date_uses_current_data_cutoff() -> None:
    assert BacktestRequest(funds=[]).end_date == "2026-06-30"


def test_backtest_normalizes_manual_weights_and_outputs_metrics() -> None:
    funds = {
        "A": FundSeries("A", "基金A", "经理A", series([1.0, 1.1, 1.2, 1.1, 1.3])),
        "B": FundSeries("B", "基金B", "经理B", series([1.0, 1.0, 1.1, 1.2, 1.2])),
    }
    benchmark = FundSeries("000300.SH", "沪深300", "", series([1.0, 1.05, 1.08, 1.02, 1.1]))

    result = run_fund_backtest(
        BacktestRequest(
            funds=[{"id": "A", "weight": 70}, {"id": "B", "weight": 30}],
            weight_mode="manual",
            benchmark="000300.SH",
        ),
        funds,
        {"000300.SH": benchmark},
    )

    assert result["weights"] == pytest.approx({"A": 0.7, "B": 0.3})
    assert result["metrics"]["total_return"] == pytest.approx(0.27)
    assert result["series"]["portfolio"][0]["value"] == pytest.approx(1.0)
    assert result["series"]["portfolio"][-1]["value"] == pytest.approx(1.27)
    assert result["series"]["benchmark"][-1]["value"] == pytest.approx(1.1)
    assert result["annual_returns"][0]["portfolio"] == pytest.approx(0.27)
    assert result["annual_contributions"][0]["funds"]["A"] == pytest.approx(0.21)
    assert result["annual_contributions"][0]["funds"]["B"] == pytest.approx(0.06)
    assert [row["label"] for row in result["period_returns"]] == ["近1月", "近3月", "近6月", "近1年", "近3年", "运作以来"]
    since_inception = next(row for row in result["period_returns"] if row["period"] == "all")
    assert since_inception["portfolio"] == pytest.approx(0.27)
    assert since_inception["benchmark"] == pytest.approx(0.1)
    assert since_inception["excess"] == pytest.approx(0.17)
    period_contribution = next(row for row in result["period_contributions"] if row["period"] == "all")
    assert period_contribution["funds"]["A"] == pytest.approx(0.21)
    assert period_contribution["funds"]["B"] == pytest.approx(0.06)
    assert period_contribution["total"] == pytest.approx(0.27)


def test_weekly_frequency_resamples_nav_and_uses_weekly_annualization() -> None:
    dates = pd.bdate_range("2021-01-04", periods=10)
    nav = pd.Series([1.0, 1.01, 1.02, 1.03, 1.04, 1.05, 1.06, 1.07, 1.08, 1.09], index=dates)
    funds = {"A": FundSeries("A", "基金A", "经理A", nav)}
    benchmark = FundSeries("000300.SH", "沪深300", "", pd.Series([1.0] * len(dates), index=dates))

    result = run_fund_backtest(
        BacktestRequest(
            funds=[{"id": "A"}],
            weight_mode="equal",
            benchmark="000300.SH",
            start_date="2021-01-04",
            end_date="2021-01-15",
            data_frequency="weekly",
        ),
        funds,
        {"000300.SH": benchmark},
    )

    weekly_return = 1.09 / 1.04 - 1.0
    expected_vol = pd.Series([0.0, weekly_return]).std(ddof=0) * math.sqrt(52)
    assert result["frequency"] == "weekly"
    assert result["frequency_label"] == "周频"
    assert result["periods_per_year"] == 52
    assert result["window"]["days"] == 2
    assert [point["date"] for point in result["series"]["portfolio"]] == ["2021-01-08", "2021-01-15"]
    assert result["metrics"]["total_return"] == pytest.approx(weekly_return)
    assert result["metrics"]["annual_volatility"] == pytest.approx(expected_vol)


def test_weekly_rebalance_plan_maps_to_next_frequency_point_without_warning() -> None:
    dates = pd.bdate_range("2021-01-04", periods=10)
    funds = {
        "A": FundSeries("A", "基金A", "经理A", pd.Series([1.0, 1.0, 1.0, 1.0, 1.0, 1.1, 1.1, 1.1, 1.1, 1.1], index=dates)),
        "B": FundSeries("B", "基金B", "经理B", pd.Series([1.0] * len(dates), index=dates)),
    }
    benchmark = FundSeries("000300.SH", "沪深300", "", pd.Series([1.0] * len(dates), index=dates))

    result = run_fund_backtest(
        BacktestRequest(
            funds=[{"id": "A"}, {"id": "B"}],
            benchmark="000300.SH",
            start_date="2021-01-04",
            end_date="2021-01-15",
            data_frequency="weekly",
            rebalance_plan=[
                {"date": "2021-01-06", "holdings": [{"fundId": "A", "weight": 100}]},
                {"date": "2021-01-12", "holdings": [{"fundId": "B", "weight": 100}]},
            ],
        ),
        funds,
        {"000300.SH": benchmark},
    )

    assert result["rebalance_dates"] == ["2021-01-08", "2021-01-15"]
    assert result["weights"] == pytest.approx({"A": 0.0, "B": 1.0})
    assert not any("调仓在" in warning for warning in result["warnings"])


def test_monthly_correlation_warns_when_sample_is_small_but_returns_result() -> None:
    dates = pd.bdate_range("2021-01-04", periods=70)
    funds = {
        "A": FundSeries("A", "基金A", "经理A", pd.Series([1.0 + index * 0.01 for index in range(len(dates))], index=dates)),
        "B": FundSeries("B", "基金B", "经理B", pd.Series([1.0 + index * 0.005 for index in range(len(dates))], index=dates)),
    }
    benchmark = FundSeries("000300.SH", "沪深300", "", pd.Series([1.0] * len(dates), index=dates))

    result = run_fund_backtest(
        BacktestRequest(
            funds=[{"id": "A"}, {"id": "B"}],
            benchmark="000300.SH",
            start_date="2021-01-04",
            end_date="2021-04-09",
            data_frequency="monthly",
        ),
        funds,
        {"000300.SH": benchmark},
    )

    assert result["frequency"] == "monthly"
    assert result["attribution"]["correlation"]["fund_matrix"]["matrix"]
    assert any("月频" in warning and "样本" in warning for warning in result["warnings"])


def test_backtest_outputs_drawdown_profile_and_annual_win_rate_metrics() -> None:
    dates = pd.to_datetime(
        [
            "2020-12-31",
            "2021-12-31",
            "2022-03-31",
            "2022-06-30",
            "2022-12-30",
            "2023-12-29",
            "2024-12-31",
            "2025-12-31",
            "2026-05-29",
        ]
    )
    nav = pd.Series([1.0, 1.2, 1.3, 0.9, 1.0, 1.4, 1.2, 1.5, 1.6], index=dates)
    funds = {"A": FundSeries("A", "基金A", "经理A", nav)}
    benchmark = FundSeries("000300.SH", "沪深300", "", pd.Series([1.0] * len(dates), index=dates))

    result = run_fund_backtest(
        BacktestRequest(
            funds=[{"id": "A"}],
            weight_mode="equal",
            benchmark="000300.SH",
            start_date="2020-12-31",
            end_date="2026-05-29",
        ),
        funds,
        {"000300.SH": benchmark},
    )

    assert result["metrics"]["drawdown_period"] == "2022年3月 - 2022年6月"
    assert result["metrics"]["drawdown_months"] == 3
    assert result["metrics"]["recovery_months"] == pytest.approx(18.2)
    assert result["metrics"]["annual_win_rate"] == pytest.approx(0.6)


def test_drawdown_profile_uses_full_backtest_window_before_2017() -> None:
    dates = pd.to_datetime(["2015-12-31", "2016-12-30", "2017-12-29", "2018-12-31"])
    nav = pd.Series([2.0, 1.0, 1.2, 1.1], index=dates)
    funds = {"A": FundSeries("A", "基金A", "经理A", nav)}
    benchmark = FundSeries("000300.SH", "沪深300", "", pd.Series([1.0] * len(dates), index=dates))

    result = run_fund_backtest(
        BacktestRequest(
            funds=[{"id": "A"}],
            weight_mode="equal",
            benchmark="000300.SH",
            start_date="2015-12-31",
            end_date="2018-12-31",
        ),
        funds,
        {"000300.SH": benchmark},
    )

    assert result["metrics"]["max_drawdown"] == pytest.approx(-0.5)
    assert result["metrics"]["drawdown_period"] == "2015年12月 - 2016年12月"
    assert result["metrics"]["drawdown_months"] == 12


def test_annual_returns_and_contributions_use_previous_year_end_base() -> None:
    dates = pd.to_datetime(["2020-12-31", "2021-01-04", "2021-12-31"])
    funds = {"A": FundSeries("A", "基金A", "经理A", pd.Series([1.0, 1.1, 1.1], index=dates))}
    benchmark = FundSeries("000300.SH", "沪深300", "", pd.Series([1.0, 1.0, 1.0], index=dates))

    result = run_fund_backtest(
        BacktestRequest(
            funds=[{"id": "A"}],
            weight_mode="equal",
            benchmark="000300.SH",
            start_date="2020-12-31",
            end_date="2021-12-31",
        ),
        funds,
        {"000300.SH": benchmark},
    )

    annual_2021 = next(row for row in result["annual_returns"] if row["year"] == 2021)
    contribution_2021 = next(row for row in result["annual_contributions"] if row["year"] == 2021)
    assert annual_2021["portfolio"] == pytest.approx(0.1)
    assert contribution_2021["total"] == pytest.approx(0.1)


def test_backtest_outputs_sleeve_attribution_rows_and_excess_correlation() -> None:
    dates = pd.bdate_range("2021-01-01", periods=6)
    funds = {
        "A": FundSeries("A", "基金A", "经理A", pd.Series([1.0, 1.1, 1.0, 1.2, 1.3, 1.35], index=dates)),
        "B": FundSeries("B", "基金B", "经理B", pd.Series([1.0, 1.0, 1.2, 1.1, 1.0, 1.05], index=dates)),
        "C": FundSeries("C", "基金C", "经理C", pd.Series([1.0, 0.95, 0.9, 0.92, 0.96, 1.0], index=dates)),
    }
    benchmark = FundSeries("000300.SH", "沪深300", "", pd.Series([1.0, 1.01, 1.02, 1.03, 1.04, 1.05], index=dates))

    result = run_fund_backtest(
        BacktestRequest(
            funds=[{"id": "A", "weight": 25}, {"id": "B", "weight": 25}, {"id": "C", "weight": 50}],
            sleeves=[
                {"id": "defensive", "name": "防守组", "funds": ["A", "B"]},
                {"id": "aggressive", "name": "进攻组", "funds": ["C"]},
            ],
            weight_mode="manual",
            benchmark="000300.SH",
            start_date="2021-01-01",
            end_date="2021-01-08",
        ),
        funds,
        {"000300.SH": benchmark},
    )

    attribution = result["attribution"]
    rows = attribution["rows"]
    assert rows[0]["id"] == "portfolio"
    assert rows[0]["type"] == "portfolio"
    assert {row["id"] for row in rows if row["type"] == "sleeve"} == {"defensive", "aggressive"}
    assert {row["id"] for row in rows if row["type"] == "fund"} == {"A", "B", "C"}
    assert all("average_weight" in row and "current_weight" in row for row in rows)
    assert all("volatility_contribution" in row for row in rows)
    assert all("annual_volatility" in row for row in rows)
    assert all("drawdown_contribution" in row for row in rows)

    portfolio = next(row for row in rows if row["id"] == "portfolio")
    defensive = next(row for row in rows if row["id"] == "defensive")
    aggressive = next(row for row in rows if row["id"] == "aggressive")
    fund_a = next(row for row in rows if row["id"] == "A")
    fund_b = next(row for row in rows if row["id"] == "B")
    fund_c = next(row for row in rows if row["id"] == "C")
    assert defensive["level"] == 1
    assert fund_a["parent_id"] == "defensive"
    assert fund_a["return_contribution_to_parent"] is not None
    assert fund_a["return_contribution_share_to_parent"] is not None
    assert fund_a["volatility_contribution_to_parent"] is not None
    assert fund_a["drawdown_contribution_to_parent"] is not None

    assert portfolio["annual_volatility"] == pytest.approx(result["metrics"]["annual_volatility"])
    assert portfolio["volatility_contribution"] == pytest.approx(portfolio["annual_volatility"])
    assert portfolio["volatility_contribution"] != pytest.approx(1.0)
    assert defensive["volatility_contribution"] + aggressive["volatility_contribution"] == pytest.approx(
        portfolio["annual_volatility"]
    )
    assert fund_a["volatility_contribution"] + fund_b["volatility_contribution"] + fund_c["volatility_contribution"] == pytest.approx(
        portfolio["annual_volatility"]
    )
    assert fund_a["volatility_contribution_to_parent"] + fund_b["volatility_contribution_to_parent"] == pytest.approx(
        defensive["annual_volatility"]
    )
    assert fund_c["volatility_contribution_to_parent"] == pytest.approx(aggressive["annual_volatility"])
    assert fund_a["return_contribution_to_parent"] + fund_b["return_contribution_to_parent"] == pytest.approx(
        defensive["period_return"]
    )
    assert fund_a["return_contribution_to_parent"] != pytest.approx(
        fund_a["return_contribution"] / defensive["average_weight"]
    )
    assert fund_a["return_contribution_share_to_parent"] == pytest.approx(
        fund_a["return_contribution_to_parent"] / defensive["period_return"]
    )
    assert fund_a["drawdown_contribution_to_parent"] + fund_b["drawdown_contribution_to_parent"] == pytest.approx(
        defensive["max_drawdown"]
    )
    assert defensive["current_weight"] == pytest.approx(fund_a["current_weight"] + fund_b["current_weight"])

    sleeve_corr = attribution["correlation"]["sleeves"]
    assert sleeve_corr["labels"] == ["防守组", "进攻组"]
    assert len(sleeve_corr["matrix"]) == 2
    assert sleeve_corr["matrix"][0][0] == pytest.approx(1.0)
    sleeve_fund_matrices = attribution["correlation"]["sleeve_fund_matrices"]
    defensive_matrix = next(item for item in sleeve_fund_matrices if item["id"] == "defensive")
    aggressive_matrix = next(item for item in sleeve_fund_matrices if item["id"] == "aggressive")
    assert defensive_matrix["name"] == "防守组"
    assert defensive_matrix["labels"] == ["基金A", "基金B"]
    assert len(defensive_matrix["matrix"]) == 2
    assert defensive_matrix["matrix"][0][0] == pytest.approx(1.0)
    assert aggressive_matrix["labels"] == ["基金C"]
    assert aggressive_matrix["matrix"] == [[pytest.approx(1.0)]]
    assert attribution["correlation"]["fund_summary"]["highest_pair"]["left"]
    assert attribution["fund_details"]["A"]["self"]["period_return"] == pytest.approx(0.35)


def test_attribution_annual_sleeve_returns_are_based_on_sleeve_nav_path() -> None:
    dates = pd.to_datetime(["2020-12-31", "2021-01-04", "2021-12-31"])
    funds = {
        "A": FundSeries("A", "基金A", "经理A", pd.Series([1.0, 1.1, 1.1], index=dates)),
        "B": FundSeries("B", "基金B", "经理B", pd.Series([1.0, 1.0, 1.0], index=dates)),
    }
    benchmark = FundSeries("000300.SH", "沪深300", "", pd.Series([1.0, 1.0, 1.0], index=dates))

    result = run_fund_backtest(
        BacktestRequest(
            funds=[{"id": "A", "weight": 50}, {"id": "B", "weight": 50}],
            sleeves=[
                {"id": "defensive", "name": "防守组", "funds": ["A"]},
                {"id": "balanced", "name": "稳健组", "funds": ["B"]},
            ],
            weight_mode="manual",
            benchmark="000300.SH",
            start_date="2020-12-31",
            end_date="2021-12-31",
        ),
        funds,
        {"000300.SH": benchmark},
    )

    defensive_2021 = next(row for row in result["attribution"]["annual_sleeve_returns"]["defensive"] if row["year"] == 2021)
    assert defensive_2021["return"] == pytest.approx(0.1)


def test_correlation_uses_pairwise_valid_returns_for_late_starting_funds() -> None:
    dates = pd.bdate_range("2021-01-01", periods=5)
    funds = {
        "A": FundSeries("A", "基金A", "经理A", pd.Series([1.0, 1.1, 1.2, 1.3, 1.4], index=dates)),
        "B": FundSeries("B", "基金B", "经理B", pd.Series([None, None, 1.0, 1.1, 1.3], index=dates, dtype=float)),
    }
    benchmark = FundSeries("000300.SH", "沪深300", "", pd.Series([1.0] * len(dates), index=dates))

    result = run_fund_backtest(
        BacktestRequest(
            funds=[{"id": "A"}, {"id": "B"}],
            benchmark="000300.SH",
            start_date="2021-01-01",
            end_date="2021-01-07",
        ),
        funds,
        {"000300.SH": benchmark},
    )

    labels = result["attribution"]["correlation"]["fund_matrix"]["labels"]
    matrix = result["attribution"]["correlation"]["fund_matrix"]["matrix"]
    index_a = labels.index("基金A")
    index_b = labels.index("基金B")
    expected = pd.concat([funds["A"].nav.pct_change(fill_method=None), funds["B"].nav.pct_change(fill_method=None)], axis=1).corr().iloc[0, 1]
    assert matrix[index_a][index_b] == pytest.approx(expected)


def test_negative_total_return_does_not_report_contribution_share() -> None:
    funds = {"A": FundSeries("A", "基金A", "经理A", series([1.0, 0.9, 0.8]))}
    benchmark = FundSeries("000300.SH", "沪深300", "", series([1.0, 1.0, 1.0]))

    result = run_fund_backtest(
        BacktestRequest(funds=[{"id": "A"}], weight_mode="equal", benchmark="000300.SH"),
        funds,
        {"000300.SH": benchmark},
    )

    portfolio_row = next(row for row in result["attribution"]["rows"] if row["type"] == "portfolio")
    fund_row = next(row for row in result["attribution"]["rows"] if row["type"] == "fund")
    assert portfolio_row["return_contribution_share"] is None
    assert fund_row["return_contribution_share"] is None


def test_backtest_without_sleeves_returns_ungrouped_attribution() -> None:
    funds = {"A": FundSeries("A", "基金A", "经理A", series([1.0, 1.1, 1.2]))}
    benchmark = FundSeries("000300.SH", "沪深300", "", series([1.0, 1.0, 1.0]))

    result = run_fund_backtest(
        BacktestRequest(funds=[{"id": "A"}], weight_mode="equal", benchmark="000300.SH"),
        funds,
        {"000300.SH": benchmark},
    )

    sleeve_rows = [row for row in result["attribution"]["rows"] if row["type"] == "sleeve"]
    assert len(sleeve_rows) == 1
    assert sleeve_rows[0]["id"] == "ungrouped"
    assert sleeve_rows[0]["name"] == "未分组"


def test_equal_weight_backtest_uses_common_date_window() -> None:
    funds = {
        "A": FundSeries("A", "基金A", "经理A", series([1.0, 1.2, 1.4, 1.6])),
        "B": FundSeries("B", "基金B", "经理B", series([1.0, 1.1, 1.2], start="2021-01-04")),
    }
    benchmark = FundSeries("000300.SH", "沪深300", "", series([1.0, 1.1, 1.2, 1.3, 1.4]))

    result = run_fund_backtest(
        BacktestRequest(
            funds=[{"id": "A"}, {"id": "B"}],
            weight_mode="equal",
            benchmark="000300.SH",
        ),
        funds,
        {"000300.SH": benchmark},
    )

    assert result["window"]["start"] == "2021-01-01"
    assert result["window"]["end"] == "2021-01-06"
    assert result["weights"] == pytest.approx({"A": 0.5, "B": 0.5})
    assert result["warnings"]


def test_backtest_rebalances_to_target_on_qe_jan_quarterly_dates() -> None:
    dates = pd.to_datetime(["2021-01-29", "2021-02-01", "2021-02-02", "2021-02-03"])
    funds = {
        "A": FundSeries("A", "基金A", "经理A", pd.Series([1.0, 2.0, 2.0, 2.0], index=dates)),
        "B": FundSeries("B", "基金B", "经理B", pd.Series([1.0, 1.0, 2.0, 2.0], index=dates)),
    }
    benchmark = FundSeries("000300.SH", "沪深300", "", pd.Series([1.0, 1.0, 1.0, 1.0], index=dates))

    result = run_fund_backtest(
        BacktestRequest(
            funds=[{"id": "A"}, {"id": "B"}],
            weight_mode="equal",
            benchmark="000300.SH",
        ),
        funds,
        {"000300.SH": benchmark},
    )

    assert result["rebalance_dates"] == ["2021-01-29", "2021-02-01"]
    assert result["series"]["portfolio"][-1]["value"] == pytest.approx(2.25)
    assert result["metrics"]["total_return"] == pytest.approx(1.25)


def test_late_starting_fund_joins_on_next_quarterly_rebalance_with_warning() -> None:
    dates = pd.to_datetime(["2021-01-29", "2021-02-01", "2021-02-02", "2021-04-29", "2021-04-30", "2021-05-03"])
    funds = {
        "A": FundSeries("A", "老基金", "经理A", pd.Series([1.0, 1.0, 1.2, 1.2, 1.2, 1.2], index=dates)),
        "B": FundSeries("B", "新基金", "经理B", pd.Series([None, None, 1.0, 1.0, 2.0, 2.0], index=dates, dtype=float)),
    }
    benchmark = FundSeries("000300.SH", "沪深300", "", pd.Series([1.0] * len(dates), index=dates))

    result = run_fund_backtest(
        BacktestRequest(
            funds=[{"id": "A"}, {"id": "B"}],
            weight_mode="equal",
            benchmark="000300.SH",
            start_date="2021-01-29",
            end_date="2021-05-03",
        ),
        funds,
        {"000300.SH": benchmark},
    )

    assert any("新基金" in warning and "下一次季度调仓" in warning for warning in result["warnings"])
    assert result["rebalance_dates"] == ["2021-01-29", "2021-02-01", "2021-04-30"]
    assert result["series"]["portfolio"][-1]["value"] == pytest.approx(1.2)
    assert result["annual_contributions"][0]["funds"]["A"] == pytest.approx(0.2)
    assert result["annual_contributions"][0]["funds"].get("B", 0.0) == pytest.approx(0.0)


def test_rebalance_plan_switches_target_holdings_on_planned_date() -> None:
    dates = pd.to_datetime(["2021-01-04", "2021-01-05", "2021-01-06", "2021-01-07"])
    funds = {
        "A": FundSeries("A", "基金A", "经理A", pd.Series([1.0, 2.0, 2.0, 2.0], index=dates)),
        "B": FundSeries("B", "基金B", "经理B", pd.Series([1.0, 1.0, 2.0, 2.0], index=dates)),
    }
    benchmark = FundSeries("000300.SH", "沪深300", "", pd.Series([1.0] * len(dates), index=dates))

    result = run_fund_backtest(
        BacktestRequest(
            funds=[{"id": "A"}, {"id": "B"}],
            sleeves=[
                {"id": "defensive", "name": "防守", "funds": ["A"]},
                {"id": "growth", "name": "进攻", "funds": ["B"]},
            ],
            benchmark="000300.SH",
            start_date="2021-01-04",
            end_date="2021-01-07",
            rebalance_plan=[
                {
                    "date": "2021-01-04",
                    "source": "initial",
                    "holdings": [{"fundId": "A", "sleeveId": "defensive", "weight": 100}],
                },
                {
                    "date": "2021-01-06",
                    "source": "manual",
                    "holdings": [{"fundId": "B", "sleeveId": "growth", "weight": 100}],
                },
            ],
        ),
        funds,
        {"000300.SH": benchmark},
    )

    assert result["rebalance_dates"] == ["2021-01-04", "2021-01-06"]
    assert result["weights"] == pytest.approx({"A": 0.0, "B": 1.0})
    assert result["series"]["portfolio"][-1]["value"] == pytest.approx(2.0)
    assert result["funds"] == [
        {"id": "A", "name": "基金A", "manager": "经理A", "weight": 0.0},
        {"id": "B", "name": "基金B", "manager": "经理B", "weight": 1.0},
    ]


def test_cash_constrained_backtest_buys_fractional_shares_and_tracks_account_metrics() -> None:
    dates = pd.to_datetime(["2021-01-04", "2021-01-05"])
    funds = {"A": FundSeries("A", "基金A", "经理A", pd.Series([1.0, 1.1], index=dates))}
    benchmark = FundSeries("000300.SH", "沪深300", "", pd.Series([1.0, 1.0], index=dates))

    result = run_fund_backtest(
        BacktestRequest(
            funds=[{"id": "A"}],
            benchmark="000300.SH",
            start_date="2021-01-04",
            end_date="2021-01-05",
            rebalance_plan=[
                {
                    "date": "2021-01-04",
                    "source": "initial",
                    "holdings": [{"fundId": "A", "sleeveId": "defensive", "weight": 100}],
                }
            ],
            initial_cash=1000.0,
            fee_config={"purchase": 0.001, "redemption": 0.0},
        ),
        funds,
        {"000300.SH": benchmark},
    )

    position = result["final_positions"][0]
    assert result["cash_mode"] is True
    assert result["series"]["portfolio"][0]["value"] == pytest.approx(1.0)
    assert result["series"]["portfolio"][-1]["value"] == pytest.approx(1.098901)
    assert result["series"]["account_value"][-1]["value"] == pytest.approx(1098.901)
    assert result["metrics"]["initial_cash"] == pytest.approx(1000.0)
    assert result["metrics"]["final_value"] == pytest.approx(1098.901)
    assert result["metrics"]["net_contribution"] == pytest.approx(1000.0)
    assert result["metrics"]["total_profit"] == pytest.approx(98.901)
    assert result["metrics"]["cash_balance"] == pytest.approx(0.001)
    assert result["metrics"]["total_fees"] == pytest.approx(0.999)
    assert position["fundId"] == "A"
    assert position["quantity"] == pytest.approx(999.0)
    assert position["cost_amount"] == pytest.approx(999.999)
    assert position["market_value"] == pytest.approx(1098.9)
    assert position["profit"] == pytest.approx(98.901)
    assert position["weight"] == pytest.approx(1098.9 / 1098.901)


def test_cash_constrained_transaction_fee_applies_to_initial_buy() -> None:
    dates = pd.to_datetime(["2021-01-04", "2021-01-05"])
    funds = {"A": FundSeries("A", "基金A", "经理A", pd.Series([1.0, 1.1], index=dates))}
    benchmark = FundSeries("000300.SH", "沪深300", "", pd.Series([1.0, 1.0], index=dates))

    result = run_fund_backtest(
        BacktestRequest(
            funds=[{"id": "A"}],
            benchmark="000300.SH",
            start_date="2021-01-04",
            end_date="2021-01-05",
            rebalance_plan=[
                {
                    "date": "2021-01-04",
                    "source": "initial",
                    "holdings": [{"fundId": "A", "sleeveId": "defensive", "weight": 100}],
                }
            ],
            initial_cash=1000.0,
            fee_config={"purchase": 0.0, "redemption": 0.0, "transaction": 0.001},
        ),
        funds,
        {"000300.SH": benchmark},
    )

    assert result["final_positions"][0]["quantity"] == pytest.approx(999.0)
    assert result["metrics"]["total_fees"] == pytest.approx(0.999)
    assert result["metrics"]["cash_balance"] == pytest.approx(0.001)


def test_cash_constrained_cash_inflow_does_not_pollute_time_weighted_nav() -> None:
    dates = pd.to_datetime(["2021-01-04", "2021-01-05", "2021-01-06"])
    funds = {"A": FundSeries("A", "基金A", "经理A", pd.Series([1.0, 1.1, 1.21], index=dates))}
    benchmark = FundSeries("000300.SH", "沪深300", "", pd.Series([1.0, 1.0, 1.0], index=dates))

    result = run_fund_backtest(
        BacktestRequest(
            funds=[{"id": "A"}],
            benchmark="000300.SH",
            start_date="2021-01-04",
            end_date="2021-01-06",
            rebalance_plan=[
                {"date": "2021-01-04", "holdings": [{"fundId": "A", "weight": 100}]},
                {"date": "2021-01-05", "cashFlow": 110, "holdings": [{"fundId": "A", "weight": 100}]},
            ],
            initial_cash=100.0,
            fee_config={"purchase": 0.0, "redemption": 0.0},
        ),
        funds,
        {"000300.SH": benchmark},
    )

    assert [point["value"] for point in result["series"]["portfolio"]] == pytest.approx([1.0, 1.1, 1.21])
    assert [point["value"] for point in result["series"]["account_value"]] == pytest.approx([100.0, 220.0, 242.0])
    assert result["metrics"]["net_contribution"] == pytest.approx(210.0)
    assert result["metrics"]["total_profit"] == pytest.approx(32.0)


def test_cash_constrained_cash_outflow_sells_positions_when_cash_is_not_enough() -> None:
    dates = pd.to_datetime(["2021-01-04", "2021-01-05"])
    funds = {"A": FundSeries("A", "基金A", "经理A", pd.Series([1.0, 1.0], index=dates))}
    benchmark = FundSeries("000300.SH", "沪深300", "", pd.Series([1.0, 1.0], index=dates))

    result = run_fund_backtest(
        BacktestRequest(
            funds=[{"id": "A"}],
            benchmark="000300.SH",
            start_date="2021-01-04",
            end_date="2021-01-05",
            rebalance_plan=[
                {"date": "2021-01-04", "holdings": [{"fundId": "A", "weight": 100}]},
                {"date": "2021-01-05", "cashFlow": -10, "holdings": [{"fundId": "A", "weight": 100}]},
            ],
            initial_cash=100.0,
            fee_config={"purchase": 0.0, "redemption": 0.0},
        ),
        funds,
        {"000300.SH": benchmark},
    )

    assert result["final_positions"][0]["quantity"] == pytest.approx(90.0)
    assert result["metrics"]["cash_balance"] == pytest.approx(0.0)
    assert result["metrics"]["net_contribution"] == pytest.approx(90.0)
    assert result["metrics"]["total_profit"] == pytest.approx(0.0)
    assert any(row["type"] == "sell" and row["amount"] == pytest.approx(10.0) for row in result["transactions"])
    assert any(row["type"] == "cash_flow" and row["cash_change"] == pytest.approx(-10.0) for row in result["transactions"])


def test_cash_constrained_cash_outflow_sells_extra_for_redemption_fee() -> None:
    dates = pd.to_datetime(["2021-01-04", "2021-01-05"])
    funds = {"A": FundSeries("A", "基金A", "经理A", pd.Series([1.0, 1.0], index=dates))}
    benchmark = FundSeries("000300.SH", "沪深300", "", pd.Series([1.0, 1.0], index=dates))

    result = run_fund_backtest(
        BacktestRequest(
            funds=[{"id": "A"}],
            benchmark="000300.SH",
            start_date="2021-01-04",
            end_date="2021-01-05",
            rebalance_plan=[
                {"date": "2021-01-04", "holdings": [{"fundId": "A", "weight": 100}]},
                {"date": "2021-01-05", "cashFlow": -10, "holdings": [{"fundId": "A", "weight": 100}]},
            ],
            initial_cash=100.0,
            fee_config={"purchase": 0.0, "redemption": 0.01, "transaction": 0.001},
        ),
        funds,
        {"000300.SH": benchmark},
    )

    buy = next(row for row in result["transactions"] if row["type"] == "buy" and row["date"] == "2021-01-04")
    sell = next(row for row in result["transactions"] if row["type"] == "sell" and row["date"] == "2021-01-05")
    assert sell["amount"] > 10.0
    assert sell["fee"] == pytest.approx(sell["amount"] * 0.011)
    assert result["metrics"]["cash_balance"] >= 0
    assert result["metrics"]["total_fees"] == pytest.approx(buy["fee"] + sell["fee"])
    assert result["metrics"]["total_profit"] == pytest.approx(-result["metrics"]["total_fees"])


def test_cash_constrained_rebalance_sells_before_buying_new_target() -> None:
    dates = pd.to_datetime(["2021-01-04", "2021-01-05"])
    funds = {
        "A": FundSeries("A", "基金A", "经理A", pd.Series([1.0, 1.0], index=dates)),
        "B": FundSeries("B", "基金B", "经理B", pd.Series([1.0, 1.0], index=dates)),
    }
    benchmark = FundSeries("000300.SH", "沪深300", "", pd.Series([1.0, 1.0], index=dates))

    result = run_fund_backtest(
        BacktestRequest(
            funds=[{"id": "A"}, {"id": "B"}],
            benchmark="000300.SH",
            start_date="2021-01-04",
            end_date="2021-01-05",
            rebalance_plan=[
                {"date": "2021-01-04", "holdings": [{"fundId": "A", "sleeveId": "defensive", "weight": 100}]},
                {"date": "2021-01-05", "holdings": [{"fundId": "B", "sleeveId": "growth", "weight": 100}]},
            ],
            initial_cash=100.0,
            fee_config={"purchase": 0.0, "redemption": 0.0},
        ),
        funds,
        {"000300.SH": benchmark},
    )

    assert result["weights"] == pytest.approx({"A": 0.0, "B": 1.0})
    assert result["metrics"]["final_value"] == pytest.approx(100.0)
    assert result["final_positions"] == [
        {
            "fundId": "B",
            "name": "基金B",
            "manager": "经理B",
            "sleeveId": "growth",
            "quantity": pytest.approx(100.0),
            "cost_amount": pytest.approx(100.0),
            "cost_basis": pytest.approx(1.0),
            "market_price": pytest.approx(1.0),
            "market_value": pytest.approx(100.0),
            "profit": pytest.approx(0.0),
            "profit_pct": pytest.approx(0.0),
            "weight": pytest.approx(1.0),
        }
    ]


def test_cash_constrained_rebalance_scales_buys_for_transaction_fees_without_cash_warning() -> None:
    dates = pd.to_datetime(["2021-01-04", "2021-01-05"])
    funds = {
        "A": FundSeries("A", "基金A", "经理A", pd.Series([1.0, 1.0], index=dates)),
        "B": FundSeries("B", "基金B", "经理B", pd.Series([1.0, 1.0], index=dates)),
    }
    benchmark = FundSeries("000300.SH", "沪深300", "", pd.Series([1.0, 1.0], index=dates))

    result = run_fund_backtest(
        BacktestRequest(
            funds=[{"id": "A"}, {"id": "B"}],
            benchmark="000300.SH",
            start_date="2021-01-04",
            end_date="2021-01-05",
            rebalance_plan=[
                {"date": "2021-01-04", "holdings": [{"fundId": "A", "sleeveId": "defensive", "weight": 100}]},
                {"date": "2021-01-05", "holdings": [{"fundId": "B", "sleeveId": "growth", "weight": 100}]},
            ],
            initial_cash=100.0,
            fee_config={"purchase": 0.002, "redemption": 0.0065},
        ),
        funds,
        {"000300.SH": benchmark},
    )

    assert result["warnings"] == []
    assert result["final_positions"][0]["fundId"] == "B"
    assert result["final_positions"][0]["quantity"] == pytest.approx(98.95)
    assert result["metrics"]["cash_balance"] == pytest.approx(0.0038)
    assert result["metrics"]["total_fees"] == pytest.approx(1.0462)


def test_cash_constrained_backtest_records_transaction_log() -> None:
    dates = pd.to_datetime(["2021-01-04", "2021-01-05"])
    funds = {
        "A": FundSeries("A", "基金A", "经理A", pd.Series([1.0, 1.0], index=dates)),
        "B": FundSeries("B", "基金B", "经理B", pd.Series([1.0, 1.0], index=dates)),
    }
    benchmark = FundSeries("000300.SH", "沪深300", "", pd.Series([1.0, 1.0], index=dates))

    result = run_fund_backtest(
        BacktestRequest(
            funds=[{"id": "A"}, {"id": "B"}],
            benchmark="000300.SH",
            start_date="2021-01-04",
            end_date="2021-01-05",
            rebalance_plan=[
                {"date": "2021-01-04", "holdings": [{"fundId": "A", "sleeveId": "defensive", "weight": 100}]},
                {"date": "2021-01-05", "cashFlow": 10, "holdings": [{"fundId": "B", "sleeveId": "growth", "weight": 100}]},
            ],
            initial_cash=100.0,
            fee_config={"purchase": 0.002, "redemption": 0.0065},
        ),
        funds,
        {"000300.SH": benchmark},
    )

    rows = result["transactions"]
    assert [row["type"] for row in rows] == ["initial_cash", "buy", "cash_flow", "sell", "buy"]
    assert rows[0]["cash_change"] == pytest.approx(100.0)
    assert rows[1]["fundId"] == "A"
    assert rows[1]["action"] == "买入"
    assert rows[1]["quantity"] > 0
    assert rows[1]["price"] == pytest.approx(1.0)
    assert rows[1]["amount"] > 0
    assert rows[1]["fee"] > 0
    assert rows[1]["weight_change"].endswith("%")
    assert "→" in rows[1]["weight_change"]
    assert rows[2]["action"] == "资金转入"
    assert rows[2]["cash_change"] == pytest.approx(10.0)
    assert rows[2]["weight_change"] == ""
    assert rows[3]["fundId"] == "A"
    assert rows[3]["action"] == "卖出"
    assert rows[3]["fee"] > 0
    assert rows[3]["weight_change"].endswith("→0.00%")
    assert rows[4]["fundId"] == "B"
    assert rows[4]["cash_balance"] >= 0
    assert rows[4]["weight_change"].startswith("0.00%→")


def test_cash_constrained_transaction_weight_change_uses_whole_rebalance_weights() -> None:
    dates = pd.to_datetime(["2021-01-04", "2021-01-05"])
    funds = {
        "A": FundSeries("A", "基金A", "经理A", pd.Series([1.0, 2.0], index=dates)),
        "B": FundSeries("B", "基金B", "经理B", pd.Series([1.0, 1.0], index=dates)),
        "C": FundSeries("C", "基金C", "经理C", pd.Series([1.0, 1.0], index=dates)),
    }
    benchmark = FundSeries("000300.SH", "沪深300", "", pd.Series([1.0, 1.0], index=dates))

    result = run_fund_backtest(
        BacktestRequest(
            funds=[{"id": "A"}, {"id": "B"}, {"id": "C"}],
            benchmark="000300.SH",
            start_date="2021-01-04",
            end_date="2021-01-05",
            rebalance_plan=[
                {
                    "date": "2021-01-04",
                    "holdings": [
                        {"fundId": "A", "weight": 50},
                        {"fundId": "B", "weight": 50},
                    ],
                },
                {
                    "date": "2021-01-05",
                    "holdings": [
                        {"fundId": "A", "weight": 30},
                        {"fundId": "B", "weight": 50},
                        {"fundId": "C", "weight": 20},
                    ],
                },
            ],
            initial_cash=100.0,
            fee_config={"purchase": 0.0, "redemption": 0.0},
        ),
        funds,
        {"000300.SH": benchmark},
    )

    rows_by_fund = {
        row["fundId"]: row
        for row in result["transactions"]
        if row["date"] == "2021-01-05" and row["type"] in {"buy", "sell"}
    }
    assert rows_by_fund["A"]["weight_change"] == "66.67%→30.00%"
    assert rows_by_fund["B"]["weight_change"] == "33.33%→50.00%"
    assert rows_by_fund["C"]["weight_change"] == "0.00%→20.00%"


def test_cash_constrained_contribution_uses_account_weight_when_cash_remains() -> None:
    dates = pd.to_datetime(["2021-01-04", "2021-01-05"])
    funds = {
        "A": FundSeries("A", "基金A", "经理A", pd.Series([1.0, 1.1], index=dates)),
        "B": FundSeries("B", "基金B", "经理B", pd.Series([100000.0, 100000.0], index=dates)),
    }
    benchmark = FundSeries("000300.SH", "沪深300", "", pd.Series([1.0, 1.0], index=dates))

    result = run_fund_backtest(
        BacktestRequest(
            funds=[{"id": "A"}, {"id": "B"}],
            benchmark="000300.SH",
            start_date="2021-01-04",
            end_date="2021-01-05",
            rebalance_plan=[
                {
                    "date": "2021-01-04",
                    "holdings": [
                        {"fundId": "A", "weight": 50},
                        {"fundId": "B", "weight": 50},
                    ],
                }
            ],
            initial_cash=100.0,
            fee_config={"purchase": 0.0, "redemption": 0.0},
        ),
        funds,
        {"000300.SH": benchmark},
    )

    contributions = {row["id"]: row["contribution"] for row in result["fund_contributions"]}
    assert result["metrics"]["total_return"] == pytest.approx(0.05)
    assert contributions["A"] == pytest.approx(0.05)
    assert result["annual_contributions"][0]["total"] == pytest.approx(0.05)


def test_cash_analysis_window_uses_drifted_account_state_instead_of_restarting() -> None:
    dates = pd.to_datetime(["2021-01-04", "2021-01-05", "2021-01-06"])
    funds = {
        "A": FundSeries("A", "基金A", "经理A", pd.Series([1.0, 2.0, 2.0], index=dates)),
        "B": FundSeries("B", "基金B", "经理B", pd.Series([1.0, 1.0, 2.0], index=dates)),
    }
    benchmark = FundSeries("000300.SH", "沪深300", "", pd.Series([1.0, 1.0, 1.0], index=dates))
    request = BacktestRequest(
        funds=[{"id": "A"}, {"id": "B"}],
        benchmark="000300.SH",
        start_date="2021-01-04",
        end_date="2021-01-06",
        rebalance_plan=[
            {
                "date": "2021-01-04",
                "holdings": [
                    {"fundId": "A", "sleeveId": "defensive", "weight": 50},
                    {"fundId": "B", "sleeveId": "balanced", "weight": 50},
                ],
            }
        ],
        initial_cash=100.0,
        fee_config={"purchase": 0.0, "redemption": 0.0},
    )

    result = run_fund_backtest_analysis_window(
        request,
        funds,
        {"000300.SH": benchmark},
        "2021-01-05",
        "2021-01-06",
    )

    portfolio = next(row for row in result["attribution"]["rows"] if row["id"] == "portfolio")
    fund_b = next(row for row in result["attribution"]["rows"] if row["id"] == "B")
    assert result["window"] == {"start": "2021-01-05", "end": "2021-01-06", "days": 2}
    assert portfolio["period_return"] == pytest.approx(1 / 3)
    assert portfolio["return_contribution"] == pytest.approx(1 / 3)
    assert fund_b["return_contribution"] == pytest.approx(1 / 3)


def test_rebalance_plan_maps_non_trading_date_to_next_calendar_date() -> None:
    dates = pd.to_datetime(["2021-01-04", "2021-01-05", "2021-01-06"])
    funds = {
        "A": FundSeries("A", "基金A", "经理A", pd.Series([1.0, 2.0, 2.0], index=dates)),
        "B": FundSeries("B", "基金B", "经理B", pd.Series([1.0, 1.0, 2.0], index=dates)),
    }
    benchmark = FundSeries("000300.SH", "沪深300", "", pd.Series([1.0] * len(dates), index=dates))

    result = run_fund_backtest(
        BacktestRequest(
            funds=[{"id": "A"}, {"id": "B"}],
            benchmark="000300.SH",
            start_date="2021-01-04",
            end_date="2021-01-06",
            rebalance_plan=[
                {"date": "2021-01-04", "holdings": [{"fundId": "A", "weight": 100}]},
                {"date": "2021-01-02", "holdings": [{"fundId": "B", "weight": 100}]},
            ],
        ),
        funds,
        {"000300.SH": benchmark},
    )

    assert result["rebalance_dates"] == ["2021-01-04"]
    assert result["weights"] == pytest.approx({"A": 0.0, "B": 1.0})
    assert result["series"]["portfolio"][-1]["value"] == pytest.approx(2.0)


def test_rebalance_plan_rejects_invalid_weight_total_and_duplicate_fund() -> None:
    funds = {"A": FundSeries("A", "基金A", "经理A", series([1.0, 1.1, 1.2]))}
    benchmark = FundSeries("000300.SH", "沪深300", "", series([1.0, 1.0, 1.0]))

    with pytest.raises(ValueError, match="权重合计必须等于 100%"):
        run_fund_backtest(
            BacktestRequest(
                funds=[{"id": "A"}],
                benchmark="000300.SH",
                rebalance_plan=[{"date": "2021-01-04", "holdings": [{"fundId": "A", "weight": 80}]}],
            ),
            funds,
            {"000300.SH": benchmark},
        )

    with pytest.raises(ValueError, match="重复基金"):
        run_fund_backtest(
            BacktestRequest(
                funds=[{"id": "A"}],
                benchmark="000300.SH",
                rebalance_plan=[
                    {
                        "date": "2021-01-04",
                        "holdings": [
                            {"fundId": "A", "weight": 50},
                            {"fundId": "A", "weight": 50},
                        ],
                    }
                ],
            ),
            funds,
            {"000300.SH": benchmark},
        )


def test_manual_weights_must_be_positive_and_sum_to_more_than_zero() -> None:
    funds = {"A": FundSeries("A", "基金A", "经理A", series([1.0, 1.1, 1.2]))}
    benchmark = FundSeries("000300.SH", "沪深300", "", series([1.0, 1.1, 1.2]))

    with pytest.raises(ValueError, match="权重之和必须大于 0"):
        run_fund_backtest(
            BacktestRequest(
                funds=[{"id": "A", "weight": 0}],
                weight_mode="manual",
                benchmark="000300.SH",
            ),
            funds,
            {"000300.SH": benchmark},
        )


def test_missing_fund_raises_clear_error() -> None:
    with pytest.raises(ValueError, match="未找到基金"):
        run_fund_backtest(
            BacktestRequest(
                funds=[{"id": "MISSING"}],
                weight_mode="equal",
                benchmark="000300.SH",
            ),
            {},
            {"000300.SH": FundSeries("000300.SH", "沪深300", "", series([1.0, 1.1, 1.2]))},
        )
