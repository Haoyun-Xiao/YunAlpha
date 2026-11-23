from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from fund_research_agent.paths import WriteBoundary, sha256_file
from fund_research_agent.workflows.index_enhancement import prompts
from fund_research_agent.workflows.index_enhancement.materials import (
    lake_snapshot,
    normalized,
    prepare_materials,
    validate_facts,
    write_json,
)


def fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


def parse_json(text: str) -> dict:
    clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    return json.loads(clean)


class ResearchState(TypedDict, total=False):
    request: dict
    material_hash: str
    facts: dict
    handbook: str
    part_input: dict
    part1: str
    audit: dict
    revisions: int
    outputs: dict


def validate_text(text: str, kind: str, sources: list[str]) -> list[str]:
    errors = []
    headings = re.findall(r"(?m)^##\s+(.+)$", text)
    expected = (
        list(prompts.HANDBOOK_SECTIONS)
        if kind == "handbook"
        else [
            f"{number}、{label}" for number, label in zip("一二三四", prompts.SECTIONS, strict=True)
        ]
    )
    if headings != expected:
        errors.append(f"section_structure:{headings}")
    lower, upper = (4000, 8000) if kind == "handbook" else (600, 5000)
    # The Part1 prompt requests Chinese characters, not Markdown markers,
    # citation IDs, dates, percentages and Latin contract identifiers.
    size = (
        len(re.sub(r"\s", "", text))
        if kind == "handbook"
        else len(re.findall(r"[\u4e00-\u9fff]", text))
    )
    if not lower <= size <= upper:
        errors.append(f"length:{size}:expected_{lower}_{upper}")
    unknown = set(re.findall(r"\[(S\d+)\]", text)) - set(sources)
    if unknown:
        errors.append(f"unknown_citations:{sorted(unknown)}")
    if not re.search(r"\[S\d+\]", text):
        errors.append("missing_citations")
    if re.search(r"(?m)^#\s|待续|TODO|\{handbook\}|\{data\}", text):
        errors.append("placeholder_or_unexpected_title")
    if kind == "part1":
        for sentence in re.split(r"[。；;\n]", normalized(text)):
            if "股指期货" in sentence and "20%" in sentence:
                if not re.search(r"交易日内|单个交易日|当日", sentence) or not re.search(
                    r"不包括平仓|不含平仓|扣除平仓", sentence
                ):
                    errors.append("futures_turnover_limit_missing_qualifiers")
    return errors


class IndexEnhancementWorkflow:
    def __init__(
        self,
        root: Path,
        lake_root: Path,
        model,
        boundary: WriteBoundary,
        code: str = "000311.OF",
        as_of: date = date(2026, 10, 26),
        refresh: bool = False,
    ):
        self.root = boundary.ensure_dir(root)
        self.lake_root, self.model, self.boundary = lake_root, model, boundary
        self.code, self.as_of, self.refresh = code, as_of, refresh
        self.materials: dict = {}
        self.snapshot: dict = {}
        self.t0 = time.monotonic()
        for name in ("sources", "inputs", "cache", "prompts", "output"):
            boundary.ensure_dir(root / name)

    async def event(self, stage: str, **payload) -> None:
        row = {"at": datetime.now(UTC).isoformat(), "stage": stage, **payload}
        path = self.boundary.assert_writable(self.root / "events.jsonl")
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        if stage != "model_progress" or payload.get("phase") in {
            "started",
            "completed",
            "retrying",
        }:
            print(json.dumps(row, ensure_ascii=False, default=str), flush=True)

    async def call(
        self, operation: str, role: str, instruction: str, data: dict, structured: bool = False
    ) -> str:
        user = instruction + "\n\n" + json.dumps(data, ensure_ascii=False, default=str)
        key = fingerprint(
            {
                "version": prompts.VERSION,
                "system": prompts.SYSTEM,
                "user": user,
                "model": self.model.descriptor(role),
                "role_config": vars(self.model.role(role)),
                "structured": structured,
            }
        )
        cache = self.root / "cache" / f"{operation}-{key}.json"
        write_json(
            self.boundary,
            self.root / "prompts" / f"{operation}.json",
            {
                "system": prompts.SYSTEM,
                "user": user,
                "input_hash": key,
                "model": self.model.descriptor(role),
                "structured": structured,
            },
        )
        if cache.exists():
            await self.event(operation, status="cached", input_hash=key)
            return json.loads(cache.read_text())["content"]
        await self.event(operation, status="started", input_hash=key)
        started = time.monotonic()
        response = await self.model.chat(
            role,
            [{"role": "system", "content": prompts.SYSTEM}, {"role": "user", "content": user}],
            response_format={"type": "json_object"} if structured else None,
            max_tokens=12000 if operation == "extract_facts" else None,
            operation=f"index_{operation}",
        )
        if response.get("finish_reason") == "length" or not response.get("content", "").strip():
            raise ValueError(f"model_incomplete:{operation}:{response.get('finish_reason')}")
        if structured:
            parse_json(response["content"])
        write_json(
            self.boundary,
            cache,
            {
                **response,
                "input_hash": key,
                "model": self.model.descriptor(role),
                "duration_seconds": round(time.monotonic() - started, 2),
            },
        )
        await self.event(
            operation,
            status="completed",
            characters=len(response["content"]),
            usage=response.get("usage"),
        )
        return response["content"].strip()

    async def prepare(self, state: ResearchState) -> dict:
        await self.event("prepare_materials", status="started")
        self.materials = await asyncio.to_thread(
            prepare_materials,
            self.root,
            self.lake_root,
            self.code,
            self.as_of,
            self.boundary,
            self.refresh,
        )
        self.snapshot = await asyncio.to_thread(
            lake_snapshot, self.lake_root, self.code, self.as_of
        )
        write_json(self.boundary, self.root / "inputs/lake_snapshot.json", self.snapshot)
        await self.event(
            "prepare_materials", status="completed", sources=len(self.materials["sources"])
        )
        return {"material_hash": fingerprint(self.materials), "revisions": 0}

    async def extract(self, state: ResearchState) -> dict:
        materials = [
            {k: s[k] for k in ("source_id", "kind", "title", "published_at", "excerpts")}
            for s in self.materials["sources"]
        ]
        text = await self.call(
            "extract_facts",
            "report",
            prompts.FACTS,
            {"fund_symbol": self.code, "as_of": str(self.as_of), "materials": materials},
            True,
        )
        extracted = parse_json(text)
        validation = validate_facts(extracted, self.materials, self.snapshot)
        write_json(self.boundary, self.root / "inputs/fact_validation.json", validation)
        if not validation["valid"]:
            # One repair opportunity fixes extraction/localization, not source facts.
            text = await self.call(
                "repair_facts",
                "report",
                prompts.FACTS + "\n修复上次抽取的明确错误，不修改原文；返回完整facts JSON。",
                {"materials": materials, "previous": extracted, "errors": validation["errors"]},
                True,
            )
            extracted = parse_json(text)
        write_json(self.boundary, self.root / "inputs/extracted_facts.json", extracted)
        return {"facts": extracted}

    async def check_facts(self, state: ResearchState) -> dict:
        validation = validate_facts(state["facts"], self.materials, self.snapshot)
        write_json(self.boundary, self.root / "inputs/fact_validation.json", validation)
        if not validation["valid"]:
            raise ValueError(f"fact_validation_failed:{validation['errors']}")
        await self.event("validate_facts", status="passed", facts=len(validation["facts"]))
        return {"facts": validation}

    async def handbook(self, state: ResearchState) -> dict:
        text = await self.call(
            "handbook",
            "handbook",
            prompts.HANDBOOK,
            {
                "fund_symbol": self.code,
                "as_of": str(self.as_of),
                "verified_facts": state["facts"],
                "materials": [
                    {k: s[k] for k in ("source_id", "title", "published_at", "excerpts")}
                    for s in self.materials["sources"]
                ],
            },
        )
        self.boundary.write_text(self.root / "handbook.md", text + "\n")
        return {"handbook": text}

    async def check_handbook(self, state: ResearchState) -> dict:
        issues = validate_text(
            state["handbook"], "handbook", [s["source_id"] for s in self.materials["sources"]]
        )
        write_json(
            self.boundary,
            self.root / "handbook_validation.json",
            {"valid": not issues, "errors": issues},
        )
        if issues:
            raise ValueError(f"handbook_validation_failed:{issues}")
        return {}

    async def part_input(self, state: ResearchState) -> dict:
        # A prospectus can repeat an older portfolio. Use the periodic report
        # for actual tool-use observations, not the legal-document copy.
        tool_evidence = []
        for source in self.materials["sources"]:
            if source["kind"] != "periodic":
                continue
            for page in json.loads(Path(source["text_path"]).read_text()):
                if any(
                    term in normalized(page["text"])
                    for term in (
                        "权证投资明细",
                        "本基金投资股指期货的投资政策",
                        "衍生金融资产",
                        "权益衍生工具",
                    )
                ):
                    tool_evidence.append(
                        {
                            "source_id": source["source_id"],
                            "title": source["title"],
                            "published_at": source["published_at"],
                            **page,
                        }
                    )
        value = {
            "fund_symbol": self.code,
            "as_of": str(self.as_of),
            "chapter": "Part1 产品定位与基准",
            "verified_facts": state["facts"],
            "handbook_sha256": fingerprint(state["handbook"]),
            "tool_usage_evidence": tool_evidence,
            "sources": [
                {k: s[k] for k in ("source_id", "title", "published_at", "url")}
                for s in self.materials["sources"]
            ],
            "data_boundaries": [
                "未计算业绩归因、超额稳定性与同类排名",
                "最新半年报全持仓不完整，不用于全组合结论",
                "本章基金份额为A，C/Y仅作产品沿革背景",
            ],
        }
        write_json(self.boundary, self.root / "inputs/part1.json", value)
        return {"part_input": value}

    async def write_part1(self, state: ResearchState) -> dict:
        text = await self.call(
            "part1",
            "report",
            prompts.PART1,
            {"完整投资手册": state["handbook"], "本章事实JSON": state["part_input"]},
        )
        self.boundary.write_text(self.root / "part1.md", text + "\n")
        return {"part1": text}

    async def audit(self, state: ResearchState) -> dict:
        errors = validate_text(
            state["part1"], "part1", [s["source_id"] for s in self.materials["sources"]]
        )
        try:
            result = parse_json(
                await self.call(
                    "audit",
                    "evaluator",
                    prompts.AUDIT,
                    {
                        "handbook": state["handbook"],
                        "facts": state["facts"],
                        "source_index": state["part_input"]["sources"],
                        "periodic_tool_usage_evidence": state["part_input"].get(
                            "tool_usage_evidence", []
                        ),
                        "part1": state["part1"],
                    },
                    True,
                )
            )
            if not isinstance(result.get("passed"), bool) or not isinstance(
                result.get("issues"), list
            ):
                raise ValueError("invalid_audit_schema")
            if result["issues"]:
                result["passed"] = False
        except Exception as exc:
            result = {"passed": False, "issues": [], "unavailable": type(exc).__name__}
        result["deterministic_errors"] = errors
        chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", state["part1"]))
        result["length_target"] = {
            "chinese_characters": chinese_chars,
            "target": [1500, 2200],
            "within_target": 1500 <= chinese_chars <= 2200,
            "policy": "editorial_target_not_publication_gate",
        }
        if errors:
            result["passed"] = False
        write_json(self.boundary, self.root / "audit.json", result)
        await self.event("audit", status="passed" if result["passed"] else "issues", **result)
        return {"audit": result}

    async def revise(self, state: ResearchState) -> dict:
        text = await self.call(
            "revise",
            "report",
            prompts.PART1 + "\n只修复下面审核确认的问题，输出完整四节正文。",
            {
                "完整投资手册": state["handbook"],
                "本章事实JSON": state["part_input"],
                "previous": state["part1"],
                "audit": state["audit"],
            },
        )
        self.boundary.write_text(self.root / "part1.md", text + "\n")
        return {"part1": text, "revisions": state.get("revisions", 0) + 1}

    async def render(self, state: ResearchState) -> dict:
        from fund_research_agent.workflows.index_enhancement.render import render_outputs

        if state["audit"]["deterministic_errors"]:
            raise ValueError(f"part1_validation_failed:{state['audit']['deterministic_errors']}")
        await self.event("render", status="started")
        outputs = await asyncio.to_thread(
            render_outputs, self.root, state["part_input"], state["part1"], self.boundary
        )
        stage_calls = []
        for prompt_path in sorted((self.root / "prompts").glob("*.json")):
            prompt = json.loads(prompt_path.read_text())
            matches = list(
                (self.root / "cache").glob(f"{prompt_path.stem}-{prompt['input_hash']}.json")
            )
            if matches:
                saved = json.loads(matches[0].read_text())
                stage_calls.append(
                    {
                        "operation": prompt_path.stem,
                        "input_hash": prompt["input_hash"],
                        "duration_seconds": saved.get("duration_seconds"),
                        "usage": saved.get("usage", {}),
                        "model": saved.get("model"),
                    }
                )
        manifest = {
            "status": "completed" if state["audit"]["passed"] else "completed_with_warnings",
            "fund_symbol": self.code,
            "as_of": str(self.as_of),
            "scope": "part1_only",
            "prompt_version": prompts.VERSION,
            "revisions": state.get("revisions", 0),
            "material_hash": state["material_hash"],
            "part_input_hash": fingerprint(state["part_input"]),
            "handbook_hash": fingerprint(state["handbook"]),
            "audit": state["audit"],
            "model_roles": {
                r: self.model.descriptor(r) for r in ("handbook", "report", "evaluator")
            },
            "outputs": outputs,
            "stage_model_calls": stage_calls,
            "elapsed_this_invocation_seconds": round(time.monotonic() - self.t0, 2),
        }
        write_json(self.boundary, self.root / "result_manifest.json", manifest)
        await self.event("done", status=manifest["status"], outputs=outputs)
        return {"outputs": outputs}

    def graph(self, checkpointer=None):
        builder = StateGraph(ResearchState)
        nodes = {
            "prepare_materials": self.prepare,
            "extract_facts": self.extract,
            "validate_facts": self.check_facts,
            "generate_handbook": self.handbook,
            "validate_handbook": self.check_handbook,
            "build_part1_input": self.part_input,
            "generate_part1": self.write_part1,
            "audit_part1": self.audit,
            "revise_part1": self.revise,
            "render": self.render,
        }
        for name, node in nodes.items():
            builder.add_node(name, node)
        chain = list(nodes)[:8]
        for a, b in zip([START, *chain[:-1]], chain, strict=True):
            builder.add_edge(a, b)
        builder.add_conditional_edges(
            "audit_part1",
            lambda s: (
                "revise_part1"
                if (s["audit"]["issues"] or s["audit"]["deterministic_errors"])
                and not s.get("revisions", 0)
                else "render"
            ),
            ["revise_part1", "render"],
        )
        builder.add_edge("revise_part1", "audit_part1")
        builder.add_edge("render", END)
        return builder.compile(checkpointer=checkpointer)


async def run(
    root: Path, settings, code: str, as_of: date, refresh: bool = False, resume: bool = False
) -> dict:
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    from fund_research_agent.adapters.model import ModelGateway

    boundary = WriteBoundary(settings.project_root, settings.legacy_root, settings.var_root)
    boundary.ensure_dir(root)
    os.environ["LANGGRAPH_STRICT_MSGPACK"] = "true"
    model = ModelGateway(
        settings.project_root / "config/model_roles.yaml",
        boundary=boundary,
        log_path=root / "calls.jsonl",
    )
    workflow = IndexEnhancementWorkflow(
        root, settings.legacy_root, model, boundary, code, as_of, refresh
    )
    token = model.set_progress_callback(lambda p: workflow.event("model_progress", **p))
    session = model.set_session_id(f"index-enhancement-{root.name}")
    config = {"configurable": {"thread_id": root.name}, "recursion_limit": 30}
    signature = fingerprint(
        {
            "code": code,
            "as_of": as_of,
            "version": prompts.VERSION,
            "models": {r: vars(model.role(r)) for r in ("handbook", "report", "evaluator")},
            "provider": model.base_url,
            "implementation": {p.name: sha256_file(p) for p in Path(__file__).parent.glob("*.py")},
        }
    )
    try:
        async with AsyncSqliteSaver.from_conn_string(str(root / "checkpoints.sqlite")) as saver:
            graph = workflow.graph(saver)
            prior = await graph.aget_state(config)
            if resume and prior.next:
                if prior.values.get("request", {}).get("signature") != signature or refresh:
                    raise ValueError(
                        "实现/模型/请求已变化；不使用--resume，重新进入图并复用匹配阶段缓存"
                    )
                await workflow.prepare({})
                if fingerprint(workflow.materials) != prior.values.get("material_hash"):
                    raise ValueError("资料已变化，不能继续旧Checkpoint")
                state = await graph.ainvoke(None, config)
            else:
                state = await graph.ainvoke(
                    {"request": {"signature": signature}, "revisions": 0}, config
                )
        return state
    except Exception as exc:
        await workflow.event("failed", error_type=type(exc).__name__, message=str(exc)[:1200])
        raise
    finally:
        model.reset_progress_callback(token)
        model.reset_session_id(session)
