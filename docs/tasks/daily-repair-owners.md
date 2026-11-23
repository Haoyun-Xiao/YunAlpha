# 资金流、国家队、公募持仓日更修补

- 基线 `af268a77e1ffec85eecf0e334ae3365faa7de1d7`；分支 `codex/daily-repair-owners`。已选七台母版 `d3e47185a708e132`，冻结源 `d451f099a954d584`。
- 修改范围：`apps/vic_desks/seven_desks/owners_refresh.py`、`builders/owners_daily_*.py`、`adapters/owners.py`、专属测试和本文。正式湖、主仓母版只读；不启动服务、不触发采集。工作树独立 Python 3.12 与 `.runtime`。
- 完成判据：在原页签读到实际重算结果；以 11 月 16 日为观察上界对照正式来源、冻结历史与原公式，分别核对有数据的日期及缺口；同日两次独立构建文件逐个 SHA-256 一致。非 owners 部分由总控验收。

调用 `seven_desks.owners_refresh.refresh(x, base_data)`，其中 `base_data` 指向已选版本的 `data/`，`DailyInputs x` 已初始化日历。返回 `boards[board][component]`（含 `status/data_date/reason` 及分项日期或覆盖）与 `outputs`。依赖现有 `x.db/view/emit/files`，总控继续调用 `x.verify()`。输出清单全部登记到 `x.output`，路径位于 `x.staging/revision/owners_daily/`，以该次清单整体复制到新候选的 `data/`；没有输出的旧文件由母版继承。此命名空间自带已有增量行保全，不能套 `revision/daily` 的按日期整片合并。适配器自动优先使用 owners 完整字段和滚动窗口，老 `revision/daily/{capital-market,capital-stock,etf-flow}.parquet` 可留给兼容，不能再覆盖 owners 结果。

总控 `GROUPS`：资金流加 `sector_windows`、`margin_exchange`、`margin_securities`、`cross_border`、`dragon_events`、`dragon_seats`、`capital_balance`；国家队加 `shadow_portfolio`、`support_signals`、`support_temperature`；公募加 `company_nav`。对应键均可增加中文 `LABELS`；`quarter_top10` 只作报告期标签，不推进行情观察日。移除原两条合并占位 `margin_cross_dragon_balance`、`portfolio_temperature`。

| 分项 | 实跑及口径 |
|---|---|
| 主力及板块 | 11-16 的 `tushare.moneyflow_dc` 正式有效样本 5,185 只，原生主力合计 57.2193 亿元。5、20、60、100 日市场主力分别 −697.3728、−3,416.7965、−9,649.5304、−24,820.4777 亿元。冻结市场 formula 为逐日原生截面求和，不含固定 5000 门槛；窗口严格连续交易日，**样本随日变化**，不是 5,918 只全覆盖。11-10 至 11-13 有 5,918–5,919 行；11-16 原始分页已读空尾页（5299 原始行、5185 正式有效样本），不得把缺口补零。个股收益/成交额按原相邻后复权、完整窗重算；申万、同花顺、东财板块原生窗口及三来源个股对照独立。 |
| 两融 | 个券东财样本已接：11-13 上海 1,681 条，深圳和北交所各至 11-12。相邻交易日余额变化与融资买入减偿还分列；11-10 后东财无偿还额，沪深样本数明显少于同日交易所明细身份，不能代替交易所汇总。交易所沪深合计及融资占比仍各自到 11-09。 |
| 跨境 | 恢复此前应用漏接的 2024-08-19 后正式北向全市场 CNY 成交，11-09 两通道合计 2,162.364 亿元；实际日期和 A 股成交占比分列。北向净买停披；南向原港元和十大成交各到 11-09，十大成交不代总量，人民币数据和港元不混加。 |
| 龙虎榜 | 事件到 11-16，31 个股日，27 个有可加总金额，净额 7.1126 亿元；冲突金额保留空。接入当日正式换手率 31 条，更新主题、五日常客和可用次日收益；11-16 不生成未来收益。席位实际止于 11-09。 |
| 资金平衡 | 至 11-16 有交易日网格，按原行项计算已观测分项。份额净申购经济日到 11-13、11-16 可见；股东减持/IPO没有新合格观测，不补零。现有股票 ETF 截面完整性没有认证且缺同口径净买/人民币南向；不生成严格净额。 |
| 国家队 | 沿用 26Q3 已核实组合，11-02 收盘调仓，11-12缺口恢复后重算：11-16 窄口径 NAV 350.60455、中口径 341.14499。组合、回撤、月度超额、成分涨跌和区间贡献按原公式续算。11-13 宽基池 239 只有效，净申购 −125.8299 亿元，11-16 才可见；原阈值信号和 252 观测窗本地托底温度分别重算，11-13 温度 48.0915。 |
| 公募 | 11-13 真实公司等权、前一交易日已公开净资产加权净值；示例易方达有效 211 份、加权 210 份，等权 −1.56991%、规模加权 −2.25029%。新净值分区未给出新增正规模；继续使用已披露历史真实净资产，示例最后权重信息 08-26。基准和区间收益用实际行情及各公司数据日。正式持仓最新公告 10-27，半年报全持仓保持 26H1、季报前十大保持 26Q3；将来若发现新披露，仍需先经过原 A/C 份额归并、覆盖与同口径披露构建。本次无新披露可推动这两项。 |

已追溯冻结版 `data/owners/input_manifest.json`、`data/revision/owners/input-manifest.json`、正式湖 `scripts/seven_compute_owners_20261113.py`（资金、两融、龙虎榜、ETF、公司净值和影子组合原公式）、本仓 `owners_completion.py`、`revision_owners.py`、`owners_summaries.py`、`owners_signals.py`。首次验收时，正式湖的11-12 `daily_bars` 只有2,183条Tushare行，普通沪深日线/因子尚未发布，含该日的价格/成交额窗为空、组合按末价结转。该历史缺口现已在湖侧 `69b630b` 定向恢复：仅补11-12缺键，正式日线8,070条、hfq因子7,793条，原有行保留。应用真实复测已改为验证恢复后的正式价格、连续收益和当日估值，不能再将11-12称为全沪深行情缺口。未证实的个别缺键仍不填补。

原始归档核对：`lake/raw/tushare-margin/**/margin_exchange_daily.json` 最大业务日 11-09，11-10 是抓取时间；`lake/raw/hkex/**/northbound_turnover_public_history_candidate.parquet`、`lake/raw/eastmoney-cross_border/**/cross_border_daily_stats.parquet`、`lake/raw/eastmoney-dragon_tiger_seats/**/dragon_tiger_seat_observations.parquet` 均最大 11-09。未发现交易所两融、跨境或龙虎榜席位有晚于 11-09 的待投影原始响应；同日股票行情和北向应用漏接分别作为独立问题记录。龙虎榜换手是本轮湖侧正式字段投影，应用已读取，不使用替代字段。

从任务工作树根执行：

```sh
export OWNERS_TEST_LAKE=/path/to/local-user/项目/多资产智能投研平台-v2/lake
export OWNERS_TEST_BASE_DATA=/path/to/local-user/项目/综合投研平台/.runtime/phase2/vic_desks/seven/versions/d3e47185a708e132/data
PYTHONPATH=apps/vic_desks YUNALPHA_RUNTIME="$PWD/.runtime" \
  .runtime/venv/bin/python -m pytest -q apps/vic_desks/tests/test_owners_refresh.py \
  --basetemp "$PWD/.runtime/tmp/pytest-owners"
```

验证覆盖两次正式湖构建、全部 23 个 owners 页签、历史观察日的 ETF T+1、正式来源/冻结数据对照和缺口检查。工作树未启动服务或切换主选版。


## 湖侧11-12恢复后的应用复验

本次基线为机构线交付 `93c5770`。正式湖已由总控合入 `69b630b`；本次只读正式湖，不重复发布，不启动服务或切主选版。继续以已选七台 `d3e47185a708e132` 为母版，在本工作树独立临时目录构建两次。

`test_owners_refresh.py` 保留原资金窗口、公司NAV、23页接口、T+1和逐文件SHA-256复跑断言，并把旧缺口断言替换成可独立计算的真实输入对照：

- 茅台11-12成交额4,536,814,518元，相邻正式后复权日收益 **0.568352%**。11-16完整20日收益 **9.730786%**；测试从正式交易日历取21个连续交易日收盘，以正式hfq因子计算逐日收益复合，并与首尾后复权价比值及应用窗口同时核对。11-12与11-13的20日收益分别恢复为7.408225%、7.995485%。
- 窄口径影子组合35只成分，以已验证的11-02收盘调仓权重和正式行情/因子独立计价，核对11-11、12、13、16的组合价值、净值及新价权重，另验证11-12当日每只成分的价格来源日期就是当日。11-12组合收益 **0.907150%**、NAV **339.126917**、新价权重约100%，11-13收益 **1.442019%**。中口径11-12收益 **0.820305%**、NAV **330.301345**、新价权重99.994154%；11-16 NAV **341.144985**。状态 `days_without_fresh_prices=[]`，不再出现整日末价结转。
- 恢复行情后复跑暴露龙虎榜按股日/题材/五日常客聚合的浮点累加顺序不稳定；只在原SUM/AVG中固定证券或交易日顺序，金额公式、样本和窗口不变。没有删除或降低文件一致性验证。

真实复验 **3项通过（27.75秒）**，包括两次构建逐文件SHA-256及23个页签。复验仍使用本文原命令，仅临时目录改为 `.runtime/tmp/pytest-owners-restored`，日志 `.runtime/logs/owners-restored-validation.log`。验证完成后临时构建清理，日志保留；此提交由总控cherry-pick，不包含湖发布或配置。
