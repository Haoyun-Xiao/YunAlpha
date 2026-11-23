# 投研台接湖日更

本轮沿用数据湖已有的北京时间 18:00 任务。湖完成取数、补抓和收尾后，调用 YunAlpha 的刷新入口；没有第二个固定时间任务。

## 手动运行

在主工作区执行：

```sh
./scripts/refresh_desks.sh
```

只构建候选、不切换主站：

```sh
./scripts/refresh_desks.sh --build-only
```

湖侧传入的 `MAL_TRADE_DATE` 是日期上界，`MAL_TRIGGER` 记录触发来源。手动运行默认按已正式发布的输入取实际日期。脚本使用本仓投研台虚拟环境、`config/local.json` 中的湖位置和本仓 `.runtime`；不采集或写入正式湖。

## 查看结果

- `.runtime/desk-refresh/latest.json`：整轮结果、候选版本、服务切换情况、开始和结束时间。
- `.runtime/desk-refresh/build.log`：七台构建输出；`crowding-build.log` 为最近一次抱团完整构建输出。
- `.runtime/phase2/vic_desks/daily/latest.json`：各看板输入、实际日期和保留项。
- `.runtime/logs/seven.log`、`.runtime/logs/crowding.log`：两个看板服务日志。

先生成候选，再检查并切换。仅重启本工作区登记且正在运行的两个看板服务，其他服务不重启；原先关闭的服务仍保持关闭。切换失败保留原配置并尝试恢复原运行版本，具体结果写入摘要。重复输入应复用已有版本。

`completed` 表示刷新流程完成，不表示八台全部指标都是当日数据。持仓披露、财务公告、指数估值、ETF 和筹码等按各自真实数据日期展示；缺少上游的指标继续保留原结果。要看指标是否更新，应核对各台明细，不能只看进程退出码。

湖侧配置在其本机 `configs/.env.local` 中，用单一可执行文件路径：

```sh
MAL_DESKS_REFRESH_EXEC="/path/to/local-user/项目/综合投研平台/scripts/refresh_desks.sh"
```

只在真实构建与主站切换验收后启用。湖和看板失败分别记录；湖侧不会因应用刷新失败改写已有数据发布结果。

## 验收记录

真实构建、版本、实际数据日期和定时启用情况，以 [本轮交付记录](tasks/desks-daily-control.md) 为准。首次 18:00 日历触发尚未发生时，不能把手动实跑写成定时验收完成。
