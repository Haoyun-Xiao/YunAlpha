"""Package public YunAlpha pages from a local running app and approved report library."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import shutil
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import urlopen

PREFIX = "/YunAlpha/"
SEVEN = "/workspaces/vic-desks"
CROWDING = "/workspaces/crowding"
REPORT = "/api/v1/research/content/"
REPORT_ASSET_TYPES = {".css", ".js", ".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif", ".woff", ".woff2"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-url", default="http://127.0.0.1:19100")
    parser.add_argument("--library", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    output = args.output
    shutil.copytree(root / "apps/web/dist", output, dirs_exist_ok=args.resume)

    def fetch(path):
        with urlopen(args.local_url + quote(path, safe="/?=&%+,:-"), timeout=90) as response:
            return response.read()

    def read(path):
        return json.loads(fetch(path))

    def put(path, data):
        if not isinstance(data, bytes):
            data = json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
        item = output / path.lstrip("/")
        if not item.resolve().is_relative_to(output.resolve()):
            raise ValueError("Output path outside release directory")
        item.parent.mkdir(parents=True, exist_ok=True)
        item.write_bytes(data)

    def sample(value):
        """Bound bulk tables while keeping chart series and sourced aggregate results."""
        if isinstance(value, dict):
            return {
                key: sample(rows[:250] if key == "rows" and isinstance(rows, list)
                            and value.get("kind") not in {"line", "bar", "area", "scatter"} else rows)
                for key, rows in value.items()
                if key not in {"file_path", "source_path", "local_path", "raw_path"}
            }
        if isinstance(value, list):
            return [sample(item) for item in value]
        if isinstance(value, str) and re.search(r"/(?:Users/|home/|data/www/|private/var/)", value):
            return "本地归档"
        return value

    def capture(path, output_path=None):
        destination = output_path or urlsplit(path).path
        if not args.resume or not (output / destination.lstrip("/")).is_file():
            put(destination, sample(read(path)))

    def desk_html(folder, message):
        path = folder / "index.html"
        html = path.read_text()
        strip = (
            '<div style="padding:9px 16px;background:#edf3fa;color:#254668;font:13px system-ui">'
            f'<a href="{PREFIX}">返回 YunAlpha</a>　{message}</div>'
        )
        path.write_text(html.replace("<body>", "<body>" + strip, 1))

    desk_src = root / "apps/vic_desks"
    seven_out = output / SEVEN.lstrip("/")
    crowd_out = output / CROWDING.lstrip("/")
    shutil.copytree(desk_src / "seven_desks/web", seven_out, dirs_exist_ok=args.resume)
    shutil.copytree(desk_src / "crowding/web", crowd_out, dirs_exist_ok=args.resume)
    desk_html(seven_out, "公开快照 · 默认观察日及部分明细，完整筛选与下钻见本机版。")
    desk_html(crowd_out, "公开快照 · 可切换六个页签，个股透视提供平安银行样例。")
    # Snapshots accept the single exported observation, and local search/sort remains available
    # wherever the underlying view already operates on browser data.
    js = seven_out / "app.js"
    source = js.read_text()
    source = source.replace(
        "function renderControls(controls){",
        "function renderControls(controls){controls=[];",
    )
    source = source.replace("'数据服务在线'","'公开快照'")
    js.write_text(source)

    version = read(SEVEN + "/api/dashboards/health")
    put(SEVEN + "/api/dashboards/health", version)
    boards = read(SEVEN + "/api/dashboards")
    jobs = []
    for board in boards["boards"]:
        path = f"{SEVEN}/api/dashboards/{board['id']}"
        meta = read(path + "/metadata")
        put(path + "/metadata", meta)
        params = urlencode({"data_version": version["data_version"], "date": meta["defaults"]["date"]})
        jobs.extend((f"{path}/{tab['id']}?{params}", None) for tab in meta["tabs"])

    health = read(CROWDING + "/api/crowding/health")
    put(CROWDING + "/api/crowding/health", health)
    prefix = CROWDING + "/api/dashboards/fund_crowding/"
    for panel in ["overview", "stocks", "stocks/000001.SZ", "funds", "industries", "crowd_index"]:
        params = urlencode({"start": "hp", "data_version": health["active_version"]})
        destination = prefix + panel + "/index.json" if panel in {"stocks", "stocks/000001.SZ"} else None
        jobs.append((f"{prefix}{panel}?{params}", destination))
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda job: capture(*job), jobs))
    crowd_js = crowd_out / "dashboard.js"
    source = crowd_js.read_text().replace(
        "params.get('code') || ''", "params.get('code') || '000001.SZ'",
    ).replace(
        "const response = await fetch(key,",
        "const path = new URL(key, location.href).pathname;\n"
        "  const snapshot = /\\/stocks(?:\\/[^/]+)?$/.test(path) ? path+'/index.json' : path;\n"
        "  const response = await fetch(snapshot,",
    )
    # Keep all tabs and the fixed example. Suppress server-driven filters for this static release.
    crowd_js.write_text(source.replace("function urlFor(page) {", "function urlFor(page) {\n  state.start='hp';"))

    put("/api/v1/research/modules", read("/api/v1/research/modules"))
    put("/api/v1/strategy-catalog.json", read("/api/v1/strategies"))
    put("/api/v1/strategies/dividend-lowvol", fetch("/api/v1/strategies/dividend-lowvol"))
    library = json.loads((args.library / "index.json").read_text())
    cloud_reports = {row["id"]: row for row in library["items"] if row["status"] == "formal"
                     and any(source["kind"] == "cloud" for source in row["sources"])}
    reports = read("/api/v1/research/reports")
    reports["items"] = [r for r in reports["items"] if r["id"] in cloud_reports]
    put("/api/v1/research/reports", reports)

    def publish_report(row):
        path = REPORT + row["id"] + "/"
        html = fetch(path + "report.html").decode()
        html = html.replace(REPORT, PREFIX.rstrip("/") + REPORT)
        html = re.sub(r'<a\b[^>]*href=["\']report\.docx["\'][^>]*>[\s\S]*?</a>', "", html, flags=re.I)
        put(path + "report.html", html.encode())
        for resource in row["files"]:
            if resource.startswith("assets/") and Path(resource).suffix.lower() in REPORT_ASSET_TYPES:
                if not re.search(r"sidebar", resource, re.I) and resource in html:
                    put(path + resource, fetch(path + resource))

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(publish_report, [cloud_reports[item["id"]] for item in reports["items"]]))
    print(f"Published {len(jobs)} desk panels and {len(reports['items'])} public fund reports.")


if __name__ == "__main__":
    main()
