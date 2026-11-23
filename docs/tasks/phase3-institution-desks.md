# 第三阶段 B · 公募持仓、国家队全景、逆向投资

- 基线 `8f983b39feca091da17284303b23154c84e5423b`；分支 `codex/phase3-institution-desks`；工作树 `/path/to/local-user/项目/.worktrees/综合投研平台/phase3-institution-desks`。
- 数据母版为已含市场估值更新的 `249854a814731c52`。正式湖、母版和旧工程只读，不使用 DS。服务端口 19232，运行前复核并登记。
- 修改范围：`apps/vic_desks/seven_desks/adapters/{owners,contrarian}.py`、`apps/vic_desks/seven_desks/prepare_phase3_institution.py`、专属测试、`apps/vic_desks/evidence/phase3-institution-desks/`、本文；公共启动和页面由总控统一。
- 交付：三台原站和湖数据口径核对；对能核实的问题实现候选、同日期固定样本和代表交互；留下待核原始字段和原因。候选继承估值母版，重新生成自身清单与运行代码指纹，以 `--version` 验证后交总控。

本地验收候选为 `d747f5d7308e9657`，继承 `249854a814731c52` 的全部冻结数据和 11-13 市场估值；仅覆盖下列四份机构数据。候选路径：

`/path/to/local-user/项目/.worktrees/综合投研平台/phase3-institution-desks/.runtime/phase2/vic_desks/seven/versions/d747f5d7308e9657`

总控合并两条线源码后，用下列**约 2 MB**的数据包，不必转移上述整份七台候选：

`/path/to/local-user/项目/.worktrees/综合投研平台/phase3-institution-desks/.runtime/artifacts/institution-revision/`

| 目标（新候选的 data/ 下） | SHA-256 |
|---|---|
| `revision/owners/phase3_fund_scale.parquet` | `71a3f82cae187b25bab4bae96721d7ca502d6f4f52d7179e0025b791959508d3` |
| `revision/owners/phase3_fund_companies.parquet` | `78d8928768e0e38b8937057d5f4debafdf2fe7b216671b69f4b9dd258cc73fb3` |
| `revision/owners/phase3_float_holders.parquet` | `ce21f67f5202a9c32df6cc863db4fc66618e342713c06a078b31fc4ecaed39aa` |
| `revision/contrarian/phase3-audit-events.parquet` | `678be74fb0d3c79334af51f5636b2dfa39a766785bfe973a56550779efa9d822` |

联合构建时，将上述文件和 C 线增量覆入从 `249854a814731c52` 继承的新候选，由当前代码计算 `code_hashes()` 和版本，保留估值截止 `2026-11-13`，以 `seven_desks.server --version <新版本> --port <空闲端口>` 验证。不要手改旧清单或回到 `d451f099a954d584`；现有共享 `create_version()` 生成的清单未自动继承 `valuation_cutoff`，联合构建须显式保留该字段。冻结旧版的源码兼容校验由总控按联合版本策略处理；本线未改公共兼容记录。

独立复建：从工作树根以 `PYTHONPATH="$PWD/apps/vic_desks"` 调用 `-m seven_desks.prepare_phase3_institution prepare --base-root <249854a814731c52目录> --revision <当前工作树.runtime输出> --lake-config <正式湖TOML>`（湖侧 Python）；再用投研台 Python 调 `assemble` 并提供同名 `--base-root`、`--revision`。后者保留母版估值截止并计算自身运行指纹；运行不需要数据湖。交付候选不激活。

只修改了 `owners.py` 的公募与国家队分支，以及 `contrarian.py` 的个股财务内容；`_capital` 和公共 `_Data`、启动器、市场模块、页面均未改动。Chrome 与接口证据见 [口径记录](../../apps/vic_desks/evidence/phase3-institution-desks/RECONCILIATION.md)、[固定样本](../../apps/vic_desks/evidence/phase3-institution-desks/verification.json)。本轮只跑三台代表交互与 14 个接口，没有重跑七台全量；19232 的本任务进程已停止。
