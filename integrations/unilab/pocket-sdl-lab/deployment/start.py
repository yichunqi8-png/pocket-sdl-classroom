"""Run with the Python interpreter that has UniLabOS 0.12.3 installed."""
import argparse,json,subprocess,sys
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--mode',choices=['SIMULATED','PHYSICAL'],default='SIMULATED');p.add_argument('--port',type=int,default=8788);p.add_argument('--check',action='store_true');a=p.parse_args()
root=Path(__file__).resolve().parents[1];work=root/'.runtime';cache=work/'community_devices';cache.mkdir(parents=True,exist_ok=True)
(cache/'manifest.json').write_text(json.dumps({'packages':{'community.pocket_sdl_lab':{'package_dir':str(root),'version':'1.1.0','aliases':{'community.pocket_sdl_lab.pico_spectro':'pico_spectro'}}}},indent=2),encoding="utf-8")
graph=root/'deployment/graphs'/('production.json' if a.mode=='PHYSICAL' else 'dry-run.json')
cmd=[sys.executable,'-m','unilabos.app.main','--backend','hostlink','--working_dir',str(work),'--server_database_root',str(work/'db'),'--graph',str(graph),'--port_management',str(a.port),'--hostlink_port',str(a.port+1),'--disable_browser','--no_safe_restart','--external_devices_only']
if a.check:cmd+=['--check_mode']
raise SystemExit(subprocess.call(cmd,cwd=root))
