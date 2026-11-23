---
name: fund_research
trigger: 对单只基金做综合研究、解读或评价
allowed_tools: [fund.resolve, fund.research_snapshot, kb.search]
budget: {model_turns: 6, tool_calls: 5}
risk_level: low
---
基金身份确认后，优先只调用一次 fund.research_snapshot 取得结构化快照；需要经理理念、历史报告或定性解释时，再调用一到两次 kb.search。不追加放大原始数组的查询。数字以数据湖为准，最终按“事实—归纳—数据缺口”结构收敛。
