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

## 13. Kênh nhỏ TikTok (LIVE/VIDEO/PSA/AFF) — Report "Kênh AFF" mới

- **⚠️ Cần chạy SQL migration trên Supabase trước khi dùng được** — xem
  `/Users/canhgiang/.claude/plans/declarative-rolling-curry.md` để lấy đúng
  câu SQL tạo 2 bảng mới: `aff_channel_reports` (Report Kênh AFF) và
  `inhouse_creator_handles` (danh sách ID Inhouse, quản lý qua UI). Chưa
  chạy migration thì tính năng này tự động no-op (best-effort try/except,
  giống Combo/Master File lúc mới thêm) — Dashboard không bị lỗi, chỉ là
  Kênh nhỏ luôn trống.
- **Rule phân loại** (xác nhận với user 2026-08-27, dùng file thật
  `affiliate_orders_7669777829750097685.xlsx` +
  `Tất cả đơn hàng-2026-08-04-11_54.xlsx`):
  1. Match theo (Mã đơn hàng, SKU ID) với file Kênh AFF đã upload → luôn
     luôn **AFF**, bất kể trạng thái "Đã quyết toán"/"Không đủ điều kiện".
  2. Còn lại, dựa vào 2 cột có sẵn trên file Đơn hàng TikTok — "Creator
     Handle" và "Order Channel": handle trống/"0" → **PSA** (kênh chính);
     handle nằm trong danh sách "ID Inhouse" (quản lý được, seed
     bbstores.vn/bbcongso/bbstores_forlady) → map theo Order Channel
     (Videos→VIDEO, Product cards→PSA, LIVE→LIVE); handle khác → **AFF**.
  3. Chỉ áp dụng cho đơn kênh TikTok — kênh khác luôn để trống (NULL).
  - **Sửa lại đề xuất ban đầu của user**: cột join đúng là **"SKU ID"** (mã
    nội bộ TikTok, ~19 chữ số) chứ không phải "SKU phân loại" (map từ
    "Seller SKU") — 2 cột này khác giá trị hoàn toàn, đã verify bằng đơn
    thật (`582836886501426351`) trước khi code.
- **Backend**: `app/aff_channel_to_parquet.py` (converter mới, output
  (orderId, skuId) đã dedupe); `app/mapping.py` (3 field mới `skuId`/
  `creatorHandle`/`contentChannel`, optional, chỉ TikTok có); `skuId` luôn
  đọc dạng text (không qua `to_number()`, tránh mất độ chính xác số 19 chữ
  số); `app/query_engine.py` (`_aff_channel_join`, `kenhNho` CASE expr mới
  trong `_build_orders_working`, cột `kenhNho` cho Detail-table/Group
  theo/Export/facet); `app/routers/aff_channel_reports.py` (Report CRUD,
  không có Kênh bán hàng riêng); `app/routers/_named_list_crud.py` (factory
  dùng chung cho Kênh bán hàng + ID Inhouse, refactor từ
  `sales_channels.py`); `app/routers/inhouse_handles.py` (CRUD mới).
- **Frontend**: option "Kênh AFF" trong dropdown "Loại file" của tab Dữ
  liệu bán hàng; option "ID Inhouse" trong tab Danh mục; bộ lọc "Kênh nhỏ"
  mới trên Dashboard (giống Kênh bán hàng); `wireNamedListTab()` factory
  dùng chung cho 2 tab named-list.

## 14. Upload chung 1 file cho 31 LVS/HARA/WEBSITE/ZALO

- File `sale_report_*.xlsx` (xác nhận với user 2026-08-28, file thật
  `sale_report_28_08_2026_927871_1`) gộp cả 4 kênh vào 1 file, đánh dấu
  từng dòng bằng cột "Kênh bán hàng" (POS/Harasocial/Web/Zalo) — giờ upload
  được trực tiếp qua tab Đơn hàng như bình thường (không cần tách 4 file,
  không cần chọn kênh lúc upload — hệ thống tự nhận diện theo cột này,
  không cần đổi UI upload).
  - Map: POS→31 LVS, Harasocial→HARA, Web→WEBSITE, chứa "zalo"→ZALO.
    `app/derive.py` (`normalize_combined_sales_channel`,
    `COMBINED_SALES_CHANNEL_MAP`). Không nhận diện được (kênh khác) thì
    giữ nguyên kênh gán lúc upload (như trước giờ).
  - Piship tự động tắt cho cả 4 kênh này ở từng dòng (không phụ thuộc kênh
    chọn lúc upload).
  - **Phát hiện quan trọng khi test bằng file thật**: file này lưu "Số sản
    phẩm trả"/"Giảm giá"/"Hoàn trả" dưới dạng **số ÂM** (khác quy ước dương
    của Shopee/TikTok) — đã chuẩn hoá về dương (`abs()`) khi đọc, nếu không
    sẽ tính sai SL thực (cộng nhầm thay vì trừ) và Doanh thu thuần (cộng
    giảm giá thay vì trừ).
  - File này cũng không có "Trạng thái đơn hàng"/"Giá gốc"/"Lý do hủy" —
    3 field này chuyển từ `required=True` sang required tùy điều kiện (có
    "Trạng thái đơn hàng" thì vẫn bắt buộc đủ 3, không có thì bắt buộc
    "Doanh thu" thay "Giá gốc" — xem `app/excel_to_parquet.py`). Không có
    Trạng thái đơn hàng → mọi dòng mặc định "Hoàn thành" (trừ khi SL hoàn
    trả cho thấy Hoàn hàng/Hoàn 1 phần) — xác nhận: file dạng này không có
    khái niệm đơn hủy.
  - Field mapping trực tiếp theo đúng yêu cầu user (không tính lại qua
    công thức Shopee): Số lượng=Số sản phẩm, SL hoàn trả=Số sản phẩm trả,
    Giảm giá=Giảm giá (field mới `discountAmount`), Doanh số=Doanh thu
    (originalPrice suy ra = Doanh thu/Số lượng khi không có Giá gốc), Doanh
    số hoàn=Hoàn trả (field mới `refundAmount`, cột mới `hoanAmount` —
    persist ở Parquet, KPI "hoan" đổi từ tính lại `originalPrice x
    returnedQty` sang `SUM(hoanAmount)`, có fallback cho Report cũ chưa có
    cột này).
  - `app/mapping.py` (field mới `channelRaw`/`discountAmount`/
    `refundAmount`, keyword mới cho quantity/returnedQty/skuVariant, guard
    exact-match-only cho 3 field mới tránh nhầm cột dài hơn của Shopee).

## 15. Khóa Report để tránh xóa nhầm

- **⚠️ Cần chạy SQL migration trên Supabase trước khi dùng được**:
  ```sql
  alter table reports add column locked boolean not null default false;
  alter table cashflow_reports add column locked boolean not null default false;
  alter table combo_reports add column locked boolean not null default false;
  alter table master_reports add column locked boolean not null default false;
  alter table adjustments_reports add column locked boolean not null default false;
  alter table aff_channel_reports add column locked boolean not null default false;
  ```
  Chưa chạy thì nút "Khóa" vẫn hiện nhưng bấm sẽ báo lỗi (cột chưa tồn
  tại) — không ảnh hưởng phần còn lại của hệ thống.
- Mỗi Report (cả 6 loại: Đơn hàng/Dòng tiền/Combo/Master File/Điều chỉnh
  doanh thu/Kênh AFF) có nút "Khóa"/"Mở khóa" (admin) trong danh sách —
  Report khóa hiện 🔒 trước tên, nút "Xóa" bị vô hiệu hóa (kèm tooltip).
  Backend cũng chặn xóa Report đã khóa (409) dù request đến trực tiếp qua
  API, không chỉ chặn ở giao diện. `app/routers/_report_crud.py`
  (`update_lock`, guard trong `delete_report`), `app/models.py`
  (`LockUpdateRequest`, `ReportOut.locked`), `frontend/js/app.js`
  (`createReportTab`'s Thao tác column).

## 16. Sub-tab "Phân tích tháng" (bảng P&L theo tháng)

- **⚠️ Cần chạy SQL migration trên Supabase trước khi dùng được**:
  ```sql
  create table monthly_expenses (
    month date primary key,
    chi_phi_ban_hang numeric not null default 0,
    chi_phi_quan_ly numeric not null default 0,
    updated_at timestamptz not null default now(),
    updated_by uuid references profiles(id)
  );
  alter table monthly_expenses enable row level security;
  ```
  Chưa chạy thì tab vẫn hiện, Chi phí bán hàng/Chi phí quản lý mặc định 0
  (không báo lỗi — try/except giống Combo/Master File/Kênh AFF lúc mới
  thêm), chỉ là chưa lưu được số nhập vào.
- Thêm sub-tab thứ 3 "Phân tích tháng" cạnh "Tổng quan"/"Dữ liệu chi
  tiết" trong Dashboard — bảng P&L theo tháng đúng công thức trong file
  mẫu user gửi (đối chiếu khớp chính xác với số liệu thật, 2026-08-28):
  Tháng, GMV, %NMV/GMV, NMV, %LNG (=LNG/NMV), Lợi nhuận gộp, %CPBH/LNG,
  Chi phí bán hàng, %CPQL/LNG, Chi phí quản lý, %LN/NMV, Lợi nhuận (=LNG −
  Chi phí bán hàng − Chi phí quản lý), %TCP/LNG.
  - **Cố ý KHÔNG áp dụng bộ lọc Dashboard** (Thời gian/Trạng thái/Kênh bán
    hàng...) — luôn hiện toàn bộ lịch sử theo tháng, đúng tinh thần báo
    cáo tài chính tổng thể (xác nhận với user). Ẩn luôn thanh filter khi
    đang ở sub-tab này để tránh gây hiểu nhầm là có lọc.
  - GMV/NMV/Lợi nhuận gộp: tổng hợp từ toàn bộ Report đã sẵn sàng qua
    query engine hiện có (không cần dữ liệu mới). Chi phí bán hàng/Chi
    phí quản lý: chi phí vận hành cấp công ty, KHÔNG có trong file Excel
    nào — admin bấm trực tiếp vào ô trên bảng để nhập/sửa theo tháng, lưu
    qua API (không cần form/màn hình riêng).
  - `app/query_engine.py` (`run_monthly_analysis_query` — duy nhất trong
    các `run_*_query` không nhận tham số lọc nào); `app/routers/
    monthly_analysis.py` (mới, tái dùng `_all_ready_reports`/
    `_all_dashboard_sources` từ `dashboard.py`); `frontend/index.html`
    (sub-tab + bảng mới), `frontend/js/app.js` (`fetchAndRenderMonthly
    Analysis`, ô nhập trực tiếp `.monthly-expense-input`).

## Việc còn để ngỏ (chưa làm, chờ thông tin)

- **Đa kênh khác (Lazada,...)**: áp dụng cách làm tương tự mục 10/11 khi có
  mẫu file thật.

## Cache-busting frontend

Mỗi lần sửa `frontend/js/app.js` hoặc `frontend/index.html`, nhớ tăng số
`?v=N` ở 2 dòng `<script src="js/...">` cuối `index.html` — nếu không trình
duyệt có thể dùng bản JS cũ trong cache. Phiên bản hiện tại: **v=37**.

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
