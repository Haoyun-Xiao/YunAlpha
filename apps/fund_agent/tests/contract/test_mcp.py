from fund_research_agent.bootstrap import build_container


async def test_real_lake_mcp_is_read_only_allowlisted():
    client = build_container().mcp
    names = {tool["name"] for tool in await client.list_tools()}
    assert names == {
        "describe_lake",
        "resolve_symbol",
        "query_bars",
        "query_fundamentals",
        "query_dataset",
        "run_sql",
    }
    result = await client.call("run_sql", {"sql": "SELECT count(*) AS n FROM fund_profiles"})
    assert result["rows"][0][0] > 20_000
