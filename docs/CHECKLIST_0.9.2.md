# Checklist bản 0.9.2: panel mới, chép và bôi đen trong app

Các cột giống `docs/CHECKLIST_0.8.0.md`:

- **Đã thêm**
- **Đã test**
- **Lỗi tìm thấy**
- **Đã sửa**

Ký hiệu: ✅ xong · 🟡 làm một phần (lý do ở cuối).

| # | Thay đổi | Đã thêm | Đã test | Lỗi tìm thấy | Đã sửa |
|---|---|---|---|---|---|
| 1 | Panel: 6 tab có icon, Cài đặt thành nút bánh răng, dòng Job / Frame + profile một hàng | ✅ | ✅ smoke v3: 6 tab có icon, bánh răng mở / đóng Cài đặt + ảnh chụp | — | — |
| 2 | Thanh Ghi cố định dưới cùng: "Ghi N, sang frame tiếp", Chỉ ghi, Hoàn tác, Bỏ đề xuất | ✅ | ✅ smoke v1, v2, v3, i18n: ghi, hoàn tác nhiều bước, frame cuối chỉ có nút ghi | — | — |
| 3 | Tab dài chia mục gập, mặc định mở mục đầu, nhớ mục đã mở; mục đang chạy luôn mở | ✅ | ✅ smoke v3: mặc định, nhớ khi quay lại tab | L3 | ✅ |
| 4 | Cài đặt chia nhóm, công tắc, phần Nâng cao gập | ✅ | ✅ smoke v3: ≥ 7 công tắc, Nâng cao gập sẵn và nhớ khi mở | — | — |
| 5 | Nút thu gọn có icon, nhãn và phím tắt | ✅ | ✅ smoke v3: thu gọn và mở lại | — | — |
| 6 | Chép nhật ký qua app (cửa sổ app chặn API clipboard của trình duyệt) | ✅ | ✅ control smoke: nội dung chép đúng, có thông báo · Windows thật: chưa | L1 | ✅ |
| 7 | Bôi đen được chữ trong bảng điều khiển và cửa sổ CVAT | ✅ | ✅ test_v092 · Windows thật: chưa | L2 | ✅ |
| 8 | Nhật ký mặc định chỉ hiện lần chạy này, nút xem mọi lần | ✅ | ✅ control smoke: lỗi của lần trước không hiện, bật Mọi lần chạy thì hiện | — | — |
| 9 | Release một file exe | ✅ | ✅ test_v092 | — | — |
| 10 | Chữ mới có bản tiếng Anh; tài liệu và thông báo không còn nói "tab Cài đặt" | ✅ | ✅ test_i18n, i18n smoke, control smoke | L4 | ✅ |

## Rà phần cũ

| Phần cũ | Thay đổi |
|---|---|
| Test panel (bấm nút theo chữ) | Bỏ qua tiêu đề mục gập (cũng là nút); tự quay lại từ Cài đặt khi cần bấm tab |
| Hướng dẫn sử dụng, README | Tên nút mới (Ghi N, sang frame tiếp; Chỉ ghi), Cài đặt ở nút bánh răng; ảnh chụp panel mới |
| Thông báo lỗi "kiểm tra ở tab Cài đặt" | Đổi thành "Cài đặt panel (nút bánh răng)", kể cả bản tiếng Anh |
| Ghi chú trong profile tự tạo | "chọn đích danh ở ô profile của panel" (không còn tab Cài đặt) |
| Dòng GPU dưới Job / Frame | Chuyển vào chú thích của nhãn trạng thái ở góc panel |
| Dòng "Đang dùng profile…" | Thành chữ trong ô profile ("Tự chọn: …") và chú thích |
| App 0.9.0 trở về trước | Không còn bộ cài tên cũ trong Release: các bản đó hiện nút "Mở trang Releases" để tải tay một lần |

## Lỗi tìm thấy khi làm và test

| # | Lỗi | Ở đâu | Đã sửa |
|---|---|---|---|
| L1 | Nút chép nhật ký báo "Không chép được": cửa sổ app chặn API clipboard của trình duyệt | bảng điều khiển | ✅ chép qua app |
| L2 | Không bôi đen được chữ ở mọi cửa sổ (thư viện cửa sổ mặc định chặn), kể cả trang CVAT | app desktop | ✅ |
| L3 | Tiêu đề mục tách phần trong ngoặc nên không khớp từ điển, tiếng Anh còn "Theo dõi đối tượng" | panel | ✅ giữ nguyên tiêu đề |
| L4 | Thông báo lỗi và tài liệu chỉ tới "tab Cài đặt" không còn tồn tại | panel, tài liệu | ✅ |

## Làm một phần

- 🟡 **Windows thật:** chép qua clipboard của Windows và bôi đen chữ trong cửa sổ app cần thử trên máy thật (môi trường
  test không có Windows).
