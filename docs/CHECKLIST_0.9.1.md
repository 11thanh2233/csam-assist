# Checklist bản 0.9.1: đổi tên, cài đặt chắc chắn hơn, cập nhật an toàn

Các cột giống `docs/CHECKLIST_0.8.0.md`:

- **Đã thêm**
- **Đã test**
- **Lỗi tìm thấy**
- **Đã sửa**

Ký hiệu: ✅ xong · 🟡 làm một phần (lý do ở cuối).

| # | Thay đổi | Đã thêm | Đã test | Lỗi tìm thấy | Đã sửa |
|---|---|---|---|---|---|
| 1 | Đổi tên SAM3 CVAT Assist → SAM3 Label Assist, giữ định danh bên trong | ✅ | ✅ test_desktop: tên mới và tên cũ của bộ cài đều tự cập nhật được; token, thư mục dữ liệu, taskbar, AppId giữ nguyên | — | — |
| 2 | Bộ cài tự cài Microsoft Visual C++ khi máy thiếu | ✅ | ✅ test_v091 (bộ cài + CI kiểm chữ ký số của Microsoft) · máy Windows mới cài: chưa | L7 | ✅ |
| 3 | Kiểm tra nhanh sau khi cài (chạy SAM 3 trên ảnh mẫu), chạy lại ở trang Hỗ trợ | ✅ | ✅ test_api (endpoint), test_backend_start (tự bật backend thật rồi chạy), control smoke (bước Sẵn sàng, trang Hỗ trợ) | — | — |
| 4 | Tự sao lưu trước khi cập nhật (giữ 3 bản); sao lưu lỗi thì không cập nhật | ✅ | ✅ test_v091: thứ tự sao lưu → tắt backend → chạy bộ cài → thoát | — | — |
| 5 | Bản mới không mở được thì quay về bản cũ và báo | ✅ | ✅ test_v091 (nội dung script) + CI Windows chạy thật trường hợp bộ cài lỗi | — | — |
| 6 | Cài đặt của mọi bản cũ đọc được; file hỏng được giữ lại; mục lạ không bị xoá | ✅ | ✅ test_v091: mẫu cài đặt 0.6.0, 0.8.2, 0.9.0, file của bản mới hơn, file hỏng | L5 | ✅ |
| 7 | Trang Giới thiệu: giấy phép SAM 3 (tóm tắt + toàn văn), model và phần mềm đi kèm | ✅ | ✅ test_v091 (danh sách, chỉ mở trang có sẵn) + control smoke (vi/en) | L3 | ✅ |
| 8 | Thống kê công việc ở Tổng quan, biểu đồ 7 ngày, xuất CSV | ✅ | ✅ test_v091 (tóm tắt, CSV) + control smoke (số liệu, 7 cột) | L2, L4 | ✅ |
| 9 | Báo rõ khi panel và backend lệch bản, chỉ cách cập nhật theo từng trường hợp | ✅ | ✅ smoke v3: panel cũ hơn, backend cũ hơn, app desktop dùng máy chia sẻ, cùng bản thì không báo | — | — |
| 10 | Hướng dẫn sử dụng có ảnh (`docs/HUONG_DAN.md`), trang quyền riêng tư (`docs/PRIVACY.md`) | ✅ | ✅ test_v091: ảnh và liên kết tồn tại, phím tắt khớp phím mặc định của panel | — | — |
| 11 | Panel trong CVAT thoáng hơn | 🟡 bản mẫu | — | L1 | ✅ |
| 12 | Chữ mới có bản tiếng Anh | ✅ | ✅ test_i18n + control smoke ở chế độ English | — | — |

## Rà phần cũ

| Phần cũ | Thay đổi |
|---|---|
| Bản 0.9.0 trở về trước tự cập nhật | Release kèm bộ cài tên cũ; app mới nhận cả hai tên, ưu tiên tên mới |
| Lối tắt Desktop / Start Menu tên cũ | Bộ cài xoá lối tắt tên cũ, không để hai biểu tượng |
| Tự chạy cùng Windows (bật ở bản cũ) | Vẫn nhận ra mục tên cũ; bật / tắt lại thì dọn mục tên cũ; gỡ cài đặt xoá cả hai |
| Token Hugging Face đã lưu | Giữ tên cũ trong Windows Credential Manager, không phải nhập lại |
| Tampermonkey | Giữ `@name` cũ để không thành hai panel chạy cùng lúc |
| Script cập nhật (`run-update.cmd`) | Thay bằng script có giữ bản cũ và quay về (`desktop/safe_update.py`) |
| Gỡ cài đặt | Xoá cả bản cũ script cập nhật giữ lại (`app.prev`, `runtime.prev`) |
| Đổi ngôn ngữ | Trang đang mở (Giới thiệu, Hỗ trợ, Tổng quan) được nạp lại dữ liệu sau khi dựng lại giao diện |
| Ô báo lỗi thiếu Visual C++ trên Tổng quan (0.9.0) | Giữ làm phương án dự phòng khi người dùng từ chối quyền admin lúc cài |

## Lỗi tìm thấy khi làm và test

| # | Lỗi | Ở đâu | Đã sửa |
|---|---|---|---|
| L1 | Chữ "null" hiện dưới dòng Job/Frame (hàm dựng lại giao diện in giá trị rỗng thành chữ) | panel | ✅ lọc giá trị rỗng ở cả 4 chỗ, có test |
| L2 | Thống kê tính ngày theo UTC: ở Việt Nam việc làm 0h-7h sáng bị tính sang hôm trước | panel | ✅ tính theo giờ máy |
| L3 | Đổi ngôn ngữ khi đang ở trang Giới thiệu / Hỗ trợ thì bảng giấy phép, nhật ký bị trống | bảng điều khiển | ✅ |
| L4 | Xuất CSV in dòng trống cho mục dữ liệu hỏng | bảng điều khiển | ✅ bỏ qua mục hỏng |
| L5 | `settings.json` hỏng (mất điện khi đang ghi) bị ghi đè bằng cài đặt mặc định, mất hết cài đặt | app desktop | ✅ giữ file hỏng thành `settings.broken-*.json` |
| L6 | Bản mẫu panel ghi phím thu gọn là Alt+S, phím thật là Alt+P | bản mẫu | ✅ sửa khi làm panel |
| L7 | Chú thích tiếng Việt trong phần code của bộ cài (test ngôn ngữ bộ cài bắt được) | bộ cài | ✅ |

## Làm một phần

- 🟡 **Panel thoáng hơn:** bản mẫu đã vẽ (trang thiết kế, mục "Panel trong CVAT 0.9.1"), đang chờ duyệt.
- 🟡 **Windows thật:**
  - Tự cài Visual C++ và trường hợp "bản mới mở được" của script cập nhật cần thử trên máy thật.
  - CI chỉ chạy được trường hợp bộ cài lỗi, vì máy CI đã có sẵn Visual C++.
- 🟡 **Giấy phép của tool:** chưa chọn (đề xuất MIT). Trang Giới thiệu hiện "Chưa chọn"; thêm file `LICENSE` là trang
  tự hiện.
- 🟡 **Giấy phép model ViTPose và MediaPipe Face Landmarker:** ghi Apache-2.0 theo model card. Trong lúc làm, mình
  chưa mở được trang gốc để kiểm lại, nên trang Giới thiệu có nút mở trang chính thức của từng model.
- 🟡 **Hướng dẫn sử dụng:** mới có tiếng Việt.
