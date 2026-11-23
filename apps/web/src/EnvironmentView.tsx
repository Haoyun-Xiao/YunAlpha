import {useEffect,useState} from 'react';
import {request, type Environment, type Task} from './api';

const labels = {queued:'等待执行',running:'正在执行',succeeded:'自检完成',failed:'自检失败'};
export function EnvironmentView(){
  const [env,setEnv]=useState<Environment|null>(null);
  const [error,setError]=useState('');
  const [taskError,setTaskError]=useState('');
  const [task,setTask]=useState<Task|null>(null);
  const [taskId,setTaskId]=useState(new URLSearchParams(location.search).get('task'));
  const [busy,setBusy]=useState(false);
  async function refresh(){try{setEnv(await request<Environment>('/environment'));setError('');}catch(e){setError(e instanceof Error?e.message:'后端连接失败');setEnv(null);}}
  useEffect(()=>{void refresh();const id=setInterval(()=>void refresh(),5000);return()=>clearInterval(id);},[]);
  useEffect(()=>{
    if(!taskId)return;
    let disposed=false;let timer:ReturnType<typeof setTimeout>;
    const path = `/self-checks/${encodeURIComponent(taskId)}`;
    async function poll(){try{const value=await request<Task>(path);if(disposed)return;setTask(value);setTaskError('');if(value.status==='queued'||value.status==='running')timer=setTimeout(()=>void poll(),1000);}catch(e){if(disposed)return;setTaskError(e instanceof Error?e.message:'任务读取失败');}}
    void poll();return()=>{disposed=true;clearTimeout(timer);};
  },[taskId]);
  async function submit(){
    const page = new URL(location.href);
    setBusy(true);setTaskError('');
    try {
      const value = await request<Task>('/self-checks','POST');
      setTask(value);setTaskId(value.id);
      if(location.href === page.href){
        page.searchParams.set('task', value.id);
        history.replaceState(null, '', page);
      }
    } catch(e){setTaskError(e instanceof Error?e.message:'提交失败');}
    finally{setBusy(false);}
  }
  return <div className="environment-view">
    <section className="page-heading"><h1>环境自检</h1><p>检查本机服务连接、数据目录与后台任务。</p></section>
    {error&&<div role="alert" className="error">后端暂不可用：{error} <button onClick={()=>void refresh()}>重新连接</button></div>}
    <section className="checks" aria-label="环境状态">
      {[['后端服务',!!env,'接口连接'],['数据目录',!!env?.lake_directory_readable,'仅检查可读取性'],['后台 Worker',!!env?.worker_running,'独立任务进程']].map(([title,ready,caption])=><article key={String(title)}><span className={`dot ${ready?'ready':''}`}/><h2>{title}</h2><strong>{ready?'已就绪':'未就绪'}</strong><p>{caption}</p></article>)}
    </section>
    <section className="task-panel"><div className="panel-heading"><div><h2>运行一次自检</h2><p>读取配置 → 检查目录 → 保存并读回结果。</p></div><button className="primary" onClick={()=>void submit()} disabled={!env||busy||task?.status==='queued'||task?.status==='running'}>{busy?'提交中…':'开始自检'}</button></div>
      {!task&&!taskError&&<div className="empty">尚未提交自检任务</div>}
      {task&&<div className="result" aria-live="polite"><div className="result-title"><strong>{labels[task.status]}</strong><span>{task.steps.length} / 4 步骤</span></div><progress value={task.steps.length} max={4}/>{task.status==='queued'&&!env?.worker_running&&<p>Worker 尚未启动，任务已保存并等待执行。</p>}<ol>{task.steps.map(step=><li key={step}>{step}</li>)}</ol>{task.error&&<p className="error" role="alert">{task.error}</p>}{task.result&&<p>结果文件：<code>{task.result.artifact}</code></p>}<small>任务编号 {task.id}</small></div>}
      {taskError&&<p className="error" role="alert">{taskError}</p>}
    </section>
    <footer><p>数据湖仅作为只读来源；目录可访问不表示业务数据完整。</p><details><summary>当前工作区</summary><code>{env?.workspace||'等待后端连接'}</code></details></footer>
  </div>;
}
