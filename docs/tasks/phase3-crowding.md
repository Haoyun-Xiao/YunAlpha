# 第三阶段 B · 公募抱团首批

- 基线：`7652ed4c476f9df9ee557c2039d1f9aefb3f194d`；分支 `codex/phase3-crowding`；工作树 `/path/to/local-user/项目/.worktrees/综合投研平台/phase3-crowding`。
- 修改范围：`apps/vic_desks/crowding/`、专属构建脚本、专属测试、`apps/vic_desks/evidence/phase3-crowding/`、本任务单。
- 依赖：冻结版 `a8bbbe0793ac426c`、正式湖已发布资料（只读）、原站可见口径及已留存记录。候选独立落本工作树 `.runtime/`，不切换默认冻结版。预留端口 19232，运行前复核。
- 验证：固定同日样本核对份额、基金与规模口径及图表；验收六页、代表筛选和下钻。明确列出未闭合口径及数据缺口。

候选版 `a49acf3ac9d03625`（冻结母版 `a8bbbe0793ac426c`，母版快照指纹 `47d1f0adfcc79baa9d18`）。仅增量候选清单需要转移；保留主仓已安装的冻结输入。口径、未闭合项、两只基金的原始单位和报告对账见 [口径核对](../../apps/vic_desks/evidence/phase3-crowding/reconciliation.md)；四起点接口验收见 [测试记录](../../apps/vic_desks/evidence/phase3-crowding/verification.json)。

主仓拿到本分支的对应代码后，复制以下目录到主仓相同的 `.runtime/phase2/vic_desks/crowding/versions/` 下：

```sh
src='/path/to/local-user/项目/.worktrees/综合投研平台/phase3-crowding/.runtime/phase2/vic_desks/crowding/versions/a49acf3ac9d03625'
dst='/path/to/local-user/项目/综合投研平台/.runtime/phase2/vic_desks/crowding/versions'
mkdir -p "$dst"
cp -R "$src" "$dst/"
shasum -a 256 "$dst/a49acf3ac9d03625/manifest.json"
```

唯一必需文件是 `manifest.json`（SHA-256 `defd9fa48a9efea0eb4579f27a24272a58826b58f3e8a14128bb2183bc17c366`）。在主仓现有启动流程设置 `config/local.json` 的 `phase3_versions.crowding = "a49acf3ac9d03625"`；也可直接以 `YUNALPHA_CROWDING_VERSION=a49acf3ac9d03625 apps/vic_desks/run.sh crowding <空闲端口>` 启动，运行时只读本机快照，持仓数据仍取主仓 `.runtime/phase2-inputs`。删去选版配置后恢复冻结默认。指定不存在、被改动或不匹配的候选时拒绝启动，旧版本 URL 会收到 409，由页面提供切换至当前只读版本的入口。

复建只读候选（在任务工作树执行，SDK 环境仅在构建阶段使用）：

```sh
lake='/path/to/local-user/项目/多资产智能投研平台-v2'
YUNALPHA_INPUTS='/path/to/local-user/项目/综合投研平台/.runtime/phase2-inputs' \
  "$lake/.venv/bin/python" apps/vic_desks/scripts/build_crowding_cache.py \
  --fund-scale-candidate 2026-09-30 --as-of 2026-11-16 \
  --knowledge-cutoff '2026-11-16T16:35:00+08:00' \
  --lake-config "$lake/configs/multi-asset-lake.toml"
```

验收：投研台测试套件 90 项通过（以主仓冻结输入只读运行）。Chrome 六页及规模双向排序、四起点接口对账、资金窗口、行业筛股、指数模式与指数到个股、总览加权。额外验证缺值始终排序末尾、基金原比率不变、持仓/无持仓详情收益等于列表、风险分保持不变。将单一清单复制到另一独立运行目录，移除湖配置后验证候选和冻结默认均可启动（快照构建次数 0）；不存在版本拒绝启动。测试服务均已停止，19232 不再监听。主仓配置、共享启动器和湖未改动。
