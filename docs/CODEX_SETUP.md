# Codex 项目入口

项目目录：`/path/to/local-user/项目/综合投研平台`。

本轮文件系统环境已建立，但项目侧栏尚未自动添加：Computer Use 明确禁止控制 `com.openai.codex`，现有连接器没有新增项目接口。不修改 Codex 内部数据库或绕过该限制，不移动已有聊天。

在 Codex 的项目列表中添加/打开上述文件夹即可。项目内 `.codex/environments/environment.toml` 已提供初始化和检查命令，TOML 与命令执行已验证；应用内识别尚待添加项目后确认。

如本机版本需要从“本地环境”设置导入，设置脚本为：

```sh
./scripts/workspace.sh init
./scripts/workspace.sh setup
./scripts/workspace.sh check
```

常用操作为 `setup`、`start`、`stop`、`check`，均通过 `./scripts/workspace.sh` 调用。
不改变全局工作树位置；本项目脚本创建的任务树使用项目专用目录，Codex托管工作树仍按应用设置。

官方说明：https://learn.chatgpt.com/docs/environments/local-environment
