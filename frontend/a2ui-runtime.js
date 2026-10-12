// Restricted ordinary UI only. Private website values never enter this module.
import {html, svg, nothing} from 'lit';
import {z} from 'zod';
import {A2uiLitElement, A2uiSurface} from '@a2ui/lit/v0_9';
import {Catalog, MessageProcessor} from '@a2ui/web_core/v0_9';
import {A2uiCard, A2uiColumn, A2uiRow, A2uiTextField} from '@a2ui/web_core/v0_9/basic_catalog';
import rawCatalog from '../oak/web/a2ui-catalog.json';

class OakSurface extends A2uiSurface {createRenderRoot() {return this;}}
customElements.define('oak-a2ui-surface', OakSurface);
class OakText extends A2uiLitElement {
  createRenderRoot() {return this;}
  render() { const p = this.controller?.props; return p ? html`<p class=${p.variant === 'heading' ? 'a2ui-heading' : p.variant === 'hint' ? 'hint' : 'a2ui-text'}>${p.text ?? ''}</p>` : nothing; }
}
class OakTextField extends A2uiTextField.element {
  updated(changes) {
    super.updated(changes);
    const p = this.controller?.props, input = this.querySelector('input'), label = this.querySelector('label');
    if (!input || !p) return;
    input.id = `a2ui-${this.context.dataContext.surface.id}-${this.context.componentModel.id}`;
    input.required = p.required === true; input.maxLength = 2000; input.autocomplete = 'off';
    if (label) label.htmlFor = input.id;
    this.classList.add('a2ui-field');
  }
}
class OakChoicePicker extends A2uiLitElement {
  createRenderRoot() {return this;}
  updated(changes) {super.updated(changes);const select=this.querySelector('select');if (select) select.value=this.controller?.props.value?.[0] || '';} 
  render() {const p = this.controller?.props; if (!p) return nothing;
    const id = `a2ui-${this.context.dataContext.surface.id}-${this.context.componentModel.id}`;
    return html`<div class="a2ui-field"><label for=${id}>${p.label}</label><select id=${id} ?required=${p.required === true}
      .value=${p.value?.[0] || ''} @input=${e => p.setValue(e.target.value ? [e.target.value] : [])}>
      <option value="">Обери варіант</option>${(p.options || []).map(o => html`<option value=${o.value}>${o.label}</option>`)}</select></div>`;
  }
}
class OakButton extends A2uiLitElement {
  createRenderRoot() {return this;}
  render() {const p = this.controller?.props; return p ? html`<button type="button" class=${p.variant === 'quiet' ? 'quiet' : p.variant === 'danger' ? 'danger' : ''} @click=${() => p.action()}>${p.label}</button>` : nothing;}
}
class OakDivider extends A2uiLitElement {
  createRenderRoot() {return this;}
  render() {return html`<hr class="a2ui-divider">`;}
}
class OakBadge extends A2uiLitElement {
  createRenderRoot() {return this;}
  render() {const p=this.controller?.props;return p ? html`<span class=${'a2ui-badge a2ui-tone-' + (p.tone || 'neutral')}>${p.text ?? ''}</span>` : nothing;}
}
class OakMetric extends A2uiLitElement {
  createRenderRoot() {return this;}
  render() {const p=this.controller?.props;return p ? html`<div class="a2ui-metric"><span class="a2ui-metric-label">${p.label}</span><strong class="a2ui-metric-value">${p.value ?? ''}</strong>${p.hint ? html`<span class="hint">${p.hint}</span>` : nothing}</div>` : nothing;}
}
class OakProgress extends A2uiLitElement {
  createRenderRoot() {return this;}
  render() {const p=this.controller?.props;if (!p) return nothing;
    const total=p.segments.reduce((sum,s)=>sum+s.value,0);let offset=0;
    const description=p.segments.map(s=>`${s.label}: ${s.value}`).join(', ');
    return html`<figure class="a2ui-progress">${p.label ? html`<figcaption>${p.label}</figcaption>` : nothing}
      <svg class="a2ui-progress-bar" viewBox="0 0 100 12" preserveAspectRatio="none" role="img" aria-label=${description}>
      ${p.segments.map((s,i)=>{const x=offset,width=100*s.value/total;offset+=width;return svg`<rect class=${'a2ui-series-' + i} x=${x} y="0" width=${width} height="12"><title>${s.label}: ${s.value}</title></rect>`;})}</svg>
      <div class="a2ui-legend">${p.segments.map((s,i)=>html`<span><i class=${'a2ui-series-' + i}></i>${s.label}: ${s.value}</span>`)}</div></figure>`;
  }
}
class OakTable extends A2uiLitElement {
  createRenderRoot() {return this;}
  render() {const p=this.controller?.props;return p ? html`<div class="a2ui-table-scroll" tabindex="0" role="region" aria-label=${p.caption || 'Таблиця'}><table class="a2ui-table">${p.caption ? html`<caption>${p.caption}</caption>` : nothing}<thead><tr>${p.columns.map(c=>html`<th scope="col">${c}</th>`)}</tr></thead><tbody>${p.rows.map(row=>html`<tr>${row.map(cell=>html`<td>${cell}</td>`)}</tr>`)}</tbody></table></div>` : nothing;}
}
class OakChart extends A2uiLitElement {
  createRenderRoot() {return this;}
  render() {const p=this.controller?.props;if (!p) return nothing;
    const values=p.series.flatMap(s=>s.values),low=Math.min(0,...values),high=Math.max(0,...values),range=high-low || 1;
    const left=40,top=12,width=268,height=126,step=width/p.labels.length;
    const y=value=>top+(high-value)*height/range,zero=y(0),x=i=>left+step*(i+.5);
    const description=p.series.map(s=>s.label+': '+s.values.map((v,i)=>`${p.labels[i]} ${v}${p.unit ? ' '+p.unit : ''}`).join(', ')).join('; ');
    return html`<figure class="a2ui-chart">${p.title ? html`<figcaption>${p.title}</figcaption>` : nothing}<svg viewBox="0 0 320 180" role="img" aria-label=${description}>
      <line class="a2ui-chart-axis" x1=${left} x2="308" y1=${zero} y2=${zero}></line>
      <text class="a2ui-chart-label" x="36" y=${top+4} text-anchor="end">${high}</text>
      <text class="a2ui-chart-label" x="36" y=${top+height} text-anchor="end">${low}</text>
      ${p.labels.map((label,i)=>svg`<text class="a2ui-chart-label" x=${x(i)} y="158" text-anchor="middle">${label}</text>`)}
      ${p.series.map((series,si)=>p.kind === 'line' ? svg`<polyline class=${'a2ui-chart-line a2ui-series-' + si} points=${series.values.map((v,i)=>`${x(i)},${y(v)}`).join(' ')}></polyline>${series.values.map((v,i)=>svg`<circle class=${'a2ui-series-' + si} cx=${x(i)} cy=${y(v)} r="3"><title>${series.label} · ${p.labels[i]}: ${v} ${p.unit || ''}</title></circle>`)}` : series.values.map((v,i)=>{
        const bar=step*.8/p.series.length;return svg`<rect class=${'a2ui-series-' + si} x=${left+step*i+step*.1+bar*si} y=${Math.min(zero,y(v))} width=${Math.max(.5,bar-1)} height=${Math.abs(y(v)-zero)}><title>${series.label} · ${p.labels[i]}: ${v} ${p.unit || ''}</title></rect>`;
      }))}</svg><div class="a2ui-legend">${p.series.map((s,i)=>html`<span><i class=${'a2ui-series-' + i}></i>${s.label}</span>`)}</div></figure>`;
  }
}

// Only active image elements own URLs. Removing the last owner aborts its fetch
// and revokes the URL; a fetch which ignores abort still cannot publish late.
function createImageResources(artifact) {
  const entries=new Map(),owners=new Map();let disposed=false;
  function release(owner) {
    const entry=owners.get(owner);if (!entry) return;owners.delete(owner);entry.users.delete(owner);
    if (!entry.users.size) {entry.controller.abort();if (entry.url) URL.revokeObjectURL(entry.url);entries.delete(entry.id);}
  }
  return {
    acquire(owner,id) {
      release(owner);if (disposed) return;
      let entry=entries.get(id);
      if (!entry) {
        entry={id,controller:new AbortController(),users:new Set(),url:null,failed:false};entries.set(id,entry);
        Promise.resolve().then(()=>{if (typeof artifact !== 'function') throw new Error('No artifact loader');return artifact(id,{signal:entry.controller.signal});}).then(blob=>{
          if (disposed || entry.controller.signal.aborted || entries.get(id)!==entry || !entry.users.size) return;
          if (!(blob instanceof Blob) || blob.size > 10*1024*1024 || !['image/png','image/jpeg','image/webp','image/gif'].includes(blob.type)) throw new Error('Unsupported image');
          entry.url=URL.createObjectURL(blob);for (const user of entry.users) user.imageReady(id,entry.url);
        }).catch(()=>{if (disposed || entries.get(id)!==entry) return;entry.failed=true;for (const user of entry.users) user.imageReady(id,null);});
      }
      entry.users.add(owner);owners.set(owner,entry);
      if (entry.url || entry.failed) owner.imageReady(id,entry.url);
    },
    release,
    dispose() {disposed=true;for (const owner of [...owners.keys()]) release(owner);},
  };
}
class OakImage extends A2uiLitElement {
  createRenderRoot() {return this;}
  updated(changes) {
    super.updated(changes);const id=this.controller?.props?.artifact,manager=this.closest('oak-a2ui-surface')?._oakImages;
    if (id===this._artifact && manager===this._manager) return;
    this._manager?.release(this);this._artifact=id;this._manager=manager;this._url=null;this._failed=false;
    if (id && manager) manager.acquire(this,id);this.requestUpdate();
  }
  imageReady(id,url) {if (id!==this._artifact || !this.isConnected) return;this._url=url;this._failed=!url;this.requestUpdate();}
  disconnectedCallback() {this._manager?.release(this);this._manager=null;this._artifact=null;super.disconnectedCallback();}
  render() {const p=this.controller?.props;if (!p) return nothing;
    return this._url ? html`<img class="a2ui-image" src=${this._url} alt=${p.alt} loading="lazy" decoding="async" @error=${()=>{this._url=null;this._failed=true;this.requestUpdate();}}>` : html`<p class="hint" role="status">${this._failed ? 'Зображення недоступне' : 'Завантаження зображення'}${p.alt ? ': '+p.alt : ''}</p>`;
  }
}
const custom = {Text:OakText,TextField:OakTextField,ChoicePicker:OakChoicePicker,Button:OakButton,Divider:OakDivider,Badge:OakBadge,Metric:OakMetric,Progress:OakProgress,Table:OakTable,Chart:OakChart,Image:OakImage};
const basic = {Card:A2uiCard,Column:A2uiColumn,Row:A2uiRow};
// Use the canonical schemas unchanged in shape. Binder annotations supply the
// protocol behavior lost when narrow path-only schemas are loaded from JSON.
// The never branch cannot add another accepted value or function call.
const schemaCatalog = Catalog.fromSchema(rawCatalog, '0.9.1');
const entries = [...schemaCatalog.components.values()].map(rawApi => {
  let schema = rawApi.schema.omit({accessibility:true});
  if (rawApi.name === 'Card') schema = schema.extend({child:schema.shape.child.describe('REF:#/$defs/ComponentId')});
  if (['Column','Row'].includes(rawApi.name)) schema = schema.extend({children:schema.shape.children.describe('REF:#/$defs/ChildList')});
  if (['Text','Badge'].includes(rawApi.name)) schema = schema.extend({text:schema.shape.text.describe('REF:#/$defs/DynamicString')});
  if (rawApi.name === 'Metric') schema = schema.extend({value:schema.shape.value.describe('REF:#/$defs/DynamicString')});
  if (['TextField','ChoicePicker'].includes(rawApi.name)) schema = schema.extend({value:z.union([schema.shape.value,z.never()])});
  if (rawApi.name === 'Button') schema = schema.extend({action:schema.shape.action.describe('REF:#/$defs/Action')});
  const api = {...rawApi,schema};
  const cls = custom[api.name];
  if (!cls) return {...basic[api.name], schema:api.schema};
  const tagName = 'oak-a2ui-' + api.name.toLowerCase();
  customElements.define(tagName, cls);
  return {...api,tagName,element:cls};
});
export const catalog = new Catalog(rawCatalog.catalogId, '0.9.1', entries, [], schemaCatalog.themeSchema);
const VERSION = 'v0.9.1';
const validationConfig = {allowOrphanComponents:true, allowDanglingReferences:true,
  allowMissingRoot:true, allowUnknownElements:false, maxDepth:16,
  targetVersion:VERSION, allowedMessages:['createSurface','updateComponents','updateDataModel','deleteSurface']};

// Host metadata and transport remain Oak's. The official processor owns every
// component, data-model binding, action context and renderer subscription.
export function createCanonicalRenderer(onAction, {artifact} = {}) {
  const processor = new MessageProcessor([catalog], onAction, {validationConfig});
  const hosts = new Map(),images=createImageResources(artifact);
  const message = (operation, surfaceId, body = {}) => ({version:VERSION, [operation]:{surfaceId,...body}});
  function disable(host, disabled) {
    host.inert = disabled;
    host.setAttribute('aria-disabled', String(disabled));
    for (const control of host.querySelectorAll('input,select,button')) control.disabled = disabled;
  }
  return {
    update(incoming) {
      const components = Object.values(incoming.components);
      if (components.length > 64 || new TextEncoder().encode(JSON.stringify({components:incoming.components,dataModel:incoming.dataModel})).length > 65536) throw new Error('Unsupported surface limits');
      for (const component of components) {
        if (!component || !/^[A-Za-z0-9_-]{1,64}$/.test(component.id) || incoming.components[component.id] !== component) throw new Error('Invalid component identity');
        const api = catalog.components.get(component.component);
        if (!api) throw new Error('Unsupported component');
        const {id, component:kind, ...props} = component;
        if (!api.schema.safeParse(props).success) throw new Error('Invalid catalogue properties');
        if (kind==='Image' && (props.path !== undefined || !/^[a-f0-9]{32}$/.test(props.artifact || ''))) throw new Error('Invalid artifact');
        if (kind==='Table' && props.rows.some(row=>row.length!==props.columns.length)) throw new Error('Invalid table');
        if (kind==='Progress' && (props.segments.some(s=>!Number.isFinite(s.value) || s.value<0 || s.value>1e15) || props.segments.reduce((sum,s)=>sum+s.value,0)<=0)) throw new Error('Invalid progress');
        if (kind==='Chart' && props.series.some(s=>s.values.length!==props.labels.length || s.values.some(v=>!Number.isFinite(v) || Math.abs(v)>1e15))) throw new Error('Invalid chart');
      }
      if (components.filter(c=>c.component==='Image').length>8) throw new Error('Too many images');
      if (!processor.getSurface(incoming.id)) processor.processMessages(message('createSurface', incoming.id, {catalogId:catalog.id,sendDataModel:false}));
      if (components.length) processor.processMessages(message('updateComponents', incoming.id, {components}));
      processor.processMessages(message('updateDataModel', incoming.id, {value:incoming.dataModel}));
      let host = hosts.get(incoming.id);
      if (!host) {
        host = document.createElement('oak-a2ui-surface');
        host._oakImages = images;
        host.surface = processor.getSurface(incoming.id); host.id = 'a2ui-surface-' + incoming.id;
        host.tabIndex = -1; host.setAttribute('role','group'); host.setAttribute('aria-label','Картка від Oak');
        host._oakDisabled = true;
        host.addEventListener('click', event => {if (host._oakDisabled) {event.preventDefault();event.stopImmediatePropagation();}}, true);
        host._oakObserver = new MutationObserver(() => disable(host, host._oakDisabled));
        host._oakObserver.observe(host, {childList:true,subtree:true});
        hosts.set(incoming.id, host); disable(host,true);
      }
      return host;
    },
    disable(id, disabled) {const host=hosts.get(id);if (host) {host._oakDisabled=disabled;disable(host,disabled);}},
    inputs(id) {
      const values = {}, host = hosts.get(id);
      for (const field of host?.querySelectorAll('oak-a2ui-textfield,oak-a2ui-choicepicker') || []) {
        const path = field.context.componentModel.properties.value.path;
        values[path] = field.controller.props.value ?? (field.context.componentModel.type === 'TextField' ? '' : []);
      }
      return values;
    },
    remove(id) {
      const host=hosts.get(id); host?._oakObserver.disconnect(); host?.remove(); hosts.delete(id);
      if (processor.getSurface(id)) processor.processMessages(message('deleteSurface',id));
    },
    dispose() {for (const host of hosts.values()) {host._oakObserver.disconnect();host.remove();}hosts.clear();images.dispose();processor.dispose();},
  };
}

