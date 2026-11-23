import type {AgentEvent, Message, ReportRef, RunSnapshot} from './api';

export type Step = {id: string; label: string; status: string};
export type Turn = {
  id: string;
  status: string;
  reason: string;
  text: string;
  final: boolean;
  persisted: boolean;
  summary: string;
  tools: Record<string, Step>;
  todos: Step[];
  warning: string;
};

export function newTurn(id: string): Turn {
  return {id, status: 'running', reason: '', text: '', final: false, persisted: false, summary: '', tools: {}, todos: [], warning: ''};
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? value as Record<string, unknown> : {};
}

export function reportRef(value: unknown): ReportRef | undefined {
  const {job_id, fund_name, fund_symbol, type, title} = record(value);
  if (typeof job_id !== 'string' || !job_id) return;
  return {job_id, type: type === 'industry_job' ? type : 'report_job', title: typeof title === 'string' ? title : undefined,
    fund_name: typeof fund_name === 'string' ? fund_name : undefined, fund_symbol: typeof fund_symbol === 'string' ? fund_symbol : undefined};
}

export function reportsFromBlocks(blocks: unknown): ReportRef[] {
  return Array.isArray(blocks) ? blocks.flatMap(value => {
    const ref = ['report_job', 'industry_job'].includes(String(record(value).type)) ? reportRef(value) : undefined;
    return ref ? [ref] : [];
  }) : [];
}

export function reportsFromEvent({event_type, payload}: AgentEvent): ReportRef[] {
  if (event_type === 'done') return reportsFromBlocks(payload.ui_blocks);
  const ref = ['report_job_created', 'industry_job_created'].includes(event_type)
    ? reportRef(event_type === 'industry_job_created' ? {...payload, type: 'industry_job'} : payload) : undefined;
  return ref ? [ref] : [];
}

function text(value: unknown) {
  return typeof value === 'string' ? value : '';
}

const toolLabels: Record<string, string> = {
  'industry.research': '创建产业链研究',
  'industry.status': '查询产业链研究进度',
  'industry.result': '阅读产业链研究成果',
  'industry.resume': '继续产业链研究',
  'industry.cancel': '取消产业链研究',
  'fund.resolve': '识别基金与份额',
  'fund.profile': '读取基金信息',
  'fund.performance': '查询基金业绩',
  'fund.holdings': '查看基金持仓',
  'report.request': '创建深度报告',
  'report.generate': '创建深度报告',
  'fund.research_snapshot': '读取基金研究数据',
  'fund.metric': '查询基金指标',
  'fund.inspect_coverage': '核对数据覆盖',
  'report.resume': '继续报告任务',
  'report.cancel': '取消报告任务',
  'report.status': '查询报告状态',
  'report.result': '读取报告成果',
  'artifact.read': '阅读研究成果',
  'web.search_manager_materials': '检索基金经理材料',
  'memory.list': '读取研究记忆',
  'memory.remember': '记录已确认信息',
  'memory.forget': '移除研究记忆',
  'kb.search': '检索研究资料',
};

export const statusLabels: Record<string, string> = {
  running: '研究中', completed: '已完成', recoverable: '等待继续', failed: '研究失败',
  stopped: '已停止', cancelled: '已取消', pending: '待执行', in_progress: '进行中',
  success: '已完成', partial: '部分完成', error: '执行失败',
};

export function turnReason(reason: string) {
  const reasons: Record<string, string> = {
    chat_deadline_exceeded: '本轮达到运行时限，可从断点继续。',
    runtime_step_limit: '本轮达到执行步数上限，可从断点继续。',
    model_or_runtime_failure: '模型或研究服务中断，可从断点继续。',
    api_restarted: '研究服务已重启，可从断点继续。',
    user_started_new_run: '该会话已开始新一轮研究。',
  };
  return reasons[reason];
}

export function applyEvent(turn: Turn, {event_type: type, payload}: AgentEvent): Turn {
  switch (type) {
    case 'message_delta':
      if (payload.safe_completed_answer === false || turn.final) return turn;
      return {...turn, text: (payload.index === 0 ? '' : turn.text) + text(payload.content)};
    case 'done': {
      const reason = text(payload.stop_reason);
      return {...turn, status: reason === 'goal_satisfied' || !reason ? 'completed' : 'stopped',
        reason, text: typeof payload.answer === 'string' ? payload.answer : turn.text, final: true};
    }
    case 'run_recoverable':
      return {...turn, status: 'recoverable', reason: text(payload.reason)};
    case 'agent_progress':
      return {...turn, summary: text(payload.summary)};
    case 'todo_updated': {
      const todos = Array.isArray(payload.todos) ? payload.todos.flatMap((item, index) => {
        const todo = record(item);
        const label = text(todo.content) || text(todo.task) || text(todo.title);
        return label ? [{id: String(index), label, status: text(todo.status) || 'pending'}] : [];
      }) : [];
      return {...turn, todos};
    }
    case 'tool_call_started':
    case 'tool_call_finished':
    case 'tool_error':
    case 'subagent_started':
    case 'subagent_finished': {
      const expert = type.startsWith('subagent');
      const id = text(expert ? payload.task_call_id : payload.tool_call_id);
      if (!id) return turn;
      const name = text(payload.name);
      const status = type.endsWith('_started') ? 'running' : type === 'tool_error' ? 'error'
        : text(record(payload.result).status) || text(payload.status) || 'completed';
      const label = turn.tools[id]?.label || (expert ? '研究专家' : toolLabels[name] || '研究工具');
      return {...turn, tools: {...turn.tools, [id]: {id, label, status}}};
    }
    case 'warning':
      return {...turn, warning: text(payload.message) || '本轮运行有提示，请查看回复。'};
    default:
      return turn;
  }
}

export function restoreTurn(run: RunSnapshot, messages: Message[]) {
  const events = run.events ?? [];
  let cursor = 0;
  let turn = newTurn(run.run_id);
  for (const event of events) {
    if (event.event_id <= cursor) continue;
    turn = applyEvent(turn, event);
    cursor = event.event_id;
  }
  const last = messages.at(-1);
  const persisted = last?.role === 'assistant' && (
    (last.created_at && run.created_at && last.created_at >= run.created_at) ||
    (run.status === 'completed' && last.content === turn.text)
  );
  // A replay may include the recoverable event from a previous attempt of this run.
  return {cursor, turn: {...turn, status: run.status, reason: run.stop_reason ?? '',
    final: run.status !== 'running' && turn.final, persisted: Boolean(persisted)}};
}
