// oak-a2ui-dom@1.0.0; A2UI v0.9.1 / urn:oak:a2ui:canonical:v1.
// Restricted Oak catalogue. Model HTML, URLs, styles and functions are never rendered.
const VERSION = 'v0.9.1', RENDERER = 'oak-a2ui-dom@1.1.0', CATALOG = 'urn:oak:a2ui:canonical:v1';
const kinds = new Set(['Card', 'Column', 'Row', 'Text', 'TextField', 'ChoicePicker', 'Button',
  'Divider', 'Badge', 'Metric', 'Progress', 'Table', 'Chart', 'Image']);
const tones = new Set(['neutral', 'success', 'warning', 'danger', 'info']);
function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}
const SVG = 'http://www.w3.org/2000/svg';
function svg(tag, attributes = {}, text) {
  const element = document.createElementNS(SVG, tag);
  for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, String(value));
  if (text !== undefined) element.textContent = text;
  return element;
}
const finite = value => typeof value === 'number' && Number.isFinite(value);
const format = value => Number.isInteger(value) ? String(value) : String(Math.round(value * 100) / 100);
function strings(value, low, high) {
  if (!Array.isArray(value) || value.length < low || value.length > high || value.some(item => typeof item !== 'string')) throw new Error('Invalid list');
  return value;
}

function progress(item) {
  const segments = item.segments;
  if (!Array.isArray(segments) || !segments.length || segments.length > 6 || segments.some(s => typeof s?.label !== 'string' || !finite(s.value) || s.value < 0)) throw new Error('Invalid progress');
  const total = segments.reduce((sum, s) => sum + s.value, 0);
  if (!(total > 0)) throw new Error('Invalid progress');
  const element = node('figure', undefined, 'a2ui-progress');
  if (item.label) element.append(node('figcaption', item.label, 'hint'));
  const bar = node('div', undefined, 'a2ui-progress-bar'), legend = node('div', undefined, 'a2ui-legend');
  segments.forEach((segment, index) => {
    const share = Math.round(100 * segment.value / total);
    const part = node('span', undefined, 'a2ui-series-' + index);
    part.style.flexGrow = String(segment.value);
    bar.append(part);
    legend.append(node('span', segment.label + ' ' + share + '%', 'a2ui-key a2ui-series-' + index));
  });
  bar.setAttribute('role', 'img');
  bar.setAttribute('aria-label', segments.map(s => s.label + ' ' + Math.round(100 * s.value / total) + '%').join(', '));
  element.append(bar, legend);
  return element;
}

function table(item) {
  const columns = strings(item.columns, 1, 8);
  if (!Array.isArray(item.rows) || item.rows.length > 50) throw new Error('Invalid table');
  const wrapper = node('div', undefined, 'a2ui-table'), element = node('table');
  if (item.caption) element.append(node('caption', item.caption));
  const head = node('tr');
  for (const column of columns) { const cell = node('th', column); cell.scope = 'col'; head.append(cell); }
  const body = node('tbody');
  for (const row of item.rows) {
    const line = node('tr');
    for (const value of strings(row, columns.length, columns.length)) line.append(node('td', value));
    body.append(line);
  }
  const thead = node('thead'); thead.append(head);
  element.append(thead, body); wrapper.append(element);
  return wrapper;
}

function chart(item) {
  const labels = strings(item.labels, 1, 32), series = item.series;
  if (!['bar', 'line'].includes(item.kind) || !Array.isArray(series) || !series.length || series.length > 4
      || series.some(s => typeof s?.label !== 'string' || !Array.isArray(s.values) || s.values.length !== labels.length || !s.values.every(finite))) throw new Error('Invalid chart');
  const unit = typeof item.unit === 'string' && item.unit ? ' ' + item.unit : '';
  const values = series.flatMap(s => s.values), low = Math.min(0, ...values), high = Math.max(0, ...values);
  const range = high - low || 1, width = 320, height = 180, left = 8, top = 12, bottom = 28, plot = height - top - bottom;
  const x = index => left + (width - left * 2) * (index + 0.5) / labels.length;
  const y = value => top + plot * (high - value) / range;
  const element = node('figure', undefined, 'a2ui-chart');
  if (item.title) element.append(node('figcaption', item.title, 'a2ui-chart-title'));
  const canvas = svg('svg', {viewBox: `0 0 ${width} ${height}`, role: 'img'});
  canvas.setAttribute('aria-label', (item.title ? item.title + ': ' : '') + series.map(s => s.label + ' ' + s.values.map((v, i) => labels[i] + ' ' + format(v) + unit).join(', ')).join('; '));
  canvas.append(svg('line', {x1: left, x2: width - left, y1: y(0), y2: y(0), class: 'a2ui-axis'}));
  const step = (width - left * 2) / labels.length, band = step * 0.7 / series.length;
  series.forEach((line, index) => {
    if (item.kind === 'bar') {
      line.values.forEach((value, point) => {
        const bar = svg('rect', {x: x(point) - step * 0.35 + band * index, y: Math.min(y(value), y(0)), width: Math.max(band - 2, 1),
          height: Math.max(Math.abs(y(value) - y(0)), 1), rx: 3, class: 'a2ui-fill-' + index});
        bar.append(svg('title', {}, labels[point] + ' · ' + line.label + ': ' + format(value) + unit));
        canvas.append(bar);
      });
    } else {
      canvas.append(svg('polyline', {points: line.values.map((value, point) => x(point) + ',' + y(value)).join(' '), class: 'a2ui-stroke-' + index}));
      line.values.forEach((value, point) => {
        const dot = svg('circle', {cx: x(point), cy: y(value), r: 3.5, class: 'a2ui-fill-' + index});
        dot.append(svg('title', {}, labels[point] + ' · ' + line.label + ': ' + format(value) + unit));
        canvas.append(dot);
      });
    }
  });
  const every = Math.ceil(labels.length / 8);
  labels.forEach((label, point) => {
    if (point % every === 0) canvas.append(svg('text', {x: x(point), y: height - 9, 'text-anchor': 'middle', class: 'a2ui-tick'}, label.length > 12 ? label.slice(0, 11) + '…' : label));
  });
  element.append(canvas);
  const legend = node('div', undefined, 'a2ui-legend');
  series.forEach((line, index) => legend.append(node('span', line.label + ' · макс. ' + format(Math.max(...line.values)) + unit, 'a2ui-key a2ui-series-' + index)));
  element.append(legend);
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

// Contextual requests reuse the canonical field widgets, with a distinct transport.
export function renderRequestFields(container, presentation) {
  if (presentation.catalogId !== CATALOG || presentation.version !== VERSION || !Array.isArray(presentation.fields) || presentation.fields.length > 3) throw new Error('Unsupported request form');
  container.replaceChildren();
  const controls = new Map();
  for (const field of presentation.fields) {
    if (!/^[A-Za-z][A-Za-z0-9_]{0,31}$/.test(field.id)) throw new Error('Invalid request field');
    const wrapper = node('div', undefined, 'a2ui-field'), label = node('label', field.label);
    const control = node(field.options ? 'select' : 'input');
    control.id = 'request-field-' + field.id; label.htmlFor = control.id; control.required = true;
    if (field.options) {
      const empty = node('option', 'Обери варіант'); empty.value = ''; control.append(empty);
      for (const value of field.options) { const option = node('option', value); option.value = value; control.append(option); }
    } else { control.type = 'text'; control.maxLength = 2000; control.autocomplete = 'off'; }
    wrapper.append(label, control); container.append(wrapper); controls.set(field.id, control);
  }
  return () => {
    for (const control of controls.values()) if (!control.reportValidity()) return null;
    return Object.fromEntries([...controls].map(([key, control]) => [key, control.value]));
  };
}
function reconcile(parent, children) {
  children.forEach((child, index) => { if (parent.children[index] !== child) parent.insertBefore(child, parent.children[index] || null); });
  while (parent.children.length > children.length) parent.lastElementChild.remove();
}

export function createA2UIPanel({api, artifact, available, epoch, busy, setBusy, target = () => null, targetHandled = () => {}}) {
  const section = document.getElementById('a2ui-panel'), content = document.getElementById('a2ui-surfaces'), status = document.getElementById('a2ui-status');
  const surfaces = new Map(), images = new Map();
  function image(element, id) {
    if (element.dataset.artifact === id) return;
    element.dataset.artifact = id;
    if (!images.has(id)) images.set(id, artifact(id).then(blob => URL.createObjectURL(blob)).catch(error => { images.delete(id); throw error; }));
    const loading = generation;
    images.get(id).then(url => { if (loading === generation && element.dataset.artifact === id) element.src = url; })
      .catch(() => { if (element.dataset.artifact === id) { delete element.dataset.artifact; element.alt = 'Зображення недоступне: ' + element.alt; } });
  }
  let loading = false, actionBusy = false, generation = 0, serverBusy = false, targetNotice = '';
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
        element = node({Button: 'button', Text: 'p', Image: 'img', Divider: 'hr', Badge: 'span'}[item.component] || 'div');
        element.dataset.kind = item.component; surface.nodes.set(id, element);
      }
      if (item.component === 'Card' || item.component === 'Column' || item.component === 'Row') {
        element.className = {Card: 'topic-form a2ui-card', Column: 'a2ui-column', Row: 'a2ui-row'}[item.component];
        reconcile(element, (item.component === 'Card' ? [item.child] : item.children).map(child => build(child, new Set([...ancestors, id]))));
      } else if (item.component === 'Divider') {
        element.className = 'a2ui-divider';
      } else if (item.component === 'Badge') {
        const text = resolve(item.text, surface.dataModel);
        if (text != null && typeof text !== 'string') throw new Error('Invalid badge binding');
        element.className = 'a2ui-badge a2ui-tone-' + (tones.has(item.tone) ? item.tone : 'neutral');
        element.textContent = text || '';
      } else if (item.component === 'Metric') {
        const value = resolve(item.value, surface.dataModel);
        if (value != null && typeof value !== 'string') throw new Error('Invalid metric binding');
        element.className = 'a2ui-metric';
        const parts = [node('strong', value || '—'), node('span', item.label, 'hint')];
        if (item.hint) parts.push(node('small', item.hint, 'hint'));
        element.replaceChildren(...parts);
      } else if (item.component === 'Progress') {
        element.className = 'a2ui-block'; element.replaceChildren(progress(item));
      } else if (item.component === 'Table') {
        element.className = 'a2ui-block'; element.replaceChildren(table(item));
      } else if (item.component === 'Chart') {
        element.className = 'a2ui-block'; element.replaceChildren(chart(item));
      } else if (item.component === 'Image') {
        if (typeof item.artifact !== 'string' || !/^[a-f0-9]{32}$/.test(item.artifact)) throw new Error('Invalid image');
        element.className = 'a2ui-image'; element.alt = item.alt; element.decoding = 'async'; element.loading = 'lazy';
        image(element, item.artifact);
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
        if (!surface) {
          surface = {id: incoming.id, element: node('div'), nodes: new Map(), inputs: {}};
          surface.element.id = 'a2ui-surface-' + incoming.id; surface.element.tabIndex = -1;
          surface.element.setAttribute('role', 'group'); surface.element.setAttribute('aria-label', 'Картка від Oak');
          surfaces.set(incoming.id, surface);
        }
        if (surface.revision !== incoming.revision) {
          Object.assign(surface, incoming, {inputs: {}}); render(surface);
          const form = Object.values(surface.components).some(item => ['TextField', 'ChoicePicker', 'Button'].includes(item?.component));
          notice(serverBusy ? 'Oak ще працює…' : form ? 'Форма для вибраної сесії. Відповідь повернеться Oak у Telegram.' : 'Картка від Oak для вибраної сесії.');
        } else { surface.consumed ||= incoming.consumed; surface.expires = incoming.expires; }
      }
      for (const [id, surface] of surfaces) if (!ids.has(id)) { surface.element.remove(); surfaces.delete(id); }
      reconcile(content, [...surfaces.values()].map(surface => surface.element));
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
  function reset() {
    generation++; loading = false; actionBusy = false; serverBusy = false; targetNotice = ''; surfaces.clear(); content.replaceChildren(); section.hidden = true; notice('');
    for (const url of images.values()) void url.then(URL.revokeObjectURL, () => {});
    images.clear();
  }
  const timer = setInterval(() => { void refresh(); }, 3000);
  window.addEventListener('pagehide', () => clearInterval(timer), {once: true});
  document.addEventListener('visibilitychange', () => { if (!document.hidden) void refresh(); });
  return {refresh, reset, disable};
}
