"""Explicitly gated same-sample top-ten holding changes.

Products supplied to the pure calculator must be verified and active.  This
module never infers product identity from names or treats missing reports as
positions of zero.  The lake adapter only proceeds when both quarters have
published relation evidence; unfinished coverage is returned as a gap.
"""
from __future__ import annotations
from collections import defaultdict
from datetime import date, datetime, time, timezone
from math import isfinite
from pathlib import Path

from crowding.lake import DEFAULT_LAKE_ROOT
from crowding.resources import connect
from crowding.verified_holdings import _read_published_relations, verified_product_mapping
from crowding.fund_classification import classify_fund_profiles


def denominator_bounds(verified_products, unverified_shares, *, uncertain_shares=None, disclosure_complete=False):
    """Bound observed denominator, without assuming unverified share identities.

    Each verified product and each unknown share maps to {stock: shares}; the
    input sets must be disjoint and their per-stock rows already deduplicated.
    A share may duplicate another unknown share or a verified product. The
    safe observed lower bound is max(sum verified, largest unknown), and the
    upper bound is sum verified + sum unknown. These bound *observations* only:
    an incomplete published universe leaves the true upper bound unknown.
    """
    values=defaultdict(lambda:{'verified':0.,'unknown_sum':0.,'unknown_max':0.,'unknown_count':0})
    for data,known in ((verified_products,True),(unverified_shares,False)):
        for positions in data.values():
            for symbol,shares in positions.items():
                if isinstance(shares,bool) or not isinstance(shares,(int,float)) or not isfinite(shares) or shares<0:
                    raise ValueError('invalid denominator shares')
                v=values[symbol]
                if known:v['verified']+=shares
                else:
                    v['unknown_sum']+=shares;v['unknown_max']=max(v['unknown_max'],shares);v['unknown_count']+=1
    # Unclassified fund shares may be inactive and therefore can increase the
    # conservative upper bound only, never the active-fund lower bound.
    for positions in (uncertain_shares or {}).values():
        for symbol,shares in positions.items():
            if isinstance(shares,bool) or not isinstance(shares,(int,float)) or not isfinite(shares) or shares<0:
                raise ValueError('invalid denominator shares')
            values[symbol]['unknown_sum']+=shares
            values[symbol]['unknown_count']+=1
    return {symbol:{'observed_lower':max(x['verified'],x['unknown_max']),
                    'observed_upper':x['verified']+x['unknown_sum'],
                    'true_upper':x['verified']+x['unknown_sum'] if disclosure_complete else None,
                    'verified_product_shares':x['verified'],'unverified_share_sum':x['unknown_sum'],
                    'possibly_duplicate_shares':x['unknown_count'],
                    'disclosure_complete':disclosure_complete} for symbol,x in values.items()}


def coverage_from_bounds(numerator, bounds, threshold=.8):
    """Classify a threshold only if the true denominator can be bounded."""
    if isinstance(numerator,bool) or not isinstance(numerator,(int,float)) or not isfinite(numerator) or numerator<0:
        raise ValueError('invalid numerator')
    if not bounds or bounds['observed_lower']<=0:
        return {'minimum':None,'maximum':None,'threshold_state':'unknown'}
    lower=bounds['observed_lower']; upper=bounds.get('true_upper')
    if numerator>lower+1e-9:
        # A supposedly verified numerator larger than an observed strict lower
        # bound merely tightens the lower bound; numerator is itself evidence.
        lower=numerator
    maximum=numerator/lower
    minimum=numerator/upper if upper and upper>0 else None
    state=('definitely_below' if maximum<threshold else
           'definitely_above' if minimum is not None and minimum>=threshold else
           'ambiguous' if minimum is not None else 'unknown')
    return {'minimum':minimum,'maximum':maximum,'threshold_state':state}

def calculate_top10_changes(current, previous, full_holdings, prices, *, denominator_complete=False,
                            coverage_min=.8, prior_fund_min=5, split_multipliers=None,
                            valuation_multipliers=None, unavailable_symbols=None):
    """Calculate with {verified_product: {stock: shares}} for each period.

    Presence of a product in both top-ten dictionaries means both reports are
    published and contain valid input. Missing stock inside a present report
    means it entered/exited the *top ten*, not that it was traded to/from zero.
    ``full_holdings`` represents the full active-product denominator and is
    trusted only when its caller can prove coverage/identity completeness.
    """
    same = set(current) & set(previous)
    statistics = defaultdict(lambda: {'current': 0., 'previous': 0., 'prior_products': 0, 'new_in': 0, 'new_out': 0, 'products': []})
    for product in same:
        now, old = current[product], previous[product]
        for symbol in set(now) | set(old):
            a, b = now.get(symbol, 0), old.get(symbol, 0)
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not isfinite(v) or v < 0 for v in (a, b)):
                raise ValueError('invalid top-ten holding shares')
            statistics[symbol]['current'] += a
            statistics[symbol]['previous'] += b
            if symbol in old: statistics[symbol]['prior_products'] += 1
            if symbol in now and symbol not in old: statistics[symbol]['new_in'] += 1
            if symbol in old and symbol not in now: statistics[symbol]['new_out'] += 1
            statistics[symbol]['products'].append({'product': product, 'current_shares': a, 'previous_shares': b,
                'state': 'new_in' if symbol not in old else 'new_out' if symbol not in now else 'retained'})
    full = defaultdict(float)
    for positions in full_holdings.values():
        for symbol, shares in positions.items():
            if isinstance(shares, bool) or not isinstance(shares, (int, float)) or not isfinite(shares) or shares < 0:
                raise ValueError('invalid full holding shares')
            full[symbol] += shares
    result = []
    for symbol, item in statistics.items():
        base = full.get(symbol)
        # The full disclosed denominator is the earlier holding period, so
        # its same-sample numerator must be the earlier top-ten positions.
        # Also return the cross-period ratio as a diagnostic only: the source
        # wording does not independently resolve this ambiguity.
        coverage = item['previous'] / base if base and base > 0 else None
        current_over_prior_denominator = item['current'] / base if base and base > 0 else None
        multiplier = (split_multipliers or {}).get(symbol,1.)
        valuation_multiplier = (valuation_multipliers or {}).get(symbol,1.)
        for value in (multiplier,valuation_multiplier):
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not isfinite(value) or value<=0:
                raise ValueError('invalid split multiplier')
        previous_adjusted = item['previous'] * multiplier
        comparable = symbol not in (unavailable_symbols or {})
        delta = item['current'] - previous_adjusted if comparable else None
        price = prices.get(symbol)
        if price is not None and (isinstance(price, bool) or not isinstance(price, (int, float)) or not isfinite(price) or price <= 0):
            price = None
        reasons = []
        if not denominator_complete: reasons.append('active_full_denominator_not_verified')
        if not comparable: reasons.append((unavailable_symbols or {})[symbol])
        if coverage is None: reasons.append('full_holding_missing')
        elif coverage < coverage_min: reasons.append('coverage_below_80_percent')
        elif coverage > 1 + 1e-8: reasons.append('numerator_exceeds_full_denominator')
        if item['prior_products'] < prior_fund_min: reasons.append('prior_holders_below_5')
        if price is None: reasons.append('market_price_missing')
        result.append({'symbol': symbol, 'current_shares': item['current'], 'previous_shares': item['previous'],
                       'previous_shares_adjusted': previous_adjusted if comparable else None,
                       'split_multiplier': multiplier if comparable else None, 'valuation_multiplier': valuation_multiplier if comparable else None,
                       'delta_shares': delta, 'delta_pct': delta / previous_adjusted if delta is not None and previous_adjusted > 0 else None,
                       'delta_value': delta * valuation_multiplier * price if delta is not None and price is not None else None, 'coverage': coverage,
                       'new_in': item['new_in'], 'new_out': item['new_out'], 'participating_products': item['products'],
                       'coverage_previous': coverage, 'coverage_current_over_previous': current_over_prior_denominator,
                       'prior_products': item['prior_products'], 'eligible_for_rank': not reasons,
                       'quality_flags': reasons})
    result.sort(key=lambda r: (not r['eligible_for_rank'], -(r['delta_value'] or 0), r['symbol']))
    return {'rows': result, 'metadata': {'same_sample_products': len(same),
            'current_only_excluded': len(set(current)-same), 'previous_only_excluded': len(set(previous)-same),
            'coverage_denominator_complete': denominator_complete,
            'coverage_basis': '上一持仓期同样本前十大 / 上一持仓期全部持股（同口径本地实现；原站分子时点未完全证实）',
            'note': '前十大进出为榜单口径，不代表实际买卖；股数按已发布拆送股事件校正，变化金额折算为估值日股数。'}}


def lake_top10_changes(*, as_of: date, lake_root: Path = DEFAULT_LAKE_ROOT):
    """Compare exact period product identities; expose every failed gate."""
    from crowding.product_view import load_product_view
    root = Path(lake_root).expanduser().resolve()
    holdings = sorted(str(p) for p in (root/'curated/fund_holdings').rglob('*.parquet'))
    if not holdings:
        return {'rows': [], 'metadata': {'reason': 'fund_holdings_missing'}}
    db = connect()
    try:
        periods = [r[0] for r in db.execute("""SELECT DISTINCT TRY_CAST(report_period AS DATE)
            FROM read_parquet(?,hive_partitioning=false,union_by_name=true)
            WHERE TRY_CAST(report_period AS DATE)<=? AND announce_date IS NOT NULL AND announce_date<=?
            ORDER BY 1 DESC LIMIT 2""", [holdings,as_of,as_of]).fetchall()]
        if len(periods)<2:
            return {'rows': [], 'metadata': {'reason': 'insufficient_disclosed_quarters'}}
        current_period, previous_period = periods
        classification = classify_fund_profiles(lake_root=root)
        views = {p: load_product_view(as_of=as_of,report_period=p,lake_root=root,
                    classification=classification,share_to_master={},conn=db) for p in periods}
        groups = {}
        for period, view in views.items():
            portfolios = defaultdict(list)
            exact = {key for key,value in view['products'].items()
                     if value['is_active'] and value['relation_status']=='exact'}
            for row in view['rows']:
                if row['product_symbol'] in exact:
                    portfolios[row['product_symbol']].append(row)
            groups[period] = {product: {r['holding_symbol']:float(r['shares']) for r in sorted(rows,key=lambda r:(-r['market_value'],r['holding_symbol']))[:10]}
                for product,rows in portfolios.items()
                if all(isinstance(r.get('shares'),(int,float)) and not isinstance(r['shares'],bool)
                       and isfinite(r['shares']) and r['shares']>=0 for r in rows)}
        full = defaultdict(dict)
        for row in views[previous_period]['rows']:
            shares = row.get('shares')
            if isinstance(shares,(int,float)) and not isinstance(shares,bool) and isfinite(shares) and shares>=0:
                full[row['product_symbol']][row['holding_symbol']] = float(shares)
        prior_coverage = views[previous_period]['coverage']
        denominator_reasons = []
        if any(not isinstance(r.get('shares'),(int,float)) or isinstance(r.get('shares'),bool)
               or not isfinite(r['shares']) or r['shares']<0 for r in views[previous_period]['rows']):
            denominator_reasons.append('invalid_or_missing_full_holding_shares')
        if previous_period.month not in (6,12):
            denominator_reasons.append('previous_period_not_full_disclosure_quarter')
        if prior_coverage.get('unmerged_active_shares',0):
            denominator_reasons.append('unmerged_active_share_identities')
        if prior_coverage.get('approx_active_products',0):
            denominator_reasons.append('approximate_active_product_relations')
        if prior_coverage.get('excluded_conflict_groups',0):
            denominator_reasons.append('conflicting_product_portfolios')
        if classification.get('unknown_count',0):
            denominator_reasons.append('unclassified_fund_universe')
        ledger_path = root/'meta/quality/fund_coverage_ledger.parquet'
        active = set(classification.get('active_symbols',[]))
        published = set()
        unresolved = []
        if ledger_path.exists():
            ledger = db.execute('SELECT symbol,holdings_status FROM read_parquet(?) WHERE report_period=?', [str(ledger_path),previous_period]).fetchall()
            published = {symbol for symbol,status in ledger if status=='published'}
            unresolved = sorted(active-published)
        else:
            denominator_reasons.append('disclosure_coverage_ledger_missing')
        if unresolved:
            denominator_reasons.append('active_funds_missing_verified_full_disclosure')
        denominator_complete = not denominator_reasons and bool(active)
        bars = sorted(str(p) for p in (root/'curated/daily_bars').rglob('*.parquet'))
        prices, market_date = {}, None
        if bars:
            market_date = db.execute('SELECT MAX(trade_date) FROM read_parquet(?,hive_partitioning=false,union_by_name=true) WHERE trade_date<=?', [bars,as_of]).fetchone()[0]
            if market_date:
                prices = dict(db.execute("""SELECT symbol,close FROM read_parquet(?,hive_partitioning=false,union_by_name=true)
                    WHERE trade_date=? AND close>0 QUALIFY ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY fetched_at DESC NULLS LAST)=1""", [bars,market_date]).fetchall())
    finally:
        db.close()
    split = split_adjustments(root,previous_period,current_period,as_of)
    valuation = split_adjustments(root,current_period,market_date or current_period,as_of)
    bad = {**split['unavailable_symbols'],**valuation['unavailable_symbols']}
    symbols = {symbol for group in groups.values() for rows in group.values() for symbol in rows}
    if not split['available'] or not valuation['available']:
        bad.update({symbol:'corporate_actions_missing' for symbol in symbols})
    result = calculate_top10_changes(groups[current_period],groups[previous_period],full,prices,
        denominator_complete=denominator_complete, split_multipliers=split['multipliers'],
        valuation_multipliers=valuation['multipliers'],unavailable_symbols=bad)
    known, unknown, uncertain = defaultdict(dict), defaultdict(dict), defaultdict(dict)
    for item in views[previous_period]['all_rows']:
        symbol, product, shares = item['holding_symbol'], item['product_symbol'], item.get('shares')
        if not isinstance(shares,(int,float)) or isinstance(shares,bool) or not isfinite(shares) or shares<0:
            continue
        meta = views[previous_period]['products'][product]
        if meta['is_active']:
            target = known if meta['relation_status']=='exact' else unknown
        elif meta['category']=='unknown':
            target = uncertain
        else:
            continue
        target[product][symbol] = float(shares)
    diagnostic_bounds = denominator_bounds(known,unknown,uncertain_shares=uncertain,disclosure_complete=denominator_complete)
    for row in result['rows']:
        bounds = diagnostic_bounds.get(row['symbol']) if previous_period.month in (6,12) else None
        row['denominator_bounds'] = bounds
        row['coverage_previous_bounds'] = coverage_from_bounds(row['previous_shares'],bounds)
        row['price_date'] = market_date.isoformat() if market_date else None
        row['quality_flags'].extend(denominator_reasons)
    result['metadata'].update(current_period=current_period.isoformat(),previous_period=previous_period.isoformat(),
        market_date=market_date.isoformat() if market_date else None,
        denominator_period=previous_period.isoformat(),denominator_complete=denominator_complete,
        denominator_quality_flags=denominator_reasons,unresolved_active_disclosure_count=len(unresolved),
        unresolved_active_disclosure_sample=unresolved[:30],
        product_view_coverage={p.isoformat():v['coverage'] for p,v in views.items()},
        corporate_actions={'comparison':split['coverage'],'valuation':valuation['coverage']},
        eligible_rank_rows=sum(r['eligible_for_rank'] for r in result['rows']),
        reason='quality_gates_evaluated_per_stock',
        scope='两期均为当期精确关系的主动权益及指数增强产品；缺报告不计退出；当前分类回看，非PIT')
    return result


def lake_full_denominator_diagnostic(*, as_of: date, lake_root: Path = DEFAULT_LAKE_ROOT):
    """Diagnostic bounds for latest disclosed Q2/Q4 stock holdings.

    Current-only active classification is non-PIT. Unclassified funds are
    included as unknown observations, while verified inactive funds are omitted.
    Completeness is deliberately false: filings and the product map are partial.
    """
    root=Path(lake_root).expanduser().resolve()
    paths=sorted(str(p) for p in (root/'curated/fund_holdings').rglob('*.parquet'))
    if not paths:return {'rows':{},'metadata':{'reason':'fund_holdings_missing'}}
    db=connect()
    try:
        period=db.execute('''SELECT MAX(report_period) FROM read_parquet(?,hive_partitioning=false,union_by_name=true)
          WHERE report_period<=? AND month(report_period) IN (6,12) AND announce_date IS NOT NULL AND announce_date<=?''',[paths,as_of,as_of]).fetchone()[0]
        if not period:return {'rows':{},'metadata':{'reason':'full_period_missing'}}
        rows=db.execute('''SELECT regexp_replace(fund_symbol,'\\..*$',''),holding_symbol,shares,market_value,announce_date
          FROM read_parquet(?,hive_partitioning=false,union_by_name=true)
          WHERE report_period=? AND announce_date IS NOT NULL AND announce_date<=?
           AND holding_symbol ~ '^[0-9]{6}\\.(SH|SZ|BJ)$'
          QUALIFY ROW_NUMBER() OVER(PARTITION BY fund_symbol,holding_symbol,report_period ORDER BY announce_date DESC NULLS LAST,fetched_at DESC NULLS LAST)=1''',[paths,period,as_of]).fetchall()
    finally:db.close()
    classification=classify_fund_profiles(lake_root=root)
    active={x.split('.')[0] for x in classification.get('active_symbols',[])}
    # Unknown classification is deliberately retained in the conservative
    # upper observation, because excluding it would understate uncertainty.
    inactive={r['symbol'].split('.')[0] for r in classification.get('rows',[]) if r.get('symbol') and r.get('is_active_for_crowding') is False}
    label=f'{period.year}Q{(period.month-1)//3+1}'
    inputs=[{'fund_symbol':f,'holding_symbol':s,'shares':shares,'market_value':mv,
             'announce_date':ann.isoformat(),'report_period':label} for f,s,shares,mv,ann in rows if f not in inactive]
    verified=verified_product_mapping(inputs,lake_root=root,report_period=label,announcement_cutoff=as_of.isoformat(),knowledge_cutoff=datetime.combine(as_of,time.max,tzinfo=timezone.utc))
    mapping=verified['share_to_master'];known,unknown,uncertain=defaultdict(dict),defaultdict(dict),defaultdict(dict);invalid=0
    group_codes=defaultdict(set)
    for code,master in mapping.items():group_codes[master].add(code)
    for r in inputs:
        f,s,shares=r['fund_symbol'],r['holding_symbol'],r['shares']
        if isinstance(shares,bool) or not isinstance(shares,(int,float)) or not isfinite(shares) or shares<0:
            invalid+=1;continue
        master=mapping.get(f)
        # A verified product is lower-bound eligible only if every associated
        # class is currently classified active. Otherwise treat each observed
        # share as uncertain, avoiding inactive/unknown product overstatement.
        codes=group_codes.get(master,set())
        if master and all(c in active for c in codes):known[master][s]=shares
        elif f in active:unknown[f][s]=shares
        else:uncertain[f][s]=shares
    bounds=denominator_bounds(known,unknown,uncertain_shares=uncertain,disclosure_complete=False)
    return {'rows':bounds,'metadata':{'full_holding_period':period.isoformat(),'as_of':as_of.isoformat(),
      'verified_active_products':len(known),'unverified_active_shares':len(unknown),'unclassified_shares':len(uncertain),'symbols':len(bounds),'invalid_rows':invalid,
      'classification_scope':'当前基金分类回看，未知分类保留为待核验；非PIT',
      'disclosure_complete':False,'relation_status':verified.get('relation_status'),
      'warning':'仅约束已观察持仓，未知未披露基金使真实分母上限不可知；拆送股和行情尚未核验，不能生成排名'}}


def split_adjustments(lake_root, start, end, knowledge_date=None):
    """Published mechanical share multipliers for ``start < ex_date <= end``.

    Return multipliers only for observed actions. Absence means 1 only when
    ``available`` is true; unavailable_symbols must always be excluded. Cash
    dividends do not change shares and adjustment factors are never used as a
    split proxy. Lake ratios are per held share. Rows sharing an ex-date are
    checked across action types so a combined bonus/transfer is applied once.
    """
    paths = sorted(str(p) for p in (Path(lake_root)/'curated/corporate_actions').rglob('*.parquet'))
    if not paths:
        return {'multipliers': {}, 'unavailable_symbols': {}, 'available': False,
                'coverage': {'reason': 'corporate_actions_missing'}}
    db = connect()
    try:
        records = db.execute('''SELECT symbol,ex_date,action_type,bonus_ratio,transfer_ratio,allotment_ratio
            FROM read_parquet(?,hive_partitioning=false,union_by_name=true)
            WHERE ex_date>? AND ex_date<=? AND ex_date<=?
            QUALIFY ROW_NUMBER() OVER(PARTITION BY symbol,ex_date,action_type
              ORDER BY fetched_at DESC NULLS LAST)=1''', [paths,start,end,knowledge_date or end]).fetchall()
    finally:
        db.close()
    events = defaultdict(list)
    for symbol,day,kind,bonus,transfer,allotment in records:
        events[(symbol,day)].append((kind,bonus,transfer,allotment))
    multipliers, bad = {}, {}
    for (symbol,day), rows in events.items():
        if any(kind not in ('cash_dividend','bonus','transfer','split','allotment') for kind,*_ in rows):
            bad[symbol] = 'unsupported_corporate_action'; continue
        if any(allotment is not None and allotment != 0 for _,_,_,allotment in rows):
            bad[symbol] = 'allotment_participation_not_observed'; continue
        if any(not isinstance(x,(int,float)) or isinstance(x,bool) or not isfinite(x) or x < 0
               for _,bonus,transfer,_ in rows for x in (bonus,transfer)):
            bad[symbol] = 'invalid_or_missing_share_ratio'; continue
        bonuses = {float(bonus) for _,bonus,_,_ in rows if bonus != 0}
        transfers = {float(transfer) for _,_,transfer,_ in rows if transfer != 0}
        if len(bonuses)>1 or len(transfers)>1:
            bad[symbol] = 'conflicting_same_day_share_ratios'; continue
        multiplier = 1 + sum(bonuses) + sum(transfers)
        multipliers[symbol] = multipliers.get(symbol,1.) * multiplier
    for symbol in bad:
        multipliers.pop(symbol,None)
    return {'multipliers': multipliers, 'unavailable_symbols': bad, 'available': True,
            'coverage': {'start_exclusive': str(start), 'end_inclusive': str(end),
                         'published_action_rows': len(records), 'excluded_symbols': len(bad),
                         'scope': 'published corporate_actions; source snapshot non-PIT; no adjustment-factor proxy'}}
