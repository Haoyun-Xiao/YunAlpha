# 原站策略库研究 · YunAlpha

2026-11-16 复核[原站策略库](https://wangzhuo.vip/CB_Backtest/strategies)：**37 条当前策略均为 `holding-plan-v1`**，包括 20 条股票、15 条可转债量化和 2 条基金持仓复刻。旧档为 36 条，两版共有 35 个策略 ID，对应运行全部更新。[完整目录](catalog.md)区分两批运行；新记录的信号和账本未经本轮重算。

建议维护四条[研究规则](rules.md)。**双低**适合做可转债基准，**高平价低溢价**用于对照更偏股性的选法，**隐波差**用于独立因子研究，**20 日动量**用于价格信号对照。两期旧隐波差样本能从归档因子直接重建选券数量、名单、顺位与等权；双低、高平价低溢价和动量的原始候选池仍有边界差异。优先做规范与冻结样例，暂不迁入业务策略。两条新 Tushare 记录共用一个持仓计划，公开备注已给出筛选、评分和加权细节；因与已有红利低波重合、且备注披露前视风险，另列[方法候补](rules.md#候补tushare-红利低波质量-ver20)。

| 规则 | 可解释性与互补性 | 可用数据 | 排序证据与建议 |
|---|---|---|---|
| R1 双低前 1/6 月度 | 价格和溢价相加，和已有股票红利低波、普林格用的是不同输入；旧本地回测已有同类 | 转债日线、期限、当日转股溢价；需冻结历史可交易池；可优先复用旧本地同类 | 描述和报告明确；旧样本池差 1 只。先作为可转债排序基准，复用现有基础 |
| R2 高平价低溢价前 1/6 月度 | 区分转股价值和溢价，可对照 R1 选出的低价券；旧本地有 Top20 | 在 R1 基础上需要同时点平价与转股溢价 | 可计算分位和；同样待补池边界。可复用 R1 的冻结池，另核对平价字段和百分位 |
| R3 隐波差最低 1/6 月度／每日 | 和价格价值信号互补；月度为主，每日只用于检验换手影响 | 归档有 `ivSpread`；正式可重复生成需正股复权和历史隐波/条款，原 IV 求解器未取得 | 两个截面名单、顺位、数量都一致；先研究快照信号，依赖新因子口径和历史覆盖，接入成本最高 |
| R4 20 日动量 Top20 月度 | 单一价格信号，可作排序实现的对照；旧本地已有同类 | 转债日线、期限、完整市场交易日历；不依赖估值 | 旧样本前 20 与顺位一致，但原池少 1 只。所需输入最少，宜做首个湖侧小样本 |

请将三类证据分开：**目录与曲线**仅表明结果可读；**目标重放与账本对账**核验已有组合和执行记录；**信号复算**必须从冻结候选和因子独立导出数量、名单、顺位及权重。即使有目标重放，也不能据此宣称取得原站的选池程序、撮合源码或模型。旧 [账本验证](rules.md#执行和事件边界) 与本轮 [截面审查](rules.md#独立截面检查) 各自绑定历史运行。

## 证据索引

`EVIDENCE_ROOT` 取旧项目 [SOURCE_MAP.md](/path/to/local-user/项目/可转债投研平台/SOURCE_MAP.md) 的同名项。无需运行旧程序或访问正式湖来阅读归档。浏览器观察和审查回执已校验归档至 `/path/to/local-user/项目/.workspace-archive/2026-11-16/strategy-research/`，`MANIFEST.json` 记录逐文件 SHA-256。后文 `.runtime/cb-strategy-research/` 指采集时位置；该目录内容在上述归档中同名保存。原始档案未复制进 Git。

| 索引 | 可查证的来源 |
|---|---|
| E1 当前界面 | 已登录 Chrome 专用标签访问回测首页、策略库和看板，只读取各页标题与 URL，随后关闭该标签；浏览器未暴露可用的既有调试端口。另以 REA 临时隔离 Chrome 只加载一次策略库，未注入凭据。证据 `ev_6e53d181ae6a0a972b1c39d417eea28c8686cfe5e6da8ac593de69b5dc097636`（`live-library.json`）；可见 `GET /CB_Backtest/api/saved-strategies`、`GET /CB_Backtest/api/research-reports` 均为 200。37 条来自界面卡片与结果链接，未以接口体或逐策略运行复验；写操作未执行。目录对齐回执 `live-vs-archive.json`。 |
| E2 前端 JS | REA 静态证据 `ev_36d3cfb89cca0a83c6ddb856193097ceab4be2064018529fee57ae636127acfe`（`rea-js.json`），解析 4 份归档业务 JS，无解析失败。对照 `convertibles/strategies.readable.js` 的两路列表 GET、`backtest.readable.js` 的计划/运行结果加载、`dashboard.readable.js` 的 `pe` 和 `momentumEligible` 分支（约 13466、13679 行）。看板直接使用快照字段分组；这段代码不能证明离线研究回测用同一个原始池。 |
| E3 旧目录和接口 | `reverse-complete/manifest.jsonl` 和 `reverse-complete/raw/backtest/{saved-strategies,strategies,research-reports}.json`；旧浏览器捕获 `captures/cb-library.json`（`ev_9a742cb878e727d17f8ce6011345b4dd874ccb250e975bc93989a137c54388fe`）。旧 `saved-strategies.json` SHA-256：`cd3d2a8ec859a62a4d37625caeaa0756412d4515fa9a14129a8bda90ac28affe`。 |
| E4 原站报告与信号 | `convertibles/evidence/raw/report-{double-low-monthly,high-parity-low-premium-monthly,iv-spread-low-valuation-top20-monthly}-v1.json`，及 E3 中对应旧卡片描述。CatBoost 原报告在同目录 `catboost-report.json`。 |
| E5 历史运行和原始截面 | `reverse-complete/raw/backtest/runs_<run_id>_diagnostics.json` 的 `imported_holdings`（日期、目标代码、权重及记载顺位），`holding-plans_<plan_id>.json` 只预览计划；`reverse-complete/raw/dashboard/market-research/<YYYY-MM-DD>.json` 是看板快照。旧脚本 `reverse-complete/verify_rules.py`、旧输出 `reverse-complete/receipts/rule-reproduction.json`；本轮 [离线脚本](check_pool.py)、[最小夹具](pool-fixture.json)与运行回执 `fixture-audit.json` 查验了 8 份输入的归档哈希。 |
| E6 执行和对账 | `reverse-complete/raw/backtest/runs_<run_id>{,_results}.json`；旧 `convertibles/ledger_checks.json`、`reverse-complete/receipts/verification.json`、`reverse-complete/analysis/02-转债策略库与回测-逆向说明.md`（含 16 条策略的绩效/状态汇总）。旧报告与账本核对直接复用。 |
| E7 本地衔接 | YunAlpha [策略交付记录](../tasks/phase1-strategies.md)；可转债旧项目 `cb_platform/catalogue.py` 与 `cb_platform/market.py`；正式湖 `docs/datasets/catalog.md`、`docs/operations/phase3-lake-datasets-20261116.csv`；旧 `reverse-complete/analysis/04-策略字段到数据湖映射.md` 的历史缺口须区分数据集登记与逐日字段覆盖。新增四日只读统计 `lake-coverage.json`，见[湖侧输入](rules.md#湖侧输入与接入顺序)。 |
| E8 新股票策略备注 | 原站公开详情：[`strategy-7bd19538451e`](https://wangzhuo.vip/CB_Backtest/api/saved-strategies/strategy-7bd19538451e)、[`strategy-bb713dbf097e`](https://wangzhuo.vip/CB_Backtest/api/saved-strategies/strategy-bb713dbf097e)，2026-11-16 两个定点 GET 均为 200。原响应保留在 `.runtime/cb-strategy-research/strategy-*.json`；SHA-256 分别为 `dd71903b399b6f434ebe2ca79af62b511d394926121dda99c618fe328880e9e3`、`475aac98e229d678e80e888128165ee223103716d49311ce814d78a64997e13c`。只核对说明与导入配置，未抓取两套运行。 |
