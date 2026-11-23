"""Read the lake's trading calendar, completion receipts and published inputs.

Invoked with the lake project's own Python. The database is opened read-only.
"""
from collections import defaultdict
from datetime import date, datetime, timezone
import json
import sqlite3
import sys

from multi_asset_lake.config import load_config
from multi_asset_lake.orchestrator.daily_handoff import (
    FETCH_STEP, group_freshness, group_runs, selected_steps,
)
from multi_asset_lake.orchestrator.run_lock import DAILY_INGESTION_LOCK, is_run_locked
from multi_asset_lake.steps.common import completed_trade_date


def status(config_path, slot, today, groups, unscheduled):
    cfg = load_config(config_path)
    unknown = set(cfg.schedule_groups) - set(groups) - set(unscheduled)
    missing = set(groups) - set(cfg.schedule_groups)
    if unknown or missing:
        raise ValueError(f"日更分组配置已变化，待登记={sorted(unknown)}，已失效={sorted(missing)}")

    target = completed_trade_date(cfg, datetime.fromisoformat(slot))
    replay = target != date.fromisoformat(today)
    date_text = target.isoformat()
    # The local trading date can begin before UTC midnight.
    started = datetime.combine(target, datetime.min.time(), datetime.fromisoformat(slot).tzinfo)
    started = started.astimezone(timezone.utc).isoformat()
    runs = group_runs(cfg, started, target)
    freshness = group_freshness(cfg, groups, target, replay=replay)
    batch_status = defaultdict(dict)
    if runs:
        db_uri = cfg.manifest_path.as_uri() + "?mode=ro"
        with sqlite3.connect(db_uri, uri=True) as connection:
            ids = [r["run_id"] for r in runs.values()]
            args = ",".join("?" for _ in ids)
            for run, step, incomplete in connection.execute(
                f"SELECT run_id,dataset,SUM(CASE WHEN status='success' THEN 0 ELSE 1 END) "
                f"FROM ingestion_batches WHERE run_id IN ({args}) GROUP BY run_id,dataset", ids,
            ):
                batch_status[run][step] = not incomplete

    success = {"success", "skipped_non_trading_day"}
    result = {}
    for group in groups:
        steps = selected_steps(cfg, group, replay=replay)
        skipped = sorted(set(cfg.schedule_groups[group].steps) - set(steps))
        run = runs.get(group)
        details = freshness[group]
        pending = [name for name, value in details.items() if value["arrival_status"] == "pending"]
        failed_steps = []
        repair_receipts = []
        needs_group = not run and bool(steps)
        if run and run["status"] not in success:
            batches = batch_status[run["run_id"]]
            failed_steps = [step for step in steps if not batches.get(step)]
            for name, retry in runs.items():
                if not (":stale" in name or name == "stale" or ":targeted" in name):
                    continue
                if retry["status"] not in success or retry["started_at"] <= run["started_at"]:
                    continue
                fixed = [step for step in failed_steps if batch_status[retry["run_id"]].get(step)]
                if fixed:
                    failed_steps = [step for step in failed_steps if step not in fixed]
                    repair_receipts.append(retry["run_id"])
            needs_group = not batches or bool(set(failed_steps) - {
                FETCH_STEP.get(name, name) for name in pending
            })
        outcome = "pending_group" if needs_group else "pending_data" if pending else "complete"
        result[group] = {
            "status": outcome, "pending": pending, "datasets": details, "manifest": run,
            "unresolved_steps": failed_steps, "repair_receipts": repair_receipts,
            "skipped_live_steps": skipped,
        }

    return {
        "trade_date": date_text, "replay": replay,
        "busy": is_run_locked(cfg.meta_root, DAILY_INGESTION_LOCK),
        "status": ("complete" if all(row["status"] == "complete" for row in result.values())
                   else "incomplete"),
        "groups": result,
    }


if __name__ == "__main__":
    config, slot, today, groups, unscheduled = sys.argv[1:]
    print(json.dumps(status(config, slot, today, json.loads(groups), json.loads(unscheduled))))
