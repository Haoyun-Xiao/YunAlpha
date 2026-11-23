---
name: manager_style
trigger: 研究基金经理理念、风格和言行一致性
allowed_tools: [fund.resolve, fund.research_snapshot, kb.search, web.search_manager_materials]
budget: {model_turns: 6, tool_calls: 6}
risk_level: low
---
分开写经理原话、组合行为、模型归纳与推断。观点必须绑定任期、基金和来源，检查同名经理污染。
