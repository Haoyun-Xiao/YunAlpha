# P1-C · 策略目录与已有成果

- 基线：`16468f154a07acb61beb6a540e34d2673d0987d4`；分支：`codex/strategy-module-entry`；工作树：`/path/to/local-user/.codex/worktrees/520e/综合投研平台`。
- 执行：对话 C；应用挂载与前端联调由总控负责。修改范围只有 `apps/api/strategies/` 和本文。
- 接口依赖：[第一阶段目录接口](../PHASE1_CONTRACT.md)。`strategies` 导出 `router`，由总控在 `/api/v1` 挂载。目录 `GET /api/v1/strategies` 返回 `{"items": [...]}`；缺源时目录标为 `unavailable`、入口为空，直接访问缺失成果返回 404。
- 运行位置：本工作树忽略的 `.runtime/`；本机预留 Web `19130`、API `19131`。本次用进程内 TestClient 验证，无服务进程。

## 首期交付与来源

- **普林格周期**：`GET /api/v1/strategies/pring/index.html` 打开原始首页、组合介绍、组合的诞生。项目根从 [索引](../../config/projects.json) 的 `pring` 定位，来源基线为 `2dab86a2f2d8367a004f7b695306b7eee4a4ce14`。本机只读转接这三页和原页直接引用的 95 张 PNG、18 个可下载的 Excel 附表。未列出的文件不对外提供。首页另链接 [旧站宏观组合回测](https://howardyun.cn/Macro_Research_Platform/backtest/)，注明需原站登录；匿名访问返回 401，本轮未验证登录后的操作。首期未迁入该回测的执行或策略保存，也未接入实盘跟踪。
- **红利低波**：`GET /api/v1/strategies/dividend-lowvol` 展示 `cfo_timing80` 冻结目标历史重放。项目根从索引的 `cb-strategy` 定位，来源项目基线为 `bea8b052d1a486b2e65a80fb523daf172b4ace95`。读取其 `var/acceptance-dividend-v1/validation-summary.json` 及运行 `run_d6d6083e7ce64dc8ac601175c148a70e` 的验收结果；只提取所需的指标和逐日净值，不传输整份结果。检查两文件指标和指纹相同、净值日期与验收窗口一致。样本为 2015-05-04 至 2026-08-10（2,741 个交易日，基准 H20269.CSI）；冻结目标文件 SHA-256 为 `b11c64653fa89c3a0ce27b308f9a0caacb4786b01fb10e73dc5c17cd797638b1`，实读 34 期，末期为 2026-05-06。该日之后沿用最后一期目标，不重新选股或调参。页面只用这次 `price_only_v1` 统一执行的结果，不混入原整手研究数据。
- **FOF**：目录标为 `planned`，没有入口。后续从索引的 `fof` 接入完整工作台，保留基金池、手工分组与配置、调仓计划及 Excel 导入、资金流与费率、日／周／月回测、组合保存与快照、持仓和风格穿透。迁入时保持组合与执行状态独立于旧站和正式数据湖。

目录依赖本机索引和上述现成文件；红利低波依赖已保存的验收运行，不读取当前数据湖行情。源文件缺失不会尝试重跑，也不回退到不同口径的旧指标。没有新增依赖、复制大成果或写入旧项目。

## 验证与完成判据

进程内挂载 router 后，目录两项可用、FOF 未开放；从普林格首页追踪核对三页和全部 113 个引用资源，非清单路径返回 404。红利低波结果页含验收指标、样本区间、冻结末期、执行口径及两条各 2,741 点的净值曲线，不接收提交。用缺源配置验证两项自动变为不可用，成果地址返回 404。API 原有自检测试 2 项通过。

本机复查脚本和结果保存在 `.runtime/artifacts/phase1-strategies-verify.py` 和 `.runtime/artifacts/phase1-strategies-verification.json`。总控挂载后需从策略目录实走两个入口，并核对浏览器中原报告的导航和附表下载。
