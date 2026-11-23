import { api, subscribe } from "./api.js";
import { renderMarkdown } from "./markdown.js";

const $ = selector => document.querySelector(selector);
const state = {
  sessionId: null,
  events: [],
  reportId: null,
  reportDetails: new Map(),
  reportSubscriptions: new Map(),
  sessionRuntime: null,
  currentRunId: null,
  liveMessage: null,
  agentContext: null,
  todos: [],
  subagents: new Map(),
  skills: [],
  stopReason: null,
  chatSubscriptions: new Map(),
  unreadSessions: new Set(),
  renderedEventIds: new Set(),
  sessionLoadToken: 0,
  thinkingMessage: null,
};
const escape = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
const formatMessage = value => renderMarkdown(value);
const terminalReport = new Set(["completed", "completed_with_warnings", "failed", "failed_validation", "cancelled"]);

function toast(message, error = false) {
  const node = $("#toast");
  node.textContent = message;
  node.className = `toast show ${error ? "error" : ""}`;
  setTimeout(() => node.classList.remove("show"), 2800);
}

function closeReportSubscriptions() {
  state.reportSubscriptions.forEach(source => source.close());
  state.reportSubscriptions.clear();
}

function message(role, content, meta = "", uiBlocks = []) {
  const article = document.createElement("article");
  article.className = `message ${role}`;
  const body = role === "user" ? escape(content).replace(/\n/g, "<br>") : formatMessage(content);
  article.innerHTML = `<header><span>${role === "user" ? "YOU" : "AGENT"}</span><small>${escape(meta)}</small></header><div class="${role === "user" ? "" : "markdown-body"}">${body}</div>`;
  $("#messages").append(article);
  uiBlocks.filter(block => block.type === "report_job").forEach(block => {
    ensureChatReport(block);
    hydrateReport(block.job_id, block);
  });
  $("#messages").scrollTop = $("#messages").scrollHeight;
  return article;
}

function ensureLiveMessage() {
  if (state.liveMessage?.isConnected) return state.liveMessage;
  const article = document.createElement("article");
  article.className = "message assistant live-message";
  article.innerHTML = `<header><span>AGENT</span><small>DEEP AGENTS · STREAMING</small></header><div class="live-content markdown-body"></div>`;
  $("#messages").append(article);
  state.liveMessage = article;
  return article;
}

function appendLiveMessage(content) {
  const article = ensureLiveMessage();
  article.querySelector(".live-content").append(document.createTextNode(content));
  $("#messages").scrollTop = $("#messages").scrollHeight;
}

function finalizeLiveMessage(data) {
  const article = state.liveMessage?.isConnected ? state.liveMessage : ensureLiveMessage();
  article.querySelector("header small").textContent = data.stop_reason || "goal_satisfied";
  article.querySelector(".live-content").innerHTML = formatMessage(data.answer || "");
  (data.ui_blocks || []).filter(block => block.type === "report_job").forEach(block => {
    ensureChatReport(block); hydrateReport(block.job_id, block);
  });
  state.liveMessage = null;
}

function updateThinkingMessage(data) {
  if (!state.thinkingMessage?.isConnected) {
    const article = document.createElement("article");
    article.className = "message assistant thinking-message";
    article.innerHTML = `<header><span>AGENT ACTIVITY</span><small>PROCESSING</small></header><div><i></i><section><strong></strong><small></small></section></div>`;
    $("#messages").append(article);
    state.thinkingMessage = article;
  }
  const article = state.thinkingMessage;
  const completed = data.phase === "completed";
  article.classList.toggle("completed", completed);
  article.querySelector("header small").textContent = completed ? "COMPLETED" : "LIVE";
  article.querySelector("strong").textContent = data.summary || "Agent 正在处理";
  const activity = data.last_activity_seconds == null
    ? "等待模型"
    : `最后活动 ${Number(data.last_activity_seconds).toFixed(1)}s 前`;
  article.querySelector("section small").textContent = `已运行 ${Number(data.elapsed_seconds || 0).toFixed(1)}s · ${activity} · 流事件 ${data.stream_events || 0} · 推理 ${data.reasoning_chars || 0} 字符`;
  $("#messages").scrollTop = $("#messages").scrollHeight;
}

function showRecoverable(data) {
  const article = ensureLiveMessage();
  const body = article.querySelector(".live-content");
  body.textContent = data.message || "Agent 运行已中断，可从最后 Checkpoint 继续。";
  const button = document.createElement("button");
  button.className = "resume-run-button";
  button.textContent = "从断点继续";
  button.onclick = async () => {
    try {
      await api.resumeRun(state.currentRunId);
      button.disabled = true;
      $("#runStatus").textContent = "RESUMING";
    } catch (error) { toast(error.message, true); }
  };
  body.append(document.createElement("br"), button);
}

async function loadSessions(selectFirst = false) {
  const sessions = await api.sessions();
  $("#sessionList").innerHTML = sessions.map(item => {
    const active = item.session_id === state.sessionId;
    const unread = !active && state.unreadSessions.has(item.session_id);
    return `<button data-session="${item.session_id}" class="session-item ${active ? "active" : ""} ${unread ? "unread" : ""}"><i></i><span>${escape(item.title)}</span><small>${new Date(item.updated_at).toLocaleDateString("zh-CN")}</small></button>`;
  }).join("") || `<p class="empty">暂无会话</p>`;
  document.querySelectorAll("[data-session]").forEach(button => button.onclick = () => openSession(button.dataset.session));
  if (selectFirst && sessions[0]) await openSession(sessions[0].session_id);
}

async function openSession(id) {
  const loadToken = ++state.sessionLoadToken;
  closeReportSubscriptions();
  state.reportDetails.clear();
  state.sessionId = id;
  state.unreadSessions.delete(id);
  state.liveMessage = null;
  state.thinkingMessage = null;
  state.currentRunId = null;
  state.events = [];
  state.renderedEventIds = new Set();
  state.agentContext = null;
  state.todos = [];
  state.subagents.clear();
  state.skills = [];
  state.stopReason = null;
  $("#traceRail").innerHTML = `<p class="empty">等待一次 Agent Run。</p>`;
  $("#eventCount").textContent = "0 EVENTS";
  $("#routeView").textContent = "尚无 Agent 决策与上下文";
  const session = await api.session(id);
  if (loadToken !== state.sessionLoadToken || state.sessionId !== id) return;
  state.sessionRuntime = session.runtime_kind || "legacy_readonly";
  $("#sessionTitle").textContent = session.title;
  $("#messages").innerHTML = "";
  const messageReportIds = new Set();
  session.messages.forEach(item => message(
    item.role,
    item.content,
    item.metadata?.stop_reason || "",
    (item.metadata?.ui_blocks || []).filter(block => {
      if (block.type === "report_job" && block.job_id) messageReportIds.add(block.job_id);
      return true;
    }),
  ));
  const readonly = state.sessionRuntime !== "deep_agents";
  $("#chatInput").disabled = readonly;
  $("#chatForm").querySelector("button[type=submit]").disabled = readonly;
  $("#chatInput").placeholder = readonly
    ? "历史会话只读，请点击左上角 + 新建 Deep Agents 研究会话"
    : "例如：比较大成高鑫A与景顺长城成长之星的收益来源、风险和经理风格，并给出证据。";
  $("#runStatus").textContent = readonly ? "READ ONLY" : "IDLE";
  if (session.context?.active_report_job_id && !messageReportIds.has(session.context.active_report_job_id)) {
    ensureChatReport({ job_id: session.context.active_report_job_id });
    hydrateReport(session.context.active_report_job_id).catch(() => {});
  }
  if (session.latest_run) {
    state.currentRunId = session.latest_run.run_id;
    (session.latest_run.events || []).forEach(event => {
      addTrace(event.event_type, event.payload, event.event_id, true);
    });
    state.stopReason = session.latest_run.stop_reason || state.stopReason;
    if (session.latest_run.status === "running") {
      watchChatRun(id, session.latest_run.run_id);
      $("#runStatus").textContent = "RUNNING";
    }
  }
  await loadSessions();
  renderAgentInspector();
}

function renderAgentInspector() {
  const context = state.agentContext;
  const funds = context?.active_funds || [];
  const contextRows = [
    funds.length ? `<div class="inspector-chip"><strong>活动基金</strong><br>${funds.map(escape).join("、")}</div>` : "",
    context?.active_manager ? `<div class="inspector-chip"><strong>基金经理</strong><br>${escape(context.active_manager)}</div>` : "",
    context?.active_report_job_id ? `<div class="inspector-chip"><strong>活动报告任务</strong><br>${escape(context.active_report_job_id)}</div>` : "",
    context?.active_report_document_id ? `<div class="inspector-chip"><strong>活动报告文档</strong><br>${escape(context.active_report_document_id)}</div>` : "",
    state.stopReason ? `<div class="inspector-chip"><strong>停止原因</strong><br>${escape(state.stopReason)}</div>` : "",
  ].filter(Boolean);
  $("#contextSummary").innerHTML = contextRows.join("") || `<p class="empty">尚未加载会话上下文</p>`;

  $("#todoSummary").innerHTML = state.todos.map(todo => {
    const label = todo.content || todo.task || todo.title || JSON.stringify(todo);
    return `<div class="inspector-row"><strong>${escape(label)}</strong><small>${escape(todo.status || "pending")}</small></div>`;
  }).join("") || `<p class="empty">当前没有研究计划</p>`;

  const experts = [...state.subagents.values()].map(item => `<div class="inspector-row"><strong>${escape(item.name || item.task_call_id || "固定专家")}</strong><small>${escape(item.status || "running")}${item.duration_ms != null ? ` · ${escape(item.duration_ms)}ms` : ""}</small></div>`);
  const skills = state.skills.map(item => `<div class="inspector-row"><strong>Skill</strong><small>${escape(item.skill || item.source || "已按需加载")}</small></div>`);
  $("#expertSummary").innerHTML = [...experts, ...skills].join("") || `<p class="empty">尚未调用专家或 Skill</p>`;
}

function addTrace(type, data, eventId, replay = false) {
  const eventKey = eventId == null || eventId === "" ? null : String(eventId);
  if (eventKey && state.renderedEventIds.has(eventKey)) return;
  if (eventKey) state.renderedEventIds.add(eventKey);
  if (type === "agent_progress") {
    if (!replay) updateThinkingMessage(data);
    return;
  }
  if (type === "message_delta") {
    if (!replay && data.content) appendLiveMessage(data.content);
    return;
  }
  state.events.push({ type, data, eventId });
  if (type === "agent_context_loaded") state.agentContext = data;
  if (type === "todo_updated") state.todos = data.todos || [];
  if (type === "skill_loaded") state.skills.push(data);
  if (type === "subagent_started") state.subagents.set(data.task_call_id, data);
  if (type === "subagent_finished") state.subagents.set(data.task_call_id, {...state.subagents.get(data.task_call_id), ...data});
  if (type === "done" || type === "run_recoverable") state.stopReason = data.stop_reason || data.reason;
  renderAgentInspector();
  $("#eventCount").textContent = `${state.events.length} EVENTS`;
  const rail = $("#traceRail");
  if (rail.querySelector(".empty")) rail.innerHTML = "";
  const item = document.createElement("article");
  item.className = `trace-event ${type === "done" ? "done" : ""}`;
  const summary = data.name || data.stage || data.stop_reason || data.message || data.feedback || data.fund_name || data.content || "";
  item.innerHTML = `<i></i><div><header><strong>${escape(type)}</strong><small>#${escape(eventId)}</small></header><p>${escape(summary)}</p></div>`;
  item.onclick = () => { $("#routeView").textContent = JSON.stringify(data, null, 2); activateTab("route"); };
  rail.append(item);
  rail.scrollTop = rail.scrollHeight;
  if (type === "route_decided") $("#routeView").textContent = JSON.stringify(data, null, 2);
  if (["agent_context_loaded", "todo_updated", "skill_loaded", "checkpoint_saved"].includes(type)) $("#routeView").textContent = JSON.stringify(data, null, 2);
  if (type === "run_recoverable") { if (!replay) showRecoverable(data); $("#runStatus").textContent = "RECOVERABLE"; }
  if (type === "report_job_created") {
    ensureChatReport(data);
    hydrateReport(data.job_id, data);
  }
  if (type === "done" && data.answer) {
    if (!replay) finalizeLiveMessage(data);
    $("#runStatus").textContent = data.stop_reason.toUpperCase();
  }
}

function markSessionUnread(sessionId) {
  if (!sessionId || sessionId === state.sessionId || state.unreadSessions.has(sessionId)) return;
  state.unreadSessions.add(sessionId);
  loadSessions().catch(() => {});
}

function handleRunEvent(sessionId, runId, type, data, eventId) {
  if (sessionId !== state.sessionId || runId !== state.currentRunId) {
    if (["message_delta", "done", "run_recoverable", "report_job_created", "warning"].includes(type)) {
      markSessionUnread(sessionId);
    }
    return;
  }
  addTrace(type, data, eventId);
}

function watchChatRun(sessionId, runId) {
  if (!sessionId || !runId || state.chatSubscriptions.has(runId)) return;
  const source = subscribe(`/api/v1/runs/${runId}/events`, (type, data, eventId) => {
    handleRunEvent(sessionId, runId, type, data, eventId);
    if (["done", "run_recoverable"].includes(type)) {
      source.close();
      state.chatSubscriptions.delete(runId);
      loadSessions().catch(() => {});
    }
  });
  state.chatSubscriptions.set(runId, { source, sessionId });
}

function activateTab(name) {
  document.querySelectorAll(".tabs button").forEach(node => node.classList.toggle("active", node.dataset.tab === name));
  document.querySelectorAll(".tab-view").forEach(node => node.classList.toggle("active", node.id === `${name}Tab`));
}

function ensureChatReport(block) {
  if (!block?.job_id || document.querySelector(`[data-report-owner="${block.job_id}"]`)) return;
  const article = document.createElement("article");
  article.className = "message assistant report-message";
  article.dataset.reportOwner = block.job_id;
  article.innerHTML = `<header><span>REPORT WORKFLOW</span><small>${escape(block.job_id)}</small></header><div class="report-card-shell" data-report-job="${block.job_id}"><p class="empty">正在读取报告任务…</p></div>`;
  $("#messages").append(article);
}

function ensureInspectorReport(jobId) {
  state.reportId = jobId;
  if (!$("#reportView").querySelector(`[data-report-job="${jobId}"]`)) {
    $("#reportView").innerHTML = `<div data-report-job="${jobId}"><p class="empty">正在读取报告任务…</p></div>`;
  }
}

function artifactActions(jobId, artifacts) {
  const newest = [...artifacts].reverse();
  const markdown = newest.find(item => item.kind === "report_markdown");
  const docx = newest.find(item => item.kind === "report_docx");
  const html = newest.find(item => item.kind === "report_html");
  return [
    html ? `<a class="report-action primary" target="_blank" rel="noopener" href="${api.reportView(jobId)}">打开网页报告 ↗</a>` : "",
    docx ? `<a class="report-action" href="${api.artifact(docx.artifact_id)}">下载 Word</a>` : "",
    markdown ? `<a class="report-action" href="${api.artifact(markdown.artifact_id)}">下载 Markdown</a>` : "",
  ].join("");
}

function reportMarkup(job, artifacts) {
  const pct = Math.round(Number(job.progress || 0) * 100);
  const request = job.request || {};
  const name = job.fund_name || job.fundName || request.fund_query || job.fund_symbol || job.job_id;
  const warnings = Array.isArray(job.warnings) ? job.warnings : [];
  const errors = Array.isArray(job.errors) ? job.errors : [];
  const canCancel = !terminalReport.has(job.status) && job.status !== "cancelling";
  const canResume = ["recoverable", "failed", "failed_validation"].includes(job.status);
  const modelProgress = job.model_progress;
  const phaseLabels = {
    request_started: "请求已发送",
    waiting_response: "等待模型响应",
    output_limit_retry: "输出额度不足，正在重试当前节点",
    thinking: "模型正在思考",
    writing: "模型正在写作",
    completed: "本次模型调用完成",
  };
  const liveProgress = modelProgress ? `<section class="model-live-progress">
    <header><span>MODEL LIVE</span><b>${escape(phaseLabels[modelProgress.phase] || modelProgress.phase)}</b></header>
    <p>${escape(modelProgress.operation || modelProgress.role || "model")}</p>
    <dl><div><dt>已运行</dt><dd>${escape(modelProgress.elapsed_seconds ?? 0)}s</dd></div><div><dt>最后活动</dt><dd>${modelProgress.last_activity_seconds == null ? "等待首包" : `${escape(modelProgress.last_activity_seconds)}s 前`}</dd></div><div><dt>流事件</dt><dd>${escape(modelProgress.stream_events ?? 0)}</dd></div><div><dt>思考 / 正文</dt><dd>${escape(modelProgress.reasoning_chars ?? 0)} / ${escape(modelProgress.content_chars ?? 0)} 字符</dd></div></dl>
  </section>` : "";
  return `<article class="report-card">
    <header><span>${escape(name)}</span><b class="status-${escape(job.status)}">${escape(job.status || "accepted")}</b></header>
    <small class="job-id">${escape(job.job_id)}</small>
    <div class="progress"><i style="width:${pct}%"></i></div>
    <div class="report-stage"><strong>${pct}%</strong><span>${escape(job.current_stage || "queued")}</span></div>
    <dl><div><dt>截至日期</dt><dd>${escape(request.as_of || job.as_of || "—")}</dd></div><div><dt>尝试次数</dt><dd>${escape(job.attempt ?? 0)}</dd></div><div><dt>最后活动</dt><dd>${job.updated_at ? new Date(job.updated_at).toLocaleTimeString("zh-CN", {hour:"2-digit", minute:"2-digit"}) : "—"}</dd></div></dl>
    ${liveProgress}
    ${warnings.length ? `<p class="warning">${warnings.map(escape).join(" · ")}</p>` : ""}
    ${errors.length ? `<p class="report-error">${errors.map(escape).join(" · ")}</p>` : ""}
    <div class="report-actions">${artifactActions(job.job_id, artifacts)}
      ${job.trace_id ? `<a class="report-action" target="_blank" rel="noopener" href="${api.trace(job.trace_id)}">查看 Trace</a>` : ""}
      ${canResume ? `<button data-resume-report="${job.job_id}">从断点继续</button>` : ""}
      ${canCancel ? `<button class="danger" data-cancel-report="${job.job_id}">取消任务</button>` : ""}
    </div>
  </article>`;
}

function paintReport(jobId) {
  const details = state.reportDetails.get(jobId);
  if (!details) return;
  document.querySelectorAll(`[data-report-job="${jobId}"]`).forEach(node => {
    node.innerHTML = reportMarkup(details.job, details.artifacts || []);
    node.querySelectorAll("[data-resume-report]").forEach(button => button.onclick = async () => {
      await api.resumeReport(jobId); await hydrateReport(jobId); watchReport(jobId);
    });
    node.querySelectorAll("[data-cancel-report]").forEach(button => button.onclick = async () => {
      await api.cancelReport(jobId); await hydrateReport(jobId);
    });
  });
}

async function hydrateReport(jobId, seed = {}) {
  if (!jobId) return;
  const previous = state.reportDetails.get(jobId)?.job || {};
  const job = await api.reportJob(jobId);
  let artifacts = state.reportDetails.get(jobId)?.artifacts || [];
  if (job.artifact_ids?.length || terminalReport.has(job.status)) {
    artifacts = (await api.reportArtifacts(jobId)).artifacts || [];
  }
  state.reportDetails.set(jobId, { job: { ...previous, ...seed, ...job }, artifacts });
  paintReport(jobId);
  if (!terminalReport.has(job.status)) watchReport(jobId);
}

function watchReport(jobId) {
  if (!jobId || state.reportSubscriptions.has(jobId)) return;
  const source = subscribe(`/api/v1/report-jobs/${jobId}/events`, async (type, data, id) => {
    addTrace(type, data, id);
    if (type === "model_call_progress") {
      const details = state.reportDetails.get(jobId);
      if (details) {
        details.job.model_progress = data;
        paintReport(jobId);
      }
      return;
    }
    try { await hydrateReport(jobId, type === "report_published" ? data : {}); }
    catch (error) { toast(error.message, true); }
    if (type === "done") state.reportSubscriptions.delete(jobId);
  });
  state.reportSubscriptions.set(jobId, source);
}

async function runChat(event) {
  event.preventDefault();
  const input = $("#chatInput");
  const content = input.value.trim();
  if (!content) return;
  if (state.sessionId && state.sessionRuntime !== "deep_agents") { toast("历史会话只读，请新建研究会话", true); return; }
  if (!state.sessionId) {
    const created = await api.createSession("基金研究 · " + content.slice(0, 18));
    state.sessionId = created.session_id;
    state.sessionRuntime = created.runtime_kind || "deep_agents";
    await loadSessions();
  }
  const runSessionId = state.sessionId;
  message("user", content);
  input.value = "";
  $("#runStatus").textContent = "RUNNING";
  state.events = [];
  state.renderedEventIds = new Set();
  state.thinkingMessage = null;
  $("#traceRail").innerHTML = "";
  try {
    const result = await api.run(runSessionId, content);
    if (state.sessionId === runSessionId) state.currentRunId = result.run_id;
    watchChatRun(runSessionId, result.run_id);
  } catch (error) { toast(error.message, true); }
}

async function createReport() {
  try {
    const body = { fund_query: $("#reportFund").value.trim(), as_of: $("#reportDate").value, web_mode: $("#webMode").value, output_formats: ["markdown", "html", "docx"] };
    const result = await api.report(body);
    ensureInspectorReport(result.job_id);
    activateTab("report");
    await hydrateReport(result.job_id, result);
    watchReport(result.job_id);
    toast(`任务已创建：${result.job_id.slice(-8)}`);
  } catch (error) { toast(error.message, true); }
}

async function loadMemories() {
  const records = await api.memories();
  $("#memoryList").innerHTML = records.map(item => `<article class="memory-item"><span>${escape(item.kind)}</span><p>${escape(item.content)}</p><button data-forget="${item.memory_id}">删除</button></article>`).join("") || `<p class="empty">没有已确认记忆</p>`;
  document.querySelectorAll("[data-forget]").forEach(button => button.onclick = async () => { await api.forget(button.dataset.forget); await loadMemories(); });
}

document.querySelectorAll(".tabs button").forEach(button => button.onclick = () => { activateTab(button.dataset.tab); if (button.dataset.tab === "memory") loadMemories(); });
$("#chatForm").onsubmit = runChat;
let chatInputComposing = false;
$("#chatInput").addEventListener("compositionstart", () => { chatInputComposing = true; });
$("#chatInput").addEventListener("compositionend", () => { chatInputComposing = false; });
$("#chatInput").onkeydown = event => {
  if (event.key !== "Enter" || event.shiftKey) return;
  if (event.isComposing || chatInputComposing || event.keyCode === 229) return;
  event.preventDefault();
  $("#chatForm").requestSubmit();
};
$("#newSession").onclick = async () => { const session = await api.createSession(); await openSession(session.session_id); };
$("#createReport").onclick = createReport;
$("#saveMemory").onclick = async () => { try { await api.remember({kind: $("#memoryKind").value, content: $("#memoryContent").value, scope: "global"}); $("#memoryContent").value = ""; await loadMemories(); toast("记忆已保存"); } catch (error) { toast(error.message, true); } };
$("#healthButton").onclick = async () => { const data = await api.health(); toast(data.healthy ? "核心依赖正常" : "依赖不完整", !data.healthy); };
$("#reportDate").value = new Date().toISOString().slice(0, 10);
loadSessions(true).catch(error => toast(error.message, true));
