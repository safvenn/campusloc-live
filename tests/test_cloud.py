import json
import threading
import time
import unittest
from unittest.mock import patch
from starlette.requests import Request
from fastapi import HTTPException
from live import (app, DeviceStore, RemoteScanReport, report_remote_scan,
                  web_user_heartbeat, WebUserPayload, public_result, devices, history)


def request():
    return Request({'type': 'http', 'headers': [(b'authorization', b'Bearer test')]})


class CloudTests(unittest.TestCase):
    def setUp(self):
        app.state.store = DeviceStore()
        app.state.latest = app.state.store.latest
        app.state.lock = threading.Lock()
        app.state.connected = False
        app.state.bundle = {'feature_names': ['aa:bb:cc:dd:ee:01']}

    def payload(self, device='phone', rssi=-50, age=0):
        return RemoteScanReport(device_id=device, display_name='Name', scan_id=str(time.time_ns()),
                                captured_at=time.time()-age, signals={'aa:bb:cc:dd:ee:01': rssi})

    @patch('live.db.record_live_heartbeat')
    @patch('live.db.decode_token', return_value={'sub': '1', 'name': 'Alice', 'role': 'user'})
    @patch('live.predict_signals', return_value={'predicted_floor': 'Ground'})
    def test_https_without_mqtt_and_duplicate(self, predict, decode, record):
        payload = self.payload()
        result = report_remote_scan(payload, request())
        self.assertEqual(result['connection_status'], 'live')
        self.assertEqual(result['display_name'], 'Alice')
        self.assertEqual(len(devices(request())['devices']), 1)
        with self.assertRaises(HTTPException) as cm: report_remote_scan(payload, request())
        self.assertEqual(cm.exception.status_code, 409)

    @patch('live.db.record_live_heartbeat')
    @patch('live.predict_signals', side_effect=lambda b,s: {'predicted_floor': 'Ground' if next(iter(s.values())) > -60 else 'Second'})
    def test_two_users_same_phone_id_are_isolated(self, predict, record):
        with patch('live.db.decode_token', return_value={'sub':'1','name':'Alice','role':'user'}):
            a=report_remote_scan(self.payload(rssi=-50), request())
        with patch('live.db.decode_token', return_value={'sub':'2','name':'Bob','role':'user'}):
            b=report_remote_scan(self.payload(rssi=-80), request())
            self.assertEqual([r['predicted_floor'] for r in devices(request())['devices']], ['Second'])
            with self.assertRaises(HTTPException) as cm: history(a['device_id'], request())
            self.assertEqual(cm.exception.status_code,403)
        self.assertNotEqual(a['device_id'],b['device_id'])
        self.assertEqual(a['predicted_floor'],'Ground')

    @patch('live.db.decode_token', return_value=None)
    def test_auth_required(self, decode):
        with self.assertRaises(HTTPException) as cm: report_remote_scan(self.payload(),request())
        self.assertEqual(cm.exception.status_code,401)

    @patch('live.db.decode_token', return_value={'sub':'1','name':'Alice','role':'user'})
    def test_stale_http_scan_rejected(self, decode):
        with self.assertRaises(HTTPException) as cm: report_remote_scan(self.payload(age=200),request())
        self.assertEqual(cm.exception.status_code,422)

    @patch('live.scan_and_predict', side_effect=AssertionError('Must not scan host'))
    @patch('live.db.decode_token', return_value=None)
    def test_browser_never_inherits_another_floor(self, decode, scan):
        app.state.latest['host']={'received_at':time.time(),'predicted_floor':'Fourth'}
        result=web_user_heartbeat(WebUserPayload(device_id='web-a',display_name='Visitor'),request())
        self.assertIsNone(result['predicted_floor'])
        self.assertEqual(result['connection_status'],'unlocated')
        self.assertNotIn('web-a',app.state.latest)


if __name__ == '__main__': unittest.main()
