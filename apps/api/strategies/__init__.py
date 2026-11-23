import json
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

import core
from workspaces import entry
from .dividend import load_snapshot, result_page


router = APIRouter()

_PRING_PAGES = {"index.html", "组合介绍/index.html", "组合的诞生/index.html"}
_PRING_FILES = _PRING_PAGES | set(
    Path(__file__).with_name("pring-assets.txt").read_text(encoding="utf-8").splitlines()
)
_LEGACY_BACKTEST = "https://howardyun.cn/Macro_Research_Platform/backtest/"
_PRING_HEADERS = {
    "Content-Security-Policy": (
        "sandbox allow-scripts allow-same-origin allow-downloads; default-src 'none'; "
        "img-src 'self' data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
}


def _project_roots():
    try:
        projects = json.loads((core.ROOT / "config/projects.json").read_text(encoding="utf-8"))["projects"]
        return {
            project["id"]: Path(project["local_path"]).resolve()
            for project in projects
            if project.get("local_path") and Path(project["local_path"]).is_absolute()
        }
    except (OSError, ValueError, TypeError, KeyError, RuntimeError):
        return {}


def _pring_file(root, name):
    path = root / name
    try:
        # Reject aliases, including symlinks in parent directories.
        if path.resolve(strict=True) == path and path.is_file() and os.access(path, os.R_OK):
            return path
    except (OSError, RuntimeError):
        pass
    return None


def _entry(label, href, available):
    return {"kind": "local", "label": label, "href": href} if available else None


@router.get("/strategies")
def strategy_catalog():
    roots = _project_roots()
    pring_entry = entry("pring", "打开普林格回测", "/workspaces/pring/")
    dividend_ready = load_snapshot(roots.get("cb-strategy")) is not None
    fof_entry = entry('fof', '打开 FOF 工作台', '/workspaces/fof/')
    return {"items": [
        {
            "id": "pring",
            "name": "普林格周期",
            "groups": ["大类资产"],
            "description": "选择股、债、商品和可转债标的，配置阶段权重与风控，运行历史回测并保存策略。",
            "status": "available" if pring_entry else "unavailable",
            "entry": pring_entry,
        },
        {
            "id": "dividend-lowvol",
            "name": "红利低波",
            "groups": ["股票策略"],
            "description": "查看 cfo_timing80 冻结目标的历史重放结果和净值曲线。",
            "status": "available" if dividend_ready else "unavailable",
            "entry": _entry("查看历史回测", "/api/v1/strategies/dividend-lowvol", dividend_ready),
        },
        {
            "id": "fof",
            "name": "FOF 组合管理",
            "groups": ["基金组合"],
            "description": "手工配置、组合回测、调仓、持仓分析和组合管理。",
            "status": "available" if fof_entry else "unavailable",
            "entry": fof_entry,
        },
    ]}


@router.get("/strategies/pring/{name:path}", response_class=HTMLResponse)
def pring_content(name: str):
    root = _project_roots().get("pring")
    path = _pring_file(root, name) if root is not None and name in _PRING_FILES else None
    if path is None:
        raise HTTPException(404, "普林格成果或资源不可读")
    if name in _PRING_PAGES:
        try:
            html = path.read_text(encoding="utf-8")
        except (OSError, ValueError):
            raise HTTPException(404, "普林格成果不可读") from None
        if name == "index.html":
            html = html.replace(
                "</main>",
                f'<p><a href="{_LEGACY_BACKTEST}" rel="noreferrer">打开旧站回测（需原站登录）</a></p></main>',
                1,
            )
        return HTMLResponse(html, headers=_PRING_HEADERS)
    headers = {"X-Content-Type-Options": "nosniff"}
    if path.suffix == ".xlsx":
        return FileResponse(path, filename=path.name, headers=headers)
    return FileResponse(path, media_type="image/png", headers=headers)


@router.get("/strategies/dividend-lowvol", response_class=HTMLResponse)
def dividend_history():
    snapshot = load_snapshot(_project_roots().get("cb-strategy"))
    if snapshot is None:
        raise HTTPException(404, "红利低波历史验收结果不可读")
    return HTMLResponse(result_page(*snapshot))


__all__ = ["router"]
