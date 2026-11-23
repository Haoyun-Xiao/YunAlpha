#!/usr/bin/env python3
"""Exercise effective non-default queries that are absent from the frozen page cache."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import urlencode
from urllib.request import urlopen

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from seven_desks.settings import CACHE, FROZEN, FROZEN_VERSION
from seven_desks.store import version_root

CASES = [
    ("national_team", "overview", {"scope": "narrow", "period": "2026-06-30"}),
    ("contrarian", "candidates", {"board": "low_pb", "red": "1", "page_size": "20", "order": "asc"}),
    ("sentiment", "overview", {"w_valuation": "1.7", "date": "2026-11-06"}),
    ("chips", "screen", {"wr_min": "20", "wr_max": "40", "c90_max": "15", "page_size": "20"}),
    ("valuation", "index", {"code": "000300.SH", "metric": "pb", "win": "5", "range": "3y"}),
    ("fund_holdings", "corp", {"corp": "华夏基金管理有限公司", "period": "2026-06-30", "date": "2026-11-06"}),
    ("capital_flow", "stocks", {"win": "100", "code": "300308.SZ", "date": "2026-11-06", "page_size": "20"}),
]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default="http://127.0.0.1:19221/workspaces/vic-desks")
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    records = []
    for board, panel, selected in CASES:
        params = {"date": "2026-11-09", "data_version": FROZEN_VERSION, **selected}
        key = sha256(json.dumps([board, panel, params], sort_keys=True).encode()).hexdigest()
        cache_rel = Path("versions") / FROZEN_VERSION / "pages" / f"{key}.json"
        assert not (FROZEN / cache_rel).exists(), (board, panel, "unexpected frozen page")
        runtime_cache = CACHE / cache_rel
        runtime_cache.unlink(missing_ok=True)
        start = time.perf_counter()
        with urlopen(args.url + f"/api/dashboards/{board}/{panel}?" + urlencode(params), timeout=60) as response:
            payload = json.load(response)
        duration = round((time.perf_counter() - start) * 1000, 2)
        assert payload["data_version"] == FROZEN_VERSION
        sections = payload["sections"]
        assert sections and any(s["rows"] for s in sections)
        assert runtime_cache.is_file()
        records.append({
            "board": board, "panel": panel, "params": selected,
            "sections": len(sections), "rows": sum(len(s["rows"]) for s in sections),
            "missing_sections": [{"id": s["id"], "reason": s.get("missing_reason")} for s in sections if not s["rows"]],
            "source_page_cached": False, "query_ms": duration,
            "runtime_cache": str(cache_rel),
        })
    report = {"version": FROZEN_VERSION, "nondefault_queries": len(records),
              "all_computed_from_frozen_data": True, "queries": records}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"nondefault_queries": len(records), "rows": [r["rows"] for r in records]}))


if __name__ == "__main__":
    main()
