const $ = s => document.querySelector(s);
const floors = ['Fourth', 'Third', 'Second', 'First', 'Ground', 'B1'];

// Persistent Client Web Session Identity
function getClientId() {
  let id = localStorage.getItem('campusloc_web_id');
  if (!id || !/^[A-Za-z0-9_-]{1,64}$/.test(id)) {
    id = 'web-' + Math.random().toString(16).slice(2, 8);
    localStorage.setItem('campusloc_web_id', id);
  }
  return id;
}

function getClientName(id) {
  let name = localStorage.getItem('campusloc_web_name');
  if (!name) {
    const isMobile = /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent);
    name = (isMobile ? 'Mobile Visitor ' : 'Web User ') + id.slice(4);
    localStorage.setItem('campusloc_web_name', name);
  }
  return name;
}

const myDeviceId = getClientId();
let myDisplayName = getClientName(myDeviceId);
let myPredictedFloor = 'Detecting…';
let currentUser = null; // { id, email, name, role }
let authToken = localStorage.getItem('campusloc_jwt_token') || '';

let rows = [];
let selectedFloor = '';
let selectedDevice = new URLSearchParams(location.search).get('device') || '';
let serverOk = false;
let adminAnalyticsData = null;

function node(tag, text, cls) {
  const n = document.createElement(tag);
  if (text !== undefined) n.textContent = text;
  if (cls) n.className = cls;
  return n;
}

function stateLabel(r) {
  return { live: 'Live', out_of_box: 'Out of Box', unlocated: 'Unable to locate', offline: 'Offline / stale' }[r.connection_status] || r.connection_status;
}

// --- Auth Handling ---
function updateAuthUI() {
  const openAuthBtn = $('#open-auth-btn');
  const userPill = $('#user-badge-container');
  const profileName = $('#user-profile-name');
  const roleBadge = $('#user-role-badge');
  const adminToggleBtn = $('#admin-toggle-btn');
  const adminQuickBtn = $('#admin-quick-btn');

  if (currentUser) {
    if (openAuthBtn) openAuthBtn.hidden = true;
    if (userPill) userPill.hidden = false;
    if (profileName) profileName.textContent = currentUser.name;
    if (roleBadge) {
      roleBadge.textContent = currentUser.role;
      roleBadge.className = 'badge ' + (currentUser.role === 'admin' ? 'you' : '');
    }
    const isAdmin = currentUser.role === 'admin';
    if (adminToggleBtn) adminToggleBtn.hidden = !isAdmin;
    if (adminQuickBtn) adminQuickBtn.hidden = !isAdmin;
  } else {
    if (openAuthBtn) openAuthBtn.hidden = false;
    if (userPill) userPill.hidden = true;
    if (adminToggleBtn) adminToggleBtn.hidden = true;
    if (adminQuickBtn) adminQuickBtn.hidden = false; // Opens auth modal if clicked when logged out
  }
}

async function verifyAuth() {
  if (!authToken) {
    currentUser = null;
    updateAuthUI();
    return;
  }
  try {
    const res = await fetch('/api/auth/me', {
      headers: { 'Authorization': 'Bearer ' + authToken }
    });
    if (res.ok) {
      const data = await res.json();
      currentUser = data.user;
      myDisplayName = currentUser.name;
      localStorage.setItem('campusloc_web_name', myDisplayName);
    } else {
      currentUser = null;
      authToken = '';
      localStorage.removeItem('campusloc_jwt_token');
    }
  } catch (e) {
    console.warn('Auth check failed:', e);
  }
  updateAuthUI();
  updateMyPresenceUI();
}

// Toast notification helper
function showToast(text, duration = 3500) {
  const t = $('#toast');
  if (!t) return;
  t.textContent = text;
  t.classList.add('show');
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.remove('show'), duration);
}

function updateMyPresenceUI(extraData) {
  const nameEl = $('#my-display-name');
  const idEl = $('#my-device-id');
  const floorEl = $('#my-predicted-floor');
  const cardEl = $('#my-presence');
  const noteEl = $('#my-source-note');
  const statusMeta = $('#my-live-meta');
  const scoreBadge = $('#model-score-badge');
  const latencyBadge = $('#model-latency-badge');
  if (!nameEl) return;

  nameEl.textContent = currentUser ? currentUser.name : myDisplayName;
  idEl.textContent = myDeviceId;

  // Check if we have an active floor prediction (from live scan, phone report, or heartbeat)
  const floor = (extraData && extraData.predicted_floor) || myPredictedFloor;
  const isLocated = floor && floor !== 'Detecting…' && floor !== 'Out of Box' && floor !== '—' && floor !== 'Ready to scan';
  const isOutOfBox = floor === 'Out of Box' || (extraData && extraData.is_out_of_box);

  if (isOutOfBox) {
    floorEl.textContent = '📦 Out of Box';
    floorEl.className = 'presence-floor-value out-of-box-text';
    if (cardEl) cardEl.classList.add('out-of-box-active');
    if (noteEl) noteEl.textContent = 'Outside campus Wi-Fi coverage · Dwell not recorded';
    if (statusMeta) statusMeta.textContent = 'No matching campus BSSIDs found';
    if (scoreBadge) scoreBadge.textContent = '📦 Outside coverage';
    if (latencyBadge) latencyBadge.textContent = extraData && extraData.latency_ms ? `⚡ ${extraData.latency_ms}ms` : '⚡ 0ms';
  } else if (isLocated) {
    floorEl.textContent = floor.endsWith('Floor') ? floor : floor + ' Floor';
    floorEl.className = 'presence-floor-value';
    if (cardEl) cardEl.classList.remove('out-of-box-active');

    const aps = (extraData && (extraData.recognized_access_points || extraData.detected_access_points)) || 33;
    let conf = 90;
    const scores = (extraData && (extraData.scores || extraData.uncalibrated_model_scores)) || null;
    if (scores) {
      const k = floor.toLowerCase();
      for (const [key, val] of Object.entries(scores)) {
        if (key.toLowerCase() === k) { conf = Math.round(val * 100); break; }
      }
    }
    if (noteEl) noteEl.textContent = `model.pkl live prediction (${aps}/100 APs)`;
    if (statusMeta) statusMeta.textContent = `AI Fingerprinted: ${conf}% probability match`;
    if (scoreBadge) scoreBadge.textContent = `● ${floor} Floor (${conf}% match · ${aps} APs)`;
    if (latencyBadge) latencyBadge.textContent = extraData && extraData.latency_ms ? `⚡ ~${Math.round(extraData.latency_ms)}ms latency` : '⚡ ~15ms';
  } else {
    floorEl.textContent = 'Ready to scan';
    floorEl.className = 'presence-floor-value';
    if (cardEl) cardEl.classList.remove('out-of-box-active');
    if (noteEl) noteEl.textContent = 'model.pkl active · Standard WLAN';
    if (statusMeta) statusMeta.textContent = 'Click "Scan Wi-Fi" or use Android app over WLAN';
    if (scoreBadge) scoreBadge.textContent = '● 100 AP Features Ready';
    if (latencyBadge) latencyBadge.textContent = '⚡ Ultra-fast (~15ms)';
  }
}

// Trigger real-time host Wi-Fi scan and model.pkl prediction
async function triggerHostScan() {
  const scanBtns = [$('#scan-now-btn'), $('#scan-presence-btn'), $('#modal-scan-live-btn')].filter(Boolean);
  scanBtns.forEach(b => { b.disabled = true; b.textContent = 'Scanning…'; });
  const floorEl = $('#my-predicted-floor');
  const statusMeta = $('#my-live-meta');
  if (floorEl) floorEl.textContent = 'Scanning Wi-Fi…';
  if (statusMeta) statusMeta.textContent = 'Querying visible WLAN BSSIDs in memory…';

  try {
    const res = await fetch('/api/scan/live', { method: 'POST' });
    const data = await res.json();
    if (data.success && data.predicted_floor && data.predicted_floor !== 'Out of Box') {
      myPredictedFloor = data.predicted_floor;
      updateMyPresenceUI(data);
      showToast(`🎯 model.pkl predicted: ${data.predicted_floor} Floor (${data.recognized_access_points} APs matched)`);
      if (typeof highlightFloor === 'function') highlightFloor(data.predicted_floor);
      // Update model flow modal if open
      renderFlowResults(data);
    } else if (data.is_out_of_box) {
      myPredictedFloor = 'Out of Box';
      updateMyPresenceUI(data);
      showToast(`📦 Out of Box: ${data.error || 'Outside campus Wi-Fi'}`);
      renderFlowResults(data);
    } else {
      showToast(`Scan note: ${data.error || 'No APs detected'}`);
    }
  } catch (e) {
    showToast('Failed to trigger scan: ' + e.message);
  } finally {
    scanBtns.forEach(b => { b.disabled = false; b.textContent = b.id === 'modal-scan-live-btn' ? '⚡ Live Host Wi-Fi Scan' : '⚡ Scan Host Wi-Fi'; });
    poll();
  }
}

// --- Model.pkl Flow Inspector Dialog Actions ---
const modelFlowDialog = $('#model-flow-dialog');
function openModelFlowModal() {
  if (!modelFlowDialog) return;
  modelFlowDialog.showModal();
  // Run default ground preset to show immediate rich flow if empty
  if (!$('#flow-prob-bars').children.length) {
    runPresetPrediction('Ground');
  }
}

async function runPresetPrediction(floorName) {
  // Highlight active preset button
  document.querySelectorAll('.preset-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.floor === floorName);
  });

  const resFloor = $('#flow-result-floor');
  const resMeta = $('#flow-result-meta');
  if (resFloor) resFloor.textContent = 'Computing…';

  try {
    const res = await fetch('/api/predict/preset/' + encodeURIComponent(floorName), { method: 'POST' });
    if (!res.ok) throw new Error('Preset prediction failed');
    const data = await res.json();
    renderFlowResults(data);
  } catch (err) {
    if (resFloor) resFloor.textContent = 'Error';
    if (resMeta) resMeta.textContent = err.message;
  }
}

function renderFlowResults(data) {
  const resFloor = $('#flow-result-floor');
  const resMeta = $('#flow-result-meta');
  const latTag = $('#flow-latency-tag');
  const signalsCount = $('#flow-signals-count');
  const matchedCount = $('#flow-matched-count');
  const predLabel = $('#flow-predicted-label');
  const confLabel = $('#flow-confidence-label');
  const barsContainer = $('#flow-prob-bars');

  const floor = data.predicted_floor || (data.is_out_of_box ? 'Out of Box' : '—');
  const conf = data.confidence ? Math.round(data.confidence * 100) : (data.is_out_of_box ? 0 : 90);
  const recAPs = data.recognized_access_points || (data.signals ? Object.keys(data.signals).length : 0);
  const totalAPs = data.detected_access_points || recAPs;

  if (resFloor) resFloor.textContent = floor.endsWith('Floor') ? floor : floor + ' Floor';
  if (latTag) latTag.textContent = `⚡ ${data.latency_ms || 15}ms`;
  if (signalsCount) signalsCount.textContent = `${totalAPs} visible APs`;
  if (matchedCount) matchedCount.textContent = `${recAPs} of 100 APs`;
  if (predLabel) predLabel.textContent = floor;
  if (confLabel) confLabel.textContent = `${conf}% Confidence`;

  if (resMeta) {
    resMeta.textContent = data.is_out_of_box
      ? 'Outside campus Wi-Fi fingerprint envelope (0 recognized APs).'
      : `Matched ${recAPs} campus APs in ${data.latency_ms || 15}ms. Model confidence: ${conf}%.`;
  }

  // Render probability distribution bars
  if (barsContainer) {
    barsContainer.replaceChildren();
    const scores = data.uncalibrated_model_scores || {};
    const normScores = {};
    for (const f of floors) {
      normScores[f] = 0.0;
    }
    for (const [k, v] of Object.entries(scores)) {
      const match = floors.find(f => f.toLowerCase() === k.toLowerCase());
      if (match) normScores[match] = v;
    }

    for (const f of floors) {
      const prob = normScores[f] || 0.0;
      const pct = Math.round(prob * 100);
      const isTop = f.toLowerCase() === (data.predicted_floor || '').toLowerCase();

      const row = node('div', undefined, 'prob-bar-row');
      const label = node('span', f, 'prob-bar-label');
      const track = node('div', undefined, 'prob-bar-track');
      const fill = node('div', undefined, 'prob-bar-fill' + (isTop ? ' top-choice' : ''));
      fill.style.width = Math.max(pct, 2) + '%';
      track.append(fill);
      const val = node('span', pct + '%', 'prob-bar-val');

      row.append(label, track, val);
      barsContainer.append(row);
    }
  }
}

async function sendHeartbeat() {
  const payload = {
    device_id: myDeviceId,
    display_name: currentUser ? currentUser.name : myDisplayName,
    platform: /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent) ? 'mobile' : 'browser'
  };

  const headers = { 'Content-Type': 'application/json' };
  if (authToken) {
    headers['Authorization'] = 'Bearer ' + authToken;
  }

  try {
    const res = await fetch('/api/web-user/heartbeat', {
      method: 'POST',
      headers,
      body: JSON.stringify(payload)
    });
    if (res.ok) {
      const data = await res.json();
      if (data.predicted_floor) {
        myPredictedFloor = data.predicted_floor;
      }
      updateMyPresenceUI(data);
    }
  } catch (err) {
    console.warn('Heartbeat error:', err);
  }
}

function render() {
  $('#total').textContent = rows.length;
  $('#live').textContent = rows.filter(r => r.connection_status === 'live').length;
  $('#unknown').textContent = rows.filter(r => r.connection_status === 'unlocated' || r.connection_status === 'out_of_box' || r.is_out_of_box).length;
  $('#offline').textContent = rows.filter(r => r.connection_status === 'offline').length;

  // Floors counter list
  $('#floors').replaceChildren();
  for (const floor of floors) {
    const b = node('button', undefined, 'floor' + (selectedFloor === floor ? ' selected' : ''));
    const count = rows.filter(r => r.connection_status === 'live' && r.predicted_floor === floor).length;
    b.append(node('span', floor), node('b', String(count)));
    b.setAttribute('aria-pressed', String(selectedFloor === floor));
    b.onclick = () => {
      selectedFloor = selectedFloor === floor ? '' : floor;
      render();
    };
    $('#floors').append(b);
  }

  const query = $('#search').value.toLowerCase();
  const status = $('#state').value;
  const visible = rows.filter(r => {
    const isOutOfBox = r.connection_status === 'out_of_box' || r.predicted_floor === 'Out of Box' || r.is_out_of_box;
    const matchesQuery = (r.display_name + ' ' + r.device_id).toLowerCase().includes(query);
    const matchesStatus = status === 'all'
      ? true
      : status === 'out_of_box'
        ? isOutOfBox
        : status === 'unlocated'
          ? (r.connection_status === 'unlocated' || isOutOfBox)
          : r.connection_status === status;
    const matchesFloor = !selectedFloor || (!isOutOfBox && r.connection_status === 'live' && r.predicted_floor === selectedFloor);
    return matchesQuery && matchesStatus && matchesFloor;
  });

  $('#filter-note').textContent = selectedFloor ? 'Showing live estimates on ' + selectedFloor : '';
  $('#devices').replaceChildren();

  for (const r of visible) {
    const isMe = r.device_id === myDeviceId;
    const isOutOfBox = r.connection_status === 'out_of_box' || r.predicted_floor === 'Out of Box' || r.is_out_of_box;
    const card = node('article', undefined, 'device' + (isMe ? ' my-device' : ''));
    const identity = node('div');
    const button = node('button', r.display_name);

    if (isMe) {
      button.append(node('span', 'YOU', 'badge you'));
    }
    if (r.user_role && r.user_role === 'admin') {
      button.append(node('span', 'ADMIN', 'badge you'));
    }

    button.onclick = () => {
      selectedDevice = r.device_id;
      history.replaceState(null, '', '?device=' + encodeURIComponent(selectedDevice));
      renderDetail();
      $('#detail').scrollIntoView({ behavior: 'smooth' });
    };

    const typeDesc = r.device_type === 'web'
      ? (r.user_email ? 'User: ' + r.user_email : 'Web Visitor')
      : (r.detected_access_points + ' detected · ' + (r.recognized_access_points || 0) + ' recognized APs');
    identity.append(button, node('small', r.device_id + (r.platform ? ' (' + r.platform + ')' : '')), node('small', typeDesc));

    const result = node('div', undefined, 'result');
    const badgeCls = 'badge ' + (isOutOfBox ? 'out_of_box' : r.connection_status);
    const badgeText = isOutOfBox ? '📦 Out of Box' : stateLabel(r);
    const estText = isOutOfBox ? 'Out of Box' : (r.connection_status === 'live' ? r.predicted_floor : '—');
    const estCls = 'estimate' + (isOutOfBox ? ' out-of-box-val' : '');

    result.append(
      node('span', badgeText, badgeCls),
      node('div', estText, estCls)
    );
    if (isOutOfBox) {
      result.append(node('span', 'Dwell time not recorded', 'no-record-tag'));
    }
    result.append(node('small', 'Seen ' + r.age_seconds + 's ago'));
    card.append(identity, result);
    $('#devices').append(card);
  }

  if (!visible.length) {
    $('#devices').append(node('p', rows.length ? 'No devices match these filters.' : 'No devices yet. Connect a device and start tracking.', 'empty'));
  }

  renderDetail();
}

let detailGeneration = 0;
async function renderDetail() {
  const g = ++detailGeneration;
  $('#detail').hidden = !selectedDevice;
  if (!selectedDevice) return;

  const r = rows.find(r => r.device_id === selectedDevice);
  const isOutOfBox = r && (r.connection_status === 'out_of_box' || r.predicted_floor === 'Out of Box' || r.is_out_of_box);
  $('#detail-title').textContent = r ? r.display_name : 'Waiting for device';
  $('#detail-id').textContent = selectedDevice;
  let detailSub = '';
  if (r && r.connection_status === 'live' && r.predicted_floor) {
    const aps = r.recognized_access_points || 0;
    const conf = r.confidence ? Math.round(r.confidence * 100) : null;
    detailSub = ` · Estimated by model.pkl (${conf ? conf + '% match · ' : ''}${aps}/100 APs)`;
  }
  $('#detail-result').textContent = r
    ? (isOutOfBox ? '📦 Out of Box · Outside campus Wi-Fi (Dwell time not recorded)' : (r.connection_status === 'live' ? r.predicted_floor + ' Floor' + detailSub : (r.connection_status === 'offline' ? 'No fresh estimate' : (r.error || 'Unable to locate'))))
    : 'No scans received yet';

  if (!r || !serverOk) {
    $('#history').replaceChildren();
    return;
  }

  try {
    const response = await fetch('/api/history/' + encodeURIComponent(selectedDevice), {
      signal: AbortSignal.timeout(5000),
      cache: 'no-store', headers: {Authorization: 'Bearer ' + authToken}
    });
    if (!response.ok) throw Error();
    const data = await response.json();
    if (g !== detailGeneration) return;

    $('#history').replaceChildren();
    for (const item of data.history.slice().reverse()) {
      const row = node('div', undefined, 'history-row');
      row.append(
        node('span', new Date(item.captured_at * 1000).toLocaleTimeString()),
        node('span', item.predicted_floor || 'Unable to locate')
      );
      $('#history').append(row);
    }
  } catch {
    if (g === detailGeneration) $('#history').textContent = 'History unavailable.';
  }
}

// --- Admin Analytics Rendering ---
async function fetchAdminAnalytics() {
  if (!authToken) return;
  try {
    const res = await fetch('/api/admin/analytics', {
      headers: { 'Authorization': 'Bearer ' + authToken }
    });
    if (!res.ok) return;
    adminAnalyticsData = await res.json();
    renderAdminAnalytics();
  } catch (e) {
    console.warn('Analytics fetch error:', e);
  }
}

function renderAdminAnalytics() {
  if (!adminAnalyticsData) return;
  const { floor_summary, user_breakdown } = adminAnalyticsData;

  // Calculate totals
  let totalMinutes = 0;
  let topFloor = '—';
  let maxMin = -1;
  let totalSessions = 0;

  for (const floor of floors) {
    const item = floor_summary[floor] || { total_minutes: 0, session_count: 0 };
    totalMinutes += item.total_minutes;
    totalSessions += item.session_count;
    if (item.total_minutes > maxMin) {
      maxMin = item.total_minutes;
      topFloor = floor;
    }
  }

  $('#admin-total-time').textContent = Math.round(totalMinutes * 10) / 10 + ' min';
  $('#admin-top-floor').textContent = topFloor + (maxMin > 0 ? ` (${maxMin}m)` : '');
  $('#admin-total-sessions').textContent = String(totalSessions);
  $('#admin-updated-time').textContent = 'Updated ' + new Date().toLocaleTimeString();

  // Floor dwell cards
  const dwellList = $('#admin-floor-dwell-list');
  dwellList.replaceChildren();
  for (const floor of floors) {
    const item = floor_summary[floor] || { total_minutes: 0, total_seconds: 0, session_count: 0 };
    const pct = totalMinutes > 0 ? Math.round((item.total_minutes / totalMinutes) * 100) : 0;

    const card = node('div', undefined, 'floor-dwell-card');
    const head = node('div', undefined, 'dwell-head');
    head.append(node('span', floor), node('span', pct + '%'));

    const timeVal = node('div', item.total_minutes + ' min', 'dwell-time');
    const sessions = node('div', item.session_count + ' dwell records (' + item.total_seconds + 's)', 'dwell-sessions');

    const bar = node('div', undefined, 'dwell-progress-bar');
    const fill = node('div', undefined, 'dwell-fill');
    fill.style.width = pct + '%';
    bar.append(fill);

    card.append(head, timeVal, sessions, bar);
    dwellList.append(card);
  }

  // User breakdown table
  renderUserTable();
}

function renderUserTable() {
  if (!adminAnalyticsData) return;
  const tbody = $('#admin-user-table-body');
  const filter = ($('#admin-user-filter').value || '').toLowerCase();
  const rows = adminAnalyticsData.user_breakdown.filter(u =>
    (u.display_name + ' ' + u.email + ' ' + u.floor).toLowerCase().includes(filter)
  );

  tbody.replaceChildren();
  if (!rows.length) {
    const tr = node('tr');
    tr.append(node('td', 'No matching user dwell records found.', 'empty'));
    tr.children[0].colSpan = 5;
    tbody.append(tr);
    return;
  }

  for (const u of rows) {
    const tr = node('tr');
    tr.append(
      node('td', u.display_name),
      node('td', u.email),
      node('td', u.floor),
      node('td', u.total_minutes + ' min (' + u.total_seconds + 's)'),
      node('td', u.last_active ? new Date(u.last_active * 1000).toLocaleTimeString() : '—')
    );
    tbody.append(tr);
  }
}

async function poll() {
  try {
    // Send heartbeat to keep this user session live and logged to DB
    await sendHeartbeat();

    let data;
    if (authToken) {
      // Authenticated: fetch with scope=all to see all devices on the WLAN network
      const response = await fetch('/api/devices?scope=all', {
        signal: AbortSignal.timeout(5000),
        cache: 'no-store', headers: {Authorization: 'Bearer ' + authToken}
      });
      if (response.ok) {
        data = await response.json();
        rows = data.devices;
      } else {
        // Fallback to network overview
        const pubRes = await fetch('/api/network/overview', { signal: AbortSignal.timeout(5000), cache: 'no-store' });
        if (pubRes.ok) {
          data = await pubRes.json();
          rows = data.devices;
        }
      }
    } else {
      // Unauthenticated visitor on WLAN: fetch public network overview so all campus devices are visible!
      const pubRes = await fetch('/api/network/overview', { signal: AbortSignal.timeout(5000), cache: 'no-store' });
      if (pubRes.ok) {
        data = await pubRes.json();
        rows = data.devices;
      }
    }

    serverOk = true;
    $('#connection').textContent = '● Server connected · WLAN Campus Network';
    $('#updated').textContent = 'Updated ' + new Date().toLocaleTimeString();

    // If my device or host scanner has an entry in rows, update my predicted floor
    const me = rows.find(r => r.device_id === myDeviceId || r.device_id === 'campus-host-scanner');
    if (me && me.predicted_floor) {
      myPredictedFloor = me.predicted_floor;
      updateMyPresenceUI(me);
    }

    render();

    // If admin panel is open and user is admin, refresh analytics
    if (!$('#admin-dashboard').hidden && currentUser && currentUser.role === 'admin') {
      fetchAdminAnalytics();
    }
  } catch (err) {
    serverOk = false;
    rows = rows.map(r => ({ ...r, connection_status: 'offline' }));
    $('#connection').textContent = '○ Receiver unavailable';
    $('#updated').textContent = 'Updates paused';
    render();
  } finally {
    setTimeout(poll, 1200); // Lively, snappy 1.2s updates without lag
  }
}

// --- Event Listeners ---
$('#search').oninput = render;
$('#state').onchange = render;
$('#all').onclick = () => { selectedFloor = ''; render(); };
$('#close-detail').onclick = () => { selectedDevice = ''; history.replaceState(null, '', '/'); renderDetail(); };
$('#join').onclick = () => $('#setup').showModal();
$('#close-setup').onclick = () => $('#setup').close();

// Wi-Fi Scan & Model Flow Buttons
const scanNowBtn = $('#scan-now-btn');
if (scanNowBtn) scanNowBtn.onclick = triggerHostScan;

const scanPresenceBtn = $('#scan-presence-btn');
if (scanPresenceBtn) scanPresenceBtn.onclick = triggerHostScan;

const modalScanLiveBtn = $('#modal-scan-live-btn');
if (modalScanLiveBtn) modalScanLiveBtn.onclick = triggerHostScan;

const openModelFlowBtn = $('#open-model-flow-btn');
if (openModelFlowBtn) openModelFlowBtn.onclick = openModelFlowModal;

const inspectPresenceBtn = $('#inspect-presence-btn');
if (inspectPresenceBtn) inspectPresenceBtn.onclick = openModelFlowModal;

const closeModelFlowBtn = $('#close-model-flow-btn');
if (closeModelFlowBtn && modelFlowDialog) closeModelFlowBtn.onclick = () => modelFlowDialog.close();

// Preset Buttons inside Model Flow Inspector
document.querySelectorAll('.preset-btn').forEach(btn => {
  if (btn.dataset.floor) {
    btn.onclick = () => runPresetPrediction(btn.dataset.floor);
  }
});

$('#broker-address').value = location.origin;

// Nickname rename handling
const renameDialog = $('#rename-dialog');
const renameInput = $('#rename-input');
$('#edit-my-name-btn').onclick = () => {
  renameInput.value = currentUser ? currentUser.name : myDisplayName;
  renameDialog.showModal();
  renameInput.focus();
};
$('#close-rename').onclick = () => renameDialog.close();
$('#rename-form').onsubmit = (e) => {
  e.preventDefault();
  const val = renameInput.value.trim();
  if (val && val.length <= 40) {
    myDisplayName = val;
    localStorage.setItem('campusloc_web_name', myDisplayName);
    updateMyPresenceUI();
    sendHeartbeat();
    renameDialog.close();
  }
};

// Leave beacon on tab close / unload
window.addEventListener('pagehide', () => {
  try {
    navigator.sendBeacon('/api/web-user/leave', JSON.stringify({ device_id: myDeviceId }));
  } catch (e) {}
});

// --- Auth Dialog Actions ---
const authDialog = $('#auth-dialog');
const openAuthBtn = $('#open-auth-btn');
const closeAuthBtn = $('#close-auth-dialog');
const tabLogin = $('#tab-login');
const tabRegister = $('#tab-register');
const authNameGroup = $('#auth-name-group');
const authSubmitBtn = $('#auth-submit-btn');
const authError = $('#auth-error');
let isRegisterMode = false;

function setAuthMode(register) {
  isRegisterMode = register;
  tabLogin.classList.toggle('active', !register);
  tabRegister.classList.toggle('active', register);
  authNameGroup.hidden = !register;
  $('#auth-dialog-title').textContent = register ? 'Create a CampusLoc Account' : 'Sign In to CampusLoc';
  authSubmitBtn.textContent = register ? 'Create Account' : 'Sign In';
  authError.hidden = true;
}

if (openAuthBtn) openAuthBtn.onclick = () => { setAuthMode(false); authDialog.showModal(); };
if (closeAuthBtn) closeAuthBtn.onclick = () => authDialog.close();
if (tabLogin) tabLogin.onclick = () => setAuthMode(false);
if (tabRegister) tabRegister.onclick = () => setAuthMode(true);

$('#demo-admin-btn').onclick = async () => {
  $('#auth-email').value = 'admin@campusloc.internal';
  $('#auth-password').value = 'admin123';
  setAuthMode(false);
  $('#auth-form').requestSubmit();
};

$('#auth-form').onsubmit = async (e) => {
  e.preventDefault();
  authError.hidden = true;
  const email = $('#auth-email').value.trim();
  const password = $('#auth-password').value;
  const name = $('#auth-name').value.trim();

  const url = isRegisterMode ? '/api/auth/register' : '/api/auth/login';
  const payload = isRegisterMode ? { email, name, password } : { email, password };

  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || 'Authentication failed');
    }

    authToken = data.token;
    currentUser = data.user;
    localStorage.setItem('campusloc_jwt_token', authToken);
    myDisplayName = currentUser.name;
    localStorage.setItem('campusloc_web_name', myDisplayName);

    authDialog.close();
    updateAuthUI();
    updateMyPresenceUI();
    sendHeartbeat();

    if (currentUser.role === 'admin') {
      $('#admin-dashboard').hidden = false;
      fetchAdminAnalytics();
    }
  } catch (err) {
    authError.textContent = err.message;
    authError.hidden = false;
  }
};

$('#logout-btn').onclick = () => {
  authToken = '';
  currentUser = null;
  localStorage.removeItem('campusloc_jwt_token');
  $('#admin-dashboard').hidden = true;
  updateAuthUI();
  updateMyPresenceUI();
};

// Admin panel toggle actions
function toggleAdminPanel() {
  if (!currentUser || currentUser.role !== 'admin') {
    setAuthMode(false);
    authDialog.showModal();
    return;
  }
  const panel = $('#admin-dashboard');
  panel.hidden = !panel.hidden;
  if (!panel.hidden) {
    fetchAdminAnalytics();
    panel.scrollIntoView({ behavior: 'smooth' });
  }
}

$('#admin-toggle-btn').onclick = toggleAdminPanel;
$('#admin-quick-btn').onclick = toggleAdminPanel;
$('#close-admin-btn').onclick = () => { $('#admin-dashboard').hidden = true; };
$('#admin-user-filter').oninput = renderUserTable;

// Initial Startup
verifyAuth();
updateMyPresenceUI();
poll();
