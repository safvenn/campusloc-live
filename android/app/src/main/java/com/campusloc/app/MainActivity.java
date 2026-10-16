package com.campusloc.app;

import android.Manifest;
import android.app.Activity;
import android.content.*;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.net.wifi.*;
import android.os.*;
import android.text.InputType;
import android.view.*;
import android.widget.*;
import org.eclipse.paho.client.mqttv3.*;
import org.eclipse.paho.client.mqttv3.persist.MemoryPersistence;
import org.json.JSONObject;
import java.nio.charset.StandardCharsets;
import java.util.*;

public class MainActivity extends Activity {
    private final Handler handler = new Handler(Looper.getMainLooper());
    private WifiManager wifi;
    private MqttAsyncClient client;
    private EditText broker, username, password, displayName;
    private String activeName;
    private TextView status, floor, details, networks;
    private TextView freshness, accessPoints, connectionStatus;
    private LinearLayout dashboardPanel, settingsPanel;
    private final Map<String, TextView> floorRows = new LinkedHashMap<>();
    private long lastEstimateAt;
    private final Runnable ageTick = new Runnable() { public void run() {
        if (lastEstimateAt > 0) freshness.setText("Updated " + ((SystemClock.elapsedRealtime() - lastEstimateAt) / 1000) + "s ago");
        if (running) handler.postDelayed(this, 1000);
    } };
    private Button start;
    private boolean running, waiting;
    private String deviceId, latestScan;
    private long requestedAt;
    private final Runnable loop = new Runnable() { public void run() { scan(); if (running) handler.postDelayed(this, 35000); } };
    private final Runnable timeout = () -> { if (waiting) { waiting = false; status.setText("Scan timed out. Retrying on next interval."); } };
    private final Runnable stale = () -> clearLocation("Estimate expired. Waiting for a fresh result.");
    private final BroadcastReceiver receiver = new BroadcastReceiver() {
        @Override public void onReceive(Context context, Intent intent) {
            if (!running || !waiting) return;
            waiting = false; handler.removeCallbacks(timeout);
            if (!intent.getBooleanExtra(WifiManager.EXTRA_RESULTS_UPDATED, false)) { status.setText("Scan throttled or unavailable; waiting for next scan."); return; }
            publishScan();
        }
    };
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        wifi = (WifiManager) getApplicationContext().getSystemService(WIFI_SERVICE);
        SharedPreferences prefs = getSharedPreferences("campusloc", MODE_PRIVATE);
        deviceId = prefs.getString("device", UUID.randomUUID().toString());
        prefs.edit().putString("device", deviceId).apply();
        ScrollView scroll = new ScrollView(this);
        LinearLayout layout = new LinearLayout(this); layout.setOrientation(LinearLayout.VERTICAL); layout.setPadding(28, 40, 28, 32); layout.setBackgroundColor(Color.rgb(242,246,250));
        scroll.addView(layout); setContentView(scroll);
        layout.setOnApplyWindowInsetsListener((v, insets) -> { v.setPadding(28, insets.getSystemWindowInsetTop()+24, 28, insets.getSystemWindowInsetBottom()+24); return insets; });
        label(layout, "CAMPUSLOC  /  WI-FI POSITIONING", 14);
        label(layout, "My location", 30);
        label(layout, "Experimental estimate • Do not use for navigation", 14);
        LinearLayout tabs = new LinearLayout(this); layout.addView(tabs);
        Button myLocation = new Button(this); myLocation.setText("My location"); tabs.addView(myLocation, new LinearLayout.LayoutParams(0, -2, 1));
        Button settings = new Button(this); settings.setText("Connection setup"); tabs.addView(settings, new LinearLayout.LayoutParams(0, -2, 1));
        start = new Button(this); start.setText("Start tracking"); layout.addView(start);
        start.setOnClickListener(v -> { if (running) stop(); else begin(); });
        connectionStatus = label(layout, "○ Not connected", 14);
        status = label(layout, "Ready. Configure your broker in Connection setup, then start.", 14);
        dashboardPanel = new LinearLayout(this); dashboardPanel.setOrientation(LinearLayout.VERTICAL); layout.addView(dashboardPanel);
        LinearLayout locationCard = card(dashboardPanel);
        label(locationCard, "YOUR ESTIMATED FLOOR", 12);
        floor = label(locationCard, "—", 48);
        floor.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);
        details = label(locationCard, "Start tracking to estimate your floor.", 16);
        freshness = label(locationCard, "No estimate yet", 13);
        accessPoints = label(locationCard, "Wi-Fi readings: —", 14);
        LinearLayout building = card(dashboardPanel);
        label(building, "Building overview", 22);
        label(building, "Your estimated floor is highlighted below.", 13);
        for (String name : new String[]{"Fourth", "Third", "Second", "First", "Ground", "B1"}) {
            TextView row = label(building, name, 17); row.setPadding(dp(16), dp(12), dp(16), dp(12));
            LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1, -2); params.setMargins(0, dp(3), 0, dp(3)); row.setLayoutParams(params);
            floorRows.put(name, row);
        }
        highlightFloor("");
        label(dashboardPanel, "Floor-level estimate only. Room, building identity and GPS coordinates are not available from this model. Keep this screen open while tracking.", 14);
        settingsPanel = new LinearLayout(this); settingsPanel.setOrientation(LinearLayout.VERTICAL); layout.addView(settingsPanel); settingsPanel.setVisibility(View.GONE);
        myLocation.setOnClickListener(v -> showDashboard(true)); settings.setOnClickListener(v -> showDashboard(false));
        label(settingsPanel, "Connection setup", 24);
        displayName = input(settingsPanel, "Your display name or nickname", false);
        displayName.setFilters(new android.text.InputFilter[]{new android.text.InputFilter.LengthFilter(40)});
        displayName.setText(prefs.getString("name", "Phone " + deviceId.substring(0, 4)));
        broker = input(settingsPanel, "Broker URI: tcp://192.168.1.10:1883", false);
        broker.setText(prefs.getString("broker", "tcp://192.168.1.10:1883"));
        username = input(settingsPanel, "MQTT username (optional)", false);
        password = input(settingsPanel, "MQTT password (not saved)", true);
        label(settingsPanel, "Changes apply the next time you start tracking. Enable Wi-Fi and Location on your phone.", 14);
        label(settingsPanel, "Device ID: " + deviceId, 12);
        Button copy = new Button(this); copy.setText("Copy device ID"); settingsPanel.addView(copy);
        copy.setOnClickListener(v -> { ((android.content.ClipboardManager)getSystemService(CLIPBOARD_SERVICE)).setPrimaryClip(ClipData.newPlainText("CampusLoc device", deviceId)); Toast.makeText(this, "Device ID copied", Toast.LENGTH_SHORT).show(); });
        Button dashboard = new Button(this); dashboard.setText("Open shared website (stops scanning)"); settingsPanel.addView(dashboard);
        dashboard.setOnClickListener(v -> {
            try { String host = java.net.URI.create(broker.getText().toString().trim()).getHost();
                if (host == null) throw new IllegalArgumentException();
                startActivity(new Intent(Intent.ACTION_VIEW, android.net.Uri.parse("http://" + host + ":8000/?device=" + deviceId)));
            } catch (Exception e) { status.setText("Enter a valid broker address first."); }
        });
        label(layout, "Starting tracking shares your nickname and floor estimates with viewers of your server. Stop tracking to stop sending new scans.", 13);
        label(settingsPanel, "Nearby access points", 22);
        networks = label(settingsPanel, "No scan yet", 14);
        label(settingsPanel, "Scans run every 35 seconds while tracking. Switching between these two app panels keeps scanning active.", 14);
        if (Build.VERSION.SDK_INT >= 33) registerReceiver(receiver, new IntentFilter(WifiManager.SCAN_RESULTS_AVAILABLE_ACTION), Context.RECEIVER_EXPORTED);
        else registerReceiver(receiver, new IntentFilter(WifiManager.SCAN_RESULTS_AVAILABLE_ACTION));
    }
    private int dp(int value) { return Math.round(value * getResources().getDisplayMetrics().density); }
    private LinearLayout card(LinearLayout parent) {
        LinearLayout box = new LinearLayout(this); box.setOrientation(LinearLayout.VERTICAL); box.setPadding(dp(16), dp(12), dp(16), dp(12));
        android.graphics.drawable.GradientDrawable background = new android.graphics.drawable.GradientDrawable(); background.setColor(Color.WHITE); background.setCornerRadius(dp(16)); box.setBackground(background);
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1, -2); params.setMargins(0, dp(10), 0, dp(6)); parent.addView(box, params); return box;
    }
    private void showDashboard(boolean show) { dashboardPanel.setVisibility(show ? View.VISIBLE : View.GONE); settingsPanel.setVisibility(show ? View.GONE : View.VISIBLE); }
    private void highlightFloor(String selected) {
        for (Map.Entry<String,TextView> item : floorRows.entrySet()) {
            boolean active = item.getKey().equalsIgnoreCase(selected); TextView row = item.getValue();
            row.setText(item.getKey() + (active ? "  •  Estimated here" : ""));
            row.setTextColor(active ? Color.WHITE : Color.rgb(35,65,53));
            android.graphics.drawable.GradientDrawable bg = new android.graphics.drawable.GradientDrawable(); bg.setColor(active ? Color.rgb(32,94,70) : Color.rgb(241,245,240)); bg.setCornerRadius(dp(8)); row.setBackground(bg);
        }
    }
    private void clearLocation(String reason) { lastEstimateAt = 0; floor.setText("—"); details.setText(reason); freshness.setText("No current estimate"); accessPoints.setText("Wi-Fi readings: —"); highlightFloor(""); }
    private TextView label(LinearLayout parent, String value, int size) { TextView v = new TextView(this); v.setText(value); v.setTextSize(size); v.setTextColor(Color.rgb(20,40,60)); v.setPadding(0,12,0,12); parent.addView(v); return v; }
    private EditText input(LinearLayout parent, String hint, boolean secret) { EditText v = new EditText(this); v.setHint(hint); v.setSingleLine(true); v.setInputType(InputType.TYPE_CLASS_TEXT | (secret ? InputType.TYPE_TEXT_VARIATION_PASSWORD : InputType.TYPE_TEXT_VARIATION_URI)); parent.addView(v); return v; }
    private void begin() {
        if (checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION}, 7); return;
        }
        try {
            activeName = displayName.getText().toString().trim();
            if (activeName.isEmpty()) throw new IllegalArgumentException("Enter a display name");
            String uri = broker.getText().toString().trim();
            if (!(uri.startsWith("tcp://") || uri.startsWith("ssl://"))) throw new IllegalArgumentException("Use tcp:// or ssl:// broker URI");
            getSharedPreferences("campusloc", MODE_PRIVATE).edit().putString("broker", uri).putString("name", activeName).apply();
            final MqttAsyncClient connection = new MqttAsyncClient(uri, "campusloc-" + deviceId, new MemoryPersistence()); client = connection;
            running = true; start.setText("Stop tracking"); status.setText("Connecting to broker…");
            connectionStatus.setText("○ Connecting…"); showDashboard(true); handler.post(ageTick);
            connection.setCallback(new MqttCallbackExtended() {
                public void connectComplete(boolean reconnect, String server) {
                    try { connection.subscribe("campusloc/" + deviceId + "/prediction", 1, null, new IMqttActionListener() {
                        public void onSuccess(IMqttToken token) { runOnUiThread(() -> { if (running && client == connection) { handler.removeCallbacks(loop); connectionStatus.setText("● Connected to broker"); status.setText("Connected. Scanning…"); handler.post(loop); } }); }
                        public void onFailure(IMqttToken token, Throwable error) { show("Subscription failed: " + error.getMessage()); }
                    }); } catch (Exception e) { show(e.getMessage()); }
                }
                public void connectionLost(Throwable cause) { runOnUiThread(() -> { if (running && client == connection) { connectionStatus.setText("○ Disconnected • reconnecting"); clearLocation("Connection lost. Waiting for fresh data."); status.setText("Broker disconnected. Reconnecting…"); } }); }
                public void deliveryComplete(IMqttDeliveryToken token) {}
                public void messageArrived(String topic, MqttMessage message) throws Exception {
                    JSONObject result = new JSONObject(new String(message.getPayload(), StandardCharsets.UTF_8));
                    runOnUiThread(() -> {
                        if (!running || client != connection || !result.optString("scan_id").equals(latestScan)) return;
                        String estimate = result.optString("predicted_floor", "");
                        if (result.has("error") || !floorRows.containsKey(estimate)) { clearLocation(result.optString("error", "No supported floor estimate received.")); return; }
                        double age = System.currentTimeMillis()/1000.0 - result.optDouble("captured_at", 0);
                        if (!Double.isFinite(age) || age > 90 || age < -30) { clearLocation("Received an expired estimate. Waiting for a fresh scan."); return; }
                        floor.setText(estimate); highlightFloor(estimate);
                        details.setText("You may be on " + estimate + ". Experimental estimate.");
                        lastEstimateAt = SystemClock.elapsedRealtime(); freshness.setText("Updated just now");
                        accessPoints.setText(result.optInt("recognized_access_points") + " recognized / " + result.optInt("detected_access_points") + " detected access points");
                        handler.removeCallbacks(stale); handler.postDelayed(stale, 90000);
                    });
                }
            });
            MqttConnectOptions options = new MqttConnectOptions(); options.setAutomaticReconnect(true); options.setCleanSession(true); options.setConnectionTimeout(10);
            if (!username.getText().toString().isEmpty()) { options.setUserName(username.getText().toString()); options.setPassword(password.getText().toString().toCharArray()); }
            connection.connect(options, null, new IMqttActionListener() {
                public void onSuccess(IMqttToken token) {}
                public void onFailure(IMqttToken token, Throwable error) { runOnUiThread(() -> { if (client == connection) { stop(); status.setText("Connection failed: " + error.getMessage()); } }); }
            });
        } catch (Exception e) { stop(); status.setText("Cannot connect: " + e.getMessage()); }
    }
    @Override public void onRequestPermissionsResult(int request, String[] permissions, int[] results) { super.onRequestPermissionsResult(request, permissions, results); if (request == 7 && checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED) begin(); else status.setText("Precise location permission is required for Wi-Fi scans."); }
    private void scan() {
        if (!running || client == null || !client.isConnected()) return;
        try { requestedAt = SystemClock.elapsedRealtime(); waiting = wifi.startScan(); status.setText(waiting ? "Scanning nearby Wi-Fi…" : "Scan unavailable. Check Wi-Fi / Location; Android may throttle scans."); if (waiting) handler.postDelayed(timeout, 15000); }
        catch (SecurityException e) { stop(); status.setText("Location permission is missing. Grant precise location and restart."); }
    }
    private void publishScan() {
        try {
            JSONObject signals = new JSONObject(); StringBuilder list = new StringBuilder();
            for (ScanResult result : wifi.getScanResults()) {
                if (result.timestamp / 1000 < requestedAt - 2000 || result.level < -110 || result.level > 0 || result.BSSID.equals("02:00:00:00:00:00")) continue;
                String bssid = result.BSSID.toLowerCase(Locale.ROOT);
                if (!signals.has(bssid) || result.level > signals.getInt(bssid)) signals.put(bssid, result.level);
            }
            Iterator<String> keys = signals.keys(); while (keys.hasNext()) { String key = keys.next(); list.append(key).append("     ").append(signals.getInt(key)).append(" dBm\n"); }
            networks.setText(list.length() == 0 ? "No fresh access points" : list.toString());
            if (signals.length() == 0) { status.setText("Empty scan; no data sent."); return; }
            latestScan = UUID.randomUUID().toString();
            JSONObject payload = new JSONObject().put("device_id", deviceId).put("display_name", activeName).put("scan_id", latestScan).put("captured_at", System.currentTimeMillis()/1000.0).put("signals", signals);
            client.publish("campusloc/" + deviceId + "/scan", payload.toString().getBytes(StandardCharsets.UTF_8), 1, false);
            status.setText("Sent " + signals.length() + " access points. Waiting for estimate…");
        } catch (Exception e) { status.setText("Scan delivery failed: " + e.getMessage()); }
    }
    private void show(String text) { runOnUiThread(() -> { if (running) status.setText(text); }); }
    private void stop() {
        running = false; waiting = false; handler.removeCallbacksAndMessages(null); start.setText("Start tracking"); status.setText("Tracking stopped"); connectionStatus.setText("○ Not connected"); clearLocation("Tracking stopped. Start to update your location.");
        MqttAsyncClient old = client; client = null;
        if (old != null) new Thread(() -> { try { old.disconnectForcibly(); old.close(); } catch (Exception ignored) {} }).start();
    }
    @Override protected void onStop() { super.onStop(); stop(); }
    @Override protected void onDestroy() { unregisterReceiver(receiver); super.onDestroy(); }
}
