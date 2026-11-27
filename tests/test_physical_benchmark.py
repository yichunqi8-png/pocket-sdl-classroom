import copy
import csv
import io
import json
import threading
import unittest
from unittest.mock import patch
import test_classroom
import test_reliability
from pocket_sdl import LabError
from pocket_sdl.config import load_config
from pathlib import Path

class PhysicalBenchmarkTests(unittest.TestCase):
    tearDown=test_classroom.ClassroomTests.tearDown
    wait=test_classroom.ClassroomTests.wait
    def setUp(self):
        test_classroom.ClassroomTests.setUp(self)
        test_classroom.ClassroomTests.prepare(self)
        self.e.device.close()
        self.sensor=test_reliability.RecordedSensor()
        self.e.device=self.sensor
        self.e.mode='PHYSICAL'
        self.e.config=load_config(Path(__file__).parents[1]/'configs/physical.json')

    def test_acquisition_prefix_counts_and_archive(self):
        arena=copy.deepcopy(self.e.arena)
        with patch('workshop.classroom.SimDevice', side_effect=AssertionError('must not simulate physical data')):
            self.e.benchmark_start([1,2],[6,8]);self.wait()
        b=self.e.course['benchmark']
        self.assertEqual(b['status'],'completed')
        self.assertEqual(b['mode'],'PHYSICAL')
        self.assertEqual(b['completed_measurements'],32)
        self.assertEqual(b['total_measurements'],32)
        self.assertEqual(len(self.sensor.calls),32)
        self.assertEqual(self.sensor.rgb,[0,0,0])
        self.assertEqual(self.e.arena,arena)
        self.assertEqual([r['strategy'] for r in b['runs']],['random','bo','bo','random'])
        for seed in [1,2]:
            runs=[r for r in b['runs'] if r['seed']==seed]
            self.assertEqual([r['x'] for r in runs[0]['rows'][:4]],[r['x'] for r in runs[1]['rows'][:4]])
            for run in runs:
                self.assertTrue(all(row['raw']==self.sensor.raw.tolist() for row in run['rows']))
        self.assertEqual(len(b['summary']),2)
        rows=list(csv.DictReader(io.StringIO(self.e.export(self.e.session_id,'csv').decode('utf-8-sig'))))
        physical=[r for r in rows if r['benchmark_id']==b['id']]
        self.assertEqual(len(physical),32)
        self.assertTrue(all(r['mode']=='PHYSICAL' for r in physical))
        self.assertEqual({r['strategy'] for r in physical},{'random','bo'})
        bid=b['id']
        self.e.finish_round()
        self.assertIsNone(self.e.course['benchmark'])
        self.assertIn(bid,self.e.export(self.e.session_id,'jsonl').decode())

    def test_stop_preserves_partial_data_no_summary(self):
        entered=threading.Event();release=threading.Event();original=self.e._measure_raw
        def delayed(*a,**kw):
            if len(self.sensor.calls)==1:
                entered.set();release.wait(2)
            return original(*a,**kw)
        self.e._measure_raw=delayed
        self.e.benchmark_start([1,2],[6]);self.assertTrue(entered.wait(2))
        self.e.led_off();release.set();self.wait()
        b=self.e.course['benchmark']
        self.assertEqual(b['status'],'interrupted')
        self.assertEqual(b['completed_measurements'],1)
        self.assertEqual(b['summary'],[])
        self.assertEqual(self.sensor.rgb,[0,0,0])
        self.assertIn(b['id'],self.e.export(self.e.session_id).decode())

    def test_sensor_failure_no_fake_samples(self):
        self.sensor.error=LabError('SERIAL_TIMEOUT','test timeout')
        self.e.benchmark_start([1,2],[6]);self.wait()
        b=self.e.course['benchmark']
        self.assertEqual(b['status'],'interrupted')
        self.assertEqual(b['completed_measurements'],0)
        self.assertEqual(b['runs'][0]['rows'],[])
        self.assertFalse(self.e.course['stability']['passed'])
        self.assertEqual(self.sensor.rgb,[0,0,0])

    def test_input_validation_and_connection(self):
        for seeds,budgets in [([], [6]),([1,1],[6]),([1,2],[]),([1,2],[37])]:
            with self.assertRaises(LabError):self.e.benchmark_start(seeds,budgets)
        self.e.device_connected=False
        with self.assertRaises(LabError):self.e.benchmark_start([1,2],[6])

class SimulationBenchmarkExportTests(unittest.TestCase):
    setUp=test_classroom.ClassroomTests.setUp
    tearDown=test_classroom.ClassroomTests.tearDown
    wait=test_classroom.ClassroomTests.wait
    def test_raw_export_and_target_change(self):
        test_classroom.ClassroomTests.prepare(self)
        self.e.benchmark_start([1,2],[6]);self.wait()
        bid=self.e.course['benchmark']['id']
        rows=list(csv.DictReader(io.StringIO(self.e.export(self.e.session_id,'csv').decode('utf-8-sig'))))
        self.assertEqual(len([r for r in rows if r['benchmark_id']==bid]),24)
        self.e.arena_new_target()
        self.assertIsNone(self.e.course['benchmark'])
        self.assertIn(bid,self.e.export(self.e.session_id,'jsonl').decode())
