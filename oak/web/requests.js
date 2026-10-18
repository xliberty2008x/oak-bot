import {renderRequestFields} from './a2ui.js';

export function createInputRequests({api, epoch, scope, available, changed}) {
  const $ = id => document.getElementById(id), dialog = $('input-request'), fields = $('input-request-fields');
  let current = null, readValues = null, submitting = false, generation = 0, poll = null, removeFrameListeners = null;
  const labels = {submitted: 'Відповідь збережено.', cancelled: 'Запит скасовано.', expired: 'Час очікування минув.',
    authenticated: 'Синтетичний вхід перевірено.', owner_confirmed: 'Ти підтвердив завершення форми. Oak може продовжити цю задачу.', failed: 'Вхід завершився помилкою.'};
  function resultLabel(value) {
    const real = ['sign_in', 'cli_auth'].includes(value.kind) && value.form?.simulated === false;
    const prefix = value.outcome === 'authenticated' && real ? 'Вхід підтверджено для поточної задачі.' : labels[value.outcome] || 'Запит недоступний.';
    const resource = value.kind === 'cli_auth' ? 'приватного процесу' : 'браузера';
    const cleanup = value.broker?.cleanup === 'uncertain' ? ' Закриття ' + resource + ' не підтверджено.' :
      value.broker?.cleanup === 'attempted' ? ' Очікуємо закриття ' + resource + '.' : '';
    if (value.delivery === 'ready') return prefix + ' Очікуємо передавання до початкової задачі.' + cleanup;
    if (value.delivery === 'uncertain') return prefix + ' Передавання не підтверджено. Перевір задачу в Telegram; автоматичного повтору немає.' + cleanup;
    if (value.delivery === 'sent') return prefix + ' Надіслано початковому запиту; продовження задачі перевір у Telegram.' + cleanup;
    return prefix + cleanup;
  }
  function status(message) { $('input-request-status').textContent = message; }
  function releaseFrame() { removeFrameListeners?.(); removeFrameListeners = null; dialog.classList.remove('signin-host', 'signin-loading'); }
  function clear() { generation++; current = readValues = null; submitting = false; releaseFrame(); fields.replaceChildren(); dialog.close(); clearInterval(poll); poll = null; }
  function controls(disabled) { $('input-request-cancel').disabled = disabled; $('input-request-submit').disabled = disabled; for (const control of fields.querySelectorAll('input,select')) control.disabled = disabled; }
  function ended(value) {
    current = value; releaseFrame(); fields.replaceChildren(); readValues = null; controls(true);
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
  function website(value, startEpoch, startGeneration) {
    const cli = value.kind === 'cli_auth';
    const generic = value.form.provider === 'website';
    const name = cli ? 'Colab CLI' : value.form.provider === 'google' ? 'Google' : generic ? value.form.destination : 'Instagram';
    $('input-request-title').textContent = generic ? 'Вхід до ' + new URL(value.form.destination).hostname : 'Вхід у ' + name;
    $('input-request-description').textContent = cli
      ? 'Дозволь окремий вхід у Colab для цієї задачі. Код вводиться лише у приватній формі. Перевірка не запускає GPU.'
      : 'Введи дані в окремій формі. Доступ Oak до цієї задачі діє до 15 хвилин.';
    $('input-request-destination').textContent = value.form.destination;
    const start = document.createElement('button'); start.type = 'button'; start.textContent = cli ? 'Відкрити приватний вхід у Colab' : 'Продовжити вхід';
    fields.append(start); readValues = null;
    if (value.broker?.phase !== 'available') { start.disabled = true; status('Вхід уже розпочато. Автоматичного повтору немає.'); return; }
    status('Пароль і коди вводяться лише в окремій формі входу.');
    const launchWebsite = async () => {
      if (start.disabled || generation !== startGeneration || epoch() !== startEpoch || !available()) return;
      start.disabled = true;
      try {
        const launch = await api('/api/requests/' + value.id + '/signin?conversation=' + encodeURIComponent(scope()), {});
        if (epoch() !== startEpoch || generation !== startGeneration || submitting
            || current?.id !== value.id || current?.outcome !== 'pending') {
          if (launch && typeof launch === 'object') launch.ticket = null;
          return;
        }
        if (launch.provider !== value.form.provider || launch.simulated !== false || launch.request_id !== value.id || !/^[A-Za-z0-9_-]{43}$/.test(launch.ticket)) throw new Error('Invalid channel');
        if (cli && launch.path !== '/cli/') throw new Error('Invalid private path');
        const target = new URL(launch.origin);
        if (target.origin !== launch.origin || target.protocol !== 'https:' || target.origin === location.origin) throw new Error('Invalid origin');
        const frame = document.createElement('iframe'); frame.title = 'Вхід у ' + name;
        frame.referrerPolicy = 'no-referrer'; frame.className = 'signin-broker';
        frame.setAttribute('sandbox', cli ? 'allow-scripts allow-same-origin allow-popups allow-popups-to-escape-sandbox' : 'allow-scripts allow-same-origin');
        const channel = cli ? 'oak-cli' : 'oak-login';
        let attached = false, laidOut = false;
        const receive = message => {
          if (generation !== startGeneration || epoch() !== startEpoch || message.source !== frame.contentWindow || message.origin !== launch.origin) return;
          const data = message.data;
          if (attached && data?.type === channel + '-layout' && Object.keys(data).length === 2
              && Number.isInteger(data.height) && data.height >= 1 && data.height <= 4096) {
            frame.style.height = Math.max(240, data.height) + 'px';
            laidOut = true;
            clearTimeout(handshake);
            dialog.classList.remove('signin-loading');
          } else if (!attached && data?.type === channel + '-ready' && Object.keys(data).length === 1) {
            attached = true;
            frame.contentWindow.postMessage({type: channel + '-attach', request_id: launch.request_id, ticket: launch.ticket}, launch.origin);
            launch.ticket = null;
          }
        };
        const handshake = setTimeout(() => {
          if (generation !== startGeneration || epoch() !== startEpoch || laidOut) return;
          releaseFrame(); fields.replaceChildren();
          status('Форма не завантажилась. Скасуй запит і почни новий вхід.');
        }, 30000);
        releaseFrame();
        removeFrameListeners = () => { clearTimeout(handshake); window.removeEventListener('message', receive); launch.ticket = null; };
        window.addEventListener('message', receive);
        frame.src = launch.origin + (cli ? '/cli/' : '/'); fields.replaceChildren(frame); dialog.classList.add('signin-host', 'signin-loading'); status('Відкриваємо захищену форму…');
      } catch {
        if (generation === startGeneration && epoch() === startEpoch && !submitting
            && current?.id === value.id && current?.outcome === 'pending') {
          status('Форму входу не відкрито. Перевір стан або скасуй запит.');
        }
      }
    };
    start.addEventListener('click', launchWebsite, {once: true});
    return cli ? null : launchWebsite; // CLI requires its explicit owner button.
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
          status('Стан відкриття каналу невідомий. Автоматично не повторюємо; запит можна скасувати.');
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
      let launchWebsite = null;
      $('input-request-destination').hidden = !['sign_in', 'cli_auth'].includes(value.kind);
      $('input-request-submit').hidden = ['sign_in', 'cli_auth'].includes(value.kind);
      if (value.kind === 'cli_auth' && value.form.provider === 'colab' && value.form.capability === true) {
        website(value, startEpoch, startGeneration);
      } else if (value.kind === 'sign_in') {
        if (value.form.provider === 'oak_synthetic' && value.form.simulated === true && value.form.capability === true) {
          synthetic(value, startEpoch, startGeneration);
        } else if (['google', 'instagram', 'website'].includes(value.form.provider) && value.form.simulated === false && value.form.capability === true) {
          launchWebsite = website(value, startEpoch, startGeneration);
        } else {
        $('input-request-title').textContent = value.form.provider === 'website' ? 'Форма сайту' : 'Вхід у ' + (value.form.provider === 'google' ? 'Google' : 'Instagram');
        $('input-request-description').textContent = 'Захищений вхід ще недоступний. Oak не має підтвердженого доступу. Скасуй запит, щоб повідомити задачі про це.';
        $('input-request-destination').textContent = value.form.destination + ' — ціль форми. Поточне підключення до сайту не перевірено.';
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
      if (value.outcome === 'pending') void launchWebsite?.();
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
