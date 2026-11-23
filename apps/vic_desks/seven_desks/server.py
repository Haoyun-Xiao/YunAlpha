"""Read-only local API and frontend for seven desks; no remote fallback or ingestion."""

from __future__ import annotations
import argparse
import gzip
import hashlib
import importlib
import inspect
import json
import mimetypes
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs, unquote
from seven_desks.store import Store, create_version, CACHE, PROJECT, clean, version_root, code_hashes
from seven_desks.settings import CUTOFF, FROZEN_VERSION
from seven_desks.daily import annotate, cutoff_for

BOARDS = {
    "national_team": "owners",
    "fund_holdings": "owners",
    "capital_flow": "owners",
    "contrarian": "contrarian",
    "chips": "market",
    "sentiment": "market",
    "valuation": "market",
}
TITLES = {
    "national_team": "国家队全景",
    "contrarian": "逆向投资",
    "sentiment": "情绪温度计",
    "chips": "全A筹码结构",
    "valuation": "市场估值",
    "fund_holdings": "A股公募持仓",
    "capital_flow": "A股资金流",
}
WEB = PROJECT / "seven_desks/web"
ACTIVE = None
PINNED_VERSION = None
LOCK = threading.RLock()
STORES = {}
QUERY_SLOTS = threading.BoundedSemaphore(4)
PROCESS_CODE = code_hashes()
BROWSER_PREFIX = "/workspaces/vic-desks"
MIGRATION = PROJECT / "evidence/seven/migration-runtime.json"


def active_version():
    return PINNED_VERSION or ACTIVE or FROZEN_VERSION


def adapter(board):
    return importlib.import_module("seven_desks.adapters." + BOARDS[board])


def metadata(board, version=None):
    selected = get_store(version) if version else None
    target = (
        version_root(version) / "metadata" / f"{board}.json" if version else None
    )
    if target and target.exists():
        return json.loads(target.read_text())
    if version:
        verify_algorithm(version)
    function = adapter(board).metadata
    if "store" in inspect.signature(function).parameters and version:
        result = function(board, store=get_store(version))
    else:
        result = function(board)
    if version:
        selected = get_store(version)
        if board == "valuation" and selected.valuation_cutoff != CUTOFF:
            dates = selected.read("market/valuation/temperature.parquet", columns=["trade_date"])
            result["defaults"]["date"] = result["as_of"] = selected.valuation_cutoff
            for control in result["controls"]:
                if control["key"] == "date":
                    control["value"] = selected.valuation_cutoff
        else:
            dates = selected.read("market/sentiment/daily.parquet", columns=["trade_date"])
        cutoff = cutoff_for(selected, board, selected.valuation_cutoff if board == "valuation" else CUTOFF)
        if selected.daily_boards and cutoff > (selected.valuation_cutoff if board == "valuation" else CUTOFF):
            result["defaults"]["date"] = cutoff
            for control in result.get("controls", ()):
                if control["key"] == "date":
                    control["value"] = cutoff
            dates += selected.read("revision/daily/stock-quotes.parquet", columns=["trade_date"],
                                   where="trade_date<=?", params=[cutoff])
        result["calendar_dates"] = sorted({str(r["trade_date"])[:10] for r in dates})
    if version:
        target = CACHE / "versions" / version / "metadata" / f"{board}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(f".{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(clean(result), ensure_ascii=False, allow_nan=False))
        tmp.replace(target)
    return result


def get_store(v):
    if not isinstance(v, str) or not __import__("re").fullmatch(r"[a-f0-9]{16}", v):
        raise ValueError("invalid data_version")
    if not (version_root(v) / "manifest.json").exists():
        raise ValueError("unknown data_version")
    with LOCK:
        if v not in STORES:
            STORES[v] = Store(v)
        return STORES[v]


def verify_algorithm(v):
    manifest_file = version_root(v) / "manifest.json"
    manifest = json.loads(manifest_file.read_bytes())
    if v == FROZEN_VERSION:
        boundary = json.loads(MIGRATION.read_text())
        if hashlib.sha256(manifest_file.read_bytes()).hexdigest() != boundary["source_manifest_sha256"]:
            raise ValueError("冻结快照发布清单已变化")
        original = dict(manifest["code_hashes"])
        release = json.loads((PROJECT / "evidence/seven/source-release.json").read_text())
        if any(release["source_hashes"].get(path) != digest for path, digest in original.items()):
            raise ValueError("冻结快照与原发布源码不匹配")
        expected = boundary["runtime_hashes"]
        if code_hashes() != expected:
            # Scoped valuation update: existing frozen tables are supported by the
            # same legacy branches, with every other runtime hash still enforced.
            compat = PROJECT / "evidence/phase3-valuation/frozen-runtime-hashes.json"
            expected = {**expected, **json.loads(compat.read_text())}
    else:
        expected = dict(manifest["code_hashes"])
    current = code_hashes()
    # The immediately preceding release predates these optional overlays.
    # Its exact known code boundary and unmodified data remain selectable.
    previous = PROJECT / "evidence/desks-daily/previous-runtime-hashes.json"
    if v in (FROZEN_VERSION, "308617544109caa5") and not manifest.get("daily_outputs") and previous.is_file():
        if expected == json.loads(previous.read_text()):
            expected = current
    if current != expected or any(PROCESS_CODE.get(p) != digest for p, digest in expected.items()):
        raise ValueError("所选快照的运行计算边界已变化；请构建并验证新版本")


def response(board, panel, params):
    params = dict(params)
    v = params.get("data_version") or active_version()
    if not __import__("re").fullmatch(r"[a-f0-9]{16}", v):
        raise ValueError("invalid data_version")
    s = get_store(v)
    cutoff = cutoff_for(s, board, s.valuation_cutoff if board == "valuation" else CUTOFF)
    params.setdefault("date", cutoff)
    key = hashlib.sha256(
        json.dumps([board, panel, params], sort_keys=True).encode()
    ).hexdigest()
    frozen_page = version_root(v) / "pages" / f"{key}.json"
    if frozen_page.exists():
        return frozen_page.read_bytes()
    cache = CACHE / "versions" / v / "pages" / f"{key}.json"
    if cache.exists():
        verify_algorithm(v)
        return cache.read_bytes()
    verify_algorithm(v)
    t = time.monotonic()
    with QUERY_SLOTS:
        if cache.exists():
            return cache.read_bytes()
        result = adapter(board).panel(board, panel, params, s)
        if board == "capital_flow":
            from seven_desks.adapters.market import capital_additions
            result = capital_additions(panel, params, s, result)
    annotate(s, board, result)
    result.update(
        {
            "data_version": v,
            "cutoff": cutoff,
            "origin": "local_readonly_snapshot",
            "duration_ms": round((time.monotonic() - t) * 1000, 1),
        }
    )
    data = json.dumps(clean(result), ensure_ascii=False, allow_nan=False).encode()
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_suffix(f".{threading.get_ident()}.tmp")
    tmp.write_bytes(data)
    tmp.replace(cache)
    return data


class Handler(BaseHTTPRequestHandler):
    def send(self, code, data, ctype="application/json; charset=utf-8", cache=False):
        if not isinstance(data, bytes):
            data = json.dumps(clean(data), ensure_ascii=False, allow_nan=False).encode()
        etag = '"' + hashlib.sha256(data).hexdigest() + '"'
        if code == 200 and self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.end_headers()
            return
        compressed = (
            "gzip" in self.headers.get("Accept-Encoding", "") and len(data) > 1500
        )
        if compressed:
            data = gzip.compress(data)
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("ETag", etag)
        self.send_header(
            "Cache-Control", "private, max-age=60" if cache else "no-cache"
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        if compressed:
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlsplit(self.path)
        p = unquote(u.path)
        if p == BROWSER_PREFIX:
            self.send_response(308)
            self.send_header("Location", BROWSER_PREFIX + "/" + ("?" + u.query if u.query else ""))
            self.end_headers()
            return
        if p.startswith(BROWSER_PREFIX + "/"):
            p = p.removeprefix(BROWSER_PREFIX)
        params = {k: v[-1] for k, v in parse_qs(u.query).items()}
        try:
            if p == "/api/dashboards/health":
                return self.send(
                    200,
                    {
                        "status": "ready",
                        "data_version": active_version(),
                        "cutoff": "2026-11-09",
                        "valuation_cutoff": get_store(active_version()).valuation_cutoff,
                        "boards": list(BOARDS),
                        "collection_enabled": False,
                        "source": "local_snapshot",
                    },
                )
            if p == "/api/dashboards":
                return self.send(
                    200,
                    {
                        "data_version": active_version(),
                        "boards": [{"id": k, "title": v} for k, v in TITLES.items()],
                    },
                )
            if p.startswith("/api/dashboards/"):
                chunks = p.split("/")
                board = chunks[3]
                if board not in BOARDS:
                    return self.send(404, {"error": "unknown_board"})
                if len(chunks) < 5:
                    return self.send(404, {"error": "missing_panel"})
                panel = chunks[4]
                selected = params.get("data_version") or active_version()
                store = get_store(selected)
                cutoff = cutoff_for(store, board, store.valuation_cutoff if board == "valuation" else CUTOFF)
                if panel == "metadata":
                    r = metadata(board, selected)
                    r.update(
                        {
                            "board": board,
                            "data_version": params.get("data_version")
                            or active_version(),
                            "cutoff": cutoff,
                        }
                    )
                    return self.send(200, r)
                if panel == "entities":
                    if len(chunks) < 6:
                        return self.send(400, {"error": "entity_required"})
                    params["entity_id"] = chunks[5]
                    params["code"] = chunks[5]
                    panel = "entity"
                if params.get("date", params.get("trade_date", cutoff)) > cutoff:
                    return self.send(
                        200,
                        {
                            "title": "日期超出快照范围",
                            "sections": [
                                {
                                    "id": "date",
                                    "title": "暂无该日期快照",
                                    "kind": "table",
                                    "rows": [],
                                    "missing_reason": f"本地验收数据截止{cutoff}，请选择更早日期。",
                                }
                            ],
                            "data_version": selected,
                            "data_date": cutoff,
                        },
                    )
                return self.send(200, response(board, panel, params))
            file = WEB / (
                "index.html"
                if p in ("", "/") or p.startswith("/dashboard/")
                else p.lstrip("/")
            )
            if not file.resolve().is_relative_to(WEB.resolve()) or not file.is_file():
                return self.send(404, {"error": "not_found"})
            return self.send(
                200,
                file.read_bytes(),
                mimetypes.guess_type(str(file))[0] or "application/octet-stream",
                True,
            )
        except (BrokenPipeError, ConnectionResetError):
            return
        except (ValueError, KeyError) as e:
            return self.send(400, {"error": "invalid_request", "message": str(e)})
        except Exception as e:
            return self.send(
                503,
                {
                    "error": "data_unavailable",
                    "message": str(e),
                    "data_version": active_version(),
                    "retryable": False,
                },
            )

    def log_message(self, fmt, *args):
        print(fmt % args, flush=True)


def main():
    global ACTIVE, PINNED_VERSION
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=19221)
    ap.add_argument("--version", help="Serve one already built candidate without changing active pointer")
    ap.add_argument("--no-publish", action="store_true", help="Build and validate without switching active pointer")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--base-version", default=FROZEN_VERSION, help="Build from a frozen version, not live data packages")
    ap.add_argument("--build-only", action="store_true")
    ap.add_argument("--prewarm", action="store_true")
    args = ap.parse_args()
    if not 1 <= args.port <= 65535:
        ap.error("端口必须在 1–65535 内")
    CACHE.mkdir(parents=True, exist_ok=True)
    previous = FROZEN_VERSION
    active = CACHE / "active.json"
    candidate = args.version or (create_version(args.base_version) if args.refresh else FROZEN_VERSION)
    get_store(candidate)
    verify_algorithm(candidate)
    if args.version:
        get_store(args.version)
        PINNED_VERSION = args.version
    if args.refresh:
        from seven_desks.prepare import build
        build(candidate)
    ACTIVE = candidate
    errors = []
    if args.prewarm or args.refresh or previous is None:
        for b in BOARDS:
            for t in metadata(b, candidate)["tabs"]:
                try:
                    payload = json.loads(
                        response(
                            b,
                            t["id"],
                            {"date": "2026-11-09", "data_version": candidate},
                        )
                    )
                    if not payload.get("sections"):
                        raise ValueError("panel has no sections")
                    print("warmed", b, t["id"], flush=True)
                except Exception as e:
                    errors.append({"board": b, "panel": t["id"], "error": str(e)})
        (CACHE / "versions" / candidate / "build-validation.json").write_text(
            json.dumps(
                {"errors": errors, "valid": not errors}, ensure_ascii=False, indent=2
            )
        )
    if errors:
        ACTIVE = previous
        raise RuntimeError(
            "Candidate validation failed; active version unchanged: " + str(errors)
        )
    if candidate != previous and not args.no_publish and not args.version:
        tmp = active.with_suffix(".tmp")
        tmp.write_text(json.dumps({"data_version": candidate}))
        tmp.replace(active)
    if args.build_only:
        print("validated_version=" + candidate + (" unpublished" if args.no_publish else " published"), flush=True)
        return
    print(f"Seven desks http://127.0.0.1:{args.port} version={ACTIVE}", flush=True)
    with ThreadingHTTPServer(("127.0.0.1", args.port), Handler) as service:
        service.serve_forever()


if __name__ == "__main__":
    main()
