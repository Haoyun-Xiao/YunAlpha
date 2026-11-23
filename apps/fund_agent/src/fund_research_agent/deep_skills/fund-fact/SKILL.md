---
name: fund-fact
description: 查询基金单个确定性事实或指标时使用，如年度收益、净值、规模或换手率。
allowed-tools: fund_resolve fund_metric
---

# 基金事实查询

先确认基金份额和截止日，再用 `fund_metric` 只查询目标指标。回答必须包含数据期、单位和口径；缺失直接披露，不将缺失值写为 0。单一事实不建立 Todo，不启动 Subagent。
