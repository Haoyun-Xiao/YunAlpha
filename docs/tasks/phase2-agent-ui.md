# P2-A：基金研究 Agent 会话界面

- 基线：`ef67be4e578c0144ff509467922f8a127e518411`；分支 `codex/phase2-agent-ui`；工作树 `/path/to/local-user/项目/.worktrees/综合投研平台/phase2-agent-ui`。
- 修改范围：`apps/web/src/`、`docs/tasks/phase2-agent-ui.md`。不修改启动器、代理配置或 Agent 契约。
- 依赖：[第二阶段集成约定](../PHASE2_CONTRACT.md)，以及 D 线 `.runtime/artifacts/agent-contract.md` 中原协议的会话、消息、SSE、报告任务与成果样例。运行时走同源 `/agent-runtime/api/v1`，由总控挂载 D 线。
- 完成判据：新建会话、历史列表、深链接与重开；回复增量与最终文本、工具与工作流进度、断线游标和原协议恢复；报告以任务查询为准，成功时可打开原版并按成果 ID 下载；窄屏可操作，原投研台和自检保留。
- 验证：构建与类型检查；Chrome 走本机受控样例（Web 19210、API 19211），不读写旧项目或运行真实研究。验收证据放本工作树 `.runtime/artifacts/phase2-agent-ui/`；结束后停止本任务服务。真实 Agent 后端由总控验收。

本轮实现沿用 YunAlpha 侧栏，开放新建会话和个人空间，历史会话由 Agent API 持久化；选择的 session_id 写入 URL，浏览器回退与刷新后可重开。会话页支持最终回复的基本 Markdown、原协议工具和计划进度，以及从消息、会话上下文和 run 事件发现的报告卡片。未新增研究逻辑、独立报告表单或模型配置页。

SSE 通过 fetch 读取并传 Last-Event-ID，按流去重；聊天 done 的 answer 替换增量文本。重开会话复用已保存消息、事件及游标，不把已保存答案再插一次。离开页面取消订阅与重连。报告事件只触发任务查询，GET job 决定阶段、恢复与完成状态；旧 done 不会使新尝试提前完成。成果只使用当前任务的 artifact_ids 与元数据中的 artifact_id，原网页走 job view；不把服务端 path 当 URL。

仅在 dependencies/health 的 model_key 明确为 false 时提示“尚未配置研究模型，配置后可开始研究”，历史会话和成果仍可打开。聊天原 recoverable 状态保留继续入口；旧只读会话不能发消息。

验证结果：

- 构建（含 TypeScript 检查）与 git diff --check 通过。
- 协议检查通过：UTF-8 分块、CRLF 帧、数字游标重连和去重、最终答案替换、忽略非安全增量、历史不重复以及 AbortController 关闭订阅。
- Chrome 通过受控 HTTP/SSE 样例走通两轮消息、断线重连、聊天恢复、持久历史重开、直接链接、浏览器前进后退、个人空间搜索、只读与找不到会话状态、报告校验失败后继续、旧 done 与权威任务状态不一致时的显示、完成且有提示的报告、原版新标签打开、模型缺失提示的 false/true 分支、360px 窄屏与键盘操作。
- 另行核对离开会话会中止活动 SSE 且停止重连，重开运行中会话从快照游标 10 继续。页面脚本错误为 0。
- 证据位于本工作树 `.runtime/artifacts/phase2-agent-ui/`：六张截图、browser-check.json、leave-check.json、protocol-check.json、controlled-requests.jsonl、build.txt、lifecycle.json。所有截图与聊天/报告回答均来自受控验收样例，未进入产品代码。临时脚本已清理，Web 19210 与受控 API 19211 均已停止。

已知限制：本分支未合入 D 的迁入后端及总控代理挂载，真实 API、Worker 和模型的端到端验收由总控执行。本轮没有调用真实模型或重新生成研究报告；当前缺少模型凭据是总控已核实的实际环境限制。文本渲染支持常用 Markdown 块和安全链接，不渲染回复内的原始 HTML；完整报告通过原版页面阅读。
