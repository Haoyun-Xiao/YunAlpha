# AIHOT 舆情接入

YunAlpha 左栏第三项「舆情」嵌入与主站同源的 `/YunAlpha/news/`。AIHOT 同时提供 AI 资讯和 A 股舆情。主站的代码和 AIHOT 独立维护。

- 云端部署目录为 `/data/www/AIHOT`。应用版本由 `current` 选择，数据由独立的 PostgreSQL 数据库和 `state/market` 保留。本机端口为 Web 19320 与 API 19321。
- AIHOT 基线为 `f1aca10`，挂载适配提交为 `6e82687`。在开发机以 `AIHOT_BASE_PATH=/YunAlpha/news/ npm run build -w @aihot/web` 构建。Web、API、Worker 使用相同的 `SITE_URL`。Nginx 转发保留原路径、查询字符串和 HTTPS 主机名。
- `yunalpha-aihot@api`、`yunalpha-aihot@web` 负责读取及交付页面。A 股采集由 `yunalpha-aihot-collector` 运行。AI 信源与按期研究由 `yunalpha-aihot@worker` 执行。生产环境和值在服务器端本机文件中维护。
- A 股需要只读交易日历、证券和行业分类，以及湖侧新闻档案。上线使用独立小型副本，交易日历覆盖至 2027 年。当期无 FINTEL 评分时，保留缺项。
- 紧急恢复时优先保障原站、AIHOT 页面及 A 股采集。暂停 AI Worker 后先验收端口与站点，再限制 Worker 内存和处理并发后单独恢复。采集、热点、日报、Idea、趋势分别核对实际执行记录。仅在资源验证通过后恢复全部后台研究。

首次上线后已通过双频道、日报、Idea、趋势页面的 HTTPS 读回。23:18 的云端 A 股手动验收显示 14 路成功，热点接口返回 10 条。随后在 AI 逐源验证期间，共享服务器的 SSH 和 HTTPS 同时超时。当前需要先恢复云主机管理通道，再确认根因与自然采集周期。预变更的 Nginx 片段和 YunAlpha 首页分别备份为 `/data/www/backups/yunalpha-showcase.pre-aihot-20261123.conf` 与 `/data/www/backups/yunalpha-index.pre-aihot-20261123.html`。
