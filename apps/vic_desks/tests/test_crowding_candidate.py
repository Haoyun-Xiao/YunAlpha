from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crowding.candidate import apply_fund_scale, make_fund_scale


def test_report_period_product_scale_excludes_partial_duplicate_and_other_quarters():
    quarter = '2026-09-30'

    def receipt(master, *shares):
        return [{'master_code': master, 'share_code': share, 'report_period': quarter,
                 'observation_id': master, 'announce_date': '2026-10-28'} for share in shares]

    def scale(code, size, period=quarter, src='eastmoney_gmbd'):
        return {'symbol': code + '.OF', 'report_period': date.fromisoformat(period),
                'scale_yi': size, 'src': src}

    result = make_fund_scale(
        {'005844.OF', '001000.OF', '002000.OF', '003000.OF'},
        receipt('005844', '005844', '017811') + receipt('001000', '001000', '001001')
        + receipt('002000', '002000') + receipt('003000', '003000'),
        [scale('005844', 35.19), scale('017811', 277.34), scale('017811', 313.28, '2026-06-30'),
         scale('001000', 5), scale('001001', 7, '2026-06-30'),
         scale('002000', 2), scale('002000', 2), scale('003000', 4, src='tushare_asset_nav')],
        report_period=quarter,
    )
    assert result['005844.OF']['net_asset_cny'] == 31_253_000_000
    assert {result[code]['status'] for code in ('001000.OF', '002000.OF', '003000.OF')} == {'incomplete_share_scale'}

    base = {'data': {'holding_period': '2026-06-30', 'funds': {'rows': [
        {'display_code': '005844.OF', 'net_asset': 35_124_000_000,
         'pool_reported_mv': 32_980_144_908, 'pool_of_nav_pct': 93.8963}
    ]}}}
    page = apply_fund_scale(base, {'fund_scale': {'005844.OF': result['005844.OF']},
                                 'holding_period': '2026-06-30', 'report_period': quarter,
                                 'coverage': {'observed_non_pit': 1}})
    row = page['data']['funds']['rows'][0]
    assert row['net_asset'] == 35_124_000_000
    assert row['pool_of_nav_pct'] == 93.8963
    assert 'later_scale' not in base['data']['funds']['rows'][0]
