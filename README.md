# YunAlpha · 综合投研平台

[在线体验](https://www.howardyun.cn/YunAlpha/) · [整体架构](docs/ARCHITECTURE.md) · [公开实例说明](deploy/showcase/README.md)

YunAlpha 将八个投研看板、基金报告库、FOF 组合管理、普林格周期和红利低波策略整合进同一个研究工作台。React/TypeScript 前端连接独立 API 和业务后端。Agent 将基金研究与产业链研究拆成取材、分析、审核和导出步骤，通过后台任务生成可追溯成果。

第二阶段已完成本机迁入：八个投研台、基金报告库、FOF 组合工作台、普林格回测和基金研究 Agent 可在 YunAlpha 使用，红利低波保留历史回测结果。Agent 已通过真实模型问答验收。本机体验修复见 [交付记录](docs/tasks/fix-local-experience.md)。

产品采用 VIC 式布局，核心为模块化投研台、策略与统一研究 Agent，另预留“深入”。产品品牌为 YunAlpha，项目目录暂不变。

从这里开始：

- [启动环境](docs/ENVIRONMENT.md)：安装、启动、检查与停止。
- [项目索引](docs/PROJECTS.md)：本地位置、维护基线、云端入口和未来归属。
- [整体架构](docs/ARCHITECTURE.md)：导航、模块和数据边界。
- [阶段路线](docs/ROADMAP.md)：现有交付与后续顺序。
- [第一阶段分工](docs/PHASE1_PLAN.md)：一个总控、三个执行对话。
- [第二阶段交付](docs/PHASE2_PLAN.md)：迁入范围、源码基线和验收结果。
- [第三阶段计划](docs/PHASE3_PLAN.md)：补齐八个投研台、整理数据湖与本机日更。
- [工作树操作](docs/WORKTREES.md)：创建、初始化、检查与收回。
- [工作区整理交付](docs/WORKSPACE_BASELINE.md)：本轮提交、归档、保留项与验证。
- [可行性报告](docs/FEASIBILITY.md)：历史调研与已有能力，当前方向以整体架构和路线为准。

在本目录执行：

```sh
./scripts/workspace.sh init
./scripts/workspace.sh setup
./scripts/workspace.sh start
./scripts/workspace.sh check
./scripts/workspace.sh stop
```

工作区入口使用现有Python3.12。setup安装本项目锁定依赖；start启动本项目服务，stop仅停止本项目进程。旧项目保持原样。
配置模板在 config/environment.example.json；本机覆盖在 config/local.json，不提交。
报告、策略冻结输入和应用运行状态各自保存在本机 `.runtime/`，不进 Git。正式数据湖保持独立，默认只读。
