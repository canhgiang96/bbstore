# 📊 Dashboard Kinh Doanh

Trang web tổng hợp dữ liệu kinh doanh từ nhiều file Excel (đơn hàng, master file, combo, dòng tiền, điều chỉnh doanh thu) và tạo báo cáo dashboard — chạy hoàn toàn trên trình duyệt, không cần server. Dữ liệu được lưu lại trong trình duyệt (IndexedDB) nên vẫn còn khi bạn đóng và mở lại trang.

**Demo trực tuyến:** _(cập nhật link GitHub Pages sau khi publish)_

## Tính năng

**Dashboard**
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
- Mỗi lần upload được nhóm theo tên file trong mục **"Theo file đã tải lên"** — xem số dòng/thời gian và **xóa cả file đó** chỉ với 1 click, không cần xóa từng dòng
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

## Cho máy khác xem Dashboard (publish báo cáo)

Trang là static site nên không tự đồng bộ dữ liệu giữa các máy theo thời gian thực. Để máy khác xem được Dashboard:

1. Trên máy có dữ liệu (đã upload ở tab **Đơn hàng**), vào tab **Dashboard**, bấm **📤 Xuất báo cáo** — tải về file `orders.json`.
2. Đặt file này vào `data/orders.json` trong thư mục dự án, rồi commit + push lên GitHub (hoặc gửi file cho Claude để publish giúp).
3. Sau khi GitHub Pages deploy xong, bất kỳ máy nào **chưa có dữ liệu cục bộ** mở trang sẽ tự động thấy Dashboard dựa trên báo cáo đã publish (banner hiển thị "📡 Đang xem báo cáo đã publish lúc ..."), ở chế độ chỉ xem.
4. Máy nào **đã có dữ liệu riêng** trong IndexedDB vẫn luôn ưu tiên xem dữ liệu cục bộ của máy đó (không bị ghi đè bởi báo cáo đã publish).

Mỗi lần dữ liệu đơn hàng thay đổi và muốn cập nhật cho người xem khác, lặp lại bước 1–2.

## Deploy lên GitHub Pages

1. Vào **Settings → Pages** của repository
2. Chọn branch `main`, thư mục `/ (root)`
3. Truy cập trang tại `https://<username>.github.io/<repo-name>/`
