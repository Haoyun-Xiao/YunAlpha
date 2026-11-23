"""Local API/contract checks; never calls the original website or data providers."""

import json
import os
import time
from pathlib import Path
from urllib.request import urlopen
from urllib.parse import urlencode, quote

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.environ["YUNALPHA_RUNTIME"]) / "artifacts/seven-default-pages"
OUT.mkdir(parents=True, exist_ok=True)
BASE = os.environ.get("SEVEN_DESKS_BASE_URL", "http://127.0.0.1:19221/workspaces/vic-desks")


def get(path):
    st = time.perf_counter()
    with urlopen(BASE + path, timeout=60) as r:
        data = json.load(r)
    return data, (time.perf_counter() - st) * 1000


def main():
    health, _ = get("/api/dashboards/health")
    version = health["data_version"]
    boards, _ = get("/api/dashboards")
    results = []
    errors = []
    for b in boards["boards"]:
        board = b["id"]
        m, _ = get("/api/dashboards/" + board + "/metadata")
        for t in m["tabs"]:
            p = (
                "/api/dashboards/"
                + board
                + "/"
                + t["id"]
                + "?"
                + urlencode({"date": "2026-11-09", "data_version": version})
            )
            try:
                d, ms = get(p)
                d2, warm = get(p)
                sections = d.get("sections", [])
                assert d["data_version"] == version
                assert isinstance(sections, list) and sections, "no sections"
                assert all(isinstance(s.get("rows"), list) for s in sections)
                empties = [
                    {
                        "id": s.get("id"),
                        "title": s.get("title"),
                        "reason": s.get("missing_reason"),
                    }
                    for s in sections
                    if not s["rows"]
                ]
                results.append(
                    {
                        "board": board,
                        "tab": t["id"],
                        "label": t["label"],
                        "section_count": len(sections),
                        "data_sections": sum(bool(s["rows"]) for s in sections),
                        "empty_sections": empties,
                        "first_ms": ms,
                        "warm_ms": warm,
                        "rows": sum(len(s["rows"]) for s in sections),
                    }
                )
            except Exception as e:
                errors.append({"board": board, "tab": t["id"], "error": str(e)})
    for board, entity in [
        ("chips", "600519.SH"),
        ("national_team", "601988.SH"),
        ("fund_holdings", "华夏基金管理有限公司"),
        ("capital_flow", "300308.SZ"),
        ("contrarian", "600519.SH"),
    ]:
        try:
            d, ms = get(
                "/api/dashboards/"
                + board
                + "/entities/"
                + quote(entity, safe="")
                + "?"
                + urlencode({"data_version": version, "date": "2026-11-09"})
            )
            results.append(
                {
                    "board": board,
                    "entity": entity,
                    "first_ms": ms,
                    "data_sections": sum(
                        bool(s.get("rows")) for s in d.get("sections", [])
                    ),
                }
            )
        except Exception as e:
            errors.append({"board": board, "entity": entity, "error": str(e)})
    summary = {
        "version": version,
        "tabs": sum("tab" in r for r in results),
        "errors": errors,
        "warm_api_p95_ms": sorted(r["warm_ms"] for r in results if "warm_ms" in r)[
            int(0.95 * (sum("warm_ms" in r for r in results) - 1))
        ],
        "results": results,
    }
    (OUT / "api-contract-results.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2)
    )
    assert not errors and summary["tabs"] == 42, summary
    print(
        json.dumps(
            {k: v for k, v in summary.items() if k != "results"}, ensure_ascii=False
        )
    )


if __name__ == "__main__":
    main()
