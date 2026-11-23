# 第三阶段 · 情绪、筹码、资金流

- 基线 `1b779acf3a96c384781464ae323c780829aaafc3`，分支 `codex/phase3-market-desks`，工作树 `/path/to/local-user/项目/.worktrees/综合投研平台/phase3-market-desks`。
- 范围：`seven_desks/adapters/market.py`、`prepare_market.py`、新增 `prepare_phase3_market.py`，专属测试、`evidence/phase3-market-desks/` 和本文。资金流原适配器及共享服务文件不修改；如需接线，交总控。
- 原数据快照 `249854a814731c52`，正式湖严格只读。新样本与可合并 `revision/market` 输入留本工作树 `.runtime/`。REA 核对以已有静态证据和独立浏览器标签的代表行为为限；DS 已知额度耗尽，不重复探针。
- 验收：逐台核对原站及本地样本、日期、单位、公式和代表筛选；讲清未接入或不等价的指标，保留旧可用能力。完成三台真实数据增量、候选构建办法、代表交互证据和提交。验收端口 19231。

## 接入与验收

- 增量包：`.runtime/phase2/vic_desks/seven/revision-market/`，含 `phase3-market-manifest.json` 和 6 份 Parquet。包内日期和 SHA-256 可追溯；未覆盖原 `chip-cost-position`。候选 `6897dca0d3073de0` 只供本线验收，完整目录在本工作树 `.runtime/phase2/vic_desks/seven/versions/`；估值仍到 11-13，情绪／筹码／资金按源数据到 11-09 或更早。最终合并后需重建版本哈希。
- 总控在共享 server 对 `capital_flow` 的 `adapter(board).panel(...)` 结果调用 `seven_desks.adapters.market.capital_additions(panel, params, store, result)`（序列化及缓存之前），或在 owners 等效位置调用；无需改旧资金查询。旧运行哈希白名单需总控随合入一起更新。主仓最终候选可用保留 `valuation_cutoff` 的统一 store 构建；专属脚本 `python -m seven_desks.prepare_phase3_market --base BASE_DIR --out MARKET_REVISION_DIR --compose [--overlay REVISION_OWNERS_DIR] [--overlay REVISION_CONTRARIAN_DIR]` 亦可在最终合并代码下克隆/合成，`--lake LAKE_ROOT` 可先重建增量。
- 4 个专项测试覆盖情绪时间防穿越、筹码原生值和筛选、资金跨境与净买、估值日期继承。三台在同一浏览器服务通过代表交互，无页面和接口错误；本线只在验收进程临时挂载上述资金函数，不改共享文件。浏览器验证版本 `90170a714f9dfe0d` 后仅增加 E3 最近源公告日列；最终候选重跑专项测试。
- [逐项样本、公式与缺口](../../apps/vic_desks/evidence/phase3-market-desks/RECONCILIATION.md)；运行证据保留 `.runtime/artifacts/phase3-market/`。验收端口 19231 已释放。
