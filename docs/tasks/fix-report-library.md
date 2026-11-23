# 基金深度报告库补全

- 目标：纳入真实云端、本地及旧 Agent 报告，复制到 YunAlpha 自有输入；目录动态读取索引，保留原版报告和图表、真实不同版本与正式/草稿状态。
- 基线：`291ba7ede28832dfda786089c6eda7d98224be13`；分支 `codex/fix-report-library`；工作树 `/path/to/local-user/项目/.worktrees/综合投研平台/fix-report-library`。
- 范围：`apps/api/research/**`、专属新增 `scripts/import_fund_reports.py`、本任务说明。根挂载、前端和共享配置由总控协调。
- 输入：项目索引 fund-report 和 fund-agent；只读云端 Mutual_Fund；目的地 `/path/to/local-user/项目/综合投研平台/.runtime/phase2-inputs/fund_reports`。禁止链接旧目录或依赖旧服务，凭据不写入源码/输出。
- 验证：来源数量与去重清单、真实元数据、目录/HTML/图表/资源、草稿及缺项标记、路径边界；抽查与既有 API 回归。运行证据留工作树 `.runtime/artifacts/`。

- 盘点：只读 SSH 确认云端 `/data/www/Mutual_Fund/output` 有 55 个 HTML，真实单基金报告 54 篇，与 54 条云端索引一致；下载并核对 167 个文件。原项目本地也有 54 篇。云端合集含 54 篇、本地合集含 17 篇，逐篇移除合集 DOM 前缀后，正文和图表各与同源单篇一致，无独有报告，不另列。
- 去重：云端与本地 51 对的正文、数据日期、图表和 Markdown 一致，共用目录条目并保留双方来源。财通价值动量A（720001.OF）、安信洞见成长A（016558.OF）、财通新视野A（005851.OF）三对的图表内容不同，均保留云端和本地各一版。
- 旧 Agent：27 个有 HTML 的已完成任务、30 个不可变 HTML 产物，合并两个同内容重复登记，另纳入已生成的景顺长城沪深300指数增强A Part1（草稿）；合计 29 条，含 1 篇通过审核的正式报告。失败、取消与临时冒烟产物不进目录。真实回放资料报告保留为草稿；未通过审核或没有对应版本的审核记录也标草稿。
- 结果：86 条报告版本、69 只基金，58 条正式、28 条草稿。目录原字段 `id,fund,title,date,date_kind,entry` 保持；新增 `source`（来源显示字符串，多个来源用顿号连接）、`status`（`formal|draft`）与 `version`（16 位内容标识）。本次所有日期是 `as_of`，不把生成时间当发布日期。历史合集无需独立入口；导入器若发现有独有内容的合集，会单列到 `/api/v1/research/reports/collections`，不计入单基金报告数。
- 运行：主工作区 `.runtime/phase2-inputs/fund_reports/index.json` 保存完整条目、来源、文件摘要与排除原因；`sources.json` 保存可再导入来源，不含凭据。重跑：`python3.12 scripts/import_fund_reports.py --source-manifest <sources.json> --destination <fund_reports> --cloud-key <本机已有私钥路径>`。服务只从 YunAlpha 复制目录读取，不调用旧库/旧进程。
- 功能：原正文、内联图表和静态 ECharts 继续提供；可下载原 Word 或 Markdown；旧经理选择仅跳转 YunAlpha 条目，旧问答不启用，Part1 原投资手册跳转未收入。旧郑希链接保留重定向。
- 验证：现有 API 测试 2 项通过；独立服务 86 个正文、57 个引用资源、172 次 Word/Markdown 下载检查通过，255 个独立对象逐一核对 SHA256，未知/越界路径返回 404。Chrome 打开旧郑希链接并正确重定向至报告页；记录的一条错误是试验服务未提供站点 favicon。集成浏览器由总控验收。19141 验收服务和临时浏览器均已停止。
