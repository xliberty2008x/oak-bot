"""Actual StepPlanner proposals on fresh anonymous pages; no user values."""
import asyncio, hashlib, json, os, sys, time
from pathlib import Path
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parent;sys.path.insert(0,str(ROOT/'code'))
from oak.website_agent import WebsiteAgentForms
from oak.website_composer import step_planner_factory
from oak.website_planner import WebsitePlannerError
SITES=[('GitHub','https://github.com/login'),('Dropbox','https://www.dropbox.com/login'),('Spotify','https://accounts.spotify.com/en/login')]
MAX=12;reserved=0
factory=step_planner_factory(json.loads(Path(os.environ['OAK_EVAL_JEV_CONFIG']).read_text()),os.getuid())
def emit(x):print(json.dumps(x,sort_keys=True),flush=True)
async def reserve():
 global reserved
 if reserved>=MAX:raise WebsitePlannerError('unavailable')
 reserved+=1
async def trial(name,url):
 agent=WebsiteAgentForms(url,origin_approval=True,anonymous_static_resources=True,navigation_seconds=150)
 start=time.monotonic();planner=factory();result={'event':'result','site':name,'private_input_count':0,'authentication_verified':False,'production':False,'native_controller':False,'actual_jev':True}
 try:
  async with asyncio.timeout(65):
   await agent.start();await asyncio.sleep(4)
   obs=await asyncio.wait_for(agent.agent_observe(),10)
   emit({'event':'observation','site':name,'stage':obs['stage'],'controls':[c['type'] for c in obs['scene']['controls']], 'action_count':len(obs['scene']['actions']),'surface_count':len(obs['scene'].get('surfaces',[])),'overflow':obs['scene']['overflow'],'state_sha256':hashlib.sha256(json.dumps(obs,sort_keys=True).encode()).hexdigest(),'elapsed_ms':round((time.monotonic()-start)*1000)})
   async def record(meta):emit({'event':'jev_request','site':name,**meta})
   proposal=await planner.plan(obs,reserve=reserve,record=record)
   result.update(operation=proposal['operation'],calls=planner.calls,usage=proposal['usage'],model=proposal['model'],proposal_elapsed_ms=proposal['elapsed_ms'],decision=proposal['decision'])
   if proposal['operation']=='present':
    plan=proposal['plan'];surface=next(s for s in obs['scene']['surfaces'] if s['ref']==plan['surface_ref'])
    await asyncio.wait_for(agent.agent_present(proposal['revision'],plan),10)
    private=await asyncio.wait_for(agent.snapshot(),10)
    result.update(presented=private['stage']=='form',field_types=[f['type'] for f in private['fields']],field_count=len(private['fields']),action_count=len(private['actions']),action_labels=[a['label'] for a in private['actions']],available_action_count=sum(a['available'] for a in private['actions']),complete_selected_surface=len(private['fields'])==len(surface['controls']) and len(private['actions'])==len(surface['actions']),observed_control_count=len(obs['scene']['controls']),observed_action_count=len(obs['scene']['actions']),can_complete=private['can_complete'])
   else:result['presented']=False
 except Exception as e:
  result.update(error=getattr(e,'code',None) or ('timeout' if isinstance(e,TimeoutError) else 'adapter_error'),calls=planner.calls,presented=False)
 finally:
  result['elapsed_ms']=round((time.monotonic()-start)*1000)
  try:
   await asyncio.wait_for(agent.close(),15)
   result['cleanup']=agent.browser is None and agent.context is None and agent.driver is None and agent._navigation.diagnostics()['proxy']['active']==0
  except Exception:result['cleanup']=False
  emit(result)
async def run():
 emit({'event':'start','sample':SITES,'selection_frozen_before_network':True,'actual_jev':True,'max_paid_calls':MAX,'owner_account':False,'private_input_count':0,'code_digest':'dc64613dcc8084963847f5d5d0572061117720af7c004339b9745efab9043fbb'})
 for name,url in SITES:await trial(name,url)
 emit({'event':'complete','paid_attempts':reserved})
asyncio.run(run())
