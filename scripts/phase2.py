"""Commands and local routes for the migrated YunAlpha workspaces."""

import json


MODULES = {
    'agent': ('fund_agent', ('api',), 19102, '/agent-runtime', '/api/v1/dependencies/health'),
    'fof': ('fof', (), 19103, '/workspaces/fof', '/api/assets'),
    'pring': ('pring', (), 19106, '/workspaces/pring', '/api/assets'),
    'seven': ('vic_desks', ('seven',), 19104, '/workspaces/vic-desks', '/api/dashboards/health'),
    'crowding': ('vic_desks', ('crowding',), 19105, '/workspaces/crowding', '/api/crowding/health'),
}

PROCESS_MODULES = {
    'agent': 'fund_research_agent.api.app',
    'agent-worker': 'fund_research_agent.workflows.report.worker',
    'fof': 'backtest_platform.server',
    'pring': 'backtest_platform.server',
    'seven': 'seven_desks.server',
    'crowding': 'crowding.server',
}

AGENT_WHEEL = 'fund_agent/wheels/multi_asset_lake-0.6.0-py3-none-any.whl'


def packages(root):
    return [root / 'apps' / name for name in ('fund_agent', 'fof', 'vic_desks', 'pring')
            if (root / 'apps' / name / 'uv.lock').is_file()]


def commands(root, cfg):
    inputs = root / '.runtime/phase2-inputs'
    enabled = cfg.get('phase2_modules', [])
    unknown = set(enabled) - MODULES.keys()
    if unknown:
        raise ValueError(f'未知迁入模块：{", ".join(sorted(unknown))}')
    env = {
        'YUNALPHA_RUNTIME': str(root / '.runtime'),
        'YUNALPHA_INPUTS': str(inputs),
        'YUNALPHA_LAKE_ROOT': cfg['lake_root'],
        'YUNALPHA_PHASE2_MODULES': ','.join(enabled),
    }
    if cfg.get('lake_config'):
        env['YUNALPHA_LAKE_CONFIG'] = cfg['lake_config']
    versions = cfg.get('phase3_versions', {})
    env['YUNALPHA_SEVEN_VERSION'] = versions.get('seven', '')
    env['YUNALPHA_CROWDING_VERSION'] = versions.get('crowding', '')
    processes = {}
    probes = {}
    routes = {}
    for name in enabled:
        folder, mode, default_port, prefix, health = MODULES[name]
        app = root / 'apps' / folder
        port = int(cfg.get('phase2_ports', {}).get(name, default_port))
        if port in probes:
            raise ValueError(f'迁入模块端口重复：{port}')
        if not (app / 'run.sh').is_file() or not (app / '.venv/bin/python').is_file():
            raise RuntimeError(f'{name} 尚未安装，请在本工作区运行 setup')
        if not (inputs / folder).is_dir():
            raise RuntimeError(f'{name} 缺少迁入数据：{inputs / folder}')
        processes[name] = ['bash', str(app / 'run.sh'), *mode, str(port)]
        probes[port] = health
        routes[prefix] = port
        if name == 'agent':
            processes['agent-worker'] = ['bash', str(app / 'run.sh'), 'worker']
    env['YUNALPHA_PROXY_PORTS'] = json.dumps(routes)
    return processes, probes, env
