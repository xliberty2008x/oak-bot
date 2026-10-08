import {renderRequestFields} from './a2ui.js';

export function createInputRequests({api, epoch, scope, available, changed}) {
  const $ = id => document.getElementById(id), dialog = $('input-request'), fields = $('input-request-fields');
  let current = null, readValues = null, submitting = false, generation = 0, poll = null;
  const labels = {submitted: 'Відповідь збережено.', cancelled: 'Запит скасовано.', expired: 'Час очікування минув.',
    authenticated: 'Синтетичний вхід перевірено.', failed: 'Вхід завершився помилкою.'};
  function resultLabel(value) {
    const prefix = labels[value.outcome] || 'Запит недоступний.';
    const cleanup = value.broker?.cleanup === 'uncertain' ? ' Закриття тестового браузера не підтверджено.' :
      value.broker?.cleanup === 'attempted' ? ' Очікуємо закриття тестового браузера.' : '';
    if (value.delivery === 'ready') return prefix + ' Очікуємо передавання до початкової задачі.' + cleanup;
    if (value.delivery === 'uncertain') return prefix + ' Передавання не підтверджено. Перевір задачу в Telegram; автоматичного повтору немає.' + cleanup;
    if (value.delivery === 'sent') return prefix + ' Надіслано початковому запиту; продовження задачі перевір у Telegram.' + cleanup;
    return prefix + cleanup;
  }
  function status(message) { $('input-request-status').textContent = message; }
  function clear() { generation++; current = readValues = null; submitting = false; fields.replaceChildren(); dialog.close(); clearInterval(poll); poll = null; }
  function controls(disabled) { $('input-request-cancel').disabled = disabled; $('input-request-submit').disabled = disabled; for (const control of fields.querySelectorAll('input,select')) control.disabled = disabled; }
  function ended(value) {
    current = value; fields.replaceChildren(); readValues = null; controls(true);
    $('input-request-cancel').disabled = false; $('input-request-cancel').textContent = 'Закрити';
    status(resultLabel(value));
  }
  function synthetic(value, startEpoch, startGeneration) {
    $('input-request-title').textContent = 'Тестовий вхід';
    $('input-request-description').textContent = 'Синтетичний провайдер перевіряє окремий канал. Не вводь дані справжнього акаунта.';
    $('input-request-destination').textContent = 'Локальний тестовий провайдер. Це не вхід в Instagram.';
    const start = document.createElement('button'); start.type = 'button'; start.textContent = 'Відкрити тестовий канал';
    fields.append(start); readValues = null;
    if (value.broker?.phase !== 'available') { start.disabled = true; status('Канал уже розпочато. Запит можна скасувати; автоматичного повтору немає.'); return; }
    status('Очікуємо відкриття тестового каналу.');
    start.addEventListener('click', async () => {
      start.disabled = true;
      try {
        const launch = await api('/api/requests/' + value.id + '/signin?conversation=' + encodeURIComponent(scope()), {});
        if (epoch() !== startEpoch || generation !== startGeneration) return;
        if (launch.provider !== 'oak_synthetic' || launch.simulated !== true || launch.request_id !== value.id) throw new Error('Invalid channel');
        const target = new URL(launch.origin);
        if (target.origin !== launch.origin || !['http:','https:'].includes(target.protocol)) throw new Error('Invalid origin');
        const frame = document.createElement('iframe'); frame.title = 'Окремий канал тестового входу Oak';
        frame.referrerPolicy = 'no-referrer'; frame.className = 'signin-broker';
        frame.setAttribute('sandbox', 'allow-scripts allow-same-origin');
        const receive = message => {
          if (generation !== startGeneration || message.source !== frame.contentWindow || message.origin !== launch.origin || message.data?.type !== 'oak-broker-ready') return;
          frame.contentWindow.postMessage({type: 'oak-broker-attach', request_id: launch.request_id, ticket: launch.ticket}, launch.origin);
          launch.ticket = null; window.removeEventListener('message', receive);
        };
        window.addEventListener('message', receive);
        setTimeout(() => { window.removeEventListener('message', receive); launch.ticket = null; }, 30000);
        frame.src = launch.origin + '/'; fields.replaceChildren(frame); status('Окремий канал відкрито.');
      } catch { if (generation === startGeneration) status('Не вдалося підтвердити відкриття каналу. Автоматично не повторюємо; скасуй запит.'); }
    }, {once: true});
  }
  function watch(id, startEpoch, startGeneration) {
    clearInterval(poll);
    poll = setInterval(async () => {
      try {
        const fresh = await inspect(id, startEpoch, startGeneration);
        if (!fresh) return;
        current = fresh;
        if (fresh.outcome !== 'pending') {
          ended(fresh);
          if (fresh.delivery !== 'ready' && fresh.broker?.cleanup !== 'attempted') { clearInterval(poll); poll = null; }
        } else if (fresh.broker?.phase === 'uncertain') {
          status('Стан відкриття тестового каналу невідомий. Автоматично не повторюємо; запит можна скасувати.');
        }
      } catch {
        if (generation !== startGeneration) return;
        ended({...current, outcome: 'uncertain', delivery: 'uncertain'});
        clearInterval(poll); poll = null;
      }
    }, 2000);
  }
  async function inspect(id, startEpoch, startGeneration) {
    const value = await api('/api/requests/' + encodeURIComponent(id) + '?conversation=' + encodeURIComponent(scope()));
    if (epoch() !== startEpoch || generation !== startGeneration || !available()) return null;
    return value;
  }
  async function open(id) {
    if (!/^[a-f0-9]{32}$/.test(id) || submitting || !available() || $('confirmation').open) return;
    clear(); const startEpoch = epoch(), startGeneration = generation;
    try {
      const value = await inspect(id, startEpoch, startGeneration); if (!value) return;
      current = value; controls(false); $('input-request-cancel').textContent = 'Скасувати';
      $('input-request-destination').hidden = value.kind !== 'sign_in';
      $('input-request-submit').hidden = value.kind === 'sign_in';
      if (value.kind === 'sign_in') {
        if (value.form.provider === 'oak_synthetic' && value.form.simulated === true && value.form.capability === true) {
          synthetic(value, startEpoch, startGeneration);
        } else {
        $('input-request-title').textContent = 'Вхід в Instagram';
        $('input-request-description').textContent = 'Захищений вхід ще недоступний. Oak не має підтвердженого доступу. Скасуй запит, щоб повідомити задачі про це.';
        $('input-request-destination').textContent = 'instagram.com — ціль входу. Поточне підключення до сайту не перевірено.';
        status('Потрібен окремий захищений канал входу.'); readValues = null;
        }
      } else {
        $('input-request-title').textContent = 'Потрібні дані';
        $('input-request-description').textContent = 'Відповідь повернеться до цієї задачі Oak. Не вводь паролі або коди входу.';
        readValues = renderRequestFields(fields, value.form); status('Запит дійсний до ' + new Date(value.expires * 1000).toLocaleTimeString('uk-UA') + '.');
      }
      dialog.showModal();
      if (value.outcome !== 'pending') ended(value);
      if (value.outcome === 'pending' || value.delivery === 'ready') watch(id, startEpoch, startGeneration);
    } catch { if (generation === startGeneration) { clear(); changed('Запит недоступний, завершений або належить іншій сесії.'); } }
  }
  async function decide(decision) {
    if (!current || submitting || current.outcome !== 'pending') { clear(); return; }
    const values = decision === 'submit' ? readValues?.() : null;
    if (decision === 'submit' && !values) return;
    const startEpoch = epoch(), startGeneration = generation;
    const data = {decision, requestId: crypto.randomUUID(), revision: current.revision, ...(decision === 'submit' ? {values} : {})};
    submitting = true; controls(true); status('Передаємо відповідь…');
    try {
      const result = await api('/api/requests/' + current.id + '?conversation=' + encodeURIComponent(scope()), data);
      if (epoch() !== startEpoch || generation !== startGeneration) return;
      ended({...current, ...result}); changed(resultLabel(result));
      watch(current.id, startEpoch, startGeneration);
    } catch (error) {
      if (epoch() !== startEpoch || generation !== startGeneration) return;
      fields.replaceChildren(); readValues = null;
      status([400, 403, 404, 409].includes(error.status) ? 'Запит відхилено. Закрий його й відкрий знову.' : 'Результат передавання невідомий. Автоматично не повторюємо.');
      $('input-request-cancel').disabled = false; current.outcome = 'uncertain';
      $('input-request-cancel').textContent = 'Закрити';
    } finally { if (epoch() === startEpoch && generation === startGeneration) submitting = false; }
  }
  $('input-request-form').addEventListener('submit', event => { event.preventDefault(); void decide('submit'); });
  $('input-request-cancel').addEventListener('click', () => { void decide('cancel'); });
  dialog.addEventListener('cancel', event => { event.preventDefault(); void decide('cancel'); });
  window.addEventListener('pagehide', clear, {once: true});
  return {open, reset: clear};
}
