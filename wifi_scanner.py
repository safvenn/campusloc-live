"""Host Wi-Fi Scanner and Floor Predictor using model.pkl.

Works on Windows (via Windows Native Wifi API + netsh) and Linux (via nmcli).
Scans nearby BSSIDs and runs them through the trained model bundle to predict floor.
"""
import ctypes
from ctypes import wintypes
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path
import joblib

LOG = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent

# Support model.pkl or wifi_floor_experimental.pkl
MODEL_PATHS = [
    ROOT / 'model.pkl',
    ROOT / 'outputs' / 'model.pkl',
    ROOT / 'outputs' / 'wifi_floor_experimental.pkl'
]


def load_model_bundle():
    for p in MODEL_PATHS:
        if p.exists():
            try:
                bundle = joblib.load(p)
                return bundle, str(p)
            except Exception as e:
                LOG.warning('Failed to load %s: %s', p, e)
    raise FileNotFoundError('No model.pkl or wifi_floor_experimental.pkl found')


def trigger_windows_wlan_scan():
    """Trigger a hardware scan on Windows using wlanapi.dll."""
    try:
        wlan = ctypes.windll.wlanapi
        client_handle = wintypes.HANDLE()
        negotiated_version = wintypes.DWORD()
        ret = wlan.WlanOpenHandle(2, None, ctypes.byref(negotiated_version), ctypes.byref(client_handle))
        if ret != 0:
            return False

        class GUID(ctypes.Structure):
            _fields_ = [
                ('Data1', wintypes.DWORD),
                ('Data2', wintypes.WORD),
                ('Data3', wintypes.WORD),
                ('Data4', ctypes.c_byte * 8)
            ]

        class WLAN_INTERFACE_INFO(ctypes.Structure):
            _fields_ = [
                ('InterfaceGuid', GUID),
                ('strInterfaceDescription', ctypes.c_wchar * 256),
                ('isState', wintypes.DWORD)
            ]

        class WLAN_INTERFACE_INFO_LIST(ctypes.Structure):
            _fields_ = [
                ('dwNumberOfItems', wintypes.DWORD),
                ('dwIndex', wintypes.DWORD),
                ('InterfaceInfo', WLAN_INTERFACE_INFO * 1)
            ]

        p_list = ctypes.POINTER(WLAN_INTERFACE_INFO_LIST)()
        ret = wlan.WlanEnumInterfaces(client_handle, None, ctypes.byref(p_list))
        if ret == 0 and p_list.contents.dwNumberOfItems > 0:
            guid = p_list.contents.InterfaceInfo[0].InterfaceGuid
            wlan.WlanScan(client_handle, ctypes.byref(guid), None, None, None)

        wlan.WlanCloseHandle(client_handle, None)
        return True
    except Exception as e:
        LOG.debug('WlanScan failed: %s', e)
        return False


def scan_wifi_networks(trigger_rescan=True):
    """Scan visible Wi-Fi access points and return dict of {bssid: rssi_in_dbm}."""
    signals = {}
    if sys.platform == 'win32':
        if trigger_rescan:
            trigger_windows_wlan_scan()
            time.sleep(1.2)  # Give adapter time to collect probe responses

        try:
            output = subprocess.check_output(
                'netsh wlan show networks mode=bssid',
                shell=True,
                stderr=subprocess.DEVNULL,
                timeout=6
            ).decode('utf-8', errors='ignore')

            curr_bssid = None
            for line in output.splitlines():
                line = line.strip()
                m_bssid = re.search(r'BSSID\s+\d+\s*:\s*([0-9a-fA-F:]{17})', line)
                if m_bssid:
                    curr_bssid = m_bssid.group(1).lower()
                    continue
                m_sig = re.search(r'Signal\s*:\s*(\d+)%', line)
                if m_sig and curr_bssid:
                    pct = int(m_sig.group(1))
                    dbm = round((pct / 2.0) - 100.0, 1)
                    signals[curr_bssid] = dbm
                    curr_bssid = None
        except Exception as e:
            LOG.warning('netsh scan failed: %s', e)

    elif sys.platform.startswith('linux'):
        try:
            if trigger_rescan:
                subprocess.run(['nmcli', 'dev', 'wifi', 'rescan'], timeout=5, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                time.sleep(1.0)
            output = subprocess.check_output(
                ['nmcli', '-t', '-f', 'BSSID,SIGNAL', 'dev', 'wifi'],
                stderr=subprocess.DEVNULL,
                timeout=5
            ).decode('utf-8', errors='ignore')
            for line in output.splitlines():
                parts = line.strip().split(':')
                if len(parts) >= 7:
                    bssid = ':'.join(parts[:6]).lower()
                    pct = int(parts[6])
                    signals[bssid] = round((pct / 2.0) - 100.0, 1)
        except Exception as e:
            LOG.warning('nmcli scan failed: %s', e)

    return signals


def scan_and_predict(bundle=None):
    """Scan local host Wi-Fi and predict floor using the ML model."""
    from predict import predict_signals

    if bundle is None:
        bundle, _ = load_model_bundle()

    signals = scan_wifi_networks(trigger_rescan=True)
    if not signals:
        return {
            'success': False,
            'error': 'No Wi-Fi signals detected on host machine. Make sure Wi-Fi is turned on.',
            'is_out_of_box': True,
            'predicted_floor': 'Out of Box',
            'recognized_access_points': 0,
            'detected_access_points': 0
        }

    try:
        prediction = predict_signals(bundle, signals, min_known=3)
        floor = prediction['predicted_floor']
        floor_map = {'b1': 'B1', 'ground': 'Ground', 'first': 'First', 'second': 'Second', 'third': 'Third', 'fourth': 'Fourth'}
        floor_formatted = floor_map.get(floor.lower(), floor.capitalize())
        prediction['predicted_floor'] = floor_formatted
        prediction['success'] = True
        prediction['is_out_of_box'] = False
        prediction['detected_access_points'] = len(signals)
        prediction['raw_signals'] = signals
        return prediction
    except ValueError as e:
        return {
            'success': False,
            'error': str(e),
            'is_out_of_box': True,
            'predicted_floor': 'Out of Box',
            'recognized_access_points': 0,
            'detected_access_points': len(signals),
            'raw_signals': signals
        }


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    bundle, path = load_model_bundle()
    print(f'Loaded model from: {path}')
    res = scan_and_predict(bundle)
    print('Prediction Result:')
    import json
    # Print without raw_signals for clean output
    res_clean = {k: v for k, v in res.items() if k != 'raw_signals'}
    print(json.dumps(res_clean, indent=2))
