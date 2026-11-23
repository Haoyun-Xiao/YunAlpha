---
name: fund_compare
trigger: 比较两只或多只基金
allowed_tools: [fund.resolve, fund.research_snapshot, kb.search]
budget: {model_turns: 6, tool_calls: 8}
risk_level: low
---
用相同截止日和统计口径比较。按收益来源、组合特征、风险和互补性组织，避免只比较收益率。
