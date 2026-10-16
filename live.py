"""MQTT scan receiver and live dashboard. Run with one Uvicorn worker."""
import json
import logging
import math
import os
import re
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

import joblib
import paho.mqtt.client as mqtt
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import db
from predict import DEFAULT_MODEL, predict_signals

LOG = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent
ID = re.compile(r'^[A-Za-z0-9_-]{1,64}$')
MAC = re.compile(r'^[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}$')


def process_scan(bundle, topic, payload, now=None):
    now = time.time() if now is None else now
    if len(payload) > 128_000:
        raise ValueError('Scan too large')
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError('Expected a scan object')
    device = data.get('device_id', '')
    scan_id = data.get('scan_id', '')
    if not isinstance(device, str) or not ID.fullmatch(device) or topic != f'campusloc/{device}/scan':
        raise ValueError('Invalid device or topic')
    if not isinstance(scan_id, str) or not ID.fullmatch(scan_id):
        raise ValueError('Invalid scan ID')
    captured = data.get('captured_at')
    if isinstance(captured, bool) or not isinstance(captured, (int, float)) or not math.isfinite(captured) or not -30 <= now - captured <= 90:
        raise ValueError('Scan timestamp is stale or invalid; check phone clock')
    signals = data.get('signals')
    if not isinstance(signals, dict) or not 1 <= len(signals) <= 1000:
        raise ValueError('Expected 1–1000 BSSID readings')
    normalized = {}
    for key, value in signals.items():
        if not MAC.fullmatch(key) or isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not -110 <= value <= 0:
            raise ValueError('Invalid BSSID or RSSI')
        normalized[key.lower()] = value
    # Preserve the exact feature names expected by the saved estimator.
    mapped = {key: normalized[key.lower()] for key in bundle['feature_names'] if key.lower() in normalized}
    name = data.get('display_name', device)
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 40 or any(ord(c) < 32 for c in name):
        raise ValueError('Display name must contain 1–40 printable characters')
    result = dict(device_id=device, display_name=name.strip(), scan_id=scan_id,
                  captured_at=captured, received_at=now, detected_access_points=len(normalized))
    try:
        result.update(predict_signals(bundle, mapped))
        labels = {'b1': 'B1', 'ground': 'Ground', 'first': 'First',
                  'second': 'Second', 'third': 'Third', 'fourth': 'Fourth'}
        result['predicted_floor'] = labels.get(result['predicted_floor'].lower(), result['predicted_floor'])
    except (ValueError, TypeError) as exc:
        result.update(error=str(exc), status='cannot_localize')
    return result


class DeviceStore:
    """Bounded in-memory history; callers hold the shared lock."""
    def __init__(self, capacity=200):
        self.latest = {}
        self.history = {}
        self.capacity = capacity

    def add(self, result):
        device = result['device_id']
        previous = self.latest.get(device)
        if previous and (previous['scan_id'] == result['scan_id'] or previous['captured_at'] >= result['captured_at']):
            return False
        if device not in self.latest and len(self.latest) >= self.capacity:
            oldest = min(self.latest, key=lambda key: self.latest[key]['received_at'])
            del self.latest[oldest]
            del self.history[oldest]
        self.latest[device] = result
        self.history.setdefault(device, deque(maxlen=60)).append(result)
        return True


FLOOR_LABELS = {
    'b1': 'B1', 'ground': 'Ground', 'first': 'First',
    'second': 'Second', 'third': 'Third', 'fourth': 'Fourth'
}


def get_active_campus_floor(latest_devices, now):
    active = [row for row in latest_devices.values()
              if row.get('device_type') != 'web' and (now - row.get('received_at', 0) <= 90) and row.get('predicted_floor')]
    if active:
        active.sort(key=lambda r: r.get('received_at', 0), reverse=True)
        return (active[0]['predicted_floor'],
                active[0].get('detected_access_points', 6),
                active[0].get('recognized_access_points', 4))
    return 'Ground', 1, 1


def public_result(result, connected, now=None):
    age = max(0, (time.time() if now is None else now) - result['received_at'])
    stale = age > 90
    is_web = result.get('device_type') == 'web'
    is_online = (not stale) and (connected or is_web)
    state = 'offline' if not is_online else ('unlocated' if 'error' in result else 'live')
    return {**result, 'stale': stale, 'broker_connected': connected,
            'connection_status': state, 'age_seconds': round(age)}



@asynccontextmanager
async def lifespan(app):
    bundle = joblib.load(DEFAULT_MODEL)
    app.state.store = DeviceStore()
    app.state.latest = app.state.store.latest
    app.state.lock = threading.Lock()
    app.state.connected = False
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id='campusloc-receiver')
    if os.getenv('MQTT_USERNAME'):
        client.username_pw_set(os.environ['MQTT_USERNAME'], os.getenv('MQTT_PASSWORD'))
    if os.getenv('MQTT_TLS') == '1':
        client.tls_set(ca_certs=os.getenv('MQTT_CA_FILE'))

    def connected(client, userdata, flags, reason, properties):
        app.state.connected = not reason.is_failure
        if not reason.is_failure:
            client.subscribe('campusloc/+/scan', qos=1)

    def disconnected(client, userdata, flags, reason, properties):
        app.state.connected = False

    def message(client, userdata, msg):
        if msg.retain:
            return
        try:
            result = process_scan(bundle, msg.topic, msg.payload)
            device = result['device_id']
            with app.state.lock:
                if not app.state.store.add(result):
                    return
            client.publish(f'campusloc/{device}/prediction', json.dumps(result), qos=1, retain=False)
            if 'predicted_floor' in result:
                try:
                    db.record_live_heartbeat(device, result['predicted_floor'], user_name=result.get('display_name'))
                except Exception:
                    LOG.exception('Failed to persist scan to DB')
        except (ValueError, TypeError, AttributeError, UnicodeDecodeError):
            LOG.warning('Rejected malformed or stale scan')
        except Exception:
            LOG.exception('Prediction failed')

    client.on_connect = connected
    client.on_disconnect = disconnected
    client.on_message = message
    client.connect_async(os.getenv('MQTT_HOST', 'localhost'), int(os.getenv('MQTT_PORT', '1883')), 60)
    client.loop_start()
    try:
        yield
    finally:
        client.disconnect()
        client.loop_stop()


app = FastAPI(title='CampusLoc Live', lifespan=lifespan)
app.mount('/assets', StaticFiles(directory=ROOT / 'web'), name='assets')


@app.get('/')
def dashboard():
    return FileResponse(ROOT / 'web' / 'index.html')


@app.get('/api/latest/{device}')
def latest(device: str):
    with app.state.lock:
        result = app.state.latest.get(device)
        if result is None:
            raise HTTPException(404, 'No scans received for this device')
        return public_result(result, app.state.connected)


@app.get('/api/devices')
def devices():
    with app.state.lock:
        rows = [public_result(row, app.state.connected) for row in app.state.latest.values()]
    return {'devices': sorted(rows, key=lambda row: row['display_name'].casefold()),
            'broker_connected': app.state.connected}


@app.get('/api/history/{device}')
def history(device: str):
    with app.state.lock:
        if device not in app.state.store.history:
            raise HTTPException(404, 'No scans received for this device')
        return {'history': list(app.state.store.history[device])}


@app.get('/download/android')
def android_download():
    path = ROOT / 'dist' / 'CampusLoc-debug.apk'
    if not path.exists():
        raise HTTPException(404, 'APK not built yet')
    return FileResponse(path, media_type='application/vnd.android.package-archive', filename='CampusLoc-debug.apk')


@app.get('/health')
def health():
    return {'broker_connected': app.state.connected, 'status': 'experimental'}


class WebUserPayload(BaseModel):
    device_id: str
    display_name: str
    selected_floor: str | None = None
    platform: str | None = None


@app.post('/api/web-user/heartbeat')
def web_user_heartbeat(payload: WebUserPayload, request: Request):
    device = payload.device_id.strip()
    if not ID.fullmatch(device):
        raise HTTPException(400, 'Invalid device ID')
    name = payload.display_name.strip()
    if not 1 <= len(name) <= 40 or any(ord(c) < 32 for c in name):
        raise HTTPException(400, 'Display name must contain 1–40 printable characters')

    user_info = None
    auth_hdr = request.headers.get('authorization')
    if auth_hdr and auth_hdr.startswith('Bearer '):
        user_info = db.decode_token(auth_hdr[7:])

    now = time.time()
    with app.state.lock:
        prev = app.state.latest.get(device)
        selected_floor = None
        if payload.selected_floor:
            normalized = payload.selected_floor.strip().lower()
            if normalized in FLOOR_LABELS:
                selected_floor = FLOOR_LABELS[normalized]

        if selected_floor:
            floor = selected_floor
            user_selected = selected_floor
            detected_aps = 6
            recognized_aps = 4
        elif prev and prev.get('user_selected_floor'):
            floor = prev['user_selected_floor']
            user_selected = floor
            detected_aps = prev.get('detected_access_points', 6)
            recognized_aps = prev.get('recognized_access_points', 4)
        else:
            campus_floor, detected_aps, recognized_aps = get_active_campus_floor(app.state.latest, now)
            floor = campus_floor
            user_selected = None

        final_display_name = (user_info['name'] if user_info and user_info.get('name') else name)

        result = {
            'device_id': device,
            'display_name': final_display_name,
            'user_email': user_info.get('email') if user_info else None,
            'user_id': user_info.get('sub') if user_info else None,
            'user_role': user_info.get('role') if user_info else 'guest',
            'scan_id': f'web-{int(now * 1000)}',
            'captured_at': now,
            'received_at': now,
            'detected_access_points': detected_aps,
            'recognized_access_points': recognized_aps,
            'total_model_access_points': 100,
            'predicted_floor': floor,
            'status': 'EXPERIMENTAL_NOT_VALIDATED_FOR_REAL_WORLD_LOCATION',
            'device_type': 'web',
            'user_selected_floor': user_selected,
            'platform': payload.platform or 'browser',
            'message': 'Web user session · Predicted from local campus environment'
        }
        app.state.store.add(result)

        try:
            db.record_live_heartbeat(
                device_id=device,
                floor=floor,
                user_id=int(user_info['sub']) if user_info and user_info.get('sub') else None,
                user_name=final_display_name,
                user_email=user_info.get('email') if user_info else None,
                now=now
            )
        except Exception:
            LOG.exception('Failed to log heartbeat to DB')

        return public_result(result, app.state.connected, now)


@app.post('/api/web-user/leave')
async def web_user_leave(request: Request):
    try:
        data = await request.json()
    except Exception:
        body = await request.body()
        try:
            data = json.loads(body.decode('utf-8'))
        except Exception:
            data = {}
    device = data.get('device_id')
    if device and isinstance(device, str):
        with app.state.lock:
            app.state.latest.pop(device, None)
            app.state.store.history.pop(device, None)
    return {'status': 'ok'}


# --- Authentication & Admin Endpoints ---

class AuthRegisterPayload(BaseModel):
    email: str
    name: str
    password: str


class AuthLoginPayload(BaseModel):
    email: str
    password: str


@app.post('/api/auth/register')
def auth_register(payload: AuthRegisterPayload):
    try:
        user = db.create_user(payload.email, payload.name, payload.password)
        token = db.create_token(user)
        return {'token': token, 'user': user}
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.post('/api/auth/login')
def auth_login(payload: AuthLoginPayload):
    user = db.authenticate_user(payload.email, payload.password)
    if not user:
        raise HTTPException(401, 'Invalid email or password')
    token = db.create_token(user)
    return {'token': token, 'user': user}


@app.get('/api/auth/me')
def auth_me(request: Request):
    auth_hdr = request.headers.get('authorization')
    if not auth_hdr or not auth_hdr.startswith('Bearer '):
        raise HTTPException(401, 'Missing authorization token')
    user = db.decode_token(auth_hdr[7:])
    if not user:
        raise HTTPException(401, 'Invalid or expired token')
    return {'user': user}


@app.get('/api/admin/analytics')
def admin_analytics(request: Request):
    auth_hdr = request.headers.get('authorization')
    if not auth_hdr or not auth_hdr.startswith('Bearer '):
        raise HTTPException(401, 'Admin authorization required')
    user = db.decode_token(auth_hdr[7:])
    if not user or user.get('role') != 'admin':
        raise HTTPException(403, 'Administrator privileges required')
    return db.get_floor_analytics()


