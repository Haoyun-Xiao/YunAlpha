---
name: fund_fact
trigger: 查询单只基金的确定性事实或指标
allowed_tools: [fund.resolve, lake.query_dataset]
budget: {model_turns: 3, tool_calls: 4}
risk_level: low
---
先确认基金份额和 as_of。数字必须来自工具，回答中说明日期、单位和口径；缺失时直接披露。
