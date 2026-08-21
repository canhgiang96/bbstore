(() => {
  "use strict";

  /* ================= Utils ================= */
  function stripDiacritics(str) {
    return String(str)
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .replace(/đ/gi, "d")
      .toLowerCase()
      .trim();
  }

  function normalizeHeader(h) {
    return stripDiacritics(h).replace(/[^a-z0-9]+/g, " ").trim();
  }

  function fmtNumber(n) {
    if (n == null || !isFinite(n)) return "–";
    return Math.round(n).toLocaleString("vi-VN");
  }

  function el(id) { return document.getElementById(id); }

  function escapeHtml(s) {
    return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function formatCell(v) {
    if (v instanceof Date) return v.toLocaleDateString("vi-VN");
    if (typeof v === "number") return v.toLocaleString("vi-VN");
    return v ?? "";
  }

  // Keys prefixed with "__" are internal bookkeeping (which file a row came
  // from) and never shown as a data column or edited.
  function inferColumns(rows) {
    const seen = [];
    const set = new Set();
    rows.slice(0, 50).forEach(({ value }) => Object.keys(value).forEach(k => {
      if (k.startsWith("__")) return;
      if (!set.has(k)) { set.add(k); seen.push(k); }
    }));
    return seen;
  }

  const UNTAGGED_BATCH_LABEL = "(Thêm thủ công / không rõ nguồn)";
  function batchLabel(row) { return row.__sourceFile || UNTAGGED_BATCH_LABEL; }

  /* ================= Store metadata (Điều chỉnh — Orders, Dòng tiền, Combo and Master File are API-backed, not a generic store) ================= */
  const STORE_META = {
    adjustments: {
      label: "Điều chỉnh doanh thu",
      headers: ["Mã giao dịch", "Ngày hoàn thành điều chỉnh đơn hàng", "Loại điều chỉnh | Mô tả", "Lý do điều chỉnh", "Số tiền điều chỉnh", "Mã đơn hàng liên quan", "Ngày hoàn thành thanh toán"],
      primaryKeyHeader: null,
    },
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

  /* ================= Generic data manager (Master/Điều chỉnh) ================= */
  const managerState = {};

  function setupDataManager(storeKey) {
    const meta = STORE_META[storeKey];
    managerState[storeKey] = { all: [], filtered: [], page: 1, pageSize: 20, search: "", columns: meta.headers || [] };

    const panel = el("panel-" + storeKey);
    panel.innerHTML = `
      <div class="data-panel-header">
        <h2>${escapeHtml(meta.label)}</h2>
        <span class="muted" id="count-${storeKey}"></span>
      </div>
      <div class="drop-zone" id="dropzone-${storeKey}">
        <div class="drop-zone-icon">📁</div>
        <h2>Kéo thả file Excel/CSV vào đây</h2>
        <p>hoặc</p>
        <label class="btn btn-primary" for="file-${storeKey}">Chọn file</label>
        <input type="file" id="file-${storeKey}" accept=".xlsx,.xls,.csv" hidden />
        <div class="import-summary" id="importSummary-${storeKey}"></div>
      </div>
      <div class="card file-batches" id="fileBatches-${storeKey}" hidden>
        <h3>Theo file đã tải lên</h3>
        <div id="fileBatchList-${storeKey}"></div>
      </div>
      <div class="data-toolbar">
        <button class="btn btn-primary btn-sm" id="btnAdd-${storeKey}">+ Thêm dòng</button>
        <button class="btn btn-danger btn-sm" id="btnClearAll-${storeKey}">Xóa tất cả</button>
        <div class="spacer"></div>
        <input type="text" id="search-${storeKey}" placeholder="Tìm kiếm..." />
      </div>
      <div class="card table-card">
        <div class="table-scroll"><table id="table-${storeKey}"><thead></thead><tbody></tbody></table></div>
        <div class="table-footer">
          <button class="btn btn-ghost" id="prev-${storeKey}">← Trước</button>
          <span class="muted" id="pageInfo-${storeKey}"></span>
          <button class="btn btn-ghost" id="next-${storeKey}">Sau →</button>
        </div>
      </div>
    `;

    wireUpload(storeKey, meta);

    el("btnAdd-" + storeKey).onclick = () => openRowModal(storeKey, null);
    el("btnClearAll-" + storeKey).onclick = async () => {
      if (!confirm(`Xóa toàn bộ dữ liệu "${meta.label}"? Hành động này không thể hoàn tác.`)) return;
      await DB.clear(storeKey);
      await refreshDataManager(storeKey);
    };
    el("search-" + storeKey).oninput = e => {
      managerState[storeKey].search = e.target.value;
      applyManagerSearch(storeKey);
    };
    el("prev-" + storeKey).onclick = () => {
      const st = managerState[storeKey];
      if (st.page > 1) { st.page--; renderManagerTable(storeKey); }
    };
    el("next-" + storeKey).onclick = () => {
      const st = managerState[storeKey];
      const maxPage = Math.max(1, Math.ceil(st.filtered.length / st.pageSize));
      if (st.page < maxPage) { st.page++; renderManagerTable(storeKey); }
    };

    refreshDataManager(storeKey);
  }

  function wireUpload(storeKey, meta) {
    const dz = el("dropzone-" + storeKey);
    const input = el("file-" + storeKey);
    ["dragenter", "dragover"].forEach(evt => dz.addEventListener(evt, e => { e.preventDefault(); dz.classList.add("dragover"); }));
    ["dragleave", "drop"].forEach(evt => dz.addEventListener(evt, e => { e.preventDefault(); dz.classList.remove("dragover"); }));
    dz.addEventListener("drop", e => { const f = e.dataTransfer.files[0]; if (f) handleUpload(storeKey, meta, f); });
    input.addEventListener("change", e => {
      const f = e.target.files[0];
      if (f) handleUpload(storeKey, meta, f);
      input.value = "";
    });
  }

  function normalizeRowHeaders(row, canonicalHeaders) {
    const out = {};
    for (const [rawKey, val] of Object.entries(row)) {
      const match = canonicalHeaders.find(c => normalizeHeader(c) === normalizeHeader(rawKey));
      out[match || rawKey] = val;
    }
    canonicalHeaders.forEach(c => { if (!(c in out)) out[c] = ""; });
    return out;
  }

  function handleUpload(storeKey, meta, file) {
    const reader = new FileReader();
    reader.onload = async e => {
      try {
        const data = new Uint8Array(e.target.result);
        const wb = XLSX.read(data, { type: "array", cellDates: true });
        const ws = wb.Sheets[wb.SheetNames[0]];
        let rows = XLSX.utils.sheet_to_json(ws, { defval: "" });
        if (meta.headers) rows = rows.map(r => normalizeRowHeaders(r, meta.headers));
        const uploadedAt = new Date().toISOString();
        rows = rows.map(r => ({ ...r, __sourceFile: file.name, __uploadedAt: uploadedAt }));
        const n = await DB.bulkPut(storeKey, rows);
        showImportSummary(storeKey, `Đã nhập ${n.toLocaleString("vi-VN")} dòng từ "${file.name}" (sheet: ${wb.SheetNames[0]}).`, true);
        await refreshDataManager(storeKey);
      } catch (err) {
        showImportSummary(storeKey, "Lỗi đọc file: " + err.message, false);
      }
    };
    reader.readAsArrayBuffer(file);
  }

  function showImportSummary(storeKey, msg, ok) {
    const box = el("importSummary-" + storeKey);
    box.textContent = msg;
    box.className = "import-summary " + (ok ? "ok" : "err");
  }

  async function refreshDataManager(storeKey) {
    const meta = STORE_META[storeKey];
    const rows = await DB.getAllWithKeys(storeKey);
    const st = managerState[storeKey];
    st.all = rows;
    st.columns = meta.headers || inferColumns(rows);
    el("count-" + storeKey).textContent = `${rows.length.toLocaleString("vi-VN")} dòng`;
    renderFileBatches(storeKey);
    applyManagerSearch(storeKey);
  }

  function renderFileBatches(storeKey) {
    const st = managerState[storeKey];
    const groups = new Map(); // label -> { count, uploadedAt }
    st.all.forEach(({ value }) => {
      const label = batchLabel(value);
      if (!groups.has(label)) groups.set(label, { count: 0, uploadedAt: value.__uploadedAt || null });
      groups.get(label).count++;
    });

    const box = el("fileBatches-" + storeKey);
    if (groups.size === 0) { box.hidden = true; return; }
    box.hidden = false;

    const list = el("fileBatchList-" + storeKey);
    list.innerHTML = Array.from(groups.entries()).map(([label, info]) => {
      const when = info.uploadedAt ? new Date(info.uploadedAt).toLocaleString("vi-VN") : "";
      return `<div class="file-batch-row">
        <span class="file-batch-name">${escapeHtml(label)}</span>
        <span class="muted">${info.count.toLocaleString("vi-VN")} dòng${when ? " · " + when : ""}</span>
        <button class="btn btn-danger btn-sm" data-label="${escapeHtml(label)}">Xóa file này</button>
      </div>`;
    }).join("");

    list.querySelectorAll("button[data-label]").forEach(btn => {
      btn.onclick = async () => {
        const label = btn.dataset.label;
        if (!confirm(`Xóa toàn bộ dữ liệu từ "${label}"?`)) return;
        const targets = st.all.filter(({ value }) => batchLabel(value) === label);
        for (const t of targets) await DB.delete(storeKey, t.key);
        await refreshDataManager(storeKey);
      };
    });
  }

  function applyManagerSearch(storeKey) {
    const st = managerState[storeKey];
    const q = stripDiacritics(st.search || "");
    st.filtered = !q ? st.all : st.all.filter(({ value }) =>
      Object.values(value).some(v => stripDiacritics(String(v ?? "")).includes(q))
    );
    st.page = 1;
    renderManagerTable(storeKey);
  }

  function renderManagerTable(storeKey) {
    const st = managerState[storeKey];
    const cols = st.columns;
    const table = el("table-" + storeKey);
    const thead = table.querySelector("thead");
    const tbody = table.querySelector("tbody");

    thead.innerHTML = "<tr>" + cols.map(c => `<th>${escapeHtml(c)}</th>`).join("") + "<th>Thao tác</th></tr>";

    const start = (st.page - 1) * st.pageSize;
    const pageRows = st.filtered.slice(start, start + st.pageSize);

    if (!pageRows.length) {
      tbody.innerHTML = `<tr><td colspan="${cols.length + 1}" class="muted" style="padding:20px;">Chưa có dữ liệu</td></tr>`;
    } else {
      tbody.innerHTML = pageRows.map(row =>
        "<tr>" + cols.map(c => `<td>${escapeHtml(formatCell(row.value[c]))}</td>`).join("") +
        `<td class="row-actions"><button class="btn btn-ghost btn-sm act-edit">Sửa</button><button class="btn btn-danger btn-sm act-del">Xóa</button></td></tr>`
      ).join("");
      tbody.querySelectorAll(".act-edit").forEach((btn, i) => { btn.onclick = () => openRowModal(storeKey, pageRows[i]); });
      tbody.querySelectorAll(".act-del").forEach((btn, i) => {
        btn.onclick = async () => {
          if (!confirm("Xóa dòng này?")) return;
          await DB.delete(storeKey, pageRows[i].key);
          await refreshDataManager(storeKey);
        };
      });
    }

    const maxPage = Math.max(1, Math.ceil(st.filtered.length / st.pageSize));
    el("pageInfo-" + storeKey).textContent = `Trang ${st.page} / ${maxPage} (${st.filtered.length.toLocaleString("vi-VN")} dòng)`;
  }

  /* ================= Add / Edit row modal (shared by the 4 data managers) ================= */
  let rowModalContext = null;

  function openRowModal(storeKey, row) {
    const meta = STORE_META[storeKey];
    const st = managerState[storeKey];
    const columns = meta.headers || st.columns;
    const isNew = !row;

    rowModalContext = { storeKey, key: row ? row.key : undefined, isNew, columns, originalValue: row ? row.value : null };
    el("modalTitle").textContent = (isNew ? "Thêm dòng — " : "Sửa dòng — ") + meta.label;

    const body = el("modalBody");
    if (!columns.length) {
      body.innerHTML = `<p class="muted">Chưa xác định được cột dữ liệu — hãy tải lên ít nhất 1 file trước.</p>`;
    } else {
      const values = row ? row.value : {};
      body.innerHTML = `<div class="form-grid">` + columns.map(c => {
        const isKey = meta.primaryKeyHeader === c;
        const val = values[c] ?? "";
        const disabledAttr = (isKey && !isNew) ? "disabled" : "";
        return `<div class="form-field"><label>${escapeHtml(c)}${isKey ? " 🔑" : ""}</label><input type="text" data-col="${escapeHtml(c)}" value="${escapeHtml(val)}" ${disabledAttr}/></div>`;
      }).join("") + `</div>`;
    }
    el("modalOverlay").hidden = false;
  }

  function closeRowModal() {
    el("modalOverlay").hidden = true;
    rowModalContext = null;
  }

  function wireRowModal() {
    el("modalClose").onclick = closeRowModal;
    el("modalCancel").onclick = closeRowModal;
    el("modalSave").onclick = async () => {
      if (!rowModalContext || !rowModalContext.columns.length) { closeRowModal(); return; }
      const { storeKey, key, isNew, originalValue } = rowModalContext;
      const inputs = el("modalBody").querySelectorAll("input[data-col]");
      const record = originalValue ? { ...originalValue } : {};
      inputs.forEach(inp => { record[inp.dataset.col] = inp.value; });

      if (isNew) {
        await DB.bulkPut(storeKey, [record]);
      } else {
        await DB.put(storeKey, record, key);
      }
      closeRowModal();
      await refreshDataManager(storeKey);
    };
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
          await API.apiJson(`/api/reports/${id}`, { method: "DELETE" });
          await refreshReportsList();
          refreshDashboard();
        };
      });
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
          await API.apiJson(`/api/cashflow-reports/${id}`, { method: "DELETE" });
          await refreshCashflowReportsList();
          refreshDashboard();
        };
      });
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

  /* ================= Dashboard (aggregates every ready Report — see
     /api/dashboard/summary + /rows; the date/category/status filters below
     are how the user narrows the view, not a per-Report picker) ================= */
  const dash = {
    reportsCount: 0,
    readyCount: 0,
    subtab: "overview",
    detailSearch: "",
    detailGroupBy: "",
    detailSort: "date",
    detailSortDir: "asc",
    detailPage: 1,
    detailPageSize: 15,
    visibleCols: null, // Set, lazily loaded from localStorage — TABLE_COLS isn't defined yet at this point in the file
    expandedGroups: new Map(), // groupValue -> { rows, total, page, pageSize, loading }
    lastDetailResult: null,
    lastDetailGrouped: false,
    // Multi-select filters — each holds the set of currently-checked values;
    // an empty Set means "Tất cả" (no filter on that field).
    selectedStatus: new Set(),
    selectedWarehouseType: new Set(),
    selectedItemGroup: new Set(),
    selectedProductType: new Set(),
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
    const from = el("filterFrom").value;
    const to = el("filterTo").value;
    const sku = el("filterSku").value.trim();
    if (from) params.set("from", from);
    if (to) params.set("to", to);
    // Multi-select — each checked value becomes its own "status="/etc entry
    // (append, not set) so the backend can match "any of these".
    dash.selectedStatus.forEach(v => params.append("status", v));
    dash.selectedWarehouseType.forEach(v => params.append("warehouseType", v));
    dash.selectedItemGroup.forEach(v => params.append("itemGroup", v));
    dash.selectedProductType.forEach(v => params.append("productType", v));
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

  // Filters only apply when the user clicks "Tìm kiếm" (not on every field
  // change) — this also avoids a race where an earlier, slower request
  // (e.g. the initial unfiltered load) resolves after a later filtered one
  // and overwrites it; fetchAndRenderSummary/DetailTable guard against
  // that too.
  function initDashboardFilters() {
    el("btnApplyFilter").onclick = () => applyFiltersAndRender();
    el("btnClearFilter").onclick = () => {
      el("filterFrom").value = "";
      el("filterTo").value = "";
      dash.selectedStatus.clear();
      dash.selectedWarehouseType.clear();
      dash.selectedItemGroup.clear();
      dash.selectedProductType.clear();
      el("filterSku").value = "";
      if (dash.lastFacets) renderFacets(dash.lastFacets); // redraw checkboxes as unchecked
      applyFiltersAndRender();
    };
    el("tableSearch").oninput = e => {
      dash.detailSearch = e.target.value;
      dash.detailPage = 1;
      dash.expandedGroups.clear();
      fetchAndRenderDetailTable();
    };
    el("detailGroupBy").onchange = e => {
      dash.detailGroupBy = e.target.value;
      dash.detailPage = 1;
      dash.detailSort = dash.detailGroupBy ? "doanhSo" : "date";
      dash.detailSortDir = dash.detailGroupBy ? "desc" : "asc";
      dash.expandedGroups.clear();
      fetchAndRenderDetailTable();
    };
    el("detailPrev").onclick = () => {
      if (dash.detailPage > 1) { dash.detailPage--; dash.expandedGroups.clear(); fetchAndRenderDetailTable(); }
    };
    el("detailNext").onclick = () => { dash.detailPage++; dash.expandedGroups.clear(); fetchAndRenderDetailTable(); };
    el("btnExportExcel").onclick = () => exportExcel();
    wireSubtabs();
    ensureVisibleCols();
    renderColumnPicker();
  }

  function applyFiltersAndRender() {
    dash.detailPage = 1;
    dash.expandedGroups.clear();
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
      if (dash.detailGroupBy) {
        const params = currentFilterParams({
          search: dash.detailSearch, groupBy: dash.detailGroupBy,
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

    renderMultiSelectFacet("filterStatusList", "filterStatusSummary", dash.selectedStatus, statuses);
    renderMultiSelectFacet("filterWarehouseTypeList", "filterWarehouseTypeSummary", dash.selectedWarehouseType, warehouseTypes);
    renderMultiSelectFacet("filterItemGroupList", "filterItemGroupSummary", dash.selectedItemGroup, itemGroups);
    renderMultiSelectFacet("filterProductTypeList", "filterProductTypeSummary", dash.selectedProductType, productTypes);
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
    { key: "platformFee", label: "Phí sàn", fmt: v => fmtNumber(v) },
    { key: "piship", label: "Phí Piship", fmt: v => fmtNumber(v) },
    { key: "phiAff", label: "Phí AFF", fmt: v => fmtNumber(v) },
    { key: "phanLoaiKho", label: "Phân loại kho" },
    { key: "phanLoaiMuc", label: "Phân loại mục" },
    { key: "phanLoaiSp", label: "Phân loại sản phẩm" },
    { key: "giaVon", label: "Giá vốn", fmt: v => fmtNumber(v) },
    { key: "trangThai", label: "Trạng thái" },
  ];

  // Mirrors app/query_engine.py's ALLOWED_SORT_COLUMNS — only these flat
  // columns are click-to-sort; the backend silently falls back to "date"
  // for anything else, so the UI must not offer a sort arrow it can't honor.
  const SORTABLE_FLAT_COLUMNS = new Set([
    "date", "orderId", "product", "category", "customer", "quantity", "doanhSo", "trangThai",
  ]);

  // Mirrors app/routers/dashboard.py's GROUP_BY_LABELS/GROUP_AGG_LABELS —
  // the grouped view's own column set (all sortable — see GROUP_SORT_COLUMNS
  // in query_engine.py). Every key here except rowCount is also a TABLE_COLS
  // key, so the same column-picker Set drives both views.
  const GROUP_BY_LABELS = {
    sku: "SKU", product: "Sản phẩm", category: "Danh mục", customer: "Khách hàng",
    status: "Trạng thái", warehouseType: "Phân loại kho", itemGroup: "Phân loại mục",
    productType: "Phân loại sản phẩm", orderId: "Mã đơn hàng",
  };
  const GROUP_AGG_COLS = [
    { key: "rowCount", label: "Số dòng", fmt: v => Number(v).toLocaleString("vi-VN") },
    { key: "quantity", label: "Số lượng", fmt: v => Number(v).toLocaleString("vi-VN") },
    { key: "returnedQty", label: "SL hoàn trả", fmt: v => Number(v).toLocaleString("vi-VN") },
    { key: "soLuongThuc", label: "SL thực", fmt: v => Number(v).toLocaleString("vi-VN") },
    { key: "doanhSo", label: "Doanh số", fmt: v => fmtNumber(v) },
    { key: "discount", label: "Giảm giá", fmt: v => fmtNumber(v) },
    { key: "voucher", label: "Voucher", fmt: v => fmtNumber(v) },
    { key: "platformFee", label: "Phí sàn", fmt: v => fmtNumber(v) },
    { key: "piship", label: "Phí Piship", fmt: v => fmtNumber(v) },
    { key: "phiAff", label: "Phí AFF", fmt: v => fmtNumber(v) },
    { key: "giaVon", label: "Giá vốn", fmt: v => fmtNumber(v) },
  ];

  function sortArrow(sortKey, currentSort, currentDir) {
    if (currentSort !== sortKey) return '<span class="sort-arrow">↕</span>';
    return `<span class="sort-arrow">${currentDir === "desc" ? "▼" : "▲"}</span>`;
  }

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
        dash.expandedGroups.clear();
        fetchAndRenderDetailTable();
      };
    });
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
    const groupLabel = GROUP_BY_LABELS[dash.detailGroupBy] || "Nhóm";
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
      tbody.innerHTML = result.rows.map(r => renderGroupRowHtml(r, cols)).join("");
      wireGroupRowInteractions(tbody);
    }

    const maxPage = Math.max(1, Math.ceil(result.total / result.pageSize));
    el("detailPageInfo").textContent = `Trang ${result.page} / ${maxPage} (${result.total.toLocaleString("vi-VN")} nhóm)`;
  }

  function renderGroupRowHtml(r, cols) {
    const key = String(r.groupValue ?? "");
    const expanded = dash.expandedGroups.has(key);
    const chevron = expanded ? "▼" : "▶";
    const label = r.groupValue == null || r.groupValue === "" ? "(Trống)" : r.groupValue;
    let html = `<tr class="group-row" data-group-value="${escapeHtml(key)}">` +
      `<td><span class="group-chevron">${chevron}</span>${escapeHtml(label)}</td>` +
      cols.map(c => `<td>${c.fmt(r[c.key])}</td>`).join("") +
      `</tr>`;
    if (expanded) html += renderGroupDetailRowsHtml(key, cols.length + 1);
    return html;
  }

  // The drill-down uses its own nested <table> inside one wide <td>, rather
  // than trying to align full detail columns under the grouped-aggregate
  // header row — the two column sets don't match, so sharing one <table>'s
  // column grid would misrender.
  function renderGroupDetailRowsHtml(groupValue, colspan) {
    const state = dash.expandedGroups.get(groupValue);
    if (!state) return "";
    if (state.loading) {
      return `<tr class="group-detail-row"><td colspan="${colspan}" class="muted" style="padding:12px;">Đang tải...</td></tr>`;
    }
    const flatCols = TABLE_COLS.filter(c => dash.visibleCols.has(c.key));
    const rowsHtml = state.rows.map(row =>
      "<tr>" + flatCols.map(c => {
        const v = row[c.key];
        return `<td>${v == null || v === "" ? "" : (c.fmt ? c.fmt(v) : escapeHtml(v))}</td>`;
      }).join("") + "</tr>"
    ).join("");
    const remaining = state.total - state.rows.length;
    const moreHtml = remaining > 0
      ? `<button class="btn btn-ghost btn-sm group-load-more" data-group-value="${escapeHtml(groupValue)}">Tải thêm (còn ${remaining.toLocaleString("vi-VN")})</button>`
      : "";
    return `<tr class="group-detail-row"><td colspan="${colspan}">
        <div class="table-scroll" style="max-height:260px;">
          <table><thead><tr>${flatCols.map(c => `<th>${escapeHtml(c.label)}</th>`).join("")}</tr></thead>
          <tbody>${rowsHtml || `<tr><td colspan="${flatCols.length}" class="muted">Không có dữ liệu</td></tr>`}</tbody></table>
        </div>
        ${moreHtml}
      </td></tr>`;
  }

  function wireGroupRowInteractions(tbody) {
    tbody.querySelectorAll("tr.group-row").forEach(tr => {
      tr.onclick = () => toggleGroupExpand(tr.dataset.groupValue);
    });
    tbody.querySelectorAll(".group-load-more").forEach(btn => {
      btn.onclick = e => { e.stopPropagation(); loadMoreGroupDetail(btn.dataset.groupValue); };
    });
  }

  async function toggleGroupExpand(groupValue) {
    if (dash.expandedGroups.has(groupValue)) {
      dash.expandedGroups.delete(groupValue);
      rerenderDetailTableOnly();
      return;
    }
    dash.expandedGroups.set(groupValue, { rows: [], total: 0, page: 1, pageSize: 50, loading: true });
    rerenderDetailTableOnly();
    await fetchGroupDetailPage(groupValue, 1);
  }

  async function fetchGroupDetailPage(groupValue, page) {
    const params = currentFilterParams({
      search: dash.detailSearch, groupBy: dash.detailGroupBy, groupValue,
      sort: "date", sort_dir: "asc", page, pageSize: 50,
    });
    try {
      const result = await API.apiJson(`/api/dashboard/rows?${params.toString()}`);
      const prev = dash.expandedGroups.get(groupValue);
      if (!prev) return; // collapsed while the request was in flight
      const rows = page === 1 ? result.rows : [...prev.rows, ...result.rows];
      dash.expandedGroups.set(groupValue, { rows, total: result.total, page, pageSize: 50, loading: false });
      rerenderDetailTableOnly();
    } catch (err) {
      console.error("Group drill-down fetch failed:", err);
      dash.expandedGroups.delete(groupValue);
      rerenderDetailTableOnly();
    }
  }

  function loadMoreGroupDetail(groupValue) {
    const state = dash.expandedGroups.get(groupValue);
    if (state) fetchGroupDetailPage(groupValue, state.page + 1);
  }

  async function exportExcel() {
    const btn = el("btnExportExcel");
    const originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Đang xuất...";
    try {
      ensureVisibleCols();
      const exportCols = dash.detailGroupBy
        ? ["groupValue", "rowCount", ...GROUP_AGG_COLS.filter(c => c.key !== "rowCount" && dash.visibleCols.has(c.key)).map(c => c.key)]
        : TABLE_COLS.filter(c => dash.visibleCols.has(c.key)).map(c => c.key);
      const params = currentFilterParams({
        search: dash.detailSearch, sort: dash.detailSort, sortDir: dash.detailSortDir,
        columns: exportCols.join(","),
      });
      if (dash.detailGroupBy) params.set("groupBy", dash.detailGroupBy);

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
    Object.keys(STORE_META).forEach(setupDataManager);
    wireRowModal();
    wireOrdersTab();
    wireCashflowTab();
    wireComboTab();
    wireMasterTab();
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
