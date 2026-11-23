"""Audit ranking with frozen dashboard fields; no network or third-party packages."""
import argparse
import hashlib
import json
import math
import re
from pathlib import Path

SAMPLES = [
    ('double_low', '2026-06-30', 'run-20260812-094916-28fabe14'),
    ('parity_premium', '2026-06-30', 'run-20260813-090951-90eec308'),
    ('iv_spread', '2026-07-31', 'run-20260909-135003-457f2f97'),
    ('iv_spread', '2026-08-25', 'run-20260909-134923-a343ab4b'),
    ('momentum20', '2026-07-31', 'run-20260821-140851-a1141e63'),
]
FIELDS = {'double_low': ('conversionPremium',), 'parity_premium': ('conversionValue', 'conversionPremium'),
          'iv_spread': ('ivSpread',), 'momentum20': ('momentum20',)}
FRAME_FIELDS = {
    '2026-06-30': ('conversionValue', 'conversionPremium'),
    '2026-07-31': ('ivSpread', 'momentum20', 'momentumEligible'),
    '2026-08-25': ('ivSpread',),
}
# (input candidates, source candidates, independently selected, target size, hits, same rank)
EXPECTED = [
    (259, 258, 44, 43, 43, 11), (259, 258, 44, 43, 43, 15),
    (240, 240, 40, 40, 40, 40), (256, 256, 43, 43, 43, 43),
    (259, 258, 20, 20, 20, 20),
]
FIXTURE = Path(__file__).with_name('pool-fixture.json')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def project_archive(evidence_root):
    root = evidence_root / 'reverse-complete'
    records = (json.loads(line) for line in (root / 'manifest.jsonl').read_text().splitlines())
    archive = {r['path']: r['sha256'] for r in records if r.get('status') == 200}
    hashes = {}

    def read(relative):
        body = (root / relative).read_bytes()
        digest = hashlib.sha256(body).hexdigest()
        require(digest == archive[relative], f'Archive digest differs: {relative}')
        hashes[relative] = digest
        return json.loads(body)

    frames = {}
    for date, metrics in FRAME_FIELDS.items():
        snapshot = read(f'raw/dashboard/market-research/{date}.json')
        require(snapshot['dataAsOf'] == date, f'Unexpected read date: {date}')
        fields = ('bondCode', 'close', 'yearsLeft', *metrics)
        frames[date] = {'fields': fields, 'rows': [[row[key] for key in fields] for row in snapshot['bonds']]}
    targets = {}
    for _, date, run in SAMPLES:
        diagnostics = read(f'raw/backtest/runs_{run}_diagnostics.json')
        basket = {}
        for row in diagnostics['imported_holdings']:
            if row['decision_date'][:10] != date:
                continue
            code = row['bond_code']
            match = re.search(r'排名(\d+)/(\d+)', row['reason'])
            require(match and code not in basket, f'Missing rank or repeated target: {run}, {code}')
            basket[code] = [row['target_weight'], int(match[1]), int(match[2])]
        targets[run] = basket
    return {'frames': frames, 'targets': targets, 'archive_sha256': hashes}


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def rank(rows, field, descending=False):
    counts = {}
    for row in rows:
        value = row[field]
        counts[value] = counts.get(value, 0) + 1
    position, ranks = 1, {}
    for value in sorted(counts, reverse=descending):
        ranks[value] = (position + (counts[value] - 1) / 2) / len(rows)
        position += counts[value]
    return ranks


def sample(mode, date, run, fixture):
    frame = fixture['frames'][date]
    require(all(len(values) == len(frame['fields']) for values in frame['rows']), f'Invalid row width: {date}')
    rows = [dict(zip(frame['fields'], values)) for values in frame['rows']]
    source = {r['bondCode']: r for r in rows}
    require(len(rows) == len(source), f'Repeated candidate in {date}')
    eligible = [
        row for row in rows
        if all(finite(row[f]) for f in ('close', 'yearsLeft', *FIELDS[mode]))
        and row['close'] > 0 and row['yearsLeft'] >= .5
    ]

    def select(pool):
        if mode == 'parity_premium':
            by_parity = rank(pool, 'conversionValue', descending=True)
            by_premium = rank(pool, 'conversionPremium')
            score = lambda r: by_parity[r['conversionValue']] + by_premium[r['conversionPremium']]
        else:
            score = {'double_low': lambda r: r['close'] + r['conversionPremium'],
                     'iv_spread': lambda r: r['ivSpread'],
                     'momentum20': lambda r: -r['momentum20']}[mode]
        size = min(20, len(pool)) if mode == 'momentum20' else math.ceil(len(pool) / 6)
        return [r['bondCode'] for r in sorted(pool, key=lambda r: (score(r), r['bondCode']))][:size]

    selected = select(eligible)
    # Rank, threshold and basket size have been decided without reading these targets.
    targets = fixture['targets'][run]
    require(targets, f'Empty reference basket: {run}')
    reason_pools = {r[2] for r in targets.values()}
    require(len(reason_pools) == 1, f'Inconsistent pool in target reasons: {run}')

    def compare(picked):
        got = set(picked)
        return {'size': len(picked), 'hits': len(got & targets.keys()),
                'missing': {code: {'in_snapshot': code in source, 'target_rank': targets[code][1]}
                            for code in sorted(targets.keys() - got)},
                'extra': {code: {k: source[code][k] for k in ('close', 'yearsLeft', *FIELDS[mode])}
                          for code in sorted(got - targets.keys())},
                'same_rank': sum(code in targets and targets[code][1] == i for i, code in enumerate(picked, 1))}

    error = max(abs(r[0] - 1 / len(targets)) for r in targets.values())
    result = {'mode': mode, 'date': date, 'run': run, 'snapshot': len(rows), 'pool': len(eligible),
              'source_pool': reason_pools.pop(), 'target_count': len(targets),
              'max_target_error_from_equal': error, **compare(selected)}
    require(error < 4e-18, f'Target no longer matches equal weight: {run}')
    if mode == 'momentum20':
        alt = [r for r in eligible if r['momentumEligible'] is True]
        result['momentumEligible'] = {'pool': len(alt), **compare(select(alt))}
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--compare-archive', type=Path, help='Optionally re-project the original archive and verify the fixture')
    args = ap.parse_args()
    frozen = json.loads(FIXTURE.read_text(encoding='utf-8'))
    if args.compare_archive:
        projected = project_archive(args.compare_archive)
        # JSON arrays are restored as lists on either side.
        require(json.loads(json.dumps(projected)) == frozen, 'Frozen fixture differs from the archive projection')
    results = []
    keys = ('pool', 'source_pool', 'size', 'target_count', 'hits', 'same_rank')
    for parameters, expected in zip(SAMPLES, EXPECTED):
        result = sample(*parameters, frozen)
        actual = tuple(result[key] for key in keys)
        require(actual == expected, f'{result["run"]}: expected {expected}, got {actual}')
        results.append(result)
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
