from datetime import date
import importlib.util
from pathlib import Path
import sys

APP=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(APP))
spec=importlib.util.spec_from_file_location('refresh_daily',APP/'scripts/refresh_daily.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def test_stopped_runner_catches_up_from_oldest_published_component():
    manifest={'valuation_cutoff':'2026-11-09','daily_files':{
        'revision/daily/stock-quotes.parquet':{'data_date':'2026-11-16'},
        'revision/daily/capital-market.parquet':{'data_date':'2026-11-13'},
    }}
    assert module.refresh_start(manifest,date(2026,12,16))==date(2026,10,26)


def test_oldest_component_is_bounded_by_requested_date():
    manifest={'valuation_cutoff':'2026-11-16','daily_files':{}}
    assert module.refresh_start(manifest,date(2026,11,9))==date(2026,10,26)
