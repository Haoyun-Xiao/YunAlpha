from pathlib import Path
import pytest
from fastapi.testclient import TestClient
import core
from app import app


@pytest.fixture
def workspace(tmp_path,monkeypatch):
    monkeypatch.setattr(core,'ROOT',tmp_path)
    monkeypatch.setattr(core,'RUNTIME',tmp_path/'.runtime')
    monkeypatch.setattr(core,'DB',tmp_path/'.runtime/state/tasks.sqlite')
    monkeypatch.setattr(core,'config',lambda:{'lake_access':'read_only','lake_root':str(tmp_path/'lake')})
    (tmp_path/'lake').mkdir()
    return tmp_path


def test_submit_execute_and_reload(workspace):
    with TestClient(app) as client:
        response=client.post('/api/v1/self-checks')
        assert response.status_code==202
        task=response.json();assert task['status']=='queued'
        assert client.get('/api/v1/environment').json()['worker_running'] is False
        assert core.claim()==task['id']
        core.execute(task['id'])
        result=client.get('/api/v1/self-checks/'+task['id']).json()
        assert result['status']=='succeeded' and len(result['steps'])==4
        assert (workspace/result['result']['artifact']).is_file()
        assert list((workspace/'lake').iterdir())==[]
    with TestClient(app) as client:
        assert client.get('/api/v1/self-checks/'+task['id']).json()['status']=='succeeded'
        assert client.get('/api/v1/self-checks/missing').status_code==404


def test_failure_and_restart(workspace):
    core.initialize();task=core.create_task();core.claim();core.recover()
    assert core.get_task(task['id'])['status']=='queued'
    (workspace/'lake').rmdir()
    assert core.claim()==task['id']
    core.execute(task['id'])
    result=core.get_task(task['id'])
    assert result['status']=='failed' and '数据湖目录' in result['error']
    assert result['steps']==['运行配置读取完成']
