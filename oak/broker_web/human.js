// Human data plane. Never post input, frames, leases or cookies to the parent.
const $ = id => document.getElementById(id), parentOrigin = document.querySelector('meta[name="oak-parent-origin"]').content;
let requestId = null, lease = null, epoch = null, timer = null, busy = false, blob = null;
let queue = Promise.resolve(), queued = 0;
let generation = 0, submitting = false;
const controllers = new Set();
function stop(message) {
  generation++; for (const controller of controllers) controller.abort(); controllers.clear();
  clearTimeout(timer); timer = null; lease = null; epoch = null;
  $('text').value = ''; $('typing').hidden = $('cancel').hidden = $('screen').hidden = true;
  $('screen').removeAttribute('src'); if (blob) URL.revokeObjectURL(blob); blob = null;
  delete $('screen').dataset.navigationEpoch;
  $('status').textContent = message;
}
async function call(path, extra = {}) {
  const controller = new AbortController(); controllers.add(controller);
  try {
  const response = await fetch(path, {method: 'POST', credentials: 'omit', cache: 'no-store', signal:controller.signal,
    headers: {'Content-Type': 'application/json', ...(lease ? {Authorization: 'Bearer ' + lease} : {})},
    body: JSON.stringify({request_id: requestId, ...extra})});
  if (!response.ok) throw new Error('Human channel unavailable');
  return new Response(await response.arrayBuffer(), {status:response.status, headers:response.headers});
  } finally { controllers.delete(controller); }
}
async function frame() {
  if (!lease || busy) { if (lease) timer = setTimeout(frame, 400); return; }
  busy = true;
  const start = generation;
  try {
    const response = await call('/api/frame');
    if (generation !== start || !lease) return;
    if (response.headers.get('Content-Type')?.startsWith('application/json')) {
      const value = await response.json();
      if (generation !== start || !lease) return;
      if (value.state === 'authenticated' && value.simulated === true) stop('Тестовий вхід перевірено. Результат повертається задачі.');
      else stop('Канал завершено.');
      return;
    }
    const pixels = await response.blob();
    if (generation !== start || !lease) return;
    const next = URL.createObjectURL(pixels), previous = blob;
    blob = next; $('screen').src = next; if (previous) URL.revokeObjectURL(previous);
    epoch = Number(response.headers.get('X-Oak-Navigation-Epoch'));
    $('screen').dataset.navigationEpoch = String(epoch);
    $('destination').textContent = 'Перевірене тестове призначення: ' + response.headers.get('X-Oak-Verified-Origin');
    $('screen').hidden = $('typing').hidden = $('cancel').hidden = false;
    $('status').textContent = 'Вибери поле на сторінці. Використовуй тільки тестові дані.';
  } catch { if (generation === start) stop('Канал завершений або недоступний. Перевір статус задачі.'); }
  finally { busy = false; if (lease) timer = setTimeout(frame, 400); }
}
async function event(value) {
  if (!lease || epoch === null) return false;
  if (queued >= 16) { $('status').textContent = 'Черга введення переповнена. Зачекай і повтори дію.'; return false; }
  const navigation = epoch, start = generation; queued++;
  queue = queue.then(async () => {
    while (busy && lease) await new Promise(resolve => setTimeout(resolve, 20));
    if (!lease || generation !== start) { queued--; return false; }
    busy = true;
    try { await call('/api/event', {event: {...value, epoch: navigation}}); return true; }
    catch { if (generation === start) stop('Введення відхилено: сторінка або строк запиту змінилися.'); return false; }
    finally { busy = false; queued--; }
  });
  return queue;
}
window.addEventListener('message', async message => {
  if (message.source !== parent || message.origin !== parentOrigin || requestId !== null) return;
  const value = message.data;
  if (value?.type !== 'oak-broker-attach' || !/^[a-f0-9]{32}$/.test(value.request_id) || typeof value.ticket !== 'string') return;
  requestId = value.request_id;
  const start = generation;
  try {
    const response = await call('/api/attach', {ticket: value.ticket});
    if (generation !== start) return;
    const attached = await response.json();
    if (generation !== start) return;
    if (attached.provider !== 'oak_synthetic' || attached.simulated !== true) throw new Error('Invalid provider');
    lease = attached.lease; void frame();
  } catch { if (generation === start) stop('Не вдалося відкрити тестовий канал. Скасуй запит у Oak.'); }
});
$('screen').addEventListener('click', mouse => {
  const bounds = $('screen').getBoundingClientRect();
  void event({kind: 'click', x: (mouse.clientX - bounds.left) * 390 / bounds.width, y: (mouse.clientY - bounds.top) * 530 / bounds.height});
});
$('screen').addEventListener('keydown', key => {
  if (['Tab','Enter','Backspace','ArrowLeft','ArrowRight'].includes(key.key)) { key.preventDefault(); void event({kind: 'key', key: key.key}); }
  else if (key.key.length === 1 && !key.ctrlKey && !key.metaKey) { key.preventDefault(); void event({kind: 'text', text: key.key}); }
});
$('send').addEventListener('click', async () => {
  const text = $('text').value; if (!text || submitting) return;
  submitting = true; for (const control of $('typing').querySelectorAll('input,button')) control.disabled = true;
  try { if (await event({kind: 'text', text})) $('text').value = ''; }
  finally { submitting = false; for (const control of $('typing').querySelectorAll('input,button')) control.disabled = false; }
});
$('text').addEventListener('keydown', key => { if (key.key === 'Enter') { key.preventDefault(); $('send').click(); } });
for (const name of ['tab','enter']) $(name).addEventListener('click', () => { void event({kind: 'key', key: name === 'tab' ? 'Tab' : 'Enter'}); });
$('cancel').addEventListener('click', async () => { try { await call('/api/cancel'); } finally { stop('Тестовий вхід скасовано.'); } });
window.addEventListener('pagehide', () => {
  if (lease) void fetch('/api/cancel', {method:'POST', credentials:'omit', keepalive:true,
    headers:{'Content-Type':'application/json', Authorization:'Bearer '+lease}, body:JSON.stringify({request_id:requestId})}).catch(() => {});
  stop('Канал закрито.');
});
parent.postMessage({type: 'oak-broker-ready'}, parentOrigin);
