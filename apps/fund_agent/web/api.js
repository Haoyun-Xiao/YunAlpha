const basePath = new URL(".", document.baseURI).pathname.replace(/\/$/, "");
export const endpoint = path => `${basePath}${path.startsWith("/") ? path : `/${path}`}`;

const json = async (path, options = {}) => {
  const response = await fetch(endpoint(path), {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (!response.ok) throw new Error((await response.text()) || `${response.status}`);
  return response.json();
};

export const api = {
  sessions: () => json("/api/v1/sessions"),
  session: id => json(`/api/v1/sessions/${id}`),
  createSession: title => json("/api/v1/sessions", { method: "POST", body: JSON.stringify({ title }) }),
  run: (session_id, message) => json("/api/v1/chat/runs", { method: "POST", body: JSON.stringify({ session_id, message }) }),
  resumeRun: id => json(`/api/v1/runs/${id}/resume`, { method: "POST" }),
  report: body => json("/api/v1/report-jobs", { method: "POST", body: JSON.stringify(body) }),
  reportJob: id => json(`/api/v1/report-jobs/${id}`),
  reportArtifacts: id => json(`/api/v1/report-jobs/${id}/artifacts`),
  reportView: id => endpoint(`/api/v1/report-jobs/${id}/view`),
  artifact: id => endpoint(`/api/v1/artifacts/${id}`),
  trace: id => endpoint(`/api/v1/traces/${id}`),
  resumeReport: id => json(`/api/v1/report-jobs/${id}/resume`, { method: "POST" }),
  cancelReport: id => json(`/api/v1/report-jobs/${id}/cancel`, { method: "POST" }),
  memories: () => json("/api/v1/memories"),
  remember: body => json("/api/v1/memories", { method: "POST", body: JSON.stringify(body) }),
  forget: id => json(`/api/v1/memories/${id}`, { method: "DELETE" }),
  health: () => json("/api/v1/dependencies/health"),
};

export const subscribe = (url, onEvent) => {
  const source = new EventSource(endpoint(url));
  const types = ["run_started", "agent_context_loaded", "agent_progress", "skill_loaded", "message_delta", "todo_updated", "subagent_started", "subagent_finished", "context_compacted", "checkpoint_saved", "run_recoverable", "route_decided", "plan_created", "task_created", "task_updated", "agent_started", "agent_finished", "tool_call_started", "tool_call_finished", "tool_error", "evidence_added", "goal_checked", "report_job_created", "report_stage_started", "report_progress", "model_call_progress", "report_stage_finished", "report_recoverable", "report_published", "warning", "done"];
  types.forEach(type => source.addEventListener(type, event => {
    onEvent(type, JSON.parse(event.data), event.lastEventId);
    if (type === "done") source.close();
  }));
  return source;
};
