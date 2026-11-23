# 第二阶段集成约定

业务来源基线：基金 Agent `df71bf5`，FOF `4926507`，VIC 七台冻结版 `d451f099a954d584`、公募抱团 `a8bbbe0793ac426c`。总控负责共同启动、代理、导航和目录注册。执行线的源码、迁移清单和必要工具都进 Git；不得提交快照、旧状态、密钥或第三方成品包以外的原站发布资产。

| 线 | 所有权 | 启动器与参数 | 浏览器路径 |
|---|---|---|---|
| D | `apps/fund_agent/`、`docs/tasks/phase2-fund-agent.md` | `bash apps/fund_agent/run.sh api PORT`，`bash apps/fund_agent/run.sh worker` | `/agent-runtime/` |
| B | `apps/vic_desks/`、`docs/tasks/phase2-vic-desks.md` | `bash apps/vic_desks/run.sh seven PORT`，`bash apps/vic_desks/run.sh crowding PORT` | `/workspaces/vic-desks/`、`/workspaces/crowding/` |
| C | `apps/fof/`、`docs/tasks/phase2-fof-workspace.md` | `bash apps/fof/run.sh PORT` | `/workspaces/fof/` |
| A | `apps/web/src/`、`docs/tasks/phase2-agent-ui.md` | 保持原 Vite 生命周期 | `/?view=session`、`/?view=space` |

启动器使用当前应用目录下由锁文件安装的 `.venv/bin/python`，只监听 127.0.0.1，用 exec 让生命周期管理到实际进程。每个 Python 应用提供 Python 3.12 的 `pyproject.toml` 和 `uv.lock`。启动器从环境读取 `YUNALPHA_RUNTIME`（本工作树内 `.runtime`）、`YUNALPHA_INPUTS`（YunAlpha 自有冻结输入根）、`YUNALPHA_LAKE_ROOT`（正式湖目录）。允许用自身目录推断合理默认值；程序运行时不能指向三处旧仓、借用旧虚拟环境或依赖旧服务。

冻结输入放在 `YUNALPHA_INPUTS/{fund_agent,fof,vic_desks}`；UI/API 按需读，旧数据的取得只发生在一次性迁入阶段，总控验收用主工作区 `.runtime/phase2-inputs`。开发可以读取这份已冻结副本；所有可写数据库、缓存、产物、临时文件在各自 `YUNALPHA_RUNTIME/phase2/<模块>`。FOF 初次把种子组合、旧策略复制到工作树独立状态，不能在重启时覆盖已有工作。VIC 七台冻结输入只导入一次，不把 19 GB 复制到每棵树。不要创建软链接或硬链接；正式数据湖允许直接只读。

本机 Vite 代理去掉上述外层路径前缀，内部路径及原查询参数保持不变。迁入的页面和静态资源用相对路径或上述明确的基路径，禁止发到壳应用的 `/api`。新标签入口可从目录直接深链接；七台用既有 `board` 查询标识。Agent 保留原 `/api/v1`、SSE 事件和恢复协议；代理转发真实 Host，不缓存事件流。对外生成的 Agent 报告与产物链接包含 `/agent-runtime`。D 发布会话、流式事件、任务和成果的真实样例供 A 使用。

总控只在模块迁入且冻结输入准备好时开放入口。既有自检与第一阶段内容保留。明确区分本轮迁入缺项、研究口径限制和原站差异；不添加模型、报告功能、调度、数据补采或发布。
