"""Compare the FOF migration and exercise a disposable local portfolio copy."""
from __future__ import annotations

import argparse
from base64 import b64encode
from contextlib import contextmanager
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from urllib.error import URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from openpyxl import Workbook

from backtest_platform.workspace import INPUTS, RUNTIME
from migrate_inputs import BASELINE, sha256, source_files


APP = Path(__file__).resolve().parent
PORTFOLIO_ID = "portfolio_20260629_093923_682323"
FREQUENCIES = ("daily", "weekly", "monthly")
PREFIX = "/workspaces/fof/"
FIELDS = ("window", "metrics", "series", "rebalance_dates", "exposure")


def baseline(source, portfolio):
    # Run only the old calculation functions, with this project's locked interpreter.
    code = """
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from backtest_platform.server import run_backtest_payloads
portfolio = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
result = run_backtest_payloads(portfolio, "daily")
fields = ("window", "metrics", "series", "rebalance_dates", "exposure")
print(json.dumps({f: {k: snapshot[k] for k in fields} for f, snapshot in result.items()}, allow_nan=False))
"""
    return json.loads(subprocess.check_output([sys.executable, "-B", "-c", code, str(source), str(portfolio)], cwd=APP))


def request(port, path, method="GET", payload=None):
    req = Request(
        urljoin(f"http://127.0.0.1:{port}", path),
        data=json.dumps(payload).encode() if payload is not None else None,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urlopen(req, timeout=30) as response:
        return json.load(response) if response.headers.get_content_type() == "application/json" else response.read().decode()


@contextmanager
def running(port, runtime, evidence):
    with evidence.open("a", encoding="utf-8") as log:
        child = subprocess.Popen(
            ["bash", "run.sh", str(port)], cwd=APP, stdout=log, stderr=log,
            env={**os.environ, "YUNALPHA_RUNTIME": str(runtime), "YUNALPHA_INPUTS": str(INPUTS)},
        )
        log.write(f"pid={child.pid}, port={port}, runtime={runtime}\n")
        log.flush()
        try:
            deadline = monotonic() + 20
            while True:
                if child.poll() is not None or monotonic() > deadline:
                    raise RuntimeError(f"FOF 未能启动，见 {evidence}")
                try:
                    request(port, "/api/assets")
                    break
                except URLError:
                    sleep(0.1)
            yield
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(5)
            log.write(f"pid={child.pid} exited={child.returncode}\n")


def run(source, port, evidence):
    if subprocess.check_output(["git", "-C", source, "rev-parse", "HEAD"], text=True).strip() != BASELINE:
        raise ValueError("旧项目基线已变化")
    subprocess.run(["git", "-C", source, "diff", "--exit-code", BASELINE, "--", "backtest_platform"], check=True)
    frozen = INPUTS / "fof"
    records = json.loads((frozen / "manifest.json").read_text())["files"]
    sources = source_files(source / "data")
    hashes = {Path(record["path"]): record["sha256"] for record in records}
    assert hashes.keys() == sources.keys()
    for name, expected in hashes.items():
        assert sha256(sources[name]) == expected == sha256(frozen / name)
        assert (frozen / name).stat().st_nlink == 1 and not (frozen / name).is_symlink()

    fixed_name = PORTFOLIO_ID + ".json"
    fixed_source = sources[Path("seeds/portfolios") / fixed_name]
    old = baseline(source, fixed_source)
    expected_order = [
        path.stem for path in sorted((frozen / "seeds/portfolios").glob("portfolio_*.json"),
        key=lambda p: json.loads(p.read_text())["displayOrder"])
    ]
    with TemporaryDirectory(dir=RUNTIME / "tmp", prefix="fof-verify-") as temp:
        runtime = Path(temp)
        state = runtime / "phase2/fof"
        route = "/api/portfolios/" + PORTFOLIO_ID
        with running(port, runtime, evidence):
            assets = request(port, "/api/assets")
            assert len(assets["funds"]) == 111
            assert len([item for item in assets["benchmarks"] if item["kind"] == "index"]) == 10
            assert assets["tradingCalendar"][-1] == "2026-06-30"

            # Resolve URLs as the integrated browser does; proxy strips exactly PREFIX.
            assert "基金组合" in request(port, "/")
            for asset in ("styles.css", "app.js"):
                assert request(port, "/" + urljoin(PREFIX, asset).removeprefix(PREFIX))
            assert "apiPath" in request(port, "/app.js")
            assert [item["id"] for item in request(port, "/api/portfolios")["portfolios"]] == expected_order
            assert len(request(port, "/api/strategies")["strategies"]) == 1
            for name, expected in hashes.items():
                if name.parts[0] == "seeds":
                    assert sha256(state.joinpath(*name.parts[1:])) == expected
            portfolio = request(port, route)
            assert len(portfolio["rebalancePlan"]) == 23
            assert set(portfolio["backtestSnapshots"]) == set(FREQUENCIES)

            fixed = request(port, route + "/backtest", "POST")
            parity = {}
            for frequency in FREQUENCIES:
                fresh = request(port, route + "/snapshots/" + frequency)["snapshot"]
                assert {key: fresh[key] for key in FIELDS} == old[frequency], frequency
                assert fresh["exposure"]["history_tables"] and fresh["exposure"]["coverage"]
                parity[frequency] = {
                    "curve_points": len(fresh["series"]["portfolio"]),
                    "matched": list(FIELDS),
                    "holding_report_date": fresh["exposure"]["holding_report_date"],
                }
            assert fixed["result"]["metrics"] == old[portfolio["dataFrequency"]]["metrics"]

            # Add one manual rebalance and a second one parsed from an actual xlsx file.
            first, second = portfolio["rebalancePlan"][0]["holdings"][:2]
            manual = {"date": "2026-05-12", "source": "manual",
                      "holdings": [{**first, "weight": 65}, {**second, "weight": 35}]}
            book = Workbook()
            book.active.append(["调仓日期", "子组合", "基金代码", "基金名称", "权重%"])
            for row, weight in ((first, 40), (second, 60)):
                book.active.append(["2026-06-03", row["sleeveId"], row["fundId"], "", weight])
            buffer = BytesIO()
            book.save(buffer)
            imported = request(port, "/api/rebalance-plan/import", "POST", {
                "filename": "rebalance.xlsx", "contentBase64": b64encode(buffer.getvalue()).decode(),
            })["rebalancePlan"]
            assert imported[0]["date"] == "2026-06-03" and imported[0]["source"] == "excel_imported"
            assert [row["weight"] for row in imported[0]["holdings"]] == [40, 60]
            plan = sorted([*portfolio["rebalancePlan"], manual, *imported], key=lambda node: node["date"])
            saved = request(port, route, "PUT", {
                **portfolio, "name": "迁移验收副本", "rebalancePlan": plan,
            })
            assert saved["hasBacktest"] is False
            result = request(port, route + "/backtest", "POST")["result"]
            assert result["series"]["portfolio"] != fixed["result"]["series"]["portfolio"]
            assert {manual["date"], imported[0]["date"]} <= set(result["rebalance_dates"])

        after_save = sha256(state / "portfolios" / fixed_name)
        # Restart the same state, verify plan and all snapshots reopen without seed overwrite.
        with running(port, runtime, evidence):
            reopened = request(port, route)
            assert reopened["name"] == "迁移验收副本" and reopened["rebalancePlan"] == plan
            assert reopened["lastBacktestSnapshot"]["metrics"] == result["metrics"]
            assert set(reopened["backtestSnapshots"]) == set(FREQUENCIES)
            assert sha256(state / "portfolios" / fixed_name) == after_save

    assert hashes == {name: sha256(path) for name, path in sources.items()}
    assert hashes == {name: sha256(frozen / name) for name in sources}
    print(json.dumps({
        "source_commit": BASELINE, "inputs": len(records), "original_and_frozen_unchanged": True,
        "seeded_portfolios": len(expected_order), "seeded_strategies": 1, "seed_bytes_and_display_order": True,
        "fixed_portfolio_id": PORTFOLIO_ID, "parity": parity,
        "manual_rebalance": True, "excel_import": True, "recomputed_saved_reopened": True,
        "last_market_date": assets["tradingCalendar"][-1], "port": port, "service_stopped": True,
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--port", type=int, default=19231)
    parser.add_argument("--log", required=True, type=Path)
    args = parser.parse_args()
    run(args.source.resolve(), args.port, args.log.resolve())
