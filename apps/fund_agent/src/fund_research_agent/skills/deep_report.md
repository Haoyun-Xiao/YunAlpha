---
name: deep_report
trigger: 用户明确要求生成完整深度报告
allowed_tools: [fund.resolve, fund.inspect_coverage, report.generate, report.status, report.result]
budget: {model_turns: 4, tool_calls: 6}
risk_level: medium
---
只负责确认基金、截止日和创建后台任务。`report.generate` 成功后立即向用户返回 Job ID 和已提交状态，不得在同一轮中调用 `report.status` 轮询；状态查询只响应用户后续的明确请求。报告内部使用完整投资手册，不得切片、摘要替代或静默截断。
