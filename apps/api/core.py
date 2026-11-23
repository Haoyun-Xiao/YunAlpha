import json
import os
from pathlib import Path
import sqlite3
import uuid
from datetime import datetime, timezone

ROOT = Path(os.environ.get("RESEARCH_WORKSPACE", Path(__file__).resolve().parents[2])).resolve()
RUNTIME = ROOT / ".runtime"
DB = RUNTIME / "state/tasks.sqlite"


def now():
    return datetime.now(timezone.utc).isoformat()


def config():
    local = ROOT / "config/local.json"
    return json.loads((local if local.exists() else ROOT / "config/environment.example.json").read_text())


def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def initialize():
    with connect() as con:
        con.execute("CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, status TEXT, steps TEXT, result TEXT, error TEXT, created_at TEXT, updated_at TEXT)")


def create_task():
    task_id = uuid.uuid4().hex
    with connect() as con:
        con.execute("INSERT INTO tasks VALUES (?, 'queued', '[]', NULL, NULL, ?, ?)", (task_id, now(), now()))
    return get_task(task_id)


def get_task(task_id):
    with connect() as con:
        row = con.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if row is None:
        return None
    task = dict(row)
    task["steps"] = json.loads(task["steps"])
    task["result"] = json.loads(task["result"]) if task["result"] else None
    return task


def update_task(task_id, status, steps, result=None, error=None):
    with connect() as con:
        con.execute("UPDATE tasks SET status=?,steps=?,result=?,error=?,updated_at=? WHERE id=?", (status,json.dumps(steps,ensure_ascii=False),json.dumps(result,ensure_ascii=False) if result else None,error,now(),task_id))


def claim():
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT id FROM tasks WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
        if row:
            con.execute("UPDATE tasks SET status='running',updated_at=? WHERE id=?", (now(),row['id']))
            return row['id']
    return None


def recover():
    # A single worker owns this queue; interrupted self-checks are safe to repeat.
    with connect() as con:
        con.execute("UPDATE tasks SET status='queued',steps='[]',result=NULL,error=NULL WHERE status='running'")


def execute(task_id):
    steps = []
    def complete(label):
        steps.append(label)
        update_task(task_id, "running", steps)
    try:
        cfg = config()
        if cfg.get('lake_access') != 'read_only':
            raise ValueError('数据湖必须配置为只读来源')
        complete('运行配置读取完成')
        lake = Path(cfg['lake_root'])
        if not lake.is_dir() or not os.access(lake, os.R_OK):
            raise ValueError('数据湖目录不存在或不可读取')
        complete('数据湖目录可读取（未检查业务数据完整性）')
        folder = RUNTIME / 'artifacts' / task_id
        folder.mkdir(parents=True, exist_ok=True)
        artifact = folder / 'self-check.json'
        payload = {'task_id':task_id,'workspace':str(ROOT),'lake_directory_readable':True,'lake_writes':False}
        artifact.write_text(json.dumps(payload,ensure_ascii=False,indent=2))
        assert json.loads(artifact.read_text()) == payload
        complete('本工作区结果文件写入并读回成功')
        result = {'message':'环境自检完成','artifact':str(artifact.relative_to(ROOT)),'lake_writes':False}
        steps.append('自检结果已保存')
        update_task(task_id,'succeeded',steps,result=result)
    except Exception as exc:
        update_task(task_id,'failed',steps,error=str(exc))
