# 公募抱团六页日更

- 基线 `bb919bcdcb46d4c1584a14f2d0f1a7d143314be6`，分支 `codex/crowding-daily-core`。范围：`apps/vic_desks/crowding/`、`apps/vic_desks/scripts/build_crowding_cache.py`、抱团专属测试、本任务单。七台、共享调度及启动配置由总控协调。
- 输入：母版基金规模 v1 `a49acf3ac9d03625`，冻结参照 `.runtime/phase2-inputs/vic_desks/crowding`，正式湖 `/path/to/local-user/项目/多资产智能投研平台-v2/lake`。只读 Parquet 和已有的份额关系发布凭据。构建与验收产物仅落当前工作树 `.runtime/`。
- 完成判据：六页全部构建，股票、无持仓、总览、基金、行业四个收益起点；指数页及个股下钻同版。实算收益、持仓现值、行业和风险；披露统计、规模和净值的真实日期独立保留。相同输入重复运行直接复用。选版健康接口及错误版本拒绝通过。任务端口 19233。

## 构建与选版合同

总控合入后，从主仓调用以下命令，使用应用自己的 Python 3.12 环境（现有 `uv sync --frozen --project apps/vic_desks --python 3.12`）：

```sh
cd '/path/to/local-user/项目/综合投研平台'
export YUNALPHA_RUNTIME="$PWD/.runtime"
export YUNALPHA_INPUTS="$YUNALPHA_RUNTIME/phase2-inputs"
export YUNALPHA_LAKE_ROOT='/path/to/local-user/项目/多资产智能投研平台-v2/lake'
apps/vic_desks/.venv/bin/python apps/vic_desks/scripts/build_crowding_cache.py \
  --daily --as-of 2026-11-16 --base-version a49acf3ac9d03625
```

`--as-of` 是查询上界；以输出 `metadata.market_date` 判断实际行情日。`--base-version` 可传母版或当前已选的日更版；后者由清单 `base_version` 解析至同一个规模母版，同输入保持同版本。原母版清单须保留在运行目录的 `phase2/vic_desks/crowding/versions/a49acf3ac9d03625/manifest.json`。日更构建和服务均只用投研台锁定依赖。成功退出为 0，stdout 最后一行为 JSON，含 `status`（`complete` 或 `reused`）、`version`、`path`、`elapsed_seconds`、`metadata`、`fund_scale_period`、空的 `failures`。失败退出非 0（并发构建锁占用为 2），现有服务继续使用原版本。

产物位于 `$YUNALPHA_RUNTIME/phase2/vic_desks/crowding/versions/<version>/`，清单的 `schema` 为 `crowding-daily-v1`。构建只发布完整目录；不会修改当前选版。重用同一版本由湖输入指纹、构建及计算源码、观察上界和母版确定。根调度如需升级服务，须用返回的版本号核对健康服务；无需把它解释成基金规模 v1。已有总控配置保持使用 `phase3_versions.crowding`，或以 `YUNALPHA_CROWDING_VERSION=<version> apps/vic_desks/run.sh crowding <port>` 启动。GET `/api/crowding/health` 必须返回 HTTP 200、`status=ok`、`active_version=<version>`、`schema=crowding-daily-v1`，并给出行情日、持仓期及规模期。显式请求其他版本的 API 返回 409。回退时把选版值设回母版即可。

如复用验收产物，将整个版本目录先复制到主仓版本目录下的临时路径，再在同一文件系统内重命名成最终目录，随后选版；仅复制清单不够。完整日更包包含本版快照、各页与下钻所需缓存，服务不读取数据湖、旧应用或任务目录。报告期披露市值仍按披露计算；行情市值用披露股数与同日收盘价估算。股价、估值错峰时综合指标仍使用实有的共同交易日。晚于持仓期的规模只有份额关系和所有份额同季数据均已验证才展示，独立标注非 PIT，不用作半年报持仓占净值分母。

## 2026-11-16 实跑与交接

- 完整版 `92d9d1e2f67cfbb3`，位于任务工作树 `.runtime/phase2/vic_desks/crowding/versions/92d9d1e2f67cfbb3/`，约 306 MB、11,193 文件。清单 SHA-256：`1023ab69a95ac3655a567aa94d1a8879d8c05247397d0512383cf52938542d8c`。完整文件摘要在 `.runtime/artifacts/crowding-92d9d1e2f67cfbb3.sha256`；复制后可在版本目录执行 `shasum -a 256 -c <摘要文件>`。
- 独立应用环境成功构建耗时 288.61 秒（命令墙钟 289.69 秒）。首次尝试发现已有构建路径缺少上期快照缓存初始化，未发布；修复后复用该次正式湖快照，首次快照另耗时约 70 秒。最慢面板是基金、指数历史各约 79 秒，行业约 49 秒。将 `--base-version` 改为刚发布的日更版重跑，1.92 秒返回 `reused`，版本与路径一致。
- 行情、估值 11 月 16 日；完整持仓期 6 月 30 日，实际使用的申万行业分类为 10 月 23 日，指数最近披露期为 2026Q3。四个收益基准日分别为 6 月 30 日、9 月 30 日、11 月 9 日、10 月 19 日。有持仓样本 5,307 只，无持仓样本 276 只。收益有值的有持仓股票 5,144 只。
- 相对冻结行情 11 月 13 日，持仓期以来抱团池收益从 −24.0570% 到 −24.2733%，电力设备行业收益从 −36.9609% 到 −37.8883%，高风险股票从 105 只到 141 只。宁德时代披露市值仍约 1,357.03 亿元，持仓估算现值从 822.40 到 832.08 亿元；正式 Parquet 收盘价 240.50 元乘以披露归并股数 345,981,249，与新页面一致。
- 三季报规模按原口径复算 50 只基金，与母版逐项相等。005844.OF 多份额规模合计为 312.53 亿元（9 月 30 日），半年报持仓占净值仍为 93.8963%，基金净值终点为 11 月 13 日。行业仓位仍隐藏混用整产品资产的规模加权值。
- 在 19233 用单独复制的完整包启动，未配置冻结输入、数据湖或规模母版；健康版本正确，快照构建次数为 0。四起点页面、筛选、列表和下钻对账、市场与基金各自日期、错误版本 409 均通过。既有 HTTP 探针也通过 20 个下钻样本、全部视图和双向排序，页面与首次个股下钻 p95 分别为 23.38 ms 和 3.92 ms。母版 v1 回退启动和规模读回通过，测试进程已停止。`apps/vic_desks/tests`：92 通过、7 跳过。

构建、复用、健康、湖读回与 HTTP 证据均留在任务工作树 `.runtime/artifacts/crowding-*`。总控可直接集成本提交并在主仓重建，或先复制验收版本并校验再选版。主站和正式湖未改动。
