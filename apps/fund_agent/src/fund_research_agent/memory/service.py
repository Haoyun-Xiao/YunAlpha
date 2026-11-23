from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

from fund_research_agent.domain.contracts import MemoryRecord
from fund_research_agent.domain.errors import PolicyDenied
from fund_research_agent.persistence.database import Database

FORBIDDEN_MEMORY = re.compile(
    r"(当前|最新|实时).{0,12}(净值|持仓|规模|行情|价格|基金经理|余额|订单)|"
    r"(API.?KEY|TOKEN|密钥|密码|账户余额|下单)",
    re.IGNORECASE,
)


class MemoryService:
    def __init__(self, database: Database, user_id: str = "local") -> None:
        self.database = database
        self.user_id = user_id

    async def remember(self, kind: str, content: str, scope: str = "global") -> MemoryRecord:
        if FORBIDDEN_MEMORY.search(content):
            raise PolicyDenied("实时金融事实、交易状态和敏感信息不能写入长期记忆")
        now = datetime.now(UTC)
        record = MemoryRecord(
            memory_id=f"memory_{uuid.uuid4().hex}",
            user_id=self.user_id,
            scope=scope,
            kind=kind,
            content=content.strip(),
            created_at=now,
            updated_at=now,
        )
        await self.database.add_memory(record)
        return record

    async def list(self) -> list[MemoryRecord]:
        return await self.database.list_memories(self.user_id)

    async def forget(self, memory_id: str) -> bool:
        return await self.database.delete_memory(memory_id)

