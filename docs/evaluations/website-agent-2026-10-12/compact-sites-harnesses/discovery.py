"""Frozen anonymous live-site direct adapter evaluation, no model/account values."""
import asyncio, hashlib, json, os, sys, time
from pathlib import Path
HERE=Path(__file__).resolve().parent
CODE=HERE/'code'
sys.path.insert(0,str(CODE));sys.dont_write_bytecode=True
from oak.website_agent import WebsiteAgentForms
from oak.website_scene import CATALOG_ID,CATALOG_VERSION

SITES=[
 {'site':'GitHub','url':'https://github.com/login','transition_labels':['Forgot password?','Forgot password']},
 {'site':'Wikipedia','url':'https://en.wikipedia.org/w/index.php?title=Special:UserLogin','transition_labels':['Create an account','Help with logging in','Forgot your password?']},
 {'site':'WordPress.com','url':'https://wordpress.com/log-in','transition_labels':['Log in with password','Email me a login link','Lost your password?']},
 {'site':'Slack','url':'https://slack.com/signin','transition_labels':['Sign in manually','Find your workspace','Find your workspaces','Sign in with a password']},
 {'site':'Microsoft Live','url':'https://login.live.com/','transition_labels':['Sign-in options','Sign in options']},
 {'site':'Dropbox','url':'https://www.dropbox.com/login','transition_labels':['Sign in with email','Continue with email','Forgot your password?','Forgot password?']},
 {'site':'Spotify','url':'https://accounts.spotify.com/en/login','transition_labels':['Continue with phone number','Forgot your password?','Forgot password?','Log in with a password']},
]
# No cross-origin approval is preauthorized in this independent sample.
COMPONENT={'text':'TextInput','email':'EmailInput','password':'PasswordInput','otp':'CodeInput','select':'Select','checkbox':'Checkbox'}
def hashes():
 return {str(p.relative_to(CODE)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((CODE/'oak').rglob('*')) if p.is_file()}
def digest(x):return hashlib.sha256(json.dumps(x,sort_keys=True).encode()).hexdigest()
def emit(x):
 OUT.write(json.dumps(x,sort_keys=True)+'\n');OUT.flush();os.fsync(OUT.fileno())
def norm(x):return ' '.join(x.casefold().split())
def plan_for(obs,chosen):
 scene=obs['scene'];controls={c['ref']:c for c in scene['controls']};actions={a['ref']:a for a in scene['actions']}
 candidates=[s for s in scene.get('surfaces',[]) if chosen['ref'] in s['actions']]
 for surface in sorted(candidates,key=lambda s:len(s['controls'])+len(s['actions'])):
  binds=[]
  for ref in surface['actions']:
   a=actions[ref]
   fields=[c['ref'] for c in scene['controls'] if c['form']==a['form']] if a['permitted'] and a['dispatch']=='submit' else []
   if not set(fields)<=set(surface['controls']):break
   binds.append({'ref':ref,'presentation':'primary' if ref==chosen['ref'] else 'secondary','field_refs':fields})
  else:
   return {'catalog_id':CATALOG_ID,'catalog_version':CATALOG_VERSION,'revision':obs['revision'],
    'surface_ref':surface['ref'],'anchor':chosen['ref'],
    'fields':[{'ref':ref,'component':COMPONENT[controls[ref]['type']]} for ref in surface['controls']],
    'actions':binds,'message_ref':None}
 raise ValueError('no_surface')
def summary(obs):
 scene=obs['scene']
 return {'stage':obs['stage'],'scene_stage':scene['stage'],
  'control_types':[c['type'] for c in scene['controls']],
  'control_roles':[c.get('role') for c in scene['controls']],
  'action_count':len(scene['actions']),'available_actions':sum(a['permitted'] and a['enabled'] for a in scene['actions']),
  'action_labels':[a['label'] for a in scene['actions']],
  'overflow':scene['overflow'],'surface_count':len(scene.get('surfaces',[]))}
def geometry(private):
 return {'stage':private['stage'],'field_types':[f['type'] for f in private['fields']],
  'field_count':len(private['fields']),'action_count':len(private['actions']),
  'available_action_count':sum(a['available'] for a in private['actions']),
  'option_counts':[len(f.get('options',[])) for f in private['fields']],
  'can_complete':private['can_complete']}
async def trial(site):
 start=time.monotonic(); agent=WebsiteAgentForms(site['url'],origin_approval=True,
  anonymous_static_resources=True,navigation_seconds=150)
 context=None;outcome='incomplete';inputs=0;accepted=0;forms=0;changed=False;failure=None
 async def observe():return await asyncio.wait_for(agent.agent_observe(),12)
 async def compose(obs,chosen=None):
  nonlocal forms
  scene=obs['scene']
  if chosen is None:
   # Generic presentation preference: an available native submit with observed
   # fields, then any available action, preserving all members of its surface.
   options=[a for a in scene['actions'] if a['permitted'] and a['enabled']]
   options.sort(key=lambda a: (not (a['dispatch']=='submit' and any(c['form']==a['form'] for c in scene['controls'])),a['order']))
   if not options:return None
   for anchor in options:
    try:plan=plan_for(obs,anchor)
    except Exception:continue
    break
   else:return None
  else:plan=plan_for(obs,chosen)
  await asyncio.wait_for(agent.agent_present(obs['revision'],plan),12)
  private=await asyncio.wait_for(agent.snapshot(),12)
  forms+=1
  emit({'event':'private_form','site':site['site'],'index':forms,'elapsed_ms':round((time.monotonic()-start)*1000),
    'complete_selected_surface':len(private['fields'])==len(plan['fields']) and len(private['actions'])==len(plan['actions']),**geometry(private)})
  return private
 try:
  async with asyncio.timeout(75):
   await asyncio.wait_for(agent.start(),38);context=agent.context
   obs=None
   # Read hydration; collect every changed scene but never retry a site action.
   last=None
   for i in range(10):
    obs=await observe();s=summary(obs)
    if s!=last:emit({'event':'observation','site':site['site'],'read':i,'elapsed_ms':round((time.monotonic()-start)*1000),**s});last=s
    if obs['stage'] in {'provider_blocked','unsupported_challenge','device_approval'}:break
    if obs['scene']['controls'] and obs['scene']['actions']:break
    await asyncio.sleep(1)
   if obs['stage'] in {'provider_blocked','unsupported_challenge','device_approval'}:
    outcome=obs['stage'];return
   if not obs['scene']['actions']:outcome='no_actions';return
   wanted={norm(x) for x in site['transition_labels']}
   transition=[a for a in obs['scene']['actions'] if norm(a['label']) in wanted and a['permitted'] and a['enabled']]
   if len(transition)>1:transition=sorted(transition,key=lambda a:site['transition_labels'].index(next(x for x in site['transition_labels'] if norm(x)==norm(a['label']))))[:1]
   chosen=transition[0] if transition else None
   private=await compose(obs,chosen)
   if private is None:outcome='no_composable_surface';return
   if chosen is None:outcome='form_presented_transition_unavailable';return
   action=next(a for a in private['actions'] if a['id']==chosen['ref'])
   if action['field_refs'] or not action['available']:
    outcome='transition_requires_private_values';return
   old_revision=obs['revision'];old_shape=summary(obs)
   emit({'event':'transition_attempt','site':site['site'],'label':chosen['label'],'private_value_count':0})
   await asyncio.wait_for(agent.submit(private['revision'],action['id'],{}),15);accepted+=1
   for i in range(12):
    await asyncio.sleep(.5);obs=await observe();s=summary(obs)
    if s!=last:emit({'event':'observation','site':site['site'],'read':10+i,'elapsed_ms':round((time.monotonic()-start)*1000),**s});last=s
    if obs['stage'] in {'provider_blocked','unsupported_challenge','device_approval'}:
     outcome=obs['stage'];return
    changed=obs['revision']!=old_revision and s!=old_shape
    if changed and (obs['scene']['controls'] or obs['scene']['actions']):break
   if not changed:outcome='transition_no_changed_scene';return
   private=await compose(obs)
   outcome='rebuilt_after_value_free_transition' if private is not None else 'changed_scene_not_composable'
 except TimeoutError:outcome='timeout'
 except Exception as e:
  outcome='adapter_error';failure=getattr(e,'code',None) or type(e).__name__
  if failure not in {'stale_form','human_step_pending','invalid_plan','navigation_requires_owner','navigation_pending','action_uncertain','ValueError'}:failure='other'
 finally:
  record={'event':'site_result','site':site['site'],'outcome':outcome,'error':failure,'forms':forms,
   'accepted_value_free_actions':accepted,'scene_changed':changed,'private_value_count':inputs,
   'model_calls':0,'direct_adapter_only':True,'authentication_verified':False,
   'same_context':context is not None and agent.context is context,
   'elapsed_ms':round((time.monotonic()-start)*1000),
   'diagnostics':agent.diagnostics()}
  try:
   await asyncio.wait_for(agent.close(),15)
   record['cleanup']=agent.browser is None and agent.context is None and agent.driver is None and agent._navigation.diagnostics()['proxy']['active']==0
  except Exception:record['cleanup']=False
  record['code_unchanged']=hashes()==MANIFEST['files'];emit(record)
  print(json.dumps({k:record[k] for k in ('site','outcome','forms','accepted_value_free_actions','cleanup')}),flush=True)
async def run():
 for site in SITES:await trial(site)
if '--freeze' in sys.argv:
 files=hashes();manifest={'sites':SITES,'files':files,'code_digest':digest(files),'base_commit':os.environ.get('OAK_EVAL_BASE_COMMIT','unspecified'),'selection_frozen_before_network':True,'direct_adapter_only':True,'owner_account':False,'private_inputs':False,'model_calls':0,'approved_cross_origins':[]}
 (HERE/'manifest.json').write_text(json.dumps(manifest,sort_keys=True,indent=2)+'\n');print(digest(manifest));raise SystemExit
MANIFEST=json.loads((HERE/'manifest.json').read_text())
if MANIFEST['sites']!=SITES or MANIFEST['files']!=hashes():raise SystemExit('pin mismatch')
if '--run' not in sys.argv:print(json.dumps({'validated':True,'network':False}));raise SystemExit
fd=os.open(HERE/'receipt.jsonl',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w') as OUT:
 emit({'event':'start','manifest_sha256':hashlib.sha256((HERE/'manifest.json').read_bytes()).hexdigest(),'code_digest':MANIFEST['code_digest'],'site_count':len(SITES),'model_calls':0,'owner_account':False,'production':False})
 asyncio.run(run())
print(json.dumps({'receipt_sha256':hashlib.sha256((HERE/'receipt.jsonl').read_bytes()).hexdigest()}))
