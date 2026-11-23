from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Literal, TypedDict

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import AIMessageChunk, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from pydantic import Field, create_model

from . import prompts
from .contracts import (
    CandidateReview,
    ChainSpec,
    CompanyAssessment,
    CompanyDiscovery,
    ResearchClaim,
    ResearchPriority,
    RiskReview,
    ScoreItem,
    SegmentResearch,
    Synthesis,
)
from .harness import agent
from .lake import LakeRepository
from .llm import ModelService, _message_text
from .render import render_html, render_markdown
from .sources import CONTENT_VERSION, SourceService
from .store import Store, digest, encode, now

DIMENSIONS = ["景气度", "价值池厚度", "卡位强度", "国产替代弹性", "估值性价比", "拥挤度反向"]
WEIGHTS = [0.20, 0.20, 0.25, 0.15, 0.10, 0.10]
SNAPSHOT_VERSION = "mapped-candidates-targeted-sources-v6-current-valuation"
METRICS = [
    "close",
    "pe_ttm",
    "pb",
    "ps_ttm",
    "total_mv",
    "float_mv",
    "revenue",
    "total_revenue",
    "revenue_yoy",
    "net_profit",
    "net_profit_yoy",
    "gross_margin",
    "roe",
    "net_cash_operate",
    "total_assets",
    "total_equity",
    "total_liabilities",
]
METRICS += ["pe_percentile_3y", "pb_percentile_3y", "ytd_return_hfq"]


def pack_summary(pack):
    segments = pack.get("business_segments", [])
    latest = max((str(r.get("report_period", "")) for r in segments), default="")
    return {
        k: pack.get(k) for k in ["symbol", "name", "as_of", "warnings", "source_policy", "identity"]
    } | {
        "metrics": {k: pack["metrics"][k] for k in METRICS if k in pack["metrics"]},
        "business_segments": [r for r in segments if str(r.get("report_period", "")) == latest][
            :30
        ],
        "input_hash": digest(pack),
        "view_policy": "核心财务与最新业务分部摘要；完整历史及输入保存在冻结快照，不是空值填零",
    }


class ResearchState(TypedDict):
    job_id: str
    artifacts: dict


class FinishAfterNoProgress(AgentMiddleware):
    """Stop repeated retrieval, not long or token-heavy productive research."""

    def __init__(self, activity):
        self.activity = activity

    async def awrap_model_call(self, request, handler):
        repeats = self.activity["duplicate_reads"] >= 2
        calls = [
            call
            for message in request.messages[-20:]
            for call in getattr(message, "tool_calls", [])
            if call["name"] in {"read_file", "grep", "read_segment_data"}
        ]
        if len(calls) >= 3:
            signatures = [encode([c["name"], c["args"]]) for c in calls[-3:]]
            repeats = repeats or len(set(signatures)) == 1
        if repeats:
            # The response-format tool is added by LangChain after this middleware;
            # removing retrieval tools leaves only structured final submission.
            prompt = (
                _message_text(request.system_message)
                + "\n已确认连续重复读取相同内容，没有新增证据。现在停止检索，基于已读资料提交最终SegmentResearch；未能证实的内容列为gaps，不编造。"
            )
            request = request.override(tools=[], system_message=SystemMessage(content=prompt))
        return await handler(request)


def scorecard(segments):
    rows = []
    for segment in segments:
        lookup = {x["dimension"]: x for x in segment.get("scores", [])}
        cells = [
            lookup.get(name, {"score": None, "note": "无足够证据", "evidence_ids": []})
            for name in DIMENSIONS
        ]
        # No invented neutral score and no redistribution of missing weight.
        total = (
            round(sum(c["score"] * w for c, w in zip(cells, WEIGHTS, strict=True)), 2)
            if all(c.get("score") is not None and c.get("evidence_ids") for c in cells)
            else None
        )
        rows.append({"segment": segment["segment"], "cells": cells, "total": total})
    return {
        "cols": [{"label": n, "weight": w} for n, w in zip(DIMENSIONS, WEIGHTS, strict=True)],
        "rows": rows,
        "version": prompts.VERSION,
    }


def check_evidence(value, known):
    """Reject fabricated IDs at every structured boundary, including nested claims."""
    if isinstance(value, dict):
        unknown = set(value.get("evidence_ids", [])) - known
        if unknown:
            raise ValueError(f"unknown_evidence_ids:{sorted(unknown)[:3]}")
        if (
            value.get("fact_type") == "source_fact"
            and not value.get("evidence_ids")
            and "content" not in value
        ):
            raise ValueError("source_fact_requires_evidence")
        for item in value.values():
            check_evidence(item, known)
    elif isinstance(value, list):
        for item in value:
            check_evidence(item, known)


def cited_schemas(known, allowed_symbols=None):
    """Make citations a constrained framework schema, not a post-hoc fuzzy guess.

    Deep Agents' ToolStrategy can correct invalid IDs inside the same expert
    turn, without repeating all source reading or the entire report.
    """
    evidence_id = Literal.__getitem__(tuple(sorted(known)))
    cited = (list[evidence_id], Field(default_factory=list))
    claim = create_model("CitedClaim", __base__=ResearchClaim, evidence_ids=cited)
    company_fields = {"evidence_ids": cited}
    if allowed_symbols:
        company_fields["symbol"] = (Literal.__getitem__(tuple(sorted(allowed_symbols))), ...)
    company = create_model("CitedCompany", __base__=CompanyAssessment, **company_fields)
    score = create_model("CitedScore", __base__=ScoreItem, evidence_ids=cited)
    segment = create_model(
        "SegmentResearch",
        __base__=SegmentResearch,
        findings=(list[claim], ...),
        companies=(
            list[company],
            Field(
                default_factory=list,
                **(
                    {"max_length": 0} if allowed_symbols is not None and not allowed_symbols else {}
                ),
            ),
        ),
        scores=(list[score], Field(default_factory=list)),
    )
    risk = create_model("RiskReview", __base__=RiskReview, risks=(list[claim], ...))
    synthesis = create_model("Synthesis", __base__=Synthesis, value_pool_items=(list[claim], ...))
    priority = create_model("CitedPriority", __base__=ResearchPriority, evidence_ids=cited)
    synthesis = create_model(
        "Synthesis",
        __base__=synthesis,
        research_priorities=(list[priority], Field(default_factory=list, max_length=5)),
    )
    return segment, risk, synthesis


def verified_candidates(review, candidates, docs):
    """Only exact, supported candidate decisions can enter frozen company packs."""
    sources = {d["evidence_id"]: d["content"] for d in docs}
    expected = {(node, c["symbol"]) for node, rows in candidates.items() for c in rows}
    seen = set()
    accepted = {node: [] for node in candidates}
    audit = []
    for decision in review["decisions"]:
        key = (decision["node_id"], decision["symbol"])
        if key not in expected or key in seen:
            raise ValueError("candidate_review_identity_mismatch")
        seen.add(key)
        candidate = next(c for c in candidates[key[0]] if c["symbol"] == key[1])
        source_id, quote = decision.get("source_id", ""), decision.get("support_quote", "").strip()
        content = (
            (candidate.get("main_business") or candidate.get("business_evidence") or "")
            if source_id == "business:" + key[1]
            else sources.get(source_id, "")
        )
        supported = quote_in_source(quote, content or "")
        item = {**decision, "quote_verified": supported}
        if decision["relevance"] in {"direct", "indirect"} and supported:
            accepted[key[0]].append({**candidate, "mapping": item})
        else:
            if decision["relevance"] in {"direct", "indirect"}:
                item["relevance"] = "unconfirmed"
                item["reason"] += "；引用原文未通过逐字核对，未进入量化比较"
        audit.append(item)
    if seen != expected:
        raise ValueError("candidate_review_incomplete")
    return accepted, audit


def quote_in_source(quote, content):
    # Formatting removal only; no semantic/fuzzy match. An explicit ellipsis
    # can join verbatim fragments, which must occur in source order.
    def plain(text):
        text = re.sub(r"!?\[([^\]]*)\]\([^\n]*?\)", r"\1", text)
        return re.sub(r"[\s*_`#]+", "", text)

    source = plain(content)
    fragments = [plain(x) for x in re.split(r"…+|\.{3,}", quote)]
    fragments = [x for x in fragments if x]
    if not fragments or any(len(x) < 8 for x in fragments):
        return False
    offset = 0
    for fragment in fragments:
        found = source.find(fragment, offset)
        if found < 0:
            return False
        offset = found + len(fragment)
    return True


def mapping_materials(docs, candidates):
    names = {c.get("name", "") for rows in candidates.values() for c in rows} - {""}
    output = []
    for d in docs:
        text = re.sub(r"!?\[([^\]]*)\]\([^\n]*?\)", r"\1", d.get("content", ""))
        windows = []
        for name in sorted(names, key=len, reverse=True):
            for match in list(re.finditer(re.escape(name), text))[:3]:
                windows.append((max(0, match.start() - 150), min(len(text), match.end() + 650)))
        windows.sort()
        merged = []
        for start, end in windows:
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        output.append(
            {
                "evidence_id": d["evidence_id"],
                "title": d.get("title"),
                "excerpt": "\n[原文间隔]\n".join(text[a:b] for a, b in merged)[:14000]
                if merged
                else text[:1500],
            }
        )
    return output


def scoped_documents(node, snapshot, docs):
    candidates = snapshot["candidates"][node["id"]]
    source_ids = set(
        node.get("evidence_ids", []) + snapshot.get("segment_source_ids", {}).get(node["id"], [])
    )
    source_ids.update(c.get("mapping", {}).get("source_id") for c in candidates)
    symbols = {c["symbol"] for c in candidates}
    relevant = [
        d
        for d in docs
        if d["evidence_id"] in source_ids
        or d.get("source_type") == "lake_snapshot"
        and any(sym in d["content"] for sym in symbols)
    ]
    return relevant or [d for d in docs if d.get("source_type") != "lake_snapshot"][:3]


def normalize_citations(value, known):
    """Expand a unique, explicitly ellipsized hash prefix; ambiguous ones remain unknown.

    This only restores display aliases of existing frozen sources. It never
    creates a source or treats a matching prefix as proof a claim is supported.
    """
    aliases = {}

    def string(text):
        def replace(match):
            prefix = match.group(1)
            found = [eid for eid in known if eid.startswith(prefix)]
            if len(found) == 1:
                aliases[match.group(0)] = found[0]
                return found[0]
            return match.group(0)

        text = re.sub(
            r"(?<![0-9A-Za-z_])((?:lake_)?[0-9a-f]{6,})(?:…|\.{3})(?![0-9A-Za-z_])", replace, text
        )

        def bracket(match):
            def token(item):
                prefix = item.group(0)
                if prefix in known:
                    return prefix
                found = [eid for eid in known if eid.startswith(prefix)]
                if len(found) == 1:
                    aliases[prefix] = found[0]
                    return found[0]
                return prefix

            return (
                "["
                + re.sub(
                    r"(?<![0-9A-Za-z_])(?:lake_)?[0-9a-f]{6,}(?![0-9A-Za-z_])",
                    token,
                    match.group(1),
                )
                + "]"
            )

        # Bare abbreviations are permitted only inside explicit citation
        # brackets and only when their existing source identity is unique.
        return re.sub(r"\[([^\]\n]+)\]", bracket, text)

    def walk(item):
        if isinstance(item, str):
            return string(item)
        if isinstance(item, list):
            return [walk(x) for x in item]
        if isinstance(item, dict):
            return {k: walk(v) for k, v in item.items()}
        return item

    return walk(value), aliases


def excerpts(docs, chars=6000):
    return [
        {k: d.get(k) for k in ["evidence_id", "title", "url", "published_at", "quality_flags"]}
        | {
            "excerpt": d.get("content", "")[:chars],
            "excerpt_truncated": len(d.get("content", "")) > chars,
        }
        for d in docs
    ]


def segment_dossier(context):
    # Offloaded tool results must have physical lines. A single giant JSON line
    # makes read_file/grep return the same truncated line repeatedly.
    header = {k: v for k, v in context.items() if k != "documents"}
    pieces = ["# 冻结环节输入", json.dumps(header, ensure_ascii=False, indent=2, allow_nan=False)]
    for d in context["documents"]:
        pieces.extend(
            [
                "\n## 来源 " + d["evidence_id"],
                json.dumps({k: v for k, v in d.items() if k != "excerpt"}, ensure_ascii=False),
                d["excerpt"],
            ]
        )
    return "\n".join(pieces)


class ResearchWorkflow:
    def __init__(self, settings, store=None, model=None, sources=None, lake=None):
        self.settings = settings
        self.store = store or Store(settings)
        self.model = model or ModelService(settings)
        self.model_config = self.model.fingerprint() if isinstance(self.model, ModelService) else {}
        self.sources = sources or SourceService(settings)
        self.lake = lake or LakeRepository(settings.lake_root)

    def emit(self, jid, kind, **data):
        j = self.store.job(jid)
        self.store.emit(jid, j["session_id"], kind, {"job_id": jid, **data})

    def checkpoint(self, jid, name, inputs):
        prompt = (
            prompts.SEGMENT
            if name.startswith("segment_")
            else {
                "structure": prompts.STRUCTURE,
                "review": prompts.RISK,
                "synthesis": prompts.SYNTHESIS,
            }.get(name, "")
        )
        fingerprint = digest(
            [prompts.VERSION, prompt, self.settings.base_url, self.model_config, inputs]
        )
        old = self.store.step(jid, name, fingerprint)
        if old:
            path = self.store.path(old["path"])
            if path.exists() and digest(json.loads(path.read_text())) == old["hash"]:
                return fingerprint, json.loads(path.read_text())
        return fingerprint, None

    def save(self, jid, name, fingerprint, data):
        relative = f"jobs/{jid}/checkpoints/{name}.json"
        self.store.write_json(relative, data)
        self.store.save_step(
            jid,
            name,
            fingerprint,
            "completed",
            {"path": relative, "hash": digest(data), "finished_at": now()},
        )
        return data

    async def stage(self, jid, name, progress, inputs, execute):
        if self.store.job(jid)["cancel_requested"]:
            raise asyncio.CancelledError()
        fingerprint, old = self.checkpoint(jid, name, inputs)
        self.store.update_job(jid, stage=name, progress=progress, live={})
        self.emit(jid, "stage_started", stage=name, cached=old is not None)
        if old is not None:
            self.emit(jid, "stage_finished", stage=name, cached=True)
            return old
        self.store.save_step(jid, name, fingerprint, "running", {"started_at": now()})
        started = time.monotonic()
        try:
            result = await execute()
            self.save(jid, name, fingerprint, result)
            self.emit(
                jid,
                "stage_finished",
                stage=name,
                duration_seconds=round(time.monotonic() - started, 2),
            )
            return result
        except Exception as exc:
            self.store.save_step(
                jid, name, fingerprint, "failed", {"error": str(exc), "type": type(exc).__name__}
            )
            raise

    def progress(self, jid, operation):
        last_emit = 0.0

        async def callback(data):
            nonlocal last_emit
            tick = time.monotonic()
            if tick - last_emit > 2 or data.get("phase") in {"completed", "failed"}:
                self.store.update_job(jid, live={**data, "operation": operation})
                self.emit(jid, "model_progress", **{**data, "operation": operation})
                last_emit = tick
                if self.store.job(jid)["cancel_requested"]:
                    raise asyncio.CancelledError()

        return callback

    def load(self, jid, name):
        return json.loads(self.store.path(f"jobs/{jid}/checkpoints/{name}.json").read_text())

    def documents(self, jid):
        snapshot = self.load(jid, "snapshot")
        return list(
            {
                d["evidence_id"]: d
                for d in self.load(jid, "sources")["documents"]
                + snapshot.get("segment_documents", [])
                + snapshot["data_documents"]
            }.values()
        )

    async def prepare(self, state):
        jid = state["job_id"]
        j = self.store.job(jid)

        async def perform():
            docs = await self.sources.collect(
                j["anchor"],
                keywords=[
                    j["anchor"] + " 产业链 A股 公司 公告",
                    j["anchor"] + " 行业 景气 需求 价格",
                ],
                as_of=j["as_of"],
                job_dir=self.store.path(f"jobs/{jid}"),
                limit=12,
            )
            if not docs:
                raise ValueError("no_readable_sources: 未获得可阅读资料，不能凭模型记忆生成产业链")
            return {"documents": docs, "source_errors": self.sources.last_errors}

        data = await self.stage(
            jid, "sources", 8, [CONTENT_VERSION, j["anchor"], j["as_of"]], perform
        )
        return {"artifacts": {"sources": digest(data)}}

    async def structure(self, state):
        jid = state["job_id"]
        j = self.store.job(jid)
        docs = self.load(jid, "sources")["documents"]

        async def perform():
            result = await self.model.complete(
                "structure",
                prompts.STRUCTURE,
                encode({"anchor": j["anchor"], "as_of": j["as_of"], "sources": excerpts(docs)}),
                ChainSpec,
                self.progress(jid, "structure"),
            )
            check_evidence(result, {d["evidence_id"] for d in docs})
            return result

        spec = await self.stage(jid, "structure", 18, [j["anchor"], digest(docs)], perform)
        self.store.update_job(
            jid,
            segments=[
                {"id": n["id"], "name": n["name"], "status": "pending"} for n in spec["nodes"]
            ],
        )
        return {"artifacts": {**state["artifacts"], "structure": digest(spec)}}

    async def snapshot(self, state):
        jid = state["job_id"]
        j = self.store.job(jid)
        spec = self.load(jid, "structure")

        async def perform():
            candidates = {}
            company_nodes = {}
            order = []
            for n in spec["nodes"]:
                # Resolve named candidates first; never accept model-supplied ticker identity.
                found = []
                for q in n["candidate_companies"][:8]:
                    found.extend(await asyncio.to_thread(self.lake.resolve_company, q, 2))
                found.extend(await asyncio.to_thread(self.lake.find_candidates, n["keywords"], 5))
                unique = {}
                for c in found:
                    unique[c["symbol"]] = {**c, **unique.get(c["symbol"], {})}
                for c in unique.values():
                    c["main_business"] = c.get("main_business") or c.get("business_evidence") or ""
                candidates[n["id"]] = list(unique.values())[:6]
                for sym in unique:
                    if sym not in order:
                        order.append(sym)
                    company_nodes.setdefault(sym, []).append(n["id"])
            # Acquire actual per-segment/company material before accepting broad
            # keyword candidates. Cache it separately from financial snapshots.
            semaphore = asyncio.Semaphore(self.settings.segment_concurrency)

            async def enrich(node):
                async with semaphore:

                    async def collect():
                        service = (
                            SourceService(self.sources.settings)
                            if type(self.sources) is SourceService
                            else self.sources
                        )
                        return await service.collect_segment(
                            j["anchor"],
                            {**node, "candidate_companies": candidates[node["id"]][:2]},
                            j["as_of"],
                            self.store.path(f"jobs/{jid}"),
                            limit=3,
                        )

                    return await self.stage(
                        jid,
                        "materials_" + digest(node["id"])[:12],
                        22,
                        [CONTENT_VERSION, node, candidates[node["id"]], j["as_of"]],
                        collect,
                    )

            targeted = await asyncio.gather(*(enrich(n) for n in spec["nodes"]))
            web_docs = list(
                {
                    d["evidence_id"]: d
                    for d in self.load(jid, "sources")["documents"]
                    + [d for rows in targeted for d in rows]
                }.values()
            )
            if web_docs and any(candidates.values()):

                async def discover():
                    # Industry articles may name valid companies outside the
                    # initial three candidates. Extract names, then resolve
                    # through the lake; never invent codes from the model.
                    result = await self.model.complete(
                        "company_discovery",
                        prompts.DISCOVER_COMPANIES,
                        encode(
                            {
                                "nodes": spec["nodes"],
                                "existing": candidates,
                                "sources": excerpts(web_docs, 10000),
                            }
                        ),
                        CompanyDiscovery,
                        self.progress(jid, "company_discovery"),
                    )
                    by_id = {d["evidence_id"]: d["content"] for d in web_docs}
                    found = []
                    for item in result["companies"]:
                        content = by_id.get(item["evidence_id"], "")
                        if item["node_id"] not in candidates or item["name"] not in content:
                            continue
                        if not quote_in_source(item["support_quote"], content):
                            continue
                        resolved = await asyncio.to_thread(
                            self.lake.resolve_company, item["name"], 1
                        )
                        if resolved:
                            found.append({**item, "company": resolved[0]})
                    return found

                discoveries = await self.stage(
                    jid,
                    "company_discovery",
                    24,
                    [prompts.DISCOVER_COMPANIES, digest(web_docs), candidates],
                    discover,
                )
                for item in discoveries:
                    rows = candidates[item["node_id"]]
                    if (
                        item["company"]["symbol"] not in {c["symbol"] for c in rows}
                        and len(rows) < 9
                    ):
                        rows.append(item["company"])
                        symbol = item["company"]["symbol"]
                        if symbol not in order:
                            order.append(symbol)
                        company_nodes.setdefault(symbol, []).append(item["node_id"])
            if any(candidates.values()):

                async def mapping():
                    materials = mapping_materials(web_docs, candidates)
                    review = await self.model.complete(
                        "candidate_mapping",
                        prompts.CANDIDATES,
                        encode(
                            {"nodes": spec["nodes"], "candidates": candidates, "sources": materials}
                        ),
                        CandidateReview,
                        self.progress(jid, "candidate_mapping"),
                    )
                    accepted, audit = verified_candidates(review, candidates, web_docs)
                    invalid = [
                        d
                        for d in audit
                        if d["relevance"] == "unconfirmed"
                        and not d["quote_verified"]
                        and "逐字核对" in d["reason"]
                    ]
                    if invalid:
                        # One targeted quote repair, not another discovery loop.
                        repair = await self.model.complete(
                            "candidate_quote_repair",
                            prompts.CANDIDATES,
                            encode(
                                {
                                    "nodes": spec["nodes"],
                                    "candidates": {
                                        key: [
                                            c
                                            for c in rows
                                            if any(
                                                x["node_id"] == key and x["symbol"] == c["symbol"]
                                                for x in invalid
                                            )
                                        ]
                                        for key, rows in candidates.items()
                                    },
                                    "sources": materials,
                                    "invalid_quotes": invalid,
                                    "instruction": "只返回以上失败公司。support_quote必须从所给原文复制，选一段足够证明产品归属的短句；不要改写或拼接未标省略的文字。没有证明则unconfirmed。",
                                }
                            ),
                            CandidateReview,
                            self.progress(jid, "candidate_quote_repair"),
                        )
                        repaired = {(d["node_id"], d["symbol"]): d for d in repair["decisions"]}
                        review["decisions"] = [
                            repaired.get((d["node_id"], d["symbol"]), d)
                            for d in review["decisions"]
                        ]
                        accepted, audit = verified_candidates(review, candidates, web_docs)
                    return {"candidates": accepted, "decisions": audit}

                mapped = await self.stage(
                    jid,
                    "candidate_mapping",
                    25,
                    [
                        "source-windows-verbatim-v2",
                        prompts.CANDIDATES,
                        candidates,
                        digest(web_docs),
                    ],
                    mapping,
                )
                candidates = mapped["candidates"]
                self.store.write_json(f"jobs/{jid}/audit/company-mapping.json", mapped["decisions"])
            # Round-robin prevents upstream candidates consuming all 24 slots.
            balanced = list(
                dict.fromkeys(
                    c["symbol"]
                    for i in range(9)
                    for rows in candidates.values()
                    for c in rows[i : i + 1]
                )
            )
            selected = balanced[:24]
            bundle = await asyncio.to_thread(
                self.lake.freeze_bundle,
                selected,
                j["as_of"],
                self.store.path(f"jobs/{jid}/inputs/financials"),
            )
            for n in candidates:
                candidates[n] = [c for c in candidates[n] if c["symbol"] in selected]
            summaries = {symbol: pack_summary(pack) for symbol, pack in bundle["packs"].items()}
            data_docs = []
            for symbol, summary in summaries.items():
                content = encode(summary)
                h = digest(summary)
                data_docs.append(
                    {
                        "evidence_id": "lake_" + h[:20],
                        "title": summary["name"] + " 冻结财务与业务快照",
                        "url": "",
                        "published_at": None,
                        "fetched_at": bundle["manifest"]["created_at"],
                        "content": content,
                        "content_hash": h,
                        "source_type": "lake_snapshot",
                        "fact_type": "source_fact",
                        "quality_flags": summary["warnings"],
                        "artifact_ref": f"inputs/financials/company_pack_{symbol.replace('.', '_')}.json",
                    }
                )
            return {
                **bundle,
                "summaries": summaries,
                "data_documents": data_docs,
                "candidates": candidates,
                "segment_documents": web_docs,
                "segment_source_ids": {
                    n["id"]: [d["evidence_id"] for d in rows]
                    for n, rows in zip(spec["nodes"], targeted, strict=True)
                },
                "segment_source_gaps": [
                    n["name"] + "：定向搜索没有取得可读正文，沿用已有全局资料"
                    for n, rows in zip(spec["nodes"], targeted, strict=True)
                    if not rows
                ],
                "excluded_candidates": [s for s in order if s not in selected],
                "company_nodes": company_nodes,
            }

        data = await self.stage(jid, "snapshot", 28, [SNAPSHOT_VERSION, digest(spec)], perform)
        return {"artifacts": {**state["artifacts"], "snapshot": digest(data)}}

    async def segment_expert(self, jid, node, inputs):
        reads = {}
        activity = {"duplicate_reads": 0, "dossier_reads": 0}
        web = [d for d in inputs["documents"] if d["source_type"] != "lake_snapshot"]
        terms = [node["name"], *node["keywords"]]
        web.sort(
            key=lambda d: sum(d.get("content", "").lower().count(t.lower()) for t in terms),
            reverse=True,
        )
        symbols = set(inputs["company_packs"])
        quantitative = [
            d
            for d in inputs["documents"]
            if d["source_type"] == "lake_snapshot" and any(sym in d["content"] for sym in symbols)
        ]
        context = {
            **inputs,
            "documents": excerpts(web[:6], 4000),
            "financial_evidence": [
                {k: d.get(k) for k in ["evidence_id", "title"]} for d in quantitative
            ],
            "other_materials": [
                {k: d.get(k) for k in ["evidence_id", "title"]} | {"total_chars": len(d["content"])}
                for d in web[6:]
            ],
        }

        @tool
        def read_segment_data() -> str:
            """Read this segment's frozen company packs and document excerpts; no live changes."""
            self.emit(jid, "tool_finished", tool="read_segment_data", segment_id=node["id"])
            activity["dossier_reads"] += 1
            if activity["dossier_reads"] > 1:
                activity["duplicate_reads"] += 1
            return segment_dossier(context)

        @tool
        def read_material(evidence_id: str, offset: int = 0) -> str:
            """Read up to 24000 characters from a known full source. Follow next_offset/eof instead of guessing offsets. Unknown IDs are denied."""
            self.emit(
                jid,
                "tool_started",
                tool="read_material",
                segment_id=node["id"],
                evidence_id=evidence_id,
            )
            for d in inputs["documents"]:
                if d["evidence_id"] == evidence_id:
                    start = max(0, offset)
                    self.emit(
                        jid,
                        "tool_finished",
                        tool="read_material",
                        segment_id=node["id"],
                        evidence_id=evidence_id,
                    )
                    content = d["content"]
                    if d["source_type"] == "lake_snapshot":
                        content = json.dumps(json.loads(content), ensure_ascii=False, indent=2)
                    total = len(content)
                    end = min(start + 24000, total)
                    if any(a <= start and end <= b for a, b in reads.get(evidence_id, [])):
                        activity["duplicate_reads"] += 1
                        return encode(
                            {
                                "evidence_id": evidence_id,
                                "already_read": True,
                                "requested_range": [start, end],
                                "total_chars": total,
                                "eof": end >= total,
                                "instruction": "本片段已完整提供，使用此前结果；需要新内容时读取尚未覆盖的区间，依据足够则提交研究。",
                            }
                        )
                    reads.setdefault(evidence_id, []).append((start, end))
                    activity["duplicate_reads"] = 0
                    metadata = {
                        "evidence_id": evidence_id,
                        "offset": start,
                        "total_chars": total,
                        "eof": end >= total,
                        "next_offset": end if end < total else None,
                    }
                    return encode(metadata) + "\n---\n" + content[start:end]
            return "error: unknown_evidence_id"

        # Full documents are only exposed via a bounded read tool, not duplicated into every prompt.
        expert = agent(
            self.settings,
            self.model.model("researcher"),
            tools=[read_segment_data, read_material],
            prompt=prompts.SEGMENT
            + "\n首先调用read_segment_data，必要时read_material。company_packs已给出可用核心量化，financial_evidence给出对应引用ID，不必为相同数字重复打开lake_snapshot正文。相同片段不要重复读，按eof停止。选择与本环节有关的材料，不必读完全部文档；依据足够就提交最终SegmentResearch，缺口明确列出。不要另派专家。",
            response_format=ToolStrategy(
                cited_schemas({d["evidence_id"] for d in inputs["documents"]}, symbols)[0]
            ),
            name="segment-researcher",
            offload_tokens=None,
            middleware=[FinishAfterNoProgress(activity)],
        )
        output = None
        ticks = 0
        chars = 0
        start = time.monotonic()
        trace = []
        callback = self.progress(jid, f"segment:{node['id']}")
        # Do not kill an actively streaming study because of total elapsed time.
        # The provider client's read timeout bounds inactivity, not research duration.
        async for mode, chunk in expert.astream(
            {"messages": [HumanMessage(content=encode({"node": node, "as_of": inputs["as_of"]}))]},
            {"recursion_limit": 128},
            stream_mode=["messages", "updates"],
        ):
            if mode == "messages":
                message, metadata = chunk
                if not isinstance(message, AIMessageChunk):
                    continue  # Tool results are not evidence of model/network activity.
                ticks += 1
                structured_chars = sum(len(c.get("args") or "") for c in message.tool_call_chunks)
                chars += len(_message_text(message)) + structured_chars
                await callback(
                    {
                        "phase": "writing" if chars else "processing",
                        "stream_events": ticks,
                        "elapsed_seconds": round(time.monotonic() - start, 1),
                        "content_chars": chars,
                        "last_activity_seconds": 0,
                        "last_activity": now(),
                    }
                )
            else:
                for update in chunk.values():
                    if isinstance(update, dict):
                        for message in update.get("messages", []):
                            trace.append(
                                {
                                    "at": now(),
                                    "type": getattr(message, "type", ""),
                                    "name": getattr(message, "name", None),
                                    "content": _message_text(message)[:2000],
                                    "tool_calls": getattr(message, "tool_calls", []),
                                    "invalid_tool_calls": getattr(
                                        message, "invalid_tool_calls", []
                                    ),
                                    "response_metadata": getattr(message, "response_metadata", {}),
                                }
                            )
                            if getattr(message, "type", "") == "ai":
                                # Some providers buffer forced structured Tool Calls.
                                # A completed model response is real activity, not
                                # a streamed token; label the distinction explicitly.
                                await callback(
                                    {
                                        "phase": "response_received",
                                        "stream_events": ticks,
                                        "elapsed_seconds": round(time.monotonic() - start, 1),
                                        "content_chars": chars,
                                        "last_activity_seconds": 0,
                                        "last_activity": now(),
                                    }
                                )
                        self.store.write_json(
                            f"jobs/{jid}/traces/segment_{digest(node['id'])[:12]}.json", trace
                        )
                    if isinstance(update, dict) and update.get("structured_response"):
                        output = update["structured_response"]
        if output is None:
            raise ValueError("segment_missing_structured_response")
        return SegmentResearch.model_validate(output).model_dump()

    async def segments(self, state):
        jid = state["job_id"]
        spec = self.load(jid, "structure")
        snapshot = self.load(jid, "snapshot")
        docs = self.documents(jid)
        j = self.store.job(jid)
        semaphore = asyncio.Semaphore(self.settings.segment_concurrency)

        async def research(node):
            async with semaphore:
                name = "segment_" + digest(node["id"])[:12]
                # Without narrowing, each specialist could read all 36 pages,
                # including unrelated downstream material, before writing.
                # Full documents remain archived and available to the report.
                data = {
                    "node": node,
                    "as_of": j["as_of"],
                    "candidates": snapshot["candidates"][node["id"]],
                    "candidate_mapping": [
                        c.get("mapping", {}) for c in snapshot["candidates"][node["id"]]
                    ],
                    "company_packs": {
                        c["symbol"]: snapshot["summaries"][c["symbol"]]
                        for c in snapshot["candidates"][node["id"]]
                    },
                    "documents": scoped_documents(node, snapshot, docs),
                }

                async def perform():
                    self.segment_status(jid, node["id"], "running")
                    self.emit(jid, "segment_started", segment_id=node["id"], segment=node["name"])
                    result = await self.segment_expert(jid, node, data)
                    if result["segment_id"] != node["id"] or result["segment"] != node["name"]:
                        raise ValueError("segment_identity_mismatch")
                    allowed = {c["symbol"] for c in data["candidates"]}
                    if any(c["symbol"] not in allowed for c in result["companies"]):
                        raise ValueError("unresolved_company_in_segment")
                    known = {d["evidence_id"] for d in docs}
                    check_evidence(result, known)
                    # Scores with no evidence are unknown, not a neutral score.
                    for score in result["scores"]:
                        if not score["evidence_ids"]:
                            score["score"] = None
                    return result

                # Cache identity is the full immutable source catalog, not the
                # smaller reading view. Narrowing retrieval does not change
                # the facts supplied to an already completed specialist.
                fingerprint, cached = self.checkpoint(
                    jid, name, digest({**data, "documents": docs})
                )
                reused = cached is not None
                if cached is None:
                    self.store.save_step(jid, name, fingerprint, "running", {"started_at": now()})
                    try:
                        cached = await perform()
                    except Exception as exc:
                        self.segment_status(jid, node["id"], "failed")
                        self.store.save_step(
                            jid,
                            name,
                            fingerprint,
                            "failed",
                            {"error": str(exc), "type": type(exc).__name__},
                        )
                        self.emit(jid, "warning", segment_id=node["id"], message=str(exc)[:500])
                        raise
                    self.save(jid, name, fingerprint, cached)
                self.segment_status(jid, node["id"], "completed")
                self.emit(
                    jid,
                    "segment_finished",
                    segment_id=node["id"],
                    segment=node["name"],
                    cached=reused,
                )
                return cached

        # Successful siblings checkpoint independently even if another sibling fails.
        results = await asyncio.gather(
            *(research(n) for n in spec["nodes"]), return_exceptions=True
        )
        errors = [
            f"{n['name']}:{type(r).__name__}:{r}"
            for n, r in zip(spec["nodes"], results, strict=True)
            if isinstance(r, BaseException)
        ]
        if errors:
            raise RuntimeError("segment_failed:" + "; ".join(errors))
        self.save(jid, "segments", digest([digest(r) for r in results]), results)
        return {"artifacts": {**state["artifacts"], "segments": digest(results)}}

    def segment_status(self, jid, node_id, status):
        segments = self.store.job(jid)["segments"]
        for s in segments:
            if s["id"] == node_id:
                s["status"] = status
        self.store.update_job(
            jid,
            segments=segments,
            stage="segments",
            progress=30
            + int(40 * sum(s["status"] == "completed" for s in segments) / len(segments)),
        )

    async def review(self, state):
        jid = state["job_id"]
        segments = self.load(jid, "segments")
        docs = self.documents(jid)

        async def perform():
            result = await self.model.complete(
                "risk_review",
                prompts.RISK,
                encode({"structure": self.load(jid, "structure"), "segments": segments}),
                cited_schemas({d["evidence_id"] for d in docs})[1],
                self.progress(jid, "risk_review"),
            )
            check_evidence(result, {d["evidence_id"] for d in docs})
            return result

        data = await self.stage(jid, "review", 76, digest(segments), perform)
        return {"artifacts": {**state["artifacts"], "review": digest(data)}}

    async def synthesis(self, state):
        jid = state["job_id"]
        segments = self.load(jid, "segments")
        review = self.load(jid, "review")
        docs = self.documents(jid)

        async def perform():
            result = await self.model.complete(
                "synthesis",
                prompts.SYNTHESIS,
                encode(
                    {
                        "structure": self.load(jid, "structure"),
                        "segments": segments,
                        "risk_review": review,
                        "required_fixes": review["required_fixes"],
                        "source_catalog": excerpts(docs, 0),
                    }
                ),
                cited_schemas({d["evidence_id"] for d in docs})[2],
                self.progress(jid, "synthesis"),
            )
            check_evidence(result, {d["evidence_id"] for d in docs})
            return result

        result = await self.stage(jid, "synthesis", 85, digest([segments, review]), perform)
        return {"artifacts": {**state["artifacts"], "synthesis": digest(result)}}

    async def publish(self, state):
        jid = state["job_id"]
        j = self.store.job(jid)
        spec = self.load(jid, "structure")
        segments = self.load(jid, "segments")
        snapshot = self.load(jid, "snapshot")
        sources = self.load(jid, "sources")
        docs = self.documents(jid)
        review = self.load(jid, "review")
        synthesis = self.load(jid, "synthesis")

        async def perform():
            nonlocal spec, segments, review, synthesis
            known = {d["evidence_id"] for d in docs}
            normalized, aliases = normalize_citations(
                {"spec": spec, "segments": segments, "review": review, "synthesis": synthesis},
                known,
            )
            spec, segments, review, synthesis = (
                normalized[k] for k in ["spec", "segments", "review", "synthesis"]
            )
            if aliases:
                self.store.write_json(f"jobs/{jid}/audit/citation_aliases.json", aliases)
            chain_id = "chain_" + digest(j["anchor"].lower().strip())[:16]
            version = jid.removeprefix("job_")
            rid = "report_" + version
            graph = {k: spec[k] for k in ["nodes", "edges"]}
            gaps = list(
                dict.fromkeys(
                    spec["warnings"]
                    + review["gaps"]
                    + review["source_conflicts"]
                    + synthesis["gaps"]
                    + [g for s in segments for g in s["gaps"]]
                    + snapshot.get("segment_source_gaps", [])
                    + [w for p in snapshot["packs"].values() for w in p["warnings"]]
                )
            )
            if sources["source_errors"]:
                gaps.append("部分资料获取失败，详见输入manifest")
            report = {
                "report_id": rid,
                "job_id": jid,
                "chain_id": chain_id,
                "version": version,
                "anchor": j["anchor"],
                "as_of": j["as_of"],
                **synthesis,
                "chain_graph": graph,
                "segment_tables": segments,
                "companies": list(snapshot["summaries"].values()),
                "scorecard": scorecard(segments),
                "value_pool": {
                    "items": synthesis["value_pool_items"],
                    "method": "定性比较；不将跨环节公司总利润视作环节利润",
                },
                "risks": review["risks"],
                "evidence": [{k: v for k, v in d.items() if k != "content"} for d in docs],
                "data_gaps": gaps,
                "created_at": now(),
                "manifest": {
                    "prompt_version": prompts.VERSION,
                    "provider": self.settings.base_url,
                    "model": self.settings.model,
                    "model_roles": self.model_config,
                    "snapshot": snapshot["manifest"],
                    "source_hashes": {d["evidence_id"]: d["content_hash"] for d in docs},
                    "source_errors": sources["source_errors"],
                    "review_fixes_applied_in_synthesis": review["required_fixes"],
                },
            }
            report["manifest"]["publication"] = {"storage": "local", "formal_lake_written": False}
            self.store.write_json(f"jobs/{jid}/output/report.json", report)
            self.store.write_text(f"jobs/{jid}/output/report.md", render_markdown(report))
            self.store.write_text(f"jobs/{jid}/output/report.html", render_html(report))
            self.store.save_report(report)
            self.store.index(report, docs)
            return {"report_id": rid, "warnings": gaps}

        result = await self.stage(jid, "publish", 94, digest(state["artifacts"]), perform)

        def complete(current):
            if current["cancel_requested"]:
                raise asyncio.CancelledError()
            return {
                "status": "completed_with_warnings" if result["warnings"] else "completed",
                "stage": "completed",
                "progress": 100,
                "report_id": result["report_id"],
                "warnings": result["warnings"],
                "live": {},
            }

        self.store.transition_job(jid, complete)
        self.emit(jid, "report_ready", report_id=result["report_id"])
        self.emit(jid, "done", status=self.store.job(jid)["status"])
        return {"artifacts": {**state["artifacts"], "report_id": result["report_id"]}}

    async def run(self, jid):
        path = self.store.path("state/research-checkpoints.sqlite")
        async with AsyncSqliteSaver.from_conn_string(str(path)) as saver:
            builder = StateGraph(ResearchState)
            stages = [
                "prepare",
                "structure",
                "snapshot",
                "segments",
                "review",
                "synthesis",
                "publish",
            ]
            for name in stages:
                builder.add_node(name, getattr(self, name))
            for a, b in zip([START] + stages, stages + [END], strict=True):
                builder.add_edge(a, b)
            graph = builder.compile(checkpointer=saver)
            config = {"configurable": {"thread_id": jid}, "recursion_limit": 30}
            # Re-enter deterministic hash gates on resume. This also detects
            # changed inputs/code instead of blindly continuing a stale next node.
            # Successful unchanged stages/segments are read, never model-rerun.
            await graph.ainvoke({"job_id": jid, "artifacts": {}}, config)
