import os
import time
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
import live


class HttpFlowTests(unittest.TestCase):
    def test_real_model_http_flow_without_broker(self):
        user={'id':7654321, 'name':'Synthetic test', 'email':'test@example.invalid', 'role':'user'}
        with patch.dict(os.environ, {'ENABLE_MQTT':'0','ENABLE_HOST_SCANNER':'0'}), \
             patch('live.db.authenticate_user',return_value=user), \
             patch('live.db.record_live_heartbeat'), patch('live.db.close_user_session'), \
             TestClient(live.app) as client:
            self.assertEqual(client.get('/health').json()['version'],'1.3')
            token=client.post('/api/auth/login',json={'email':user['email'],'password':'test'}).json()['token']
            headers={'Authorization':'Bearer '+token}
            signals={k:-65 for k in live.app.state.bundle['feature_names'][:5]}
            payload=dict(device_id='phone-one',display_name='Test',scan_id='http-scan',captured_at=time.time(),signals=signals)
            self.assertEqual(client.post('/api/scan/report',json=payload).status_code,401)
            response=client.post('/api/scan/report',headers=headers,json=payload)
            self.assertEqual(response.status_code,200,response.text)
            result=response.json()
            self.assertEqual(result['connection_status'],'live')
            self.assertEqual(result['recognized_access_points'],5)
            self.assertEqual(client.get('/api/devices',headers=headers).json()['devices'][0]['device_id'],result['device_id'])
            self.assertEqual(client.get('/api/devices').status_code,401)
            self.assertEqual(client.get('/api/history/'+result['device_id'],headers=headers).status_code,200)
            self.assertEqual(client.get('/download/android').content[:2],b'PK')
            payload['signals'][next(iter(signals))]=True
            self.assertEqual(client.post('/api/scan/report',headers=headers,json=payload).status_code,422)


if __name__=='__main__': unittest.main()
