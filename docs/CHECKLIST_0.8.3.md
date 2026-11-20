# Checklist bản 0.8.3: thiết kế mới

Làm theo bản mẫu đã duyệt (trang thiết kế "Bản mẫu cửa sổ cài đặt SAM3 Assist") và logo phương án E.

Các cột giống `docs/CHECKLIST_0.8.0.md`:

- **Đã thêm**
- **Đã test**
- **Lỗi tìm thấy**
- **Đã sửa**

Ký hiệu: ✅ xong · 🟡 làm một phần (lý do ở cuối).

| # | Thay đổi | Đã thêm | Đã test | Lỗi tìm thấy | Đã sửa |
|---|---|---|---|---|---|
| 1 | Logo E: bộ icon 16–256 px (cỡ nhỏ nét dày riêng) cho exe, bộ cài, lối tắt, khay, cửa sổ | ✅ | ✅ test_v083: đủ cỡ, mọi nơi cùng một logo | — | — |
| 2 | Logo trên panel CVAT, bảng điều khiển, trang duyệt guideline, tab trình duyệt của backend | ✅ | ✅ test_v083 + các smoke panel | — | — |
| 3 | Taskbar hiện icon và tên SAM3 CVAT Assist (thay Python) | ✅ | ✅ test_v083 (cấu hình) · Windows thật: chưa | — | — |
| 4 | Màn hình chờ có logo lúc mở app, tự đóng khi cửa sổ hiện | ✅ | 🟡 CI Windows: biên dịch · Windows có màn hình: chưa | — | — |
| 5 | Cửa sổ cài đặt: cột bước bên trái, thanh nút cố định, 6 bước | ✅ | ✅ control smoke (trạng thái bước, trang hoàn tất) + ảnh chụp sáng/tối | — | — |
| 6 | Trang tải: tiến độ tổng, tốc độ, thời gian còn lại, trạng thái từng thành phần | ✅ | ✅ test_v083 (số liệu) + ảnh chụp | L1 | ✅ |
| 7 | Bảng điều khiển: cột trái có mục lục, sáng/tối theo Windows | ✅ | ✅ control smoke (mục lục trỏ đúng thẻ) + ảnh chụp | — | — |
| 8 | Bộ cài: dải màu thương hiệu, logo nhỏ, lối tắt gom một biểu tượng | ✅ | ✅ test_v083 · CI Windows: biên dịch và cài thử | — | — |
| 9 | Chữ mới có bản tiếng Anh | ✅ | ✅ test_i18n + control smoke ở chế độ English | L2 | ✅ |

## Rà phần cũ

| Phần cũ | Thay đổi |
|---|---|
| Thứ tự nút trên panel | Logo đứng trước tên, chấm trạng thái backend chuyển ra sau tên |
| Màu nhấn của panel, trang duyệt guideline | Đổi sang xanh thương hiệu `#2456D6` (đủ tương phản với chữ trắng) |
| Thông tin cập nhật giả lập trong test | Thêm phần tiếng Anh, giống ghi chú Release thật từ bản 0.8.1 |
| pywebview | Yêu cầu bản 6 trở lên (đặt được icon cửa sổ) |
| Biên dịch `SAM3 Assist.exe` | Thêm thư viện vẽ (System.Drawing) cho màn hình chờ |

## Lỗi tìm thấy khi test

| # | Lỗi | Ở đâu | Đã sửa |
|---|---|---|---|
| L1 | Phần tính tiến độ tổng bị đặt nhầm vào hàm tiến độ cập nhật app | app desktop | ✅ chuyển về đúng hàm |
| L2 | Ghi chú cập nhật giả lập trong test chỉ có tiếng Việt, nên kiểm chế độ English báo sót | test | ✅ |

## Mục 🟡

**4. Màn hình chờ:** GitHub chỉ biên dịch được, không có màn hình để xem. Lần mở app đầu trên máy bạn là lần kiểm cuối.
