from datetime import date
import json
from math import isfinite


_RUN_ID = "run_d6d6083e7ce64dc8ac601175c148a70e"
_ACCEPTANCE = "var/acceptance-dividend-v1"
_TARGET_SHA256 = "b11c64653fa89c3a0ce27b308f9a0caacb4786b01fb10e73dc5c17cd797638b1"
# Last decision_date in the target artifact identified by the hash above.
_LAST_TARGET = date(2026, 5, 6)
_METRICS = {
    "total_return": "累计收益",
    "annualized_return": "年化收益",
    "volatility": "年化波动",
    "max_drawdown": "最大回撤",
}


def _object_after(text, marker):
    start = text.index(marker) + len(marker)
    return json.JSONDecoder().raw_decode(text, start)[0]


def load_snapshot(project_root):
    if project_root is None:
        return None
    source = project_root / _ACCEPTANCE
    try:
        summary = json.loads((source / "validation-summary.json").read_text(encoding="utf-8"))
        fingerprint = summary["fingerprint"]
        if (
            summary["run_id"] != _RUN_ID
            or summary["target_sha256"] != _TARGET_SHA256
            or fingerprint["event_version"] != "price_only_v1"
            or fingerprint["benchmark_code"] != "H20269.CSI"
        ):
            return None
        metrics = summary["metrics"]
        if not all(isfinite(metrics[key]) for key in _METRICS):
            return None
        start, end = (date.fromisoformat(summary[key]) for key in ("start_date", "end_date"))
        with (source / "checkpoints" / _RUN_ID / "result.json").open("rb") as stream:
            # In this artifact the metrics and daily ledger are first, and the fingerprint
            # is at the end. Skip the large intervening holdings and transaction tables.
            prefix = stream.read(2 * 1024 * 1024).decode("utf-8", errors="ignore")
            stream.seek(-8192, 2)
            suffix = stream.read().decode("utf-8", errors="ignore")
        if _object_after(prefix, '\n  "metrics": ') != metrics or (
            _object_after(suffix, '\n  "fingerprint": ') != fingerprint
        ):
            return None
        ledger = _object_after(prefix, '\n  "tables": {\n    "daily_ledger": ')
        points = [
            (date.fromisoformat(row["trade_date"]), row["nav"], row["benchmark_nav"]) for row in ledger
        ]
        if (
            len(points) < 2
            or len(points) != summary["tables"]["daily_ledger"]
            or points[0][0] != start
            or points[-1][0] != end
            or any(previous[0] >= current[0] for previous, current in zip(points, points[1:]))
            or not all(isfinite(nav) and isfinite(bench) for _, nav, bench in points)
        ):
            return None
        return metrics, points
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        return None


def _nav_chart(points):
    start, end = points[0][0], points[-1][0]
    low = min(min(nav, bench) for _, nav, bench in points)
    high = max(max(nav, bench) for _, nav, bench in points)
    spread = (high - low) or 1

    def line(index, color):
        coords = " ".join(
            f"{60 + 786 * (row[0] - start) / (end - start):.1f},"
            f"{242 - 218 * (row[index] - low) / spread:.1f}"
            for row in points
        )
        return f'<polyline points="{coords}" stroke="{color}" stroke-width="2" fill="none"/>'

    grid = "".join(
        f'<path d="M60 {242 - 218 * f:.1f}H846" stroke="#d8e1e5"/>'
        f'<text x="53" y="{247 - 218 * f:.1f}" text-anchor="end">{low + spread * f:.2f}</text>'
        for f in (0, 0.25, 0.5, 0.75, 1)
    )
    return f"""<svg viewBox="0 0 860 270" role="img" aria-labelledby="nav-title">
      <title id="nav-title">红利低波策略和基准的逐日净值，起点为 1</title>
      {grid}{line(1, "#006957")}{line(2, "#6a579d")}
      <text x="60" y="266">{start}</text><text x="846" y="266" text-anchor="end">{end}</text>
    </svg>"""


def result_page(metrics, points):
    stats = "".join(f"<div><dt>{label}</dt><dd>{metrics[key]:.2%}</dd></div>" for key, label in _METRICS.items())
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>红利低波历史回测 · YunAlpha</title>
<style>
body {{margin:0;background:#f3f6f7;color:#182a31;font:16px/1.65 system-ui,sans-serif}}
main {{max-width:920px;margin:auto;padding:36px 20px}} h1,h2,p {{margin:0 0 12px}}
.muted,dt {{color:#4b5e66}} .card {{border:1px solid #d8e1e5;border-radius:12px;background:white;padding:20px;margin-top:20px}}
dl {{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:16px;margin:0}}
dd {{margin:0;font-size:1.55rem;font-weight:650}} svg {{width:100%;height:auto;color:#4b5e66;font-size:13px}} svg text {{fill:currentColor}}
.strategy {{color:#006957}} .benchmark {{color:#6a579d}}
</style></head>
<body><main>
  <p class="muted">YunAlpha 策略 · 冻结目标历史重放</p>
  <h1>红利低波质量 · cfo_timing80</h1>
  <p>样本区间：{points[0][0]} 至 {points[-1][0]}，共 {len(points)} 个交易日。</p>
  <section class="card" aria-label="回测指标"><dl>{stats}</dl></section>
  <section class="card"><h2>累计净值</h2>
    <p><span class="strategy">● 红利低波</span>　<span class="benchmark">● 基准 H20269.CSI</span></p>
    {_nav_chart(points)}
  </section>
  <section class="card"><h2>数据与执行口径</h2>
    <p>最后一期冻结目标为 {_LAST_TARGET}，之后沿用最后一期目标。页面展示已完成的历史结果，不实时选股，也不接受新参数。</p>
    <p>指标和逐日净值均来自可转债投研平台同一次统一执行验收（price_only_v1）。策略执行不计入股息等事件现金流，不能与原整手研究结果混用。</p>
  </section>
</main></body></html>"""
