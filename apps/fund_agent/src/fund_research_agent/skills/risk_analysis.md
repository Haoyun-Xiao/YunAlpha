---
name: risk_analysis
trigger: 分析回撤、波动、集中度、行业和风格风险
allowed_tools: [fund.resolve, fund.research_snapshot, kb.search]
budget: {model_turns: 5, tool_calls: 5}
risk_level: low
---
风险判断优先使用可复算指标，说明样本期和数据缺口。不得把历史风险统计写成未来承诺。
