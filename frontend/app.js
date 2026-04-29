/* ═══════════════════════════════════════════════════
   Sri Aakrithis AI Receptionist — Dashboard App
   ═══════════════════════════════════════════════════ */

'use strict';

// ── State ────────────────────────────────────────────────────────────────────
const state = {
  logs: [], bookings: [], contacts: [], analytics: null,
  calYear: new Date().getFullYear(), calMonth: new Date().getMonth(),
  calSelected: null, currentTranscriptId: null,
};

// ── DOM helpers ───────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);
const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html !== undefined) e.innerHTML = html; return e; };
const fmt = {
  dur: s => s >= 60 ? `${Math.floor(s/60)}m ${s%60}s` : `${s}s`,
  date: iso => { if (!iso) return '—'; try { return new Date(iso).toLocaleDateString('en-IN',{day:'2-digit',month:'short',year:'numeric'}); } catch { return iso; } },
  time: iso => { if (!iso) return '—'; try { return new Date(iso).toLocaleTimeString('en-IN',{hour:'2-digit',minute:'2-digit'}); } catch { return iso; } },
  datetime: iso => { if (!iso) return '—'; try { const d = new Date(iso); return d.toLocaleDateString('en-IN',{day:'2-digit',month:'short'}) + ' ' + d.toLocaleTimeString('en-IN',{hour:'2-digit',minute:'2-digit'}); } catch { return iso; } },
  phone: p => p || '—',
  initials: name => { if (!name || name === 'Unknown') return '?'; return name.split(' ').map(w=>w[0]).join('').toUpperCase().slice(0,2); },
  avatarColor: str => { const colors = ['#4f8ef7','#3ecf8e','#a78bfa','#fbbf24','#f87171','#34d399','#60a5fa']; let h=0; for(let i=0;i<str.length;i++) h=(h*31+str.charCodeAt(i))&0xffffff; return colors[Math.abs(h)%colors.length]; },
  reltime: iso => { if (!iso) return ''; const d = Date.now()-new Date(iso).getTime(); const m=Math.floor(d/60000); if(m<1) return 'just now'; if(m<60) return `${m}m ago`; const h=Math.floor(m/60); if(h<24) return `${h}h ago`; return `${Math.floor(h/24)}d ago`; },
};

// ── API ───────────────────────────────────────────────────────────────────────
async function api(path) {
  try {
    const r = await fetch(path);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return await r.json();
  } catch (e) { console.warn(`API ${path}:`, e); return null; }
}

// ── Toast ─────────────────────────────────────────────────────────────────────
function toast(msg, dur=3000) {
  const t = $('toast'); t.textContent = msg; t.classList.add('show');
  setTimeout(()=>t.classList.remove('show'), dur);
}

// ── Theme ─────────────────────────────────────────────────────────────────────
let darkMode = localStorage.getItem('theme') !== 'light';
function applyTheme() {
  document.body.setAttribute('data-theme', darkMode ? 'dark' : 'light');
  $('theme-icon-moon').style.display = darkMode ? 'block' : 'none';
  $('theme-icon-sun').style.display  = darkMode ? 'none'  : 'block';
  localStorage.setItem('theme', darkMode ? 'dark' : 'light');
}
applyTheme();
$('theme-toggle').addEventListener('click', () => { darkMode = !darkMode; applyTheme(); });

// ── Navigation ────────────────────────────────────────────────────────────────
const pageMap = { overview:'Overview', calls:'Call Logs', analytics:'Analytics', crm:'CRM', calendar:'Appointments' };
let activePage = 'overview';
function navigate(page) {
  if (!pageMap[page]) return;
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  const pg = $(`page-${page}`); if (pg) pg.classList.add('active');
  const nb = $(`nav-${page}`); if (nb) nb.classList.add('active');
  $('page-title').textContent = pageMap[page];
  activePage = page;
  if (page === 'calendar') renderCalendar();
  // close mobile sidebar
  $('sidebar').classList.remove('open');
}
document.querySelectorAll('.nav-item').forEach(btn => {
  btn.addEventListener('click', () => navigate(btn.dataset.page));
});

// Mobile sidebar
$('mobile-menu-btn').addEventListener('click', () => $('sidebar').classList.toggle('open'));

// ── Refresh ───────────────────────────────────────────────────────────────────
$('refresh-btn').addEventListener('click', () => { loadAll(); toast('Refreshed'); });

// ── Modal ─────────────────────────────────────────────────────────────────────
function openModal(logId) {
  state.currentTranscriptId = logId;
  const mo = $('transcript-modal'); mo.classList.add('open'); mo.setAttribute('aria-hidden','false');
  $('modal-content').textContent = 'Loading…';
  fetch(`/api/logs/${logId}/transcript`).then(r=>r.text()).then(t=>{ $('modal-content').textContent = t; }).catch(()=>{ $('modal-content').textContent = 'Failed to load transcript.'; });
}
function closeModal() {
  $('transcript-modal').classList.remove('open');
  $('transcript-modal').setAttribute('aria-hidden','true');
}
$('modal-close').addEventListener('click', closeModal);
$('modal-close-2').addEventListener('click', closeModal);
$('transcript-modal').addEventListener('click', e => { if (e.target === $('transcript-modal')) closeModal(); });
$('modal-download').addEventListener('click', () => {
  const txt = $('modal-content').textContent;
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([txt], {type:'text/plain'}));
  a.download = `transcript_${state.currentTranscriptId || 'call'}.txt`;
  a.click();
});

// ── Render: Overview KPIs ────────────────────────────────────────────────────
function renderKPIs(stats, contactsLen) {
  $('kpi-calls').textContent    = stats?.total_calls ?? '—';
  $('kpi-bookings').textContent = stats?.total_bookings ?? '—';
  $('kpi-patients').textContent = contactsLen ?? '—';
  $('kpi-rate').textContent     = stats?.booking_rate != null ? `${stats.booking_rate}%` : '—';
}

// ── Render: Upcoming Appointments (Overview) ─────────────────────────────────
function renderOverviewAppointments() {
  const tbody = $('overview-appointments');
  const upcoming = state.bookings.filter(b => b.appointment_time && new Date(b.appointment_time) >= new Date()).slice(0,6);
  $('appt-count').textContent = upcoming.length;
  if (!upcoming.length) { tbody.innerHTML = `<tr><td colspan="3" class="empty-cell">No upcoming appointments</td></tr>`; return; }
  tbody.innerHTML = upcoming.map(b => {
    const name = b.caller_name || 'Unknown';
    return `<tr>
      <td><strong>${name}</strong></td>
      <td>${fmt.datetime(b.appointment_time)}</td>
      <td><span class="tag tag-green">Confirmed</span></td>
    </tr>`;
  }).join('');
}

// ── Render: Recent Callers (Overview) ────────────────────────────────────────
function renderOverviewPatients() {
  const wrap = $('overview-patients');
  const recent = state.contacts.slice(0, 8);
  $('recent-count').textContent = recent.length;
  if (!recent.length) { wrap.innerHTML = '<div class="empty-state"><p>No callers yet</p></div>'; return; }
  wrap.innerHTML = '';
  recent.forEach(c => {
    const name = c.caller_name || c.phone_number || 'Unknown';
    const color = fmt.avatarColor(name);
    const div = el('div', 'recent-caller');
    div.innerHTML = `
      <div class="caller-avatar" style="background:${color}22;color:${color}">${fmt.initials(name)}</div>
      <div class="recent-caller-info">
        <div class="recent-caller-name">${name}</div>
        <div class="recent-caller-phone">${fmt.phone(c.phone_number)}</div>
      </div>
      <div class="recent-caller-time">${fmt.reltime(c.last_seen)}</div>`;
    wrap.appendChild(div);
  });
}

// ── Render: Call Logs ─────────────────────────────────────────────────────────
function renderCallLogs(filter='') {
  const tbody = $('calls-body');
  const fl = filter.toLowerCase();
  const rows = state.logs.filter(l => !fl || (l.phone_number||'').includes(fl) || (l.caller_name||'').toLowerCase().includes(fl));
  if (!rows.length) { tbody.innerHTML = `<tr><td colspan="6" class="empty-cell">No call logs found</td></tr>`; return; }
  tbody.innerHTML = rows.map(l => {
    const name = l.caller_name || 'Unknown';
    const dur  = l.duration_seconds ? fmt.dur(l.duration_seconds) : '—';
    const sum  = l.summary ? l.summary.slice(0,60)+(l.summary.length>60?'…':'') : '—';
    return `<tr>
      <td><strong>${name}</strong></td>
      <td><span style="font-family:var(--mono);font-size:11px">${fmt.phone(l.phone_number)}</span></td>
      <td>${dur}</td>
      <td title="${l.summary||''}">${sum}</td>
      <td>${fmt.date(l.created_at)}</td>
      <td><button class="btn btn-ghost" style="padding:4px 8px;font-size:11px" onclick="openModal('${l.id}')">Transcript</button></td>
    </tr>`;
  }).join('');
}
$('calls-search').addEventListener('input', e => renderCallLogs(e.target.value));

// ── Render: Analytics ─────────────────────────────────────────────────────────
function renderAnalytics() {
  const a = state.analytics;
  if (!a) return;
  const k = a.kpis || {};
  $('ana-duration').textContent = k.avg_duration != null ? fmt.dur(k.avg_duration) : '—';
  $('ana-booked').textContent   = k.booked_calls ?? '—';
  $('ana-connect').textContent  = k.connect_rate != null ? `${k.connect_rate}%` : '—';
  $('ana-total').textContent    = k.total_calls ?? '—';

  // Bar chart
  const daily = a.daily_series || [];
  const chartEl = $('daily-chart');
  if (!daily.length) { chartEl.innerHTML = '<div class="empty-state" style="height:140px"><p>No data yet</p></div>'; }
  else {
    const max = Math.max(...daily.map(d=>d.calls), 1);
    chartEl.innerHTML = '';
    daily.forEach(d => {
      const pct = Math.max(4, Math.round((d.calls/max)*100));
      const label = d.date ? d.date.slice(5) : '';
      const wrap = el('div','bar-wrap');
      const bar = el('div','bar'); bar.style.height = `${pct}%`; bar.dataset.val = `${d.calls} calls`;
      const lbl = el('div','bar-label',label);
      wrap.appendChild(bar); wrap.appendChild(lbl); chartEl.appendChild(wrap);
    });
  }

  // Outcomes
  const og = $('outcomes-grid'); og.innerHTML = '';
  const outcomes = a.outcomes || {};
  const total = Object.values(outcomes).reduce((s,v)=>s+v,0) || 1;
  const oColors = { booked:'var(--green)', completed:'var(--blue)', cancelled:'var(--amber)', unknown:'var(--text-3)' };
  Object.entries(outcomes).forEach(([k,v]) => {
    const pct = Math.round((v/total)*100);
    const row = el('div','outcome-row');
    row.innerHTML = `
      <span class="outcome-label">${k.charAt(0).toUpperCase()+k.slice(1)}</span>
      <div class="outcome-track"><div class="outcome-fill" style="width:${pct}%;background:${oColors[k]||'var(--blue)'}"></div></div>
      <span class="outcome-val">${v}</span>`;
    og.appendChild(row);
  });
}

// ── Render: CRM ───────────────────────────────────────────────────────────────
function renderCRM(filter='') {
  const tbody = $('crm-body');
  const fl = filter.toLowerCase();
  const rows = state.contacts.filter(c => !fl || (c.phone_number||'').includes(fl) || (c.caller_name||'').toLowerCase().includes(fl));
  if (!rows.length) { tbody.innerHTML = `<tr><td colspan="4" class="empty-cell">No contacts found</td></tr>`; return; }
  tbody.innerHTML = rows.map((c,i) => {
    const name = c.caller_name || 'Unknown';
    const status = c.is_booked ? '<span class="tag tag-green">Booked</span>' : '<span class="tag tag-gray">Lead</span>';
    return `<tr class="clickable" data-idx="${i}" onclick="showCRMDetail(${i})">
      <td><strong>${name}</strong></td>
      <td><span style="font-family:var(--mono);font-size:11px">${fmt.phone(c.phone_number)}</span></td>
      <td>${c.total_calls || 1}</td>
      <td>${status}</td>
    </tr>`;
  }).join('');
}
$('crm-search').addEventListener('input', e => renderCRM(e.target.value));

function showCRMDetail(idx) {
  const c = state.contacts[idx]; if (!c) return;
  const name = c.caller_name || 'Unknown';
  const color = fmt.avatarColor(name);
  const relLogs = state.logs.filter(l => l.phone_number === c.phone_number).slice(0,5);
  const histHtml = relLogs.length ? relLogs.map(l => `
    <div class="call-history-item">
      <div class="call-hist-date">${fmt.datetime(l.created_at)} · ${l.duration_seconds ? fmt.dur(l.duration_seconds) : '—'}</div>
      <div class="call-hist-summary">${l.summary || 'No summary'}</div>
      ${l.id ? `<button class="call-hist-btn" onclick="openModal('${l.id}')">View Transcript</button>` : ''}
    </div>`).join('')
    : '<div class="call-history-item"><div class="call-hist-summary">No call history found</div></div>';

  $('crm-details').innerHTML = `
    <div class="caller-card">
      <div style="display:flex;align-items:center;gap:12px;margin-bottom:12px">
        <div class="caller-avatar" style="width:46px;height:46px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:15px;font-weight:700;background:${color}22;color:${color}">${fmt.initials(name)}</div>
        <div>
          <div class="caller-name">${name}</div>
          <div class="caller-phone">${fmt.phone(c.phone_number)}</div>
        </div>
      </div>
      <div class="caller-meta">
        <span class="tag ${c.is_booked?'tag-green':'tag-gray'}">${c.is_booked?'Booked':'Lead'}</span>
        <span class="tag tag-blue">${c.total_calls||1} call${(c.total_calls||1)>1?'s':''}</span>
        <span class="tag tag-amber">Last: ${fmt.reltime(c.last_seen)}</span>
      </div>
    </div>
    <div style="font-size:12px;font-weight:600;color:var(--text-3);text-transform:uppercase;letter-spacing:.07em;margin:12px 0 4px">Call History</div>
    ${histHtml}`;
}

// ── Render: Calendar ─────────────────────────────────────────────────────────
function renderCalendar() {
  const { calYear: y, calMonth: m } = state;
  const label = new Date(y, m, 1).toLocaleDateString('en-IN',{month:'long',year:'numeric'});
  $('calendar-month-label').textContent = label;

  const firstDay = new Date(y, m, 1).getDay();
  const daysInMonth = new Date(y, m+1, 0).getDate();
  const today = new Date();

  // Build lookup of appointment dates
  const apptDates = {};
  state.bookings.forEach(b => {
    if (!b.appointment_time) return;
    const d = new Date(b.appointment_time);
    if (d.getFullYear()===y && d.getMonth()===m) {
      const key = d.getDate(); apptDates[key] = (apptDates[key]||0)+1;
    }
  });

  const grid = $('calendar-grid'); grid.innerHTML = '';

  // Leading blanks
  for (let i=0; i<firstDay; i++) { grid.appendChild(el('div','cal-day other-month','')); }

  for (let d=1; d<=daysInMonth; d++) {
    const isToday = today.getDate()===d && today.getMonth()===m && today.getFullYear()===y;
    const hasAppt = !!apptDates[d];
    const isSelected = state.calSelected === `${y}-${String(m+1).padStart(2,'0')}-${String(d).padStart(2,'0')}`;
    let cls = 'cal-day';
    if (isToday) cls += ' today';
    if (hasAppt) cls += ' has-appt';
    if (isSelected) cls += ' selected';
    const cell = el('div', cls);
    cell.innerHTML = `<span>${d}</span>${hasAppt?`<div class="cal-dot"></div>`:''}`;
    if (hasAppt || true) {
      const dateKey = `${y}-${String(m+1).padStart(2,'0')}-${String(d).padStart(2,'0')}`;
      cell.addEventListener('click', () => { state.calSelected = dateKey; renderCalendar(); renderCalAppts(dateKey); });
    }
    grid.appendChild(cell);
  }

  renderCalAppts(state.calSelected);
}

function renderCalAppts(dateKey) {
  const tbody = $('cal-appts-body');
  const title = $('cal-appts-title');
  let appts = state.bookings;
  if (dateKey) {
    title.textContent = `Appointments — ${fmt.date(dateKey)}`;
    appts = state.bookings.filter(b => b.appointment_time && b.appointment_time.startsWith(dateKey));
  } else {
    title.textContent = 'All Appointments';
  }
  if (!appts.length) { tbody.innerHTML = `<tr><td colspan="4" class="empty-cell">No appointments${dateKey?' on this day':''}</td></tr>`; return; }
  tbody.innerHTML = appts.map(b => `<tr>
    <td><strong>${b.caller_name||'Unknown'}</strong></td>
    <td><span style="font-family:var(--mono);font-size:11px">${fmt.phone(b.phone_number)}</span></td>
    <td>${fmt.datetime(b.appointment_time)}</td>
    <td><span class="tag tag-green">Confirmed</span></td>
  </tr>`).join('');
}

// Calendar nav
$('cal-prev').addEventListener('click', () => { state.calMonth--; if(state.calMonth<0){state.calMonth=11;state.calYear--;} renderCalendar(); });
$('cal-next').addEventListener('click', () => { state.calMonth++; if(state.calMonth>11){state.calMonth=0;state.calYear++;} renderCalendar(); });

// ── Orb: avg duration ─────────────────────────────────────────────────────────
function updateOrbStats(analytics) {
  const avg = analytics?.kpis?.avg_duration;
  $('orb-avg-dur').textContent = avg != null ? fmt.dur(avg) : '—';
  // Active calls — no real socket, fake 0 for now
  $('active-calls-count').textContent = '0';
}

// ── Load All Data ─────────────────────────────────────────────────────────────
async function loadAll() {
  const [logs, bookings, contacts, stats, analytics] = await Promise.all([
    api('/api/logs'),
    api('/api/bookings'),
    api('/api/contacts'),
    api('/api/stats'),
    api('/api/analytics'),
  ]);

  state.logs      = logs     || [];
  state.bookings  = bookings || [];
  state.contacts  = contacts || [];
  state.analytics = analytics;

  renderKPIs(stats, state.contacts.length);
  renderOverviewAppointments();
  renderOverviewPatients();
  renderCallLogs($('calls-search').value);
  renderAnalytics();
  renderCRM($('crm-search').value);
  if (activePage === 'calendar') renderCalendar();
  updateOrbStats(analytics);
}

// ── Init ──────────────────────────────────────────────────────────────────────
loadAll();
// Auto-refresh every 60 seconds
setInterval(loadAll, 60000);
