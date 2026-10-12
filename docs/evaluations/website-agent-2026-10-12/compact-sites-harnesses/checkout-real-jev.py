"""Public demo accounts only; no owner accounts, models or secrets in receipts."""
import asyncio,hashlib,json,sys,time
from pathlib import Path
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parent;sys.path.insert(0,str(ROOT/'code'))
from oak.website_agent import WebsiteAgentForms
from oak.website_scene import CATALOG_ID,CATALOG_VERSION
from oak.signin_providers import RecoverableFormError
SITES=[('SauceDemo','https://www.saucedemo.com/','standard_user','secret_sauce','Login')]
COMP={'text':'TextInput','email':'EmailInput','password':'PasswordInput','checkbox':'Checkbox','select':'Select','otp':'CodeInput'}
def emit(x):print(json.dumps(x,sort_keys=True),flush=True)
def plan(obs,anchor):
 sc=obs['scene'];cc={x['ref']:x for x in sc['controls']};aa={x['ref']:x for x in sc['actions']}
 own={c['ref'] for c in sc['controls'] if c['form']==anchor['form']}
 ss=sorted((s for s in sc['surfaces'] if anchor['ref'] in s['actions'] and own<=set(s['controls'])),key=lambda s:len(s['controls'])+len(s['actions']))
 for s in ss:
  bindings=[{'ref':r,'presentation':'primary' if r==anchor['ref'] else 'secondary','field_refs':[c['ref'] for c in sc['controls'] if c['form']==aa[r]['form']] if aa[r]['permitted'] and aa[r]['dispatch']=='submit' else []} for r in s['actions']]
  if all(set(b['field_refs'])<=set(s['controls']) for b in bindings):return {'catalog_id':CATALOG_ID,'catalog_version':CATALOG_VERSION,'revision':obs['revision'],'surface_ref':s['ref'],'anchor':anchor['ref'],'fields':[{'ref':r,'component':COMP[cc[r]['type']]} for r in s['controls']],'actions':bindings,'message_ref':None}
 raise ValueError('no_surface')
async def trial(name,url,username,password,label):
 a=WebsiteAgentForms(url,origin_approval=True,anonymous_static_resources=True,navigation_seconds=150);result={'event':'result','site':name,'public_demo_account_only':True,'owner_account':False,'models':0,'authentication_verified':False,'accepted_submissions':0};start=time.monotonic()
 async def observe():
  for i in range(50):
   try:return await asyncio.wait_for(a.agent_observe(),5)
   except RecoverableFormError as e:
    if e.code!='navigation_pending':raise
    await asyncio.sleep(.1)
  raise TimeoutError
 try:
  async with asyncio.timeout(70):
   await a.start();context=a.context;await asyncio.sleep(3);o=await observe()
   emit({'event':'before','site':name,'stage':o['stage'],'control_types':[c['type'] for c in o['scene']['controls']],'action_count':len(o['scene']['actions']),'overflow':o['scene']['overflow']})
   target=next(x for x in o['scene']['actions'] if x['label'].casefold()==label.casefold() and x['permitted'] and x['enabled']);p=plan(o,target)
   await a.agent_present(o['revision'],p);private=await a.snapshot();action=next(x for x in private['actions'] if x['id']==target['ref']);fieldmap={f['name']:f for f in private['fields']}
   values={r:password if fieldmap[r]['type']=='password' else username for r in action['field_refs']}
   if len(values)!=2 or not any(fieldmap[r]['type']=='password' for r in values):raise ValueError('guard')
   old=o['revision'];await a.submit(private['revision'],action['id'],values);result['accepted_submissions']=1
   for i in range(20):
    await asyncio.sleep(.3);o=await observe()
    if o['revision']!=old and not any(c['type']=='password' for c in o['scene']['controls']):break
   wire=json.dumps(o)+json.dumps(a.diagnostics());canaries_not_in_model_or_diagnostics=username not in wire and password not in wire
   result.update(stage=o['stage'],same_context=a.context is context,revision_changed=o['revision']!=old,control_types=[c['type'] for c in o['scene']['controls']],action_count=len(o['scene']['actions']),action_labels=[x['label'] for x in o['scene']['actions']],canaries_not_in_model_or_diagnostics=canaries_not_in_model_or_diagnostics,overflow=o['scene']['overflow'])
   for label_start in ('Cart,','Checkout'):
    available=[x for x in o['scene']['actions'] if x['permitted'] and x['enabled'] and x['label'].startswith(label_start)]
    if len(available)!=1:raise ValueError('ambiguous_step')
    anchor=available[0];p=plan(o,anchor);await a.agent_present(o['revision'],p);f=await a.snapshot();action=next(x for x in f['actions'] if x['id']==anchor['ref'])
    if action['field_refs']:raise ValueError('private_values_required')
    await a.submit(f['revision'],action['id'],{});result['accepted_value_free_actions']=result.get('accepted_value_free_actions',0)+1
    await asyncio.sleep(.5);o=await observe()
    emit({'event':'step','site':name,'step':label_start,'stage':o['stage'],'control_types':[c['type'] for c in o['scene']['controls']],'action_count':len(o['scene']['actions']),'overflow':o['scene']['overflow'],'private_values_count':0})
   from oak.website_composer import step_planner_factory
   import os
   goal='Prepare the public demonstration checkout details form for the human to fill. Stop before submitting checkout details or placing any order.'
   config=json.loads(Path(os.environ['OAK_EVAL_JEV_CONFIG']).read_text());config['max_calls']=3
   factory=step_planner_factory(config,os.getuid())
   planner=factory(goal)
   async def record(meta):emit({'event':'jev_request','site':name,**meta})
   proposal=await planner.plan(o,record=record)
   result.update(actual_jev=True,models=planner.calls,operation=proposal['operation'],usage=proposal['usage'],model=proposal['model'],proposal_elapsed_ms=proposal['elapsed_ms'],after_input=o['scene']['after_input'],decision=proposal['decision'])
   if proposal['operation']=='present':
    await a.agent_present(proposal['revision'],proposal['plan']);f=await a.snapshot()
    result.update(rebuilt_private_stage=f['stage'],rebuilt_field_types=[x['type'] for x in f['fields']],rebuilt_action_count=len(f['actions']),can_complete=f['can_complete'],form_actions=[{'label':x['label'],'field_count':len(x['field_refs'])} for x in f['actions']],complete_selected_surface=len(f['fields'])==len(proposal['plan']['fields']) and len(f['actions'])==len(proposal['plan']['actions']))
   wire=json.dumps(o)+json.dumps(a.diagnostics())+json.dumps(proposal)
   result['canaries_not_in_model_or_diagnostics']=username not in wire and password not in wire
   result['checkout_details_submitted']=False;result['order_placed']=False

 except Exception as e:result['error']=getattr(e,'code',None) or ('timeout' if isinstance(e,TimeoutError) else 'adapter_error')
 finally:
  result['elapsed_ms']=round((time.monotonic()-start)*1000)
  try:await asyncio.wait_for(a.close(),15);result['cleanup']=a.browser is None and a.context is None and a.driver is None
  except Exception:result['cleanup']=False
  emit(result)
async def main():
 emit({'event':'start','sites':[{'name':x[0],'url':x[1]} for x in SITES],'selection_frozen_before_network':True,'real_models':True,'max_paid_calls_this_followup':3,'prior_paid_attempts':9,'owner_account':False,'purpose':'live_public_demo_private_submit_and_rebuild','provider_authenticated_verdict':False})
 for site in SITES:await trial(*site)
asyncio.run(main())
