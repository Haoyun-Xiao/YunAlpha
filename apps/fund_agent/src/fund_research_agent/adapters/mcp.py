from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from fund_research_agent.domain.errors import DependencyUnavailable, PolicyDenied
from fund_research_agent.paths import WriteBoundary

ALLOWED_MCP_TOOLS = {
    "describe_lake",
    "resolve_symbol",
    "query_bars",
    "query_fundamentals",
    "query_dataset",
    "run_sql",
}


class LakeMCPClient:
    """Small newline-delimited JSON-RPC client for the legacy read-only MCP server."""

    def __init__(
        self,
        executable: Path,
        config: Path,
        runtime_root: Path,
        boundary: WriteBoundary,
    ) -> None:
        self.executable = executable.resolve()
        self.config = config.resolve()
        self.runtime_root = boundary.ensure_dir(runtime_root)
        self.boundary = boundary

    async def list_tools(self) -> list[dict[str, Any]]:
        result = await self._request("tools/list", {})
        return [tool for tool in result.get("tools", []) if tool.get("name") in ALLOWED_MCP_TOOLS]

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name not in ALLOWED_MCP_TOOLS:
            raise PolicyDenied(f"MCP tool is not allowed: {name}")
        if name == "run_sql":
            sql = str(arguments.get("sql", ""))
            forbidden = ("insert", "update", "delete", "create", "drop", "alter", "copy", "attach")
            if not sql.lstrip().lower().startswith(("select", "with")) or any(
                token in sql.lower() for token in forbidden
            ):
                raise PolicyDenied("MCP run_sql 仅允许 SELECT/CTE")
        result = await self._request("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise DependencyUnavailable(self._content_text(result))
        text = self._content_text(result)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"content": text}

    @staticmethod
    def _content_text(result: dict[str, Any]) -> str:
        content = result.get("content") or []
        return "\n".join(str(item.get("text", "")) for item in content if item.get("type") == "text")

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.executable.exists():
            raise DependencyUnavailable(f"MCP executable not found: {self.executable}")
        env = os.environ.copy()
        env.update(
            {
                "TMPDIR": str(self.boundary.ensure_dir(self.runtime_root / "tmp")),
                "PYTHONPYCACHEPREFIX": str(
                    self.boundary.ensure_dir(self.runtime_root / "pycache")
                ),
            }
        )
        initialize = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "fund-research-agent", "version": "0.1.0"},
            },
        }
        request = {"jsonrpc": "2.0", "id": 2, "method": method, "params": params}
        payload = (json.dumps(initialize) + "\n" + json.dumps(request) + "\n").encode()
        process = await asyncio.create_subprocess_exec(
            str(self.executable),
            "mcp",
            "--config",
            str(self.config),
            cwd=self.runtime_root,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(payload), timeout=60)
        if process.returncode:
            raise DependencyUnavailable(stderr.decode(errors="replace")[-2000:])
        responses = [json.loads(line) for line in stdout.decode().splitlines() if line.strip()]
        response = next((item for item in responses if item.get("id") == 2), None)
        if response is None:
            raise DependencyUnavailable("MCP response missing")
        if response.get("error"):
            raise DependencyUnavailable(str(response["error"]))
        return response.get("result") or {}
