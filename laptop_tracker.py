"""Read Windows WLAN BSS RSSI values and publish complete scans to CampusLoc."""
import argparse
import ctypes as C
from ctypes import wintypes as W
import json
import time
import uuid
import paho.mqtt.client as mqtt


class GUID(C.Structure):
    _fields_ = [('data', C.c_ubyte * 16)]


class Interface(C.Structure):
    _fields_ = [('guid', GUID), ('description', W.WCHAR * 256), ('state', W.DWORD)]


class SSID(C.Structure):
    _fields_ = [('length', W.ULONG), ('value', C.c_ubyte * 32)]


class Rates(C.Structure):
    _fields_ = [('length', W.ULONG), ('rates', W.USHORT * 126)]


class BSS(C.Structure):
    _fields_ = [('ssid', SSID), ('phy_id', W.ULONG), ('mac', C.c_ubyte * 6),
                ('bss_type', C.c_int), ('phy_type', C.c_int), ('rssi', W.LONG),
                ('quality', W.ULONG), ('in_reg_domain', C.c_ubyte),
                ('beacon_period', W.USHORT), ('timestamp', C.c_ulonglong),
                ('host_timestamp', C.c_ulonglong), ('capability', W.USHORT),
                ('frequency', W.ULONG), ('rates', Rates),
                ('ie_offset', W.ULONG), ('ie_size', W.ULONG)]


class BSSList(C.Structure):
    _fields_ = [('size', W.DWORD), ('count', W.DWORD), ('entries', BSS * 1)]


def scan():
    wlan = C.WinDLL('wlanapi')
    wlan.WlanOpenHandle.argtypes = [W.DWORD, C.c_void_p, C.POINTER(W.DWORD), C.POINTER(W.HANDLE)]
    wlan.WlanEnumInterfaces.argtypes = [W.HANDLE, C.c_void_p, C.POINTER(C.c_void_p)]
    wlan.WlanScan.argtypes = [W.HANDLE, C.POINTER(GUID), C.c_void_p, C.c_void_p, C.c_void_p]
    wlan.WlanGetNetworkBssList.argtypes = [W.HANDLE, C.POINTER(GUID), C.c_void_p, C.c_int, W.BOOL, C.c_void_p, C.POINTER(C.c_void_p)]
    wlan.WlanFreeMemory.argtypes = [C.c_void_p]
    wlan.WlanCloseHandle.argtypes = [W.HANDLE, C.c_void_p]
    handle, version, interfaces = W.HANDLE(), W.DWORD(), C.c_void_p()
    def check(code):
        if code:
            raise OSError(code, 'Windows Wi-Fi API failed. Enable Wi-Fi and Windows Location access. ' + C.FormatError(code))
    check(wlan.WlanOpenHandle(2, None, C.byref(version), C.byref(handle)))
    try:
        check(wlan.WlanEnumInterfaces(handle, None, C.byref(interfaces)))
        count = C.cast(interfaces, C.POINTER(W.DWORD))[0]
        adapters = (Interface * count).from_address(interfaces.value + 8)
        signals = {}
        for adapter in adapters:
            started = time.time()
            check(wlan.WlanScan(handle, C.byref(adapter.guid), None, None, None))
            time.sleep(5)
            pointer = C.c_void_p()
            check(wlan.WlanGetNetworkBssList(handle, C.byref(adapter.guid), None, 3, False, None, C.byref(pointer)))
            try:
                header = C.cast(pointer, C.POINTER(BSSList)).contents
                entries = (BSS * header.count).from_address(pointer.value + BSSList.entries.offset)
                for entry in entries:
                    # Windows host timestamps are 100ns ticks since 1601.
                    observed = entry.host_timestamp / 10_000_000 - 11644473600
                    if observed < started - 2 or not -110 <= entry.rssi <= 0:
                        continue
                    mac = ':'.join(f'{part:02x}' for part in entry.mac)
                    signals[mac] = max(signals.get(mac, -110), entry.rssi)
            finally:
                wlan.WlanFreeMemory(pointer)
        return signals
    finally:
        if interfaces.value:
            wlan.WlanFreeMemory(interfaces)
        wlan.WlanCloseHandle(handle, None)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--name', default='My laptop')
    args = parser.parse_args()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id='campusloc-laptop-scanner')
    client.connect('localhost', 1883)
    client.loop_start()
    try:
        while True:
            signals = scan()
            if signals:
                payload = dict(device_id='laptop', display_name=args.name, scan_id=uuid.uuid4().hex,
                               captured_at=time.time(), signals=signals)
                client.publish('campusloc/laptop/scan', json.dumps(payload), qos=1).wait_for_publish(10)
                print(f'Sent {len(signals)} access points for device laptop', flush=True)
            else:
                print('No fresh Wi-Fi readings. Check Wi-Fi and Windows Location access.', flush=True)
            if args.once:
                break
            time.sleep(30)
    finally:
        client.disconnect()
        client.loop_stop()


if __name__ == '__main__':
    main()
