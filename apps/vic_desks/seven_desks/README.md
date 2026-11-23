# 七台共享应用

运行 `bash apps/vic_desks/run.sh seven 19221`，入口 `/workspaces/vic-desks/?board=national_team`。各台 ID：national_team、contrarian、sentiment、chips、valuation、fund_holdings、capital_flow。Python 3.12 与应用自身 uv.lock 安装的依赖。

固定版本 `d451f099a954d584`、截至 2026-11-09。所有数据和预计算页面从 `YUNALPHA_INPUTS/vic_desks/seven/versions/d451f099a954d584/` 读取；不访问旧项目或旧 HTTP。缺文件保留缺项或报错，不主动补采。新查询读取同版 Parquet 后，仅在本工作树 `YUNALPHA_RUNTIME/phase2/vic_desks/seven/` 缓存响应和 DuckDB 临时文件。带前缀与代理剥除前缀均可访问，页面 API/静态资源不会落到壳应用根 /api。

## 来源与运行边界

`evidence/seven/source-release.json` 记录原发布 32 项源码摘要；冻结数据清单摘要见 `source-snapshot.json`。迁入时逐文件校验完整版本，独立文件复制，禁止链接。

原 manifest 的代码指纹仍保留。两个计算适配器的函数完全未变（contrarian 仅去行尾空白；market 只调整缺省文件位置）；owners 只有元数据缺省目录和连接初始化调整，其他 32 个顶层定义的 AST 一致。核对脚本为 `scripts/check_seven_boundary.py`，结果写 `migration-runtime.json`。每次非预计算查询会同时验证原发布对应关系、迁入运行哈希和进程哈希，不因合理路径适配而把有效查询关掉。后续运行源码变化需重新核验或创建新候选。

## 验证与候选重建

`scripts/verify_seven_desks.py` 检查 42 默认页及代表实体，`scripts/verify_seven_queries.py --output PATH` 验证七台各一项确实未预缓存的有效查询。详细记录见同目录 tests 与 evidence。

显式重建：以应用 .venv 中 Python，在应用目录执行 `-m seven_desks.server --build-only --refresh --prewarm --no-publish`，基于同版冻结输入克隆到本工作树，并重新派生页面和索引。不会刷新正式湖或切换服务；当前启动器始终使用固定发布版。构建脚本仅供明确的新候选任务，读取冻结数据或显式配置的只读湖，产物留在运行目录。本轮未做19GB全量重建，只验证小样候选隔离与真实非缓存查询。

数据缺口、原站差异和近似口径保留在 `evidence/seven/DATA-GAPS.csv` 与原交付记录，接口成功不表示与原站数值一致。
