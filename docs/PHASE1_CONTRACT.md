# YunAlpha 第一阶段目录接口

供前端、投研台和策略三个任务共用。目录只负责发现与访问已有成果。各列表按显示顺序返回 `{"items": [...]}`，空列表返回 `{"items": []}`。浏览器使用同源 `/api/v1`，开发代理沿用 Vite 配置。

| GET 路径 | 项目 |
|---|---|
| `/api/v1/research/modules` | 投研台模块 |
| `/api/v1/research/reports` | 已验证的基金深度报告索引 |
| `/api/v1/strategies` | 策略目录 |

投研台与策略共用目录形状：

```json
{
  "items": [{
    "id": "fund-deep-reports",
    "name": "基金深度报告",
    "groups": ["主动基金"],
    "description": "浏览已完成的深度研究报告。",
    "status": "available",
    "entry": {"kind": "local", "label": "查看报告列表", "href": "/?view=reports"}
  }]
}
```

- `groups` 是显示名称列表。同一投研台模块可出现在多个分组，入口不重复实现。策略页可以直接使用组名作分类标签。
- `status` 为 `available`（可访问）、`planned`（未开放）、`unavailable`（所需本机来源暂不可读）。后两者 `entry` 为 `null`；前端显示状态，无可点击的假入口。
- `entry.kind` 为 `local`（同源页面或本机只读转接）或 `legacy`（既有 HTTPS 网站）。`label` 是用户看到的操作名称，`href` 由业务模块给出。原报告、策略成果和外部入口从新标签页打开，`/?view=reports` 在同一 YunAlpha 页面导航。

报告列表形状（值仅为接口示例）：

```json
{
  "items": [{
    "id": "example-fund-report",
    "fund": {"code": "000000.OF", "name": "示例基金"},
    "title": "示例基金深度报告",
    "date": "2026-05-30",
    "date_kind": "as_of",
    "entry": {"kind": "local", "label": "打开原版报告", "href": "/api/v1/research/content/example-fund-report/report.html"}
  }]
}
```

日期必须来自真实资料。`date_kind` 为 `published`（报告发布日期）、`as_of`（资料截止日）或 `null`（日期不可证实，`date` 同为 `null`）。前端区分显示，不将数据日期标成发布日期，不按文件修改时间猜测。

B、C 分别在 `research`、`strategies` 包导出 `router`；各自路由定义不含 `/api/v1`，总控统一挂载。模块自己的只读成果页面及必要资源也由对应 router 提供。以项目索引解析旧项目根目录，只开放当前入口需要的产物，不发布整个旧仓库。索引不返回本机绝对路径。缺少来源时显示不可用；无报告时列表为空。无需实时探测外部网站。

首期目录不增加策略运行、报告生成或下载队列。红利低波如采用既有结果，入口和描述应明确为历史回测快照。前端使用这里的形状开发，集成环境只使用真实接口。原自检页保留，建议 `/?view=environment`，原 `?task=...` 链接继续可用。
