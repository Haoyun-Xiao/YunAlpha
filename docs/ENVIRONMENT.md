# 本机开发环境

在仓库或任务工作树根目录执行：

```sh
./scripts/workspace.sh setup
./scripts/workspace.sh start
./scripts/workspace.sh check
./scripts/workspace.sh stop
```

第一次setup按锁文件下载依赖；uv固定安装到本工作树 .runtime/tools，API使用 apps/api/.venv，前端使用自身 node_modules。
后续setup可以重复执行，不覆盖配置、数据库或结果。初始化不会自动启动常驻服务。

默认访问 http://127.0.0.1:19100 打开 YunAlpha 投研台，左栏可切换策略和环境自检。config/local.json 可以设置 web_port、api_port；多个工作树分别指定不同端口。无需复制数据湖。

从左栏打开“环境自检”（`/?view=environment`），可检查后端连接、数据目录是否可读、Worker 进程是否存在。点击“开始自检”登记任务；Worker 未启动时等待，启动后继续。任务 ID 保存在网址参数，刷新后仍可查看，原有 `/?task=...` 链接也可打开。
Worker仅顺序执行自检；重启会重新执行未完成任务。结果在 .runtime/artifacts，任务库在 .runtime/state/tasks.sqlite。
错误会保留在任务中，不自动重试失败任务。新任务可重新自检。

环境自检 API：GET /api/v1/health、GET /api/v1/environment、POST /api/v1/self-checks、GET /api/v1/self-checks/{id}。投研台和策略接口见 [第一阶段目录接口](PHASE1_CONTRACT.md)。
这是一套本机开发环境，不包含云端部署、用户认证或生产任务编排。

集中检查：

```sh
(cd apps/web && npm run build)
(cd apps/api && .venv/bin/python -m pytest -q -p no:cacheprovider)
```

stop只停止本工作树登记且命令身份一致的进程，不按端口杀进程。日志在 .runtime/logs。
回收开发工作树前先stop；依赖可按后续任务需要保留或移除，不能把依赖软链接与实际业务产物混为一谈。现有工作树回收工具会拒绝未处理的额外依赖目录。

## 第二阶段迁入应用

本机配置 `phase2_modules` 登记要启动的 `agent`、`fof`、`seven`、`crowding`、`pring`。使用同一个 `setup/start/stop` 入口，各模块独立锁定依赖并由当前工作区登记进程。默认端口分别为 19102、19103、19104、19105、19106；可用 `phase2_ports` 按模块名改写，不能与前后端或其他工作树重复。

浏览器始终从 YunAlpha 的 19100 访问：基金 Agent 的 `/agent-runtime/`，FOF 的 `/workspaces/fof/`，七台的 `/workspaces/vic-desks/`，抱团的 `/workspaces/crowding/`，普林格的 `/workspaces/pring/`。前端代理去掉外层前缀，原应用内部 API 与交互保留。

迁入的冻结输入存放本工作区 `.runtime/phase2-inputs/{fund_agent,fof,vic_desks,pring}`，不进入 Git，也不是指向旧项目的链接。一次性迁入工具与输入说明在三个应用各自的 README；初始化和日常启动不会重新读取旧应用目录。每个工作树的可写状态位于自身 `.runtime/phase2/`，FOF 和普林格的种子只首次复制，重启不覆盖用户组合或策略。正式数据湖仍为单独的只读来源。

`setup` 为基金 Agent 安装冻结的 MAL wheel；首次 `start` 准备本工作树的只读湖视图。源研究材料和知识索引已经独立复制，旧聊天、检查点和历史任务库没有迁入。Agent API 和 Worker 自动从本机 `config/.env.fund-agent.local` 读取 `OPENCODE_GO_API_KEY` 和 `TAVILY_API_KEYS`，已有进程变量优先。该文件由 Git 忽略。更新配置后重启本工作区。

本机体验修复已走通 Agent 真实模型问答和基金工具调用。Agent 实时网页搜索、定期报告和规模补充已恢复，补充数据只写本机运行目录。真实报告执行见 [验收记录](tasks/live-fund-report.md)。普林格当前冻结行情默认回测至 2026-06-10、信号至 2026-03-31。FOF 的冻结净值和基准截至 2026-06-30。七台、公募抱团各自显示来源日期与数据缺口，不自动更新快照。

基金深度报告库从 `.runtime/phase2-inputs/fund_reports/index.json` 加载。已有报告和资产为本机独立副本；加载或浏览报告不访问原项目和云端。导入方式见 [报告库任务记录](tasks/fix-report-library.md)。

## 第三阶段看板选版

第三阶段验收后的看板候选，可在本机配置 `phase3_versions` 中指定 `seven` 和 `crowding` 版本。启动器传入 `YUNALPHA_SEVEN_VERSION`、`YUNALPHA_CROWDING_VERSION`；七台使用已有 `--version` 接口。候选及输入先保存在主工作区自身运行目录，不能指向待回收工作树。空配置仍使用原冻结版；这里仅选择已生成结果，不触发采集或构建。

## 环境初始化验收（2026-11-06）

- React/TypeScript生产构建通过。
- 后端两项针对性测试通过：任务提交/查询/执行/持久结果，以及失败/重启恢复。Starlette有一项httpx测试客户端弃用提示，不影响通过。
- 浏览器真实提交自检成功，刷新后结果保留，控制台错误0。
- 停止本任务Worker后，API仍能提交任务并保持queued；重启本工作区后同一任务完成。
- 结果文件仅写当前工作区 .runtime，未打开正式数据湖文件或数据库。
- 本轮不重新执行旧平台测试。验收进程在收尾停止，主工作区可按上方命令重新启动。
