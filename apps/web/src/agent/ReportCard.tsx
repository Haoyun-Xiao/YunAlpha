import {useEffect, useRef, useState} from 'react';
import {agentApi, followEvents, type Connection, type ReportArtifact, type ReportJob, type ReportRef} from './api';

const statuses: Record<string, string> = {
  queued: '等待执行', resolving: '识别基金', checking_coverage: '核对数据覆盖', preparing_json: '整理基金数据',
  acquiring_materials: '准备研究材料', building_handbook: '编制投资手册', reusing_handbook: '读取既有手册',
  validating_handbook: '校验投资手册', splitting_part_data: '准备分节数据', context_preflight: '检查研究上下文',
  generating_parts: '撰写章节', summarizing: '整合报告', auditing: '审核内容', rewriting: '修订报告',
  rendering: '生成报告文件', indexing: '登记成果', recoverable: '等待继续', cancelling: '正在取消', cancelled: '已取消',
  completed: '已完成', completed_with_warnings: '已完成，有提示', failed_validation: '校验未通过', failed: '生成失败',
};

const successful = new Set(['completed', 'completed_with_warnings']);
const terminal = new Set([...successful, 'failed', 'failed_validation', 'cancelled']);
const resumable = new Set(['recoverable', 'failed', 'failed_validation']);
const downloads: Record<string, string> = {report_html: '下载网页', report_docx: '下载 Word', report_markdown: '下载 Markdown'};

export function ReportCard({report}: {report: ReportRef}) {
  const [job, setJob] = useState<ReportJob>();
  const [artifacts, setArtifacts] = useState<ReportArtifact[]>([]);
  const [error, setError] = useState('');
  const [resumeError, setResumeError] = useState('');
  const [busy, setBusy] = useState(false);
  const [connection, setConnection] = useState<Connection>('connecting');
  const [retry, setRetry] = useState(0);
  const cursor = useRef(0);
  const request = useRef<AbortController | null>(null);
  const id = report.job_id;

  useEffect(() => {
    const controller = new AbortController();
    request.current = controller;
    const stream = new AbortController();
    const {signal} = controller;
    let watching = false;
    let fetching = false;
    let refreshPending = false;
    let durableDone = false;

    async function refresh() {
      if (signal.aborted || durableDone) return;
      if (fetching) { refreshPending = true; return; }
      fetching = true;
      try {
        const snapshot = await agentApi.report(id, signal);
        if (signal.aborted) return;
        setJob(snapshot);
        const done = terminal.has(snapshot.status);
        if (done) {
          stream.abort();
          if (successful.has(snapshot.status) && snapshot.artifact_ids.length) {
            const result = await agentApi.artifacts(id, signal);
            if (signal.aborted) return;
            setArtifacts(result.artifacts.filter(item => snapshot.artifact_ids.includes(item.artifact_id)));
          }
          durableDone = true;
        } else if (!watching) {
          watching = true;
          void followEvents(agentApi.reportEvents(id), {
            signal: stream.signal,
            cursor: cursor.current,
            onConnection: setConnection,
            onEvent(event) {
              cursor.current = event.event_id;
              if (event.event_type !== 'model_call_progress') void refresh();
              // 'done' may belong to a previous attempt. Only GET can end tracking.
              return true;
            },
          });
        }
        setError('');
      } catch {
        if (!signal.aborted) setError('暂时无法更新报告状态或读取成果。');
      } finally {
        fetching = false;
        if (refreshPending) { refreshPending = false; void refresh(); }
      }
    }

    void refresh();
    const timer = window.setInterval(() => void refresh(), 2500);
    return () => {
      controller.abort();
      stream.abort();
      window.clearInterval(timer);
    };
  }, [id, retry]);

  async function resume() {
    const signal = request.current?.signal;
    if (!signal || signal.aborted || busy) return;
    setBusy(true);
    setResumeError('');
    try {
      await agentApi.resumeReport(id, signal);
      if (signal.aborted) return;
      setArtifacts([]);
      setRetry(value => value + 1);
    } catch {
      if (!signal.aborted) setResumeError('无法继续报告任务，请重试。');
    } finally {
      if (!signal.aborted) setBusy(false);
    }
  }

  const finished = job && successful.has(job.status);
  const hasHtml = artifacts.some(item => item.kind === 'report_html');
  const title = report.fund_name || job?.request.fund_query || report.fund_symbol || '基金深度报告';
  const percent = job ? Math.round(job.progress * 100) : 0;

  return <article className="agent-report-card">
    <div className="agent-report-heading">
      <h3>{title}</h3>
      {job && <span className={`status-label ${finished ? 'status-available' : ''}`}>{statuses[job.status] || '状态待确认'}</span>}
    </div>
    {!job && !error && <p role="status">正在读取报告任务…</p>}
    {job && <>
      <progress aria-label={`${title}进度`} value={job.progress} max={1}/>
      <p className="agent-report-meta">
        <span>{percent}%</span>
        {job.current_stage !== job.status && statuses[job.current_stage] && <span>{statuses[job.current_stage]}</span>}
        {job.request.as_of && <span>资料截止日 {job.request.as_of}</span>}
        {job.attempt > 1 && <span>第 {job.attempt} 次尝试</span>}
      </p>
      {job.warnings.length > 0 && <ul className="agent-report-notes">{job.warnings.map((note, i) => <li key={i}>{note}</li>)}</ul>}
      {job.errors.length > 0 && <ul className="agent-report-notes agent-error">{job.errors.map((note, i) => <li key={i}>{note}</li>)}</ul>}
      {!terminal.has(job.status) && connection === 'reconnecting' &&
        <p role="status">报告进度连接中断，正在重连并查询任务状态。</p>}
      {finished && !artifacts.length && !error && <p>{job.artifact_ids.length ? '正在读取成果…' : '此任务未返回可打开的成果。'}</p>}
      <div className="agent-actions">
        {finished && hasHtml && <a className="agent-button agent-primary" href={agentApi.reportView(id)} target="_blank" rel="noopener noreferrer">打开原版报告 <span aria-hidden="true">↗</span></a>}
        {finished && artifacts.map(item => downloads[item.kind] &&
          <a key={item.artifact_id} href={agentApi.artifact(item.artifact_id)}>{downloads[item.kind]}</a>)}
        {resumable.has(job.status) && <button type="button" className="agent-button" onClick={() => void resume()} disabled={busy}>{busy ? '正在请求继续…' : '继续报告任务'}</button>}
      </div>
    </>}
    {resumeError && <p className="agent-error" role="alert">{resumeError}</p>}
    {error && <p className="agent-error" role="alert">{error} <button type="button" className="agent-button" onClick={() => setRetry(value => value + 1)}>重试</button></p>}
  </article>;
}
