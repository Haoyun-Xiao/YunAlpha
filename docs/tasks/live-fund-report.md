# 基金 Agent 实时资料与真实报告验收

基线 `247a13e`，分支 `codex/live-fund-report`。范围 `apps/fund_agent/` 和本记录。恢复原有网页、官方定期报告与规模补充，保留 YunAlpha 独立缓存和任务状态。正式数据湖只读，不改旧应用、不部署，也不启动第三阶段。

沿用原 `/agent-runtime/api/v1` 会话、报告任务、SSE 和导出协议。验收从 YunAlpha 会话发起新任务，使用真实模型，检查新任务的取数、投资手册、分章、审核和 HTML/Word/Markdown 实际产物，不使用历史报告回放。

## 结果（2026-11-16）

- 生效提交：`cb2a726`（恢复原上游能力）。API 与 Worker 通用本机凭据，正式湖仍只读。新下载、JSON 补充和搜索缓存只写 YunAlpha 运行目录。
- Chrome 新会话 `session_<local-id>` 发起银华集成电路 A（013840.OF、方建），截止 2026-11-16。新任务 `report_<local-id>`，`force_refresh=true`、`web_mode=live`，未重用历史报告；完成用时 23 分 35 秒。
- 实时网页搜索 37 个候选、31 个提取到正文，筛选后采用 2 份。实际下载解析 28 份定期报告，0 失败，最新披露期 2026-09-30。该基金湖内规模已覆盖该期，主任务无须补规模；同一份额的原规模补充器独立请求上游，A/C 两类共取回 40 条，无报错。
- 真实 DeepSeek 模型生成 47,449 字投资手册（校验通过）、十章正文和总结，执行三次审核及两轮定向返修；第 10 章和第 1 章返修各有一次原模型输出上限重试。最终结构及渲染校验通过，内容审核仍标出 Part4–8 的五章问题，集中在日期/回撤口径、重复、估值截断解释与盈亏来源说明。保留 `completed_with_warnings`，按待复核成果处理。
- Chrome 从会话打开新报告，64 张网页图表正常渲染，Word 链接可见。HTML、DOCX 和 Markdown 经现有接口下载，分别核对 SHA-256；DOCX 包完整且有 22 张图表，Markdown 十章齐全。定向回归 21 项通过。
- 验收记录 `.runtime/artifacts/live-fund-report/verification.json`；报告原始任务与评审明细留在 `.runtime/phase2/fund_agent/jobs/report_<local-id>/`。会话及本机服务保持可用。本轮未启动第三阶段。
