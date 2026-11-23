# 工作树操作

主目录是统筹与集成区。任务工作树位于 `/path/to/local-user/项目/.worktrees/综合投研平台/`。

创建前写明任务目标与允许修改范围，从已经提交的明确基线开始：

```sh
./scripts/workspace.sh create fund-catalog --base main --task '实现基金目录查询' --scope services/funds
```

分支为 codex/fund-catalog。基线解析为提交号保存在任务登记中；工作树不会继承主目录未提交代码。
初始化自动生成独立的 .runtime/state、logs、tmp、artifacts 和本机配置，不启动业务服务。
再次执行 init 不覆盖本机配置、任务和产物。主仓库本机路径配置会供新工作树初始化使用。

检查与查看：

```sh
./scripts/workspace.sh check
./scripts/workspace.sh list
```

任务的允许修改范围保存在 .runtime/task.json；这是协作约定，合并前需检查 diff，并非文件权限沙箱。
尚未分配服务端口，后续任务启动服务前登记在自身 .runtime/environment.json，先检查占用，不能接管已有进程。

完成检查并合入 main 后，从主目录收回：

```sh
./scripts/workspace.sh close fund-catalog --into main
```

收回拒绝未提交文件、未合入提交、改变分支、额外未保全忽略文件及运行资料软链接。
运行资料先归档并校验 SHA256，再移除工作树；分支保留。不会自动停服务，调用前确认本任务服务已停止。
其他依赖目录（如 .venv 或 node_modules）须在任务收尾时单独处理，不能强制删除带未知忽略文件的目录。

Codex 创建的工作树可调用相同的 init/check 入口，路径由 Codex 管理。其回收使用应用自己的工作树管理；本脚本 close 只管理通过 create 登记的工作树。
不修改 Codex 全局工作树根目录。需要固定在本项目专用根目录时使用本项目 create 命令，再在对应目录工作。
