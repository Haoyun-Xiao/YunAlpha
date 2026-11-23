import {appPath, basePath} from './deployment';
import {useEffect, useRef, useState, type MouseEvent, type ReactNode} from 'react';
import {request, type CatalogItem, type Entry, type ReportItem} from './api';

type Navigate = (event: MouseEvent<HTMLAnchorElement>) => void;
export type DirectoryOptions = {query: string; group: string | null; layout: 'cards' | 'list'};

const statusLabels = {
  available: '可访问',
  planned: '未开放',
  unavailable: '来源暂不可用',
};

type DirectoryState<T> =
  | {status: 'loading'}
  | {status: 'error'}
  | {status: 'ready'; items: T[]};

function Directory<T>({path, empty, children}: {
  path: string;
  empty: string;
  children: (items: T[]) => ReactNode;
}) {
  const [state, setState] = useState<DirectoryState<T>>({status: 'loading'});
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    request<{items: T[]}>(path, 'GET', controller.signal)
      .then(data => {
        if (!Array.isArray(data.items)) throw new Error('目录响应缺少 items');
        if (!controller.signal.aborted) setState({status: 'ready', items: data.items});
      })
      .catch(() => { if (!controller.signal.aborted) setState({status: 'error'}); });
    return () => controller.abort();
  }, [path, attempt]);

  if (state.status === 'loading') return <p className="directory-message" role="status">正在加载目录…</p>;
  if (state.status === 'error') return <div className="directory-message">
    <p role="alert">暂时无法加载目录，请重试。</p>
    <button type="button" onClick={() => {
      setState({status: 'loading'});
      setAttempt(attempt + 1);
    }}>重试</button>
  </div>;
  if (state.items.length === 0) return <p className="directory-message">{empty}</p>;
  return children(state.items);
}

/** Reports index stays in YunAlpha. Original content links open a new tab. */
function EntryLink({entry, navigate, children, className = 'entry-link', name}: {entry: Entry; navigate?: Navigate; children?: ReactNode; className?: string; name?: string}) {
  let url: URL;
  try {
    url = new URL(entry.kind === 'local' ? appPath(entry.href) : entry.href, location.origin);
  } catch {
    return null;
  }

  if (entry.kind === 'local' && (url.origin !== location.origin || url.protocol !== location.protocol)) return null;
  if (entry.kind === 'legacy' && url.protocol !== 'https:') return null;

  const isIndex = entry.kind === 'local' && url.pathname === basePath && url.search === '?view=reports';
  if (isIndex) return <a className={className} aria-label={name} href={url.href} onClick={navigate}>{children ?? entry.label}</a>;

  return <a className={className} aria-label={name} href={url.href} target="_blank" rel="noopener noreferrer">
    {children ?? <>{entry.label}<span className="entry-note">{entry.kind === 'legacy' ? '原站 · 新标签页' : '新标签页'}</span></>}
  </a>;
}

function ModuleCard({item, navigate, group}: {item: CatalogItem; navigate: Navigate; group?: string}) {
  const strategy = group === undefined;
  const contents = <>
    <div className="card-preview" aria-hidden="true">
      <span>{group ?? item.groups[0] ?? '策略研究'}</span>
      <svg width="94" height="58" viewBox="0 0 94 58" fill="none" stroke="currentColor" strokeWidth="1.5">
        {strategy ? <>
          <rect x="12" y="14" width="31" height="29" rx="4"/><rect x="48" y="8" width="32" height="42" rx="4"/>
          <path d="M19 23h17m-17 6h13m-13 6h9m37-16v17m-9-12h18m-16 1-3 8h6zm14 0-3 8h6z"/>
        </> : <>
          <rect x="9" y="8" width="73" height="42" rx="5"/><path d="M9 18h73M34 18v32M16 27h11m-11 7h11m-11 7h7"/>
          <rect x="41" y="24" width="15" height="18" rx="2"/><rect x="61" y="24" width="14" height="7" rx="2"/><rect x="61" y="35" width="14" height="7" rx="2"/>
        </>}
      </svg>
    </div>
    <div className="card-details">
      <div className="card-heading">
        <h3>{item.name}</h3>
        <span className={`status-label status-${item.status}`}>{statusLabels[item.status]}</span>
      </div>
      {strategy && item.groups.length > 0 && <ul className="tags" aria-label="分类">
        {getGroups(item).map(group => <li key={group}>{group}</li>)}
      </ul>}
      <p>{item.description}</p>
    </div>
  </>;
  return <li className="module-card">
    {item.status === 'available' && item.entry
      ? <EntryLink entry={item.entry} navigate={navigate} className="module-card-link" name={item.name}>{contents}</EntryLink>
      : contents}
  </li>;
}

function ResearchGroups({groups, navigate}: {groups: Map<string, CatalogItem[]>; navigate: Navigate}) {
  return <div className="research-groups">
    {Array.from(groups, ([name, modules]) => modules.length > 0 && <section key={name} className="catalog-group">
      <h2>{name}</h2>
      <ul className="module-grid">
        {modules.map(item => <ModuleCard key={item.id} item={item} navigate={navigate} group={name}/>)}
      </ul>
    </section>)}
  </div>;
}

function getGroups(item: CatalogItem) {
  return item.groups.length ? [...new Set(item.groups)] : ['其他'];
}

function FilteredCatalog({items, research, navigate, options, onChange, reset}: {
  items: CatalogItem[];
  research: boolean;
  navigate: Navigate;
  options: DirectoryOptions;
  onChange: (options: DirectoryOptions) => void;
  reset: () => void;
}) {
  const query = options.query.trim().toLocaleLowerCase();
  const matches = items.filter(item => [item.name, item.description, ...getGroups(item)]
    .some(value => value.toLocaleLowerCase().includes(query)));
  const groups = new Map<string, CatalogItem[]>();
  for (const item of items) for (const group of getGroups(item)) if (!groups.has(group)) groups.set(group, []);
  for (const item of matches) for (const group of getGroups(item)) groups.get(group)!.push(item);
  const selected = options.group !== null && groups.has(options.group) ? options.group : null;
  const results = selected === null ? matches : groups.get(selected)!;

  function filter(group: string | null, count: number) {
    return <button type="button" key={group === null ? 'all' : `group:${group}`} aria-pressed={selected === group}
      onClick={() => onChange({...options, group})}>{group ?? '全部'} <span>{count}</span></button>;
  }

  return <>
    <div className="category-filter" role="group" aria-label="目录分类">
      {filter(null, matches.length)}
      {Array.from(groups, ([name, group]) => filter(name, group.length))}
    </div>
    {results.length === 0 ? <div className="directory-message">
      <p role="status">没有找到符合条件的{research ? '投研模块' : '策略'}。</p>
      <button type="button" onClick={reset}>清除筛选</button>
    </div> : research ? <ResearchGroups groups={selected === null ? groups : new Map([[selected, results]])} navigate={navigate}/>
      : <section className="catalog-group">
        <h2>策略目录</h2>
        <ul className={options.layout === 'cards' ? 'module-grid' : 'module-list'}>
          {results.map(item => <ModuleCard key={item.id} item={item} navigate={navigate}/>)}
        </ul>
      </section>}
  </>;
}

export function CatalogView({kind, navigate, options, onChange}: {
  kind: 'research' | 'strategies';
  navigate: Navigate;
  options: DirectoryOptions;
  onChange: (options: DirectoryOptions) => void;
}) {
  const research = kind === 'research';
  const search = useRef<HTMLInputElement>(null);

  function clearSearch(resetGroup = false) {
    onChange({...options, query: '', group: resetGroup ? null : options.group});
    search.current?.focus();
  }

  return <>
    <div className="page-heading directory-heading">
      <div>
        <h1>{research ? '投研台' : '策略'}</h1>
        <p>{research ? '按研究方向查找看板、分析工具和深度报告。' : '查看已有策略、研究说明与历史回测结果。'}</p>
      </div>
      <div className="directory-tools">
        <div className="search-field">
          <svg aria-hidden="true" width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"><circle cx="10" cy="10" r="6"/><path d="m14.5 14.5 5 5"/></svg>
          <input type="search" ref={search} value={options.query} aria-label={research ? '搜索投研模块' : '搜索策略'}
            placeholder={research ? '搜索投研模块' : '搜索策略'} onChange={event => onChange({...options, query: event.target.value})}/>
          {options.query && <button type="button" onClick={() => clearSearch()}>清除</button>}
        </div>
        {!research && <div className="layout-switch" role="group" aria-label="策略显示方式">
          <button type="button" aria-pressed={options.layout === 'cards'} onClick={() => onChange({...options, layout: 'cards'})}>卡片</button>
          <button type="button" aria-pressed={options.layout === 'list'} onClick={() => onChange({...options, layout: 'list'})}>列表</button>
        </div>}
      </div>
    </div>
    <Directory<CatalogItem> path={research ? '/research/modules' : '/strategies'}
      empty={research ? '暂无投研模块。' : '暂无策略。'}>
      {items => <FilteredCatalog items={items} research={research} navigate={navigate}
        options={options} onChange={onChange} reset={() => clearSearch(true)}/>}
    </Directory>
  </>;
}

function ReportList({items}: {items: ReportItem[]}) {
  const [query, setQuery] = useState('');
  const value = query.trim().toLocaleLowerCase();
  const reports = items.filter(item => [item.title, item.fund.name, item.fund.code, item.source ?? '']
    .some(text => text.toLocaleLowerCase().includes(value)));
  return <>
    <div className="report-library-tools">
      <input type="search" aria-label="搜索基金报告" placeholder="搜索基金、代码或报告名称"
        value={query} onChange={event => setQuery(event.target.value)}/>
      <span role="status">{value ? `${reports.length} / ${items.length}` : items.length} 份报告</span>
    </div>
    {reports.length === 0 && <p className="directory-message">没有找到符合条件的报告。</p>}
    <ul className="report-list" aria-label="基金深度报告列表">
      {reports.map(item => <li className="report-row" key={item.id}>
        <div className="report-info">
          <p className="report-fund">{item.fund.name} <span>{item.fund.code}</span></p>
          <h2>{item.title}</h2>
          <p className="report-date">{item.date && item.date_kind ? <>
            {item.date_kind === 'published' ? '发布日期' : '资料截止日'} <time dateTime={item.date}>{item.date}</time>
          </> : '日期未确认'}{item.source && <> · {item.source}</>}{item.status === 'draft' && <> · 草稿</>}</p>
        </div>
        <EntryLink entry={item.entry}/>
      </li>)}
    </ul>
  </>;
}

export function ReportsView() {
  return <>
    <div className="page-heading">
      <h1>基金深度报告</h1>
      <p>浏览已完成的基金研究，打开报告原版。</p>
    </div>
    <Directory<ReportItem> path="/research/reports" empty="暂无可打开的基金深度报告。">
      {items => <ReportList items={items}/>}
    </Directory>
  </>;
}
