# 普林格入口改为可运行回测

- 基线：`291ba7ede28832dfda786089c6eda7d98224be13`；分支：`codex/fix-pring-backtest`；工作树：`/path/to/local-user/项目/.worktrees/综合投研平台/fix-pring-backtest`。
- 修改范围：`apps/pring/**`、`apps/api/strategies/__init__.py`、本任务单。源项目只读，来源提交 `70ab331c7c3233d378d97c1c4f4baf80ef80a018`。
- 原应用直接运行 `backtest_platform/server.py PORT`，Python HTTP 服务只监听 127.0.0.1；依赖 NumPy、pandas、openpyxl，三份原静态文件已使用相对资源和 API 地址。

## 交付协议

`bash apps/pring/setup.sh` 安装本应用的 Python 3.12 锁定依赖；`bash apps/pring/run.sh PORT` 用本应用 `.venv/bin/python` 启动并以 `-m backtest_platform.server` 通过 exec 管理实际进程，集成默认端口 `19106`，本工作树单独验收用 `19123`。入口 `/workspaces/pring/` 由现有 Vite 代理去掉 `/workspaces/pring` 前缀后转发到此端口，保留查询参数。内部就绪检查为 `GET /api/assets`（另保留 `/api/health`）。资源和 API 均留在同一前缀下。

输入从 `YUNALPHA_INPUTS/pring` 读取，默认当前项目 `.runtime/phase2-inputs/pring`；总控共享输入为 `/path/to/local-user/项目/综合投研平台/.runtime/phase2-inputs/pring`。策略状态首次从种子复制到当前工作树 `YUNALPHA_RUNTIME/phase2/pring/saved_strategies.json`，重启不覆盖，默认 `YUNALPHA_RUNTIME=.runtime`。

总控已在 `3fc22f1` 的 `scripts/phase2.py` 登记 `pring`；应用协议与之对齐。Vite 的 `YUNALPHA_PROXY_PORTS` 使用 `/workspaces/pring:19106`；目录开关 `YUNALPHA_PHASE2_MODULES` 中启用 `pring`。目录入口将直接打开回测，原介绍页面的只读路由保留，但不再占据主入口。

## 实际迁入及验证

- 本仓保留原三份静态页面文件及完整后端；只替换数据根、状态文件位置和首次初始化，不改计算、参数、页面或交互。原有回测测试也迁入，仅调整包内导入。
- 冻结输入共 21 个文件、8,966,433 字节，覆盖计算实际读取的基金／指数／增强工作簿、基金经理净值及标签、行业轮动 CSV、月度普林格阶段表；附带原有 10 份策略及标签的保存文件作种子。所有文件是独立复制并记录 SHA-256；无软链接、硬链接，未复制运行环境。
- 在本应用 Python 3.12 锁定环境通过四项原有输入检查：阶段表、默认标的、资产分类数量、基金经理和标签。读出 342 个资产：基金 18、基金经理 52、指数 108、增强 164。
- 真实 HTTP 回测以默认资产篮子、沪深 300 基准、股票／商品风控开启，区间 2016-04-01 至 2026-06-10。迁入版与旧源码读取原输入的窗口、指标、2,474 条曲线、交易日志、年度和阶段归因逐项相等。策略实盘版年化 8.7086%、最大回撤 -13.9813%（历史样本结果）。验证另存新策略、停止和重启后可读，种子没有覆盖修改。全部旧输入和冻结副本 SHA-256 未变。
- 浏览器以与 Vite 相同的前缀重写规则打开 `/workspaces/pring/` 并点击原页面“运行回测”，成功显示结果；样式、脚本、资产、策略库和回测请求均在同一前缀，页面错误 0。总控仍需在真实壳应用中验收卡片、代理和生命周期。浏览器检查没有启动壳应用。
- 必要证据在本工作树 `.runtime/artifacts/pring-verification.json`、`pring-input-checks.txt`、`pring-browser-verification.txt` 和 `pring-backtest.png`；进程身份和退出记录在 `.runtime/logs/pring-verification.log`、`pring-browser.log`。测试临时状态已清理，端口 19123 无本任务监听进程。

## 数据范围与缺口

月度阶段信号固定为 2016-03-31 至 2026-03-31 的 121 条；默认资产共有回测窗口到 2026-06-10，部分其他资产覆盖不同，页面沿用真实日期展示。原算法在末期信号之后沿用最后阶段；本次不生成新的信号、不补数，也不把历史回测称作实时策略。现有数据中的重复指数／旧策略缺失标的提示保持原样。介绍页只读路由继续保留，主目录不再用它替代回测。
