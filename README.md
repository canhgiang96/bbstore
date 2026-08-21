# 📊 Dashboard Kinh Doanh

Trang web tổng hợp dữ liệu kinh doanh từ nhiều file Excel (đơn hàng, master file, combo, dòng tiền, điều chỉnh doanh thu) và tạo báo cáo dashboard.

> **⚠️ Đang chuyển sang kiến trúc mới (Giai đoạn 2).** Thư mục [`backend/`](backend/README.md) là bản kế tiếp — có server thật (FastAPI + Supabase + Cloudflare R2), đăng nhập theo tài khoản, Admin upload xong thì B/C/D xem được ngay trên máy khác không cần thao tác thủ công. Phần còn lại của README này (`index.html`/`css/`/`js/` ở gốc repo) là bản Giai đoạn 1 — 100% chạy trong trình duyệt, IndexedDB, publish thủ công qua GitHub Pages — vẫn đang chạy thật cho tới khi bản Giai đoạn 2 deploy xong và được xác nhận hoạt động đúng, sau đó các file này sẽ được gỡ bỏ. Xem [`backend/README.md`](backend/README.md) để deploy/vận hành bản mới.

**Demo trực tuyến (Giai đoạn 1, sẽ ngừng dùng):** _(cập nhật link GitHub Pages sau khi publish)_

## Tính năng

**Dashboard**
- **Mỗi file Excel tải lên ở tab Đơn hàng là 1 Report độc lập** — dropdown "Report đang xem" chọn Report nào, Dashboard chỉ tính KPI/biểu đồ/bảng trên riêng dữ liệu của Report đó, không gộp chung với các Report khác
- Tự động nhận diện các cột: Ngày, Sản phẩm, Danh mục, Khách hàng, Số lượng, Đơn giá, Giá gốc, Doanh thu, Trạng thái đơn hàng, Lý do hủy, SL sản phẩm hoàn trả, Mã đơn hàng, SKU phân loại hàng
- Nút "Chỉnh cột ⚙️" để ghi đè nhận diện tự động khi cần
- **Cột tự tính:**
  - `SKU` = SKU phân loại hàng, bỏ `-` và phần phía sau (mã cha của biến thể)
  - `Doanh số` = Giá gốc × Số lượng
  - `Số lượng thực` = Số lượng − Số lượng sản phẩm được hoàn trả
  - `Trạng thái` (suy ra theo thứ tự ưu tiên): **Hủy sau XK** (trạng thái chứa "hủy" + lý do hủy chứa "Giao hàng thất bại") → **Hủy chưa XK** (chứa "hủy", lý do khác) → **Hoàn hàng** (Số lượng thực = 0) → **Hoàn 1 phần** (có hoàn trả nhưng Số lượng thực > 0) → **Hoàn thành** (trạng thái đơn hàng = Hoàn thành) → còn lại là **Đang giao**
- 5 chỉ số KPI: Doanh số, GMV (Hoàn thành + Đang giao), Doanh số hủy chưa XK, Doanh số hủy sau XK, Doanh số hoàn (Hoàn hàng + Hoàn 1 phần) — 4 mục sau cộng lại đúng bằng Doanh số tổng
- Bộ lọc theo khoảng thời gian, danh mục, và trạng thái (6 giá trị suy ra ở trên)
- 4 biểu đồ theo Doanh số, bảng dữ liệu chi tiết có tìm kiếm và phân trang
- Nút "Dùng dữ liệu mẫu" để xem thử ngay không cần file

**Quản lý dữ liệu (5 tab riêng: Đơn hàng, Master File, Combo, Dòng tiền, Điều chỉnh doanh thu)**
- Upload file `.xlsx`, `.xls`, `.csv` (kéo thả hoặc chọn file) — dữ liệu được lưu vào IndexedDB của trình duyệt
- Với Master File / Combo / Dòng tiền: tải lên lại sẽ **cập nhật** dòng có khóa trùng (SKU phân loại / SKU COMBO / Mã đơn hàng) thay vì tạo trùng lặp
- Tab **Đơn hàng** hiển thị danh sách **Report** (mỗi lần upload = 1 Report), mỗi Report có nút riêng: **Xuất báo cáo** (chỉ xuất dữ liệu của Report đó) và **Xóa Report này**
- Các tab còn lại nhóm theo tên file trong mục "Theo file đã tải lên" — xóa cả file đó chỉ với 1 click
- Thêm dòng thủ công, sửa từng dòng, xóa từng dòng, hoặc xóa toàn bộ dữ liệu một loại
- Tìm kiếm và phân trang trên mỗi bảng dữ liệu

## Cách dùng

Mở trực tiếp `index.html` bằng trình duyệt, hoặc chạy local server:

```bash
python3 -m http.server 8000
```

rồi truy cập `http://localhost:8000`.

## Cấu trúc file Excel gợi ý

| Ngày | Sản phẩm | Danh mục | Khách hàng | Số lượng | Đơn giá | Doanh thu |
|------|----------|----------|------------|----------|---------|-----------|
| 01/02/2025 | Áo thun | Thời trang | Nguyễn Văn A | 3 | 150000 | 450000 |

Không bắt buộc phải có đủ tất cả các cột — chỉ cần cột **Ngày** và (**Doanh thu** hoặc cặp **Đơn giá + Số lượng**).

## Công nghệ

- HTML/CSS/JavaScript thuần, không cần build
- [SheetJS (xlsx)](https://sheetjs.com/) để đọc file Excel
- [Chart.js](https://www.chartjs.org/) để vẽ biểu đồ
- IndexedDB (`js/db.js`) để lưu dữ liệu trong trình duyệt

## Lưu ý về dữ liệu

Dữ liệu lưu trong IndexedDB chỉ tồn tại **trên trình duyệt/máy hiện tại** — mở trang bằng trình duyệt khác hoặc máy khác sẽ không thấy dữ liệu cũ. Đây chưa phải nơi ghép nối (join) dữ liệu giữa 5 loại file để tính lợi nhuận thực — phần đó sẽ được bổ sung sau khi có công thức tính toán cụ thể.

## Cho máy khác xem Dashboard (publish Report)

Trang là static site nên không tự đồng bộ dữ liệu giữa các máy theo thời gian thực — Admin phải chủ động "publish" từng Report. Quy trình:

1. Trên máy Admin (đã upload Excel ở tab **Đơn hàng**), tìm Report cần publish — trong danh sách Report ở tab Đơn hàng, hoặc chọn đúng Report đó trong dropdown **"Report đang xem"** ở tab Dashboard — rồi bấm **Xuất báo cáo**. File tải về tên theo Report (vd `01082026.json`), gồm dữ liệu + cấu hình cột đã nhận diện của riêng Report đó.
2. Gửi file này cho Claude (hoặc tự làm nếu quen với git): thêm vào `data/reports/<tên-file>.json`, cập nhật `data/reports/index.json` (mảng liệt kê `id`, `name`, `uploadedAt`, `rowCount`, `file`), rồi commit + push.
3. Sau khi GitHub Pages deploy xong, bất kỳ máy nào **chưa có dữ liệu cục bộ** mở trang sẽ thấy dropdown "Report đang xem" liệt kê tất cả Report đã publish — B/C/D tự chọn Report muốn xem, mỗi lần chọn chỉ tải đúng dữ liệu Report đó (không tải hết mọi Report cùng lúc).
4. Máy nào **đã có dữ liệu riêng** trong IndexedDB vẫn luôn ưu tiên xem Report cục bộ của máy đó, không bị ghi đè bởi các Report đã publish.

Cấu trúc thư mục `data/reports/`:
```
data/reports/
  index.json        ← danh sách Report đã publish
  01082026.json      ← dữ liệu + mapping của từng Report
  08082026.json
```

Mỗi khi có Report mới muốn công khai, lặp lại bước 1–2 cho Report đó — các Report cũ đã publish không cần đụng tới.

## Deploy lên GitHub Pages

1. Vào **Settings → Pages** của repository
2. Chọn branch `main`, thư mục `/ (root)`
3. Truy cập trang tại `https://<username>.github.io/<repo-name>/`
