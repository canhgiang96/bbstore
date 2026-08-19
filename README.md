# 📊 Dashboard Kinh Doanh

Trang web tổng hợp dữ liệu kinh doanh và tạo báo cáo dashboard trực tiếp từ file Excel — chạy hoàn toàn trên trình duyệt, không cần server, dữ liệu không rời khỏi máy của bạn.

**Demo trực tuyến:** _(cập nhật link GitHub Pages sau khi publish)_

## Tính năng

- Upload file `.xlsx`, `.xls`, `.csv` (kéo thả hoặc chọn file)
- Tự động nhận diện các cột: Ngày, Sản phẩm, Danh mục, Khách hàng, Số lượng, Đơn giá, Doanh thu
- Cho phép chỉnh lại ánh xạ cột nếu nhận diện chưa đúng
- Tự tính Doanh thu = Đơn giá × Số lượng nếu file không có sẵn cột doanh thu
- Bộ lọc theo khoảng thời gian và danh mục
- 4 chỉ số KPI: Tổng doanh thu, Tổng đơn/dòng, Số lượng bán, Giá trị trung bình/đơn
- 4 biểu đồ: Doanh thu theo thời gian, Top sản phẩm, Doanh thu theo danh mục, Top khách hàng
- Bảng dữ liệu chi tiết có tìm kiếm và phân trang
- Nút "Dùng dữ liệu mẫu" để xem thử ngay không cần file

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

## Deploy lên GitHub Pages

1. Vào **Settings → Pages** của repository
2. Chọn branch `main`, thư mục `/ (root)`
3. Truy cập trang tại `https://<username>.github.io/<repo-name>/`
