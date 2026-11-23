# 第二阶段 D：基金研究 Agent 迁入

- 目标：YunAlpha 的新会话接入原基金 Agent 后端，保留两轮问答、上下文、重开、SSE、可恢复报告任务与成果读取。
- YunAlpha 基线：`f4e9abb7ddeb244447a4f854cb48188fd78b0590`；来源：基金研究 Agent 实验平台 `df71bf566f75f4106bdef94d2a8a6eba953e0683`。
- 分支与工作树：`codex/phase2-fund-agent`，`/path/to/local-user/.codex/worktrees/ad5d/综合投研平台`。只修改 `apps/fund_agent/`、本文；共享接口与代理由总控协调。
- 输入：`YUNALPHA_INPUTS=/path/to/local-user/项目/综合投研平台/.runtime/phase2-inputs`，本线只维护 `fund_agent/`，198 个普通文件，约 461 MB。哈希清单已校验；公共知识索引占主要体积。未迁旧会话、旧任务、旧 checkpoint 或凭据。
- 数据：`YUNALPHA_LAKE_ROOT` 直读正式湖 Parquet；自有 15 张持久视图与独立 MAL 查询目录库。MAL 来源 `7dd2f168af99f436bbc433bd90e1ff6cb04da957`，冻结 wheel 非 editable 安装到本应用 Python 3.12.13 venv，运行依赖在 `uv.lock`。
- 接口：`bash apps/fund_agent/run.sh api PORT`、`bash apps/fund_agent/run.sh worker`；准备桥接用 `prepare`。监听 127.0.0.1，验收端口 19241。浏览器前缀 `/agent-runtime/`，代理剥掉前缀后保持原 `/api/v1`、SSE 与恢复协议。
- 状态：每棵工作树自身 `$YUNALPHA_RUNTIME/phase2/fund_agent/`；启动器拒绝将运行目录放出本树 `.runtime/`。API 和 Worker 共用本模块数据库，不与主工作区或其他工作树共用。
- 范围外：不改其他应用、全局配置和锁文件，不合并 main、不推送、不部署、不采集、不调度。未运行旧 setup，未借用旧 venv 或旧服务。

## 交付与验证

迁入原后端、Worker、SQL、Skills、提示、JSON/手册/报告流、HTML/Word 渲染及原 Web 资源。必要变更包括路径、只读桥接、冻结资料、禁止实时补采和带浏览器前缀的 Word 地址；清单见 `apps/fund_agent/MIGRATION.md`。

原协议和样例先发布到本工作树 `.runtime/artifacts/agent-contract.md`，随后补齐实际 HTTP / SSE 返回。完整验收抓包 `agent-contract-capture.json`；独立启动器 HTTP 抓包 `agent-launcher-capture.json`。这些运行资料不进 Git。

2026-11-15 本地验收结果：

| 检查 | 真实结果与边界 |
|---|---|
| 原测试和本轮集成验收 | 146 passed，52.43 秒；之后补充“首次模型失败、无 checkpoint 的恢复”测试单项 1 passed，2.17 秒。共 147 项通过；20 条原依赖弃用警告不影响结果 |
| 基金问答 | 两轮完成：“013840.OF 是什么基金，由谁管理？”→ 银华集成电路A、方建；“它截至 2026-11-05 的最近复权净值是多少？”→ 工具真实返回 2.2371（2026-11-05）。模型采用受控回复，基金识别和指标查湖真实运行 |
| 上下文、重开、聊天恢复 | 第二轮不传基金代码，由上下文补全；中断后原 run_id 恢复，游标续传无重复用户消息；新 API 实例重开会话、活动基金与报告引用仍存在 |
| 报告任务 | 经原 chat bridge 创建任务；原 Worker 领取后，在生成正文边界注入一次中断；POST resume 后第二次领取，前五个准备阶段复用本任务新产生的 checkpoint；进度恢复到 1.0、最终为 completed_with_warnings |
| 报告成果 | 重放 2026-11-05 已验收的 013840.OF 报告，原索引流程建立新报告文档；HTML 阅读、前缀 Word 链接和三种附件下载均通过，下载 SHA-256 与冻结样本逐字节一致 |
| 数据及 MAL | 正式湖 SELECT、MCP 六个原白名单工具、基金覆盖查询及十二份单基金 JSON 契约通过；冻结 198 个输入哈希全通过 |
| 独立启动器 | 19241 API 与 Worker 心跳真实通过，页面和静态文件可读；无凭据时 run 与 resume 真实进入 recoverable，不触发外部模型请求 |
| 文件与启动检查 | 修改处代码静态检查、启动器语法及 Git 空白检查通过（保留来源文件末尾空行，检查关闭 blank-at-eof）；只提交本线目录与任务文件 |

报告回放的原验收记录：净值截至 2026-11-03，持仓和规模截至 2026-09-30，68 张图、23 张 Word 图片，保留原警告。新任务另有显式 `acceptance_historical_report_replay` 警告，报告正文、手册、Audit 和附件通过测试边界重放。报告身份、覆盖、JSON、Part 数据、进度、恢复、成果入库和索引走原实现；没有全量重新调用模型、没有将回放描述成实盘或线上模型通过。生产代码没有启用该验收回放的开关。

## 集成与待完成项

总控可直接使用已经冻结的 `fund_agent/` 输入。各工作树先按应用 README 用 Python 3.12 的锁安装，然后安装同一冻结 MAL wheel，执行自己的 `prepare`。API 与 Worker 必须使用同一个工作树的运行根。模型凭据应由 YunAlpha 环境独立提供；本线未复制旧凭据，线上模型端到端尚未验收。

总控负责新会话 UI、导航开放和 Vite 代理；本线没有改第一阶段或其他执行线的文件。前端需要按事件流分别保留 Last-Event-ID，报告恢复后不要重新读取上一次尝试的 done。当前成功报告的 done 载荷仍是原结果清单，终态应读取 GET report-jobs。

验收启动的 API PID 48756、Worker PID 48757 已停止，19241 端口释放；进程登记保留在 `.runtime/phase2/fund_agent/services.json`。工作树和任务分支保留，提交由总控集成。
