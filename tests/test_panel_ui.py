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
        render = app[app.index('function renderPanel('):app.index('function clearArtifacts(')]
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

    def test_keyed_task_drafts_and_unrelated_approval_session_focus_survive_refresh(self):
        app = (ROOT / 'oak/web/app.js').read_text()
        source = '\n'.join(app[app.index(start):app.index(end)] for start, end in (
            ('function reconcileChildren(', 'function message('),
            ('function renderTasks(', 'function svgIcon('),
            ('function taskReschedule(', 'function taskPause('),
            ('function renderApprovals(', 'async function decideApproval('),
            ('function renderSessions(', 'async function loadSessions(')))
        script = r"""
const assert=require('node:assert/strict');
const document={activeElement:null,createTextNode:value=>element('#text',value)};
class Node {
  constructor(tag){this.tag=tag;this.children=[];this.dataset={};this.value='';this.events={};}
  append(...nodes){for(const node of nodes){node.remove();node.parent=this;this.children.push(node);}}
  remove(){if(this.parent){if(this.contains(document.activeElement))document.activeElement=null;this.parent.children.splice(this.parent.children.indexOf(this),1);this.parent=null;}}
  replaceChildren(){for(const node of [...this.children])node.remove();}
  insertBefore(node,anchor){node.remove();node.parent=this;this.children.splice(anchor?this.children.indexOf(anchor):this.children.length,0,node);}
  contains(node){return node===this||this.children.some(child=>child.contains(node));}
  get isConnected(){return this.root||!!this.parent?.isConnected;}
  focus(){document.activeElement=this;}
  setAttribute(name,value){this[name]=value;}
  addEventListener(name,fn){this.events[name]=fn;}
  querySelector(selector){return descendants(this).find(node=>selector==='details[open]'&&node.tag==='details'&&node.open)||null;}
}
const descendants=node=>node.children.flatMap(child=>[child,...descendants(child)]);
const nodes=new Map(),$=id=>{if(!nodes.has(id)){const node=new Node('div');node.root=true;nodes.set(id,node);}return nodes.get(id);};
const element=(tag,value,cls)=>{const node=new Node(tag);node.textContent=value;node.className=cls;return node;};
document.querySelectorAll=()=>[...nodes.values()].flatMap(descendants).filter(node=>node.dataset.session);
const dateNode=()=>element('time'),intervalText=()=>'',label=(labels,value)=>labels[value]||value;
const selectedName=()=> 'General',message=()=>{},updateActions=()=>{},renderTaskSummary=()=>{},renderSelectedSession=()=>{},fact=()=>{},count=String;
const taskHistory=()=>element('details'),taskPause=()=>element('button');
const taskLabels={pending:'Pending',running:'Running'},turnLabels={},taskReschedules=new Map(),taskPauses=new Map(),approvalBlocked=new Set(),topicBlocked=new Set();
const topicKey=(kind,session)=>kind+session.id;
let timezone='Europe/Kyiv',mutationBusy=false,refreshing=false,sessionsData=null,selectedSession='telegram';
""" + source + r"""
const task={id:'a'.repeat(32),revision:1,status:'pending',due:1234,preview:'Task',mode:'remind',interval:null};
const snapshot={tasks:[task],timezone:'Europe/Kyiv',summary:{}};renderTasks(snapshot);
const row=$('tasks-list').children[0],editor=descendants(row).find(node=>node.tag==='input');
editor.value='2026-11-01T12:30';editor.focus();renderTasks(JSON.parse(JSON.stringify(snapshot)));
assert.equal($('tasks-list').children[0],row);assert.equal(editor.value,'2026-11-01T12:30');assert.equal(document.activeElement,editor);
renderTasks({...snapshot,tasks:[{...task,revision:2,due:5678}]});
assert.notEqual($('tasks-list').children[0],row);assert.equal(descendants($('tasks-list').children[0]).find(node=>node.tag==='input').value,'');
const approval={id:'a'.repeat(10),state:'pending',summary:'Allow?',expires:1234};renderApprovals([approval]);
const approvalRow=$('approvals-list').children[0],approve=descendants(approvalRow).find(node=>node.tag==='button');approve.focus();
renderApprovals([{...approval,id:'b'.repeat(10),state:'expired'},approval]);
assert.equal($('approvals-list').children[1],approvalRow);assert.equal(document.activeElement,approve);
renderApprovals([{...approval,state:'accepted'}]);assert.equal(approve.isConnected,false);
const session={id:'telegram',name:'General',initialized:true};renderSessions({sessions:[session]});
const sessionRow=$('sessions-list').children[0],select=descendants(sessionRow).find(node=>node.dataset.session);select.focus();
renderSessions({sessions:[session,{...session,id:'topic:42',name:'Topic'}]});
assert.equal($('sessions-list').children[0],sessionRow);assert.equal(document.activeElement,select);
renderSessions({sessions:[{...session,active:true},{...session,id:'topic:42',name:'Topic'}]});
assert.equal(document.activeElement.dataset.session,'telegram');
"""
        result = subprocess.run([NODE, '-e', script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_locale_response_cannot_apply_after_session_change(self):
        app = (ROOT / 'oak/web/app.js').read_text()
        source = app[app.index('function text('):app.index('function element(')]
        source = source.replace('import("/locale.js")', 'Promise.resolve({createLocale:value=>({locale:"uk",t:key=>value[key]})})')
        script = r"""
const assert=require('node:assert/strict');let productLocale=null,localeEpoch=-1,sessionEpoch=1;
const taskLabels={pending:'Initial'},document={documentElement:{},querySelectorAll:()=>[]},requests=[];
const api=()=>new Promise(resolve=>requests.push(resolve));
""" + source + r"""
(async()=>{const old=loadLocale(1);sessionEpoch=2;const current=loadLocale(2);
requests[1]({'tasks.status.pending':'Current'});await current;requests[0]({'tasks.status.pending':'Old'});await old;
assert.equal(taskLabels.pending,'Current');assert.equal(text('tasks.status.pending','Fallback'),'Current');
sessionEpoch=3;assert.equal(text('tasks.status.pending','Fallback'),'Fallback');
})().catch(e=>{console.error(e);process.exit(1)});
"""
        result = subprocess.run([NODE, '-e', script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_artifact_preview_download_share_fetch_and_discard_late_owner_results(self):
        app = (ROOT / 'oak/web/app.js').read_text()
        source = app[app.index('function clearArtifacts('):app.index('async function loadActivity(')]
        script = r"""
const assert=require('node:assert/strict');
class Node { constructor(){this.children=[];this.events={};this.isConnected=true;} append(...nodes){this.children.push(...nodes);} replaceChildren(){this.children=[];} addEventListener(type,fn){this.events[type]=fn;} click(){} }
const nodes=new Map(),$=id=>{if(!nodes.has(id))nodes.set(id,new Node());return nodes.get(id);};
const element=tag=>Object.assign(new Node(),{tag}),message=()=>{},text=(key,fallback)=>fallback;let sessionEpoch=1,artifactsGeneration=0,artifactsNext=null;
const artifactAssets=new Map(),created=[],revoked=[],reads=[];
const URL={createObjectURL:blob=>{const url='blob:'+created.length;created.push(url);return url;},revokeObjectURL:url=>revoked.push(url)};
const api=async()=>({items:[{id:'a'.repeat(32),name:'image.png',kind:'image',mime:'image/png',size_bytes:5,available:true}],next_cursor:null});
const artifact=(id,options)=>new Promise(resolve=>reads.push({resolve,options}));
""" + source + r"""
(async()=>{
await loadArtifacts();const actions=$('artifacts-list').children[0].children[2];
const preview=actions.children[0].events.click(),download=actions.children[1].events.click();
assert.equal(reads.length,1);assert.equal(reads[0].options.limit,10*1024*1024);reads[0].resolve(new Blob(['image']));await Promise.all([preview,download]);assert.equal(created.length,1);
clearArtifacts();assert.deepEqual(revoked,created);assert.equal(reads[0].options.signal.aborted,true);
await loadArtifacts();const pending=$('artifacts-list').children[0].children[2].children[0].events.click();sessionEpoch++;clearArtifacts();
reads[1].resolve(new Blob(['late']));await pending;assert.equal(created.length,1);assert.equal(artifactAssets.size,0);
await loadArtifacts();const largeRow=$('artifacts-list').children[0],largeActions=largeRow.children[2];
const largeDownload=largeActions.children[1].events.click();assert.equal(reads[2].options.limit,100*1024*1024);
const simultaneousPreview=largeActions.children[0].events.click();reads[2].resolve({size:80*1024*1024});await Promise.all([largeDownload,simultaneousPreview]);
assert.equal(created.length,2);assert.equal(largeRow.children.filter(node=>node.tag==='img').length,0);
await largeActions.children[0].events.click();assert.equal(reads.length,3);assert.equal(largeRow.children.filter(node=>node.tag==='img').length,0);
assert.match(largeRow.children[1].textContent,/до 10 МБ/);clearArtifacts();assert.equal(revoked.length,2);
})().catch(e=>{console.error(e);process.exit(1)});
"""
        result = subprocess.run([NODE, '-e', script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

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

    def test_native_bridge_scopes_listeners_insets_and_pending_confirmation(self):
        source = (ROOT / 'oak/web/telegram-bridge.js').read_text()
        scenario = r"""
const assert=require('node:assert/strict'),vm=require('node:vm');
const mod=new vm.SourceTextModule(SOURCE);await mod.link(()=>{});await mod.evaluate();
const styles=new Map(),events=new Map();let click=null,callback=null,visible=false,backs=0,canBack=false;
const app={safeAreaInset:{top:20,bottom:-1},contentSafeAreaInset:{top:40,right:Infinity},
BackButton:{onClick:fn=>click=fn,offClick:()=>click=null,show:()=>visible=true,hide:()=>visible=false},
onEvent:(k,v)=>events.set(k,v),offEvent:k=>events.delete(k),showConfirm:(text,fn)=>callback=fn};
const bridge=mod.namespace.createTelegramBridge({app:()=>app,root:{style:{setProperty:(k,v)=>styles.set(k,v)}},canBack:()=>canBack,back:()=>backs++});
bridge.connect();bridge.connect();assert.equal(events.size,2);assert.equal(styles.get('--native-safe-top'),'20px');assert.equal(styles.get('--native-content-right'),'0px');
canBack=true;bridge.update();assert.equal(visible,true);click();assert.equal(backs,1);
let promise=bridge.confirm('Proceed?');callback(true);assert.equal(await promise,true);
promise=bridge.confirm('Pending');bridge.destroy();assert.equal(await promise,false);assert.equal(events.size,0);assert.equal(click,null);assert.equal(visible,false);
app.showConfirm=()=>{throw Error('unsupported')};assert.equal(bridge.confirm('fallback'),null);bridge.feedback();
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
