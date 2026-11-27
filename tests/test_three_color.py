import tempfile,unittest,json
from pocket_sdl.device import PicoSpectroDevice
from pocket_sdl.three_color import ThreeColorSession
class Tests(unittest.TestCase):
 def test_complete(self):
  with tempfile.TemporaryDirectory() as d:
   s=ThreeColorSession(PicoSpectroDevice(mode='SIMULATED'),d);s.connect();s.doctor()
   for rgb in [(1,0,0),(0,1,0),(0,0,1)]:s.measure(*rgb)
   s.save();self.assertTrue(s.finish()['success']);self.assertFalse(s.driver.connected);self.assertEqual(len(json.loads((s.path/'record.json').read_text(encoding='utf-8'))['rows']),3)
 def test_failure(self):
  with tempfile.TemporaryDirectory() as d:
   s=ThreeColorSession(PicoSpectroDevice(mode='SIMULATED'),d);s.connect();s.doctor()
   with self.assertRaises(Exception):s.measure(9,0,0)
   self.assertFalse(s.driver.connected);self.assertEqual(json.loads((s.path/'record.json').read_text(encoding='utf-8'))['status'],'failed')
 def test_finish_and_save_before_connect(self):
  with tempfile.TemporaryDirectory() as d:
   s=ThreeColorSession(PicoSpectroDevice(mode='SIMULATED'),d)
   res=s.finish()
   self.assertFalse(res['success'])
   self.assertIsNone(res['data']['directory'])
   with self.assertRaises(RuntimeError):s.save()
