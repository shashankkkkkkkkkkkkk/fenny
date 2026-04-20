const state = {
  logs: [],
  contacts: [],
  analytics: null,
  bookings: [],
  patients: [],
};

const PAGE_TITLES = {
  overview: "🏠 Overview",
  crm: "👥 CRM",
  analytics: "📈 Analytics",
  calendar: "📅 Calendar",
  patients: "🧾 Patients",
};

function safeDate(value) {
  if (!value) return "—";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString("en-IN");
}

function setPage(page) {
  document.querySelectorAll(".nav-item").forEach((el) => {
    el.classList.toggle("active", el.dataset.page === page);
  });
  document.querySelectorAll(".page").forEach((el) => {
    el.classList.toggle("active", el.id === `page-${page}`);
  });
  document.getElementById("page-title").textContent = PAGE_TITLES[page] || page;
}

function tag(text, klass = "") {
  return `<span class="tag ${klass}">${text}</span>`;
}

function renderOverview() {
  const callsToday = state.logs.filter((r) => (r.created_at || "").slice(0, 10) === new Date().toISOString().slice(0, 10)).length;
  const appointments = state.bookings.length;
  const patients = state.contacts.length;
  const bookingRate = state.analytics?.kpis?.booking_rate ?? 0;

  document.getElementById("kpi-calls").textContent = callsToday;
  document.getElementById("kpi-bookings").textContent = appointments;
  document.getElementById("kpi-patients").textContent = patients;
  document.getElementById("kpi-rate").textContent = `${bookingRate}%`;

  const activeCalls = state.logs.filter((r) => !r.summary || String(r.summary).trim() === "").length;
  document.getElementById("active-calls-pill").textContent = `${activeCalls} Active Calls`;

  const appBody = document.getElementById("overview-appointments");
  const topBookings = state.bookings.slice(0, 6);
  if (!topBookings.length) {
    appBody.innerHTML = `<tr><td colspan="3">No appointments yet.</td></tr>`;
  } else {
    appBody.innerHTML = topBookings.map((b) => `
      <tr>
        <td>${b.phone_number || "Unknown"}</td>
        <td>${safeDate(b.created_at)}</td>
        <td>${tag("Confirmed", "ok")}</td>
      </tr>
    `).join("");
  }

  const recentPatients = document.getElementById("overview-patients");
  const topPatients = state.patients.slice(0, 5);
  recentPatients.innerHTML = topPatients.length
    ? topPatients.map((p) => `
      <div class="list-item">
        <div class="avatar-placeholder">${(p.name || "?").slice(0, 1).toUpperCase()}</div>
        <div>
          <strong>${p.name}</strong><br>
          <small>${p.phone_number} • last seen ${safeDate(p.last_seen)}</small>
        </div>
      </div>
    `).join("")
    : `<div class="list-item"><div class="avatar-placeholder">?</div><small>No patients yet.</small></div>`;
}

function renderCRM(filtered) {
  const rows = filtered ?? state.contacts;
  const tbody = document.getElementById("crm-body");
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="5">No CRM contacts found.</td></tr>`;
    document.getElementById("crm-details").innerHTML = `<div class="crm-details-empty">No CRM data available yet.</div>`;
    return;
  }
  tbody.innerHTML = rows.map((c) => `
    <tr class="crm-row" data-phone="${c.phone_number || ""}">
      <td>${c.caller_name || "Unknown"}</td>
      <td>${c.phone_number || "—"}</td>
      <td>${c.total_calls || 0}</td>
      <td>${safeDate(c.last_seen)}</td>
      <td>${c.is_booked ? tag("Booked", "ok") : tag("Pending", "warn")}</td>
    </tr>
  `).join("");
}

function renderCRMDetails(phone) {
  const details = document.getElementById("crm-details");
  const person = state.contacts.find((c) => c.phone_number === phone);
  const calls = state.logs
    .filter((l) => (l.phone_number || "") === phone)
    .sort((a, b) => new Date(b.created_at || 0).getTime() - new Date(a.created_at || 0).getTime());

  if (!person) {
    details.innerHTML = `<div class="crm-details-empty">Select a person in CRM Pipeline to see call purpose and summaries.</div>`;
    return;
  }

  if (!calls.length) {
    details.innerHTML = `<div class="crm-details-empty">No call history found for this contact.</div>`;
    return;
  }

  details.innerHTML = `
    <div style="margin-bottom:10px;">
      <strong>${person.caller_name || "Unknown"}</strong><br>
      <small>${person.phone_number || "—"} • ${person.total_calls || 0} calls</small>
    </div>
    <div class="crm-call-history">
      ${calls.map((call) => `
        <div class="crm-call-item">
          <div class="crm-meta">${safeDate(call.created_at)} • ${call.duration_seconds || 0}s</div>
          <div class="crm-purpose">${call.call_purpose || person.latest_purpose || "General conversation"}</div>
          <div class="crm-summary">${call.call_summary || call.summary || person.latest_summary || "Call completed."}</div>
        </div>
      `).join("")}
    </div>
  `;
}

function renderAnalytics() {
  const ana = state.analytics || { kpis: {}, outcomes: {}, daily_series: [] };
  document.getElementById("ana-connect").textContent = `${ana.kpis.connect_rate || 0}%`;
  document.getElementById("ana-duration").textContent = `${ana.kpis.avg_duration || 0}s`;
  document.getElementById("ana-booked").textContent = ana.kpis.booked_calls || 0;
  document.getElementById("ana-total").textContent = ana.kpis.total_calls || 0;

  const chart = document.getElementById("daily-chart");
  const daily = ana.daily_series || [];
  const max = Math.max(1, ...daily.map((x) => x.calls || 0));
  chart.innerHTML = daily.length
    ? daily.map((x) => {
      const h = Math.max(12, Math.round(((x.calls || 0) / max) * 180));
      const shortDate = String(x.date || "").slice(5);
      return `
        <div class="chart-col" title="${x.date}: ${x.calls}">
          <div class="bar" style="height:${h}px"></div>
          <div class="bar-label">${shortDate}</div>
        </div>
      `;
    }).join("")
    : `<small>No trend data yet.</small>`;

  const out = ana.outcomes || {};
  document.getElementById("outcomes-grid").innerHTML = [
    ["Booked", out.booked || 0],
    ["Cancelled", out.cancelled || 0],
    ["Completed", out.completed || 0],
    ["Unknown", out.unknown || 0],
  ].map(([k, v]) => `<div class="outcome"><span>${k}</span><strong>${v}</strong></div>`).join("");
}

function renderCalendar() {
  const monthEl = document.getElementById("calendar-month");
  const grid = document.getElementById("calendar-grid");
  const now = new Date();
  const y = now.getFullYear();
  const m = now.getMonth();
  monthEl.textContent = now.toLocaleString("en-IN", { month: "long", year: "numeric" });

  const first = new Date(y, m, 1);
  const last = new Date(y, m + 1, 0);
  const byDate = {};
  state.bookings.forEach((b) => {
    const d = (b.created_at || "").slice(0, 10);
    if (!d) return;
    byDate[d] = (byDate[d] || 0) + 1;
  });

  const cells = [];
  for (let i = 0; i < first.getDay(); i++) cells.push(`<div class="cal-cell"></div>`);
  for (let day = 1; day <= last.getDate(); day++) {
    const ds = `${y}-${String(m + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
    const count = byDate[ds] || 0;
    cells.push(`<div class="cal-cell"><div class="cal-day">${day}</div>${count ? `<div class="cal-count">${count} booking${count > 1 ? "s" : ""}</div>` : ""}</div>`);
  }
  grid.innerHTML = cells.join("");
}

function renderPatients() {
  const container = document.getElementById("patients-cards");
  if (!state.patients.length) {
    container.innerHTML = `<div class="patient-card"><small>No patients available.</small></div>`;
    return;
  }
  container.innerHTML = state.patients.map((p) => `
    <article class="patient-card">
      <strong>${p.name}</strong><br>
      <small>${p.phone_number}</small>
      <div style="margin-top:8px;"><small>Calls: ${p.total_calls} • Engagement: ${p.engagement}</small></div>
      <div style="margin-top:8px;">${p.booked ? tag("Booked", "ok") : tag("No booking", "warn")}</div>
    </article>
  `).join("");
}

async function bootstrapData() {
  const [logs, contacts, bookings, analytics, patients] = await Promise.all([
    fetch("/api/logs").then((r) => r.json()).catch(() => []),
    fetch("/api/contacts").then((r) => r.json()).catch(() => []),
    fetch("/api/bookings").then((r) => r.json()).catch(() => []),
    fetch("/api/analytics").then((r) => r.json()).catch(() => ({})),
    fetch("/api/patients").then((r) => r.json()).catch(() => []),
  ]);
  state.logs = Array.isArray(logs) ? logs : [];
  state.contacts = Array.isArray(contacts) ? contacts : [];
  state.bookings = Array.isArray(bookings) ? bookings : [];
  state.analytics = analytics || {};
  state.patients = Array.isArray(patients) ? patients : [];

  renderOverview();
  renderCRM();
  renderAnalytics();
  renderCalendar();
  renderPatients();
}

function setupSearch() {
  const input = document.getElementById("crm-search");
  input.addEventListener("input", () => {
    const q = input.value.trim().toLowerCase();
    if (!q) return renderCRM();
    renderCRM(state.contacts.filter((c) => {
      const name = String(c.caller_name || "").toLowerCase();
      const phone = String(c.phone_number || "").toLowerCase();
      return name.includes(q) || phone.includes(q);
    }));
  });
}

function setupCRMClicks() {
  document.getElementById("crm-body").addEventListener("click", (e) => {
    const row = e.target.closest("tr.crm-row");
    if (!row) return;
    renderCRMDetails(row.dataset.phone || "");
  });
}

function setupTheme() {
  const key = "rapidx-theme";
  const icon = document.getElementById("theme-icon");
  const refreshThemeIcon = () => {
    const mode = document.body.getAttribute("data-theme") || "dark";
    icon.textContent = mode === "dark" ? "🌙" : "☀️";
  };
  const saved = localStorage.getItem(key);
  if (saved === "light" || saved === "dark") {
    document.body.setAttribute("data-theme", saved);
  }
  refreshThemeIcon();
  document.getElementById("theme-toggle").addEventListener("click", () => {
    const next = document.body.getAttribute("data-theme") === "dark" ? "light" : "dark";
    document.body.setAttribute("data-theme", next);
    localStorage.setItem(key, next);
    refreshThemeIcon();
  });
}

function setupNavigation() {
  document.getElementById("nav").addEventListener("click", (e) => {
    const btn = e.target.closest(".nav-item");
    if (!btn) return;
    setPage(btn.dataset.page);
  });
}

window.addEventListener("DOMContentLoaded", async () => {
  setupTheme();
  setupNavigation();
  setupSearch();
  setupCRMClicks();
  await bootstrapData();
  setInterval(bootstrapData, 30000);
});
