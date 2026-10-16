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
let myPredictedFloor = 'Ground';
let mySelectedFloor = localStorage.getItem('campusloc_selected_floor') || '';
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

function updateMyPresenceUI() {
  const nameEl = $('#my-display-name');
  const idEl = $('#my-device-id');
  const floorEl = $('#my-predicted-floor');
  const chipsEl = $('#my-floor-picker');
  const cardEl = $('#my-presence');
  const noteEl = $('#my-source-note');
  if (!nameEl) return;

  const isOutOfBox = myPredictedFloor === 'Out of Box' || myPredictedFloor === 'out_of_box';

  nameEl.textContent = currentUser ? currentUser.name + ' (' + currentUser.role + ')' : myDisplayName;
  idEl.textContent = myDeviceId;

  if (isOutOfBox) {
    floorEl.innerHTML = '📦 Out of Box';
    floorEl.className = 'presence-floor-value out-of-box-text';
    if (cardEl) cardEl.classList.add('out-of-box-active');
    if (noteEl) noteEl.innerHTML = '<span class="out-of-box-banner">⚡ Outside Wi-Fi · Dwell time not recorded</span>';
  } else {
    floorEl.textContent = myPredictedFloor;
    floorEl.className = 'presence-floor-value';
    if (cardEl) cardEl.classList.remove('out-of-box-active');
    if (noteEl) noteEl.textContent = 'Campus Wi-Fi synched';
  }

  chipsEl.replaceChildren();
  for (const floor of floors) {
    const btn = node('button', floor, 'floor-chip' + (myPredictedFloor === floor ? ' active' : ''));
    btn.type = 'button';
    btn.onclick = (e) => {
      e.stopPropagation();
      sendHeartbeat(floor);
    };
    chipsEl.append(btn);
  }

  // Explicit Out of Box chip for quick simulation
  const oobBtn = node('button', '📦 Out of Box', 'floor-chip out-of-box' + (isOutOfBox ? ' active' : ''));
  oobBtn.type = 'button';
  oobBtn.title = 'Switch to Out of Box (outside campus Wi-Fi — dwell time NOT recorded)';
  oobBtn.onclick = (e) => {
    e.stopPropagation();
    sendHeartbeat('Out of Box');
  };
  chipsEl.append(oobBtn);
}

async function sendHeartbeat(floorOverride) {
  if (floorOverride !== undefined) {
    mySelectedFloor = floorOverride;
    if (floorOverride) {
      localStorage.setItem('campusloc_selected_floor', floorOverride);
    } else {
      localStorage.removeItem('campusloc_selected_floor');
    }
  }

  const payload = {
    device_id: myDeviceId,
    display_name: currentUser ? currentUser.name : myDisplayName,
    selected_floor: mySelectedFloor || null,
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
      updateMyPresenceUI();
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
  $('#detail-result').textContent = r
    ? (isOutOfBox ? '📦 Out of Box · Outside campus Wi-Fi (Dwell time not recorded)' : (r.connection_status === 'live' ? r.predicted_floor + ' · Estimated floor' : (r.connection_status === 'offline' ? 'No fresh estimate' : r.error)))
    : 'No scans received yet';

  if (!r || !serverOk) {
    $('#history').replaceChildren();
    return;
  }

  try {
    const response = await fetch('/api/history/' + encodeURIComponent(selectedDevice), {
      signal: AbortSignal.timeout(5000),
      cache: 'no-store'
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

    const response = await fetch('/api/devices', {
      signal: AbortSignal.timeout(5000),
      cache: 'no-store'
    });
    if (!response.ok) throw Error();
    const data = await response.json();
    rows = data.devices;
    serverOk = true;
    $('#connection').textContent = data.broker_connected ? '● Broker connected' : '○ Broker disconnected';
    $('#updated').textContent = 'Updated ' + new Date().toLocaleTimeString();

    // If my device has an entry in rows, update my predicted floor
    const me = rows.find(r => r.device_id === myDeviceId);
    if (me && me.predicted_floor) {
      myPredictedFloor = me.predicted_floor;
      updateMyPresenceUI();
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
    setTimeout(poll, 3000);
  }
}

// --- Model.pkl Auto-Scan & Toast Handling ---
let toastTimeout = null;
function showToast(msg) {
  const t = $('#toast');
  if (!t) return;
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toastTimeout);
  toastTimeout = setTimeout(() => {
    t.classList.remove('show');
  }, 4000);
}

async function autoDetectWithModel() {
  const autoBtn = $('#auto-detect-btn');
  const headerBtn = $('#header-scan-btn');
  const badge = $('#model-score-badge');

  if (autoBtn) {
    autoBtn.disabled = true;
    autoBtn.innerHTML = '<span>🔄</span> Scanning Wi-Fi…';
  }
  if (headerBtn) {
    headerBtn.disabled = true;
    headerBtn.textContent = '🔄 Scanning Wi-Fi…';
  }

  try {
    const res = await fetch('/api/scan/live', { method: 'POST' });
    const data = await res.json();
    if (data.success && data.predicted_floor && data.predicted_floor !== 'Out of Box') {
      mySelectedFloor = '';
      localStorage.removeItem('campusloc_selected_floor');
      myPredictedFloor = data.predicted_floor;

      let confText = '';
      if (data.uncalibrated_model_scores) {
        const floorKey = data.predicted_floor.toLowerCase();
        for (const [k, v] of Object.entries(data.uncalibrated_model_scores)) {
          if (k.toLowerCase() === floorKey) {
            confText = ` (${Math.round(v * 100)}% match)`;
            break;
          }
        }
      }

      if (badge) {
        badge.textContent = `🎯 Model: ${data.predicted_floor}${confText} · ${data.recognized_access_points} APs`;
        badge.hidden = false;
      }

      updateMyPresenceUI();
      await sendHeartbeat('auto');
      showToast(`🎯 Model.pkl predicted: ${data.predicted_floor} Floor${confText}`);
    } else {
      if (badge) {
        badge.textContent = '📦 Out of Box (Outside Campus Wi-Fi)';
        badge.hidden = false;
      }
      showToast(data.error || 'No matching campus Wi-Fi access points found.');
    }
  } catch (err) {
    console.warn('Auto scan error:', err);
    showToast('⚠️ Scan request failed. Is host server running?');
  } finally {
    if (autoBtn) {
      autoBtn.disabled = false;
      autoBtn.innerHTML = '<span>⚡</span> Predict Floor with model.pkl';
    }
    if (headerBtn) {
      headerBtn.disabled = false;
      headerBtn.textContent = '⚡ Scan Wi-Fi & Predict Floor';
    }
  }
}

// --- Event Listeners ---
$('#search').oninput = render;
$('#state').onchange = render;
$('#all').onclick = () => { selectedFloor = ''; render(); };
$('#close-detail').onclick = () => { selectedDevice = ''; history.replaceState(null, '', '/'); renderDetail(); };
$('#join').onclick = () => $('#setup').showModal();
$('#close-setup').onclick = () => $('#setup').close();
if ($('#auto-detect-btn')) $('#auto-detect-btn').onclick = autoDetectWithModel;
if ($('#header-scan-btn')) $('#header-scan-btn').onclick = autoDetectWithModel;

$('#broker-address').value = ['localhost', '127.0.0.1'].includes(location.hostname)
  ? 'tcp://YOUR_LAPTOP_IP:1883'
  : 'tcp://' + location.hostname + ':1883';

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
if (!mySelectedFloor) {
  autoDetectWithModel();
}
poll();
