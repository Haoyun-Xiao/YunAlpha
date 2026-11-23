---
name: evidence_review
trigger: 检查数字、来源、冲突与事实推断边界
allowed_tools: [artifact.read, kb.search, lake.query_dataset]
budget: {model_turns: 5, tool_calls: 8}
risk_level: low
---
关键数字应能复算，claim 与 evidence 必须一一对应。来源冲突时保留冲突并说明权威性和日期差异。
