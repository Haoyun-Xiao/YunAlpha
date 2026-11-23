"""Development commands for this workspace only."""
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import phase2


def cfg(root):
    return json.loads((root/'config/local.json').read_text())


def commands(root):
    config=cfg(root)
    python=root/'apps/api/.venv/bin/python'
    web=int(config.get('web_port',19100));api=int(config.get('api_port',19101))
    return {
        'api':[str(python),'-m','uvicorn','app:app','--app-dir',str(root/'apps/api'),'--host','127.0.0.1','--port',str(api)],
        'worker':[str(python),str(root/'apps/api/worker.py')],
        'web':[config['node'],str(root/'apps/web/node_modules/vite/bin/vite.js'),'--host','127.0.0.1','--port',str(web)],
    },web,api


def alive(item,root):
    result=subprocess.run(['ps','-p',str(item['pid']),'-o','args='],capture_output=True,text=True)
    if result.returncode or item['marker'] not in result.stdout:
        return False
    if str(root) in result.stdout:
        return True
    if 'cwd' not in item:
        return False
    # macOS can replace the virtualenv interpreter path in argv with Homebrew's Python launcher.
    cwd=subprocess.run(['lsof','-a','-p',str(item['pid']),'-d','cwd','-Fn'],capture_output=True,text=True)
    return cwd.returncode==0 and f"n{item['cwd']}" in cwd.stdout.splitlines()


def read_services(root):
    p=root/'.runtime/services.json'
    return json.loads(p.read_text()) if p.exists() else {}


def stop(root):
    records=read_services(root)
    for item in records.values():
        if alive(item,root):
            try:os.killpg(item['pid'],signal.SIGTERM)
            except ProcessLookupError:pass
    for _ in range(30):
        if not any(alive(x,root) for x in records.values()):break
        time.sleep(.1)
    if any(alive(x,root) for x in records.values()):
        raise RuntimeError('本工作区进程尚未退出，请查看日志后处理')
    (root/'.runtime/services.json').write_text('{}\n')
    env=root/'.runtime/environment.json';data=json.loads(env.read_text());data['services_started']=False;env.write_text(json.dumps(data,ensure_ascii=False,indent=2))
    print('本工作区验收服务已停止')


def setup(root):
    config=cfg(root);tools=root/'.runtime/tools'
    uv=tools/'bin/uv'
    if not uv.exists():
        subprocess.run([config['python'],'-m','venv',str(tools)],check=True)
        subprocess.run([str(tools/'bin/python'),'-m','pip','install','uv==0.12.5'],check=True)
    install_env={**os.environ,'UV_LINK_MODE':'copy'}
    projects=phase2.packages(root)
    for project in [root/'apps/api', *projects]:
        subprocess.run([str(uv),'sync','--frozen','--python',config['python']],cwd=project,env=install_env,check=True)
    agent=root/'apps/fund_agent'
    wheel=root/'.runtime/phase2-inputs'/phase2.AGENT_WHEEL
    if agent in projects and wheel.is_file():
        subprocess.run([str(uv),'pip','install','--python',str(agent/'.venv/bin/python'),'--no-deps',str(wheel)],
                       env=install_env,check=True)
    subprocess.run(['npm','ci','--no-audit','--no-fund'],cwd=root/'apps/web',check=True)
    print('依赖已按锁文件安装')


def start(root):
    if any(alive(x,root) for x in read_services(root).values()):
        raise RuntimeError('本工作区已有服务在运行；无需重复启动，或先执行 stop')
    processes,web,api=commands(root)
    extra,probes,extra_env=phase2.commands(root,cfg(root))
    processes.update(extra)
    ports=[web,api,*probes]
    if len(set(ports)) != len(ports):raise ValueError('本机服务端口不能相同')
    for port in ports:
        with socket.socket() as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:s.bind(('127.0.0.1',port))
            except OSError:raise RuntimeError(f'端口 {port} 已占用；请在 config/local.json 指定其他服务端口')
    env=os.environ.copy();env.update(RESEARCH_WORKSPACE=str(root),RESEARCH_API_PORT=str(api),PYTHONDONTWRITEBYTECODE='1')
    env.update(extra_env)
    env['PATH']=str(Path(cfg(root)['node']).parent)+os.pathsep+env.get('PATH','')
    if 'agent' in extra:
        bridge=root/'.runtime/phase2/fund_agent/lake_bridge'
        if not all((bridge/path).is_file() for path in ('duckdb/agent-lake.duckdb','mcp.toml')):
            subprocess.run(['bash',extra['agent'][1],'prepare'],cwd=root,env=env,check=True)
    records={}
    try:
        for name,argv in processes.items():
            with (root/f'.runtime/logs/{name}.log').open('a') as log:
                p=subprocess.Popen(argv,cwd=root/'apps/web' if name=='web' else root,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            markers = {'api':'uvicorn','worker':'apps/api/worker.py','web':'node_modules/vite/bin/vite.js',
                       **{key:f'-m {module}' for key,module in phase2.PROCESS_MODULES.items()}}
            records[name]={'pid':p.pid,'marker':markers[name]}
            if name in phase2.PROCESS_MODULES:
                records[name]['cwd']=str(Path(argv[1]).parent)
            (root/'.runtime/services.json').write_text(json.dumps(records))
        for _ in range(40):
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{api}/api/v1/environment',timeout=1) as response:state=json.load(response)
                with urllib.request.urlopen(f'http://127.0.0.1:{web}',timeout=1) as response:response.read(20)
                if state['workspace']==str(root) and state['worker_running']:break
            except (OSError,ValueError):pass
            time.sleep(.25)
        else:raise RuntimeError('启动未完成，请查看 .runtime/logs')
        for port,path in probes.items():
            # Migrated apps can load Excel data before accepting requests.
            for _ in range(120):
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{port}{path}',timeout=1) as response:
                        response.read(1)
                    break
                except OSError:
                    time.sleep(.3)
            else:raise RuntimeError(f'迁入模块未就绪：{port}{path}，请查看 .runtime/logs')
        if 'agent-worker' in records and not alive(records['agent-worker'],root):
            raise RuntimeError('基金 Agent Worker 未启动，请查看 .runtime/logs/agent-worker.log')
        path=root/'.runtime/environment.json';data=json.loads(path.read_text());data.update(services_started=True,ports={'web':web,'api':api,**json.loads(extra_env['YUNALPHA_PROXY_PORTS'])});path.write_text(json.dumps(data,ensure_ascii=False,indent=2))
        print(f'YunAlpha：http://127.0.0.1:{web}\nAPI：http://127.0.0.1:{api}/api/v1/health')
    except Exception:
        stop(root)
        raise


def check(root):
    records=read_services(root)
    for name in dict.fromkeys(['api','worker','web',*records]):
        print(name, '运行中' if name in records and alive(records[name],root) else '未启动')
    print('API依赖：', '已安装' if (root/'apps/api/.venv/bin/python').exists() else '未安装')
    print('前端依赖：', '已安装' if (root/'apps/web/node_modules/vite').exists() else '未安装')
