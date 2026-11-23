"""Exercise one real historical backtest, strategy persistence and input immutability."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
from time import monotonic, sleep
from tempfile import TemporaryDirectory
from urllib.error import URLError
from urllib.request import Request, urlopen

from migrate_inputs import BASELINE, digest

APP = Path(__file__).resolve().parent
WORKSPACE = APP.parents[1]


def request(port, path, payload=None):
    req = Request(f"http://127.0.0.1:{port}{path}",
                  data=json.dumps(payload).encode() if payload is not None else None,
                  headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=30) as response:
        return json.load(response)


@contextmanager
def running(port, log):
    with log.open("a") as output:
        proc = subprocess.Popen(["bash", "run.sh", str(port)], cwd=APP, stdout=output, stderr=output)
        output.write(f"pid={proc.pid} port={port}\n")
        output.flush()
        try:
            deadline = monotonic() + 20
            while monotonic() < deadline and proc.poll() is None:
                try:
                    request(port, "/api/health")
                    break
                except URLError:
                    sleep(0.1)
            else:
                raise RuntimeError(f"普林格服务未启动，见 {log}")
            yield
        finally:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(5)
            output.write(f"pid={proc.pid} exited={proc.returncode}\n")


def verify(source, port):
    inputs = Path(os.environ["YUNALPHA_INPUTS"]) / "pring"
    manifest = json.loads((inputs / "manifest.json").read_text())
    assert manifest["source_commit"] == BASELINE
    for row in manifest["files"]:
        assert digest(source / row["source"]) == row["sha256"] == digest(inputs / row["path"])
        assert not (inputs / row["path"]).is_symlink() and (inputs / row["path"]).stat().st_nlink == 1
    with running(port, WORKSPACE / ".runtime/logs/pring-verification.log"):
        assets = request(port, "/api/assets")
        payload = {"baskets": assets["defaults"], "benchmarkId": assets["defaultBenchmarkId"],
                   "dateRange": {"start": "2016-04-01", "end": "2026-06-10"},
                   "ma20Controls": {"equity": True, "commodity": True}}
        actual = request(port, "/api/backtest", payload)
        # The old source is used only as a read-only reference during this one-time check.
        script = """
import json, sys
sys.path.insert(0, sys.argv[1] + '/backtest_platform')
import server
print(json.dumps(server.run_backtest(json.loads(sys.argv[2])), ensure_ascii=False, allow_nan=False))
"""
        reference = json.loads(subprocess.check_output(
            [sys.executable, "-B", "-c", script, str(source), json.dumps(payload)], cwd=APP))
        for field in ("window", "metrics", "series", "tradeLogs", "annualAttribution", "stageAttribution"):
            assert actual[field] == reference[field], field
        seed_count = len(json.loads((inputs / "seeds/saved_strategies.json").read_text())["strategies"])
        assert len(request(port, "/api/strategies")["strategies"]) == seed_count
        strategy = request(port, "/api/strategies", {"name": "迁入验证", "config": payload})
    with running(port, WORKSPACE / ".runtime/logs/pring-verification.log"):
        saved = request(port, "/api/strategies")["strategies"]
        assert any(row["id"] == strategy["id"] and row["name"] == "迁入验证" for row in saved)
    for row in manifest["files"]:
        assert digest(source / row["source"]) == row["sha256"] == digest(inputs / row["path"])
    print(json.dumps({
        "source_commit": BASELINE, "inputs": len(manifest["files"]),
        "groups": {name: len(rows) for name, rows in assets["groups"].items()},
        "monthly_signal": assets["pring"], "window": actual["window"], "metrics": actual["metrics"],
        "curve_rows": len(actual["series"]),
        "exactly_matched": ["window", "metrics", "series", "tradeLogs", "annualAttribution", "stageAttribution"],
        "seed_strategies": seed_count, "strategy_saved_and_reopened": True,
        "old_files_and_frozen_inputs_unchanged": True, "port": port, "service_stopped": True,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--port", type=int, default=19123)
    args = parser.parse_args()
    with TemporaryDirectory(dir=WORKSPACE / ".runtime/tmp", prefix="pring-verify-") as runtime:
        os.environ["YUNALPHA_RUNTIME"] = runtime
        verify(args.source.resolve(), args.port)
