import copy
import json
import threading
import unittest
import numpy as np
import test_classroom
from pocket_sdl import LabError

class ReliabilityTests(unittest.TestCase):
    setUp=test_classroom.ClassroomTests.setUp
    tearDown=test_classroom.ClassroomTests.tearDown
    wait=test_classroom.ClassroomTests.wait
    prepare=test_classroom.ClassroomTests.prepare
    def test_three_stage_comparison(self):
        self.prepare()
        arena=copy.deepcopy(self.e.arena)
        with self.assertRaises(LabError):self.e.reliability_start('drift')
        quality=copy.deepcopy(self.e.course['stability'])
        for stage,passed in [('baseline',True),('drift',True),('recovery',True)]:
            self.e.reliability_start(stage);self.wait()
            r=self.e.course['reliability']['stages'][stage]
            self.assertEqual(r['status'],'completed')
            self.assertEqual(len(r['rows']),5)
            self.assertEqual(r['passed'],passed)
            self.assertEqual(self.e.course['fault'],'none')
            self.assertEqual(self.e.course['stability'],quality)
        self.assertEqual(self.e.arena,arena)
        self.e._require_quality()
        self.e.calibrate()
        with self.assertRaises(LabError):self.e.reliability_start('drift')
        self.e.mode='PHYSICAL'
        with self.assertRaises(LabError):self.e.reliability_start('drift')

    def test_filter_spectrum_and_recovery(self):
        self.prepare()
        for color,peak in [('red',7),('green',3),('blue',0)]:
            for stage in ['baseline','drift','recovery']:
                self.e.reliability_start(stage,color_filter=color);self.wait()
            stages=self.e.course['reliability']['stages']
            dark=np.asarray(self.e.calibration['dark_mean'])
            base=np.asarray(stages['baseline']['mean_raw'])-dark
            filtered=np.asarray(stages['drift']['mean_raw'])-dark
            ratio=filtered/base
            self.assertGreater(ratio[peak],.8)
            self.assertLess(float(min(ratio)),.15)
            np.testing.assert_allclose(stages['recovery']['mean_raw'],stages['baseline']['mean_raw'],rtol=.03,atol=3)
        with self.assertRaises(LabError):self.e.reliability_start(color_filter='invalid')

    def test_cancel_filter_and_archive(self):
        self.prepare()
        self.e.reliability_start('baseline');self.wait()
        entered=threading.Event();release=threading.Event();original=self.e._measure_raw
        def delayed(*args, **kwargs):
            entered.set();release.wait(2)
            return original(*args, **kwargs)
        self.e._measure_raw=delayed
        self.e.reliability_start('drift', color_filter='blue')
        self.assertTrue(entered.wait(2))
        self.e.led_off();release.set();self.wait()
        self.assertEqual(self.e.course['reliability']['stages']['drift']['status'],'interrupted')
        self.assertEqual(self.e.device.rgb,[0,0,0])
        with self.assertRaises(LabError):self.e.reliability_start('recovery')
        self.e._measure_raw=original
        self.e.reliability_start('drift', color_filter='green');self.wait()
        self.e.reliability_start('recovery');self.wait()
        doc=json.loads(self.e.export(self.e.session_id))
        self.assertEqual(doc['course']['reliability']['color_filter'],'green')
        csv=self.e.export(self.e.session_id,'csv').decode()
        self.assertIn('color_filter',csv)
        self.assertIn(',green',csv)
        self.e.calibrate()
        with self.assertRaises(LabError):self.e.reliability_start('recovery')


class RecordedSensor:
    """Test-only device stand-in: no simulation fields or optical model."""
    __slots__ = ('raw', 'rgb', 'error', 'calls')
    def __init__(self):
        self.raw = np.array([120, 240, 360, 480, 600, 720, 840, 960])
        self.rgb = [0,0,0]
        self.error = None
        self.calls = []
    def measure(self, x):
        self.calls.append(list(x)); self.rgb = list(x)
        if self.error: raise self.error
        return self.raw.copy()
    def off(self): self.rgb = [0,0,0]; return True
    def close(self): self.off()


class PhysicalReliabilityTests(unittest.TestCase):
    tearDown=test_classroom.ClassroomTests.tearDown
    wait=test_classroom.ClassroomTests.wait
    def setUp(self):
        test_classroom.ClassroomTests.setUp(self)
        self.e.calibrate()
        self.e.device.close()
        self.sensor=RecordedSensor()
        self.e.device=self.sensor
        self.e.mode='PHYSICAL'

    def test_real_readings_unmodified_and_exported(self):
        # The device returns the observations; color names must never transform them.
        for stage, raw in [('baseline',[1000]*8),('drift',[153,287,394,481,579,613,748,892]),('recovery',[998]*8)]:
            self.sensor.raw=np.array(raw)
            self.e.reliability_start(stage,color_filter='blue');self.wait()
            report=self.e.course['reliability']['stages'][stage]
            self.assertEqual(report['status'],'completed')
            self.assertEqual(report['mean_raw'],raw)
            self.assertTrue(all(r['raw']==raw for r in report['rows']))
            self.assertEqual(report['mode'],'PHYSICAL')
            self.assertEqual(self.sensor.rgb,[0,0,0])
        self.assertEqual(self.sensor.calls,[[.5,.5,.5]]*15)
        doc=json.loads(self.e.export(self.e.session_id))
        self.assertIsNone(doc['course']['reliability']['model'])
        self.assertEqual(doc['course']['reliability']['mode'],'PHYSICAL')
        csv=self.e.export(self.e.session_id,'csv').decode()
        self.assertIn('PHYSICAL',csv)
        self.assertIn(',blue,drift',csv)
        self.e.mode='SIMULATED'
        with self.assertRaises(LabError):self.e.reliability_start('recovery')

    def test_sensor_errors_stop_without_fabricating_data(self):
        for code in ['SERIAL_TIMEOUT','DISCONNECTED']:
            self.sensor.error=LabError(code,'injected device error')
            self.e.reliability_start('baseline');self.wait()
            report=self.e.course['reliability']['stages']['baseline']
            self.assertEqual(report['status'],'interrupted')
            self.assertEqual(report['rows'],[])
            self.assertNotIn('mean_raw',report)
            self.assertFalse(report['passed'])
            self.assertEqual(self.sensor.rgb,[0,0,0])
            self.assertEqual(self.e.last_error['code'],code)
        self.sensor.error=None
        self.sensor.raw=np.array([60000]*8)
        self.e.reliability_start('baseline');self.wait()
        self.assertEqual(self.e.course['reliability']['stages']['baseline']['rows'],[])
        self.assertEqual(self.sensor.rgb,[0,0,0])
        self.e.device_connected=False
        with self.assertRaises(LabError):self.e.reliability_start('baseline')

    def test_stop_and_recalibration_invalidate_sequence(self):
        entered=threading.Event(); release=threading.Event(); original=self.e._measure_raw
        def delayed(*a,**kw):
            entered.set();release.wait(2);return original(*a,**kw)
        self.e._measure_raw=delayed
        self.e.reliability_start('baseline')
        self.assertTrue(entered.wait(2))
        self.e.led_off();release.set();self.wait()
        self.assertEqual(self.e.course['reliability']['stages']['baseline']['status'],'interrupted')
        self.assertEqual(self.sensor.rgb,[0,0,0])
        with self.assertRaises(LabError):self.e.reliability_start('drift')
        self.e._measure_raw=original
        self.e.reliability_start('baseline');self.wait()
        self.e.calibration['id']='changed-calibration'
        with self.assertRaises(LabError):self.e.reliability_start('drift')
