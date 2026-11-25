"""Save template to local UniLabOS; --run executes only after explicit flag."""
import argparse,json
from urllib.request import Request,urlopen
p=argparse.ArgumentParser();p.add_argument('--url',default='http://127.0.0.1:8788');p.add_argument('--name',default='Pocket SDL 三色测量');p.add_argument('--run',action='store_true');a=p.parse_args()
def call(path,data=None):
 req=Request(a.url+'/api/v1'+path,data=None if data is None else json.dumps(data).encode(),headers={'Content-Type':'application/json'})
 with urlopen(req,timeout=30) as r:result=json.load(r)
 if result.get('code')!=0:raise RuntimeError(str(result))
 return result['data']
templates=call('/registry/workflow-templates')['templates'];t=next(t for t in templates if t['display_name']=='Pocket SDL 三色光谱测量')
result=call('/workflows/from-template',{'template_uuid':t['uuid'],'name':a.name});print(json.dumps(result,ensure_ascii=False))
if a.run:
 wf=result['workflow'];print(json.dumps(call('/workflow-tasks',{'workflow_uuid':wf['uuid'],'run_mode':'normal'}),ensure_ascii=False))
