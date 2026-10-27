# CampusLoc 1.3: hosted operation

Phone Wi-Fi scan → authenticated HTTPS upload → hosted model → individual floor result.

No laptop, hotspot or MQTT broker is required for this flow. The phone needs internet connectivity and at least three campus BSSIDs recognized by the model. A browser cannot read the phone's Wi-Fi fingerprint.

## Deployment

Use the repository's Render Blueprint (`render.yaml`) or update the existing Python service:

- Build: `pip install -r requirements-live.txt`
- Start: `uvicorn live:app --host 0.0.0.0 --port $PORT`
- Environment: persistent random `JWT_SECRET`, PostgreSQL `DATABASE_URL`, `ENABLE_MQTT=0`, `ENABLE_HOST_SCANNER=0`.
- The Blueprint declares a free database; review provider availability and expiry before provisioning. SQLite accounts on ephemeral hosting do not survive redeploys.
- Optional initial administrator: configure a unique `ADMIN_INITIAL_PASSWORD` before first database initialization. No default administrator is created. Existing accounts are not overwritten; the old default admin password is blocked and needs a separate reset.
- Verify `/health` returns `version: 1.3` and `scan_transport: https`. `broker_connected: false` is normal.

The previously configured server URL is `https://campusloc-live.onrender.com`. This is not proof that it is currently running the updated code. Enter the actual HTTPS service URL in the app if it differs.

## Phone setup

1. Register an account on the hosted website.
2. Install `dist/CampusLoc-1.3-debug.apk`, or download from `/download/android` after deployment.
3. In Connection setup, enter the HTTPS server URL and your account email/password.
4. Enable Wi-Fi and Location, grant precise location permission and start tracking.
5. Keep the app visible. It displays the response to its own scan. Password and access token are not persisted.

Each account/device pair has a separate server ID and history. Normal accounts see their own devices; administrators can see all. Different Wi-Fi connections work if they reach the server. The model does not automatically support unfamiliar buildings. Free hosting may sleep; restart tracking if sign-in times out during wakeup.

## Changes and limits

- `/api/scan/report` now requires a bearer token, scan ID, capture timestamp and full signals mapping.
- Cloud host scanning and automatic laptop-to-cloud forwarding are disabled by default.
- Browser heartbeats do not generate location or attendance records.
- Duplicate/stale scans are rejected and HTTPS devices work without MQTT.
- Latest state/history is still bounded in-memory; it resets when the server restarts. Database session records are separate.
- Old database records produced by the shared-host-floor fallback are not repaired and are not verified location history.
- The floor model remains experimental. Physical-phone testing and independent accuracy validation are still required.

Run `python -m unittest discover -s tests -v` for regression tests, including independent users, ownership, stale/duplicate uploads and browser non-inheritance.
