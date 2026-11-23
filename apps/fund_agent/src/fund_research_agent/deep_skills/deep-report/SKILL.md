---
name: deep-report
description: 用户明确要求为某只基金写完整深度报告或查询、恢复、取消报告任务时使用。
allowed-tools: fund_resolve fund_inspect_coverage report_request report_status report_result report_resume report_cancel
---

# 深度报告工具

- 只有用户当前轮明确要求写报告时才调用 `report_request`。
- 可使用已确认的当前基金；没有当前基金或存在多个候选时必须澄清。
- `accepted` 后立即返回 Job ID 并结束本轮，不调用 `report_status` 轮询。
- `not_eligible` 时列出阻断原因，不反复调用同一工具。
- 状态、恢复和取消只响应用户后续的明确请求。
- 报告内部节点、Prompt、完整投资手册与渲染不属于对话 Agent，不得改写。
