"""Conservative, input-scoped share-class deduplication; no lake or network access.

Inputs are already selected stock rows, not raw versions. A report is usable only
when a caller has independently verified its complete evidence and supplied an
actual evidence receipt/verification time as ``known_at``. The collector's
request timestamp is not proof of receipt. All dates are ISO strings; timestamps
must include a timezone. No relationship is extrapolated to another quarter.
"""
from collections import defaultdict
from datetime import date, datetime
from math import isfinite
from pathlib import Path
import sys
import hashlib
import json
import duckdb
from crowding.resources import connect as bounded_connect
import re


def _instant(value):
    try:
        if isinstance(value, datetime):
            return value if value.tzinfo is not None and value.utcoffset() is not None else None
        text = value.replace('Z', '+00:00')
        if re.search(r'[+-]\d{2}$', text):
            text += ':00'
        result = datetime.fromisoformat(text)
        return result if result.tzinfo is not None and result.utcoffset() is not None else None
    except (ValueError, TypeError, AttributeError):
        return None


def _date(value):
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def verified_holdings(rows, reports, *, report_period, announcement_cutoff, knowledge_cutoff):
    """Return verified product rows plus unknown/conflict diagnostics, never totals.

    Rows require fund_symbol (six-digit code), holding_symbol, report_period,
    shares, market_value and announce_date. Caller must resolve holding versions
    beforehand; duplicate stock keys are rejected. Rows must represent the same
    complete stock scope across shares, and the caller must enforce the holding
    data's own knowledge cutoff before invoking this function.

    Reports require status=extracted, evidence_verified=True, report_id,
    pdf_sha256, master_code, share_codes, report_period, announce_date, known_at.
    Evidence verification and complete report versions are the caller's duty;
    multiple distinct report versions for a product are conflicts even if equal.
    Announcement cutoff is a date; knowledge cutoff is a timezone-aware instant.
    """
    cutoff, knowledge = _date(announcement_cutoff), _instant(knowledge_cutoff)
    if cutoff is None or knowledge is None:
        raise ValueError('valid announcement date and timezone-aware knowledge cutoff required')
    by_share = defaultdict(list)
    for row in rows:
        if row.get('report_period') == report_period:
            by_share[row.get('fund_symbol')].append(row)
    candidates = defaultdict(list)
    for report in reports:
        announced = _date(report.get('announce_date'))
        known = _instant(report.get('known_at'))
        codes = report.get('share_codes')
        master = report.get('master_code')
        if (report.get('report_period') != report_period or report.get('status') != 'extracted'
                or report.get('evidence_verified') is not True or not report.get('report_id')
                or not report.get('pdf_sha256') or not announced or announced > cutoff
                or not known or known > knowledge or not isinstance(codes, list)
                or not codes or len(codes) != len(set(codes)) or master not in codes
                or any(not isinstance(c, str) or len(c) != 6 or not c.isascii() or not c.isdigit() for c in codes)):
            continue
        candidates[master].append(report)
    owners = defaultdict(set)
    for master, versions in candidates.items():
        for version in versions:
            for code in version['share_codes']:
                owners[code].add(master)
    verified, conflicts, unknown, handled = [], [], [], set()
    for master, versions in sorted(candidates.items()):
        codes = set().union(*(set(v['share_codes']) for v in versions))
        present = codes & by_share.keys()
        if not present:
            continue
        handled.update(present)
        if len(versions) != 1 or any(len(owners[c]) != 1 for c in codes):
            conflicts.append({'product_id': master, 'shares': sorted(present), 'reason': 'ambiguous_mapping_or_report_version'})
            continue
        single_class_proven = (
            len(codes) == 1
            and versions[0].get('single_class_without_subordinate_field') is True
            and versions[0].get('single_class_evidence_verified') is True
        )
        if (len(codes) < 2 and not single_class_proven) or present != codes:
            unknown.append({'shares': sorted(present), 'reason': 'single_or_missing_share_holdings'})
            continue
        portfolios = {}
        invalid = False
        for code in codes:
            portfolio = {}
            for row in by_share[code]:
                symbol, announced = row.get('holding_symbol'), _date(row.get('announce_date'))
                values = (row.get('shares'), row.get('market_value'))
                if (not symbol or symbol in portfolio or not announced or announced > cutoff
                        or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not isfinite(v) for v in values)):
                    invalid = True
                    break
                portfolio[symbol] = (*values, announced)
            portfolios[code] = portfolio
        first = portfolios[sorted(codes)[0]]
        if invalid or any(portfolios[c] != first for c in codes):
            conflicts.append({'product_id': master, 'shares': sorted(present), 'reason': 'invalid_or_different_portfolios'})
            continue
        report = versions[0]
        for symbol, (shares, market_value, announced) in sorted(first.items()):
            verified.append({'product_id': master, 'holding_symbol': symbol, 'report_period': report_period,
                             'shares': shares, 'market_value': market_value, 'announce_date': announced.isoformat(),
                             'source_shares': sorted(codes), 'report_id': report['report_id'],
                             'pdf_sha256': report['pdf_sha256'], 'evidence_announce_date': report['announce_date'],
                             'evidence_known_at': report['known_at']})
    for code in sorted(by_share.keys() - handled, key=str):
        unknown.append({'shares': [code], 'reason': 'no_usable_mapping'})
    return {'verified_rows': verified, 'unknown': unknown, 'conflicts': conflicts,
            'report_period': report_period, 'announcement_cutoff': announcement_cutoff,
            'knowledge_cutoff': knowledge_cutoff, 'scope': 'input_only'}


def verified_product_mapping(
    holding_rows,
    *,
    lake_root: Path,
    report_period: str,
    announcement_cutoff: str,
    knowledge_cutoff: datetime,
):
    """Build a conservative share→master mapping from published lake evidence.

    ``read_observations`` is the lake's authoritative complete-receipt and
    knowledge-time gate.  It is imported from the local lake project only when
    this optional integration is requested; absent/invalid relation data leaves
    the input rows untouched.  A mapping is emitted only after
    ``verified_holdings`` proves the same complete portfolio for every share in
    a report.  No relation is carried to another report period.
    """
    try:
        good_rows, bad_rows = _read_published_relations(
            Path(lake_root), as_of=_date(announcement_cutoff), knowledge_cutoff=knowledge_cutoff
        )
    except (OSError, ValueError, TypeError, duckdb.Error):
        return {
            'share_to_master': {}, 'verified_products': [], 'unknown': [],
            'conflicts': [], 'relation_status': 'unavailable',
        }
    if not good_rows:
        return {
            'share_to_master': {}, 'verified_products': [],
            'unknown': [], 'conflicts': [], 'relation_status': 'no_verified_observations',
            'candidate_share_codes': [], 'candidate_products': [],
        }
    selected = [row for row in good_rows if row.get('report_period') == report_period]
    if not selected:
        return {
            'share_to_master': {}, 'verified_products': [],
            'unknown': [], 'conflicts': [], 'relation_status': 'no_period_observations',
            'candidate_share_codes': [], 'candidate_products': [],
        }
    reports = []
    groups = {}
    for row in selected:
        groups.setdefault((row['report_period'], row['master_code']), []).append(row)
    for (period, master), group in groups.items():
        first = group[0]
        fetched = first.get('fetched_at')
        known_at = fetched.isoformat() if hasattr(fetched, 'isoformat') else str(fetched)
        reports.append({
            'status': 'extracted', 'evidence_verified': True,
            'report_id': first['report_id'], 'pdf_sha256': first['pdf_sha256'],
            'master_code': master,
            'share_codes': sorted({row['share_code'] for row in group}),
            'report_period': period, 'announce_date': str(first['announce_date']),
            'known_at': known_at,
            'single_class_without_subordinate_field': first.get('single_class_without_subordinate_field') is True,
            'single_class_evidence_verified': first.get('single_class_evidence_verified') is True,
        })
    result = verified_holdings(
        holding_rows, reports, report_period=report_period,
        announcement_cutoff=announcement_cutoff,
        knowledge_cutoff=knowledge_cutoff,
    )
    mapping = {}
    products = []
    for row in result['verified_rows']:
        master = row['product_id']
        codes = row['source_shares']
        for code in codes:
            previous = mapping.get(code)
            if previous is None or previous == master:
                mapping[code] = master
        if master not in products:
            products.append(master)
    candidate_codes = sorted({row['share_code'] for row in selected})
    candidate_products = sorted({row['master_code'] for row in selected})
    return {
        'share_to_master': mapping,
        'verified_products': sorted(products),
        'unknown': result['unknown'],
        'conflicts': result['conflicts'] + [
            {'reason': 'quarantined_relation_observation', 'rows': len(bad_rows)}
        ] if bad_rows else result['conflicts'],
        'relation_status': 'verified_input_scoped',
        'observed_rows': len(selected),
        'candidate_share_codes': candidate_codes,
        'candidate_products': candidate_products,
        'unmapped_observed_codes': sorted({code for item in result['unknown'] for code in item.get('shares', [])}),
    }


def _published_single_class_proof(receipt_dir, receipt, report, group):
    """Consume the lake publisher's original-PDF verification certificate.

    The caller has already checked the publication marker binds receipt.json.
    Validate its report, original PDF and PDF-parse hashes before trusting the
    single-class flag. The publisher's receipt_frame reparsed and validated the
    original PDF (including absence of subordinate/share-class fields) before
    creating that marker; no len==1 inference is made by this reader.
    """
    master = report.get('master_code')
    if (report.get('single_class_without_subordinate_field') is not True
            or report.get('share_codes') != [master]):
        return False
    evidence = report.get('evidence', {})
    lines = evidence.get('master_lines', [])
    if (evidence.get('share_lines') != [] or len(lines) != 1
            or lines[0].get('codes') != [master]
            or type(report.get('pages_examined')) is not int or report['pages_examined'] < 1
            or type(lines[0].get('page')) is not int
            or not 1 <= lines[0]['page'] <= report['pages_examined']
            or re.findall(r'(?<!\d)\d{6}(?!\d)', lines[0].get('text', '')) != [master]):
        raise ValueError('single-class report evidence mismatch')
    parse_bytes = (receipt_dir / 'pdf_parse.json').read_bytes()
    if hashlib.sha256(parse_bytes).hexdigest() != receipt.get('pdf_parse_sha256'):
        raise ValueError('single-class PDF parse receipt mismatch')
    parsed = json.loads(parse_bytes).get('pages', {})
    parsed_master, parsed_share = parsed.get('master_lines', {}), parsed.get('share_lines', {})
    if (parsed_master.get('page') != lines[0]['page']
            or not re.fullmatch(r'[0-9a-f]{64}', parsed_master.get('page_text_sha256', ''))
            or parsed_share != {'page': None, 'page_text_sha256': None}):
        raise ValueError('single-class PDF parse evidence mismatch')
    digest = hashlib.sha256()
    with (receipt_dir / 'report.pdf').open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    pdf_hash = digest.hexdigest()
    if (pdf_hash != receipt.get('pdf_sha256') or pdf_hash != report.get('pdf_sha256')
            or any(row.get('pdf_sha256') != pdf_hash for row in group)):
        raise ValueError('single-class original PDF hash mismatch')
    return True


def _read_published_relations(lake_root: Path, *, as_of: date, knowledge_cutoff: datetime,
                              report_period: date | None = None, master_codes: list[str] | None = None):
    """Small dependency-free equivalent of lake ``read_observations``.

    The dashboard process intentionally has no dependency on the lake project's
    virtualenv. It still enforces the same publication marker, receipt hash,
    complete share-code list, report-period and knowledge-time gates.
    """
    root = Path(lake_root)
    folder = root / 'curated' / 'fund_product_share_observations'
    if report_period is not None:
        partition = f'report_period={report_period.year}Q{(report_period.month - 1) // 3 + 1}'
        files = sorted([*folder.glob('*.parquet'), *(folder / partition).rglob('*.parquet')])
    else:
        files = sorted(folder.rglob('*.parquet'))
    if not files:
        return [], []
    conn = bounded_connect()
    try:
        # Cast the timezone-aware provenance timestamp to text inside DuckDB.
        # This avoids requiring the lake project's optional pytz dependency in
        # the lightweight dashboard environment; datetime.fromisoformat below
        # handles the explicit UTC offset with the standard library.
        query = ("SELECT * EXCLUDE(fetched_at), CAST(fetched_at AS VARCHAR) AS fetched_at "
                 "FROM read_parquet(?, hive_partitioning=false, union_by_name=true) "
                 "WHERE announce_date<=? AND fetched_at<=?")
        params = [[str(p) for p in files], as_of, knowledge_cutoff]
        if report_period is not None:
            query += " AND (report_period=? OR TRY_CAST(report_period AS DATE)=?)"
            params.extend([partition.removeprefix('report_period='), report_period])
        if master_codes is not None:
            # Include reports for competing owners of any requested share.
            query = f"""
                WITH visible AS ({query}), wanted AS (
                    SELECT DISTINCT report_period, share_code FROM visible
                    WHERE master_code IN (SELECT UNNEST(?::VARCHAR[]))
                ), owners AS (
                    SELECT DISTINCT v.report_period, v.master_code
                    FROM visible v JOIN wanted w USING (report_period, share_code)
                )
                SELECT v.* FROM visible v JOIN owners o USING (report_period, master_code)
            """
            params.append(master_codes)
        rows = conn.execute(query, params).fetchall()
        columns = [x[0] for x in conn.description]
    finally:
        conn.close()
    observations = [dict(zip(columns, row)) for row in rows]
    grouped = {}
    for row in observations:
        grouped.setdefault((row['report_period'], row['master_code'], row['observation_id']), []).append(row)
    good_groups = {}
    bad_rows = []
    for key, group in grouped.items():
        period, master, observation_id = key
        receipt_dir = root / 'raw' / 'fund_product_share_observations' / 'receipts' / observation_id
        marker_path = root / 'meta' / 'fund_product_share_observations' / 'published' / f'{observation_id}.json'
        try:
            marker = json.loads(marker_path.read_text())
            receipt_bytes = (receipt_dir / 'receipt.json').read_bytes()
            if marker.get('observation_id') != observation_id or marker.get('receipt_sha256') != hashlib.sha256(receipt_bytes).hexdigest():
                raise ValueError('publication marker mismatch')
            receipt = json.loads(receipt_bytes)
            published_at = datetime.fromisoformat(marker['published_at'])
            if published_at.tzinfo is None or published_at > knowledge_cutoff:
                continue
            report_bytes = (receipt_dir / 'report.json').read_bytes()
            report = json.loads(report_bytes)
            if report.get('status') != 'extracted' or report.get('report_period') != period or report.get('master_code') != master:
                raise ValueError('incomplete report marker')
            codes = sorted(set(report.get('share_codes') or []))
            actual = sorted(set(row['share_code'] for row in group))
            if not codes or actual != codes or master not in codes:
                raise ValueError('incomplete share code list')
            single_class_proof = False
            if len(codes) == 1 and report.get('single_class_without_subordinate_field') is True:
                if hashlib.sha256(report_bytes).hexdigest() != receipt.get('report_sha256'):
                    raise ValueError('single-class report receipt hash mismatch')
                if any(row.get('report_id') != report.get('report_id')
                       or str(row.get('announce_date')) != report.get('announce_date') for row in group):
                    raise ValueError('single-class observation report mismatch')
                single_class_proof = _published_single_class_proof(receipt_dir, receipt, report, group)
            for row in group:
                row['single_class_without_subordinate_field'] = single_class_proof
                row['single_class_evidence_verified'] = single_class_proof
            good_groups.setdefault((period, master), []).append(group)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            bad_rows.extend(group)
    # Any same-period share code owned by multiple masters is a conflict.
    owners = {}
    for (period, master), groups_for_key in good_groups.items():
        codes = {row['share_code'] for group in groups_for_key for row in group}
        for code in codes:
            owners.setdefault((period, code), set()).add(master)
    conflicted = {(period, master) for (period, code), masters in owners.items() if len(masters) > 1 for master in masters}
    good, bad = [], list(bad_rows)
    for key, groups_for_key in good_groups.items():
        if key in conflicted or len(groups_for_key) != 1:
            bad.extend(row for group in groups_for_key for row in group)
        else:
            good.extend(groups_for_key[0])
    return good, bad
