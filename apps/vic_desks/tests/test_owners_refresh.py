"""Local integration: read the immutable production sample, build only under tmp_path."""
import hashlib
import math
import os
from datetime import date
from pathlib import Path

import duckdb
import pytest

from seven_desks.adapters.owners import BOARDS, panel
from seven_desks.daily import GROUPS
from seven_desks.daily_inputs import DailyInputs, market_money
from seven_desks.owners_refresh import Build, PREFIX, refresh

DAY = date(2026, 11, 16)
FROZEN = date(2026, 11, 9)


def test_refresh_replaces_unclassified_group_and_preserves_other_dates(tmp_path):
    base, stage = tmp_path/'base', tmp_path/'stage'
    rel = PREFIX + 'local_sw_flow.parquet'
    (base/rel).parent.mkdir(parents=True)
    x = DailyInputs(tmp_path, DAY, stage)
    x.days = [DAY]
    try:
        x.db.execute("""COPY (SELECT * FROM (VALUES
            (DATE '2026-11-13', NULL::VARCHAR, 100), (DATE '2026-11-16', NULL, 200),
            (DATE '2026-11-16', '801010', 300)) AS t(trade_date,industry_code,net_flow))
            TO ? (FORMAT PARQUET)""",[str(base/rel)])
        build = Build(x, base)
        assert build.emit('local_sw_flow',
            "SELECT DATE '2026-11-16' trade_date, NULL::VARCHAR industry_code, 201 net_flow",
            ('trade_date','industry_code')) == str(DAY)
        assert x.db.execute("""SELECT * FROM read_parquet(?)
            ORDER BY trade_date,industry_code NULLS LAST""",[str(stage/rel)]).fetchall() == [
            (date(2026,11,13), None, 100), (DAY, '801010', 300), (DAY, None, 201)]
    finally:
        x.db.close()


@pytest.fixture(scope='module')
def real(tmp_path_factory):
    settings = ('OWNERS_TEST_LAKE', 'OWNERS_TEST_BASE_DATA')
    if not all(os.getenv(k) for k in settings):
        pytest.skip('set OWNERS_TEST_LAKE and OWNERS_TEST_BASE_DATA for the 2026-11-16 read-only integration')
    lake, base = map(lambda key: Path(os.environ[key]), settings)

    def build(destination):
        x = DailyInputs(lake, DAY, destination)
        try:
            assert x.calendar() == DAY
            market_money(x, FROZEN)
            result = refresh(x, base)
            x.verify()
        finally:
            x.db.close()
        return result

    stage, repeat = (tmp_path_factory.mktemp(k) for k in ('owners_sample', 'owners_repeat'))
    first, second = build(stage), build(repeat)
    assert first == second
    assert first['outputs']
    for rel in first['outputs']:
        assert hashlib.sha256((stage/rel).read_bytes()).digest() == hashlib.sha256((repeat/rel).read_bytes()).digest(), rel
    return lake, base, stage, first


def formal_prices(db, lake):
    """Read actual published prices and hfq factors, independently of refresh outputs."""
    for name, relative in (
        ('formal_bars', 'curated/daily_bars'),
        ('formal_factors', 'derived/adj_factors'),
    ):
        db.read_parquet(str(lake/relative/'trade_date=2026-1[01]*/*.parquet'),
                        hive_partitioning=False, union_by_name=True).create_view(name)
    db.execute("""CREATE TEMP VIEW formal_prices AS
        WITH bars AS (
            SELECT * FROM formal_bars WHERE isfinite(close) AND close>0
            QUALIFY row_number() OVER(PARTITION BY symbol,trade_date
                ORDER BY fetched_at DESC NULLS LAST,source,data_version,close,amount)=1
        ), factors AS (
            SELECT * FROM formal_factors WHERE adjust_type='hfq' AND isfinite(factor) AND factor>0
            QUALIFY row_number() OVER(PARTITION BY symbol,trade_date
                ORDER BY fetched_at DESC NULLS LAST,source,data_version,factor)=1
        )
        SELECT b.symbol,b.trade_date,b.close,b.amount,b.close*f.factor adjusted_close
        FROM bars b LEFT JOIN factors f USING(symbol,trade_date)""")
    db.read_parquet(str(lake/'curated/trading_calendar/**/*.parquet'),
                    hive_partitioning=False, union_by_name=True).create_view('formal_calendar')


def test_original_financial_and_event_formulas(real):
    lake, base, stage, result = real
    with duckdb.connect() as db:
        def table(alias, file):
            db.read_parquet(str(file), hive_partitioning=False, union_by_name=True).create_view(alias)
        table('market', stage/PREFIX/'local_market_stock_flow_windows.parquet')
        table('stock', stage/PREFIX/'stock_fund_flow_windows.parquet')
        table('historic', base/'revision/owners/market_stock_flow_windows.parquet')
        table('flow', str(lake/'curated/fund_flow/trade_date=2026-11*/*.parquet'))
        table('security', stage/PREFIX/'margin_security_flow.parquet')
        table('border', stage/PREFIX/'cross_border_daily.parquet')
        table('events', stage/PREFIX/'dragon_tiger_events.parquet')
        table('balance', stage/PREFIX/'capital_balance_daily.parquet')
        values = db.execute("SELECT sample_count, main_net, main_net_5d,main_net_20d,main_net_60d,main_net_100d FROM market WHERE trade_date=?", [DAY]).fetchone()
        sample, total = db.execute("SELECT count(*),sum(main_net_inflow) FROM flow WHERE trade_date=? AND source='tushare.moneyflow_dc'", [DAY]).fetchone()
        assert values[:2] == pytest.approx((sample, total))
        for n, actual in zip((5,20,60,100), values[2:]):
            rows = db.execute("""SELECT main_net FROM (
                SELECT trade_date,main_net FROM historic UNION ALL SELECT trade_date,main_net FROM market
                ) ORDER BY trade_date DESC LIMIT ?""", [n]).fetchall()
            assert actual == pytest.approx(sum(row[0] for row in rows))
        row = db.execute("""SELECT flow_100d,flow_100d_valid_days,turnover,return_20d
           FROM stock WHERE symbol='600519.SH' AND trade_date=?""", [DAY]).fetchone()
        assert row[0] is not None and row[1] == 100 and row[2] > 0
        formal_prices(db, lake)
        # The controlled lake repair restored real prices, not synthetic zero returns.
        amount, adjusted_return = db.execute("""
          SELECT p.amount,p.adjusted_close/prev.adjusted_close-1
          FROM formal_prices p JOIN formal_prices prev USING(symbol)
          WHERE p.symbol='600519.SH' AND p.trade_date=DATE '2026-11-12'
            AND prev.trade_date=DATE '2026-11-11'""").fetchone()
        assert amount == pytest.approx(4536814518.0)
        restored = db.execute("""SELECT turnover,daily_return FROM stock
          WHERE symbol='600519.SH' AND trade_date=DATE '2026-11-12'""").fetchone()
        assert restored == pytest.approx((amount,adjusted_return))

        # 20 returns require 21 actual consecutive official trading closes.
        prices = db.execute("""
          WITH sessions AS (
            SELECT DISTINCT trade_date FROM formal_calendar WHERE is_trading AND trade_date<=?
            ORDER BY trade_date DESC LIMIT 21
          )
          SELECT s.trade_date,p.adjusted_close FROM sessions s LEFT JOIN formal_prices p
            ON p.symbol='600519.SH' AND p.trade_date=s.trade_date ORDER BY s.trade_date
          """, [DAY]).fetchall()
        assert len(prices) == 21 and all(value is not None and value>0 for _,value in prices)
        expected_20d = math.prod(prices[i][1]/prices[i-1][1] for i in range(1,21))-1
        assert expected_20d == pytest.approx(prices[-1][1]/prices[0][1]-1)
        assert row[3] == pytest.approx(expected_20d)
        assert db.execute("""SELECT return_20d FROM stock
          WHERE symbol='600519.SH' AND trade_date=DATE '2026-11-11'""").fetchone()[0] is not None
        assert db.execute("SELECT count(*) FROM security WHERE margin_repay IS NULL AND reported_financing_net_buy IS NOT NULL").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM security WHERE balance_change IS NOT NULL").fetchone()[0] > 0
        assert db.execute("""SELECT sum(turnover),count(net_amount) FROM border
           WHERE trade_date=? AND channel IN('north_sh','north_sz')""", [FROZEN]).fetchone() == pytest.approx((216236400000,0))
        assert db.execute("SELECT count(*) FROM events WHERE trade_date=? AND next_trading_day_return IS NOT NULL", [DAY]).fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM events WHERE amount_variants>1 AND net_amount IS NOT NULL").fetchone()[0] == 0
        assert db.execute('SELECT count(strict_net_balance),count(ipo_funding) FROM balance').fetchone() == (0,0)
    assert result['boards']['capital_flow']['margin_exchange']['data_date'] == str(FROZEN)
    assert result['boards']['capital_flow']['margin_securities']['exchange_dates']['SZ'] == '2026-11-12'


def test_shadow_and_nav_compare_to_inputs(real):
    lake, base, stage, result = real
    with duckdb.connect() as db:
        for name, path in {
            'components':stage/PREFIX/'national_shadow_component_daily.parquet',
            'shadow':stage/PREFIX/'national_shadow_nav.parquet',
            'prior':base/'owners/national_shadow_nav.parquet',
            'rebalance':base/'owners/national_shadow_rebalance_weights.parquet',
            'weights':stage/PREFIX/'fund_return_weight_evidence.parquet',
            'nav':stage/PREFIX/'company_nav_daily.parquet',
        }.items():
            db.read_parquet(str(path)).create_view(name)
        close, day_return, nav = db.execute("""SELECT segment_value,daily_return,nav FROM shadow
          WHERE portfolio='narrow' AND trade_date=DATE '2026-11-10'""").fetchone()
        anchor, previous_nav = db.execute("SELECT segment_value,nav FROM prior WHERE portfolio='narrow' AND trade_date=?", [FROZEN]).fetchone()
        expected = db.execute("""SELECT sum(weight*adjusted_close/rebalance_adjusted_close) FROM components
          WHERE portfolio='narrow' AND trade_date=DATE '2026-11-10'""").fetchone()[0]
        assert close == pytest.approx(expected)
        assert day_return == pytest.approx(expected/anchor-1)
        assert nav == pytest.approx(previous_nav*expected/anchor)
        formal_prices(db, lake)
        # Independent mark-to-market of the verified Nov 2 weights. Recompute
        # Nov 11/12/13/16 against official closes; compare component values,
        # next-day returns and anchored NAV after restoring the missing date.
        totals = db.execute("""
          SELECT w.portfolio,p.trade_date,sum(w.weight*p.adjusted_close/w.rebalance_adjusted_close) total,
            sum(w.weight) fresh,count(*) component_count
          FROM rebalance w JOIN formal_prices p USING(symbol)
          WHERE w.rebalance_date=DATE '2026-11-02' AND w.portfolio='narrow'
            AND w.weight IS NOT NULL
            AND p.trade_date IN(DATE '2026-11-11',DATE '2026-11-12',DATE '2026-11-13',DATE '2026-11-16')
          GROUP BY 1,2 ORDER BY 2""").fetchall()
        assert len(totals) == 4 and all(n == 35 for _,_,_,_,n in totals)
        expected_totals = {day:total for _,day,total,_,_ in totals}
        for portfolio, day, expected_total, expected_fresh, _ in totals:
            actual = db.execute("""SELECT segment_value,daily_return,nav,fresh_price_weight
                FROM shadow WHERE portfolio=? AND trade_date=?""", [portfolio,day]).fetchone()
            assert actual[0] == pytest.approx(expected_total)
            assert actual[3] == pytest.approx(expected_fresh)
            assert actual[2] == pytest.approx(previous_nav*expected_total/anchor)
            if day == date(2026,11,12):
                assert actual[1] == pytest.approx(expected_total/expected_totals[date(2026,11,11)]-1)
                assert abs(actual[1]) > 1e-6
            elif day == date(2026,11,13):
                assert actual[1] == pytest.approx(expected_total/expected_totals[date(2026,11,12)]-1)
        assert db.execute("""SELECT count(*) FROM components c JOIN formal_prices p USING(symbol,trade_date)
            WHERE c.portfolio='narrow' AND c.trade_date=DATE '2026-11-12'
              AND (c.valuation_price_date<>c.trade_date OR c.adjusted_close<>p.adjusted_close)""").fetchone()[0] == 0
        assert result['boards']['national_team']['shadow_portfolio']['days_without_fresh_prices'] == []
        assert db.execute('SELECT count(*) FROM weights WHERE scale_available_date>prev_date').fetchone()[0] == 0
        name = '易方达基金管理有限公司'
        equal, scale = db.execute("""SELECT equal_weight_return,scale_weight_return FROM nav
            WHERE company_id=? AND trade_date=DATE '2026-11-13'""", [name]).fetchone()
        observed = db.execute("""SELECT adjusted_nav/previous_adjusted_nav-1,previous_public_asset_nav
          FROM weights WHERE company_id=? AND nav_date=DATE '2026-11-13'
            AND adjusted_nav>0 AND previous_adjusted_nav>0""", [name]).fetchall()
        assert equal == pytest.approx(sum(r for r,_ in observed)/len(observed))
        weighted = [(r,a) for r,a in observed if a is not None]
        assert scale == pytest.approx(sum(r*a for r,a in weighted)/sum(a for _,a in weighted))


def test_serving_all_owners_tabs_and_etf_visibility(real, monkeypatch):
    _, base, stage, result = real

    class Store:
        daily_boards = result['boards']

        def path(self, rel):
            return str((stage if (stage/rel).is_file() else base)/rel)

    # Exercise the integration's new selector keys without editing daily.py.
    monkeypatch.setitem(GROUPS, 'national_team', ('etf','shadow_portfolio'))
    monkeypatch.setitem(GROUPS, 'fund_holdings', ('company_nav',))
    store = Store()
    for board, (_, tabs) in BOARDS.items():
        for tab, _ in tabs:
            response = panel(board, tab, {'code':'600519.SH', 'corp':'易方达基金管理有限公司'}, store)
            assert response['sections'] and all(s['id']!='version_input_missing' for s in response['sections']), (board,tab)

    def section(board, tab, name, params):
        return next(s for s in panel(board,tab,params,store)['sections'] if s['id']==name)

    etf = section('national_team', 'etf', 'etf_kpi', {'date':'2026-11-13'})
    assert etf['rows'][0]['trade_date'] == '2026-11-12'
    etf = section('national_team', 'etf', 'etf_kpi', {'date':'2026-11-16'})
    assert etf['rows'][0]['trade_date'] == '2026-11-13'
    shadow = section('national_team', 'portfolio', 'portfolio_kpi', {'date':'2026-11-13'})
    assert {r['end_date'] for r in shadow['rows']} == {'2026-11-13'}
    corp = section('fund_holdings','corp','company_returns',{'corp':'易方达基金管理有限公司'})
    assert {r['last_date'] for r in corp['rows']} == {'2026-11-13'}

    params = {'date':'2026-11-16'}
    cards = section('capital_flow','overview','overview_summary',params)['rows']
    assert (cards[0]['label'], cards[0]['trade_date'], cards[0]['value']) == (
        '东财大盘主力净额', '2026-11-16', -64_379_101_440)
    assert cards[1]['value'] == -36_582_826_240
    trend = section('capital_flow','overview','overview_main_trend',params)['rows']
    assert trend[-1]['main_net'] == cards[0]['value']
    sample = section('capital_flow','overview','main_money_flow_daily',params)['rows'][0]
    assert (sample['main_net'], sample['sample_count']) == (5_721_933_200, 5185)
    missing = section('capital_flow','overview','overview_summary',{'date':'2026-11-15'})['rows'][0]
    assert missing['value'] is None and missing['missing_reason']
