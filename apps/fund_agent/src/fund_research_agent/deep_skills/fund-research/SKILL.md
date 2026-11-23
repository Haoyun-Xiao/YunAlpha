---
name: fund-research
description: 对单只公募基金做综合研究、解读或评价时使用，覆盖收益、风险、规模、持仓和经理风格。
allowed-tools: fund_resolve fund_research_snapshot kb_search
---

# 单基金综合研究

1. 新基金先用 `fund_resolve` 确认代码、份额和当前经理；多个候选时询问用户。
2. 优先只调用一次 `fund_research_snapshot` 取得结构化快照。
3. 需要投资理念、季报、路演或历史报告时，再调用 1–2 次 `kb_search`。
4. 数字以数据工具为准，按“确定性事实—归纳—推断—数据缺口”组织。
5. 说明截止日、样本期、单位和统计口径，不把历史表现写成未来承诺。

停止条件：用户的核心问题已被回答，且关键数字有工具来源。不追加放大原始数组的查询。
