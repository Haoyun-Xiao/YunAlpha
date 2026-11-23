"""Report-period product portfolios shared by the crowding calculations.

Only evidence-backed equal portfolios are collapsed. Unmapped shares remain
separate observations and are never described as verified products. NAV scales
are share-class assets (CNY 1e8), summed only within the requested report period.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
import gzip
import glob
import hashlib
import json
import os
from uuid import uuid4
from datetime import date, datetime, time, timezone
from math import isclose, isfinite
from pathlib import Path
import re

import duckdb

from crowding.fund_classification import classify_fund_profiles
from crowding.resources import connect
from crowding.verified_holdings import verified_holdings, _read_published_relations

ACTIVE_CATEGORIES = {'active_equity', 'active_index_enhanced'}
from crowding.settings import STATE

CACHE_ROOT = STATE / 'products'
CACHE_VERSION = 'product-view-v5-etf-source-precision'

# The archived F10 jjcc table declares 万股/万元 and two decimal places.
# scripts/backfill_q2_target_holdings.py:_parse_period/_candidate_rows preserves
# those cells and multiplies by 10_000. Tushare's converter retains 股/元.
F10_PRECISION_SOURCE = 'eastmoney.f10_jjcc'
EXACT_HOLDING_SOURCES = {'tushare', 'tushare.raw_replay'}


def _same_identity_position(left, right):
    """Compare both units under a documented source contract, never a fit."""
    if all(isclose(left[key],right[key],rel_tol=1e-14,abs_tol=1e-6) for key in ('shares','market_value')):
        return True, False
    if left.get('source')==F10_PRECISION_SOURCE and right.get('source') in EXACT_HOLDING_SOURCES:
        coarse, precise = left,right
    elif right.get('source')==F10_PRECISION_SOURCE and left.get('source') in EXACT_HOLDING_SOURCES:
        coarse, precise = right,left
    else:
        return False, False
    for key in ('shares','market_value'):
        # Require the actual coarse value to be on the declared 100-unit grid.
        if (not isclose(coarse[key], round(coarse[key]/100)*100,rel_tol=1e-14,abs_tol=1e-6)
                or abs(coarse[key]-precise[key]) > 50.000001):
            return False, False
    return True, True


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str,
        separators=(',', ':')).encode()).hexdigest()


def _file_version(paths):
    # Published receipts are immutable; corrections receive new markers.
    return _digest([(str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in sorted(paths)])


def _atomic_json(path, value, *, compressed=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp.' + uuid4().hex)
    opener = gzip.open if compressed else open
    try:
        kwargs = {'compresslevel': 1} if compressed else {}
        with opener(temporary, 'wt', encoding='utf-8', **kwargs) as stream:
            encode = lambda obj: json.dumps(obj, ensure_ascii=False, separators=(',', ':'), default=str)
            stream.write(encode(value))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass
class ProductViewContext:
    lake_root: Path
    as_of: date
    holdings: list
    scale_paths: list
    classification: dict
    active_symbols: set | None
    share_to_master: dict | None
    relation_observations: list
    input_version: str
    cache_dir: Path
    etf_identity_evidence: dict | None = None


@lru_cache(maxsize=2)
def _published_evidence_cached(root, as_of_text, version, cache_root):
    # Cache compact relation evidence, never historical portfolios.
    path = Path(cache_root) / 'relations' / version / (as_of_text + '.json.gz')
    if path.exists():
        try:
            with gzip.open(path, 'rt', encoding='utf-8') as stream:
                return json.load(stream)
        except (OSError, ValueError, EOFError):
            pass
    rows = _read_published_relations(Path(root), as_of=date.fromisoformat(as_of_text),
        knowledge_cutoff=datetime.combine(date.fromisoformat(as_of_text), time.max, tzinfo=timezone.utc))[0]
    try:
        _atomic_json(path, rows, compressed=True)
    except OSError:
        pass
    return rows


@lru_cache(maxsize=2)
def _classification_cached(root, version):
    return classify_fund_profiles(lake_root=Path(root))


@lru_cache(maxsize=2)
def _etf_identity_evidence(paths, version):
    """Only a unique published ETF security mapping can establish aliases."""
    if not paths:
        return {'unique': {}, 'ambiguous': {}}
    conn = connect()
    try:
        symbols = [r[0] for r in conn.execute('SELECT DISTINCT symbol FROM read_parquet(?,hive_partitioning=false,union_by_name=true)',
            [list(paths)]).fetchall()]
    finally:
        conn.close()
    grouped = defaultdict(set)
    for symbol in symbols:
        if isinstance(symbol, str) and re.fullmatch(r'[0-9]{6}\.(SH|SZ)', symbol):
            grouped[symbol.split('.')[0]].add(symbol)
    return {'unique': {code: next(iter(values)) for code, values in grouped.items() if len(values)==1},
        'ambiguous': {code: sorted(values) for code, values in grouped.items() if len(values)>1}}


def normalize_etf_identities(holding_rows, evidence):
    """Deduplicate market aliases only after matching their complete portfolios.

    A report code is not itself share-class evidence. Conflicted uniquely mapped
    ETF identities are quarantined; unproven mappings retain separate identities.
    All conflicted report codes are blocked from A/C proof/fallback.
    """
    evidence = evidence or {'unique': {}, 'ambiguous': {}}
    by_symbol = defaultdict(list)
    symbols_by_code = defaultdict(set)
    for row in holding_rows:
        by_symbol[row['fund_symbol']].append(row)
        symbols_by_code[str(row['fund_symbol']).split('.')[0]].add(row['fund_symbol'])
    aliases, sources, conflicts, blocked, dropped = {}, {}, [], set(), set()
    quarantined, methods = set(), {}
    declared_codes = set(evidence.get('unique',{})) | set(evidence.get('ambiguous',{}))
    for code, symbols in symbols_by_code.items():
        if code not in declared_codes and code+'.OF' in symbols and any(s.endswith(('.SH','.SZ')) for s in symbols):
            conflicts.append({'code':code,'symbols':sorted(symbols),'reason':'no_published_etf_security_mapping'})
            blocked.add(code)
    for code, candidates in evidence.get('ambiguous', {}).items():
        present = sorted(set(by_symbol) & ({code+'.OF'} | set(candidates)))
        if present:
            conflicts.append({'code':code, 'symbols':present, 'reason':'ambiguous_published_etf_security',
                'candidate_symbols':candidates})
            blocked.add(code)
    for code, market_symbol in evidence.get('unique', {}).items():
        candidates = {code+'.OF', market_symbol}
        # A different exchange suffix is not an accepted alias.
        unexpected = symbols_by_code.get(code,set()) - candidates
        present = sorted(candidates & by_symbol.keys())
        if unexpected:
            conflicts.append({'code':code,'symbols':sorted(set(present)|unexpected),
                'reason':'etf_symbol_conflicts_with_published_security',
                'action':'quarantined_unique_market_identity'})
            blocked.add(code)
            quarantined.update(set(present)|unexpected)
            continue
        if not present:
            continue
        portfolios = {}
        valid = True
        for symbol in present:
            portfolio = {}
            for row in by_symbol[symbol]:
                stock, shares, value = row.get('holding_symbol'),row.get('shares'),row.get('market_value')
                if not stock or stock in portfolio or not _number(shares) or not _number(value):
                    valid = False
                    break
                portfolio[stock] = row
            portfolios[symbol] = portfolio
        selected = market_symbol if market_symbol in present else present[0]
        method = 'exact_portfolio_match'
        matched = valid
        if valid and len(present)==2:
            first,second=present
            a,b=portfolios[first],portfolios[second]
            common=set(a)&set(b)
            precision_used=False
            for stock in common:
                same,normalized=_same_identity_position(a[stock],b[stock])
                if not same:
                    matched=False
                    break
                precision_used |= normalized
            if matched and set(a)==set(b):
                if precision_used:
                    precise=[s for s in present if all(r.get('source') in EXACT_HOLDING_SOURCES for r in by_symbol[s])]
                    if len(precise)!=1:
                        matched=False
                    else:
                        selected=precise[0]
                        method='source_precision_normalized'
            elif matched:
                small,full=(first,second) if set(a)<set(b) else ((second,first) if set(b)<set(a) else (None,None))
                # A small quarterly source must be a true subset of the same
                # published vintage, not an unrelated or mixed-date portfolio.
                if (small is None or len(portfolios[small])>10 or not common
                        or any(not portfolios[small][stock].get('announce_date') or not portfolios[full][stock].get('announce_date') for stock in common)
                        or any(str(portfolios[small][stock].get('announce_date')) !=
                               str(portfolios[full][stock].get('announce_date')) for stock in common)
                        or any(str(r.get('report_period')) != str(by_symbol[full][0].get('report_period')) for r in by_symbol[small])):
                    matched=False
                else:
                    selected=full
                    method='same_vintage_partial_source_replaced_by_complete_source'
        if not matched:
            conflicts.append({'code':code,'symbols':present,
                'reason':'etf_alias_portfolios_differ_or_incomplete',
                'action':'quarantined_unique_market_identity',
                'quarantined_reported_mv_cny':sum(r['market_value'] for s in present for r in by_symbol[s] if _number(r.get('market_value')))})
            blocked.add(code)
            quarantined.update(present)
            continue
        aliases.update({symbol:market_symbol for symbol in candidates})
        sources[market_symbol] = present
        methods[market_symbol]={'method':method,'selected_source_symbol':selected}
        dropped.update(set(present)-{selected})
    output = []
    for symbol, rows in by_symbol.items():
        if symbol in dropped or symbol in quarantined:
            continue
        for row in rows:
            target = aliases.get(symbol,symbol)
            output.append(dict(row,fund_symbol=target) if target != symbol else row)
    return {'rows':output,'aliases':aliases,'sources':sources,'conflicts':conflicts,
        'blocked_report_codes':blocked,'input_identities':len(by_symbol),
        'collapsed_alias_identities':len(dropped),'methods':methods,
        'quarantined_identity_sources':len(quarantined),
        'removed_duplicate_reported_mv_cny':sum(r['market_value'] for s in dropped for r in by_symbol[s] if _number(r.get('market_value'))),
        'quarantined_reported_mv_cny':sum(r['market_value'] for s in quarantined for r in by_symbol[s] if _number(r.get('market_value')))}


def partition_period(path):
    """Recognized physical report periods only; unknown layouts stay fallback."""
    for part in Path(path).parts:
        match = re.fullmatch(r'report_period=(\d{4})Q([1-4])', part)
        if match:
            year, quarter = map(int, match.groups())
            month = quarter * 3
            return date(year, month, 31 if month in (3,12) else 30)
        match = re.fullmatch(r'report_period=(\d{4}-\d{2}-\d{2})', part)
        if match:
            return date.fromisoformat(match[1])
    return None


def period_paths(paths, report_period):
    target = date.fromisoformat(str(report_period))
    return [str(p) for p in paths if partition_period(p) in (None, target)]


@lru_cache(maxsize=4)
def _holding_visibility_cached(paths, file_version, as_of_text):
    # Footer statistics only: never scan a whole lake merely to build a key.
    # If a row group spans the cutoff, conservatively retain the cutoff date.
    import pyarrow.parquet as pq
    cutoff = date.fromisoformat(as_of_text)
    maximum = None
    for path in paths:
        period = partition_period(path)
        if period is not None and period > cutoff:
            continue
        metadata = pq.ParquetFile(path).metadata
        for group_index in range(metadata.num_row_groups):
            group = metadata.row_group(group_index)
            column = next((group.column(i) for i in range(group.num_columns)
                           if group.column(i).path_in_schema == 'announce_date'), None)
            stats = column.statistics if column is not None else None
            if stats is None or not stats.has_min_max:
                return as_of_text
            upper = stats.max
            upper_date = upper.date() if isinstance(upper,datetime) else (upper if isinstance(upper,date) else date.fromisoformat(str(upper)[:10]))
            if upper_date > cutoff:
                return as_of_text
            maximum = upper_date if maximum is None or upper_date > maximum else maximum
    return str(maximum) if maximum is not None else None


def product_view_context(*, as_of, lake_root, active_symbols=None, classification=None,
                         share_to_master=None, relation_observations=None, cache_root=None):
    """Inventory inputs once; share evidence/classification and disk paths.

    A context contains no holdings dictionaries. History callers reuse it across
    periods; independent modules hit the same on-disk snapshots.
    """
    root = Path(lake_root).expanduser().resolve()
    cache_root = Path(cache_root) if cache_root is not None else CACHE_ROOT
    groups = {name: sorted((root / folder).rglob('*.parquet')) for name, folder in {
        'holdings':'curated/fund_holdings', 'scales':'derived/fund_scale',
        'profiles':'curated/fund_profiles', 'etfs':'curated/etf_profiles',
        'classification':'curated/fund_classification_snapshot',
        'relations':'curated/fund_product_share_observations'}.items()}
    markers = sorted((root / 'meta/fund_product_share_observations/published').rglob('*.json'))
    versions = {name: _file_version(paths) for name,paths in groups.items()}
    relation_version = _digest([versions['relations'], _file_version(markers),
        hashlib.sha256(Path(__file__).with_name('verified_holdings.py').read_bytes()).hexdigest(), CACHE_VERSION])
    if classification is None and active_symbols is None:
        classification = _classification_cached(str(root), _digest([versions[k] for k in ('profiles','etfs','classification')]))
    if relation_observations is None:
        relation_observations = load_relation_evidence(lake_root=root, as_of=as_of,
            input_version=relation_version, cache_root=cache_root)
    code_files = [Path(__file__), Path(__file__).with_name('verified_holdings.py'), Path(__file__).with_name('fund_classification.py')]
    visibility = _holding_visibility_cached(tuple(str(p) for p in groups['holdings']), versions['holdings'], as_of.isoformat())
    version = _digest({'files':versions, 'relations':relation_version, 'holding_announcement_visibility':visibility,
        'code':[(p.name, hashlib.sha256(p.read_bytes()).hexdigest()) for p in code_files],
        'active':sorted(active_symbols) if active_symbols is not None else None,
        'classification':classification, 'mapping':share_to_master,
        'relation_observations':relation_observations, 'schema':CACHE_VERSION})
    return ProductViewContext(root, as_of, [str(p) for p in groups['holdings']],
        [str(p) for p in groups['scales']], classification or {},
        set(active_symbols) if active_symbols is not None else None,
        share_to_master, relation_observations, version, cache_root / 'periods' / version,
        _etf_identity_evidence(tuple(str(p) for p in groups['etfs']), versions['etfs']))


def disclosure_metadata(*, report_period, disclosed_identity_count, prior_year_identity_count,
                        threshold=0.7):
    """A labeled sample proxy; a missing/zero denominator stays unknown."""
    period = date.fromisoformat(str(report_period))
    prior = period.replace(year=period.year - 1)
    valid = prior_year_identity_count is not None and prior_year_identity_count > 0
    ratio = disclosed_identity_count / prior_year_identity_count if valid else None
    return {'report_period':period.isoformat(), 'disclosed_identity_count':disclosed_identity_count,
        'prior_year_period':prior.isoformat(), 'prior_year_identity_count':prior_year_identity_count,
        'coverage_ratio':ratio, 'threshold':threshold,
        'disclosing':ratio < threshold if ratio is not None else None,
        'status':'disclosing' if ratio is not None and ratio < threshold else ('threshold_met' if valid else 'unavailable'),
        'missing_reason':None if valid else 'prior_year_same_quarter_identity_count_unavailable',
        'count_scope':'主动权益及指数增强已披露身份；已归并产品与未归并主动份额；非全部已验证产品数',
        'method':'当期已披露主动身份数 / 上年同季已披露主动身份数；达到70%视为披露门槛已满足；不是全市场完整性保证'}


def disclosure_history(period_counts, previous_disclosure=None):
    counts = {str(k): v for k,v in period_counts.items()}
    previous_disclosure = previous_disclosure or {}
    result = {}
    for period, count in counts.items():
        prior = date.fromisoformat(period).replace(year=date.fromisoformat(period).year-1).isoformat()
        prior_count = counts.get(prior, previous_disclosure.get(period, {}).get('prior_year_identity_count'))
        result[period] = disclosure_metadata(report_period=period, disclosed_identity_count=count,
            prior_year_identity_count=prior_count)
    return result


def _attach_cached_disclosure(result, context):
    period = result['report_period']
    prior = date.fromisoformat(period).replace(year=date.fromisoformat(period).year-1).isoformat()
    prior_count = None
    try:
        summary = json.loads((context.cache_dir / (prior + '.summary.json')).read_text())
        if summary['input_version'] == context.input_version:
            prior_count = summary['disclosed_identity_count']
    except (OSError, ValueError, KeyError):
        pass
    count = result['coverage']['active_products_or_unmerged_shares']
    result['disclosed_identity_count'] = count
    result['disclosure'] = disclosure_metadata(report_period=period, disclosed_identity_count=count,
        prior_year_identity_count=prior_count)
    return result


def _mapping_from_prevalidated_evidence(holding_rows, observations, *, report_period, as_of,
                                      market_aliases=None, blocked_report_codes=None):
    # Reuse the same proof engine as verified_product_mapping. The receipt gate
    # already ran once, so do not reopen 30k receipts for every metric/quarter.
    groups = defaultdict(list)
    for row in observations:
        if row.get('report_period') == report_period:
            groups[row['master_code']].append(row)
    reports = []
    for master, rows in groups.items():
        first = rows[0]
        reports.append({'status':'extracted', 'evidence_verified':True,
            'report_id':first['report_id'], 'pdf_sha256':first['pdf_sha256'],
            'master_code':master, 'share_codes':sorted({r['share_code'] for r in rows}),
            'report_period':report_period, 'announce_date':str(first['announce_date']),
            'known_at':str(first['fetched_at']),
            'single_class_without_subordinate_field':first.get('single_class_without_subordinate_field') is True,
            'single_class_evidence_verified':first.get('single_class_evidence_verified') is True})
    if not reports:
        return {'share_to_master':{}, 'verified_products':[], 'conflicts':[], 'unknown':[], 'relation_status':'no_period_observations'}
    by_code = defaultdict(list)
    market_to_code = {target:source.removesuffix('.OF') for source,target in (market_aliases or {}).items()
                      if source.endswith('.OF')}
    for row in holding_rows:
        by_code[market_to_code.get(row['fund_symbol'],row['fund_symbol'].removesuffix('.OF'))].append(row)
    owners = defaultdict(set)
    for report in reports:
        for code in report['share_codes']:
            owners[code].add(report['master_code'])
    mapping, conflicts, unknown = {}, [], []
    for report in reports:
        codes = report['share_codes']
        if set(codes) & set(blocked_report_codes or []):
            continue
        if any(len(owners[code]) != 1 for code in codes):
            conflicts.append({'product_id':report['master_code'],'shares':codes,'reason':'ambiguous_mapping_or_report_version'})
            continue
        selected = [dict(row,fund_symbol=code,report_period=report_period,announce_date=str(row['announce_date']))
            for code in codes for row in by_code[code]]
        proof = verified_holdings(selected, [report], report_period=report_period,
            announcement_cutoff=as_of.isoformat(), knowledge_cutoff=datetime.combine(as_of,time.max,tzinfo=timezone.utc))
        if proof['verified_rows']:
            first = proof['verified_rows'][0]
            mapping.update({code:first['product_id'] for code in first['source_shares']})
        conflicts.extend(proof['conflicts'])
        unknown.extend(proof['unknown'])
        # Only one product's evidence/verified positions exist at a time.
        del selected, proof
    return {'share_to_master':mapping, 'verified_products':sorted(set(mapping.values())),
        'conflicts':conflicts, 'unknown':unknown,
        'relation_status':'verified_input_scoped' if groups else 'no_period_observations'}


def period_key(period):
    p = date.fromisoformat(str(period)) if not isinstance(period, date) else period
    return f'{p.year}Q{(p.month - 1) // 3 + 1}'


def _symbol(value):
    text = str(value)
    return text if '.' in text else text + '.OF'


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value) and value >= 0


def build_product_view(holding_rows, scale_rows, *, report_period, active_symbols=None,
                       classification=None, relation_mapping=None, share_to_master=None, copy_rows=True,
                       verified_single_class_symbols=None, market_aliases=None):
    """Pure view builder; input holding versions must already be resolved.

    ``relation_mapping`` is the result of verified_product_mapping for this
    period. ``share_to_master`` is a caller's verified other-period map and is
    explicitly approximate, never upgraded to current-period evidence. Both
    mappings use six-digit share codes. Conflicting portfolios are excluded
    rather than choosing a maximum. Unknown relations stay as separate shares.
    """
    period = str(report_period)
    by_share = defaultdict(dict)
    duplicate_shares = set()
    diagnostics = {'conflicts': [], 'unknown': [], 'scale_exclusions': []}
    market_aliases = market_aliases or {}
    canonical = lambda value: market_aliases.get(_symbol(value), _symbol(value))
    for row in holding_rows:
        row_period = str(row.get('report_period', period))
        if row_period not in (period, period_key(period)):
            continue
        stock, share = row.get('holding_symbol'), canonical(row.get('fund_symbol'))
        value = row.get('market_value')
        if not isinstance(stock, str) or not re.fullmatch(r'[0-9]{6}\.(SH|SZ|BJ)', stock) or not _number(value):
            continue
        if stock in by_share[share]:
            duplicate_shares.add(share)
        by_share[share][stock] = row
    relation_mapping = relation_mapping or {}
    exact = {canonical(s): canonical(m) for s, m in relation_mapping.get('share_to_master', {}).items()}
    fallback = {canonical(s): canonical(m) for s, m in (share_to_master or {}).items()}
    proven_single_classes = {canonical(s) for s in (verified_single_class_symbols or [])}
    mapping = dict(fallback)
    # A current-period conflict must not be hidden by a historical fallback.
    blocked = {canonical(s) for item in relation_mapping.get('conflicts', []) for s in item.get('shares', [])}
    for share in blocked:
        mapping.pop(share, None)
    mapping.update(exact)
    # A share participating in an exact relation cannot remain attached to an
    # older, different group through another code in the fallback map.
    exact_members = set(exact)
    for share, master in list(mapping.items()):
        if share not in exact and (master in exact_members or master in set(exact.values())):
            if exact.get(share) != master:
                mapping.pop(share)
    groups = defaultdict(set)
    expected = defaultdict(set)
    for share, master in mapping.items():
        expected[master].add(share)
    for share in by_share:
        groups[mapping.get(share, share)].add(share)
    classification = classification or {}
    categories = {canonical(r['symbol']): r.get('category', 'unknown') for r in classification.get('rows', [])}
    # The published ETF mapping is stronger identity evidence than an OF alias
    # classification. It also prevents a mistaken active alias entering rows.
    categories.update({target:'etf' for target in market_aliases.values()})
    if active_symbols is None:
        active = {canonical(s) for s in classification.get('active_symbols', [])}
        active |= {s for s, cat in categories.items() if cat in ACTIVE_CATEGORIES}
    else:
        active = {canonical(s) for s in active_symbols}
    scales = defaultdict(list)
    for row in scale_rows:
        if str(row.get('report_period')) == period:
            scales[canonical(row.get('symbol', row.get('share_code')))].append(row.get('scale_yi'))
    all_rows, products = [], {}
    conflicts = set(duplicate_shares) | blocked
    diagnostics['conflicts'].extend(relation_mapping.get('conflicts', []))

    def emit(product, shares, relation_status):
        source = sorted(shares)[0]
        category_set = {categories.get(s, 'unknown') for s in shares}
        is_active = bool((set(shares) | {product}) & active)
        explicit_excluded = category_set - ACTIVE_CATEGORIES - {'unknown'}
        if explicit_excluded and is_active:
            diagnostics['conflicts'].append({'product_symbol': product, 'reason': 'conflicting_product_classification', 'categories': sorted(category_set)})
            is_active = False
        category = next((c for c in sorted(category_set) if c in ACTIVE_CATEGORIES),
                        next((c for c in sorted(category_set) if c != 'unknown'), 'unknown'))
        if active_symbols is not None and not categories and category == 'unknown':
            category = 'active_from_caller' if is_active else 'not_active_from_caller'
        elif is_active and category == 'unknown':
            category = 'active_from_caller'
        vals = [scales[s] for s in sorted(shares)]
        valid_scale = all(v and all(_number(x) and x > 0 for x in v) and len(set(v)) == 1 for v in vals)
        scale = sum(v[0] for v in vals) if valid_scale else None
        if not valid_scale:
            diagnostics['scale_exclusions'].append({'product_symbol': product, 'reason': 'missing_or_conflicting_same_period_share_scale'})
        products[product] = {'product_symbol': product, 'share_symbols': sorted(shares),
            'relation_status': relation_status, 'is_active': is_active, 'category': category,
            'scale_yi': scale, 'scale_period': period if scale is not None else None,
            'scale_status': 'same_period_share_nav_sum_non_pit' if scale is not None else 'unavailable',
            'identity_status': 'verified' if relation_status == 'exact' else ('approximate' if relation_status == 'approx' else 'unmerged_share')}
        if relation_status == 'unknown':
            diagnostics['unknown'].append({'share_symbol': source, 'reason': 'no_verified_period_relation_retained_separately'})
        for stock, row in by_share[source].items():
            output_row = dict(row) if copy_rows else row
            output_row.update(fund_symbol=product, product_symbol=product, holding_symbol=stock,
                report_period=period, announce_date=str(row.get('announce_date')) if row.get('announce_date') is not None else None,
                market_value=float(row['market_value']), relation_status=relation_status, is_active=is_active)
            all_rows.append(output_row)

    excluded_groups = 0
    for product, shares in sorted(groups.items()):
        if shares & conflicts:
            excluded_groups += 1
            diagnostics['conflicts'].append({'product_symbol': product, 'shares': sorted(shares), 'reason': 'conflicting_or_duplicate_input_holdings'})
            continue
        known = expected.get(product, set())
        if len(shares) > 1:
            source = by_share[sorted(shares)[0]]
            def same_portfolio(other):
                if set(source) != set(other):
                    return False
                for stock in source:
                    if not isclose(source[stock]['market_value'], other[stock]['market_value'], rel_tol=1e-10, abs_tol=0.01):
                        return False
                    a, b = source[stock].get('shares'), other[stock].get('shares')
                    if a is not None or b is not None:
                        if not _number(a) or not _number(b) or not isclose(a, b, rel_tol=1e-10, abs_tol=1e-6):
                            return False
                return True
            if not all(same_portfolio(by_share[s]) for s in shares):
                excluded_groups += 1
                diagnostics['conflicts'].append({'product_symbol': product, 'shares': sorted(shares), 'reason': 'different_share_portfolios'})
                continue
        if known and known != shares:
            # Incomplete relation coverage does not establish a product NAV.
            for share in sorted(shares):
                emit(share, {share}, 'unknown')
                products[share]['scale_yi'] = None
                products[share]['scale_status'] = 'incomplete_relation_share_coverage'
            continue
        status = 'exact' if shares and all(s in exact for s in shares) else (
            'approx' if len(shares) > 1 or (shares <= proven_single_classes and shares <= fallback.keys()) else 'unknown')
        emit(product, shares, status)
    rows = [r for r in all_rows if r['is_active']]
    active_products = [p for p in products.values() if p['is_active']]
    coverage = {'input_shares': len(by_share), 'retained_identities': len(products),
        'active_products_or_unmerged_shares': len(active_products),
        'verified_products_all_categories':sum(p['relation_status']=='exact' for p in products.values()),
        'approx_products_all_categories':sum(p['relation_status']=='approx' for p in products.values()),
        'unmerged_shares_all_categories':sum(p['relation_status']=='unknown' for p in products.values()),
        'verified_single_class_products':sum(p['relation_status']=='exact' and len(p['share_symbols'])==1 for p in products.values()),
        'approx_single_class_products':sum(p['relation_status']=='approx' and len(p['share_symbols'])==1 for p in products.values()),
        'all_observations':len(all_rows),
        'verified_active_products': sum(p['relation_status'] == 'exact' for p in active_products),
        'approx_active_products': sum(p['relation_status'] == 'approx' for p in active_products),
        'unmerged_active_shares': sum(p['relation_status'] == 'unknown' for p in active_products),
        'classification_unknown_identities': sum(p['category'] == 'unknown' for p in products.values()),
        'excluded_conflict_groups': excluded_groups, 'active_observations': len(rows),
        'same_period_sized_active_identities': sum(p['scale_yi'] is not None for p in active_products)}
    return {'rows': rows, 'all_rows': all_rows, 'products': products, 'coverage': coverage,
        'diagnostics': diagnostics, 'report_period': period,
        'classification_scope': classification.get('scope', 'caller supplied active symbols'),
        'scope': '主动权益及指数增强；当期关系优先，历史已验证关系回看标approx；未归并主动份额独立保留，非全部已验证产品；同产品股票先去重；规模为同季份额净资产合计（非PIT）'}


def available_periods(*, conn, holdings, as_of, start_period, max_periods=None):
    holdings = [p for value in holdings for p in (glob.glob(str(value),recursive=True) if any(x in str(value) for x in '*?[') else [str(value)])]
    holdings = [str(p) for p in holdings if partition_period(p) is None or start_period <= partition_period(p) <= as_of]
    if not holdings:
        return []
    limit = ' LIMIT ?' if max_periods is not None else ''
    args = [holdings, start_period, as_of, as_of] + ([max_periods] if max_periods is not None else [])
    return sorted(r[0] for r in conn.execute('''SELECT DISTINCT TRY_CAST(report_period AS DATE)
        FROM read_parquet(?,hive_partitioning=false,union_by_name=true)
        WHERE TRY_CAST(report_period AS DATE) BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
          AND announce_date IS NOT NULL AND announce_date<=CAST(? AS DATE)
        ORDER BY 1 DESC''' + limit, args).fetchall())


def load_relation_evidence(*, lake_root, as_of, input_version=None, cache_root=None):
    """Receipt verification once per publication version, then compact reuse."""
    root = Path(lake_root).expanduser().resolve()
    if input_version is None:
        parquet = list((root / 'curated/fund_product_share_observations').rglob('*.parquet'))
        markers = list((root / 'meta/fund_product_share_observations/published').rglob('*.json'))
        input_version = _digest([_file_version(parquet), _file_version(markers),
            hashlib.sha256(Path(__file__).with_name('verified_holdings.py').read_bytes()).hexdigest(), CACHE_VERSION])
    try:
        return _published_evidence_cached(str(root), as_of.isoformat(), input_version, str(cache_root or CACHE_ROOT))
    except (OSError, ValueError, TypeError, duckdb.Error):
        return []


def nearest_relation_mapping(observations, *, report_period):
    """Nearest receipt-verified relation per product, explicitly non-PIT.

    Equal distance prefers an earlier report. Cross-period ownership ambiguity
    is excluded; target-period portfolio consistency is checked by the builder.
    """
    target = date.fromisoformat(str(report_period))
    grouped = defaultdict(list)
    for row in observations:
        key = row.get('report_period', '')
        match = re.fullmatch(r'(\d{4})Q([1-4])', str(key))
        if not match:
            continue
        year, quarter = map(int, match.groups())
        month = quarter * 3
        end = date(year, month, 31 if month in (3,12) else 30)
        grouped[(row['master_code'], end)].append(row)
    chosen = {}
    for (master, period), rows in grouped.items():
        if len({row['share_code'] for row in rows}) == 1 and not all(
                row.get('single_class_without_subordinate_field') is True
                and row.get('single_class_evidence_verified') is True for row in rows):
            continue
        rank = (abs((period-target).days), period>target, period)
        if master not in chosen or rank < chosen[master][0]:
            chosen[master] = (rank, period, rows)
    owners, source_periods = defaultdict(set), {}
    for master, (_, period, rows) in chosen.items():
        source_periods[_symbol(master)] = period.isoformat()
        for row in rows:
            owners[row['share_code']].add(master)
    ambiguous_masters = set().union(*(v for v in owners.values() if len(v)>1)) if owners else set()
    mapping = {share: next(iter(masters)) for share,masters in owners.items()
               if len(masters)==1 and not (masters & ambiguous_masters)}
    return mapping, source_periods


def load_product_view(*, as_of, report_period, lake_root, share_to_master=None,
                      active_symbols=None, classification=None, relation_mapping=None,
                      conn=None, relation_observations=None, context=None,
                      cache_root=None, use_cache=True):
    """Load/reuse one versioned period snapshot; never retain all periods.

    A supplied context owns classification, fallback mapping and input version.
    Explicit per-call relation_mapping inputs bypass disk caching.
    """
    root, period = Path(lake_root).expanduser().resolve(), str(report_period)
    context = context or product_view_context(as_of=as_of, lake_root=root,
        active_symbols=active_symbols, classification=classification, share_to_master=share_to_master,
        relation_observations=relation_observations, cache_root=cache_root)
    if context.lake_root != root or context.as_of != as_of:
        raise ValueError('product view context lake_root/as_of mismatch')
    active_symbols, classification = context.active_symbols, context.classification
    share_to_master = context.share_to_master
    relation_observations = context.relation_observations
    holdings, scale_paths = period_paths(context.holdings, period), period_paths(context.scale_paths, period)
    source_coverage = {'holding_files_selected':len(holdings),'holding_files_total':len(context.holdings),
        'scale_files_selected':len(scale_paths),'scale_files_total':len(context.scale_paths),
        'legacy_holding_files':sum(partition_period(p) is None for p in holdings)}
    path = context.cache_dir / (period + '.json.gz')
    use_cache = use_cache and relation_mapping is None
    if use_cache and path.exists():
        try:
            with gzip.open(path, 'rt', encoding='utf-8') as stream:
                payload = json.load(stream)
            if payload.get('schema') == CACHE_VERSION and payload.get('input_version') == context.input_version:
                result = payload['result']
                if result.get('report_period') != period:
                    raise ValueError('cached report period mismatch')
                with gzip.open(context.cache_dir / payload['rows_file'], 'rt', encoding='utf-8') as rows_stream:
                    result['all_rows'] = [json.loads(line) for line in rows_stream]
                result['rows'] = [r for r in result['all_rows'] if r['is_active']]
                result['cache'] = {'hit':True, 'input_version':context.input_version, 'path':str(path)}
                return _attach_cached_disclosure(result, context)
        except (OSError, ValueError, EOFError, KeyError, TypeError):
            pass
    own_connection = conn is None
    conn = conn or connect()
    try:
        raw = []
        if holdings:
            columns = {r[0] for r in conn.execute('DESCRIBE SELECT * FROM read_parquet(?,hive_partitioning=false,union_by_name=true)', [holdings]).fetchall()}
            shares_sql = 'shares' if 'shares' in columns else 'NULL AS shares'
            optional_fields = [name for name in ('holding_name','weight_nav','rank','source','data_version') if name in columns]
            optional_sql = ''.join(',' + name for name in optional_fields)
            query = f'''SELECT fund_symbol,holding_symbol,report_period,{shares_sql},market_value,announce_date{optional_sql}
                FROM read_parquet(?,hive_partitioning=false,union_by_name=true)
                WHERE TRY_CAST(report_period AS DATE)=CAST(? AS DATE) AND announce_date IS NOT NULL AND announce_date<=CAST(? AS DATE)
                QUALIFY ROW_NUMBER() OVER(PARTITION BY fund_symbol,holding_symbol,report_period
                  ORDER BY announce_date DESC NULLS LAST,fetched_at DESC NULLS LAST)=1'''
            cursor = conn.execute(query, [holdings, period, as_of])
            names = ['fund_symbol','holding_symbol','report_period','shares','market_value','announce_date'] + optional_fields
            while True:
                batch = cursor.fetchmany(20000)
                if not batch:
                    break
                raw.extend(dict(zip(names, row)) for row in batch)
            del batch
        scale_rows = []
        if scale_paths:
            scale_rows = [{'symbol': s, 'report_period': str(p), 'scale_yi': v} for s,p,v in conn.execute('''SELECT symbol,report_period,scale_yi
                FROM read_parquet(?,hive_partitioning=false,union_by_name=true) WHERE TRY_CAST(report_period AS DATE)=CAST(? AS DATE)''', [scale_paths, period]).fetchall()]
        market = normalize_etf_identities(raw, context.etf_identity_evidence)
        raw = market.pop('rows')
        blocked_market_codes = market['blocked_report_codes']
        if relation_mapping is None:
            relation_mapping = _mapping_from_prevalidated_evidence(raw, relation_observations,
                report_period=period_key(period), as_of=as_of, market_aliases=market['aliases'],
                blocked_report_codes=blocked_market_codes)
        elif blocked_market_codes:
            relation_mapping = dict(relation_mapping, share_to_master={s:m
                for s,m in relation_mapping.get('share_to_master',{}).items()
                if str(s).split('.')[0] not in blocked_market_codes and str(m).split('.')[0] not in blocked_market_codes})
        fallback_periods = {}
        if share_to_master is None:
            share_to_master, fallback_periods = nearest_relation_mapping(relation_observations, report_period=period)
        if blocked_market_codes:
            share_to_master = {s:m for s,m in share_to_master.items()
                if str(s).split('.')[0] not in blocked_market_codes and str(m).split('.')[0] not in blocked_market_codes}
        fallback_periods = {market['aliases'].get(product,product):source for product,source in fallback_periods.items()}
        single_classes = {row['share_code'] for row in relation_observations
            if row.get('single_class_without_subordinate_field') is True
            and row.get('single_class_evidence_verified') is True}
        result = build_product_view(raw, scale_rows, report_period=period, active_symbols=active_symbols,
            classification=classification, relation_mapping=relation_mapping, share_to_master=share_to_master,
            copy_rows=False, verified_single_class_symbols=single_classes, market_aliases=market['aliases'])
        del raw, scale_rows
        result['diagnostics']['identity_conflicts'] = market['conflicts']
        result['coverage'].update(market_identity_conflict_groups=len(market['conflicts']),
            market_alias_identities_collapsed=market['collapsed_alias_identities'],
            market_canonical_identities=len(market['sources']),
            input_identities_before_market_normalization=market['input_identities'],
            market_identity_source_precision_normalized=sum(v['method']=='source_precision_normalized' for v in market['methods'].values()),
            market_identity_partial_sources_replaced=sum(v['method']=='same_vintage_partial_source_replaced_by_complete_source' for v in market['methods'].values()),
            market_identity_quarantined_source_identities=market['quarantined_identity_sources'],
            market_identity_quarantined_reported_mv_cny=market['quarantined_reported_mv_cny'],
            market_identity_removed_duplicate_reported_mv_cny=market['removed_duplicate_reported_mv_cny'])
        for product,meta in result['products'].items():
            if product in market['sources']:
                meta['market_identity_status'] = 'published_unique_etf_security_and_equal_alias_portfolios'
                meta['market_identity_sources'] = market['sources'][product]
                meta['market_symbol'] = product
                meta['market_identity_resolution'] = market['methods'][product]
        result['source_coverage'] = source_coverage
        result['relation_evidence_status'] = relation_mapping.get('relation_status')
        for product, meta in result['products'].items():
            meta['relation_period'] = period if meta['relation_status']=='exact' else (fallback_periods.get(product) if meta['relation_status']=='approx' else None)
        result['relation_fallback_scope'] = 'nearest receipt-verified report, checked against target-period portfolios; approximate and non-PIT' if fallback_periods else 'caller supplied fallback or no fallback'
        result['cache'] = {'hit':False, 'input_version':context.input_version, 'path':str(path) if use_cache else None}
        if use_cache:
            try:
                # Store each observation once; active rows are reconstructed as
                # references to all_rows on load. gzip JSON is private local cache.
                stored = {k:v for k,v in result.items() if k not in ('rows','all_rows','cache')}
                rows_path = context.cache_dir / (period + '.rows.jsonl.gz')
                rows_path.parent.mkdir(parents=True,exist_ok=True)
                temporary_rows = rows_path.with_name(rows_path.name + '.tmp.' + uuid4().hex)
                try:
                    with gzip.open(temporary_rows,'wt',encoding='utf-8',compresslevel=1) as stream:
                        for start in range(0,len(result['all_rows']),2048):
                            stream.write(''.join(json.dumps(row,ensure_ascii=False,separators=(',',':'),default=str)+'\n'
                                for row in result['all_rows'][start:start+2048]))
                    os.replace(temporary_rows,rows_path)
                finally:
                    temporary_rows.unlink(missing_ok=True)
                _atomic_json(path, {'schema':CACHE_VERSION, 'input_version':context.input_version,
                    'rows_file':rows_path.name, 'result':stored}, compressed=True)
                _atomic_json(context.cache_dir / (period + '.summary.json'), {
                    'input_version':context.input_version, 'report_period':period,
                    'disclosed_identity_count':result['coverage']['active_products_or_unmerged_shares'],
                    'coverage':result['coverage']})
            except OSError as error:
                result['cache']['write_error'] = type(error).__name__
        return _attach_cached_disclosure(result, context)
    finally:
        if own_connection:
            conn.close()


def register_product_rows(conn, table_name, view, *, all_rows=True):
    """Register one canonical Arrow relation without per-row SQL inserts.

    The table name is an internal identifier, never SQL text from a user.
    Strings for dates make cold and cached views identical; callers cast dates
    at their SQL boundary. Optional source name/rank/weight fields stay nullable.
    """
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', table_name):
        raise ValueError('invalid internal relation name')
    import pyarrow as pa
    schema = pa.schema([('fund_symbol',pa.string()),('product_symbol',pa.string()),
        ('holding_symbol',pa.string()),('report_period',pa.string()),
        ('shares',pa.float64()),('market_value',pa.float64()),('announce_date',pa.string()),
        ('holding_name',pa.string()),('weight_nav',pa.float64()),('rank',pa.int64()),
        ('relation_status',pa.string()),('is_active',pa.bool_())])
    rows = view['all_rows' if all_rows else 'rows']
    batches = [pa.RecordBatch.from_pylist(rows[start:start+50000],schema=schema)
               for start in range(0,len(rows),50000)]
    table = pa.Table.from_batches(batches,schema=schema)
    conn.register(table_name, table)
    return table.num_rows
