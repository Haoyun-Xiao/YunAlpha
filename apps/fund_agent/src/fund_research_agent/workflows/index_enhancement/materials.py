from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import httpx
import pymupdf
from bs4 import BeautifulSoup

from fund_research_agent.adapters.quarterly_reports import QuarterlyReportProvider
from fund_research_agent.paths import WriteBoundary, sha256_file

REQUIRED = (
    "fund_name",
    "fund_full_name",
    "fund_code",
    "found_date",
    "management",
    "custodian",
    "benchmark",
    "objective",
    "stock_range",
    "constituent_min",
    "cash_min",
    "tracking_deviation",
    "tracking_error",
    "management_fee_a",
    "custody_fee_a",
    "sales_fee_a",
    "manager",
    "manager_since",
)
PAGE_TERMS = (
    "业绩比较基准",
    "投资组合比例",
    "非现金基金资产",
    "日均跟踪",
    "量化增强",
    "量化模型",
    "黎海威",
    "管理费率",
    "托管费率",
    "增设Y",
    "增设C",
    "股指期货投资",
    "投资策略和运作分析",
    "股指期货交易情况",
    "销售服务费",
)


def normalized(text: str) -> str:
    return re.sub(r"\s+", "", text).replace("％", "%").replace("－", "-")


def write_json(boundary: WriteBoundary, path: Path, value) -> Path:
    return boundary.write_text(
        path, json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n"
    )


def lake_snapshot(lake_root: Path, code: str, as_of: date) -> dict:
    """Read Parquet in an in-memory connection; never open the production DuckDB."""
    with duckdb.connect(":memory:") as db:
        db.execute("SET threads=2")
        result = {}
        for table, group, field, clause in (
            ("fund_profiles", "curated", "symbol", ""),
            ("fund_manager_tenures", "curated", "fund_symbol", "AND begin_date <= ?"),
            (
                "fund_scale",
                "derived",
                "symbol",
                "AND report_period <= ? ORDER BY report_period DESC LIMIT 1",
            ),
        ):
            glob = str(lake_root / group / table / "**/*.parquet")
            params = [glob, code] + ([as_of] if clause else [])
            cursor = db.execute(
                f"SELECT * FROM read_parquet(?,union_by_name=true,hive_partitioning=false) "
                f"WHERE {field}=? {clause}",
                params,
            )
            result[table] = [
                dict(zip([x[0] for x in cursor.description], row, strict=True))
                for row in cursor.fetchall()
            ]
    if len(result["fund_profiles"]) != 1:
        raise ValueError("Part1要求数据湖中存在唯一基金身份")
    return json.loads(json.dumps(result, default=str, ensure_ascii=False))


def prepare_materials(
    root: Path,
    lake_root: Path,
    code: str,
    as_of: date,
    boundary: WriteBoundary,
    refresh: bool = False,
) -> dict:
    source_root = boundary.ensure_dir(root / "sources")
    manifest_path = source_root / "manifest.json"
    if manifest_path.exists() and not refresh:
        saved = json.loads(manifest_path.read_text())
        if saved["as_of"] != as_of.isoformat() or saved["fund_symbol"] != code:
            raise ValueError("同一运行目录不能切换基金或资料截止日")
        for item in saved["sources"]:
            if sha256_file(Path(item["text_path"])) != item["text_sha256"]:
                raise ValueError(f"来源文本已变化：{item['source_id']}；请显式刷新资料")
            if item.get("pdf_path") and sha256_file(Path(item["pdf_path"])) != item["sha256"]:
                raise ValueError(f"来源PDF已变化：{item['source_id']}")
        return saved

    # Same announcement endpoint used by the legacy acquisition path, extended to legal documents.
    plain = code.split(".")[0]
    with httpx.Client(
        timeout=35,
        follow_redirects=True,
        headers={
            "Referer": "https://fundf10.eastmoney.com/",
            "User-Agent": "Mozilla/5.0",
        },
    ) as client:
        response = client.get(
            "https://api.fund.eastmoney.com/f10/JJGG",
            params={
                "fundcode": plain,
                "pageIndex": 1,
                "pageSize": 100,
                "type": 0,
            },
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("ErrCode") != 0:
            raise ValueError("基金公告列表返回业务错误")
        write_json(boundary, source_root / "announcements.json", payload)
        rows = [row for row in payload["Data"] if row["PUBLISHDATE"][:10] <= as_of.isoformat()]
        selectors = {
            "summary": lambda t: "产品资料概要" in t,
            "prospectus": lambda t: "更新招募说明书" in t,
            "contract": lambda t: "基金合同更新" in t,
            "periodic": lambda t: (
                ("季度报告" in t or "中期报告" in t or "年度报告" in t)
                and "提示" not in t
                and "摘要" not in t
            ),
            "share_change": lambda t: "增设Y类基金份额" in t and "修改" in t,
        }
        selected = []
        for kind, predicate in selectors.items():
            candidates = sorted(
                (r for r in rows if predicate(r["TITLE"])),
                key=lambda r: r["PUBLISHDATE"],
                reverse=True,
            )
            if not candidates and kind in {"summary", "prospectus", "periodic"}:
                raise ValueError(f"公告列表没有找到截止日前的必需材料：{kind}")
            if candidates:
                selected.append((kind, candidates[0]))
        page_url = f"https://www.igwfmc.com/main/jjcp/product/{plain}/detail.html"
        website = {"url": page_url}
        try:
            r = client.get(page_url)
            website["http_status"] = r.status_code
            r.raise_for_status()
            soup = BeautifulSoup(r.content, "html.parser")
            for tag in soup(["script", "style"]):
                tag.decompose()
            boundary.write_text(source_root / "official-page.txt", soup.get_text("\n", strip=True))
            website["note"] = "动态产品页仅作发现参考，正式事实由带日期的公告PDF核验"
        except httpx.HTTPError as exc:
            website["note"] = f"官网访问失败（{type(exc).__name__}）；使用正式公告PDF"

    def download(item):
        kind, row = item
        pdf_path = source_root / f"{kind}-{row['ID']}.pdf"
        if refresh or not pdf_path.exists():
            boundary.write_bytes(pdf_path, QuarterlyReportProvider._download_pdf(row["ID"]))
        with pymupdf.open(pdf_path) as pdf:
            pages = [{"page": i + 1, "text": p.get_text()} for i, p in enumerate(pdf)]
        if not pages or sum(len(p["text"]) for p in pages) < 500:
            raise ValueError(f"PDF没有有效文本：{kind}")
        return kind, row, pdf_path, pages

    sources = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        for number, (kind, row, pdf_path, pages) in enumerate(pool.map(download, selected), 1):
            text_path = write_json(boundary, pdf_path.with_suffix(".pages.json"), pages)
            # Select complete relevant pages, never silently truncate within a page.
            if kind in {"summary", "share_change"}:
                chosen = pages
            else:
                ranked = sorted(
                    pages,
                    key=lambda p: sum(normalized(p["text"]).count(t) for t in PAGE_TERMS),
                    reverse=True,
                )
                chosen = sorted(
                    [p for p in ranked[:20] if any(t in normalized(p["text"]) for t in PAGE_TERMS)],
                    key=lambda p: p["page"],
                )
                if pages[0] not in chosen:
                    chosen = [pages[0], *chosen]
            sources.append(
                {
                    "source_id": f"S{number}",
                    "kind": kind,
                    "title": row["TITLE"],
                    "published_at": row["PUBLISHDATE"][:10],
                    "url": f"https://pdf.dfcfw.com/pdf/H2_{row['ID']}_1.pdf",
                    "origin": "基金公司正式公告（东方财富PDF载体）",
                    "pdf_path": str(pdf_path),
                    "sha256": sha256_file(pdf_path),
                    "text_path": str(text_path),
                    "text_sha256": sha256_file(text_path),
                    "total_pages": len(pages),
                    "selected_pages": [p["page"] for p in chosen],
                    "excerpts": chosen,
                }
            )
    manifest = {
        "fund_symbol": code,
        "as_of": as_of.isoformat(),
        "fetched_at": datetime.now(UTC).isoformat(),
        "official_page": website,
        "sources": sources,
    }
    write_json(boundary, manifest_path, manifest)
    return manifest


def validate_facts(extracted: dict, materials: dict, snapshot: dict) -> dict:
    sources = {s["source_id"]: s for s in materials["sources"]}
    facts = {}
    errors = []
    for item in extracted.get("facts", []):
        key = item.get("key", "")
        if key in facts:
            errors.append(f"duplicate:{key}")
            continue
        source = sources.get(item.get("source_id"))
        if not source:
            errors.append(f"unknown_source:{key}")
            continue
        # A quote may straddle two consecutive pages; source localization remains explicit.
        page = int(item.get("page", 0))
        all_pages = json.loads(Path(source["text_path"]).read_text())
        text = "".join(p["text"] for p in all_pages if page <= p["page"] <= page + 1)
        quote = normalized(str(item.get("quote", "")))
        if len(quote) < 2 or quote not in normalized(text):
            errors.append(f"quote_not_found:{key}:{source['source_id']}:{page}")
        value_digits = set(re.findall(r"\d+(?:\.\d+)?", str(item.get("value", ""))))
        if not value_digits <= set(re.findall(r"\d+(?:\.\d+)?", quote)):
            errors.append(f"number_not_in_quote:{key}")
        facts[key] = {**item, "published_at": source["published_at"], "source_uri": source["url"]}
    errors += [f"missing:{key}" for key in REQUIRED if not facts.get(key, {}).get("value")]
    if (
        "fund_code" in facts
        and facts["fund_code"]["value"] != materials["fund_symbol"].split(".")[0]
    ):
        errors.append("fund_identity_mismatch")
    benchmark = normalized(facts.get("benchmark", {}).get("value", ""))
    if not all(t in benchmark for t in ["95%", "1.5%", "年收益", "期间折算", "沪深300"]):
        errors.append("benchmark_contract_changed")
    for key, terms in {
        "stock_range": ["90", "95", "基金资产"],
        "constituent_min": ["80", "非现金基金资产"],
        "cash_min": ["5", "基金资产净值", "保证金"],
    }.items():
        if not all(t in normalized(facts.get(key, {}).get("value", "")) for t in terms):
            errors.append(f"constraint_denominator:{key}")
    profile = snapshot["fund_profiles"][0]
    if "沪深300" not in profile["name"] or profile["symbol"] != materials["fund_symbol"]:
        errors.append("lake_identity_mismatch")
    return {
        "valid": not errors,
        "errors": errors,
        "facts": facts,
        "conflicts": extracted.get("conflicts", []),
        "missing": extracted.get("missing", []),
    }
