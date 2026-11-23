(function () {
  window.AI_ASSISTANT_SIDEBAR_VERSION = "20260625-chat-ui";
  const URL_PREFIX = (window.AI_ASSISTANT_URL_PREFIX || "").replace(/\/+$/, "");
  const API_BASE = window.AI_ASSISTANT_API_BASE || ((window.location.origin || "http://127.0.0.1:8787") + URL_PREFIX);
  const standalone = Boolean(window.AI_ASSISTANT_STANDALONE);
  let context = window.FUND_CONTEXT || {};
  const history = [];
  const REQUEST_TIMEOUT_MS = 180000;
  const MAX_ATTACHMENTS = 5;
  const MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024;
  const KB_ONLY_STORAGE_KEY = "ai_assistant_knowledge_mode";
  const ACCEPTED_ATTACHMENT_EXTENSIONS = [
    ".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".html", ".log",
    ".doc", ".docx", ".pdf", ".xls", ".xlsx", ".ppt", ".pptx"
  ];

  function ensureCriticalStyles() {
    if (document.getElementById("ai-assistant-critical-style")) return;
    const style = document.createElement("style");
    style.id = "ai-assistant-critical-style";
    style.textContent = `
      .ai-assistant-tab {
        position: fixed !important;
        right: 0 !important;
        left: auto !important;
        top: 36% !important;
        z-index: 2147483000 !important;
        display: flex !important;
        align-items: center !important;
        gap: 8px !important;
        padding: 14px 8px !important;
        writing-mode: vertical-rl !important;
        color: #3d5a98 !important;
        background: #fff !important;
        border: 1px solid #d4c4a8 !important;
        border-right: 0 !important;
        border-radius: 6px 0 0 6px !important;
        box-shadow: 0 2px 10px rgba(44, 36, 22, 0.12) !important;
        cursor: pointer !important;
        font-size: 15px !important;
        font-weight: 700 !important;
        letter-spacing: 1px !important;
      }
      .ai-assistant-tab span:first-child {
        writing-mode: horizontal-tb !important;
        transform: rotate(180deg) !important;
      }
      .ai-assistant-drawer {
        z-index: 2147483001 !important;
      }
    `;
    document.head.appendChild(style);
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  }

  function escapeHtml(text) {
    return String(text || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function normalizeMarkdown(markdown) {
    return String(markdown || "")
      .replace(/\r\n/g, "\n")
      .replace(/[ \t]+\n/g, "\n")
      .replace(/\n{3,}/g, "\n\n")
      .replace(/^\s*[*＊]\s*$/gm, "")
      .trim();
  }

  function renderInline(text) {
    const codeSpans = [];
    let html = escapeHtml(text).replace(/`([^`]+)`/g, function (_, code) {
      const token = "\u0000CODE" + codeSpans.length + "\u0000";
      codeSpans.push("<code>" + escapeHtml(code) + "</code>");
      return token;
    });
    html = html.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/\*([^*\n]+)\*/g, "<em>$1</em>");
    html = html.replace(/\u0000CODE(\d+)\u0000/g, function (_, idx) {
      return codeSpans[Number(idx)] || "";
    });
    return html;
  }

  function isTableSeparator(line) {
    const trimmed = line.trim();
    if (!trimmed.includes("|")) return false;
    return /^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(trimmed);
  }

  function splitTableRow(line) {
    let value = line.trim();
    if (value.startsWith("|")) value = value.slice(1);
    if (value.endsWith("|")) value = value.slice(0, -1);
    return value.split("|").map((cell) => cell.trim());
  }

  function renderTable(lines, start) {
    if (start + 1 >= lines.length || !isTableSeparator(lines[start + 1])) return null;
    const headers = splitTableRow(lines[start]);
    const rows = [];
    let index = start + 2;
    while (index < lines.length && lines[index].includes("|") && lines[index].trim()) {
      rows.push(splitTableRow(lines[index]));
      index += 1;
    }
    if (!headers.length || !rows.length) return null;
    const thead = "<thead><tr>" + headers.map((cell) => "<th>" + renderInline(cell) + "</th>").join("") + "</tr></thead>";
    const tbody = "<tbody>" + rows.map((row) => {
      return "<tr>" + headers.map((_h, idx) => "<td>" + renderInline(row[idx] || "") + "</td>").join("") + "</tr>";
    }).join("") + "</tbody>";
    return { html: '<div class="ai-table-wrap"><table>' + thead + tbody + "</table></div>", next: index };
  }

  function renderParagraph(lines) {
    return "<p>" + renderInline(lines.join(" ")) + "</p>";
  }

  function renderMarkdown(markdown) {
    const source = normalizeMarkdown(markdown);
    if (!source) return "";
    const lines = source.split("\n");
    const blocks = [];
    let paragraph = [];
    let list = [];
    let inCode = false;
    let codeLines = [];

    function flushParagraph() {
      if (paragraph.length) {
        blocks.push(renderParagraph(paragraph));
        paragraph = [];
      }
    }

    function flushList() {
      if (list.length) {
        blocks.push("<ul>" + list.map((item) => "<li>" + renderInline(item) + "</li>").join("") + "</ul>");
        list = [];
      }
    }

    for (let i = 0; i < lines.length; i += 1) {
      const line = lines[i];
      if (line.trim().startsWith("```")) {
        if (inCode) {
          blocks.push("<pre><code>" + escapeHtml(codeLines.join("\n").trim()) + "</code></pre>");
          codeLines = [];
          inCode = false;
        } else {
          flushParagraph();
          flushList();
          inCode = true;
        }
        continue;
      }
      if (inCode) {
        codeLines.push(line);
        continue;
      }
      if (!line.trim()) {
        flushParagraph();
        flushList();
        continue;
      }

      const table = renderTable(lines, i);
      if (table) {
        flushParagraph();
        flushList();
        blocks.push(table.html);
        i = table.next - 1;
        continue;
      }

      const heading = line.match(/^(#{1,3})\s+(.+)$/);
      if (heading) {
        flushParagraph();
        flushList();
        const tag = heading[1].length >= 3 ? "h3" : "h2";
        blocks.push("<" + tag + ">" + renderInline(heading[2]) + "</" + tag + ">");
        continue;
      }

      const bullet = line.match(/^\s*[-*]\s+(.+)$/);
      if (bullet) {
        flushParagraph();
        list.push(bullet[1]);
        continue;
      }

      flushList();
      paragraph.push(line.trim());
    }
    flushParagraph();
    flushList();
    if (inCode) blocks.push("<pre><code>" + escapeHtml(codeLines.join("\n").trim()) + "</code></pre>");
    return blocks.join("");
  }

  function setMessageContent(msg, text, markdown) {
    const body = msg.querySelector(".ai-msg-body");
    if (!body) return;
    if (markdown) body.innerHTML = renderMarkdown(text);
    else body.textContent = text;
  }

  function isNearBottom(container) {
    return container.scrollHeight - container.scrollTop - container.clientHeight < 56;
  }

  function scrollToBottom(container) {
    container.scrollTop = container.scrollHeight;
  }

  function appendMessage(container, role, text, citations, markdown, forceScroll) {
    const shouldScroll = forceScroll || isNearBottom(container);
    const msg = el("div", "ai-msg " + role);
    const body = el("div", "ai-msg-body");
    msg.appendChild(body);
    setMessageContent(msg, text || "", Boolean(markdown));
    container.appendChild(msg);
    if (shouldScroll) scrollToBottom(container);
    return msg;
  }

  function appendStatus(msg, text) {
    let status = msg.querySelector(".ai-status");
    if (!status) {
      status = el("div", "ai-status");
      const body = msg.querySelector(".ai-msg-body");
      (body || msg).appendChild(status);
    }
    status.textContent = text;
  }

  function clearStatus(msg) {
    const status = msg.querySelector(".ai-status");
    if (status) status.remove();
  }

  function createDrawer() {
    const drawer = el("aside", "ai-assistant-drawer" + (standalone ? " standalone" : ""));
    const head = el("div", "ai-assistant-head");
    head.appendChild(el("div", "ai-assistant-title", "与AI进一步展开对话"));
    const headActions = el("div", "ai-assistant-head-actions");
    const clear = el("button", "ai-assistant-clear");
    clear.type = "button";
    clear.title = "清除上下文";
    clear.setAttribute("aria-label", "清除上下文");
    clear.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15.4 3.8 20.2 8.6 18.1 10.7 13.3 5.9 15.4 3.8Z"/><path d="M12.4 6.8 17.2 11.6 9.7 19.1C8.7 20.1 7.4 20.7 6 20.7H3.4V18.1C3.4 16.7 4 15.4 5 14.4L12.4 6.8Z"/><path d="M4.4 18.2H10.4"/></svg>';
    headActions.appendChild(clear);
    if (!standalone) {
      const close = el("button", "ai-assistant-close", "×");
      close.type = "button";
      close.title = "关闭";
      close.addEventListener("click", () => drawer.classList.remove("open"));
      headActions.appendChild(close);
    }
    head.appendChild(headActions);

    const messages = el("div", "ai-assistant-messages");
    messages.appendChild(el("div", "ai-assistant-placeholder", "开始对话，AI 将为您提供专业的基金分析建议..."));

    const controls = el("div", "ai-assistant-controls");
    const modelRow = el("div", "ai-model-row");
    modelRow.appendChild(el("span", "", "模型:"));
    const select = el("select", "");
    const option = el("option", "", "DeepSeek v4 flash");
    option.value = "deepseek-v4-flash";
    select.appendChild(option);
    const proOption = el("option", "", "DeepSeek v4 pro");
    proOption.value = "deepseek-v4-pro";
    select.appendChild(proOption);
    modelRow.appendChild(select);
    const kbOnlyLabel = el("label", "ai-thinking-toggle ai-kb-only-toggle");
    const kbOnlyToggle = document.createElement("input");
    kbOnlyToggle.type = "checkbox";
    try {
      kbOnlyToggle.checked = window.localStorage.getItem(KB_ONLY_STORAGE_KEY) === "kb_only";
    } catch (err) {
      kbOnlyToggle.checked = false;
    }
    const kbOnlyText = el("span", "", "限知识库");
    kbOnlyLabel.appendChild(kbOnlyToggle);
    kbOnlyLabel.appendChild(kbOnlyText);
    modelRow.appendChild(kbOnlyLabel);
    const thinkingLabel = el("label", "ai-thinking-toggle");
    const thinkingToggle = document.createElement("input");
    thinkingToggle.type = "checkbox";
    thinkingToggle.checked = true;
    const thinkingText = el("span", "", "思考模式");
    thinkingLabel.appendChild(thinkingToggle);
    thinkingLabel.appendChild(thinkingText);
    modelRow.appendChild(thinkingLabel);

    const inputRow = el("div", "ai-input-row");
    const input = el("textarea", "ai-input");
    input.placeholder = context.fund_name ? "你好，我是基金配置助手..." : "你好，我是基金知识库助手...";
    const attachmentInput = document.createElement("input");
    attachmentInput.type = "file";
    attachmentInput.multiple = true;
    attachmentInput.accept = ACCEPTED_ATTACHMENT_EXTENSIONS.join(",");
    attachmentInput.className = "ai-file-input";
    const attachmentList = el("div", "ai-attachments");
    const buttons = el("div", "ai-button-col");
    const add = el("button", "ai-icon-btn", "+");
    add.type = "button";
    add.title = "添加文件";
    const send = el("button", "ai-icon-btn primary", "↑");
    send.type = "button";
    send.title = "发送";
    buttons.appendChild(add);
    buttons.appendChild(send);
    inputRow.appendChild(input);
    inputRow.appendChild(buttons);
    controls.appendChild(modelRow);
    controls.appendChild(attachmentInput);
    controls.appendChild(attachmentList);
    controls.appendChild(inputRow);

    let activeController = null;
    let activeTimeout = null;
    let attachments = [];
    let conversationAttachments = [];
    let activeContext = {};

    function restorePlaceholder() {
      messages.innerHTML = "";
      messages.appendChild(el("div", "ai-assistant-placeholder", "开始对话，AI 将为您提供专业的基金分析建议..."));
    }

    function updateConversationContext(nextContext) {
      context = nextContext || window.FUND_CONTEXT || {};
      if (activeController) activeController.abort();
      activeContext = {};
      input.placeholder = context.fund_name ? "你好，我是基金配置助手..." : "你好，我是基金知识库助手...";
      clearActiveRequest();
    }

    window.addEventListener("fund-context-change", (event) => {
      updateConversationContext(event.detail || window.FUND_CONTEXT || {});
    });

    function setRunning(running) {
      add.disabled = running;
      send.textContent = running ? "×" : "↑";
      send.classList.toggle("danger", running);
      send.title = running ? "停止当前回答" : "发送";
    }

    function clearActiveRequest() {
      if (activeTimeout) window.clearTimeout(activeTimeout);
      activeTimeout = null;
      activeController = null;
      setRunning(false);
    }

    function resetConversation() {
      if (activeController) activeController.abort();
      clearActiveRequest();
      history.splice(0, history.length);
      attachments = [];
      conversationAttachments = [];
      activeContext = {};
      attachmentInput.value = "";
      input.value = "";
      renderAttachments();
      restorePlaceholder();
      input.focus();
    }

    function fetchWithTimeout(url, options) {
      if (activeTimeout) window.clearTimeout(activeTimeout);
      const controller = new AbortController();
      activeController = controller;
      activeTimeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
      return fetch(url, { ...options, signal: controller.signal });
    }

    function renderAttachments() {
      attachmentList.innerHTML = "";
      if (!attachments.length) return;
      for (const item of attachments) {
        const chip = el("button", "ai-attachment-chip");
        chip.type = "button";
        chip.title = "移除文件";
        chip.textContent = item.name + " ×";
        chip.addEventListener("click", () => {
          attachments = attachments.filter((att) => att.id !== item.id);
          renderAttachments();
        });
        attachmentList.appendChild(chip);
      }
    }

    function canReadFile(file) {
      const lower = file.name.toLowerCase();
      return ACCEPTED_ATTACHMENT_EXTENSIONS.some((ext) => lower.endsWith(ext));
    }

    function readFileAsBase64(file) {
      return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => {
          const result = String(reader.result || "");
          resolve(result.includes(",") ? result.split(",").pop() : result);
        };
        reader.onerror = () => reject(reader.error || new Error("文件读取失败"));
        reader.readAsDataURL(file);
      });
    }

    async function addFiles(files) {
      const selected = Array.from(files || []);
      for (const file of selected) {
        if (attachments.length >= MAX_ATTACHMENTS) {
          appendMessage(messages, "error", "最多只能添加 " + MAX_ATTACHMENTS + " 个文件。");
          break;
        }
        if (!canReadFile(file)) {
          appendMessage(messages, "error", "暂不支持该文件类型：" + file.name);
          continue;
        }
        if (file.size > MAX_ATTACHMENT_BYTES) {
          appendMessage(messages, "error", file.name + " 超过 10MB，暂不添加。");
          continue;
        }
        const dataBase64 = await readFileAsBase64(file);
        attachments.push({
          id: String(Date.now()) + Math.random(),
          name: file.name,
          type: file.type || "text/plain",
          size: file.size,
          data_base64: dataBase64
        });
      }
      attachmentInput.value = "";
      renderAttachments();
    }

    async function submit() {
      const message = input.value.trim();
      if (activeController) {
        activeController.abort();
        return;
      }
      if ((!message && !attachments.length) || send.disabled) return;
      const placeholder = messages.querySelector(".ai-assistant-placeholder");
      if (placeholder) placeholder.remove();
      const sentAttachments = attachments.slice();
      const displayMessage = message || "请分析附件";
      const attachmentNames = sentAttachments.map((item) => item.name).join("、");
      appendMessage(
        messages,
        "user",
        attachmentNames ? displayMessage + "\n\n附件：" + attachmentNames : displayMessage,
        null,
        false,
        true
      );
      history.push({ role: "user", content: attachmentNames ? displayMessage + "（附件：" + attachmentNames + "）" : displayMessage });
      input.value = "";
      attachments = [];
      renderAttachments();
      setRunning(true);

      const requestPayload = {
        message: displayMessage,
        fund_id: context.fund_id || null,
        history: history.slice(-8),
        model: select.value,
        knowledge_mode: kbOnlyToggle.checked ? "kb_only" : "normal",
        thinking_mode: thinkingToggle.checked ? "enabled" : "disabled",
        attachments: sentAttachments.map(({ name, type, size, data_base64 }) => ({ name, type, size, data_base64 })),
        conversation_attachments: conversationAttachments,
        active_context: activeContext
      };

      try {
        const resp = await fetchWithTimeout(API_BASE + "/api/chat/stream", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(requestPayload)
        });
        if (!resp.ok || !resp.body) throw new Error("请求失败");

        const assistantMsg = appendMessage(messages, "assistant", "", null, false, true);
        appendStatus(assistantMsg, "正在思考...");
        const decoder = new TextDecoder();
        const reader = resp.body.getReader();
        let buffer = "";
        let answer = "";
        let responseMeta = {};
        let completed = false;
        let streamError = "";

        while (!completed) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const events = buffer.split("\n\n");
          buffer = events.pop() || "";

          for (const eventText of events) {
            const lines = eventText.split("\n");
            const type = (lines.find((line) => line.startsWith("event:")) || "event: message").slice(6).trim();
            const dataLine = lines.find((line) => line.startsWith("data:"));
            if (!dataLine) continue;
            const data = JSON.parse(dataLine.slice(5).trim());

            if (type === "meta") {
              responseMeta = data || {};
              conversationAttachments = data.conversation_attachments || conversationAttachments;
              activeContext = data.active_context || activeContext;
              console.debug("[AI Assistant]", data.intent, data.context_mode, data.skill);
            } else if (type === "status") {
              if (!answer) appendStatus(assistantMsg, data || "正在思考...");
            } else if (type === "delta") {
              const shouldScroll = isNearBottom(messages);
              answer += data;
              clearStatus(assistantMsg);
              setMessageContent(assistantMsg, answer, true);
              if (shouldScroll) scrollToBottom(messages);
            } else if (type === "done") {
              completed = true;
              clearStatus(assistantMsg);
            } else if (type === "error") {
              streamError = data || "请求失败";
              completed = true;
              clearStatus(assistantMsg);
              appendMessage(messages, "error", "AI 助手暂时无法回答：" + streamError);
            }
          }
        }

        if (streamError) return;
        if (!answer) throw new Error("模型没有返回内容");
        history.push({
          role: "assistant",
          content: answer,
          intent: responseMeta.intent,
          fund_ids: responseMeta.fund_ids,
          metric_terms: responseMeta.metric_terms
        });
      } catch (err) {
        if (err && err.name === "AbortError") {
          appendMessage(messages, "error", "本次回答已停止或超时，请重新发送。");
          return;
        }
        try {
          const resp = await fetchWithTimeout(API_BASE + "/api/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(requestPayload)
          });
          const data = await resp.json();
          if (!resp.ok || data.error) throw new Error(data.error || "请求失败");
          appendMessage(messages, "assistant", data.answer || "", null, true);
          conversationAttachments = data.conversation_attachments || conversationAttachments;
          activeContext = data.active_context || activeContext;
          history.push({
            role: "assistant",
            content: data.answer || "",
            intent: data.intent,
            fund_ids: data.fund_ids,
            metric_terms: data.metric_terms
          });
        } catch (fallbackErr) {
          const reason = fallbackErr && fallbackErr.name === "AbortError" ? "请求超时或已停止" : fallbackErr.message;
          appendMessage(messages, "error", "AI 助手暂时无法回答：" + reason);
        }
      } finally {
        clearActiveRequest();
        input.focus();
      }
    }

    send.addEventListener("click", submit);
    clear.addEventListener("click", resetConversation);
    kbOnlyToggle.addEventListener("change", () => {
      try {
        window.localStorage.setItem(KB_ONLY_STORAGE_KEY, kbOnlyToggle.checked ? "kb_only" : "normal");
      } catch (err) {
        // localStorage may be unavailable in some embedded contexts.
      }
    });
    add.addEventListener("click", () => attachmentInput.click());
    attachmentInput.addEventListener("change", () => addFiles(attachmentInput.files));
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        submit();
      }
    });

    drawer.appendChild(head);
    drawer.appendChild(messages);
    drawer.appendChild(controls);
    return drawer;
  }

  function mount() {
    ensureCriticalStyles();
    const drawer = createDrawer();
    if (standalone) {
      const host = el("main", "ai-chat-standalone");
      host.appendChild(drawer);
      document.body.appendChild(host);
      return;
    }
    const tab = el("button", "ai-assistant-tab");
    tab.type = "button";
    tab.innerHTML = "<span>◀</span><span>AI助手</span>";
    tab.addEventListener("click", () => drawer.classList.add("open"));
    document.body.appendChild(tab);
    document.body.appendChild(drawer);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mount);
  } else {
    mount();
  }
})();
