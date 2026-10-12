"""Public demo accounts only; no owner accounts, models or secrets in receipts."""
import asyncio,hashlib,json,sys,time
from pathlib import Path
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parent;sys.path.insert(0,str(ROOT/'code'))
from oak.website_agent import WebsiteAgentForms
from oak.website_scene import CATALOG_ID,CATALOG_VERSION
from oak.signin_providers import RecoverableFormError
SITES=[('SauceDemo','https://www.saucedemo.com/','standard_user','secret_sauce','Login'),('ExpandTesting','https://practice.expandtesting.com/login','practice','SuperSecretPassword!','Login')]
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
   available=[x for x in o['scene']['actions'] if x['permitted'] and x['enabled']]
   if available and o['stage']=='agent_working':
    anchor=next((x for x in available if 'logout' in x['label'].casefold() or 'log out' in x['label'].casefold()),available[0]);p=plan(o,anchor);await a.agent_present(o['revision'],p);f=await a.snapshot();result.update(rebuilt_private_stage=f['stage'],rebuilt_field_types=[x['type'] for x in f['fields']],rebuilt_action_count=len(f['actions']),can_complete=f['can_complete'])
 except Exception as e:result['error']=getattr(e,'code',None) or ('timeout' if isinstance(e,TimeoutError) else 'adapter_error')
 finally:
  result['elapsed_ms']=round((time.monotonic()-start)*1000)
  try:await asyncio.wait_for(a.close(),15);result['cleanup']=a.browser is None and a.context is None and a.driver is None
  except Exception:result['cleanup']=False
  emit(result)
async def main():
 emit({'event':'start','sites':[{'name':x[0],'url':x[1]} for x in SITES],'selection_frozen_before_network':True,'real_models':False,'owner_account':False,'purpose':'live_public_demo_private_submit_and_rebuild','provider_authenticated_verdict':False})
 for site in SITES:await trial(*site)
asyncio.run(main())
