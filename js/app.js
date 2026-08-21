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
    if (!isFinite(n)) return "–";
    return Math.round(n).toLocaleString("vi-VN");
  }

  function parseDateValue(v) {
    if (v instanceof Date && !isNaN(v)) return v;
    if (typeof v === "number") {
      const d = XLSX.SSF.parse_date_code(v);
      if (d) return new Date(d.y, d.m - 1, d.d);
    }
    if (typeof v === "string") {
      const s = v.trim();
      const datePart = s.split(/[\sT]/)[0];
      let m = datePart.match(/^(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{4})$/);
      if (m) return new Date(+m[3], +m[2] - 1, +m[1]);
      m = datePart.match(/^(\d{4})[\/\-](\d{1,2})[\/\-](\d{1,2})$/);
      if (m) return new Date(+m[1], +m[2] - 1, +m[3]);
      const d = new Date(s);
      if (!isNaN(d)) return d;
    }
    return null;
  }

  function toNumber(v) {
    if (typeof v === "number") return v;
    if (v == null || v === "") return 0;
    const cleaned = String(v).replace(/[^\d.,-]/g, "").replace(/\.(?=\d{3}(\D|$))/g, "").replace(",", ".");
    const n = parseFloat(cleaned);
    return isNaN(n) ? 0 : n;
  }

  function monthKey(d) {
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
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

  function inferColumns(rows) {
    const seen = [];
    const set = new Set();
    rows.slice(0, 50).forEach(({ value }) => Object.keys(value).forEach(k => {
      if (!set.has(k)) { set.add(k); seen.push(k); }
    }));
    return seen;
  }

  /* ================= Orders column detection (for the Dashboard) ================= */
  const FIELDS = [
    { key: "date", label: "Ngày", required: true },
    { key: "product", label: "Sản phẩm", required: false },
    { key: "category", label: "Danh mục", required: false },
    { key: "customer", label: "Khách hàng", required: false },
    { key: "quantity", label: "Số lượng", required: false },
    { key: "price", label: "Đơn giá", required: false },
    { key: "revenue", label: "Doanh thu", required: false },
    { key: "status", label: "Trạng thái đơn hàng", required: false },
    { key: "orderId", label: "Mã đơn hàng", required: false },
    { key: "skuVariant", label: "SKU phân loại hàng", required: false },
  ];

  const KEYWORDS = {
    date: ["ngay dat hang", "ngay ban", "ngay giao dich", "order date", "ngay", "date", "thoi gian"],
    product: ["ten san pham", "ten mat hang", "ten hang", "san pham", "mat hang", "product", "item"],
    category: ["ten phan loai hang", "danh muc san pham", "danh muc", "phan loai hang", "phan loai", "loai", "nhom", "category"],
    customer: ["ten khach hang", "khach hang", "khach", "customer"],
    quantity: ["so luong san pham", "so luong", "qty", "quantity", "sl"],
    price: ["gia uu dai", "don gia", "gia ban", "gia goc", "gia", "price", "unit price"],
    revenue: ["tong gia tri don hang", "tong so tien thanh toan", "doanh thu", "thanh tien", "tong tien", "gia tri don hang", "thanh toan", "revenue", "total", "amount", "gia tri"],
    status: ["trang thai don hang", "trang thai", "status"],
    orderId: ["ma don hang"],
    skuVariant: ["sku phan loai hang", "sku phan loai"],
  };

  const IDENTIFIER_PREFIX = /^(sku|ma|id)\b/;
  const NAME_LIKE_FIELDS = new Set(["product", "category", "customer"]);

  function detectMapping(headers) {
    const normalized = headers.map(h => ({ h, n: normalizeHeader(h) }));
    const result = {};
    for (const field of Object.keys(KEYWORDS)) {
      let bestHeader = null;
      let bestScore = -Infinity;
      for (const { h, n } of normalized) {
        const isIdentifier = IDENTIFIER_PREFIX.test(n);
        for (const w of KEYWORDS[field]) {
          let score;
          if (n === w) score = 100 + w.length;
          else if (n.includes(w)) score = w.length;
          else continue;
          if (isIdentifier && NAME_LIKE_FIELDS.has(field)) score -= 50;
          if (score > bestScore) { bestScore = score; bestHeader = h; }
        }
      }
      if (bestHeader) result[field] = bestHeader;
    }
    return result;
  }

  const CANCELLED_STATUS_WORDS = ["huy", "hoan tien", "hoan tra", "tra hang", "refund", "cancel"];
  function isCancelledStatus(status) {
    if (!status) return false;
    const n = stripDiacritics(status);
    return CANCELLED_STATUS_WORDS.some(w => n.includes(w));
  }

  const MAPPING_OVERRIDE_KEY = "bbstore_mapping_override";
  function loadMappingOverride() {
    try { return JSON.parse(localStorage.getItem(MAPPING_OVERRIDE_KEY) || "null"); }
    catch { return null; }
  }
  function saveMappingOverride(m) { localStorage.setItem(MAPPING_OVERRIDE_KEY, JSON.stringify(m)); }
  function clearMappingOverride() { localStorage.removeItem(MAPPING_OVERRIDE_KEY); }

  /* ================= Store metadata (the 5 saved data types) ================= */
  const CASHFLOW_HEADERS = ["Mã giao dịch", "Đơn hàng / Sản phẩm", "Mã đơn hàng", "Mã Số Thuế", "Mã yêu cầu hoàn tiền", "Mã sản phẩm", "Tên sản phẩm", "Ngày đặt hàng", "Ngày hoàn thành thanh toán", "Phương thức thanh toán", "Phân Loại", "Sản Phẩm Bán Chạy", "Tổng tiền đã thanh toán", "Giá sản phẩm", "Số tiền hoàn lại", "Phí vận chuyển Người mua trả", "Phí vận chuyển thực tế", "Phí vận chuyển được trợ giá từ Shopee", "Phí vận chuyển trả hàng (đơn Trả hàng/hoàn tiền)", "Phí vận chuyển được hoàn bởi PiShip", "Phí vận chuyển trả hàng (đơn giao không thành công)", "Sản phẩm được trợ giá từ Shopee", "Mã ưu đãi do Người Bán chịu", "Mã ưu đãi Đồng Tài Trợ do Người Bán chịu", "Mã hoàn xu do Người Bán chịu", "Mã hoàn xu Đồng Tài Trợ do Người Bán chịu", "Phí cố định", "Phí Dịch Vụ", "Phí xử lý giao dịch", "Phí hoa hồng Tiếp thị liên kết", "Phí dịch vụ PiShip", "Phí dịch vụ hiển thị NTTD (từ doanh thu đơn hàng)", "Thuế GTGT", "Thuế TNCN", "Phí lắp đặt người mua trả", "Phí lắp đặt thực tế", "Trade-in Bonus by Seller", "Người Mua", "Amount Paid By Buyer", "Transaction Fee Rate (%)", "Phương thức thanh toán của Người mua", "Buyer Payment Method Details_1", "Installment Plan (if applicable)", "Phí vận chuyển - Người bán hỗ trợ", "Đơn vị vận chuyển", "Courier Name", "Mã voucher", "Đền bù đơn mất hàng", "Giá sản phẩm (sau khuyến mãi)", "Shopee xu", "Shopee voucher", "Ngân hàng khuyến mãi thanh toán trên Thẻ Tín Dụng", "Shopee khuyến mãi thanh toán trên Thẻ Tín Dụng"];

  const STORE_META = {
    orders: { label: "Đơn hàng", headers: null, primaryKeyHeader: null },
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

  /* ================= Generic data manager (upload / add / edit / delete) ================= */
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
      if (storeKey === "orders") refreshDashboard();
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
        const n = await DB.bulkPut(storeKey, rows);
        showImportSummary(storeKey, `Đã nhập ${n.toLocaleString("vi-VN")} dòng từ "${file.name}" (sheet: ${wb.SheetNames[0]}).`, true);
        await refreshDataManager(storeKey);
        if (storeKey === "orders") refreshDashboard();
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
    applyManagerSearch(storeKey);
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
          if (storeKey === "orders") refreshDashboard();
        };
      });
    }

    const maxPage = Math.max(1, Math.ceil(st.filtered.length / st.pageSize));
    el("pageInfo-" + storeKey).textContent = `Trang ${st.page} / ${maxPage} (${st.filtered.length.toLocaleString("vi-VN")} dòng)`;
  }

  /* ================= Add / Edit row modal (shared by all 5 data managers) ================= */
  let rowModalContext = null;

  function openRowModal(storeKey, row) {
    const meta = STORE_META[storeKey];
    const st = managerState[storeKey];
    const columns = meta.headers || st.columns;
    const isNew = !row;

    rowModalContext = { storeKey, key: row ? row.key : undefined, isNew, columns };
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
      const { storeKey, key, isNew } = rowModalContext;
      const inputs = el("modalBody").querySelectorAll("input[data-col]");
      const record = {};
      inputs.forEach(inp => { record[inp.dataset.col] = inp.value; });

      if (isNew) {
        await DB.bulkPut(storeKey, [record]);
      } else {
        await DB.put(storeKey, record, key);
      }
      closeRowModal();
      await refreshDataManager(storeKey);
      if (storeKey === "orders") refreshDashboard();
    };
  }

  /* ================= Dashboard ================= */
  const dash = {
    raw: [],
    mapping: {},
    records: [],
    filtered: [],
    page: 1,
    pageSize: 15,
    search: "",
    charts: {},
    excludedCancelledCount: 0,
  };

  async function refreshDashboard() {
    const rows = await DB.getAllWithKeys("orders");
    dash.raw = rows;

    if (!rows.length) {
      el("dashboardEmpty").hidden = false;
      el("dashboardContent").hidden = true;
      return;
    }
    el("dashboardEmpty").hidden = true;
    el("dashboardContent").hidden = false;

    const headers = inferColumns(rows);
    const auto = detectMapping(headers);
    const override = loadMappingOverride();
    const mapping = {};
    FIELDS.forEach(f => {
      mapping[f.key] = override ? (override[f.key] || "") : (auto[f.key] || "");
    });
    dash.mapping = mapping;

    renderMappingBanner();
    buildDashboardRecords();
    initDashboardFilters();
    applyFiltersAndRender();
  }

  function renderMappingBanner() {
    const parts = FIELDS.filter(f => dash.mapping[f.key]).map(f => `${f.label} = ${dash.mapping[f.key]}`);
    el("mappingBannerText").textContent = "Đã nhận diện cột: " + (parts.length ? parts.join(" · ") : "chưa nhận diện được cột nào");
  }

  function buildDashboardRecords() {
    const m = dash.mapping;
    const records = [];
    for (const { value: row } of dash.raw) {
      const date = parseDateValue(row[m.date]);
      if (!date) continue;
      const quantity = m.quantity ? toNumber(row[m.quantity]) : null;
      const price = m.price ? toNumber(row[m.price]) : null;
      let revenue = m.revenue ? toNumber(row[m.revenue]) : null;
      if (revenue == null && price != null && quantity != null) revenue = price * quantity;
      if (revenue == null) revenue = 0;

      records.push({
        date,
        product: m.product ? String(row[m.product] ?? "").trim() || "(Không rõ)" : "(Không rõ)",
        category: m.category ? String(row[m.category] ?? "").trim() || "(Không rõ)" : "(Không rõ)",
        customer: m.customer ? String(row[m.customer] ?? "").trim() || "(Không rõ)" : "(Không rõ)",
        quantity: quantity ?? 0,
        price: price ?? 0,
        revenue,
        status: m.status ? String(row[m.status] ?? "").trim() : "",
        orderId: m.orderId ? String(row[m.orderId] ?? "").trim() : "",
        skuVariant: m.skuVariant ? String(row[m.skuVariant] ?? "").trim() : "",
      });
    }
    records.sort((a, b) => a.date - b.date);
    dash.records = records;
  }

  function initDashboardFilters() {
    const cats = Array.from(new Set(dash.records.map(r => r.category))).sort();
    const sel = el("filterCategory");
    sel.innerHTML = '<option value="">Tất cả</option>';
    cats.forEach(c => {
      const opt = document.createElement("option");
      opt.value = c; opt.textContent = c;
      sel.appendChild(opt);
    });

    if (dash.records.length) {
      el("filterFrom").value = toInputDate(dash.records[0].date);
      el("filterTo").value = toInputDate(dash.records[dash.records.length - 1].date);
    }

    const hasStatus = !!dash.mapping.status;
    el("filterCancelledWrap").hidden = !hasStatus;
    el("filterExcludeCancelled").checked = true;

    el("filterFrom").onchange = applyFiltersAndRender;
    el("filterTo").onchange = applyFiltersAndRender;
    el("filterCategory").onchange = applyFiltersAndRender;
    el("filterExcludeCancelled").onchange = applyFiltersAndRender;
    el("btnClearFilter").onclick = () => {
      el("filterFrom").value = dash.records.length ? toInputDate(dash.records[0].date) : "";
      el("filterTo").value = dash.records.length ? toInputDate(dash.records[dash.records.length - 1].date) : "";
      el("filterCategory").value = "";
      applyFiltersAndRender();
    };
    el("tableSearch").oninput = e => { dash.search = e.target.value; dash.page = 1; renderTable(); };
    el("btnPrev").onclick = () => { if (dash.page > 1) { dash.page--; renderTable(); } };
    el("btnNext").onclick = () => {
      const maxPage = Math.max(1, Math.ceil(currentTableRows().length / dash.pageSize));
      if (dash.page < maxPage) { dash.page++; renderTable(); }
    };
  }

  function toInputDate(d) {
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  }
  function parseInputDate(value) {
    if (!value) return null;
    const [y, m, d] = value.split("-").map(Number);
    return new Date(y, m - 1, d);
  }

  function applyFiltersAndRender() {
    const from = parseInputDate(el("filterFrom").value);
    const to = parseInputDate(el("filterTo").value);
    const cat = el("filterCategory").value;
    const excludeCancelled = !!dash.mapping.status && el("filterExcludeCancelled").checked;

    let excludedCount = 0;
    dash.filtered = dash.records.filter(r => {
      if (from && r.date < from) return false;
      if (to && r.date > new Date(to.getFullYear(), to.getMonth(), to.getDate(), 23, 59, 59)) return false;
      if (cat && r.category !== cat) return false;
      if (excludeCancelled && isCancelledStatus(r.status)) { excludedCount++; return false; }
      return true;
    });
    dash.excludedCancelledCount = excludedCount;

    dash.page = 1;
    renderKPIs();
    renderCharts();
    renderTable();
  }

  /* ---- KPIs ---- */
  function renderKPIs() {
    const data = dash.filtered;
    const totalRevenue = data.reduce((s, r) => s + r.revenue, 0);
    const totalQty = data.reduce((s, r) => s + r.quantity, 0);
    const orders = data.length;
    const avg = orders ? totalRevenue / orders : 0;

    el("kpiRevenue").textContent = fmtNumber(totalRevenue);
    el("kpiOrders").textContent = orders.toLocaleString("vi-VN");
    el("kpiQty").textContent = fmtNumber(totalQty);
    el("kpiAvg").textContent = fmtNumber(avg);

    const excluded = dash.excludedCancelledCount || 0;
    el("kpiRevenueSub").textContent = excluded
      ? `${data.length.toLocaleString("vi-VN")} dòng · đã loại ${excluded.toLocaleString("vi-VN")} đơn hủy/hoàn trả`
      : `${data.length.toLocaleString("vi-VN")} dòng dữ liệu`;
    el("kpiOrdersSub").textContent = "trong khoảng đã lọc";
    el("kpiQtySub").textContent = "tổng số lượng bán";
    el("kpiAvgSub").textContent = "doanh thu / dòng";
  }

  /* ---- Charts ---- */
  const PALETTE = ["#3a5cf0", "#17a673", "#f0a53a", "#e34b4b", "#8a5cf0", "#3ac7f0", "#f06ab0", "#7cb342"];

  function destroyChart(key) {
    if (dash.charts[key]) { dash.charts[key].destroy(); delete dash.charts[key]; }
  }

  function renderCharts() {
    renderTimelineChart();
    renderTopProductsChart();

    el("cardCategory").hidden = !dash.mapping.category;
    if (dash.mapping.category) renderCategoryChart();

    el("cardTopCustomers").hidden = !dash.mapping.customer;
    if (dash.mapping.customer) renderTopCustomersChart();
  }

  function renderTimelineChart() {
    const map = new Map();
    dash.filtered.forEach(r => {
      const k = monthKey(r.date);
      map.set(k, (map.get(k) || 0) + r.revenue);
    });
    const labels = Array.from(map.keys()).sort();
    const values = labels.map(k => map.get(k));

    destroyChart("timeline");
    dash.charts.timeline = new Chart(el("chartTimeline"), {
      type: "line",
      data: { labels, datasets: [{ label: "Doanh thu", data: values, borderColor: PALETTE[0], backgroundColor: "rgba(58,92,240,0.12)", fill: true, tension: 0.3, pointRadius: 3 }] },
      options: baseOptions({ y: { ticks: { callback: v => fmtNumber(v) } } }),
    });
  }

  function topN(data, keyFn, n) {
    const map = new Map();
    data.forEach(r => {
      const k = keyFn(r);
      map.set(k, (map.get(k) || 0) + r.revenue);
    });
    return Array.from(map.entries()).sort((a, b) => b[1] - a[1]).slice(0, n);
  }

  function renderTopProductsChart() {
    const top = topN(dash.filtered, r => r.product, 8);
    destroyChart("topProducts");
    dash.charts.topProducts = new Chart(el("chartTopProducts"), {
      type: "bar",
      data: { labels: top.map(t => t[0]), datasets: [{ label: "Doanh thu", data: top.map(t => t[1]), backgroundColor: PALETTE[0] }] },
      options: baseOptions({ y: { ticks: { callback: v => fmtNumber(v) } } }, true),
    });
  }

  function renderCategoryChart() {
    const top = topN(dash.filtered, r => r.category, 8);
    destroyChart("category");
    dash.charts.category = new Chart(el("chartCategory"), {
      type: "doughnut",
      data: { labels: top.map(t => t[0]), datasets: [{ data: top.map(t => t[1]), backgroundColor: PALETTE }] },
      options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: "right", labels: { boxWidth: 12, font: { size: 11 } } } } },
    });
  }

  function renderTopCustomersChart() {
    const top = topN(dash.filtered, r => r.customer, 8);
    destroyChart("topCustomers");
    dash.charts.topCustomers = new Chart(el("chartTopCustomers"), {
      type: "bar",
      data: { labels: top.map(t => t[0]), datasets: [{ label: "Doanh thu", data: top.map(t => t[1]), backgroundColor: PALETTE[1] }] },
      options: baseOptions({ y: { ticks: { callback: v => fmtNumber(v) } } }, true),
    });
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

  /* ---- Detail table ---- */
  const TABLE_COLS = [
    { key: "date", label: "Ngày", fmt: d => d.toLocaleDateString("vi-VN") },
    { key: "orderId", label: "Mã đơn hàng" },
    { key: "product", label: "Sản phẩm" },
    { key: "category", label: "Danh mục" },
    { key: "customer", label: "Khách hàng" },
    { key: "quantity", label: "Số lượng", fmt: v => v.toLocaleString("vi-VN") },
    { key: "price", label: "Đơn giá", fmt: v => fmtNumber(v) },
    { key: "revenue", label: "Doanh thu", fmt: v => fmtNumber(v) },
  ];

  function currentTableRows() {
    if (!dash.search) return dash.filtered;
    const q = stripDiacritics(dash.search);
    return dash.filtered.filter(r =>
      [r.product, r.category, r.customer, r.orderId].some(v => stripDiacritics(v).includes(q))
    );
  }

  function renderTable() {
    const cols = TABLE_COLS.filter(c => c.key !== "orderId" || dash.mapping.orderId);
    const thead = document.querySelector("#dataTable thead");
    const tbody = document.querySelector("#dataTable tbody");
    thead.innerHTML = "<tr>" + cols.map(c => `<th>${c.label}</th>`).join("") + "</tr>";

    const rows = currentTableRows();
    const start = (dash.page - 1) * dash.pageSize;
    const pageRows = rows.slice(start, start + dash.pageSize);

    tbody.innerHTML = pageRows.map(r =>
      "<tr>" + cols.map(c => {
        const v = r[c.key];
        return `<td>${c.fmt ? c.fmt(v) : escapeHtml(v)}</td>`;
      }).join("") + "</tr>"
    ).join("") || `<tr><td colspan="${cols.length}" class="muted" style="padding:20px;">Không có dữ liệu</td></tr>`;

    const maxPage = Math.max(1, Math.ceil(rows.length / dash.pageSize));
    el("pageInfo").textContent = `Trang ${dash.page} / ${maxPage} (${rows.length.toLocaleString("vi-VN")} dòng)`;
  }

  /* ---- Mapping override modal ---- */
  function wireMappingModal() {
    el("btnEditMapping").onclick = () => {
      const headers = inferColumns(dash.raw);
      const grid = el("mappingModalGrid");
      grid.innerHTML = "";
      FIELDS.forEach(f => {
        const wrap = document.createElement("div");
        wrap.className = "mapping-field";
        const label = document.createElement("label");
        label.textContent = f.label;
        if (f.required) label.classList.add("req");
        const select = document.createElement("select");
        select.id = "mmap_" + f.key;
        const noneOpt = document.createElement("option");
        noneOpt.value = ""; noneOpt.textContent = "-- Không có --";
        select.appendChild(noneOpt);
        headers.forEach(h => {
          const opt = document.createElement("option");
          opt.value = h; opt.textContent = h;
          if (dash.mapping[f.key] === h) opt.selected = true;
          select.appendChild(opt);
        });
        wrap.appendChild(label);
        wrap.appendChild(select);
        grid.appendChild(wrap);
      });
      el("mappingModalOverlay").hidden = false;
    };

    el("mappingModalClose").onclick = () => { el("mappingModalOverlay").hidden = true; };
    el("mappingModalSave").onclick = () => {
      const override = {};
      FIELDS.forEach(f => { override[f.key] = el("mmap_" + f.key).value; });
      saveMappingOverride(override);
      el("mappingModalOverlay").hidden = true;
      refreshDashboard();
    };
    el("mappingModalReset").onclick = () => {
      clearMappingOverride();
      el("mappingModalOverlay").hidden = true;
      refreshDashboard();
    };
  }

  /* ---- Sample data ---- */
  function generateSampleRows() {
    const products = [
      ["Áo thun basic", "Thời trang"], ["Quần jean", "Thời trang"], ["Giày sneaker", "Giày dép"],
      ["Túi xách", "Phụ kiện"], ["Đồng hồ", "Phụ kiện"], ["Nồi chiên không dầu", "Gia dụng"],
      ["Máy xay sinh tố", "Gia dụng"], ["Tai nghe bluetooth", "Điện tử"], ["Sạc dự phòng", "Điện tử"],
      ["Bàn phím cơ", "Điện tử"],
    ];
    const customers = ["Công ty ABC", "Cửa hàng Minh Anh", "Nguyễn Văn A", "Trần Thị B", "Đại lý Phú Quý", "Lê Văn C", "Shop Hồng Phát", "Phạm Thị D"];
    const rows = [];
    const start = new Date(2025, 1, 1);
    for (let i = 0; i < 400; i++) {
      const d = new Date(start.getTime() + Math.random() * (Date.now() - start.getTime()));
      const [product, category] = products[Math.floor(Math.random() * products.length)];
      const customer = customers[Math.floor(Math.random() * customers.length)];
      const quantity = 1 + Math.floor(Math.random() * 12);
      const price = 50000 + Math.floor(Math.random() * 950000);
      rows.push({
        "Ngày": d.toLocaleDateString("vi-VN"),
        "Sản phẩm": product,
        "Danh mục": category,
        "Khách hàng": customer,
        "Số lượng": quantity,
        "Đơn giá": price,
        "Doanh thu": quantity * price,
      });
    }
    return rows;
  }

  /* ================= Init ================= */
  document.addEventListener("DOMContentLoaded", () => {
    initTabs();
    Object.keys(STORE_META).forEach(setupDataManager);
    wireRowModal();
    wireMappingModal();

    el("btnSample").addEventListener("click", async () => {
      await DB.bulkPut("orders", generateSampleRows());
      await refreshDataManager("orders");
      refreshDashboard();
    });

    refreshDashboard();
  });
})();
