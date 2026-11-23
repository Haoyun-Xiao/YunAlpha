# YunAlpha · 基金研究 Agent

由基金研究 Agent 实验平台 `df71bf566f75f4106bdef94d2a8a6eba953e0683` 迁入。保留 Deep Agents 会话、独立报告 Worker、报告渲染、SQL 迁移和原界面资源。新界面走相同的 `/api/v1` 与 SSE。

在 YunAlpha 工作树根目录配置与启动：

```sh
export YUNALPHA_RUNTIME="$PWD/.runtime"
export YUNALPHA_INPUTS="/path/to/local-user/项目/综合投研平台/.runtime/phase2-inputs"
export YUNALPHA_LAKE_ROOT="/path/to/local-user/项目/多资产智能投研平台-v2/lake"

UV_LINK_MODE=copy uv sync --locked --python 3.12 --project apps/fund_agent
UV_LINK_MODE=copy uv pip install --python apps/fund_agent/.venv/bin/python --no-deps \
  "$YUNALPHA_INPUTS/fund_agent/wheels/multi_asset_lake-0.6.0-py3-none-any.whl"
bash apps/fund_agent/run.sh prepare

# 各自独立进程，由当前工作树的集成脚本登记、启动和停止。
bash apps/fund_agent/run.sh api 19241
bash apps/fund_agent/run.sh worker
```

`uv sync` 后补装一次冻结的 MAL wheel；其依赖由本应用锁文件提供。MAL 从 `7dd2f168af99f436bbc433bd90e1ff6cb04da957` 构建并安装进本应用的虚拟环境。Agent 的 DuckDB bridge 与 MAL SQL 生成的目录库分开保存在 `$YUNALPHA_RUNTIME/phase2/fund_agent/lake_bridge/`，两者只读正式湖的 Parquet。停止本工作树的 Agent API / Worker 后再重新执行 `prepare`。

会话库、图检查点、日志、缓存与报告成果均保存在 `$YUNALPHA_RUNTIME/phase2/fund_agent/`。API 和 Worker 共用这一个模块的独立状态。代理将浏览器的 `/agent-runtime/` 剥掉再转给 API，保留请求路径、查询、Host 和 `Last-Event-ID`。API 根目录保留原界面供对照。报告阅读及下载的浏览器路径带同一前缀。

生产会话由原模型角色配置驱动；模型凭据存放在 YunAlpha 根目录的 `config/.env.fund-agent.local`（Git 已忽略），API 与 Worker 启动时自动读取。当前原模型配置使用 `OPENCODE_GO_API_KEY`。进程环境变量优先，其次是这份本机文件，再其次是应用目录原有的 `.env.deepseek.local`、`.env.local`、`.env.opencode.local`。文件为普通文本配置，不需要 shell source，也不会执行其中的命令；本机设置建议权限为 600。当前模型与网页搜索分别使用 `OPENCODE_GO_API_KEY` 和 `TAVILY_API_KEYS`；日常启动不再读取旧项目。修改配置后重启 Agent API 与 Worker。缺少时会话进入可恢复状态；健康端点的 `model_key` 与报告 Worker 心跳分别展示。网页搜索默认自动使用可用缓存，无缓存时请求 Tavily；任务设为 `live` 时直接请求上游。定期报告检索和缺口规模补充沿用原上游，补充数据只写当前 Agent 的缓存或任务目录，不写正式湖。历史截止日的规模仍以有披露日期的 PDF 为依据。冻结输入的 `manifest.json` 记录来源提交与文件哈希，`scripts/freeze_inputs.py` 是一次性迁入工具，日常启动无需旧项目。

本地验证（准备完成后）：

```sh
apps/fund_agent/.venv/bin/python -m pytest apps/fund_agent/tests \
  --basetemp "$YUNALPHA_RUNTIME/phase2/fund_agent/tmp/pytest" \
  -o "cache_dir=$YUNALPHA_RUNTIME/phase2/fund_agent/tmp/pytest-cache"
```

独立验收使用真实 Agent、只读基金工具、API 与报告 Worker。测试在模型边界提供受控回复、重放已验收的报告和附件并校验字节，复用新任务自己产生的检查点。该回放只在测试中启用。证据与数据口径见 [阶段二任务交付](../../docs/tasks/phase2-fund-agent.md)。
