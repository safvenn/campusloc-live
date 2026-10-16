"""Ultra-fast, zero-lag Wi-Fi Scanner and Floor Predictor using model.pkl.

Uses Windows Native Wi-Fi API (wlanapi.dll) to query Wi-Fi BSSID lists directly
from the network adapter in memory (~15ms response time, zero subprocess lag).
Falls back to netsh / nmcli if Native API is unavailable.
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
import pandas as pd

LOG = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent

MODEL_PATHS = [
    ROOT / 'model.pkl',
    ROOT / 'outputs' / 'model.pkl',
    ROOT / 'outputs' / 'wifi_floor_experimental.pkl'
]

_CACHED_BUNDLE = None
_CACHED_MODEL_PATH = None


def load_model_bundle():
    global _CACHED_BUNDLE, _CACHED_MODEL_PATH
    if _CACHED_BUNDLE is not None:
        return _CACHED_BUNDLE, _CACHED_MODEL_PATH
    for p in MODEL_PATHS:
        if p.exists():
            try:
                _CACHED_BUNDLE = joblib.load(p)
                _CACHED_MODEL_PATH = str(p)
                return _CACHED_BUNDLE, _CACHED_MODEL_PATH
            except Exception as e:
                LOG.warning('Failed to load %s: %s', p, e)
    raise FileNotFoundError('No model.pkl or wifi_floor_experimental.pkl found')


# Windows Native Wi-Fi structures
if sys.platform == 'win32':
    class DOT11_SSID(ctypes.Structure):
        _fields_ = [('uSSIDLength', wintypes.ULONG), ('ucSSID', ctypes.c_char * 32)]

    class WLAN_RATE_SET(ctypes.Structure):
        _fields_ = [('uRateSetLength', wintypes.ULONG), ('usRates', wintypes.USHORT * 126)]

    class WLAN_BSS_ENTRY(ctypes.Structure):
        _fields_ = [
            ('dot11Ssid', DOT11_SSID),
            ('uPhyId', wintypes.ULONG),
            ('dot11Bssid', ctypes.c_ubyte * 6),
            ('dot11BssType', wintypes.ULONG),
            ('dot11BssPhyType', wintypes.ULONG),
            ('lRssi', ctypes.c_long),
            ('uLinkQuality', wintypes.ULONG),
            ('bInRegDomain', wintypes.BOOLEAN),
            ('usBeaconPeriod', wintypes.USHORT),
            ('ullTimestamp', ctypes.c_ulonglong),
            ('ullHostTimestamp', ctypes.c_ulonglong),
            ('usCapabilityInformation', wintypes.USHORT),
            ('ulChCenterFrequency', wintypes.ULONG),
            ('wlanRateSet', WLAN_RATE_SET),
            ('ulIeOffset', wintypes.ULONG),
            ('ulIeSize', wintypes.ULONG),
        ]

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

    class WLAN_BSS_LIST(ctypes.Structure):
        _fields_ = [
            ('dwTotalSize', wintypes.DWORD),
            ('dwNumberOfItems', wintypes.DWORD),
            ('wlanBssEntries', WLAN_BSS_ENTRY * 1)
        ]


_last_hardware_scan_time = 0.0


def fast_scan_wifi():
    """Retrieve visible Wi-Fi access points in ~10-15ms using Native Wi-Fi API."""
    global _last_hardware_scan_time
    signals = {}
    now = time.time()

    if sys.platform == 'win32':
        try:
            wlan = ctypes.windll.wlanapi
            handle = wintypes.HANDLE()
            ver = wintypes.DWORD()
            if wlan.WlanOpenHandle(2, None, ctypes.byref(ver), ctypes.byref(handle)) != 0:
                return _fallback_scan()

            p_list = ctypes.POINTER(WLAN_INTERFACE_INFO_LIST)()
            if wlan.WlanEnumInterfaces(handle, None, ctypes.byref(p_list)) != 0 or p_list.contents.dwNumberOfItems == 0:
                wlan.WlanCloseHandle(handle, None)
                return _fallback_scan()

            guid = p_list.contents.InterfaceInfo[0].InterfaceGuid

            # Trigger a background hardware refresh every 8 seconds if needed
            if now - _last_hardware_scan_time >= 8.0:
                _last_hardware_scan_time = now
                wlan.WlanScan(handle, ctypes.byref(guid), None, None, None)

            p_bss = ctypes.POINTER(WLAN_BSS_LIST)()
            ret = wlan.WlanGetNetworkBssList(handle, ctypes.byref(guid), None, 1, 0, None, ctypes.byref(p_bss))
            if ret == 0 and p_bss:
                count = p_bss.contents.dwNumberOfItems
                raw_ptr = ctypes.addressof(p_bss.contents) + 8
                entry_size = ctypes.sizeof(WLAN_BSS_ENTRY)

                for i in range(count):
                    entry = WLAN_BSS_ENTRY.from_address(raw_ptr + i * entry_size)
                    mac = ':'.join(f'{b:02x}' for b in entry.dot11Bssid)
                    rssi = float(entry.lRssi)
                    if -110.0 <= rssi <= 0.0:
                        signals[mac] = rssi

                wlan.WlanFreeMemory(p_bss)

            wlan.WlanCloseHandle(handle, None)
            if signals:
                return signals
        except Exception as e:
            LOG.debug('Native fast Wi-Fi scan exception: %s', e)

    return _fallback_scan()


def _fallback_scan():
    """Fallback scanner using netsh (Windows) or nmcli (Linux)."""
    signals = {}
    if sys.platform == 'win32':
        try:
            output = subprocess.check_output(
                'netsh wlan show networks mode=bssid',
                shell=True,
                stderr=subprocess.DEVNULL,
                timeout=4
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
        except Exception:
            pass
    elif sys.platform.startswith('linux'):
        try:
            output = subprocess.check_output(
                ['nmcli', '-t', '-f', 'BSSID,SIGNAL', 'dev', 'wifi'],
                stderr=subprocess.DEVNULL,
                timeout=3
            ).decode('utf-8', errors='ignore')
            for line in output.splitlines():
                parts = line.strip().split(':')
                if len(parts) >= 7:
                    bssid = ':'.join(parts[:6]).lower()
                    pct = int(parts[6])
                    signals[bssid] = round((pct / 2.0) - 100.0, 1)
        except Exception:
            pass
    return signals


scan_wifi_networks = fast_scan_wifi


def scan_and_predict(bundle=None):
    """Scan local host Wi-Fi with ultra-low latency (~15ms) and predict floor via model.pkl."""
    t0 = time.time()
    if bundle is None:
        bundle, _ = load_model_bundle()

    signals = fast_scan_wifi()
    if not signals:
        return {
            'success': False,
            'error': 'No Wi-Fi signals detected. Ensure Wi-Fi adapter is active.',
            'is_out_of_box': True,
            'predicted_floor': 'Out of Box',
            'recognized_access_points': 0,
            'detected_access_points': 0,
            'latency_ms': round((time.time() - t0) * 1000, 1)
        }

    cols = bundle['feature_names']
    readings = {}
    for mac, rssi in signals.items():
        if mac in cols:
            readings[mac] = rssi

    if len(readings) < 3:
        return {
            'success': False,
            'error': f'Only {len(readings)} recognized campus BSSIDs found; minimum 3 required.',
            'is_out_of_box': True,
            'predicted_floor': 'Out of Box',
            'recognized_access_points': len(readings),
            'detected_access_points': len(signals),
            'latency_ms': round((time.time() - t0) * 1000, 1)
        }

    vector = pd.DataFrame([{col: readings.get(col, -110.0) for col in cols}], columns=cols)
    model = bundle['model']
    raw_label = str(model.predict(vector)[0])

    floor_map = {
        'b1': 'B1', 'ground': 'Ground', 'first': 'First',
        'second': 'Second', 'third': 'Third', 'fourth': 'Fourth'
    }
    predicted_floor = floor_map.get(raw_label.lower(), raw_label.capitalize())

    uncalibrated = {}
    if hasattr(model, 'predict_proba'):
        probs = model.predict_proba(vector)[0]
        uncalibrated = {str(k): round(float(v), 4) for k, v in zip(model.classes_, probs)}

    elapsed_ms = round((time.time() - t0) * 1000, 1)
    return {
        'success': True,
        'predicted_floor': predicted_floor,
        'status': bundle.get('status', 'EXPERIMENTAL_NOT_VALIDATED_FOR_REAL_WORLD_LOCATION'),
        'recognized_access_points': len(readings),
        'total_model_access_points': len(cols),
        'detected_access_points': len(signals),
        'uncalibrated_model_scores': uncalibrated,
        'signals': signals,
        'latency_ms': elapsed_ms,
        'is_out_of_box': False,
        'message': f"Real-time prediction from model.pkl ({len(readings)} APs matched in {elapsed_ms}ms)"
    }


if __name__ == '__main__':
    bundle, path = load_model_bundle()
    res = scan_and_predict(bundle)
    import json
    print(json.dumps(res, indent=2))
