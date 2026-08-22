(() => {
  "use strict";

  /* ================= Utils ================= */
  function fmtNumber(n) {
    if (n == null || !isFinite(n)) return "–";
    return Math.round(n).toLocaleString("vi-VN");
  }

  function el(id) { return document.getElementById(id); }

  function escapeHtml(s) {
    return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  /* ---- Date-range helpers for the Dashboard's time-filter popover ---- */
  function pad2(n) { return String(n).padStart(2, "0"); }
  function toIsoDate(d) { return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`; }
  function addDays(d, n) { const r = new Date(d); r.setDate(r.getDate() + n); return r; }
  function formatVnDate(iso) {
    if (!iso) return "";
    const [y, m, d] = iso.split("-");
    return `${d}/${m}/${y}`;
  }

  // "Current period" presets (today/thisMonth/thisQuarter/thisYear) run
  // through *today* (a live dashboard shouldn't imply data exists for
  // future days); "past period" presets are fixed, fully-elapsed ranges.
  function computePresetRange(key) {
    const today = new Date();
    const y = today.getFullYear();
    const m = today.getMonth(); // 0-indexed
    switch (key) {
      case "today": return { from: toIsoDate(today), to: toIsoDate(today) };
      case "yesterday": { const d = addDays(today, -1); return { from: toIsoDate(d), to: toIsoDate(d) }; }
      case "last7": return { from: toIsoDate(addDays(today, -6)), to: toIsoDate(today) };
      case "last30": return { from: toIsoDate(addDays(today, -29)), to: toIsoDate(today) };
      case "thisMonth": return { from: toIsoDate(new Date(y, m, 1)), to: toIsoDate(today) };
      case "lastMonth": return { from: toIsoDate(new Date(y, m - 1, 1)), to: toIsoDate(new Date(y, m, 0)) };
      case "thisQuarter": {
        const qStart = Math.floor(m / 3) * 3;
        return { from: toIsoDate(new Date(y, qStart, 1)), to: toIsoDate(today) };
      }
      case "lastQuarter": {
        const qStart = Math.floor(m / 3) * 3 - 3;
        return { from: toIsoDate(new Date(y, qStart, 1)), to: toIsoDate(new Date(y, qStart + 3, 0)) };
      }
      case "thisYear": return { from: toIsoDate(new Date(y, 0, 1)), to: toIsoDate(today) };
      case "lastYear": return { from: toIsoDate(new Date(y - 1, 0, 1)), to: toIsoDate(new Date(y - 1, 11, 31)) };
      default: return null;
    }
  }

  function computeMonthRange(monthValue) { // "YYYY-MM" from <input type="month">
    const [y, m] = monthValue.split("-").map(Number);
    return { from: toIsoDate(new Date(y, m - 1, 1)), to: toIsoDate(new Date(y, m, 0)) };
  }

  function computeQuarterRange(quarter, year) {
    const qStart = (quarter - 1) * 3;
    return { from: toIsoDate(new Date(year, qStart, 1)), to: toIsoDate(new Date(year, qStart + 3, 0)) };
  }

  function computeYearRange(year) {
    return { from: toIsoDate(new Date(year, 0, 1)), to: toIsoDate(new Date(year, 11, 31)) };
  }

  const TIME_PRESET_LABELS = {
    "": "Tất cả thời gian", today: "Hôm nay", yesterday: "Hôm qua",
    last7: "7 ngày trước", last30: "30 ngày trước",
    thisMonth: "Tháng này", lastMonth: "Tháng trước",
    thisQuarter: "Quý này", lastQuarter: "Quý trước",
    thisYear: "Năm nay", lastYear: "Năm trước",
  };

  /* ================= Tabs ================= */
  function initTabs() {
    // Scoped to #mainTabs — the Dashboard's own sub-tab nav (#dashboardSubtabs,
    // see wireSubtabs) reuses the same .tab-btn class for visual styling only
    // and must not be picked up here (its buttons carry data-subtab, not
    // data-tab).
    document.querySelectorAll("#mainTabs .tab-btn").forEach(btn => {
      btn.addEventListener("click", () => {
        document.querySelectorAll("#mainTabs .tab-btn").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        document.querySelectorAll(".tab-panel").forEach(p => { p.hidden = true; });
        el("panel-" + btn.dataset.tab).hidden = false;
      });
    });
  }

  /* ================= Orders / Reports tab (API-backed) ================= */
  let pollTimers = {};

  function wireOrdersTab() {
    const dz = el("uploadDropzone");
    const input = el("uploadInput");
    if (!API.isAdmin()) {
      dz.hidden = true;
    } else {
      ["dragenter", "dragover"].forEach(evt => dz.addEventListener(evt, e => { e.preventDefault(); dz.classList.add("dragover"); }));
      ["dragleave", "drop"].forEach(evt => dz.addEventListener(evt, e => { e.preventDefault(); dz.classList.remove("dragover"); }));
      dz.addEventListener("drop", e => { const f = e.dataTransfer.files[0]; if (f) handleReportUpload(f); });
      input.addEventListener("change", e => {
        const f = e.target.files[0];
        if (f) handleReportUpload(f);
        input.value = "";
      });
    }
    refreshReportsList();
  }

  async function handleReportUpload(file) {
    const box = el("uploadSummary");
    box.className = "import-summary";
    box.textContent = `Đang tải lên "${file.name}"...`;
    try {
      const formData = new FormData();
      formData.append("file", file);
      const res = await API.apiFetch("/api/reports", { method: "POST", body: formData });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `Lỗi ${res.status}`);
      }
      const created = await res.json();
      box.className = "import-summary ok";
      box.textContent = `Đã tải lên — đang xử lý...`;
      await refreshReportsList();
      pollReportStatus(created.id);
    } catch (err) {
      box.className = "import-summary err";
      box.textContent = "Lỗi tải lên: " + err.message;
    }
  }

  function pollReportStatus(reportId) {
    if (pollTimers[reportId]) clearInterval(pollTimers[reportId]);
    pollTimers[reportId] = setInterval(async () => {
      try {
        const report = await API.apiJson(`/api/reports/${reportId}`);
        if (report.status !== "processing") {
          clearInterval(pollTimers[reportId]);
          delete pollTimers[reportId];
          await refreshReportsList();
          if (report.status === "ready") refreshDashboard(); // the aggregate now includes it
        }
      } catch (e) { /* transient — keep polling */ }
    }, 2500);
  }

  const STATUS_BADGE = {
    processing: '<span class="pill warn">Đang xử lý</span>',
    ready: '<span class="pill good">Sẵn sàng</span>',
    failed: '<span class="pill bad">Lỗi</span>',
  };

  // "Kênh bán hàng" assignment — shared by the Đơn hàng and Dòng tiền
  // Report lists (see wireChannelSelects' call sites). dash.salesChannels
  // is fetched once at startup (see refreshSalesChannelsCache) and kept in
  // sync by the Kênh bán hàng tab's own add/delete actions.
  function channelName(channelId) {
    const c = dash.salesChannels.find(c => c.id === channelId);
    return c ? c.name : "(Chưa gán)";
  }

  function channelSelectHtml(reportId, currentChannelId) {
    const options = ['<option value="">(Chưa gán)</option>'].concat(
      dash.salesChannels.map(c =>
        `<option value="${escapeHtml(c.id)}" ${c.id === currentChannelId ? "selected" : ""}>${escapeHtml(c.name)}</option>`
      )
    );
    return `<select class="channel-select" data-report-id="${escapeHtml(reportId)}">${options.join("")}</select>`;
  }

  function wireChannelSelects(container, endpointBase, afterSave) {
    container.querySelectorAll(".channel-select").forEach(sel => {
      sel.onchange = async () => {
        const reportId = sel.dataset.reportId;
        try {
          await API.apiJson(`${endpointBase}/${reportId}/channel`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ sales_channel_id: sel.value || null }),
          });
          if (afterSave) await afterSave();
        } catch (err) {
          alert("Lỗi gán kênh bán hàng: " + err.message);
        }
      };
    });
  }

  async function refreshReportsList() {
    const isAdmin = API.isAdmin();
    let reports;
    try {
      reports = await API.apiJson("/api/reports");
    } catch (e) {
      el("reportsListBody").innerHTML = `<p class="muted">Không tải được danh sách Report: ${escapeHtml(e.message)}</p>`;
      return;
    }
    el("reportsListCount").textContent = `${reports.length.toLocaleString("vi-VN")} Report`;

    const body = el("reportsListBody");
    if (!reports.length) {
      body.innerHTML = `<p class="muted" style="padding:16px;">Chưa có Report nào.</p>`;
      return;
    }

    body.innerHTML = `<div class="table-scroll"><table><thead><tr>
        <th>Report</th><th>Trạng thái</th><th>Số dòng</th><th>Tải lên lúc</th><th>Kênh bán hàng</th>${isAdmin ? "<th>Thao tác</th>" : ""}
      </tr></thead><tbody>` + reports.map(r => `
        <tr>
          <td>${escapeHtml(r.name)}</td>
          <td>${STATUS_BADGE[r.status] || escapeHtml(r.status)}${r.status === "failed" && r.error_message ? `<div class="muted" style="margin-top:4px;">${escapeHtml(r.error_message)}</div>` : ""}</td>
          <td>${r.row_count != null ? r.row_count.toLocaleString("vi-VN") : "–"}</td>
          <td>${new Date(r.uploaded_at).toLocaleString("vi-VN")}</td>
          <td>${isAdmin ? channelSelectHtml(r.id, r.sales_channel_id) : escapeHtml(channelName(r.sales_channel_id))}</td>
          ${isAdmin ? `<td><button class="btn btn-danger btn-sm" data-del="${escapeHtml(r.id)}">Xóa</button></td>` : ""}
        </tr>
      `).join("") + `</tbody></table></div>`;

    if (isAdmin) {
      body.querySelectorAll("button[data-del]").forEach(btn => {
        btn.onclick = async () => {
          const id = btn.dataset.del;
          const report = reports.find(r => r.id === id);
          if (!confirm(`Xóa toàn bộ Report "${report ? report.name : id}"? Hành động này không thể hoàn tác.`)) return;
          await API.apiJson(`/api/reports/${id}`, { method: "DELETE" });
          await refreshReportsList();
          refreshDashboard();
        };
      });
      wireChannelSelects(body, "/api/reports", async () => { await refreshReportsList(); refreshDashboard(); });
    }

    // Any still-processing report needs a poller (e.g. after a page reload
    // mid-conversion) — pollReportStatus() is a no-op re-arm if already polling.
    reports.filter(r => r.status === "processing").forEach(r => pollReportStatus(r.id));
  }

  /* ================= Dòng tiền / Cashflow Reports tab (API-backed) — mirrors
     the Orders tab above 1:1, pointed at /api/cashflow-reports. Cashflow
     Reports exist solely to supply Phí AFF for the Orders Dashboard's
     query-time join, so uploading/deleting one also refreshes the Dashboard. ================= */
  let cashflowPollTimers = {};

  function wireCashflowTab() {
    const dz = el("cashflowUploadDropzone");
    const input = el("cashflowUploadInput");
    if (!API.isAdmin()) {
      dz.hidden = true;
    } else {
      ["dragenter", "dragover"].forEach(evt => dz.addEventListener(evt, e => { e.preventDefault(); dz.classList.add("dragover"); }));
      ["dragleave", "drop"].forEach(evt => dz.addEventListener(evt, e => { e.preventDefault(); dz.classList.remove("dragover"); }));
      dz.addEventListener("drop", e => { const f = e.dataTransfer.files[0]; if (f) handleCashflowUpload(f); });
      input.addEventListener("change", e => {
        const f = e.target.files[0];
        if (f) handleCashflowUpload(f);
        input.value = "";
      });
    }
    refreshCashflowReportsList();
  }

  async function handleCashflowUpload(file) {
    const box = el("cashflowUploadSummary");
    box.className = "import-summary";
    box.textContent = `Đang tải lên "${file.name}"...`;
    try {
      const formData = new FormData();
      formData.append("file", file);
      const res = await API.apiFetch("/api/cashflow-reports", { method: "POST", body: formData });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `Lỗi ${res.status}`);
      }
      const created = await res.json();
      box.className = "import-summary ok";
      box.textContent = `Đã tải lên — đang xử lý...`;
      await refreshCashflowReportsList();
      pollCashflowStatus(created.id);
    } catch (err) {
      box.className = "import-summary err";
      box.textContent = "Lỗi tải lên: " + err.message;
    }
  }

  function pollCashflowStatus(reportId) {
    if (cashflowPollTimers[reportId]) clearInterval(cashflowPollTimers[reportId]);
    cashflowPollTimers[reportId] = setInterval(async () => {
      try {
        const report = await API.apiJson(`/api/cashflow-reports/${reportId}`);
        if (report.status !== "processing") {
          clearInterval(cashflowPollTimers[reportId]);
          delete cashflowPollTimers[reportId];
          await refreshCashflowReportsList();
          if (report.status === "ready") refreshDashboard(); // Phí AFF now includes it
        }
      } catch (e) { /* transient — keep polling */ }
    }, 2500);
  }

  async function refreshCashflowReportsList() {
    const isAdmin = API.isAdmin();
    let reports;
    try {
      reports = await API.apiJson("/api/cashflow-reports");
    } catch (e) {
      el("cashflowReportsListBody").innerHTML = `<p class="muted">Không tải được danh sách Report: ${escapeHtml(e.message)}</p>`;
      return;
    }
    el("cashflowReportsListCount").textContent = `${reports.length.toLocaleString("vi-VN")} Report`;

    const body = el("cashflowReportsListBody");
    if (!reports.length) {
      body.innerHTML = `<p class="muted" style="padding:16px;">Chưa có Report nào.</p>`;
      return;
    }

    body.innerHTML = `<div class="table-scroll"><table><thead><tr>
        <th>Report</th><th>Trạng thái</th><th>Số dòng</th><th>Tải lên lúc</th><th>Kênh bán hàng</th>${isAdmin ? "<th>Thao tác</th>" : ""}
      </tr></thead><tbody>` + reports.map(r => `
        <tr>
          <td>${escapeHtml(r.name)}</td>
          <td>${STATUS_BADGE[r.status] || escapeHtml(r.status)}${r.status === "failed" && r.error_message ? `<div class="muted" style="margin-top:4px;">${escapeHtml(r.error_message)}</div>` : ""}</td>
          <td>${r.row_count != null ? r.row_count.toLocaleString("vi-VN") : "–"}</td>
          <td>${new Date(r.uploaded_at).toLocaleString("vi-VN")}</td>
          <td>${isAdmin ? channelSelectHtml(r.id, r.sales_channel_id) : escapeHtml(channelName(r.sales_channel_id))}</td>
          ${isAdmin ? `<td><button class="btn btn-danger btn-sm" data-del="${escapeHtml(r.id)}">Xóa</button></td>` : ""}
        </tr>
      `).join("") + `</tbody></table></div>`;

    if (isAdmin) {
      body.querySelectorAll("button[data-del]").forEach(btn => {
        btn.onclick = async () => {
          const id = btn.dataset.del;
          const report = reports.find(r => r.id === id);
          if (!confirm(`Xóa toàn bộ Report "${report ? report.name : id}"? Hành động này không thể hoàn tác.`)) return;
          await API.apiJson(`/api/cashflow-reports/${id}`, { method: "DELETE" });
          await refreshCashflowReportsList();
          refreshDashboard();
        };
      });
      wireChannelSelects(body, "/api/cashflow-reports", refreshCashflowReportsList);
    }

    reports.filter(r => r.status === "processing").forEach(r => pollCashflowStatus(r.id));
  }

  /* ================= Combo Reports tab (API-backed) — mirrors the Cashflow
     tab above 1:1, pointed at /api/combo-reports. Combo Reports exist solely
     to explode matching Orders skuVariant into their sub-SKU components at
     query time, so uploading/deleting one also refreshes the Dashboard. ================= */
  let comboPollTimers = {};

  function wireComboTab() {
    const dz = el("comboUploadDropzone");
    const input = el("comboUploadInput");
    if (!API.isAdmin()) {
      dz.hidden = true;
    } else {
      ["dragenter", "dragover"].forEach(evt => dz.addEventListener(evt, e => { e.preventDefault(); dz.classList.add("dragover"); }));
      ["dragleave", "drop"].forEach(evt => dz.addEventListener(evt, e => { e.preventDefault(); dz.classList.remove("dragover"); }));
      dz.addEventListener("drop", e => { const f = e.dataTransfer.files[0]; if (f) handleComboUpload(f); });
      input.addEventListener("change", e => {
        const f = e.target.files[0];
        if (f) handleComboUpload(f);
        input.value = "";
      });
    }
    refreshComboReportsList();
  }

  async function handleComboUpload(file) {
    const box = el("comboUploadSummary");
    box.className = "import-summary";
    box.textContent = `Đang tải lên "${file.name}"...`;
    try {
      const formData = new FormData();
      formData.append("file", file);
      const res = await API.apiFetch("/api/combo-reports", { method: "POST", body: formData });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `Lỗi ${res.status}`);
      }
      const created = await res.json();
      box.className = "import-summary ok";
      box.textContent = `Đã tải lên — đang xử lý...`;
      await refreshComboReportsList();
      pollComboStatus(created.id);
    } catch (err) {
      box.className = "import-summary err";
      box.textContent = "Lỗi tải lên: " + err.message;
    }
  }

  function pollComboStatus(reportId) {
    if (comboPollTimers[reportId]) clearInterval(comboPollTimers[reportId]);
    comboPollTimers[reportId] = setInterval(async () => {
      try {
        const report = await API.apiJson(`/api/combo-reports/${reportId}`);
        if (report.status !== "processing") {
          clearInterval(comboPollTimers[reportId]);
          delete comboPollTimers[reportId];
          await refreshComboReportsList();
          if (report.status === "ready") refreshDashboard(); // combo explosion now includes it
        }
      } catch (e) { /* transient — keep polling */ }
    }, 2500);
  }

  async function refreshComboReportsList() {
    const isAdmin = API.isAdmin();
    let reports;
    try {
      reports = await API.apiJson("/api/combo-reports");
    } catch (e) {
      el("comboReportsListBody").innerHTML = `<p class="muted">Không tải được danh sách Report: ${escapeHtml(e.message)}</p>`;
      return;
    }
    el("comboReportsListCount").textContent = `${reports.length.toLocaleString("vi-VN")} Report`;

    const body = el("comboReportsListBody");
    if (!reports.length) {
      body.innerHTML = `<p class="muted" style="padding:16px;">Chưa có Report nào.</p>`;
      return;
    }

    body.innerHTML = `<div class="table-scroll"><table><thead><tr>
        <th>Report</th><th>Trạng thái</th><th>Số dòng</th><th>Tải lên lúc</th>${isAdmin ? "<th>Thao tác</th>" : ""}
      </tr></thead><tbody>` + reports.map(r => `
        <tr>
          <td>${escapeHtml(r.name)}</td>
          <td>${STATUS_BADGE[r.status] || escapeHtml(r.status)}${r.status === "failed" && r.error_message ? `<div class="muted" style="margin-top:4px;">${escapeHtml(r.error_message)}</div>` : ""}</td>
          <td>${r.row_count != null ? r.row_count.toLocaleString("vi-VN") : "–"}</td>
          <td>${new Date(r.uploaded_at).toLocaleString("vi-VN")}</td>
          ${isAdmin ? `<td><button class="btn btn-danger btn-sm" data-del="${escapeHtml(r.id)}">Xóa</button></td>` : ""}
        </tr>
      `).join("") + `</tbody></table></div>`;

    if (isAdmin) {
      body.querySelectorAll("button[data-del]").forEach(btn => {
        btn.onclick = async () => {
          const id = btn.dataset.del;
          const report = reports.find(r => r.id === id);
          if (!confirm(`Xóa toàn bộ Report "${report ? report.name : id}"? Hành động này không thể hoàn tác.`)) return;
          await API.apiJson(`/api/combo-reports/${id}`, { method: "DELETE" });
          await refreshComboReportsList();
          refreshDashboard();
        };
      });
    }

    reports.filter(r => r.status === "processing").forEach(r => pollComboStatus(r.id));
  }

  /* ================= Master File Reports tab (API-backed) — mirrors the
     Combo tab above 1:1, pointed at /api/master-reports. Master File Reports
     exist solely to supply Phân loại kho/mục/sản phẩm and Giá vốn for the
     Orders Dashboard's query-time join, so uploading/deleting one also
     refreshes the Dashboard. ================= */
  let masterPollTimers = {};

  function wireMasterTab() {
    const dz = el("masterUploadDropzone");
    const input = el("masterUploadInput");
    if (!API.isAdmin()) {
      dz.hidden = true;
    } else {
      ["dragenter", "dragover"].forEach(evt => dz.addEventListener(evt, e => { e.preventDefault(); dz.classList.add("dragover"); }));
      ["dragleave", "drop"].forEach(evt => dz.addEventListener(evt, e => { e.preventDefault(); dz.classList.remove("dragover"); }));
      dz.addEventListener("drop", e => { const f = e.dataTransfer.files[0]; if (f) handleMasterUpload(f); });
      input.addEventListener("change", e => {
        const f = e.target.files[0];
        if (f) handleMasterUpload(f);
        input.value = "";
      });
    }
    refreshMasterReportsList();
  }

  async function handleMasterUpload(file) {
    const box = el("masterUploadSummary");
    box.className = "import-summary";
    box.textContent = `Đang tải lên "${file.name}"...`;
    try {
      const formData = new FormData();
      formData.append("file", file);
      const res = await API.apiFetch("/api/master-reports", { method: "POST", body: formData });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `Lỗi ${res.status}`);
      }
      const created = await res.json();
      box.className = "import-summary ok";
      box.textContent = `Đã tải lên — đang xử lý...`;
      await refreshMasterReportsList();
      pollMasterStatus(created.id);
    } catch (err) {
      box.className = "import-summary err";
      box.textContent = "Lỗi tải lên: " + err.message;
    }
  }

  function pollMasterStatus(reportId) {
    if (masterPollTimers[reportId]) clearInterval(masterPollTimers[reportId]);
    masterPollTimers[reportId] = setInterval(async () => {
      try {
        const report = await API.apiJson(`/api/master-reports/${reportId}`);
        if (report.status !== "processing") {
          clearInterval(masterPollTimers[reportId]);
          delete masterPollTimers[reportId];
          await refreshMasterReportsList();
          if (report.status === "ready") refreshDashboard(); // Giá vốn/category lookups now include it
        }
      } catch (e) { /* transient — keep polling */ }
    }, 2500);
  }

  async function refreshMasterReportsList() {
    const isAdmin = API.isAdmin();
    let reports;
    try {
      reports = await API.apiJson("/api/master-reports");
    } catch (e) {
      el("masterReportsListBody").innerHTML = `<p class="muted">Không tải được danh sách Report: ${escapeHtml(e.message)}</p>`;
      return;
    }
    el("masterReportsListCount").textContent = `${reports.length.toLocaleString("vi-VN")} Report`;

    const body = el("masterReportsListBody");
    if (!reports.length) {
      body.innerHTML = `<p class="muted" style="padding:16px;">Chưa có Report nào.</p>`;
      return;
    }

    body.innerHTML = `<div class="table-scroll"><table><thead><tr>
        <th>Report</th><th>Trạng thái</th><th>Số dòng</th><th>Tải lên lúc</th>${isAdmin ? "<th>Thao tác</th>" : ""}
      </tr></thead><tbody>` + reports.map(r => `
        <tr>
          <td>${escapeHtml(r.name)}</td>
          <td>${STATUS_BADGE[r.status] || escapeHtml(r.status)}${r.status === "failed" && r.error_message ? `<div class="muted" style="margin-top:4px;">${escapeHtml(r.error_message)}</div>` : ""}</td>
          <td>${r.row_count != null ? r.row_count.toLocaleString("vi-VN") : "–"}</td>
          <td>${new Date(r.uploaded_at).toLocaleString("vi-VN")}</td>
          ${isAdmin ? `<td><button class="btn btn-danger btn-sm" data-del="${escapeHtml(r.id)}">Xóa</button></td>` : ""}
        </tr>
      `).join("") + `</tbody></table></div>`;

    if (isAdmin) {
      body.querySelectorAll("button[data-del]").forEach(btn => {
        btn.onclick = async () => {
          const id = btn.dataset.del;
          const report = reports.find(r => r.id === id);
          if (!confirm(`Xóa toàn bộ Report "${report ? report.name : id}"? Hành động này không thể hoàn tác.`)) return;
          await API.apiJson(`/api/master-reports/${id}`, { method: "DELETE" });
          await refreshMasterReportsList();
          refreshDashboard();
        };
      });
    }

    reports.filter(r => r.status === "processing").forEach(r => pollMasterStatus(r.id));
  }

  /* ================= Điều chỉnh doanh thu Reports tab (API-backed) — mirrors
     the Master File tab above, pointed at /api/adjustments-reports. Unlike
     Combo/Cashflow/Master File, this data isn't joined into the Orders
     Dashboard's query engine (it's a standalone record-keeping viewer, same
     role the old IndexedDB manager played), so instead of "uploading also
     refreshes the Dashboard" it gets its own read-only rows viewer — click
     "Xem dữ liệu" on a ready Report to expand its first 50 rows inline. ================= */
  let adjustmentsPollTimers = {};
  let adjustmentsExpandedReportId = null;
  let adjustmentsExpandedRows = null; // {rows, total, page, pageSize} for the currently expanded report, or null while loading

  const ADJUSTMENT_ROW_COLS = [
    { key: "transactionId", label: "Mã giao dịch" },
    { key: "adjustmentDate", label: "Ngày hoàn thành điều chỉnh đơn hàng" },
    { key: "adjustmentType", label: "Loại điều chỉnh | Mô tả" },
    { key: "reason", label: "Lý do điều chỉnh" },
    { key: "amount", label: "Số tiền điều chỉnh", fmt: v => fmtNumber(v) },
    { key: "relatedOrderId", label: "Mã đơn hàng liên quan" },
    { key: "paymentCompletedDate", label: "Ngày hoàn thành thanh toán" },
  ];

  function wireAdjustmentsTab() {
    const dz = el("adjustmentsUploadDropzone");
    const input = el("adjustmentsUploadInput");
    if (!API.isAdmin()) {
      dz.hidden = true;
    } else {
      ["dragenter", "dragover"].forEach(evt => dz.addEventListener(evt, e => { e.preventDefault(); dz.classList.add("dragover"); }));
      ["dragleave", "drop"].forEach(evt => dz.addEventListener(evt, e => { e.preventDefault(); dz.classList.remove("dragover"); }));
      dz.addEventListener("drop", e => { const f = e.dataTransfer.files[0]; if (f) handleAdjustmentsUpload(f); });
      input.addEventListener("change", e => {
        const f = e.target.files[0];
        if (f) handleAdjustmentsUpload(f);
        input.value = "";
      });
    }
    refreshAdjustmentsReportsList();
  }

  async function handleAdjustmentsUpload(file) {
    const box = el("adjustmentsUploadSummary");
    box.className = "import-summary";
    box.textContent = `Đang tải lên "${file.name}"...`;
    try {
      const formData = new FormData();
      formData.append("file", file);
      const res = await API.apiFetch("/api/adjustments-reports", { method: "POST", body: formData });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `Lỗi ${res.status}`);
      }
      const created = await res.json();
      box.className = "import-summary ok";
      box.textContent = `Đã tải lên — đang xử lý...`;
      await refreshAdjustmentsReportsList();
      pollAdjustmentsStatus(created.id);
    } catch (err) {
      box.className = "import-summary err";
      box.textContent = "Lỗi tải lên: " + err.message;
    }
  }

  function pollAdjustmentsStatus(reportId) {
    if (adjustmentsPollTimers[reportId]) clearInterval(adjustmentsPollTimers[reportId]);
    adjustmentsPollTimers[reportId] = setInterval(async () => {
      try {
        const report = await API.apiJson(`/api/adjustments-reports/${reportId}`);
        if (report.status !== "processing") {
          clearInterval(adjustmentsPollTimers[reportId]);
          delete adjustmentsPollTimers[reportId];
          await refreshAdjustmentsReportsList();
        }
      } catch (e) { /* transient — keep polling */ }
    }, 2500);
  }

  async function toggleAdjustmentsExpand(reportId) {
    if (adjustmentsExpandedReportId === reportId) {
      adjustmentsExpandedReportId = null;
      adjustmentsExpandedRows = null;
      await refreshAdjustmentsReportsList();
      return;
    }
    adjustmentsExpandedReportId = reportId;
    adjustmentsExpandedRows = null;
    await refreshAdjustmentsReportsList();
    try {
      const result = await API.apiJson(`/api/adjustments-reports/${reportId}/rows?page=1&pageSize=50`);
      if (adjustmentsExpandedReportId !== reportId) return; // collapsed while the request was in flight
      adjustmentsExpandedRows = result;
      await refreshAdjustmentsReportsList();
    } catch (err) {
      adjustmentsExpandedReportId = null;
      adjustmentsExpandedRows = null;
      alert("Lỗi tải dữ liệu: " + err.message);
      await refreshAdjustmentsReportsList();
    }
  }

  function renderAdjustmentsExpandedRowHtml(colspan) {
    if (!adjustmentsExpandedRows) {
      return `<tr class="group-detail-row"><td colspan="${colspan}" class="muted" style="padding:12px;">Đang tải...</td></tr>`;
    }
    const { rows, total } = adjustmentsExpandedRows;
    const rowsHtml = rows.map(row =>
      "<tr>" + ADJUSTMENT_ROW_COLS.map(c => {
        const v = row[c.key];
        return `<td>${v == null || v === "" ? "" : (c.fmt ? c.fmt(v) : escapeHtml(v))}</td>`;
      }).join("") + "</tr>"
    ).join("");
    const note = total > rows.length
      ? `<p class="muted" style="margin:8px 0 0;">Hiển thị ${rows.length.toLocaleString("vi-VN")} / ${total.toLocaleString("vi-VN")} dòng đầu tiên.</p>`
      : "";
    return `<tr class="group-detail-row"><td colspan="${colspan}">
        <div class="table-scroll" style="max-height:320px;">
          <table><thead><tr>${ADJUSTMENT_ROW_COLS.map(c => `<th>${escapeHtml(c.label)}</th>`).join("")}</tr></thead>
          <tbody>${rowsHtml || `<tr><td colspan="${ADJUSTMENT_ROW_COLS.length}" class="muted">Không có dữ liệu</td></tr>`}</tbody></table>
        </div>
        ${note}
      </td></tr>`;
  }

  async function refreshAdjustmentsReportsList() {
    const isAdmin = API.isAdmin();
    let reports;
    try {
      reports = await API.apiJson("/api/adjustments-reports");
    } catch (e) {
      el("adjustmentsReportsListBody").innerHTML = `<p class="muted">Không tải được danh sách Report: ${escapeHtml(e.message)}</p>`;
      return;
    }
    el("adjustmentsReportsListCount").textContent = `${reports.length.toLocaleString("vi-VN")} Report`;

    const body = el("adjustmentsReportsListBody");
    if (!reports.length) {
      body.innerHTML = `<p class="muted" style="padding:16px;">Chưa có Report nào.</p>`;
      return;
    }

    const colspan = 6 + (isAdmin ? 1 : 0);
    body.innerHTML = `<div class="table-scroll"><table><thead><tr>
        <th>Report</th><th>Trạng thái</th><th>Số dòng</th><th>Tải lên lúc</th><th>Kênh bán hàng</th><th>Dữ liệu</th>${isAdmin ? "<th>Thao tác</th>" : ""}
      </tr></thead><tbody>` + reports.map(r => {
        const isExpanded = adjustmentsExpandedReportId === r.id;
        const rowHtml = `
        <tr>
          <td>${escapeHtml(r.name)}</td>
          <td>${STATUS_BADGE[r.status] || escapeHtml(r.status)}${r.status === "failed" && r.error_message ? `<div class="muted" style="margin-top:4px;">${escapeHtml(r.error_message)}</div>` : ""}</td>
          <td>${r.row_count != null ? r.row_count.toLocaleString("vi-VN") : "–"}</td>
          <td>${new Date(r.uploaded_at).toLocaleString("vi-VN")}</td>
          <td>${isAdmin ? channelSelectHtml(r.id, r.sales_channel_id) : escapeHtml(channelName(r.sales_channel_id))}</td>
          <td>${r.status === "ready" ? `<button class="btn btn-ghost btn-sm adjustments-view-btn" data-report-id="${escapeHtml(r.id)}">${isExpanded ? "Ẩn" : "Xem"} dữ liệu</button>` : ""}</td>
          ${isAdmin ? `<td><button class="btn btn-danger btn-sm" data-del="${escapeHtml(r.id)}">Xóa</button></td>` : ""}
        </tr>`;
        return rowHtml + (isExpanded ? renderAdjustmentsExpandedRowHtml(colspan) : "");
      }).join("") + `</tbody></table></div>`;

    if (isAdmin) {
      body.querySelectorAll("button[data-del]").forEach(btn => {
        btn.onclick = async () => {
          const id = btn.dataset.del;
          const report = reports.find(r => r.id === id);
          if (!confirm(`Xóa toàn bộ Report "${report ? report.name : id}"? Hành động này không thể hoàn tác.`)) return;
          await API.apiJson(`/api/adjustments-reports/${id}`, { method: "DELETE" });
          if (adjustmentsExpandedReportId === id) { adjustmentsExpandedReportId = null; adjustmentsExpandedRows = null; }
          await refreshAdjustmentsReportsList();
        };
      });
      wireChannelSelects(body, "/api/adjustments-reports", refreshAdjustmentsReportsList);
    }

    body.querySelectorAll(".adjustments-view-btn").forEach(btn => {
      btn.onclick = () => toggleAdjustmentsExpand(btn.dataset.reportId);
    });

    reports.filter(r => r.status === "processing").forEach(r => pollAdjustmentsStatus(r.id));
  }

  /* ================= Sales Channels (Kênh bán hàng) — a plain named list,
     not a file-upload Report, so this tab is much simpler than the ones
     above: no dropzone, no background processing, just add/delete. Used to
     tag Đơn hàng/Dòng tiền Report uploads (see wireChannelSelects) and as a
     Dashboard filter/group-by/column dimension for Orders. ================= */
  async function refreshSalesChannelsCache() {
    try {
      dash.salesChannels = await API.apiJson("/api/sales-channels");
    } catch (e) {
      dash.salesChannels = [];
    }
  }

  function wireSalesChannelsTab() {
    const isAdmin = API.isAdmin();
    el("channelAddCard").hidden = !isAdmin;
    if (isAdmin) {
      el("btnAddChannel").onclick = async () => {
        const input = el("newChannelName");
        const name = input.value.trim();
        const box = el("channelAddSummary");
        if (!name) return;
        try {
          await API.apiJson("/api/sales-channels", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name }),
          });
          input.value = "";
          box.className = "import-summary ok";
          box.textContent = `Đã thêm kênh "${name}".`;
          await refreshSalesChannelsCache();
          await refreshSalesChannelsList();
        } catch (err) {
          box.className = "import-summary err";
          box.textContent = "Lỗi: " + err.message;
        }
      };
    }
    refreshSalesChannelsList();
  }

  async function refreshSalesChannelsList() {
    const isAdmin = API.isAdmin();
    let channels;
    try {
      channels = await API.apiJson("/api/sales-channels");
    } catch (e) {
      el("channelsListBody").innerHTML = `<p class="muted">Không tải được danh sách: ${escapeHtml(e.message)}</p>`;
      return;
    }
    dash.salesChannels = channels;
    el("channelsListCount").textContent = `${channels.length.toLocaleString("vi-VN")} kênh`;

    const body = el("channelsListBody");
    if (!channels.length) {
      body.innerHTML = `<p class="muted" style="padding:16px;">Chưa có kênh bán hàng nào.</p>`;
      return;
    }

    body.innerHTML = `<div class="table-scroll"><table><thead><tr>
        <th>Tên kênh</th><th>Tạo lúc</th>${isAdmin ? "<th>Thao tác</th>" : ""}
      </tr></thead><tbody>` + channels.map(c => `
        <tr>
          <td>${escapeHtml(c.name)}</td>
          <td>${new Date(c.created_at).toLocaleString("vi-VN")}</td>
          ${isAdmin ? `<td><button class="btn btn-danger btn-sm" data-del="${escapeHtml(c.id)}">Xóa</button></td>` : ""}
        </tr>
      `).join("") + `</tbody></table></div>`;

    if (isAdmin) {
      body.querySelectorAll("button[data-del]").forEach(btn => {
        btn.onclick = async () => {
          const id = btn.dataset.del;
          const channel = channels.find(c => c.id === id);
          if (!confirm(`Xóa kênh "${channel ? channel.name : id}"? Các Report đang gán kênh này sẽ chuyển về "(Chưa gán)".`)) return;
          await API.apiJson(`/api/sales-channels/${id}`, { method: "DELETE" });
          await refreshSalesChannelsCache();
          await refreshSalesChannelsList();
        };
      });
    }
  }

  /* ================= Dashboard (aggregates every ready Report — see
     /api/dashboard/summary + /rows; the date/category/status filters below
     are how the user narrows the view, not a per-Report picker) ================= */
  const dash = {
    reportsCount: 0,
    readyCount: 0,
    subtab: "overview",
    timeFrom: "", // ISO "YYYY-MM-DD" — "" means no lower bound
    timeTo: "",
    timeLabel: "Tất cả thời gian", // the time-filter popover's summary text
    detailSearch: "",
    detailGroupByLevels: [], // ordered list of GROUP_BY_COLUMNS keys — [] means "Không group"; index = hierarchy level
    detailSort: "date",
    detailSortDir: "asc",
    detailPage: 1,
    detailPageSize: 15,
    visibleCols: null, // Set, lazily loaded from localStorage — TABLE_COLS isn't defined yet at this point in the file
    // Nested "Group theo" tree state — a node's path is the ordered chain of
    // {column, value} filters from the root down to (and including) it, e.g.
    // [{column:"category",value:"Áo"},{column:"warehouseType",value:"Kho HN"}].
    // expandedGroups is keyed by pathKey() and holds what's currently fetched
    // for that expanded node (either the next nesting level's grouped rows,
    // or — once every selected level is exhausted — raw drill-down rows).
    // pathFiltersByKey recovers the actual path array from a key (DOM
    // data-attributes only carry the key, not the array).
    expandedGroups: new Map(), // pathKey -> { rows, total, page, pageSize, loading, isGrouped }
    pathFiltersByKey: new Map(), // pathKey -> [{column, value}, ...]
    lastDetailResult: null,
    lastDetailGrouped: false,
    // Multi-select filters — each holds the set of currently-checked values;
    // an empty Set means "Tất cả" (no filter on that field).
    selectedStatus: new Set(),
    selectedWarehouseType: new Set(),
    selectedItemGroup: new Set(),
    selectedProductType: new Set(),
    selectedSalesChannel: new Set(),
    salesChannels: [], // raw {id,name,...} list from /api/sales-channels — see refreshSalesChannelsCache
    lastFacets: null, // cached so "Xóa lọc" can redraw the checkbox lists without waiting on a fetch
    filtersWired: false,
    summarySeq: 0,
    detailSeq: 0,
  };

  async function refreshDashboard() {
    let reports;
    try {
      reports = await API.apiJson("/api/reports");
    } catch (e) {
      reports = [];
    }
    dash.reportsCount = reports.length;
    dash.readyCount = reports.filter(r => r.status === "ready").length;

    if (!dash.readyCount) {
      el("dashboardEmpty").hidden = false;
      el("dashboardContent").hidden = true;
      if (!reports.length) {
        el("dashboardEmptyHint").innerHTML = API.isAdmin()
          ? `Vào tab <strong>Đơn hàng</strong> để tải lên file Excel.`
          : `Chưa có Admin nào tải lên Report.`;
      } else {
        el("dashboardEmptyHint").textContent = "Report đang được xử lý, vui lòng chờ trong giây lát...";
      }
      return;
    }

    el("dashboardEmpty").hidden = true;
    el("dashboardContent").hidden = false;
    el("dashboardSourceNote").textContent = `Tổng hợp ${dash.readyCount.toLocaleString("vi-VN")} Report đã sẵn sàng`;

    if (!dash.filtersWired) { initDashboardFilters(); dash.filtersWired = true; }
    // Both sub-tabs are fetched together so switching "Tổng quan"/"Dữ liệu
    // chi tiết" is instant — no re-fetch on tab switch.
    await Promise.all([fetchAndRenderSummary(), fetchAndRenderDetailTable()]);
  }

  function currentFilterParams(extra) {
    const params = new URLSearchParams();
    const sku = el("filterSku").value.trim();
    if (dash.timeFrom) params.set("from", dash.timeFrom);
    if (dash.timeTo) params.set("to", dash.timeTo);
    // Multi-select — each checked value becomes its own "status="/etc entry
    // (append, not set) so the backend can match "any of these".
    dash.selectedStatus.forEach(v => params.append("status", v));
    dash.selectedWarehouseType.forEach(v => params.append("warehouseType", v));
    dash.selectedItemGroup.forEach(v => params.append("itemGroup", v));
    dash.selectedProductType.forEach(v => params.append("productType", v));
    dash.selectedSalesChannel.forEach(v => params.append("salesChannel", v));
    if (sku) params.set("sku", sku);
    Object.entries(extra || {}).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== "") params.set(k, v); });
    return params;
  }

  function wireSubtabs() {
    document.querySelectorAll("#dashboardSubtabs .tab-btn").forEach(btn => {
      btn.onclick = () => {
        document.querySelectorAll("#dashboardSubtabs .tab-btn").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        dash.subtab = btn.dataset.subtab;
        el("dashboardOverview").hidden = dash.subtab !== "overview";
        el("dashboardDetail").hidden = dash.subtab !== "detail";
      };
    });
  }

  const VISIBLE_COLS_KEY = "bbstore_detail_visible_cols";

  function ensureVisibleCols() {
    if (dash.visibleCols) return;
    let cols = null;
    try {
      const raw = localStorage.getItem(VISIBLE_COLS_KEY);
      if (raw) {
        const arr = JSON.parse(raw);
        if (Array.isArray(arr)) cols = arr.filter(k => TABLE_COLS.some(c => c.key === k));
      }
    } catch (e) { /* ignore corrupt localStorage value */ }
    dash.visibleCols = new Set(cols && cols.length ? cols : TABLE_COLS.map(c => c.key));
  }

  function saveVisibleCols() {
    try { localStorage.setItem(VISIBLE_COLS_KEY, JSON.stringify([...dash.visibleCols])); } catch (e) { /* storage unavailable */ }
  }

  // Generic multi-select checkbox popover — shared by the 4 filter-bar
  // pickers (Trạng thái/Phân loại kho/mục/sản phẩm). sortedValues must
  // already be in display order; entries no longer present in it (e.g. the
  // facet list changed) are dropped from selectedSet so a stale filter
  // can't silently keep narrowing results the user can no longer see.
  function renderMultiSelectFacet(listId, summaryId, selectedSet, sortedValues) {
    [...selectedSet].forEach(v => { if (!sortedValues.includes(v)) selectedSet.delete(v); });
    const list = el(listId);
    list.innerHTML = sortedValues.length
      ? sortedValues.map(v =>
          `<label><input type="checkbox" value="${escapeHtml(v)}" ${selectedSet.has(v) ? "checked" : ""}/> ${escapeHtml(v)}</label>`
        ).join("")
      : `<div class="muted">Không có giá trị</div>`;
    list.querySelectorAll('input[type="checkbox"]').forEach(cb => {
      cb.onchange = () => {
        if (cb.checked) selectedSet.add(cb.value);
        else selectedSet.delete(cb.value);
        updateMultiSelectSummary(summaryId, selectedSet);
      };
    });
    updateMultiSelectSummary(summaryId, selectedSet);
  }

  function updateMultiSelectSummary(summaryId, selectedSet) {
    el(summaryId).textContent = selectedSet.size === 0 ? "Tất cả" : `${selectedSet.size} đã chọn`;
  }

  // Sets the active time range + popover summary text and closes the
  // popover — does NOT re-fetch on its own, matching every other filter in
  // this bar (applies only when "🔍 Tìm kiếm" is clicked). presetKey
  // highlights the matching preset button; omit it for a custom
  // day/month/quarter/year selection (none of the preset buttons apply).
  function setTimeFilter(from, to, label, presetKey) {
    dash.timeFrom = from;
    dash.timeTo = to;
    dash.timeLabel = label;
    el("timeFilterSummary").textContent = label;
    el("timeFilterList").querySelectorAll(".time-preset-btn").forEach(btn => {
      btn.classList.toggle("active", presetKey !== undefined && btn.dataset.preset === presetKey);
    });
    el("timeFilterPicker").open = false;
  }

  function wireTimeFilter() {
    const currentYear = new Date().getFullYear();
    const yearOptionsHtml = Array.from({ length: 6 }, (_, i) => currentYear - i)
      .map(y => `<option value="${y}">${y}</option>`).join("");
    el("customQuarterYear").innerHTML = yearOptionsHtml;
    el("customYear").innerHTML = yearOptionsHtml;
    // Default to "Tháng này" instead of all-time — an unbounded dashboard
    // load rescans every historical Report on every open, which is the
    // slowest possible first paint; scoping to the current month by default
    // also lets the backend push the date range into the Parquet scan
    // itself (see query_engine.py's _build_orders_working) instead of
    // filtering after joining the entire history.
    const defaultRange = computePresetRange("thisMonth");
    setTimeFilter(defaultRange.from, defaultRange.to, TIME_PRESET_LABELS.thisMonth, "thisMonth");

    el("timeFilterList").querySelectorAll(".time-preset-btn").forEach(btn => {
      btn.onclick = () => {
        const key = btn.dataset.preset;
        if (key === "custom") {
          el("timeCustomPanel").hidden = !el("timeCustomPanel").hidden;
          return;
        }
        const range = key ? computePresetRange(key) : { from: "", to: "" };
        setTimeFilter(range.from, range.to, TIME_PRESET_LABELS[key], key);
      };
    });

    el("timeFilterList").querySelectorAll(".time-custom-tab").forEach(tab => {
      tab.onclick = () => {
        el("timeFilterList").querySelectorAll(".time-custom-tab").forEach(t => t.classList.remove("active"));
        tab.classList.add("active");
        const target = tab.dataset.customTab;
        el("timeFilterList").querySelectorAll(".time-custom-body").forEach(body => {
          body.hidden = body.dataset.customBody !== target;
        });
      };
    });

    el("btnApplyCustomDay").onclick = () => {
      const from = el("customFrom").value;
      const to = el("customTo").value;
      if (!from && !to) return;
      const label = from && to ? `${formatVnDate(from)} - ${formatVnDate(to)}` : "Tùy chọn";
      setTimeFilter(from, to, label);
    };
    el("btnApplyCustomMonth").onclick = () => {
      const monthValue = el("customMonth").value;
      if (!monthValue) return;
      const range = computeMonthRange(monthValue);
      const [y, m] = monthValue.split("-");
      setTimeFilter(range.from, range.to, `Tháng ${Number(m)}/${y}`);
    };
    el("btnApplyCustomQuarter").onclick = () => {
      const q = Number(el("customQuarterQ").value);
      const y = Number(el("customQuarterYear").value);
      const range = computeQuarterRange(q, y);
      setTimeFilter(range.from, range.to, `Quý ${q}/${y}`);
    };
    el("btnApplyCustomYear").onclick = () => {
      const y = Number(el("customYear").value);
      const range = computeYearRange(y);
      setTimeFilter(range.from, range.to, `Năm ${y}`);
    };
  }

  function renderColumnPicker() {
    const list = el("colPickerList");
    list.innerHTML = TABLE_COLS.map(c =>
      `<label><input type="checkbox" data-col="${escapeHtml(c.key)}" ${dash.visibleCols.has(c.key) ? "checked" : ""}/> ${escapeHtml(c.label)}</label>`
    ).join("");
    list.querySelectorAll("input[data-col]").forEach(cb => {
      cb.onchange = () => {
        const key = cb.dataset.col;
        if (cb.checked) dash.visibleCols.add(key);
        else if (dash.visibleCols.size > 1) dash.visibleCols.delete(key);
        else cb.checked = true; // always keep at least 1 column visible
        saveVisibleCols();
        rerenderDetailTableOnly();
      };
    });
  }

  // Clears every expanded "Group theo" node — called whenever the top-level
  // query changes (filters/search/sort/page/group columns), since a stale
  // node's cached rows would no longer necessarily belong under the new
  // top-level result set.
  function clearGroupState() {
    dash.expandedGroups.clear();
    dash.pathFiltersByKey.clear();
  }

  function groupByLabel(key) { return GROUP_BY_LABELS[key] || key; }

  function updateGroupBySummary() {
    el("detailGroupBySummary").textContent = dash.detailGroupByLevels.length
      ? dash.detailGroupByLevels.map(groupByLabel).join(" → ")
      : "Không group";
    el("detailGroupByExportNote").hidden = dash.detailGroupByLevels.length <= 1;
  }

  // Ordered multi-select — checking an option appends it to the end of
  // detailGroupByLevels (defining the next nesting level down); unchecking
  // removes it from wherever it is. No drag-and-drop reordering — re-check
  // in the order you want the levels if you need to change the order.
  function renderGroupByPicker() {
    const list = el("detailGroupByList");
    list.innerHTML = Object.keys(GROUP_BY_LABELS).map(key =>
      `<label><input type="checkbox" data-groupby="${key}" ${dash.detailGroupByLevels.includes(key) ? "checked" : ""}/> ${escapeHtml(groupByLabel(key))}</label>`
    ).join("");
    list.querySelectorAll("input[data-groupby]").forEach(cb => {
      cb.onchange = () => {
        const key = cb.dataset.groupby;
        if (cb.checked) {
          if (!dash.detailGroupByLevels.includes(key)) dash.detailGroupByLevels.push(key);
        } else {
          dash.detailGroupByLevels = dash.detailGroupByLevels.filter(k => k !== key);
        }
        updateGroupBySummary();
        dash.detailPage = 1;
        dash.detailSort = dash.detailGroupByLevels.length ? "doanhSo" : "date";
        dash.detailSortDir = dash.detailGroupByLevels.length ? "desc" : "asc";
        clearGroupState();
        fetchAndRenderDetailTable();
      };
    });
    updateGroupBySummary();
  }

  // Filters only apply when the user clicks "Tìm kiếm" (not on every field
  // change) — this also avoids a race where an earlier, slower request
  // (e.g. the initial unfiltered load) resolves after a later filtered one
  // and overwrites it; fetchAndRenderSummary/DetailTable guard against
  // that too.
  function initDashboardFilters() {
    el("btnApplyFilter").onclick = () => applyFiltersAndRender();
    el("btnClearFilter").onclick = () => {
      setTimeFilter("", "", TIME_PRESET_LABELS[""], "");
      el("customFrom").value = "";
      el("customTo").value = "";
      el("timeCustomPanel").hidden = true;
      dash.selectedStatus.clear();
      dash.selectedWarehouseType.clear();
      dash.selectedItemGroup.clear();
      dash.selectedProductType.clear();
      dash.selectedSalesChannel.clear();
      el("filterSku").value = "";
      if (dash.lastFacets) renderFacets(dash.lastFacets); // redraw checkboxes as unchecked
      applyFiltersAndRender();
    };
    el("tableSearch").oninput = e => {
      dash.detailSearch = e.target.value;
      dash.detailPage = 1;
      clearGroupState();
      fetchAndRenderDetailTable();
    };
    el("detailPrev").onclick = () => {
      if (dash.detailPage > 1) { dash.detailPage--; clearGroupState(); fetchAndRenderDetailTable(); }
    };
    el("detailNext").onclick = () => { dash.detailPage++; clearGroupState(); fetchAndRenderDetailTable(); };
    el("btnExportExcel").onclick = () => exportExcel();
    wireSubtabs();
    wireTimeFilter();
    ensureVisibleCols();
    renderColumnPicker();
    renderGroupByPicker();
  }

  function applyFiltersAndRender() {
    dash.detailPage = 1;
    clearGroupState();
    fetchAndRenderSummary();
    fetchAndRenderDetailTable();
  }

  async function fetchAndRenderSummary() {
    const seq = ++dash.summarySeq;
    const params = currentFilterParams();
    try {
      const summary = await API.apiJson(`/api/dashboard/summary?${params.toString()}`);
      if (seq !== dash.summarySeq) return; // a newer request already superseded this one
      renderKPIs(summary.kpis);
      renderFacets(summary.facets);
    } catch (err) {
      console.error("Dashboard summary fetch failed:", err);
    }
  }

  async function fetchAndRenderDetailTable() {
    const seq = ++dash.detailSeq;
    ensureVisibleCols();
    try {
      if (dash.detailGroupByLevels.length) {
        const params = currentFilterParams({
          search: dash.detailSearch, groupBy: dash.detailGroupByLevels[0],
          sort: dash.detailSort, sortDir: dash.detailSortDir,
          page: dash.detailPage, pageSize: dash.detailPageSize,
        });
        const result = await API.apiJson(`/api/dashboard/rows/grouped?${params.toString()}`);
        if (seq !== dash.detailSeq) return; // a newer request already superseded this one
        renderGroupedTable(result);
      } else {
        const params = currentFilterParams({
          search: dash.detailSearch, sort: dash.detailSort, sort_dir: dash.detailSortDir,
          page: dash.detailPage, pageSize: dash.detailPageSize,
        });
        const result = await API.apiJson(`/api/dashboard/rows?${params.toString()}`);
        if (seq !== dash.detailSeq) return;
        renderFlatTable(result);
      }
    } catch (err) {
      console.error("Dashboard detail-table fetch failed:", err);
    }
  }

  // Checkbox lists are rebuilt from the (unfiltered) summary response each
  // time, but the user's current selections are preserved if still valid
  // (see renderMultiSelectFacet).
  function renderFacets(facets) {
    dash.lastFacets = facets;
    const STATUS_ORDER = ["Hoàn thành", "Đang giao", "Hoàn 1 phần", "Hoàn hàng", "Hủy chưa XK", "Hủy sau XK"];
    const statuses = [...(facets.statuses || [])].sort((a, b) => STATUS_ORDER.indexOf(a) - STATUS_ORDER.indexOf(b));
    const warehouseTypes = [...(facets.warehouseTypes || [])].sort((a, b) => a.localeCompare(b, "vi"));
    const itemGroups = [...(facets.itemGroups || [])].sort((a, b) => a.localeCompare(b, "vi"));
    const productTypes = [...(facets.productTypes || [])].sort((a, b) => a.localeCompare(b, "vi"));
    const salesChannels = [...(facets.salesChannels || [])].sort((a, b) => a.localeCompare(b, "vi"));

    renderMultiSelectFacet("filterStatusList", "filterStatusSummary", dash.selectedStatus, statuses);
    renderMultiSelectFacet("filterWarehouseTypeList", "filterWarehouseTypeSummary", dash.selectedWarehouseType, warehouseTypes);
    renderMultiSelectFacet("filterItemGroupList", "filterItemGroupSummary", dash.selectedItemGroup, itemGroups);
    renderMultiSelectFacet("filterProductTypeList", "filterProductTypeSummary", dash.selectedProductType, productTypes);
    renderMultiSelectFacet("filterSalesChannelList", "filterSalesChannelSummary", dash.selectedSalesChannel, salesChannels);
  }

  /* ---- KPIs ---- */
  function renderKPIs(kpis) {
    el("kpiDoanhSo").textContent = fmtNumber(kpis.doanhSo);
    el("kpiDoanhSoThuan").textContent = fmtNumber(kpis.gmv);
    el("kpiDiscount").textContent = fmtNumber(kpis.discount);
    el("kpiVoucher").textContent = fmtNumber(kpis.voucher);
    el("kpiDoanhThuThuan").textContent = fmtNumber(kpis.doanhThuThuan);
    el("kpiNmv").textContent = fmtNumber(kpis.nmv);
    el("kpiHuyChuaXK").textContent = fmtNumber(kpis.huyChuaXK);
    el("kpiHuySauXK").textContent = fmtNumber(kpis.huySauXK);
    el("kpiHoan").textContent = fmtNumber(kpis.hoan);
    el("kpiPlatformFee").textContent = fmtNumber(kpis.platformFee);
    el("kpiPiship").textContent = fmtNumber(kpis.piship);
    el("kpiPhiAff").textContent = fmtNumber(kpis.phiAff);
    el("kpiGiaVon").textContent = fmtNumber(kpis.giaVon);
    el("kpiLoiNhuanGop").textContent = fmtNumber(kpis.loiNhuanGop);
    el("kpiDoanhSoSub").textContent = `${kpis.rowCount.toLocaleString("vi-VN")} dòng dữ liệu`;
  }

  /* ---- Detail table ---- */
  const TABLE_COLS = [
    { key: "date", label: "Ngày", fmt: v => new Date(v).toLocaleDateString("vi-VN") },
    { key: "orderId", label: "Mã đơn hàng" },
    { key: "sku", label: "SKU" },
    { key: "skuVariant", label: "SKU phân loại" },
    { key: "product", label: "Sản phẩm" },
    { key: "category", label: "Danh mục" },
    { key: "customer", label: "Khách hàng" },
    { key: "quantity", label: "Số lượng", fmt: v => Number(v).toLocaleString("vi-VN") },
    { key: "returnedQty", label: "SL hoàn trả", fmt: v => Number(v).toLocaleString("vi-VN") },
    { key: "soLuongThuc", label: "SL thực", fmt: v => Number(v).toLocaleString("vi-VN") },
    { key: "doanhSo", label: "Doanh số", fmt: v => fmtNumber(v) },
    { key: "discount", label: "Giảm giá", fmt: v => fmtNumber(v) },
    { key: "voucher", label: "Voucher", fmt: v => fmtNumber(v) },
    { key: "gmv", label: "GMV", fmt: v => fmtNumber(v) },
    { key: "doanhThuThuan", label: "Doanh thu thuần", fmt: v => fmtNumber(v) },
    { key: "platformFee", label: "Phí sàn", fmt: v => fmtNumber(v) },
    { key: "piship", label: "Phí Piship", fmt: v => fmtNumber(v) },
    { key: "phiAff", label: "Phí AFF", fmt: v => fmtNumber(v) },
    { key: "nmv", label: "NMV", fmt: v => fmtNumber(v) },
    { key: "phanLoaiKho", label: "Phân loại kho" },
    { key: "phanLoaiMuc", label: "Phân loại mục" },
    { key: "phanLoaiSp", label: "Phân loại sản phẩm" },
    { key: "giaVon", label: "Giá vốn", fmt: v => fmtNumber(v) },
    { key: "loiNhuanGop", label: "Lợi nhuận gộp", fmt: v => fmtNumber(v) },
    { key: "trangThai", label: "Trạng thái" },
    { key: "salesChannel", label: "Kênh bán hàng" },
  ];

  // Mirrors app/query_engine.py's ALLOWED_SORT_COLUMNS — only these flat
  // columns are click-to-sort; the backend silently falls back to "date"
  // for anything else, so the UI must not offer a sort arrow it can't honor.
  const SORTABLE_FLAT_COLUMNS = new Set([
    "date", "orderId", "product", "category", "customer", "quantity", "doanhSo", "trangThai",
    "gmv", "doanhThuThuan", "nmv", "loiNhuanGop",
  ]);

  // Mirrors app/routers/dashboard.py's GROUP_BY_LABELS/GROUP_AGG_LABELS —
  // the grouped view's own column set (all sortable — see GROUP_SORT_COLUMNS
  // in query_engine.py). Every key here except rowCount is also a TABLE_COLS
  // key, so the same column-picker Set drives both views.
  const GROUP_BY_LABELS = {
    sku: "SKU", product: "Sản phẩm", category: "Danh mục", customer: "Khách hàng",
    status: "Trạng thái", warehouseType: "Phân loại kho", itemGroup: "Phân loại mục",
    productType: "Phân loại sản phẩm", orderId: "Mã đơn hàng", salesChannel: "Kênh bán hàng",
  };
  const GROUP_AGG_COLS = [
    { key: "rowCount", label: "Số dòng", fmt: v => Number(v).toLocaleString("vi-VN") },
    { key: "quantity", label: "Số lượng", fmt: v => Number(v).toLocaleString("vi-VN") },
    { key: "returnedQty", label: "SL hoàn trả", fmt: v => Number(v).toLocaleString("vi-VN") },
    { key: "soLuongThuc", label: "SL thực", fmt: v => Number(v).toLocaleString("vi-VN") },
    { key: "doanhSo", label: "Doanh số", fmt: v => fmtNumber(v) },
    { key: "discount", label: "Giảm giá", fmt: v => fmtNumber(v) },
    { key: "voucher", label: "Voucher", fmt: v => fmtNumber(v) },
    { key: "gmv", label: "GMV", fmt: v => fmtNumber(v) },
    { key: "doanhThuThuan", label: "Doanh thu thuần", fmt: v => fmtNumber(v) },
    { key: "platformFee", label: "Phí sàn", fmt: v => fmtNumber(v) },
    { key: "piship", label: "Phí Piship", fmt: v => fmtNumber(v) },
    { key: "phiAff", label: "Phí AFF", fmt: v => fmtNumber(v) },
    { key: "nmv", label: "NMV", fmt: v => fmtNumber(v) },
    { key: "giaVon", label: "Giá vốn", fmt: v => fmtNumber(v) },
    { key: "loiNhuanGop", label: "Lợi nhuận gộp", fmt: v => fmtNumber(v) },
  ];

  function sortArrow(sortKey, currentSort, currentDir) {
    if (currentSort !== sortKey) return '<span class="sort-arrow">↕</span>';
    return `<span class="sort-arrow">${currentDir === "desc" ? "▼" : "▲"}</span>`;
  }

  // Only wires the TOP-level (level 0) header row — nested levels (level 1+,
  // embedded inside an expanded node) are intentionally static/unsortable to
  // keep the recursive tree UI bounded; they're always doanhSo-desc.
  function wireSortableHeaders(thead) {
    thead.querySelectorAll("th.sortable").forEach(th => {
      th.onclick = () => {
        const key = th.dataset.sort;
        if (dash.detailSort === key) {
          dash.detailSortDir = dash.detailSortDir === "desc" ? "asc" : "desc";
        } else {
          dash.detailSort = key;
          dash.detailSortDir = "asc";
        }
        dash.detailPage = 1;
        clearGroupState();
        fetchAndRenderDetailTable();
      };
    });
  }

  function pathKey(pathFilters) {
    return pathFilters.map(f => `${f.column}=${f.value}`).join("|");
  }

  function rerenderDetailTableOnly() {
    if (!dash.lastDetailResult) return;
    if (dash.lastDetailGrouped) renderGroupedTable(dash.lastDetailResult);
    else renderFlatTable(dash.lastDetailResult);
  }

  function renderFlatTable(result) {
    dash.lastDetailResult = result;
    dash.lastDetailGrouped = false;
    const thead = document.querySelector("#detailTable thead");
    const tbody = document.querySelector("#detailTable tbody");
    const cols = TABLE_COLS.filter(c => dash.visibleCols.has(c.key));

    thead.innerHTML = "<tr>" + cols.map(c => {
      if (!SORTABLE_FLAT_COLUMNS.has(c.key)) return `<th>${escapeHtml(c.label)}</th>`;
      const activeClass = dash.detailSort === c.key ? " sort-active" : "";
      return `<th class="sortable${activeClass}" data-sort="${c.key}">${escapeHtml(c.label)}${sortArrow(c.key, dash.detailSort, dash.detailSortDir)}</th>`;
    }).join("") + "</tr>";
    wireSortableHeaders(thead);

    tbody.innerHTML = result.rows.map(r =>
      "<tr>" + cols.map(c => {
        const v = r[c.key];
        return `<td>${v == null || v === "" ? "" : (c.fmt ? c.fmt(v) : escapeHtml(v))}</td>`;
      }).join("") + "</tr>"
    ).join("") || `<tr><td colspan="${cols.length}" class="muted" style="padding:20px;">Không có dữ liệu</td></tr>`;

    const maxPage = Math.max(1, Math.ceil(result.total / result.pageSize));
    el("detailPageInfo").textContent = `Trang ${result.page} / ${maxPage} (${result.total.toLocaleString("vi-VN")} dòng)`;
  }

  function renderGroupedTable(result) {
    dash.lastDetailResult = result;
    dash.lastDetailGrouped = true;
    const thead = document.querySelector("#detailTable thead");
    const tbody = document.querySelector("#detailTable tbody");
    const level = 0;
    const groupLabel = groupByLabel(dash.detailGroupByLevels[level]);
    // rowCount is always shown (it's structural, not a TABLE_COLS entry) —
    // every other aggregate column is gated by the same column picker Set
    // used by the flat view, since their keys coincide.
    const cols = GROUP_AGG_COLS.filter(c => c.key === "rowCount" || dash.visibleCols.has(c.key));

    const groupTh = (() => {
      const activeClass = dash.detailSort === "groupValue" ? " sort-active" : "";
      return `<th class="sortable${activeClass}" data-sort="groupValue">${escapeHtml(groupLabel)}${sortArrow("groupValue", dash.detailSort, dash.detailSortDir)}</th>`;
    })();
    thead.innerHTML = "<tr>" + groupTh + cols.map(c => {
      const activeClass = dash.detailSort === c.key ? " sort-active" : "";
      return `<th class="sortable${activeClass}" data-sort="${c.key}">${escapeHtml(c.label)}${sortArrow(c.key, dash.detailSort, dash.detailSortDir)}</th>`;
    }).join("") + "</tr>";
    wireSortableHeaders(thead);

    if (!result.rows.length) {
      tbody.innerHTML = `<tr><td colspan="${cols.length + 1}" class="muted" style="padding:20px;">Không có dữ liệu</td></tr>`;
    } else {
      tbody.innerHTML = result.rows.map(r => renderGroupRowHtml(r, cols, level, [])).join("");
      // A single pass over the WHOLE (possibly multi-level-deep) subtree —
      // querySelectorAll reaches nested <table>s embedded inside expanded
      // <td>s too, so every level's rows/buttons get wired in one call.
      wireGroupRowInteractions(tbody);
    }

    const maxPage = Math.max(1, Math.ceil(result.total / result.pageSize));
    el("detailPageInfo").textContent = `Trang ${result.page} / ${maxPage} (${result.total.toLocaleString("vi-VN")} nhóm)`;
  }

  // level = index into dash.detailGroupByLevels for the column THIS row is
  // grouped on. pathFilters = the ancestor chain (NOT including this row's
  // own filter). Recorded into pathFiltersByKey so toggleGroupExpand can
  // recover the full path later from just the DOM's data-path-key.
  function renderGroupRowHtml(r, cols, level, pathFilters) {
    const groupByKey = dash.detailGroupByLevels[level];
    const thisFilter = { column: groupByKey, value: String(r.groupValue ?? "") };
    const fullPath = [...pathFilters, thisFilter];
    const key = pathKey(fullPath);
    dash.pathFiltersByKey.set(key, fullPath);

    const expanded = dash.expandedGroups.has(key);
    const chevron = expanded ? "▼" : "▶";
    const label = r.groupValue == null || r.groupValue === "" ? "(Trống)" : r.groupValue;
    let html = `<tr class="group-row" data-path-key="${escapeHtml(key)}">` +
      `<td><span class="group-chevron">${chevron}</span>${escapeHtml(label)}</td>` +
      cols.map(c => `<td>${c.fmt(r[c.key])}</td>`).join("") +
      `</tr>`;
    if (expanded) html += renderExpandedNodeHtml(key, level, cols.length + 1);
    return html;
  }

  // Renders whatever's cached for an expanded node: either the NEXT nesting
  // level's grouped rows (a nested <table>, itself built from
  // renderGroupRowHtml called recursively at level+1 — so a 3rd/4th level
  // expands exactly the same way), or — once every selected group-by column
  // has been consumed — the final flat drill-down rows. Either way this is
  // a nested <table> inside one wide <td>, never sharing a column grid with
  // its parent (the column sets don't match).
  function renderExpandedNodeHtml(key, level, colspan) {
    const state = dash.expandedGroups.get(key);
    if (!state) return "";
    if (state.loading) {
      return `<tr class="group-detail-row"><td colspan="${colspan}" class="muted" style="padding:12px;">Đang tải...</td></tr>`;
    }
    const pathFilters = dash.pathFiltersByKey.get(key) || [];
    const remaining = state.total - state.rows.length;
    const moreHtml = remaining > 0
      ? `<button class="btn btn-ghost btn-sm group-load-more" data-path-key="${escapeHtml(key)}">Tải thêm (còn ${remaining.toLocaleString("vi-VN")}${state.isGrouped ? " nhóm" : ""})</button>`
      : "";

    if (state.isGrouped) {
      const nextLevel = level + 1;
      const nextGroupLabel = groupByLabel(dash.detailGroupByLevels[nextLevel]);
      const cols = GROUP_AGG_COLS.filter(c => c.key === "rowCount" || dash.visibleCols.has(c.key));
      const theadHtml = `<tr><th>${escapeHtml(nextGroupLabel)}</th>${cols.map(c => `<th>${escapeHtml(c.label)}</th>`).join("")}</tr>`;
      const bodyHtml = state.rows.length
        ? state.rows.map(r => renderGroupRowHtml(r, cols, nextLevel, pathFilters)).join("")
        : `<tr><td colspan="${cols.length + 1}" class="muted">Không có dữ liệu</td></tr>`;
      return `<tr class="group-detail-row"><td colspan="${colspan}">
          <div class="table-scroll" style="max-height:320px;">
            <table><thead>${theadHtml}</thead><tbody>${bodyHtml}</tbody></table>
          </div>
          ${moreHtml}
        </td></tr>`;
    }

    const flatCols = TABLE_COLS.filter(c => dash.visibleCols.has(c.key));
    const rowsHtml = state.rows.map(row =>
      "<tr>" + flatCols.map(c => {
        const v = row[c.key];
        return `<td>${v == null || v === "" ? "" : (c.fmt ? c.fmt(v) : escapeHtml(v))}</td>`;
      }).join("") + "</tr>"
    ).join("");
    return `<tr class="group-detail-row"><td colspan="${colspan}">
        <div class="table-scroll" style="max-height:260px;">
          <table><thead><tr>${flatCols.map(c => `<th>${escapeHtml(c.label)}</th>`).join("")}</tr></thead>
          <tbody>${rowsHtml || `<tr><td colspan="${flatCols.length}" class="muted">Không có dữ liệu</td></tr>`}</tbody></table>
        </div>
        ${moreHtml}
      </td></tr>`;
  }

  function wireGroupRowInteractions(container) {
    container.querySelectorAll("tr.group-row").forEach(tr => {
      tr.onclick = () => toggleGroupExpand(tr.dataset.pathKey);
    });
    container.querySelectorAll(".group-load-more").forEach(btn => {
      btn.onclick = e => { e.stopPropagation(); loadMoreGroupNode(btn.dataset.pathKey); };
    });
  }

  async function toggleGroupExpand(key) {
    if (dash.expandedGroups.has(key)) {
      dash.expandedGroups.delete(key);
      rerenderDetailTableOnly();
      return;
    }
    const pathFilters = dash.pathFiltersByKey.get(key) || [];
    const level = pathFilters.length - 1; // the level of the row being expanded
    const isGrouped = level + 1 < dash.detailGroupByLevels.length;
    dash.expandedGroups.set(key, { rows: [], total: 0, page: 1, pageSize: 50, loading: true, isGrouped });
    rerenderDetailTableOnly();
    await fetchGroupNodePage(key, pathFilters, level, isGrouped, 1);
  }

  async function fetchGroupNodePage(key, pathFilters, level, isGrouped, page) {
    try {
      let result;
      if (isGrouped) {
        const nextGroupByKey = dash.detailGroupByLevels[level + 1];
        const params = currentFilterParams({
          search: dash.detailSearch, groupBy: nextGroupByKey,
          sort: "doanhSo", sortDir: "desc", page, pageSize: 50,
        });
        pathFilters.forEach(f => { params.append("pathBy", f.column); params.append("pathValue", f.value); });
        result = await API.apiJson(`/api/dashboard/rows/grouped?${params.toString()}`);
      } else {
        const params = currentFilterParams({
          search: dash.detailSearch, sort: "date", sort_dir: "asc", page, pageSize: 50,
        });
        pathFilters.forEach(f => { params.append("pathBy", f.column); params.append("pathValue", f.value); });
        result = await API.apiJson(`/api/dashboard/rows?${params.toString()}`);
      }
      const prev = dash.expandedGroups.get(key);
      if (!prev) return; // collapsed while the request was in flight
      const rows = page === 1 ? result.rows : [...prev.rows, ...result.rows];
      dash.expandedGroups.set(key, { rows, total: result.total, page, pageSize: 50, loading: false, isGrouped });
      rerenderDetailTableOnly();
    } catch (err) {
      console.error("Group node fetch failed:", err);
      dash.expandedGroups.delete(key);
      rerenderDetailTableOnly();
    }
  }

  function loadMoreGroupNode(key) {
    const state = dash.expandedGroups.get(key);
    if (!state) return;
    const pathFilters = dash.pathFiltersByKey.get(key) || [];
    const level = pathFilters.length - 1;
    fetchGroupNodePage(key, pathFilters, level, state.isGrouped, state.page + 1);
  }

  async function exportExcel() {
    const btn = el("btnExportExcel");
    const originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Đang xuất...";
    try {
      ensureVisibleCols();
      // Export always uses the TOP-level group (index 0) only — a specific
      // nested branch isn't exportable, matching detailGroupByExportNote's
      // caption shown whenever more than 1 level is selected.
      const topGroupBy = dash.detailGroupByLevels[0];
      const exportCols = topGroupBy
        ? ["groupValue", "rowCount", ...GROUP_AGG_COLS.filter(c => c.key !== "rowCount" && dash.visibleCols.has(c.key)).map(c => c.key)]
        : TABLE_COLS.filter(c => dash.visibleCols.has(c.key)).map(c => c.key);
      const params = currentFilterParams({
        search: dash.detailSearch, sort: dash.detailSort, sortDir: dash.detailSortDir,
        columns: exportCols.join(","),
      });
      if (topGroupBy) params.set("groupBy", topGroupBy);

      const res = await API.apiFetch(`/api/dashboard/export?${params.toString()}`);
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `Lỗi ${res.status}`);
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "du-lieu-chi-tiet.xlsx";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      alert("Lỗi xuất Excel: " + err.message);
    } finally {
      btn.disabled = false;
      btn.textContent = originalText;
    }
  }

  /* ================= Auth / login ================= */
  function showApp() {
    el("loginScreen").hidden = true;
    el("appShell").hidden = false;
    el("userDisplayName").textContent = `${API.getDisplayName() || ""} (${API.getRole() === "admin" ? "Admin" : "Viewer"})`;
  }

  function showLogin(message) {
    el("loginScreen").hidden = false;
    el("appShell").hidden = true;
    if (message) {
      const box = el("loginError");
      box.textContent = message;
      box.hidden = false;
    }
  }

  async function initApp() {
    initTabs();
    await refreshSalesChannelsCache(); // Đơn hàng/Dòng tiền/Điều chỉnh lists render a channel <select> per row from this
    wireOrdersTab();
    wireCashflowTab();
    wireComboTab();
    wireMasterTab();
    wireAdjustmentsTab();
    wireSalesChannelsTab();
    showApp();
    refreshDashboard();
  }

  document.addEventListener("DOMContentLoaded", () => {
    el("loginForm").addEventListener("submit", async e => {
      e.preventDefault();
      const errBox = el("loginError");
      errBox.hidden = true;
      try {
        await API.login(el("loginEmail").value.trim(), el("loginPassword").value);
        initApp();
      } catch (err) {
        errBox.textContent = err.message;
        errBox.hidden = false;
      }
    });

    el("btnLogout").addEventListener("click", () => API.logout());

    if (API.getAccessToken()) {
      API.apiJson("/api/auth/me").then(() => initApp()).catch(() => showLogin());
    } else {
      showLogin();
    }
  });
})();
