import {createPanelPoll} from "/panel-poll.js";

const MAX_TEXT = 200000, MAX_EVENTS = 500, MAX_BUFFER = 1000000, MAX_MESSAGES = 80;
const node = (tag, text, className) => {
  const value = document.createElement(tag);
  if (text !== undefined) value.textContent = text;
  if (className) value.className = className;
  return value;
};
const clip = text => {
  const value = text.slice(0, MAX_TEXT);
  return /[\uD800-\uDBFF]$/.test(value) ? value.slice(0, -1) : value;
};
const key = event => JSON.stringify([event.threadId, event.runId, event.messageId]);
const runKey = event => JSON.stringify([event.threadId, event.runId]);

function safeURL(value) {
  if (typeof value !== "string" || new TextEncoder().encode(value).length > 2048 || /[\s\\\p{C}]/u.test(value)) return null;
  try {
    if (/\p{C}/u.test(decodeURIComponent(value))) return null;
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) && url.hostname && !url.hostname.includes("%") && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}

// Shared server AST only. Unknown or excessive structure stays readable literally.
export function renderDocument(ast, source) {
  let count = 0, characters = 0;
  function visit(value, depth = 0) {
    if (!value || typeof value !== "object" || ++count > 4000 || depth > 16) throw new Error("AST limit");
    const type = value.type;
    if (["text", "code", "code_block"].includes(type)) {
      if (typeof value.value !== "string" || (characters += value.value.length) > MAX_TEXT) throw new Error("Text limit");
      if (type === "text") return document.createTextNode(value.value);
      const code = node("code", value.value);
      if (type === "code") return code;
      const pre = node("pre"); pre.tabIndex = 0; pre.setAttribute("aria-label", "Код відповіді"); pre.append(code); return pre;
    }
    const tags = {document: "div", paragraph: "p", heading: "h" + Math.min(6, Math.max(2, (Number(value.level) || 1) + 1)),
      strong: "strong", emphasis: "em", strike: "s", link: "a", blockquote: "blockquote", list: value.ordered ? "ol" : "ul",
      list_item: "li", table: "table", table_row: "tr", table_cell: value.header ? "th" : "td", break: "br"};
    if (!Object.hasOwn(tags, type) || (value.children !== undefined && !Array.isArray(value.children))) throw new Error("Unknown AST node");
    const result = node(tags[type]);
    if (type === "link") {
      const url = safeURL(value.url);
      if (url) { result.href = url; result.target = "_blank"; result.rel = "noopener noreferrer"; }
    }
    if (type === "list" && value.ordered && Number.isSafeInteger(value.start) && value.start >= 1 && value.start <= 1000000) result.start = value.start;
    if (type === "table_cell" && value.header) result.scope = "col";
    if (type === "table_cell" && ["left", "center", "right"].includes(value.align)) result.className = "message-align-" + value.align;
    for (const child of value.children || []) result.append(visit(child, depth + 1));
    if (type === "table") {
      const wrap = node("div", undefined, "message-table"); wrap.tabIndex = 0;
      wrap.setAttribute("role", "region"); wrap.setAttribute("aria-label", "Таблиця відповіді. Доступне горизонтальне прокручування.");
      wrap.append(result); return wrap;
    }
    return result;
  }
  try {
    if (ast?.type !== "document" || ast.literal || source.length > MAX_TEXT) throw new Error("Literal document");
    return visit(ast);
  } catch { return node("div", clip(source), "message-literal"); }
}

export function assembleMessages(events) {
  const messages = new Map(), runs = new Map(), seen = new Set();
  for (const event of [...events].sort((a, b) => a.sequence - b.sequence)) {
    if (!Number.isSafeInteger(event.sequence) || seen.has(event.sequence) ||
        ![event.threadId, event.runId].every(value => typeof value === "string" && value.length <= 512)) continue;
    seen.add(event.sequence);
    if (event.type === "run_end") { runs.set(runKey(event), event.status); continue; }
    if (!["start", "delta", "end"].includes(event.type) || typeof event.messageId !== "string" || event.messageId.length > 512) continue;
    const id = key(event);
    if (!messages.has(id)) messages.set(id, {id, run: runKey(event), sequence: event.sequence, text: "", complete: false, partial: true, truncated: false});
    const message = messages.get(id);
    if (event.type === "start") message.partial = false;
    if (event.type === "delta" && !message.complete && typeof event.delta === "string") {
      const text = message.text + event.delta;
      message.text = clip(text); message.truncated ||= !!event.truncated || text.length > MAX_TEXT;
    }
    if (event.type === "end") {
      if (typeof event.text === "string") {
        message.text = clip(event.text); message.partial = false;
        message.truncated = !!event.truncated || event.text.length > MAX_TEXT;
      }
      message.complete = true;
    }
  }
  return [...messages.values()].map(message => ({...message, status: runs.get(message.run) || "running"}));
}

export function createMessageHistory({api, available, epoch, list, status, older}) {
  let cursor = null, before = null, generation = 0, limited = false, loadingOlder = false, formatJob = null, runtimeEpoch = null;
  const events = new Map(), rows = new Map(), requests = new Set();
  const current = (version, owner) => version === generation && owner === epoch() && available();
  const say = value => { status.textContent = value; };
  function pause() {
    poll.reset(); generation++; loadingOlder = false;
    for (const request of requests) request.abort();
    requests.clear(); formatJob = null;
    for (const row of rows.values()) row.formatting = null;
    older.disabled = false;
  }
  function reset() {
    pause(); cursor = before = null; runtimeEpoch = null; limited = false; events.clear(); rows.clear();
    list.replaceChildren(); older.hidden = true; say("Повідомлення ще не завантажено.");
  }
  function format() {
    if (formatJob || !available()) return;
    const version = generation, owner = epoch(), job = {};
    formatJob = job;
    async function worker() {
      while (current(version, owner)) {
        const row = [...rows.values()].find(item => item.message.complete && !item.message.partial && !item.message.truncated && !item.formatted && !item.formatting);
        if (!row) return;
        const request = new AbortController(), text = row.message.text, revision = row.revision;
        row.formatting = request; requests.add(request);
        try {
          const ast = await api("/api/render", {text}, {signal: request.signal});
          if (current(version, owner) && rows.get(row.message.id) === row && row.revision === revision) {
            row.body.replaceChildren(renderDocument(ast, text)); row.formatted = true;
          }
        } catch {
          // Safe literal text is already visible; no retry loop for formatting.
          if (current(version, owner) && row.revision === revision) row.formatted = true;
        } finally {
          requests.delete(request);
          if (row.formatting === request) row.formatting = null;
        }
      }
    }
    void Promise.all([worker(), worker()]).finally(() => { if (formatJob === job) formatJob = null; });
  }
  function render() {
    let messages = assembleMessages(events.values());
    if (messages.length > MAX_MESSAGES) { messages = messages.slice(-MAX_MESSAGES); limited = true; before = null; }
    const retained = new Set(messages.map(message => message.id));
    for (const [id, row] of rows) if (!retained.has(id)) { row.element.remove(); rows.delete(id); }
    for (const [index, message] of messages.entries()) {
      let row = rows.get(message.id);
      if (!row) {
        const article = node("article", undefined, "message-entry"), title = node("h2", "Відповідь Oak"), meta = node("p", "", "hint");
        const body = node("div", undefined, "message-body"), note = node("p", "", "hint");
        article.append(title, meta, body, note);
        row = {element: article, meta, body, note, revision: 0}; rows.set(message.id, row);
      }
      const previous = row.message;
      if (!previous || ["text", "complete", "partial", "truncated"].some(field => previous[field] !== message[field])) {
        row.revision++; row.formatted = false;
        row.body.replaceChildren(node("div", message.text || "…", "message-literal"));
      }
      row.message = message;
      row.meta.textContent = message.status === "failed" ? "Виконання завершилося помилкою" : message.status === "interrupted" ? "Виконання зупинено" :
        message.complete ? "Відповідь отримано" : "Отримуємо відповідь…";
      row.note.textContent = message.truncated ? "Довгий текст скорочено для перегляду. Повна відповідь залишається в Telegram." :
        message.partial ? "Початок повідомлення поза цим вікном історії. Завантаж раніші повідомлення." : "";
      row.note.hidden = !row.note.textContent;
      if (list.children[index] !== row.element) list.insertBefore(row.element, list.children[index] || null);
    }
    older.hidden = before === null;
    say(!messages.length ? "У цьому вікні історії ще немає відповідей Oak." : limited ?
      "Показано частину історії. Повні відповіді доступні в Telegram." : before !== null ? "Показано останні повідомлення." : "Повідомлення вибраної сесії.");
    format();
  }
  function apply(value, earlier = false) {
    if (!Array.isArray(value.events)) throw new Error("Invalid history");
    if (typeof value.runtime_epoch !== "string" || !value.runtime_epoch) throw new Error("Missing runtime epoch");
    if (runtimeEpoch && runtimeEpoch !== value.runtime_epoch) {
      reset(); runtimeEpoch = value.runtime_epoch; poll.resume(); return;
    }
    runtimeEpoch = value.runtime_epoch;
    for (const event of value.events) if (Number.isSafeInteger(event.sequence)) events.set(event.sequence, event);
    let total = 0;
    const ordered = [...events.values()].sort((a, b) => b.sequence - a.sequence);
    for (const [index, event] of ordered.entries()) {
      total += (event.delta?.length || 0) + (event.text?.length || 0);
      if (index >= MAX_EVENTS || total > MAX_BUFFER) { events.delete(event.sequence); limited = true; }
    }
    if (cursor === null || earlier) before = value.before;
    if (!earlier) cursor = Math.max(cursor || 0, value.cursor);
    limited ||= !!value.partial && value.before === null;
    if (limited) before = null;
    render();
  }
  const poll = createPanelPoll({epoch, available: () => available() && !loadingOlder,
    read: signal => api("/api/messages" + (cursor === null ? "" : "?after=" + cursor), undefined, {signal}),
    apply: value => apply(value), failed: () => say("Повідомлення не оновлено. Перевір з’єднання та натисни «Оновити». Попередній текст збережено.")});
  older.addEventListener("click", async () => {
    if (before === null || loadingOlder || !available()) return;
    poll.reset(); loadingOlder = true; older.disabled = true;
    const version = generation, owner = epoch(), request = new AbortController(); requests.add(request);
    try {
      const value = await api("/api/messages?before=" + before, undefined, {signal: request.signal});
      if (current(version, owner)) apply(value, true);
    } catch { if (current(version, owner)) say("Раніші повідомлення недоступні. Спробуй ще раз."); }
    finally {
      requests.delete(request);
      if (current(version, owner)) { loadingOlder = false; older.disabled = false; poll.start(); }
    }
  });
  return {reset, pause, refresh: () => { if (available()) { format(); poll.resume(); } else pause(); }};
}
