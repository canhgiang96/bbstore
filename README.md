# 📊 Dashboard Kinh Doanh — BBStore

Hệ thống tổng hợp dữ liệu kinh doanh từ Excel và tạo báo cáo dashboard cho BBStore.

**Đang chạy tại:** https://bbstore-backend.onrender.com

Toàn bộ ứng dụng (backend + frontend) nằm trong [`backend/`](backend/README.md) —
xem file đó để biết chi tiết kiến trúc, cách chạy local, và hướng dẫn deploy.

## Tóm tắt

- **Đơn hàng**: Admin upload file Excel → server tự nhận diện cột, tính Doanh
  số/GMV/trạng thái đơn hàng, lưu thành 1 Report độc lập. Bất kỳ ai đăng nhập
  (Admin hoặc Viewer) đều xem được Dashboard của Report đó ngay, không cần
  thao tác thủ công gì thêm.
- **Master File / Combo / Dòng tiền / Điều chỉnh doanh thu**: 4 loại dữ liệu
  tham chiếu còn lại, hiện vẫn quản lý trực tiếp trên trình duyệt (IndexedDB)
  trong mỗi tab tương ứng — chưa ghép nối với Đơn hàng, chờ công thức tính lợi
  nhuận thực cụ thể.
- **Phân quyền**: tài khoản Admin (upload/xóa Report) và Viewer (chỉ xem),
  quản lý qua Supabase Auth.

## Kế hoạch / kiến trúc đầy đủ

Xem `~/.claude/plans/temporal-rolling-crystal.md` (schema Postgres, danh sách
API endpoint, lộ trình build) hoặc [`backend/README.md`](backend/README.md).
