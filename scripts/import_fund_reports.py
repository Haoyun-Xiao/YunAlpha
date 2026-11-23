#!/usr/bin/env python3
"""Read-only import of real legacy/cloud/Agent reports into a YunAlpha library."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
from hashlib import sha256
from html import unescape
from html.parser import HTMLParser
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import tarfile
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, unquote, urlsplit


class References(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values = []

    def handle_starttag(self, tag, attrs):
        data = dict(attrs)
        if tag in ("script", "link", "img", "source"):
            for key in ("src", "href"):
                if key in data:
                    self.values.append(data[key])


def read_json(path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default


def checked_file(path, root):
    path, root = Path(path), Path(root).resolve()
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError("来源文件越界或使用软链接")
    return path if path.is_file() else None


def title_and_date(text):
    title = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    heading = unescape(re.sub("<[^>]+>", "", title[1])).strip() if title else None
    date = re.search(r"(?:数据截至|研究截止日)\s*(\d{4})年(\d{1,2})月(\d{1,2})日", text)
    return heading, f"{int(date[1]):04}-{int(date[2]):02}-{int(date[3]):02}" if date else None


def collect_assets(path, root, text):
    refs = References()
    refs.feed(text)
    files, mapping, missing = {}, {}, []
    extensions = {".js", ".css", ".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif", ".woff", ".woff2"}
    for ref in refs.values:
        split = urlsplit(ref)
        if split.scheme or split.netloc or not split.path or split.path.startswith("#"):
            if split.scheme not in ("", "data"):
                missing.append({"reference": ref, "reason": "external_resource_not_imported"})
            continue
        suffix = Path(split.path).suffix.lower()
        if suffix not in extensions:
            continue
        resolved = root / unquote(split.path.lstrip("/")) if split.path.startswith("/") else path.parent / unquote(split.path)
        actual = checked_file(resolved, root)
        if not actual:
            missing.append({"reference": ref, "reason": "source_resource_missing"})
            continue
        payload = actual.read_bytes()
        logical = f"assets/{sha256(payload).hexdigest()[:16]}{suffix}"
        files[logical] = payload
        mapping[ref] = logical
    return files, mapping, missing


def candidate(path, root, source, fund, *, status, reason, date=None, generated_at=None, files=None, warnings=None, scope="full"):
    content = path.read_bytes()
    text = content.decode("utf-8")
    title, html_date = title_and_date(text)
    if re.search(r"测试报告|模拟报告|占位报告|示例基金|FAKE_REPORT|fixture report", text[:3000], re.I):
        return None
    assets, mappings, gaps = collect_assets(path, root, text)
    return {
        "fund": fund,
        "title": title if title and title != "基金深度报告" else f"基金深度报告 — {fund['name']}（{fund['code']}）",
        "date": html_date or date, "date_kind": "as_of" if html_date or date else None,
        "generated_at": generated_at, "status": status, "status_reason": reason, "scope": scope,
        "html_sha256": sha256(content).hexdigest(), "html": content,
        "files": {**assets, **(files or {})}, "resource_map": mappings, "missing_resources": gaps,
        "warnings": warnings or [],
        "sources": [{**source, "relative_path": path.relative_to(root).as_posix(), "html_sha256": sha256(content).hexdigest()}],
    }


def _js_data(text, variable):
    match = re.search(r"var " + variable + r" = ([^\n]+);", text)
    return json.loads(match[1]) if match else {}


def collection_audit(path, root):
    """Compare each embedded report, undoing only the collection's DOM ID prefixes."""
    text = path.read_text(encoding="utf-8")
    meta = _js_data(text, "MANAGER_META")
    charts = _js_data(text, "MANAGER_CHART_JS")
    differences = []
    for key, item in meta.items():
        funds = parse_qs(urlsplit(item["exportUrl"]).query).get("fund", [])
        name = funds[0] if funds else ""
        p = checked_file(root / "output" / name / "report.html", root)
        if not p:
            differences.append({"fund": name, "reason": "missing_single_report"})
            continue
        single = p.read_text(encoding="utf-8")
        panel = re.search(r'<section\b[^>]+id="manager-' + re.escape(key) + r'"[^>]*>\s*(.*?)\s*</section>', text, re.S)
        normalized = "\n".join(re.findall(
            r'(<div class="tab-panel.*?)(?=\n\n<div class="tab-panel|\n<div class="footnote">)', single, flags=re.S
        )).strip()
        actual = re.sub(r'id="' + re.escape(key) + r'-(tab-|chart-)', r'id="\1', panel[1].strip()) if panel else ""
        normalized = normalized.replace('class="tab-panel active"', 'class="tab-panel"')
        expected_charts = _js_data(single, "CHART_JS")
        actual_charts = {part: [snippet.replace(f"getElementById('{key}-chart-", "getElementById('chart-")
                                .replace(f'getElementById("{key}-chart-', 'getElementById("chart-')
                                for snippet in scripts] for part, scripts in charts.get(key, {}).items()}
        if not normalized or actual != normalized or expected_charts != actual_charts:
            differences.append({"fund": name, "reason": "embedded_body_or_chart_differs"})
    return {"embedded_reports": len(meta), "differences": differences, "redundant": bool(meta) and not differences}


def report_digest(row):
    # Deployment dropdowns, HTML shell and file paths do not create another research version.
    # Require the same fund, data date,正文, chart payload and Markdown for cross-deployment deduplication.
    if row.get("legacy_name"):
        text = row["html"].decode("utf-8")
        article = re.search(r"<main\b.*?</main>", text, re.S)
        charts = _js_data(text, "CHART_JS")
        markdown = row["files"].get("report.md")
        if article and charts and markdown:
            parts = [row["date"], article[0], charts, sha256(markdown).hexdigest()]
            return sha256(json.dumps(parts, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return row["html_sha256"]


def legacy_reports(root, source):
    manifest = read_json(root / "ai_assistant/data/funds_manifest.json", [])
    by_name = {item["fund_name"]: item for item in manifest}
    items, excluded, collections = [], [], []
    found = list(sorted((root / "output").glob("*/report.html")))
    for p in found:
        meta = by_name.get(p.parent.name)
        heading, _ = title_and_date(p.read_text(encoding="utf-8"))
        code = re.search(r"(\d{6}\.OF)", heading or "")
        if not meta and not code:
            audit = collection_audit(p, root)
            info = {"path": p.relative_to(root).as_posix(), **audit}
            excluded.append({**info, "reason": "collection_is_not_a_single_report"})
            if not audit["redundant"]:
                payload = p.read_bytes()
                assets, mapping, gaps = collect_assets(p, root, payload.decode("utf-8"))
                collections.append({
                    "title": heading or "基金经理报告合集", "source": source["name"], "sources": [source],
                    "files": {"report.html": payload, **assets}, "resource_map": mapping, "missing_resources": gaps,
                    "collection": info, "html_sha256": sha256(payload).hexdigest(),
                    "status": "formal", "legacy_name": None,
                })
            continue
        fund_code = meta.get("fund_code") if meta else code[1]
        if not re.fullmatch(r"\d{6}\.OF", fund_code or ""):
            excluded.append({"path": str(p.relative_to(root)), "reason": "unverified_fund_identity"})
            continue
        name = meta.get("fund_short_name") if meta else (heading.split("—")[-1].split("（")[0].strip())
        files = {}
        if (p.parent / "report.md").is_file():
            files["report.md"] = (p.parent / "report.md").read_bytes()
        word = sorted(p.parent.glob("*.docx"))
        if len(word) == 1:
            files["report.docx"] = word[0].read_bytes()
        row = candidate(p, root, source, {"code": fund_code, "name": name},
                        status="formal", reason="existing_legacy_report_library", files=files)
        if row:
            row["legacy_name"] = p.parent.name
            row["manager"] = meta.get("manager") if meta else None
            items.append(row)
        else:
            excluded.append({"path": str(p.relative_to(root)), "reason": "test_placeholder"})
    return items, {"html_candidates": len(found), "report_candidates": len(items), "excluded": excluded}, collections


def agent_reports(root, source):
    database = root / "var/state/agent.sqlite"
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    jobs = {row["job_id"]: dict(row) for row in connection.execute("SELECT * FROM report_jobs")}
    artifacts = [dict(row) for row in connection.execute("SELECT * FROM artifacts WHERE kind IN ('report_html','report_docx','report_markdown')")]
    connection.close()
    groups = {}
    for artifact in artifacts:
        artifact["metadata"] = json.loads(artifact["metadata_json"])
        groups.setdefault(artifact["metadata"].get("job_id"), []).append(artifact)
    items, excluded = [], []
    for artifact in artifacts:
        if artifact["kind"] != "report_html":
            continue
        job_id = artifact["metadata"].get("job_id")
        job = jobs.get(job_id)
        if not job or job["status"] not in ("completed", "completed_with_warnings", "succeeded"):
            excluded.append({"artifact_id": artifact["artifact_id"], "reason": "no_completed_real_job"})
            continue
        request = json.loads(job["request_json"])
        directory = root / "var/jobs" / job_id
        result = read_json(directory / "result_manifest.json", {})
        identity = result.get("fund") or {}
        fund_code = identity.get("fund_symbol")
        if not re.fullmatch(r"\d{6}\.OF", fund_code or ""):
            code = re.search(r"\b\d{6}\.OF\b", request.get("fund_query", ""))
            fund_code = code[0] if code else None
        if not fund_code:
            excluded.append({"artifact_id": artifact["artifact_id"], "reason": "unverified_fund_identity"})
            continue
        p = checked_file(Path(artifact["path"]), root)
        if not p or sha256(p.read_bytes()).hexdigest() != artifact["sha256"]:
            excluded.append({"artifact_id": artifact["artifact_id"], "reason": "missing_or_changed_artifact"})
            continue
        companions = {}
        timestamp = datetime.fromisoformat(artifact["created_at"])
        for kind, logical in (("report_docx", "report.docx"), ("report_markdown", "report.md")):
            nearby = sorted((a for a in groups[job_id] if a["kind"] == kind),
                            key=lambda a: abs((datetime.fromisoformat(a["created_at"]) - timestamp).total_seconds()))
            if nearby and abs((datetime.fromisoformat(nearby[0]["created_at"]) - timestamp).total_seconds()) < 5:
                f = checked_file(Path(nearby[0]["path"]), root)
                if f and sha256(f.read_bytes()).hexdigest() == nearby[0]["sha256"]:
                    companions[logical] = f.read_bytes()
        current = artifact["artifact_id"] in result.get("artifacts", [])
        passed = (result.get("audit") or {}).get("passed") is True and current and request.get("web_mode") != "replay"
        row = candidate(p, root, {**source, "job_id": job_id, "artifact_id": artifact["artifact_id"]},
                        {"code": fund_code, "name": identity.get("fund_name") or fund_code},
                        status="formal" if passed else "draft",
                        reason=("audit_passed" if passed else "replay_material" if request.get("web_mode") == "replay"
                                else "audit_not_passed" if current else "historical_artifact_audit_not_bound"),
                        date=request.get("as_of"), generated_at=artifact["created_at"], files=companions,
                        warnings=json.loads(job["warnings_json"] or "[]"))
        if row:
            row["manager"] = identity.get("manager_name")
            row["legacy_name"] = None
            items.append(row)
    # A separately generated real Part1 research report is retained explicitly as partial draft.
    partials = []
    for manifest_file in sorted((root / "var/index_enhancement").glob("*/result_manifest.json")):
        result = read_json(manifest_file)
        report = result.get("outputs", {}).get("html", {})
        if result.get("status") != "completed" or not report.get("path"):
            continue
        p = checked_file(Path(report["path"]), root)
        code = result.get("fund_symbol")
        if not p or sha256(p.read_bytes()).hexdigest() != report.get("sha256") or not re.fullmatch(r"\d{6}\.OF", code or ""):
            continue
        text = p.read_text(encoding="utf-8")
        title, _ = title_and_date(text)
        name = (title or code).split(" 指增研究")[0]
        files = {}
        for kind, logical in (("markdown", "report.md"), ("docx", "report.docx")):
            info = result.get("outputs", {}).get(kind, {})
            f = checked_file(Path(info["path"]), root) if info.get("path") else None
            if f and sha256(f.read_bytes()).hexdigest() == info.get("sha256"):
                files[logical] = f.read_bytes()
        row = candidate(p, root, {**source, "job_id": manifest_file.parent.name}, {"code": code, "name": name},
                        status="draft", reason="partial_report", date=result.get("as_of"), files=files, scope=result.get("scope", "partial"))
        if row:
            items.append(row)
            partials.append(str(p.relative_to(root)))
    return items, {"database_html_artifacts": sum(a["kind"] == "report_html" for a in artifacts),
                   "jobs_with_html": len(list((root / "var/jobs").glob("*/output/report.html"))),
                   "job_status_counts": dict(Counter(j["status"] for j in jobs.values())),
                   "report_candidates": len(items), "partial_reports": partials, "excluded": excluded}


REMOTE_EXPORT = r"""
from pathlib import Path
from html.parser import HTMLParser
from urllib.parse import urlsplit, unquote
from hashlib import sha256
import io,json,tarfile,sys
root=Path('/data/www/Mutual_Fund')
class Refs(HTMLParser):
 def handle_starttag(self,tag,attrs):
  a=dict(attrs)
  if tag in ('script','link','img','source'):
   for key in ('src','href'):
    if key in a:self.refs.append(a[key])
 def __init__(self):super().__init__();self.refs=[]
html=sorted((root/'output').glob('*/report.html'))
files=set(html)
m=root/'ai_assistant/data/funds_manifest.json'
if m.is_file():files.add(m)
for p in html:
 files.update(p.parent.glob('*.docx'))
 if (p.parent/'report.md').is_file():files.add(p.parent/'report.md')
 parser=Refs();parser.feed(p.read_text())
 for ref in parser.refs:
  u=urlsplit(ref)
  if u.scheme or u.netloc:continue
  target=(root/unquote(u.path.lstrip('/'))) if u.path.startswith('/') else p.parent/unquote(u.path)
  if target.resolve().is_relative_to(root) and target.is_file() and target.suffix.lower() in ('.js','.css','.png','.jpg','.jpeg','.svg','.webp','.woff','.woff2'):files.add(target.resolve())
inventory={'html_count':len(html),'files':[{'relative':p.relative_to(root).as_posix(),'sha256':sha256(p.read_bytes()).hexdigest()} for p in sorted(files)]}
with tarfile.open(fileobj=sys.stdout.buffer,mode='w|') as archive:
 for p in sorted(files):
  if p.is_symlink():raise RuntimeError('symlink source rejected')
  archive.add(p,arcname=p.relative_to(root).as_posix(),recursive=False)
 payload=json.dumps(inventory).encode()
 entry=tarfile.TarInfo('cloud_inventory.json');entry.size=len(payload)
 archive.addfile(entry,io.BytesIO(payload))
"""


def cloud_snapshot(source, destination):
    if source.get("remote_root", "/data/www/Mutual_Fund") != "/data/www/Mutual_Fund":
        raise ValueError("只允许已授权的 Mutual_Fund 云端目录")
    args = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10",
            "-i", source["key"], source["host"], "python3 -"]
    package = destination / "cloud.tar"
    with package.open("wb") as output:
        completed = subprocess.run(args, input=REMOTE_EXPORT.encode(), stdout=output, stderr=subprocess.PIPE, timeout=180)
    if completed.returncode:
        raise RuntimeError("云端只读导出失败；未更改云端服务")
    root = destination / "cloud"
    root.mkdir()
    with tarfile.open(package) as archive:
        if any(not (m.isfile() or m.isdir()) for m in archive.getmembers()):
            raise ValueError("云端导出含不支持的文件类型")
        archive.extractall(root, filter="data")
    inventory = read_json(root / "cloud_inventory.json")
    for item in inventory["files"]:
        p = checked_file(root / item["relative"], root)
        if not p or sha256(p.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("云端复制摘要不一致")
    return root, inventory


def store_blob(root, content, suffix):
    digest = sha256(content).hexdigest()
    relative = f"objects/{digest}{suffix}"
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.is_symlink() or sha256(target.read_bytes()).hexdigest() != digest:
            raise ValueError("自有对象文件损坏")
    else:
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_bytes(content)
        temporary.replace(target)
    return {"path": relative, "sha256": digest, "bytes": len(content)}


def import_library(plan, destination):
    destination.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink():
        raise ValueError("报告库不能是软链接")
    all_rows, inventory, archives = [], [], []
    scratch = Path(os.environ.get("YUNALPHA_RUNTIME", Path(__file__).resolve().parents[1] / ".runtime")) / "tmp"
    scratch.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="fund-report-import-", dir=scratch) as tmp:
        for number, spec in enumerate(plan["sources"]):
            source = {"kind": spec["kind"], "name": spec["name"]}
            if spec["kind"] == "cloud":
                temp = Path(tmp) / str(number)
                temp.mkdir()
                root, remote = cloud_snapshot(spec, temp)
                rows, stats, collections = legacy_reports(root, source)
                archives.extend(collections)
                stats["remote_html_count"] = remote["html_count"]
                stats["remote_files_verified"] = len(remote["files"])
            else:
                root = Path(spec["root"]).resolve()
                if spec["kind"] == "agent":
                    rows, stats = agent_reports(root, source)
                else:
                    rows, stats, collections = legacy_reports(root, source)
                    archives.extend(collections)
            inventory.append({**source, **stats})
            all_rows.extend(rows)
        grouped = {}
        for row in all_rows:
            key = (row["fund"]["code"], report_digest(row))
            old = grouped.get(key)
            if old:
                old["sources"].extend(s for s in row["sources"] if s not in old["sources"])
                if row["status"] == "formal":
                    old["status"], old["status_reason"] = row["status"], row["status_reason"]
                for logical, value in row["files"].items():
                    old["files"].setdefault(logical, value)
                continue
            grouped[key] = row
        items = []
        for (_, digest), row in grouped.items():
            row["id"] = f"fund-{row['fund']['code'].split('.')[0]}-{digest[:16]}"
            row["version"] = digest[:16]
            files = {"report.html": row.pop("html"), **row.pop("files")}
            row["files"] = {name: store_blob(destination, value, Path(name).suffix) for name, value in files.items()}
            row["source"] = "、".join(dict.fromkeys(s["name"] for s in row["sources"]))
            items.append(row)
        archive_items = {}
        for row in archives:
            digest = row["html_sha256"]
            existing = archive_items.get(digest)
            if existing:
                existing["source"] += "、" + row["source"]
                continue
            row["id"] = f"collection-{digest[:16]}"
            row["files"] = {name: store_blob(destination, value, Path(name).suffix) for name, value in row["files"].items()}
            archive_items[digest] = row
    items.sort(key=lambda row: (row["date"] or "", row["generated_at"] or "", row["id"]), reverse=True)
    index = {"schema_version": 1, "items": items, "collections": list(archive_items.values()), "inventory": inventory,
             "summary": {"reports": len(items), "funds": len({r["fund"]["code"] for r in items}),
                         "statuses": dict(Counter(r["status"] for r in items)),
                         "candidates_before_deduplication": len(all_rows), "collection_archives": len(archive_items)},
             "source_plan": "sources.json"}
    reusable = {"sources": [{k: v for k, v in spec.items() if k != "key"} for spec in plan["sources"]]}
    (destination / "sources.json").write_text(json.dumps(reusable, ensure_ascii=False, indent=2) + "\n")
    staged = destination / "index.json.tmp"
    staged.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n")
    staged.replace(destination / "index.json")
    return index


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-manifest", type=Path, help="重用上次导入生成的 sources.json")
    parser.add_argument("--projects", type=Path, default=Path(__file__).resolve().parents[1] / "config/projects.json")
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--cloud-host")
    parser.add_argument("--cloud-key", type=Path)
    args = parser.parse_args()
    if args.source_manifest:
        plan = read_json(args.source_manifest)
        for source in plan["sources"]:
            if source["kind"] == "cloud":
                if not args.cloud_key:
                    parser.error("云端再导入需要 --cloud-key")
                source["key"] = str(args.cloud_key)
    else:
        projects = {item["id"]: item for item in read_json(args.projects)["projects"]}
        plan = {"sources": [
            {"kind": "legacy", "name": "本地深度报告", "root": projects["fund-report"]["local_path"]},
            {"kind": "agent", "name": "原基金研究Agent", "root": projects["fund-agent"]["local_path"]},
        ]}
        if args.cloud_host:
            if not args.cloud_key:
                parser.error("--cloud-host 需要 --cloud-key")
            plan["sources"].insert(0, {"kind": "cloud", "name": "云端深度报告", "host": args.cloud_host,
                                      "key": str(args.cloud_key), "remote_root": "/data/www/Mutual_Fund"})
    index = import_library(plan, args.destination)
    print(json.dumps({"summary": index["summary"], "inventory": index["inventory"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
