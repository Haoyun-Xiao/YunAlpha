from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import subprocess
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import core
from research import router as research_router
from strategies import router as strategies_router


@asynccontextmanager
async def lifespan(app):
    core.initialize()
    yield


app = FastAPI(title='YunAlpha', version='0.1.0', lifespan=lifespan)
app.include_router(research_router, prefix='/api/v1')
app.include_router(strategies_router, prefix='/api/v1')


class Task(BaseModel):
    id: str
    status: Literal['queued','running','succeeded','failed']
    steps: list[str]
    result: dict | None
    error: str | None
    created_at: str
    updated_at: str


def worker_running():
    path = core.RUNTIME / 'worker.json'
    if not path.exists():
        return False
    try:
        pid = json.loads(path.read_text())['pid']
        command = subprocess.run(['ps','-p',str(pid),'-o','args='],capture_output=True,text=True).stdout
        return str(core.ROOT / 'apps/api/worker.py') in command
    except (OSError, ValueError, KeyError):
        return False


@app.get('/api/v1/health')
def health():
    return {'service':'integrated-research-api','version':'0.1.0','workspace':str(core.ROOT)}


@app.get('/api/v1/environment')
def environment():
    cfg = core.config()
    lake = Path(cfg['lake_root'])
    return {'workspace':str(core.ROOT),'lake_directory_readable':lake.is_dir() and os.access(lake,os.R_OK),
            'lake_access':cfg['lake_access'],'worker_running':worker_running(),'task_database_ready':core.DB.is_file()}


@app.post('/api/v1/self-checks',status_code=202,response_model=Task)
def create():
    return core.create_task()


@app.get('/api/v1/self-checks/{task_id}',response_model=Task)
def get(task_id: str):
    task = core.get_task(task_id)
    if task is None:
        raise HTTPException(404,'未找到此工作区的任务')
    return task
