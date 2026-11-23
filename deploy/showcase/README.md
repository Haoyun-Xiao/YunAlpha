# YunAlpha 公开展示实例

访问 <https://www.howardyun.cn/YunAlpha/>。独立部署的文件根为 /data/www/YunAlpha，按版本放入 releases/，current 指向当前版本。Nginx 通过单独的片段挂载该入口。

| 模块 | 公开实例 |
|---|---|
| 投研台 | 七台共 42 个默认页签和公募抱团六个页签。以发布时的历史快照为准，支持页签切换、图表和部分表格交互。 |
| 基金研究报告 | 展示已有云端正式报告及页面资源。 |
| 策略 | 两个独立后端运行 FOF 与普林格的访客临时回测，展示红利低波历史结果。 |
| 研究 Agent | 展示工作流程、报告成果与源码。在线问答与报告生成在配好数据和模型的本机环境运行。 |

## 重新发布

1. 在支持 Node 20.19 或更新版本的环境中执行 \`npm --prefix apps/web ci\`，然后执行 \`npm --prefix apps/web run build -- --mode showcase\`。开发模式继续使用根路径。
2. 启动具有完整运行数据的 YunAlpha 源实例，运行 \`python3.12 deploy/showcase/export.py --library REPORT_LIBRARY --output NEW_SITE_DIR\` 导出展示页面。导出器读取指定实例现有快照和已标注为云端正式版的报告。收录前仍需确认本次报告及展示数据可以公开。
3. 将站点目录、两套回测源码及所需冻结输入复制到新的版本目录。将本目录的访客脚本放到站点根目录，在两套回测的 HTML 末尾引用 /YunAlpha/readonly.js。FOF 使用独立的种子目录，通过 seed_fof.py 生成演示组合。普林格使用空策略种子。为两个系统服务分别提供 PORT=19313、PORT=19316。各实例的运行状态放在版本自己的 .runtime 中。
4. 切换 current 后只重启 yunalpha-backtest@fof 与 yunalpha-backtest@pring。如果更新 Nginx 片段，先检查配置再重载。回退时将 current 切回上一版，并重启这两个服务。旧投研站仍使用独立入口。

公开发布材料由 GitHub 仓库代码与此实例的展示数据组成。初始公开仓库基于本机版本的已提交源码重新建立历史，研究资料、私有行情库、个人组合和会话状态由本机配置管理。
