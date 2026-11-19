# Checklist bản 0.8.0

Mỗi mục ghi bốn cột:

- **Đã thêm**
- **Đã test**: test tự động nào kiểm nó; "GPU thật" là phần chỉ kiểm được trên máy có card NVIDIA
- **Lỗi tìm thấy**
- **Đã sửa**

Ký hiệu: ✅ xong · 🟡 làm một phần (lý do ở cuối).

Tên các bộ test:

- **smoke v3:** `tests/userscript_smoke_v3.cjs`, chạy panel trong trình duyệt giả với CVAT giả và backend giả lập.
- **control smoke:** `tests/desktop_control_smoke.cjs`, bảng điều khiển của app.
- **test_v080 / test_desktop:** test Python.
- **CI Windows:** lần build trên máy Windows của GitHub.

| # | Tính năng | Đã thêm | Đã test | Lỗi tìm thấy | Đã sửa |
|---|---|---|---|---|---|
| 1 | Gợi ý sẵn frame tiếp theo | ✅ | ✅ smoke v3: frame 4 hiện ngay, không gọi lại model | — | — |
| 2 | Alt+Enter: ghi và sang frame tiếp | ✅ | ✅ smoke v3 | — | — |
| 3 | Duyệt bằng phím + Alt+bấm trên ảnh | ✅ | ✅ smoke v3 | Alt+H mặc định trùng menu Help của Firefox | ✅ đổi sang Alt+P |
| 4 | Thanh ngưỡng + ẩn class tức thì | ✅ | ✅ smoke v3 (không gọi lại backend) | — | — |
| 5 | Hoàn tác nhiều bước (20 lần ghi) | ✅ | ✅ smoke v1 + v3 | Hoàn tác không trừ số nhãn của job trong thống kê | ✅ |
| 6 | Nạp sẵn model + khởi động nóng | ✅ | ✅ test_v080 (EngineProxy) · GPU thật: chưa | — | — |
| 7 | Tự nhả VRAM khi rảnh (mặc định 15 phút) | ✅ | ✅ test_v080: nhả rồi tự nạp lại | — | — |
| 8 | Job gần đây | ✅ | ✅ test_desktop + control smoke | — | — |
| 9 | Gọn còn một cửa sổ (mở thẳng CVAT) | ✅ | 🟡 chỉ kiểm phần logic; cửa sổ thật cần máy Windows có màn hình | — | — |
| 10 | Thông báo Windows khi việc dài xong | ✅ | ✅ smoke v3 (gợi ý cả job, tìm frame) + test_desktop (cầu nối) | — | — |
| 11 | Tạm dừng / tiếp tục "Cả job" | ✅ | ✅ smoke v3: dừng giữa chừng, tiếp tục chỉ chạy frame còn lại | — | — |
| 12 | Duyệt cả job dạng lưới ảnh nhỏ | ✅ | ✅ smoke v3 | — | — |
| 13 | Gửi báo lỗi một chạm | ✅ | ✅ test_desktop: không lộ token/key/cookie + control smoke | — | — |
| 14 | Sao lưu / khôi phục | ✅ | ✅ test_desktop: chặn file sao lưu có đường dẫn nguy hiểm | — | — |
| 15 | Hướng dẫn lần đầu | ✅ | ✅ smoke v3 | — | — |
| 16 | Thống kê phiên | ✅ | ✅ smoke v3 | — | — |
| 17 | Kiểm trước khi ghi | ✅ | ✅ smoke v3 | — | — |
| 18 | Tự điền truncated / occluded | ✅ | ✅ test_v080 + smoke v3 | — | — |
| 19 | Bấm điểm để tách vật | 🟡 | ✅ test_v080 + smoke v3 · GPU thật: chưa | — | — |
| 20 | Tinh chỉnh polygon (giới hạn đỉnh, làm mượt) | ✅ | ✅ test_v080 | — | — |
| 21 | Nhắc vật có thể sót | ✅ | ✅ smoke v3 | — | — |
| 22 | Một máy GPU phục vụ cả nhóm (LAN) | ✅ | ✅ test_desktop: key sai bị từ chối, http qua Internet bị chặn | — | — |
| 23 | Đồng bộ qua thư mục chung | ✅ | ✅ test_desktop: không gửi dữ liệu tự học, giữ bản của bạn khi xung đột | — | — |
| 24 | Chế độ reviewer | ✅ | ✅ test_v080 + smoke v3 | — | — |
| 25 | Báo cáo năng suất CSV | ✅ | ✅ smoke v3 | — | — |
| 26 | Tự học tách theo người | ✅ | ✅ test_v080: chuyển dữ liệu cũ, làm sạch tên người dùng | — | — |
| 27 | Đầu nối Label Studio | 🟡 | ✅ test_v080 (định dạng, xác thực, task lỗi không làm hỏng cả lô) · Label Studio thật: chưa | — | — |
| 28 | Ký số bộ cài | 🟡 | Bước ký tự bật khi repo có chứng chỉ | — | — |
| 29 | Giao diện tiếng Anh | 🟡 | ✅ smoke v3 | — | — |
| 30 | macOS / Linux | 🟡 | ✅ CI ubuntu + macos: test desktop, chạy backend từ mã nguồn | — | — |
| 31 | Chọn frame đáng làm trước | ✅ | ✅ test_v080 + smoke v3 | — | — |
| 32 | Model riêng của dự án | 🟡 | ✅ test_v080 (dữ liệu, đánh giá) · CI Windows: huấn luyện thử trên CPU | — | — |
| 33 | Tìm frame bằng chữ | ✅ | ✅ test_v080 + smoke v3 · GPU thật: chưa | — | — |
| 34 | Keyframe theo chuyển động | ✅ | ✅ test_v080 + smoke v3 | — | — |
| 35 | Độ đồng thuận giữa hai người | ✅ | ✅ test_v080 + smoke v3 | — | — |
| 36 | Phím tắt tự đặt, cỡ chữ, tương phản | ✅ | ✅ smoke v3 (chặn trùng phím) | — | — |
| 37 | Đồng hồ GPU | ✅ | ✅ test_v080 (an toàn khi không có GPU) · GPU thật: chưa | — | — |

## Lỗi tìm thấy khi test

| # | Lỗi | Ở đâu | Đã sửa |
|---|---|---|---|
| L1 | Alt+H (ẩn panel) trùng menu Help của Firefox | panel | ✅ đổi sang Alt+P |
| L2 | Hoàn tác không trừ số nhãn của job trong thống kê | panel | ✅ |
| L3 | Bảng điều khiển báo lỗi khi đọc thông tin chia sẻ LAN lúc backend chưa có cổng | app desktop | ✅ |
| L4 | Test cũ dùng khoá hoàn tác một bước | test | ✅ app tự chuyển lần hoàn tác cũ sang danh sách mới |
| L5 | Test "có gì mới" và "có bản mới" gắn cứng số phiên bản | test | ✅ đọc phiên bản từ code |

## Các mục 🟡 và lý do

- **9. Gọn còn một cửa sổ:** logic chọn mở thẳng CVAT đã test. Cửa sổ thật chỉ thấy được trên Windows có màn hình,
  máy build không có.
- **19. Bấm điểm để tách vật:** từ các điểm bấm, tool dựng một hộp rồi chọn mask chứa điểm dương và loại điểm âm.
  Chưa dùng chế độ điểm riêng của SAM 3, vì sẽ phải nạp thêm một model nữa vào card 8 GB.
- **27. Label Studio:** làm đúng chuẩn ML backend, test bằng dữ liệu mẫu, chưa thử với Label Studio thật.
- **28. Ký số:** cần chứng chỉ (SignPath Foundation miễn phí cho mã nguồn mở, hoặc mua). Có chứng chỉ thì bước ký tự chạy.
- **29. Tiếng Anh:** phần chữ chính của panel đã dịch. Một số thông báo sâu, phần lớn đến từ backend, vẫn là tiếng Việt.
- **30. macOS / Linux:** chạy từ mã nguồn, có test trên máy macOS/Linux của GitHub. Chưa có bộ cài `.dmg` / `.AppImage`.
- **32. Model riêng:** huấn luyện và đánh giá chạy được (CI huấn luyện thử trên CPU). Tốt đến đâu chỉ biết khi
  dùng dữ liệu thật của bạn.

**Phần cần GPU thật** (SAM 3, khởi động nóng, nhả VRAM, đồng hồ GPU, tìm bằng chữ) đã kiểm bằng model giả lập.
Lần chạy đầu trên máy bạn là lần kiểm cuối.
