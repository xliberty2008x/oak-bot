// Human-only origin. No terminal transcript, values, URL or lease reaches parent.
const $ = id => document.getElementById(id);
const parentOrigin = document.querySelector('meta[name="oak-parent-origin"]').content;
let requestId = null, lease = null, revision = null, closed = false, busy = false, sent = false, timer = null;
const terminal = {authenticated: 'Вхід перевірено. Типи GPU підтримуються, але їхня доступність невідома.',
  cancelled: 'Вхід скасовано.', expired: 'Час входу минув.', failed: 'Вхід не підтверджено.',
  uncertain: 'Результат невідомий. Код не буде передано повторно.'};
const record = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const token = value => typeof value === 'string' && /^[A-Za-z0-9_-]{43}$/.test(value);
function layout() {
  if (!lease || closed) return;
  const height = Math.min(4096, Math.max(1, Math.ceil(document.documentElement.scrollHeight)));
  window.parent.postMessage({type: 'oak-cli-layout', height}, parentOrigin);
}
function clearInput() { $('code').value = ''; $('authorize').removeAttribute('href'); $('authorize').hidden = true; }
function finish(message) {
  closed = true; clearTimeout(timer); clearInput(); $('login').hidden = true; $('cancel').hidden = true;
  $('status').textContent = message; lease = null;
}
async function call(route, data = {}, keepalive = false) {
  const response = await fetch('/cli/api/'+route, {method: 'POST', credentials: 'omit', cache: 'no-store',
    referrerPolicy: 'no-referrer', keepalive, headers: {'Content-Type': 'application/json', ...(lease ? {Authorization: 'Bearer '+lease} : {})},
    body: JSON.stringify({request_id: requestId, ...data})});
  if (!response.ok) throw new Error('Private channel unavailable');
  return response.json();
}
function detach(route = 'detach') {
  if (!lease || closed) return;
  void call(route, {}, true).catch(() => {});
}
function fail() { detach('cancel'); finish('Канал недоступний. Введення не повторюється. Перевір стан у панелі Oak.'); }
function validateURL(value) {
  const url = new URL(value);
  if (url.origin !== 'https://accounts.google.com' || url.username || url.password || url.hash
      || !['/o/oauth2/auth', '/o/oauth2/v2/auth'].includes(url.pathname)
      || typeof value !== 'string' || value.length > 8192 || /[\x00-\x20\x7f\\]/.test(value)) throw new Error('Invalid destination');
  return value;
}
async function poll() {
  if (closed || busy || !lease) return;
  try {
    const value = await call('form');
    if (closed) return;
    if (!record(value) || Object.keys(value).sort().join() !== 'authorization_url,revision,state,submitted'
        || typeof value.submitted !== 'boolean' || !/^[a-f0-9]{32}$/.test(value.revision)) throw new Error('Invalid form');
    if (Object.hasOwn(terminal, value.state)) { finish(terminal[value.state]); return; }
    if (value.state !== 'active') throw new Error('Invalid state');
    if (revision !== null && revision !== value.revision) clearInput();
    revision = value.revision;
    sent ||= value.submitted;
    if (value.authorization_url && !sent) {
      $('authorize').href = validateURL(value.authorization_url); $('authorize').hidden = false;
      $('login').hidden = false; $('status').textContent = 'Відкрий Google, підтвердь дозволи та введи отриманий код тут.';
    } else {
      $('login').hidden = true;
      $('status').textContent = sent ? 'Код передано. Очікуємо перевірку Colab…' : 'Очікуємо приватний процес…';
    }
    layout(); timer = setTimeout(poll, 1000);
  } catch { if (!closed) fail(); }
}
async function submitCode(event) {
  event.preventDefault();
  if (closed || busy || sent || !lease || !revision || !$('code').value) return;
  busy = true; sent = true; clearTimeout(timer); $('submit').disabled = true;
  let code = $('code').value;
  clearInput(); $('login').hidden = true; $('status').textContent = 'Код передається приватному процесу…';
  try {
    const pending = call('submit', {revision, action_id: crypto.randomUUID().replaceAll('-', ''), code});
    code = '';
    const response = await pending;
    if (!record(response) || response.accepted !== true) throw new Error('Input not confirmed');
    if (!closed) { busy = false; void poll(); }
  } catch { if (!closed) fail(); }
  finally { code = ''; busy = false; }
}
// The iframe intentionally has no allow-forms; values travel only over fetch.
$('submit').addEventListener('click', submitCode);
$('login').addEventListener('submit', event => event.preventDefault());
$('code').addEventListener('keydown', event => { if (event.key === 'Enter') void submitCode(event); });
$('cancel').addEventListener('click', () => { detach('cancel'); finish('Вхід скасовано.'); });
window.addEventListener('pagehide', () => { detach(); finish('Форму закрито.'); });
window.addEventListener('message', async event => {
  if (event.source !== window.parent || event.origin !== parentOrigin || requestId !== null || closed) return;
  const value = event.data;
  if (!record(value) || Object.keys(value).sort().join() !== 'request_id,ticket,type'
      || value.type !== 'oak-cli-attach' || !/^[a-f0-9]{32}$/.test(value.request_id) || !token(value.ticket)) return;
  requestId = value.request_id;
  try {
    const response = await call('attach', {ticket: value.ticket});
    value.ticket = null;
    if (closed) return;
    if (!record(response) || Object.keys(response).sort().join() !== 'lease,provider'
        || !token(response.lease) || response.provider !== 'colab') throw new Error('Invalid lease');
    lease = response.lease; $('cancel').hidden = false; layout(); void poll();
  } catch { if (!closed) fail(); }
});
try {
  if (new URL(parentOrigin).origin !== parentOrigin || !parentOrigin.startsWith('https://') || window.parent === window) throw new Error('Invalid parent');
  window.parent.postMessage({type: 'oak-cli-ready'}, parentOrigin);
  new ResizeObserver(layout).observe(document.body);
} catch { finish('Відкрий приватний вхід із панелі Oak.'); }
