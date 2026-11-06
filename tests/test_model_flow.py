"""Tests for model.pkl prediction flow, WLAN network visibility, and preset simulation."""
import time
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
import live


class ModelFlowTests(unittest.TestCase):
    def setUp(self):
        self.user = {'id': 1001, 'name': 'Tester Alice', 'email': 'alice@campus.edu', 'role': 'user'}
        self.admin = {'id': 9999, 'name': 'Admin User', 'email': 'admin@campus.edu', 'role': 'admin'}

    def test_preset_endpoints(self):
        with TestClient(live.app) as client:
            res = client.get('/api/predict/presets')
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertIn('Ground', data['floors'])
            self.assertIn('B1', data['floors'])
            self.assertIn('First', data['floors'])

            # Test predicting Ground preset
            ground_res = client.post('/api/predict/preset/Ground')
            self.assertEqual(ground_res.status_code, 200)
            gdata = ground_res.json()
            self.assertTrue(gdata['success'])
            self.assertEqual(gdata['predicted_floor'], 'Ground')
            self.assertGreater(gdata['confidence'], 0.5)
            self.assertIn('uncalibrated_model_scores', gdata)
            self.assertIn('pipeline_steps', gdata)

            # Test predicting B1 preset
            b1_res = client.post('/api/predict/preset/B1')
            self.assertEqual(b1_res.status_code, 200)
            bdata = b1_res.json()
            self.assertTrue(bdata['success'])
            self.assertEqual(bdata['predicted_floor'], 'B1')

            # Test invalid preset
            inv_res = client.post('/api/predict/preset/Roof')
            self.assertEqual(inv_res.status_code, 404)

    def test_custom_scan_prediction_flow(self):
        with TestClient(live.app) as client:
            feature_names = live.app.state.bundle['feature_names']
            # Send top Ground floor APs
            signals = {feature_names[0]: -45.0, feature_names[1]: -50.0, feature_names[2]: -55.0}
            res = client.post('/api/predict/custom', json={'signals': signals})
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertTrue(data['success'])
            self.assertIn('predicted_floor', data)
            self.assertIn('confidence', data)
            self.assertIn('uncalibrated_model_scores', data)
            self.assertIn('recognized_access_points', data)

    def test_network_overview_public(self):
        with TestClient(live.app) as client:
            res = client.get('/api/network/overview')
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertIn('total_devices', data)
            self.assertIn('live_estimates', data)
            self.assertIn('floors', data)
            self.assertIn('Ground', data['floors'])
            self.assertIn('B1', data['floors'])

    def test_devices_scope_all(self):
        with patch('live.db.authenticate_user', return_value=self.user), \
             patch('live.db.record_live_heartbeat'), \
             TestClient(live.app) as client:
            # Login as regular user
            token = client.post('/api/auth/login', json={'email': 'alice@campus.edu', 'password': 'pw'}).json()['token']
            headers = {'Authorization': 'Bearer ' + token}

            # Add two devices to store: one belonging to user, one host scanner
            now = time.time()
            with live.app.state.lock:
                live.app.state.store.add({
                    'device_id': 'campus-host-scanner',
                    'display_name': 'Campus Scanner (Host Wi-Fi)',
                    'scan_id': 'host-1',
                    'captured_at': now,
                    'received_at': now,
                    'predicted_floor': 'Ground',
                    'connection_status': 'live',
                    'device_type': 'scanner',
                    'user_id': '9999'
                })
                live.app.state.store.add({
                    'device_id': 'user-1001-dev',
                    'display_name': 'Tester Alice',
                    'scan_id': 'alice-1',
                    'captured_at': now,
                    'received_at': now,
                    'predicted_floor': 'First',
                    'connection_status': 'live',
                    'device_type': 'phone',
                    'user_id': '1001'
                })

            # Default scope: user sees only their device
            user_devices = client.get('/api/devices', headers=headers).json()['devices']
            self.assertEqual(len(user_devices), 1)
            self.assertEqual(user_devices[0]['device_id'], 'user-1001-dev')

            # Scope all: user sees all network devices
            all_devices = client.get('/api/devices?scope=all', headers=headers).json()['devices']
            self.assertGreaterEqual(len(all_devices), 2)
            device_ids = [d['device_id'] for d in all_devices]
            self.assertIn('campus-host-scanner', device_ids)
            self.assertIn('user-1001-dev', device_ids)

    def test_admin_credentials_login(self):
        with TestClient(live.app) as client:
            res = client.post('/api/auth/login', json={'email': 'admin@campusloc.internal', 'password': 'admin123'})
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertIn('token', data)
            self.assertEqual(data['user']['role'], 'admin')
            self.assertEqual(data['user']['email'], 'admin@campusloc.internal')

            # Verify admin token allows accessing /api/admin/analytics
            auth_header = {'Authorization': f'Bearer {data["token"]}'}
            analytics_res = client.get('/api/admin/analytics', headers=auth_header)
            self.assertEqual(analytics_res.status_code, 200)
            analytics = analytics_res.json()
            self.assertIn('floor_summary', analytics)
            self.assertIn('user_breakdown', analytics)

            # Verify /api/auth/me returns admin
            me_res = client.get('/api/auth/me', headers=auth_header)
            self.assertEqual(me_res.status_code, 200)
            self.assertEqual(me_res.json()['user']['role'], 'admin')


if __name__ == '__main__':
    unittest.main()
