#!/opt/homebrew/bin/python3.12
"""Install YunAlpha's entry point on the existing macOS daily job."""
import argparse
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
LABEL = "com.multiassetlake.daily"
LAUNCHCTL = "/bin/launchctl"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preview", action="store_true")
    args = parser.parse_args()
    if sys.platform != "darwin" or not str(Path("/etc/localtime").resolve()).endswith("/Asia/Shanghai"):
        raise SystemExit("需要系统时区为 Asia/Shanghai 的 macOS。")
    if ROOT != Path(subprocess.check_output(
            ["git", "worktree", "list", "--porcelain"], cwd=ROOT, text=True
            ).splitlines()[0].removeprefix("worktree ")):
        raise SystemExit("请从 YunAlpha 主工作区安装。")

    domain = f"gui/{os.getuid()}"
    target = f"{domain}/{LABEL}"
    plist = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
    old = plistlib.loads(plist.read_bytes())
    wrapper = ROOT / "scripts/daily_update.py"
    new = {**old, "ProgramArguments": [str(wrapper), "--trigger", "launchd"],
           "WorkingDirectory": str(ROOT), "RunAtLoad": True,
           "StartCalendarInterval": [{"Hour": 18, "Minute": 0}, {"Hour": 21, "Minute": 30}]}
    if args.preview:
        sys.stdout.buffer.write(plistlib.dumps(new))
        return

    active = subprocess.run([LAUNCHCTL, "print", target], capture_output=True, text=True)
    if re.search(r"^\s+(?:state = running|pid = [1-9]\d*)\s*$", active.stdout, re.M):
        raise SystemExit("原日更正在执行，任务结束后再切换。")
    state = ROOT / ".runtime/daily-update"
    state.mkdir(parents=True, exist_ok=True)
    backup = state / f"{LABEL}.before-yunalpha.plist"
    if not backup.exists():
        shutil.copy2(plist, backup)
    candidate = plist.with_suffix(".new")
    candidate.write_bytes(plistlib.dumps(new))
    candidate.chmod(plist.stat().st_mode & 0o777)
    try:
        subprocess.run(["/usr/bin/plutil", "-lint", str(candidate)], check=True)
        if active.returncode == 0:
            subprocess.run([LAUNCHCTL, "bootout", target], check=True)
        candidate.replace(plist)
        try:
            subprocess.run([LAUNCHCTL, "bootstrap", domain, str(plist)], check=True)
        except subprocess.CalledProcessError:
            candidate.write_bytes(plistlib.dumps(old))
            candidate.replace(plist)
            if active.returncode == 0:
                subprocess.run([LAUNCHCTL, "bootstrap", domain, str(plist)], check=True)
            raise
    finally:
        candidate.unlink(missing_ok=True)
    print(f"{LABEL} 已加载。登录、18:00、21:30 运行。原配置 {backup}")


if __name__ == "__main__":
    main()
