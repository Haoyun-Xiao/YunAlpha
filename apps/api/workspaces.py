"""Catalog entries for local workspaces started by the YunAlpha launcher."""

import os


_DESKS = (
    ('national_team', '国家队全景', '持有人结构', '查看主体、ETF 与个股的披露持仓和变动。'),
    ('contrarian', '逆向投资', '市场研究', '观察市场环境、行业估值与逆向候选。'),
    ('sentiment', '情绪温度计', '市场研究', '查看市场情绪、维度指标与历史区间。'),
    ('chips', '全 A 筹码结构', '资金与筹码', '查看获利比例、成本带、行业结构和个股详情。'),
    ('valuation', '市场估值', '市场研究', '查看宽基估值、历史分位与对照。'),
    ('fund_holdings', 'A 股公募持仓', '主动基金', '查看公募公司、个股及行业持仓。'),
    ('capital_flow', 'A 股资金流', '资金与筹码', '查看主力、两融、跨境和 ETF 资金。'),
)


def entry(module, label, href):
    if module not in os.getenv('YUNALPHA_PHASE2_MODULES', '').split(','):
        return None
    return {'kind': 'local', 'label': label, 'href': href}


def research_desks():
    rows = [('fund-crowding', '公募抱团', '主动基金', '查看集中度、披露持仓及相关风险指标。',
             'crowding', '/workspaces/crowding/')]
    rows.extend((id_, name, group, description, 'seven', f'/workspaces/vic-desks/?board={id_}')
                for id_, name, group, description in _DESKS)
    for id_, name, group, description, module, href in rows:
        launch = entry(module, '打开投研台', href)
        yield {
            'id': id_, 'name': name, 'groups': [group], 'description': description,
            'status': 'available' if launch else 'unavailable', 'entry': launch,
        }
