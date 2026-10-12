"""Offline human-plane UI checks; no provider, browser profile or live service.

Run with a local Node executable on PATH, or set OAK_TEST_NODE explicitly.
The tiny DOM only supplies browser interfaces exercised by the login module.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
NODE = os.environ.get('OAK_TEST_NODE') or shutil.which('node')
HARNESS = r'''
const assert = require('node:assert/strict');
const vm = require('node:vm');
class Element {
  constructor(tag = 'div') {
    this.tagName = tag; this.children = []; this.listeners = new Map(); this.attributes = {};
    this.value = ''; this.textContent = ''; this.hidden = false; this.disabled = false;
  }
  addEventListener(name, handler) { this.listeners.set(name, handler); }
  setAttribute(name, value) { this.attributes[name] = value; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  querySelectorAll(tag) {
    return this.children.flatMap(child => [ ...(child.tagName === tag ? [child] : []), ...child.querySelectorAll(tag)]);
  }
  focus() { this.focused = true; }
  reportValidity() { return !this.required || Boolean(this.value); }
}
const pause = async () => { for (let i = 0; i < 8; i++) await new Promise(setImmediate); };
async function createUI({layout = false} = {}) {
  const ids = Object.fromEntries(['title', 'destination', 'current-origin', 'status', 'error', 'login', 'fields', 'actions', 'cancel', 'close', 'complete', 'complete-note', 'origin-approval', 'approval-origin', 'approve-origin'].map(id => [id, new Element()]));
  const listeners = new Map(), requests = [], responses = [], messages = [], timers = new Map();
  let nextTimer = 1;
  const main = new Element('main'); let mainHeight = 330, resizeCallback = null;
  main.getBoundingClientRect = () => ({height: mainHeight});
  const parent = {postMessage: (value, origin) => messages.push({value, origin})};
  const window = {parent, addEventListener: (name, handler) => listeners.set(name, handler),
    getComputedStyle: () => ({paddingTop: '12px', paddingBottom: '12px'})};
  const document = {
    getElementById: id => ids[id], createElement: tag => new Element(tag),
    createElementNS: (ns, tag) => { assert.equal(ns, 'http://www.w3.org/2000/svg'); return new Element(tag); },
    querySelector: selector => { if (selector === 'main') return main;
      assert.equal(selector, 'meta[name="oak-parent-origin"]'); return {content: 'https://oak.example'}; },
  };
  const context = vm.createContext({
    window, document, URL, AbortController, TextDecoder,
    ...(layout ? {ResizeObserver: class { constructor(callback) { resizeCallback = callback; }
      observe(target) { assert.equal(target, main); this.target = target; } }} : {}),
    setTimeout: (fn, ms) => { const id = nextTimer++; timers.set(id, {fn, ms}); return id; },
    clearTimeout: id => timers.delete(id),
    fetch: async (url, options) => {
      assert.ok(['/api/attach', '/api/form', '/api/submit', '/api/cancel', '/api/detach', '/api/complete', '/api/approve-origin'].includes(url));
      requests.push({url, ...options, body: JSON.parse(options.body)});
      const next = responses.shift();
      if (next instanceof Error) throw next;
      const value = typeof next === 'function' ? await next() : next;
      if (value instanceof Response) return value;
      return new Response(JSON.stringify(value ?? {state: 'cancelled'}), {headers: {'Content-Type': 'application/json'}});
    },
  });
  const controlsModule = new vm.SourceTextModule(CONTROLS_SOURCE, {context});
  const module = new vm.SourceTextModule(SOURCE, {context});
  await module.link(specifier => {
    assert.equal(specifier, '/form-controls.js');
    return controlsModule;
  });
  await module.evaluate();
  return {
    ids, requests, responses, messages, parent, listeners, timers,
    resize: height => { mainHeight = height; resizeCallback(); },
    controls: controlsModule.namespace,
    a2ui: async () => {
      const module = new vm.SourceTextModule(A2UI_SOURCE, {context});
      await module.link(specifier => {
        assert.equal(specifier, './form-controls.js');
        return controlsModule;
      });
      await module.evaluate();
      return module.namespace;
    },
    input: name => [...ids.fields.querySelectorAll('input'), ...ids.fields.querySelectorAll('select')].find(child => child.name === name),
    message: (data, origin = 'https://oak.example', source = parent) => listeners.get('message')({data, origin, source}),
    attach: async (form, provider = 'google', destination = 'https://example.test', extra = {}) => {
      responses.push({lease: 'L'.repeat(43), provider, ...(provider === 'website' ? {destination} : {}), ...extra}, form);
      await listeners.get('message')({data: {type: 'oak-login-attach', request_id: 'a'.repeat(32), ticket: 'T'.repeat(43)}, origin: 'https://oak.example', source: parent});
      await pause();
    },
    submit: action => ids.login.listeners.get('submit')({preventDefault() {}, ...(action ? {submitter: {value: action}} : {})}),
    poll: async value => {
      responses.push(value);
      const entry = [...timers.entries()].find(([, timer]) => timer.ms === 1500);
      assert.ok(entry, 'poll remains scheduled'); timers.delete(entry[0]); await entry[1].fn(); await pause();
    },
  };
}
function snapshot(stage = 'password', revision = 'page:1', provider = 'google') {
  const definitions = {
    identifier: {name: 'identifier', type: 'email', label: 'Електронна пошта', max_length: 254},
    password: {name: 'password', type: 'password', label: 'Пароль', max_length: 1024},
    otp: {name: 'otp', type: 'text', label: 'Код', max_length: 16},
  };
  const names = stage === 'login' ? ['identifier', 'password'] : definitions[stage] ? [stage] : [];
  return {state: 'active', form: {provider, stage, revision, fields: names.map(name => ({...definitions[name]})),
    actions: names.length ? [{id: 'submit', label: 'Продовжити'}] : [], message: ''}};
}
function website(fields = [], actions = [{id: 'a1', label: 'Продовжити'}], stage = 'form', revision = 'page:1') {
  return {state: 'active', form: {provider: 'website', destination: 'https://example.test', stage, revision, fields, actions, message: ''}};
}
function composed(provider = 'google', revision = 'page:1') {
  const value = website([
    {name:'f1',type:'email',label:'Email',max_length:254},
    {name:'f2',type:'password',label:'Password',max_length:1024},
    {name:'f3',type:'otp',label:'Code',max_length:16},
    {name:'f4',type:'checkbox',label:'Remember',max_length:1},
    {name:'f5',type:'text',label:'Optional',max_length:1024},
  ], [{id:'a1',label:'Back'},{id:'a2',label:'Continue'}], 'form', revision);
  Object.assign(value.form, {provider, form_schema:'website-v1',
    destination: provider === 'google' ? 'https://accounts.google.com' : 'https://www.instagram.com'});
  return value;
}
'''


@unittest.skipUnless(NODE, 'Node is required for offline login JavaScript checks')
class LoginUITests(unittest.TestCase):
    def run_ui(self, scenario):
        source = (ROOT / 'oak/login_web/login.js').read_text()
        controls = (ROOT / 'oak/web/form-controls.js').read_text()
        a2ui = (ROOT / 'oak/web/a2ui.js').read_text()
        script = ('const SOURCE = ' + json.dumps(source) + ';\n'
                  + 'const CONTROLS_SOURCE = ' + json.dumps(controls) + ';\n'
                  + 'const A2UI_SOURCE = ' + json.dumps(a2ui) + ';\n' + HARNESS)
        script += '\n(async () => {\n' + scenario + '\n})().catch(error => { console.error(error); process.exitCode = 1; });'
        result = subprocess.run([NODE, '--experimental-vm-modules', '-'], input=script, text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_password_reveal_is_local_preserves_value_and_masks_when_cleared(self):
        self.run_ui(r'''
const ui = await createUI(); await ui.attach(snapshot());
const input = ui.input('password'); input.value = 'private-reveal-canary';
const toggle = ui.ids.fields.querySelectorAll('button')[0];
assert.equal(toggle.type,'button'); assert.equal(toggle.attributes['aria-controls'],input.id);
const requests = ui.requests.length;
await toggle.listeners.get('click')();
assert.equal(input.type,'text'); assert.equal(input.value,'private-reveal-canary');
assert.equal(toggle.attributes['aria-pressed'],'true');
assert.equal(toggle.attributes['aria-label'],'Приховати пароль');
assert.equal(ui.requests.length,requests); assert.equal(ui.messages.length,1);
await ui.poll(snapshot());
assert.equal(input.value,'private-reveal-canary'); assert.equal(input.type,'text');
ui.responses.push({accepted:true},snapshot('otp','next-page:1')); await ui.submit();
assert.equal(input.value,''); assert.equal(input.type,'password');
assert.equal(toggle.attributes['aria-pressed'],'false');
assert.equal(ui.input('otp').focused,true);
assert.equal(ui.requests.filter(r=>r.url==='/api/submit').length,1);
assert.ok(!JSON.stringify(ui.messages).includes('private-reveal-canary'));
''')

    def test_close_cancels_owned_lease_and_never_posts_drafts(self):
        self.run_ui(r'''
const ui = await createUI(); await ui.attach(snapshot());
const field = ui.input('password'); field.value = 'private-close-canary';
assert.equal(ui.ids.close.hidden,false);
ui.responses.push({state:'cancelled'}); await ui.ids.close.listeners.get('click')();
assert.equal(field.value,''); assert.equal(ui.ids.close.hidden,true);
assert.equal(ui.ids.fields.children.length,0);
assert.deepEqual(ui.requests.at(-1).body,{request_id:'a'.repeat(32)});
assert.equal(ui.requests.at(-1).url,'/api/cancel');
assert.ok(!JSON.stringify(ui.requests).includes('private-close-canary'));
assert.ok(!JSON.stringify(ui.messages).includes('private-close-canary'));
''')

    def test_private_layout_reports_only_bounded_height_after_valid_attachment(self):
        self.run_ui(r'''
const ui = await createUI({layout:true});
ui.resize(300); assert.equal(ui.messages.length,1,'no layout before attachment');
await ui.attach(snapshot());
assert.deepEqual(JSON.parse(JSON.stringify(ui.messages.at(-1))),{value:{type:'oak-login-layout',height:324},origin:'https://oak.example'});
ui.input('password').value='private-layout-canary';
ui.resize(470.2);
assert.deepEqual(JSON.parse(JSON.stringify(ui.messages.at(-1).value)),{type:'oak-login-layout',height:495});
const count=ui.messages.length;
ui.resize(470.2); ui.resize(-100); ui.resize(NaN); ui.resize(Infinity);
assert.equal(ui.messages.length,count,'unchanged and invalid sizes cannot emit');
ui.resize(5000);
assert.deepEqual(JSON.parse(JSON.stringify(ui.messages.at(-1).value)),{type:'oak-login-layout',height:4096});
ui.resize(6000); ui.resize(5000);
assert.equal(ui.messages.length,count+1,'stable capped sizes cannot emit again');
assert.ok(!JSON.stringify(ui.messages).includes('private-layout-canary'));
assert.ok(ui.messages.slice(1).every(message=>Object.keys(message.value).sort().join(',')==='height,type'));
''')

    def test_shared_controls_keep_ordinary_values_and_private_transport_separate(self):
        self.run_ui(r'''
const ui = await createUI(), a2ui = await ui.a2ui();
const container = new Element();
const read = a2ui.renderRequestFields(container, {
  version: 'v0.9.1', catalogId: 'urn:oak:a2ui:canonical:v1', fields: [
    {id: 'topic', label: '<b>Plain label</b>', options: null},
    {id: 'pace', label: 'Темп', options: ['Спокійно', 'Швидко']},
  ],
});
const text = container.querySelectorAll('input')[0], select = container.querySelectorAll('select')[0];
assert.equal(read(), null);
assert.equal(container.querySelectorAll('label')[0].textContent, '<b>Plain label</b>');
assert.equal(container.querySelectorAll('label')[0].htmlFor, text.id);
assert.equal(text.maxLength, 2000);
assert.equal(select.children[0].disabled, false);
text.value = 'План'; select.value = 'Спокійно';
assert.deepEqual(JSON.parse(JSON.stringify(read())), {topic: 'План', pace: 'Спокійно'});
assert.equal(ui.requests.length, 0);
await ui.attach(snapshot());
assert.equal(ui.input('password').type, 'password');
assert.equal(ui.input('password').maxLength, 1024);
assert.equal(ui.input('password').attributes['aria-describedby'], 'status');
assert.equal(ui.messages.length, 1, 'shared widgets cannot publish values to parent');
''')

    def test_recoverable_stale_refresh_preserves_draft_without_replaying_submit(self):
        self.run_ui(r'''
const ui = await createUI(); await ui.attach(snapshot('identifier'));
ui.input('identifier').value = 'fixture@example.test';
ui.responses.push(new Response(JSON.stringify({recoverable:true,code:'stale_form',error:'Refresh'}),
  {status:409,headers:{'Content-Type':'application/json'}}), snapshot('identifier','page:2'));
await ui.submit();
assert.equal(ui.requests.filter(r=>r.url==='/api/submit').length,1);
assert.equal(ui.requests.filter(r=>r.url==='/api/cancel').length,0);
assert.equal(ui.input('identifier').value,'fixture@example.test');
assert.equal(ui.ids.login.hidden,false);
assert.equal(ui.ids.actions.querySelectorAll('button')[0].disabled,false);
assert.equal(ui.messages.length,1);
''')

    def test_navigation_pending_retains_form_clears_secrets_and_only_polls(self):
        self.run_ui(r'''
const ui = await createUI(); await ui.attach(snapshot());
ui.input('password').value = 'fixture-secret';
const pending=()=>new Response(JSON.stringify({recoverable:true,code:'navigation_pending',error:'Refresh'}),
  {status:409,headers:{'Content-Type':'application/json'}});
ui.responses.push({accepted:true},pending(),pending()); await ui.submit();
assert.equal(ui.input('password').value,'');
assert.equal(ui.ids.login.hidden,false);
assert.equal(ui.ids.cancel.disabled,false);
assert.equal(ui.ids.actions.querySelectorAll('button')[0].disabled,true);
await ui.submit();
assert.equal(ui.requests.filter(r=>r.url==='/api/submit').length,1);
assert.equal(ui.requests.filter(r=>r.url==='/api/cancel').length,0);
await ui.poll(snapshot('otp','next-document:1'));
assert.ok(ui.input('otp')); assert.equal(ui.ids.actions.querySelectorAll('button')[0].disabled,false);
assert.equal(ui.requests.filter(r=>r.url==='/api/submit').length,1);
''')

    def test_unknown_recovery_code_or_uncertain_write_fails_closed(self):
        self.run_ui(r'''
for(const payload of [{recoverable:true,code:'arbitrary',error:'Refresh'},
                      {code:'submit_uncertain',error:'Unknown outcome'}]) {
 const ui=await createUI(); await ui.attach(snapshot()); ui.input('password').value='fixture-secret';
 ui.responses.push(new Response(JSON.stringify(payload),{status:409,headers:{'Content-Type':'application/json'}}));
 await ui.submit();
 assert.equal(ui.ids.fields.children.length,0);
 assert.equal(ui.requests.filter(r=>r.url==='/api/submit').length,1);
 assert.equal(ui.requests.at(-1).url,'/api/cancel');
}
''')

    def test_composition_error_keeps_cancel_usable_without_replaying(self):
        self.run_ui(r'''
const ui=await createUI(); await ui.attach(snapshot());
ui.input('password').value='synthetic-secret';
const pending=new Response(JSON.stringify({recoverable:true,code:'composition_unavailable',error:'Unavailable'}),
  {status:409,headers:{'Content-Type':'application/json'}});
await ui.poll(pending);
assert.equal(ui.ids.cancel.hidden,false); assert.equal(ui.ids.cancel.disabled,false);
assert.equal(ui.ids.actions.querySelectorAll('button')[0].disabled,true);
assert.equal(ui.input('password').value,'');
assert.match(ui.ids.error.textContent,/Скасуй запит/);
assert.equal(ui.requests.filter(r=>r.url==='/api/submit').length,0);
assert.equal(ui.requests.filter(r=>r.url==='/api/cancel').length,0);
''')

    def test_generic_unsupported_layout_is_not_reported_as_provider_challenge(self):
        self.run_ui(r'''
const ui=await createUI(); await ui.attach(website([],[],'unsupported_layout'),'website');
assert.match(ui.ids.status.textContent,/розпізнати форму/);
assert.equal(ui.ids.error.hidden,true);
assert.equal(ui.ids.cancel.hidden,false);
assert.equal(ui.requests.filter(r=>r.url==='/api/cancel').length,0);
''')

    def test_shared_widget_update_preserves_input_and_handlers_without_state_binding(self):
        self.run_ui(r'''
const ui = await createUI();
const widgets = ui.controls.createFormControl({id: 'ordinary', label: 'Before', type: 'text'});
const handler = () => {}; widgets.control.addEventListener('input', handler);
widgets.control.value = 'local draft';
ui.controls.updateFormControl(widgets, {id: 'ordinary', label: 'After', type: 'text', required: true});
assert.equal(widgets.label.textContent, 'After');
assert.equal(widgets.control.value, 'local draft');
assert.equal(widgets.control.listeners.get('input'), handler);
assert.equal(widgets.control.required, true);
assert.equal(ui.requests.length, 0);
assert.throws(() => ui.controls.createFormControl({id: 'file', label: 'File', type: 'file'}));
''')

    def test_exact_parent_origin_source_and_ticket(self):
        self.run_ui(r'''
const ui = await createUI();
assert.equal(ui.messages.length, 1);
assert.equal(ui.messages[0].origin, 'https://oak.example');
assert.equal(ui.messages[0].value.type, 'oak-login-ready');
const valid = {type: 'oak-login-attach', request_id: 'a'.repeat(32), ticket: 'T'.repeat(43)};
await ui.message(valid, 'https://other.example');
await ui.message(valid, 'https://oak.example', {});
await ui.message({...valid, ticket: 'bad'});
await ui.message({...valid, request_id: 'bad'});
assert.equal(ui.requests.length, 0);
await ui.attach(snapshot());
await ui.message(valid);
assert.equal(ui.requests.length, 2);
assert.equal(ui.ids.title.textContent, 'Вхід у Google');
assert.equal(ui.ids.destination.textContent, 'accounts.google.com');
assert.equal(ui.requests[0].headers.Authorization, undefined);
assert.equal(ui.requests[1].headers.Authorization, 'Bearer ' + 'L'.repeat(43));
for (const request of ui.requests) {
  assert.equal(request.credentials, 'omit'); assert.equal(request.redirect, 'error');
  assert.equal(request.method, 'POST'); assert.ok(!request.url.includes('T'.repeat(43)));
}
assert.equal(ui.messages.length, 1);
''')

    def test_revision_preserves_input_then_clears_and_refocuses(self):
        self.run_ui(r'''
const ui = await createUI(); await ui.attach(snapshot());
const first = ui.input('password'); first.value = 'test-password';
await ui.poll(snapshot());
assert.equal(ui.input('password'), first); assert.equal(first.value, 'test-password');
await ui.poll(snapshot('otp', 'page:2'));
assert.equal(first.value, ''); assert.equal(ui.input('otp').value, '');
assert.equal(ui.input('otp').focused, true); assert.equal(ui.input('otp').inputMode, 'numeric');
''')

    def test_submit_clears_secrets_immediately_and_serializes_actions(self):
        self.run_ui(r'''
const ui = await createUI(); await ui.attach(snapshot('login', 'page:1', 'instagram'), 'instagram');
ui.input('identifier').value = 'example-user'; ui.input('password').value = 'test-password';
let release; ui.responses.push(() => new Promise(resolve => { release = resolve; }));
const submitting = ui.submit(); await pause();
assert.equal(ui.input('password').value, ''); assert.equal(ui.ids.cancel.disabled, true);
assert.equal(ui.ids.actions.querySelectorAll('button')[0].disabled, true);
assert.equal(ui.requests.at(-1).body.values.password, 'test-password');
assert.equal(ui.requests.at(-1).body.revision, 'page:1');
const count = ui.requests.length; await ui.submit();
assert.equal(ui.requests.length, count);
ui.responses.push(snapshot('otp', 'page:2', 'instagram')); release({accepted: true}); await submitting;
ui.input('otp').value = '123456'; ui.responses.push({accepted: true}, {state: 'authenticated'}); await ui.submit();
assert.equal(ui.ids.login.hidden, true); assert.equal(ui.ids.fields.children.length, 0);
assert.match(ui.ids.status.textContent, /Вхід підтверджено/);
assert.equal(ui.messages.length, 1);
''')

    def test_waiting_states_poll_and_never_infer_authentication(self):
        self.run_ui(r'''
const ui = await createUI(); const waiting = snapshot('device_approval');
waiting.form.message = '<a href="https://evil.example">unexpected raw page</a>';
await ui.attach(waiting);
assert.match(ui.ids.status.textContent, /на своєму пристрої/);
assert.ok(!ui.ids.status.textContent.includes('evil.example'));
assert.equal(ui.ids.login.hidden, true); assert.equal(ui.ids.cancel.hidden, false);
await ui.poll(snapshot('authenticated', 'page:2'));
assert.match(ui.ids.status.textContent, /Перевіряємо/);
assert.equal(ui.ids.cancel.hidden, false);
await ui.poll({state: 'authenticated'});
assert.match(ui.ids.status.textContent, /Вхід підтверджено/);
assert.equal(ui.ids.cancel.hidden, true);
''')

    def test_malformed_schema_closes_and_cancels_resource(self):
        self.run_ui(r'''
for (const mutate of [
  form => { form.fields[0].max_length = 1025; },
  form => { form.fields[0].name = '__proto__'; },
  form => { form.fields[0].value = 'should-never-arrive'; },
  form => { form.actions[0].id = 'open_url'; },
  form => { form.provider = 'unknown'; },
  form => { form.revision = 5; },
]) {
  const ui = await createUI(); const value = snapshot(); mutate(value.form); await ui.attach(value);
  assert.equal(ui.ids.error.hidden, false); assert.equal(ui.ids.fields.children.length, 0);
  assert.equal(ui.requests.at(-1).url, '/api/cancel');
  assert.equal(ui.requests.at(-1).keepalive, true);
  assert.deepEqual(Object.keys(ui.requests.at(-1).body), ['request_id']);
}
const ui = await createUI(); await ui.attach(snapshot()); ui.input('password').value = 'test-password';
const changed = snapshot(); changed.form.fields[0].type = 'text'; await ui.poll(changed);
assert.equal(ui.ids.error.hidden, false); assert.equal(ui.ids.fields.children.length, 0);
''')

    def test_instagram_fixed_prompt_allows_submit_without_fields(self):
        self.run_ui(r'''
const ui = await createUI(); const prompt = snapshot('login', 'page:1', 'instagram');
prompt.form.fields = []; prompt.form.message = 'Продовжимо без збереження даних входу.';
await ui.attach(prompt, 'instagram');
assert.equal(ui.ids.login.hidden, false); assert.equal(ui.ids.fields.children.length, 0);
ui.responses.push({accepted: true}, {state: 'authenticated'}); await ui.submit();
assert.deepEqual(ui.requests.find(request => request.url === '/api/submit').body.values, {});
assert.match(ui.ids.status.textContent, /Вхід підтверджено/);
''')

    def test_cancel_and_pagehide_remove_fields_without_posting_values(self):
        self.run_ui(r'''
for (const hide of [false, true]) {
  const ui = await createUI(); await ui.attach(snapshot()); const field = ui.input('password'); field.value = 'test-password';
  if (hide) ui.listeners.get('pagehide')();
  else { ui.responses.push({state: 'cancelled'}); await ui.ids.cancel.listeners.get('click')(); }
  await pause();
  assert.equal(field.value, ''); assert.equal(ui.ids.fields.children.length, 0);
  assert.equal(ui.ids.cancel.hidden, true); assert.equal(ui.messages.length, 1);
  const request = ui.requests.at(-1); assert.equal(request.url, hide ? '/api/detach' : '/api/cancel');
  assert.deepEqual(Object.keys(request.body), ['request_id']);
  assert.equal(request.headers.Authorization, 'Bearer ' + 'L'.repeat(43));
  if (hide) assert.equal(request.keepalive, true);
}
''')

    def test_late_response_after_pagehide_cannot_restore_form(self):
        self.run_ui(r'''
const ui = await createUI(); await ui.attach(snapshot());
ui.input('password').value = 'test-password';
let release; ui.responses.push(() => new Promise(resolve => { release = resolve; }));
const submitting = ui.submit(); await pause(); ui.listeners.get('pagehide')();
release({accepted: true}); await submitting;
assert.equal(ui.ids.fields.children.length, 0); assert.equal(ui.ids.login.hidden, true);
assert.equal(ui.ids.cancel.hidden, true);
''')

    def test_website_dynamic_fields_actions_and_secrets_stay_in_human_plane(self):
        self.run_ui(r'''
const fields = [
  {name: 'f0', type: 'email', label: 'Email', max_length: 254},
  {name: 'f1', type: 'password', label: 'Пароль', max_length: 1024},
  {name: 'f2', type: 'otp', label: 'Код', max_length: 16},
  {name: 'f3', type: 'checkbox', label: 'Увімкнути', max_length: 1},
  {name: 'f4', type: 'select', label: '<img src=x onerror=bad()>', max_length: 64,
    options: [{value: 'o7', label: 'Перший'}, {value: 'o8', label: 'Другий'}]},
];
const ui = await createUI(); await ui.attach(website(fields, [{id: 'a1', label: 'Назад'}, {id: 'a2', label: 'Далі'}]), 'website');
assert.equal(ui.ids.title.textContent, 'Вхід до example.test'); assert.equal(ui.ids.destination.textContent, 'https://example.test');
assert.ok(ui.ids.fields.querySelectorAll('label').some(child => child.textContent === '<img src=x onerror=bad()>'));
ui.input('f0').value = 'example@example.test'; ui.input('f1').value = 'dummy-password'; ui.input('f2').value = '123456';
ui.input('f3').checked = true; ui.input('f4').value = 'o8';
let release; ui.responses.push(() => new Promise(resolve => { release = resolve; }));
const submitting = ui.submit('a2'); await pause();
assert.equal(ui.input('f1').value, ''); assert.equal(ui.input('f2').value, '');
assert.deepEqual(ui.requests.at(-1).body.values, {f0:'example@example.test',f1:'dummy-password',f2:'123456',f3:true,f4:'o8'});
assert.equal(ui.requests.at(-1).body.action, 'a2');
ui.responses.push(website([], [{id:'confirm',label:'Підтвердити завершення'}], 'review', 'page:2'));
release({accepted:true}); await submitting;
assert.equal(ui.ids.fields.children.length, 0);
assert.ok(!ui.ids.status.textContent.includes('Вхід підтверджено'));
ui.responses.push({accepted:true}, {state:'owner_confirmed'}); await ui.submit();
assert.deepEqual(ui.requests.findLast(request => request.url === '/api/submit').body.values, {});
assert.match(ui.ids.status.textContent, /підтверджено тобою/);
assert.ok(!ui.ids.status.textContent.includes('підтверджено сервісом'));
assert.equal(ui.messages.length, 1);
''')

    def test_website_optional_values_and_explicit_confirmation(self):
        self.run_ui(r'''
const fields = [{name:'f0',type:'text',label:'Необов’язково',max_length:1024},
                {name:'f1',type:'checkbox',label:'Прапорець',max_length:1}];
const ui = await createUI(); await ui.attach(website(fields), 'website');
ui.responses.push({accepted:true}, website(fields, [{id:'a1',label:'Зберегти'},{id:'confirm',label:'Підтвердити завершення'}], 'form', 'page:2'));
await ui.submit();
assert.deepEqual(ui.requests.find(request => request.url === '/api/submit').body.values, {f0:'',f1:false});
ui.input('f0').value = 'This unsent value must not accompany confirmation';
ui.responses.push({accepted:true}, {state:'owner_confirmed'}); await ui.submit('confirm');
const confirmed = ui.requests.findLast(request => request.url === '/api/submit').body;
assert.equal(confirmed.action, 'confirm'); assert.deepEqual(confirmed.values, {});
''')

    def test_large_surface_select_renders_all_choices_and_submits_only_opaque_bound_value(self):
        self.run_ui(r'''
const ui=await createUI(),options=Array.from({length:83},(_,i)=>({value:'o'+(i+1),label:'PRIVATE_OPTION_CAPTION_'+(i+1)}));
const form=website([
 {name:'f1',type:'select',label:'Choose region',max_length:64,options},
 {name:'f2',type:'email',label:'Unrelated email',max_length:254},
], [{id:'a1',label:'Continue',field_refs:['f1'],available:true},
    {id:'a2',label:'Another method',field_refs:[],available:true}]);
Object.assign(form.form,{form_schema:'website-v2',current_origin:'https://example.test',can_complete:false});
await ui.attach(form,'website','https://example.test',{form_schema:'website-v2'});
const select=ui.input('f1');assert.equal(select.tagName,'select');assert.equal(select.children.length,84);
assert.equal(select.children.at(-1).textContent,'PRIVATE_OPTION_CAPTION_83');
select.value='o84';await ui.submit('a1');assert.equal(ui.requests.filter(r=>r.url==='/api/submit').length,0);
select.value='o83';ui.input('f2').value='PRIVATE_UNRELATED_EMAIL';
const next=JSON.parse(JSON.stringify(form));next.form.revision='page:2';
ui.responses.push({accepted:true},next);await ui.submit('a1');
const posted=ui.requests.find(r=>r.url==='/api/submit');
assert.equal(posted.body.action,'a1');assert.deepEqual(posted.body.values,{f1:'o83'});
assert.equal(select.value,'');assert.equal(ui.input('f1').children.length,84);
ui.input('f1').value='o83';ui.input('f2').value='PRIVATE_UNRELATED_EMAIL';
const waiting=website([],[],'agent_working','page:3');
Object.assign(waiting.form,{form_schema:'website-v2',current_origin:'https://example.test',can_complete:false});
ui.responses.push({accepted:true},waiting);await ui.submit('a2');
assert.deepEqual(ui.requests.filter(r=>r.url==='/api/submit').at(-1).body.values,{});
assert.ok(!JSON.stringify(ui.requests).includes('PRIVATE_OPTION_CAPTION_'));
assert.ok(!JSON.stringify(ui.requests).includes('PRIVATE_UNRELATED_EMAIL'));
assert.ok(!JSON.stringify(ui.messages).includes('PRIVATE_OPTION_CAPTION_'));
assert.equal(ui.messages.length,1);
''')

    def test_surface_select_limit_is_256_and_legacy_limit_remains_32(self):
        self.run_ui(r'''
for(const [schema,count,accepted] of [['website-v2',256,true],['website-v2',257,false],
 ['website-v1',32,true],['website-v1',33,false]]) {
 const ui=await createUI(),form=website([{name:'f1',type:'select',label:'Choose',max_length:64,
  options:Array.from({length:count},(_,i)=>({value:'o'+(i+1),label:'Option '+(i+1)}))}],
  [schema==='website-v2'?{id:'a1',label:'Continue',field_refs:['f1'],available:true}:{id:'a1',label:'Continue'}]);
 form.form.form_schema=schema;
 await ui.attach(form,'website','https://example.test',{form_schema:schema});
 assert.equal(ui.ids.error.hidden,accepted);
 if(accepted) {
  assert.equal(ui.input('f1').children.length,count+1);
  ui.input('f1').value='o'+count;
  ui.responses.push({accepted:true},{state:'authenticated'});await ui.submit('a1');
  assert.deepEqual(ui.requests.find(r=>r.url==='/api/submit').body.values,{f1:'o'+count});
 } else {
  assert.equal(ui.ids.fields.children.length,0);
  assert.equal(ui.requests.filter(r=>r.url==='/api/submit').length,0);
  assert.equal(ui.requests.at(-1).url,'/api/cancel');
 }
}
''')

    def test_website_checkbox_select_preserve_same_revision_and_clear_on_change(self):
        self.run_ui(r'''
const fields = [{name:'f0',type:'checkbox',label:'Прапорець',max_length:1},
                {name:'f1',type:'select',label:'Вибір',max_length:64,options:[{value:'o1',label:'Варіант'}]}];
const ui = await createUI(); await ui.attach(website(fields), 'website');
const checkbox = ui.input('f0'), select = ui.input('f1'); checkbox.checked = true; select.value = 'o1';
await ui.poll(website(fields)); assert.equal(checkbox.checked, true); assert.equal(select.value, 'o1');
await ui.poll(website(fields, [{id:'a1',label:'Продовжити'}], 'form', 'page:2'));
assert.equal(checkbox.checked, false); assert.equal(select.value, '');
assert.equal(ui.input('f0').checked, false);
''')

    def test_website_schema_origin_and_confirmation_guards(self):
        self.run_ui(r'''
const field = {name:'f0',type:'text',label:'Поле',max_length:1024};
for (const mutate of [
  value => { value.form.destination = 'https://other.example'; },
  value => { value.form.fields[0].name = '__proto__'; },
  value => { value.form.fields[0].type = 'file'; },
  value => { value.form.fields[0].value = 'should-not-arrive'; },
  value => { value.form.fields = Array.from({length:17}, (_,i) => ({...field,name:'f'+i})); },
  value => { value.form.actions = [{id:'https://other.example',label:'Go'}]; },
  value => { value.form.fields[0].type = 'password'; value.form.actions = [{id:'confirm',label:'Finish'}]; },
  value => { value.form.fields[0] = {...field,type:'select',max_length:64,options:[{value:'https://other.example',label:'Go'}]}; },
  value => { value.state = 'unknown'; },
]) {
  const ui = await createUI(), value = website([{...field}]); mutate(value); await ui.attach(value, 'website');
  assert.equal(ui.ids.error.hidden, false); assert.equal(ui.ids.fields.children.length, 0);
  assert.equal(ui.requests.at(-1).url, '/api/cancel');
}
for (const destination of ['http://example.test', 'https://example.test/path', 'https://user:pass@example.test', 'javascript:alert(1)']) {
  const ui = await createUI(); await ui.attach(website(), 'website', destination); assert.equal(ui.ids.error.hidden, false);
}
''')

    def test_website_server_verification_is_distinct_from_owner_confirmation(self):
        self.run_ui(r'''
const ui = await createUI(); await ui.attach(website(), 'website');
await ui.poll({state:'authenticated'});
assert.match(ui.ids.status.textContent, /підтверджено сервісом/);
assert.ok(!ui.ids.status.textContent.includes('підтверджено тобою'));
assert.equal(ui.ids.fields.children.length, 0);
assert.equal(ui.messages.length, 1);
''')

    def test_named_dynamic_schema_submits_opaque_fields_and_clears_secrets(self):
        self.run_ui(r'''
for (const provider of ['google','instagram']) {
 const ui=await createUI(), first=composed(provider), destination=first.form.destination;
 await ui.attach(first,provider,destination,{form_schema:'website-v1',destination});
 assert.equal(ui.ids.destination.textContent,new URL(destination).hostname);
 assert.equal(ui.ids.error.hidden,true);
 assert.equal(ui.ids.actions.children.length,2);
 ui.input('f1').value='fixture@example.test'; ui.input('f2').value='FIXTURE_PASSWORD';
 ui.input('f3').value='123456'; ui.input('f4').checked=true;
 const password=ui.input('f2'), otp=ui.input('f3');
 await ui.poll(composed(provider));
 assert.equal(ui.input('f2'),password); assert.equal(password.value,'FIXTURE_PASSWORD');
 let release; ui.responses.push(()=>new Promise(resolve=>{release=resolve;}));
 const submitting=ui.submit('a2'); await pause();
 assert.equal(password.value,''); assert.equal(otp.value,'');
 const request=ui.requests.at(-1);
 assert.equal(request.url,'/api/submit'); assert.equal(request.body.action,'a2');
 assert.deepEqual(request.body.values,{f1:'fixture@example.test',f2:'FIXTURE_PASSWORD',f3:'123456',f4:true,f5:''});
 assert.equal(request.credentials,'omit'); assert.equal(ui.ids.cancel.disabled,true);
 const waiting=composed(provider,'page:2');
 Object.assign(waiting.form,{stage:'device_approval',fields:[],actions:[]});
 ui.responses.push(waiting); release({accepted:true}); await submitting;
 assert.equal(ui.ids.fields.children.length,0); assert.equal(ui.ids.cancel.hidden,false);
 assert.equal(ui.requests.filter(r=>r.url==='/api/submit').length,1);
 await ui.poll({state:'authenticated'});
 assert.match(ui.ids.status.textContent,/підтверджено сервісом/);
 assert.equal(ui.messages.length,1);
 assert.ok(!JSON.stringify(ui.messages).includes('FIXTURE_PASSWORD'));
}
''')

    def test_named_dynamic_provider_prompt_has_opaque_zero_field_action(self):
        self.run_ui(r'''
const ui=await createUI(), value=composed('instagram'), destination=value.form.destination;
Object.assign(value.form,{fields:[],actions:[{id:'a0',label:'Продовжити'}]});
await ui.attach(value,'instagram',destination,{form_schema:'website-v1',destination});
assert.equal(ui.ids.login.hidden,false);
ui.responses.push({accepted:true},{state:'authenticated'}); await ui.submit();
const submitted=ui.requests.find(r=>r.url==='/api/submit');
assert.equal(submitted.body.action,'a0'); assert.deepEqual(submitted.body.values,{});
assert.match(ui.ids.status.textContent,/підтверджено сервісом/);
''')

    def test_explicit_schema_marker_cannot_change_or_disappear_during_lease(self):
        self.run_ui(r'''
for (const marker of ['provider-v1','unknown',null,{},undefined]) {
 const ui=await createUI(), first=composed(), destination=first.form.destination;
 await ui.attach(first,'google',destination,{form_schema:'website-v1',destination});
 const password=ui.input('f2'); password.value='FIXTURE_PASSWORD';
 const changed=composed('google','page:2');
 if(marker===undefined) delete changed.form.form_schema; else changed.form.form_schema=marker;
 await ui.poll(changed);
 assert.equal(password.value,''); assert.equal(ui.ids.fields.children.length,0);
 assert.equal(ui.ids.error.hidden,false); assert.equal(ui.requests.at(-1).url,'/api/cancel');
 assert.deepEqual(Object.keys(ui.requests.at(-1).body),['request_id']);
 assert.equal(ui.requests.filter(r=>r.url==='/api/submit').length,0);
}
// Generic explicit leases have the same marker binding.
const ui=await createUI(), value=website(); value.form.form_schema='website-v1';
await ui.attach(value,'website','https://example.test',{form_schema:'website-v1'});
await ui.poll(website()); assert.equal(ui.ids.error.hidden,false);
''')

    def test_attach_schema_and_named_dynamic_destination_are_fixed(self):
        self.run_ui(r'''
for (const [provider, extra] of [
 ['google',{form_schema:'unknown'}], ['google',{form_schema:null}],
 ['google',{form_schema:'website-v1'}],
 ['google',{form_schema:'website-v1',destination:'https://www.instagram.com'}],
 ['instagram',{form_schema:'website-v1',destination:'https://accounts.google.com'}],
 ['google',{form_schema:'website-v1',destination:'https://accounts.google.com/path'}],
 ['google',{form_schema:'website-v1',destination:'https://user:pass@accounts.google.com'}],
 ['website',{form_schema:'provider-v1'}],
]) {
 const ui=await createUI(); await ui.attach(composed(provider),provider,'https://example.test',extra);
 assert.equal(ui.ids.error.hidden,false); assert.equal(ui.ids.fields.children.length,0);
 assert.equal(ui.requests.filter(r=>r.url==='/api/form').length,0);
 assert.equal(ui.requests.at(-1).url,'/api/cancel');
}
''')

    def test_named_dynamic_snapshots_cannot_change_identity_or_confirm_completion(self):
        self.run_ui(r'''
for (const provider of ['google','instagram']) for (const mutate of [
 value=>{value.form.provider='website';},
 value=>{value.form.destination='https://other.example';},
 value=>{value.form.stage='review';value.form.fields=[];value.form.actions=[{id:'confirm',label:'Finish'}];},
 value=>{value.form.fields=[];value.form.actions=[{id:'confirm',label:'Finish'}];},
 value=>{value.state='owner_confirmed';},
 value=>{value.form.fields[0].name='identifier';},
 value=>{value.form.fields=Array.from({length:17},(_,i)=>({name:'f'+i,type:'text',label:'Field',max_length:1024}));},
 value=>{value.form.actions=Array.from({length:9},(_,i)=>({id:'a'+i,label:'Action'}));},
]) {
 const ui=await createUI(), first=composed(provider), destination=first.form.destination;
 await ui.attach(first,provider,destination,{form_schema:'website-v1',destination});
 const password=ui.input('f2'); password.value='FIXTURE_PASSWORD';
 const changed=composed(provider,'page:2'); mutate(changed); await ui.poll(changed);
 assert.equal(password.value,''); assert.equal(ui.ids.error.hidden,false);
 assert.equal(ui.ids.fields.children.length,0); assert.equal(ui.requests.at(-1).url,'/api/cancel');
 assert.equal(ui.messages.length,1); assert.equal(ui.requests.filter(r=>r.url==='/api/submit').length,0);
}
''')

    def test_origin_approval_shows_private_consent_and_sends_only_bound_revision_once(self):
        self.run_ui(r'''
const ui=await createUI();
const initial=website([{name:'f1',type:'password',label:'Password',max_length:1024}],
 [{id:'a1',label:'Continue',field_refs:['f1'],available:true}]);
Object.assign(initial.form,{form_schema:'website-v2',can_complete:false,current_origin:'https://example.test'});
await ui.attach(initial,'website','https://example.test',{form_schema:'website-v2'});
const old=ui.input('f1');old.value='PRIVATE_ORIGIN_PASSWORD';
const pending=website([],[],'device_approval','page:2');
Object.assign(pending.form,{form_schema:'website-v2',can_complete:false,current_origin:'https://example.test',
 origin_approval:{origin:'https://identity.example'}});
await ui.poll(pending);
assert.equal(old.value,'');assert.equal(ui.ids.login.hidden,true);
assert.equal(ui.ids['origin-approval'].hidden,false);
assert.equal(ui.ids['approval-origin'].textContent,'https://identity.example');
assert.equal(ui.ids['approve-origin'].disabled,false);
assert.equal(ui.ids.complete.hidden,true);assert.equal(ui.ids.cancel.disabled,false);
const next=JSON.parse(JSON.stringify(initial));Object.assign(next.form,{revision:'next:1',current_origin:'https://identity.example'});
ui.responses.push({approved:true},next);
await Promise.all([ui.ids['approve-origin'].listeners.get('click')(),ui.ids['approve-origin'].listeners.get('click')()]);
const posts=ui.requests.filter(r=>r.url==='/api/approve-origin');assert.equal(posts.length,1);
assert.deepEqual(posts[0].body,{request_id:'a'.repeat(32),revision:'page:2'});
assert.equal(posts[0].credentials,'omit');assert.equal(posts[0].redirect,'error');
assert.equal(ui.ids['origin-approval'].hidden,true);
assert.match(ui.ids['current-origin'].textContent,/https:\/\/identity\.example/);
assert.equal(ui.ids.destination.textContent,'https://identity.example');
assert.equal(ui.input('f1').value,'');
assert.ok(!JSON.stringify(ui.messages).includes('identity.example'));
assert.ok(!JSON.stringify(ui.requests).includes('PRIVATE_ORIGIN_PASSWORD'));
assert.equal(ui.requests.filter(r=>r.url==='/api/submit').length,0);
''')

    def test_origin_approval_rejects_malformed_or_wrong_schema_capabilities(self):
        self.run_ui(r'''
for(const mutate of [
 v=>{v.form.origin_approval.origin='http://identity.example';},
 v=>{v.form.origin_approval.origin='https://identity.example/path?code=PRIVATE_CANARY';},
 v=>{v.form.origin_approval.origin='https://user:pass@identity.example';},
 v=>{v.form.origin_approval.origin='https://identity.example:8443';},
 v=>{v.form.origin_approval.extra=true;},
 v=>{v.form.origin_approval=null;},
 v=>{v.form.can_complete=true;},
 v=>{v.form.stage='form';v.form.actions=[{id:'a1',label:'Continue',field_refs:[],available:true}];},
 v=>{v.form.fields=[{name:'f1',type:'password',label:'Password',max_length:1024}];},
 v=>{v.form.current_origin='https://identity.example/path';},
 v=>{v.form.form_schema='website-v1';},
]) {
 const ui=await createUI(),pending=website([],[],'device_approval');
 Object.assign(pending.form,{form_schema:'website-v2',can_complete:false,current_origin:'https://example.test',
  origin_approval:{origin:'https://identity.example'}});mutate(pending);
 await ui.attach(pending,'website','https://example.test',{form_schema:'website-v2'});
 assert.equal(ui.ids.error.hidden,false);assert.equal(ui.ids['origin-approval'].hidden,true);
 assert.equal(ui.requests.filter(r=>r.url==='/api/approve-origin').length,0);
 assert.equal(ui.requests.at(-1).url,'/api/cancel');
 assert.ok(!JSON.stringify(ui.messages).includes('PRIVATE_CANARY'));
}
for(const provider of ['google','instagram']) {
 const ui=await createUI(),value=snapshot('device_approval','page:1',provider);
 value.form.origin_approval={origin:'https://identity.example'};
 await ui.attach(value,provider);assert.equal(ui.ids.error.hidden,false);
 assert.equal(ui.requests.filter(r=>r.url==='/api/approve-origin').length,0);
}
''')

    def test_stale_origin_approval_waits_for_fresh_revision_without_replay(self):
        self.run_ui(r'''
const ui=await createUI(),pending=website([],[],'device_approval','pending:1');
Object.assign(pending.form,{form_schema:'website-v2',can_complete:false,current_origin:'https://example.test',
 origin_approval:{origin:'https://identity.example'}});
await ui.attach(pending,'website','https://example.test',{form_schema:'website-v2'});
ui.responses.push(new Response(JSON.stringify({recoverable:true,code:'stale_form',error:'Refresh'}),
 {status:409,headers:{'Content-Type':'application/json'}}));
await ui.ids['approve-origin'].listeners.get('click')();
assert.equal(ui.ids.error.hidden,false);assert.equal(ui.ids.cancel.disabled,false);
assert.equal(ui.ids['approve-origin'].disabled,true);
await ui.ids['approve-origin'].listeners.get('click')();
assert.equal(ui.requests.filter(r=>r.url==='/api/approve-origin').length,1);
const fresh=JSON.parse(JSON.stringify(pending));fresh.form.revision='pending:2';await ui.poll(fresh);
assert.equal(ui.ids['approve-origin'].disabled,false);
ui.responses.push({approved:true},website([],[],'agent_working','page:2'));
// The fixture response must retain the negotiated schema.
ui.responses[1].form.form_schema='website-v2';ui.responses[1].form.can_complete=false;
ui.responses[1].form.current_origin='https://example.test';
await ui.ids['approve-origin'].listeners.get('click')();
assert.deepEqual(ui.requests.filter(r=>r.url==='/api/approve-origin').map(r=>r.body.revision),['pending:1','pending:2']);
assert.equal(ui.requests.filter(r=>r.url==='/api/submit').length,0);
assert.equal(ui.requests.filter(r=>r.url==='/api/cancel').length,0);
''')

    def test_origin_change_drops_all_drafts_even_during_recoverable_refresh(self):
        self.run_ui(r'''
const ui=await createUI(),first=website([{name:'f1',type:'text',label:'Name',max_length:1024}],
 [{id:'a1',label:'Continue',field_refs:['f1'],available:true}]);
Object.assign(first.form,{form_schema:'website-v2',can_complete:false,current_origin:'https://example.test'});
await ui.attach(first,'website','https://example.test',{form_schema:'website-v2'});
const old=ui.input('f1');old.value='PRIVATE_ORIGIN_DRAFT';
await ui.poll(new Response(JSON.stringify({recoverable:true,code:'navigation_pending',error:'Refresh'}),
 {status:409,headers:{'Content-Type':'application/json'}}));
const next=JSON.parse(JSON.stringify(first));Object.assign(next.form,{revision:'page:2',current_origin:'https://identity.example'});
await ui.poll(next);assert.equal(old.value,'');assert.equal(ui.input('f1').value,'');
assert.match(ui.ids['current-origin'].textContent,/https:\/\/identity\.example/);
const changed=JSON.parse(JSON.stringify(next));changed.form.current_origin='https://third.example';await ui.poll(changed);
assert.equal(ui.ids.error.hidden,false);assert.equal(ui.ids.fields.children.length,0);
assert.ok(!JSON.stringify(ui.messages).includes('PRIVATE_ORIGIN_DRAFT'));
''')

    def test_owner_cancel_at_origin_approval_sends_no_grant_or_value(self):
        self.run_ui(r'''
const ui=await createUI(),pending=website([],[],'device_approval');
Object.assign(pending.form,{form_schema:'website-v2',can_complete:false,current_origin:'https://example.test',
 origin_approval:{origin:'https://identity.example'}});
await ui.attach(pending,'website','https://example.test',{form_schema:'website-v2'});
ui.responses.push({state:'cancelled'});await ui.ids.cancel.listeners.get('click')();
assert.equal(ui.ids['origin-approval'].hidden,true);assert.equal(ui.ids['approval-origin'].textContent,'');
assert.equal(ui.requests.filter(r=>r.url==='/api/approve-origin').length,0);
assert.deepEqual(ui.requests.at(-1).body,{request_id:'a'.repeat(32)});
assert.ok(!JSON.stringify(ui.messages).includes('identity.example'));
''')

    def test_explicit_current_origin_cannot_silently_disappear(self):
        self.run_ui(r'''
const ui=await createUI(),first=website([{name:'f1',type:'password',label:'Password',max_length:1024}],
 [{id:'a1',label:'Continue',field_refs:['f1'],available:true}]);
Object.assign(first.form,{form_schema:'website-v2',can_complete:false,current_origin:'https://identity.example'});
await ui.attach(first,'website','https://example.test',{form_schema:'website-v2'});
const old=ui.input('f1');old.value='PRIVATE_ORIGIN_PASSWORD';
const missing=JSON.parse(JSON.stringify(first));missing.form.revision='page:2';delete missing.form.current_origin;
await ui.poll(missing);assert.equal(old.value,'');assert.equal(ui.ids.fields.children.length,0);
assert.equal(ui.ids.error.hidden,false);assert.equal(ui.requests.at(-1).url,'/api/cancel');
assert.ok(!JSON.stringify(ui.messages).includes('PRIVATE_ORIGIN_PASSWORD'));
''')

    def test_provider_v1_and_unmarked_legacy_leases_keep_original_schema(self):
        self.run_ui(r'''
for (const provider of ['google','instagram']) {
 const ui=await createUI(), first=snapshot('password','page:1',provider);
 first.form.form_schema='provider-v1';
 await ui.attach(first,provider,'https://example.test',{form_schema:'provider-v1'});
 assert.equal(ui.ids.error.hidden,true); assert.ok(ui.input('password'));
 const missing=snapshot('password','page:2',provider); await ui.poll(missing);
 assert.equal(ui.ids.error.hidden,false);
 const legacy=await createUI(); await legacy.attach(snapshot('password','page:1',provider),provider);
 assert.equal(legacy.ids.error.hidden,true); legacy.input('password').value=''; await legacy.submit();
 assert.equal(legacy.requests.filter(r=>r.url==='/api/submit').length,0);
 await legacy.poll(composed(provider,'page:2'));
 assert.equal(legacy.ids.error.hidden,false); assert.equal(legacy.ids.fields.children.length,0);
}
''')


if __name__ == '__main__':
    unittest.main()
