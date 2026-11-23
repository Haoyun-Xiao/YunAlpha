import {useEffect, useState} from 'react';
import {agentApi, type IndustryJob, type ReportRef} from './api';

const labels: Record<string, string> = {
  queued: '等待执行', running: '研究中', prepare: '准备公开材料', structure: '拆解产业链',
  snapshot: '核对公司与冻结数据', segments: '分环节研究', review: '独立风险审核',
  synthesis: '综合研究结论', publish: '导出研究报告', completed: '已完成',
  completed_with_warnings: '已完成，有资料缺口', recoverable: '等待继续',
  failed: '研究失败', cancelled: '已取消', cancelling: '正在取消',
};
const complete = new Set(['completed', 'completed_with_warnings']);
const terminal = new Set([...complete, 'cancelled', 'failed']);
function runtimeUrl(url: string) {
  return url.startsWith('/api/v1/') ? '/agent-runtime' + url : url;
}

export function IndustryCard({report}: {report: ReportRef}) {
  const [job, setJob] = useState<IndustryJob>();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let timer: number;
    async function refresh() {
      let again = true;
      try {
        const value = await agentApi.industry(report.job_id, controller.signal);
        if (controller.signal.aborted) return;
        setJob(value); setError('');
        again = !terminal.has(value.status);
      } catch {
        if (!controller.signal.aborted) setError('暂时无法读取产业链任务，请重试。');
      }
      if (again && !controller.signal.aborted) timer = window.setTimeout(() => void refresh(), 3000);
    }
    void refresh();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [report.job_id, revision]);

  async function action(kind: 'resume' | 'cancel') {
    if (busy) return;
    setBusy(true); setError('');
    try {
      await agentApi.industryAction(report.job_id, kind, new AbortController().signal);
      setRevision(value => value + 1);
    } catch { setError(kind === 'resume' ? '未能继续任务，请重试。' : '未能取消任务，请重试。'); }
    finally { setBusy(false); }
  }

  const title = job?.anchor || report.title || '产业链研究';
  const done = job && complete.has(job.status);
  return <article className="agent-report-card">
    <div className="agent-report-heading"><h3>{title}</h3>
      {job && <span className={`status-label ${done ? 'status-available' : ''}`}>{labels[job.status] || job.status}</span>}
    </div>
    <p className="agent-report-meta">产业链深度研究</p>
    {!job && !error && <p role="status">正在读取研究任务…</p>}
    {job && <>
      <progress aria-label={`${title}研究进度`} value={job.progress} max={100}/>
      <p className="agent-report-meta"><span>{Math.round(job.progress)}%</span><span>{labels[job.stage] || job.stage}</span>
        {job.as_of && <span>研究基准日 {job.as_of}</span>}</p>
      {job.error && !done && <p className="agent-error">{job.error}</p>}
      {!!job.warnings?.length && <details className="agent-report-notes"><summary>资料与口径提示（{job.warnings.length}）</summary>
        <ul>{job.warnings.map((item, i) => <li key={i}>{item}</li>)}</ul></details>}
      <div className="agent-actions">
        {done && job.view_url && <a className="agent-button agent-primary" href={runtimeUrl(job.view_url)} target="_blank" rel="noopener noreferrer">打开产业链报告 ↗</a>}
        {done && job.artifacts?.map(item => <a key={item.url} href={runtimeUrl(item.url)} target="_blank" rel="noopener noreferrer">{item.label}</a>)}
        {['recoverable', 'failed'].includes(job.status) && <button className="agent-button" disabled={busy} onClick={() => void action('resume')}>继续研究任务</button>}
        {!terminal.has(job.status) && <button className="agent-text-button" disabled={busy} onClick={() => void action('cancel')}>取消任务</button>}
      </div>
    </>}
    {error && <p className="agent-error" role="alert">{error} <button className="agent-text-button" onClick={() => setRevision(value => value + 1)}>重试</button></p>}
  </article>;
}
