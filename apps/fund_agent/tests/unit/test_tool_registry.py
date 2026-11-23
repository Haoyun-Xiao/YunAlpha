from pydantic import BaseModel

from fund_research_agent.tools.registry import ToolRegistry, ToolSpec


class EmptyInput(BaseModel):
    pass


async def handler(_value: EmptyInput):
    return {}


def test_registry_only_exposes_tools_allowed_by_loaded_skill():
    registry = ToolRegistry()
    registry.register(ToolSpec("fund.resolve", "resolve", EmptyInput, handler))
    registry.register(ToolSpec("lake.describe", "describe", EmptyInput, handler))
    registry.register(ToolSpec("report.generate", "generate", EmptyInput, handler))

    schemas = registry.schemas(names=["fund.resolve", "report.generate"])

    assert [item["function"]["name"] for item in schemas] == [
        "fund__resolve",
        "report__generate",
    ]
