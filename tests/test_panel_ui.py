"""Offline panel lifecycle checks using the repository's Node/vm test pattern."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
NODE = os.environ.get('OAK_TEST_NODE') or shutil.which('node')


@unittest.skipUnless(NODE, 'Node is required for panel JavaScript checks')
class PanelUITests(unittest.TestCase):
    def run_js(self, scenario):
        source = (ROOT / 'oak/web/panel-poll.js').read_text()
        script = r'''
const assert = require('node:assert/strict'), vm = require('node:vm');
const timers = new Map(); let next=1, owner=1, visible=true, reads=[], applied=[], errors=[];
const context=vm.createContext({AbortController, setTimeout:(fn,ms)=>{const id=next++;timers.set(id,{fn,ms});return id;},clearTimeout:id=>timers.delete(id)});
const mod = new vm.SourceTextModule(SOURCE,{context}); await mod.link(()=>{}); await mod.evaluate();
const panel=mod.namespace.createPanelPoll({epoch:()=>owner,available:()=>visible,
read:signal=>new Promise((resolve,reject)=>reads.push({signal,resolve,reject})),apply:value=>applied.push(value),failed:error=>errors.push(error)});
const flush=async()=>{for(let i=0;i<4;i++)await new Promise(setImmediate);};
const tick=async()=>{assert.equal(timers.size,1);const [id,timer]=[...timers][0];timers.delete(id);timer.fn();await flush();};
'''
        process = subprocess.run([NODE, '--experimental-vm-modules', '-e', 'const SOURCE=' + json.dumps(source) + ';(async()=>{' + script + scenario + '})().catch(e=>{console.error(e);process.exit(1)});'], capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stderr)

    def test_live_snapshot_preserves_drafts_and_existing_request_controls(self):
        app = (ROOT / 'oak/web/app.js').read_text()
        render = app[app.index('function renderPanel('):app.index('function resetMemory(')]
        script = r"""
const assert=require('node:assert/strict');
const nodes=new Map();function $(id){if(!nodes.has(id))nodes.set(id,{dataset:{},value:'typed draft',replaced:0,replaceChildren(){this.replaced++;},removeAttribute(){}});return nodes.get(id);}
let panelData=null,authenticated=false,timezone='',stopRequestState='',telegramLaunch=false;
const window={};let modelRenders=0;
const renderSelectedSession=()=>{},count=String,setAttention=()=>{},safeHTTPS=()=>null;
const renderApprovals=()=>{};
const renderModels=()=>{modelRenders++;},renderComputer=()=>{},message=()=>{},updateActions=()=>{};
""" + render + r"""
const snapshot={session:{active:true,input_requests:[],input_request_attention:[],awaiting_confirmation:0,uncertain_inputs:0},settings:{timezone:'Europe/Kyiv'},memory:{count:1}};
$('pending-input-requests').dataset.requests=JSON.stringify([[],[]]);
renderPanel(snapshot,true);renderPanel({...snapshot,session:{...snapshot.session,active:false}},true);
assert.equal($('session-state').textContent,'Розмову ще не розпочато');
assert.equal($('pending-input-requests').replaced,0);
assert.equal($('settings-list').replaced,0);assert.equal(modelRenders,0);
assert.equal($('model-select').value,'typed draft');
"""
        process = subprocess.run([NODE, '-e', script], capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stderr)

    def test_telegram_theme_rejects_unsafe_or_unreadable_hints_and_cleans_up(self):
        source = (ROOT / 'oak/web/telegram-theme.js').read_text()
        scenario = r"""
const assert=require('node:assert/strict'),vm=require('node:vm');
const mod=new vm.SourceTextModule(SOURCE);await mod.link(()=>{});await mod.evaluate();
const values=new Map(),listeners=new Map(),root={dataset:{},style:{setProperty:(k,v)=>values.set(k,v)}};
let app={colorScheme:'dark',themeParams:{bg_color:'url(secret)',text_color:'#050a08',hint_color:'#050a08'},onEvent:(k,v)=>listeners.set(k,v),offEvent:k=>listeners.delete(k)};
const theme=mod.namespace.createTelegramTheme({app:()=>app,root});theme.connect();theme.connect();
assert.equal(root.dataset.theme,'dark');assert.equal(values.get('--bg'),'#050a08');assert.equal(values.get('--ink'),'#ecf5f0');assert.equal(listeners.size,1);
app.colorScheme='light';app.themeParams={bg_color:'#ffffff',secondary_bg_color:'#f8f8f8',text_color:'#112211',hint_color:'#ffffff'};listeners.get('themeChanged')();
assert.equal(root.dataset.theme,'light');assert.equal(values.get('--bg'),'#ffffff');assert.equal(values.get('--ink'),'#112211');assert.equal(values.get('--muted'),'#425c4c');
theme.destroy();assert.equal(listeners.size,0);app=null;theme.connect();assert.equal(root.dataset.theme,'dark');
"""
        result = subprocess.run([NODE, '--experimental-vm-modules', '-e', 'const SOURCE=' + json.dumps(source) + ';(async()=>{' + scenario + '})().catch(e=>{console.error(e);process.exit(1)});'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_single_request_and_five_second_poll(self):
        self.run_js('''panel.start();assert.equal([...timers.values()][0].ms,5000);await tick();
assert.equal(reads.length,1);assert.equal(timers.size,0);reads[0].resolve({active:true});await flush();
assert.equal(applied[0].active,true);assert.equal([...timers.values()][0].ms,5000);
await tick();assert.equal(reads.length,2);''')

    def test_switch_hidden_and_late_results_never_apply(self):
        self.run_js('''panel.resume();await flush();const old=reads[0];owner++;panel.reset();
assert.equal(old.signal.aborted,true);old.resolve('old');await flush();assert.deepEqual(applied,[]);assert.equal(timers.size,0);
visible=false;panel.resume();await flush();assert.equal(reads.length,1);
visible=true;panel.resume();await flush();reads[1].resolve('new');await flush();assert.deepEqual(applied,['new']);
panel.reset();assert.equal(timers.size,0);''')

    def test_failure_backoff_is_bounded_and_success_recovers(self):
        self.run_js('''panel.resume();await flush();for(let i=0;i<4;i++){
reads[i].reject(new Error('offline'));await flush();assert.equal([...timers.values()][0].ms,[10000,20000,30000,30000][i]);await tick();}
assert.equal(errors.length,4);reads[4].resolve('recovered');await flush();assert.equal([...timers.values()][0].ms,5000);assert.deepEqual(applied,['recovered']);''')


if __name__ == '__main__':
    unittest.main()
