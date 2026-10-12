// oak-a2ui-lit@1.1.0; A2UI v0.9.1 / urn:oak:a2ui:canonical:v1.
// Restricted Oak catalogue. Model HTML, URLs, styles and functions are never rendered.
import {createFormControl} from './form-controls.js';
const VERSION = 'v0.9.1', RENDERER = 'oak-a2ui-lit@1.1.0', CATALOG = 'urn:oak:a2ui:canonical:v1';
function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}
// Contextual requests reuse the canonical field widgets, with a distinct transport.
export function renderRequestFields(container, presentation) {
  if (presentation.catalogId !== CATALOG || presentation.version !== VERSION || !Array.isArray(presentation.fields) || presentation.fields.length > 3) throw new Error('Unsupported request form');
  container.replaceChildren();
  const controls = new Map();
  for (const field of presentation.fields) {
    if (!/^[A-Za-z][A-Za-z0-9_]{0,31}$/.test(field.id)) throw new Error('Invalid request field');
    const wrapper = node('div', undefined, 'a2ui-field');
    const {label, control} = createFormControl({id: 'request-field-' + field.id,
      label: field.label, type: field.options ? 'select' : 'text', required: true,
      options: field.options?.map(value => ({value, label: value}))});
    wrapper.append(label, control); container.append(wrapper); controls.set(field.id, control);
  }
  return () => {
    for (const control of controls.values()) if (!control.reportValidity()) return null;
    return Object.fromEntries([...controls].map(([key, control]) => [key, control.value]));
  };
}
export function createA2UIPanel({api, artifact, available, epoch, busy, setBusy, target = () => null, targetHandled = () => {}}) {
  const section = document.getElementById('a2ui-panel'), content = document.getElementById('a2ui-surfaces'), status = document.getElementById('a2ui-status');
  const surfaces = new Map();
  let loading = false, actionBusy = false, generation = 0, serverBusy = false, targetNotice = '', renderer = null;
  async function getRenderer() {
    if (renderer) return renderer;
    const startGeneration = generation, module = await import('/vendor/a2ui/runtime.js');
    if (generation !== startGeneration) throw new Error('Session changed');
    renderer ||= module.createCanonicalRenderer(action => {
      const surface = surfaces.get(action.surfaceId); if (surface) void act(surface, action);
    }, {artifact});
    return renderer;
  }
  function notice(text, error = false) { status.textContent = text; status.classList.toggle('error-message', error); }
  function disable() {
    for (const surface of surfaces.values()) renderer?.disable(surface.id,
      busy() || actionBusy || serverBusy || surface.consumed || Date.now() / 1000 >= surface.expires);
  }
  function render(surface, incoming) {surface.element = renderer.update(incoming);disable();}
  async function act(surface, action) {
    if (!available() || busy() || actionBusy || serverBusy || surface.consumed) return;
    if (Date.now() / 1000 >= surface.expires) return;
    if (action.name !== 'cancel') for (const control of surface.element.querySelectorAll('input,select')) if (!control.reportValidity()) return;
    const startEpoch = epoch(), startGeneration = generation;
    const {name, surfaceId, sourceComponentId, timestamp, context} = action;
    const data = {requestId: crypto.randomUUID(), revision: surface.revision, inputs: renderer.inputs(surface.id),
      message: {version: VERSION, action: {name, surfaceId, sourceComponentId, timestamp, context}}};
    actionBusy = true; setBusy(true); surface.consumed = true; disable(); notice('Передаємо відповідь Oak…');
    try {
      const result = await api('/api/a2ui/action', data);
      if (epoch() !== startEpoch || generation !== startGeneration) return;
      notice(result.status === 'accepted' ? 'Відповідь передано. Oak готує оновлення.' : result.status === 'rejected' ? 'Сесію змінено або Oak зайнятий. Попроси оновлену форму в Telegram.' : 'Результат передавання невідомий. Перевір Telegram перед повтором.', result.status !== 'accepted');
    } catch (error) {
      if ([400, 403, 404, 409].includes(error.status)) surface.consumed = false;
      if (epoch() === startEpoch && generation === startGeneration) notice(error.status === 409 ? 'Форма застаріла або Oak ще працює. Онови панель.' : 'Відповідь не підтверджено. Перевір Telegram перед повтором.', true);
    } finally {
      if (epoch() === startEpoch && generation === startGeneration) { actionBusy = false; setBusy(false); disable(); void refresh(); }
    }
  }
  async function refresh() {
    if (loading || actionBusy || !available()) return;
    loading = true;
    const startEpoch = epoch(), startGeneration = generation;
    try {
      const value = await api('/api/a2ui');
      if (epoch() !== startEpoch || generation !== startGeneration || !available()) return;
      if (value.protocol !== VERSION || value.renderer !== RENDERER || value.catalogId !== CATALOG || !Array.isArray(value.surfaces) || value.surfaces.length > 4) throw new Error('Unsupported protocol');
      await getRenderer();
      if (epoch() !== startEpoch || generation !== startGeneration || !available()) return;
      serverBusy = value.busy === true;
      const ids = new Set();
      for (const incoming of value.surfaces) {
        if (!incoming || !/^[A-Za-z0-9_-]{1,64}$/.test(incoming.id) || ids.has(incoming.id)
            || !Number.isSafeInteger(incoming.revision) || incoming.revision < 1
            || typeof incoming.consumed !== 'boolean' || !Number.isFinite(incoming.expires)
            || !incoming.components || Array.isArray(incoming.components)
            || !incoming.dataModel || Array.isArray(incoming.dataModel)) throw new Error('Invalid surface');
        ids.add(incoming.id);
        let surface = surfaces.get(incoming.id);
        if (!surface) {
          surface = {id: incoming.id};
          surfaces.set(incoming.id, surface);
        }
        if (surface.revision !== incoming.revision) {
          Object.assign(surface, {revision:incoming.revision,consumed:incoming.consumed,expires:incoming.expires}); render(surface,incoming);
          const form = Object.values(incoming.components).some(item => ['TextField', 'ChoicePicker', 'Button'].includes(item?.component));
          notice(serverBusy ? 'Oak ще працює…' : form ? 'Форма для вибраної сесії. Відповідь повернеться Oak у Telegram.' : 'Картка від Oak для вибраної сесії.');
        } else { surface.consumed ||= incoming.consumed; surface.expires = incoming.expires; }
      }
      for (const [id, surface] of surfaces) if (!ids.has(id)) { renderer.remove(id); surfaces.delete(id); }
      [...surfaces.values()].forEach((surface, index) => {
        if (content.children[index] !== surface.element) content.insertBefore(surface.element, content.children[index] || null);
      });
      const requested = target(), linked = requested ? surfaces.get(requested) : null;
      if (requested) {
        targetHandled();
        if (!linked || Date.now() / 1000 >= linked.expires) targetNotice = 'Картка з посилання недоступна або її термін дії минув. Попроси Oak надіслати нову картку в Telegram.';
      }
      section.hidden = surfaces.size === 0 && !targetNotice;
      if (targetNotice) notice(targetNotice, true);
      disable();
      if (requested && !targetNotice && !document.activeElement?.matches('input,select,textarea,[contenteditable="true"]') && !document.querySelector('dialog[open]')) {
        linked.element.focus({preventScroll: true});
        linked.element.scrollIntoView({block: 'start'});
      }
    } catch {
      if (epoch() === startEpoch && generation === startGeneration && available()) { serverBusy = true; disable(); if (target()) section.hidden = false; notice('Форми недоступні. Перевір з’єднання та натисни «Оновити».', true); }
    } finally { if (generation === startGeneration) loading = false; }
  }
  function reset() { generation++; loading = false; actionBusy = false; serverBusy = false; targetNotice = ''; renderer?.dispose(); renderer = null; surfaces.clear(); content.replaceChildren(); section.hidden = true; notice(''); }
  const timer = setInterval(() => { void refresh(); }, 3000);
  const visibility = () => {if (!document.hidden) void refresh();};
  document.addEventListener('visibilitychange', visibility);
  window.addEventListener('pagehide', () => {clearInterval(timer);document.removeEventListener('visibilitychange',visibility);reset();}, {once: true});
  return {refresh, reset, disable};
}
