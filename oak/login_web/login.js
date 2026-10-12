// This origin owns the human input plane. Never send values or leases to the parent.
import {createFormControl} from '/form-controls.js';
const $ = id => document.getElementById(id);
const parentOrigin = document.querySelector('meta[name="oak-parent-origin"]').content;
const PROVIDERS = Object.freeze({
  google: {title: 'Вхід у Google', domain: 'accounts.google.com'},
  instagram: {title: 'Вхід в Instagram', domain: 'www.instagram.com'},
  website: {title: 'Форма сайту', domain: null},
});
const STAGES = Object.freeze({
  identifier: 'Введи адресу електронної пошти або логін.',
  password: 'Введи пароль до акаунта.',
  otp: 'Введи код підтвердження.',
  login: 'Введи логін і пароль до акаунта.',
  device_approval: 'Підтвердь вхід на своєму пристрої. Форма оновиться автоматично.',
  provider_blocked: 'Не вдалося продовжити цей крок. Причину блокування не підтверджено. Можна скасувати запит.',
  unsupported_challenge: 'Сервіс просить перевірку, яку ця форма поки не підтримує. Скасуй цей запит.',
  unsupported_layout: 'Не вдалося розпізнати форму цієї сторінки. Можна скасувати запит.',
  authenticated: 'Перевіряємо завершення входу…',
  form: 'Заповни поля та обери дію на сайті.',
  agent_working: 'Агент переглядає сторінку й готує наступний крок…',
  clarification: 'Обери, для якої дії показати поля. Цей вибір не виконує дію на сайті.',
  review: 'Перевір результат на сайті. Підтверди завершення, якщо потрібну дію виконано.',
});
const BLOCK_MESSAGES = new Set([
  'Oak зупинив перехід за межі схваленої адреси. Цей запит не дозволяє продовження на іншому сайті. Можна скасувати запит.',
  'Сайт відкрив додаткове вікно. Oak закрив його: цей спосіб входу поки не підтримується. Можна скасувати запит.',
  'Сайт повернув помилку завантаження сторінки. Причину відмови не підтверджено. Можна скасувати запит.',
  'Сторінка повідомляє про блокування цього способу входу. Можна скасувати запит.',
  'Не вдалося завантажити сторінку через помилку з’єднання. Відмову сайту не підтверджено. Можна скасувати запит.',
  'Oak не зміг безпечно обробити запит сторінки й зупинив продовження. Можна скасувати запит.',
  'Не вдалося продовжити цей крок. Причину блокування не підтверджено. Можна скасувати запит.',
]);
const FIELD_TYPES = Object.freeze({identifier: ['text', 'email'], password: ['password'], otp: ['otp', 'text', 'password']});
const FIELD_LIMITS = Object.freeze({identifier: 254, password: 1024, otp: 16});
const STAGE_FIELDS = Object.freeze({identifier: ['identifier'], password: ['password'], otp: ['otp'], login: ['identifier', 'password']});
const END_MESSAGES = Object.freeze({
  authenticated: 'Вхід підтверджено сервісом. Можна повернутися до Oak.',
  cancelled: 'Вхід скасовано.',
  expired: 'Час для входу минув. Створи новий запит у Oak.',
  failed: 'Не вдалося завершити вхід. Створи новий запит у Oak.',
  owner_confirmed: 'Завершення дії підтверджено тобою. Можна повернутися до Oak.',
});
let requestId = null, lease = null, provider = null, destination = null, current = null;
let formSchema = null, explicitSchema = false;
let timer = null, busy = false, closed = false, generation = 0;
let refreshing = false;
const controllers = new Set();
const inputs = new Map();
const secretNames = new Set();
const passwordToggles = new Map();
let attachmentReady = false, layoutObserver = null, lastLayoutHeight = 0;
const owns = (object, key) => Object.hasOwn(object, key);
const record = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const boundedText = (value, max) => typeof value === 'string' && value.length <= max && !/[\u0000-\u0008\u000b-\u001f\u007f]/u.test(value);
const token = value => typeof value === 'string' && /^[A-Za-z0-9_-]{43}$/.test(value);

function websiteOrigin(value) {
  if (typeof value !== 'string' || value.length > 512) throw new Error('Invalid destination');
  const url = new URL(value);
  if (url.protocol !== 'https:' || url.port || url.username || url.password || url.origin !== value) throw new Error('Invalid destination');
  return value;
}

function normalizeWebsite(value) {
  const surface = formSchema === 'website-v2';
  if (current?.originExplicit && !owns(value, 'current_origin')) throw new Error('Missing current origin');
  const currentOrigin = owns(value, 'current_origin') ? websiteOrigin(value.current_origin) : destination;
  let originApproval = null;
  if (owns(value, 'origin_approval')) {
    if (!surface || provider !== 'website' || !record(value.origin_approval)
        || Object.keys(value.origin_approval).length !== 1 || !owns(value.origin_approval, 'origin')
        || value.stage !== 'device_approval' || value.fields.length || value.actions.length
        || value.can_complete !== false) throw new Error('Invalid origin approval');
    originApproval = {origin: websiteOrigin(value.origin_approval.origin)};
  }
  if (owns(value, 'can_complete') && (!surface || typeof value.can_complete !== 'boolean')) throw new Error('Invalid completion capability');
  if (value.destination !== destination || !['form', 'agent_working', 'clarification', 'review', 'device_approval', 'provider_blocked', 'unsupported_challenge', 'unsupported_layout', 'authenticated'].includes(value.stage)) {
    throw new Error('Invalid website form');
  }
  if (provider !== 'website' && value.stage === 'review') throw new Error('Invalid provider review');
  const limits = {text: 1024, email: 254, password: 1024, otp: 16, checkbox: 1, select: 64};
  const names = new Set(), ids = new Set();
  const fields = value.fields.map(field => {
    if (!record(field) || typeof field.name !== 'string' || !/^f[0-9]{1,12}$/.test(field.name) || names.has(field.name) || !owns(limits, field.type)
        || !boundedText(field.label, 120) || !field.label.trim() || !Number.isInteger(field.max_length)
        || field.max_length < 1 || field.max_length > limits[field.type]
        || Object.keys(field).some(key => !['name', 'type', 'label', 'max_length', ...(field.type === 'select' ? ['options'] : [])].includes(key))) {
      throw new Error('Invalid website field');
    }
    names.add(field.name);
    const result = {name: field.name, type: field.type, label: field.label, max_length: field.max_length};
    if (field.type === 'select') {
      if (!Array.isArray(field.options) || !field.options.length || field.options.length > (surface ? 256 : 32)) throw new Error('Invalid options');
      const values = new Set();
      result.options = field.options.map(option => {
        if (!record(option) || typeof option.value !== 'string' || !/^o[0-9]{1,12}$/.test(option.value)
            || values.has(option.value) || !boundedText(option.label, 120) || !option.label.trim()
            || Object.keys(option).some(key => !['value', 'label'].includes(key))) throw new Error('Invalid option');
        values.add(option.value);
        return {value: option.value, label: option.label};
      });
    }
    return result;
  });
  const actions = value.actions.map(action => {
    if (!record(action) || typeof action.id !== 'string' || !(/^[a][0-9]{1,12}$/.test(action.id) || action.id === 'confirm')
        || ids.has(action.id) || !boundedText(action.label, 120) || !action.label.trim()
        || Object.keys(action).some(key => !['id', 'label', ...(surface ? ['field_refs', 'available'] : [])].includes(key))) throw new Error('Invalid website action');
    ids.add(action.id);
    if (surface) {
      if (!Array.isArray(action.field_refs) || action.field_refs.length > 32
          || new Set(action.field_refs).size !== action.field_refs.length
          || action.field_refs.some(ref => typeof ref !== 'string' || !names.has(ref))
          || typeof action.available !== 'boolean' || action.id === 'confirm') {
        throw new Error('Invalid surface action binding');
      }
      return {id: action.id, label: action.label, field_refs: [...action.field_refs], available: action.available};
    }
    return {id: action.id, label: action.label};
  });
  if (provider !== 'website' && ids.has('confirm')) throw new Error('Invalid provider confirmation');
  if (!surface && actions.length === 9 && !ids.has('confirm')) throw new Error('Too many website actions');
  if (ids.has('confirm') && fields.some(field => ['password', 'otp'].includes(field.type))) throw new Error('Credential step cannot confirm completion');
  if (value.stage === 'clarification') {
    if (fields.length !== 1 || fields[0].name !== 'f0' || fields[0].type !== 'select'
        || actions.length !== 1 || actions[0].id !== 'a0' || actions[0].label !== 'Показати поля') {
      throw new Error('Invalid clarification');
    }
  } else if (value.stage === 'form') {
    if (!actions.length) throw new Error('Missing website action');
  } else if (value.stage === 'review') {
    if (fields.length || actions.length !== 1 || !ids.has('confirm')) throw new Error('Invalid review');
  } else if (fields.length || actions.length) throw new Error('Invalid waiting step');
  return {provider, stage: value.stage, revision: value.revision, fields, actions,
    currentOrigin, originExplicit: owns(value, 'current_origin'), originApproval,
    canComplete: surface && value.can_complete === true, message: value.message ?? ''};
}

function normalizeForm(value) {
  if (!record(value) || value.provider !== provider || !owns(STAGES, value.stage)
      || !boundedText(value.revision, 128) || !value.revision.length
      || !Array.isArray(value.fields) || value.fields.length > (formSchema === 'website-v2' ? 32 : formSchema === 'website-v1' ? 16 : 3)
      || !Array.isArray(value.actions) || value.actions.length > (formSchema === 'website-v2' ? 64 : formSchema === 'website-v1' ? 9 : 1)
      || !boundedText(value.message ?? '', 600)) throw new Error('Invalid form');
  // Attach fixes the schema for the whole lease. An explicitly marked lease
  // cannot silently fall back when a later snapshot omits or changes the marker.
  const schema = owns(value, 'form_schema') ? value.form_schema : explicitSchema ? null : formSchema;
  if (schema !== formSchema) throw new Error('Changed form schema');
  if ((owns(value, 'current_origin') || owns(value, 'origin_approval'))
      && (formSchema !== 'website-v2' || provider !== 'website')) throw new Error('Invalid origin capability');
  if (['website-v1', 'website-v2'].includes(formSchema)) return normalizeWebsite(value);
  if (['form', 'agent_working', 'clarification', 'review'].includes(value.stage)) throw new Error('Invalid provider stage');
  const seen = new Set();
  const fields = value.fields.map(field => {
    if (!record(field) || !owns(FIELD_TYPES, field.name) || seen.has(field.name)
        || !FIELD_TYPES[field.name].includes(field.type) || !boundedText(field.label, 100) || !field.label.trim()
        || !Number.isInteger(field.max_length) || field.max_length < 1 || field.max_length > FIELD_LIMITS[field.name]
        || Object.keys(field).some(key => !['name', 'type', 'label', 'max_length'].includes(key))) throw new Error('Invalid field');
    seen.add(field.name);
    return {name: field.name, type: field.type, label: field.label, max_length: field.max_length};
  });
  const actions = value.actions.map(action => {
    if (!record(action) || action.id !== 'submit' || !boundedText(action.label, 80) || !action.label.trim()
        || Object.keys(action).some(key => !['id', 'label'].includes(key))) throw new Error('Invalid action');
    return {id: 'submit', label: action.label};
  });
  // Instagram may offer a fixed "Not now" action after password verification.
  const promptOnly = provider === 'instagram' && value.stage === 'login' && fields.length === 0;
  const expected = promptOnly ? [] : STAGE_FIELDS[value.stage] ?? [];
  if (fields.length !== expected.length || expected.some(name => !seen.has(name))
      || actions.length !== (expected.length || promptOnly ? 1 : 0)) throw new Error('Invalid stage');
  return {provider, stage: value.stage, revision: value.revision, fields, actions, message: value.message ?? ''};
}

function clearFields(secretsOnly = false) {
  for (const [name, input] of inputs) if (!secretsOnly || secretNames.has(name)) {
    input.value = '';
    if (input.type === 'checkbox') input.checked = false;
    const toggle = passwordToggles.get(name);
    if (toggle) {
      input.type = 'password';
      toggle.setAttribute('aria-label', 'Показати пароль');
      toggle.setAttribute('aria-pressed', 'false');
    }
  }
}

function updateRecipient(origin) {
  if (provider !== 'website') return;
  $('destination').textContent = origin;
  $('title').textContent = 'Вхід до ' + new URL(origin).hostname;
  document.title = 'Oak — ' + $('title').textContent;
}

function reportLayout() {
  if (!attachmentReady || typeof ResizeObserver === 'undefined') return;
  const card = document.querySelector('main');
  const style = window.getComputedStyle(document.body);
  const measured = Math.ceil(card.getBoundingClientRect().height
    + parseFloat(style.paddingTop) + parseFloat(style.paddingBottom));
  if (!Number.isInteger(measured) || measured < 1) return;
  const height = Math.min(4096, measured);
  if (height === lastLayoutHeight) return;
  lastLayoutHeight = height;
  // Layout only: never include request IDs, origins, labels or human values.
  window.parent.postMessage({type: 'oak-login-layout', height}, parentOrigin);
}

function passwordToggle(input) {
  const toggle = document.createElement('button');
  toggle.type = 'button'; toggle.className = 'password-toggle';
  toggle.setAttribute('aria-label', 'Показати пароль');
  toggle.setAttribute('aria-pressed', 'false');
  toggle.setAttribute('aria-controls', input.id);
  const icon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  icon.setAttribute('viewBox', '0 0 24 24'); icon.setAttribute('fill', 'none');
  icon.setAttribute('stroke', 'currentColor'); icon.setAttribute('stroke-width', '1.7');
  icon.setAttribute('aria-hidden', 'true');
  const outline = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  outline.setAttribute('d', 'M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z');
  const pupil = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
  pupil.setAttribute('cx', '12'); pupil.setAttribute('cy', '12'); pupil.setAttribute('r', '3');
  icon.append(outline, pupil); toggle.append(icon);
  toggle.addEventListener('click', () => {
    if (busy || refreshing || closed) return;
    const revealed = input.type === 'password';
    input.type = revealed ? 'text' : 'password';
    toggle.setAttribute('aria-label', revealed ? 'Приховати пароль' : 'Показати пароль');
    toggle.setAttribute('aria-pressed', String(revealed));
  });
  return toggle;
}

function setBusy(value) {
  busy = value;
  $('login').setAttribute('aria-busy', String(value));
  for (const button of $('actions').querySelectorAll('button')) {
    const action = current?.actions.find(item => item.id === button.value);
    button.disabled = value || refreshing || action?.available === false;
  }
  $('cancel').disabled = value;
  $('close').disabled = value;
  for (const toggle of passwordToggles.values()) toggle.disabled = value || refreshing;
  $('complete').disabled = value || refreshing || !current?.canComplete;
  $('approve-origin').disabled = value || refreshing || !current?.originApproval;
}

function finish(message, error = false) {
  closed = true; generation++;
  clearTimeout(timer); timer = null;
  for (const controller of controllers) controller.abort();
  controllers.clear();
  clearFields(); inputs.clear(); secretNames.clear(); passwordToggles.clear(); current = null; lease = null;
  $('fields').replaceChildren(); $('actions').replaceChildren();
  $('login').hidden = $('cancel').hidden = $('complete').hidden = $('complete-note').hidden = true;
  $('close').hidden = true;
  $('origin-approval').hidden = $('current-origin').hidden = true;
  $('approval-origin').textContent = '';
  $('current-origin').textContent = '';
  $('status').textContent = error ? 'Форму закрито.' : message;
  $('error').textContent = error ? message : '';
  $('error').hidden = !error;
  setBusy(false);
}

function cancelResource(path = '/api/cancel') {
  if (!lease || !requestId) return;
  // A best-effort close also works during pagehide; no input values are included.
  void fetch(path, {
    method: 'POST', credentials: 'omit', cache: 'no-store', redirect: 'error', keepalive: true,
    headers: {'Content-Type': 'application/json', Authorization: 'Bearer ' + lease},
    body: JSON.stringify({request_id: requestId}),
  }).catch(() => {});
}

function fail() {
  cancelResource();
  finish('Канал входу недоступний або запит змінився. Відкрий новий запит у Oak.', true);
}

class FormRefresh extends Error {
  constructor(code) { super(code); this.code = code; }
}

function waitForForm(error) {
  if (!(error instanceof FormRefresh)) return false;
  refreshing = true;
  clearFields(true);
  $('error').textContent = error.code === 'composition_unavailable'
    ? 'Не вдалося скласти форму. Скасуй запит і відкрий новий.'
    : 'Форма оновлюється. Дані не надсилаються повторно.';
  $('error').hidden = false;
  $('cancel').hidden = false;
  return true;
}

async function call(path, extra = {}) {
  const controller = new AbortController(); controllers.add(controller);
  const timeout = setTimeout(() => controller.abort(), 35000);
  try {
    const response = await fetch(path, {
      method: 'POST', credentials: 'omit', cache: 'no-store', redirect: 'error', signal: controller.signal,
      headers: {'Content-Type': 'application/json', ...(lease ? {Authorization: 'Bearer ' + lease} : {})},
      body: JSON.stringify({request_id: requestId, ...extra}),
    });
    if (!response.headers.get('Content-Type')?.startsWith('application/json')) throw new Error('Unavailable');
    const reader = response.body.getReader(), decoder = new TextDecoder();
    let text = '', bytes = 0;
    try {
      for (;;) {
        const chunk = await reader.read();
        if (chunk.done) break;
        bytes += chunk.value.byteLength;
        if (bytes > (formSchema === 'website-v2' ? 131072 : 32768)) throw new Error('Oversized response');
        text += decoder.decode(chunk.value, {stream: true});
      }
      const value = JSON.parse(text + decoder.decode());
      if (!response.ok) {
        if (response.status === 409 && ['/api/form', '/api/submit', '/api/choose-action', '/api/complete', '/api/approve-origin'].includes(path) && record(value)
            && value.recoverable === true && ['stale_form', 'navigation_pending', 'composition_unavailable'].includes(value.code)
            && Object.keys(value).every(key => ['recoverable', 'code', 'error'].includes(key))
            && boundedText(value.error, 200)) throw new FormRefresh(value.code);
        throw new Error('Unavailable');
      }
      return value;
    } finally { await reader.cancel().catch(() => {}); }
  } finally { clearTimeout(timeout); controllers.delete(controller); }
}

function render(value, preserveDrafts = false) {
  if (!record(value) || typeof value.state !== 'string') throw new Error('Invalid state');
  if (owns(END_MESSAGES, value.state)) {
    // Only the backend can establish provider authentication, including a
    // recognized provider reached through the general website adapter.
    if (value.state === 'owner_confirmed' && provider !== 'website') throw new Error('Invalid confirmation');
    finish(END_MESSAGES[value.state]); return;
  }
  if (value.state !== 'active') throw new Error('Invalid state');
  const next = normalizeForm(value.form);
  const drafts = new Map();
  if (preserveDrafts && current && !next.originApproval && !current.originApproval
      && current.currentOrigin === next.currentOrigin
      && current.revision.split(':').slice(0, -1).join(':') === next.revision.split(':').slice(0, -1).join(':')
      && JSON.stringify(current.fields) === JSON.stringify(next.fields)) {
    for (const field of current.fields) if (!secretNames.has(field.name)) {
      const input = inputs.get(field.name);
      drafts.set(field.name, field.type === 'checkbox' ? input.checked : input.value);
    }
  }
  refreshing = false;
  const signature = form => JSON.stringify([form.stage, form.fields, form.actions, form.canComplete, form.currentOrigin, form.originApproval]);
  if (current && current.revision === next.revision) {
    if (signature(current) !== signature(next)) throw new Error('Changed form');
  } else {
    clearFields(); inputs.clear(); secretNames.clear(); passwordToggles.clear();
    $('fields').replaceChildren(); $('actions').replaceChildren();
    for (const field of next.fields) {
      const {label, control: input} = createFormControl({id: 'field-' + field.name,
        name: field.name, label: field.label, type: field.type, maxLength: field.max_length,
        describedBy: 'status', privateInput: true, numeric: field.name === 'otp',
        options: field.options, placeholderDisabled: true});
      if (field.name === 'otp' || ['password', 'otp'].includes(field.type)) secretNames.add(field.name);
      inputs.set(field.name, input);
      if (field.type === 'checkbox') {
        const row = document.createElement('div'); row.className = 'form-field checkbox-field'; input.checked = false;
        row.append(input, label); $('fields').append(row);
      } else {
        const row = document.createElement('div'); row.className = 'form-field';
        if (field.type === 'password') {
          const control = document.createElement('div'); control.className = 'password-control';
          const toggle = passwordToggle(input); passwordToggles.set(field.name, toggle);
          control.append(input, toggle); row.append(label, control);
        } else row.append(label, input);
        $('fields').append(row);
      }
      if (drafts.has(field.name)) {
        if (field.type === 'checkbox') input.checked = drafts.get(field.name);
        else input.value = drafts.get(field.name);
      }
    }
    for (const action of next.actions) {
      const row = document.createElement('div'); row.className = 'action-item';
      const button = document.createElement('button');
      button.type = 'submit'; button.name = 'action'; button.value = action.id; button.textContent = action.label;
      if (action.field_refs?.length === 0 && next.fields.length) button.className = 'quiet';
      button.disabled = busy || action.available === false; row.append(button);
      if (action.available === false) {
        const explanation = document.createElement('p');
        explanation.id = 'unavailable-' + action.id;
        explanation.className = 'action-unavailable';
        explanation.textContent = 'Ця дія зараз недоступна для цього браузера.';
        button.setAttribute('aria-describedby', explanation.id);
        row.append(explanation);
      }
      $('actions').append(row);
    }
    $('login').hidden = next.actions.length === 0;
    const first = inputs.values().next().value ?? $('actions').querySelectorAll('button')[0];
    if (first) first.focus();
  }
  current = next;
  if (next.currentOrigin) updateRecipient(next.currentOrigin);
  $('origin-approval').hidden = !next.originApproval;
  $('approval-origin').textContent = next.originApproval?.origin ?? '';
  // The exact recipient is already the trusted header; avoid a second URL block.
  $('current-origin').hidden = true;
  $('current-origin').textContent = next.currentOrigin && next.currentOrigin !== destination
    ? 'Поточна форма й отримувач введених даних: ' + next.currentOrigin : '';
  $('complete').hidden = $('complete-note').hidden = next.canComplete !== true;
  // Waiting/challenge copy is ours, never a raw provider page or arbitrary link.
  $('status').textContent = (next.originApproval && 'Сайт просить дозволу на перехід. Перевір адресу сервісу нижче.')
    || ((owns(STAGE_FIELDS, next.stage) || next.stage === 'form') && next.message)
    || (next.stage === 'provider_blocked' && BLOCK_MESSAGES.has(next.message) && next.message)
    || STAGES[next.stage];
  $('error').textContent = ''; $('error').hidden = true;
  $('cancel').hidden = false;
  $('close').hidden = false;
}

function schedule() {
  clearTimeout(timer);
  if (lease && !closed) timer = setTimeout(poll, 1500);
}

async function poll() {
  if (closed || !lease) return;
  if (busy) { schedule(); return; }
  const start = generation; setBusy(true);
  try {
    const value = await call('/api/form');
    if (generation === start && !closed) render(value, refreshing);
  } catch (error) { if (generation === start && !closed && !waitForForm(error)) fail(); }
  finally { if (generation === start && !closed) { setBusy(false); schedule(); } }
}

$('login').addEventListener('submit', async event => {
  event.preventDefault();
  if (busy || refreshing || closed || !lease || !current?.actions.length) return;
  const action = event.submitter?.value ?? (current.actions.length === 1 ? current.actions[0].id : null);
  const selected = current.actions.find(item => item.id === action);
  if (!selected || selected.available === false) {
    $('error').textContent = 'Обери дію кнопкою під формою.'; $('error').hidden = false; return;
  }
  const values = Object.create(null);
  const fields = action === 'confirm' ? [] : selected.field_refs
    ? selected.field_refs.map(ref => current.fields.find(field => field.name === ref)) : current.fields;
  for (const field of fields) {
    const input = inputs.get(field.name);
    const value = field.type === 'checkbox' ? input.checked === true : input.value;
    if (field.type !== 'checkbox' && (typeof value !== 'string' || (!['website-v1', 'website-v2'].includes(formSchema) && !value)
        || value.length > field.max_length || field.type === 'select' && !field.options.some(item => item.value === value))) {
      $('error').textContent = 'Заповни поля у вказаних межах.'; $('error').hidden = false;
      inputs.get(field.name).focus(); return;
    }
    values[field.name] = value;
  }
  const start = generation; clearTimeout(timer); setBusy(true);
  try {
    const pending = current.stage === 'clarification'
      ? call('/api/choose-action', {revision: current.revision, choice: values.f0})
      : call('/api/submit', {revision: current.revision, action, values});
    clearFields(true);
    for (const name of Object.keys(values)) delete values[name];
    const accepted = await pending;
    if (generation !== start || closed) return;
    if (!record(accepted) || accepted.accepted !== true) throw new Error('Submission rejected');
    const value = await call('/api/form');
    if (generation === start && !closed) render(value);
  } catch (error) {
    if (generation === start && !closed) {
      if (!waitForForm(error)) fail();
      else {
        try {
          const value = await call('/api/form');
          if (generation === start && !closed) render(value, true);
        } catch (refreshError) {
          if (generation === start && !closed && !waitForForm(refreshError)) fail();
        }
      }
    }
  }
  finally {
    for (const name of Object.keys(values)) delete values[name];
    if (generation === start && !closed) { setBusy(false); schedule(); }
  }
});

async function cancelLogin() {
  if (busy || closed || !lease) return;
  const start = generation; clearTimeout(timer); setBusy(true); clearFields();
  try {
    const value = await call('/api/cancel');
    if (!record(value) || value.state !== 'cancelled') throw new Error('Cancellation rejected');
  }
  catch { if (generation === start && !closed) { fail(); return; } }
  if (generation === start && !closed) finish(END_MESSAGES.cancelled);
}
$('cancel').addEventListener('click', cancelLogin);
$('close').addEventListener('click', cancelLogin);

$('complete').addEventListener('click', async () => {
  if (busy || refreshing || closed || !lease || current?.canComplete !== true) return;
  const start = generation; clearTimeout(timer); setBusy(true); clearFields();
  try {
    const accepted = await call('/api/complete', {revision: current.revision});
    if (!record(accepted) || accepted.accepted !== true) throw new Error('Completion rejected');
    const value = await call('/api/form');
    if (generation === start && !closed) render(value);
  } catch (error) {
    if (generation === start && !closed && !waitForForm(error)) fail();
  } finally {
    if (generation === start && !closed) { setBusy(false); schedule(); }
  }
});

$('approve-origin').addEventListener('click', async () => {
  if (busy || refreshing || closed || !lease || !current?.originApproval) return;
  const start = generation; clearTimeout(timer); setBusy(true); clearFields();
  try {
    const accepted = await call('/api/approve-origin', {revision: current.revision});
    if (!record(accepted) || accepted.approved !== true) throw new Error('Approval rejected');
    const value = await call('/api/form');
    if (generation === start && !closed) render(value);
  } catch (error) {
    if (generation === start && !closed && !waitForForm(error)) fail();
  } finally {
    if (generation === start && !closed) { setBusy(false); schedule(); }
  }
});

window.addEventListener('message', async message => {
  if (message.source !== window.parent || message.origin !== parentOrigin || requestId !== null || closed) return;
  const value = message.data;
  if (!record(value) || value.type !== 'oak-login-attach' || typeof value.request_id !== 'string'
      || !/^[a-f0-9]{32}$/.test(value.request_id) || !token(value.ticket)) return;
  requestId = value.request_id;
  const start = generation; setBusy(true);
  try {
    const attached = await call('/api/attach', {ticket: value.ticket});
    if (generation !== start || closed) return;
    if (!record(attached) || !token(attached.lease)) throw new Error('Invalid lease');
    lease = attached.lease;
    if (!owns(PROVIDERS, attached.provider)) throw new Error('Invalid provider');
    provider = attached.provider;
    explicitSchema = owns(attached, 'form_schema');
    formSchema = explicitSchema ? attached.form_schema : provider === 'website' ? 'website-v1' : 'provider-v1';
    if (!['website-v1', 'website-v2', 'provider-v1'].includes(formSchema)
        || provider === 'website' && !['website-v1', 'website-v2'].includes(formSchema)
        || formSchema === 'website-v2' && provider !== 'website') throw new Error('Invalid form schema');
    if (['website-v1', 'website-v2'].includes(formSchema)) {
      destination = websiteOrigin(attached.destination);
      if (provider !== 'website' && destination !== 'https://' + PROVIDERS[provider].domain) throw new Error('Invalid provider destination');
    }
    $('title').textContent = PROVIDERS[provider].title;
    $('destination').textContent = provider === 'website' ? destination : PROVIDERS[provider].domain;
    document.title = 'Oak — ' + PROVIDERS[provider].title;
    if (provider === 'website') updateRecipient(destination);
    attachmentReady = true;
    $('close').hidden = false;
    reportLayout();
    setBusy(false); void poll();
  } catch { if (generation === start && !closed) fail(); }
});

window.addEventListener('pagehide', () => {
  cancelResource('/api/detach'); finish('Форму закрито.');
});

try {
  const target = new URL(parentOrigin);
  if (target.origin !== parentOrigin || !['https:', 'http:'].includes(target.protocol) || window.parent === window) throw new Error('Invalid parent');
  window.parent.postMessage({type: 'oak-login-ready'}, parentOrigin);
  if (typeof ResizeObserver !== 'undefined') {
    layoutObserver = new ResizeObserver(reportLayout);
    layoutObserver.observe(document.querySelector('main'));
    window.addEventListener('resize', reportLayout);
  }
} catch { finish('Відкрий форму входу із запиту в Oak.', true); }
