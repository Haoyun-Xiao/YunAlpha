# 第三阶段 · 市场估值

- 基线：`7652ed4c476f9df9ee557c2039d1f9aefb3f194d`；分支：`codex/phase3-valuation`；独立工作树：`/path/to/local-user/项目/.worktrees/综合投研平台/phase3-valuation`。
- 范围：`apps/vic_desks/seven_desks/` 内估值功能及其准备逻辑、应用专属测试与 `evidence/phase3-valuation/`、本文。共享启动和依赖由总控维护。
- 冻结输入：第二阶段七台快照只读，正式湖只读。只在本工作树 `.runtime/` 写候选、样本、证据和运行状态；端口 19231。
- 完成判据：同日期、同样本核对 VIC 四个估值页签、代表筛选和下钻；用湖数据重建可验证子集，保留原快照与切换办法；区分已对账、明确本地口径和未解算法。验证后提交。

## 交付与切换

候选 `249854a814731c52`，路径：

`/path/to/local-user/项目/.worktrees/综合投研平台/phase3-valuation/.runtime/phase2/vic_desks/seven/versions/249854a814731c52`

这是全七台快照：从 YunAlpha 自有冻结版 APFS 独立克隆原输入，仅替换/新增八份估值 Parquet；运行时不读旧工程或数据湖。可将整个候选目录 APFS 克隆到主工作区同名 `.runtime/phase2/vic_desks/seven/versions/`。总控设 `config/local.json` 的 `phase3_versions.seven=249854a814731c52`，由统一启动器把 `YUNALPHA_SEVEN_VERSION` 转为 `--version`；不要改冻结快照或其激活记录。候选清单绑定提交时运行源码；后续共享运行源码改变时应用重建流程，而非手改候选哈希。

单独运行时在 `apps/vic_desks` 使用本应用 Python 3.12 和锁定依赖，设置 `YUNALPHA_INPUTS` 指向主仓冻结输入父目录、`YUNALPHA_RUNTIME` 指向当前工作树 `.runtime`，执行 `.venv/bin/python -B -m seven_desks.server --version 249854a814731c52 --port 19231`。去掉 `--version` 启动原冻结版，也可从候选页用 `?board=valuation&data_version=d451f099a954d584` 回看原版。

数据湖正式读入口：用湖侧环境和正式 `configs/multi-asset-lake.toml` 执行 `extract_valuation_lake.py --config FILE --output DIR`，通过 SDK `load()` 导出有界切片；用应用环境执行 `-m seven_desks.prepare_valuation --source DIR` 重建。当前实际读取正式指数估值、行情、交易日历、股票估值与主档，更新市场估值至 11-13。候选另接入湖已发布 11-09 的五年PE收益档统计，以及保留原格式的中证/乐咕对照。冻结原版、其他六台截止仍是 11-09。没有写湖、发布或自行切换。

[同日数值对账及缺口](../../apps/vic_desks/evidence/phase3-valuation/RECONCILIATION.md)记录原站可见定义与数据日、比较样本和未对齐量。测试 `test_phase3_valuation.py` + `test_seven_migration.py` 9项通过；非估值六台各首签代表非预缓存查询与旧输入执行的区块相同。浏览器完成四签、5年窗口、创业板行下钻、PB及近3年筛选、36月收益、结构和前一交易日、旧版 URL 回退，无错误。总览可一键查看 11-09 完整截面并回到最新；指数详情缺所选指标时可回看最后可用日，当前日不填旧值。验收证据在 `.runtime/artifacts/valuation-tests.txt`、`valuation-browser.txt`、`valuation-shortcuts-browser.txt`、`valuation-live/`。
