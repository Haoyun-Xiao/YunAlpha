from __future__ import annotations

import json
import re
from typing import Any

from fund_research_agent.domain.errors import ContextOverflow

PROTECTED_KEYS = {"fund_symbol", "manager_name", "as_of", "unit", "evidence_id"}


def estimate_tokens(value: str) -> int:
    # Conservative mixed Chinese/English estimate without binding to a vendor tokenizer.
    chinese = len(re.findall(r"[\u4e00-\u9fff]", value))
    other = max(0, len(value) - chinese)
    return chinese + (other + 3) // 4


class ContextManager:
    def __init__(self, budget_tokens: int = 32000, compact_at_tokens: int = 24000) -> None:
        self.budget_tokens = budget_tokens
        self.compact_at_tokens = compact_at_tokens

    def compact(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        encoded = json.dumps(messages, ensure_ascii=False)
        if estimate_tokens(encoded) <= self.compact_at_tokens:
            return messages
        if len(messages) <= 6:
            return messages
        head = messages[:2]
        tail = messages[-6:]
        omitted = messages[2:-6]
        anchors = []
        for message in omitted:
            text = str(message.get("content", ""))
            if any(key in text for key in PROTECTED_KEYS):
                anchors.append(text[:1200])
        summary = {
            "role": "system",
            "content": "【压缩的历史上下文】\n" + "\n".join(anchors[:10]),
        }
        result = [*head, summary, *tail]
        if estimate_tokens(json.dumps(result, ensure_ascii=False)) > self.budget_tokens:
            raise ContextOverflow("对话上下文超过预算且关键金融锚点不可继续裁剪")
        return result

    @staticmethod
    def report_preflight(
        handbook: str,
        prompt: str,
        part_data: str,
        output_tokens: int,
        context_window: int,
        safety_margin: float = 0.20,
    ) -> dict[str, int | bool]:
        protected_tokens = estimate_tokens(handbook)
        other_tokens = estimate_tokens(prompt) + estimate_tokens(part_data)
        required = protected_tokens + other_tokens + output_tokens
        limit = int(context_window * (1 - safety_margin))
        return {
            "protected_handbook_tokens": protected_tokens,
            "other_input_tokens": other_tokens,
            "reserved_output_tokens": output_tokens,
            "required_tokens": required,
            "usable_tokens": limit,
            "fits": required <= limit,
        }

