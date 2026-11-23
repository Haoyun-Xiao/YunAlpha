import {useEffect, useState} from 'react';
import {agentApi, type ResearchSkill} from './agent/api';
import {MessageText} from './agent/MessageText';
import './skills.css';

// Local skill references are shown below, rather than navigating to a virtual agent path.
const readableMethod = (text: string) => text.replace(/\[([^\]]+)\]\((?:references\/|\/skills\/)[^)]+\)/g, '$1');

export function SkillsView({started}: {started: (id: string, prompt: string) => void}) {
  const [items, setItems] = useState<ResearchSkill[]>([]);
  const [selected, setSelected] = useState('industry-research');
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError('');
    agentApi.skills(controller.signal).then(result => {
      if (!controller.signal.aborted) setItems(result.items);
    }).catch(() => { if (!controller.signal.aborted) setError('暂时无法读取 Skills，请重试。'); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [attempt]);
  const filtered = items.filter(item => `${item.name} ${item.description}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  const active = filtered.find(item => item.id === selected) || filtered[0];
  async function start(skill: ResearchSkill) {
    setBusy(true); setError('');
    try {
      const session = await agentApi.create(skill.name, new AbortController().signal);
      started(session.session_id, skill.starter_prompt || `请使用${skill.name}，帮助我研究：`);
    } catch { setError('暂时无法创建会话，请重试。'); }
    finally { setBusy(false); }
  }
  return <>
    <div className="page-heading directory-heading"><div><h1>Skills</h1><p>已有的研究方法与工作流，可在统一 Agent 中调用。</p></div>
      <label className="search-field"><input type="search" aria-label="搜索 Skills" placeholder="搜索研究能力" value={query} onChange={event => setQuery(event.target.value)}/></label>
    </div>
    {error && <p role="alert" className="directory-message">{error} <button onClick={() => setAttempt(value => value + 1)}>重试</button></p>}
    {loading ? <p role="status" className="directory-message">正在读取研究能力…</p>
      : !active ? <p className="directory-message">{query ? '没有匹配的研究能力。' : '尚未加载研究能力。'}</p>
      : <div className="skills-layout">
        <nav className="skills-list" aria-label="研究 Skills">{filtered.map(item => <button key={item.id} type="button"
          className={item.id === active.id ? 'is-selected' : ''} aria-pressed={item.id === active.id} onClick={() => setSelected(item.id)}>
          <strong>{item.name}</strong><span>{item.description}</span>
        </button>)}</nav>
        <section className="skill-detail" aria-label={active.name}>
          <header><span className="status-label status-available">已接入 Agent</span><h2>{active.name}</h2><p>{active.description}</p></header>
          <button className="agent-button agent-primary" disabled={busy} onClick={() => void start(active)}>{busy ? '正在打开会话…' : '在 Agent 中使用'}</button>
          <p className="skill-hint">进入会话后补充研究对象，再发送问题。完整研究会在后台执行，成果保存在会话中。</p>
          <div className="skill-method"><h3>研究方法</h3><MessageText text={readableMethod(active.instructions)}/></div>
          {active.references?.map(reference => <details key={reference.name}><summary>{reference.name}</summary><MessageText text={reference.content}/></details>)}
          {!!active.tools?.length && <details><summary>可调用工具</summary><ul>{active.tools.map(tool => <li key={tool}><code>{tool}</code></li>)}</ul></details>}
        </section>
      </div>}
  </>;
}
