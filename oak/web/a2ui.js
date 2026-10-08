// oak-a2ui-dom@1.0.0; A2UI v0.9.1 / urn:oak:a2ui:canonical:v1.
// Restricted Oak catalogue. Model HTML, URLs, styles and functions are never rendered.
const VERSION = 'v0.9.1', RENDERER = 'oak-a2ui-dom@1.0.0', CATALOG = 'urn:oak:a2ui:canonical:v1';
const kinds = new Set(['Card', 'Column', 'Text', 'TextField', 'ChoicePicker', 'Button']);
function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}
function parts(path) {
  if (typeof path !== 'string' || !path.startsWith('/') || /~(?![01])/.test(path)) throw new Error('Invalid path');
  const keys = path.slice(1).split('/').map(key => key.replaceAll('~1', '/').replaceAll('~0', '~'));
  if (keys.some(key => !key || ['__proto__', 'constructor', 'prototype'].includes(key))) throw new Error('Unsafe path');
  return keys;
}
const read = (model, path) => parts(path).reduce((value, key) => value && typeof value === 'object' && Object.hasOwn(value, key) ? value[key] : undefined, model);
function write(model, path, value) {
  const keys = parts(path); let parent = model;
  const plain = value => value && typeof value === 'object' && !Array.isArray(value) && [Object.prototype, null].includes(Object.getPrototypeOf(value));
  for (const key of keys.slice(0, -1)) {
    if (!plain(parent)) throw new Error('Invalid binding parent');
    if (!Object.hasOwn(parent, key)) parent[key] = {};
    parent = parent[key];
  }
  if (!plain(parent)) throw new Error('Invalid binding parent');
  parent[keys.at(-1)] = value;
}
const resolve = (value, model) => value && typeof value === 'object' ? read(model, value.path) : value;
function reconcile(parent, children) {
  children.forEach((child, index) => { if (parent.children[index] !== child) parent.insertBefore(child, parent.children[index] || null); });
  while (parent.children.length > children.length) parent.lastElementChild.remove();
}

export function createA2UIPanel({api, available, epoch, busy, setBusy}) {
  const section = document.getElementById('a2ui-panel'), content = document.getElementById('a2ui-surfaces'), status = document.getElementById('a2ui-status');
  const surfaces = new Map();
  let loading = false, actionBusy = false, generation = 0, serverBusy = false;
  function notice(text, error = false) { status.textContent = text; status.classList.toggle('error-message', error); }
  function disable() {
    for (const surface of surfaces.values()) for (const control of surface.element.querySelectorAll('button,input,select')) {
      control.disabled = busy() || actionBusy || serverBusy || surface.consumed || Date.now() / 1000 >= surface.expires;
    }
  }
  function render(surface) {
    const visited = new Set();
    function build(id, ancestors = new Set()) {
      if (ancestors.has(id) || visited.has(id) || ancestors.size > 16) throw new Error('Invalid component graph');
      const item = Object.hasOwn(surface.components, id) ? surface.components[id] : null;
      if (!item) return node('p', 'Oak готує цей елемент…', 'hint');
      if (visited.size >= 64) throw new Error('Invalid component graph');
      visited.add(id);
      if (!kinds.has(item.component)) throw new Error('Unsupported component');
      let element = surface.nodes.get(id);
      if (element?.dataset.kind !== item.component) {
        element = node(item.component === 'Button' ? 'button' : item.component === 'Text' ? 'p' : 'div');
        element.dataset.kind = item.component; surface.nodes.set(id, element);
      }
      if (item.component === 'Card' || item.component === 'Column') {
        element.className = item.component === 'Card' ? 'topic-form a2ui-card' : 'a2ui-column';
        reconcile(element, (item.component === 'Card' ? [item.child] : item.children).map(child => build(child, new Set([...ancestors, id]))));
      } else if (item.component === 'Text') {
        element.className = item.variant === 'hint' ? 'hint' : item.variant === 'heading' ? 'a2ui-heading' : 'a2ui-text';
        const text = resolve(item.text, surface.dataModel);
        if (text != null && typeof text !== 'string') throw new Error('Invalid text binding');
        element.textContent = text || '';
      } else if (item.component === 'TextField' || item.component === 'ChoicePicker') {
        element.className = 'a2ui-field';
        const inputId = 'a2ui-' + surface.id + '-' + id;
        let label = element.querySelector('label'), control = element.querySelector('input,select');
        if (!control) {
          label = node('label'); label.htmlFor = inputId;
          control = node(item.component === 'TextField' ? 'input' : 'select'); control.id = inputId;
          control.addEventListener('input', () => {
            const current = surface.components[id];
            const value = current.component === 'TextField' ? control.value : control.value ? [control.value] : [];
            write(surface.dataModel, current.value.path, value); surface.inputs[current.value.path] = value;
          });
          element.replaceChildren(label, control);
        }
        label.textContent = item.label; control.required = item.required === true;
        if (item.component === 'TextField') { control.type = 'text'; control.maxLength = 2000; control.autocomplete = 'off'; }
        else {
          const options = [node('option', 'Обери варіант')]; options[0].value = '';
          for (const choice of item.options) { const option = node('option', choice.label); option.value = choice.value; options.push(option); }
          control.replaceChildren(...options);
        }
        const value = read(surface.dataModel, item.value.path);
        control.value = item.component === 'TextField' ? typeof value === 'string' ? value : '' : Array.isArray(value) ? value[0] || '' : '';
        if (value == null) write(surface.dataModel, item.value.path, item.component === 'TextField' ? '' : []);
      } else if (item.component === 'Button') {
        element.type = 'button'; element.className = item.variant === 'quiet' ? 'quiet' : item.variant === 'danger' ? 'danger' : '';
        element.textContent = item.label; element.onclick = () => { void act(surface, id); };
      }
      return element;
    }
    reconcile(surface.element, [build('root')]);
    for (const id of surface.nodes.keys()) if (!visited.has(id)) surface.nodes.delete(id);
    disable();
  }
  async function act(surface, id) {
    if (!available() || busy() || actionBusy || serverBusy || surface.consumed) return;
    const event = surface.components[id].action.event;
    if (event.name !== 'cancel') for (const control of surface.element.querySelectorAll('input,select')) if (!control.reportValidity()) return;
    const startEpoch = epoch(), startGeneration = generation;
    const context = Object.fromEntries(Object.entries(event.context || {}).map(([key, value]) => [key, resolve(value, surface.dataModel) ?? null]));
    const data = {requestId: crypto.randomUUID(), revision: surface.revision, inputs: surface.inputs,
      message: {version: VERSION, action: {name: event.name, surfaceId: surface.id, sourceComponentId: id, timestamp: new Date().toISOString(), context}}};
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
      serverBusy = value.busy === true;
      const ids = new Set();
      for (const incoming of value.surfaces) {
        if (!/^[A-Za-z0-9_-]{1,64}$/.test(incoming.id) || !Number.isSafeInteger(incoming.revision) || incoming.revision < 1 || !incoming.components || !incoming.dataModel) throw new Error('Invalid surface');
        ids.add(incoming.id);
        let surface = surfaces.get(incoming.id);
        if (!surface) { surface = {id: incoming.id, element: node('div'), nodes: new Map(), inputs: {}}; surfaces.set(incoming.id, surface); }
        if (surface.revision !== incoming.revision) {
          Object.assign(surface, incoming, {inputs: {}}); render(surface);
          notice(serverBusy ? 'Oak ще працює…' : 'Форма для вибраної сесії. Відповідь повернеться Oak у Telegram.');
        } else { surface.consumed ||= incoming.consumed; surface.expires = incoming.expires; }
      }
      for (const [id, surface] of surfaces) if (!ids.has(id)) { surface.element.remove(); surfaces.delete(id); }
      reconcile(content, [...surfaces.values()].map(surface => surface.element)); section.hidden = surfaces.size === 0; disable();
    } catch {
      if (epoch() === startEpoch && generation === startGeneration && available()) { serverBusy = true; disable(); notice('Форми недоступні. Перевір з’єднання та натисни «Оновити».', true); }
    } finally { if (generation === startGeneration) loading = false; }
  }
  function reset() { generation++; loading = false; actionBusy = false; serverBusy = false; surfaces.clear(); content.replaceChildren(); section.hidden = true; notice(''); }
  const timer = setInterval(() => { void refresh(); }, 3000);
  window.addEventListener('pagehide', () => clearInterval(timer), {once: true});
  document.addEventListener('visibilitychange', () => { if (!document.hidden) void refresh(); });
  return {refresh, reset, disable};
}
