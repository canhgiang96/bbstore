# BBStore Dashboard — Nhật ký thay đổi

Tóm tắt các tính năng và bản sửa lỗi đã triển khai, theo thứ tự thời gian.
Dùng file này để tra cứu nhanh "cái gì đã làm, ở đâu" khi mở lại project
trên VSCode. Chi tiết kiến trúc/schema đầy đủ xem
`/Users/canhgiang/.claude/plans/temporal-rolling-crystal.md`.

Kiến trúc nền tảng: FastAPI + Supabase (Postgres qua PostgREST) + Cloudflare
R2 (lưu file gốc + Parquet) + DuckDB (query engine) + frontend JS thuần.
Mô hình "1 file Excel = 1 Report" — mỗi lần upload tạo ra một Report độc
lập, dữ liệu được join với nhau tại **thời điểm truy vấn** (query-time),
không gộp sẵn lúc upload.

---

## 1. Bố cục KPI & công thức Giá vốn

- Nhóm lại các thẻ KPI: **Doanh thu thuần → Phí sàn → Phí Piship → Phí AFF**
  chung 1 dòng; **NMV → Giá vốn** chung 1 dòng.
- **Giá vốn** chỉ tính trên đơn hàng trạng thái Hoàn thành/Đang giao/Hoàn 1
  phần — không tính trên đơn hủy.
- File chính: `app/query_engine.py` (`GMV_STATUSES_SQL`, các `*_row_expr`
  trong `_build_orders_working`).

## 2. Tab "Dữ liệu chi tiết" (Detail-table)

- Tab con trong Dashboard: bảng dữ liệu chi tiết với group-by, filter, sort,
  chọn cột hiển thị, xuất Excel — tương tự cách làm của Combo/Dòng tiền.
- **Group theo nhiều tầng (phân cấp/cây mở rộng)**: chọn nhiều tiêu chí
  group-by theo thứ tự, mỗi tầng là một cây con có thể mở rộng, tầng cuối
  cùng hiển thị dữ liệu chi tiết. Cột mới: **GMV, NMV, Lợi nhuận gộp** (tính
  theo đúng công thức KPI, để SUM theo group luôn khớp với thẻ KPI).
- File chính: `app/query_engine.py` (`run_grouped_rows_query`,
  `_grouped_agg_sql`, `GROUP_BY_COLUMNS`), `frontend/js/app.js`
  (`dash.expandedGroups`, `dash.pathFiltersByKey`, `renderGroupByPicker`).
- Bug đã sửa: `initTabs()` bắt nhầm nút của tab con (`data-subtab`) do
  selector không giới hạn phạm vi — làm sập toàn bộ menu tab chính. Đã sửa
  bằng cách giới hạn `querySelectorAll` trong `#mainTabs`.

## 3. Bộ lọc multi-select + loại trừ Piship

- Các bộ lọc Trạng thái/Phân loại kho/Phân loại mục/Phân loại sản phẩm/Kênh
  bán hàng chuyển từ chọn 1 giá trị sang **chọn nhiều giá trị** (multi-select
  checkbox, gửi API dạng `?status=A&status=B`).
- **Phí Piship không tính trên đơn "Hủy chưa XK"** (trước đây tính trên mọi
  đơn trừ dòng phụ trong 1 đơn).
- File chính: `app/query_engine.py` (`_in_clause`, `_where_clause`,
  `piship_row_expr`).

## 4. Tối ưu Dashboard cho điện thoại/iPad

- Sửa layout topbar/filter bar bị vỡ trên màn hình nhỏ — responsive cho
  mobile và tablet.
- File chính: `frontend/css/style.css`, `frontend/index.html`.

## 5. Bộ lọc thời gian dạng preset (kiểu Shopee/Lazada)

- Thay 2 ô "Từ ngày"/"Đến ngày" bằng 1 popover thời gian với các lựa chọn
  nhanh: Hôm nay, Hôm qua, 7/30 ngày trước, Tháng này/trước, Quý này/trước,
  Năm nay/trước, và **Tùy chọn** (chọn theo ngày/tháng/quý/năm cụ thể).
- File chính: `frontend/js/app.js` (`computePresetRange`,
  `computeMonthRange/QuarterRange/YearRange`, `wireTimeFilter`),
  `frontend/index.html` (`#timeFilterPicker`).

## 6. Kênh bán hàng (Sales Channels)

- Tab quản lý riêng "Kênh bán hàng" (CRUD: thêm/sửa/xóa tên kênh — Shopee,
  Lazada, TikTok Shop...).
- File Đơn hàng và Dòng tiền: gán Kênh bán hàng cho từng Report sau khi
  upload (`PATCH /api/reports/{id}/channel`,
  `PATCH /api/cashflow-reports/{id}/channel`).
- Dashboard: thêm cột **Kênh bán hàng** vào bảng chi tiết, thêm vào group-by,
  thêm bộ lọc multi-select theo kênh.
- Kỹ thuật: DuckDB `UNION ALL BY NAME` gắn nhãn kênh cho từng Report ngay
  tại thời điểm truy vấn (không sửa dữ liệu gốc trong Parquet).
- File chính: `app/routers/sales_channels.py` (mới),
  `app/query_engine.py` (`_channel_tagged_source_sql`).

## 7. Điều chỉnh doanh thu → chuyển lên server

- Trước đây "Điều chỉnh doanh thu" chỉ lưu ở trình duyệt (IndexedDB), không
  liên kết với Dashboard. Đã chuyển thành Report trên server giống Combo/
  Dòng tiền/Master File: upload → xử lý nền → lưu Parquet trên R2, có thêm
  xem danh sách dòng dữ liệu đã upload (read-only) và gán Kênh bán hàng.
- **Lưu ý đã báo trước đó**: dữ liệu cũ lưu trong trình duyệt (IndexedDB)
  không tự chuyển sang — cần upload lại qua tab mới.
- File chính: `app/adjustments_to_parquet.py` (mới),
  `app/routers/adjustments_reports.py` (mới). Đã xóa `frontend/js/db.js`
  (không còn dùng IndexedDB).

## 8. Tối ưu tốc độ upload & tải Dashboard

Chia làm 2 đợt vì đợt đầu chưa giải quyết đúng gốc vấn đề:

**Đợt 1 — chỉ giúp khi có nhiều người dùng cùng lúc, KHÔNG giúp 1 người
dùng đơn lẻ nhanh hơn:**
- Chuyển toàn bộ thao tác chặn luồng (block event loop) — đọc/ghi R2
  (boto3), parse Excel (openpyxl), chạy truy vấn DuckDB — sang thread pool
  (`starlette.concurrency.run_in_threadpool`), để 1 request nặng không làm
  đứng mọi request khác.
- Song song hóa các lượt gọi Supabase/R2 độc lập trong Dashboard bằng
  `asyncio.gather` thay vì gọi tuần tự.
- File chính: `app/query_engine.py` (`get_local_parquet_async`),
  `app/routers/dashboard.py`, và cả 5 router upload (`reports.py`,
  `cashflow_reports.py`, `combo_reports.py`, `master_reports.py`,
  `adjustments_reports.py`).

**Đợt 2 — thực sự giảm khối lượng công việc, giúp 1 lượt tải/upload nhanh
hơn:**
- **Đẩy bộ lọc ngày xuống trước khi join**: trước đây `orders_working`
  (bảng tạm gộp Combo/Cashflow/Master File) luôn được dựng trên TOÀN BỘ
  lịch sử đơn hàng, rồi mới lọc theo ngày ở bước sau — nghĩa là chọn khoảng
  thời gian hẹp không hề giảm khối lượng xử lý. Đã sửa để lọc ngày ngay khi
  đọc dữ liệu gốc, trước khi join — chọn "Tháng này" giờ thực sự chỉ xử lý
  dữ liệu của tháng đó.
- **Dashboard mặc định "Tháng này"** thay vì "Tất cả thời gian", để tối ưu
  trên tự động áp dụng mỗi lần mở Dashboard, không cần người dùng tự chọn.
- **Parse Excel nhanh hơn**: đọc file qua chế độ `read_only` của openpyxl
  thay vì chế độ mặc định (phải dựng toàn bộ cấu trúc ô + định dạng trong
  bộ nhớ) — nhanh hơn đáng kể với file có style/định dạng (như file xuất từ
  Shopee).
- File chính: `app/query_engine.py` (`_build_orders_working`,
  `_base_date_filter_sql`), `app/excel_to_parquet.py` (`read_excel_rows`),
  `frontend/js/app.js` (`wireTimeFilter` — default preset `thisMonth`).

---

## 10. Hỗ trợ file Đơn hàng kênh TikTok Shop

- Trước đây `app/mapping.py`/`app/excel_to_parquet.py` chỉ nhận diện được
  cấu trúc cột tiếng Việt của Shopee. Đã bổ sung nhận diện cột tiếng Anh
  của TikTok Shop vào **cùng** danh sách từ khóa hiện có (không cần kiến
  trúc "theo kênh" riêng, vì tiếng Việt và tiếng Anh không trùng từ khóa
  nên không có rủi ro xung đột giữa các kênh).
- Mapping cột cho TikTok (đã xác nhận với user, dựa trên file export thật
  ngày 2026-08-26): `Order ID`→Mã đơn hàng, `Created Time`→Ngày,
  `Order Status`→Trạng thái, `Cancel Reason`→Lý do hủy,
  `Seller SKU`→SKU phân loại hàng, `Quantity`→Số lượng,
  `SKU Unit Original Price`→Giá gốc, `Sku Quantity of return`→SL hoàn trả,
  `SKU Seller Discount`→Người bán trợ giá (Giảm giá = SKU Seller Discount /
  Số lượng). File Đơn hàng TikTok **không có** Voucher/Phí sàn/Phí
  AFF/Piship — các khoản này sẽ đến từ file Dòng tiền tương ứng của TikTok
  (chưa làm — xem mục "Việc còn để ngỏ").
- **Giá trị trạng thái/lý do hủy của TikTok khác Shopee dù cùng tiếng
  Việt**: TikTok dùng "Đã hoàn tất" (Shopee dùng "Hoàn thành") và "Giao
  gói hàng thất bại" (Shopee dùng "Giao hàng thất bại" — thiếu chữ "gói").
  Đã bổ sung nhận diện cả 2 cách viết trong `app/derive.py`.
- **Bug sửa kèm theo (ảnh hưởng mọi kênh, không riêng TikTok)**: file
  TikTok test thực tế lộ ra lỗi đọc Excel — một số công cụ xuất file tạo
  ra 1 thẻ `<row>` XML riêng cho **mỗi ô** thay vì mỗi dòng thực, khiến chế
  độ đọc nhanh (`read_only=True`, thêm ở mục 8) âm thầm cắt file 56 cột
  xuống còn 1 cột mà không báo lỗi gì. `read_excel_rows` giờ tự phát hiện
  kết quả bất thường (≤1 cột) và đọc lại ở chế độ đầy đủ.
- File chính: `app/mapping.py` (`KEYWORDS`), `app/derive.py`
  (`derive_order_status`), `app/excel_to_parquet.py` (`read_excel_rows`).

## 11. Hỗ trợ file Dòng tiền TikTok Shop (Phí sàn/Phí AFF) + chọn Kênh bán hàng lúc upload

- **File Dòng tiền TikTok** (`income_*.xlsx`, tiếng Việt, đã xác nhận với
  user dựa trên file thật 2026-08-26/27):
  - Phí AFF = Hoa hồng liên kết + Hoa hồng liên kết Quảng cáo cửa hàng.
  - Phí sàn = Tổng phí − Hoa hồng liên kết − Hoa hồng liên kết Quảng cáo
    cửa hàng − Thuế GTGT do TikTok Shop khấu trừ − Thuế TNCN do TikTok
    Shop khấu trừ.
  - Cả 2 đều lưu số âm trong file gốc, đảo dấu thành dương (giống Phí AFF
    Shopee). Loại trừ các dòng "Loại giao dịch" = "Khoản bồi hoàn của nền
    tảng" (không phải giao dịch đơn hàng thật).
  - `app/cashflow_to_parquet.py` giờ dùng `score_headers` (exact-match) thay
    vì `first_match_mapping` để tránh nhầm "Hoa hồng liên kết" với các cột
    dài hơn chứa chuỗi này. Cột "platformFee" mới (Phí sàn) chỉ khác 0 với
    file TikTok — file Shopee (chỉ có "phiAff") vẫn giữ nguyên hành vi cũ.
  - `app/query_engine.py`'s `_cashflow_agg_join` giờ join+cộng thêm Phí sàn
    từ Cashflow vào cột "platformFee" (cộng dồn với Phí sàn tính từ file
    Đơn hàng — mỗi kênh chỉ đóng góp 1 trong 2 nguồn).
- **Piship chỉ áp dụng cho kênh Shopee**: phát hiện Phí Piship (1.620đ/đơn)
  trước đây bị tính cho MỌI kênh không phân biệt. Giờ chỉ tính khi Report
  được gán kênh "Shopee" (không phân biệt hoa/thường) — kênh khác (TikTok,
  Lazada,...) mặc định KHÔNG tính; không chọn kênh nào thì mặc định vẫn
  tính (giữ hành vi cũ cho toàn bộ report trước đây, đều là Shopee).
  `app/derive.py` (`channel_has_piship`, `PISHIP_CHANNEL_NAMES`).
- **Chọn Kênh bán hàng ngay lúc upload** (thay vì chỉ gán sau khi upload
  xong): thêm ô chọn kênh vào form upload của 3 tab Đơn hàng/Dòng
  tiền/Điều chỉnh doanh thu — để backend biết kênh NGAY khi xử lý file
  (Piship cần biết kênh tại thời điểm này, không thể biết sau). PATCH gán
  kênh sau khi upload vẫn còn (sửa nhầm), nhưng KHÔNG re-convert lại file
  — muốn áp dụng lại Piship theo kênh mới phải dùng "Chỉnh cột" để convert
  lại. File chính: `app/routers/_report_crud.py` (`channel_aware_converter`,
  `sales_channel_id` tại `POST /api/reports`), `frontend/js/app.js`
  (`uploadChannelSelectId`, `populateUploadChannelSelects`).
- **Đơn TikTok bị hoàn toàn bộ → Phí AFF = 0** (xác nhận với user
  2026-08-27, đơn thật `582572544565151151`): khi 1 đơn có nhiều dòng
  trong file Dòng tiền và tổng "Tổng doanh thu" của các dòng đó = 0 (đơn
  bị hoàn/huỷ hoàn toàn), cột Hoa hồng liên kết/Hoa hồng liên kết Quảng
  cáo cửa hàng được coi là 0 khi tính cả Phí AFF lẫn Phí sàn cho đơn đó —
  vì dòng hoàn của TikTok không luôn đảo ngược lại đúng 2 cột này, cộng
  thô sẽ để lại Phí AFF khác 0 cho 1 đơn không tạo ra doanh thu nào. Tổng
  chi phí (Phí AFF + Phí sàn) không đổi, chỉ chuyển hẳn sang Phí sàn.
  `app/cashflow_to_parquet.py` (`totalRevenue` mapping mới, `_REVENUE_EPSILON`).
- **PATCH gán Kênh bán hàng giờ tự re-convert** (fix bug: report Đơn hàng
  TikTok cũ được gán kênh SAU khi upload — qua tab Kênh bán hàng — vẫn hiện
  Phí Piship khác 0 trên Dashboard). Trước đây PATCH `/{id}/channel` chỉ
  sửa metadata, không tính lại Piship; giờ với Report Đơn hàng
  (`channel_aware_converter`), PATCH này tự tải lại file gốc từ R2 và
  convert lại bằng đúng mapping cột đã lưu, chỉ đổi kênh dùng để gate
  Piship. `app/routers/_report_crud.py` (`update_channel`).

## 12. Gộp tab nav thành "Dữ liệu bán hàng" + "Danh mục"

- Theo yêu cầu user: 7 tab trên cùng (Đơn hàng, Master File, Combo, Dòng
  tiền, Điều chỉnh doanh thu, Kênh bán hàng) rút gọn còn 3 (Dashboard, Dữ
  liệu bán hàng, Danh mục):
  - **Dữ liệu bán hàng**: gộp Đơn hàng + Dòng tiền + Điều chỉnh doanh thu,
    chọn qua dropdown "Loại file" (sẽ thêm Kênh AFF TikTok vào đây sau).
  - **Danh mục**: gộp Master File + Combo + Kênh bán hàng, cùng cơ chế
    dropdown "Loại file".
  - Chỉ đổi cấu trúc nav/hiển thị — mỗi panel con (`panel-orders`,
    `panel-cashflow`,...) giữ nguyên id/wiring cũ từ `createReportTab()`,
    không đổi logic upload/list/xóa/gán kênh nào cả. `frontend/index.html`
    (bỏ class `tab-panel` khỏi 6 panel con, chỉ 3 panel gộp còn giữ class
    này), `frontend/js/app.js` (`wireFileTypeGroup`).

## Việc còn để ngỏ (chưa làm, chờ thông tin)

- **Đa kênh khác (Lazada,...)**: áp dụng cách làm tương tự mục 10/11 khi có
  mẫu file thật.
- **Kênh nhỏ TikTok (LIVE/VIDEO/PSA/AFF)**: user gửi mẫu file
  `affiliate_orders_*.xlsx` (cột "ID đơn hàng"/"Loại nội dung"/"Tên người
  dùng nhà sáng tạo",...) để join thêm 1 bộ lọc "Kênh nhỏ" theo LIVE/VIDEO/
  PSA/AFF cho đơn TikTok. Còn thiếu: (1) cách map giá trị cột "Loại nội
  dung" (Video/Trưng bày/Chương trình Lưu lượng truy cập bên ngoài — file
  mẫu không có giá trị LIVE) sang 4 nhãn LIVE/VIDEO/PSA/AFF, (2) nhãn gán
  cho đơn KHÔNG xuất hiện trong file này. User sẽ giải thích chi tiết sau;
  phần upload chung (dropdown "Loại file" trong tab "Dữ liệu bán hàng") đã
  làm sẵn ở mục 12, chỉ cần thêm 1 option "Kênh AFF" + Report/parser mới
  khi có đủ thông tin.

## Cache-busting frontend

Mỗi lần sửa `frontend/js/app.js` hoặc `frontend/index.html`, nhớ tăng số
`?v=N` ở 2 dòng `<script src="js/...">` cuối `index.html` — nếu không trình
duyệt có thể dùng bản JS cũ trong cache. Phiên bản hiện tại: **v=33**.

## 9. Tối ưu hóa code (reuse/simplification/efficiency)

- **Backend**: dùng chung 1 `httpx.AsyncClient`/boto3 client thay vì tạo
  mới mỗi lần gọi Supabase/R2; `db.mark_failed()` chống Report kẹt mãi ở
  "processing" nếu bước ghi lỗi cũng lỗi; gộp code trùng ở
  `query_engine.py` (4 hàm `run_*_query`), 5 router upload (Đơn hàng/Dòng
  tiền/Combo/Master File/Điều chỉnh — qua `routers/_report_crud.py`), và
  logic nhận diện cột Excel (`mapping.score_headers`/`first_match_mapping`,
  dùng lại ở `master_to_parquet.py`/`adjustments_to_parquet.py`/
  `cashflow_to_parquet.py`/`combo_to_parquet.py`); gộp 2 lượt tải Parquet
  từ R2 khi `/summary` và `/rows` chạy song song lúc cache nguội
  (`get_local_parquet_async`); bỏ cột "sku" dư thừa không ai đọc lại trong
  Parquet Đơn hàng.
- **Frontend**: gộp 5 controller tab upload/poll/list/delete gần như giống
  hệt nhau (Đơn hàng/Dòng tiền/Combo/Master File/Điều chỉnh) thành 1
  `createReportTab()` dùng chung trong `app.js`.
- File chính: `app/db.py`, `app/storage.py`, `app/query_engine.py`,
  `app/routers/_report_crud.py` (mới), `app/mapping.py`,
  `frontend/js/app.js` (`createReportTab`).
