"""Serve the imported report library without touching the source projects."""
from functools import lru_cache
from hashlib import sha256
from html import escape, unescape
import json
import os
from pathlib import Path
import re
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import HTMLResponse, RedirectResponse


reports_router = APIRouter()
CONTENT = "/api/v1/research/content"
WORKSPACE = Path(__file__).resolve().parents[3]
PREVIOUS_REPORT = "fund-001513-deep-report"
PREVIOUS_CONTENT_HASH = "3ff070c83d0e1e542d1bd4503d4146e4ad24c6426a066a23d35db8fafe26ce8b"
SCRIPTS = re.compile(r"<script\b([^>]*)>(.*?)</script\s*>", re.I | re.S)
EXTERNAL_LINKS = re.compile(r"<link\b[^>]*>", re.I)
RESOURCE_REF = re.compile(r"""\b(src|href)\s*=\s*(["'])(.*?)\2""", re.I)
MEDIA_TYPES = {
    ".js": "text/javascript", ".css": "text/css", ".png": "image/png",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".svg": "image/svg+xml",
    ".webp": "image/webp", ".gif": "image/gif", ".woff": "font/woff", ".woff2": "font/woff2",
    ".md": "text/markdown", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def library_root():
    if "YUNALPHA_INPUTS" in os.environ:
        return Path(os.environ["YUNALPHA_INPUTS"]) / "fund_reports"
    workspace = Path(os.getenv("RESEARCH_WORKSPACE", WORKSPACE))
    return workspace / ".runtime/phase2-inputs/fund_reports"


@lru_cache(maxsize=4)
def _load_index(path, fingerprint, section):
    data = json.loads(path.read_text(encoding="utf-8"))
    if data["schema_version"] != 1 or not isinstance(data["items"], list):
        raise ValueError("unsupported report library")
    pattern = r"fund-\d{6}-[0-9a-f]{16}" if section == "items" else r"collection-[0-9a-f]{16}"
    return {
        item["id"]: item for item in data.get(section, [])
        if re.fullmatch(pattern, item["id"]) and item["status"] in ("formal", "draft")
    }


def _index(root, section="items"):
    path = root / "index.json"
    try:
        stat = path.stat()
        return _load_index(path, (stat.st_ino, stat.st_size, stat.st_mtime_ns), section)
    except (OSError, ValueError, TypeError, KeyError, RuntimeError):
        return {}


def library_items(root):
    return _index(root)


def _object_path(root, info):
    if not isinstance(info, dict):
        return None
    relative = info.get("path")
    if not isinstance(relative, str) or not re.fullmatch(r"objects/[0-9a-f]{64}\.[a-z0-9]+", relative):
        return None
    path = root / relative
    try:
        if (path.is_symlink() or not path.resolve().is_relative_to(root.resolve())
                or not path.is_file() or path.stat().st_size != info.get("bytes")):
            return None
    except (OSError, RuntimeError):
        return None
    return path


def _read_object(root, info):
    path = _object_path(root, info)
    if path is not None:
        try:
            value = path.read_bytes()
            if sha256(value).hexdigest() == info.get("sha256"):
                return value
        except OSError:
            pass
    raise HTTPException(404, "报告资源不可用")


def available_items():
    root = library_root()
    return {
        id_: doc for id_, doc in library_items(root).items()
        if _object_path(root, doc.get("files", {}).get("report.html")) is not None
    }


def _href(report_id, file="report.html"):
    return f"{CONTENT}/{report_id}/{file}"


@reports_router.get("/reports")
def reports():
    return {"items": [{
        **{field: row[field] for field in ("id", "fund", "title", "date", "date_kind", "source", "status", "version")},
        "entry": {"kind": "local", "label": "打开草稿" if row["status"] == "draft" else "打开原版报告",
                  "href": _href(row["id"])},
    } for row in available_items().values()]}


@reports_router.get("/reports/collections")
def collections():
    root = library_root()
    return {"items": [{
        "id": row["id"], "title": row["title"], "source": row["source"],
        "entry": {"kind": "local", "label": "打开历史合集", "href": _href(row["id"])},
    } for row in _index(root, "collections").values()
        if _object_path(root, row["files"]["report.html"]) is not None]}


def _rewrite_report(text, row, items):
    refs = row.get("resource_map", {})

    def map_attributes(tag):
        def replace(match):
            ref = unescape(match[3])
            if ref in refs:
                return f'{match[1]}="{escape(refs[ref], quote=True)}"'
            return match[0]
        return RESOURCE_REF.sub(replace, tag)

    def script(match):
        attrs, body = match.groups()
        if "AI_ASSISTANT_SIDEBAR_VERSION" in body or re.search(r"\bsrc\s*=.*?sidebar\.js", attrs):
            return ""
        if re.search(r"\bsrc\s*=", attrs):
            attrs = map_attributes(attrs)
        return "<script" + attrs + ">" + body + "</script>"

    text = SCRIPTS.sub(script, text)
    text = EXTERNAL_LINKS.sub(lambda m: "" if "sidebar.css" in m[0] else map_attributes(m[0]), text)
    text = re.sub(r"<(?:img|source)\b[^>]+>", lambda m: map_attributes(m[0]), text, flags=re.I)
    docx = "report.docx" in row["files"]
    text = re.sub(r'(<a\b[^>]*\bclass="export-word-link"[^>]*?\bhref=")[^"]*(")', r'\g<1>report.docx\2', text)
    same_collection = {}
    source_kind = row["sources"][0]["kind"]
    for peer in items.values():
        if peer.get("legacy_name") and any(s["kind"] == source_kind for s in peer["sources"]):
            same_collection.setdefault(peer["legacy_name"], peer["id"])
    same_collection[row.get("legacy_name") or ""] = row["id"]
    # The old dropdown keeps its styling and filtering, but navigation stays on the YunAlpha route.
    links = {key: _href(value) for key, value in same_collection.items() if key}
    is_collection = bool(row.get("collection"))
    visible_manager = is_collection or bool(links and row.get("legacy_name") and "REPORT_FUND_OPTIONS" in text)
    if visible_manager and not is_collection:
        mapping = json.dumps(links, ensure_ascii=False).replace("<", "\\u003c")
        hookup = f"""<script>
    (() => {{
      const urls = {mapping};
      document.querySelectorAll('.manager-option').forEach(button => {{
        let name; try {{ name = decodeURIComponent(button.dataset.key); }} catch {{ button.remove(); return; }}
        if (!Object.hasOwn(urls, name)) button.remove();
      }});
      if (typeof selectReportFund === 'function') window.selectReportFund = (key) => {{
        let name; try {{ name = decodeURIComponent(key); }} catch {{ return; }}
        if (Object.hasOwn(urls, name)) window.location.assign(urls[name]);
      }};
    }})();
    </script>"""
    else:
        hookup = ""
    styles = "" if visible_manager else ".manager-filter{display:none!important}"
    if not docx:
        styles += ".sidebar-export,.export-word-link{display:none!important}"
    if styles:
        if "</head>" in text.lower():
            text = re.sub(r"</head>", lambda m: f"<style>{styles}</style>{m[0]}", text, count=1, flags=re.I)
    notices = []
    if is_collection:
        notices.append("历史合集归档 · 不重复计入单基金报告")
    if row["status"] == "draft":
        notices.append("草稿 · 指增研究 Part1（非完整报告）" if row.get("scope") == "part1_only" else "草稿 · 尚无适用于当前版本的通过审核记录")
    if row.get("missing_resources"):
        notices.append("部分原报告资源未随源文件提供")
    if docx and 'class="export-word-link"' not in text:
        notices.append('<a href="report.docx" download>下载原报告 Word</a>')
    if notices:
        label = " ｜ ".join(notices)
        text = re.sub(r"<body\b[^>]*>", lambda m: m[0] + (
            '<div style="box-sizing:border-box;position:fixed;right:12px;top:8px;z-index:20;'
            f'background:#fff8dc;padding:4px 8px;color:#5b4300;font:13px system-ui">{label}</div>'
        ), text, count=1, flags=re.I)
    if hookup:
        text = re.sub(r"</body>", lambda m: hookup + m[0], text, count=1, flags=re.I)
    return text


@reports_router.get("/content/{report_id}/report.html", response_class=HTMLResponse)
def report_content(report_id: str):
    root = library_root()
    items = library_items(root)
    if report_id == PREVIOUS_REPORT:
        match = next((r for r in items.values() if any(
            s.get("html_sha256") == PREVIOUS_CONTENT_HASH for s in r["sources"]
        )), None)
        if match is not None:
            return RedirectResponse(_href(match["id"]), 307)
    row = items.get(report_id) or _index(root, "collections").get(report_id)
    if row is None:
        raise HTTPException(404, "报告不存在")
    value = _read_object(root, row["files"]["report.html"])
    return HTMLResponse(_rewrite_report(value.decode("utf-8"), row, items),
                        headers={"Content-Security-Policy": "default-src 'none'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
                                 "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; "
                                 "base-uri 'none'; form-action 'none'",
                                 "X-Content-Type-Options": "nosniff"})


@reports_router.get("/content/{report_id}/{resource:path}", include_in_schema=False)
def report_resource(report_id: str, resource: str):
    root = library_root()
    row = library_items(root).get(report_id) or _index(root, "collections").get(report_id)
    if row is None or resource not in row["files"] or resource == "report.html":
        raise HTTPException(404, "报告资源不存在")
    suffix = Path(resource).suffix
    if suffix not in MEDIA_TYPES:
        raise HTTPException(404, "报告资源不存在")
    headers = {"X-Content-Type-Options": "nosniff"}
    if suffix in (".docx", ".md"):
        headers["Content-Disposition"] = f'attachment; filename="{resource}"'
    return Response(_read_object(root, row["files"][resource]), media_type=MEDIA_TYPES[suffix], headers=headers)
