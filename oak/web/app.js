"use strict";

const $ = id => document.getElementById(id);
let sessionToken = "", sessionEpoch = 0, authenticated = false, refreshing = false, mutationBusy = false, telegramLaunch = false;
let panelData = null, integrationData = null, timezone = "", stopRequestState = "";
let modelsData = null, lastModel = "", modelsUnavailable = false, draftEffort = null, lastEffort = null, draftTurbo = null, lastTurbo = null;
let computerChanging = false;
let remotePanel = null, remoteLoading = null;
let a2uiPanel = null, a2uiLoading = null;
let inputRequestsPanel = null, inputRequestsLoading = null;
let panelPoll = null, telegramTheme = null, telegramBridge = null, bridgeObserver = null, confirmationPending = false;
const approvalBlocked = new Set();
let memoryNext = null, memoryQuery = "", memoryGeneration = 0, memoryOperation = null;
const memoryDeletes = new Map();
let activityNext = null, activityGeneration = 0;
let artifactsNext = null, artifactsGeneration = 0;
const artifactAssets = new Map();
let recoveryNext = null, recoveryGeneration = 0;
const recoveryRetries = new Map();
let taskCreateOperation = null;
const taskReschedules = new Map(), taskPauses = new Map();
let initialRequest = new URLSearchParams(location.search).get("request"), requestScopeResolved = false;
if (initialRequest && !/^[a-f0-9]{32}$/.test(initialRequest)) initialRequest = null;
let selectedSession = "telegram", sessionsData = null, syncingSessions = false;
const launchParams = new URLSearchParams(location.search);
let initialConversation = launchParams.get("conversation");
let surfaceLaunch = !initialRequest && launchParams.has("surface") ? {conversation: initialConversation, id: launchParams.get("surface"), opened: false} : null;
const topicBlocked = new Set();
const appLinks = new Map(), oauthLinks = new Map(), oauthBlocked = new Set(), openIntegrations = new Set();
const taskLabels = {paused: "Призупинено", pending: "Заплановано", running: "Виконується", uncertain: "Потрібна перевірка", done: "Завершено", cancelled: "Скасовано"};
const turnLabels = {inProgress: "Виконується", running: "Виконується", completed: "Завершено", interrupted: "Перервано", failed: "Помилка виконання", uncertain: "Результат невідомий"};
const authLabels = {unknown: "Не визначено", unsupported: "Не підтримується", notLoggedIn: "Потрібен вхід", bearerToken: "Токен налаштовано", oAuth: "OAuth-доступ збережено"};
const runtimeLabels = {notStarted: "Ще не запущено", starting: "Запускається", connected: "Підключено до середовища", authenticationRequired: "Потрібна авторизація", failed: "Помилка", cancelled: "Запуск скасовано", disabled: "Вимкнено"};
const pluginLabels = {enabled: "Увімкнено", disabled: "Вимкнено", disabled_by_admin: "Вимкнено адміністратором", plan_not_eligible: "Недоступно за підпискою", required_app_unavailable: "Потрібний застосунок недоступний", unknown: "Не визначено"};
const loginLabels = {requested: "Вхід запитано", pending: "Очікуємо завершення входу", uncertain: "Результат входу невідомий", expired: "Час очікування минув; результат не підтверджено"};
const effortLabels = {low: "Light", medium: "Medium", high: "High", xhigh: "Extra High", max: "Max", ultra: "Ultra", minimal: "Minimal", none: "None"};
const authPolicyLabels = {ON_INSTALL: "Під час встановлення", ON_USE: "Під час використання", unknown: "Не визначено"};
const toolWords = {one: "інструмент", few: "інструменти", many: "інструментів", other: "інструмента"};
// First match wins; unmatched names get a monogram.
const integrationIcons = [
  ["calendar", /calendar/], ["mail", /mail|outlook|inbox/], ["chat", /slack|teams|discord|chat/],
  ["folder", /drive|dropbox|onedrive|sharepoint|\bbox\b|\bfiles?\b/], ["code", /github|gitlab|bitbucket|\bgit\b|code/],
  ["doc", /notion|confluence|\bdocs?\b|\bnotes?\b|\bword\b/], ["tasks", /linear|jira|asana|trello|todo|\btasks?\b/],
  ["pen", /figma|canva|design/], ["search", /search|research/], ["globe", /browser|playwright|chrome|\bweb\b/],
  ["chart", /data|\bsheets?\b|excel|analytic|chart/], ["monitor", /desktop|computer|screen/]
];

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
function relativeText(seconds) {
  const diff = seconds - Date.now() / 1000;
  if (!Number.isFinite(diff)) return "";
  const format = new Intl.RelativeTimeFormat("uk-UA", {numeric: "auto"}), size = Math.abs(diff);
  return size < 3600 ? format.format(Math.round(diff / 60), "minute") : size < 86400 ? format.format(Math.round(diff / 3600), "hour") : format.format(Math.round(diff / 86400), "day");
}
function renderTaskSummary(summary) {
  const known = summary && ["active_count", "running_count", "uncertain_count"].every(key => Number.isInteger(summary[key]) && summary[key] >= 0);
  $("task-count").textContent = known ? count(summary.active_count) : "—";
  setAttention("task-count", known && summary.uncertain_count > 0);
  const next = known && summary.next_task?.status === "pending" && typeof summary.next_task.due === "number" && Number.isFinite(summary.next_task.due) ? summary.next_task : null;
  if (!next) {
    $("next-task").textContent = !known ? "—" : summary.uncertain_count > 0 ? "Потрібна перевірка" : summary.running_count > 0 ? "Oak працює" : summary.active_count > 0 ? "Час не повідомлено" : "Немає запусків";
    $("next-task-meta").textContent = !known ? "Стан задач недоступний. Натисни «Оновити»." : summary.uncertain_count > 0 ? "Перевір результат задач у Telegram перед повторним запуском" : summary.running_count > 0 ? "Наступний запуск буде визначено після завершення роботи" : summary.active_count > 0 ? "Час наступного запуску недоступний. Натисни «Оновити»." : "Створи нагадування або задачу в Telegram";
    return;
  }
  $("next-task").replaceChildren(dateNode(next.due));
  $("next-task-meta").textContent = [next.mode === "remind" ? "Нагадування" : "Задача Oak", intervalText(next.interval), relativeText(next.due), summary.uncertain_count > 0 ? "Є задачі, що потребують перевірки" : ""].filter(Boolean).join(" · ");
}
function setAttention(id, active) { $(id).parentElement.classList.toggle("attention", active); }
function setConnection(text, state) {
  $("connection").textContent = text;
  $("connection").dataset.state = state;
}

function modelName(model) {
  return modelsData?.models.find(row => row.model === model)?.display_name || model;
}
function modelChoice() { return modelsData?.models.find(row => row.model === $("model-select").value); }
function effortName(effort) { return Object.hasOwn(effortLabels, effort) ? effortLabels[effort] : effort || "Не визначено"; }
function modelDraftChanged() {
  const choice = modelChoice();
  return choice && modelsData && (choice.model !== modelsData.selected || (choice.efforts.includes(draftEffort) && draftEffort !== modelsData.effort) || draftTurbo !== modelsData.turbo);
}
function updateModelActions() {
  const select = $("model-select"), known = modelsData && modelsData.models.length > 0;
  select.disabled = mutationBusy || refreshing || !known || modelsData.busy;
  const choice = modelChoice(), supported = choice?.efforts.includes(draftEffort);
  $("model-save").disabled = select.disabled || !modelDraftChanged();
  $("effort-range").disabled = select.disabled || !supported;
  $("effort-reset").disabled = select.disabled || !choice?.efforts.includes(choice.default_effort);
  $("turbo-toggle").disabled = select.disabled || !choice || (!choice.turbo_available && draftTurbo !== true);
}
function renderTurbo() {
  const choice = modelChoice(), saved = choice?.model === modelsData?.selected && draftTurbo === modelsData?.turbo;
  $("turbo-toggle").setAttribute("aria-pressed", draftTurbo === null ? "mixed" : String(draftTurbo));
  $("turbo-badge").hidden = draftTurbo !== true;
  const inherited = "Швидкість успадковується з runtime; окремий вибір у Oak ще не збережено.";
  let text;
  if (!modelsData) text = modelsUnavailable ? "Каталог недоступний. Підтримку Turbo не перевірено; останній перевірений вибір не змінюємо." : "Перевіряємо підтримку Turbo (Fast)…";
  else if (!choice) text = "Підтримку Turbo цієї моделі не підтверджено." + (draftTurbo === null ? " " + inherited : "");
  else if (!choice.turbo_available) text = draftTurbo === true ? "Збережений Turbo увімкнено, але каталог не підтверджує підтримку. Можна підготувати його вимкнення." : draftTurbo !== modelsData.turbo ? "Чернетка: Turbo буде вимкнено після збереження й підтвердження." : "Каталог цієї моделі не повідомив підтримку Turbo (Fast)." + (draftTurbo === null ? " " + inherited : " Збережено стандартний режим.");
  else if (draftTurbo === null) text = inherited + " Кнопка Turbo підготує Fast для збереження.";
  else text = (saved ? "Поточний вибір: " : "Чернетка: ") + (draftTurbo ? "Turbo (Fast) увімкнено." : "Стандартний режим; Turbo вимкнено.") + (saved ? "" : " Потрібні збереження й підтвердження.");
  if (draftTurbo === true) text += " Турбо витрачає включені ліміти підписки у 2,5 раза швидше.";
  message("turbo-status", text);
}
function renderEffort() {
  const choice = modelChoice(), levels = choice?.efforts || [], index = levels.indexOf(draftEffort), range = $("effort-range");
  range.max = String(Math.max(0, levels.length - 1));
  range.value = String(Math.max(0, index));
  range.classList.toggle("unselected", index < 0);
  $("effort-slider").classList.toggle("unselected", index < 0);
  $("effort-control").dataset.ultra = String(index >= 0 && draftEffort === "ultra");
  $("effort-progress").max = Math.max(1, levels.length - 1);
  $("effort-progress").value = Math.max(0, index);
  $("effort-ticks").replaceChildren(...levels.map((_, position) => {
    const tick = element("span"); tick.dataset.passed = String(index >= position); return tick;
  }));
  const name = effortName(draftEffort);
  const saved = choice?.model === modelsData?.selected && draftEffort === modelsData?.effort;
  $("effort-value").textContent = draftEffort ? name : "Не визначено";
  $("effort-model").textContent = modelName($("model-select").value) || "Модель не перевірено";
  const text = !modelsData ? modelsUnavailable ? "Каталог недоступний. Збережений рівень не змінюємо." : "Перевіряємо доступні рівні…" : !levels.length ? "Runtime не повідомив доступні рівні міркування для цієї моделі." : index < 0 ? (draftEffort ? "Збережений рівень «" + name + "» відсутній у каталозі. " : "Рівень не визначено. ") + (levels.includes(choice.default_effort) ? "Кнопка скидання обере стандартний рівень лише для чернетки." : "Стандартний рівень недоступний; вибір міркування не змінюємо.") : (saved ? "Поточний рівень: " : "Чернетка: ") + name + ". Зміни діятимуть з наступного запиту після збереження й підтвердження.";
  range.setAttribute("aria-valuetext", index >= 0 ? name : "Рівень не обрано: " + name);
  message("effort-status", text);
  renderTurbo();
  updateModelActions();
}
function renderModels() {
  const select = $("model-select"), selected = modelsData?.selected || lastModel;
  select.replaceChildren();
  const listed = modelsData?.models.some(row => row.model === selected);
  if (!listed) {
    const current = element("option", selected ? selected + (modelsData ? " — збережений вибір, зараз недоступний у каталозі" : modelsUnavailable ? " — збережений вибір; каталог недоступний" : " — перевіряємо каталог…") : modelsUnavailable ? "Каталог недоступний" : "Завантажуємо каталог…");
    current.value = selected; current.disabled = true; current.selected = true;
    select.append(current);
  }
  for (const row of modelsData?.models || []) {
    const option = element("option", row.display_name || row.model);
    option.value = row.model; select.append(option);
  }
  select.value = selected;
  draftEffort = modelsData ? modelsData.effort : lastEffort;
  draftTurbo = modelsData ? modelsData.turbo : lastTurbo;
  $("model-current").textContent = selected ? (modelsData || panelData ? "Обрана модель: " : "Останній перевірений вибір: ") + modelName(selected) : "Обрану модель ще не перевірено.";
  message("model-status", modelsUnavailable ? "Каталог моделей недоступний. Збережений вибір не змінюємо. Натисни «Оновити»." : !modelsData ? "Завантажуємо каталог моделей…" : modelsData.busy ? "Oak працює або готує запит у цій сесії. Модель можна змінити після завершення роботи." : !modelsData.models.length ? "У каталозі немає моделей для вибору. Збережений вибір не змінюємо." : !listed ? "Збережено модель, якої зараз немає в каталозі. Обери іншу лише якщо хочеш змінити вибір." : "Вибір стосується лише сесії «" + selectedName() + "».", modelsUnavailable);
  renderEffort();
}
async function loadModels(epoch) {
  try {
    const value = await api("/api/models");
    if (epoch !== sessionEpoch) return false;
    if (!value || typeof value.selected !== "string" || !value.selected || (value.effort !== null && typeof value.effort !== "string") || (value.turbo !== null && typeof value.turbo !== "boolean") || !Array.isArray(value.models) || typeof value.busy !== "boolean") throw new Error("Каталог недоступний.");
    const seen = new Set();
    const models = value.models.filter(row => row && typeof row.model === "string" && row.model && typeof row.display_name === "string" && !seen.has(row.model) && seen.add(row.model)).map(row => {
      const efforts = Array.isArray(row.efforts) ? [...new Set(row.efforts.filter(effort => typeof effort === "string" && effort))] : [];
      return {model: row.model, display_name: row.display_name, efforts, default_effort: efforts.includes(row.default_effort) ? row.default_effort : null, turbo_available: row.turbo_available === true};
    });
    modelsData = {selected: value.selected, effort: value.effort, turbo: value.turbo, models, busy: value.busy};
    lastModel = value.selected;
    lastEffort = value.effort;
    lastTurbo = value.turbo;
    modelsUnavailable = false;
    renderModels();
    return true;
  } catch {
    if (epoch === sessionEpoch) {
      modelsData = null;
      modelsUnavailable = true;
      renderModels();
    }
    return false;
  }
}
async function changeModel() {
  const model = $("model-select").value, epoch = sessionEpoch;
  const choice = modelChoice(), effort = choice?.efforts.includes(draftEffort) ? draftEffort : undefined;
  if (mutationBusy || refreshing || $("workspace").hidden || !modelsData || modelsData.busy || !modelDraftChanged()) return;
  const turbo = typeof draftTurbo === "boolean" && (draftTurbo !== modelsData.turbo || (model !== modelsData.selected && (choice.turbo_available || modelsData.turbo === true))) ? draftTurbo : undefined;
  const effortText = effort === undefined ? " Доступний рівень міркування не повідомлено; його окремо не обираємо." : " Рівень міркування: «" + effortName(effort) + "».";
  const turboText = draftTurbo === null ? " Швидкість успадковується з runtime; окремий Turbo не обираємо." : draftTurbo ? " Turbo (Fast) увімкнено. Турбо витрачає включені ліміти підписки у 2,5 раза швидше." : " Turbo вимкнено; буде обрано стандартний режим.";
  if (!await confirmAction("Зберегти налаштування моделі?", "Для сесії «" + selectedName() + "» буде обрано «" + modelName(model) + "» з наступного запиту." + effortText + turboText + " Контекст і пам’ять сесії зберігаються.", "Зберегти вибір") || epoch !== sessionEpoch || $("workspace").hidden) { if (epoch === sessionEpoch) renderModels(); return; }
  mutationBusy = true;
  updateActions();
  let failure = null;
  try {
    const value = await api("/api/models", {confirmed: true, model, ...(effort === undefined ? {} : {effort}), ...(turbo === undefined ? {} : {turbo})});
    if (epoch !== sessionEpoch) return;
    if (value?.selected !== model || (value.effort !== null && typeof value.effort !== "string") || (value.turbo !== null && typeof value.turbo !== "boolean") || (effort !== undefined && value.effort !== effort) || (turbo !== undefined && value.turbo !== turbo)) throw new Error("Вибір не підтверджено.");
    lastModel = value.selected;
    lastEffort = value.effort;
    lastTurbo = value.turbo;
  } catch (error) { failure = error; }
  try {
    if (epoch !== sessionEpoch) return;
    modelsData = null;
    const results = await Promise.allSettled([loadModels(epoch), loadPanel(epoch)]);
    if (epoch !== sessionEpoch) return;
    const checked = results.some(result => result.status === "fulfilled" && result.value === true);
    if (failure) {
      const reason = failure.status === 409 ? "Зміна моделі або міркування недоступна під час роботи або підготовки запиту." : failure.status === 400 ? "Цю модель або рівень міркування не вдалося обрати." : failure.status === 503 ? "Під час зміни каталог моделей був недоступний." : "Відповідь на зміну моделі або міркування не отримано.";
      message("notice", reason + (checked ? " Поточний вибір перевірено на сервері." : " Поточний вибір недоступний; натисни «Оновити» перед новою спробою."), true);
    } else if (!checked) message("notice", "Запит прийнято, але поточний вибір не вдалося перевірити. Натисни «Оновити».", true);
    else message("notice", "Налаштування моделі збережено для сесії «" + selectedName() + "». Зміна діятиме з наступного запиту.");
  } finally {
    if (epoch === sessionEpoch) mutationBusy = false;
    updateActions();
  }
}

function selectedName() {
  return sessionsData?.sessions.find(session => session.id === selectedSession)?.name || (selectedSession === "telegram" ? "Загальна" : "Вибрана тема");
}
function renderSelectedSession() {
  const name = selectedName();
  $("selected-session").textContent = "Сесія: " + name;
  $("selected-session").title = name;
  $("selected-session").hidden = !authenticated;
  $("status-context").textContent = "Сесія «" + name + "» у Telegram";
  $("task-create-zone").textContent = "Часовий пояс: " + (timezone || "не повідомлено");
  $("tasks-timezone").textContent = "Задачі сесії «" + name + "»" + (timezone ? ". Часовий пояс: " + timezone : "");
  $("integration-scope").textContent = integrationData ? integrationData.scope === "telegram" ? "Можливості сесії «" + name + "»" : "Інтеграції середовища Oak. Доступність у вибраній темі перевір у Telegram." : "Можливості сесії «" + name + "»";
}
function topicKey(kind, session, name) {
  return kind === "create" ? "create:" + name : "rename:" + session.id;
}
function renderTopicCapability() {
  const enabled = sessionsData?.topics_enabled === true;
  $("create-topic").disabled = mutationBusy || refreshing || !enabled || topicBlocked.has(topicKey("create", null, $("topic-name").value.trim()));
  $("topic-name").disabled = mutationBusy || !enabled;
  $("topic-capability").textContent = !sessionsData ? "Підтримку тем не вдалося перевірити. Натисни «Оновити»." : !enabled ? "Створення тем недоступне. Власник бота має увімкнути теми в приватних чатах через BotFather; після цього натисни «Оновити»." : sessionsData.users_can_create_topics === false ? "Oak може створити тему. Створення тем самим користувачем у Telegram вимкнено в налаштуваннях бота." : "Тема з’явиться в Telegram. Перейменування змінить її назву і тут, і в Telegram.";
}
function renderSessions(value) {
  if (!value || !Array.isArray(value.sessions) || !value.sessions.some(session => session.id === "telegram")) throw new Error("Не вдалося прочитати сесії.");
  sessionsData = value;
  const focused = document.activeElement?.dataset.session;
  const list = $("sessions-list");
  list.replaceChildren();
  for (const session of value.sessions) {
    if (typeof session.name !== "string" || (session.id !== "telegram" && !/^topic:[1-9][0-9]*$/.test(session.id))) continue;
    const selected = session.id === selectedSession;
    const item = element("li", undefined, "item session-item" + (selected ? " selected" : ""));
    const heading = element("div", undefined, "item-heading");
    const avatar = element("span", Array.from(session.name.trim())[0]?.toLocaleUpperCase("uk-UA") || "#", "avatar");
    avatar.setAttribute("aria-hidden", "true");
    heading.append(avatar, element("h3", session.name));
    if (session.closed) heading.append(element("span", "Тему закрито", "badge warning"));
    else if (selected) heading.append(element("span", "Вибрана", "badge good"));
    const state = session.awaiting_confirmation > 0 ? "Чекає на підтвердження" : session.active ? "Oak працює" : session.initialized ? "Очікує повідомлення" : "Розмову ще не розпочато";
    item.append(heading, element("p", state, "item-description"));
    const facts = element("dl", undefined, "inventory-facts");
    fact(facts, "Записів пам’яті", count(session.memory_count));
    fact(facts, "Задач", count(session.task_count));
    if (session.awaiting_confirmation > 0) fact(facts, "Підтверджень", count(session.awaiting_confirmation));
    item.append(facts);
    const actions = element("div", undefined, "item-actions"), button = element("button", selected ? "Вибрано для перегляду" : "Переглянути сесію", "quiet");
    button.type = "button";
    button.dataset.session = session.id;
    button.setAttribute("aria-pressed", String(selected));
    button.setAttribute("aria-label", "Переглянути сесію «" + session.name + "»");
    button.addEventListener("click", () => { void selectSession(session.id); });
    actions.append(button); item.append(actions);
    if (session.id !== "telegram") {
      const remove = element("button", "Видалити тему", "danger");
      remove.type = "button"; remove.dataset.mutation = "delete-topic";
      remove.setAttribute("aria-label", "Видалити тему «" + session.name + "»");
      remove.addEventListener("click", () => { void deleteTopic(session); });
      actions.append(remove);
      const details = element("details", undefined, "topic-rename"), form = element("form", undefined, "topic-form");
      const input = element("input"), inputId = "rename-" + session.id.slice(6);
      input.id = inputId; input.value = session.name; input.maxLength = 128; input.required = true; input.autocomplete = "off"; input.pattern = ".*\\S.*";
      const inputLabel = element("label", "Нова назва теми"); inputLabel.htmlFor = inputId;
      const row = element("div", undefined, "topic-form-row"), save = element("button", "Зберегти назву", "quiet");
      save.type = "submit"; save.dataset.mutation = "rename";
      save.dataset.locked = topicBlocked.has(topicKey("rename", session)) ? "true" : "false";
      row.append(input, save); form.append(inputLabel, row);
      if (save.dataset.locked === "true") form.append(element("p", "Результат перейменування невідомий. Онови список і перевір назву теми в Telegram. Повторний запит заблоковано до перевірки оператором.", "item-description warning"));
      form.addEventListener("submit", event => { event.preventDefault(); void changeTopic("rename", session, input.value); });
      details.append(element("summary", "Перейменувати тему"), form); item.append(details);
    }
    list.append(item);
  }
  message("sessions-status", "");
  renderSelectedSession();
  updateActions();
  if (focused) Array.from(document.querySelectorAll("[data-session]")).find(button => button.dataset.session === focused)?.focus({preventScroll: true});
}
async function loadSessions(epoch, background = false) {
  try {
    const value = await api("/api/sessions");
    if (epoch !== sessionEpoch || (background && sessionRefreshBlocked())) return false;
    if (!value || !Array.isArray(value.sessions) || !value.sessions.some(session => session.id === "telegram")) throw new Error("Не вдалося прочитати сесії.");
    if (surfaceLaunch && (!/^[A-Za-z0-9_-]{1,64}$/.test(surfaceLaunch.id) || (surfaceLaunch.conversation !== "telegram" && !/^topic:[1-9][0-9]*$/.test(surfaceLaunch.conversation)))) {
      surfaceLaunch = null;
      message("notice", "Посилання на картку недійсне. Відкрий нове посилання від Oak у Telegram.", true);
    }
    if (initialConversation !== null) {
      const found = value.sessions.some(session => session.id === initialConversation && typeof session.name === "string" && (session.id === "telegram" || /^topic:[1-9][0-9]*$/.test(session.id)));
      selectedSession = found ? initialConversation : "telegram";
      if (!found) {
        surfaceLaunch = null;
        message("notice", "Сесія з посилання недоступна або її видалено. Картку не відкрито. Показуємо сесію «Загальна».", true);
      }
      initialConversation = null;
    }
    if (Array.isArray(value.sessions) && value.sessions.some(session => session.id === "telegram") && !value.sessions.some(session => session.id === selectedSession)) {
      surfaceLaunch = null;
      selectedSession = "telegram";
      resetSessionView();
      message("notice", "Вибрану тему видалено. Показуємо сесію «Загальна».");
    }
    renderSessions(value);
    return true;
  } catch {
    if (background) return false;
    if (epoch === sessionEpoch) {
      sessionsData = null;
      $("sessions-list").replaceChildren();
      message("sessions-status", "Список сесій недоступний. Натисни «Оновити».", true);
      updateActions();
    }
    return false;
  }
}
function sessionRefreshBlocked() {
  return !authenticated || document.hidden || $("workspace").hidden || refreshing || mutationBusy || computerChanging || document.querySelector("dialog[open]") || document.activeElement?.closest(".topic-form, #create-session") || document.querySelector(".topic-rename[open]");
}
async function syncSessions() {
  if (syncingSessions || sessionRefreshBlocked()) return;
  syncingSessions = true;
  const epoch = sessionEpoch, previousSession = selectedSession;
  try {
    await loadSessions(epoch, true);
    if (epoch === sessionEpoch && previousSession !== selectedSession) await refresh();
    else if (epoch === sessionEpoch && surfaceLaunch && sessionsData) void dynamicPanel();
  } finally { syncingSessions = false; }
}
async function selectSession(id) {
  if (id === selectedSession || mutationBusy || confirmationPending || $("confirmation").open || !sessionsData?.sessions.some(session => session.id === id)) return;
  panelPoll?.reset();
  sessionEpoch++;
  initialConversation = null;
  surfaceLaunch = null;
  selectedSession = id;
  refreshing = false;
  resetSessionView();
  renderSessions(sessionsData);
  await refresh();
}
function resetSessionView() {
  resetMemory();
  resetTaskCreate();
  a2uiPanel?.reset();
  inputRequestsPanel?.reset();
  approvalBlocked.clear();
  $("approvals-list").replaceChildren();
  delete $("approvals-list").dataset.snapshot;
  $("approvals-panel").hidden = true;
  panelData = integrationData = null;
  modelsData = null; lastModel = ""; modelsUnavailable = false; draftEffort = lastEffort = null; draftTurbo = lastTurbo = null;
  renderModels();
  stopRequestState = "";
  appLinks.clear(); oauthLinks.clear(); oauthBlocked.clear(); openIntegrations.clear();
  $("session-state").textContent = "Перевіряємо стан…";
  $("session-description").textContent = "Завантажуємо дані вибраної сесії.";
  $("session-indicator").className = "status-dot";
  for (const name of ["confirmations", "uncertain-inputs", "memory-count"]) { $(name).textContent = "—"; setAttention(name, false); }
  renderTaskSummary(null);
  $("tasks-list").replaceChildren();
  $("integrations-content").replaceChildren();
  $("settings-list").replaceChildren();
  message("tasks-status", "Завантажуємо задачі вибраної сесії…");
  message("integrations-status", "Завантажуємо інтеграції вибраної сесії…");
  message("panel-error", ""); message("notice", "");
}
async function changeTopic(kind, session, rawName) {
  const name = rawName.trim(), epoch = sessionEpoch;
  if (mutationBusy || refreshing || $("workspace").hidden || (kind === "create" && sessionsData?.topics_enabled !== true)) return;
  if (!name || Array.from(name).length > 128) { message("topic-operation", "Вкажи назву від 1 до 128 символів.", true); message("notice", "Вкажи назву від 1 до 128 символів.", true); return; }
  const key = topicKey(kind, session, name);
  if (topicBlocked.has(key)) return;
  const creating = kind === "create";
  if (!await confirmAction(creating ? "Створити тему в Telegram?" : "Перейменувати тему в Telegram?", creating ? "Oak створить тему «" + name + "» з окремим контекстом, пам’яттю та задачами." : "Назва теми «" + session.name + "» зміниться на «" + name + "» у Telegram і панелі Oak.", creating ? "Створити тему" : "Зберегти назву") || epoch !== sessionEpoch || $("workspace").hidden) return;
  mutationBusy = true;
  updateActions();
  let dispatched = false, removed = false;
  try {
    const operationId = crypto.randomUUID();
    topicBlocked.add(key);
    dispatched = true;
    const value = await api(creating ? "/api/sessions" : "/api/sessions/" + encodeURIComponent(session.id) + "/rename", {confirmed: true, name, operation_id: operationId});
    if (epoch !== sessionEpoch) return;
    if (!value.session || (creating && value.state !== "created")) throw new Error("Результат не підтверджено.");
    topicBlocked.delete(key);
    if (creating) $("topic-name").value = "";
    message("topic-operation", "");
    message("notice", creating ? "Тему створено в Telegram. Обери її в списку, щоб переглянути стан." : "Назву теми змінено в Telegram.");
    const previousSession = selectedSession;
    await loadSessions(epoch);
    removed = selectedSession !== previousSession;
  } catch (error) {
    if (epoch === sessionEpoch) {
      if (!dispatched || (error.status >= 400 && error.status < 500 && ![408, 409, 429].includes(error.status))) topicBlocked.delete(key);
      removed = error.status === 404;
      const uncertain = topicBlocked.has(key);
      const text = uncertain ? "Результат зміни невідомий. Онови список і перевір тему в Telegram. Цей запит не повторюємо; для нової спроби з цією темою потрібна перевірка оператором." : "Зміну не прийнято. Перевір назву, доступність тем і натисни «Оновити».";
      message("topic-operation", text, true);
      message("notice", text, true);
      if (sessionsData) renderSessions(sessionsData);
    }
  } finally {
    if (epoch === sessionEpoch) mutationBusy = false;
    updateActions();
  }
  if (removed && epoch === sessionEpoch) await refresh();
}
async function deleteTopic(session) {
  const epoch = sessionEpoch;
  if (session.id === "telegram" || mutationBusy || refreshing || $("workspace").hidden) return;
  if (!await confirmAction("Видалити тему «" + session.name + "»?", "Тема та її повідомлення будуть видалені з Telegram, а сесія зникне з панелі Oak. Цю дію неможливо скасувати.", "Видалити тему", true) || epoch !== sessionEpoch || $("workspace").hidden) return;
  mutationBusy = true;
  updateActions();
  let deleted = false;
  try {
    const value = await api("/api/sessions/" + encodeURIComponent(session.id) + "/delete", {confirmed: true});
    if (epoch !== sessionEpoch) return;
    if (value?.state !== "deleted" || value.id !== session.id) throw new Error("Видалення не підтверджено.");
    deleted = true;
  } catch (error) {
    if (epoch !== sessionEpoch) return;
    deleted = error.status === 404;
    if (!deleted) message("notice", error.status === 409 ? "Тему зараз не можна видалити. Онови стан сесії та повтори спробу після завершення її роботи." : error.status >= 400 && error.status < 500 && ![408, 429].includes(error.status) ? "Видалення відхилено. Онови список і перевір доступ до теми в Telegram." : "Відповідь на видалення не отримано. Онови список і перевір тему в Telegram перед новою спробою.", true);
  } finally {
    if (epoch === sessionEpoch) mutationBusy = false;
    updateActions();
  }
  if (epoch !== sessionEpoch || !deleted) return;
  topicBlocked.delete(topicKey("rename", session));
  topicBlocked.delete(topicKey("create", null, session.name));
  await refresh();
  if (epoch === sessionEpoch) message("notice", "Тему видалено з Telegram і панелі Oak.");
}

function requireLogin(text = "Сеанс завершився. Відкрий Oak у Telegram або увійди за приватним ключем.") {
  panelPoll?.reset();
  resetMemory();
  resetTaskCreate();
  approvalBlocked.clear();
  $("approvals-list").replaceChildren();
  delete $("approvals-list").dataset.snapshot;
  $("approvals-panel").hidden = true;
  if (surfaceLaunch) { initialConversation = surfaceLaunch.conversation; surfaceLaunch.opened = false; }
  remotePanel?.reset();
  a2uiPanel?.reset();
  inputRequestsPanel?.reset();
  sessionEpoch++;
  sessionToken = "";
  authenticated = false;
  refreshing = false;
  mutationBusy = false;
  panelData = null;
  integrationData = null;
  modelsData = null; lastModel = ""; modelsUnavailable = false; draftEffort = lastEffort = null; draftTurbo = lastTurbo = null;
  renderModels();
  appLinks.clear(); oauthLinks.clear(); oauthBlocked.clear(); openIntegrations.clear();
  stopRequestState = "";
  computerChanging = false;
  selectedSession = "telegram";
  sessionsData = null;
  topicBlocked.clear();
  $("sessions-list").replaceChildren();
  $("topic-name").value = "";
  renderSelectedSession();
  renderComputer(null);
  $("workspace").hidden = true;
  $("login").hidden = false;
  $("key").value = "";
  renderTaskSummary(null);
  setConnection("Потрібен вхід", "off");
  if ($("confirmation").open) $("confirmation").close("cancel");
  message("error", text, true);
  message("notice", "");
}

async function api(path, data, {signal} = {}) {
  const epoch = sessionEpoch, controller = new AbortController();
  const abort = () => controller.abort();
  if (signal?.aborted) abort();
  else signal?.addEventListener("abort", abort, {once: true});
  const endpoint = path.split("?")[0];
  if (/^\/api\/(?:panel|models|tasks(?:\/.*)?|memory(?:\/.*)?|activity|artifacts|recovery(?:\/.*)?|integrations(?:\/.*)?|decision|a2ui(?:\/.*)?|stop)$/.test(endpoint)) {
    const url = new URL(path, location.origin);
    url.searchParams.set("conversation", selectedSession);
    path = url.pathname + url.search;
  }
  const timeout = setTimeout(() => controller.abort(), endpoint === "/api/integrations" ? 90000 : endpoint === "/api/remote/start" ? 45000 : 15000);
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
  } finally { clearTimeout(timeout); signal?.removeEventListener("abort", abort); }
}

function updateActions() {
  remotePanel?.update();
  a2uiPanel?.disable();
  $("refresh").disabled = refreshing || mutationBusy;
  for (const button of document.querySelectorAll("[data-mutation]")) button.disabled = mutationBusy || refreshing || button.dataset.locked === "true";
  $("stop").disabled = mutationBusy || refreshing || !panelData?.session?.active || !!stopRequestState;
  $("computer-switch").disabled = mutationBusy || refreshing || $("computer-switch").dataset.locked !== "false";
  for (const button of document.querySelectorAll("[data-session]")) button.disabled = mutationBusy || confirmationPending || button.dataset.session === selectedSession;
  updateModelActions();
  renderTopicCapability();
}
function confirmAction(title, description, action, danger = false) {
  if (confirmationPending) return Promise.resolve(false);
  confirmationPending = true; updateActions();
  const native = telegramLaunch ? telegramBridge?.confirm(title + "\n\n" + description) : null;
  const result = native || new Promise(resolve => {
    const dialog = $("confirmation");
    $("confirmation-title").textContent = title;
    $("confirmation-description").textContent = description;
    $("confirmation-submit").textContent = action;
    $("confirmation-submit").classList.toggle("danger", danger);
    dialog.returnValue = "cancel";
    dialog.addEventListener("close", () => resolve(dialog.returnValue === "confirm"), {once: true});
    dialog.showModal(); telegramBridge?.update();
  });
  return result.finally(() => { confirmationPending = false; updateActions(); telegramBridge?.update(); });
}
async function mutation(title, description, action, work, danger = false) {
  if (mutationBusy || refreshing || $("workspace").hidden) return;
  const requestedEpoch = sessionEpoch;
  if (!await confirmAction(title, description, action, danger) || requestedEpoch !== sessionEpoch || $("workspace").hidden) return;
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

function renderComputer(value) {
  const button = $("computer-switch");
  const known = value && ["configured", "enabled", "busy"].every(key => typeof value[key] === "boolean");
  const configured = known && value.configured;
  button.dataset.locked = configured ? "false" : "true";
  if (known) button.setAttribute("aria-checked", configured && value.enabled ? "true" : "false");
  $("computer-switch-state").textContent = computerChanging ? "Змінюємо…" : !known ? "Недоступно" : !configured ? "Не налаштовано" : value.enabled ? "Увімкнено" : "Вимкнено";
  $("computer-status").textContent = computerChanging ? "Змінюємо доступ. Новий стан ще не підтверджено." : !known ? "Стан доступу недоступний. Натисни «Оновити»." : !configured ? "Керування комп’ютером не налаштовано на сервері Oak." : !value.enabled ? "Oak не має дозволу керувати комп’ютером у твоїх сесіях." : value.busy ? "Доступ увімкнено. Комп’ютер зараз використовується." : "Доступ увімкнено для всіх твоїх сесій у Telegram.";
}

async function toggleComputer() {
  const state = panelData?.computer;
  if (mutationBusy || refreshing || $("workspace").hidden || !state?.configured || typeof state.enabled !== "boolean") return;
  const enabled = !state.enabled, epoch = sessionEpoch;
  mutationBusy = computerChanging = true;
  renderComputer(state);
  updateActions();
  let failure = null;
  try { await api("/api/computer", {enabled, confirmed: true}); }
  catch (error) { failure = error; }
  try {
    if (epoch !== sessionEpoch) return;
    const updated = await loadPanel(epoch);
    if (epoch !== sessionEpoch) return;
    if (failure) message("notice", failure.status === 403 ? failure.message : updated ? "Відповідь на зміну не отримано. Поточний стан доступу перевірено на сервері." : "Відповідь на зміну не отримано. Стан доступу недоступний; натисни «Оновити» перед новою спробою.", true);
    else if (!updated) message("notice", "Запит прийнято, але поточний стан доступу перевірити не вдалося. Натисни «Оновити».", true);
    else if (typeof panelData.computer?.configured !== "boolean" || typeof panelData.computer?.enabled !== "boolean") message("notice", "Стан доступу недоступний. Натисни «Оновити».", true);
    else message("notice", panelData.computer?.configured ? panelData.computer.enabled ? "Керування комп’ютером увімкнено." : "Керування комп’ютером вимкнено." : "Керування комп’ютером не налаштовано на сервері Oak.");
  } finally {
    if (epoch === sessionEpoch) {
      mutationBusy = computerChanging = false;
      renderComputer(panelData?.computer);
    }
    updateActions();
  }
}

function renderApprovals(items) {
  const list = $("approvals-list"), rows = Array.isArray(items) ? items : [];
  const snapshot = JSON.stringify([rows, [...approvalBlocked]]);
  if (list.dataset.snapshot === snapshot) return;
  list.dataset.snapshot = snapshot;
  list.replaceChildren(); $("approvals-panel").hidden = rows.length === 0;
  const states = {accepted: "Дозволено", declined: "Відхилено", answered: "Відповідь прийнято", expired: "Прострочено", cancelled: "Скасовано"};
  for (const approval of rows) {
    if (!/^[a-f0-9]{10}$/.test(approval.id)) continue;
    const item = element("li", undefined, "item"), pending = approval.state === "pending";
    item.append(element("h3", approval.summary || "Дозволити дію?"));
    const status = element("p", pending ? "Очікує відповіді · до " : states[approval.state] || "Стан невідомий", "hint");
    if (pending) status.append(dateNode(approval.expires));
    item.append(status);
    if (pending) {
      const actions = element("div", undefined, "item-actions");
      for (const [accepted, label] of [[true, "Дозволити"], [false, "Відхилити"]]) {
        const button = element("button", label, accepted ? "" : "quiet"); button.type = "button";
        button.dataset.mutation = "approval"; button.dataset.locked = String(approvalBlocked.has(approval.id));
        button.disabled = mutationBusy || refreshing || approvalBlocked.has(approval.id);
        button.addEventListener("click", () => { void decideApproval(approval.id, accepted); });
        actions.append(button);
      }
      item.append(actions);
      if (approvalBlocked.has(approval.id)) item.append(element("p", "Передавання потребує перевірки. Оновлюємо стан без повторної відповіді.", "hint"));
    }
    list.append(item);
  }
}
async function decideApproval(id, accepted) {
  if (mutationBusy || refreshing || approvalBlocked.has(id) || $("workspace").hidden) return;
  const epoch = sessionEpoch;
  telegramBridge?.feedback();
  approvalBlocked.add(id); mutationBusy = true;
  renderApprovals(panelData?.session?.legacy_approvals); updateActions();
  try {
    const result = await api("/api/decision", {kind: "approval", id, accepted});
    if (epoch !== sessionEpoch) return;
    message("notice", result.message);
  } catch (error) {
    if (epoch !== sessionEpoch) return;
    message("notice", [400, 403, 404, 409].includes(error.status) ? "Запит недоступний або вже завершено. Перевіряємо стан." : "Відповідь могла бути прийнята. Перевіряємо стан без повторного надсилання.", true);
  } finally {
    if (epoch === sessionEpoch) { mutationBusy = false; await loadPanel(epoch); updateActions(); }
  }
}

function renderPanel(value, live = false) {
  if (!value || !value.session || !value.settings) throw new Error("Не вдалося прочитати стан Oak. Натисни «Оновити».");
  panelData = value;
  authenticated = true;
  renderSelectedSession();
  timezone = typeof value.settings.timezone === "string" ? value.settings.timezone : "";
  const session = value.session;
  renderApprovals(session.legacy_approvals);
  const pendingInputs = Array.isArray(session.input_requests) ? session.input_requests : [];
  const inputAttention = Array.isArray(session.input_request_attention) ? session.input_request_attention : [];
  const requestKey = JSON.stringify([pendingInputs, inputAttention]);
  const requestsChanged = $("pending-input-requests").dataset.requests !== requestKey;
  if (requestsChanged) $("pending-input-requests").replaceChildren();
  $("pending-input-requests").dataset.requests = requestKey;
  $("input-requests-panel").hidden = pendingInputs.length + inputAttention.length === 0;
  if (requestsChanged) for (const {id, attention} of [...pendingInputs.map(id => ({id})), ...inputAttention.map(item => ({id: item.id, attention: true}))]) {
    if (!/^[a-f0-9]{32}$/.test(id)) continue;
    const button = element("button", attention ? "Перевірити передавання" : "Відкрити запит", "quiet"); button.type = "button";
    button.addEventListener("click", () => { void inputRequestPanel().then(panel => panel.open(id)); });
    $("pending-input-requests").append(button);
  }
  if (!session.active) stopRequestState = "";
  let title, description, indicator = "";
  if (stopRequestState) {
    title = stopRequestState === "requested" ? "Зупинку запитано" : "Очікуємо перевірки зупинки";
    description = "Завершення роботи ще не підтверджено. Натисни «Оновити», щоб перевірити стан.";
    indicator = "waiting";
  } else if (pendingInputs.length) {
    title = "Потрібні дані";
    description = "Відкрий запит у цій панелі. Oak чекає на відповідь у поточній задачі.";
    indicator = "waiting";
  } else if (inputAttention.length) {
    title = "Передавання потребує перевірки";
    description = "Відповідь збережено. Перевір стан передавання до початкової задачі; повторної відповіді не надсилаємо.";
    indicator = "waiting";
  } else if (session.awaiting_confirmation > 0) {
    title = "Чекає на підтвердження";
    description = "Переглянь підтвердження нижче або відповідай у вибраній темі Telegram.";
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
    description = session.initialized ? "Вибрана тема в Telegram готова до наступного повідомлення." : "Напиши Oak у вибраній темі Telegram, щоб почати розмову.";
  }
  if (session.turn_status && !stopRequestState) description += " Останнє виконання: " + label(turnLabels, session.turn_status).toLocaleLowerCase("uk-UA") + ".";
  $("session-state").textContent = title;
  $("session-description").textContent = description;
  $("session-indicator").className = "status-dot" + (indicator ? " " + indicator : "");
  $("confirmations").textContent = count(session.awaiting_confirmation);
  $("uncertain-inputs").textContent = count(session.uncertain_inputs);
  $("memory-count").textContent = count(value.memory?.count);
  setAttention("confirmations", session.awaiting_confirmation > 0);
  setAttention("uncertain-inputs", session.uncertain_inputs > 0);
  const telegramURL = safeHTTPS(value.telegram_url);
  const canClose = telegramLaunch && typeof window.Telegram?.WebApp?.close === "function";
  $("return-telegram").hidden = !canClose;
  $("telegram-link").hidden = canClose || !telegramURL;
  if (telegramURL) $("telegram-link").href = telegramURL;
  else $("telegram-link").removeAttribute("href");
  if (!live) {
  const settings = value.settings, list = $("settings-list");
  if (typeof settings.model === "string" && settings.model) lastModel = settings.model;
  if (settings.effort === null || typeof settings.effort === "string") lastEffort = settings.effort;
  if (settings.turbo === null || typeof settings.turbo === "boolean") lastTurbo = settings.turbo;
  if (!modelsData) renderModels();
  list.replaceChildren();
  fact(list, "Модель", settings.model || "Не повідомлено");
  fact(list, "Рівень міркування", typeof settings.effort === "string" && settings.effort ? effortName(settings.effort) : "Не повідомлено");
  fact(list, "Вхід у модель", settings.auth === "chatgpt" ? "ChatGPT" : "Недоступний");
  fact(list, "Часовий пояс", settings.timezone || "Не повідомлено");
  fact(list, "Доступ до файлів", ({"workspace-write": "Файли робочої папки", "read-only": "Лише читання", "danger-full-access": "Повний доступ"})[settings.sandbox] || settings.sandbox || "Не повідомлено");
  fact(list, "Підтвердження дій", ({never: "Без інтерактивних підтверджень", "on-request": "За запитом Oak", "on-failure": "У разі обмеження", untrusted: "Для неперевірених дій"})[settings.approval_policy] || settings.approval_policy || "Не повідомлено");
  fact(list, "Транспорт", ({poll: "Опитування", sse: "Потік подій"})[settings.transport] || settings.transport || "Не повідомлено");
  }
  renderComputer(value.computer);
  message("panel-error", "");
  updateActions();
}

function clearArtifacts() {
  for (const asset of artifactAssets.values()) { asset.controller.abort(); if (asset.url) URL.revokeObjectURL(asset.url); }
  artifactAssets.clear();
}
async function loadArtifacts(before = null) {
  const epoch = sessionEpoch, generation = ++artifactsGeneration;
  if (before === null) { clearArtifacts(); $("artifacts-list").replaceChildren(); }
  $("artifacts-more").disabled = true; message("artifacts-status", "Завантажуємо файли…");
  try {
    const value = await api("/api/artifacts" + (before === null ? "" : "?before=" + before));
    if (epoch !== sessionEpoch || generation !== artifactsGeneration) return;
    for (const file of value.items) {
      const row = element("li", undefined, "item"), status = element("p", file.available ? ({image: "Зображення", video: "Відео", audio: "Аудіо", document: "Документ"}[file.kind] || "Файл") + (file.size_bytes !== null ? " · " + Math.ceil(file.size_bytes / 1024) + " КБ" : "") : "Файл більше недоступний", "hint");
      row.append(element("h3", file.name), status);
      if (file.available) {
        const actions = element("div", undefined, "item-actions");
        for (const preview of file.kind === "image" ? [true, false] : [false]) {
          const button = element("button", preview ? "Переглянути" : "Завантажити", "quiet"); button.type = "button";
          button.disabled = file.size_bytes > (preview ? 10 : 100) * 1024 * 1024;
          if (button.disabled) status.textContent = preview ? "Перегляд доступний для зображень до 10 МБ." : "Файл більший за 100 МБ. Відкрий його в Telegram.";
          button.addEventListener("click", async () => {
            button.disabled = true;
            const asset = artifactAssets.get(file.id) || {controller: new AbortController(), url: null}; artifactAssets.set(file.id, asset);
            try {
              if (!asset.url) {
                if (!asset.loading) asset.loading = artifact(file.id, {signal: asset.controller.signal, limit: 100 * 1024 * 1024, types: [file.mime]}).finally(() => { asset.loading = null; });
                const blob = await asset.loading;
                if (epoch !== sessionEpoch || asset.controller.signal.aborted || !row.isConnected) return;
                if (!asset.url) asset.url = URL.createObjectURL(blob);
              }
              if (epoch !== sessionEpoch || asset.controller.signal.aborted) return;
              if (preview) { const image = element("img"); image.alt = file.name; image.src = asset.url; image.className = "artifact-preview"; row.append(image); button.hidden = true; }
              else { const link = element("a"); link.href = asset.url; link.download = file.name; link.click(); }
              status.textContent = "Файл готовий.";
            } catch { if (epoch === sessionEpoch && !asset.controller.signal.aborted) status.textContent = "Файл не завантажено. Перевір доступ і онови список."; }
            finally { if (epoch === sessionEpoch) button.disabled = false; }
          });
          actions.append(button);
        }
        row.append(actions);
      }
      $("artifacts-list").append(row);
    }
    artifactsNext = value.next_cursor; $("artifacts-more").hidden = artifactsNext === null;
    message("artifacts-status", value.items.length || before !== null ? "" : "Файлів у цій розмові ще немає.");
  } catch { if (epoch === sessionEpoch && generation === artifactsGeneration) message("artifacts-status", "Список файлів недоступний. Онови панель.", true); }
  finally { if (epoch === sessionEpoch && generation === artifactsGeneration) $("artifacts-more").disabled = false; }
}
$("artifacts-more").addEventListener("click", () => { if (artifactsNext !== null) void loadArtifacts(artifactsNext); });

async function loadActivity(before = null) {
  const epoch = sessionEpoch, generation = ++activityGeneration;
  $("activity-more").disabled = true; message("activity-status", "Завантажуємо активність…");
  try {
    const value = await api("/api/activity" + (before === null ? "" : "?before=" + before));
    if (epoch !== sessionEpoch || generation !== activityGeneration) return;
    if (before === null) $("activity-list").replaceChildren();
    for (const run of value.items) {
      const row = element("li", undefined, "item"), heading = element("div", undefined, "item-heading");
      const states = {inProgress: "Виконання триває", completed: "Виконання завершено", failed: "Помилка виконання", interrupted: "Виконання перервано", uncertain: "Стан виконання невідомий", unknown: "Стан не визначено"};
      heading.append(element("h3", states[run.status] || states.unknown));
      row.append(heading, dateNode(run.started_at));
      if (run.duration_seconds !== null) row.append(element("p", "Тривалість: " + Math.round(run.duration_seconds).toLocaleString("uk-UA") + " с", "item-meta"));
      else row.append(element("p", run.status === "inProgress" ? "Тривалість буде відома після завершення." : "Тривалість не відома.", "item-meta"));
      if (run.tool_count) row.append(element("p", "Дій: " + count(run.tool_count) + ". " + run.tool_labels.join(" · ") + (run.tool_labels_truncated ? " · Показано частину видів дій" : ""), "item-description"));
      $("activity-list").append(row);
    }
    activityNext = value.next_cursor; $("activity-more").hidden = activityNext === null;
    message("activity-status", value.items.length || before !== null ? "" : "Історії активності поки немає.");
  } catch { if (epoch === sessionEpoch && generation === activityGeneration) message("activity-status", "Історія недоступна. Онови панель.", true); }
  finally { if (epoch === sessionEpoch && generation === activityGeneration) $("activity-more").disabled = false; }
}
$("activity-more").addEventListener("click", () => { if (activityNext !== null) void loadActivity(activityNext); });

async function loadRecovery(before = null) {
  const epoch = sessionEpoch, generation = ++recoveryGeneration;
  $("recovery-more").disabled = true; message("recovery-status", "Завантажуємо запити…");
  try {
    const value = await api("/api/recovery" + (before === null ? "" : "?before=" + before));
    if (epoch !== sessionEpoch || generation !== recoveryGeneration) return;
    if (before === null) $("recovery-list").replaceChildren();
    for (const item of value.items) {
      const row = element("li", undefined, "item"); row.append(element("h3", item.label));
      if (item.preview) row.append(element("p", item.preview, "item-description"));
      row.append(element("p", item.reviewed ? "Позначено перевіреним; результат залишається невідомим." : "Результат не підтверджено.", "hint"));
      const actions = element("div", undefined, "item-actions");
      if (!item.reviewed) {
        const button = element("button", "Я перевірив", "quiet"); button.type = "button"; button.dataset.mutation = "review";
        button.addEventListener("click", async () => {
          if (mutationBusy || refreshing) return;
          const current = sessionEpoch; mutationBusy = true; updateActions();
          try { await api("/api/recovery/" + encodeURIComponent(item.id) + "/review", {}); if (current === sessionEpoch) await loadRecovery(); }
          catch { if (current === sessionEpoch) message("recovery-status", "Не вдалося перевірити позначку. Онови список; жодну дію не повторено.", true); }
          finally { if (current === sessionEpoch) { mutationBusy = false; updateActions(); } }
        });
        actions.append(button);
      }
      if (item.retry) {
        const states = {accepted: "Новий запит прийнято", not_started: "Новий запуск не розпочато", dispatching: "Новий запит передається", uncertain: "Передавання нового запиту не підтверджено", failed: "Новий запуск завершився помилкою", cancelled: "Новий запуск скасовано"};
        row.append(element("p", (states[item.retry.state] || "Стан нового запуску невідомий") + ". Первісний результат залишається невідомим.", "hint"));
      }
      if (item.can_retry) {
        const retry = element("button", "Повторити як новий запит", "quiet"); retry.type = "button"; retry.dataset.mutation = "retry";
        retry.addEventListener("click", () => mutation("Повторити запит?", "Спершу перевір результат у зовнішньому сервісі: повтор може дублювати вже виконану дію. Буде створено новий запит; попередній залишиться невизначеним.", "Створити новий запит", async current => {
          if (!recoveryRetries.has(item.id)) recoveryRetries.set(item.id, crypto.randomUUID().replace(/-/g, ""));
          try {
            const result = await api("/api/recovery/" + encodeURIComponent(item.id) + "/retry", {confirmed: true, operation_id: recoveryRetries.get(item.id)});
            if (current !== sessionEpoch) return;
            message("notice", result.state === "accepted" ? "Новий запит прийнято. Первісний результат залишається невідомим." : "Перевір стан нового запиту нижче. Автоматичного повтору немає.");
          } catch { if (current === sessionEpoch) message("notice", "Передавання потребує перевірки. Оновлюємо стан без автоматичного повтору.", true); }
          if (current === sessionEpoch) await loadRecovery();
        }, true));
        actions.append(retry);
      }
      row.append(actions); $("recovery-list").append(row);
    }
    recoveryNext = value.next_cursor; $("recovery-more").hidden = recoveryNext === null;
    message("recovery-status", value.items.length || before !== null ? "" : "Запитів із невідомим результатом немає."); updateActions();
  } catch { if (epoch === sessionEpoch && generation === recoveryGeneration) message("recovery-status", "Не вдалося прочитати запити. Онови панель.", true); }
  finally { if (epoch === sessionEpoch && generation === recoveryGeneration) $("recovery-more").disabled = false; }
}
$("recovery-more").addEventListener("click", () => { if (recoveryNext !== null) void loadRecovery(recoveryNext); });

function resetMemory() {
  activityGeneration++; activityNext = null; $("activity-list").replaceChildren(); $("activity-more").hidden = true;
  clearArtifacts(); artifactsGeneration++; artifactsNext = null; $("artifacts-list").replaceChildren(); $("artifacts-more").hidden = true;
  recoveryGeneration++; recoveryNext = null; recoveryRetries.clear(); $("recovery-list").replaceChildren(); $("recovery-more").hidden = true;
  memoryGeneration++; memoryNext = null; memoryQuery = ""; memoryOperation = null; memoryDeletes.clear();
  $("memory-list").replaceChildren(); $("memory-more").hidden = true;
  $("memory-text").value = ""; $("memory-text").disabled = false; $("memory-query").value = "";
  $("memory-save").textContent = "Зберегти запис"; message("memory-save-status", "");
}
async function loadMemory(offset = 0) {
  const epoch = sessionEpoch, generation = ++memoryGeneration;
  if (!offset) memoryQuery = $("memory-query").value;
  $("memory-more").disabled = true;
  message("memory-status", "Завантажуємо пам’ять…");
  try {
    const value = await api("/api/memory?q=" + encodeURIComponent(memoryQuery) + "&offset=" + offset);
    if (epoch !== sessionEpoch || generation !== memoryGeneration) return;
    if (!offset) $("memory-list").replaceChildren();
    for (const note of value.items) {
      const item = element("li", undefined, "item");
      item.append(element("p", note.text, "item-description"));
      const button = element("button", "Видалити запис", "danger"); button.type = "button"; button.dataset.mutation = "memory";
      button.addEventListener("click", () => mutation("Видалити запис пам’яті?", "Запис буде видалено лише з цієї розмови. Дію неможливо скасувати.", "Видалити", async current => {
        if (!memoryDeletes.has(note.id)) memoryDeletes.set(note.id, crypto.randomUUID().replace(/-/g, ""));
        await api("/api/memory/" + encodeURIComponent(note.id) + "/delete", {confirmed: true, operation_id: memoryDeletes.get(note.id)});
        if (current !== sessionEpoch) return;
        message("notice", "Запис видалено."); await loadMemory();
      }, true));
      item.append(button); $("memory-list").append(item);
    }
    memoryNext = value.next_offset; $("memory-more").hidden = memoryNext === null;
    $("memory-context").textContent = "Пам’ять сесії «" + selectedName() + "»";
    message("memory-status", value.items.length || offset ? "" : "Записів не знайдено.");
    updateActions();
  } catch { if (epoch === sessionEpoch && generation === memoryGeneration) message("memory-status", "Не вдалося прочитати пам’ять. Повтори пошук або онови панель.", true); }
  finally { if (epoch === sessionEpoch && generation === memoryGeneration) $("memory-more").disabled = false; }
}
async function saveMemory() {
  const text = $("memory-text").value;
  if (!text.trim()) return;
  await mutation("Зберегти запис пам’яті?", "Oak використовуватиме цей запис лише у вибраній розмові.", "Зберегти", async epoch => {
    if (!memoryOperation) memoryOperation = {operation_id: crypto.randomUUID().replace(/-/g, ""), text};
    $("memory-text").disabled = true;
    try {
      await api("/api/memory", {...memoryOperation, confirmed: true});
      if (epoch !== sessionEpoch) return;
      memoryOperation = null; $("memory-text").value = ""; $("memory-text").disabled = false;
      $("memory-save").textContent = "Зберегти запис";
      message("memory-save-status", "Запис збережено."); await loadMemory();
    } catch (error) {
      if (epoch !== sessionEpoch) return;
      if ([400, 403, 404, 409].includes(error.status)) { memoryOperation = null; $("memory-text").disabled = false; message("memory-save-status", "Запис не прийнято. Перевір дані та доступ.", true); }
      else { $("memory-save").textContent = "Перевірити збереження"; message("memory-save-status", "Відповідь не отримано. Повторна перевірка використовує той самий запит і не створить дубль.", true); }
    }
  });
}
$("memory-search").addEventListener("submit", event => { event.preventDefault(); void loadMemory(); });
$("memory-more").addEventListener("click", () => { if (memoryNext !== null) void loadMemory(memoryNext); });
$("memory-add").addEventListener("submit", event => { event.preventDefault(); void saveMemory(); });

const taskCreateFields = ["task-prompt", "task-mode", "task-at", "task-repeat", "task-offset"];
function resetTaskCreate() {
  taskCreateOperation = null; taskReschedules.clear(); taskPauses.clear();
  $("task-create-form").reset();
  for (const id of taskCreateFields) $(id).disabled = false;
  $("task-create-submit").textContent = "Створити"; message("task-create-status", "");
}
async function createTask() {
  await mutation("Створити задачу?", "Розклад буде збережено лише у вибраній розмові. Перевір дату, час і тип задачі.", "Створити", async epoch => {
    if (!taskCreateOperation) taskCreateOperation = {operation_id: crypto.randomUUID().replace(/-/g, ""),
      prompt: $("task-prompt").value, mode: $("task-mode").value, at: $("task-at").value + $("task-offset").value,
      interval_seconds: $("task-repeat").value ? Number($("task-repeat").value) : null};
    for (const id of taskCreateFields) $(id).disabled = true;
    try {
      await api("/api/tasks", {...taskCreateOperation, confirmed: true});
      if (epoch !== sessionEpoch) return;
      resetTaskCreate(); message("task-create-status", "Задачу створено."); await loadTasks(epoch);
    } catch (error) {
      if (epoch !== sessionEpoch) return;
      if ([400, 403, 404, 409].includes(error.status)) {
        taskCreateOperation = null; for (const id of taskCreateFields) $(id).disabled = false;
        message("task-create-status", "Задачу не прийнято. Перевір текст і час. Для неоднозначного часу вкажи UTC-зсув; неіснуючий час потрібно змінити.", true);
      } else {
        $("task-create-submit").textContent = "Перевірити створення";
        message("task-create-status", "Відповідь не отримано. Повторна перевірка цього запиту не створить дубль задачі.", true);
      }
    }
  });
}
$("task-create-form").addEventListener("submit", event => { event.preventDefault(); void createTask(); });

function taskReschedule(task) {
  const details = element("details", undefined, "task-history"), form = element("form", undefined, "topic-form");
  details.append(element("summary", "Перенести запуск"), form);
  const date = element("input"), offset = element("input"), button = element("button", "Зберегти новий час"), status = element("p", "Часовий пояс: " + timezone, "hint");
  date.type = "datetime-local"; date.required = true; date.id = "task-time-" + task.id;
  offset.placeholder = "+03:00"; offset.pattern = "[+-][0-9]{2}:[0-9]{2}"; offset.maxLength = 6; offset.id = "task-offset-" + task.id;
  const dateLabel = element("label", "Нові дата й час"), offsetLabel = element("label", "UTC-зсув, якщо час неоднозначний");
  dateLabel.htmlFor = date.id; offsetLabel.htmlFor = offset.id; button.type = "submit"; button.dataset.mutation = "task-reschedule";
  status.setAttribute("role", "status");
  const saved = taskReschedules.get(task.id);
  if (saved) { date.value = saved.local; offset.value = saved.offset; date.disabled = offset.disabled = true; button.textContent = "Перевірити перенесення"; }
  form.append(dateLabel, date, offsetLabel, offset, status, button);
  form.addEventListener("submit", event => {
    event.preventDefault();
    void mutation("Перенести запуск?", "Зміниться час наступного запуску цієї задачі. Період повторення залишиться тим самим.", "Перенести", async epoch => {
      if (!taskReschedules.has(task.id)) taskReschedules.set(task.id, {operation_id: crypto.randomUUID().replace(/-/g, ""), expected_revision: task.revision, at: date.value + offset.value, local: date.value, offset: offset.value});
      date.disabled = offset.disabled = true;
      const {operation_id, expected_revision, at} = taskReschedules.get(task.id);
      try {
        await api("/api/tasks/" + encodeURIComponent(task.id) + "/reschedule", {confirmed: true, operation_id, expected_revision, at});
        if (epoch !== sessionEpoch) return;
        taskReschedules.delete(task.id); message("notice", "Час запуску змінено."); await loadTasks(epoch);
      } catch (error) {
        if (epoch !== sessionEpoch) return;
        if ([400, 403, 404, 409].includes(error.status)) {
          taskReschedules.delete(task.id); date.disabled = offset.disabled = false;
          status.textContent = error.status === 409 ? "Стан задачі змінився. Онови список перед перенесенням." : "Перевір час і доступ. Для неоднозначного часу вкажи UTC-зсув.";
        } else { button.textContent = "Перевірити перенесення"; status.textContent = "Відповідь не отримано. Перевірка повторить лише той самий запит."; }
      }
    });
  });
  return details;
}

function taskPause(task) {
  const paused = task.status !== "paused", button = element("button", taskPauses.has(task.id) ? "Перевірити зміну" : paused ? "Призупинити" : "Відновити", "quiet");
  button.type = "button"; button.dataset.mutation = "task-pause";
  button.addEventListener("click", () => mutation(paused ? "Призупинити розклад?" : "Відновити розклад?",
    paused ? "Наступні запуски не відбудуться до відновлення. Час і повторення збережуться." : "Початковий час збережеться. Якщо він уже минув, задача може початися одразу після відновлення.", paused ? "Призупинити" : "Відновити", async epoch => {
      if (!taskPauses.has(task.id)) taskPauses.set(task.id, {operation_id: crypto.randomUUID().replace(/-/g, ""), expected_revision: task.revision, paused});
      try {
        const result = await api("/api/tasks/" + encodeURIComponent(task.id) + "/pause", {confirmed: true, ...taskPauses.get(task.id)});
        if (epoch !== sessionEpoch) return;
        taskPauses.delete(task.id); message("notice", result.status === "paused" ? "Розклад призупинено." : "Розклад відновлено."); await loadTasks(epoch);
      } catch (error) {
        if (epoch !== sessionEpoch) return;
        if ([400, 403, 404, 409].includes(error.status)) { taskPauses.delete(task.id); message("notice", "Стан задачі змінився або дія недоступна. Онови список перед новою спробою.", true); }
        else { button.textContent = "Перевірити зміну"; message("notice", "Відповідь не отримано. Перевірка використовує той самий запит без нової зміни розкладу.", true); }
      }
    }));
  return button;
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
  $("tasks-timezone").textContent = "Задачі сесії «" + selectedName() + "»" + (timezone ? ". Часовий пояс: " + timezone : "");
  const list = $("tasks-list");
  list.replaceChildren();
  message("tasks-status", value.tasks.length ? "" : "Задач у розкладі поки немає. Створи нагадування або заплануй роботу в Telegram.");
  for (const task of value.tasks) {
    const item = element("li", undefined, "item"), heading = element("div", undefined, "item-heading");
    if (Object.hasOwn(taskLabels, task.status)) item.dataset.status = task.status;
    const tone = task.status === "uncertain" ? "warning" : task.status === "running" || task.status === "pending" ? "good" : "";
    heading.append(element("h3", task.preview || (task.mode === "remind" ? "Нагадування" : "Задача Oak")), element("span", label(taskLabels, task.status), "badge " + tone));
    const meta = element("p", undefined, "item-meta");
    meta.append(document.createTextNode((task.mode === "remind" ? "Нагадування" : "Задача Oak") + " · "), dateNode(task.due), document.createTextNode(". " + intervalText(task.interval) + ". ID: "), element("code", task.id));
    item.append(heading, meta);
    if (task.turn_status) item.append(element("p", "Виконання: " + label(turnLabels, task.turn_status).toLocaleLowerCase("uk-UA"), "item-description"));
    if (task.status === "uncertain") item.append(element("p", "Результат невідомий. Перевір його в Telegram перед повторним запуском.", "item-description warning"));
    if (["pending", "running", "uncertain", "paused"].includes(task.status)) {
      const actions = element("div", undefined, "item-actions"), button = element("button", "Скасувати розклад", "danger");
      button.type = "button"; button.dataset.mutation = "cancel";
      button.addEventListener("click", () => mutation("Скасувати розклад?", "Майбутні запуски цієї задачі буде скасовано. Якщо вона вже працює, окремо натисни «Зупинити Oak» у розділі «Стан».", "Скасувати розклад", async epoch => {
        button.dataset.locked = "true";
        const result = await api("/api/tasks/" + encodeURIComponent(task.id) + "/cancel", {confirmed: true});
        if (epoch !== sessionEpoch) return;
        message("notice", result.cancelled ? "Розклад скасовано. Робота, що вже почалась, потребує окремої зупинки." : "Задача вже завершена або скасована. Оновлюємо стан.");
        await loadTasks(epoch);
      }, true));
      actions.append(button);
      if (["pending", "paused"].includes(task.status)) actions.append(taskPause(task));
      item.append(actions);
    }
    if (["pending", "paused"].includes(task.status)) item.append(taskReschedule(task));
    item.append(taskHistory(task));
    list.append(item);
  }
  renderTaskSummary(value.summary);
  updateActions();
}

function svgIcon(name, className = "icon") {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg"), use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  svg.setAttribute("class", className);
  svg.setAttribute("aria-hidden", "true");
  use.setAttribute("href", "#i-" + name);
  svg.append(use);
  return svg;
}
function integrationIcon(name, muted = false) {
  const text = typeof name === "string" ? name.trim() : "", lower = text.toLowerCase();
  const match = integrationIcons.find(([, pattern]) => pattern.test(lower));
  const node = element("span", undefined, "integration-icon" + (muted ? " muted" : ""));
  node.setAttribute("aria-hidden", "true");
  if (match) {
    node.classList.add("icon-" + match[0]);
    node.append(svgIcon(match[0]));
  } else {
    let hash = 0;
    for (const char of lower) hash = (hash * 31 + char.codePointAt(0)) >>> 0;
    node.classList.add("monogram", "tone-" + hash % 6);
    node.textContent = Array.from(text)[0]?.toLocaleUpperCase("uk-UA") || "#";
  }
  return node;
}
function toolsText(value) {
  return Number.isInteger(value) && value >= 0 ? count(value) + " " + toolWords[new Intl.PluralRules("uk-UA").select(value)] : "Інструменти не визначено";
}
function serverNeedsLogin(server) { return server.auth_status === "notLoggedIn" || server.runtime_status === "authenticationRequired"; }
function serverAttention(server) { return server.runtime_status === "failed" || serverNeedsLogin(server) || ["uncertain", "expired"].includes(server.login_state); }
function appStatus(app) { return !app.enabled ? ["Вимкнено", ""] : app.callable ? ["Працює", "good"] : ["Обмежено", "warning"]; }
function pluginStatus(plugin) { return plugin.status === "enabled" ? ["Увімкнено", "good"] : [plugin.status === "unknown" ? "Не визначено" : "Вимкнено", ""]; }
function serverStatus(server) {
  if (server.runtime_status === "failed") return ["Помилка", "bad"];
  if (serverNeedsLogin(server)) return ["Потрібен вхід", "warning"];
  if (server.runtime_status === "connected") return ["Підключено", "good"];
  return [server.runtime_status ? label(runtimeLabels, server.runtime_status) : "Стан невідомий", ""];
}
function inventoryGroup(title, inventory, emptyText, renderItem, summary) {
  const section = element("section", undefined, "integration-group"), heading = element("div", undefined, "integration-group-heading");
  heading.append(element("h2", title));
  section.append(heading);
  if (!inventory || inventory.available !== true || !Array.isArray(inventory.items)) {
    section.append(element("p", "Дані недоступні. Натисни «Оновити», щоб перевірити доступність.", "inventory-empty"));
  } else {
    if (inventory.items.length) heading.append(element("p", summary[0] + ": " + count(inventory.items.filter(summary[1]).length) + " з " + count(inventory.items.length) + (inventory.complete === false ? "+" : "")));
    if (inventory.complete === false) section.append(element("p", "Список неповний: частину даних не вдалося отримати. Натисни «Оновити», щоб перевірити повний список.", "item-description warning"));
    if (!inventory.items.length) section.append(element("p", inventory.complete === false ? "Повний список недоступний." : emptyText, "inventory-empty"));
    else {
      const list = element("ul", undefined, "integration-list");
      for (const item of inventory.items) list.append(renderItem(item));
      section.append(list);
    }
  }
  return section;
}
function integrationText(name, subtitle, tone = "") {
  const text = element("span", undefined, "integration-text");
  text.append(element("span", name || "Назву не повідомлено", "integration-name"), element("span", subtitle, "integration-subtitle" + (tone ? " " + tone : "")));
  return text;
}
function integrationItem(value, subtitle, status, muted = false) {
  const item = element("li"), details = element("details", undefined, "integration"), summary = element("summary", undefined, "integration-row");
  const badge = element("span", status[0], "badge" + (status[1] ? " " + status[1] : "")), body = element("div", undefined, "integration-details");
  summary.append(integrationIcon(value.name, muted), integrationText(value.name, subtitle), badge, svgIcon("chevron", "icon chevron"));
  details.dataset.integration = value.id;
  details.open = openIntegrations.has(value.id);
  details.addEventListener("toggle", () => { if (details.open) openIntegrations.add(value.id); else openIntegrations.delete(value.id); });
  details.append(summary, body);
  item.append(details);
  return {item, body};
}
function renderApp(app) {
  const {item, body} = integrationItem(app, app.callable ? "Можна викликати" : "Виклик недоступний", appStatus(app), !app.enabled);
  const facts = element("dl", undefined, "integration-facts");
  fact(facts, "Стан", app.enabled ? "Увімкнено" : "Вимкнено");
  fact(facts, "Доступність для Oak", app.callable ? "Можна викликати" : "Виклик недоступний");
  body.append(facts);
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
    body.append(actions);
  } else body.append(element("p", "Зовнішні налаштування цього застосунку зараз недоступні.", "item-description"));
  return item;
}
function renderPlugin(plugin) {
  const restricted = ["disabled_by_admin", "plan_not_eligible", "required_app_unavailable"].includes(plugin.status);
  const subtitle = restricted ? label(pluginLabels, plugin.status) : "Вхід: " + label(authPolicyLabels, plugin.auth_policy).toLocaleLowerCase("uk-UA");
  const {item, body} = integrationItem(plugin, subtitle, pluginStatus(plugin), plugin.status !== "enabled");
  const facts = element("dl", undefined, "integration-facts");
  fact(facts, "Увімкнення", plugin.enabled ? "Увімкнено" : "Вимкнено");
  fact(facts, "Стан", label(pluginLabels, plugin.status));
  fact(facts, "Запит на вхід", label(authPolicyLabels, plugin.auth_policy));
  body.append(facts);
  return item;
}
function serverLogin(server, compact = false) {
  const actions = element("div", undefined, "item-actions"), button = element("button", compact ? "Увійти" : "Увійти на зовнішній сторінці", compact ? undefined : "quiet");
  if (compact) button.setAttribute("aria-label", "Увійти в «" + server.name + "» на зовнішній сторінці");
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
  return actions;
}
function renderServer(server) {
  const {item, body} = integrationItem(server, server.login_state ? label(loginLabels, server.login_state) : toolsText(server.tool_count), serverStatus(server), server.runtime_status === "disabled");
  const facts = element("dl", undefined, "integration-facts");
  fact(facts, "Авторизація", label(authLabels, server.auth_status));
  fact(facts, "Стан у середовищі", server.runtime_status === null ? "Не повідомлено" : label(runtimeLabels, server.runtime_status));
  fact(facts, "Інструментів", Number.isInteger(server.tool_count) && server.tool_count >= 0 ? count(server.tool_count) : "Не визначено");
  body.append(facts);
  if (server.login_state) body.append(element("p", label(loginLabels, server.login_state), "item-description" + (["uncertain", "expired"].includes(server.login_state) ? " warning" : "")));
  if (server.oauth_available) {
    body.append(serverLogin(server));
    if (["requested", "pending", "uncertain"].includes(server.login_state) && !oauthLinks.has(server.id)) body.append(element("p", "Перевір стан входу через «Оновити». Новий запит автоматично не надсилаємо.", "item-description"));
  } else body.append(element("p", server.oauth_reason || "Зовнішній вхід для цього підключення недоступний.", "item-description"));
  return item;
}
function attentionGroup(servers) {
  const pending = servers.filter(serverAttention);
  if (!pending.length) return null;
  const section = element("section", undefined, "integration-attention"), heading = element("h2"), list = element("ul", undefined, "attention-list");
  heading.id = "integrations-attention";
  section.setAttribute("aria-labelledby", heading.id);
  heading.append(svgIcon("alert"), element("span", "Потребує уваги"), element("span", count(pending.length), "group-count"));
  for (const server of pending) {
    const row = element("li", undefined, "integration-row"), [, tone] = serverStatus(server);
    const reason = server.login_state ? label(loginLabels, server.login_state) : server.runtime_status === "failed" ? "Сервер не запустився" : "Без входу інструменти недоступні";
    row.append(integrationIcon(server.name), integrationText(server.name, reason, tone));
    if (server.oauth_available) row.append(serverLogin(server, true));
    else {
      const button = element("button", "Деталі", "quiet");
      button.type = "button";
      button.setAttribute("aria-label", "Деталі «" + (server.name || "Назву не повідомлено") + "»");
      button.addEventListener("click", () => {
        const target = Array.from($("integrations-content").querySelectorAll("details[data-integration]")).find(node => node.dataset.integration === server.id);
        if (!target) return;
        target.open = true;
        target.querySelector("summary").focus();
      });
      row.append(button);
    }
    list.append(row);
  }
  section.append(heading, list);
  return section;
}
function renderIntegrations(value) {
  if (!value || typeof value !== "object") throw new Error("Не вдалося прочитати інтеграції. Натисни «Оновити».");
  integrationData = value;
  $("integration-scope").textContent = value.scope === "telegram" ? "Можливості сесії «" + selectedName() + "»" : "Інтеграції середовища Oak. Доступність у вибраній темі перевір у Telegram.";
  const servers = value.servers?.available === true && Array.isArray(value.servers.items) ? value.servers.items : [];
  $("integrations-content").replaceChildren(...[
    attentionGroup(servers),
    inventoryGroup("Застосунки", value.apps, "Застосунків немає.", renderApp, ["Працюють", app => app.enabled && app.callable]),
    inventoryGroup("Плагіни", value.plugins, "Плагінів немає.", renderPlugin, ["Увімкнено", plugin => plugin.status === "enabled"]),
    inventoryGroup("Сервери інструментів", value.servers, "Серверів інструментів немає.", renderServer, ["Підключено", server => server.runtime_status === "connected"])
  ].filter(Boolean));
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
      for (const id of ["confirmations", "uncertain-inputs", "memory-count"]) { $(id).textContent = "—"; setAttention(id, false); }
      $("settings-list").replaceChildren();
      fact($("settings-list"), "Стан", "Дані недоступні. Натисни «Оновити».");
      renderComputer(null);
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
      renderTaskSummary(null);
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
  panelPoll?.reset();
  refreshing = true;
  const epoch = sessionEpoch;
  setConnection("Оновлюємо…", "busy");
  $("workspace").setAttribute("aria-busy", "true");
  updateActions();
  try {
    if (initialRequest && !requestScopeResolved && /^[a-f0-9]{32}$/.test(initialRequest)) {
      try {
        const request = await api('/api/requests/' + initialRequest);
        if (epoch !== sessionEpoch) return;
        initialConversation = request.conversation;
        requestScopeResolved = true;
      } catch (error) {
        if (error.status === 401) throw error;
        initialRequest = null;
        message('notice', 'Запит із посилання недоступний.', true);
      }
    }
    const sessionsOK = await loadSessions(epoch);
    if (epoch !== sessionEpoch) return;
    const panelOK = await loadPanel(epoch);
    if (epoch !== sessionEpoch) return;
    if (!panelOK && !authenticated) {
      requireLogin("Не вдалося перевірити вхід. Перевір з’єднання, відкрий Oak із меню Telegram або введи приватний ключ.");
      return;
    }
    $("login").hidden = true;
    $("workspace").hidden = false;
    if (initialRequest) {
      const id = initialRequest; initialRequest = null;
      void inputRequestPanel().then(panel => panel.open(id));
    }
    void dynamicPanel();
    message("error", "");
    const results = await Promise.allSettled([loadTasks(epoch), loadIntegrations(epoch), loadModels(epoch)]);
    if (epoch !== sessionEpoch) return;
    const complete = sessionsOK && panelOK && results.every(result => result.status === "fulfilled" && result.value);
    setConnection(complete ? "На зв’язку" : "Не всі дані доступні", complete ? "ok" : "partial");
    $("last-refresh").textContent = (complete ? "Оновлено о " : "Остання спроба о ") + new Intl.DateTimeFormat("uk-UA", {hour: "2-digit", minute: "2-digit", second: "2-digit"}).format(new Date());
  } finally {
    if (epoch === sessionEpoch) {
      refreshing = false;
      $("workspace").removeAttribute("aria-busy");
      if (authenticated) panelPoll?.start();
    }
    updateActions();
  }
}

async function desktopPanel() {
  if (!remoteLoading) remoteLoading = import("/remote.js").then(module => {
    remotePanel = module.createRemotePanel({api, confirmAction, available: () => authenticated && !$("workspace").hidden && !$("view-desktop").hidden && !document.hidden, busy: () => mutationBusy || refreshing || $("confirmation").open});
    return remotePanel;
  }).catch(() => {
    remoteLoading = null;
    message("remote-message", "Не вдалося завантажити робочий стіл. Перевір з’єднання та натисни «Оновити».", true);
    return null;
  });
  const panel = await remoteLoading;
  if (!$("view-desktop").hidden && !$("workspace").hidden) await panel?.refresh();
}

async function artifact(id, {signal, limit = 10 * 1024 * 1024, types = ["image/png", "image/jpeg", "image/webp", "image/gif"]} = {}) {
  if (!/^[a-f0-9]{32}$/.test(id)) throw new Error("Зображення недоступне.");
  const epoch = sessionEpoch, controller = new AbortController();
  const abort = () => controller.abort(), timeout = setTimeout(abort, 30000);
  if (signal?.aborted) abort();
  else signal?.addEventListener("abort", abort, {once: true});
  let reader;
  try {
    const response = await fetch("/api/artifacts/" + encodeURIComponent(id) + "?conversation=" + encodeURIComponent(selectedSession), {credentials: "same-origin", signal: controller.signal,
      cache: "no-store", redirect: "error", headers: sessionToken ? {Authorization: "Bearer " + sessionToken} : {}});
    const type = (response.headers.get("Content-Type") || "").split(";", 1)[0].trim().toLowerCase();
    const length = Number(response.headers.get("Content-Length"));
    if (!response.ok || epoch !== sessionEpoch || controller.signal.aborted ||
        !types.includes(type) ||
        !Number.isFinite(length) || length < 0 || length > limit || !response.body) throw new Error("Зображення недоступне.");
    reader = response.body.getReader();
    const chunks = []; let size = 0;
    while (true) {
      const {done, value} = await reader.read();
      if (epoch !== sessionEpoch || controller.signal.aborted) throw new Error("Зображення недоступне.");
      if (done) break;
      size += value.byteLength;
      if (size > limit) throw new Error("Зображення недоступне.");
      chunks.push(value);
    }
    return new Blob(chunks, {type});
  } finally {
    controller.abort();
    if (reader) {try {await reader.cancel();} catch {} reader.releaseLock();}
    clearTimeout(timeout); signal?.removeEventListener("abort", abort);
  }
}

async function dynamicPanel() {
  if (document.querySelector("dialog[open]")) return;
  if (surfaceLaunch && initialConversation === null && sessionsData && authenticated && !$("workspace").hidden && !surfaceLaunch.opened) {
    surfaceLaunch.opened = true;
    if (!document.activeElement?.matches('input,select,textarea,[contenteditable="true"]') && !$("confirmation").open) showView("status", false);
  }
  if (!a2uiLoading) a2uiLoading = import('/a2ui.js').then(module => {
    a2uiPanel = module.createA2UIPanel({api, artifact,
      available: () => authenticated && sessionsData && initialConversation === null && !$("workspace").hidden && !$("view-status").hidden && !document.hidden && !document.querySelector("dialog[open]"),
      epoch: () => sessionEpoch, busy: () => mutationBusy || refreshing || $("confirmation").open,
      target: () => surfaceLaunch?.conversation === selectedSession ? surfaceLaunch.id : null,
      targetHandled: () => { surfaceLaunch = null; },
      setBusy: value => { mutationBusy = value; updateActions(); }});
    return a2uiPanel;
  }).catch(() => {
    a2uiLoading = null;
    if (surfaceLaunch) message("notice", "Картку не вдалося завантажити. Перевір з’єднання та натисни «Оновити».", true);
    return null;
  });
  const panel = await a2uiLoading;
  await panel?.refresh();
}

function showView(view, focus = true) {
  if (view !== "desktop") remotePanel?.leave();
  for (const navButton of document.querySelectorAll("[data-view]")) {
    if (navButton.dataset.view === view) navButton.setAttribute("aria-current", "page");
    else navButton.removeAttribute("aria-current");
  }
  for (const section of document.querySelectorAll(".view")) section.hidden = section.id !== "view-" + view;
  telegramBridge?.update();
  if (focus) {
    telegramBridge?.feedback();
    window.scrollTo({top: 0});
    $(view + "-heading").focus({preventScroll: true});
  }
}
for (const button of document.querySelectorAll("[data-view]")) button.addEventListener("click", () => {
  const view = button.dataset.view;
  showView(view);
  if (view === "activity") void loadActivity();
  if (view === "artifacts") void loadArtifacts();
  if (view === "recovery") void loadRecovery();
  if (view === "memory") void loadMemory();
  if (view === "desktop") void desktopPanel();
  if (view === "status") void dynamicPanel();
});
$("create-session").addEventListener("submit", event => { event.preventDefault(); void changeTopic("create", null, $("topic-name").value); });
$("topic-name").addEventListener("input", renderTopicCapability);
$("refresh").addEventListener("click", () => { message("notice", ""); void refresh(); void dynamicPanel(); if (!$("view-activity").hidden) void loadActivity(); if (!$("view-artifacts").hidden) void loadArtifacts(); if (!$("view-recovery").hidden) void loadRecovery(); if (!$("view-memory").hidden) void loadMemory(); if (!$("view-desktop").hidden) void desktopPanel(); });
setInterval(() => { void syncSessions(); }, 15000);
document.addEventListener("visibilitychange", () => {
  if (document.hidden) panelPoll?.reset();
  else { void syncSessions(); panelPoll?.resume(); }
});
window.addEventListener("pagehide", () => { panelPoll?.reset(); telegramTheme?.destroy(); telegramBridge?.destroy(); bridgeObserver?.disconnect(); clearArtifacts(); });
window.addEventListener("pageshow", () => { telegramTheme?.connect(); telegramBridge?.connect(); bridgeObserver?.observe(document.body, {subtree: true, attributes: true, attributeFilter: ["open", "hidden"]}); panelPoll?.resume(); });
$("return-telegram").addEventListener("click", () => { if (telegramLaunch) window.Telegram?.WebApp?.close(); });
$("computer-switch").addEventListener("click", () => { void toggleComputer(); });
$("model-form").addEventListener("submit", event => { event.preventDefault(); void changeModel(); });
$("model-select").addEventListener("change", () => {
  draftEffort = $("model-select").value === modelsData?.selected ? modelsData.effort : modelChoice()?.default_effort || null;
  if (!modelChoice()?.turbo_available && draftTurbo === true) draftTurbo = false;
  renderEffort();
});
$("turbo-toggle").addEventListener("click", () => {
  if ($("turbo-toggle").disabled) return;
  draftTurbo = draftTurbo !== true;
  renderTurbo();
  updateModelActions();
});
$("effort-range").addEventListener("input", () => {
  if ($("effort-range").disabled) return;
  const levels = modelChoice()?.efforts || [], index = Number($("effort-range").value);
  if (!Number.isInteger(index) || !levels[index]) return;
  draftEffort = levels[index];
  renderEffort();
});
$("effort-reset").addEventListener("click", () => {
  if ($("effort-reset").disabled) return;
  draftEffort = modelChoice().default_effort;
  renderEffort();
});
$("stop").addEventListener("click", () => mutation("Зупинити поточну роботу Oak?", "Запит стосується вибраної сесії «" + selectedName() + "». Уже виконані зовнішні дії залишаться виконаними. Заплановані задачі потрібно скасовувати окремо.", "Запитати зупинку", async epoch => {
  stopRequestState = "uncertain";
  const result = await api("/api/stop", {conversation: selectedSession});
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
  telegramTheme?.connect(); telegramBridge?.connect();
}
async function inputRequestPanel() {
  if (inputRequestsPanel) return inputRequestsPanel;
  if (!inputRequestsLoading) inputRequestsLoading = import('./requests.js').then(module => {
    inputRequestsPanel = module.createInputRequests({api, epoch: () => sessionEpoch, scope: () => selectedSession,
      available: () => authenticated && !$("workspace").hidden,
      changed: text => { message('notice', text); void refresh(); }});
    return inputRequestsPanel;
  });
  return inputRequestsLoading;
}
$("telegram-sdk").addEventListener("load", telegramReady);
(async () => {
  try {
    const {createTelegramTheme} = await import("/telegram-theme.js");
    telegramTheme = createTelegramTheme({app: () => window.Telegram?.WebApp, root: document.documentElement});
    const {createTelegramBridge} = await import("/telegram-bridge.js");
    telegramBridge = createTelegramBridge({app: () => window.Telegram?.WebApp, root: document.documentElement,
      canBack: () => authenticated && (!!document.querySelector("dialog[open]") || $("view-status").hidden),
      back: () => {
        const dialog = document.querySelector("dialog[open]");
        if (dialog) { if (dialog.dispatchEvent(new Event("cancel", {cancelable: true}))) dialog.close("cancel"); }
        else if (authenticated && $("view-status").hidden) showView("status");
        telegramBridge.update();
      }});
    bridgeObserver = new MutationObserver(() => telegramBridge?.update());
    bridgeObserver.observe(document.body, {subtree: true, attributes: true, attributeFilter: ["open", "hidden"]});
    const {createPanelPoll} = await import("/panel-poll.js");
    panelPoll = createPanelPoll({
      read: signal => api("/api/panel", undefined, {signal}),
      apply: value => { renderPanel(value, true); },
      failed: () => { message("panel-error", "Стан міг застаріти. Повторно перевіряємо з’єднання…", true); },
      available: () => authenticated && !document.hidden && !$("workspace").hidden && !refreshing && !mutationBusy,
      epoch: () => sessionEpoch});
    for (let i = 0; i < 30 && !window.Telegram?.WebApp; i++) await new Promise(resolve => setTimeout(resolve, 100));
    telegramReady();
    if (!initialRequest) {
      const start = window.Telegram?.WebApp?.initDataUnsafe?.start_param || new URLSearchParams(location.search).get('tgWebAppStartParam') || '';
      const match = /^request_([a-f0-9]{32})$/.exec(start);
      if (match) { initialRequest = match[1]; surfaceLaunch = null; } // Locator only; the backend authenticates owner/scope.
    }
    const fragment = new URLSearchParams(location.hash.slice(1));
    const initData = window.Telegram?.WebApp?.initData || fragment.get("tgWebAppData");
    if (initData) { await api("/api/session", {initData}); telegramLaunch = true; }
    await refresh();
  } catch (error) {
    if (error.status !== 401) requireLogin("Під’єднання недоступне. Відкрий Oak із меню Telegram або введи приватний ключ доступу.");
  } finally { telegramReady(); }
})();
