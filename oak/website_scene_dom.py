"""DOM observation and single-dispatch binding for the component scene catalog.

Only sanitized descriptors leave the page. Native nodes, attributes used for
routing, and current values stay inside this transaction. Open shadow roots are
inspected; inaccessible frames are never treated as a completed login.
"""

from .website_scene import MAX_SELECT_OPTIONS


SCENE_SCRIPT = r"""async ({stateKey, expectedOrigin, operation, revision, action, values, mayConfirm,
    redactions, textRedactions, credentialValues, afterInput, plan, allowOriginApproval, maxSelectOptions}) => {
  const optionLimit=maxSelectOptions===__MAX_SELECT_OPTIONS__?__MAX_SELECT_OPTIONS__:32;
  let state = window[stateKey];
  if (!state) {
    state = {document:crypto.randomUUID(), revision:0, signature:'', disclosure:'', nodes:new WeakMap(), next:0, secrets:[]};
    Object.defineProperty(window,stateKey,{value:state});
  }
  const id = e => { if (!state.nodes.has(e)) state.nodes.set(e,++state.next); return state.nodes.get(e); };
  state.secrets = [...new Set([...state.secrets,...(redactions||[])])].slice(-128);
  const parent = e => e.assignedSlot || e.parentElement || e.getRootNode()?.host || null;
  const within = (e, scope) => {
    for(let node=e;node;node=parent(node)) if(node===scope)return true;
    return false;
  };
  const rawVisible = e => {
    if (!e || !e.isConnected || !e.getClientRects().length) return false;
    const css = e.ownerDocument.defaultView.getComputedStyle(e);
    if(css.visibility==='hidden'||css.visibility==='collapse'||css.display==='none')return false;
    for(let node=e;node;node=parent(node)) {
      if(node.inert || node.getAttribute('aria-hidden')?.toLowerCase()==='true')return false;
    }
    return true;
  };
  let modalScope = null;
  const visible = e => rawVisible(e) && (!modalScope || within(e,modalScope));
  const active = e => visible(e) && !e.disabled && !e.readOnly;
  function all(selector) {
    const result=[], roots=[document];
    for (let i=0;i<roots.length && i<32;i++) {
      const root=roots[i];
      result.push(...root.querySelectorAll(selector));
      for (const e of root.querySelectorAll('*')) if(e.shadowRoot) roots.push(e.shadowRoot);
    }
    return result;
  }
  const controls = () => all('input,textarea,select');
  function activeModal() {
    // Raw visibility must not depend on the previous inspection's modal.
    const modals=all('[role="dialog"][aria-modal="true"],[role="alertdialog"][aria-modal="true"],dialog').filter(e=>rawVisible(e) &&
      (e.getAttribute('aria-modal')==='true'||e.matches(':modal')));
    let focus=document.activeElement;
    while(focus?.shadowRoot?.activeElement)focus=focus.shadowRoot.activeElement;
    return modals.filter(e=>within(focus,e)).at(-1)||modals.at(-1)||null;
  }
  const caption = e => {
    const walker=e.ownerDocument.createTreeWalker(e,NodeFilter.SHOW_TEXT,{acceptNode:n => {
      const p=n.parentElement;
      return p && visible(p) && !p.closest('input,textarea,select,script,style') ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
    }});
    let text='',node; while((node=walker.nextNode())) text+=' '+node.textContent;
    return text;
  };
  const captionFor = e => e.getAttribute('aria-label') || [...(e.labels||[])].filter(visible).map(caption).join(' ') ||
    e.getAttribute('placeholder') || (e.getAttribute('aria-labelledby')||'').split(/\s+/).map(ref => {
      const node=e.getRootNode().getElementById?.(ref); return node ? caption(node) : '';
    }).join(' ');
  const kind = e => {
    if(e.tagName==='SELECT') return e.multiple ? null : 'select';
    if(e.tagName==='TEXTAREA') return 'text';
    if(e.type==='password') return 'password';
    if(/(?:^|\s)one-time-code(?:\s|$)/.test(e.autocomplete||'') ||
      /(?:^|[_-])(otp|totp|verification_?code|security_?code)(?:$|[_-])/i.test(e.name+'_'+e.id)) return 'otp';
    return ({text:'text',email:'email',tel:'text',number:'text',search:'text',checkbox:'checkbox'})[e.type]||null;
  };
  const formOf = e => e.form || e.closest('form');
  const isLink = e => e.tagName==='A' || e.getAttribute('role')==='link';
  const owner = e => isLink(e) ? null : formOf(e);
  const dispatchKind = e => isLink(e) ? 'link' :
    ['BUTTON','INPUT'].includes(e.tagName) && e.type==='submit' && e.form ? 'submit' : 'button';
  const scriptOnly = form => !!form && !form.hasAttribute('method') && !form.hasAttribute('action') &&
    !form.hasAttribute('target') && form.method.toLowerCase()==='get' && [...form.elements].some(e=>active(e)&&kind(e));
  const safeForm = (form, button) => {
    try {
      if(form && (new URL(form.action,location.href).origin!==expectedOrigin || form.target ||
          (form.method.toLowerCase()!=='post' && !scriptOnly(form)))) return false;
      if(form?.method.toLowerCase()==='post' && state.nativeFormsDisabled) return false;
      if(button?.hasAttribute('formaction') && new URL(button.formAction,location.href).origin!==expectedOrigin) return false;
      if(button?.getAttribute('formtarget') || (button?.getAttribute('formmethod') && button.formMethod.toLowerCase()!=='post')) return false;
      if(button?.getAttribute('target') || button?.hasAttribute('download')) return false;
      if(button?.hasAttribute('href')) {
        const url=new URL(button.getAttribute('href'),location.href);
        if(url.username || url.password || (url.origin!==expectedOrigin &&
          !(allowOriginApproval===true && url.protocol==='https:' && (!url.port || url.port==='443')))) return false;
      }
      return true;
    } catch { return false; }
  };
  let secrets=[],optionSecrets=[];
  const normalize = s => String(s||'').replace(/[\x00-\x1f\x7f]/g,' ').replace(/\s+/g,' ').trim();
  function prepareRedactions(nodes) {
    const entered=[...state.secrets,...nodes.filter(e=>!['submit','button','reset'].includes(e.type) &&
      (!['checkbox','radio'].includes(e.type)||e.hasAttribute('value'))).map(e=>e.value),
      ...nodes.filter(e=>e.tagName==='SELECT').flatMap(e=>[...e.selectedOptions].map(o=>o.label))].filter(Boolean);
    // Option captions are private human UI, never model evidence. A selected
    // enum caption is not free text: retain it unless it echoes actual text or
    // credentials. Other scene text still masks the full selected-value set.
    const optionEntered=[...(textRedactions||[]),...(credentialValues||[]),
      ...nodes.filter(e=>e.tagName!=='SELECT' &&
        !['checkbox','radio','submit','button','reset'].includes(e.type)).map(e=>e.value)].filter(Boolean);
    const variants = entries => {
      const result=[];
      for(const value of entries) {
        const normalized=normalize(value);
        result.push(value,normalized,encodeURIComponent(value));
        if(value.length>=4) {
          try { const b=btoa(unescape(encodeURIComponent(value))); result.push(b,b.replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'')); } catch {}
          result.push(JSON.stringify(value).slice(1,-1));
        }
      }
      return [...new Set(result.filter(Boolean))].sort((a,b)=>b.length-a.length);
    };
    secrets=variants(entered);
    optionSecrets=variants(optionEntered);
  }
  const clean = (value,fallback,limit=160,privateOptions=false) => {
    let text=normalize(value);
    for(const secret of privateOptions?optionSecrets:secrets) text=text.split(secret).join('•••');
    return text.replace(/[^\s@]+@[^\s@]+\.[^\s@]+/g,'•••')
      .replace(/(?:https?:\/\/|www\.)[^\s<>]+/gi,'•••')
      .replace(/\b[A-Za-z0-9_-]{24,}\b/g,'•••').slice(0,limit).replace(/[\uD800-\uDBFF]$/,'')||fallback;
  };
  function inspect() {
    modalScope=activeModal();
    // Privacy scans retain every native control, including inaccessible
    // background defaults; only the actionable scene is scoped to the modal.
    const native=controls(); prepareRedactions(native);
    const visibleControls=native.filter(e=>visible(e)&&!['hidden','submit','button','reset'].includes(e.type));
    const usable=visibleControls.filter(active);
    const supported=usable.filter(e=>kind(e) && (kind(e)!=='select' ||
      (e.options.length<=optionLimit && [...e.options].some(o=>!o.disabled))));
    const selected=supported.slice(0,32);
    const availableButtons=all('button,input[type=submit],input[type=button],[role=button],a[href],[role=link]')
      .filter(e=>visible(e)&&e.type!=='reset');
    const buttons=availableButtons.slice(0,64);
    const textNodes=[...new Set([...all('h1,h2,h3,[role=alert],[aria-live]'),
      ...native.flatMap(e=>(e.getAttribute('aria-describedby')||'').split(/\s+/).map(ref=>e.getRootNode().getElementById?.(ref)).filter(Boolean)),
      ...all('form p')])].filter(visible);
    const texts=textNodes.slice(0,24);
    const badRequired = form => form && [...form.elements].some(e=>e.required && !e.disabled && !e.readOnly && e.type!=='hidden' && !selected.includes(e));
    const body=((modalScope||document.body)?caption(modalScope||document.body):'').toLowerCase();
    const frames=all('iframe').filter(visible);
    const challenge=[...frames,...native].some(e=>visible(e)&&/captcha|recaptcha|hcaptcha|turnstile/i.test([e.name,e.id,e.src].join(' ')));
    const matchesCredential = v => (credentialValues||[]).some(secret=>secret && normalize(v).includes(normalize(secret)));
    const credentials=native.some(e=>e.type==='password'||kind(e)==='otp'||matchesCredential(e.value||''))||matchesCredential(document.body?.innerText||'');
    let stage='unsupported_layout';
    if(location.origin!==expectedOrigin) stage='provider_blocked';
    else if(state.nativeFormBlocked) stage='unsupported_layout';
    else if(/this browser or app may not be secure|automated queries|unusual traffic|temporarily blocked/.test(body)) stage='provider_blocked';
    else if(challenge) stage='unsupported_challenge';
    else if(/check your (phone|other device)|approve (the|this) (sign.?in|login)|sent a notification/.test(body)) stage='device_approval';
    else if(buttons.length) stage='form';
    else if(mayConfirm && !usable.length && !credentials && !frames.length && !all('form').some(visible)) stage='review';
    const scene={provider:'website',destination:expectedOrigin,revision:'',stage:stage==='form'?'form':'unsupported_layout',
      controls:selected.map((e,order)=>{
        const type=kind(e),limit=({text:1024,email:254,password:1024,otp:16,checkbox:1,select:64})[type];
        const item={ref:'f'+id(e),type,label:clean(captionFor(e),'Поле'),required:!!e.required,
          form:formOf(e)?'g'+id(formOf(e)):null,order,max_length:e.maxLength>0?Math.min(e.maxLength,limit):limit};
        const roles=String(e.autocomplete||'').split(/\s+/).filter(t=>t!=='webauthn');
        const role=roles.at(-1);
        const compatible={username:['text','email'],'current-password':['password'],'new-password':['password'],
          'one-time-code':['otp'],email:['email','text'],tel:['text']};
        if(compatible[role]?.includes(type)) item.role=role;
        if(type==='select') item.options=[...e.options].filter(o=>!o.disabled).map(o=>({value:'o'+id(o),label:clean(o.label,'Варіант',120,true)}));
        return item;
      }),
      actions:buttons.map((e,order)=>({ref:'a'+id(e),label:clean(e.getAttribute('aria-label')||caption(e)||(e.type==='submit'?e.value:''),'Продовжити'),
        form:owner(e)?'g'+id(owner(e)):null,order,kind:isLink(e)?'link':'button',dispatch:dispatchKind(e),enabled:!e.disabled&&e.getAttribute('aria-disabled')!=='true',
        permitted:safeForm(owner(e),e)&&!badRequired(owner(e))})),
      texts:texts.map(e=>({ref:'t'+id(e),kind:/^H[123]$/.test(e.tagName)?'heading':e.getAttribute('role')==='alert'||e.hasAttribute('aria-live')?'error':'help',text:clean(caption(e),'Сторінка',240)})),
      after_input:!!afterInput,overflow:{controls:Math.max(0,visibleControls.length-selected.length),
        actions:Math.max(0,availableButtons.length-buttons.length),texts:Math.max(0,textNodes.length-texts.length)}};
    // Surface membership is native evidence, not model-chosen omissions. The
    // active dialog is authoritative; otherwise retain bounded overlapping
    // form/semantic/ancestor scopes plus the whole observed document.
    const candidates=[], candidateNodes=new Set();
    const addSurface = (node,kind) => {
      if(candidates.length>=15&&!['dialog','document'].includes(kind))return;
      if(!node||candidateNodes.has(node)||!rawVisible(node))return;
      const inside=(nodes,prefix)=>nodes.filter(e=>within(e,node)).map(e=>prefix+id(e));
      const member={ref:'s'+id(node),kind,controls:inside(selected,'f'),actions:inside(buttons,'a'),texts:inside(texts,'t')};
      if(!member.controls.length&&!member.actions.length)return;
      candidateNodes.add(node);candidates.push(member);
    };
    const surfaceKind = node => node.tagName==='FORM'?'form':
      node.matches('main,section,article,[role="region"],[role="main"]')?'region':'container';
    if(modalScope) addSurface(modalScope,'dialog');
    else {
      for(const field of selected) {
        let node=parent(field);
        for(let depth=0;node&&depth<4;depth++,node=parent(node)) {
          if(node!==document.body&&node!==document.documentElement)addSurface(node,surfaceKind(node));
        }
      }
      for(const node of all('form,main,section,article,[role="region"],[role="main"]'))addSurface(node,surfaceKind(node));
      if(!selected.length)for(const button of buttons) {
        let node=parent(button);
        for(let depth=0;node&&depth<3;depth++,node=parent(node)) {
          if(node!==document.body&&node!==document.documentElement)addSurface(node,surfaceKind(node));
        }
      }
      candidates.splice(15);
      addSurface(document.body||document.documentElement,'document');
    }
    scene.surfaces=candidates.slice(0,16);
    // Native source identity, ownership and captions bind the pending action.
    // Input values and button disabled state can change because of our fill.
    const signature=JSON.stringify([location.href,stage,modalScope?id(modalScope):null,scene.surfaces,[...native,...buttons,...texts,...all('form')].map(e=>
      [id(e),e.tagName,e.type,visible(e),buttons.includes(e)?null:!!e.disabled,!!e.readOnly,!!e.required,e.maxLength,e.multiple,
       formOf(e)?id(formOf(e)):null,e.autocomplete,e.getAttribute('name'),e.getAttribute('id'),
       e.getAttribute('action'),e.getAttribute('method'),e.getAttribute('target'),e.getAttribute('formaction'),
       e.getAttribute('formmethod'),e.getAttribute('formtarget'),e.getAttribute('href'),e.hasAttribute('download'),
       native.includes(e)?captionFor(e):caption(e),e.type==='hidden'?e.value:null,
       e.tagName==='SELECT'?[...e.options].map(o=>[id(o),o.value,o.label,o.disabled]):null])]);
    const disclosure=JSON.stringify(scene);
    if(signature!==state.signature || (operation!=='submit'&&disclosure!==state.disclosure)) state.revision++;
    state.signature=signature;state.disclosure=disclosure;
    scene.revision=state.document+':'+state.revision;
    return {scene,stage,revision:scene.revision,selected,buttons,signature,
      confirmable:!!mayConfirm&&!credentials&&!frames.length&&(stage==='form'||stage==='review')};
  }
  const initial=inspect();
  if(operation==='snapshot') return {stage:initial.stage,revision:initial.revision,origin:location.origin,
    fields:[],actions:[],groups:[],confirmable:initial.confirmable,scene:initial.scene};
  let dispatched=false;
  const reject=()=>({accepted:false,code:dispatched?'submit_uncertain':'stale_form'});
  if(!['submit','private_redactions'].includes(operation)||initial.stage!=='form'||revision!==initial.revision||!plan||plan.revision!==revision||
    !Array.isArray(plan.actions)||!Array.isArray(plan.fields)||!values||typeof values!=='object') return reject();
  const surfaceMode=Object.hasOwn(plan,'surface_ref');
  const actionPlan=plan.actions.find(a=>a.ref===action);
  if(!actionPlan)return reject();
  const refs=surfaceMode?actionPlan.field_refs:plan.fields.map(f=>f.ref);
  if(!Array.isArray(refs)||new Set(refs).size!==refs.length||
      refs.some(ref=>!plan.fields.some(f=>f.ref===ref))||Object.keys(values).sort().join(',')!==[...refs].sort().join(','))return reject();
  const selectedFields=surfaceMode?plan.fields.filter(f=>refs.includes(f.ref)):plan.fields;
  if(surfaceMode) {
    const surface=initial.scene.surfaces.find(s=>s.ref===plan.surface_ref);
    const exact=(expected,actual)=>expected.length===actual.length&&new Set(actual).size===actual.length&&expected.every(ref=>actual.includes(ref));
    if(!surface||!exact(surface.controls,plan.fields.map(f=>f.ref))||!exact(surface.actions,plan.actions.map(a=>a.ref)))return reject();
  }
  const anchor=initial.buttons.find(e=>'a'+id(e)===plan.anchor);
  const selectedButton=initial.buttons.find(e=>'a'+id(e)===action);
  const actionMetadata=initial.scene.actions.find(a=>a.ref===action);
  if(!anchor||!selectedButton||(selectedFields.length&&isLink(selectedButton))||
      (!surfaceMode&&owner(anchor)!==owner(selectedButton))||!safeForm(owner(selectedButton),selectedButton)||
      (surfaceMode&&!actionMetadata?.permitted)) return reject();
  const fieldOwner=owner(surfaceMode?selectedButton:anchor);
  if(surfaceMode&&(dispatchKind(selectedButton)==='submit'||selectedFields.length)&&fieldOwner&&
      [...fieldOwner.elements].some(e=>e.required&&!e.disabled&&!e.readOnly&&e.type!=='hidden'&&
        !selectedFields.some(f=>'f'+id(e)===f.ref)))return reject();
  if(surfaceMode&&dispatchKind(selectedButton)==='submit'&&
      initial.selected.some(e=>formOf(e)===fieldOwner&&
        !selectedFields.some(f=>'f'+id(e)===f.ref)))return reject();
  for(const field of selectedFields) {
    const e=initial.selected.find(e=>'f'+id(e)===field.ref);
    if(!e||formOf(e)!==fieldOwner||isLink(surfaceMode?selectedButton:anchor)) return reject();
  }
  if(operation==='private_redactions') return {redactions:selectedFields.flatMap(field=>{
    const e=initial.selected.find(e=>'f'+id(e)===field.ref);
    if(kind(e)==='checkbox') return values[field.ref]===true&&e.hasAttribute('value')&&e.value?[e.value]:[];
    if(kind(e)!=='select')return [];
    const option=[...e.options].find(o=>'o'+id(o)===values[field.ref]);
    return option?[option.value,option.label].filter(Boolean):[];
  })};
  let guarded=false;
  if(scriptOnly(owner(selectedButton))&&!state.nativeFormsDisabled) {
    if(!document.head) return reject();
    document.addEventListener('securitypolicyviolation',event=>{if(event.effectiveDirective==='form-action'&&event.disposition==='enforce')state.nativeFormBlocked=true;});
    const policy=document.createElement('meta');policy.httpEquiv='Content-Security-Policy';policy.content="form-action 'none'";
    document.head.append(policy);
    if(!policy.isConnected||policy.parentNode!==document.head||policy.content!=="form-action 'none'") return reject();
    guarded=true;
  }
  try {
    for(const field of selectedFields) {
      const current=inspect();
      if(current.signature!==initial.signature||current.revision!==revision||location.origin!==expectedOrigin) return reject();
      const e=current.selected.find(e=>'f'+id(e)===field.ref);
      if(!e||!safeForm(formOf(e))||formOf(e)!==fieldOwner) return reject();
      const value=values[field.ref];
      if(kind(e)==='select') {
        const option=[...e.options].find(o=>'o'+id(o)===value&&!o.disabled);if(!option)return reject();
        dispatched=true;e.selectedIndex=option.index;
      } else if(kind(e)==='checkbox') {
        dispatched=true;Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'checked').set.call(e,value);
      } else {
        dispatched=true;Object.getOwnPropertyDescriptor(e.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype,'value').set.call(e,value);
      }
      e.dispatchEvent(new Event('input',{bubbles:true,composed:true}));e.dispatchEvent(new Event('change',{bubbles:true,composed:true}));
      await new Promise(resolve=>requestAnimationFrame(resolve));
    }
    const current=inspect(),button=current.buttons.find(e=>'a'+id(e)===action);
    if(current.signature!==initial.signature||current.revision!==revision||location.origin!==expectedOrigin||!button||
      button.disabled||button.getAttribute('aria-disabled')==='true'||!safeForm(owner(button),button)) return reject();
    state.signature='';state.revision++;dispatched=true;button.click();return {accepted:true};
  } finally { if(guarded)state.nativeFormsDisabled=true; }
}""".replace('__MAX_SELECT_OPTIONS__', str(MAX_SELECT_OPTIONS))
