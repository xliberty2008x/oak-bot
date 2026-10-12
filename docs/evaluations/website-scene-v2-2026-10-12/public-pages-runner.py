import asyncio,json,os,sys,time,hashlib
from pathlib import Path
sys.dont_write_bytecode=True
ROOT=Path(os.environ['OAK_EVALUATION_CODE_ROOT'])
sys.path.insert(0,str(ROOT))
from oak.website_forms import WebsiteForms
from oak.website_composer import LeaseComposer,read_key
from oak.website_scene_planner import ScenePlanner
from oak.signin_providers import RecoverableFormError
SITES=[('GitHub','https://github.com/login'),('Slack','https://slack.com/signin'),
 ('Netflix','https://www.netflix.com/login'),('Amazon','https://www.amazon.com/ap/signin'),
 ('Spotify','https://accounts.spotify.com/en/login'),('Wikipedia','https://en.wikipedia.org/w/index.php?title=Special:UserLogin')]
async def main():
 planner=ScenePlanner(read_key('/var/lib/oak-website-forms/openrouter.key',os.geteuid()),'typesafe/jev-1.13')
 path=Path(os.environ['OAK_EVALUATION_RECEIPT'])
 fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 with os.fdopen(fd,'w') as out:
  for name,url in SITES:
   composer=LeaseComposer(planner,4);browser=WebsiteForms(url,composer=composer);start=time.monotonic()
   result={'site':name,'inputs':0,'actions':0,'production_broker':False,'stage':None,'field_types':[]}
   try:
    await asyncio.wait_for(browser.start(),35)
    await asyncio.sleep(2)
    state=await asyncio.wait_for(browser.snapshot(),30)
    result.update(stage=state['stage'],field_types=[x['type'] for x in state['fields']],action_count=len(state['actions']))
   except RecoverableFormError as error:result['error']=error.code
   except Exception as error:result['error_type']=type(error).__name__
   finally:
    try:
     await asyncio.wait_for(browser.close(),15);result['closed']=True
    except Exception:result['closed']=False
   result['jev']=composer.diagnostics();result['elapsed_ms']=round((time.monotonic()-start)*1000)
   out.write(json.dumps(result,sort_keys=True)+'\n');out.flush();os.fsync(out.fileno())
   print(json.dumps(result,sort_keys=True),flush=True)
 print(json.dumps({'receipt':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}))
asyncio.run(main())
