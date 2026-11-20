# Checklist bản 0.9.0: bảng điều khiển mới, backend tự hồi phục, hộp thoại dễ đóng

Làm theo bản mẫu đã duyệt (trang thiết kế "App 0.9: bản mẫu", 6 màn hình và bộ icon).

Các cột giống `docs/CHECKLIST_0.8.0.md`:

- **Đã thêm**
- **Đã test**
- **Lỗi tìm thấy**
- **Đã sửa**

Ký hiệu: ✅ xong · 🟡 làm một phần (lý do ở cuối).

| # | Thay đổi | Đã thêm | Đã test | Lỗi tìm thấy | Đã sửa |
|---|---|---|---|---|---|
| 1 | Sửa lỗi Bật backend / Mở CVAT không phản ứng (tìm ra từ gói báo lỗi của người dùng) | ✅ | ✅ test_backend_start: bật tiến trình backend thật tới khi trả lời /health | L1 | ✅ |
| 2 | Thanh bên 6 trang, mỗi lúc một trang, nhớ trang đang mở, Alt+1…6 | ✅ | ✅ control smoke: bấm từng mục, kiểm trang hiện, aria-current, localStorage | — | — |
| 3 | Bộ icon (nét 1,8, lưới 24) dùng chung cả bảng điều khiển | ✅ | ✅ control smoke: không còn chỗ icon chưa vẽ, kể cả sau khi đổi ngôn ngữ + ảnh chụp | — | — |
| 4 | Tổng quan: trạng thái backend, card, VRAM, nhiệt độ GPU, mở nhanh trang và job gần đây | ✅ | ✅ control smoke + ảnh chụp sáng/tối/English | — | — |
| 5 | Backend lỗi: nguyên nhân và các bước sửa có nút (thiếu Visual C++, hết VRAM, driver cũ, thiếu thư viện, bị chặn quyền…) | ✅ | ✅ test_backend_start (đoán nguyên nhân) + control smoke (khối lỗi, nút Thử lại) | L4 | ✅ |
| 6 | Backend tự bật lại khi tắt bất ngờ (tối đa 3 lần / 10 phút), báo qua khay hệ thống, tắt được | ✅ | ✅ test_backend_start: giết tiến trình thật, backend tự chạy lại · Windows thật: chưa | L7 | ✅ |
| 7 | Hỗ trợ: gói báo lỗi kèm mô tả, xem nhật ký trong app (lọc chữ, chỉ dòng lỗi, chép, mở thư mục) | ✅ | ✅ control smoke: đọc nhật ký, lọc chỉ lỗi, tô chữ đang lọc | — | — |
| 8 | Cài đặt: công tắc và nút chọn; giao diện sáng/tối/theo Windows; tự bật lại backend | ✅ | ✅ control smoke: lưu từng cài đặt, đổi giao diện + ảnh chụp tối | — | — |
| 9 | Ctrl+K: tìm và chạy mọi thao tác, gõ không dấu cũng được | ✅ | ✅ control smoke: mở, lọc, Enter chạy, Esc đóng | L3 | ✅ |
| 10 | Hộp thoại bảng điều khiển: Esc, bấm ra ngoài, ✕; xác nhận bằng hộp thoại của app; tiêu điểm trở lại chỗ cũ | ✅ | ✅ control smoke: bấm ngoài đóng "Có gì mới", Esc huỷ xác nhận | L2 | ✅ |
| 11 | Hộp thoại panel trong CVAT thay confirm/prompt của trình duyệt: Esc, bấm ngoài, Enter, Ctrl+Enter để gửi | ✅ | ✅ smoke v3: Esc và bấm ngoài thì không ghi, Enter thì ghi · 4 smoke panel | L6 | ✅ |
| 12 | Nút nào cũng có phản hồi: vòng xoay khi chạy, thông báo khi xong; xoá trang có Hoàn tác | ✅ | ✅ control smoke: Hoàn tác đưa trang quay lại | — | — |
| 13 | Trang gán nhãn có ghi nền tảng (CVAT), cài đặt cũ tự thêm | ✅ | ✅ control smoke + test_desktop | — | — |
| 14 | Chữ mới có bản tiếng Anh | ✅ | ✅ test_i18n + control smoke ở chế độ English + ảnh chụp | L5 | ✅ |

## Rà phần cũ

| Phần cũ | Thay đổi |
|---|---|
| Cài đặt từ bản trước 0.9 | Trang gán nhãn chưa có nền tảng thì tự ghi là CVAT; thêm `theme`, `auto_restart_backend` |
| Ô chọn ngôn ngữ ở thanh bên | Chỉ còn lúc cài; bảng điều khiển đổi ngôn ngữ ở trang Cài đặt |
| Khung "Cập nhật app" | Chuyển vào trang Cài đặt; nút "Có bản mới" ở thanh bên dẫn tới đó |
| Trình cài đặt | Hỏi xác nhận bằng hộp thoại của app; báo lỗi bằng thông báo thay hộp thoại của trình duyệt |
| Bộ dịch của bảng điều khiển | Khối HTML dịch dở thì dịch từng đoạn chữ (trước đây có câu dịch được một nửa) |
| Test panel cũ (dựa vào `window.confirm`) | Tự bấm nút chính trên hộp thoại mới của panel |
| `docs/DESKTOP.md` | Mô tả sáu trang, phím tắt, tự bật lại backend, không cần `Set-ExecutionPolicy` |

## Lỗi tìm thấy khi làm và test

| # | Lỗi | Ở đâu | Đã sửa |
|---|---|---|---|
| L1 | Hai tham số cùng tên khi bật tiến trình backend: bấm Bật / Mở CVAT báo lỗi ngầm, backend không chạy (0.8.0–0.8.2) | app desktop | ✅ tách tên, báo lỗi lên bảng điều khiển nếu vẫn không bật được |
| L2 | Hộp xác nhận đặt tiêu điểm vào Huỷ thay vì nút chính | bảng điều khiển | ✅ |
| L3 | Gõ ngay khi vừa mở Ctrl+K thì mất vài chữ đầu | bảng điều khiển | ✅ đặt tiêu điểm ngay |
| L4 | Dòng mã lỗi quá dài so với bản mẫu | bảng điều khiển | ✅ rút gọn, bản đầy đủ khi rê chuột |
| L5 | Khối HTML lớn dịch được một nửa ("Update chỉ thay phần app…") | bảng điều khiển, English | ✅ |
| L6 | Chú thích tiếng Việt trong CSS của panel bị coi là chữ chưa dịch | panel | ✅ đổi sang tiếng Anh |
| L7 | Tự bật lại backend không đóng file nhật ký cũ | app desktop | ✅ |

## Làm một phần

- 🟡 **Windows thật:** tự bật lại backend và nhận ra lỗi thiếu Visual C++ mới test trên Linux (tiến trình thật) và CI.
  Cần thử trên máy Windows của người dùng sau khi cài bản này.
- 🟡 **Nền tảng gán nhãn khác:** mới có chỗ ghi nền tảng. Bộ chuyển đổi cho từng nền tảng làm sau khi chốt danh sách.
