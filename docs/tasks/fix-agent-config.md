# 基金 Agent 真实问答配置修复

- 目标与结果：修复主界面“未配置基金 Agent 模型凭据”而无法回答。真实模型完成“你好”和基金工具问答，会话重开后四条消息完整。
- 基线：`291ba7ede28832dfda786089c6eda7d98224be13`。
- 分支与工作树：`codex/fix-agent-config`，`/path/to/local-user/项目/.worktrees/综合投研平台/fix-agent-config`。
- 允许修改：`apps/fund_agent/**` 与本任务记录；共享启动脚本由总控负责。
- 接口依赖：保持原模型配置、`/api/v1`、原 Deep Agents 与基金工具；未改研究方法、模型角色、报告流程或数据。
- 运行：独立端口 19122，API PID 67327，已停止并释放端口。自身运行根 `.runtime/phase2/fund_agent`，验证资料 `.runtime/agent-config`。按授权使用主目录自己的 Python 3.12 venv 和冻结输入；通过 PYTHONPATH 运行当前工作树源码，数据库与桥接均在当前工作树。
- 范围外：未修改主目录源码、共享 scripts、旧项目源码或凭据，未使用旧项目 venv、软链接或旧服务，未停止任何主服务。

## 原因与修复

旧项目通过应用目录下的 `.env.deepseek.local`、`.env.local`、`.env.opencode.local` 加载环境；当前原模型配置使用 OpenCode Go 的 `deepseek-v4.1-flash`，模型变量为 `OPENCODE_GO_API_KEY`。迁入时未迁凭据，因此没有可用模型配置。

在原加载器中增加 YunAlpha 根目录的 `config/.env.fund-agent.local`。固定优先级为：进程环境、YunAlpha 本机文件、原有应用目录本机文件。不依赖启动工作目录，API 与 Worker 读取同一份文件。

本次只将 `OPENCODE_GO_API_KEY` 复制为普通文件，未复制 Tavily 或其他模型变量。已准备：

- 主目录：`/path/to/local-user/项目/综合投研平台/config/.env.fund-agent.local`。
- 验证工作树：`/path/to/local-user/项目/.worktrees/综合投研平台/fix-agent-config/config/.env.fund-agent.local`。

两份文件均受已有 Git 忽略规则覆盖，权限 600，没有链接。源码、日志与任务记录只包含配置路径及变量名，不含密钥值。以后运行无需访问旧项目。

## 真实验证（2026-11-16）

| 检查 | 结果 |
|---|---|
| 真实模型与地址 | 原 `config/model_roles.yaml` 保持原样；`https://opencode.ai/inference/go/openai/v1`，`deepseek-v4.1-flash`，未使用 mock、回放或手工注入回复 |
| 健康检查 | 独立 API 的 model_key=true，healthy=true；新进程自动读取本机文件 |
| 第一轮 | “你好”→ 正常返回 Agent 自我介绍，completed，6.15 秒 |
| 第二轮 | 同会话“请查询 013840.OF 是什么基金、基金经理是谁？请用本地基金工具确认。”→ 实际完成工具调用，回答银华集成电路A、方建，completed，7.09 秒 |
| 重开 | GET session 保留完整四条用户/助手消息 |
| 配置回归 | 3 passed，验证任意工作目录自动加载、助手/报告角色共用凭据、进程变量优先及原路径兼容 |
| 源码检查 | 修改文件 Ruff 与 git diff --check 通过 |
| 生命周期 | 独立 API 67327 已停止，19122 无监听；主目录登记的八个服务 PID 仍存活 |

完整真实请求、回复和事件留在本工作树 `.runtime/agent-config/artifacts/live-verification.json`；日志为 `logs/live-verification.log` 和 `logs/api.log`，进程登记为 `service.json`。只保留本次必要证据，未提交运行库、密钥或验收临时代码。

## 主目录生效步骤

由总控合入源码后执行。主目录本机文件已备好，不需再次填写密钥，不需重新安装依赖。共享启动器当前使用全工作区生命周期；以下会短暂重启主工作区服务，本任务没有执行：

```sh
cd /path/to/local-user/项目/综合投研平台
./scripts/workspace.sh stop
./scripts/workspace.sh start
./scripts/workspace.sh check
```

如总控先增加 Agent 单独重启能力，可只重启 Agent API 与 agent-worker；两者都应读取根目录同一份本机配置。重启后确认 Agent 健康端点 model_key=true，再从新会话验证真实回答即可。
