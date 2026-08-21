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

  /* ================= Store metadata (Master/Combo/Dòng tiền/Điều chỉnh — Orders is now API-backed, not a generic store) ================= */
  const CASHFLOW_HEADERS = ["Mã giao dịch", "Đơn hàng / Sản phẩm", "Mã đơn hàng", "Mã Số Thuế", "Mã yêu cầu hoàn tiền", "Mã sản phẩm", "Tên sản phẩm", "Ngày đặt hàng", "Ngày hoàn thành thanh toán", "Phương thức thanh toán", "Phân Loại", "Sản Phẩm Bán Chạy", "Tổng tiền đã thanh toán", "Giá sản phẩm", "Số tiền hoàn lại", "Phí vận chuyển Người mua trả", "Phí vận chuyển thực tế", "Phí vận chuyển được trợ giá từ Shopee", "Phí vận chuyển trả hàng (đơn Trả hàng/hoàn tiền)", "Phí vận chuyển được hoàn bởi PiShip", "Phí vận chuyển trả hàng (đơn giao không thành công)", "Sản phẩm được trợ giá từ Shopee", "Mã ưu đãi do Người Bán chịu", "Mã ưu đãi Đồng Tài Trợ do Người Bán chịu", "Mã hoàn xu do Người Bán chịu", "Mã hoàn xu Đồng Tài Trợ do Người Bán chịu", "Phí cố định", "Phí Dịch Vụ", "Phí xử lý giao dịch", "Phí hoa hồng Tiếp thị liên kết", "Phí dịch vụ PiShip", "Phí dịch vụ hiển thị NTTD (từ doanh thu đơn hàng)", "Thuế GTGT", "Thuế TNCN", "Phí lắp đặt người mua trả", "Phí lắp đặt thực tế", "Trade-in Bonus by Seller", "Người Mua", "Amount Paid By Buyer", "Transaction Fee Rate (%)", "Phương thức thanh toán của Người mua", "Buyer Payment Method Details_1", "Installment Plan (if applicable)", "Phí vận chuyển - Người bán hỗ trợ", "Đơn vị vận chuyển", "Courier Name", "Mã voucher", "Đền bù đơn mất hàng", "Giá sản phẩm (sau khuyến mãi)", "Shopee xu", "Shopee voucher", "Ngân hàng khuyến mãi thanh toán trên Thẻ Tín Dụng", "Shopee khuyến mãi thanh toán trên Thẻ Tín Dụng"];

  const STORE_META = {
    master: {
      label: "Master File",
      headers: ["SKU", "SKU phân loại", "Màu", "Size", "Mục", "Phân loại SP", "Phân loại kho ONL", "Gía vốn"],
      primaryKeyHeader: "SKU phân loại",
    },
    combo: {
      label: "Combo",
      headers: ["PHÂN LOẠI", "Tỉ lệ SKU 1", "Tỉ lệ SKU 2", "Tỉ lệ SKU 3", "SKU1", "SKU2", "SKU3", "SKU COMBO", "Giá vốn SKU 1", "Giá vốn SKU 2", "Giá vốn SKU 3", "Giá vốn"],
      primaryKeyHeader: "SKU COMBO",
    },
    cashflow: { label: "Dòng tiền", headers: CASHFLOW_HEADERS, primaryKeyHeader: "Mã đơn hàng" },
    adjustments: {
      label: "Điều chỉnh doanh thu",
      headers: ["Mã giao dịch", "Ngày hoàn thành điều chỉnh đơn hàng", "Loại điều chỉnh | Mô tả", "Lý do điều chỉnh", "Số tiền điều chỉnh", "Mã đơn hàng liên quan", "Ngày hoàn thành thanh toán"],
      primaryKeyHeader: null,
    },
  };

  /* ================= Tabs ================= */
  function initTabs() {
    document.querySelectorAll(".tab-btn").forEach(btn => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        document.querySelectorAll(".tab-panel").forEach(p => { p.hidden = true; });
        el("panel-" + btn.dataset.tab).hidden = false;
      });
    });
  }

  /* ================= Generic data manager (Master/Combo/Dòng tiền/Điều chỉnh) ================= */
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

  /* ================= Dashboard (aggregates every ready Report — see
     /api/dashboard/summary + /rows; the date/category/status filters below
     are how the user narrows the view, not a per-Report picker) ================= */
  const dash = {
    reportsCount: 0,
    readyCount: 0,
    tableSearch: "",
    tablePage: 1,
    tablePageSize: 15,
    tableSort: "date",
    tableSortDir: "asc",
    filtersWired: false,
    charts: {},
    summarySeq: 0,
    rowsSeq: 0,
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
    await Promise.all([fetchAndRenderSummary(), fetchAndRenderRows()]);
  }

  function currentFilterParams(extra) {
    const params = new URLSearchParams();
    const from = el("filterFrom").value;
    const to = el("filterTo").value;
    const status = el("filterStatus").value;
    if (from) params.set("from", from);
    if (to) params.set("to", to);
    if (status) params.set("status", status);
    Object.entries(extra || {}).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== "") params.set(k, v); });
    return params;
  }

  // Filters only apply when the user clicks "Tìm kiếm" (not on every field
  // change) — this also avoids a race where an earlier, slower request
  // (e.g. the initial unfiltered load) resolves after a later filtered one
  // and overwrites it; fetchAndRenderSummary/Rows guard against that too.
  function initDashboardFilters() {
    el("btnApplyFilter").onclick = () => applyFiltersAndRender();
    el("btnClearFilter").onclick = () => {
      el("filterFrom").value = "";
      el("filterTo").value = "";
      el("filterStatus").value = "";
      applyFiltersAndRender();
    };
    el("tableSearch").oninput = e => {
      dash.tableSearch = e.target.value;
      dash.tablePage = 1;
      fetchAndRenderRows();
    };
    el("btnPrev").onclick = () => { if (dash.tablePage > 1) { dash.tablePage--; fetchAndRenderRows(); } };
    el("btnNext").onclick = () => { dash.tablePage++; fetchAndRenderRows(); };
  }

  function applyFiltersAndRender() {
    dash.tablePage = 1;
    // TEMP DEBUG — shows the raw input values read at the moment of click.
    const debugClickEl = el("dashboardFilterDebugClick");
    if (debugClickEl) {
      debugClickEl.textContent = `[debug-click] Bấm Tìm kiếm lúc ${new Date().toLocaleTimeString("vi-VN")} — filterFrom.value="${el("filterFrom").value}", filterTo.value="${el("filterTo").value}", filterStatus.value="${el("filterStatus").value}"`;
    }
    fetchAndRenderSummary();
    fetchAndRenderRows();
  }

  async function fetchAndRenderSummary() {
    const seq = ++dash.summarySeq;
    const params = currentFilterParams();
    const debugEl = el("dashboardFilterDebug");
    try {
      const summary = await API.apiJson(`/api/dashboard/summary?${params.toString()}`);
      if (seq !== dash.summarySeq) return; // a newer request already superseded this one
      renderKPIs(summary.kpis);
      renderFacets(summary.facets);
      renderCharts(summary);
      // TEMP DEBUG — remove once the filter issue is confirmed fixed.
      if (debugEl) {
        debugEl.textContent = `[debug] gửi: /api/dashboard/summary?${params.toString()} → server trả về ${summary.kpis.rowCount} dòng, doanhSo=${summary.kpis.doanhSo}`;
      }
    } catch (err) {
      if (debugEl) {
        debugEl.textContent = `[debug-error] /api/dashboard/summary?${params.toString()} → LỖI: ${err.message}`;
      }
    }
  }

  async function fetchAndRenderRows() {
    const seq = ++dash.rowsSeq;
    const params = currentFilterParams({
      search: dash.tableSearch,
      sort: dash.tableSort,
      sort_dir: dash.tableSortDir,
      page: dash.tablePage,
      pageSize: dash.tablePageSize,
    });
    try {
      const result = await API.apiJson(`/api/dashboard/rows?${params.toString()}`);
      if (seq !== dash.rowsSeq) return; // a newer request already superseded this one
      renderTable(result);
    } catch (err) {
      const debugEl = el("dashboardFilterDebug");
      if (debugEl) {
        debugEl.textContent += ` | [debug-error rows] ${err.message}`;
      }
    }
  }

  // Status dropdown is rebuilt from the (unfiltered) summary response each
  // time, but the user's current selection is preserved if still valid.
  function renderFacets(facets) {
    const statusSel = el("filterStatus");
    const curStatus = statusSel.value;
    const STATUS_ORDER = ["Hoàn thành", "Đang giao", "Hoàn 1 phần", "Hoàn hàng", "Hủy chưa XK", "Hủy sau XK"];
    const statuses = [...facets.statuses].sort((a, b) => STATUS_ORDER.indexOf(a) - STATUS_ORDER.indexOf(b));
    statusSel.innerHTML = '<option value="">Tất cả</option>' + statuses.map(s => `<option value="${escapeHtml(s)}">${escapeHtml(s)}</option>`).join("");
    if (facets.statuses.includes(curStatus)) statusSel.value = curStatus;
  }

  /* ---- KPIs ---- */
  function renderKPIs(kpis) {
    el("kpiDoanhSo").textContent = fmtNumber(kpis.doanhSo);
    el("kpiDoanhSoThuan").textContent = fmtNumber(kpis.gmv);
    el("kpiHuyChuaXK").textContent = fmtNumber(kpis.huyChuaXK);
    el("kpiHuySauXK").textContent = fmtNumber(kpis.huySauXK);
    el("kpiHoan").textContent = fmtNumber(kpis.hoan);
    el("kpiDoanhSoSub").textContent = `${kpis.rowCount.toLocaleString("vi-VN")} dòng dữ liệu`;
  }

  /* ---- Charts ---- */
  const PALETTE = ["#3a5cf0", "#17a673", "#f0a53a", "#e34b4b", "#8a5cf0", "#3ac7f0", "#f06ab0", "#7cb342"];

  function destroyChart(key) {
    if (dash.charts[key]) { dash.charts[key].destroy(); delete dash.charts[key]; }
  }

  function baseOptions(scaleOverrides = {}, horizontalLabels = false) {
    return {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { autoSkip: true, maxRotation: horizontalLabels ? 30 : 0 } },
        y: { beginAtZero: true, ...scaleOverrides.y },
      },
    };
  }

  function renderCharts(summary) {
    destroyChart("timeline");
    dash.charts.timeline = new Chart(el("chartTimeline"), {
      type: "line",
      data: {
        labels: summary.timeline.map(p => p.month),
        datasets: [{ label: "Doanh số", data: summary.timeline.map(p => p.value), borderColor: PALETTE[0], backgroundColor: "rgba(58,92,240,0.12)", fill: true, tension: 0.3, pointRadius: 3 }],
      },
      options: baseOptions({ y: { ticks: { callback: v => fmtNumber(v) } } }),
    });

    destroyChart("topProducts");
    dash.charts.topProducts = new Chart(el("chartTopProducts"), {
      type: "bar",
      data: { labels: summary.topProducts.map(p => p.label), datasets: [{ label: "Doanh số", data: summary.topProducts.map(p => p.value), backgroundColor: PALETTE[0] }] },
      options: baseOptions({ y: { ticks: { callback: v => fmtNumber(v) } } }, true),
    });

    const hasCategory = summary.categoryBreakdown.length > 0;
    el("cardCategory").hidden = !hasCategory;
    if (hasCategory) {
      destroyChart("category");
      dash.charts.category = new Chart(el("chartCategory"), {
        type: "doughnut",
        data: { labels: summary.categoryBreakdown.map(p => p.label), datasets: [{ data: summary.categoryBreakdown.map(p => p.value), backgroundColor: PALETTE }] },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: "right", labels: { boxWidth: 12, font: { size: 11 } } } } },
      });
    }

    const hasCustomers = summary.topCustomers.length > 0;
    el("cardTopCustomers").hidden = !hasCustomers;
    if (hasCustomers) {
      destroyChart("topCustomers");
      dash.charts.topCustomers = new Chart(el("chartTopCustomers"), {
        type: "bar",
        data: { labels: summary.topCustomers.map(p => p.label), datasets: [{ label: "Doanh số", data: summary.topCustomers.map(p => p.value), backgroundColor: PALETTE[1] }] },
        options: baseOptions({ y: { ticks: { callback: v => fmtNumber(v) } } }, true),
      });
    }
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
    { key: "trangThai", label: "Trạng thái" },
  ];

  function renderTable(result) {
    const thead = document.querySelector("#dataTable thead");
    const tbody = document.querySelector("#dataTable tbody");
    thead.innerHTML = "<tr>" + TABLE_COLS.map(c => `<th>${c.label}</th>`).join("") + "</tr>";

    tbody.innerHTML = result.rows.map(r =>
      "<tr>" + TABLE_COLS.map(c => {
        const v = r[c.key];
        return `<td>${v == null || v === "" ? "" : (c.fmt ? c.fmt(v) : escapeHtml(v))}</td>`;
      }).join("") + "</tr>"
    ).join("") || `<tr><td colspan="${TABLE_COLS.length}" class="muted" style="padding:20px;">Không có dữ liệu</td></tr>`;

    const maxPage = Math.max(1, Math.ceil(result.total / result.pageSize));
    el("pageInfo").textContent = `Trang ${result.page} / ${maxPage} (${result.total.toLocaleString("vi-VN")} dòng)`;
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
