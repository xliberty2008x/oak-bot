"use strict";

const $ = id => document.getElementById(id);
let sessionToken = "", sessionEpoch = 0, authenticated = false, refreshing = false, mutationBusy = false, telegramLaunch = false;
let panelData = null, integrationData = null, timezone = "", stopRequestState = "";
const appLinks = new Map(), oauthLinks = new Map(), oauthBlocked = new Set();
const taskLabels = {pending: "Заплановано", running: "Виконується", uncertain: "Потрібна перевірка", done: "Завершено", cancelled: "Скасовано"};
const turnLabels = {inProgress: "Виконується", running: "Виконується", completed: "Завершено", interrupted: "Перервано", failed: "Помилка виконання", uncertain: "Результат невідомий"};
const authLabels = {unknown: "Не визначено", unsupported: "Не підтримується", notLoggedIn: "Потрібен вхід", bearerToken: "Токен налаштовано", oAuth: "OAuth-доступ збережено"};
const runtimeLabels = {notStarted: "Ще не запущено", starting: "Запускається", connected: "Підключено до середовища", authenticationRequired: "Потрібна авторизація", failed: "Помилка", cancelled: "Запуск скасовано", disabled: "Вимкнено"};
const pluginLabels = {enabled: "Увімкнено", disabled: "Вимкнено", disabled_by_admin: "Вимкнено адміністратором", plan_not_eligible: "Недоступно за підпискою", required_app_unavailable: "Потрібний застосунок недоступний", unknown: "Не визначено"};
const loginLabels = {requested: "Вхід запитано", pending: "Очікуємо завершення входу", uncertain: "Результат входу невідомий", expired: "Час очікування минув; результат не підтверджено"};

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function message(id, text, isError = false) {
  const node = $(id);
  node.textContent = text;
  node.hidden = !text;
  node.classList.toggle("error-message", isError);
}
function count(value) { return Number.isInteger(value) && value >= 0 ? value.toLocaleString("uk-UA") : "—"; }
function label(labels, value) { return labels[value] || "Не визначено"; }
function fact(list, name, value) {
  const row = element("div");
  row.append(element("dt", name), element("dd", value));
  list.append(row);
}
function safeHTTPS(value, host) {
  if (typeof value !== "string" || value.length > 8192 || /[\u0000-\u0020\u007f]/.test(value)) return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password && (!host || url.hostname === host) ? url.href : null;
  } catch { return null; }
}
function externalLink(url, text) {
  const link = element("a", text, "button-link");
  link.href = url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  return link;
}
function dateNode(value, milliseconds = false) {
  const date = new Date(typeof value === "number" && Number.isFinite(value) ? value * (milliseconds ? 1 : 1000) : NaN);
  if (Number.isNaN(date.valueOf())) return element("span", "Час не повідомлено");
  let text;
  try { text = new Intl.DateTimeFormat("uk-UA", {dateStyle: "medium", timeStyle: "short", ...(timezone ? {timeZone: timezone} : {})}).format(date); }
  catch { text = new Intl.DateTimeFormat("uk-UA", {dateStyle: "medium", timeStyle: "short"}).format(date); }
  const node = element("time", text);
  node.dateTime = date.toISOString();
  return node;
}
function intervalText(value) {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) return "Одноразово";
  if (value === 86400) return "Щодня";
  if (value === 3600) return "Щогодини";
  if (value % 86400 === 0) return "Кожні " + value / 86400 + " дн.";
  if (value % 3600 === 0) return "Кожні " + value / 3600 + " год.";
  if (value % 60 === 0) return "Кожні " + value / 60 + " хв.";
  return "Кожні " + value + " с";
}

function requireLogin(text = "Сеанс завершився. Відкрий Oak у Telegram або увійди за приватним ключем.") {
  sessionEpoch++;
  sessionToken = "";
  authenticated = false;
  refreshing = false;
  mutationBusy = false;
  panelData = null;
  integrationData = null;
  appLinks.clear(); oauthLinks.clear(); oauthBlocked.clear();
  stopRequestState = "";
  $("workspace").hidden = true;
  $("login").hidden = false;
  $("key").value = "";
  $("connection").textContent = "Потрібен вхід";
  if ($("confirmation").open) $("confirmation").close("cancel");
  message("error", text, true);
  message("notice", "");
}

async function api(path, data) {
  const epoch = sessionEpoch, controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);
  const headers = sessionToken ? {Authorization: "Bearer " + sessionToken} : {};
  const options = {credentials: "same-origin", headers, signal: controller.signal};
  if (data !== undefined) {
    options.method = "POST";
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(data);
  }
  try {
    const response = await fetch(path, options);
    if (!response.ok) {
      if (response.status === 401 && epoch === sessionEpoch) requireLogin(path === "/api/session" ? "Не вдалося увійти. Відкрий Oak у Telegram або перевір приватний ключ." : undefined);
      const error = new Error(response.status === 401 ? "Потрібен вхід." : response.status === 403 ? "Дію відхилено. Перевір доступ і онови дані." : "Не вдалося отримати дані. Перевір з’єднання та натисни «Оновити».");
      error.status = response.status;
      throw error;
    }
    const result = await response.json();
    if (epoch !== sessionEpoch) throw new Error("Сеанс змінився.");
    if (path === "/api/session") {
      if (typeof result.access_token !== "string" || !result.access_token) throw new Error("Не вдалося підтвердити вхід. Відкрий Oak у Telegram ще раз.");
      sessionToken = result.access_token;
      authenticated = true;
    }
    return result;
  } finally { clearTimeout(timeout); }
}

function updateActions() {
  $("refresh").disabled = refreshing || mutationBusy;
  for (const button of document.querySelectorAll("[data-mutation]")) button.disabled = mutationBusy || refreshing || button.dataset.locked === "true";
  $("stop").disabled = mutationBusy || refreshing || !panelData?.session?.active || !!stopRequestState;
}
function confirmAction(title, description, action, danger = false) {
  return new Promise(resolve => {
    const dialog = $("confirmation");
    $("confirmation-title").textContent = title;
    $("confirmation-description").textContent = description;
    $("confirmation-submit").textContent = action;
    $("confirmation-submit").classList.toggle("danger", danger);
    dialog.returnValue = "cancel";
    dialog.addEventListener("close", () => resolve(dialog.returnValue === "confirm"), {once: true});
    dialog.showModal();
  });
}
async function mutation(title, description, action, work, danger = false) {
  if (mutationBusy || refreshing || $("workspace").hidden) return;
  if (!await confirmAction(title, description, action, danger) || $("workspace").hidden) return;
  mutationBusy = true;
  updateActions();
  const epoch = sessionEpoch;
  try { await work(epoch); }
  catch (error) {
    if (epoch === sessionEpoch) {
      if (stopRequestState && panelData) renderPanel(panelData);
      message("notice", error.status === 403 ? error.message : "Не вдалося отримати відповідь на дію. Запит міг бути прийнятий. Натисни «Оновити», щоб перевірити стан перед новою спробою.", true);
    }
  } finally { if (epoch === sessionEpoch) mutationBusy = false; updateActions(); }
}

function renderPanel(value) {
  if (!value || !value.session || !value.settings) throw new Error("Не вдалося прочитати стан Oak. Натисни «Оновити».");
  panelData = value;
  authenticated = true;
  timezone = typeof value.settings.timezone === "string" ? value.settings.timezone : "";
  const session = value.session;
  if (!session.active) stopRequestState = "";
  let title, description, indicator = "";
  if (stopRequestState) {
    title = stopRequestState === "requested" ? "Зупинку запитано" : "Очікуємо перевірки зупинки";
    description = "Завершення роботи ще не підтверджено. Натисни «Оновити», щоб перевірити стан.";
    indicator = "waiting";
  } else if (session.awaiting_confirmation > 0) {
    title = "Чекає на підтвердження";
    description = "Відповідай на запити Oak у поточній розмові в Telegram.";
    indicator = "waiting";
  } else if (session.active) {
    title = "Oak працює";
    description = "Уточнення та результат роботи доступні в Telegram.";
    indicator = "active";
  } else if (session.uncertain_inputs > 0) {
    title = "Є запити для перевірки";
    description = "Результат деяких запитів невідомий. Перевір їх у Telegram перед повторним виконанням.";
    indicator = "waiting";
  } else {
    title = session.initialized ? "Очікує повідомлення" : "Розмову ще не розпочато";
    description = session.initialized ? "Поточна розмова в Telegram готова до наступного повідомлення." : "Напиши Oak у Telegram, щоб почати розмову.";
  }
  if (session.turn_status && !stopRequestState) description += " Останнє виконання: " + label(turnLabels, session.turn_status).toLocaleLowerCase("uk-UA") + ".";
  $("session-state").textContent = title;
  $("session-description").textContent = description;
  $("session-indicator").className = "status-dot" + (indicator ? " " + indicator : "");
  $("confirmations").textContent = count(session.awaiting_confirmation);
  $("uncertain-inputs").textContent = count(session.uncertain_inputs);
  $("memory-count").textContent = count(value.memory?.count);
  const telegramURL = safeHTTPS(value.telegram_url);
  const canClose = telegramLaunch && typeof window.Telegram?.WebApp?.close === "function";
  $("return-telegram").hidden = !canClose;
  $("telegram-link").hidden = canClose || !telegramURL;
  if (telegramURL) $("telegram-link").href = telegramURL;
  else $("telegram-link").removeAttribute("href");
  const settings = value.settings, list = $("settings-list");
  list.replaceChildren();
  fact(list, "Модель", settings.model || "Не повідомлено");
  fact(list, "Вхід у модель", settings.auth === "chatgpt" ? "ChatGPT" : "Недоступний");
  fact(list, "Часовий пояс", settings.timezone || "Не повідомлено");
  fact(list, "Доступ до файлів", ({"workspace-write": "Файли робочої папки", "read-only": "Лише читання", "danger-full-access": "Повний доступ"})[settings.sandbox] || settings.sandbox || "Не повідомлено");
  fact(list, "Підтвердження дій", ({never: "Без інтерактивних підтверджень", "on-request": "За запитом Oak", "on-failure": "У разі обмеження", untrusted: "Для неперевірених дій"})[settings.approval_policy] || settings.approval_policy || "Не повідомлено");
  fact(list, "Транспорт", ({poll: "Опитування", sse: "Потік подій"})[settings.transport] || settings.transport || "Не повідомлено");
  message("panel-error", "");
  updateActions();
}

function taskHistory(task) {
  const details = element("details", undefined, "task-history"), content = element("div", "Завантажуємо історію…", "hint");
  details.append(element("summary", "Історія виконання"), content);
  let loaded = false;
  details.addEventListener("toggle", async () => {
    if (!details.open || loaded) return;
    loaded = true;
    const epoch = sessionEpoch;
    try {
      const value = await api("/api/tasks/" + encodeURIComponent(task.id));
      if (epoch !== sessionEpoch) return;
      if (!Array.isArray(value.timeline)) throw new Error("Історія недоступна.");
      if (!value.timeline.length) { content.textContent = "Записів про виконання ще немає."; return; }
      const timeline = element("ul", undefined, "timeline");
      for (const event of value.timeline) {
        const names = {RUN_STARTED: "Розпочато", RUN_FINISHED: "Завершено", RUN_ERROR: "Помилка виконання"};
        if (!names[event.type]) continue;
        const row = element("li", names[event.type] + (event.status ? ": " + label(turnLabels, event.status).toLocaleLowerCase("uk-UA") : ""));
        row.append(dateNode(event.timestamp, true));
        timeline.append(row);
      }
      content.replaceChildren(timeline);
    } catch { if (epoch === sessionEpoch) content.textContent = "Історію не вдалося завантажити. Онови задачі та спробуй ще раз."; }
  });
  return details;
}
function renderTasks(value) {
  if (!value || !Array.isArray(value.tasks)) throw new Error("Не вдалося прочитати задачі. Натисни «Оновити».");
  if (typeof value.timezone === "string") timezone = value.timezone;
  $("tasks-timezone").textContent = "Нагадування та запланована робота Oak" + (timezone ? ". Часовий пояс: " + timezone : "");
  const list = $("tasks-list");
  list.replaceChildren();
  message("tasks-status", value.tasks.length ? "" : "Задач у розкладі поки немає. Створи нагадування або заплануй роботу в Telegram.");
  for (const task of value.tasks) {
    const item = element("li", undefined, "item"), heading = element("div", undefined, "item-heading");
    const tone = task.status === "uncertain" ? "warning" : task.status === "running" || task.status === "pending" ? "good" : "";
    heading.append(element("h3", task.mode === "remind" ? "Нагадування" : "Задача Oak"), element("span", label(taskLabels, task.status), "badge " + tone));
    const meta = element("p", undefined, "item-meta");
    meta.append(dateNode(task.due), document.createTextNode(". " + intervalText(task.interval) + ". ID: "), element("code", task.id));
    item.append(heading, meta);
    if (task.turn_status) item.append(element("p", "Виконання: " + label(turnLabels, task.turn_status).toLocaleLowerCase("uk-UA"), "item-description"));
    if (task.status === "uncertain") item.append(element("p", "Результат невідомий. Перевір його в Telegram перед повторним запуском.", "item-description warning"));
    if (["pending", "running", "uncertain"].includes(task.status)) {
      const actions = element("div", undefined, "item-actions"), button = element("button", "Скасувати розклад", "danger");
      button.type = "button"; button.dataset.mutation = "cancel";
      button.addEventListener("click", () => mutation("Скасувати розклад?", "Майбутні запуски цієї задачі буде скасовано. Якщо вона вже працює, окремо натисни «Зупинити Oak» у розділі «Стан».", "Скасувати розклад", async epoch => {
        button.dataset.locked = "true";
        const result = await api("/api/tasks/" + encodeURIComponent(task.id) + "/cancel", {confirmed: true});
        if (epoch !== sessionEpoch) return;
        message("notice", result.cancelled ? "Розклад скасовано. Робота, що вже почалась, потребує окремої зупинки." : "Задача вже завершена або скасована. Оновлюємо стан.");
        await loadTasks(epoch);
      }, true));
      actions.append(button); item.append(actions);
    }
    item.append(taskHistory(task));
    list.append(item);
  }
  updateActions();
}

function inventoryGroup(title, inventory, emptyText, renderItem) {
  const section = element("section", undefined, "integration-group");
  section.append(element("h2", title));
  if (!inventory || inventory.available !== true || !Array.isArray(inventory.items)) {
    section.append(element("p", "Дані недоступні. Натисни «Оновити», щоб перевірити доступність.", "inventory-empty"));
  } else {
    if (inventory.complete === false) section.append(element("p", "Список неповний: частину даних не вдалося отримати. Натисни «Оновити», щоб перевірити повний список.", "item-description warning"));
    if (!inventory.items.length) section.append(element("p", inventory.complete === false ? "Повний список недоступний." : emptyText, "inventory-empty"));
    else {
      const list = element("ul", undefined, "item-list");
      for (const item of inventory.items) list.append(renderItem(item));
      section.append(list);
    }
  }
  return section;
}
function integrationItem(name) {
  const item = element("li", undefined, "item");
  item.append(element("h3", name || "Назву не повідомлено"));
  return item;
}
function renderApp(app) {
  const item = integrationItem(app.name), facts = element("dl", undefined, "inventory-facts");
  fact(facts, "Стан", app.enabled ? "Увімкнено" : "Вимкнено");
  fact(facts, "Доступність для Oak", app.callable ? "Можна викликати" : "Виклик недоступний");
  item.append(facts);
  if (app.manage_available) {
    const actions = element("div", undefined, "item-actions"), button = element("button", "Налаштування ChatGPT", "quiet");
    button.type = "button"; button.dataset.mutation = "manage";
    button.dataset.locked = appLinks.has(app.id) ? "true" : "false";
    button.addEventListener("click", () => mutation("Відкрити зовнішні налаштування?", "Oak підготує посилання на налаштування застосунку «" + app.name + "» у ChatGPT. Зміни виконуєш на сторінці ChatGPT.", "Отримати посилання", async epoch => {
      button.dataset.locked = "true";
      const result = await api("/api/integrations/apps/" + encodeURIComponent(app.id) + "/manage", {confirmed: true});
      if (epoch !== sessionEpoch) return;
      const url = safeHTTPS(result.url, "chatgpt.com");
      if (!url) throw new Error("Безпечне посилання не отримано.");
      appLinks.set(app.id, url);
      renderIntegrations(integrationData);
      message("notice", "Посилання готове. Натисни «Відкрити в ChatGPT». Налаштування в панелі Oak не змінено.");
    }));
    actions.append(button);
    if (appLinks.has(app.id)) actions.append(externalLink(appLinks.get(app.id), "Відкрити в ChatGPT"));
    item.append(actions);
  } else item.append(element("p", "Зовнішні налаштування цього застосунку зараз недоступні.", "item-description"));
  return item;
}
function renderPlugin(plugin) {
  const item = integrationItem(plugin.name), facts = element("dl", undefined, "inventory-facts");
  fact(facts, "Увімкнення", plugin.enabled ? "Увімкнено" : "Вимкнено");
  fact(facts, "Стан", label(pluginLabels, plugin.status));
  fact(facts, "Запит на вхід", ({ON_INSTALL: "Під час встановлення", ON_USE: "Під час використання", unknown: "Не визначено"})[plugin.auth_policy] || "Не визначено");
  item.append(facts);
  return item;
}
function renderServer(server) {
  const item = integrationItem(server.name), facts = element("dl", undefined, "inventory-facts");
  fact(facts, "Авторизація", label(authLabels, server.auth_status));
  fact(facts, "Стан у середовищі", server.runtime_status === null ? "Не повідомлено" : label(runtimeLabels, server.runtime_status));
  fact(facts, "Інструментів", Number.isInteger(server.tool_count) && server.tool_count >= 0 ? count(server.tool_count) : "Не визначено");
  item.append(facts);
  if (server.login_state) item.append(element("p", label(loginLabels, server.login_state), "item-description" + (["uncertain", "expired"].includes(server.login_state) ? " warning" : "")));
  if (server.oauth_available) {
    const actions = element("div", undefined, "item-actions"), button = element("button", "Увійти на зовнішній сторінці", "quiet");
    button.type = "button"; button.dataset.mutation = "oauth";
    button.dataset.locked = ["requested", "pending", "uncertain"].includes(server.login_state) || oauthBlocked.has(server.id) ? "true" : "false";
    button.addEventListener("click", () => mutation("Почати зовнішній вхід?", "Oak запитає посилання для авторизації «" + server.name + "». Після входу повернись у панель і натисни «Оновити», щоб перевірити стан.", "Отримати посилання", async epoch => {
      oauthBlocked.add(server.id); button.dataset.locked = "true";
      const result = await api("/api/integrations/servers/" + encodeURIComponent(server.id) + "/oauth", {confirmed: true});
      if (epoch !== sessionEpoch) return;
      const url = safeHTTPS(result.url);
      if (!url || result.state !== "pending") throw new Error("Безпечне посилання не отримано.");
      oauthLinks.set(server.id, url);
      server.login_state = "pending";
      renderIntegrations(integrationData);
      message("notice", "Посилання для входу готове. Відкрий сторінку входу, а потім повернись і натисни «Оновити». Авторизацію ще не підтверджено.");
    }));
    actions.append(button);
    if (oauthLinks.has(server.id)) actions.append(externalLink(oauthLinks.get(server.id), "Відкрити сторінку входу"));
    item.append(actions);
    if (["requested", "pending", "uncertain"].includes(server.login_state) && !oauthLinks.has(server.id)) item.append(element("p", "Перевір стан входу через «Оновити». Новий запит автоматично не надсилаємо.", "item-description"));
  } else item.append(element("p", server.oauth_reason || "Зовнішній вхід для цього підключення недоступний.", "item-description"));
  return item;
}
function renderIntegrations(value) {
  if (!value || typeof value !== "object") throw new Error("Не вдалося прочитати інтеграції. Натисни «Оновити».");
  integrationData = value;
  $("integration-scope").textContent = value.scope === "telegram" ? "Можливості поточної розмови в Telegram" : "Інтеграції середовища Oak. Доступність у розмові перевір у Telegram.";
  $("integrations-content").replaceChildren(
    inventoryGroup("Застосунки", value.apps, "Застосунків немає.", renderApp),
    inventoryGroup("Плагіни", value.plugins, "Плагінів немає.", renderPlugin),
    inventoryGroup("Сервери інструментів", value.servers, "Серверів інструментів немає.", renderServer)
  );
  message("integrations-status", "");
  updateActions();
}

async function loadPanel(epoch) {
  try {
    const value = await api("/api/panel");
    if (epoch !== sessionEpoch) return false;
    renderPanel(value);
    return true;
  } catch (error) {
    if (epoch === sessionEpoch) {
      panelData = null;
      $("session-state").textContent = "Стан недоступний";
      $("session-description").textContent = "Не вдалося перевірити поточну розмову. Натисни «Оновити».";
      $("session-indicator").className = "status-dot";
      for (const id of ["confirmations", "uncertain-inputs", "memory-count"]) $(id).textContent = "—";
      $("settings-list").replaceChildren();
      fact($("settings-list"), "Стан", "Дані недоступні. Натисни «Оновити».");
      message("panel-error", error.name === "AbortError" ? "Оновлення затримується. Перевір з’єднання та спробуй ще раз." : error.message, true);
      updateActions();
    }
    return false;
  }
}
async function loadTasks(epoch) {
  message("tasks-status", "Завантажуємо задачі…");
  try {
    const value = await api("/api/tasks");
    if (epoch !== sessionEpoch) return false;
    renderTasks(value);
    return true;
  } catch {
    if (epoch === sessionEpoch) {
      $("tasks-list").replaceChildren();
      message("tasks-status", "Задачі недоступні. Перевір з’єднання та натисни «Оновити».", true);
    }
    return false;
  }
}
async function loadIntegrations(epoch) {
  message("integrations-status", "Завантажуємо інтеграції…");
  try {
    const value = await api("/api/integrations");
    if (epoch !== sessionEpoch) return false;
    oauthBlocked.clear();
    for (const server of value.servers?.items || []) if (!["requested", "pending"].includes(server.login_state)) oauthLinks.delete(server.id);
    renderIntegrations(value);
    return true;
  } catch {
    if (epoch === sessionEpoch) {
      integrationData = null;
      $("integrations-content").replaceChildren();
      message("integrations-status", "Інтеграції недоступні. Перевір з’єднання та натисни «Оновити».", true);
    }
    return false;
  }
}
async function refresh() {
  if (refreshing || mutationBusy) return;
  refreshing = true;
  const epoch = sessionEpoch;
  $("connection").textContent = "Оновлюємо…";
  $("workspace").setAttribute("aria-busy", "true");
  updateActions();
  try {
    const panelOK = await loadPanel(epoch);
    if (epoch !== sessionEpoch) return;
    if (!panelOK && !authenticated) {
      requireLogin("Не вдалося перевірити вхід. Перевір з’єднання, відкрий Oak із меню Telegram або введи приватний ключ.");
      return;
    }
    $("login").hidden = true;
    $("workspace").hidden = false;
    message("error", "");
    const results = await Promise.allSettled([loadTasks(epoch), loadIntegrations(epoch)]);
    if (epoch !== sessionEpoch) return;
    const complete = panelOK && results.every(result => result.status === "fulfilled" && result.value);
    $("connection").textContent = complete ? "На зв’язку" : "Не всі дані доступні";
    $("last-refresh").textContent = (complete ? "Оновлено о " : "Остання спроба о ") + new Intl.DateTimeFormat("uk-UA", {hour: "2-digit", minute: "2-digit", second: "2-digit"}).format(new Date());
  } finally {
    if (epoch === sessionEpoch) {
      refreshing = false;
      $("workspace").removeAttribute("aria-busy");
    }
    updateActions();
  }
}

for (const button of document.querySelectorAll("[data-view]")) button.addEventListener("click", () => {
  const view = button.dataset.view;
  for (const navButton of document.querySelectorAll("[data-view]")) {
    if (navButton === button) navButton.setAttribute("aria-current", "page");
    else navButton.removeAttribute("aria-current");
  }
  for (const section of document.querySelectorAll(".view")) section.hidden = section.id !== "view-" + view;
  $(view + "-heading").focus({preventScroll: true});
});
$("refresh").addEventListener("click", () => { message("notice", ""); void refresh(); });
$("return-telegram").addEventListener("click", () => { if (telegramLaunch) window.Telegram?.WebApp?.close(); });
$("stop").addEventListener("click", () => mutation("Зупинити поточну роботу Oak?", "Запит стосується поточної розмови в Telegram. Уже виконані зовнішні дії залишаться виконаними. Заплановані задачі потрібно скасовувати окремо.", "Запитати зупинку", async epoch => {
  stopRequestState = "uncertain";
  const result = await api("/api/stop", {conversation: "telegram"});
  if (epoch !== sessionEpoch) return;
  if (result.status === "requested") {
    stopRequestState = "requested";
    renderPanel(panelData);
    message("notice", "Запит на зупинку передано. Завершення роботи ще не підтверджено. Натисни «Оновити», щоб перевірити стан.");
  } else {
    stopRequestState = "";
    message("notice", result.status === "idle" ? "Зараз Oak не виконує роботу." : result.status === "not_active" ? "Активного виконання не виявлено. Оновлюємо стан." : "Запит прийнято. Перевіряємо поточний стан.");
    await loadPanel(epoch);
  }
}, true));
$("login").addEventListener("submit", async event => {
  event.preventDefault();
  if ($("sign-in").disabled) return;
  $("sign-in").disabled = true;
  sessionToken = "";
  telegramLaunch = false;
  const key = $("key").value;
  $("key").value = "";
  try { await api("/api/session", {key}); await refresh(); }
  catch (error) { if (error.status !== 401) requireLogin("Не вдалося увійти. Перевір з’єднання та приватний ключ."); }
  finally { $("sign-in").disabled = false; }
});

function telegramReady() {
  try { window.Telegram?.WebApp?.ready(); window.Telegram?.WebApp?.expand(); } catch {}
}
$("telegram-sdk").addEventListener("load", telegramReady);
(async () => {
  try {
    for (let i = 0; i < 30 && !window.Telegram?.WebApp; i++) await new Promise(resolve => setTimeout(resolve, 100));
    telegramReady();
    const fragment = new URLSearchParams(location.hash.slice(1));
    const initData = window.Telegram?.WebApp?.initData || fragment.get("tgWebAppData");
    if (initData) { await api("/api/session", {initData}); telegramLaunch = true; }
    await refresh();
  } catch (error) {
    if (error.status !== 401) requireLogin("Під’єднання недоступне. Відкрий Oak із меню Telegram або введи приватний ключ доступу.");
  } finally { telegramReady(); }
})();
