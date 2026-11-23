import copy
import json
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from pocket_sdl import LabError
from workshop.classroom import ClassroomEngine
from workshop.course_server import course_handler

class ClassroomTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.e=ClassroomEngine(mode='SIMULATED',sim_delay_s=0,data_dir=self.tmp.name)
    def tearDown(self):
        self.e.shutdown()
        if self.e._worker_thread:self.e._worker_thread.join(5)
        self.tmp.cleanup()
    def wait(self):
        deadline=time.monotonic()+30
        while self.e.active_job and time.monotonic()<deadline:time.sleep(.01)
        self.assertIsNone(self.e.active_job)
    def prepare(self):
        self.e.calibrate();self.e.stability_start();self.wait()
        self.assertTrue(self.e.course['stability']['passed'])
        self.e.settings(group='test',budget=8,seed=11)
        self.e.arena_new_target(kind='custom',x=[.3,.6,.4])
    def test_gate_recalibration_and_reconnect(self):
        with self.assertRaises(LabError):self.e.arena_new_target()
        self.prepare();self.e.calibrate()
        self.assertIsNone(self.e.arena['round_id'])
        with self.assertRaises(LabError):self.e.arena_start_ai()
        self.e.stability_start();self.wait();self.e.connect()
        self.assertIsNone(self.e.calibration)
        self.assertIsNone(self.e.course['stability'])
    def test_search_confirmation_isolation(self):
        self.prepare()
        with self.assertRaises(LabError):self.e.settings(seed=12,budget=8)
        self.e.arena_human_start('test',budget=20)
        self.assertEqual(self.e.arena['human']['budget'],8)
        self.e.arena_human_measure([.3,.6,.4])
        for strategy in ['random','bo']:
            self.e.arena_start_ai(strategy,budget=24);self.wait()
        for run in self.e.arena['ai_runs'].values():
            self.assertEqual(len(run['rows']),8)
            self.assertEqual([r['x'] for r in run['rows'][:4]],self.e.arena['common_initial_x'])
        before=copy.deepcopy(self.e.arena);self.e.confirm_start(3);self.wait()
        self.assertEqual(before,self.e.arena)
        report=self.e.course['confirmations'][-1];self.assertEqual(report['status'],'completed')
        for r in report['results'].values():
            self.assertEqual(len(r['rows']),3);self.assertIsNotNone(r['sample_sd'])
            self.assertTrue(all(row['x']==r['x'] for row in r['rows']))
    def test_persistence_previous_round_restart(self):
        self.prepare();self.e.arena_human_measure([.4,.5,.6])
        rid=self.e.arena['round_id'];sid=self.e.session_id
        self.e.arena_new_target()
        events=self.e.export(sid,'jsonl').decode()
        self.assertIn(rid,events);self.assertIn('human_step',events)
        self.assertIn('F8',self.e.export(sid,'csv').decode())
        other=ClassroomEngine(mode='SIMULATED',sim_delay_s=0,data_dir=self.tmp.name)
        try:
            self.assertIn(sid,[r['id'] for r in other.archive_list()])
            self.assertIsNone(other.calibration)
            self.assertEqual(json.loads(other.export(sid))['course']['group'],'test')
            with self.assertRaises(LabError):other.export('../x')
        finally:other.shutdown()
    def test_faults_recovery(self):
        self.prepare()
        for fault,code in [('timeout','SERIAL_TIMEOUT'),('saturation','SATURAT')]:
            self.e.fault_set(fault)
            with self.assertRaises(LabError) as cm:self.e.live_measure_once([.5,.5,.5])
            self.assertIn(code,cm.exception.code);self.assertEqual(self.e.device.rgb,[0,0,0])
        self.e.fault_set('drift');self.e.stability_start();self.wait()
        self.assertFalse(self.e.course['stability']['passed'])
        self.e.fault_set('none');self.e.stability_start();self.wait()
        self.assertTrue(self.e.course['stability']['passed'])
        self.e.led_off();self.e.calibrate()
    def test_benchmark_prefixes_no_arena_mutation(self):
        self.prepare();before=copy.deepcopy(self.e.arena)
        self.e.benchmark_start([1,2],[6,8]);self.wait()
        b=self.e.course['benchmark'];self.assertEqual(b['status'],'completed')
        self.assertEqual(self.e.arena,before);self.assertEqual(len(b['runs']),4)
        for seed in [1,2]:
            r=[x for x in b['runs'] if x['seed']==seed];self.assertEqual(r[0]['rows'][:4],r[1]['rows'][:4])
        for s in b['summary']:self.assertEqual(s['bo_wins']+s['ties']+s['bo_losses'],2)
        self.e.mode='PHYSICAL'
        with self.assertRaises(LabError):self.e.fault_set('timeout')
        with self.assertRaises(LabError):self.e.benchmark_start()
    def test_stop_confirmation_preserves_search(self):
        self.prepare();self.e.arena_human_measure([.3,.6,.4])
        before=copy.deepcopy(self.e.arena)
        entered=threading.Event();release=threading.Event();original=self.e._measure_raw
        def delayed(*a,**kw):
            entered.set();release.wait(2)
            return original(*a,**kw)
        self.e._measure_raw=delayed
        self.e.confirm_start(20);self.assertTrue(entered.wait(2))
        self.e.led_off();release.set();self.wait()
        self.assertEqual(self.e.course['confirmations'][-1]['status'],'interrupted')
        self.assertEqual(before,self.e.arena)
        self.assertEqual(self.e.device.rgb,[0,0,0])

    def test_http_audience_boundaries(self):
        servers=[]
        try:
            for audience in [False,True]:
                s=ThreadingHTTPServer(('127.0.0.1',0),course_handler(self.e,8773,audience));servers.append(s)
                threading.Thread(target=s.serve_forever,daemon=True).start();base=f'http://127.0.0.1:{s.server_port}'
                with urlopen(base+'/mobile') as r:self.assertEqual(r.status,200)
                if audience:
                    for path in ['/','/console','/api/course/archives','/api/course/export?id=x']:
                        with self.assertRaises(HTTPError) as cm:urlopen(base+path)
                        self.assertEqual(cm.exception.code,403)
                    with self.assertRaises(HTTPError) as cm:urlopen(Request(base+'/api/calibrate',data=b'{}'))
                    self.assertEqual(cm.exception.code,403)
                    with urlopen(Request(base+'/api/queue/submit',data=b'{"x":[0.2,0.3,0.4]}')) as r:self.assertTrue(json.load(r)['ok'])
                else:
                    with urlopen(base+'/') as r:self.assertIn('课程工作台',r.read().decode())
                    with self.assertRaises(HTTPError) as cm:urlopen(Request(base+'/api/calibrate',data=b'{}',headers={'Origin':'https://untrusted.example'}))
                    self.assertEqual(cm.exception.code,403)
        finally:
            for s in servers:s.shutdown();s.server_close()
if __name__=='__main__':unittest.main()
