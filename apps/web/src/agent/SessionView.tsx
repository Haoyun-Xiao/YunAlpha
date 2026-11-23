import {publicShowcase} from '../deployment';
import {useCallback, useEffect, useRef, useState, type FormEvent, type MouseEvent} from 'react';
import {AgentError, agentApi, followEvents, sessionUrl, type Connection, type ReportRef, type Session, type SessionSummary} from './api';
import {applyEvent, newTurn, reportRef, reportsFromBlocks, reportsFromEvent, restoreTurn, statusLabels, turnReason, type Turn} from './turn';
import {ReportCard} from './ReportCard';
import {IndustryCard} from './IndustryCard';
import {MessageText} from './MessageText';
import './agent.css';

export function useSessions() {
  const [items, setItems] = useState<SessionSummary[]>([]);
  const [loading, setLoading] = useState(!publicShowcase);
  const [error, setError] = useState(false);
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => setRevision(value => value + 1), []);

  useEffect(() => {
    if (publicShowcase) return;
    const controller = new AbortController();
    setLoading(true);
    agentApi.sessions(controller.signal).then(items => {
      if (controller.signal.aborted) return;
      setItems(items);
      setError(false);
    }).catch(() => {
      if (!controller.signal.aborted) setError(true);
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [revision]);

  return {items, loading, error, refresh};
}

export function SessionHistory({sessions, selected, navigate}: {
  sessions: ReturnType<typeof useSessions>;
  selected: string | null;
  navigate: (event: MouseEvent<HTMLAnchorElement>) => void;
}) {
  return <div className="history-panel">
    <div className="history-heading"><p className="nav-heading">会话历史</p>
      <button type="button" className="agent-text-button" onClick={sessions.refresh} disabled={sessions.loading}>刷新</button>
    </div>
    {sessions.error ? <p className="history-note" role="status">暂时无法读取会话，请刷新。</p>
      : sessions.loading && !sessions.items.length ? <p className="history-note" role="status">正在读取会话…</p>
      : !sessions.items.length && <p className="history-note">尚无会话，从新建会话开始。</p>}
    <nav className="session-history" aria-label="会话历史">
      {sessions.items.map(item => <a key={item.session_id} className={selected === item.session_id ? 'is-current' : ''}
        aria-current={selected === item.session_id ? 'page' : undefined} title={item.title}
        href={sessionUrl(item.session_id)} onClick={navigate}>{item.title}</a>)}
    </nav>
  </div>;
}

export function PersonalSpace({sessions, navigate}: {
  sessions: ReturnType<typeof useSessions>;
  navigate: (event: MouseEvent<HTMLAnchorElement>) => void;
}) {
  const [query, setQuery] = useState('');
  const filtered = sessions.items.filter(item => item.title.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  return <>
    <div className="page-heading directory-heading"><div><h1>个人空间</h1><p>重开研究会话，查看其中的研究过程与报告。</p></div>
      <a className="agent-button agent-primary" href="/?view=session" onClick={navigate}>新建会话</a>
    </div>
    <div className="agent-space-tools">
      <label className="search-field"><input type="search" aria-label="搜索会话" placeholder="搜索会话" value={query} onChange={event => setQuery(event.target.value)}/></label>
      <button type="button" className="agent-button" onClick={sessions.refresh} disabled={sessions.loading}>刷新会话</button>
    </div>
    {sessions.error ? <p className="directory-message" role="alert">暂时无法读取会话，请重试。</p>
      : sessions.loading ? <p role="status" className="directory-message">正在读取会话…</p>
      : !filtered.length ? <div className="directory-message"><p>{query ? '没有找到符合条件的会话。' : '尚无研究会话。'}</p>
        {query && <button type="button" onClick={() => setQuery('')}>清除搜索</button>}</div>
      : <ul className="agent-space-list">{filtered.map(item => <li key={item.session_id}>
        <a href={sessionUrl(item.session_id)} onClick={navigate}>{item.title}</a>
        <time dateTime={item.updated_at}>{new Date(item.updated_at).toLocaleString('zh-CN')}</time>
      </li>)}</ul>}
  </>;
}

function ModelNotice() {
  const [missing, setMissing] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    void agentApi.health(controller.signal).then(health => {
      if (!controller.signal.aborted) setMissing(health.model_key === false);
    }).catch(() => {});
    return () => controller.abort();
  }, []);
  return missing ? <p className="agent-model-notice" role="status">尚未配置研究模型，配置后可开始研究</p> : null;
}

function Progress({turn}: {turn: Turn}) {
  const steps = [...turn.todos, ...Object.values(turn.tools)];
  return <section className="agent-progress" aria-label="研究进度">
    <div className="agent-progress-header"><h3>研究过程</h3><span className="status-label">{statusLabels[turn.status] || '状态待确认'}</span></div>
    {turn.summary && <p role="status">{turn.summary}</p>}
    {steps.length > 0 && <ul>{steps.map(step => <li key={step.id}>
      <span>{step.label}</span><small>{statusLabels[step.status] || '状态待确认'}</small>
    </li>)}</ul>}
    {turn.warning && <p>{turn.warning}</p>}
  </section>;
}

export function SessionView({id, created, changed, initialDraft = ''}: {
  id: string | null;
  initialDraft?: string;
  created: (id: string) => void;
  changed: () => void;
}) {
  const [session, setSession] = useState<Session>();
  const [error, setError] = useState('');
  const [actionError, setActionError] = useState('');
  const [loading, setLoading] = useState(Boolean(id));
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState(initialDraft);
  const [title, setTitle] = useState('');
  const [turn, setTurn] = useState<Turn>();
  const [reports, setReports] = useState<ReportRef[]>([]);
  const [connection, setConnection] = useState<Connection>('connecting');
  const [revision, setRevision] = useState(0);
  const lifetime = useRef<AbortController | null>(null);
  const stream = useRef<AbortController | null>(null);
  const cursor = useRef(0);
  const working = useRef(false);
  const restoreFocus = useRef(false);
  const input = useRef<HTMLTextAreaElement>(null);
  const messageEnd = useRef<HTMLDivElement>(null);
  const autoScroll = useRef(true);

  function addReports(refs: ReportRef[]) {
    if (!refs.length) return;
    setReports(previous => {
      const merged = new Map(previous.map(ref => [ref.job_id, ref]));
      for (const ref of refs) merged.set(ref.job_id, {...merged.get(ref.job_id), ...ref});
      return [...merged.values()];
    });
  }

  function watch(runId: string, signal: AbortSignal) {
    stream.current?.abort();
    const controller = new AbortController();
    stream.current = controller;
    const abort = () => controller.abort();
    signal.addEventListener('abort', abort, {once: true});
    void followEvents(agentApi.runEvents(runId), {
      signal: controller.signal, cursor: cursor.current, onConnection: setConnection,
      onEvent(event) {
        cursor.current = event.event_id;
        addReports(reportsFromEvent(event));
        const answer = event.event_type === 'done' && typeof event.payload.answer === 'string' ? event.payload.answer : null;
        setTurn(value => value?.id === runId ? {...applyEvent(value, event), ...(answer !== null ? {persisted: true} : {})} : value);
        if (answer !== null) setSession(value => value ? {...value, messages: [...value.messages, {
          message_id: `answer-${runId}`, role: 'assistant', content: answer,
        }]} : value);
        if (event.event_type === 'done' || event.event_type === 'run_recoverable') {
          changed();
          return false;
        }
        return true;
      },
      async onReconnect() {
        try {
          const snapshot = await agentApi.run(runId, controller.signal);
          if (controller.signal.aborted) return false;
          if (!['running', 'recoverable'].includes(snapshot.status)) {
            // Fetch durable history in case the final event was lost.
            setRevision(value => value + 1);
            return false;
          }
          setTurn(value => value ? {...value, status: snapshot.status, reason: snapshot.stop_reason ?? ''} : value);
        } catch { /* Reconnect keeps its cursor if the state query is also offline. */ }
        return true;
      },
    }).finally(() => signal.removeEventListener('abort', abort));
  }

  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    const {signal} = controller;
    working.current = false;
    setBusy(false);
    if (id) {
      setLoading(true);
      setError('');
      agentApi.session(id, signal).then(value => {
        if (signal.aborted) return;
        setSession(value);
        if (initialDraft && value.messages.length) setDraft('');
        const refs = value.messages.flatMap(message => reportsFromBlocks(message.metadata?.ui_blocks));
        const contextReport = reportRef({job_id: value.context?.active_report_job_id});
        if (contextReport) refs.push(contextReport);
        for (const event of value.latest_run?.events ?? []) refs.push(...reportsFromEvent(event));
        addReports(refs);
        setTurn(undefined);
        if (value.latest_run) {
          const restored = restoreTurn(value.latest_run, value.messages);
          setTurn(restored.turn);
          cursor.current = restored.cursor;
          if (value.latest_run.status === 'running') watch(value.latest_run.run_id, signal);
        }
      }).catch(error => {
        if (!signal.aborted) setError(error instanceof AgentError && error.status === 404
          ? '找不到这个会话，请从会话历史重新选择。' : '暂时无法打开会话，请重试。');
      }).finally(() => { if (!signal.aborted) setLoading(false); });
    }
    return () => {
      controller.abort();
      stream.current?.abort();
    };
  // The parent remounts when the selected session changes.
  }, [id, revision]);

  const active = busy || loading || turn?.status === 'running';
  useEffect(() => {
    if (!active && restoreFocus.current) {
      restoreFocus.current = false;
      input.current?.focus({preventScroll: true});
    }
  }, [active]);
  useEffect(() => {
    if (autoScroll.current) messageEnd.current?.scrollIntoView({block: 'nearest'});
  }, [turn?.text, session?.messages.length]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const signal = lifetime.current?.signal;
    if (!signal || signal.aborted || working.current) return;
    if (id && (!session || session.runtime_kind !== 'deep_agents' || active)) return;
    working.current = true;
    setBusy(true);
    setActionError('');
    try {
      if (!id) {
        const value = await agentApi.create(title.trim() || '新研究会话', signal);
        if (!signal.aborted) { changed(); created(value.session_id); }
      } else {
        const content = draft.trim();
        if (!content) return;
        const accepted = await agentApi.send(id, content, signal);
        if (signal.aborted) return;
        setSession(value => value ? {...value, messages: [...value.messages, {
          message_id: `pending-${accepted.run_id}`, role: 'user', content,
        }]} : value);
        setDraft('');
        setTurn(newTurn(accepted.run_id));
        cursor.current = 0;
        autoScroll.current = true;
        restoreFocus.current = true;
        watch(accepted.run_id, signal);
        changed();
      }
    } catch (error) {
      if (signal.aborted) return;
      setActionError(error instanceof AgentError && error.status === 409
        ? '这个会话有正在执行的任务或状态已变化。已重新读取会话，请确认后继续。'
        : id ? '发送结果未能确认，请先重新读取会话，避免重复发送。' : '暂时无法创建会话，请重试。');
      if (id) setRevision(value => value + 1);
    } finally {
      working.current = false;
      if (!signal.aborted) setBusy(false);
    }
  }

  async function resume() {
    const signal = lifetime.current?.signal;
    if (!signal || signal.aborted || !turn || working.current) return;
    working.current = true;
    setBusy(true);
    setActionError('');
    try {
      await agentApi.resumeRun(turn.id, signal);
      if (signal.aborted) return;
      setTurn(value => value ? {...value, status: 'running', reason: '', final: false} : value);
      restoreFocus.current = true;
      watch(turn.id, signal);
    } catch {
      if (!signal.aborted) {
        setActionError('无法确认继续状态，已重新读取会话，请稍后再试。');
        setRevision(value => value + 1);
      }
    } finally {
      working.current = false;
      if (!signal.aborted) setBusy(false);
    }
  }

  if (!id) return <section className="agent-new">
    <div className="page-heading"><h1>开始一次研究</h1><p>研究基金或产业链，查询材料、梳理问题并生成深度报告。</p></div>
    <ModelNotice/>
    <form onSubmit={event => void submit(event)}>
      <label htmlFor="session-title">会话名称</label>
      <input id="session-title" value={title} maxLength={160} placeholder="例如：基金经理风格、光模块产业链" onChange={event => setTitle(event.target.value)}/>
      <button className="agent-button agent-primary" type="submit" disabled={busy}>{busy ? '正在创建…' : '创建会话'}</button>
    </form>
    {actionError && <p role="alert" className="agent-error">{actionError}</p>}
  </section>;

  if (error) return <div className="directory-message"><p role="alert">{error}</p>
    <button type="button" onClick={() => setRevision(value => value + 1)}>重试</button></div>;
  if (loading || !session) return <p role="status" className="directory-message">正在打开会话…</p>;

  const readOnly = session.runtime_kind !== 'deep_agents';
  return <div className={`agent-workspace ${reports.length ? 'has-reports' : ''}`}>
    <section className="agent-conversation" aria-label="研究会话">
      <header className="agent-conversation-header"><h1>{session.title}</h1>
        <span className="status-label">{readOnly ? '只读会话' : turn ? statusLabels[turn.status] || '状态待确认' : '可以开始'}</span>
      </header>
      <ModelNotice/>
      <div className="agent-messages" onScroll={event => {
        const element = event.currentTarget;
        autoScroll.current = element.scrollHeight - element.scrollTop - element.clientHeight < 80;
      }} role="log" aria-label="会话消息" aria-live="polite" aria-relevant="additions text">
        {!session.messages.length && !turn && <div className="agent-welcome">
          <h2>这次想研究什么？</h2><p>可以输入基金名称、代码或产业链主题，并描述你关心的问题。</p>
        </div>}
        {session.messages.map(message => <article key={message.message_id} className={`agent-message ${message.role === 'user' ? 'is-user' : ''}`}>
          <h2>{message.role === 'user' ? '你' : '研究 Agent'}</h2><MessageText text={message.content}/>
        </article>)}
        {turn && <>
          <Progress turn={turn}/>
          {!turn.persisted && turn.text && <article className="agent-message">
            <h2>研究 Agent</h2><MessageText text={turn.text}/>
          </article>}
          {turn.status === 'running' && connection !== 'connected' && <p className="agent-connection" role="status">
            {connection === 'reconnecting' ? '消息连接中断，正在重连；已收到的内容会保留。' : '正在连接消息流…'}
          </p>}
          {turn.status === 'recoverable' && <div className="agent-recoverable" role="status">
            <p>{turnReason(turn.reason) || '研究已中断，可从断点继续。'}</p>
            <button type="button" className="agent-button" disabled={busy} onClick={() => void resume()}>{busy ? '正在请求继续…' : '从断点继续'}</button>
          </div>}
          {turn.status === 'stopped' && <p className="agent-connection">{turnReason(turn.reason) || '本轮研究已停止，可以继续提问。'}</p>}
          {turn.status === 'failed' && <p className="agent-error" role="alert">本轮研究失败，可以继续提问。</p>}
        </>}
        <div ref={messageEnd}/>
      </div>
      {actionError && <p role="alert" className="agent-error">{actionError}</p>}
      <form className="agent-composer" onSubmit={event => void submit(event)}>
        <label htmlFor="agent-message">{readOnly ? '历史会话只读，请新建会话。' : '研究问题'}</label>
        <textarea id="agent-message" ref={input} value={draft} maxLength={20000} rows={3}
          disabled={readOnly || active} placeholder="输入基金或产业链研究问题…"
          onChange={event => setDraft(event.target.value)}/>
        <div><small>{active ? '当前研究结束或暂停后可以继续提问。' : 'Enter 换行，点击发送提交问题。'}</small>
          <button type="submit" className="agent-button agent-primary" disabled={readOnly || active || !draft.trim()}>
            {busy ? '正在提交…' : '发送'}
          </button>
        </div>
      </form>
    </section>
    {reports.length > 0 && <aside className="agent-artifacts" aria-label="研究成果">
      <h2>研究成果 <span>{reports.length}</span></h2>
      {reports.map(report => report.type === 'industry_job'
        ? <IndustryCard key={report.job_id} report={report}/>
        : <ReportCard key={report.job_id} report={report}/>)}
    </aside>}
  </div>;
}
