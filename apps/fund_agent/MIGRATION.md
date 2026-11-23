# 基金 Agent 迁入清单

应用来源提交为 `df71bf566f75f4106bdef94d2a8a6eba953e0683`；YunAlpha 起点为 `f4e9abb7ddeb244447a4f854cb48188fd78b0590`。迁入源代码使用 Git archive 读取该提交，没有复制来源目录的未提交改动。

| 内容 | 去向与处理 |
|---|---|
| 原 FastAPI、Deep Agents、基金工具、证据、记忆、持久化 | 原 `src/fund_research_agent`；会话协议和数据库迁移保留 |
| 报告 Worker、手册流、Part1–10、Audit、HTML/Word | 保留原实现；路径统一到自有输入和运行目录；不扩展业务 |
| 提示与 Skills | `skills/`、`deep_skills/`、`legacy_port/handbook_engine/skill/`、`legacy_port/report_engine/prompts.py` 保留 |
| 旧 JSON 引擎和渲染资产 | `legacy_port/fund_json/`、`legacy_port/renderers/` 保留；只迁当前运行使用的副本，未迁重复的 `legacy_port/snapshot/` 历史源码 |
| SQL / 原 Web | `sql/` 全部迁移；`web/` 原资源全部保留 |
| 原测试 | 迁入；修正单基金契约输出目录和历史换手率样本的披露日期，新增迁入验收 |
| 部署、采集、软链接配置、旧 setup | 未迁入执行入口；未运行 |
| 正式湖 | 直接只读 Parquet；自有 bridge 的持久视图保持原 `union_by_name` 读取口径，补足原报告需要的 `index_bars`；不打开湖的正式 DuckDB 写连接 |
| MAL | 从湖项目冻结提交 `7dd2f168af99f436bbc433bd90e1ff6cb04da957` 构建 wheel，自身 venv 非 editable 安装。其查询目录库写在本工作树；on-demand 禁用 |
| 基金清单 / 全样本清单 / NAV 补充 | 复制成普通文件到 `YUNALPHA_INPUTS/fund_agent/` |
| 公共材料索引、手册和路演 | 仅复制指定索引、`.md/.docx` 手册与 `.txt/.md` 路演；无链接；运行只读 |
| 回放 | 仅通过版本检查的公开网页资料、固定基金的季报与规模补充，以及 2026-11-05 已验收的 013840.OF 报告和手册 |
| 旧会话 / checkpoint / 任务库 / 凭据 | 不迁入，运行时不回读 |

首轮迁入与原代码的差异：启动器、配置与输入路径、独立桥接、首轮暂时关闭实时补充、无模型凭据快速进入可恢复状态、首次 checkpoint 前失败后的恢复、带代理前缀的报告下载。生产运行没有加载受控模型或固定报告回放的隐藏开关；它们仅通过验收测试注入。报告进度、幂等、lease、Checkpoint 和索引仍由原代码执行。

`scripts/freeze_inputs.py` 是一次性取数工具，显式接收来源路径、拒绝覆盖已有冻结目录，输出有 SHA-256 的清单。日常安装只读已冻结的 MAL wheel，日常启动使用冻结输入及正式湖，无旧源码和服务依赖。随后已恢复 [Agent 实时网页、定期报告与规模补充](../../docs/tasks/live-fund-report.md)，上游请求只写本机缓存或任务产物。
