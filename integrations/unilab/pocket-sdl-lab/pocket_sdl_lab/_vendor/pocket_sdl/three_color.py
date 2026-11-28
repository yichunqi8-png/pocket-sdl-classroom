"""Audited three-colour acquisition, shared by CLI and UniLabOS actions."""
import csv
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

class ThreeColorSession:
    def __init__(self, driver, output_dir='three-color-data'):
        self.driver = driver
        self.root = Path(output_dir)
        self.path = None
        self.report = {}

    def _write(self):
        tmp = self.path / 'record.tmp'
        tmp.write_text(json.dumps(self.report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        tmp.replace(self.path / 'record.json')

    def cleanup(self):
        errors=[]
        for action in ('stop','disconnect'):
            try: getattr(self.driver,action)()
            except Exception as e: errors.append(f'{action}: {e}')
        return errors

    def _call(self, action, **kwargs):
        try:
            result=getattr(self.driver,action)(**kwargs)
            if result.get('success') is not True: raise RuntimeError(str(result))
            return result
        except Exception as e:
            self.report.update(status='failed',error=str(e),cleanup_errors=self.cleanup())
            if self.path: self._write()
            raise

    def connect(self):
        if self.driver.connected: raise RuntimeError('请先断开设备，再开始新记录')
        self.path=self.root/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:8])
        self.path.mkdir(parents=True)
        self.report=dict(status='running',mode=self.driver.mode,rows=[],started_at=datetime.now(timezone.utc).isoformat())
        self._write()
        result=self._call('connect');self.report['device']=result;self._write();return result

    def doctor(self):
        if not self.path: raise RuntimeError('先连接并开始记录')
        result=self._call('doctor');self.report['doctor']=result;self._write();return result

    def measure(self,r,g,b):
        if 'doctor' not in self.report: raise RuntimeError('先完成自检')
        result=self._call('measure',r=r,g=g,b=b,auto_off=True)
        self.report['rows'].append(dict(rgb=[r,g,b],timestamp=datetime.now(timezone.utc).isoformat(),result=result))
        try:self._write()
        except Exception:
            self.cleanup();raise
        return result

    def save(self, expected_rows=3):
        try:
            if not self.path: raise RuntimeError('先连接并开始记录')
            rows = self.report.get('rows', [])
            if expected_rows is not None and len(rows) != expected_rows:
                raise RuntimeError(f'需要完整 {expected_rows} 组读数（当前 {len(rows)} 组）')
            if not rows:
                raise RuntimeError('没有可保存的读数')
            with (self.path/'readings.csv').open('w',newline='',encoding='utf-8-sig') as f:
                writer=csv.writer(f);writer.writerow(['mode','time','r','g','b','415','445','480','515','555','590','630','680'])
                for row in rows:writer.writerow([self.report['mode'],row['timestamp'],*row['rgb'],*row['result']['data']['raw']])
            self.report['status']='measurements_saved';self._write()
            return {'success':True,'data':{'directory':str(self.path.resolve()),'rows':len(rows)}}
        except Exception:
            self.cleanup();raise

    def finish(self):
        errors=self.cleanup()
        if not self.path:
            if errors:raise RuntimeError(str(errors))
            return {'success':False,'data':{'directory':None,'note':'尚未开始三色记录，已执行安全清理'}}
        self.report['cleanup_errors']=errors
        self.report['status']='completed' if not errors and self.report.get('status')=='measurements_saved' else 'failed'
        self._write()
        if errors:raise RuntimeError(str(errors))
        return {'success':self.report['status']=='completed','data':{'directory':str(self.path.resolve())}}

def main():
    import argparse
    from pocket_sdl.device import PicoSpectroDevice
    p=argparse.ArgumentParser();p.add_argument('--mode',choices=['SIMULATED','PHYSICAL'],default='SIMULATED');p.add_argument('--port',default='auto');p.add_argument('--output',default='three-color-data');a=p.parse_args()
    session=ThreeColorSession(PicoSpectroDevice(mode=a.mode,port=a.port),a.output)
    try:
        session.connect();session.doctor()
        for rgb in [(1,0,0),(0,1,0),(0,0,1)]:session.measure(*rgb)
        session.save();print(json.dumps(session.finish(),ensure_ascii=False))
    finally:session.cleanup()
if __name__=='__main__':main()
