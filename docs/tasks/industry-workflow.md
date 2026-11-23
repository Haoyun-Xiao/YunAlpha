# 产业链研究后端迁入

YunAlpha 基线 `dcb17f582548181b8a18ed3e7727b200d7c6756b`，任务分支 `codex/industry-workflow`。产业链研究 Agent 源码基线 `6ceb3ac`，作为只读接入来源。本分支修改 `apps/fund_agent/src`、新增 `apps/fund_agent/tests/integration/test_industry_workflow.py` 和本文件。前端、依赖清单、锁文件和主站启动由集成分支负责。

- 七阶段仍为 prepare → structure → snapshot → segments → review → synthesis → publish。保留原公司映射、PIT 财务、证据校验、六维评分、阶段与环节检查点以及完整 HTML / Markdown / JSON 报告。正式湖只用 Polars 读 Parquet。发布阶段仅写本地报告和 FTS 检索索引，未迁入原湖写入实现、旧会话、数据库、成果和独立站点。
- 唯一 Skill 在 `apps/fund_agent/src/fund_research_agent/deep_skills/industry-research/`。统一 Deep Agents 与环节研究员都挂载这个 Skills 目录。模型和凭据继承现有 ModelGateway，网页搜索继承 YunAlpha 的 `web.mode`、`web.search_depth` 和 `TAVILY_API_KEY(S)`。产业链保留原长调用预算；可由 ModelGateway 的 `industry_structure`、`industry_researcher`、`industry_synthesis` 角色覆盖，未配置时使用现有 `researcher` / `report` 的模型和凭据。
- 现有 `agent-worker` 同进程轮询产业链队列，默认一个产业链任务并发，与基金 Job 独立。业态状态在 `$FUND_AGENT_VAR_ROOT/industry/state/industry.sqlite`，图检查点另存同目录，成果位于 `industry/jobs/<job_id>/output/`。常规启动路径为 `.runtime/phase2/fund_agent/industry/`。无额外服务或端口。本次验证用 ASGI 测试客户端，未登记监听端口。
- 新增运行依赖只有 `markdown-it-py>=3,<5`（传递依赖 `mdurl`）；已由集成线更新锁文件。正式湖 Parquet 读取不依赖 lake SDK 或原项目环境。

统一 Agent 工具：`industry_research(anchor, as_of?)` 只在用户明确要求完整报告时提交，同一 YunAlpha run 幂等。状态、结果、恢复、取消分别用 `industry_status`、`industry_result`、`industry_resume`、`industry_cancel`，省略 `job_id` 时用当前会话最近任务。任务关联 YunAlpha `session_id` 和 `run_id`。会话消息的 `metadata.ui_blocks` 和 SSE `report_job_created` 使用同一个带 `type: "industry_job"` 的块，包含 `job_id/title/status/view_url`。后台状态由前端任务卡查询。

现有 Agent API 新增：

| 路径 | 返回值 |
| --- | --- |
| `GET /api/v1/skills` | `{items: [{id, name, description, instructions, starter_prompt?, tools?, references?}]}`，扫描实际挂载的 Skills；`references` 为 `[{name, content}]`，`name` 是 Skill 目录相对路径 |
| `POST /api/v1/industry-jobs` | 202；直接验收入口，接受 `anchor, as_of?, session_id?, idempotency_key?` |
| `GET /api/v1/industry-jobs/{job_id}` | 任务状态、七阶段名、0–100 进度、主题、报告 ID、错误和缺口，以及 `view_url`、`artifacts: [{label, url}]` |
| `POST /api/v1/industry-jobs/{job_id}/{resume,cancel}` | 更新后的任务状态；不符合恢复条件返回 409 |
| `GET /api/v1/industry-jobs/{job_id}/view` | 完整自包含 HTML；未完成返回 409 |
| `GET /api/v1/industry-jobs/{job_id}/download?format=html\|md\|json` | 对应文件下载 |

浏览器链接由现有 `base_path` 生成，工作区代理下为 `/agent-runtime/...`。状态为 `queued → running → completed/completed_with_warnings`，中断可进入 `recoverable`，也可取消。

验证：本工作树独立依赖环境中，新增集成用例和基金 Tool Bridge、DeepAgent、恢复、模型兼容回归共 32 项通过。受控模型走真实 Skills 挂载、统一会话、工具网关和任务卡持久化。后台环节被中断后重启 Worker，已完成阶段和环节未重跑；重试生成完整 HTML 与可下载 JSON，同一会话可以查回报告内容，测试湖文件集合与内容无变化。另用实际正式湖 Parquet 只读核对了 `300308.SZ` 身份和 2026-11-06 快照，Skill 校验通过。

真实验收由集成人员在 main 的新统一会话执行，例如“请为我生成一份关于 AI 数据中心 800G/1.6T 光模块的完整 A 股产业链研究报告，注明数据截止日、公司映射证据和判断失效信号。”先验收 Skill、工具调用和任务卡，再打开完成后的报告、下载成果并追问“刚才报告里哪些证据支持公司映射？”。需要有效模型和 Tavily 凭据，`web.mode` 用 `auto` 或 `live`。取不到可读证据时任务保留断点进入 `recoverable`，修复上游后可恢复。受控测试不代替真实模型验收。
