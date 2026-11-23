"""Read-only client for the lake's Tushare proxy MCP."""

from __future__ import annotations

import json
import os
import shlex
import tomllib
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from fund_research_agent.domain.errors import DependencyUnavailable

_FIELDS = {
    "trade_cal": ("exchange", "cal_date", "is_open"),
    "daily": ("ts_code", "trade_date", "open", "high", "low", "close"),
}
_MAX_RESPONSE = 2_000_000


def _mcp_url(config_path: Path) -> str:
    """Use the same local credentials as daily ingestion, including token rotation."""
    try:
        source = tomllib.loads(config_path.read_text())["sources"]["tushare"]
        token_name = source.get("token_env", "TUSHARE_TOKEN")
        url_name = source.get("api_url_env", "TUSHARE_HTTP_URL")
        values = {key: os.getenv(key, "") for key in (token_name, url_name)}
        env_path = config_path.with_name(".env.local")
        if env_path.is_file():
            for line in env_path.read_text().splitlines():
                key, separator, value = line.strip().removeprefix("export ").partition("=")
                if separator and key.strip() in values:
                    parts = shlex.split(value, comments=True)
                    values[key.strip()] = parts[0] if len(parts) == 1 else ""
        base = urlsplit(values[url_name])
        token = values[token_name]
        if not token or base.scheme != "https" or not base.hostname or base.username is not None:
            raise ValueError
        return urlunsplit(("https", base.netloc, "/mcp", urlencode({"token": token}), ""))
    except (OSError, KeyError, ValueError):
        raise DependencyUnavailable("代理 MCP 缺少有效的本机 HTTPS 地址或 token 配置") from None


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _json_response(response, expected_id: int) -> dict:
    if response.headers.get_content_type() != "text/event-stream":
        payload = response.read(_MAX_RESPONSE + 1)
        if len(payload) > _MAX_RESPONSE:
            raise ValueError
        return json.loads(payload)
    event = []
    total = 0
    while line := response.readline(_MAX_RESPONSE + 1):
        total += len(line)
        if total > _MAX_RESPONSE:
            raise ValueError
        if line.startswith(b"data:"):
            event.append(line[5:].strip())
        elif not line.strip() and event:
            data = json.loads(b"\n".join(event))
            if data.get("id") == expected_id:
                return data
            event = []
    raise ValueError


class ProxyMCP:
    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path

    def lookup(self, name: str, arguments: dict[str, str]) -> list[dict[str, Any]]:
        url = _mcp_url(self.config_path)
        opener = build_opener(_NoRedirect())
        headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}

        def request(method, params=None, request_id=None):
            message = {"jsonrpc": "2.0", "method": method}
            if params is not None:
                message["params"] = params
            if request_id is not None:
                message["id"] = request_id
            with opener.open(Request(url, json.dumps(message).encode(), headers, method="POST"), timeout=8) as reply:
                if session := reply.headers.get("Mcp-Session-Id"):
                    headers["Mcp-Session-Id"] = session
                if request_id is None:
                    return {}
                data = _json_response(reply, request_id)
            if data.get("id") != request_id or "error" in data or not isinstance(data.get("result"), dict):
                raise DependencyUnavailable(f"代理 MCP 未成功执行 {method}")
            return data["result"]

        try:
            initialized = request("initialize", {
                "protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": {"name": "YunAlpha", "version": "1"},
            }, 1)
            headers["MCP-Protocol-Version"] = initialized["protocolVersion"]
            request("notifications/initialized")
            listing = request("tools/list", {}, 2)
            spec = next((item for item in listing["tools"] if item["name"] == name), None)
            if spec is None:
                raise DependencyUnavailable(f"代理 MCP 当前未提供 {name} 工具")
            properties = spec.get("inputSchema", {}).get("properties", {})
            if "fields" in properties:
                field_spec = properties["fields"]
                is_array = any(
                    rule.get("type") == "array" for rule in [field_spec, *field_spec.get("anyOf", [])]
                )
                arguments = dict(arguments, fields=list(_FIELDS[name]) if is_array else ",".join(_FIELDS[name]))
            result = request("tools/call", {"name": name, "arguments": arguments}, 3)
            if result.get("isError"):
                raise DependencyUnavailable(f"代理 MCP 的 {name} 查询失败")
            payload = result.get("structuredContent")
            if payload is None:
                text_blocks = [item["text"] for item in result.get("content", []) if item.get("type") == "text"]
                payload = [json.loads(text) for text in text_blocks]
                if len(payload) == 1:
                    payload = payload[0]
            return _rows(payload)
        except HTTPError as exc:
            # Exception URLs can contain the query token. Only retain status.
            raise DependencyUnavailable(f"代理 MCP 返回 HTTP {exc.code}") from None
        except (URLError, OSError, KeyError, TypeError, ValueError):
            raise DependencyUnavailable("代理 MCP 连接失败或返回格式有变化") from None


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list) and all(isinstance(row, dict) for row in payload):
        return payload
    if isinstance(payload, dict):
        if payload.get("code", 0) not in (0, "0"):
            raise DependencyUnavailable("代理 MCP 数据接口未返回成功")
        if isinstance(payload.get("fields"), list) and isinstance(payload.get("items"), list):
            return [dict(zip(payload["fields"], row, strict=True)) for row in payload["items"]]
        if "cal_date" in payload or "trade_date" in payload:
            return [payload]
        for key in ("data", "rows", "result"):
            if key in payload:
                return _rows(payload[key])
    raise ValueError
