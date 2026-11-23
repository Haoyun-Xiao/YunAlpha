# 第二阶段 C · FOF 组合工作台

- YunAlpha 基线：`f4e9abb7ddeb244447a4f854cb48188fd78b0590`；分支：`codex/phase2-fof-workspace`；独立工作树：`/path/to/local-user/项目/.worktrees/综合投研平台/phase2-fof-workspace`。
- 允许修改：`apps/fof/` 和本任务单。执行 C 提交模块；总控完成共同启动、Vite 代理、导航及目录注册。
- 业务来源：快速回测引擎 `4926507b7f8a32aa34e0580240461bbc170dfa94`。回测计算、持仓穿透、存储模块和三份页面文件沿用该提交；迁入组合库、分组及权重、调仓时间表及 Excel 导入、资金和费率、日周月回测及分析、组合重开、持仓与风格穿透。未迁入数据生产、独立研究工具、报告或演示文稿项目。

## 运行与冻结输入

按[第二阶段集成约定](../PHASE2_CONTRACT.md)，在 `apps/fof` 用 Python 3.12 运行 `uv sync --frozen`。从项目根执行 `bash apps/fof/run.sh PORT`。进程只监听 `127.0.0.1` 的指定端口，端口冲突即退出。任务验收用 `19231`。浏览器访问 `/workspaces/fof/`（末尾斜杠保留），Vite 去掉前缀后转发。原页以相对地址读取 JS、CSS，原有 `apiPath` 把 API 请求解析到同一前缀。

- 共享验收输入：`/path/to/local-user/项目/综合投研平台/.runtime/phase2-inputs/fof`；将其父目录传入 `YUNALPHA_INPUTS`。没有配置时默认使用当前项目 `.runtime/phase2-inputs`。启动以 `fof/manifest.json` 为冻结包就绪标志。一次性迁入工具 `apps/fof/migrate_inputs.py --source OLD_PROJECT --inputs INPUTS_ROOT` 校验旧项目提交、复制前后 SHA-256 和文件身份；不会覆盖已存在的冻结包。
- 数据：128,995,201 字节、136 个文件（不含清单）。含基金主表及标签、111 份复权基金净值、10 份指数行情、7 份查询输入：基金经理任职起点、披露报告、持仓、风格及行业画像、股票行业和行业大类映射。冻结净值和基准截至 **2026-06-30**。查询输入按各基金实际披露覆盖展示，不跟随正式湖滚动更新。
- 种子：4 份完整组合 JSON、1 份旧策略 JSON、1 份旧策略导入标记。四份原组合的 `displayOrder` 就是旧库顺序；不存在独立排序文件。种子字节级保留，调仓、各频率快照和结果均随原文件迁入。文件没有软链接或硬链接。
- 写入仅在本工作树的 `YUNALPHA_RUNTIME/phase2/fof/{portfolios,strategies}`，默认 `YUNALPHA_RUNTIME=.runtime`。首次启动把种子两个目录完整复制进工作状态；重启只读已有状态，不重跑旧策略导入。程序不访问旧仓、旧服务、旧虚拟环境或正式湖，`YUNALPHA_LAKE_ROOT` 对本模块无需使用。

## 验证与完成判据

- 用迁入应用的锁定环境运行旧代码的计算函数作读侧对照。固定组合 `portfolio_20260629_093923_682323` 在三频的窗口、指标、全部曲线、调仓日期和穿透输出均逐项相等；日／周／月的组合净值曲线分别有 **1,328 / 281 / 66** 点，穿透披露日为 **2026-03-31**。
- 在独立可丢弃的工作状态确认 4 份组合及 1 份策略可读、顺序和初始种子字节与原文件一致。增加手工调仓、上传实际 XLSX 调仓表，验证保存使旧快照失效；重算得到不同曲线，停止并重启后调仓、指标和三频快照均可打开，工作组合文件未被种子覆盖。再次校验全部旧输入和冻结文件 SHA-256，均未变化。
- 迁入的模块测试 **123 项通过**；有 4 条 pandas/NumPy 弃用提示。删除一条原已失效、要求把截止日期写死在前端的静态断言；实际前端从交易日历取得默认值。旧工具的数据生成与研究测试不属于工作台运行链路。
- 浏览器按同一规则去除 `/workspaces/fof/` 前缀后，真实加载页面、样式、脚本和 4 份组合，切换周频、月频成功；新建组合的默认截止日取自交易日历（2026-06-30）。无越过工作台前缀的请求或页面错误。
- 用 `apps/fof/verify_migration.py --source OLD_PROJECT --port 19231 --log LOG_PATH` 可复查上述迁入对照（仅验证工具需旧目录）。本机证据：`.runtime/artifacts/fof-migration-verification.json`、`.runtime/artifacts/fof-pytest.txt`、`.runtime/artifacts/fof-browser-verification.txt` 和 `.runtime/logs/fof-verification.log`。总控需在实际 YunAlpha Vite 代理和策略目录联调；单应用验证未启动壳应用。
