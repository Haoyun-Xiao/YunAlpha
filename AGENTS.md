# YunAlpha · 综合投研平台

本项目在当前生效的全局规则 `/path/to/local-user/.codex/AGENTS.md` 基础上补充约定。

- 第二阶段已完成本机迁入：八台、FOF 和原基金研究 Agent。新增模块的维护源码在本仓，冻结输入和运行状态不进 Git。基金 Agent 从忽略的本机配置读取模型凭据，在线问答已验收；中台和云端发布尚未开始。
- 产业链研究工作流现位于 `apps/fund_agent/src/industry_agent`，与基金研究共用统一 Agent API 和 Worker；唯一方法位于 `deep_skills/industry-research`。Skills 页面读取真实挂载目录，产业链成果和队列位于本工作树 `.runtime/phase2/fund_agent/industry`，正式湖只读。迁入与验收见 `docs/tasks/industry-skill.md`。
- 第三阶段八台本批修复已在本机合入并切换；逐台口径与暂缓项见 docs/tasks/phase3-control.md。数据湖已完成18:00调度切换，首次真实定时触发与数据读回待验收；未完成前不得称第三阶段全量验收通过。
- 先读 README.md、config/projects.json、docs/ARCHITECTURE.md、docs/ROADMAP.md 和具体任务说明；第一阶段协作见 docs/PHASE1_PLAN.md。
- 投研台按具体看板、工具和报告模块组织，资产类别用于分组；Agent 与策略独立。保留导航不代表本阶段实现全部功能。
- main 是集成基线。具体开发使用短期 codex/ 分支与独立工作树，2～3条独立任务并行。
- 任务说明必须写明基线提交、允许修改的相对目录、接口依赖、验证方法和完成判据。
- 外部取数、数据源原始查询和补数调用统一使用 `opencode-go/deepseek-v4.1-flash` 子对话执行。复杂公式、代码审查和已落盘数据的计算修复可交其他模型，但任务说明必须明确禁止其代替 DS 发起取数。
- 公共接口、共享类型、根配置、依赖锁文件和数据库迁移由集成人员统一协调。任务完成后提供提交和检查结果。
- 旧项目位置和维护基线见项目索引；它们是接入来源，未经任务授权不得通过绝对路径修改。
- 正式数据湖默认只读。需要补数或发布研究成果时，另走湖侧受控流程。本工作区脚本不触发采集或正式写入。
- 每个工作树使用自身 .runtime/ 下的状态、日志、临时文件和产物。禁止多个工作树共用业务数据库。
- 运行服务前登记端口，启动后记录进程身份；收回工作树前先停止该任务自己的服务，不能停止其他任务。
- 对照测试冻结必要输入；不能用数据湖最新日期冒充当前任务完整性。
- 不提交本机配置、密钥、原始大数据和运行库。不为隐藏未提交源码添加忽略规则。
- 分支通过相关检查后由集成人员合入 main；工作树清理前保全运行资料，默认保留任务分支。
- 维护项目事实时同步更新决策与交付记录。CLAUDE.md 如需兼容，仅引用本文件。

- 八台已接既有湖任务结束后的日更入口 scripts/refresh_desks.sh，逐台刷新和冻结范围见 docs/tasks/desks-daily-control.md；不是全部指标当日化。策略库四类规则研究见 docs/strategy-research/README.md，尚未新增在线策略。
- 数据湖与投研台双重日更的入口、配置与中台交接见 [日更说明](docs/tasks/daily-update.md)。修改湖分组、看板输入、回执或 DSH/OpenCodex 启动方式时，同步核对该说明和 `config/daily-update.json`。
