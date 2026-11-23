#!/usr/bin/env python3
"""Local workspace lifecycle. Never runs legacy services or writes to the lake."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def root_dir():
    return Path(__file__).resolve().parents[1]


def settings(root):
    path = root / "config/local.json"
    if not path.exists():
        path = root / "config/environment.example.json"
    return json.loads(path.read_text())


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def init(root):
    local = root / "config/local.json"
    if not local.exists():
        common = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"))
        original = common.parent / "config/local.json"
        source = original if original.exists() else root / "config/environment.example.json"
        shutil.copyfile(source, local)
        local.chmod(0o600)
    cfg = settings(root)
    if cfg["lake_access"] != "read_only":
        raise ValueError("工作区只允许配置 read_only 数据湖访问")
    runtime = root / ".runtime"
    if runtime.is_symlink():
        raise ValueError("运行目录不能是共享软链接")
    for name in ["state", "logs", "tmp", "artifacts"]:
        path = runtime / name
        if path.is_symlink():
            raise ValueError("运行子目录不能是共享软链接")
        path.mkdir(parents=True, exist_ok=True)
    environment = {
        "workspace": str(root),
        "workspace_id": hashlib.sha256(str(root).encode()).hexdigest()[:12],
        "state_dir": str(runtime / "state"),
        "logs_dir": str(runtime / "logs"),
        "tmp_dir": str(runtime / "tmp"),
        "artifacts_dir": str(runtime / "artifacts"),
        "lake_root": cfg["lake_root"], "lake_access": "read_only",
        "ports": {}, "services_started": False,
    }
    previous = runtime / "environment.json"
    if previous.exists():
        saved = json.loads(previous.read_text())
        if saved.get("workspace") != str(root):
            raise ValueError("运行配置属于其他工作树，拒绝覆盖")
        environment["ports"] = saved.get("ports", {})
        environment["services_started"] = saved.get("services_started", False)
    write_json(previous, environment)
    print("工作区已初始化：", root)


def check(root):
    cfg = settings(root)
    checks = []
    for name, command, minimum in [
        ("Git", "git", (2,)),
        ("Python", cfg["python"], (3, 12)),
        ("Node", cfg["node"], (24,)),
    ]:
        try:
            output = subprocess.check_output([command, "--version"], text=True).strip()
            match = re.search(r"(\d+)\.(\d+)", output)
            passed = bool(match) and tuple(map(int, match.groups())) >= minimum
            checks.append({"name": name, "ok": passed, "detail": output})
        except (OSError, subprocess.CalledProcessError) as error:
            checks.append({"name": name, "ok": False, "detail": str(error)})
    checks.append({"name": "数据湖目录", "ok": Path(cfg["lake_root"]).is_dir(),
                   "detail": "只检查目录，不扫描、查询或更新正式数据"})
    checks.append({"name": "数据湖访问约定", "ok": cfg["lake_access"] == "read_only",
                   "detail": "配置约定；本脚本没有湖写入入口，不代表操作系统级权限隔离"})
    manifest = root / "config/projects.json"
    if manifest.exists():
        for project in json.loads(manifest.read_text())["projects"]:
            if project.get("local_path"):
                checks.append({"name": project["name"], "ok": Path(project["local_path"]).exists(),
                               "detail": project["local_path"]})
    runtime = root / ".runtime/environment.json"
    checks.append({"name": "独立运行目录", "ok": runtime.exists() and
                   json.loads(runtime.read_text()).get("workspace") == str(root), "detail": str(runtime)})
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    return all(item["ok"] for item in checks)


def task_path(root, name):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", name):
        raise ValueError("任务名使用小写字母、数字和短横线")
    return Path(settings(root)["worktree_root"]).expanduser().resolve() / name


def create(root, args):
    if not args.scope or not args.task.strip():
        raise ValueError("必须提供任务描述和允许修改的相对目录")
    for scope in args.scope:
        p = Path(scope)
        if p.is_absolute() or ".." in p.parts or ".git" in p.parts:
            raise ValueError("修改范围必须位于仓库内")
    base = git(root, "rev-parse", "--verify", args.base + "^{commit}")
    dest = task_path(root, args.name)
    if dest.exists():
        raise ValueError("目标目录已存在：" + str(dest))
    branch = "codex/" + args.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(root), "worktree", "add", "-b", branch, str(dest), base], check=True)
    init(dest)
    record = {"name": args.name, "task": args.task, "branch": branch, "base": base,
              "path": str(dest), "allowed_paths": args.scope, "status": "open"}
    write_json(dest / ".runtime/task.json", record)
    common = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    write_json(common / "workspace-tasks" / (args.name + ".json"), record)
    print(json.dumps(record, ensure_ascii=False, indent=2))


def close(root, args):
    dest = task_path(root, args.name)
    common = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    registry = common / "workspace-tasks" / (args.name + ".json")
    record = json.loads(registry.read_text())
    if record["status"] != "open" or Path(record["path"]).resolve() != dest:
        raise ValueError("任务登记与工作树不匹配")
    if dest == root or not dest.exists():
        raise ValueError("不能收回当前目录或不存在的工作树")
    if git(dest, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("工作树仍有未提交文件，请先提交或保全")
    if git(dest, "rev-list", "--count", args.into + "..HEAD") != "0":
        raise ValueError("任务提交尚未合入指定集成分支")
    if git(dest, "branch", "--show-current") != record["branch"]:
        raise ValueError("工作树分支已变化，拒绝自动收回")
    environment = json.loads((dest / ".runtime/environment.json").read_text())
    if environment.get("services_started"):
        raise ValueError("工作树登记有运行服务，先停止本任务服务并更新状态")
    # Ignored runtime files are real user work: archive them before Git removes the checkout.
    ignored_raw = subprocess.check_output(["git", "-C", str(dest), "ls-files", "--others", "--ignored", "--exclude-standard", "-z"])
    ignored = [os.fsdecode(p) for p in ignored_raw.split(b"\0") if p]
    allowed = (".runtime/", "config/local.json", "__pycache__/")
    unexpected = [p for p in ignored if not p.startswith(allowed)]
    if unexpected:
        raise ValueError("存在额外忽略文件，先单独保全：" + ", ".join(unexpected[:5]))
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    archive = Path(settings(root)["archive_root"]) / "worktree-retired" / (args.name + "-" + stamp)
    archive.mkdir(parents=True, mode=0o700)
    manifest = []
    for rel in ignored:
        source = dest / rel
        target = archive / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_symlink():
            raise ValueError("忽略文件含软链接，需单独检查：" + rel)
        shutil.copy2(source, target)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        if digest != hashlib.sha256(source.read_bytes()).hexdigest():
            raise ValueError("归档时文件发生变化：" + rel)
        manifest.append({"path": rel, "sha256": digest})
    write_json(archive / "manifest.json", manifest)
    subprocess.run(["git", "-C", str(root), "worktree", "remove", str(dest)], check=True)
    record.update(status="closed", archive=str(archive))
    write_json(registry, record)
    print("已收回工作树；分支保留；运行资料归档：", archive)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sub.add_parser("check")
    sub.add_parser("list")
    sub.add_parser("setup")
    sub.add_parser("start")
    sub.add_parser("stop")
    new = sub.add_parser("create")
    new.add_argument("name")
    new.add_argument("--base", required=True)
    new.add_argument("--task", required=True)
    new.add_argument("--scope", action="append", required=True)
    end = sub.add_parser("close")
    end.add_argument("name")
    end.add_argument("--into", default="main")
    args = parser.parse_args()
    root = root_dir()
    try:
        if args.command == "init": init(root)
        elif args.command == "check":
            import services
            ok = check(root)
            services.check(root)
            return 0 if ok else 1
        elif args.command in {"setup", "start", "stop"}:
            import services
            init(root)
            getattr(services, args.command)(root)
        elif args.command == "create": create(root, args)
        elif args.command == "close": close(root, args)
        elif args.command == "list": print(git(root, "worktree", "list"))
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print("未完成：", error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
