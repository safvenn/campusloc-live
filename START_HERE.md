# CampusLoc Android + live tracking

## Version 1.1: multi-device dashboard

Open the website root to see all participating devices, floor counts, name/ID search, status filters, and the latest 60 estimates per device. Clicking a device opens its detail view; existing `?device=laptop` links still work. Only fresh, located devices count toward floor totals. A disconnected broker or an estimate older than 90 seconds removes a device from live counts. Names are self-reported, not verified identities.

The Android app now supports a saved nickname, copying its device ID, and opening the dashboard. Opening a browser leaves the app and stops phone scanning; keep the dashboard open on the laptop when tracking phones. Install the new APK over version 1.0 to retain the device ID and broker settings.

To join through a laptop hotspot, enable Windows Mobile hotspot, connect the phone, and use the laptop's hotspot adapter IPv4 address in the broker URI. Find it with `ipconfig` (do not assume the normal Wi-Fi adapter address is the hotspot address). From the phone open `http://LAPTOP_HOTSPOT_IP:8000` and choose **Connect a device** to download the APK. Both ports 1883 and 8000 must be reachable on that private network. Wi-Fi client isolation on some campus networks can prevent direct device connections.

Connecting to the hotspot alone never tracks a person. Each participant must install the app, grant permission and start tracking. The UI explains sharing; leaving the screen stops new scans. Past estimates remain in server memory until restart or eviction. Maximum 200 devices and 60 scans per device; no raw scan history is retained by the receiver. This is a private-network development setup without dashboard login or verified identities, not a production people-tracking service.

Laptop scanning: `.\.venv\Scripts\python.exe laptop_tracker.py --name "My laptop"`. Scans continue roughly every 35 seconds while that process runs. Stop it with Ctrl+C when running in a terminal.

The native Android app scans BSSID/RSSI readings, publishes a complete scan over MQTT, and displays the model's reply. The Python receiver uses your existing `.pkl` bundle. A browser dashboard shows the latest result for a selected phone.

## 1. Start a broker

With Docker Desktop installed and running, from this directory:

```powershell
docker compose up -d
```

Alternatively install Mosquitto and run `mosquitto -c broker/mosquitto.conf`.
The provided broker configuration allows anonymous access for private LAN testing only. Do not expose ports 1883 or 8000 to the public internet. For deployment configure MQTT authentication, per-device topic ACLs, TLS, and authenticated HTTPS access to the dashboard. The dashboard currently has no authentication.

## 2. Start the receiver and website

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-live.txt
.\.venv\Scripts\python.exe -m uvicorn live:app --host 0.0.0.0 --port 8000
```

Use one worker. Latest results are held in memory and reset on restart. Default broker is `localhost:1883`. Optional environment variables: `MQTT_HOST`, `MQTT_PORT`, `MQTT_USERNAME`, `MQTT_PASSWORD`, `MQTT_TLS=1`, `MQTT_CA_FILE`. TLS uses certificate verification.

Open http://localhost:8000 on this computer. Enter the device ID displayed in the Android app. The page polls every three seconds and expires estimates after 90 seconds. Open `http://COMPUTER_LAN_IP:8000` from another device on the same LAN. Permit inbound ports 1883 and 8000 in Windows Firewall for your private network if needed.

## 3. Build and install Android

Open the `android` folder in Android Studio. Install Android SDK 35 and select JDK 17 for Gradle. The project uses Android Gradle Plugin 8.9.2 and Gradle 8.11.1. Sync, connect a physical Android phone with USB debugging enabled, then click Run. Minimum Android version is 8.0. Wi-Fi scan behavior needs a physical phone for validation.

To create a debug APK, use Android Studio's Build APK action or:

```powershell
cd android
.\gradlew.bat assembleDebug
```

APK output: `android/app/build/outputs/apk/debug/app-debug.apk`.

In the app:

1. Enter `tcp://COMPUTER_LAN_IP:1883` (not `localhost`). Use `ssl://HOST:8883` for a TLS broker with a trusted certificate.
2. Enter credentials if your broker requires them. Passwords are not saved.
3. Enable Wi-Fi and phone Location, grant precise location permission, and tap **Start tracking**.
4. Keep the app visible. It scans every 35 seconds; Android can throttle or reject scans. Leaving the app stops tracking. Return and tap Start to resume.
5. Copy the displayed device ID into the website.

## Message contract

Publish QoS 1, non-retained JSON to `campusloc/{device_id}/scan`:

```json
{
  "device_id": "phone-1",
  "scan_id": "unique-scan-id",
  "captured_at": 1792100000.0,
  "signals": {"aa:bb:cc:dd:ee:ff": -61}
}
```

`captured_at` is the actual scan time in Unix seconds; the example must be replaced. Each message contains one complete scan, so the receiver never combines different devices or scan intervals. This is a custom app protocol, not a parser for the separate Network Survey app's MQTT format. Replies use `campusloc/{device_id}/prediction` and include the matching `scan_id`, timestamps, and either `predicted_floor` or `error`. The model needs at least three recognized BSSIDs; unfamiliar networks produce an error, not a fabricated floor.

Malformed, retained, stale, duplicate and out-of-order scans are discarded. Device clocks must be synchronized. Latest results are limited to 200 devices; the oldest device is evicted at capacity.

## Verification

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The saved model has contradictory training labels. Its outputs remain experimental; the UI does not present uncalibrated scores as location confidence. See README.md for the model audit and data repair steps.
