# 📊 Dashboard Kinh Doanh

Trang web tổng hợp dữ liệu kinh doanh từ nhiều file Excel (đơn hàng, master file, combo, dòng tiền, điều chỉnh doanh thu) và tạo báo cáo dashboard — chạy hoàn toàn trên trình duyệt, không cần server. Dữ liệu được lưu lại trong trình duyệt (IndexedDB) nên vẫn còn khi bạn đóng và mở lại trang.

**Demo trực tuyến:** _(cập nhật link GitHub Pages sau khi publish)_

## Tính năng

**Dashboard**
- Tự động nhận diện các cột: Ngày, Sản phẩm, Danh mục, Khách hàng, Số lượng, Đơn giá, Doanh thu, Trạng thái đơn hàng, Mã đơn hàng, SKU phân loại hàng
- Nút "Chỉnh cột ⚙️" để ghi đè nhận diện tự động khi cần
- Tự tính Doanh thu = Đơn giá × Số lượng nếu file không có sẵn cột doanh thu
- Tự loại trừ đơn đã hủy/hoàn trả khỏi số liệu (dựa trên cột Trạng thái đơn hàng)
- Bộ lọc theo khoảng thời gian và danh mục
- 4 chỉ số KPI, 4 biểu đồ, bảng dữ liệu chi tiết có tìm kiếm và phân trang
- Nút "Dùng dữ liệu mẫu" để xem thử ngay không cần file

**Quản lý dữ liệu (5 tab riêng: Đơn hàng, Master File, Combo, Dòng tiền, Điều chỉnh doanh thu)**
- Upload file `.xlsx`, `.xls`, `.csv` (kéo thả hoặc chọn file) — dữ liệu được lưu vào IndexedDB của trình duyệt
- Với Master File / Combo / Dòng tiền: tải lên lại sẽ **cập nhật** dòng có khóa trùng (SKU phân loại / SKU COMBO / Mã đơn hàng) thay vì tạo trùng lặp
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

## Deploy lên GitHub Pages

1. Vào **Settings → Pages** của repository
2. Chọn branch `main`, thư mục `/ (root)`
3. Truy cập trang tại `https://<username>.github.io/<repo-name>/`
