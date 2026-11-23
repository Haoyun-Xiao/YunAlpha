const base = '/agent-runtime/api/v1';
const idPart = encodeURIComponent;

export type SessionSummary = {
  session_id: string;
  title: string;
  updated_at: string;
  runtime_kind: string;
};

export type ReportRef = {job_id: string; type?: 'report_job' | 'industry_job'; title?: string; fund_name?: string; fund_symbol?: string};
export type ResearchSkill = {id: string; name: string; description: string; instructions: string; starter_prompt?: string; tools?: string[]; references?: {name: string; content: string}[]};
export type IndustryJob = {job_id: string; status: string; stage: string; progress: number; anchor: string;
  as_of?: string; report_id?: string; error?: string; warnings?: string[]; view_url?: string;
  artifacts?: {label: string; url: string}[]};
export type Message = {
  message_id: string;
  created_at?: string;
  role: string;
  content: string;
  metadata?: {ui_blocks?: unknown};
};

export type AgentEvent = {event_id: number; event_type: string; payload: Record<string, unknown>};
export type RunSnapshot = {run_id: string; status: string; stop_reason?: string | null; created_at?: string; events?: AgentEvent[]};
export type Session = SessionSummary & {
  messages: Message[];
  latest_run: RunSnapshot | null;
  context?: {active_report_job_id?: string | null};
};

export type ReportJob = {
  job_id: string;
  status: string;
  current_stage: string;
  progress: number;
  request: {fund_query: string; as_of: string};
  attempt: number;
  warnings: string[];
  errors: string[];
  artifact_ids: string[];
};

export type ReportArtifact = {artifact_id: string; kind: string};

export class AgentError extends Error {
  constructor(readonly status: number) {
    super(`Agent 服务请求失败（${status}）`);
  }
}

async function json<T>(path: string, signal: AbortSignal, method = 'GET', body?: object): Promise<T> {
  const response = await fetch(base + path, {
    method, signal, cache: 'no-store',
    ...(body ? {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)} : {}),
  });
  if (!response.ok) throw new AgentError(response.status);
  return response.json();
}

export const agentApi = {
  skills: (signal: AbortSignal) => json<{items: ResearchSkill[]}>('/skills', signal),
  industry: (id: string, signal: AbortSignal) => json<IndustryJob>(`/industry-jobs/${idPart(id)}`, signal),
  industryAction: (id: string, action: 'resume' | 'cancel', signal: AbortSignal) =>
    json<unknown>(`/industry-jobs/${idPart(id)}/${action}`, signal, 'POST'),
  health: (signal: AbortSignal) => json<{model_key: boolean}>('/dependencies/health', signal),
  sessions: (signal: AbortSignal) => json<SessionSummary[]>('/sessions', signal),
  session: (id: string, signal: AbortSignal) => json<Session>(`/sessions/${idPart(id)}`, signal),
  create: (title: string, signal: AbortSignal) => json<Session>('/sessions', signal, 'POST', {title}),
  send: (session_id: string, message: string, signal: AbortSignal) =>
    json<{run_id: string}>('/chat/runs', signal, 'POST', {session_id, message}),
  run: (id: string, signal: AbortSignal) => json<RunSnapshot>(`/runs/${idPart(id)}`, signal),
  resumeRun: (id: string, signal: AbortSignal) =>
    json<{run_id: string}>(`/runs/${idPart(id)}/resume`, signal, 'POST'),
  report: (id: string, signal: AbortSignal) => json<ReportJob>(`/report-jobs/${idPart(id)}`, signal),
  artifacts: (id: string, signal: AbortSignal) =>
    json<{artifacts: ReportArtifact[]}>(`/report-jobs/${idPart(id)}/artifacts`, signal),
  resumeReport: (id: string, signal: AbortSignal) =>
    json<unknown>(`/report-jobs/${idPart(id)}/resume`, signal, 'POST'),
  runEvents: (id: string) => `/runs/${idPart(id)}/events`,
  reportEvents: (id: string) => `/report-jobs/${idPart(id)}/events`,
  reportView: (id: string) => `${base}/report-jobs/${idPart(id)}/view`,
  artifact: (id: string) => `${base}/artifacts/${idPart(id)}`,
};

export function sessionUrl(id: string) {
  return `/?view=session&session=${idPart(id)}`;
}

export type Connection = 'connecting' | 'connected' | 'reconnecting';

function pause(delay: number, signal: AbortSignal) {
  return new Promise<void>(resolve => {
    if (signal.aborted) { resolve(); return; }
    const timer = window.setTimeout(finish, delay);
    signal.addEventListener('abort', finish, {once: true});
    function finish() {
      window.clearTimeout(timer);
      signal.removeEventListener('abort', finish);
      resolve();
    }
  });
}

// The server supplies a numeric id on every event, shared across its streams.
async function readStream(response: Response, onEvent: (event: AgentEvent) => boolean) {
  const reader = response.body!.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = '';
  let id = 0;
  let type = '';
  let data: string[] = [];
  try {
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) return;
      buffer += chunk.value;
      let newline = buffer.indexOf('\n');
      while (newline >= 0) {
        const line = buffer.slice(0, newline).replace(/\r$/, '');
        buffer = buffer.slice(newline + 1);
        if (!line) {
          if (id > 0 && type && data.length && !onEvent({event_id: id, event_type: type, payload: JSON.parse(data.join('\n'))})) return;
          id = 0; type = ''; data = [];
        } else if (!line.startsWith(':')) {
          const colon = line.indexOf(':');
          const field = colon === -1 ? line : line.slice(0, colon);
          const value = colon === -1 ? '' : line.slice(colon + 1).replace(/^ /, '');
          if (field === 'id' && /^\d+$/.test(value)) id = Number(value);
          if (field === 'event') type = value;
          if (field === 'data') data.push(value);
        }
        newline = buffer.indexOf('\n');
      }
    }
  } finally {
    await reader.cancel().catch(() => {});
  }
}

export async function followEvents(path: string, options: {
  signal: AbortSignal;
  cursor: number;
  onEvent: (event: AgentEvent) => boolean;
  onConnection: (state: Connection) => void;
  onReconnect?: () => Promise<boolean>;
}) {
  const {signal, onEvent, onConnection, onReconnect} = options;
  let cursor = options.cursor;
  let delay = 700;
  let keepReading = true;
  onConnection('connecting');
  while (!signal.aborted && keepReading) {
    try {
      const headers: Record<string, string> = {Accept: 'text/event-stream'};
      if (cursor) headers['Last-Event-ID'] = String(cursor);
      const response = await fetch(base + path, {headers, cache: 'no-store', signal});
      if (!response.ok || !response.body || !response.headers.get('content-type')?.includes('text/event-stream')) {
        await response.body?.cancel();
        throw new AgentError(response.status);
      }
      if (signal.aborted) return;
      onConnection('connected');
      await readStream(response, event => {
        if (signal.aborted) return false;
        if (event.event_id <= cursor) return true;
        keepReading = onEvent(event);
        cursor = event.event_id;
        delay = 700;
        return keepReading;
      });
    } catch {
      if (signal.aborted) return;
    }
    if (!keepReading || signal.aborted) return;
    onConnection('reconnecting');
    await pause(delay, signal);
    if (signal.aborted) return;
    if (onReconnect && !(await onReconnect())) return;
    delay = Math.min(delay * 2, 8000);
  }
}
