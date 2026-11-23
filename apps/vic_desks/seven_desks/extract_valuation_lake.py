"""Export bounded, read-only source slices using the formal data lake SDK."""
import argparse
from pathlib import Path

from multi_asset_lake.config import load_config
from multi_asset_lake.query import load

FROZEN_DAY = "2026-11-09"
CUTOFF = "2026-11-13"
INDEX = ["000001.SH", "000016.SH", "000300.SH", "000905.SH", "399001.SZ", "399006.SZ",
         "000688.SH", "000852.SH", "000510.SH", "000985.CSI", "932000.CSI", "000922.CSI"]


def extract(config, output):
    cfg = load_config(str(config))
    slices = (
        ("official", "index_valuation_metrics", "2004-01-01", INDEX),
        ("prices", "index_bars", "2026-10-01", INDEX),
        ("calendar", "trading_calendar", "2026-10-01", None),
        ("stock", "valuation_metrics", FROZEN_DAY, None),
        ("identity", "instruments", None, None),
        ("comparisons", "index_valuation_observations", "2026-10-13", INDEX),
        ("published", "seven_dashboard_metrics", FROZEN_DAY, None),
    )
    output.mkdir(parents=True, exist_ok=True)
    for filename, dataset, start, symbols in slices:
        table = load(dataset, config=cfg, start=start, end=CUTOFF if start else None, symbols=symbols)
        if filename == "published":
            table = table.filter(
                (table["dashboard"] == "valuation") & (table["entity_type"] == "index_valuation_band")
            )
        table.write_parquet(output / (filename + ".parquet"), compression="zstd")
        print(dataset, table.height, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    extract(args.config, args.output)
