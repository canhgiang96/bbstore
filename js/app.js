(() => {
  "use strict";

  /* ---------------- State ---------------- */
  const state = {
    workbook: null,
    sheetName: null,
    headers: [],        // raw header strings from the selected sheet
    rawRows: [],         // array of row objects keyed by header
    mapping: {},          // field -> header name (or "" for none)
    records: [],          // normalized records after mapping
    filtered: [],         // records after filters applied
    page: 1,
    pageSize: 15,
    search: "",
    charts: {},
  };

  const FIELDS = [
    { key: "date", label: "Ngày", required: true },
    { key: "product", label: "Sản phẩm", required: false },
    { key: "category", label: "Danh mục", required: false },
    { key: "customer", label: "Khách hàng", required: false },
    { key: "quantity", label: "Số lượng", required: false },
    { key: "price", label: "Đơn giá", required: false },
    { key: "revenue", label: "Doanh thu", required: false },
    { key: "status", label: "Trạng thái đơn hàng", required: false },
  ];

  // Longer/more specific phrases first so scoring naturally favors them.
  const KEYWORDS = {
    date: ["ngay dat hang", "ngay ban", "ngay giao dich", "order date", "ngay", "date", "thoi gian"],
    product: ["ten san pham", "ten mat hang", "ten hang", "san pham", "mat hang", "product", "item"],
    category: ["ten phan loai hang", "danh muc san pham", "danh muc", "phan loai hang", "phan loai", "loai", "nhom", "category"],
    customer: ["ten khach hang", "khach hang", "khach", "customer"],
    quantity: ["so luong san pham", "so luong", "qty", "quantity", "sl"],
    price: ["gia uu dai", "don gia", "gia ban", "gia goc", "gia", "price", "unit price"],
    revenue: ["tong gia tri don hang", "tong so tien thanh toan", "doanh thu", "thanh tien", "tong tien", "gia tri don hang", "thanh toan", "revenue", "total", "amount", "gia tri"],
    status: ["trang thai don hang", "trang thai", "status"],
  };

  // Columns that are identifiers/codes (SKU, mã...) should not win name-like fields
  // even if their header text happens to contain a matching keyword substring.
  const IDENTIFIER_PREFIX = /^(sku|ma|id)\b/;
  const NAME_LIKE_FIELDS = new Set(["product", "category", "customer"]);

  /* ---------------- Utils ---------------- */
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

  // For every field, pick the header with the strongest match (exact match beats
  // substring, longer keyword beats shorter, identifier-style columns are penalized
  // for name-like fields). Returns { field: headerName }.
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

          if (score > bestScore) {
            bestScore = score;
            bestHeader = h;
          }
        }
      }
      if (bestHeader) result[field] = bestHeader;
    }
    return result;
  }

  function fmtNumber(n) {
    if (!isFinite(n)) return "–";
    return Math.round(n).toLocaleString("vi-VN");
  }

  function parseDateValue(v) {
    if (v instanceof Date && !isNaN(v)) return v;
    if (typeof v === "number") {
      // Excel serial date fallback
      const d = XLSX.SSF.parse_date_code(v);
      if (d) return new Date(d.y, d.m - 1, d.d);
    }
    if (typeof v === "string") {
      const s = v.trim();
      // Strip a trailing time part like "2026-02-01 00:01" or "01/02/2026 00:01:00"
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
  function show(id) { el(id).hidden = false; }
  function hide(id) { el(id).hidden = true; }

  /* ---------------- File loading ---------------- */
  const dropZone = el("dropZone");
  const fileInput = el("fileInput");

  ["dragenter", "dragover"].forEach(evt =>
    dropZone.addEventListener(evt, e => { e.preventDefault(); dropZone.classList.add("dragover"); })
  );
  ["dragleave", "drop"].forEach(evt =>
    dropZone.addEventListener(evt, e => { e.preventDefault(); dropZone.classList.remove("dragover"); })
  );
  dropZone.addEventListener("drop", e => {
    const file = e.dataTransfer.files[0];
    if (file) handleFile(file);
  });
  fileInput.addEventListener("change", e => {
    const file = e.target.files[0];
    if (file) handleFile(file);
  });

  el("btnReset").addEventListener("click", () => location.reload());

  el("btnSample").addEventListener("click", () => {
    const wb = XLSX.utils.book_new();
    const ws = XLSX.utils.json_to_sheet(generateSampleRows());
    XLSX.utils.book_append_sheet(wb, ws, "Sample");
    loadWorkbook(wb);
  });

  function handleFile(file) {
    const reader = new FileReader();
    reader.onload = e => {
      const data = new Uint8Array(e.target.result);
      const wb = XLSX.read(data, { type: "array", cellDates: true });
      loadWorkbook(wb);
    };
    reader.readAsArrayBuffer(file);
  }

  function loadWorkbook(wb) {
    state.workbook = wb;
    const sheetSelect = el("sheetSelect");
    sheetSelect.innerHTML = "";
    wb.SheetNames.forEach(name => {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      sheetSelect.appendChild(opt);
    });
    sheetSelect.onchange = () => loadSheet(sheetSelect.value);
    loadSheet(wb.SheetNames[0]);

    hide("uploadScreen");
    show("mappingScreen");
    show("btnReset");
  }

  function loadSheet(name) {
    state.sheetName = name;
    const ws = state.workbook.Sheets[name];
    const rows = XLSX.utils.sheet_to_json(ws, { defval: "" });
    state.rawRows = rows;
    state.headers = rows.length ? Object.keys(rows[0]) : [];
    buildMappingUI();
    el("rowCount").textContent = `${rows.length.toLocaleString("vi-VN")} dòng dữ liệu`;
  }

  /* ---------------- Mapping UI ---------------- */
  function buildMappingUI() {
    const grid = el("mappingGrid");
    grid.innerHTML = "";
    const auto = detectMapping(state.headers);

    FIELDS.forEach(f => {
      const wrap = document.createElement("div");
      wrap.className = "mapping-field";
      const label = document.createElement("label");
      label.textContent = f.label;
      if (f.required) label.classList.add("req");
      const select = document.createElement("select");
      select.id = "map_" + f.key;

      const noneOpt = document.createElement("option");
      noneOpt.value = "";
      noneOpt.textContent = "-- Không có --";
      select.appendChild(noneOpt);

      state.headers.forEach(h => {
        const opt = document.createElement("option");
        opt.value = h;
        opt.textContent = h;
        if (auto[f.key] === h) opt.selected = true;
        select.appendChild(opt);
      });

      wrap.appendChild(label);
      wrap.appendChild(select);
      grid.appendChild(wrap);
    });
  }

  el("btnBuildDashboard").addEventListener("click", () => {
    const mapping = {};
    FIELDS.forEach(f => { mapping[f.key] = el("map_" + f.key).value; });

    if (!mapping.date) {
      alert("Vui lòng chọn cột Ngày để tạo dashboard.");
      return;
    }
    if (!mapping.revenue && !(mapping.price && mapping.quantity)) {
      alert("Vui lòng chọn cột Doanh thu, hoặc cả Đơn giá và Số lượng để hệ thống tự tính doanh thu.");
      return;
    }

    state.mapping = mapping;
    buildRecords();
    hide("mappingScreen");
    show("dashboardScreen");
    initFilters();
    applyFiltersAndRender();
  });

  function buildRecords() {
    const m = state.mapping;
    const records = [];
    for (const row of state.rawRows) {
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
      });
    }
    records.sort((a, b) => a.date - b.date);
    state.records = records;
  }

  const CANCELLED_STATUS_WORDS = ["huy", "hoan tien", "hoan tra", "tra hang", "refund", "cancel"];
  function isCancelledStatus(status) {
    if (!status) return false;
    const n = stripDiacritics(status);
    return CANCELLED_STATUS_WORDS.some(w => n.includes(w));
  }

  /* ---------------- Filters ---------------- */
  function initFilters() {
    const cats = Array.from(new Set(state.records.map(r => r.category))).sort();
    const sel = el("filterCategory");
    sel.innerHTML = '<option value="">Tất cả</option>';
    cats.forEach(c => {
      const opt = document.createElement("option");
      opt.value = c; opt.textContent = c;
      sel.appendChild(opt);
    });

    if (state.records.length) {
      const minD = state.records[0].date;
      const maxD = state.records[state.records.length - 1].date;
      el("filterFrom").value = toInputDate(minD);
      el("filterTo").value = toInputDate(maxD);
    }

    const hasStatus = !!state.mapping.status;
    el("filterCancelledWrap").hidden = !hasStatus;
    el("filterExcludeCancelled").checked = true;

    el("filterFrom").onchange = applyFiltersAndRender;
    el("filterTo").onchange = applyFiltersAndRender;
    el("filterCategory").onchange = applyFiltersAndRender;
    el("filterExcludeCancelled").onchange = applyFiltersAndRender;
    el("btnClearFilter").onclick = () => {
      el("filterFrom").value = state.records.length ? toInputDate(state.records[0].date) : "";
      el("filterTo").value = state.records.length ? toInputDate(state.records[state.records.length - 1].date) : "";
      el("filterCategory").value = "";
      applyFiltersAndRender();
    };
    el("tableSearch").oninput = e => { state.search = e.target.value; state.page = 1; renderTable(); };
    el("btnPrev").onclick = () => { if (state.page > 1) { state.page--; renderTable(); } };
    el("btnNext").onclick = () => {
      const maxPage = Math.max(1, Math.ceil(currentTableRows().length / state.pageSize));
      if (state.page < maxPage) { state.page++; renderTable(); }
    };
  }

  function toInputDate(d) {
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  }

  // <input type="date"> values are plain "YYYY-MM-DD" strings; `new Date(str)` would
  // parse that as UTC midnight, which drifts against the local-time dates records use.
  function parseInputDate(value) {
    if (!value) return null;
    const [y, m, d] = value.split("-").map(Number);
    return new Date(y, m - 1, d);
  }

  function applyFiltersAndRender() {
    const from = parseInputDate(el("filterFrom").value);
    const to = parseInputDate(el("filterTo").value);
    const cat = el("filterCategory").value;
    const excludeCancelled = !!state.mapping.status && el("filterExcludeCancelled").checked;

    let excludedCount = 0;
    state.filtered = state.records.filter(r => {
      if (from && r.date < from) return false;
      if (to && r.date > new Date(to.getFullYear(), to.getMonth(), to.getDate(), 23, 59, 59)) return false;
      if (cat && r.category !== cat) return false;
      if (excludeCancelled && isCancelledStatus(r.status)) { excludedCount++; return false; }
      return true;
    });
    state.excludedCancelledCount = excludedCount;

    state.page = 1;
    renderKPIs();
    renderCharts();
    renderTable();
  }

  /* ---------------- KPIs ---------------- */
  function renderKPIs() {
    const data = state.filtered;
    const totalRevenue = data.reduce((s, r) => s + r.revenue, 0);
    const totalQty = data.reduce((s, r) => s + r.quantity, 0);
    const orders = data.length;
    const avg = orders ? totalRevenue / orders : 0;

    el("kpiRevenue").textContent = fmtNumber(totalRevenue);
    el("kpiOrders").textContent = orders.toLocaleString("vi-VN");
    el("kpiQty").textContent = fmtNumber(totalQty);
    el("kpiAvg").textContent = fmtNumber(avg);

    const excluded = state.excludedCancelledCount || 0;
    el("kpiRevenueSub").textContent = excluded
      ? `${data.length.toLocaleString("vi-VN")} dòng · đã loại ${excluded.toLocaleString("vi-VN")} đơn hủy/hoàn trả`
      : `${data.length.toLocaleString("vi-VN")} dòng dữ liệu`;
    el("kpiOrdersSub").textContent = "trong khoảng đã lọc";
    el("kpiQtySub").textContent = "tổng số lượng bán";
    el("kpiAvgSub").textContent = "doanh thu / dòng";
  }

  /* ---------------- Charts ---------------- */
  const PALETTE = ["#3a5cf0", "#17a673", "#f0a53a", "#e34b4b", "#8a5cf0", "#3ac7f0", "#f06ab0", "#7cb342"];

  function destroyChart(key) {
    if (state.charts[key]) { state.charts[key].destroy(); delete state.charts[key]; }
  }

  function renderCharts() {
    renderTimelineChart();
    renderTopProductsChart();

    el("cardCategory").hidden = !state.mapping.category;
    if (state.mapping.category) renderCategoryChart();

    el("cardTopCustomers").hidden = !state.mapping.customer;
    if (state.mapping.customer) renderTopCustomersChart();
  }

  function renderTimelineChart() {
    const map = new Map();
    state.filtered.forEach(r => {
      const k = monthKey(r.date);
      map.set(k, (map.get(k) || 0) + r.revenue);
    });
    const labels = Array.from(map.keys()).sort();
    const values = labels.map(k => map.get(k));

    destroyChart("timeline");
    state.charts.timeline = new Chart(el("chartTimeline"), {
      type: "line",
      data: {
        labels,
        datasets: [{
          label: "Doanh thu",
          data: values,
          borderColor: PALETTE[0],
          backgroundColor: "rgba(58,92,240,0.12)",
          fill: true,
          tension: 0.3,
          pointRadius: 3,
        }],
      },
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
    const top = topN(state.filtered, r => r.product, 8);
    destroyChart("topProducts");
    state.charts.topProducts = new Chart(el("chartTopProducts"), {
      type: "bar",
      data: {
        labels: top.map(t => t[0]),
        datasets: [{ label: "Doanh thu", data: top.map(t => t[1]), backgroundColor: PALETTE[0] }],
      },
      options: baseOptions({ y: { ticks: { callback: v => fmtNumber(v) } } }, true),
    });
  }

  function renderCategoryChart() {
    const top = topN(state.filtered, r => r.category, 8);
    destroyChart("category");
    state.charts.category = new Chart(el("chartCategory"), {
      type: "doughnut",
      data: {
        labels: top.map(t => t[0]),
        datasets: [{ data: top.map(t => t[1]), backgroundColor: PALETTE }],
      },
      options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: "right", labels: { boxWidth: 12, font: { size: 11 } } } } },
    });
  }

  function renderTopCustomersChart() {
    const top = topN(state.filtered, r => r.customer, 8);
    destroyChart("topCustomers");
    state.charts.topCustomers = new Chart(el("chartTopCustomers"), {
      type: "bar",
      data: {
        labels: top.map(t => t[0]),
        datasets: [{ label: "Doanh thu", data: top.map(t => t[1]), backgroundColor: PALETTE[1] }],
      },
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

  /* ---------------- Table ---------------- */
  const TABLE_COLS = [
    { key: "date", label: "Ngày", fmt: d => d.toLocaleDateString("vi-VN") },
    { key: "product", label: "Sản phẩm" },
    { key: "category", label: "Danh mục" },
    { key: "customer", label: "Khách hàng" },
    { key: "quantity", label: "Số lượng", fmt: v => v.toLocaleString("vi-VN") },
    { key: "price", label: "Đơn giá", fmt: v => fmtNumber(v) },
    { key: "revenue", label: "Doanh thu", fmt: v => fmtNumber(v) },
  ];

  function currentTableRows() {
    if (!state.search) return state.filtered;
    const q = stripDiacritics(state.search);
    return state.filtered.filter(r =>
      [r.product, r.category, r.customer].some(v => stripDiacritics(v).includes(q))
    );
  }

  function renderTable() {
    const thead = document.querySelector("#dataTable thead");
    const tbody = document.querySelector("#dataTable tbody");
    thead.innerHTML = "<tr>" + TABLE_COLS.map(c => `<th>${c.label}</th>`).join("") + "</tr>";

    const rows = currentTableRows();
    const start = (state.page - 1) * state.pageSize;
    const pageRows = rows.slice(start, start + state.pageSize);

    tbody.innerHTML = pageRows.map(r =>
      "<tr>" + TABLE_COLS.map(c => {
        const v = r[c.key];
        return `<td>${c.fmt ? c.fmt(v) : v}</td>`;
      }).join("") + "</tr>"
    ).join("") || `<tr><td colspan="${TABLE_COLS.length}" class="muted" style="padding:20px;">Không có dữ liệu</td></tr>`;

    const maxPage = Math.max(1, Math.ceil(rows.length / state.pageSize));
    el("pageInfo").textContent = `Trang ${state.page} / ${maxPage} (${rows.length.toLocaleString("vi-VN")} dòng)`;
  }

  /* ---------------- Sample data ---------------- */
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
})();
