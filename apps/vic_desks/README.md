# YunAlpha · 公募抱团

由 Python 3.12 独立提供六个页签。安装锁定依赖：

```sh
uv sync --frozen --project apps/vic_desks --python 3.12
bash apps/vic_desks/run.sh crowding 19222
```

设置 `YUNALPHA_INPUTS` 指向冻结输入根，`YUNALPHA_RUNTIME` 指向当前工作树的运行目录。未设置时分别使用工作树的 `.runtime/phase2-inputs` 与 `.runtime`。访问 `/workspaces/crowding/`；壳代理剥掉这一前缀，资源、API 和带查询的深链接均走同一入口。

## 冻结输入

默认固定 `YUNALPHA_INPUTS/vic_desks/crowding` 下的版本 `a8bbbe0793ac426c`。行情日 2026-11-13、完整持仓期 2026-06-30；该服务不会刷新快照，不依赖旧服务或数据湖。个股透视现场组装该版本预计算的行情与持有人输入。缺失字段保持原解释与空值。

总控验收目录已有一次性复制。另一个输入根需要重新导入时，用 `scripts/import_crowding_release.py --source OLD_PROJECT_ROOT --inputs YUNALPHA_INPUTS`。脚本校验原发布清单和整批文件摘要，先复制到临时目录，完整后原子移入；已存在的冻结目录不会被覆盖。摘要见 `evidence/source-release.json`。

`scripts/probe_crowding_release.py --output PATH` 检查本机 `19222` 上的冻结数据接口；证据摘要见 `evidence/acceptance.json`。`scripts/build_crowding_cache.py` 为显式候选构建工具，仅在配置 `YUNALPHA_LAKE_ROOT` 后只读读取正式湖，产物写入 `YUNALPHA_RUNTIME/phase2/vic_desks/crowding/`。候选不会覆盖已冻结版本。本轮验证未执行全量候选重建。

公募抱团与七台使用同一启动器、独立端口；七台第二道里程碑如下。

## 七台共享应用（第二道里程碑）

`bash apps/vic_desks/run.sh seven 19221` 已接通。访问 `/workspaces/vic-desks/?board=<ID>`；数据只读固定 `d451f099a954d584`。安装依赖使用本目录更新后的 uv.lock。完整迁入、指纹适配、运行缓存隔离和候选重建说明见 [七台运行说明](seven_desks/README.md)；结果见 `evidence/seven/`。原公募抱团目录及源码未改动。
