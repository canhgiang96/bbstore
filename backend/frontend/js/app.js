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

  // "Dữ liệu bán hàng" (Đơn hàng/Dòng tiền/Điều chỉnh doanh thu) and "Danh
  // mục" (Master File/Combo/Kênh bán hàng) each bundle several Report tabs
  // behind one top-level nav entry — a "Loại file" <select> switches which
  // one is visible inside. The individual panels (panel-orders,
  // panel-cashflow, ...) keep their own ids/wiring from createReportTab()
  // unchanged; this only toggles their `hidden` attribute, independent of
  // #mainTabs' own show/hide (nesting inside a hidden parent tab already
  // keeps them invisible — this only decides which one shows once the
  // parent tab itself is active).
  function wireFileTypeGroup(selectId, panelIdByValue) {
    const sel = el(selectId);
    function sync() {
      Object.entries(panelIdByValue).forEach(([value, panelId]) => {
        el(panelId).hidden = sel.value !== value;
      });
    }
    sel.addEventListener("change", sync);
    sync();
  }

  /* ================= Report tabs (API-backed) =================
     Đơn hàng/Dòng tiền/Combo/Master File/Điều chỉnh doanh thu all follow the
     same "dropzone upload -> process in background -> poll -> render list
     -> [channel PATCH] -> delete" pattern. createReportTab() below is the
     one implementation all 5 build on; each just supplies its endpoint,
     DOM ids, and the handful of things that actually differ between them
     (see the per-tab config blocks further down):
       - hasChannel: only Đơn hàng/Dòng tiền/Điều chỉnh doanh thu carry a
         "Kênh bán hàng" column — Combo/Master File don't.
       - afterChange: fired after a report finishes processing (poll sees
         status leave "processing") and after a delete — Đơn hàng/Dòng
         tiền/Combo/Master File all feed the Orders Dashboard, so this is
         refreshDashboard; Điều chỉnh doanh thu is a standalone
         record-keeping viewer (not joined into the Dashboard), so it's
         null there.
       - afterChannelChange: fired after a channel <select> is saved. Only
         Đơn hàng's channel is actually read by the Dashboard's query
         engine (see query_engine._channel_tagged_source_sql) — Dòng
         tiền/Điều chỉnh doanh thu's channel is pure organizational
         tagging on their own list, so only Đơn hàng passes
         refreshDashboard here.
     Điều chỉnh doanh thu also has its own read-only expandable row viewer,
     wired on via extraColumns/rowSuffix/afterListRendered/onBeforeDelete —
     see its section below. */
  function createReportTab({
    endpoint, dropzoneId, inputId, summaryId, listBodyId, listCountId,
    hasChannel = false, afterChange = null, afterChannelChange = null,
    extraColumns = [], rowSuffix = null, afterListRendered = null, onBeforeDelete = null,
    uploadChannelSelectId = null,
  }) {
    const pollTimers = {};

    function pollStatus(reportId) {
      if (pollTimers[reportId]) clearInterval(pollTimers[reportId]);
      pollTimers[reportId] = setInterval(async () => {
        try {
          const report = await API.apiJson(`${endpoint}/${reportId}`);
          if (report.status !== "processing") {
            clearInterval(pollTimers[reportId]);
            delete pollTimers[reportId];
            await refresh();
            if (report.status === "ready" && afterChange) afterChange(); // the aggregate now includes it
          }
        } catch (e) { /* transient — keep polling */ }
      }, 2500);
    }

    async function refresh() {
      const isAdmin = API.isAdmin();
      let reports;
      try {
        reports = await API.apiJson(endpoint);
      } catch (e) {
        el(listBodyId).innerHTML = `<p class="muted">Không tải được danh sách Report: ${escapeHtml(e.message)}</p>`;
        return;
      }
      el(listCountId).textContent = `${reports.length.toLocaleString("vi-VN")} Report`;

      const body = el(listBodyId);
      if (!reports.length) {
        body.innerHTML = `<p class="muted" style="padding:16px;">Chưa có Report nào.</p>`;
        return;
      }

      const channelCol = hasChannel ? [{
        label: "Kênh bán hàng",
        cell: (r, isAdmin) => isAdmin ? channelSelectHtml(r.id, r.sales_channel_id) : escapeHtml(channelName(r.sales_channel_id)),
      }] : [];
      const cols = channelCol.concat(extraColumns);
      const colspan = 4 + cols.length + (isAdmin ? 1 : 0);

      body.innerHTML = `<div class="table-scroll"><table><thead><tr>
          <th>Report</th><th>Trạng thái</th><th>Số dòng</th><th>Tải lên lúc</th>${cols.map(c => `<th>${escapeHtml(c.label)}</th>`).join("")}${isAdmin ? "<th>Thao tác</th>" : ""}
        </tr></thead><tbody>` + reports.map(r => {
          const rowHtml = `<tr>
            <td>${r.locked ? "🔒 " : ""}${escapeHtml(r.name)}</td>
            <td>${STATUS_BADGE[r.status] || escapeHtml(r.status)}${r.status === "failed" && r.error_message ? `<div class="muted" style="margin-top:4px;">${escapeHtml(r.error_message)}</div>` : ""}</td>
            <td>${r.row_count != null ? r.row_count.toLocaleString("vi-VN") : "–"}</td>
            <td>${new Date(r.uploaded_at).toLocaleString("vi-VN")}</td>
            ${cols.map(c => `<td>${c.cell(r, isAdmin)}</td>`).join("")}
            ${isAdmin ? `<td>
                <button class="btn btn-ghost btn-sm" data-lock="${escapeHtml(r.id)}" data-locked="${r.locked ? "1" : "0"}">${r.locked ? "Mở khóa" : "Khóa"}</button>
                <button class="btn btn-danger btn-sm" data-del="${escapeHtml(r.id)}"${r.locked ? ' disabled title="Report đã khóa — mở khóa trước khi xóa"' : ""}>Xóa</button>
              </td>` : ""}
          </tr>`;
          return rowHtml + (rowSuffix ? rowSuffix(r, isAdmin, colspan) : "");
        }).join("") + `</tbody></table></div>`;

      if (isAdmin) {
        body.querySelectorAll("button[data-del]").forEach(btn => {
          btn.onclick = async () => {
            const id = btn.dataset.del;
            const report = reports.find(r => r.id === id);
            if (!confirm(`Xóa toàn bộ Report "${report ? report.name : id}"? Hành động này không thể hoàn tác.`)) return;
            try {
              await API.apiJson(`${endpoint}/${id}`, { method: "DELETE" });
            } catch (err) {
              alert("Lỗi xóa Report: " + err.message);
              return;
            }
            if (onBeforeDelete) onBeforeDelete(id);
            await refresh();
            if (afterChange) afterChange();
          };
        });
        body.querySelectorAll("button[data-lock]").forEach(btn => {
          btn.onclick = async () => {
            const id = btn.dataset.lock;
            const nextLocked = btn.dataset.locked !== "1";
            try {
              await API.apiJson(`${endpoint}/${id}/lock`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ locked: nextLocked }),
              });
              await refresh();
            } catch (err) {
              alert("Lỗi khóa/mở khóa Report: " + err.message);
            }
          };
        });
        if (hasChannel) wireChannelSelects(body, endpoint, async () => { await refresh(); if (afterChannelChange) afterChannelChange(); });
      }

      if (afterListRendered) afterListRendered(body);

      // Any still-processing report needs a poller (e.g. after a page
      // reload mid-conversion) — pollStatus() is a no-op re-arm if already polling.
      reports.filter(r => r.status === "processing").forEach(r => pollStatus(r.id));
    }

    async function handleUpload(file) {
      const box = el(summaryId);
      box.className = "import-summary";
      box.textContent = `Đang tải lên "${file.name}"...`;
      try {
        const formData = new FormData();
        formData.append("file", file);
        if (uploadChannelSelectId) {
          const channelId = el(uploadChannelSelectId).value;
          if (channelId) formData.append("sales_channel_id", channelId);
        }
        const res = await API.apiFetch(endpoint, { method: "POST", body: formData });
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          throw new Error(body.detail || `Lỗi ${res.status}`);
        }
        const created = await res.json();
        box.className = "import-summary ok";
        box.textContent = `Đã tải lên — đang xử lý...`;
        await refresh();
        pollStatus(created.id);
      } catch (err) {
        box.className = "import-summary err";
        box.textContent = "Lỗi tải lên: " + err.message;
      }
    }

    function wire() {
      const dz = el(dropzoneId);
      const input = el(inputId);
      if (!API.isAdmin()) {
        dz.hidden = true;
      } else {
        ["dragenter", "dragover"].forEach(evt => dz.addEventListener(evt, e => { e.preventDefault(); dz.classList.add("dragover"); }));
        ["dragleave", "drop"].forEach(evt => dz.addEventListener(evt, e => { e.preventDefault(); dz.classList.remove("dragover"); }));
        dz.addEventListener("drop", e => { const f = e.dataTransfer.files[0]; if (f) handleUpload(f); });
        input.addEventListener("change", e => {
          const f = e.target.files[0];
          if (f) handleUpload(f);
          input.value = "";
        });
      }
      refresh();
    }

    return { wire, refresh };
  }

  const STATUS_BADGE = {
    processing: '<span class="pill warn">Đang xử lý</span>',
    ready: '<span class="pill good">Sẵn sàng</span>',
    failed: '<span class="pill bad">Lỗi</span>',
  };

  // "Kênh bán hàng" assignment — shared by the Đơn hàng, Dòng tiền và Điều
  // chỉnh doanh thu Report lists (see wireChannelSelects' call sites).
  // dash.salesChannels is fetched once at startup (see
  // refreshSalesChannelsCache) and kept in sync by the Kênh bán hàng tab's
  // own add/delete actions.
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

  // The Đơn hàng/Dòng tiền/Điều chỉnh doanh thu upload dropzones each have
  // their own "Kênh bán hàng" <select> so the channel is known BEFORE
  // conversion runs (Đơn hàng's Phí Piship is Shopee-only — see
  // derive.channel_has_piship on the backend — so it needs the channel at
  // upload time, not just after via the post-upload PATCH). Repopulated
  // whenever dash.salesChannels changes (see wireSalesChannelsTab).
  const UPLOAD_CHANNEL_SELECT_IDS = ["uploadChannelSelect", "cashflowUploadChannelSelect", "adjustmentsUploadChannelSelect"];

  function populateUploadChannelSelects() {
    const optionsHtml = ['<option value="">(Chưa gán)</option>']
      .concat(dash.salesChannels.map(c => `<option value="${escapeHtml(c.id)}">${escapeHtml(c.name)}</option>`))
      .join("");
    UPLOAD_CHANNEL_SELECT_IDS.forEach(id => {
      const sel = el(id);
      const prevValue = sel.value;
      sel.innerHTML = optionsHtml;
      if ([...sel.options].some(o => o.value === prevValue)) sel.value = prevValue;
    });
  }

  /* ---- Đơn hàng (Orders) — the only tab whose channel feeds the Dashboard's
     query engine, so it's the only one wiring afterChannelChange. ---- */
  const ordersTab = createReportTab({
    endpoint: "/api/reports",
    dropzoneId: "uploadDropzone", inputId: "uploadInput", summaryId: "uploadSummary",
    listBodyId: "reportsListBody", listCountId: "reportsListCount",
    hasChannel: true,
    afterChange: () => refreshDashboard(),
    afterChannelChange: () => refreshDashboard(),
    uploadChannelSelectId: "uploadChannelSelect",
  });

  /* ---- Dòng tiền (Cashflow) — supplies Phí AFF for the Dashboard's
     query-time join, so uploading/deleting one also refreshes it. ---- */
  const cashflowTab = createReportTab({
    endpoint: "/api/cashflow-reports",
    dropzoneId: "cashflowUploadDropzone", inputId: "cashflowUploadInput", summaryId: "cashflowUploadSummary",
    listBodyId: "cashflowReportsListBody", listCountId: "cashflowReportsListCount",
    hasChannel: true,
    afterChange: () => refreshDashboard(),
    uploadChannelSelectId: "cashflowUploadChannelSelect",
  });

  /* ---- Combo — explodes matching Orders skuVariant into sub-SKU
     components at query time, so uploading/deleting one also refreshes
     the Dashboard. No Kênh bán hàng column. ---- */
  const comboTab = createReportTab({
    endpoint: "/api/combo-reports",
    dropzoneId: "comboUploadDropzone", inputId: "comboUploadInput", summaryId: "comboUploadSummary",
    listBodyId: "comboReportsListBody", listCountId: "comboReportsListCount",
    afterChange: () => refreshDashboard(),
  });

  /* ---- Master File — supplies Phân loại kho/mục/sản phẩm and Giá vốn for
     the Dashboard's query-time join, so uploading/deleting one also
     refreshes it. No Kênh bán hàng column. ---- */
  const masterTab = createReportTab({
    endpoint: "/api/master-reports",
    dropzoneId: "masterUploadDropzone", inputId: "masterUploadInput", summaryId: "masterUploadSummary",
    listBodyId: "masterReportsListBody", listCountId: "masterReportsListCount",
    afterChange: () => refreshDashboard(),
  });

  /* ================= Điều chỉnh doanh thu (revenue adjustments) =================
     Unlike Combo/Cashflow/Master File, this data isn't joined into the
     Orders Dashboard's query engine (it's a standalone record-keeping
     viewer, same role the old IndexedDB manager played) — so instead of
     "uploading also refreshes the Dashboard" it gets its own read-only
     rows viewer: click "Xem dữ liệu" on a ready Report to expand its first
     50 rows inline. That expand/collapse feature is unique to this tab, so
     it's wired on top of createReportTab via extraColumns/rowSuffix/
     afterListRendered/onBeforeDelete rather than being part of the shared
     factory. ================= */
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

  async function toggleAdjustmentsExpand(reportId) {
    if (adjustmentsExpandedReportId === reportId) {
      adjustmentsExpandedReportId = null;
      adjustmentsExpandedRows = null;
      await adjustmentsTab.refresh();
      return;
    }
    adjustmentsExpandedReportId = reportId;
    adjustmentsExpandedRows = null;
    await adjustmentsTab.refresh();
    try {
      const result = await API.apiJson(`/api/adjustments-reports/${reportId}/rows?page=1&pageSize=50`);
      if (adjustmentsExpandedReportId !== reportId) return; // collapsed while the request was in flight
      adjustmentsExpandedRows = result;
      await adjustmentsTab.refresh();
    } catch (err) {
      adjustmentsExpandedReportId = null;
      adjustmentsExpandedRows = null;
      alert("Lỗi tải dữ liệu: " + err.message);
      await adjustmentsTab.refresh();
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

  const adjustmentsTab = createReportTab({
    endpoint: "/api/adjustments-reports",
    dropzoneId: "adjustmentsUploadDropzone", inputId: "adjustmentsUploadInput", summaryId: "adjustmentsUploadSummary",
    listBodyId: "adjustmentsReportsListBody", listCountId: "adjustmentsReportsListCount",
    hasChannel: true,
    uploadChannelSelectId: "adjustmentsUploadChannelSelect",
    extraColumns: [{
      label: "Dữ liệu",
      cell: r => r.status === "ready"
        ? `<button class="btn btn-ghost btn-sm adjustments-view-btn" data-report-id="${escapeHtml(r.id)}">${adjustmentsExpandedReportId === r.id ? "Ẩn" : "Xem"} dữ liệu</button>`
        : "",
    }],
    rowSuffix: (r, isAdmin, colspan) => adjustmentsExpandedReportId === r.id ? renderAdjustmentsExpandedRowHtml(colspan) : "",
    afterListRendered: body => {
      body.querySelectorAll(".adjustments-view-btn").forEach(btn => {
        btn.onclick = () => toggleAdjustmentsExpand(btn.dataset.reportId);
      });
    },
    onBeforeDelete: id => {
      if (adjustmentsExpandedReportId === id) { adjustmentsExpandedReportId = null; adjustmentsExpandedRows = null; }
    },
  });

  /* ---- Kênh AFF — supplies (orderId, skuId) pairs for the Dashboard's
     "Kênh nhỏ" query-time join (see query_engine._aff_channel_join), so
     uploading/deleting one also refreshes it. No Kênh bán hàng column —
     this Report type is inherently TikTok-only. ---- */
  const affChannelTab = createReportTab({
    endpoint: "/api/aff-channel-reports",
    dropzoneId: "affChannelUploadDropzone", inputId: "affChannelUploadInput", summaryId: "affChannelUploadSummary",
    listBodyId: "affChannelReportsListBody", listCountId: "affChannelReportsListCount",
    afterChange: () => refreshDashboard(),
  });

  /* ================= Named-list tabs (Kênh bán hàng / ID Inhouse) — a
     plain named list, not a file-upload Report, so these are much simpler
     than createReportTab's tabs above: no dropzone, no background
     processing, just add/delete. wireNamedListTab is the one shared
     implementation both wireSalesChannelsTab and wireInhouseHandlesTab
     build on (mirrors createReportTab's role for the file-upload tabs). ================= */
  function wireNamedListTab({
    endpoint, addCardId, addButtonId, inputId, summaryId, listBodyId, listCountId,
    emptyLabel, nounLabel, colLabel, deleteConfirmFn, afterChange,
  }) {
    const isAdmin = API.isAdmin();
    el(addCardId).hidden = !isAdmin;

    async function refresh() {
      let items;
      try {
        items = await API.apiJson(endpoint);
      } catch (e) {
        el(listBodyId).innerHTML = `<p class="muted">Không tải được danh sách: ${escapeHtml(e.message)}</p>`;
        return;
      }
      if (afterChange) afterChange(items);
      el(listCountId).textContent = `${items.length.toLocaleString("vi-VN")} ${nounLabel}`;

      const body = el(listBodyId);
      if (!items.length) {
        body.innerHTML = `<p class="muted" style="padding:16px;">${emptyLabel}</p>`;
        return;
      }

      body.innerHTML = `<div class="table-scroll"><table><thead><tr>
          <th>${escapeHtml(colLabel)}</th><th>Tạo lúc</th>${isAdmin ? "<th>Thao tác</th>" : ""}
        </tr></thead><tbody>` + items.map(c => `
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
            const item = items.find(c => c.id === id);
            if (!confirm(deleteConfirmFn(item ? item.name : id))) return;
            await API.apiJson(`${endpoint}/${id}`, { method: "DELETE" });
            await refresh();
          };
        });
      }
    }

    if (isAdmin) {
      el(addButtonId).onclick = async () => {
        const input = el(inputId);
        const name = input.value.trim();
        const box = el(summaryId);
        if (!name) return;
        try {
          await API.apiJson(endpoint, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name }),
          });
          input.value = "";
          box.className = "import-summary ok";
          box.textContent = `Đã thêm "${name}".`;
          await refresh();
        } catch (err) {
          box.className = "import-summary err";
          box.textContent = "Lỗi: " + err.message;
        }
      };
    }

    refresh();
    return { refresh };
  }

  // dash.salesChannels is fetched once at startup here (Đơn hàng/Dòng tiền/
  // Điều chỉnh lists render a channel <select> per row from this) and kept
  // in sync afterwards by wireSalesChannelsTab's own add/delete actions —
  // see UPLOAD_CHANNEL_SELECT_IDS/populateUploadChannelSelects above.
  async function refreshSalesChannelsCache() {
    try {
      dash.salesChannels = await API.apiJson("/api/sales-channels");
    } catch (e) {
      dash.salesChannels = [];
    }
    populateUploadChannelSelects();
  }

  function wireSalesChannelsTab() {
    wireNamedListTab({
      endpoint: "/api/sales-channels",
      addCardId: "channelAddCard", addButtonId: "btnAddChannel", inputId: "newChannelName",
      summaryId: "channelAddSummary", listBodyId: "channelsListBody", listCountId: "channelsListCount",
      emptyLabel: "Chưa có kênh bán hàng nào.", nounLabel: "kênh", colLabel: "Tên kênh",
      deleteConfirmFn: name => `Xóa kênh "${name}"? Các Report đang gán kênh này sẽ chuyển về "(Chưa gán)".`,
      afterChange: items => { dash.salesChannels = items; populateUploadChannelSelects(); },
    });
  }

  // "ID Inhouse" — the shop's own TikTok Creator Handles (bbstores.vn,
  // bbcongso, bbstores_forlady, ...), used server-side by the Dashboard's
  // "Kênh nhỏ" classification (see query_engine._aff_channel_join) to tell
  // the shop's own main-channel activity apart from an outside creator's.
  // No client-side cache needed — unlike Kênh bán hàng, nothing else on
  // this page reads the list (it's only ever sent to the backend).
  function wireInhouseHandlesTab() {
    wireNamedListTab({
      endpoint: "/api/inhouse-handles",
      addCardId: "inhouseAddCard", addButtonId: "btnAddInhouseHandle", inputId: "newInhouseHandle",
      summaryId: "inhouseAddSummary", listBodyId: "inhouseListBody", listCountId: "inhouseListCount",
      emptyLabel: "Chưa có ID Inhouse nào.", nounLabel: "ID", colLabel: "Tên người sáng tạo (Handle)",
      deleteConfirmFn: name => `Xóa ID Inhouse "${name}"?`,
    });
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
    selectedKenhNho: new Set(),
    salesChannels: [], // raw {id,name,...} list from /api/sales-channels — see refreshSalesChannelsCache
    lastFacets: null, // cached so "Xóa lọc" can redraw the checkbox lists without waiting on a fetch
    lastKpis: null, // cached so exportOverviewExcel() can reuse exactly what's on screen, no extra fetch
    filtersWired: false,
    summarySeq: 0,
    detailSeq: 0,
    monthlyAnalysisLoaded: false,
    monthlyAnalysisRows: new Map(), // month ("YYYY-MM") -> row, so an expense-cell save can read its sibling cell's current value
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
          ? `Vào tab <strong>Dữ liệu bán hàng</strong> → chọn Loại file <strong>Đơn hàng</strong> để tải lên file Excel.`
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
    // chi tiết" is instant — no re-fetch on tab switch. "Phân tích tháng"
    // is fetched lazily instead (see wireSubtabs) since it's a heavier,
    // rarely-visited, unfiltered whole-history query — just mark it stale
    // here so the next visit to that sub-tab picks up this upload/delete.
    dash.monthlyAnalysisLoaded = false;
    if (dash.subtab === "monthly") fetchAndRenderMonthlyAnalysis();
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
    dash.selectedKenhNho.forEach(v => params.append("kenhNho", v));
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
        el("dashboardMonthlyAnalysis").hidden = dash.subtab !== "monthly";
        // "Phân tích tháng" deliberately ignores every filter (whole-
        // business trend view, confirmed with the user 2026-08-28) — the
        // filter bar would be misleading to leave visible there.
        el("dashboardFilters").hidden = dash.subtab === "monthly";
        if (dash.subtab === "monthly" && !dash.monthlyAnalysisLoaded) fetchAndRenderMonthlyAnalysis();
      };
    });
  }

  /* ---- "Phân tích tháng" — a whole-history monthly P&L table (Doanh thu
     thuần/NMV/Lợi nhuận gộp from the query engine — the user's reference
     spreadsheet led with GMV, but explicitly asked for Doanh thu thuần
     instead here, 2026-08-28 — plus Chi phí bán hàng/Chi phí quản lý
     entered by hand per month — see routers/monthly_analysis.py). Never
     filtered by the Dashboard's own Thời gian/Trạng thái/etc pickers. ---- */
  function monthLabel(month) { // "2026-01" -> "1/2026", matching the user's reference spreadsheet
    const [y, m] = month.split("-");
    return `${Number(m)}/${y}`;
  }

  function parseVnNumberInput(s) {
    const cleaned = String(s ?? "").replace(/[^\d-]/g, "");
    const n = parseInt(cleaned, 10);
    return isNaN(n) ? 0 : n;
  }

  async function fetchAndRenderMonthlyAnalysis() {
    const body = el("monthlyAnalysisBody");
    body.innerHTML = `<tr><td colspan="13" class="muted" style="padding:16px;">Đang tải...</td></tr>`;
    let rows;
    try {
      rows = await API.apiJson("/api/monthly-analysis");
    } catch (err) {
      body.innerHTML = `<tr><td colspan="13" class="muted" style="padding:16px;">Không tải được: ${escapeHtml(err.message)}</td></tr>`;
      return;
    }
    dash.monthlyAnalysisLoaded = true;
    dash.monthlyAnalysisRows = new Map(rows.map(r => [r.month, r]));
    renderMonthlyAnalysisTable(rows);
  }

  function renderMonthlyAnalysisTable(rows) {
    const isAdmin = API.isAdmin();
    const body = el("monthlyAnalysisBody");
    if (!rows.length) {
      body.innerHTML = `<tr><td colspan="13" class="muted" style="padding:16px;">Chưa có dữ liệu.</td></tr>`;
      return;
    }

    const expenseCell = (r, field) => isAdmin
      ? `<input type="text" class="monthly-expense-input" data-month="${escapeHtml(r.month)}" data-field="${field}" value="${fmtNumber(r[field])}" />`
      : fmtNumber(r[field]);

    body.innerHTML = rows.map(r => `<tr>
        <td>${monthLabel(r.month)}</td>
        <td>${fmtNumber(r.doanhThuThuan)}</td>
        <td>${fmtPercentOfBase(r.nmv, r.doanhThuThuan)}</td>
        <td>${fmtNumber(r.nmv)}</td>
        <td>${fmtPercentOfBase(r.loiNhuanGop, r.nmv)}</td>
        <td>${fmtNumber(r.loiNhuanGop)}</td>
        <td>${fmtPercentOfBase(r.chiPhiBanHang, r.loiNhuanGop)}</td>
        <td>${expenseCell(r, "chiPhiBanHang")}</td>
        <td>${fmtPercentOfBase(r.chiPhiQuanLy, r.loiNhuanGop)}</td>
        <td>${expenseCell(r, "chiPhiQuanLy")}</td>
        <td>${fmtPercentOfBase(r.loiNhuan, r.nmv)}</td>
        <td>${fmtNumber(r.loiNhuan)}</td>
        <td>${fmtPercentOfBase(r.chiPhiBanHang + r.chiPhiQuanLy, r.loiNhuanGop)}</td>
      </tr>`).join("");

    if (!isAdmin) return;
    body.querySelectorAll(".monthly-expense-input").forEach(input => {
      const commit = async () => {
        const month = input.dataset.month;
        const row = dash.monthlyAnalysisRows.get(month);
        if (!row) return;
        const field = input.dataset.field;
        const newValue = parseVnNumberInput(input.value);
        if (row[field] === newValue) return; // unchanged — skip the request
        const chiPhiBanHang = field === "chiPhiBanHang" ? newValue : row.chiPhiBanHang;
        const chiPhiQuanLy = field === "chiPhiQuanLy" ? newValue : row.chiPhiQuanLy;
        try {
          await API.apiJson(`/api/monthly-analysis/${month}`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ chiPhiBanHang, chiPhiQuanLy }),
          });
          await fetchAndRenderMonthlyAnalysis();
        } catch (err) {
          alert("Lỗi lưu chi phí: " + err.message);
        }
      };
      input.addEventListener("blur", commit);
      input.addEventListener("keydown", e => { if (e.key === "Enter") input.blur(); });
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
    el("btnApplyFilter").onclick = () => withButtonLoading(el("btnApplyFilter"), applyFiltersAndRender);
    el("btnClearFilter").onclick = () => withButtonLoading(el("btnClearFilter"), async () => {
      setTimeFilter("", "", TIME_PRESET_LABELS[""], "");
      el("customFrom").value = "";
      el("customTo").value = "";
      el("timeCustomPanel").hidden = true;
      dash.selectedStatus.clear();
      dash.selectedWarehouseType.clear();
      dash.selectedItemGroup.clear();
      dash.selectedProductType.clear();
      dash.selectedSalesChannel.clear();
      dash.selectedKenhNho.clear();
      el("filterSku").value = "";
      if (dash.lastFacets) renderFacets(dash.lastFacets); // redraw checkboxes as unchecked
      await applyFiltersAndRender();
    });
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
    el("btnExportOverviewExcel").onclick = () => exportOverviewExcel();
    wireSubtabs();
    wireTimeFilter();
    ensureVisibleCols();
    renderColumnPicker();
    renderGroupByPicker();
  }

  async function applyFiltersAndRender() {
    dash.detailPage = 1;
    clearGroupState();
    await Promise.all([fetchAndRenderSummary(), fetchAndRenderDetailTable()]);
  }

  // Shows a spinner + disables the button for the duration of an async
  // action (e.g. "Tìm kiếm"/"Xóa lọc" while their filtered data loads) so
  // clicking it doesn't feel unresponsive on a slow query. Restores the
  // button's exact original content afterward, success or failure.
  async function withButtonLoading(btn, fn) {
    const originalHtml = btn.innerHTML;
    const originalDisabled = btn.disabled;
    btn.disabled = true;
    btn.innerHTML = `<span class="btn-spinner"></span>${escapeHtml(btn.textContent.trim())}`;
    try {
      await fn();
    } finally {
      btn.innerHTML = originalHtml;
      btn.disabled = originalDisabled;
    }
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
    const kenhNhoValues = [...(facets.kenhNho || [])].sort((a, b) => a.localeCompare(b, "vi"));

    renderMultiSelectFacet("filterStatusList", "filterStatusSummary", dash.selectedStatus, statuses);
    renderMultiSelectFacet("filterWarehouseTypeList", "filterWarehouseTypeSummary", dash.selectedWarehouseType, warehouseTypes);
    renderMultiSelectFacet("filterItemGroupList", "filterItemGroupSummary", dash.selectedItemGroup, itemGroups);
    renderMultiSelectFacet("filterProductTypeList", "filterProductTypeSummary", dash.selectedProductType, productTypes);
    renderMultiSelectFacet("filterSalesChannelList", "filterSalesChannelSummary", dash.selectedSalesChannel, salesChannels);
    renderMultiSelectFacet("filterKenhNhoList", "filterKenhNhoSummary", dash.selectedKenhNho, kenhNhoValues);
  }

  /* ---- KPIs ---- */
  // Percentage of a card's "base" value — e.g. Phí sàn/Phí Piship/Phí AFF
  // each show their share of Doanh thu thuần, and GMV shows its share of
  // Doanh số (the previous row's base) — see KPI_CARDS' base fields below.
  function fmtPercentOfBase(value, base) {
    if (!base) return "";
    return (value / base * 100).toLocaleString("vi-VN", { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + "%";
  }

  // One entry per KPI card, in the exact order they appear on the
  // Overview tab (row by row) — the single source of truth for both
  // renderKPIs (fills the on-screen cards) and exportOverviewExcel
  // (writes the same numbers to a spreadsheet), so the two can't drift.
  // `base` is omitted for a row's own first card except where it's also
  // shown as a % of the PREVIOUS row's base (GMV/Doanh thu thuần/NMV/Lợi
  // nhuận gộp — the "funnel" ratios); "Doanh số" itself has no base.
  // `orders`/`ordersId` (only on the 9 cards that asked for it) show the
  // COUNT(DISTINCT orderId) behind that number — see run_summary_query's
  // *_orders columns, each scoped to the same row set as that card's own
  // value so "how many orders" always matches what's actually summed.
  const KPI_CARDS = [
    { label: "Doanh số", valueId: "kpiDoanhSo", value: k => k.doanhSo, ordersId: "kpiDoanhSoOrders", orders: k => k.doanhSoOrders },
    { label: "Doanh số hủy chưa XK", valueId: "kpiHuyChuaXK", pctId: "kpiHuyChuaXKPct", value: k => k.huyChuaXK, base: k => k.doanhSo, note: "Hủy trước khi xuất kho", ordersId: "kpiHuyChuaXKOrders", orders: k => k.huyChuaXKOrders },
    { label: "Doanh số hủy sau XK", valueId: "kpiHuySauXK", pctId: "kpiHuySauXKPct", value: k => k.huySauXK, base: k => k.doanhSo, note: "Hủy do giao hàng thất bại", ordersId: "kpiHuySauXKOrders", orders: k => k.huySauXKOrders },
    { label: "Doanh số hoàn", valueId: "kpiHoan", pctId: "kpiHoanPct", value: k => k.hoan, base: k => k.doanhSo, note: "Giá gốc x SL hoàn trả", ordersId: "kpiHoanOrders", orders: k => k.hoanOrders },
    { label: "GMV", valueId: "kpiDoanhSoThuan", pctId: "kpiDoanhSoThuanPct", value: k => k.gmv, base: k => k.doanhSo, note: "Hoàn thành + Đang giao + Hoàn 1 phần", ordersId: "kpiDoanhSoThuanOrders", orders: k => k.gmvOrders },
    { label: "Giảm giá", valueId: "kpiDiscount", pctId: "kpiDiscountPct", value: k => k.discount, base: k => k.gmv, note: "Người bán trợ giá" },
    { label: "Voucher", valueId: "kpiVoucher", pctId: "kpiVoucherPct", value: k => k.voucher, base: k => k.gmv, note: "Mã giảm giá của Shop" },
    { label: "Doanh thu thuần", valueId: "kpiDoanhThuThuan", pctId: "kpiDoanhThuThuanPct", value: k => k.doanhThuThuan, base: k => k.gmv, note: "GMV − Giảm giá − Voucher", ordersId: "kpiDoanhThuThuanOrders", orders: k => k.doanhThuThuanOrders },
    { label: "Phí sàn", valueId: "kpiPlatformFee", pctId: "kpiPlatformFeePct", value: k => k.platformFee, base: k => k.doanhThuThuan, note: "Phí cố định + Phí dịch vụ + Phí xử lý giao dịch" },
    { label: "Phí Piship", valueId: "kpiPiship", pctId: "kpiPishipPct", value: k => k.piship, base: k => k.doanhThuThuan, note: "1.620 / đơn hàng", ordersId: "kpiPishipOrders", orders: k => k.pishipOrders },
    { label: "Phí AFF", valueId: "kpiPhiAff", pctId: "kpiPhiAffPct", value: k => k.phiAff, base: k => k.doanhThuThuan, note: "Phí hoa hồng Tiếp thị liên kết", ordersId: "kpiPhiAffOrders", orders: k => k.phiAffOrders },
    { label: "NMV", valueId: "kpiNmv", pctId: "kpiNmvPct", value: k => k.nmv, base: k => k.doanhThuThuan, note: "Doanh thu thuần − Phí sàn − Phí Piship − Phí AFF", ordersId: "kpiNmvOrders", orders: k => k.nmvOrders },
    { label: "Giá vốn", valueId: "kpiGiaVon", pctId: "kpiGiaVonPct", value: k => k.giaVon, base: k => k.doanhThuThuan, note: "Số lượng thực x Giá vốn (Master File)" },
    { label: "Lợi nhuận gộp", valueId: "kpiLoiNhuanGop", pctId: "kpiLoiNhuanGopPct", value: k => k.loiNhuanGop, base: k => k.doanhThuThuan, note: "NMV − Giá vốn" },
  ];

  function fmtOrders(n) {
    return `${(n || 0).toLocaleString("vi-VN")} đơn hàng`;
  }

  function renderKPIs(kpis) {
    dash.lastKpis = kpis;
    KPI_CARDS.forEach(card => {
      const value = card.value(kpis);
      el(card.valueId).textContent = fmtNumber(value);
      if (card.pctId) el(card.pctId).textContent = fmtPercentOfBase(value, card.base(kpis));
      if (card.ordersId) el(card.ordersId).textContent = fmtOrders(card.orders(kpis));
    });
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
    { key: "kenhNho", label: "Kênh nhỏ" },
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
    kenhNho: "Kênh nhỏ",
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

  // Overview tab export — unlike exportExcel() (the Detail-table's row
  // dump, which needs a fresh server-side DuckDB scan), everything here
  // is already in the browser: dash.lastKpis (see renderKPIs) and the
  // current filter selections. Built client-side with the SheetJS
  // library already loaded in index.html, no backend round-trip needed.
  async function exportOverviewExcel() {
    const btn = el("btnExportOverviewExcel");
    const originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Đang xuất...";
    try {
      const kpis = dash.lastKpis;
      if (!kpis) { alert("Chưa có dữ liệu để xuất, vui lòng thử lại sau khi Dashboard tải xong."); return; }

      const setLabel = s => (s.size ? [...s].join(", ") : "Tất cả");
      const filterRows = [
        ["Tiêu chí", "Giá trị"],
        ["Khoảng thời gian", dash.timeLabel],
        ["Trạng thái", setLabel(dash.selectedStatus)],
        ["Phân loại kho", setLabel(dash.selectedWarehouseType)],
        ["Phân loại mục", setLabel(dash.selectedItemGroup)],
        ["Phân loại sản phẩm", setLabel(dash.selectedProductType)],
        ["Kênh bán hàng", setLabel(dash.selectedSalesChannel)],
        ["Kênh nhỏ", setLabel(dash.selectedKenhNho)],
        ["SKU", el("filterSku").value.trim() || "Tất cả"],
      ];

      const kpiRows = KPI_CARDS.map(card => {
        const value = card.value(kpis);
        const pct = card.pctId ? fmtPercentOfBase(value, card.base(kpis)) : "";
        const orders = card.ordersId ? card.orders(kpis) : "";
        const note = card.valueId === "kpiDoanhSo"
          ? `${kpis.rowCount.toLocaleString("vi-VN")} dòng dữ liệu`
          : (card.note || "");
        return [card.label, value, pct, orders, note];
      });

      const sheetRows = [
        ["BỘ LỌC ĐANG ÁP DỤNG"],
        ...filterRows,
        [],
        ["CHỈ SỐ TỔNG QUAN"],
        ["Chỉ số", "Giá trị (VNĐ)", "Tỉ lệ", "Số đơn hàng", "Ghi chú"],
        ...kpiRows,
      ];

      const ws = XLSX.utils.aoa_to_sheet(sheetRows);
      ws["!cols"] = [{ wch: 26 }, { wch: 18 }, { wch: 10 }, { wch: 14 }, { wch: 45 }];
      const wb = XLSX.utils.book_new();
      XLSX.utils.book_append_sheet(wb, ws, "Tổng quan");
      XLSX.writeFile(wb, `tong-quan-${new Date().toISOString().slice(0, 10)}.xlsx`);
    } catch (err) {
      alert("Lỗi xuất Excel: " + err.message);
    } finally {
      btn.disabled = false;
      btn.textContent = originalText;
    }
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
    wireFileTypeGroup("salesDataTypeSelect", {
      orders: "panel-orders", cashflow: "panel-cashflow", adjustments: "panel-adjustments",
      affchannel: "panel-affchannel",
    });
    wireFileTypeGroup("catalogTypeSelect", {
      master: "panel-master", combo: "panel-combo", channels: "panel-channels", inhouse: "panel-inhouse",
    });
    await refreshSalesChannelsCache(); // Đơn hàng/Dòng tiền/Điều chỉnh lists render a channel <select> per row from this
    ordersTab.wire();
    cashflowTab.wire();
    comboTab.wire();
    masterTab.wire();
    adjustmentsTab.wire();
    affChannelTab.wire();
    wireSalesChannelsTab();
    wireInhouseHandlesTab();
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
