import json
import time
import unittest
from unittest.mock import patch
from live import process_scan, DeviceStore, public_result


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.now = time.time()
        self.bundle = {'feature_names': ['AA:BB:CC:DD:EE:FF']}
        self.scan = dict(device_id='phone', scan_id='scan1', captured_at=self.now,
                         signals={'aa:bb:cc:dd:ee:ff': -55})

    def process(self):
        return process_scan(self.bundle, 'campusloc/phone/scan', json.dumps(self.scan).encode(), self.now)

    @patch('live.predict_signals', return_value={'predicted_floor': 'Ground'})
    def test_complete_scan_maps_model_case(self, predict):
        result = self.process()
        self.assertEqual(result['predicted_floor'], 'Ground')
        predict.assert_called_once_with(self.bundle, {'AA:BB:CC:DD:EE:FF': -55})

    def test_stale_scan_rejected(self):
        self.scan['captured_at'] -= 100
        with self.assertRaises(ValueError): self.process()

    def test_invalid_names_rejected(self):
        for name in ['', 'x' * 41, 'bad\nname', 42]:
            self.scan['display_name'] = name
            with self.assertRaises(ValueError): self.process()

    def test_status_excludes_stale_and_disconnected_estimates(self):
        row = {'received_at': 100, 'predicted_floor': 'Ground'}
        self.assertEqual(public_result(row, True, 150)['connection_status'], 'live')
        self.assertEqual(public_result(row, True, 200)['connection_status'], 'offline')
        self.assertEqual(public_result(row, False, 150)['connection_status'], 'offline')
        self.assertEqual(public_result({**row, 'error': 'unknown APs'}, True, 150)['connection_status'], 'unlocated')

    def test_independent_devices_history_and_duplicate_handling(self):
        store = DeviceStore(capacity=2)
        def row(device, stamp):
            return dict(device_id=device, scan_id=str(stamp), captured_at=stamp, received_at=stamp)
        self.assertTrue(store.add(row('a', 1)))
        self.assertTrue(store.add(row('b', 2)))
        self.assertFalse(store.add(row('a', 1)))
        self.assertFalse(store.add(row('b', 1)))
        self.assertEqual(len(store.history['a']), 1)
        for stamp in range(3, 70): store.add(row('a', stamp))
        self.assertEqual(len(store.history['a']), 60)
        store.add(row('c', 70))
        self.assertEqual(set(store.latest), {'a', 'c'})
        self.assertEqual(set(store.history), {'a', 'c'})

    def test_mismatched_device_rejected(self):
        self.scan['device_id'] = 'other'
        with self.assertRaises(ValueError): self.process()

    def test_invalid_readings_rejected(self):
        for value in [True, float('nan'), -111, 1, 'bad']:
            self.scan['signals']['aa:bb:cc:dd:ee:ff'] = value
            with self.assertRaises(ValueError): self.process()

    @patch('live.predict_signals', side_effect=ValueError('Cannot localize'))
    def test_unknown_scan_returns_error_without_floor(self, predict):
        result = self.process()
        self.assertEqual(result['status'], 'cannot_localize')
        self.assertNotIn('predicted_floor', result)


    def test_web_user_status_live_without_broker(self):
        row = {'received_at': 100, 'predicted_floor': 'Ground', 'device_type': 'web'}
        self.assertEqual(public_result(row, False, 150)['connection_status'], 'live')
        self.assertEqual(public_result(row, False, 200)['connection_status'], 'offline')


if __name__ == '__main__': unittest.main()

