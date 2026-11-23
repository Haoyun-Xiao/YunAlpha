"""Refresh published lake inputs, then select and serve validated desk candidates."""
import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time
import urllib.request

import phase2
import services

ROOT = Path(__file__).resolve().parents[1]


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    if path.exists():
        temp.chmod(path.stat().st_mode & 0o777)
    temp.replace(path)


def restart(root, name, cfg, *, restore_running=False):
    records = services.read_services(root)
    old = records.get(name)
    if not old:
        return 'not_running'
    if services.alive(old, root):
        os.killpg(os.getpgid(old['pid']), signal.SIGTERM)
        for _ in range(50):
            if not services.alive(old, root):
                break
            time.sleep(.1)
        else:
            raise RuntimeError(f'{name}: old process has not stopped')
    elif not restore_running:
        return 'not_running'
    commands, _, env = phase2.commands(root, cfg)
    with (root / f'.runtime/logs/{name}.log').open('ab') as log:
        process = subprocess.Popen(commands[name], cwd=root,
            env={**os.environ, **env, 'PYTHONDONTWRITEBYTECODE': '1'},
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    records[name] = {**old, 'pid': process.pid}
    save(root / '.runtime/services.json', records)
    _, _, default_port, _, route = phase2.MODULES[name]
    port = cfg.get('phase2_ports', {}).get(name, default_port)
    expected = cfg['phase3_versions'][name]
    for _ in range(60):
        if process.poll() is not None:
            raise RuntimeError(f'{name}: candidate process exited')
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}{route}', timeout=2) as response:
                data = json.load(response)
            version = data.get('data_version', data.get('active_version'))
            if version == expected:
                return 'restarted'
        except (OSError, ValueError):
            pass
        time.sleep(.25)
    raise RuntimeError(f'{name}: candidate health/version check failed')


def run(root, build_only=False):
    out = root / '.runtime/desk-refresh'
    out.mkdir(parents=True, exist_ok=True)
    with (out / 'refresh.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        cfg = services.cfg(root)
        versions = cfg['phase3_versions']
        result = {'started_at': datetime.now(timezone.utc).isoformat(),
                  'trigger': os.environ.get('MAL_TRIGGER', 'manual'),
                  'requested_date': os.environ.get('MAL_TRADE_DATE'), 'status': 'building'}
        save(out / 'latest.json', result)
        try:
            command = [str(root / 'apps/vic_desks/.venv/bin/python'),
                       str(root / 'apps/vic_desks/scripts/refresh_daily.py'),
                       '--lake-config', cfg.get('lake_config', str(Path(cfg['lake_root']).parent / 'configs/multi-asset-lake.toml')),
                       '--base-runtime', str(root / '.runtime'),
                       '--seven-version', versions['seven'], '--crowding-version', versions['crowding']]
            if result['requested_date']:
                command += ['--as-of', result['requested_date']]
            env = {**os.environ, 'YUNALPHA_RUNTIME': str(root / '.runtime'),
                   'YUNALPHA_INPUTS': str(root / '.runtime/phase2-inputs'),
                   'YUNALPHA_LAKE_ROOT': cfg['lake_root'], 'PYTHONDONTWRITEBYTECODE': '1'}
            with (out / 'build.log').open('ab') as log:
                subprocess.run(command, cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
            build = json.loads((root / '.runtime/phase2/vic_desks/daily/latest.json').read_text())
            crowd_command = [command[0], str(root / 'apps/vic_desks/scripts/build_crowding_cache.py'),
                             '--daily', '--as-of', build['target_trading_date'],
                             '--base-version', versions['crowding']]
            crowd_log = out / 'crowding-build.log'
            with crowd_log.open('w') as log:
                subprocess.run(crowd_command, cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
            crowd = json.loads(crowd_log.read_text().strip().splitlines()[-1])
            if crowd.get('status') not in ('complete', 'reused') or crowd.get('failures'):
                raise ValueError('crowding full-page build did not complete')
            build['candidates']['crowding'] = {'version': crowd['version'], 'path': crowd['path'],
                                                'created': crowd['status'] == 'complete'}
            meta = crowd['metadata']
            market_date = meta['market_date']
            build['boards']['crowding'] = {
                'core_returns_and_risk': {'status': 'current' if market_date == build['target_trading_date'] else 'lagged',
                    'data_date': market_date, 'reason': '六页及四个收益起点按已发布输入重建；基金净值日期见基金页'},
                'holdings': {'status': 'disclosure', 'data_date': meta['holding_period'],
                    'reason': '正式全持仓报告期，未用季度前十大替代'},
                'fund_scale': {'status': 'disclosure', 'data_date': crowd['fund_scale_period'],
                    'reason': '仅展示同季关系和份额均已验证的规模，缺失仍保留'}}
            build['crowding_build'] = {'status': crowd['status'], 'elapsed_seconds': crowd['elapsed_seconds']}
            new_versions = {name: build['candidates'][name]['version'] for name in ('seven', 'crowding')}
            build['status'] = 'ready' if any(new_versions[n] != versions[n] for n in new_versions) else 'unchanged'
            save(root / '.runtime/phase2/vic_desks/daily/latest.json', build)
            if any(not re.fullmatch('[0-9a-f]{16}', v) for v in new_versions.values()):
                raise ValueError('invalid candidate version')
            result['build'] = build
            if build_only:
                result['status'] = 'built_not_selected'
            else:
                new_cfg = {**cfg, 'phase3_versions': {**versions, **new_versions}}
                result['services'] = {}
                changed = []
                try:
                    for name, version in new_versions.items():
                        if version == versions[name]:
                            result['services'][name] = 'unchanged'
                            continue
                        record = services.read_services(root).get(name)
                        if record and services.alive(record, root):
                            changed.append(name)
                        result['services'][name] = restart(root, name, new_cfg)
                    save(root / 'config/local.json', new_cfg)
                    result['status'] = 'completed'
                except Exception:
                    for name in changed:
                        try:
                            result['services'][name] = 'rollback_' + restart(root, name, cfg, restore_running=True)
                        except Exception as error:
                            result['services'][name] = 'rollback_failed: ' + str(error)
                    raise
        except Exception as error:
            result.update(status='failed', error=str(error))
            raise
        finally:
            result['finished_at'] = datetime.now(timezone.utc).isoformat()
            save(out / 'latest.json', result)
        print(json.dumps({'status': result['status'], 'versions': new_versions, 'summary': str(out / 'latest.json')}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-only', action='store_true')
    run(ROOT, parser.parse_args().build_only)
