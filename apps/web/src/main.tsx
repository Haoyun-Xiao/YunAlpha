import {appPath, newsUrl, publicShowcase} from './deployment';
import {StrictMode, useEffect, useRef, useState, type MouseEvent, type ReactNode} from 'react';
import {createRoot} from 'react-dom/client';
import {CatalogView, ReportsView, type DirectoryOptions} from './CatalogView';
import {EnvironmentView} from './EnvironmentView';
import {SkillsView} from './SkillsView';
import {PersonalSpace, SessionHistory, SessionView, useSessions} from './agent/SessionView';
import {sessionUrl} from './agent/api';
import './styles.css';

const navigation = [
  {id: 'research', label: '投研台', icon: 'research'},
  {id: 'news', label: '舆情', icon: 'news'},
  {id: 'strategies', label: '策略', icon: 'strategies'},
  {id: 'watchlist', label: '自选', icon: 'watchlist', planned: true},
  {id: 'signals', label: '信号', icon: 'signals', planned: true},
  {id: 'skills', label: 'Skills', icon: 'skills'},
  {id: 'tasks', label: '任务', icon: 'tasks', planned: true},
  {id: 'plaza', label: '广场', icon: 'plaza', planned: true},
  {id: 'deep', label: '深入', icon: 'deep', planned: true},
] as const;

const views = [
  ...navigation,
  {id: 'session', label: '新建会话'},
  {id: 'space', label: '个人空间'},
  {id: 'reports', label: '基金深度报告'},
  {id: 'environment', label: '环境自检'},
] as const;

type View = typeof views[number]['id'];

const iconPaths = {
  plus: 'M12 5v14M5 12h14',
  research: 'M3 4h7v7H3zM14 4h7v4h-7zM3 15h7v5H3zM14 12h7v8h-7z',
  strategies: 'M3 19h18M5 15l5-5 4 3 5-8M15 5h4v4',
  news: 'M4 5h16v14H4zM7 9h5M7 12h5M7 15h10M15 9h2v3h-2z',
  watchlist: 'M12 3l2.7 5.5 6.1.9-4.4 4.3 1 6.1-5.4-2.9-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z',
  signals: 'M3 12h4l3-7 4 14 3-7h4',
  skills: 'M12 3v18M3 12h18M6 6l12 12M18 6L6 18',
  tasks: 'M9 5H5v16h14V5h-4M9 3h6v4H9zM8 12h8M8 16h6',
  plaza: 'M4 21V9h16v12M2 9l10-6 10 6M9 21v-6h6v6M2 21h20',
  deep: 'M7 4h10v5l3 5v5H4v-5l3-5zM7 9h10M8 15h8',
  space: 'M4 5h6l2 3h8v11H4z',
  environment: 'M3 5h18v12H3zM8 21h8M12 17v4M7 9l3 2-3 2M12 13h4',
  menu: 'M4 6h16M4 12h16M4 18h16',
  collapse: 'M3 5h18v14H3zM9 5v14M15 9l-3 3 3 3',
  expand: 'M3 5h18v14H3zM9 5v14M12 9l3 3-3 3',
};

function Icon({name}: {name: keyof typeof iconPaths}) {
  return <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round"><path d={iconPaths[name]}/></svg>;
}

function currentView(): View {
  const params = new URLSearchParams(location.search);
  const view = params.get('view');
  return views.find(item => item.id === view)?.id ??
    (view === null && params.has('task') ? 'environment' : 'research');
}

function NewsView() {
  return <section className="news-view" aria-label="舆情">
    <div className="news-toolbar"><span>舆情 · AIHOT</span><a href={newsUrl} target="_blank" rel="noreferrer">在新标签页打开 ↗</a></div>
    <iframe src={newsUrl} title="AIHOT 舆情"/>
  </section>;
}

function App() {
  const [{view, revision}, setRoute] = useState(() => ({view: currentView(), revision: 0}));
  const sessions = useSessions();
  const [skillDraft, setSkillDraft] = useState<{id: string; prompt: string}>();
  const selectedSession = view === 'session' ? new URLSearchParams(location.search).get('session') : null;
  const [menuOpen, setMenuOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [research, setResearch] = useState<DirectoryOptions>({query: '', group: null, layout: 'cards'});
  const [strategies, setStrategies] = useState<DirectoryOptions>({query: '', group: null, layout: 'cards'});
  const content = useRef<HTMLElement>(null);
  const label = selectedSession ? '研究会话' : views.find(item => item.id === view)!.label;

  useEffect(() => {
    function sync() {
      setRoute(previous => ({view: currentView(), revision: previous.revision + 1}));
      setMenuOpen(false);
    }
    window.addEventListener('popstate', sync);
    return () => window.removeEventListener('popstate', sync);
  }, []);
  useEffect(() => { document.title = `${label} | YunAlpha`; }, [label]);

  function openSession(id: string, prompt = '') {
    setSkillDraft({id, prompt});
    history.pushState(null, '', sessionUrl(id));
    setRoute(previous => ({view: 'session', revision: previous.revision + 1}));
    setMenuOpen(false);
    content.current?.focus({preventScroll: true});
  }

  function navigate(event: MouseEvent<HTMLAnchorElement>) {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    if (event.currentTarget.href !== location.href) {
      history.pushState(null, '', event.currentTarget.href);
      setRoute(previous => ({view: currentView(), revision: previous.revision + 1}));
    }
    setMenuOpen(false);
    content.current?.focus({preventScroll: true});
    window.scrollTo(0, 0);
  }

  function navLink(id: View, icon: keyof typeof iconPaths, text: string, planned = false): ReactNode {
    const active = view === id || (id === 'research' && view === 'reports');
    const name = planned ? `${text}（未开放）` : text;
    return <a key={id} className={`nav-link ${active ? 'is-current' : ''}`} href={appPath(`/?view=${id}`)}
      onClick={navigate} aria-label={name} title={name} aria-current={view === id ? 'page' : undefined}>
      <Icon name={icon}/><span>{text}</span>{planned && <small>未开放</small>}
    </a>;
  }

  return <div className={`app-shell ${collapsed ? 'sidebar-collapsed' : ''}`}>
    <aside className="sidebar" aria-label="左侧栏">
      <div className="sidebar-top">
        <a className="wordmark" href={appPath('/')} onClick={navigate} aria-label="YunAlpha 首页">
          <svg aria-hidden="true" width="34" height="34" viewBox="0 0 34 34">
            <rect width="34" height="34" rx="8" fill="currentColor"/>
            <path d="M8 24 15 10l4 8 3-6 4 12M11 20h5" fill="none" stroke="#14233d" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round"/>
          </svg>
          <span>YunAlpha</span>
        </a>
        <button type="button" className="menu-toggle" onClick={() => setMenuOpen(!menuOpen)}
          aria-controls="workspace-menu" aria-expanded={menuOpen}><Icon name="menu"/>导航</button>
      </div>
      <div id="workspace-menu" className={`sidebar-body ${menuOpen ? 'is-open' : ''}`}>
        <div className="sidebar-scroll">
          <nav className="session-links" aria-label="会话与空间">
            {navLink('session', 'plus', publicShowcase ? '研究 Agent' : '新建会话')}
          </nav>
          <nav className="primary-nav" aria-label="工作台导航">
            <p className="nav-heading">研究工作台</p>
            {navigation.map(item => <div key={item.id}>{navLink(item.id, item.icon, item.label, 'planned' in item)}{item.id === 'news' && !publicShowcase && navLink('space', 'space', '个人空间')}</div>)}
          </nav>
          {!publicShowcase && <SessionHistory sessions={sessions} selected={selectedSession} navigate={navigate}/>}
        </div>
        {!publicShowcase && <nav className="sidebar-footer" aria-label="本机工具">
          {navLink('environment', 'environment', '环境自检')}
        </nav>}
      </div>
    </aside>
    <main className="workspace" ref={content} tabIndex={-1}>
      <div className="workspace-bar">
        <button type="button" className="desktop-toggle" onClick={() => setCollapsed(!collapsed)}
          aria-controls="workspace-menu" aria-expanded={!collapsed} aria-label={collapsed ? '展开侧栏' : '收起侧栏'}
          title={collapsed ? '展开侧栏' : '收起侧栏'}><Icon name={collapsed ? 'expand' : 'collapse'}/></button>
        {view === 'reports'
          ? <nav aria-label="当前位置"><a href={appPath('/?view=research')} onClick={navigate}>投研台</a><span aria-hidden="true">/</span><span aria-current="page">{label}</span></nav>
          : <div><span>研究工作台</span><span aria-hidden="true">/</span><span>{label}</span></div>}
      </div>
      <div className={`workspace-content ${view === 'session' ? 'is-agent-view' : ''} ${view === 'news' ? 'is-news-view' : ''}`} key={revision}>
        {publicShowcase && ['session', 'space', 'environment'].includes(view) ? <div className="page-heading">
            <h1>研究 Agent</h1>
            <p>基金深度报告与产业链研究共用 Agent、工具调用和后台报告任务。公开版提供研究成果与实现源码。</p>
            <p><a className="entry-link" href={appPath('/?view=reports')} onClick={navigate}>查看基金深度报告</a></p>
            <p><a className="entry-link" href="https://github.com/Haoyun-Xiao/YunAlpha/tree/main/apps/fund_agent" target="_blank" rel="noreferrer">查看 Agent 与工作流源码 ↗</a></p>
          </div> : view === 'research' || view === 'strategies' ? <CatalogView kind={view} navigate={navigate}
          options={view === 'research' ? research : strategies} onChange={view === 'research' ? setResearch : setStrategies}/>
          : view === 'news' ? <NewsView/>
          : view === 'reports' ? <ReportsView/>
          : view === 'environment' ? <EnvironmentView/>
          : view === 'skills' ? <SkillsView started={(id, prompt) => { sessions.refresh(); openSession(id, prompt); }}/>
          : view === 'session' ? <SessionView id={selectedSession} initialDraft={skillDraft?.id === selectedSession ? skillDraft.prompt : ''} created={openSession} changed={sessions.refresh}/>
          : view === 'space' ? <PersonalSpace sessions={sessions} navigate={navigate}/>
          : <div className="page-heading">
            <span className="status-label status-planned">未开放</span>
            <h1>{label}</h1>
            <p>此功能将在后续阶段开放。</p>
          </div>}
      </div>
    </main>
  </div>;
}

createRoot(document.getElementById('root')!).render(<StrictMode><App/></StrictMode>);
