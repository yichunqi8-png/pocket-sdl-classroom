import copy
import unittest
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
        for stage,passed in [('baseline',True),('drift',False),('recovery',True)]:
            self.e.reliability_start(stage);self.wait()
            r=self.e.course['reliability']['stages'][stage]
            self.assertEqual(r['status'],'completed')
            self.assertEqual(len(r['rows']),5)
            self.assertEqual(r['passed'],passed)
            self.assertEqual(self.e.course['fault'],'none')
            if not passed:
                with self.assertRaises(LabError):self.e._require_quality()
        self.assertEqual(self.e.arena,arena)
        self.e._require_quality()
        self.e.calibrate()
        with self.assertRaises(LabError):self.e.reliability_start('drift')
        self.e.mode='PHYSICAL'
        with self.assertRaises(LabError):self.e.reliability_start('baseline')
