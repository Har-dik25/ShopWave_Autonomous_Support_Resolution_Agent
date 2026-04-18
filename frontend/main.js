import './style.css';
import Chart from 'chart.js/auto';

// ═══════════════════════════════════════════════════════════
//  ShopWave — Autonomous Support Command Center v5
//  Premium Frontend Logic
// ═══════════════════════════════════════════════════════════

// --- Global State ---
const state = {
  activeView: 'dashboard',
  isRunning: false,
  results: [],
  auditLog: [],
  evaluation: null,
  progress: 0,
  total: 0,
  charts: {},
  selectedTicketId: null,
};

// --- DOM References ---
const views = document.querySelectorAll('.page-view');
const navItems = document.querySelectorAll('.nav-item[data-view]');
const runBtn = document.getElementById('run-sweep-btn');
const stream = document.getElementById('reasoning-stream');
const viewTitle = document.getElementById('view-title');
const viewSubtitle = document.getElementById('view-subtitle');
const toastContainer = document.getElementById('toast-container');
const progressBar = document.getElementById('sweep-progress');
const progressFill = document.getElementById('progress-fill');
const progressCount = document.getElementById('progress-count');
const ticketFilter = document.getElementById('ticket-filter');

// --- API Service ---
const API_BASE = 'http://localhost:8000/api';

// ═══════════════════════════════════════════════════════════
//  INITIALIZATION
// ═══════════════════════════════════════════════════════════
function init() {
  setupNavigation();
  setupCharts();
  syncResults();
  setupSSE();
  setupTicketFilter();

  runBtn.addEventListener('click', startSweep);

  // Stagger-in animation for metric cards
  document.querySelectorAll('.metric-card').forEach((card, i) => {
    card.style.opacity = '0';
    card.style.transform = 'translateY(20px)';
    setTimeout(() => {
      card.style.transition = 'all 0.6s cubic-bezier(0.16, 1, 0.3, 1)';
      card.style.opacity = '1';
      card.style.transform = 'translateY(0)';
    }, 200 + i * 100);
  });
}

// ═══════════════════════════════════════════════════════════
//  NAVIGATION
// ═══════════════════════════════════════════════════════════
function setupNavigation() {
  navItems.forEach(item => {
    item.addEventListener('click', () => {
      const target = item.dataset.view;

      navItems.forEach(i => i.classList.remove('active'));
      item.classList.add('active');

      views.forEach(v => {
        v.classList.toggle('active', v.id === `view-${target}`);
      });

      const titles = {
        dashboard: ['Command Center', 'Autonomous Support Resolution OS'],
        tickets: ['Resolution Hub', 'Inspect Neural Reasoning Chains'],
        analytics: ['Pulse Analytics', 'System-wide Performance Metrics'],
      };
      [viewTitle.textContent, viewSubtitle.textContent] = titles[target] || ['', ''];

      state.activeView = target;
      if (target === 'tickets') renderTicketHub();
      if (target === 'analytics') updateAnalytics();
    });
  });
}

// ═══════════════════════════════════════════════════════════
//  CHARTS
// ═══════════════════════════════════════════════════════════
function setupCharts() {
  // Global chart defaults for dark theme
  Chart.defaults.color = 'rgba(255, 255, 255, 0.5)';
  Chart.defaults.borderColor = 'rgba(255, 255, 255, 0.04)';

  const miniOptions = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: { legend: { display: false }, tooltip: { enabled: false } },
    scales: {
      x: { display: false },
      y: { display: false },
    },
    elements: {
      line: {
        tension: 0.4,
        borderWidth: 2,
        borderColor: '#3b82f6',
        fill: true,
        backgroundColor: createGradient('chart-mini-throughput', '#3b82f6'),
      },
      point: { radius: 0, hoverRadius: 0 },
    },
  };

  // Mini Throughput Chart
  const ctxMini = document.getElementById('chart-mini-throughput');
  state.charts.throughput = new Chart(ctxMini, {
    type: 'line',
    data: {
      labels: Array(12).fill(''),
      datasets: [{
        data: [2, 4, 3, 7, 5, 9, 6, 12, 8, 15, 11, 18],
        fill: true,
        backgroundColor: 'rgba(59, 130, 246, 0.08)',
        borderColor: '#3b82f6',
        borderWidth: 2,
        tension: 0.4,
        pointRadius: 0,
      }],
    },
    options: miniOptions,
  });

  // Health Chart
  const ctxHealth = document.getElementById('chart-health');
  state.charts.health = new Chart(ctxHealth, {
    type: 'bar',
    data: {
      labels: ['Cycle 1', 'Cycle 2', 'Cycle 3', 'Cycle 4', 'Cycle 5'],
      datasets: [{
        data: [98, 99, 97, 100, 99],
        backgroundColor: 'rgba(52, 211, 153, 0.3)',
        borderColor: '#34d399',
        borderWidth: 1,
        borderRadius: 6,
        barPercentage: 0.6,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { enabled: true } },
      scales: {
        x: {
          display: true,
          grid: { display: false },
          ticks: { font: { size: 9 } },
        },
        y: {
          display: true,
          grid: { color: 'rgba(255, 255, 255, 0.03)' },
          min: 90,
          max: 100,
          ticks: { font: { size: 9 }, callback: v => `${v}%` },
        },
      },
    },
  });
}

function createGradient(canvasId, color) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return color;
  const ctx = canvas.getContext('2d');
  const gradient = ctx.createLinearGradient(0, 0, 0, canvas.height);
  gradient.addColorStop(0, `${color}22`);
  gradient.addColorStop(1, `${color}02`);
  return gradient;
}

// ═══════════════════════════════════════════════════════════
//  SSE — Server-Sent Events
// ═══════════════════════════════════════════════════════════
function setupSSE() {
  try {
    const eventSource = new EventSource(`${API_BASE}/events`);

    eventSource.onmessage = (event) => {
      const data = JSON.parse(event.data);

      if (data.type === 'reasoning_step') {
        appendStream(data.ticket_id, data.data);
      } else if (data.type === 'ticket_complete') {
        state.progress++;
        updateProgressBar();
        updateProgressUI();
      } else if (data.type === 'sweep_complete') {
        state.isRunning = false;
        stream.classList.remove('scanning');
        showToast('success', `✅ Sweep Complete — ${state.progress} tickets processed in ${data.elapsed?.toFixed(1) || '?'}s`);
        syncResults();
        hideProgressBar();
      }
    };

    eventSource.onerror = () => {
      console.warn('SSE connection issue. Will reconnect...');
    };
  } catch (e) {
    console.warn('SSE not available:', e);
  }
}

function appendStream(tid, step) {
  // Remove placeholder
  const placeholder = stream.querySelector('.stream-placeholder');
  if (placeholder) placeholder.remove();

  const div = document.createElement('div');
  div.className = 'stream-item';

  const iconMap = {
    get_order: '📦',
    get_order_details: '📦',
    check_inventory: '📊',
    issue_refund: '💰',
    escalate: '⬆️',
    escalate_to_human: '⬆️',
    send_reply: '📨',
    send_response: '📨',
    lookup_policy: '📋',
    check_shipping_status: '🚚',
  };
  const icon = iconMap[step.action] || '💭';

  const thought = step.thought || step.reasoning || '';
  const truncated = thought.length > 90 ? thought.substring(0, 90) + '…' : thought;

  div.innerHTML = `
    <span class="stream-tid">${tid}</span>
    <span class="stream-icon">${icon}</span>
    <span class="stream-thought">${truncated}</span>
  `;

  stream.prepend(div);
  if (stream.children.length > 50) stream.lastChild.remove();
}

// ═══════════════════════════════════════════════════════════
//  SWEEP EXECUTION
// ═══════════════════════════════════════════════════════════
async function startSweep() {
  if (state.isRunning) return;

  // Clear stream
  stream.innerHTML = '';
  state.isRunning = true;
  state.progress = 0;
  runBtn.disabled = true;
  runBtn.classList.add('loading');
  stream.classList.add('scanning');
  stream.style.position = 'relative';

  showToast('info', '⚡ Autonomous sweep initiated...');
  showProgressBar();

  try {
    const res = await fetch(`${API_BASE}/run`, { method: 'POST' });
    const data = await res.json();
    state.total = data.total || 0;
    updateProgressBar();
  } catch (e) {
    state.isRunning = false;
    runBtn.disabled = false;
    runBtn.classList.remove('loading');
    hideProgressBar();
    showToast('error', '❌ Failed to start sweep — is the server running?');
  }
}

// ═══════════════════════════════════════════════════════════
//  DATA SYNC
// ═══════════════════════════════════════════════════════════
async function syncResults() {
  try {
    const res = await fetch(`${API_BASE}/results`);
    const data = await res.json();

    state.results = data.results || [];
    state.auditLog = data.audit_log || [];
    state.evaluation = data.evaluation || null;

    updateDashboardUI();
    if (state.activeView === 'tickets') renderTicketHub();
    if (state.activeView === 'analytics') updateAnalytics();
  } catch (e) {
    console.warn('Sync failed (server may not be running):', e.message);
  }
}

// ═══════════════════════════════════════════════════════════
//  DASHBOARD UI
// ═══════════════════════════════════════════════════════════
function updateDashboardUI() {
  if (!state.evaluation) return;

  const evalData = state.evaluation;

  // Animate counters
  animateCounter('stat-total', evalData.total);
  animateCounter('stat-resolved', evalData.correct);
  animateCounter('stat-escalated', evalData.partial);

  const rate = evalData.total > 0 ? Math.round((evalData.correct / evalData.total) * 100) : 0;
  document.getElementById('stat-autonomous-rate').textContent = `${rate}%`;

  const accuracy = Math.round((evalData.weighted_score || 0) * 100);
  document.getElementById('stat-eval').textContent = `${accuracy}%`;

  // Animate the gauge fill
  setTimeout(() => {
    document.getElementById('eval-progress').style.width = `${accuracy}%`;
  }, 300);

  // Health metrics
  const failures = state.auditLog.filter(e => e.tool && e.tool.includes('failure')).length;
  const recoveries = state.auditLog.filter(e => e.recovered_from_failure).length;
  document.getElementById('res-recoveries').textContent = `${recoveries}/${failures || 0}`;
  document.getElementById('stat-tools').textContent = state.auditLog.length || '--';

  // Average latency estimation
  const latency = state.auditLog.length > 0
    ? Math.round((state.auditLog.reduce((s, e) => s + (e.duration || 0), 0) / state.auditLog.length) * 1000)
    : 0;
  document.getElementById('stat-latency').textContent = latency > 0 ? `${latency}ms` : '--ms';

  runBtn.disabled = false;
  runBtn.classList.remove('loading');
}

function animateCounter(elementId, target) {
  const el = document.getElementById(elementId);
  if (!el) return;

  const start = parseInt(el.textContent) || 0;
  const duration = 800;
  const startTime = performance.now();

  function tick(now) {
    const elapsed = now - startTime;
    const progress = Math.min(elapsed / duration, 1);
    // Ease out cubic
    const eased = 1 - Math.pow(1 - progress, 3);
    el.textContent = Math.round(start + (target - start) * eased);
    if (progress < 1) requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
}

function updateProgressUI() {
  // Called on each ticket_complete
}

// ═══════════════════════════════════════════════════════════
//  PROGRESS BAR
// ═══════════════════════════════════════════════════════════
function showProgressBar() {
  progressBar.classList.add('active');
  progressFill.style.width = '0%';
  progressCount.textContent = '0 / 0';
}

function hideProgressBar() {
  setTimeout(() => {
    progressBar.classList.remove('active');
  }, 2000);
}

function updateProgressBar() {
  if (state.total > 0) {
    const pct = Math.round((state.progress / state.total) * 100);
    progressFill.style.width = `${pct}%`;
    progressCount.textContent = `${state.progress} / ${state.total}`;
  }
}

// ═══════════════════════════════════════════════════════════
//  TICKET HUB (Resolution Hub)
// ═══════════════════════════════════════════════════════════
function setupTicketFilter() {
  if (!ticketFilter) return;
  ticketFilter.addEventListener('input', () => {
    renderTicketHub(ticketFilter.value.toLowerCase().trim());
  });
}

function renderTicketHub(filter = '') {
  const list = document.getElementById('ticket-list');
  if (!list) return;

  if (!state.results.length) {
    list.innerHTML = `
      <div style="padding: 2rem; text-align: center; color: rgba(255,255,255,0.2); font-size: 0.85rem;">
        No tickets resolved yet. Run a sweep first.
      </div>
    `;
    return;
  }

  const filtered = state.results.filter(r => {
    if (!filter) return true;
    return (
      (r.ticket_id || '').toLowerCase().includes(filter) ||
      (r.category || '').toLowerCase().includes(filter) ||
      (r.resolution || '').toLowerCase().includes(filter)
    );
  });

  list.innerHTML = filtered.map(r => {
    const isSelected = r.ticket_id === state.selectedTicketId;
    return `
      <div class="scroller-item ${isSelected ? 'selected' : ''}" data-tid="${r.ticket_id}">
        <div class="item-top">
          <span class="item-id">${r.ticket_id}</span>
          <span class="item-cat">${r.category || 'unknown'}</span>
        </div>
        <div class="item-status ${r.resolution || ''}">${formatResolution(r.resolution)}</div>
      </div>
    `;
  }).join('');

  // Attach click events
  list.querySelectorAll('.scroller-item').forEach(el => {
    el.addEventListener('click', () => {
      const tid = el.dataset.tid;
      state.selectedTicketId = tid;

      // Update selection styling
      list.querySelectorAll('.scroller-item').forEach(s => s.classList.remove('selected'));
      el.classList.add('selected');

      inspectTicket(tid);
    });
  });
}

function formatResolution(res) {
  const map = {
    full_resolution: '✓ Resolved',
    partial_resolution: '⬆ Escalated',
    resolved: '✓ Resolved',
    escalated: '⬆ Escalated',
    awaiting_customer_info: '⏳ Awaiting',
    pending: '⏳ Pending',
    incorrect: '✗ Incorrect',
    error: '⚠ Error',
  };
  return map[res] || res || '--';
}

function inspectTicket(tid) {
  const ticket = state.results.find(r => r.ticket_id === tid);
  const inspector = document.getElementById('ticket-inspector');
  if (!ticket || !inspector) return;

  const steps = ticket.reasoning_steps || ticket.steps || [];
  const confidence = ticket.confidence_score || ticket.confidence || 0;

  inspector.innerHTML = `
    <div class="inspector-header">
      <h2>🔍 Inspection: ${tid}</h2>
      <div class="conf-pill">🎯 Confidence: ${Math.round(confidence * 100)}%</div>
    </div>
    <div class="decision-timeline">
      ${steps.length > 0
        ? steps.map((s, i) => `
          <div class="timeline-node glass-medium">
            <div class="node-num">${String(i + 1).padStart(2, '0')}</div>
            <div class="node-content">
              <p class="node-thought">${s.thought || s.reasoning || ''}</p>
              ${s.action ? `<div class="node-action">🔧 ${s.action}${s.action_input ? `(${truncObj(s.action_input)})` : ''}</div>` : ''}
              ${s.observation ? `<div class="node-obs">${truncStr(s.observation, 200)}</div>` : ''}
            </div>
          </div>
        `).join('')
        : '<div class="inspector-empty">No reasoning steps recorded for this ticket.</div>'
      }
    </div>
  `;
}

function truncStr(str, max = 120) {
  if (!str) return '';
  return str.length > max ? str.substring(0, max) + '…' : str;
}

function truncObj(obj) {
  if (!obj) return '';
  if (typeof obj === 'string') return truncStr(obj, 50);
  try {
    const s = JSON.stringify(obj);
    return truncStr(s, 60);
  } catch {
    return '...';
  }
}

// ═══════════════════════════════════════════════════════════
//  ANALYTICS — Full Rebuild
// ═══════════════════════════════════════════════════════════
function updateAnalytics() {
  if (!state.results.length) return;

  const results = state.results;
  const palette = [
    '#3b82f6', '#34d399', '#a78bfa', '#fbbf24', '#f87171',
    '#22d3ee', '#f472b6', '#fb923c', '#4ade80', '#c084fc',
  ];

  // ── KPI Strip ──
  const resolved = results.filter(r =>
    r.resolution === 'full_resolution' || r.resolution === 'resolved'
  ).length;
  const escalated = results.filter(r =>
    r.resolution === 'partial_resolution' || r.resolution === 'escalated'
  ).length;
  const avgConf = results.reduce((s, r) => s + (r.confidence_score || r.confidence || 0), 0) / results.length;
  const totalSteps = results.reduce((s, r) => s + ((r.reasoning_steps || r.steps || []).length), 0);
  const avgSteps = (totalSteps / results.length).toFixed(1);
  const accuracy = state.evaluation ? Math.round((state.evaluation.weighted_score || 0) * 100) : '--';

  document.getElementById('kpi-accuracy').textContent = `${accuracy}%`;
  document.getElementById('kpi-resolved').textContent = resolved;
  document.getElementById('kpi-escalated').textContent = escalated;
  document.getElementById('kpi-avg-conf').textContent = `${Math.round(avgConf * 100)}%`;
  document.getElementById('kpi-tools').textContent = state.auditLog.length || '--';
  document.getElementById('kpi-avg-steps').textContent = avgSteps;

  // Stagger-in KPI pills
  document.querySelectorAll('.kpi-pill').forEach((pill, i) => {
    pill.style.opacity = '0';
    pill.style.transform = 'translateY(12px)';
    setTimeout(() => {
      pill.style.transition = 'all 0.5s cubic-bezier(0.16, 1, 0.3, 1)';
      pill.style.opacity = '1';
      pill.style.transform = 'translateY(0)';
    }, 100 + i * 80);
  });

  // ── 1. Resolution Timeline (Area Line Chart) ──
  const ctxVelocity = document.getElementById('chart-velocity');
  if (state.charts.velocity) state.charts.velocity.destroy();

  const timelineLabels = results.map((r, i) => r.ticket_id || `#${i + 1}`);
  const confData = results.map(r => Math.round((r.confidence_score || r.confidence || 0) * 100));

  // Create gradient
  const velCtx = ctxVelocity.getContext('2d');
  const velGradient = velCtx.createLinearGradient(0, 0, 0, 250);
  velGradient.addColorStop(0, 'rgba(59, 130, 246, 0.25)');
  velGradient.addColorStop(1, 'rgba(59, 130, 246, 0.01)');

  state.charts.velocity = new Chart(ctxVelocity, {
    type: 'line',
    data: {
      labels: timelineLabels,
      datasets: [{
        label: 'Confidence %',
        data: confData,
        fill: true,
        backgroundColor: velGradient,
        borderColor: '#3b82f6',
        borderWidth: 2.5,
        tension: 0.35,
        pointRadius: 5,
        pointBackgroundColor: '#0f172a',
        pointBorderColor: '#3b82f6',
        pointBorderWidth: 2,
        pointHoverRadius: 8,
        pointHoverBackgroundColor: '#3b82f6',
        pointHoverBorderColor: '#fff',
        pointHoverBorderWidth: 2,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { intersect: false, mode: 'index' },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: 'rgba(10, 14, 20, 0.95)',
          borderColor: 'rgba(59, 130, 246, 0.3)',
          borderWidth: 1,
          titleColor: '#fff',
          bodyColor: 'rgba(255,255,255,0.7)',
          padding: 12,
          cornerRadius: 10,
          displayColors: false,
          callbacks: {
            title: (items) => `Ticket: ${items[0].label}`,
            label: (item) => `Confidence: ${item.raw}%`,
          },
        },
      },
      scales: {
        x: {
          grid: { display: false },
          ticks: {
            font: { size: 9, family: "'JetBrains Mono', monospace" },
            color: 'rgba(255,255,255,0.3)',
            maxRotation: 45,
          },
        },
        y: {
          min: 0,
          max: 100,
          grid: { color: 'rgba(255, 255, 255, 0.03)' },
          ticks: {
            font: { size: 10 },
            color: 'rgba(255,255,255,0.3)',
            callback: v => `${v}%`,
            stepSize: 20,
          },
        },
      },
    },
  });

  // ── 2. Category Pulse (Doughnut) ──
  const ctxCat = document.getElementById('chart-categories');
  if (state.charts.categories) state.charts.categories.destroy();

  const catNames = [...new Set(results.map(r => r.category || 'unknown'))];
  const catCounts = catNames.map(name => results.filter(r => (r.category || 'unknown') === name).length);

  state.charts.categories = new Chart(ctxCat, {
    type: 'doughnut',
    data: {
      labels: catNames.map(n => n.replace(/_/g, ' ')),
      datasets: [{
        data: catCounts,
        backgroundColor: palette.slice(0, catNames.length).map(c => c + '80'),
        borderColor: palette.slice(0, catNames.length),
        borderWidth: 2,
        hoverOffset: 12,
        spacing: 3,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: '62%',
      plugins: {
        legend: {
          display: true,
          position: 'right',
          labels: {
            color: 'rgba(255, 255, 255, 0.65)',
            font: { size: 11, family: "'Inter', sans-serif", weight: 500 },
            padding: 14,
            usePointStyle: true,
            pointStyleWidth: 10,
          },
        },
        tooltip: {
          backgroundColor: 'rgba(10, 14, 20, 0.95)',
          borderColor: 'rgba(255,255,255,0.1)',
          borderWidth: 1,
          padding: 10,
          cornerRadius: 10,
        },
      },
    },
  });

  // ── 3. Resolution Outcomes (Polar Area) ──
  const ctxOutcomes = document.getElementById('chart-outcomes');
  if (state.charts.outcomes) state.charts.outcomes.destroy();

  const outcomeMap = {};
  results.forEach(r => {
    const key = r.resolution || 'unknown';
    outcomeMap[key] = (outcomeMap[key] || 0) + 1;
  });

  const outcomeLabels = Object.keys(outcomeMap).map(k => formatResolution(k));
  const outcomeData = Object.values(outcomeMap);
  const outcomeColors = [
    'rgba(52, 211, 153, 0.5)', 'rgba(251, 191, 36, 0.5)',
    'rgba(96, 165, 250, 0.5)', 'rgba(248, 113, 113, 0.5)',
    'rgba(167, 139, 250, 0.5)', 'rgba(34, 211, 238, 0.5)',
  ];

  state.charts.outcomes = new Chart(ctxOutcomes, {
    type: 'polarArea',
    data: {
      labels: outcomeLabels,
      datasets: [{
        data: outcomeData,
        backgroundColor: outcomeColors.slice(0, outcomeData.length),
        borderColor: outcomeColors.slice(0, outcomeData.length).map(c => c.replace('0.5', '1')),
        borderWidth: 1.5,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          display: true,
          position: 'right',
          labels: {
            color: 'rgba(255, 255, 255, 0.65)',
            font: { size: 11, family: "'Inter', sans-serif", weight: 500 },
            padding: 12,
            usePointStyle: true,
          },
        },
      },
      scales: {
        r: {
          grid: { color: 'rgba(255, 255, 255, 0.04)' },
          ticks: { display: false },
        },
      },
    },
  });

  // ── 4. Confidence Distribution (Gradient Histogram) ──
  const ctxConf = document.getElementById('chart-confidence');
  if (state.charts.confidence) state.charts.confidence.destroy();

  const confBuckets = [0, 0, 0, 0, 0];
  results.forEach(r => {
    const c = (r.confidence_score || r.confidence || 0) * 100;
    const idx = Math.min(Math.floor(c / 20), 4);
    confBuckets[idx]++;
  });

  const histColors = [
    'rgba(248, 113, 113, 0.5)',
    'rgba(251, 191, 36, 0.45)',
    'rgba(251, 191, 36, 0.5)',
    'rgba(52, 211, 153, 0.45)',
    'rgba(52, 211, 153, 0.6)',
  ];
  const histBorders = ['#f87171', '#fbbf24', '#fbbf24', '#34d399', '#34d399'];

  state.charts.confidence = new Chart(ctxConf, {
    type: 'bar',
    data: {
      labels: ['0–20%', '20–40%', '40–60%', '60–80%', '80–100%'],
      datasets: [{
        label: 'Tickets',
        data: confBuckets,
        backgroundColor: histColors,
        borderColor: histBorders,
        borderWidth: 1.5,
        borderRadius: 8,
        barPercentage: 0.65,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: 'rgba(10, 14, 20, 0.95)',
          borderColor: 'rgba(255,255,255,0.1)',
          borderWidth: 1,
          padding: 10,
          cornerRadius: 10,
        },
      },
      scales: {
        x: {
          grid: { display: false },
          ticks: { font: { size: 10 }, color: 'rgba(255,255,255,0.35)' },
        },
        y: {
          grid: { color: 'rgba(255, 255, 255, 0.03)' },
          ticks: { font: { size: 10 }, color: 'rgba(255,255,255,0.3)', stepSize: 1 },
          beginAtZero: true,
        },
      },
    },
  });

  // ── 5. Category Performance Radar ──
  const ctxRadar = document.getElementById('chart-radar');
  if (state.charts.radar) state.charts.radar.destroy();

  const radarLabels = catNames.map(n => n.replace(/_/g, ' '));
  const radarSuccess = catNames.map(name => {
    const catTickets = results.filter(r => (r.category || 'unknown') === name);
    const catResolved = catTickets.filter(r =>
      r.resolution === 'full_resolution' || r.resolution === 'resolved'
    ).length;
    return catTickets.length > 0 ? Math.round((catResolved / catTickets.length) * 100) : 0;
  });
  const radarConf = catNames.map(name => {
    const catTickets = results.filter(r => (r.category || 'unknown') === name);
    const avg = catTickets.reduce((s, r) => s + (r.confidence_score || r.confidence || 0), 0) / (catTickets.length || 1);
    return Math.round(avg * 100);
  });

  state.charts.radar = new Chart(ctxRadar, {
    type: 'radar',
    data: {
      labels: radarLabels,
      datasets: [
        {
          label: 'Resolution Rate %',
          data: radarSuccess,
          backgroundColor: 'rgba(59, 130, 246, 0.15)',
          borderColor: '#3b82f6',
          borderWidth: 2,
          pointBackgroundColor: '#3b82f6',
          pointBorderColor: '#0f172a',
          pointBorderWidth: 2,
          pointRadius: 4,
        },
        {
          label: 'Avg Confidence %',
          data: radarConf,
          backgroundColor: 'rgba(167, 139, 250, 0.12)',
          borderColor: '#a78bfa',
          borderWidth: 2,
          pointBackgroundColor: '#a78bfa',
          pointBorderColor: '#0f172a',
          pointBorderWidth: 2,
          pointRadius: 4,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          display: true,
          position: 'bottom',
          labels: {
            color: 'rgba(255, 255, 255, 0.6)',
            font: { size: 10, family: "'Inter', sans-serif" },
            padding: 16,
            usePointStyle: true,
            pointStyleWidth: 8,
          },
        },
      },
      scales: {
        r: {
          angleLines: { color: 'rgba(255, 255, 255, 0.05)' },
          grid: { color: 'rgba(255, 255, 255, 0.05)' },
          pointLabels: {
            color: 'rgba(255, 255, 255, 0.5)',
            font: { size: 10, family: "'Inter', sans-serif" },
          },
          ticks: { display: false },
          suggestedMin: 0,
          suggestedMax: 100,
        },
      },
    },
  });
}

// ═══════════════════════════════════════════════════════════
//  TOAST NOTIFICATIONS
// ═══════════════════════════════════════════════════════════
function showToast(type, message) {
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.textContent = message;
  toastContainer.appendChild(toast);

  // Auto-dismiss
  setTimeout(() => {
    toast.classList.add('leaving');
    setTimeout(() => toast.remove(), 400);
  }, 4500);
}

// ═══════════════════════════════════════════════════════════
//  BOOT
// ═══════════════════════════════════════════════════════════
init();
