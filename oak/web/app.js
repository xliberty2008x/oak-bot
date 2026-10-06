"use strict";
const $ = id => document.getElementById(id);
let current = "telegram", cursor = 0, source = null, generation = 0, transport = "sse", sessionToken = "", telegramLaunch = false;
const messages = new Map(), runs = new Set(), activities = new Set(), seenControls = new Set();
function failure(text) { $("error").textContent = text; }
async function api(path, data) {
  const headers=sessionToken?{Authorization:"Bearer "+sessionToken}:{};
  const options=data === undefined ? {credentials:"same-origin",headers} : {method:"POST",credentials:"same-origin",headers:{...headers,"Content-Type":"application/json"},body:JSON.stringify(data)};
  options.signal=AbortSignal.timeout(path.startsWith("/api/events")?30000:15000);
  const response = await fetch(path, options);
  if (!response.ok) throw new Error(response.status === 401 ? "Увійди через Telegram або приватний ключ доступу." : "Не вдалося виконати запит. Перевір з’єднання; прийняту задачу автоматично не повторюємо.");
  const value=await response.json();if(path==="/api/session")sessionToken=value.access_token||"";return value;
}
async function downloadArtifact(value) {
  try {
    const response=await fetch("/api/artifacts/"+value.id,{credentials:"same-origin",headers:sessionToken?{Authorization:"Bearer "+sessionToken}:{},signal:AbortSignal.timeout(60000)});
    if(!response.ok)throw new Error("Не вдалося завантажити файл. Перевір вхід і з’єднання.");
    const blob=await response.blob(),url=URL.createObjectURL(blob),link=document.createElement("a");
    link.href=url;link.download=value.name||"artifact";link.click();setTimeout(()=>URL.revokeObjectURL(url),60000);
  }catch(error){failure(error.message);}
}
function element(tag, text) { const node=document.createElement(tag); if(text!==undefined)node.textContent=text; return node; }
function safeURL(value) {
  if(typeof value!=="string" || /[\u0000-\u0020\u007f]/.test(value) || new TextEncoder().encode(value).length>2048)return null;
  try { const url=new URL(value); return ["https:","http:"].includes(url.protocol)&&!url.username&&!url.password?url.href:null; } catch {return null;}
}
function tree(documentTree) {
  let count=0;
  function node(value,depth) {
    if(++count>4000||depth>18||!value||typeof value!=="object")throw new Error("Render limit");
    const children=()=>{const f=document.createDocumentFragment();for(const c of value.children||[])f.append(node(c,depth+1));return f;};
    if(value.type==="text")return document.createTextNode(value.value||"");
    if(value.type==="document")return children();
    if(value.type==="break")return element("br");
    if(value.type==="code_block"){const p=element("pre"),c=element("code",value.value||"");p.append(c);return p;}
    if(value.type==="code")return element("code",value.value||"");
    if(value.type==="link"){const url=safeURL(value.url);if(!url)return children();const a=element("a");a.href=url;a.target="_blank";a.rel="noopener noreferrer";a.append(children());return a;}
    if(value.type==="table"){const wrap=element("div");wrap.className="table-wrap";wrap.tabIndex=0;wrap.setAttribute("role","region");wrap.setAttribute("aria-label","Таблиця, гортай горизонтально");const table=element("table");table.append(children());wrap.append(table);return wrap;}
    const tags={paragraph:"p",strong:"strong",emphasis:"em",strike:"s",blockquote:"blockquote",list_item:"li",table_row:"tr",table_cell:value.header?"th":"td"};
    let tag=tags[value.type];
    if(value.type==="heading")tag="h"+Math.max(1,Math.min(6,Number(value.level)||1));
    if(value.type==="list")tag=value.ordered?"ol":"ul";
    if(!tag)throw new Error("Unsupported node");
    const result=element(tag);if(tag==="ol"&&Number.isInteger(value.start))result.start=value.start;
    if(["left","center","right"].includes(value.align))result.style.textAlign=value.align;
    result.append(children());return result;
  }
  return node(documentTree,0);
}
function message(id,role="assistant") {
  if(messages.has(id))return messages.get(id);
  $("empty")?.remove();const article=element("article");article.className="message "+role;
  const author=element("div",role==="user"?"Ти":"Oak");author.className="author";
  const content=element("div");content.className="content streaming";article.append(author,content);$("messages").append(article);
  const result={content,text:"",revision:0};messages.set(id,result);return result;
}
async function finalize(item) {
  const revision=++item.revision, text=item.text;
  try {const ast=await api("/api/render",{text});if(revision!==item.revision)return;const result=tree(ast);item.content.replaceChildren(result);item.content.classList.toggle("streaming",!!ast.literal);}
  catch {if(revision===item.revision){item.content.textContent=text;item.content.classList.add("streaming");}}
}
function activity() {$("activity").textContent=(runs.size||activities.size)?"Oak працює… Можна додати уточнення.":"";}
function decision(event) {
  const v=event.value,key=event.name+":"+v.id;if(seenControls.has(key))return;seenControls.add(key);
  const box=element("div");box.className="decision";box.append(element("p",v.summary||v.question||"Потрібна відповідь"));
  const act=async(data)=>{try{await api("/api/decision",{conversation:current,id:v.id,...data});box.replaceChildren(element("p","Відповідь передано."));}catch(e){failure(e.message);}};
  if(event.name==="approval_request")for(const [label,accepted] of [["Підтвердити",true],["Відхилити",false]]){const b=element("button",label);b.type="button";b.onclick=()=>act({kind:"approval",accepted});box.append(b);}
  else {const input=element("input");input.setAttribute("aria-label","Відповідь");input.maxLength=16000;const b=element("button","Відповісти");b.type="button";b.onclick=()=>act({kind:"input",text:input.value});box.append(input,b);}
  $("messages").append(box);
}
function receive(record) {
  if(!record||record.sequence<=cursor)return;cursor=record.sequence;const e={...(record.event.metadata||{}),...record.event};
  if(e.type==="RUN_STARTED")runs.add(e.runId);
  if(e.type==="RUN_FINISHED"||e.type==="RUN_ERROR"){runs.delete(e.runId);if(e.type==="RUN_ERROR")failure(e.message||"Задача завершилася з помилкою.");}
  const id=e.runId+":"+e.messageId;
  if(e.type==="TEXT_MESSAGE_START")message(id,e.role);
  if(e.type==="TEXT_MESSAGE_CONTENT"){const m=message(id);m.text+=e.delta||"";m.revision++;m.content.textContent=m.text;}
  if(e.type==="TEXT_MESSAGE_END"){const m=message(id);if(typeof e.text==="string")m.text=e.text;void finalize(m);}
  if(e.type==="CUSTOM"){
    if(e.name==="approval_request"||e.name==="user_input_request")decision(e);
    if(e.name==="activity"){if(e.value.active)activities.add(e.value.activityId);else activities.delete(e.value.activityId);}
    if(e.name==="artifact"&&/^[a-f0-9]{32}$/.test(e.value.id||"")){const key="artifact:"+e.value.id;if(!seenControls.has(key)){seenControls.add(key);const a=element("a",e.value.caption||e.value.name||"Відкрити файл");a.href="/api/artifacts/"+e.value.id;a.className="artifact";a.download=e.value.name||"";a.onclick=event=>{event.preventDefault();void downloadArtifact(e.value);};$("messages").append(a);}}
  }
  activity();
}
async function connect() {
  const mine=++generation;if(source){source.close();source=null;}cursor=0;messages.clear();runs.clear();activities.clear();seenControls.clear();$("messages").replaceChildren();activity();
  if(transport==="poll"){
    while(mine===generation){try{const records=await api("/api/events?conversation="+encodeURIComponent(current)+"&after="+cursor+"&wait=20");if(mine!==generation)return;for(const record of records)receive(record);$("connection").textContent="На зв’язку";}catch(e){if(mine!==generation)return;failure(e.message);$("connection").textContent="Відновлюємо зв’язок…";await new Promise(r=>setTimeout(r,2000));}}
  } else {
    source=new EventSource("/api/stream?conversation="+encodeURIComponent(current));
    source.onopen=()=>{if(mine===generation)$("connection").textContent="На зв’язку";};source.onmessage=m=>{if(mine!==generation)return;try{receive({sequence:Number(m.lastEventId),event:JSON.parse(m.data)});}catch{failure("Не вдалося прочитати оновлення.");}};source.onerror=()=>{if(mine===generation)$("connection").textContent="Відновлюємо зв’язок…";};
  }
}
async function ready() {
  const config=await api("/api/bootstrap");transport=telegramLaunch?"poll":config.transport;const list=await api("/api/conversations");$("conversation").replaceChildren(...list.map(c=>{const o=element("option",c.title);o.value=c.id;return o;}));$("login").hidden=true;$("workspace").hidden=false;failure("");void connect();
}
$("login").onsubmit=async e=>{e.preventDefault();try{await api("/api/session",{key:$("key").value});$("key").value="";await ready();}catch(error){failure(error.message);}};
$("compose").onsubmit=async e=>{e.preventDefault();const text=$("input").value.trim();if(!text)return;$("input").value="";const id=crypto.randomUUID();message("user:"+id,"user").content.textContent=text;try{await api("/api/input",{conversation:current,text,requestId:id});failure("");}catch(error){failure(error.message);}};
$("input").onkeydown=e=>{if(e.key==="Enter"&&!e.shiftKey&&!e.isComposing){e.preventDefault();$("compose").requestSubmit();}};
$("conversation").onchange=()=>{current=$("conversation").value;void connect();};
$("stop").onclick=async()=>{try{await api("/api/stop",{conversation:current});}catch(e){failure(e.message);}};
$("new").onclick=async()=>{try{const c=await api("/api/conversations",{title:"Нова розмова"});const option=element("option",c.title);option.value=c.id;$("conversation").append(option);$("conversation").value=c.id;current=c.id;void connect();}catch(e){failure(e.message);}};
function telegramReady(){try{window.Telegram?.WebApp?.ready();window.Telegram?.WebApp?.expand();}catch{}}
$("telegram-sdk").addEventListener("load",telegramReady);
(async()=>{try{
  for(let i=0;i<30&&!window.Telegram?.WebApp;i++)await new Promise(resolve=>setTimeout(resolve,100));
  telegramReady();
  const fragment=new URLSearchParams(location.hash.slice(1));
  const initData=window.Telegram?.WebApp?.initData||fragment.get("tgWebAppData");
  telegramLaunch=!!initData;
  if(initData)await api("/api/session",{initData});
  await ready();
}catch(error){$("login").hidden=false;$("connection").textContent="Потрібен вхід";failure(error.name==="TimeoutError"?"Під’єднання затримується. Закрий і знову відкрий Oak.":error.message||"Відкрий Oak із меню Telegram або введи приватний ключ.");telegramReady();}})();
