const TABS = [
  ['overview', '抱团总览'], ['stocks', '个股拥挤度'], ['detail', '个股透视'],
  ['funds', '基金与资金'], ['industries', '行业与仓位'], ['index', '抱团指数'],
];
const START_LABELS = { hp: '持仓期以来', qe: '季末以来', d5: '近 5 日', d20: '近 20 日' };
const FUND_TYPES={active_equity:'主动权益',active_index_enhanced:'指数增强',passive_index:'被动指数',etf:'ETF',non_equity:'非权益',fof:'FOF',qdii:'QDII',linked_fund:'联接',unknown:'未知'};
const START_KEYS = Object.fromEntries(Object.entries(START_LABELS).map(([k, v]) => [v, k]));
const VIEW_LABELS = {
  pool: '抱团池', high: '高风险 ≥ 85', held: '全部公募持仓', none: '无公募持仓',
  crowded_expensive: '拥挤且贵', top10_up: '主动前十大增持', top10_down: '主动前十大减持',
};
const params = new URLSearchParams(location.search);
const state = {
  tab: TABS.some(([key]) => key === params.get('tab')) ? params.get('tab') : 'overview',
  start: START_LABELS[params.get('start')] ? params.get('start') : 'hp',
  view: VIEW_LABELS[params.get('view')] ? params.get('view') : 'pool',
  q: params.get('q') || '', l1: params.get('l1') || '',
  sort: params.get('sort') || 'score', order: params.get('order') || 'desc', page: Math.max(1, Number(params.get('page')) || 1),
  pageSize: [20,50,100].includes(Number(params.get('page_size'))) ? Number(params.get('page_size')) : 100,
  window: ['5','20','60'].includes(params.get('window')) ? params.get('window') : '20',
  indexMode: ['top3','hhi','dev'].includes(params.get('indexMode')) ? params.get('indexMode') : 'top3',
  weight: params.get('weight')==='cw'?'cw':'equal',
  industryMode: params.get('industryMode')==='overweight'?'overweight':'share',
  focusMode: params.get('focusMode')==='full'?'full':'top10',
  dataVersion: params.get('data_version') || '', fundScaleOrder: '',
  selected: params.get('code') || '', detailQuery: '', data: null, error: null,
};
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const finite = value => value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value));
const n = value => finite(value) ? Number(value).toLocaleString('zh-CN', {maximumFractionDigits: 2}) : '—';
const integer = value => finite(value) ? Math.round(Number(value)).toLocaleString('zh-CN') : '—';
const money = value => finite(value) ? (Math.abs(Number(value))>=1e12
  ? `${(Number(value)/1e12).toLocaleString('zh-CN',{maximumFractionDigits:2})} 万亿`
  : `${(Number(value)/1e8).toLocaleString('zh-CN',{maximumFractionDigits:2})} 亿`) : '—';
const pctDecimal = value => finite(value) ? `${(Number(value)*100).toFixed(1)}%` : '—';
const pctPoint = value => finite(value) ? `${Number(value).toFixed(1)}%` : '—';
const signed = value => finite(value) ? `${Number(value) > 0 ? '+' : ''}${Number(value).toFixed(1)}` : '—';
const trendClass = value => finite(value) ? (Number(value) >= 0 ? 'up' : 'down') : '';
const productLabel = row => row.merge_status==='verified_product'?'已验证产品':row.merge_status==='approximate_product'?'近似归并产品':'未归并份额';
function disclosureQuarter(period) {
  const key=typeof period==='object'?period?.key||period?.period:period;
  const quarter=String(key||'').match(/^(?:\d{2}|\d{4})Q([1-4])$/);
  if(quarter)return Number(quarter[1]);
  const month=String(key||'').match(/^\d{4}-(03|06|09|12)-\d{2}$/);
  return month?Number(month[1])/3:null;
}
function disclosurePeriodIndex(period) {
  const key=typeof period==='object'?period?.key||period?.period:period;
  const match=String(key||'').match(/^(\d{2}|\d{4})(?:Q([1-4])|-(03|06|09|12)-\d{2})$/);
  return match?(match[1].length===2?2000+Number(match[1]):Number(match[1]))*4+(match[2]?Number(match[2]):Number(match[3])/3):null;
}
let pageCache = new Map();
let hiddenSeries = new Map();
const chartModels = new Map();
let loadSequence=0, loadController=null, lookupSequence=0, lookupController=null;

function urlFor(page) {
  const query = new URLSearchParams({start: state.start});
  if(state.dataVersion)query.set('data_version',state.dataVersion);
  if (page === 'index') return `./api/dashboards/fund_crowding/crowd_index?${query}`;
  if (page === 'stocks') {
    Object.entries({view:state.view, q:state.q, l1:state.l1, sort:state.sort, order:state.order,
      page:String(state.page), page_size:String(state.pageSize)})
      .forEach(([k,v]) => { if (v) query.set(k,v); });
    return `./api/dashboards/fund_crowding/stocks?${query}`;
  }
  if (page === 'detail' && state.selected) return `./api/dashboards/fund_crowding/stocks/${encodeURIComponent(state.selected)}?${query}`;
  return `./api/dashboards/fund_crowding/${page}?${query}`;
}

async function fetchPage(page, {force=false,signal}={}) {
  if (page === 'detail' && !state.selected) return null;
  const key = urlFor(page);
  if (!force && pageCache.has(key)) return pageCache.get(key);
  const response = await fetch(key, {headers:{Accept:'application/json'},signal});
  const body = await response.json();
  if (!response.ok || body.code !== 0) {
    const error=new Error(body.message || `HTTP ${response.status}`);
    error.status=response.status;error.code=body.code;throw error;
  }
  if(signal?.aborted)throw new DOMException('请求已取消','AbortError');
  const version=body.data?.data_version||body.data_version;
  if(state.dataVersion&&version&&String(version)!==state.dataVersion)throw new Error('返回数据版本与当前版本不一致，请刷新后重试');
  if(!state.dataVersion&&version)state.dataVersion=String(version);
  pageCache.set(urlFor(page), body.data);
  return body.data;
}

function loadingState() { return `<div class="module-empty loading-state">正在加载：本地预计算数据</div>`; }
function emptyState(reason='暂无数据') { return `<div class="module-empty">${esc(reason)}</div>`; }
function retryControls(versionConflict=false) {
  return `<div class="retry"><button data-retry="1">重试当前筛选</button>${versionConflict?'<button data-refresh-version="1">重新打开当前冻结版本</button>':''}</div>`;
}
function panel(title, hint, body, right='', bodyClass='') {
  return `<section class="module"><div class="module-head"><h2>${esc(title)}</h2><span class="hint">${esc(hint)}</span><span class="right">${right}</span></div><div class="module-body ${bodyClass}">${body}</div></section>`;
}

function table(columns, rows, opts={}) {
  if (!rows?.length) return emptyState(opts.empty || '没有符合条件的数据');
  columns=columns.map(c=>c.key==='index'?{...c,width:Math.max(36,c.width||0)}:c);
  const total = columns.reduce((sum, c) => sum + (c.width || 80), 0);
  const pinned=ci=>ci===0||(ci===1&&columns[0].key==='index');
  const head = columns.map((c,ci) => {
    const key=c.sortKey||c.key, active=c.sortable&&(opts.sort??state.sort)===key;
    const mark=active?((opts.order??state.order)==='asc'?' ↑':' ↓'):'';
    const content=c.sortable?`<button class="sort-head" data-sort="${esc(key)}">${esc(c.label)}${mark}</button>`:esc(c.label);
    return `<th class="${c.numeric?'num':'text'} ${pinned(ci)?'sticky-col':''}" style="width:${c.width||80}px;${pinned(ci)?`left:${ci?(columns[0].width||80):0}px`: ''}">${content}</th>`;
  }).join('');
  const body = rows.map((row, index) => `<tr>${columns.map((c,ci) => {
    let value = typeof c.render === 'function' ? c.render(row,index) : row[c.key];
    if (value === null || value === undefined || value === '') value = '—';
    const tooltip=esc(String(value).replace(/<[^>]*>/g,''));
    if(typeof c.render!=='function')value=esc(String(value));
    return `<td title="${tooltip}" class="${c.numeric?'num':'text'} ${pinned(ci)?'sticky-col':''}" ${pinned(ci)?`style="left:${ci?(columns[0].width||80):0}px"`:''}>${value}</td>`;
  }).join('')}</tr>`).join('');
  return `<div class="table-scroll"><table class="crowd-table" style="width:${Math.max(total, 100)}px"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function lineChart(labels, series, options={}) {
  const w=720,h=options.height||255,l=54,r=options.rightFormat?650:672,t=26,b=Math.max(170,h-38);
  const id=options.id, hidden=hiddenSeries.get(id)||new Set(), visible=series.filter(s=>!hidden.has(s.name));
  const left=visible.filter(s=>s.axis!=='right'), right=visible.filter(s=>s.axis==='right');
  const values=left.flatMap(s=>s.values||[]).filter(finite).map(Number), rightValues=right.flatMap(s=>s.values||[]).filter(finite).map(Number);
  const legend=`<div class="chart-legend">${series.map(s=>`<button data-series-toggle="${esc(id)}|${esc(s.name)}" aria-pressed="${!hidden.has(s.name)}" style="color:${s.color};opacity:${hidden.has(s.name)?.4:1};font-weight:${s.emphasis?700:400}"><i style="background:${s.color}"></i>${esc(s.name)}${s.axis==='right'?'（右轴）':''}</button>`).join('')}</div>`;
  if(!values.length&&!rightValues.length)return legend+emptyState(visible.length?options.empty||'没有可用数据':'已隐藏全部曲线，点击图例恢复');
  const extent=(items,zero=false)=>{let low=items.length?Math.min(...items):0,high=items.length?Math.max(...items):1;if(zero){low=Math.min(0,low);high=Math.max(0,high);}if(low===high){high+=1;low-=1;}return [low,high,high-low||1];};
  const [leftMin,leftMax,leftSpan]=extent(values,options.zero), [rightMin,rightMax,rightSpan]=extent(rightValues,options.zero);
  const x=i=>l+(labels.length<=1?0:i*(r-l)/(labels.length-1));
  const y=(s,v)=>s.axis==='right'?b-(Number(v)-rightMin)/rightSpan*(b-t):b-(Number(v)-leftMin)/leftSpan*(b-t);
  const fmt=options.format||pctDecimal, rightFmt=options.rightFormat||fmt;
  const paths=visible.map(s=>{let d='',on=false,previousPeriod=null;(s.values||[]).forEach((v,i)=>{if(!finite(v)){const quarter=disclosureQuarter(options.periods?.[i]);if(!s.fullDisclosure||![1,3].includes(quarter))on=false;return;}const period=disclosurePeriodIndex(options.periods?.[i]);if(s.fullDisclosure&&on&&(period===null||previousPeriod===null||period-previousPeriod!==2))on=false;d+=`${on?'L':'M'}${x(i).toFixed(1)} ${y(s,v).toFixed(1)} `;on=true;previousPeriod=period;});return `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="${s.emphasis?'3':s.dash?'1.8':'2'}" stroke-dasharray="${s.dash||''}"/>`;}).join('');
  const dots=visible.flatMap(s=>(s.values||[]).map((v,i)=>finite(v)&&(options.disclosing?.[i]||s.fullDisclosure)?`<circle cx="${x(i)}" cy="${y(s,v)}" r="${options.disclosing?.[i]?4:2.5}" fill="${options.disclosing?.[i]?'var(--panel)':s.color}" stroke="${s.color}" stroke-width="${options.disclosing?.[i]?2:1}"/>`:'')).join('');
  const tickStep=Math.max(1,Math.ceil(labels.length/8));
  const ticks=labels.map((label,i)=>labels.length<=8||i===0||i===labels.length-1||(i%tickStep===0&&labels.length-1-i>3)?`<text x="${x(i)}" y="${b+19}" text-anchor="${i===0?'start':i===labels.length-1?'end':'middle'}" fill="#606269">${esc(label.length>7?label.slice(5):label)}</text>`:'').join('');
  const grid=[0,.25,.5,.75,1].map(ratio=>{const v=leftMin+leftSpan*ratio,sy=b-ratio*(b-t);return `<line x1="${l}" y1="${sy}" x2="${r}" y2="${sy}" stroke="#e3e5ea"/><text x="2" y="${sy+4}" fill="#606269">${values.length?esc(fmt(v)):'—'}</text>${right.length?`<text x="${w-3}" y="${sy+4}" text-anchor="end" fill="#606269">${esc(rightFmt(rightMin+rightSpan*ratio))}</text>`:''}`;}).join('');
  const zeroLine=leftMin<=0&&leftMax>=0?`<line x1="${l}" y1="${y({},0)}" x2="${r}" y2="${y({},0)}" stroke="#626570" stroke-dasharray="3 3"/>`:'';
  const ref=Number.isInteger(options.refIndex)&&options.refIndex>=0?`<line x1="${x(options.refIndex)}" y1="${t}" x2="${x(options.refIndex)}" y2="${b}" stroke="#9b8c66" stroke-dasharray="4 4"/><text x="${x(options.refIndex)+4}" y="${t+10}" fill="#b9a77d">21Q1</text>`:'';
  chartModels.set(id,{labels,series:visible,format:fmt,rightFormat:rightFmt,l,r,t,b,w,x});
  return `${legend}<div class="chart-host"><svg data-chart-id="${esc(id)}" viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(options.label||'时间序列图，移动指针查看同日数据')}">${grid}${zeroLine}${ref}${paths}${dots}${ticks}<line class="chart-hover-line" x1="${l}" x2="${l}" y1="${t}" y2="${b}" stroke="#606269" stroke-dasharray="3 3" visibility="hidden"/></svg><div class="chart-tooltip" hidden></div></div>`;
}

function scatterChart(rows, opts={}) {
  rows=(rows||[]).filter(row=>finite(row.fr_pct)&&finite(row.ret_pct));
  if(!rows.length)return emptyState('没有同时具备持仓和复权收益的股票');
  const w=720,h=285,l=44,r=708,t=18,b=244;
  const xs=rows.map(x=>Number(x.fr_pct||0)),ys=rows.map(x=>Number(x.ret_pct||0)).filter(Number.isFinite);
  const xmax=Math.max(1,...xs), ymin=Math.min(0,...ys), ymax=Math.max(.01,...ys), span=ymax-ymin||1, maxmv=Math.max(1,...rows.map(x=>Number(x.hold_mv_now)||0));
  const dots=rows.map(x=>{const sx=l+(Number(x.fr_pct||0)/xmax)*(r-l), sy=b-((Number(x.ret_pct||0)-ymin)/span)*(b-t), radius=Math.max(2,Math.min(9,Math.sqrt((Number(x.hold_mv_now)||0)/maxmv)*9));const color=x.score==null?'#838891':x.score>=85?'#ef6e82':x.score>=70?'#dca65b':'#294e79';return `<circle cx="${sx}" cy="${sy}" r="${radius}" fill="${color}" fill-opacity=".55" data-stock="${esc(x.code)}" style="cursor:pointer"><title>${esc(x.name||x.code)} ${pctPoint(x.fr_pct)} ${pctPoint(x.ret_pct)}</title></circle>`;}).join('');
  return `<div class="chart-host"><svg viewBox="0 0 ${w} ${h}" role="img"><line x1="${l}" y1="${b}" x2="${r}" y2="${b}" stroke="#aeb4bf"/><line x1="${l}" y1="${t}" x2="${l}" y2="${b}" stroke="#aeb4bf"/>${ymin<=0&&ymax>=0?`<line x1="${l}" y1="${b-(0-ymin)/span*(b-t)}" x2="${r}" y2="${b-(0-ymin)/span*(b-t)}" stroke="#686a71" stroke-dasharray="3 3"/>`:''}${dots}<text x="${l}" y="${b+18}" fill="#606269">0%</text><text x="${r}" y="${b+18}" text-anchor="end" fill="#606269">${xmax.toFixed(1)}%</text><text x="2" y="${t+4}" fill="#606269">${pctPoint(ymax)}</text><text x="2" y="${b}" fill="#606269">${pctPoint(ymin)}</text></svg></div>`;
}

function histogram(bins) {
  const valid=(bins||[]).filter(x=>finite(x.n)&&finite(x.lo)&&finite(x.hi));
  if(!valid.length)return emptyState('没有可用净值分布');
  const w=720,h=300,l=46,b=245,t=22,r=708,max=Math.max(1,...valid.map(x=>Number(x.n)));
  const grid=[0,.25,.5,.75,1].map(ratio=>`<line x1="${l}" y1="${b-ratio*(b-t)}" x2="${r}" y2="${b-ratio*(b-t)}" stroke="#e3e5ea"/><text x="2" y="${b-ratio*(b-t)+4}" fill="#606269">${integer(max*ratio)}</text>`).join('');
  return `<div class="chart-host"><svg viewBox="0 0 ${w} ${h}" role="img" aria-label="基金区间收益分布，纵轴为基金数量"><text x="4" y="12" fill="#606269">只数</text>${grid}${valid.map((x,i)=>{const bw=(r-l)/valid.length-5,left=l+i*(r-l)/valid.length,height=(Number(x.n)/max)*(b-t);return `<rect x="${left}" y="${b-height}" width="${bw}" height="${height}" fill="${(Number(x.lo)+Number(x.hi))/2>=0?'#b93737':'#21704c'}"><title>${pctPoint(x.lo)} 至 ${pctPoint(x.hi)}：${integer(x.n)}只</title></rect><text x="${left+bw/2}" y="260" text-anchor="middle" fill="#606269">${pctPoint((Number(x.lo)+Number(x.hi))/2)}</text>`;}).join('')}</svg></div>`;
}

function chart(id,title,desc,body,right='') { return panel(title,desc,body,right,'no-scroll'); }

function kpiCard(item) {
  const value = item.rendered !== undefined ? item.rendered : item.key === 'hold_mv' ? money(item.value) : item.key === 'high_risk' || item.key === 'top10' ? integer(item.value) : item.key === 'top1' ? item.value : item.key === 'pool_ret' || item.key === 'crowded_ret' || item.key === 'top3' || item.key === 'dev' || item.key === 'jac_all' || item.key === 'jac_head' || item.key === 'w10_med' || item.key === 'equity_pos' || item.key === 'top_industry' ? (finite(item.value)?pctPoint(item.value):item.value) : integer(item.value);
  return `<div class="kpi"><span class="label">${esc(item.label)}</span><strong class="value ${esc(item.tone==='up'?'up':item.tone==='down'?'down':'')}">${esc(value===null||value===undefined||value===''?'—':value)}</strong><span class="sub">${esc(item.sub||'')}</span></div>`;
}

const DIMENSIONS=[['crowd','拥挤','#b93737'],['liq','流动性','#dca65b'],['chg','加仓','#dfc064'],['px','价格','#294e79'],['flow','资金','#5cb98b']];
const DIM_WEIGHTS={crowd:30,liq:20,chg:15,px:20,flow:15};
function riskPips(dims) {
  if(!dims)return '—';
  const total=DIMENSIONS.reduce((sum,[key])=>sum+(finite(dims[key])?DIM_WEIGHTS[key]:0),0);
  return `<span class="dim-mini" aria-label="五维加权贡献">${DIMENSIONS.map(([key,label,color])=>{const value=dims[key],weight=total&&finite(value)?DIM_WEIGHTS[key]/total*100:null,contribution=weight===null?null:Number(value)*weight/100;return `<i title="${label}：${finite(value)?`第 ${n(value)} 分位 · 原权重 ${DIM_WEIGHTS[key]}% · 有效权重 ${n(weight)}% · 贡献 ${n(contribution)} 分`:'缺失，不参与本次评分'}" style="--dim:${color};width:${finite(contribution)?Math.max(.5,contribution*.68):2}px;opacity:${finite(value)?1:.2}"></i>`;}).join('')}</span>`;
}

function stockLookup(value='') {
  return `<div class="stock-lookup"><input id="stockLookup" autocomplete="off" aria-label="搜索股票代码或名称" placeholder="输入代码或股票名" value="${esc(value)}"><div id="stockSuggestions" class="stock-suggestions" hidden></div></div>`;
}

function methodHtml() {
  const d=state.data||{}, marks=d.marks||{}, coverage=d.coverage||{};
  const coverageLabels={stocks:'股票',rows:'记录',holders:'持有产品',companies:'基金公司',price_points:'价格点',funds:'基金',etfs:'ETF',periods:'历史期数',industries:'行业',top_stocks:'集中股票',n_products:'产品',complete_products:'完整产品',eligible:'应覆盖',valid:'有效样本',observed:'实际观察'};
  const coverageText=Object.entries(coverage).filter(([key,value])=>coverageLabels[key]&&finite(value)).slice(0,8).map(([key,value])=>`${coverageLabels[key]} ${integer(value)}`).join('；');
  return `<div class="method-grid">
    <p><b>当前页面数据</b>：行情日 ${esc(marks.market_date||d.trade_date||'—')}；持仓期 ${esc(marks.holding_period||'—')}；缓存版本 ${esc(d.data_version||state.dataVersion||'—')}。</p>
    ${coverageText?`<p><b>覆盖摘要</b>：${esc(coverageText)}</p>`:''}
    <p><b>抱团池</b>：披露持仓市值前 200 名与占流通比前 150 名的并集，并要求披露持仓市值不低于 10 亿元。不是简单的“基金家数排名”。</p>
    <p><b>风险分</b>：拥挤 30%、流动性 20%、加仓 15%、价格 20%、资金 15%，各维先在同类样本内转百分位，再按可用维度重新归一。缺失维度不会被当作 0。</p>
    <p><b>拥挤</b>：占流通比与持仓规模共同刻画；<b>流动性</b>：持仓现值 / 20 日平均成交额，即估算清仓天数；<b>加仓</b>：占流通比较上期的变化。</p>
    <p><b>价格</b>：持仓期以来收益与 52 周高点回撤（不随顶部收益起点切换）；<b>资金</b>：大单及特大单净额和融资余额变化。所有指标均为本地独立实现，缺失值显示“—”。</p>
    <p><b>区间收益</b>：使用复权收盘价按设定起点计算；<b>回撤</b>：相对最近 52 周复权高点；<b>换手 5 日</b>：最近 5 日成交额合计 / 行情日流通市值。</p>
    <p><b>PE/PB 分位</b>：最近三年有效估值序列中的百分位；<b>前十大变化</b>：同报告期、同基金样本的两期前十大比较，覆盖率不足时保留诊断，并排除出增减持榜单。</p>
    <p><b>数据边界</b>：持仓来自半年报/年报完整披露；季报仅提供前十大，不把季报前十大冒充全量持仓。行业分类为当前 SW2021 回看；基金产品关系仅使用能验证的份额组合。</p>
  </div>`;
}

function pageChrome() {
  $('tabs').innerHTML=TABS.map(([key,label])=>`<button data-tab="${key}" class="${state.tab===key?'active':''}">${label}</button>`).join('');
  $('period').innerHTML=Object.entries(START_LABELS).map(([key,label])=>`<button data-start="${key}" class="${state.start===key?'active':''}">${label}</button>`).join('');
  $('method').onclick=()=>{$('methodContent').innerHTML=methodHtml();$('methodDialog').showModal();};
}

function loading(extra='') { $('content').innerHTML=`<div class="loading">${extra||'正在读取本地数据…'}</div>`; }

function renderOverview(data) {
  const q=data.quintiles||{}, colors=['#294e79','#67c8b2','#d6b66f','#ef9b70','#dc7c91'];
  const groupNames=['最分散','第 2 组','第 3 组','第 4 组','最拥挤'];
  const groups=(q.groups||[]).map((g,i)=>({name:groupNames[Number(g.q)-1]||`第 ${g.q} 组`,values:g[state.weight==='equal'?'ew':'cw']||[],color:colors[i]}));
  groups.push({name:'未见公募持仓',values:q.none_group?.[state.weight==='equal'?'ew':'cw']||[],color:'#a9b08e',dash:'6 4'});
  groups.push({name:'中证2000',values:q.index?.returns||[],color:'#6b7485',dash:'2 4'});
  const warnRows=(data.warn?.rows||[]).map((r,i)=>({...r,index:i+1}));
  const kpi=Object.fromEntries((data.kpi.items||[]).map(item=>[item.key,item]));
  const weightKey=state.weight==='equal'?'ew':'cw', q1=(q.groups||[]).find(x=>Number(x.q)===1)?.[weightKey]?.at(-1), q5=(q.groups||[]).find(x=>Number(x.q)===5)?.[weightKey]?.at(-1);
  const poolValues=q.pool?.[weightKey]||q.pool_group?.[weightKey]||data.pool_returns?.[weightKey];
  const poolReturn=Array.isArray(poolValues)?poolValues.at(-1):state.weight==='equal'?q.pool_return_ew??q.pool_return_equal:q.pool_return_cw??q.pool_return;
  const overviewItems=(data.kpi?.items||[]).map(item=>{if(!['pool_ret','crowded_ret'].includes(item.key))return item;const value=item.key==='pool_ret'?poolReturn:q5;return {...item,value:finite(value)?Number(value)*100:null,tone:finite(value)?Number(value)>=0?'up':'down':'neutral',sub:(state.weight==='equal'?'等权':'持仓现值加权')+' · '+START_LABELS[state.start]};});
  const poolKpi=overviewItems.find(item=>item.key==='pool_ret');
    const warn=table([
    {key:'index',label:'#',width:32},{key:'name',label:'个股',width:82,render:r=>`<button class="link" data-stock="${esc(r.code)}">${esc(r.name)}</button>`},
    {key:'l1_name',label:'行业',width:66},{key:'score',label:'风险分',width:54,numeric:true,render:r=>n(r.score)},
    {key:'dims',label:'五维构成',width:100,render:r=>riskPips(r.dims)},
    {key:'fr_pct',label:'占流通比',width:65,numeric:true,render:r=>pctPoint(r.fr_pct)},{key:'liq_days',label:'清仓天数',width:62,numeric:true,render:r=>n(r.liq_days)},
    {key:'ret_pct',label:'区间收益',width:70,numeric:true,render:r=>`<span class="${trendClass(r.ret_pct)}">${pctPoint(r.ret_pct)}</span>`},
    {key:'dd_pct',label:'回撤',width:55,numeric:true,render:r=>pctPoint(r.dd_pct)},{key:'mf30_pct',label:'大单30日',width:70,numeric:true,render:r=>pctPoint(r.mf30_pct)},
  ],warnRows,{empty:'抱团池暂无可评分股票'});
  $('content').innerHTML=`<div class="crowding-page page--overview">
    <div class="kpi-band">${overviewItems.map(kpiCard).join('')}</div>
    ${chart('quintile','拥挤度五分位 · 区间累计收益','按占流通比分组；虚线为未见持仓与中证2000',lineChart(q.days||[],groups,{id:'quintile',zero:true,empty:'没有可用收益'}),`<div class="seg"><button data-weight="cw" class="${state.weight!=='equal'?'active':''}">市值加权</button><button data-weight="equal" class="${state.weight==='equal'?'active':''}">等权</button></div>`)}
    ${chart('map','抱团地图','X 占流通比 · Y 区间收益 · 点大小持仓市值',`<div class="risk-legend"><span><i style="background:#ef6e82"></i>高风险 ≥85</span><span><i style="background:#dca65b"></i>关注 70–85</span><span><i style="background:#294e79"></i>常规 &lt;70</span><span><i style="background:#838891"></i>未评分</span><button class="link" data-tab="stocks" data-view="high">查看高风险 ›</button></div>`+scatterChart(data.map?.rows||[]))}
    ${panel('负反馈预警','抱团池风险分前 15',warn,`<span class="dimension-legend">${DIMENSIONS.map(([key,label,color])=>`<span style="color:${color}">${label} ${DIM_WEIGHTS[key]}%</span>`).join('')}</span><button class="link" data-tab="stocks">个股拥挤度 ›</button>`)}
    ${panel('要点','自动摘要',`<ul class="reasons"><li>最拥挤组 ${pctDecimal(q5)}，最分散组 ${pctDecimal(q1)}；差值 ${signed(finite(q5)&&finite(q1)?(Number(q5)-Number(q1))*100:null)} 个百分点。</li><li>抱团池区间收益 ${pctPoint(poolKpi?.value)}；${esc(poolKpi?.sub||START_LABELS[state.start])}。</li><li>高风险个股 ${integer(kpi.high_risk?.value)} 只；风险分为缺维重归一后的本地独立估算。</li><li>第一大行业 ${esc(kpi.top_industry?.sub||'—')}。</li><li><button class="link" data-tab="stocks" data-view="top10_up">主动前十大增持 ›</button> · <button class="link" data-tab="stocks" data-view="top10_down">主动前十大减持 ›</button>（仅展示满足真实覆盖条件的股票）。</li><li>偏股基金仓位中位 ${pctPoint(kpi.equity_pos?.value)}。</li></ul>`)}
  </div>`;
}

function stockColumns() {
  return [
    {key:'index',label:'#',width:34,render:(r,i)=>(state.page-1)*state.pageSize+i+1},
    {key:'name',label:'个股',width:76,sortable:true,render:r=>`<button class="link" data-stock="${esc(r.code)}">${esc(r.name||r.code)}</button>`},
    {key:'code',label:'代码',width:90,sortable:true},{key:'l1_name',label:'行业',width:60,sortable:true},
    {key:'score',label:'风险分',width:52,numeric:true,sortable:true,render:r=>r.score==null?'—':`<span class="${r.band==='high'?'up':''}">${n(r.score)}${r.dims_missing?.length?'*':''}</span>`},
    {key:'dims',label:'五维',width:100,render:r=>riskPips(r.dims)},
    {key:'n_funds',label:'基金数',width:64,numeric:true,sortable:true,render:r=>integer(r.n_funds)},
    {key:'n_funds_chg',label:'Δ基金',width:72,numeric:true,sortable:true,render:r=>signed(r.n_funds_chg)},
    {key:'n_corps',label:'公司数',width:50,numeric:true,sortable:true,render:r=>integer(r.n_corps)},
    {key:'hold_mv_h',label:'持仓(亿)',width:90,numeric:true,sortable:true,render:r=>money(r.hold_mv_h)},
    {key:'hold_mv_now',label:'现值(亿)',width:90,numeric:true,sortable:true,render:r=>money(r.hold_mv_now)},
    {key:'fr_pct',label:'占流通%',width:60,numeric:true,sortable:true,render:r=>pctPoint(r.fr_pct)},
    {key:'fr_chg_pct',label:'Δ占流通',width:60,numeric:true,sortable:true,render:r=>signed(r.fr_chg_pct)},
    {key:'fr_free_pct',label:'占自由%',width:58,numeric:true,sortable:true,render:r=>pctPoint(r.fr_free_pct)},
    {key:'top5_pct',label:'前五大%',width:56,numeric:true,sortable:true,render:r=>pctPoint(r.top5_pct)},
    {key:'passive_pct',label:'被动%',width:52,numeric:true,sortable:true,render:r=>pctPoint(r.passive_pct)},
    {key:'liq_days',label:'清仓天数',width:58,numeric:true,sortable:true,render:r=>n(r.liq_days)},
    {key:'adv_amount',label:'日均额(亿)',width:68,numeric:true,sortable:true,render:r=>money(r.adv_amount)},
    {key:'turnover5_pct',label:'换手5日%',width:64,numeric:true,sortable:true,render:r=>pctPoint(r.turnover5_pct)},
    {key:'ret_pct',label:'区间收益%',width:66,numeric:true,sortable:true,render:r=>`<span class="${trendClass(r.ret_pct)}">${pctPoint(r.ret_pct)}</span>`},
    {key:'dd_pct',label:'回撤%',width:72,numeric:true,sortable:true,render:r=>pctPoint(r.dd_pct)},
    {key:'off_high_pct',label:'距20日高%',width:68,numeric:true,sortable:true,render:r=>pctPoint(r.off_high_pct)},
    {key:'fin_chg30_pct',label:'融资30日%',width:68,numeric:true,sortable:true,render:r=>pctPoint(r.fin_chg30_pct)},
    {key:'mf30_pct',label:'大单30日%',width:68,numeric:true,sortable:true,render:r=>pctPoint(r.mf30_pct)},
    {key:'pe3y',label:'PE分位',width:52,numeric:true,sortable:true,render:r=>n(r.pe3y)},
    {key:'pb3y',label:'PB分位',width:52,numeric:true,sortable:true,render:r=>n(r.pb3y)},
    {key:'top10_chg_pct',label:'前十大Δ%/覆盖',width:100,numeric:true,sortable:true,render:r=>r.top10_chg_pct==null?'—':`${signed(r.top10_chg_pct)} / ${pctPoint(r.top10_cover_pct)}`},
    {key:'top10_chg_mv',label:'前十大Δ(亿)',width:72,numeric:true,sortable:true,render:r=>money(r.top10_chg_mv)},
    {key:'arrow',label:'',width:24,render:r=>`<button class="link" data-stock="${esc(r.code)}">›</button>`},
  ];
}

function renderStocks(data) {
  const views=data.views||{};
  const industries=(data.filters?.industries||[]).map(name=>`<option value="${esc(name)}" ${state.l1===name?'selected':''}>${esc(name)}</option>`).join('');
  const toolbar=`<div class="toolbar"><div class="seg">${Object.entries(VIEW_LABELS).map(([key,label])=>`<button class="chip ${state.view===key?'active':''}" data-view="${key}">${label}${views[key]!=null?` ${views[key]}`:''}</button>`).join('')}</div><select id="industryFilter"><option value="">全部行业</option>${industries}</select><input id="stockSearch" placeholder="搜代码或名称" value="${esc(state.q)}"><label class="muted">每页 <select id="pageSize">${[20,50,100].map(size=>`<option value="${size}" ${state.pageSize===size?'selected':''}>${size}</option>`).join('')}</select></label><span class="muted">${integer(data.table?.n)} 只</span></div>`;
  const body=data.error?emptyState(data.error)+retryControls(data.versionConflict):table(stockColumns(),data.table?.rows||[],{empty:data.note||'没有符合条件的股票'});
  const page=data.table?.page||1, pages=data.table?.pages||Math.max(1,Math.ceil((data.table?.n||0)/(data.table?.page_size||state.pageSize)));
  const hint=state.view==='none'?'最近披露样本未观察；不等于当前零持仓':'全部已披露基金 · 已验证份额合并；* 为缺维风险分';
  $('content').innerHTML=`<div class="crowding-page page--stocks">${toolbar}<section class="module stock-module"><div class="module-head"><h2>个股拥挤度</h2><span class="hint">${hint}</span><span class="right">${esc(data.trade_date||'—')}</span></div><div class="module-body">${body}</div><div class="pager"><button data-page="-1" ${page<=1?'disabled':''}>上一页</button><span>第 ${page} / ${pages} 页</span><button data-page="1" ${page>=pages?'disabled':''}>下一页</button></div></section></div>`;
}

function renderDetail(data) {
  if(!data){$('content').innerHTML=`<div class="detail-empty"><div class="detail-search"><span>个股透视</span>${stockLookup()}</div>${emptyState('输入股票代码或名称开始查询，也可以从个股拥挤度列表进入')}</div>`;return;}
  const k=data.kpi||{}, holders=data.holders||{},
    holderRows=(holders.top||[]).slice(0,30).map(r=>({...r,name:r.fund_name,code:r.fund_symbol,manager:r.management,
      value:r.disclosed_market_value,public_pct:r.share_of_public_pct,nav_pct:r.share_of_fund_nav_pct,
      kind_label:`${{active_equity:'主动',active_index_enhanced:'指数增强',passive_index:'被动',etf:'ETF',non_equity:'非权益',fof:'FOF',qdii:'QDII',linked_fund:'联接',unknown:'未知'}[r.kind]||r.kind||'未知'} · ${productLabel(r)}`})),
    holderTable=table([{key:'name',label:'基金',width:150},{key:'code',label:'代码',width:72},{key:'manager',label:'管理人',width:100},{key:'kind_label',label:'类型与关系',width:150},
      {key:'value',label:'持仓市值(亿)',width:85,numeric:true,render:r=>money(r.value)},{key:'public_pct',label:'占公募%',width:62,numeric:true,render:r=>pctPoint(r.public_pct)},
      {key:'nav_pct',label:'占净值%',width:65,numeric:true,render:r=>pctPoint(r.nav_pct)}],holderRows,{empty:'公募目前没有持有这只股票'}),
    companies=(holders.corp_rows||[]).slice(0,10), maxCompany=Math.max(1,...companies.map(x=>(x.share||0)*100)),
    companyBody=companies.length?`<div class="rank-bars">${companies.map(x=>`<div class="rank-bar" title="${esc(x.name)} · ${money(x.market_value)} · ${pctDecimal(x.share)}"><span>${esc(x.name)}</span><div class="track"><div class="fill" style="width:${(x.share||0)*100/maxCompany*100}%"></div></div><span class="num">${pctDecimal(x.share)}</span></div>`).join('')}</div>`:emptyState('没有公司持仓数据'),
    dims=data.dims?.items||[], dimColors={拥挤:'#b93737',流动性:'#dca65b',加仓:'#dfc064',价格:'#294e79',资金:'#5cb98b'},
    dimTotal=dims.reduce((sum,d)=>sum+(finite(d.pct)&&finite(d.weight)?Number(d.weight):0),0),
    dimBody=`<div class="score-head"><strong>${n(data.dims?.score)}</strong><span>满分 100 · 缺维重新归一</span></div><div>${dims.map(d=>{const effective=finite(d.pct)&&dimTotal?Number(d.weight)/dimTotal*100:null,contribution=finite(effective)?Number(d.pct)*effective/100:null,title=`${d.label}：原值 ${n(d.raw)} · 分位 ${n(d.pct)} · 原权重 ${n(d.weight)}% · 有效权重 ${n(effective)}% · 贡献 ${n(contribution)}分${finite(d.pct)?'':' · 本维缺失，不参与评分'}`;return `<div class="dim-row" title="${esc(title)}"><span>${esc(d.label)}</span><div class="bar"><i style="width:${finite(d.pct)?Math.max(0,Math.min(100,Number(d.pct))):0}%;background:${dimColors[d.label]||'#294e79'}"></i></div><span class="num">${n(d.pct)}</span><span class="muted">${n(effective)}%</span></div>`;}).join('')}</div>`,
    px=data.px_margin?.series||[], pxDates=px.length?px.map(p=>p.date):data.px_margin?.dates||[],
    pxChart=lineChart(pxDates,[{name:'前复权价格（元）',values:px.length?px.map(p=>p.close):data.px_margin?.close||[],color:'#294e79'},
      {name:'融资余额（亿元）',values:(px.length?px.map(p=>p.margin_balance):data.px_margin?.fin_balance||[]).map(v=>finite(v)?Number(v)/1e8:null),color:'#dca65b',axis:'right'}],{id:'price',height:310,empty:'价格与融资均无可用数据',format:n,rightFormat:v=>finite(v)?`${n(v)}亿`:'—'});
  const top=data.top10||{}, current=top.current_shares??top.h_sh, previous=top.previous_shares??top.t_sh,
    currentPeriod=top.current_period||top.period||data.marks?.top10_period||'本期未提供', previousPeriod=top.previous_period||data.marks?.previous_top10_period||'上期未提供',
    maxShares=Math.max(1,...[current,previous].filter(finite).map(Number)),
    topBody=`<div class="comparison"><div class="comparison-period">${esc(previousPeriod)} → ${esc(currentPeriod)}</div>${[[previousPeriod,previous,'#294e79'],[currentPeriod,current,'#dca65b']].map(([label,value,color])=>`<div class="comparison-row" title="${esc(label)} · 同样本持股 ${integer(value)} 股"><span>${esc(label)}</span><div class="track"><i style="width:${finite(value)?Number(value)/maxShares*100:0}%;background:${color}"></i></div><b>${integer(value)} 股</b></div>`).join('')}<p>变化 ${pctPoint(top.chg_pct)} · 覆盖率 ${pctPoint(top.cover_pct)} · 新进 ${integer(top.new_in??top.new_count)} · 退出 ${integer(top.out??top.exit_count)}</p><p class="muted">${esc(top.note||'同样本覆盖、未披露与缺失情况以当前接口质量信息为准')}</p></div>`;
  $('content').innerHTML=`<div class="crowding-page page--detail">
    <div class="kpi-band"><div class="kpi detail-select"><span class="label">个股 · ${esc(data.name||'')}</span>${stockLookup(data.code||'')}</div>
      ${kpiCard({label:'风险分',rendered:n(k.score),sub:!finite(k.score)?'当前不评分':k.band==='high'?'高风险':Number(k.score)>=70?'关注':'常规'})}${kpiCard({label:'占流通比',rendered:pctPoint(k.fr_pct),sub:`上期 ${pctPoint(k.fr_prev_pct)}`})}${kpiCard({label:'持有基金',rendered:integer(k.n_products),sub:`Δ ${signed(k.n_products_chg)} · 产品组 ${integer(holders.holder_count)} / 份额行 ${integer(holders.share_holder_count)}`})}${kpiCard({label:'持仓市值',rendered:money(k.holding_mv_now),sub:'按最新价估算'})}${kpiCard({label:'清仓天数',rendered:n(k.liq_days),sub:'持仓现值/20日均成交额'})}${kpiCard({label:'区间收益',rendered:pctPoint(k.ret_pct),sub:START_LABELS[state.start]})}${kpiCard({label:'大单30日净额',rendered:money(k.large30_amount),sub:`${pctPoint(k.mf30_pct)} / 流通市值`})}</div>
    ${panel('持有基金 · 前 30','按产品持仓排序',holderTable,'', 'no-scroll')}
    ${panel('基金公司持仓 · 前 10','前五大产品占比 '+pctPoint(k.top5_pct)+' · HHI '+n(k.company_hhi)+' · 被动 '+pctPoint(k.passive_pct),companyBody,'')}
    ${chart('price','股价与融资余额','左轴：价格（元）· 右轴：融资余额（亿元）',pxChart,'').replace('class="module"','class="module detail-price"')}
    ${panel('负反馈风险分 · 五维','各维先排成百分位再加权',dimBody,'')}
    ${panel('前十大口径 · 两期对比','主动产品；未披露不当作退出',topBody,'')}
  </div>`;
}

function sortFundScaleRows(rows, order) {
  if(!order)return rows;
  return [...rows].sort((a,b)=>{
    const x=a.later_net_asset_cny, y=b.later_net_asset_cny;
    if(!finite(x))return finite(y)?1:0;
    if(!finite(y))return -1;
    return order==='asc'?x-y:y-x;
  });
}

function renderFunds(data) {
  const scaleOrder=state.fundScaleOrder;
  const rows=sortFundScaleRows(data.funds?.rows||[], scaleOrder), withHp=state.start!=='hp';
  const cols=[{key:'index',label:'#',width:24,render:(r,i)=>i+1},{key:'name',label:'基金',width:140},{key:'display_code',label:'代码',width:90},{key:'corp',label:'管理人',width:68},{key:'kind',label:'类型与关系',width:150,render:r=>`${FUND_TYPES[r.kind]||r.kind||'未知'} · ${productLabel(r)}`},
    {key:'pool_reported_mv',label:'报告期池持仓',width:100,numeric:true,render:r=>money(r.pool_reported_mv)},{key:'pool_value_date',label:'持仓报告期',width:90,render:r=>esc(r.pool_value_date||data.holding_period||data.marks?.holding_period||'—')},{key:'net_asset',label:'同期净资产',width:90,numeric:true,render:r=>money(r.net_asset)},{key:'scale_period',label:'净资产/申赎期',width:100,render:r=>esc(r.scale_period||'—')},{key:'pool_of_nav_pct',label:'报告期占净值',width:90,numeric:true,render:r=>finite(r.pool_reported_mv)&&finite(r.pool_of_nav_pct)?`<span class="${Number(r.pool_of_nav_pct)>100?'quality-warn':''}" title="${Number(r.pool_of_nav_pct)>100?'产品份额关系或规模口径存在冲突，仅作诊断':''}">${pctPoint(r.pool_of_nav_pct)}${Number(r.pool_of_nav_pct)>100?' ⚠':''}</span>`:'—'},
    {key:'ret_pct',label:'区间收益',width:76,numeric:true,render:r=>`<span class="${trendClass(r.ret_pct)}">${pctPoint(r.ret_pct)}</span>`},...(withHp?[{key:'since_hp_pct',label:'持仓期以来',width:80,numeric:true,render:r=>`<span class="${trendClass(r.since_hp_pct)}">${pctPoint(r.since_hp_pct)}</span>`}]:[]),
    {key:'dd_pct',label:'距高点',width:60,numeric:true,render:r=>pctPoint(r.dd_pct)},{key:'redeem_pct',label:'上期赎回%',width:64,numeric:true,render:r=>pctPoint(r.redeem_pct)},{key:'purchase_pct',label:'上期申购%',width:64,numeric:true,render:r=>pctPoint(r.purchase_pct)},{key:'individual_pct',label:'个人持有%',width:64,numeric:true,render:r=>pctPoint(r.individual_pct)},{key:'holder_period',label:'持有人结构期',width:100,render:r=>esc(r.holder_period||'—')}];
  const later=data.later_scale;
  const laterStatus={no_verified_period_relation:'缺少同季份额清单',ambiguous_period_relation:'同季份额关系冲突',incomplete_share_scale:'至少一类份额缺少可核实的同季规模'};
  if(later) cols.splice(10,0,{key:'later_net_asset_cny',sortable:true,label:`${later.report_period} 净资产`,width:139,numeric:true,render:r=>{
    const x=r.later_scale||{}, shares=(x.share_scales||[]).map(s=>`${s.symbol} ${s.scale_yi.toFixed(2)} 亿元`).join('；');
    return `<span title="${esc(shares?shares+'；按各份额保留两位亿元后的源值合计':laterStatus[x.status]||'缺少数据')}">${money(r.later_net_asset_cny)}</span>`;
  }});
  const etfRows=data.etfs?.rows||[], etfCols=[{key:'index',label:'#',width:24,render:(r,i)=>i+1},{key:'name',label:'ETF',width:140},{key:'fund_code',label:'代码',width:90},{key:'pool_mv',label:'池持仓(亿)',width:78,numeric:true,render:r=>money(r.pool_mv)},{key:'net_asset',label:'同日净资产',width:90,numeric:true,render:r=>money(r.net_asset)},{key:'pool_of_nav_pct',label:'占净值(估)',width:90,numeric:true,render:r=>`<span title="${esc(r.pool_of_nav_basis||r.pool_of_nav_reason||'同日净资产口径；期后持仓变化未校正')}">${pctPoint(r.pool_of_nav_pct)}</span>`},{key:'nav_date',label:'净资产日期',width:90,render:r=>esc(r.nav_date||'—')},{key:'share',label:'份额(亿份)',width:70,numeric:true,render:r=>finite(r.share)?n(Number(r.share)/1e8):'—'},{key:'metric_date',label:'份额日',width:90,render:r=>esc(r.metric_date||'—')},{key:'chg_pct',label:'较基期%',width:72,numeric:true,render:r=>pctPoint(r.chg_pct)},{key:'base_date',label:'份额基期',width:90,render:r=>esc(r.base_date||'—')},{key:'spark',label:'走势',width:80,render:r=>sparkline(r.spark)}];
  const balances=data.balance?.windows||[], windowIndex=Math.max(0,['5','20','60'].indexOf(String(state.window||'20'))), w=balances[windowIndex]||balances[1]||{};
  const qualityLabels={exact:'已核实',estimated:'估算',estimate:'估算',partial:'局部覆盖',missing:'缺失',unavailable:'不可用',unverified:'含义未核实'};
  const flowReasons={incomplete_trading_window:'交易窗口不完整',window_not_aligned:'窗口未对齐',source_semantics_unverified:'来源含义未核实',event_source_completeness_unverified:'事件来源完整性未核实',disclosure_and_event_deduplication_incomplete:'披露覆盖或事件去重未完成',value_missing:'缺少数值'};
  const reasonLabel=reason=>flowReasons[reason]||reason||'';
  const excluded=new Map((w.excluded_components||[]).map(row=>[row.key,row.reason]));
  const flowRows=[...(w.inflow||[]).map(r=>({...r,section:'流入'})),...(w.outflow||[]).map(r=>({...r,section:'流出'})),...(w.standalone||[]).map(r=>({...r,section:'独立观察'}))];
  const flowQuality=row=>[!finite(row.value)?'缺失':qualityLabels[row.quality]||row.quality||'待核实',!finite(row.value)&&row.quality==='exact'?'来源口径已核实':null,row.note||reasonLabel(row.reason),excluded.has(row.key)?'未计入净额：'+reasonLabel(excluded.get(row.key)):null].filter(Boolean).join(' · ');
  const flowTable=table([{key:'section',label:'范围',width:66},{key:'label',label:'项目',width:160,render:r=>`<span title="${esc(flowQuality(r))}">${esc(r.label)}</span>`},{key:'value',label:'观察金额',width:95,numeric:true,render:r=>money(r.value)},{key:'as_of',label:'接口截止日',width:84,render:r=>excluded.get(r.key)==='window_not_aligned'?'未对齐':esc(r.as_of||'—')},{key:'quality',label:'口径与限制',width:260,render:r=>esc(flowQuality(r))}],flowRows,{empty:'资金数据缺失'});
  const strictNet=w.strict_net??(w.net_quality==='exact'?w.net:null), estimatedNet=w.estimated_net??w.estimated_net_cny??(['estimate','estimated','partial'].includes(w.net_quality)?w.net:null),
    netSummary=`<div class="flow-summary"><span>完整同口径净额：<b>${money(strictNet)}</b></span><span class="quality-warn">覆盖样本净额估算：<b>${money(estimatedNet)}</b></span><span class="muted">${esc(w.net_note||w.note||'独立观察项不计入净额；缺失或未核实分项不补零')}</span></div>`;
  const windows=`<div class="seg">${['5','20','60'].map(size=>`<button data-window="${size}" class="${String(state.window)===size?'active':''}">${size} 日</button>`).join('')}</div>`;
  $('content').innerHTML=`<div class="crowding-page page--funds"><div class="funds-top">${panel('抱团池重仓 · 主动基金',(later?`按最新价重估的池市值入选；占净值沿用持仓报告期，另列 ${later.report_period} 净资产（${later.coverage.observed_non_pit||0}/${rows.length}，事后快照），不参与持仓比例`:'报告期市值 / 同期净资产；近似产品与未归并份额分列'),table(cols,rows,{sort:scaleOrder?'later_net_asset_cny':'',order:scaleOrder}),'','no-scroll')}${chart('nav','区间净值分布','前 50 只基金收益',histogram(data.nav_dist?.bins||[]),'')}</div><div class="funds-bottom">${panel('重仓抱团股的 ETF','前 30 只 · 份额变化',table(etfCols,etfRows),'','no-scroll')}${panel('市场增量资金','各分项日期、覆盖和估算状态分别列示',netSummary+flowTable,windows)}</div></div>`;
}

function sparkline(points) {
  const vals=(points||[]).map(p=>p&&typeof p==='object'?p.n_products??p.share_pct:p), valid=vals.filter(finite).map(Number);
  if(!valid.length)return '—';
  const max=Math.max(...valid),min=Math.min(...valid),span=max-min||1,x=i=>i*80/Math.max(1,vals.length-1),y=v=>18-(Number(v)-min)/span*16;
  let path='',on=false;
  vals.forEach((v,i)=>{if(!finite(v)){on=false;return;}path+=`${on?'L':'M'}${x(i).toFixed(1)} ${y(v).toFixed(1)} `;on=true;});
  const dots=vals.map((v,i)=>finite(v)?`<circle cx="${x(i)}" cy="${y(v)}" r="1.2" fill="#294e79"/>`:'').join('');
  return `<svg viewBox="0 0 80 20" width="80" height="20"><title>${esc(vals.map(v=>finite(v)?n(v):'缺失').join(' · '))}</title><path d="${path}" fill="none" stroke="#294e79" stroke-width="1.5"/>${dots}</svg>`;
}

function renderIndustries(data) {
  const rows=data.table?.rows||[], maxHist=6;
  const cols=[{key:'name',label:'行业',width:82,render:r=>r.name==='合计'?esc(r.name):`<button class="link" data-industry="${esc(r.name)}">${esc(r.name)}</button>`},{key:'hold_mv',label:'持仓市值',width:76,numeric:true,render:r=>money(r.hold_mv)},{key:'share_pct',label:'占比%',width:58,numeric:true,render:r=>pctPoint(r.share_pct)},{key:'bench_pct',label:'基准%',width:58,numeric:true,render:r=>pctPoint(r.bench_pct)},{key:'ow_pp',label:'超配',width:56,numeric:true,render:r=>signed(r.ow_pp)},{key:'share_chg_pp',label:'Δ占比',width:56,numeric:true,render:r=>signed(r.share_chg_pp)},{key:'top100_ow_pp',label:'前100大超配',width:86,numeric:true,render:r=>signed(r.top100_ow_pp)},{key:'hist',label:'6期走势',width:70,render:r=>sparkline(r.hist)},{key:'pool_n',label:'抱团池',width:52,numeric:true,render:r=>integer(r.pool_n)},{key:'fr_median_pct',label:'占流通中位',width:70,numeric:true,render:r=>pctPoint(r.fr_median_pct)},{key:'ret_pct',label:'区间收益',width:76,numeric:true,render:r=>`<span class="${trendClass(r.ret_pct)}">${pctPoint(r.ret_pct)}</span>`}];
  const pos=data.position||{}, boxes=pos.boxes||[], labels=(pos.periods||[]).map(p=>p.key);
  const posChart=boxes.length?boxChart(labels,boxes):emptyState('基金仓位历史暂无数据');
  const typeRows=data.by_type?.rows||[];
  const typeTable=table([{key:'type',label:'基金类型',width:150},{key:'n',label:'只数',width:56,numeric:true,render:r=>integer(r.n)},{key:'med',label:'仓位中位',width:88,numeric:true,render:r=>pctPoint(r.med)},{key:'w',label:'加权仓位',width:88,numeric:true,render:r=>pctPoint(r.w)},{key:'net_asset',label:'净资产',width:84,numeric:true,render:r=>money(r.net_asset)}],typeRows,{empty:'分类型仓位暂无数据'});
  const top100=data.top100?.rows||[], maxPair=Math.max(1,...top100.flatMap(x=>[x.all_pp,x.top100_pp]).filter(finite).map(x=>Math.abs(Number(x))));
  const bars=top100.length?`<div class="rank-bars paired"><div class="paired-scale"><span>低配</span><span>0 pp</span><span>超配</span></div>${top100.map(x=>`<div class="rank-pair"><button class="link rank-name" data-industry="${esc(x.name)}">${esc(x.name)}</button><div class="rank-tracks">${[[x.all_pp,'全部','#294e79'],[x.top100_pp,'前100大','#b93737']].map(([value,label,color])=>`<div title="${esc(x.name)} · ${label}超配 ${signed(value)} pp"><span class="signed-track"><i style="left:${finite(value)&&Number(value)<0?50-Math.abs(Number(value))/maxPair*50:50}%;width:${finite(value)?Math.abs(Number(value))/maxPair*50:0}%;background:${color}"></i></span><em>${signed(value)}</em></div>`).join('')}</div></div>`).join('')}</div>`:emptyState('前100大超配暂不可用');
  const hist=(data.hist?.rows||[]).slice().sort((a,b)=>{const latest=x=>(x.share_values||x.values||[]).at(-1);return (finite(latest(b))?Number(latest(b)):-Infinity)-(finite(latest(a))?Number(latest(a)):-Infinity);});
  const overweight=state.industryMode==='overweight', series=hist.slice(0,8).map((x,i)=>({name:x.name,values:overweight?x.overweight_values||[]:x.share_values||x.values||[],color:['#294e79','#e3a05f','#5cb98b','#dc7891','#b09adf','#dfc064','#77c3c7','#ce8fbe'][i]}));
  const histPeriods=(data.hist?.periods||[]).map(p=>typeof p==='object'?p.label||p.key:p), modes=`<div class="seg"><button data-industry-mode="share" class="${!overweight?'active':''}">占比</button><button data-industry-mode="overweight" class="${overweight?'active':''}">超配</button></div>`;
  $('content').innerHTML=`<div class="crowding-page page--industries"><div class="industry-top">${panel('申万一级 · 公募持仓与超配','点击行业筛选对应股票；当前分类回看',table(cols,rows),'','no-scroll')}${chart('industryHist',`${overweight?'行业超配':'持仓占比'} · 6 期`,'最新前 8 大行业；半年度/年度披露',lineChart(histPeriods,series,{id:`industryHist-${state.industryMode}`,zero:overweight,empty:overweight?'历史行业超配数据暂无覆盖':'历史行业占比暂无数据',format:overweight?v=>finite(v)?`${signed(v)} pp`:'—':pctPoint}),modes)}</div><div class="industry-bottom">${panel('超配 · 全部 vs 前 100 大基金','蓝：全部 · 红：前100大 · 0 为分界',bars,'')}${chart('positions','偏股基金仓位 · 6 期','P10/P25/P50/P75/P90',posChart,'')}${panel('分类型仓位','报告期 '+esc(data.by_type?.period||pos.periods?.at(-1)?.label||pos.periods?.at(-1)?.key||'—')+(data.by_type?.size_weight_status?' · 份额与产品净资产口径待核实，暂缺规模加权':''),typeTable,'')}</div></div>`;

}

function boxChart(labels, boxes) {
  const w=720,h=300,l=46,r=708,t=18,b=240;
  const complete=x=>[x.p10,x.p25,x.median,x.p75,x.p90].every(finite);
  const values=boxes.filter(complete).flatMap(x=>[x.p10,x.p90]).map(Number);
  if(!values.length)return emptyState('基金仓位历史暂无数据');
  const max=Math.min(100,Math.ceil(Math.max(...values)/5)*5),min=Math.max(0,Math.floor(Math.min(...values)/5)*5),span=max-min||1;
  const scale=v=>b-(Number(v)-min)/span*(b-t);
  const yTicks=[0,1,2,3,4].map(i=>min+span*i/4);
  const grid=yTicks.map(v=>`<line x1="${l}" y1="${scale(v)}" x2="${r}" y2="${scale(v)}" stroke="#e3e5ea"/><text x="2" y="${scale(v)+3}" fill="#606269">${v.toFixed(0)}%</text>`).join('');
  return `<div class="chart-legend"><span>箱体 P25–P75</span><span>中线 P50</span><span>须线 P10–P90</span></div><div class="chart-host"><svg viewBox="0 0 ${w} ${h}">${grid}${boxes.map((x,i)=>{const cx=l+(i+.5)*(r-l)/boxes.length,bw=(r-l)/boxes.length*.55;if(!complete(x))return `<g><text x="${cx}" y="${(t+b)/2}" text-anchor="middle" fill="#606269">暂无数据</text><text x="${cx}" y="${b+19}" text-anchor="middle" fill="#606269">${esc(labels[i]||x.key)}</text></g>`;return `<g><line x1="${cx}" y1="${scale(x.p10)}" x2="${cx}" y2="${scale(x.p90)}" stroke="#8ca5c7"/><rect x="${cx-bw/2}" y="${scale(x.p75)}" width="${bw}" height="${Math.max(1,scale(x.p25)-scale(x.p75))}" fill="#263143" stroke="#8ca5c7"/><line x1="${cx-bw/2}" y1="${scale(x.median)}" x2="${cx+bw/2}" y2="${scale(x.median)}" stroke="#efb06e" stroke-width="2"/><text x="${cx}" y="${b+19}" text-anchor="middle" fill="#606269">${esc(labels[i]||x.key)}</text><title>${esc(labels[i]||x.key)} · ${integer(x.n??x.n_products)}只：${pctPoint(x.p10)} / ${pctPoint(x.p25)} / ${pctPoint(x.median)} / ${pctPoint(x.p75)} / ${pctPoint(x.p90)}</title></g>`;}).join('')}<line x1="${l}" y1="${b}" x2="${r}" y2="${b}" stroke="#aeb4bf"/></svg></div>`;
}

function renderIndex(data) {
  const s=data.series||{}, periods=data.periods||[], labels=periods.map(x=>x.label||x.key), topStocks=data.top_stocks||[];
  const indexModes={top3:'前三行业',hhi:'行业 HHI',dev:'行业偏离度'};
  const indexFormat=state.indexMode==='hhi'?n:pctPoint;
  const industrySeries=state.indexMode==='hhi'
    ?[{name:'行业 HHI · 前十大',values:s.hhi||[],color:'#294e79'},{name:'行业 HHI · 全部持仓',values:s.full_hhi||[],color:'#d7a765',dash:'4 4',fullDisclosure:true}]
    :state.indexMode==='dev'
      ?[{name:'行业偏离度 · 前十大',values:s.dev_pct||[],color:'#294e79'},{name:'行业偏离度 · 全部持仓',values:s.full_dev_pct||[],color:'#d7a765',dash:'4 4',fullDisclosure:true}]
      :[{name:'前三行业占比 · 前十大',values:s.top3_pct||[],color:'#294e79'},{name:'前三行业占比 · 全部持仓',values:s.full_top3_pct||[],color:'#d7a765',dash:'4 4',fullDisclosure:true}];
  const overlapSeries=[{name:'重合度·全部',values:s.jac_all_pct||[],color:'#294e79'},{name:'重合度·头部200（严格）',values:s.jac_head_pct||[],color:'#5bb68b'},...(s.jac_head_estimate_pct?.some(finite)?[{name:'已知规模前200估算',values:s.jac_head_estimate_pct,color:'#87cba6',dash:'4 4'}]:[]),{name:'前20股集中度',values:s.top20_pct||[],color:'#e0a55d',axis:'right'},{name:'前十大权重中位',values:s.w10_median_pct||[],color:'#d07e9d',axis:'right'}];
  const topTable=table([{key:'index',label:'#',width:26,render:(r,i)=>i+1},{key:'name',label:'股票',width:140,render:r=>`<button class="link" data-stock="${esc(r.code)}">${esc(r.name||r.code)}</button>`},{key:'l1_name',label:'行业',width:64},{key:'share_pct',label:'占前十大%',width:78,numeric:true,render:r=>pctPoint(r.share_pct)},{key:'share_chg_pp',label:'Δ(pp)',width:56,numeric:true,render:r=>signed(r.share_chg_pp)},{key:'n_products',label:'产品/未归并份额',width:156,numeric:true,render:r=>`<span title="已验证或近似产品 ${integer(r.n_products)}；未归并份额 ${integer(r.n_unmerged_shares)}；保留身份总数 ${integer(r.n_identities)}。未归并份额不等于独立产品。">${integer(r.n_products)} 产品 / ${integer(r.n_unmerged_shares)} 份额</span>`},{key:'spark',label:'12季身份数',width:82,render:r=>`<span title="保留身份数走势，包含未归并份额">${sparkline((r.spark||[]).map(p=>p?{...p,n_products:finite(p.n_identities)?p.n_identities:finite(p.n_products)&&finite(p.n_unmerged_shares)?Number(p.n_products)+Number(p.n_unmerged_shares):null}:null))}</span>`}],topStocks.slice(0,30));
  const focusCodes=new Set(data.focus||[]), full=state.focusMode==='full', modeKey=full?'full':'top10', allIndustries=(data.industries||[]).slice(),
    latestIndex=periods.findIndex(p=>p.key===data.latest), latest=latestIndex<0?periods.length-1:latestIndex;
  const focusLatest=full?periods.reduce((found,p,i)=>i<=latest&&[2,4].includes(disclosureQuarter(p))&&allIndustries.some(x=>finite(x.full?.[i]))?i:found,-1):latest,
    latestValue=x=>finite(x[modeKey]?.[focusLatest])?Number(x[modeKey][focusLatest]):-Infinity;
  allIndustries.sort((a,b)=>latestValue(b)-latestValue(a));
  const selected=(focusCodes.size?allIndustries.filter(x=>focusCodes.has(x.l1_code)):allIndustries.slice(0,4)), leader=latestValue(allIndustries[0]||{})>-Infinity?allIndustries[0]?.l1_code:null;
  const focusSeries=selected.map((x,i)=>({name:x.name,values:x[modeKey]||[],fullDisclosure:full,emphasis:x.l1_code===leader,color:['#294e79','#dca65b','#5cb98b','#d07e9d','#b49de6','#dfc064','#72c4c5','#c68aba'][i]}));
  const common={zero:false,periods,disclosing:periods.map(p=>!!p.disclosing),refIndex:periods.findIndex(p=>/^(20)?21Q1$/.test(p.key)),format:pctPoint};
  const focusChart=lineChart(labels,focusSeries,{...common,id:`focusIndustries-${state.focusMode}`,empty:'此口径的重点行业历史序列暂无数据'});
  const latestPeriod=periods[latest], focusPeriod=periods[focusLatest], focusHint=`排序期 ${focusPeriod?.label||focusPeriod?.key||'—'} · 重点行业与最新排名；加粗：该期第一大行业`, focusModes=`<div class="seg"><button data-focus-mode="top10" class="${!full?'active':''}">前十大</button><button data-focus-mode="full" class="${full?'active':''}">全部持仓</button></div>`;
  $('content').innerHTML=`<div class="crowding-page page--index"><div class="kpi-band six">${(data.kpis||[]).map(item=>kpiCard(item.key==='jac_head'&&item.quality==='estimated'?{...item,label:'已知规模前200估算',sub:'估算 · '+(item.sub||'不代表完整规模样本')}:item)).join('')}</div>${chart('industryIndex','行业集中度','实线：前十大；虚线：全部持仓；空心点：披露中',lineChart(labels,industrySeries,{...common,id:`industryIndex-${state.indexMode}`,empty:'行业集中度暂无数据',format:indexFormat}),`<div class="seg">${Object.entries(indexModes).map(([key,label])=>`<button data-index-mode="${key}" class="${state.indexMode===key?'active':''}">${label}</button>`).join('')}</div>`)}${chart('overlapIndex','个股层 · 重合与集中','左轴：重合度；右轴：集中度/前十大权重中位',lineChart(labels,overlapSeries,{...common,id:'overlapIndex',rightFormat:pctPoint,empty:'重合度暂无数据'}),'')}${chart('focusIndustries','重点行业占比',focusHint,focusChart,focusModes)}${panel('最集中的股票',`主动产品前十大 · ${latestPeriod?.label||latestPeriod?.key||'—'}${latestPeriod?.disclosing?' · 披露中':''}`,topTable,'','no-scroll')}</div>`;
}

function pageByName(name) {
  return ({overview:renderOverview,stocks:renderStocks,detail:renderDetail,funds:renderFunds,industries:renderIndustries,index:renderIndex})[name];
}

function updateChrome(data) {
  const marks=data?.marks||{}, day=marks.market_date||data?.trade_date||data?.as_of||'—';
  $('asof').textContent=`数据日 ${day}${marks.latest_price_date&&marks.latest_price_date!==day?` · 价格已到 ${marks.latest_price_date}，估值待齐`:''}`;
  $('asof').title=$('asof').textContent;
  const stage=marks.holding_period?`持仓期 ${marks.holding_period}`:'持仓期 —';
  const version=data?.data_version||state.dataVersion;
  const versionText=version?`缓存 ${String(version).slice(0,10)}`:'缓存 —';
  $('footer').textContent=`公募股票持仓按报告期披露 · ${stage} · 行情日 ${day} · ${versionText} · 冻结快照`;
}

async function load({replace=false,force=false}={}) {
  const sequence=++loadSequence, tab=state.tab;
  performance.mark(`crowding-load-start-${sequence}`);
  loadController?.abort(); loadController=new AbortController();
  lookupController?.abort();lookupSequence++;clearTimeout(window.__stockLookupTimer);
  chartModels.clear(); pageChrome();syncURL(!replace);
  if(tab==='detail'&&!state.selected){state.data=null;renderDetail(null);return;}
  loading();
  try {
    const data=await fetchPage(tab,{signal:loadController.signal,force});
    if(sequence!==loadSequence)return;
    state.data=data;updateChrome(data);
    if(tab==='detail'&&data?.kpi)state.selected=data.code||state.selected;
    pageByName(tab)(data);
    performance.mark('crowding-rendered');
    performance.measure('crowding-load',`crowding-load-start-${sequence}`,'crowding-rendered');
    document.documentElement.dataset.crowdingLoadMs=performance.getEntriesByName('crowding-load').at(-1).duration.toFixed(2);
    if(tab==='stocks'&&state.q){requestAnimationFrame(()=>{const input=$('stockSearch');if(input){input.focus();input.setSelectionRange(input.value.length,input.value.length);}});}
    syncURL();
  } catch(error) {
    if(sequence!==loadSequence||error.name==='AbortError')return;
    const versionConflict=error.status===409||error.code===409;
    if(tab==='stocks'){renderStocks({error:error.message||'接口读取失败',versionConflict,filters:{industries:state.l1?[state.l1]:[]},table:{page:state.page,pages:state.page}});return;}
    $('content').innerHTML=panel('读取失败','当前筛选已保留',(tab==='detail'?`<div class="detail-search">${stockLookup(state.selected)}</div>`:'')+emptyState(error.message||'接口读取失败')+retryControls(versionConflict),'');
  }
}

function refreshDataVersion() {
  state.dataVersion='';pageCache.clear();
  $('notice').hidden=true;$('notice').innerHTML='';
  syncURL();load({replace:true,force:true});
}

function syncURL(push=false) {
  const q=new URLSearchParams({tab:state.tab,start:state.start,weight:state.weight});
  if(state.tab==='stocks'){['view','q','l1','sort','order'].forEach(k=>{if(state[k])q.set(k,state[k])});q.set('page',state.page);q.set('page_size',state.pageSize);}
  if(state.tab==='funds')q.set('window',state.window);
  if(state.tab==='index'){q.set('indexMode',state.indexMode);q.set('focusMode',state.focusMode);}
  if(state.tab==='industries')q.set('industryMode',state.industryMode);
  if(state.selected)q.set('code',state.selected);
  if(state.dataVersion)q.set('data_version',state.dataVersion);
  const url=`?${q}`;
  if(location.search!==url)history[push?'pushState':'replaceState'](null,'',url);
}

window.addEventListener('popstate',()=>{
  const p=new URLSearchParams(location.search);
  state.tab=TABS.some(([key])=>key===p.get('tab'))?p.get('tab'):'overview';
  state.start=START_LABELS[p.get('start')]?p.get('start'):'hp';
  state.view=VIEW_LABELS[p.get('view')]?p.get('view'):'pool';
  state.q=p.get('q')||'';state.l1=p.get('l1')||'';state.selected=p.get('code')||'';
  state.sort=p.get('sort')||'score';state.order=p.get('order')==='asc'?'asc':'desc';
  state.page=Math.max(1,Number(p.get('page'))||1);state.pageSize=[20,50,100].includes(Number(p.get('page_size')))?Number(p.get('page_size')):100;
  state.window=['5','20','60'].includes(p.get('window'))?p.get('window'):'20';
  state.weight=p.get('weight')==='cw'?'cw':'equal';state.industryMode=p.get('industryMode')==='overweight'?'overweight':'share';
  state.indexMode=['top3','hhi','dev'].includes(p.get('indexMode'))?p.get('indexMode'):'top3';state.focusMode=p.get('focusMode')==='full'?'full':'top10';
  state.dataVersion=p.get('data_version')||state.dataVersion;
  load({replace:true});
});

document.addEventListener('click', event => {
  const button=event.target.closest('button,[data-stock]');
  if(!button)return;
  if(button.dataset.refreshVersion){refreshDataVersion();return;}
  if(button.dataset.retry){load({replace:true,force:true});return;}
  if(button.dataset.industry){state.tab='stocks';state.view='held';state.l1=button.dataset.industry;state.q='';state.page=1;load();return;}
  if(button.dataset.tab){state.tab=button.dataset.tab;state.page=1;state.q='';state.l1='';if(button.dataset.view)state.view=button.dataset.view;load();return;}
  if(button.dataset.start){state.start=button.dataset.start;state.page=1;pageCache.clear();load();return;}
  if(button.dataset.sort==='later_net_asset_cny'&&state.tab==='funds'){
    state.fundScaleOrder=state.fundScaleOrder==='desc'?'asc':'desc';renderFunds(state.data);return;
  }
  if(button.dataset.sort){const sort=button.dataset.sort;if(state.sort===sort)state.order=state.order==='asc'?'desc':'asc';else{state.sort=sort;state.order='desc';}state.page=1;load();return;}
  if(button.dataset.stock){state.selected=button.dataset.stock;state.tab='detail';load();return;}
  if(button.dataset.view){state.view=button.dataset.view;state.page=1;load();return;}
  if(button.dataset.weight){state.weight=button.dataset.weight;renderOverview(state.data);syncURL(true);return;}
  if(button.dataset.window){state.window=button.dataset.window;renderFunds(state.data);syncURL(true);return;}
  if(button.dataset.indexMode){state.indexMode=button.dataset.indexMode;renderIndex(state.data);syncURL(true);return;}
  if(button.dataset.industryMode){state.industryMode=button.dataset.industryMode;renderIndustries(state.data);syncURL(true);return;}
  if(button.dataset.focusMode){state.focusMode=button.dataset.focusMode;renderIndex(state.data);syncURL(true);return;}
  if(button.dataset.page){state.page=Math.max(1,state.page+Number(button.dataset.page));load();return;}
  if(button.dataset.seriesToggle){const [id,name]=button.dataset.seriesToggle.split('|');const set=hiddenSeries.get(id)||new Set();set.has(name)?set.delete(name):set.add(name);hiddenSeries.set(id,set);pageByName(state.tab)(state.data);return;}
});
document.addEventListener('input', event => {
  if(event.target.id==='stockSearch'){state.q=event.target.value;clearTimeout(window.__searchTimer);window.__searchTimer=setTimeout(()=>{state.page=1;load()},250);}
  if(event.target.id==='industryFilter'){state.l1=event.target.value;state.page=1;load();}
  if(event.target.id==='pageSize'){state.pageSize=Number(event.target.value)||100;state.page=1;pageCache.clear();load();}
  if(event.target.id==='stockLookup'){
    const value=event.target.value.trim(),box=$('stockSuggestions'),sequence=++lookupSequence;
    clearTimeout(window.__stockLookupTimer);lookupController?.abort();
    if(!value){box.hidden=true;box.innerHTML='';return;}
    box.innerHTML='<div class="stock-none">正在搜索…</div>';box.hidden=false;
    window.__stockLookupTimer=setTimeout(async()=>{
      const controller=new AbortController();lookupController=controller;
      try{
        const query=new URLSearchParams({q:value});if(state.dataVersion)query.set('data_version',state.dataVersion);
        const response=await fetch(`./api/dashboards/fund_crowding/stock-search?${query}`,{signal:controller.signal});
        const body=await response.json();
        if(sequence!==lookupSequence||!box.isConnected)return;
        if(!response.ok||body.code!==0){const error=new Error(body.message||'搜索失败');error.status=response.status;error.code=body.code;throw error;}
        const version=body.data?.data_version||body.data_version;
        if(state.dataVersion&&version&&String(version)!==state.dataVersion)throw new Error('搜索数据版本不一致，请刷新重试');
        if(!state.dataVersion&&version){state.dataVersion=String(version);syncURL();}
        const rows=body.data?.rows||[];
        box.innerHTML=rows.length?rows.map(row=>`<button data-stock="${esc(row.code)}"><strong>${esc(row.name||row.code)}</strong><span>${esc(row.code)} · ${esc(row.l1_name||'未分类')}</span></button>`).join(''):'<div class="stock-none">没有匹配股票</div>';
      }catch(error){if(error.name!=='AbortError'&&sequence===lookupSequence&&box.isConnected)box.innerHTML=`<div class="stock-none">${esc(error.message||'搜索失败')}${error.status===409||error.code===409?'<button data-refresh-version="1">重新打开当前冻结版本</button>':''}</div>`;}
    },180);
  }
});
document.addEventListener('keydown', event => {
  if(event.target.id==='stockLookup'&&event.key==='Enter'){const first=$('stockSuggestions')?.querySelector('[data-stock]');if(first){event.preventDefault();state.selected=first.dataset.stock;state.tab='detail';load();}return;}
  if(/^[1-6]$/.test(event.key)&&!event.ctrlKey&&!event.metaKey&&!event.altKey&&!['INPUT','TEXTAREA','SELECT'].includes(document.activeElement?.tagName)&&!document.activeElement?.isContentEditable&&!$('methodDialog').open){state.tab=TABS[Number(event.key)-1][0];state.page=1;load();}
});

document.addEventListener('pointermove',event=>{
  const svg=event.target.closest('svg[data-chart-id]');if(!svg)return;
  const model=chartModels.get(svg.dataset.chartId);if(!model||!model.labels.length)return;
  const point=svg.createSVGPoint();point.x=event.clientX;point.y=event.clientY;
  const ctm=svg.getScreenCTM();if(!ctm)return;
  const local=point.matrixTransform(ctm.inverse()),host=svg.parentElement,tooltip=host.querySelector('.chart-tooltip'),line=svg.querySelector('.chart-hover-line');
  if(local.x<model.l||local.x>model.r||local.y<model.t||local.y>model.b){tooltip.hidden=true;line.setAttribute('visibility','hidden');return;}
  const index=Math.max(0,Math.min(model.labels.length-1,Math.round((local.x-model.l)/(model.r-model.l)*(model.labels.length-1))));
  tooltip.innerHTML=`<strong>${esc(model.labels[index])}</strong>${model.series.map(s=>`<div><i style="background:${s.color}"></i><span>${esc(s.name)}</span><b>${esc((s.format||(s.axis==='right'?model.rightFormat:model.format))(s.values?.[index]))}</b></div>`).join('')}`;
  tooltip.hidden=false;
  const rect=host.getBoundingClientRect(),left=event.clientX-rect.left+14;
  tooltip.style.left=`${Math.max(0,Math.min(left,rect.width-tooltip.offsetWidth-6))}px`;
  tooltip.style.top=`${Math.max(0,Math.min(event.clientY-rect.top+12,rect.height-tooltip.offsetHeight-4))}px`;
  line.setAttribute('x1',model.x(index));line.setAttribute('x2',model.x(index));line.setAttribute('visibility','visible');
});
document.addEventListener('pointerout',event=>{
  const host=event.target.closest('.chart-host');if(!host||host.contains(event.relatedTarget))return;
  const tooltip=host.querySelector('.chart-tooltip');if(tooltip)tooltip.hidden=true;
  host.querySelector('.chart-hover-line')?.setAttribute('visibility','hidden');
});
document.addEventListener('click',event=>{if(!event.target.closest('.stock-lookup')){const box=$('stockSuggestions');if(box)box.hidden=true;}});
load({replace:true});
