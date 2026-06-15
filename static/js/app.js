'use strict';

// ── State ─────────────────────────────────────────────────────────────────────
let allCuts = [];
let chartInstance = null;

const CHART_COLORS = [
  '#8b0000','#3498db','#27ae60','#e67e22','#8e44ad',
  '#2980b9','#c0392b','#16a085','#f39c12','#1abc9c'
];

// ── Utilities ─────────────────────────────────────────────────────────────────
function esc(str) {
  const d = document.createElement('div');
  d.textContent = str;
  return d.innerHTML;
}

function fmt$(v) {
  return v == null ? '—' : '$' + Number(v).toFixed(2);
}

function fmtPct(v) {
  return v == null ? '—' : Number(v).toFixed(1) + '%';
}

function fmtLbs(v) {
  return v == null ? '—' : Number(v).toFixed(2) + ' lbs';
}

function today() {
  return new Date().toISOString().slice(0, 10);
}

function showAlert(elId, type, msg) {
  const el = document.getElementById(elId);
  if (!el) return;
  el.className = 'alert-banner ' + type;
  el.textContent = msg;
  el.classList.remove('hidden');
  if (type === 'success') {
    setTimeout(() => el.classList.add('hidden'), 3500);
  }
}

function hideAlert(elId) {
  const el = document.getElementById(elId);
  if (el) el.classList.add('hidden');
}

// ── Tab navigation ────────────────────────────────────────────────────────────
function showTab(name) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
  const tab = document.getElementById('tab-' + name);
  if (tab) tab.classList.add('active');
  document.querySelectorAll('.nav-btn').forEach(b => {
    if (b.textContent.trim().toLowerCase().includes(
      name === 'dashboard' ? 'dashboard' :
      name === 'weekly'    ? 'weekly'    :
      name === 'cuts'      ? 'cuts'      :
      name === 'reports'   ? 'reports'   :
      name === 'users'     ? 'users'     :
      name === 'settings'  ? 'settings'  :
      name === 'guide'     ? 'guide'     : '__none__'
    )) b.classList.add('active');
  });
  // Close mobile nav
  const hdr = document.getElementById('main-header');
  if (hdr) hdr.classList.remove('nav-open');
  // Lazy-load tab content
  if (name === 'dashboard')  loadDashboard();
  if (name === 'weekly')     initWeekly();
  if (name === 'cuts')       loadCuts();
  if (name === 'settings')   loadSettings();
  if (name === 'users')      loadUsers();
}

function toggleMobileMenu() {
  const hdr = document.getElementById('main-header');
  if (hdr) hdr.classList.toggle('nav-open');
}

// ── Modal helpers ─────────────────────────────────────────────────────────────
function showModal(id) {
  document.getElementById(id).classList.remove('hidden');
  document.getElementById('modal-overlay').classList.remove('hidden');
}
function hideModal(id) {
  document.getElementById(id).classList.add('hidden');
  document.getElementById('modal-overlay').classList.add('hidden');
}
function closeAll() {
  document.querySelectorAll('.modal').forEach(m => m.classList.add('hidden'));
  document.getElementById('modal-overlay').classList.add('hidden');
}

// ── API helpers ───────────────────────────────────────────────────────────────
async function apiFetch(url, opts = {}) {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...opts
  });
  if (res.status === 401) {
    window.location.href = '/login';
    throw new Error('Session expired');
  }
  if (!res.ok) {
    const d = await res.json().catch(() => ({}));
    throw new Error(d.error || 'Request failed (' + res.status + ')');
  }
  return res.json();
}

// ── Dashboard ─────────────────────────────────────────────────────────────────
async function loadDashboard() {
  try {
    const data = await apiFetch('/api/dashboard');
    renderDashboard(data);
  } catch (e) {
    showAlert('dash-alert', 'error', 'Failed to load dashboard: ' + e.message);
  }
}

function renderDashboard(data) {
  const { cuts, latest_date, update_interval, days_since, days_until, status } = data;

  hideAlert('dash-alert');
  if (status === 'no_data') {
    showAlert('dash-alert', 'info', 'No entries yet. Use Weekly Update to log your first prices.');
  } else if (status === 'overdue') {
    showAlert('dash-alert', 'error',
      `Update overdue by ${Math.abs(days_until)} day(s). Last updated: ${latest_date}.`);
  } else if (status === 'due_soon') {
    showAlert('dash-alert', 'warning',
      `Update due. Last updated: ${latest_date}. Please enter new prices today.`);
  } else {
    showAlert('dash-alert', 'success',
      `Up to date. Last updated: ${latest_date}. Next update in ${days_until} day(s).`);
  }

  const cardsEl = document.getElementById('dash-cards');
  const emptyEl = document.getElementById('dash-empty');
  if (cuts.length === 0) {
    cardsEl.innerHTML = '';
    emptyEl.classList.remove('hidden');
    return;
  }
  emptyEl.classList.add('hidden');

  cardsEl.innerHTML = cuts.map(c => {
    const target = c.target_yield != null ? c.target_yield : null;
    const actual = c.yield_pct > 0 ? c.yield_pct : null;
    const diff = (target != null && actual != null) ? Math.abs(actual - target) : null;
    const yieldClass = diff == null ? 'yield-none'
                     : diff <= 3   ? 'yield-good'
                     : diff <= 5   ? 'yield-ok'
                     :               'yield-poor';
    const badgeText  = diff == null ? 'No Target Set'
                     : diff <= 3   ? `On Target (${fmtPct(actual)} vs ${fmtPct(target)})`
                     : diff <= 5   ? `Near Target (${fmtPct(actual)} vs ${fmtPct(target)})`
                     :               `Off Target (${fmtPct(actual)} vs ${fmtPct(target)})`;
    return `
    <div class="card ${yieldClass}">
      <h3>${esc(c.cut_name)}</h3>
      ${c.category ? `<span class="cat-tag">${esc(c.category)}</span>` : ''}
      <div class="card-row">Last Entry: <strong>${c.entry_date}</strong></div>
      <div class="card-row">Purchase Price: <strong>${fmt$(c.purchase_price)}/lb</strong></div>
      <div class="card-row">Purchase Weight: <strong>${fmtLbs(c.purchase_weight)}</strong></div>
      <div class="card-row">Yield Loss: <strong>${fmtPct(c.yield_loss)}</strong></div>
      <div class="card-row">True Cost: <strong style="color:#8b0000;">${fmt$(c.adjusted_cost)}/lb</strong></div>
      <div class="card-row">Avg Yield %: <strong>${fmtPct(c.avg_yield_pct)}</strong> <span style="font-size:0.75rem;color:#999;">all entries</span></div>
      <div class="card-row">Avg True Cost: <strong style="color:#8b0000;">${fmt$(c.avg_true_cost)}/lb</strong> <span style="font-size:0.75rem;color:#999;">all entries</span></div>
      <span class="yield-badge">${badgeText}</span>
    </div>`;
  }).join('');
}

async function sendAlert() {
  const btn = document.getElementById('send-alert-btn');
  if (btn) btn.disabled = true;
  try {
    await apiFetch('/api/send-alert', { method: 'POST' });
    showAlert('dash-alert', 'success', 'Alert email sent successfully.');
  } catch (e) {
    showAlert('dash-alert', 'error', 'Failed to send alert: ' + e.message);
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function manualSendAlert() {
  try {
    await apiFetch('/api/send-alert', { method: 'POST' });
    const el = document.getElementById('settings-result');
    if (el) { el.textContent = 'Alert email sent.'; el.style.color = '#27ae60'; setTimeout(() => { el.textContent = ''; }, 3000); }
  } catch (e) {
    const el = document.getElementById('settings-result');
    if (el) { el.textContent = 'Send failed: ' + e.message; el.style.color = '#c0392b'; }
  }
}

// ── Weekly Update ─────────────────────────────────────────────────────────────
async function initWeekly() {
  document.getElementById('weekly-date').value = today();
  await loadWeeklyEntries();
}

async function loadWeeklyEntries() {
  hideAlert('weekly-alert');
  const dateVal = document.getElementById('weekly-date').value;
  if (!dateVal) {
    renderWeeklyTable(allCuts, []);
    return;
  }

  try {
    if (allCuts.length === 0) {
      allCuts = await apiFetch('/api/cuts?active=1');
    }
    let existingMap = {};
    if (dateVal) {
      try {
        const entries = await apiFetch('/api/entries?date=' + dateVal);
        entries.forEach(e => { existingMap[e.cut_id] = e; });
      } catch(_) {}
    }
    renderWeeklyTable(allCuts, existingMap);
  } catch (e) {
    showAlert('weekly-alert', 'error', 'Failed to load: ' + e.message);
  }
}

function renderWeeklyTable(cuts, existingMap) {
  const tbody = document.getElementById('weekly-body');
  if (cuts.length === 0) {
    tbody.innerHTML = '<tr><td colspan="9" style="text-align:center;color:#aaa;padding:24px;">No active cuts. Add cuts in the Cuts tab.</td></tr>';
    return;
  }

  tbody.innerHTML = cuts.map(c => {
    const ex = existingMap[c.id] || {};
    const pp = ex.purchase_price != null ? ex.purchase_price : '';
    const pw = ex.purchase_weight != null ? ex.purchase_weight : '';
    const tw = ex.trim_weight != null ? ex.trim_weight : '';
    const notes = ex.notes || '';
    return `
      <tr data-cut-id="${c.id}">
        <td>${esc(c.name)}</td>
        <td>${esc(c.category || '—')}</td>
        <td><input class="weekly-input" type="number" step="0.0001" min="0" placeholder="0.0000" value="${pp}" data-field="pp" oninput="calcRow(this.closest('tr'))"/></td>
        <td><input class="weekly-input" type="number" step="0.01" min="0" placeholder="0.00" value="${pw}" data-field="pw" oninput="calcRow(this.closest('tr'))"/></td>
        <td><input class="weekly-input" type="number" step="0.01" min="0" placeholder="0.00" value="${tw}" data-field="tw" oninput="calcRow(this.closest('tr'))"/></td>
        <td class="calc-val" data-out="loss">—</td>
        <td class="calc-val yield-pct-cell" data-out="pct">—</td>
        <td class="calc-val" data-out="true">—</td>
        <td><input class="weekly-notes-input" type="text" placeholder="Notes" value="${esc(notes)}" data-field="notes"/></td>
      </tr>
    `;
  }).join('');

  tbody.querySelectorAll('tr[data-cut-id]').forEach(row => calcRow(row));
}

function calcRow(row) {
  const pp = parseFloat(row.querySelector('[data-field="pp"]').value) || 0;
  const pw = parseFloat(row.querySelector('[data-field="pw"]').value) || 0;
  const tw = parseFloat(row.querySelector('[data-field="tw"]').value) || 0;

  const loss = pw > 0 ? (tw / pw) * 100 : 0;
  const pct  = 100 - loss;
  const trueCost = pct > 0 ? (pp / pct) * 100 : 0;

  row.querySelector('[data-out="loss"]').textContent  = pw > 0 ? loss.toFixed(1) + '%' : '—';

  const pctCell = row.querySelector('[data-out="pct"]');
  if (pct > 0) {
    pctCell.textContent = pct.toFixed(1) + '%';
    pctCell.className = 'calc-val yield-pct-cell ' + (pct >= 80 ? 'good' : pct >= 70 ? 'ok' : 'poor');
  } else {
    pctCell.textContent = '—';
    pctCell.className = 'calc-val yield-pct-cell';
  }

  row.querySelector('[data-out="true"]').textContent = trueCost > 0 ? '$' + trueCost.toFixed(4) : '—';
}


async function saveWeekly() {
  const dateVal = document.getElementById('weekly-date').value;
  if (!dateVal) {
    showAlert('weekly-alert', 'error', 'Please select an entry date.');
    return;
  }

  const rows = document.querySelectorAll('#weekly-body tr[data-cut-id]');
  const entries = [];
  rows.forEach(row => {
    const pp = parseFloat(row.querySelector('[data-field="pp"]').value) || 0;
    const pw = parseFloat(row.querySelector('[data-field="pw"]').value) || 0;
    const tw = parseFloat(row.querySelector('[data-field="tw"]').value) || 0;
    if (pp > 0 || pw > 0 || tw > 0) {
      entries.push({
        cut_id: parseInt(row.dataset.cutId),
        entry_date: dateVal,
        purchase_price: pp,
        purchase_weight: pw,
        trim_weight: tw,
        notes: row.querySelector('[data-field="notes"]').value.trim(),
      });
    }
  });

  if (entries.length === 0) {
    showAlert('weekly-alert', 'error', 'No data entered. Fill in at least one row.');
    return;
  }

  try {
    await apiFetch('/api/entries', { method: 'POST', body: JSON.stringify(entries) });
    showAlert('weekly-alert', 'success', `Saved ${entries.length} entry(s) for ${dateVal}.`);
  } catch (e) {
    showAlert('weekly-alert', 'error', 'Save failed: ' + e.message);
  }
}

// ── Cuts ──────────────────────────────────────────────────────────────────────
async function loadCuts() {
  try {
    const cuts = await apiFetch('/api/cuts');
    allCuts = cuts.filter(c => c.active);
    renderCuts(cuts);
    populateReportCutFilter(cuts.filter(c => c.active));
  } catch (e) {
    document.getElementById('cuts-body').innerHTML =
      '<tr><td colspan="5" style="color:#c0392b;padding:20px;">Failed to load cuts.</td></tr>';
  }
}

function renderCuts(cuts) {
  const tbody = document.getElementById('cuts-body');
  if (cuts.length === 0) {
    tbody.innerHTML = '<tr><td colspan="5" style="color:#aaa;text-align:center;padding:24px;">No cuts yet. Click + Add Cut.</td></tr>';
    return;
  }
  tbody.innerHTML = cuts.map(c => `
    <tr class="${c.active ? '' : 'cut-inactive'}">
      <td>${esc(c.name)}</td>
      <td>${esc(c.category || '—')}</td>
      <td>${esc(c.description || '—')}</td>
      <td><span class="${c.active ? 'badge-active' : 'badge-inactive'}">${c.active ? 'Active' : 'Inactive'}</span></td>
      <td style="display:flex;gap:6px;flex-wrap:wrap;">
        <button class="btn btn-secondary btn-sm" onclick="openCutModal(${JSON.stringify(c).replace(/"/g,'&quot;')})">Edit</button>
        ${c.active
          ? `<button class="btn btn-danger btn-sm" onclick="openCutDeleteModal(${c.id}, ${JSON.stringify(c.name).replace(/"/g,'&quot;')})">Delete</button>`
          : `<button class="btn btn-success btn-sm" onclick="reactivateCut(${c.id})">Reactivate</button>
             <button class="btn btn-danger btn-sm" onclick="openCutDeleteModal(${c.id}, ${JSON.stringify(c.name).replace(/"/g,'&quot;')})">Delete</button>`
        }
      </td>
    </tr>
  `).join('');
}

function openCutModal(cut) {
  document.getElementById('cut-modal-title').textContent = cut ? 'Edit Cut' : 'Add Cut';
  document.getElementById('cut-id').value = cut ? cut.id : '';
  document.getElementById('cut-name').value = cut ? cut.name : '';
  document.getElementById('cut-category').value = cut ? (cut.category || '') : '';
  document.getElementById('cut-desc').value = cut ? (cut.description || '') : '';
  document.getElementById('cut-target-yield').value = (cut && cut.target_yield != null) ? cut.target_yield : '';
  const activeField = document.getElementById('cut-active-field');
  if (cut) {
    activeField.classList.remove('hidden');
    document.getElementById('cut-active').checked = !!cut.active;
  } else {
    activeField.classList.add('hidden');
  }
  showModal('cut-modal');
  setTimeout(() => document.getElementById('cut-name').focus(), 80);
}

function closeCutModal() { hideModal('cut-modal'); }

async function saveCut() {
  const id   = document.getElementById('cut-id').value;
  const name = document.getElementById('cut-name').value.trim();
  const cat  = document.getElementById('cut-category').value.trim();
  const desc = document.getElementById('cut-desc').value.trim();
  const active = id ? (document.getElementById('cut-active').checked ? true : false) : true;
  const tyRaw = document.getElementById('cut-target-yield').value.trim();
  const target_yield = tyRaw !== '' ? parseFloat(tyRaw) : null;

  if (!name) { alert('Name is required.'); return; }

  try {
    if (id) {
      await apiFetch('/api/cuts/' + id, { method: 'PUT', body: JSON.stringify({ name, category: cat, description: desc, active, target_yield }) });
    } else {
      await apiFetch('/api/cuts', { method: 'POST', body: JSON.stringify({ name, category: cat, description: desc, target_yield }) });
    }
    closeCutModal();
    loadCuts();
  } catch (e) {
    alert('Save failed: ' + e.message);
  }
}

function openCutDeleteModal(id, name) {
  document.getElementById('cut-delete-id').value = id;
  document.getElementById('cut-delete-name').textContent = name;
  document.getElementById('cut-delete-password').value = '';
  document.getElementById('cut-delete-error').classList.add('hidden');
  showModal('cut-delete-modal');
  setTimeout(() => document.getElementById('cut-delete-password').focus(), 80);
}

async function confirmCutDelete() {
  const id       = document.getElementById('cut-delete-id').value;
  const password = document.getElementById('cut-delete-password').value;
  const errEl    = document.getElementById('cut-delete-error');
  errEl.classList.add('hidden');
  try {
    await apiFetch('/api/cuts/' + id, {
      method: 'DELETE',
      body: JSON.stringify({ password })
    });
    closeAll();
    loadCuts();
  } catch (e) {
    errEl.textContent = e.message.includes('403') ? 'Incorrect password.' : e.message;
    errEl.classList.remove('hidden');
    document.getElementById('cut-delete-password').value = '';
    document.getElementById('cut-delete-password').focus();
  }
}

async function reactivateCut(id) {
  try {
    const allCutsData = await apiFetch('/api/cuts');
    const cut = allCutsData.find(c => c.id === id);
    if (!cut) return;
    await apiFetch('/api/cuts/' + id, {
      method: 'PUT',
      body: JSON.stringify({ name: cut.name, category: cut.category, description: cut.description, active: true })
    });
    loadCuts();
  } catch (e) { alert('Error: ' + e.message); }
}

// ── Reports ───────────────────────────────────────────────────────────────────
function populateReportCutFilter(cuts) {
  const sel = document.getElementById('report-cut-filter');
  if (!sel) return;
  const prev = sel.value;
  sel.innerHTML = '<option value="">All Cuts</option>' +
    cuts.map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join('');
  if (prev) sel.value = prev;
}

function initReportDates() {
  const to = new Date();
  const from = new Date();
  from.setDate(from.getDate() - 84); // 12 weeks
  const fromEl = document.getElementById('report-from');
  const toEl   = document.getElementById('report-to');
  if (fromEl) fromEl.value = from.toISOString().slice(0, 10);
  if (toEl)   toEl.value   = to.toISOString().slice(0, 10);
}

async function runReport() {
  const typeEl  = document.getElementById('report-type');
  const fromEl  = document.getElementById('report-from');
  const toEl    = document.getElementById('report-to');
  const cutIdEl = document.getElementById('report-cut-filter');
  if (!typeEl) return;

  const type  = typeEl.value;
  const from  = fromEl ? fromEl.value : '';
  const to    = toEl   ? toEl.value   : '';
  const cutId = cutIdEl ? cutIdEl.value : '';

  let url = '/api/reports?';
  if (from)  url += 'from=' + from + '&';
  if (to)    url += 'to='   + to   + '&';
  if (cutId) url += 'cut_id=' + cutId + '&';

  try {
    const data = await apiFetch(url);
    if (data.length === 0) {
      document.getElementById('chart-empty').textContent = 'No data found for the selected range.';
      document.getElementById('chart-empty').style.display = 'flex';
      document.getElementById('report-chart').style.display = 'none';
      document.getElementById('report-table').style.display = 'none';
      return;
    }
    renderReportChart(data, type);
    renderReportTable(data, type);
  } catch (e) {
    alert('Report failed: ' + e.message);
  }
}

function exportCSV() {
  const fromEl  = document.getElementById('report-from');
  const toEl    = document.getElementById('report-to');
  const from  = fromEl ? fromEl.value : '';
  const to    = toEl   ? toEl.value   : '';
  let url = '/api/entries/export?';
  if (from) url += 'from=' + from + '&';
  if (to)   url += 'to='   + to   + '&';
  window.location.href = url;
}

function getMetricValue(row, type) {
  switch (type) {
    case 'purchase_cost': return row.purchase_price;
    case 'yield_pct':     return row.yield_pct;
    case 'true_cost':     return row.adjusted_cost;
    default:              return 0;
  }
}

function getMetricLabel(type) {
  switch (type) {
    case 'purchase_cost': return 'Purchase Cost ($/lb)';
    case 'yield_pct':     return 'Yield %';
    case 'true_cost':     return 'True Cost ($/lb)';
    default:              return '';
  }
}

function renderReportChart(data, type) {
  const dates    = [...new Set(data.map(r => r.entry_date))].sort();
  const cutNames = [...new Set(data.map(r => r.cut_name))];

  const datasets = cutNames.map((name, i) => {
    const cutData = dates.map(d => {
      const row = data.find(r => r.entry_date === d && r.cut_name === name);
      return row ? getMetricValue(row, type) : null;
    });
    return {
      label: name,
      data: cutData,
      borderColor: CHART_COLORS[i % CHART_COLORS.length],
      backgroundColor: CHART_COLORS[i % CHART_COLORS.length] + '22',
      tension: 0.3,
      pointRadius: 4,
      spanGaps: true
    };
  });

  document.getElementById('chart-empty').style.display = 'none';
  const canvas = document.getElementById('report-chart');
  canvas.style.display = 'block';

  if (chartInstance) chartInstance.destroy();
  chartInstance = new Chart(canvas, {
    type: 'line',
    data: { labels: dates, datasets },
    options: {
      responsive: true,
      plugins: {
        legend: { position: 'top' },
        title: { display: true, text: getMetricLabel(type) }
      },
      scales: {
        y: {
          beginAtZero: false,
          ticks: {
            callback: (v) => type === 'yield_pct' ? v + '%' : '$' + v.toFixed(2)
          }
        }
      }
    }
  });
}

function renderReportTable(data, type) {
  const thead = document.getElementById('report-thead');
  const tbody = document.getElementById('report-tbody');
  const table = document.getElementById('report-table');

  thead.innerHTML = `<tr>
    <th>Date</th>
    <th>Cut</th>
    <th>Category</th>
    <th>Purchase $/lb</th>
    <th>Purchase Lbs</th>
    <th>Trim Lbs</th>
    <th>Yield Loss %</th>
    <th>Yield %</th>
    <th>True Cost/lb</th>
  </tr>`;

  const sorted = [...data].sort((a, b) => {
    if (b.entry_date !== a.entry_date) return b.entry_date.localeCompare(a.entry_date);
    return (a.cut_name || '').localeCompare(b.cut_name || '');
  });

  tbody.innerHTML = sorted.map(r => {
    const yClass = r.yield_pct >= 80 ? 'good' : r.yield_pct >= 70 ? 'ok' : 'poor';
    return `<tr>
      <td>${r.entry_date}</td>
      <td>${esc(r.cut_name)}</td>
      <td>${esc(r.category || '—')}</td>
      <td>${fmt$(r.purchase_price)}</td>
      <td>${fmtLbs(r.purchase_weight)}</td>
      <td>${fmtLbs(r.trim_weight)}</td>
      <td>${fmtPct(r.yield_loss)}</td>
      <td class="yield-pct-cell ${yClass}">${fmtPct(r.yield_pct)}</td>
      <td style="font-weight:600;color:#8b0000;">${fmt$(r.adjusted_cost)}</td>
    </tr>`;
  }).join('');

  table.style.display = '';
}

// ── Delete Entries ────────────────────────────────────────────────────────────
function openDeleteModal() {
  const dateVal = document.getElementById('weekly-date').value;
  if (!dateVal) {
    showAlert('weekly-alert', 'error', 'Select a date first.');
    return;
  }
  document.getElementById('delete-date-display').textContent = 'Date: ' + dateVal;
  document.getElementById('delete-password').value = '';
  document.getElementById('delete-error').classList.add('hidden');
  showModal('delete-modal');
  setTimeout(() => document.getElementById('delete-password').focus(), 80);
}

async function confirmDelete() {
  const dateVal  = document.getElementById('weekly-date').value;
  const password = document.getElementById('delete-password').value;
  const errEl    = document.getElementById('delete-error');

  errEl.classList.add('hidden');
  try {
    await apiFetch('/api/entries/delete', {
      method: 'POST',
      body: JSON.stringify({ date: dateVal, password })
    });
    closeAll();
    showAlert('weekly-alert', 'success', 'Entries for ' + dateVal + ' deleted.');
    loadWeeklyEntries();
  } catch (e) {
    errEl.textContent = e.message.includes('403') ? 'Incorrect password.' : e.message;
    errEl.classList.remove('hidden');
    document.getElementById('delete-password').value = '';
    document.getElementById('delete-password').focus();
  }
}

// ── Settings ──────────────────────────────────────────────────────────────────
async function loadSettings() {
  try {
    const s = await apiFetch('/api/settings');
    const nameEl    = document.getElementById('s-app-name');
    const intEl     = document.getElementById('s-interval');
    const emailEl   = document.getElementById('s-alert-email');
    const enabledEl = document.getElementById('s-alert-enabled');
    if (nameEl)    nameEl.value    = s.app_name || '';
    if (intEl)     intEl.value     = s.update_interval || 7;
    if (emailEl)   emailEl.value   = s.alert_email || '';
    if (enabledEl) enabledEl.checked = !!s.alert_enabled;
  } catch (e) {
    const el = document.getElementById('settings-result');
    if (el) el.textContent = 'Failed to load settings.';
  }
}

async function saveSettings() {
  const nameEl    = document.getElementById('s-app-name');
  const intEl     = document.getElementById('s-interval');
  const emailEl   = document.getElementById('s-alert-email');
  const enabledEl = document.getElementById('s-alert-enabled');
  const result    = document.getElementById('settings-result');

  const name         = nameEl    ? nameEl.value.trim()       : undefined;
  const interval     = intEl     ? parseInt(intEl.value) || 7 : undefined;
  const alert_email  = emailEl   ? emailEl.value.trim()       : undefined;
  const alert_enabled = enabledEl ? enabledEl.checked          : undefined;

  const payload = {};
  if (name !== undefined)          payload.app_name       = name;
  if (interval !== undefined)      payload.update_interval = interval;
  if (alert_email !== undefined)   payload.alert_email    = alert_email;
  if (alert_enabled !== undefined) payload.alert_enabled  = alert_enabled;

  try {
    await apiFetch('/api/settings', { method: 'POST', body: JSON.stringify(payload) });
    if (result) {
      result.textContent = 'Saved.';
      result.style.color = '#27ae60';
      setTimeout(() => { result.textContent = ''; }, 3000);
    }
  } catch (e) {
    if (result) {
      result.textContent = 'Error: ' + e.message;
      result.style.color = '#c0392b';
    }
  }
}

// ── Users ─────────────────────────────────────────────────────────────────────
async function loadUsers() {
  const tbody = document.getElementById('users-body');
  if (!tbody) return;
  try {
    const users = await apiFetch('/api/users');
    renderUsers(users);
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="6" style="color:#c0392b;padding:20px;">Failed to load users: ${esc(e.message)}</td></tr>`;
  }
}

function renderUsers(users) {
  const tbody = document.getElementById('users-body');
  if (!tbody) return;
  if (users.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6" style="color:#aaa;text-align:center;padding:24px;">No users yet. Click + Add User.</td></tr>';
    return;
  }
  tbody.innerHTML = users.map(u => {
    const lastLogin = u.last_login ? new Date(u.last_login).toLocaleDateString() : 'Never';
    return `
      <tr>
        <td style="font-weight:600;">${esc(u.username)}</td>
        <td style="color:#888;font-size:0.85rem;">${esc(u.email || '—')}</td>
        <td><span class="role-badge ${u.role}">${u.role}</span></td>
        <td><span class="${u.active ? 'user-badge-active' : 'user-badge-inactive'}">${u.active ? 'Active' : 'Inactive'}</span></td>
        <td style="font-size:0.82rem;color:#888;">${lastLogin}</td>
        <td style="display:flex;gap:6px;flex-wrap:wrap;">
          <button class="btn btn-secondary btn-sm" onclick="openUserModal(${JSON.stringify(u).replace(/"/g,'&quot;')})">Edit</button>
          ${u.active
            ? `<button class="btn btn-danger btn-sm" onclick="deactivateUser(${u.id})">Deactivate</button>`
            : ''
          }
        </td>
      </tr>
    `;
  }).join('');
}

function openUserModal(user) {
  const uid = user ? user.id : null;
  document.getElementById('user-id').value = uid || '';
  document.getElementById('user-username').value = user ? user.username : '';
  document.getElementById('user-email').value = user ? (user.email || '') : '';
  document.getElementById('user-role').value = user ? user.role : 'butcher';
  document.getElementById('user-modal-title').textContent = user ? 'Edit User' : 'Add User';

  const pwSection    = document.getElementById('user-password-section');
  const newPwSection = document.getElementById('user-newpassword-section');
  const activeSection = document.getElementById('user-active-section');

  if (user) {
    pwSection.classList.add('hidden');
    newPwSection.classList.remove('hidden');
    activeSection.classList.remove('hidden');
    document.getElementById('user-new-password').value = '';
    document.getElementById('user-active').checked = !!user.active;
  } else {
    pwSection.classList.remove('hidden');
    newPwSection.classList.add('hidden');
    activeSection.classList.add('hidden');
    document.getElementById('user-password').value = '';
  }

  const errEl = document.getElementById('user-modal-error');
  if (errEl) errEl.classList.add('hidden');

  showModal('user-modal');
  setTimeout(() => document.getElementById('user-username').focus(), 80);
}

async function saveUser() {
  const uid      = document.getElementById('user-id').value;
  const username = document.getElementById('user-username').value.trim();
  const email    = document.getElementById('user-email').value.trim();
  const role     = document.getElementById('user-role').value;
  const errEl    = document.getElementById('user-modal-error');

  if (errEl) errEl.classList.add('hidden');

  if (!uid) {
    // Create
    const password = document.getElementById('user-password').value.trim();
    if (!username || !password) {
      if (errEl) { errEl.textContent = 'Username and password are required.'; errEl.classList.remove('hidden'); }
      return;
    }
    try {
      await apiFetch('/api/users', { method: 'POST', body: JSON.stringify({ username, email, password, role }) });
      closeAll();
      showAlert('users-alert', 'success', `User "${username}" created.`);
      loadUsers();
    } catch (e) {
      if (errEl) { errEl.textContent = e.message; errEl.classList.remove('hidden'); }
    }
  } else {
    // Update
    const new_password = document.getElementById('user-new-password').value.trim();
    const active       = document.getElementById('user-active').checked;
    const payload = { role, active, email };
    if (new_password) payload.new_password = new_password;
    try {
      await apiFetch('/api/users/' + uid, { method: 'PUT', body: JSON.stringify(payload) });
      closeAll();
      showAlert('users-alert', 'success', 'User updated.');
      loadUsers();
    } catch (e) {
      if (errEl) { errEl.textContent = e.message; errEl.classList.remove('hidden'); }
    }
  }
}

async function deactivateUser(id) {
  if (!confirm('Deactivate this user? They will no longer be able to log in.')) return;
  try {
    await apiFetch('/api/users/' + id, { method: 'DELETE' });
    showAlert('users-alert', 'success', 'User deactivated.');
    loadUsers();
  } catch (e) {
    showAlert('users-alert', 'error', 'Error: ' + e.message);
  }
}

// ── Idle timeout (1 hour) ─────────────────────────────────────────────────────
(function () {
  const IDLE_MS = 60 * 60 * 1000;
  let idleTimer;
  function resetIdle() {
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => { window.location.href = '/logout'; }, IDLE_MS);
  }
  ['mousemove', 'mousedown', 'keydown', 'touchstart', 'scroll', 'click'].forEach(evt =>
    document.addEventListener(evt, resetIdle, { passive: true })
  );
  resetIdle();
})();

// ── Init ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  try {
    const cuts = await apiFetch('/api/cuts');
    allCuts = cuts.filter(c => c.active);
    populateReportCutFilter(allCuts);
  } catch (_) {}

  initReportDates();
  loadDashboard();
});
