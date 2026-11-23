import {useEffect, useState} from 'react';
import {publicShowcase} from './deployment';
import {agentApi, type ResearchSkill} from './agent/api';
import {MessageText} from './agent/MessageText';
import './skills.css';

const files = import.meta.glob('../../fund_agent/src/fund_research_agent/deep_skills/*/SKILL.md', {query: '?raw', import: 'default', eager: true}) as Record<string, string>;
const refs = import.meta.glob('../../fund_agent/src/fund_research_agent/deep_skills/*/references/*.md', {query: '?raw', import: 'default', eager: true}) as Record<string, string>;
const bundled: ResearchSkill[] = Object.entries(files).map(([path, text]) => {
  const [, meta, body] = text.split('---', 3);
  const field = (name: string) => meta.match(new RegExp(`^\\s*${name}:\\s*(.+)$`, 'm'))?.[1]?.trim() || '';
  const id = field('name'), folder = path.slice(0, path.lastIndexOf('/'));
  return {id, name: field('display-name') || body.trim().match(/^#\s+(.+)$/m)?.[1] || id, description: field('description'), instructions: body.trim(), tools: field('allowed-tools').split(/\s+/).filter(Boolean), references: Object.entries(refs).filter(([key]) => key.startsWith(`${folder}/references/`)).map(([key, content]) => ({name: key.slice(folder.length + 1), content}))};
});
const readable = (text: string) => text.replace(/\[([^\]]+)\]\((?:references\/|\/skills\/)[^)]+\)/g, '$1');
const flows: Record<string, {title: string; steps: string[]; note: string}> = {
  'deep-report': {title: '主动基金深度报告', steps: ['确认基金与覆盖', '准备数据与资料', '生成或复用投资手册', '撰写与汇总', '审核修订', '渲染与索引'], note: '明确要求完整报告后提交后台任务；可查询、恢复或取消。'},
  'industry-research': {title: '产业链深度研究', steps: ['准备资料', '构建结构', '冻结数据', '环节研究', '独立审核', '综合判断', '导出成果'], note: '明确要求完整报告后提交后台任务，成果包含 HTML、Markdown 和 JSON。'},
};
export function SkillsView({started}: {started: (id: string, prompt: string) => void}) {
  const [items, setItems] = useState<ResearchSkill[]>(publicShowcase ? bundled : []);
  const [selected, setSelected] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(!publicShowcase);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (publicShowcase) return;
    const controller = new AbortController();
    setLoading(true); setError('');
    agentApi.skills(controller.signal).then(result => { if (!controller.signal.aborted) setItems(result.items); })
      .catch(() => { if (!controller.signal.aborted) setError('暂时无法读取 Skills，请重试。'); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [attempt]);
  const filtered = items.filter(item => `${item.name} ${item.description} ${flows[item.id]?.title || ''}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  const active = items.find(item => item.id === selected);
  const featured = ['deep-report', 'industry-research'].map(id => filtered.find(item => item.id === id)).filter((item): item is ResearchSkill => !!item);
  const others = filtered.filter(item => !flows[item.id]);
  async function start(skill: ResearchSkill) {
    setBusy(true); setError('');
    try { const session = await agentApi.create(skill.name, new AbortController().signal); started(session.session_id, skill.starter_prompt || `请使用${skill.name}，帮助我研究：`); }
    catch { setError('暂时无法创建会话，请重试。'); } finally { setBusy(false); }
  }
  function card(item: ResearchSkill) {
    const flow = flows[item.id];
    return <button className={`skill-card ${flow ? 'is-flow' : ''}`} key={item.id} onClick={() => setSelected(item.id)}>
      <span className="skill-card-kind">{flow ? '后台报告流程' : '研究方法'}</span><strong>{flow?.title || item.name}</strong><span className="skill-card-description">{item.description}</span>
      {flow && <span className="skill-card-steps">{flow.steps.map(step => <span key={step}>{step}</span>)}</span>}
      <span className="skill-card-action">查看详情 <span aria-hidden="true">↗</span></span>
    </button>;
  }
  return <>
    <div className="page-heading directory-heading"><div><h1>Skills</h1><p>从研究方法到后台报告，查看 Agent 实际使用的能力。</p></div>
      {!active && <label className="search-field"><input type="search" aria-label="搜索 Skills" placeholder="搜索研究能力" value={query} onChange={event => setQuery(event.target.value)}/></label>}
    </div>
    {error && <p role="alert" className="directory-message">{error} {!active && <button onClick={() => setAttempt(value => value + 1)}>重试</button>}</p>}
    {loading ? <p role="status" className="directory-message">正在读取研究能力…</p>
      : active ? <section className="skill-detail" aria-label={active.name}>
        <button className="skill-back" onClick={() => setSelected(null)}>← 返回全部 Skills</button>
        <header><span className="skill-card-kind">{flows[active.id] ? '后台报告流程' : '研究方法'}</span><h2>{flows[active.id]?.title || active.name}</h2><p>{active.description}</p></header>
        {flows[active.id] && <div className="skill-flow"><h3>实际工作流程</h3><ol>{flows[active.id].steps.map(step => <li key={step}>{step}</li>)}</ol><p>{flows[active.id].note}</p></div>}
        {!publicShowcase && <><button className="agent-button agent-primary" disabled={busy} onClick={() => void start(active)}>{busy ? '正在打开会话…' : '在 Agent 中使用'}</button><p className="skill-hint">进入会话后补充研究对象，再发送问题。</p></>}
        <div className="skill-method"><h3>研究方法</h3><MessageText text={readable(active.instructions)}/></div>
        {active.references?.map(reference => <details key={reference.name}><summary>{reference.name}</summary><MessageText text={reference.content}/></details>)}
        {!!active.tools?.length && <details><summary>可调用工具</summary><ul>{active.tools.map(tool => <li key={tool}><code>{tool}</code></li>)}</ul></details>}
      </section>
      : !filtered.length ? <p className="directory-message">{query ? '没有匹配的研究能力。' : '尚未加载研究能力。'}</p>
      : <div className="skills-directory">
        {!!featured.length && <section><div className="skills-section-heading"><h2>完整研究流程</h2><p>从研究对象到报告成果，任务可在后台执行。</p></div><div className="skill-feature-grid">{featured.map(card)}</div></section>}
        {!!others.length && <section><div className="skills-section-heading"><h2>研究方法</h2><p>按问题调用相应方法和工具。</p></div><div className="skill-grid">{others.map(card)}</div></section>}
        <p className="skill-boundary">指增研究目前仅有独立 Part1 样章与 CLI，尚未接入 Agent 会话或完整报告流程。</p>
      </div>}
  </>;
}
