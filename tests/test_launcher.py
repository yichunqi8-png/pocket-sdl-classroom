import unittest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
from pocket_sdl import LabError
from pocket_sdl.config import load_config
from workshop import launcher as l


def port(name, serial='board', vid=0x239A, description='Pico'):
    return SimpleNamespace(device=name, serial_number=serial, vid=vid, description=description, hwid='')


class LauncherTests(unittest.TestCase):
    def test_bluetooth_not_probed(self):
        with patch.object(l.list_ports, 'comports', return_value=[port('BT', vid=None, description='Bluetooth'), port('COM3')]):
            self.assertEqual([p.device for p in l.pico_ports()], ['COM3'])

    def test_groups_dual_cdc_and_explicit_board(self):
        rows = [port('COM3'), port('COM4'), port('COM5', 'other')]
        self.assertEqual(len(l.select_board(rows, 'board')), 2)
        self.assertEqual(l.select_board(rows, ask=lambda _: '2')[0].serial_number, 'other')
        with self.assertRaises(LabError):
            l.select_board(rows, 'missing')
        with self.assertRaises(LabError):
            l.select_board([])

    def test_probe_closes_on_success_and_rejects_bad_raw(self):
        config = load_config(l.REPO_ROOT / 'configs/physical.json')
        dev = MagicMock()
        dev.measure.return_value = [10] * 8
        with patch.object(l, 'SerialDevice', return_value=dev):
            self.assertEqual(l.probe([port('COM4')], config), 'COM4')
        dev.close.assert_called_once()
        dev.reset_mock()
        dev.measure.return_value = [60000] * 8
        with patch.object(l, 'SerialDevice', return_value=dev):
            with self.assertRaises(LabError):
                l.probe([port('COM4')], config)
        dev.close.assert_called_once()

    def test_busy_port_never_probes_device(self):
        with patch.object(l, 'ThreadingHTTPServer', side_effect=OSError()), patch.object(l, 'existing', return_value=False), patch.object(l, 'pico_ports') as scan:
            self.assertEqual(l.main(['--no-open']), 1)
            scan.assert_not_called()

    def test_repeated_launch_reuses_physical_service(self):
        with patch.object(l, 'ThreadingHTTPServer', side_effect=OSError()), patch.object(l, 'existing', return_value=True), patch.object(l, 'pico_ports') as scan:
            self.assertEqual(l.main(['--no-open']), 0)
            scan.assert_not_called()
