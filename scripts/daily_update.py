#!/opt/homebrew/bin/python3.12
"""Check and resume the YunAlpha lake and desk updates."""
import argparse
from datetime import datetime, time, timedelta
import fcntl
import json
import os
from pathlib import Path
import subprocess
from zoneinfo import ZoneInfo

from daily_update_desks import desk_state
from daily_update_dsh import decide

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/daily-update.json"
ZONE = ZoneInfo("Asia/Shanghai")
EVENING = time(18)
LATE = time(21, 30)

# Use a known PATH when launchd runs before the interactive shell has started.
os.environ["PATH"] = ":".join((
    str(Path.home() / ".local/bin"), str(Path.home() / ".npm-global/bin"),
    "/opt/homebrew/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin",
))
os.environ["TZ"] = str(ZONE)


def now():
    return datetime.now(ZONE)


def load(path):
    return json.loads(path.read_text()) if path.exists() else {}


def save(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


class DailyUpdate:
    def __init__(self, state, slot, trigger):
        self.config = load(CONFIG)
        self.repo = Path(self.config["lake_repo"])
        self.lake_config = self.repo / self.config["lake_config"]
        self.primary = self.config["lake_groups"]
        self.secondary = self.config["after_desks_groups"]
        self.state = state
        self.slot = slot
        self.trigger = trigger
        self.report = {"schema_version": 1, "slot": slot.isoformat(), "trigger": trigger,
                       "started_at": now().isoformat(), "model": self.config["model"],
                       "decisions": [], "executed": []}
        self.log = state / f'{self.report["started_at"].replace(":", "")}.log'

    def probe(self):
        command = [
            str(self.repo / ".venv/bin/python"), str(ROOT / "scripts/daily_update_lake.py"),
            str(self.lake_config), self.slot.isoformat(), now().date().isoformat(),
            json.dumps(self.primary + self.secondary), json.dumps(self.config["unscheduled_groups"]),
        ]
        probe = subprocess.run(command, cwd=self.repo, text=True, capture_output=True, timeout=120)
        if probe.returncode:
            raise RuntimeError("数据湖检查失败 " + probe.stderr.strip().splitlines()[-1])
        result = json.loads(probe.stdout)
        self.report["trade_date"] = result["trade_date"]
        self.report["lake"] = result
        return result

    def save(self, *, finished=False):
        if finished:
            self.report["finished_at"] = now().isoformat()
            save(self.log.with_suffix(".json"), self.report)
        if (day := self.report.get("trade_date")) and "desks" in self.report:
            save(self.state / f"{day}.json", self.report)
        save(self.state / "latest.json", self.report)

    def retry_due(self, lake):
        cutoff = self.slot.replace(hour=LATE.hour, minute=LATE.minute)
        return lake["replay"] or now() >= cutoff

    def lake_actions(self, lake, *, group_runs=True):
        retry_due = self.retry_due(lake)
        actions = []
        for name in self.primary:
            status = lake["groups"][name]["status"]
            if status == "pending_group" and group_runs:
                actions.append(f"lake:{name}")
            elif status == "pending_data" and retry_due:
                actions.append(f"retry:{name}")
        return actions

    def plan(self, actions):
        # DS selects only the actions that the local status check makes eligible.
        # A model string is never passed to a shell as a command.
        lake = self.report.get("lake", {})
        context = {
            "required_actions": actions, "schedule_slot": self.report["slot"],
            "trade_date": self.report.get("trade_date"), "replay": lake.get("replay"),
            "lake": {name: {**{key: row[key] for key in ("status", "pending", "unresolved_steps")},
                            "original_run_status": (row["manifest"] or {}).get("status")}
                     for name, row in lake.get("groups", {}).items()},
            "desks": self.report.get("desks", {}).get("status"),
            "last_errors": [a for a in self.report["executed"] if a["exit_code"]],
            "error": self.report.get("error"),
            "actions_guide": {"lake:<group>": "运行未完成的现有分组",
                              "retry:<group>": "只补该分组仍缺目标日期的数据",
                              "desks": "从已发布湖数据刷新并核对选中的投研台版本",
                              "maintenance": "运行报告和例行检查"},
        }
        result = decide(ROOT, self.state, self.config, context, self.log)
        self.report["decisions"].append(result)
        return set(result["actions"])

    def execute(self, name, command, *, lake=False, env=None):
        with self.log.open("a") as output:
            output.write(f"\n{now().isoformat()} {name}\n")
            output.flush()
            if lake:
                # Source the existing local provider configuration only in the
                # lake command, never in the DSH process or the launchd plist.
                command = ["/bin/bash", "-c",
                           'set -a; if [[ -f configs/.env.local ]]; then source configs/.env.local || exit $?; fi; set +a; exec "$@"',
                           "yunalpha-lake", *command]
            result = subprocess.run(command, cwd=self.repo if lake else ROOT,
                                    env={**os.environ, **(env or {})}, stdout=output, stderr=subprocess.STDOUT)
        self.report["executed"].append({"action": name, "exit_code": result.returncode})
        return result.returncode

    def fetch(self, actions, lake):
        mal = str(self.repo / ".venv/bin/mal")
        args = [mal, "run", "daily", "--config", str(self.lake_config), "--trade-date", lake["trade_date"]]
        if lake["replay"]:
            args.append("--skip-live-snapshots")
        for name in self.primary + self.secondary:
            if f"lake:{name}" in actions:
                self.execute(f"lake:{name}", [*args, "--group", name], lake=True)
        retries = [name for name in self.primary if f"retry:{name}" in actions]
        if retries:
            groups = [arg for name in retries for arg in ("--retry-group", name)]
            self.execute("lake:targeted", [*args, "--stale-only", *groups], lake=True)

    def desks(self, lake, receipt, *, refresh=False):
        row = desk_state(ROOT, self.config["desk_inputs_extra"], receipt, lake["trade_date"])
        self.report["desks"] = row
        if row["status"] in ("complete", "superseded") or not refresh:
            return row
        if lake["busy"] or lake["groups"]["core"]["status"] != "complete":
            row["reason"] = "等待核心行情和复权数据完成"
            return row
        rc = self.execute("desks", [str(ROOT / "scripts/refresh_desks.sh")],
                          env={"MAL_TRADE_DATE": lake["trade_date"], "MAL_TRIGGER": self.trigger})
        after = desk_state(ROOT, self.config["desk_inputs_extra"], row, lake["trade_date"])
        self.report["desks"] = after
        if rc:
            after.update(status="failed", reason="投研台刷新命令失败")
        elif after["status"] != "complete":
            after["reason"] = "未能核对选中版本，或刷新过程中正式输入发生变化"
        return after

    def maintenance(self, lake, prior):
        day = lake["trade_date"]
        report = self.repo / "exports/daily" / day / "report.pdf"
        result = {**prior}
        self.report["maintenance"] = result
        has_report = report.is_file() and report.stat().st_size > 0
        env = {"MAL_CONFIG": str(self.lake_config), "MAL_HEALTH_TRADE_DATE": day}
        if result.get("report") != 0:
            result["report"] = 0 if has_report else self.execute(
                "report", ["scripts/report_daily.sh", day], lake=True, env=env)
            self.save()
        if "health" not in result:
            result["health"] = self.execute("health", ["scripts/health_notify.sh"], lake=True, env=env)
            self.save()
        if "backup" not in result:
            result["backup"] = self.execute("backup", ["scripts/backup_meta.sh"], lake=True)
            self.save()
        if "clean" not in result:
            result["clean"] = self.execute("clean", [str(self.repo / ".venv/bin/mal"), "clean",
                                                     "--config", str(self.lake_config)], lake=True)

    def run(self, *, check, verify_resume):
        lake = self.probe()
        prior = load(self.state / f'{lake["trade_date"]}.json')
        prior_desk = prior.get("desks", {})
        desk = self.desks(lake, prior_desk)
        primary = self.lake_actions(lake)
        secondary = [f"lake:{g}" for g in self.secondary if lake["groups"][g]["status"] == "pending_group"]
        maintenance = prior.get("maintenance", {})
        if verify_resume and not prior and not lake["busy"]:
            if any(row["status"] != "complete" for row in lake["groups"].values()):
                raise RuntimeError("续跑验收需要选用所有分组已完成的交易日")
            if not desk["refreshed_for_target"]:
                raise RuntimeError("续跑验收应选用当前投研台已发布的同一交易日")
        if verify_resume:
            # Focus the real refresh on the application branch. Model preflight
            # and the same normal action dispatch still run.
            maintenance = {"acceptance": "desk_only"}
        elif not maintenance and (self.repo / "exports/daily" / lake["trade_date"] / "report.pdf").is_file():
            # Existing finished report from the old nightly task. Its original
            # maintenance logs are retained, so cutover doesn't replay it.
            legacy = load(self.repo / "data/multi-asset-lake/logs/daily-summary.json")
            if legacy.get("trade_date") == lake["trade_date"] and legacy.get("health") != "skipped":
                maintenance = {"legacy": legacy["health"]}
        self.report["maintenance"] = maintenance
        if (not {"acceptance", "legacy"}.intersection(maintenance)
                and (set(maintenance) != {"report", "health", "backup", "clean"}
                     or maintenance.get("report") != 0)):
            secondary.append("maintenance")
        actions = primary + (["desks"] if desk["status"] == "pending" or primary else []) + secondary
        self.report["eligible_actions"] = actions
        if check or lake["busy"] or not actions:
            if check:
                self.report["status"] = "checked"
            elif lake["busy"]:
                self.report["status"] = "busy"
            else:
                self.report["status"] = "unchanged" if lake["status"] == "complete" else "waiting"
            return

        chosen = self.plan(actions)
        trade_actions = chosen.intersection(primary)
        if trade_actions:
            self.fetch(trade_actions, lake)
            lake = self.probe()
        self.desks(lake, prior_desk, refresh="desks" in chosen)
        self.save()
        if set(secondary).intersection(chosen) - {"maintenance"}:
            self.fetch(set(secondary).intersection(chosen), lake)
            lake = self.probe()
        if "maintenance" in chosen and self.report["desks"]["status"] in ("complete", "superseded"):
            self.maintenance(lake, maintenance)

        # A long 18:00 run can overlap the 21:30 calendar trigger. Check once
        # here too, to avoid losing that retry window while launchd is busy.
        if self.retry_due(lake):
            lake = self.probe()
            retries = [action for action in self.lake_actions(lake, group_runs=False)
                       if action not in chosen]
            if retries and not lake["busy"]:
                chosen = self.plan([*retries, "desks"])
                if set(retries).intersection(chosen):
                    self.fetch(set(retries).intersection(chosen), lake)
                    lake = self.probe()
                    self.desks(lake, self.report["desks"], refresh="desks" in chosen)
        self.report["status"] = "ran"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Read status without starting DSH or jobs")
    parser.add_argument("--trigger", default="manual")
    parser.add_argument("--slot-date", type=datetime.fromisoformat, metavar="YYYY-MM-DD")
    parser.add_argument("--verify-resume", action="store_true",
                        help="Isolate the receipt for a real desk-only acceptance run")
    args = parser.parse_args()

    current = now()
    slot = current.replace(hour=18, minute=0, second=0, microsecond=0)
    if current < slot:
        slot -= timedelta(days=1)
    if args.slot_date:
        slot = datetime.combine(args.slot_date.date(), EVENING, ZONE)
    if slot > current:
        parser.error("目标 18:00 尚未到达")
    if args.verify_resume and not args.slot_date:
        parser.error("续跑验收必须指定 --slot-date")

    state = ROOT / ".runtime" / ("daily-update-acceptance" if args.verify_resume else "daily-update")
    if args.check:
        state.mkdir(parents=True, exist_ok=True)
        run = DailyUpdate(state, slot, args.trigger)
        run.run(check=True, verify_resume=args.verify_resume)
        print(json.dumps(run.report, ensure_ascii=False, indent=2))
        return 0

    lock_dir = ROOT / ".runtime/daily-update"
    lock_dir.mkdir(parents=True, exist_ok=True)
    state.mkdir(parents=True, exist_ok=True)
    with (lock_dir / "run.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("已有日更在运行，跳过重复触发。")
            return 0
        run = DailyUpdate(state, slot, args.trigger)
        try:
            run.run(check=False, verify_resume=args.verify_resume)
        except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
            run.report.update(status="failed", error=str(exc))
            if not run.report["decisions"] and "数据湖检查失败" in str(exc):
                try:
                    run.plan([])
                except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as diagnosis:
                    run.report["diagnosis_error"] = str(diagnosis)
        finally:
            run.save(finished=True)
        print(json.dumps({"status": run.report["status"], "trade_date": run.report.get("trade_date"),
                          "log": str(run.log), "summary": str(state / "latest.json"),
                          "error": run.report.get("error")}, ensure_ascii=False))
        return int(run.report["status"] == "failed")


if __name__ == "__main__":
    raise SystemExit(main())
